"""Tests for the command-table dispatch in `Include/bot_responses.py`.

These cover:
  * The `COMMAND_LOOKUP` table: every name + alias is registered, lowercased,
    and points to the right `Command` instance.
  * Pure parsing helpers: `is_int`, `_parse_quantity_or_inf`,
    `_parse_optional_quantity`, `_resolve_player_id`.
  * The full dispatch pipeline using fake `author` and `message` objects, so
    we exercise SSM auth gating, alias resolution, and async-handler awaiting
    without needing a live Discord connection.

Importing `Include.bot_responses` pulls in `discord` and `openai`. When those
aren't installed (e.g. the venv hasn't been populated yet) the whole module
is skipped instead of erroring — install the bot's requirements first to
exercise these tests:

    pip install -r requirements.txt
"""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from Include import bot_responses as br
    from Include import shop
    _IMPORT_ERROR = None
except Exception as e:  # pragma: no cover -- depends on local env
    br = None  # type: ignore[assignment]
    shop = None  # type: ignore[assignment]
    _IMPORT_ERROR = e


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fake_role(name: str) -> SimpleNamespace:
    """Fake `discord.Role`-like object that `discord.utils.get(..., name=...)` matches."""
    return SimpleNamespace(name=name)


def _fake_author(user_id: int = 42, roles: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, roles=list(roles or []))


