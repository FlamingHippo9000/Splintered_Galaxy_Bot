# Splintered Galaxy Bot

A Discord bot for the Splintered Galaxy server.

This repository contains the bot entrypoint, Discord command dispatch, a local
SQLite-backed shop, and (optional) Unbelievaboat sync helpers.

## Features

- Discord command handling for inventory, balance, shop browsing, item purchase, player-to-player sales, and admin tooling
- Local `Include/shop.db` SQLite database for player balances, item catalog, shop stock, player inventory, and settings
- Write-through in-memory caching: every mutator updates the cache surgically after a successful `BEGIN IMMEDIATE` transaction, so reads are O(1) memory hits and writes are race-free
- Single persistent SQLite connection with WAL mode + `busy_timeout=5s` for reliable concurrent access
- Centralized `.env` loading via `Include/env.py`

## Requirements

- Python 3.10+ (SQLite ≥3.35 is required for `UPDATE … RETURNING`; Python 3.10 ships with a new-enough version)
- `discord.py`
- `python-dotenv`
- `openai`
- `requests`
- `pandas`
- `gspread`
- `google-auth`
- `unbelievaboat`

## Installation

1. Clone the repository.
2. Install Python dependencies:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
3. Create a `.env` file in the repository root and fill in the keys listed below.

## Configuration

Required environment variables:

- `BOT_TOKEN` — your Discord bot token
- `OPENAI_KEY` — OpenAI API key
- `BOAT_API_KEY` — Unbelievaboat API key
- `GUILD_ID` — Discord guild/server ID
- `BOAT_API_BASE_URL` — API base URL for Unbelievaboat
- `SHEET_URL` — Google Sheet URL for item syncs

The bot loads `.env` from the repository root first and falls back to `Include/.env` if needed. All env handling lives in [Include/env.py](Include/env.py).

## Usage

Run the bot from the repository root:

```bash
python3 main.py
```

`SIGINT` / `SIGTERM` are wired to close the SQLite connection cleanly before exit.

### Discord commands

Commands are defined in a single registry in [Include/bot_responses.py](Include/bot_responses.py); `?help` is generated from that registry so the list below stays in sync.

Player commands:

- `?help` — show available commands
- `?inv` / `?inventory` — show your inventory
- `?bal` / `?balance` — show your current balance
- `?items` — list catalog items and their prices
- `?shop` — show current shop stock and quantities
- `?item_info <item_name>` — show description, price, and stock for one item
- `?buy <item_name> [quantity]` — purchase an item from the shop
- `?use_item <item_name> [quantity]` (aliases: `?use`, `?use-item`) — consume an item
- `?drop_item <item_name> [quantity]` (aliases: `?drop`, `?drop-item`) — discard an item
- `?sell_item <buyer> <item_name> <price> [quantity]` (aliases: `?sell`, `?sell-item`) — sell to another player (buyer must accept)
- `?work` — earn 5,000,000 credits (30-minute cooldown per player)

Senior System Manager commands (require the `Senior System Manager` role):

- `?shop_add <name> <price> <quantity|inf> [description]` — add or restock a catalog item
- `?shop_stock <name> <quantity|inf>` — overwrite shop stock
- `?shop_edit <name> <price|description> <value>` — edit a catalog field
- `?bal_set <player> <amount>` / `?bal_add <player> <delta>` / `?bal_remove <player> <delta>`
- `?give_item <player> <item_name> [quantity]` / `?remove_item <player> <item_name> [quantity]`
- `?currency_icon <url|clear>` — set or clear the currency icon shown on `?balance`
- `?edit_item <name> <field> <value>` / `?get_item <name>` — proxied to the Unbelievaboat API

## Database

A local SQLite file is created automatically at `Include/shop.db` on first run.

Tables:

- `players` — player balance
- `items` — catalog (name, description, price)
- `shop_stock` — per-item stock quantity (`-1` means infinite)
- `inventory` — per-player item quantities
- `settings` — small key/value store (currently holds `currency_icon`)

Reliability and performance notes:

- One process-wide SQLite connection is opened lazily and reused; it's guarded by an `RLock` for safe use from threads (`asyncio.to_thread`).
- PRAGMAs set at connection open: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`.
- Every write runs inside `BEGIN IMMEDIATE` so read-modify-write cycles (buy, transfer, balance changes) cannot race or double-spend.
- Conditional `UPDATE … WHERE balance >= ? RETURNING balance` is used for atomic debits.
- In-memory caches are updated surgically on every successful write; nothing is invalidated wholesale.

## Tests

The `tests/` directory contains a `unittest`-based suite covering the shop layer, command dispatch, and parsing helpers. No additional dependencies are required to run the shop tests.

Run the entire suite from the repository root:

```bash
python3 -m unittest discover tests
```

Run a single test file:

```bash
python3 -m unittest tests.test_shop_smoke
python3 -m unittest tests.test_shop_concurrency
python3 -m unittest tests.test_shop_regression
python3 -m unittest tests.test_dispatch
```

Run a single test method:

```bash
python3 -m unittest tests.test_shop_smoke.ShopHappyPath.test_buy_item_atomic_state_change
```

What's covered:

| File | What it exercises |
| --- | --- |
| [tests/test_shop_smoke.py](tests/test_shop_smoke.py) | Happy paths for every public shop mutator and reader (balance, catalog, stock, inventory, buy/transfer, currency icon, reopen) |
| [tests/test_shop_concurrency.py](tests/test_shop_concurrency.py) | 8-thread `change_balance` stress test, racing `buy_item`, racing `transfer_item` — verifies WAL + `BEGIN IMMEDIATE` produce zero "database is locked" errors and conserve money/items |
| [tests/test_shop_regression.py](tests/test_shop_regression.py) | Error paths, argument validation, atomic rollback, cache isolation between players, sort order, "cache doesn't see direct DB writes" invariants |
| [tests/test_dispatch.py](tests/test_dispatch.py) | Command registry shape (every alias resolves, no collisions), SSM auth gating (including DM behavior), pure parsing helpers (`is_int`, `_parse_quantity_or_inf`, `_resolve_player_id`), `cross_bot_calls` payload builder and back-off math |

Each shop test runs against a fresh temp `shop.db` (see [tests/_base.py](tests/_base.py)) so the production database is never touched.

The dispatch tests import `Include/bot_responses.py`, which transitively imports `discord` and `openai`. If those packages aren't installed in the active environment the entire `test_dispatch` module will be skipped cleanly rather than erroring — install `requirements.txt` first if you want full coverage.

## Project layout

```
main.py                     # entrypoint, wires SIGINT/SIGTERM to shop.close()
Include/
  env.py                    # one-stop .env loader + get_env helper
  SplinteredGalaxyBot.py    # Discord client wiring (on_ready, on_message)
  bot_responses.py          # command-table dispatch + every ?command handler
  bot_views.py              # Paginator and SaleConfirmationView (Discord UI)
  shop.py                   # SQLite data layer + write-through caches
  cross_bot_calls.py        # Unbelievaboat API helpers
tests/                      # unittest suite (see "Tests" above)
```

## Notes

- `.env` is ignored by git.
- `Include/shop.db` is created locally and should be preserved between restarts for player data.
