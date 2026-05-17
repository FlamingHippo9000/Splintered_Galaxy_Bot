"""Shared helpers for environment-variable loading.

Loading is performed once on import: subsequent imports reuse the values
already placed in `os.environ` by `python-dotenv`.
"""

import os
from dotenv import find_dotenv, load_dotenv  # pyright: ignore[reportMissingImports]

_dotenv_path = find_dotenv()
if not _dotenv_path:
    _dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_dotenv_path)


def get_env(name: str) -> str:
    """Return the value of a required environment variable.

    Raises:
        ValueError: when the variable is missing or empty. Failing fast at
            startup is preferred over surfacing a confusing error later.
    """
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value
