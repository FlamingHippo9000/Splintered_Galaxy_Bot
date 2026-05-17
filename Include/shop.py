"""SQLite-backed shop data layer with in-memory caching.

Architecture
------------
A single persistent SQLite connection is opened lazily on first use and held
for the lifetime of the process. WAL mode lets readers proceed while a writer
holds the lock; ``busy_timeout`` blocks competing writers for up to 5s rather
than failing immediately. An ``RLock`` serializes Python-level access so the
shared connection is safe to call from threads spawned via
``asyncio.to_thread`` in the future.

Every public mutator runs inside a ``BEGIN IMMEDIATE`` transaction so
concurrent writers serialize at the database layer and read-modify-write loops
(stock decrement, inventory transfer, etc.) execute atomically without
time-of-check/time-of-use races.

Caches mirror the on-disk state. Mutators update the cache **in place** after
the transaction commits — full invalidation only happens when the cache could
have diverged from disk (which, with the current design, is never). The
in-memory copy is therefore always at least as fresh as the last successful
write from this process.
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

DB_FILE = Path(__file__).resolve().parent / "shop.db"

# ----------------------------------------------------------------------------
# Connection management
# ----------------------------------------------------------------------------

# Re-entrant: write transactions may nest with helper queries.
_conn_lock = threading.RLock()
_connection: Optional[sqlite3.Connection] = None


def _open_connection() -> sqlite3.Connection:
    """Open the underlying SQLite file with the bot's tuned PRAGMAs.

    PRAGMAs explained:
      * ``journal_mode = WAL`` — readers don't block the writer and vice versa.
      * ``synchronous = NORMAL`` — durable across app crashes; only loses data
        on OS crash, which is the right trade-off for a Discord bot.
      * ``foreign_keys = ON`` — enforce the cascade deletes declared in the schema.
      * ``busy_timeout = 5000`` — wait up to 5s for a competing writer instead
        of raising ``OperationalError: database is locked`` immediately.
    """
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False,
        # Autocommit: we manage transactions explicitly with BEGIN IMMEDIATE
        # so reads don't accidentally hold a transaction open.
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _conn() -> sqlite3.Connection:
    """Return the process-wide connection, opening it on first call."""
    global _connection
    if _connection is None:
        _connection = _open_connection()
    return _connection


@contextmanager
def _read_tx() -> Iterator[sqlite3.Connection]:
    """Hold the connection lock for a single read. No transaction is started."""
    with _conn_lock:
        yield _conn()


@contextmanager
def _write_tx() -> Iterator[sqlite3.Connection]:
    """Run a write transaction with ``BEGIN IMMEDIATE`` (writer lock upfront).

    Rolls back on any raised exception so partial work never reaches disk.
    Cache mutations should happen **after** this block exits successfully.
    """
    with _conn_lock:
        conn = _conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def close() -> None:
    """Close the persistent connection. Idempotent; safe to call at shutdown."""
    global _connection
    with _conn_lock:
        if _connection is not None:
            _connection.close()
            _connection = None


def sync_cache() -> None:
    """No-op retained for backwards compatibility.

    The cache layer is now write-through, so there is never anything to flush.
    Old callers (``main.py``'s shutdown hooks) keep working without modification.
    """


# ----------------------------------------------------------------------------
# Caches
# ----------------------------------------------------------------------------

# Catalog (id -> row dict). Both indexes share the same dict instance per item.
_items_by_id: Dict[int, Dict[str, Any]] = {}
_items_by_name: Dict[str, Dict[str, Any]] = {}
_items_loaded = False

# Shop stock (item_id -> {id, name, description, price, quantity}).
_shop_by_id: Dict[int, Dict[str, Any]] = {}
_shop_loaded = False

# Per-player inventory: player_id -> {item_name -> quantity}.
_inventory: Dict[str, Dict[str, int]] = {}

# Per-player balance + the set of players already fetched from disk.
_balance: Dict[str, int] = {}
_balance_loaded_for: Set[str] = set()

# Cached value of the optional currency icon URL.
_currency_icon: Optional[str] = None
_currency_icon_loaded = False


def _reset_caches() -> None:
    """Drop every cache. Intended for tests only."""
    global _items_loaded, _shop_loaded, _currency_icon, _currency_icon_loaded
    _items_by_id.clear()
    _items_by_name.clear()
    _items_loaded = False
    _shop_by_id.clear()
    _shop_loaded = False
    _inventory.clear()
    _balance.clear()
    _balance_loaded_for.clear()
    _currency_icon = None
    _currency_icon_loaded = False


# ----------------------------------------------------------------------------
# Schema bootstrap
# ----------------------------------------------------------------------------


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS players (
        player_id TEXT PRIMARY KEY,
        balance INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        price INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shop_stock (
        item_id INTEGER PRIMARY KEY,
        quantity INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory (
        player_id TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(player_id, item_id),
        FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE,
        FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)


def init_db() -> None:
    """Create every table required by the bot. Idempotent.

    Runs in autocommit (no surrounding transaction) because SQLite refuses
    DDL inside a ``BEGIN IMMEDIATE`` started for another purpose, and each
    ``CREATE TABLE IF NOT EXISTS`` is already idempotent on its own.
    """
    with _conn_lock:
        conn = _conn()
        for statement in _SCHEMA:
            conn.execute(statement)


# ----------------------------------------------------------------------------
# Settings (currency icon)
# ----------------------------------------------------------------------------


def set_currency_icon(url: str) -> None:
    """Persist the currency icon URL shown on `?balance` embeds."""
    global _currency_icon, _currency_icon_loaded
    with _write_tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
            ("currency_icon", url),
        )
    _currency_icon = url
    _currency_icon_loaded = True


def clear_currency_icon() -> None:
    """Remove the configured currency icon."""
    global _currency_icon, _currency_icon_loaded
    with _write_tx() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", ("currency_icon",))
    _currency_icon = None
    _currency_icon_loaded = True


def get_currency_icon() -> Optional[str]:
    """Return the configured currency icon URL, or None if unset.

    Cached after the first read; subsequent calls are pure in-memory lookups.
    """
    global _currency_icon, _currency_icon_loaded
    if _currency_icon_loaded:
        return _currency_icon
    with _read_tx() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("currency_icon",)
        ).fetchone()
    _currency_icon = row["value"] if row else None
    _currency_icon_loaded = True
    return _currency_icon


# ----------------------------------------------------------------------------
# Catalog (items) cache
# ----------------------------------------------------------------------------


def _row_to_item(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "price": int(row["price"]),
    }


def _load_items() -> None:
    """Populate the catalog cache from disk if not already loaded."""
    global _items_loaded
    if _items_loaded:
        return
    with _read_tx() as conn:
        rows = conn.execute(
            "SELECT id, name, description, price FROM items ORDER BY name"
        ).fetchall()
    _items_by_id.clear()
    _items_by_name.clear()
    for row in rows:
        item = _row_to_item(row)
        _items_by_id[item["id"]] = item
        _items_by_name[item["name"]] = item
    _items_loaded = True


def _put_item_cache(item: Dict[str, Any]) -> None:
    """Insert or replace an item in both catalog indexes, preserving identity."""
    _items_by_id[item["id"]] = item
    _items_by_name[item["name"]] = item


def get_item_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Return the catalog row for `name`, or None if it doesn't exist."""
    _load_items()
    return _items_by_name.get(name)


def get_items() -> List[Dict[str, Any]]:
    """Return every catalog item as a list of dicts, sorted by name."""
    _load_items()
    return sorted(_items_by_id.values(), key=lambda i: i["name"])


# ----------------------------------------------------------------------------
# Shop stock cache
# ----------------------------------------------------------------------------


def _load_shop() -> None:
    """Populate the shop-stock cache from disk if not already loaded."""
    global _shop_loaded
    if _shop_loaded:
        return
    with _read_tx() as conn:
        rows = conn.execute(
            """
            SELECT items.id, items.name, items.description, items.price,
                   COALESCE(shop_stock.quantity, 0) AS quantity
            FROM items
            LEFT JOIN shop_stock ON items.id = shop_stock.item_id
            ORDER BY items.name
            """
        ).fetchall()
    _shop_by_id.clear()
    for row in rows:
        _shop_by_id[int(row["id"])] = {
            "id": int(row["id"]),
            "name": row["name"],
            "description": row["description"],
            "price": int(row["price"]),
            "quantity": int(row["quantity"]),
        }
    _shop_loaded = True


def _set_shop_cache(item_id: int, name: str, description: str, price: int, quantity: int) -> None:
    """Insert/replace one row in the shop cache. No-op if the cache isn't loaded."""
    if not _shop_loaded:
        return
    _shop_by_id[item_id] = {
        "id": item_id,
        "name": name,
        "description": description,
        "price": price,
        "quantity": quantity,
    }


def get_shop() -> List[Dict[str, Any]]:
    """Return the shop catalog with current stock quantities, sorted by name."""
    _load_shop()
    return sorted(_shop_by_id.values(), key=lambda i: i["name"])


# ----------------------------------------------------------------------------
# Player balance
# ----------------------------------------------------------------------------


def ensure_player(player_id: str, balance: int = 0) -> None:
    """Create a player row with the given starting balance if one doesn't exist.

    Always populates the balance cache so subsequent reads hit memory.
    """
    with _write_tx() as conn:
        row = conn.execute(
            """
            INSERT INTO players(player_id, balance) VALUES (?, ?)
            ON CONFLICT(player_id) DO UPDATE SET balance = balance
            RETURNING balance
            """,
            (player_id, balance),
        ).fetchone()
    _balance[player_id] = int(row["balance"])
    _balance_loaded_for.add(player_id)


def get_balance(player_id: str) -> int:
    """Return the cached balance for `player_id`, reading from disk if needed.

    Raises:
        ValueError: when the player doesn't exist. Callers that may be hitting
            a fresh player should call `ensure_player` first.
    """
    if player_id in _balance_loaded_for:
        return _balance[player_id]
    with _read_tx() as conn:
        row = conn.execute(
            "SELECT balance FROM players WHERE player_id = ?", (player_id,)
        ).fetchone()
    if not row:
        raise ValueError(f"Player {player_id} does not exist")
    _balance[player_id] = int(row["balance"])
    _balance_loaded_for.add(player_id)
    return _balance[player_id]


def change_balance(player_id: str, delta: int) -> int:
    """Atomically add `delta` to a player's balance and return the new total.

    Uses ``UPDATE ... RETURNING`` so the new balance comes back in a single
    round-trip with the write itself (no follow-up SELECT).
    """
    with _write_tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO players(player_id, balance) VALUES (?, 0)",
            (player_id,),
        )
        row = conn.execute(
            "UPDATE players SET balance = balance + ? WHERE player_id = ? RETURNING balance",
            (delta, player_id),
        ).fetchone()
    new_balance = int(row["balance"])
    _balance[player_id] = new_balance
    _balance_loaded_for.add(player_id)
    return new_balance


