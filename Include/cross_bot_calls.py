import time
import argparse
import logging
import random
from urllib.parse import quote
import pandas as pd
from unbelievaboat import Client
import requests
from dotenv import load_dotenv
import os
from enum import Enum

class BAD_RESPONSE(Enum):
    INVALID_ITEM = -1
    RATE_LIMIT = -2
    TOO_MANY_ITEMS = -3

MAX_SLEEP = 60
MAX_RETRIES = 7
getter_sleep_timer = 0.1
editor_sleep_timer = 0.1
getter_retries = 0
editor_retries = 0

# ----------------------------
# Setup
# ----------------------------
load_dotenv()
def get_env(name):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value

BOAT_API_BASE = get_env("BOAT_API_BASE_URL")
BOAT_API_KEY = get_env("BOAT_API_KEY")
SHEET_URL = get_env("SHEET_URL")

if not BOAT_API_BASE or not BOAT_API_KEY or not SHEET_URL:
    raise ValueError("Missing required environment variables in .env")

logging.basicConfig(
    filename="sync.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


# ----------------------------
# Load Data
# ----------------------------
# def load_sheet():
#     try:
#         df = pd.read_csv(SHEET_URL)
#         return df
#     except Exception as e:
#         raise RuntimeError(f"Failed to load sheet: {e}")


# ----------------------------
# Clean + Normalize Data
# ----------------------------
# def clean_data(df: pd.DataFrame) -> pd.DataFrame:
#     df.columns = [col.strip().lower() for col in df.columns]

#     required_cols = ["item_id", "name", "price", "active"]
#     missing = [c for c in required_cols if c not in df.columns]
#     if missing:
#         raise ValueError(f"Missing required columns: {missing}")

#     df["item_id"] = pd.to_numeric(df["item_id"], errors="coerce")
#     df["price"] = pd.to_numeric(df["price"], errors="coerce")

#     df["active"] = (
#         df["active"]
#         .astype(str)
#         .str.strip()
#         .str.upper()
#         .isin(["TRUE", "1", "YES"])
#     )

#     df["name"] = df["name"].astype(str).str.strip()

#     return df


# ----------------------------
# Validation
# ----------------------------
# def validate(df: pd.DataFrame):
#     errors = []

#     for idx, row in df.iterrows():
#         if pd.isna(row["item_id"]):
#             errors.append(f"Row {idx}: missing item_id")

#         if pd.isna(row["price"]) or row["price"] < 0:
#             errors.append(f"Row {idx}: invalid price")

#         if pd.isna(row["quantity"]) or row["quantity"] < 0:
#             errors.append(f"Row {idx}: invalid quantity")

#         if not row["name"]:
#             errors.append(f"Row {idx}: missing name")

#     if errors:
#         for e in errors:
#             print("❌", e)
#         raise ValueError("Validation failed. Fix sheet before syncing.")

def is_int(val):
    try:
        int(val)
        return True
    except ValueError:
        return False

# ----------------------------
# API Call
# ----------------------------
async def handle_query_item(guild_id, name):
    encoded_name = quote(name)
    url = f"{BOAT_API_BASE}/guilds/{guild_id}/items?sort=id&limit=100&page=1&query={encoded_name}"

    headers = {
        "accept": "application/json",
        "Authorization": BOAT_API_KEY
    }

    response = requests.get(url, headers=headers)
    if response.status_code == 429:
        if getter_retries >= MAX_RETRIES:
            return str(BAD_RESPONSE.RATE_LIMIT.value)
        sleep_time = getter_sleep_timer + random.uniform(0, 1)
        time.sleep(sleep_time)
        getter_sleep_timer = min(getter_sleep_timer * 2, MAX_SLEEP)
        return str(BAD_RESPONSE.RATE_LIMIT.value)
    
    parsed_response = response.json()
    if len(parsed_response["items"]) == 0:
        return str(BAD_RESPONSE.INVALID_ITEM.value)
    elif len(parsed_response["items"]) > 1:
        item_names = []
        for i in parsed_response["items"]:
            item_names.append(i["name"])
        print("WARN: multiple items found:" + str(item_names))
        return str(BAD_RESPONSE.TOO_MANY_ITEMS.value)
    else:
        item = parsed_response["items"][0]
        return item["id"]

async def update_item(guild_id, item_id, field, value):
    url = f"{BOAT_API_BASE}/guilds/{guild_id}/items/{item_id}?cascade_update=true"
    payload = {}
    if field == "name" or field == "description":
        payload = {
            field: value
        } 
    elif field == "price":
        payload = {
            field: int(value)
        } 
    elif field == "stock" and is_int(value):
        payload = {
            "unlimited_stock": False,
            "stock_remaining": int(value),
        }
    elif field == "stock" and value == "inf":
        payload = {
        "unlimited_stock": True
    }
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": BOAT_API_KEY
    }
    
    response = requests.patch(url, json=payload, headers=headers)
    if response.status_code == 429:
        if editor_retries >= MAX_RETRIES:
            return str(BAD_RESPONSE.RATE_LIMIT.value)
        sleep_time = editor_sleep_timer + random.uniform(0, 1)
        time.sleep(sleep_time)
        editor_sleep_timer = min(editor_sleep_timer * 2, MAX_SLEEP)
        return str(BAD_RESPONSE.RATE_LIMIT.value)
    parsed_response = response.json()
    return handle_edit_item_response(parsed_response, field)

def handle_edit_item_response(response, field):
    if field == "name" or field == "description" or field == "price":
        return str(response[field])
    elif field == "stock":
        return str(response["stock_remaining"])
    else:
        return ""

# ----------------------------
# Sync Logic
# ----------------------------
# def sync(df: pd.DataFrame, dry_run: bool):
#     items = df.to_dict(orient="records")

#     success = 0
#     failed = 0

#     for item in items:
#         if dry_run:
#             print(f"[DRY RUN] Would update item {int(item['item_id'])}: {item}")
#             continue

#         try:
#             res = update_item(item)

#             if res.status_code in (200, 201):
#                 print(f"✅ Updated {int(item['item_id'])}")
#                 logging.info(f"Updated {item['item_id']}")
#                 success += 1
#             else:
#                 print(f"❌ Failed {int(item['item_id'])}: {res.text}")
#                 logging.error(f"Failed {item['item_id']}: {res.text}")
#                 failed += 1

#         except Exception as e:
#             print(f"🔥 Error {int(item['item_id'])}: {e}")
#             logging.exception(f"Exception for {item['item_id']}")
#             failed += 1

#         time.sleep(0.1)  # basic rate limiting

#     print("\n--- Summary ---")
#     print(f"Success: {success}")
#     print(f"Failed: {failed}")


# ----------------------------
# Main
# ----------------------------
# def main():
#     parser = argparse.ArgumentParser(description="Sync inventory from Google Sheets to API")
#     parser.add_argument("--dry-run", action="store_true", help="Preview changes without sending API calls")

#     args = parser.parse_args()

#     print("Loading sheet...")
#     df = load_sheet()

#     print("Cleaning data...")
#     df = clean_data(df)

#     print("Validating data...")
#     validate(df)

#     print("Starting sync...")
#     sync(df, dry_run=args.dry_run)


# if __name__ == "__main__":
#     main()