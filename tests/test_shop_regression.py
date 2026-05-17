"""Regression tests for shop.py error paths, validation, and cache invariants.

Each test pins a specific behavior that has either been broken before or that
is load-bearing for callers. If one of these fails, something in the data
layer changed — investigate before "fixing the test".
"""

from tests._base import ShopTestBase


class BalanceErrors(ShopTestBase):
    def test_get_balance_unknown_player_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.shop.get_balance("nobody")
        self.assertIn("nobody", str(ctx.exception))

    def test_change_balance_creates_player_implicitly(self) -> None:
        # change_balance should auto-create rather than error on a fresh player.
        self.assertEqual(self.shop.change_balance("ghost", 42), 42)
        self.assertEqual(self.shop.get_balance("ghost"), 42)


class InventoryValidation(ShopTestBase):
    def test_add_inventory_zero_rejected(self) -> None:
        self.shop.add_item("sword", price=1, quantity=10)
        with self.assertRaises(ValueError):
            self.shop.add_inventory_item("alice", "sword", 0)

    def test_add_inventory_negative_rejected(self) -> None:
        self.shop.add_item("sword", price=1, quantity=10)
        with self.assertRaises(ValueError):
            self.shop.add_inventory_item("alice", "sword", -1)

    def test_add_inventory_unknown_item_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.shop.add_inventory_item("alice", "missing", 1)
        self.assertIn("missing", str(ctx.exception))

    def test_remove_underflow_rejected_and_state_unchanged(self) -> None:
        self.shop.add_item("sword", price=1, quantity=10)
        self.shop.ensure_player("alice")
        self.shop.add_inventory_item("alice", "sword", 2)
        with self.assertRaises(ValueError):
            self.shop.remove_inventory_item("alice", "sword", 5)
        self.assertEqual(self.shop.get_inventory("alice")["sword"], 2)

    def test_remove_exact_zero_deletes_inventory_row(self) -> None:
        self.shop.add_item("sword", price=1, quantity=10)
        self.shop.ensure_player("alice")
        self.shop.add_inventory_item("alice", "sword", 2)
        self.shop.remove_inventory_item("alice", "sword", 2)
        self.assertNotIn("sword", self.shop.get_inventory("alice"))


class CatalogValidation(ShopTestBase):
    def test_update_item_requires_a_field(self) -> None:
        self.shop.add_item("sword", price=1, quantity=1)
        with self.assertRaises(ValueError):
            self.shop.update_item("sword")

    def test_update_unknown_item_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.shop.update_item("nope", price=5)
        self.assertIn("nope", str(ctx.exception))

    def test_set_shop_stock_unknown_item_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.shop.set_shop_stock("nope", 5)


class BuyErrors(ShopTestBase):
    def test_buy_unknown_item_rejected(self) -> None:
        self.shop.ensure_player("alice", balance=1000)
        with self.assertRaises(ValueError):
            self.shop.buy_item("alice", "missing", 1)

    def test_buy_unstocked_item_rejected(self) -> None:
        # Item exists in the catalog but never had stock seeded.
        with self.shop._write_tx() as conn:
            conn.execute(
                "INSERT INTO items(name, description, price) VALUES (?, ?, ?)",
                ("phantom", "", 10),
            )
        self.shop._reset_caches()
        self.shop.ensure_player("alice", balance=1000)
        with self.assertRaises(ValueError) as ctx:
            self.shop.buy_item("alice", "phantom", 1)
        self.assertIn("stock", str(ctx.exception).lower())

    def test_buy_insufficient_stock_rejected(self) -> None:
        self.shop.add_item("sword", price=1, quantity=2)
        self.shop.ensure_player("alice", balance=1000)
        with self.assertRaises(ValueError):
            self.shop.buy_item("alice", "sword", 5)
        self.assertEqual(self.shop.get_balance("alice"), 1000)

    def test_buy_quantity_below_one_rejected(self) -> None:
        self.shop.add_item("sword", price=1, quantity=10)
        self.shop.ensure_player("alice", balance=1000)
        with self.assertRaises(ValueError):
            self.shop.buy_item("alice", "sword", 0)

    def test_buy_with_infinite_stock_does_not_decrement(self) -> None:
        self.shop.add_item("scroll", price=10, quantity=-1)
        self.shop.ensure_player("alice", balance=1000)
        self.shop.buy_item("alice", "scroll", 7)
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "scroll")
        self.assertEqual(shop_row["quantity"], -1)
        self.assertEqual(self.shop.get_balance("alice"), 1000 - 70)