def set_balance(player_id: str, amount: int) -> int:
    """Set a player's balance to an exact amount and return it."""
    with _write_tx() as conn:
        conn.execute(
            """
            INSERT INTO players(player_id, balance) VALUES (?, ?)
            ON CONFLICT(player_id) DO UPDATE SET balance = excluded.balance
            """,
            (player_id, amount),
        )
    _balance[player_id] = amount
    _balance_loaded_for.add(player_id)
    return amount


# ----------------------------------------------------------------------------
# Catalog mutators
# ----------------------------------------------------------------------------


def add_item(name: str, price: int, description: str = "", quantity: int = 0) -> int:
    """Add a catalog item (or no-op if it exists) and bump shop stock by `quantity`.

    `quantity = -1` sets the stock to infinite. Existing infinite stock is
    preserved even if the caller passes a finite value.
    """
    _load_items()  # so the catalog cache is current before we mutate
    with _write_tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO items(name, description, price) VALUES (?, ?, ?)",
            (name, description, price),
        )
        item_row = conn.execute(
            "SELECT id, name, description, price FROM items WHERE name = ?",
            (name,),
        ).fetchone()
        item_id = int(item_row["id"])

        if quantity == -1:
            new_quantity = -1
        else:
            existing = conn.execute(
                "SELECT quantity FROM shop_stock WHERE item_id = ?", (item_id,)
            ).fetchone()
            if existing and existing["quantity"] == -1:
                new_quantity = -1
            else:
                new_quantity = (existing["quantity"] if existing else 0) + quantity

        conn.execute(
            "INSERT OR REPLACE INTO shop_stock(item_id, quantity) VALUES (?, ?)",
            (item_id, new_quantity),
        )

    item = _row_to_item(item_row)
    _put_item_cache(item)
    _set_shop_cache(item_id, item["name"], item["description"], item["price"], new_quantity)
    return item_id


