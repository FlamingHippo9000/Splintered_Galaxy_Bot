"""Shared test base that redirects shop.py to a fresh temp DB per test.

Importing `Include.shop` at module-load time triggers `init_db()` against the
real `Include/shop.db`. The base class below points the module at a throwaway
file inside `setUp`, wipes every cache, and re-runs the schema bootstrap —
giving each test method a clean SQLite slate without touching production data.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of where unittest is invoked from.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from Include import shop  # noqa: E402  -- import after sys.path tweak


class ShopTestBase(unittest.TestCase):
    """unittest.TestCase that gives each test a private SQLite file."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="sg_shop_test_")
        # Close whatever connection the previous test (or import-time init_db) left
        # so we can swap DB_FILE without leaking a handle to the wrong path.
        shop.close()
        shop.DB_FILE = Path(self._tmpdir) / "shop.db"
        shop._reset_caches()
        shop.init_db()
        self.shop = shop

    def tearDown(self) -> None:
        self.shop.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