class TransferErrors(ShopTestBase):
    def test_transfer_unknown_item_rejected(self) -> None:
        self.shop.ensure_player("alice", balance=100)
        self.shop.ensure_player("bob", balance=100)
        with self.assertRaises(ValueError):
            self.shop.transfer_item("alice", "bob", "missing", quantity=1, total_price=10)

    def test_transfer_quantity_below_one_rejected(self) -> None:
        self.shop.add_item("sword", price=1, quantity=1)
        with self.assertRaises(ValueError):
            self.shop.transfer_item("alice", "bob", "sword", quantity=0, total_price=10)

    def test_transfer_negative_price_rejected(self) -> None:
        self.shop.add_item("sword", price=1, quantity=1)
        with self.assertRaises(ValueError):
            self.shop.transfer_item("alice", "bob", "sword", quantity=1, total_price=-1)

    def test_transfer_seller_insufficient_items_rolls_back(self) -> None:
        self.shop.add_item("sword", price=1, quantity=10)
        self.shop.ensure_player("alice")
        self.shop.ensure_player("bob", balance=500)
        self.shop.add_inventory_item("alice", "sword", 1)

        with self.assertRaises(ValueError):
            self.shop.transfer_item("alice", "bob", "sword", quantity=5, total_price=100)

        # Buyer wasn't charged, seller still has the item.
        self.assertEqual(self.shop.get_balance("bob"), 500)
        self.assertEqual(self.shop.get_inventory("alice")["sword"], 1)
        self.assertNotIn("sword", self.shop.get_inventory("bob"))


class CacheInvariants(ShopTestBase):
    def test_get_inventory_returns_a_copy(self) -> None:
        """Mutating the returned dict must not leak into the cache."""
        self.shop.add_item("sword", price=1, quantity=10)
        self.shop.ensure_player("alice")
        self.shop.add_inventory_item("alice", "sword", 3)
        snapshot = self.shop.get_inventory("alice")
        snapshot["sword"] = 9999
        snapshot["fake"] = 1
        fresh = self.shop.get_inventory("alice")
        self.assertEqual(fresh, {"sword": 3})

    def test_get_items_sorted_by_name(self) -> None:
        for name in ["zebra", "apple", "mango"]:
            self.shop.add_item(name, price=1, quantity=1)
        names = [i["name"] for i in self.shop.get_items()]
        self.assertEqual(names, sorted(names))

    def test_get_shop_sorted_by_name(self) -> None:
        for name in ["zebra", "apple", "mango"]:
            self.shop.add_item(name, price=1, quantity=1)
        names = [i["name"] for i in self.shop.get_shop()]
        self.assertEqual(names, sorted(names))

    def test_inventory_cache_isolated_per_player(self) -> None:
        self.shop.add_item("sword", price=1, quantity=10)
        self.shop.ensure_player("alice")
        self.shop.ensure_player("bob")
        self.shop.add_inventory_item("alice", "sword", 5)
        # Bob's inventory must not be polluted.
        self.assertEqual(self.shop.get_inventory("bob"), {})
        self.assertEqual(self.shop.get_inventory("alice")["sword"], 5)

    def test_items_cache_lazy_load_only_runs_once(self) -> None:
        """Repeated reads must not re-query the DB after the first load."""
        self.shop.add_item("sword", price=1, quantity=1)
        # First read loads the catalog cache.
        self.shop.get_items()
        # Sneak in a row directly through the connection; if a subsequent
        # get_items() call hits the DB, this would appear in the result.
        with self.shop._write_tx() as conn:
            conn.execute(
                "INSERT INTO items(name, description, price) VALUES (?, ?, ?)",
                ("hidden", "", 0),
            )
        names = [i["name"] for i in self.shop.get_items()]
        self.assertIn("sword", names)
        self.assertNotIn("hidden", names, "cache should not see direct DB writes")

    def test_currency_icon_cached_after_first_read(self) -> None:
        """Once read, currency_icon returns from memory without touching the DB."""
        self.shop.set_currency_icon("https://example.com/a.png")
        self.assertEqual(self.shop.get_currency_icon(), "https://example.com/a.png")
        # Forge a direct write the cache won't see.
        with self.shop._write_tx() as conn:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                ("https://example.com/b.png", "currency_icon"),
            )
        self.assertEqual(
            self.shop.get_currency_icon(),
            "https://example.com/a.png",
            "in-memory cache should still serve the original value",
        )


if __name__ == "__main__":
    import unittest
    unittest.main()
