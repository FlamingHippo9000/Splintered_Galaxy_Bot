"""Main entrypoint for the Splintered Galaxy Discord bot."""

import os
import signal
import ssl
import sys
import certifi

# Force Python to use certifi's certificate bundle for TLS validation.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

import Include.SplinteredGalaxyBot as SplinteredGalaxyBot
from Include import shop

# Create a reusable SSL context for any outbound HTTPS requests.
ssl_context = ssl.create_default_context(cafile=certifi.where())


def _shutdown(signum, frame) -> None:
    """Close the shop DB connection cleanly on SIGINT/SIGTERM."""
    try:
        shop.close()
    finally:
        sys.exit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        SplinteredGalaxyBot.run_discord_bot()
    finally:
        shop.close()