def update_item(
    name: str,
    price: Optional[int] = None,
    description: Optional[str] = None,
) -> None:
    """Update the price and/or description of an existing catalog item."""
    if price is None and description is None:
        raise ValueError("Either price or description must be provided")

    item = get_item_by_name(name)
    if not item:
        raise ValueError(f"Item {name} does not exist")

    fields: List[str] = []
    values: List[object] = []
    if price is not None:
        fields.append("price = ?")
        values.append(price)
    if description is not None:
        fields.append("description = ?")
        values.append(description)
    values.append(item["id"])

    with _write_tx() as conn:
        conn.execute(
            f"UPDATE items SET {', '.join(fields)} WHERE id = ?",
            tuple(values),
        )

    if price is not None:
        item["price"] = int(price)
    if description is not None:
        item["description"] = description
    if _shop_loaded and item["id"] in _shop_by_id:
        shop_entry = _shop_by_id[item["id"]]
        if price is not None:
            shop_entry["price"] = int(price)
        if description is not None:
            shop_entry["description"] = description


def set_shop_stock(item_name: str, quantity: int) -> None:
    """Overwrite the stock quantity for an item. `quantity = -1` means infinite."""
    item = get_item_by_name(item_name)
    if not item:
        raise ValueError(f"Item {item_name} does not exist")
    with _write_tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO shop_stock(item_id, quantity) VALUES (?, ?)",
            (item["id"], quantity),
        )
    _set_shop_cache(item["id"], item["name"], item["description"], item["price"], quantity)


