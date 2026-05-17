"""Concurrency tests for shop.py.

These verify the two reliability claims the WAL + BEGIN IMMEDIATE design rests on:

  1. No "database is locked" errors under heavy contention. WAL mode plus the
     5-second `busy_timeout` should let writers serialize cleanly.

  2. Race-free read-modify-write. Conditional `UPDATE ... RETURNING` and
     `BEGIN IMMEDIATE` together should prevent any thread from observing or
     consuming stale balance/stock.
"""

import threading

from tests._base import ShopTestBase


class ShopConcurrency(ShopTestBase):
    def test_concurrent_change_balance_no_drift(self) -> None:
        """1600 +1/-1 flips across 8 threads must net to zero."""
        self.shop.ensure_player("alice", balance=10_000)
        errors: list[Exception] = []

        def hammer() -> None:
            try:
                for _ in range(100):
                    self.shop.change_balance("alice", 1)
                    self.shop.change_balance("alice", -1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"unexpected errors: {errors[:3]}")
        self.assertEqual(self.shop.get_balance("alice"), 10_000)

    def test_concurrent_buys_never_oversell(self) -> None:
        """Two threads racing to buy must never push stock below zero or
        exceed the funding either of them has."""
        self.shop.add_item("apple", price=1, quantity=1000)
        self.shop.ensure_player("alice", balance=1_000_000)
        self.shop.ensure_player("bob", balance=1_000_000)

        success_count = [0, 0]

        def buy(idx: int, who: str) -> None:
            for _ in range(50):
                try:
                    self.shop.buy_item(who, "apple", 1)
                    success_count[idx] += 1
                except ValueError:
                    # Expected when balance or stock runs out; not an error.
                    pass

        threads = [
            threading.Thread(target=buy, args=(0, "alice")),
            threading.Thread(target=buy, args=(1, "bob")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = sum(success_count)
        self.assertEqual(total, 100, "every buy attempt should have succeeded")

        remaining = next(i for i in self.shop.get_shop() if i["name"] == "apple")["quantity"]
        self.assertEqual(remaining, 1000 - total, "stock accounting drift")
        self.assertEqual(
            self.shop.get_inventory("alice").get("apple", 0), success_count[0]
        )
        self.assertEqual(
            self.shop.get_inventory("bob").get("apple", 0), success_count[1]
        )

    def test_concurrent_transfers_balance_conservation(self) -> None:
        """Money should be conserved across many concurrent player-to-player sales."""
        self.shop.add_item("token", price=1, quantity=200)
        self.shop.ensure_player("alice", balance=10_000)
        self.shop.ensure_player("bob", balance=10_000)
        self.shop.buy_item("alice", "token", 100)

        before = self.shop.get_balance("alice") + self.shop.get_balance("bob")

        def transfer() -> None:
            for _ in range(20):
                try:
                    self.shop.transfer_item("alice", "bob", "token", 1, 5)
                except ValueError:
                    pass

        threads = [threading.Thread(target=transfer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        after = self.shop.get_balance("alice") + self.shop.get_balance("bob")
        self.assertEqual(before, after, "total credits must be conserved")
        # Item conservation too.
        alice_items = self.shop.get_inventory("alice").get("token", 0)
        bob_items = self.shop.get_inventory("bob").get("token", 0)
        self.assertEqual(alice_items + bob_items, 100)


if __name__ == "__main__":
    import unittest
    unittest.main()
