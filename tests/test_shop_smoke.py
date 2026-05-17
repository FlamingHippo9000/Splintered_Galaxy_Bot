"""Smoke tests for the shop/cache layer — mirrors the manual smoke run.

These exercise the happy paths of every public mutator and reader. They are
the first thing to run after touching `Include/shop.py`.
"""

from tests._base import ShopTestBase


class ShopHappyPath(ShopTestBase):
    def test_ensure_and_read_balance(self) -> None:
        self.shop.ensure_player("alice", balance=1000)
        self.assertEqual(self.shop.get_balance("alice"), 1000)

    def test_change_balance_returns_new_total(self) -> None:
        self.shop.ensure_player("alice", balance=1000)
        self.assertEqual(self.shop.change_balance("alice", 250), 1250)
        self.assertEqual(self.shop.get_balance("alice"), 1250)

    def test_set_balance_overwrites(self) -> None:
        self.shop.ensure_player("alice", balance=1000)
        self.assertEqual(self.shop.set_balance("alice", 500), 500)
        self.assertEqual(self.shop.get_balance("alice"), 500)

    def test_add_item_seeds_catalog_and_shop(self) -> None:
        self.shop.add_item("sword", price=100, description="sharp", quantity=5)
        self.assertEqual(self.shop.get_item_by_name("sword")["price"], 100)
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "sword")
        self.assertEqual(shop_row["quantity"], 5)

    def test_add_item_merges_quantity(self) -> None:
        self.shop.add_item("sword", price=100, quantity=5)
        self.shop.add_item("sword", price=100, quantity=3)
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "sword")
        self.assertEqual(shop_row["quantity"], 8)

    def test_infinite_stock_persists_across_finite_add(self) -> None:
        self.shop.add_item("sword", price=100, quantity=-1)
        # A subsequent finite add must NOT downgrade infinite stock.
        self.shop.add_item("sword", price=100, quantity=3)
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "sword")
        self.assertEqual(shop_row["quantity"], -1)

    def test_set_shop_stock_overwrites(self) -> None:
        self.shop.add_item("sword", price=100, quantity=-1)
        self.shop.set_shop_stock("sword", 10)
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "sword")
        self.assertEqual(shop_row["quantity"], 10)

    def test_update_item_patches_both_caches(self) -> None:
        self.shop.add_item("sword", price=100, description="sharp", quantity=5)
        self.shop.update_item("sword", price=200, description="sharper")
        self.assertEqual(self.shop.get_item_by_name("sword")["price"], 200)
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "sword")
        self.assertEqual(shop_row["price"], 200)
        self.assertEqual(shop_row["description"], "sharper")

    def test_buy_item_atomic_state_change(self) -> None:
        self.shop.add_item("sword", price=100, quantity=10)
        self.shop.ensure_player("alice", balance=1000)
        result = self.shop.buy_item("alice", "sword", 2)
        self.assertEqual(result["total_cost"], 200)
        self.assertEqual(self.shop.get_balance("alice"), 800)
        self.assertEqual(self.shop.get_inventory("alice")["sword"], 2)
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "sword")
        self.assertEqual(shop_row["quantity"], 8)

    def test_insufficient_balance_rejected_atomically(self) -> None:
        self.shop.add_item("sword", price=100, quantity=10)
        self.shop.ensure_player("alice", balance=50)
        with self.assertRaises(ValueError) as ctx:
            self.shop.buy_item("alice", "sword", 1)
        self.assertIn("Insufficient", str(ctx.exception))
        # No partial mutation.
        self.assertEqual(self.shop.get_balance("alice"), 50)
        self.assertEqual(self.shop.get_inventory("alice"), {})
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "sword")
        self.assertEqual(shop_row["quantity"], 10)

    def test_transfer_item_moves_goods_and_credits(self) -> None:
        self.shop.add_item("sword", price=100, quantity=10)
        self.shop.ensure_player("alice", balance=1000)
        self.shop.buy_item("alice", "sword", 2)
        self.shop.ensure_player("bob", balance=500)

        self.shop.transfer_item("alice", "bob", "sword", quantity=1, total_price=300)

        self.assertEqual(self.shop.get_balance("alice"), 800 - 0 + 300)  # 1100
        self.assertEqual(self.shop.get_balance("bob"), 500 - 300)        # 200
        self.assertEqual(self.shop.get_inventory("alice")["sword"], 1)
        self.assertEqual(self.shop.get_inventory("bob")["sword"], 1)

    def test_transfer_item_insufficient_funds_rolls_back(self) -> None:
        self.shop.add_item("sword", price=100, quantity=10)
        self.shop.ensure_player("alice", balance=1000)
        self.shop.buy_item("alice", "sword", 2)
        self.shop.ensure_player("bob", balance=200)

        with self.assertRaises(ValueError):
            self.shop.transfer_item("alice", "bob", "sword", quantity=1, total_price=99_999)

        # Both sides remain exactly as they were.
        self.assertEqual(self.shop.get_balance("alice"), 800)
        self.assertEqual(self.shop.get_balance("bob"), 200)
        self.assertEqual(self.shop.get_inventory("alice")["sword"], 2)
        self.assertNotIn("sword", self.shop.get_inventory("bob"))

    def test_currency_icon_round_trip(self) -> None:
        self.assertIsNone(self.shop.get_currency_icon())
        self.shop.set_currency_icon("https://example.com/coin.png")
        self.assertEqual(self.shop.get_currency_icon(), "https://example.com/coin.png")
        self.shop.clear_currency_icon()
        self.assertIsNone(self.shop.get_currency_icon())

    def test_state_persists_across_reopen(self) -> None:
        self.shop.add_item("sword", price=100, quantity=10)
        self.shop.ensure_player("alice", balance=1000)
        self.shop.buy_item("alice", "sword", 2)
        # Drop caches AND the connection, then ensure we read the same values
        # back from disk.
        self.shop.close()
        self.shop._reset_caches()
        self.assertEqual(self.shop.get_balance("alice"), 800)
        self.assertEqual(self.shop.get_inventory("alice")["sword"], 2)
        shop_row = next(i for i in self.shop.get_shop() if i["name"] == "sword")
        self.assertEqual(shop_row["quantity"], 8)


if __name__ == "__main__":
    import unittest
    unittest.main()