# ----------------------------------------------------------------------------
# Inventory
# ----------------------------------------------------------------------------


def _load_inventory(player_id: str) -> Dict[str, int]:
    """Return (and lazily populate) the inventory cache for one player."""
    cached = _inventory.get(player_id)
    if cached is not None:
        return cached
    with _read_tx() as conn:
        rows = conn.execute(
            """
            SELECT items.name, inventory.quantity
            FROM inventory
            JOIN items ON inventory.item_id = items.id
            WHERE inventory.player_id = ?
            ORDER BY items.name
            """,
            (player_id,),
        ).fetchall()
    cached = {row["name"]: int(row["quantity"]) for row in rows}
    _inventory[player_id] = cached
    return cached


def get_inventory(player_id: str) -> Dict[str, int]:
    """Return a copy of the player's inventory as ``{item_name -> quantity}``."""
    return dict(_load_inventory(player_id))


def _apply_inventory_delta(player_id: str, item_name: str, delta: int) -> None:
    """Update the cached inventory for one player by `delta`, dropping zero entries."""
    if player_id not in _inventory:
        return
    inv = _inventory[player_id]
    new_qty = inv.get(item_name, 0) + delta
    if new_qty > 0:
        inv[item_name] = new_qty
    else:
        inv.pop(item_name, None)


