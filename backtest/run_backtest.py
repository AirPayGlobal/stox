"""
Standalone backtest runner.

Usage:
    python backtest/run_backtest.py                 # backtest entire watchlist
    python backtest/run_backtest.py AAPL MSFT NVDA  # specific symbols
    python backtest/run_backtest.py --days 365       # custom lookback
"""
from __future__ import annotations

import sys
import os
import argparse

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from data.fetcher import fetch_bars, fetch_batch
from backtest.engine import BacktestEngine
from utils.logger import get_logger

logger = get_logger("backtest_runner")


def run(symbols: list[str], lookback_days: int = 500) -> None:
    engine = BacktestEngine(initial_capital=Config.INITIAL_CAPITAL)

    results = []
    data = fetch_batch(symbols, lookback_days=lookback_days)

    for symbol, df in data.items():
        result = engine.run(symbol, df)
        results.append(result)
        print(result.summary())

    if not results:
        print("No results — check your API keys and symbols.")
        return

    # Aggregate summary
    total_trades = sum(len(r.trades) for r in results)
    avg_return = sum(r.total_return for r in results) / len(results)
    best = max(results, key=lambda r: r.total_return)
    worst = min(results, key=lambda r: r.total_return)

    print(f"\n{'='*55}")
    print(f"  AGGREGATE RESULTS ({len(results)} symbols)")
    print(f"{'='*55}")
    print(f"  Total Trades      : {total_trades}")
    print(f"  Avg Return        : {avg_return:.2%}")
    print(f"  Best Symbol       : {best.symbol} ({best.total_return:.2%})")
    print(f"  Worst Symbol      : {worst.symbol} ({worst.total_return:.2%})")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument("symbols", nargs="*", help="Symbols to backtest (default: watchlist)")
    parser.add_argument("--days", type=int, default=500, help="Lookback days (default: 500)")
    args = parser.parse_args()

    symbols = args.symbols if args.symbols else Config.WATCHLIST[:10]  # first 10 by default
    run(symbols, lookback_days=args.days)
