"""
Alpaca credential validator — run this before starting the bot.

Usage:
    python check_auth.py

Checks that ALPACA_API_KEY / ALPACA_API_SECRET are set and valid by making a
live /v2/account request. Also prints the equivalent curl command for manual
testing.
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from config import Config  # noqa: E402 — load_dotenv must run first


def _mask(value: str) -> str:
    if not value or len(value) < 8:
        return "(not set)"
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def main() -> None:
    print("\n=== Alpaca Credential Check ===\n")

    key = Config.ALPACA_API_KEY
    secret = Config.ALPACA_API_SECRET
    mode = Config.ALPACA_MODE
    base_url = Config.alpaca_base_url()

    print(f"  ALPACA_MODE       : {mode}")
    print(f"  ALPACA_API_KEY    : {_mask(key)}")
    print(f"  ALPACA_API_SECRET : {_mask(secret)}")
    print(f"  Base URL          : {base_url}\n")

    if not key or not secret:
        print("ERROR: One or both API keys are missing.")
        print("  1. Copy .env.example to .env")
        print("  2. Add your keys from https://app.alpaca.markets/paper/dashboard/overview\n")
        sys.exit(1)

    print("Equivalent curl command for manual testing:")
    print(f'  curl -H "APCA-API-KEY-ID: {key}" \\')
    print(f'       -H "APCA-API-SECRET-KEY: {secret[:4]}..." \\')
    print(f"       {base_url}/v2/account\n")

    print("Testing connection to Alpaca...")
    try:
        from trading.alpaca_client import validate_credentials
        ok, msg = validate_credentials()
    except Exception as exc:
        print(f"ERROR: Unexpected error during check: {exc}\n")
        sys.exit(1)

    if ok:
        print(f"Authentication OK  —  {msg}\n")
    else:
        print(f"Authentication FAILED: {msg}")
        print("\nCommon fixes:")
        print("  - Make sure you copied the Paper Trading keys (not Live)")
        print("  - Re-generate keys at https://app.alpaca.markets/paper/dashboard/overview")
        print("  - Ensure ALPACA_MODE=paper in your .env when using paper keys\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