def add_inventory_item(player_id: str, item_name: str, quantity: int) -> None:
    """Add `quantity` of `item_name` to `player_id`'s inventory."""
    if quantity < 1:
        raise ValueError("Quantity must be at least 1")
    item = get_item_by_name(item_name)
    if not item:
        raise ValueError(f"Item {item_name} does not exist")
    with _write_tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO players(player_id, balance) VALUES (?, 0)",
            (player_id,),
        )
        conn.execute(
            """
            INSERT INTO inventory(player_id, item_id, quantity) VALUES (?, ?, ?)
            ON CONFLICT(player_id, item_id) DO UPDATE SET
                quantity = quantity + excluded.quantity
            """,
            (player_id, item["id"], quantity),
        )
    _apply_inventory_delta(player_id, item_name, quantity)


def remove_inventory_item(player_id: str, item_name: str, quantity: int) -> None:
    """Remove `quantity` of `item_name` from `player_id`'s inventory.

    The check-and-decrement runs inside one ``BEGIN IMMEDIATE`` transaction so
    two concurrent removals can't both succeed against the same insufficient
    stock.
    """
    if quantity < 1:
        raise ValueError("Quantity must be at least 1")
    item = get_item_by_name(item_name)
    if not item:
        raise ValueError(f"Item {item_name} does not exist")
    with _write_tx() as conn:
        row = conn.execute(
            "SELECT quantity FROM inventory WHERE player_id = ? AND item_id = ?",
            (player_id, item["id"]),
        ).fetchone()
        if not row or row["quantity"] < quantity:
            raise ValueError("Not enough quantity in inventory")
        remaining = int(row["quantity"]) - quantity
        if remaining > 0:
            conn.execute(
                "UPDATE inventory SET quantity = ? WHERE player_id = ? AND item_id = ?",
                (remaining, player_id, item["id"]),
            )
        else:
            conn.execute(
                "DELETE FROM inventory WHERE player_id = ? AND item_id = ?",
                (player_id, item["id"]),
            )
    _apply_inventory_delta(player_id, item_name, -quantity)


# ----------------------------------------------------------------------------
# Composite operations
# ----------------------------------------------------------------------------


def buy_item(player_id: str, item_name: str, quantity: int = 1) -> Dict[str, Any]:
    """Charge the player and move shop stock into their inventory atomically.

    The entire balance check, stock check, debit, stock decrement, and
    inventory insert happen inside a single ``BEGIN IMMEDIATE`` transaction,
    so concurrent purchases cannot double-spend stock or balance.

    Returns:
        ``{"player_id", "item_name", "quantity", "total_cost"}`` on success.
    """
    if quantity < 1:
        raise ValueError("Quantity must be at least 1")
    item = get_item_by_name(item_name)
    if not item:
        raise ValueError(f"Item {item_name} does not exist")

    total_cost = int(item["price"]) * quantity

    with _write_tx() as conn:
        stock = conn.execute(
            "SELECT quantity FROM shop_stock WHERE item_id = ?", (item["id"],)
        ).fetchone()
        if not stock:
            raise ValueError("Not enough stock in shop")
        stock_qty = int(stock["quantity"])
        if stock_qty != -1 and stock_qty < quantity:
            raise ValueError("Not enough stock in shop")

        balance_row = conn.execute(
            "UPDATE players SET balance = balance - ? "
            "WHERE player_id = ? AND balance >= ? "
            "RETURNING balance",
            (total_cost, player_id, total_cost),
        ).fetchone()
        if balance_row is None:
            # Either the player doesn't exist or doesn't have enough credits.
            exists = conn.execute(
                "SELECT 1 FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            raise ValueError(
                "Insufficient balance" if exists else f"Player {player_id} does not exist"
            )
        new_balance = int(balance_row["balance"])

        new_stock_qty = stock_qty
        if stock_qty != -1:
            new_stock_qty = stock_qty - quantity
            conn.execute(
                "UPDATE shop_stock SET quantity = ? WHERE item_id = ?",
                (new_stock_qty, item["id"]),
            )

        conn.execute(
            """
            INSERT INTO inventory(player_id, item_id, quantity) VALUES (?, ?, ?)
            ON CONFLICT(player_id, item_id) DO UPDATE SET
                quantity = quantity + excluded.quantity
            """,
            (player_id, item["id"], quantity),
        )

    _balance[player_id] = new_balance
    _balance_loaded_for.add(player_id)
    _apply_inventory_delta(player_id, item_name, quantity)
    if _shop_loaded and item["id"] in _shop_by_id:
        _shop_by_id[item["id"]]["quantity"] = new_stock_qty

    return {
        "player_id": player_id,
        "item_name": item_name,
        "quantity": quantity,
        "total_cost": total_cost,
    }


