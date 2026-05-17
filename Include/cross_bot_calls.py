"""Utilities for interacting with the Unbelievaboat API."""

import asyncio
import logging
import random
import time
from enum import Enum
from typing import Any, Dict
from urllib.parse import quote

import requests  # pyright: ignore[reportMissingImports]

from Include.env import get_env


class BAD_RESPONSE(Enum):
    """Sentinel values returned by the boat API helpers when a call fails.

    These are integers (returned as strings from the sync helpers) so that
    callers can distinguish them from genuine, positive item IDs.
    """

    INVALID_ITEM = -1
    RATE_LIMIT = -2
    TOO_MANY_ITEMS = -3


# Upper bound on a single back-off sleep, in seconds.
MAX_SLEEP = 60
# Number of HTTP retries before we give up and return RATE_LIMIT.
MAX_RETRIES = 7
# (guild_id, item_name) -> resolved boat item id. Process-lifetime cache.
ITEM_ID_CACHE: Dict[tuple[int, str], int] = {}

BOAT_API_BASE = get_env("BOAT_API_BASE_URL")
BOAT_API_KEY = get_env("BOAT_API_KEY")
SHEET_URL = get_env("SHEET_URL")

boat_session = requests.Session()
boat_session.headers.update(
    {
        "accept": "application/json",
        "Authorization": BOAT_API_KEY,
        "content-type": "application/json",
    }
)

logging.basicConfig(
    filename="sync.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def is_int(val: str) -> bool:
    """Return True iff `val` parses as a base-10 integer."""
    try:
        int(val)
        return True
    except ValueError:
        return False


def _backoff_seconds(attempt: int) -> float:
    """Compute the exponential back-off delay for HTTP 429 retries.

    `attempt` is 1-indexed (first retry == 1). Adds jitter so concurrent
    callers don't synchronize their retries.
    """
    return min(0.1 * 2 ** (attempt - 1) + random.uniform(0, 1), MAX_SLEEP)


def _query_item_sync(guild_id: int, name: str) -> Any:
    """Resolve a boat item name to its numeric ID.

    Returns:
        The integer item ID on success, or one of `BAD_RESPONSE.*.value`
        rendered as a string when the API call fails or is ambiguous.
        Results are memoized in `ITEM_ID_CACHE` for the process lifetime.
    """
    cache_key = (guild_id, name)
    if cache_key in ITEM_ID_CACHE:
        return ITEM_ID_CACHE[cache_key]

    encoded_name = quote(name, safe="")
    url = f"{BOAT_API_BASE}/guilds/{guild_id}/items?sort=id&limit=100&page=1&query={encoded_name}"

    for attempt in range(1, MAX_RETRIES + 1):
        response = boat_session.get(url, timeout=15)
        if response.status_code == 429:
            if attempt == MAX_RETRIES:
                return str(BAD_RESPONSE.RATE_LIMIT.value)
            time.sleep(_backoff_seconds(attempt))
            continue

        if not response.ok:
            logging.warning(
                "Boat query failed %s %s %s",
                response.status_code,
                url,
                response.text[:200],
            )
            return str(BAD_RESPONSE.INVALID_ITEM.value)

        items = response.json().get("items", [])
        if not items:
            return str(BAD_RESPONSE.INVALID_ITEM.value)
        if len(items) > 1:
            logging.warning(
                "multiple items found: %s", [item["name"] for item in items]
            )
            return str(BAD_RESPONSE.TOO_MANY_ITEMS.value)

        item_id = items[0]["id"]
        ITEM_ID_CACHE[cache_key] = item_id
        return item_id

    return str(BAD_RESPONSE.RATE_LIMIT.value)


def _build_update_payload(field: str, value: Any) -> Dict[str, Any]:
    """Translate (`field`, `value`) into the JSON body expected by the boat API.

    Raises:
        ValueError: when `field` is not one of the supported columns.
    """
    if field in ("name", "description"):
        return {field: value}
    if field == "price":
        return {field: int(value)}
    if field == "stock":
        if str(value).lower() == "inf":
            return {"unlimited_stock": True}
        if is_int(str(value)):
            return {"unlimited_stock": False, "stock_remaining": int(value)}
    raise ValueError(f"Unsupported field: {field}")


def _update_item_sync(guild_id: int, item_id: str, field: str, value: Any) -> str:
    """PATCH a boat item field and return the resulting value as a string.

    Returns:
        The updated field value on success, or `str(BAD_RESPONSE.RATE_LIMIT.value)`
        if the call is throttled or otherwise non-OK after retries.
    """
    url = f"{BOAT_API_BASE}/guilds/{guild_id}/items/{item_id}?cascade_update=true"
    payload = _build_update_payload(field, value)

    for attempt in range(1, MAX_RETRIES + 1):
        response = boat_session.patch(url, json=payload, timeout=15)
        if response.status_code == 429:
            if attempt == MAX_RETRIES:
                return str(BAD_RESPONSE.RATE_LIMIT.value)
            time.sleep(_backoff_seconds(attempt))
            continue

        if not response.ok:
            logging.warning(
                "Boat update failed %s %s %s",
                response.status_code,
                url,
                response.text[:200],
            )
            return str(BAD_RESPONSE.RATE_LIMIT.value)

        return handle_edit_item_response(response.json(), field)

    return str(BAD_RESPONSE.RATE_LIMIT.value)


async def handle_query_item(guild_id: int, name: str) -> Any:
    """Async wrapper around `_query_item_sync` that runs in a worker thread."""
    return await asyncio.to_thread(_query_item_sync, guild_id, name)


async def update_item(guild_id: int, item_id: str, field: str, value: Any) -> str:
    """Async wrapper around `_update_item_sync` that runs in a worker thread."""
    return await asyncio.to_thread(_update_item_sync, guild_id, item_id, field, value)


def handle_edit_item_response(response: Dict[str, Any], field: str) -> str:
    """Pluck the updated value out of a PATCH response body.

    Returns an empty string if `field` is unrecognized; callers treat that
    as an "unsupported field" condition.
    """
    if field in ("name", "description", "price"):
        return str(response[field])
    if field == "stock":
        return str(response["stock_remaining"])
    return ""