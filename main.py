"""
STOX Options — daytrading engine entry point.

Usage:
    python main.py               # run the engine (paper or live per .env)
    python main.py --dry-run     # full pipeline, but no orders are sent
    python main.py --once        # single tick (useful for cron/debugging)
"""
from __future__ import annotations

import argparse
import sys

from config import Config
from engine import TradingEngine
from trading.broker import get_account, validate_credentials
from utils.logger import get_logger

logger = get_logger("main")

BANNER = r"""
╔════════════════════════════════════════════════════╗
║   STOX — Intraday Options Trading Engine           ║
║   Underlyings : {unds:<35}║
║   Daily target: +${target:<10,.0f} max loss: -${loss:<8,.0f}║
║   Mode        : {mode:<35}║
╚════════════════════════════════════════════════════╝
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="STOX intraday options engine")
    parser.add_argument("--dry-run", action="store_true", help="signals only, no orders")
    parser.add_argument("--once", action="store_true", help="run a single tick and exit")
    args = parser.parse_args()

    if not Config.ALPACA_API_KEY or not Config.ALPACA_API_SECRET:
        print(
            "\nERROR: Alpaca API keys not set.\n"
            "Copy .env.example to .env and add keys from https://alpaca.markets\n"
        )
        sys.exit(1)

    ok, msg = validate_credentials()
    if not ok:
        print(f"\nERROR: Alpaca authentication failed — {msg}")
        print("Run:  python check_auth.py  for a full diagnostic.\n")
        sys.exit(1)
    logger.info(msg)

    account = get_account()
    if account["options_level"] < 1 and not args.dry_run:
        print(
            "\nERROR: this account has no options trading permission "
            f"(level={account['options_level']}).\n"
            "Enable options trading in your Alpaca dashboard first.\n"
        )
        sys.exit(1)

    print(
        BANNER.format(
            unds=",".join(Config.UNDERLYINGS),
            target=Config.DAILY_PROFIT_TARGET,
            loss=Config.DAILY_MAX_LOSS,
            mode="DRY RUN" if args.dry_run else Config.ALPACA_MODE.upper(),
        )
    )

    engine = TradingEngine(dry_run=args.dry_run)
    if args.once:
        engine.tick()
        print(engine.status())
        return

    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("Shutting down…")
        engine.stop()


if __name__ == "__main__":
    main()