def transfer_item(
    seller_id: str,
    buyer_id: str,
    item_name: str,
    quantity: int,
    total_price: int,
) -> None:
    """Atomically move items seller→buyer and credits buyer→seller.

    All checks (seller has the items, buyer has the funds) and all mutations
    (inventory move plus two balance adjustments) execute inside one
    ``BEGIN IMMEDIATE`` transaction; partial failure cannot leave either party
    short.
    """
    if quantity < 1:
        raise ValueError("Quantity must be at least 1")
    if total_price < 0:
        raise ValueError("Price must be non-negative")
    item = get_item_by_name(item_name)
    if not item:
        raise ValueError(f"Item {item_name} does not exist")

    with _write_tx() as conn:
        # Ensure both players exist before the checks.
        conn.execute(
            "INSERT OR IGNORE INTO players(player_id, balance) VALUES (?, 0)",
            (seller_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO players(player_id, balance) VALUES (?, 0)",
            (buyer_id,),
        )

        seller_inv = conn.execute(
            "SELECT quantity FROM inventory WHERE player_id = ? AND item_id = ?",
            (seller_id, item["id"]),
        ).fetchone()
        if not seller_inv or seller_inv["quantity"] < quantity:
            raise ValueError("Seller does not have enough of that item.")

        # Atomic debit: only succeeds if the buyer can afford it.
        buyer_row = conn.execute(
            "UPDATE players SET balance = balance - ? "
            "WHERE player_id = ? AND balance >= ? "
            "RETURNING balance",
            (total_price, buyer_id, total_price),
        ).fetchone()
        if buyer_row is None:
            raise ValueError("Buyer has insufficient funds to complete this sale.")
        new_buyer_balance = int(buyer_row["balance"])

        seller_balance_row = conn.execute(
            "UPDATE players SET balance = balance + ? WHERE player_id = ? RETURNING balance",
            (total_price, seller_id),
        ).fetchone()
        new_seller_balance = int(seller_balance_row["balance"])

        remaining = int(seller_inv["quantity"]) - quantity
        if remaining > 0:
            conn.execute(
                "UPDATE inventory SET quantity = ? WHERE player_id = ? AND item_id = ?",
                (remaining, seller_id, item["id"]),
            )
        else:
            conn.execute(
                "DELETE FROM inventory WHERE player_id = ? AND item_id = ?",
                (seller_id, item["id"]),
            )
        conn.execute(
            """
            INSERT INTO inventory(player_id, item_id, quantity) VALUES (?, ?, ?)
            ON CONFLICT(player_id, item_id) DO UPDATE SET
                quantity = quantity + excluded.quantity
            """,
            (buyer_id, item["id"], quantity),
        )

    _balance[buyer_id] = new_buyer_balance
    _balance[seller_id] = new_seller_balance
    _balance_loaded_for.add(buyer_id)
    _balance_loaded_for.add(seller_id)
    _apply_inventory_delta(seller_id, item_name, -quantity)
    _apply_inventory_delta(buyer_id, item_name, quantity)


# Initialize the schema at import. Safe under repeated import because every
# DDL statement uses ``CREATE TABLE IF NOT EXISTS``.
init_db()