def _fake_guild(roles: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(roles=list(roles or []))


def _fake_message(content: str, author: SimpleNamespace, guild: SimpleNamespace | None) -> SimpleNamespace:
    return SimpleNamespace(content=content, author=author, guild=guild)


def _await(coro):
    """Run a coroutine to completion in a fresh event loop."""
    return asyncio.run(coro)


@unittest.skipIf(_IMPORT_ERROR is not None, f"bot_responses unavailable: {_IMPORT_ERROR}")
class CommandRegistry(unittest.TestCase):
    """Structural checks on the COMMANDS list + COMMAND_LOOKUP map."""

    def test_lookup_includes_every_name_and_alias(self) -> None:
        for cmd in br.COMMANDS:
            self.assertIs(br.COMMAND_LOOKUP[cmd.name], cmd, f"name {cmd.name!r} not in lookup")
            for alias in cmd.aliases:
                self.assertIs(
                    br.COMMAND_LOOKUP[alias.lower()], cmd,
                    f"alias {alias!r} not in lookup for {cmd.name!r}",
                )

    def test_lookup_keys_are_lowercase(self) -> None:
        for key in br.COMMAND_LOOKUP:
            self.assertEqual(key, key.lower())

    def test_no_alias_collisions(self) -> None:
        seen: dict[str, str] = {}
        for cmd in br.COMMANDS:
            for key in (cmd.name, *cmd.aliases):
                lower = key.lower()
                self.assertNotIn(
                    lower, seen,
                    f"alias {lower!r} declared by both {seen.get(lower)} and {cmd.name}",
                )
                seen[lower] = cmd.name

    def test_help_lists_every_command(self) -> None:
        resp = br.handle_help()
        body = resp.embed.fields[0].value
        for cmd in br.COMMANDS:
            self.assertIn(f"?{cmd.name}", body, f"`?{cmd.name}` missing from ?help")


@unittest.skipIf(_IMPORT_ERROR is not None, f"bot_responses unavailable: {_IMPORT_ERROR}")
class PureHelpers(unittest.TestCase):
    """Argument parsers + small utilities used inside the handlers."""

    def test_is_int(self) -> None:
        for ok in ("0", "42", "-7", "  3  "):
            self.assertTrue(br.is_int(ok), f"{ok!r} should parse")
        for bad in ("", "abc", "1.5", "1e5", "--3"):
            self.assertFalse(br.is_int(bad), f"{bad!r} should reject")

    def test_parse_quantity_or_inf(self) -> None:
        self.assertEqual(br._parse_quantity_or_inf("5"), 5)
        self.assertEqual(br._parse_quantity_or_inf("inf"), -1)
        self.assertEqual(br._parse_quantity_or_inf("INF"), -1)
        self.assertEqual(br._parse_quantity_or_inf("Infinite"), -1)
        self.assertIsNone(br._parse_quantity_or_inf("abc"))
        self.assertIsNone(br._parse_quantity_or_inf(""))

    def test_parse_optional_quantity_default(self) -> None:
        # Arg absent → default returned.
        self.assertEqual(br._parse_optional_quantity(["cmd", "item"], idx=2), 1)
        self.assertEqual(br._parse_optional_quantity(["cmd"], idx=5, default=9), 9)

    def test_parse_optional_quantity_valid(self) -> None:
        self.assertEqual(br._parse_optional_quantity(["cmd", "item", "3"], idx=2), 3)

    def test_parse_optional_quantity_invalid_returns_error(self) -> None:
        result = br._parse_optional_quantity(["cmd", "item", "abc"], idx=2)
        self.assertIsInstance(result, br.BotResponse)

    def test_resolve_player_id(self) -> None:
        self.assertEqual(br._resolve_player_id("12345"), "12345")
        self.assertEqual(br._resolve_player_id("<@12345>"), "12345")
        self.assertEqual(br._resolve_player_id("<@!12345>"), "12345")
        self.assertEqual(br._resolve_player_id("  <@12345>  "), "12345")


@unittest.skipIf(_IMPORT_ERROR is not None, f"bot_responses unavailable: {_IMPORT_ERROR}")
class DispatchPipeline(unittest.TestCase):
    """End-to-end checks against handle_response with fabricated discord objects."""

    def test_unknown_command_returns_error(self) -> None:
        author = _fake_author()
        msg = _fake_message("?notacommand", author, _fake_guild())
        resp = _await(br.handle_response(msg, author))
        self.assertIsInstance(resp, br.BotResponse)
        self.assertIn("don't recognize", resp.embed.description)

    def test_empty_message_returns_error(self) -> None:
        author = _fake_author()
        msg = _fake_message("", author, _fake_guild())
        resp = _await(br.handle_response(msg, author))
        self.assertIn("No command", resp.embed.description)

    def test_mismatched_quotes_returns_error(self) -> None:
        author = _fake_author()
        msg = _fake_message('?buy "unclosed', author, _fake_guild())
        resp = _await(br.handle_response(msg, author))
        self.assertIn("Mismatched quotes", resp.embed.description)

    def test_openai_prefix_with_flag_off(self) -> None:
        author = _fake_author()
        msg = _fake_message("~hi grandma", author, _fake_guild())
        # OPENAI_FLAG is False by default; expect an explicit error, not a
        # silent fallthrough into the regular dispatcher.
        original = br.OPENAI_FLAG
        try:
            br.OPENAI_FLAG = False
            resp = _await(br.handle_response(msg, author))
        finally:
            br.OPENAI_FLAG = original
        self.assertIn("OpenAI", resp.embed.description)

    def test_ssm_gated_command_rejects_non_ssm(self) -> None:
        # Guild has the role defined but the author doesn't hold it.
        ssm = _fake_role(br.SENIOR_SYS_MANAGER_ROLE)
        author = _fake_author(roles=[])
        guild = _fake_guild(roles=[ssm])
        msg = _fake_message("?shop_add sword 10 5", author, guild)
        resp = _await(br.handle_response(msg, author))
        self.assertIn("not authorized", resp.embed.description.lower())

    def test_ssm_check_fails_in_dm(self) -> None:
        # No guild => SSM check must fail closed, not crash.
        author = _fake_author(roles=[])
        msg = _fake_message("?shop_add sword 10 5", author, guild=None)
        resp = _await(br.handle_response(msg, author))
        self.assertIn("not authorized", resp.embed.description.lower())



# ---------------------------------------------------------------------------
# Dispatch tests that need real shop state (use the temp-DB fixture)
# ---------------------------------------------------------------------------


if _IMPORT_ERROR is None:
    from tests._base import ShopTestBase as _ShopTestBase
else:
    _ShopTestBase = unittest.TestCase  # placeholder so the class can be defined


@unittest.skipIf(_IMPORT_ERROR is not None, f"bot_responses unavailable: {_IMPORT_ERROR}")
class DispatchWithShopFixture(_ShopTestBase):
    """Dispatch tests that touch shop state and need a clean DB per case."""

    def test_alias_resolves_to_canonical_handler(self) -> None:
        """`?inventory` should hit the same handler as `?inv`."""
        author = _fake_author(user_id=999)
        msg_short = _fake_message("?inv", author, _fake_guild())
        msg_long = _fake_message("?inventory", author, _fake_guild())
        resp_short = _await(br.handle_response(msg_short, author))
        resp_long = _await(br.handle_response(msg_long, author))
        self.assertIsInstance(resp_short, br.BotResponse)
        self.assertIsInstance(resp_long, br.BotResponse)
        # Both render an empty-inventory error and should be byte-identical.
        self.assertEqual(
            resp_short.embed.description,
            resp_long.embed.description,
        )

    def test_balance_command_returns_zero_for_new_player(self) -> None:
        author = _fake_author(user_id=12345)
        msg = _fake_message("?bal", author, _fake_guild())
        resp = _await(br.handle_response(msg, author))
        self.assertIsInstance(resp, br.BotResponse)
        self.assertIn("0 credits", resp.embed.description)

    def test_create_item_interactive_returns_view(self) -> None:
        """SSM caller should get back a BotResponse carrying a CreateItemView."""
        from Include import bot_views

        ssm = _fake_role(br.SENIOR_SYS_MANAGER_ROLE)
        author = _fake_author(user_id=42, roles=[ssm])
        guild = _fake_guild(roles=[ssm])
        msg = _fake_message("?create_item_interactive", author, guild)
        resp = _await(br.handle_response(msg, author))
        self.assertIsInstance(resp, br.BotResponse)
        self.assertIsInstance(resp.view, bot_views.CreateItemView)


@unittest.skipIf(_IMPORT_ERROR is not None, f"bot_views unavailable: {_IMPORT_ERROR}")
class ModalValidation(unittest.TestCase):
    """Strict-int parser used by CreateItemModal's on_submit."""

    def test_accepts_positive(self) -> None:
        from Include.bot_views import _parse_int
        self.assertEqual(_parse_int("5", "Price"), 5)

    def test_accepts_negative(self) -> None:
        from Include.bot_views import _parse_int
        self.assertEqual(_parse_int("-1", "Quantity"), -1)

    def test_rejects_double_minus(self) -> None:
        from Include.bot_views import _parse_int
        with self.assertRaises(ValueError):
            _parse_int("--5", "Quantity")

    def test_rejects_empty(self) -> None:
        from Include.bot_views import _parse_int
        with self.assertRaises(ValueError):
            _parse_int("", "Price")
        with self.assertRaises(ValueError):
            _parse_int("-", "Quantity")

    def test_rejects_non_integer(self) -> None:
        from Include.bot_views import _parse_int
        with self.assertRaises(ValueError):
            _parse_int("1.5", "Price")
        with self.assertRaises(ValueError):
            _parse_int("abc", "Quantity")


# ---------------------------------------------------------------------------
# cross_bot_calls helpers (no network calls; pure functions only)
# ---------------------------------------------------------------------------


try:
    from Include import cross_bot_calls as cbc
    _CBC_ERROR = None
except Exception as e:  # pragma: no cover
    cbc = None  # type: ignore[assignment]
    _CBC_ERROR = e


@unittest.skipIf(_CBC_ERROR is not None, f"cross_bot_calls unavailable: {_CBC_ERROR}")
class CrossBotPureHelpers(unittest.TestCase):
    def test_is_int(self) -> None:
        for ok in ("0", "42", "-7"):
            self.assertTrue(cbc.is_int(ok))
        for bad in ("", "abc", "1.5"):
            self.assertFalse(cbc.is_int(bad))

    def test_backoff_seconds_monotone_and_bounded(self) -> None:
        # First attempt small; later attempts grow but never exceed MAX_SLEEP.
        prev = -1.0
        for attempt in range(1, cbc.MAX_RETRIES + 1):
            v = cbc._backoff_seconds(attempt)
            self.assertGreaterEqual(v, 0)
            self.assertLessEqual(v, cbc.MAX_SLEEP)
            # Jitter means strict monotonicity isn't guaranteed, but the
            # *floor* of each attempt does grow up to the cap.
            floor = min(0.1 * 2 ** (attempt - 1), cbc.MAX_SLEEP)
            self.assertGreaterEqual(v, floor)

    def test_build_update_payload_name_and_description(self) -> None:
        self.assertEqual(cbc._build_update_payload("name", "Sword"), {"name": "Sword"})
        self.assertEqual(cbc._build_update_payload("description", "Sharp"), {"description": "Sharp"})

    def test_build_update_payload_price_coerces_to_int(self) -> None:
        self.assertEqual(cbc._build_update_payload("price", "42"), {"price": 42})

    def test_build_update_payload_stock_finite_and_infinite(self) -> None:
        self.assertEqual(
            cbc._build_update_payload("stock", "10"),
            {"unlimited_stock": False, "stock_remaining": 10},
        )
        self.assertEqual(
            cbc._build_update_payload("stock", "inf"),
            {"unlimited_stock": True},
        )
        self.assertEqual(
            cbc._build_update_payload("stock", "INF"),
            {"unlimited_stock": True},
        )

    def test_build_update_payload_unknown_field_rejected(self) -> None:
        with self.assertRaises(ValueError):
            cbc._build_update_payload("color", "red")

    def test_handle_edit_item_response_known_fields(self) -> None:
        self.assertEqual(cbc.handle_edit_item_response({"price": 99}, "price"), "99")
        self.assertEqual(cbc.handle_edit_item_response({"stock_remaining": 5}, "stock"), "5")
        self.assertEqual(cbc.handle_edit_item_response({"name": "S"}, "name"), "S")

    def test_handle_edit_item_response_unknown_field_returns_empty(self) -> None:
        self.assertEqual(cbc.handle_edit_item_response({"foo": 1}, "foo"), "")


if __name__ == "__main__":
    unittest.main()
