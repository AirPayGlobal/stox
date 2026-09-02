"""
CLI: run the ETF strategy validation on real adjusted daily bars.

    python -m portfolio.validate_etf                 # real bars (needs Alpaca keys)
    python -m portfolio.validate_etf --start 2016-01-01

Bars are real; option/equity fills are SIMULATED via the execution scenarios.
Requires ALPACA_API_KEY / ALPACA_API_SECRET in the environment for data access;
without them it exits with a clear message (no synthetic substitution for a
report that would be read as validation).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd

from config import Config
from portfolio.backtest import Sleeve
from portfolio.profiles import PROFILE_IDS, get_profile
from portfolio.strategy_validation import render_markdown, run_strategy_validation
from strategy import etf_relative_momentum as relmom
from strategy import etf_trend as trend

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC"]


def fetch_daily_adjusted(symbols: list, start: str) -> dict:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import Adjustment

    client = StockHistoricalDataClient(Config.ALPACA_API_KEY, Config.ALPACA_API_SECRET)
    start_dt = datetime.fromisoformat(start)
    bars = {}
    for sym in symbols:
        df = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=sym, timeframe=TimeFrame.Day,
            start=start_dt, adjustment=Adjustment.ALL)).df
        if df.empty:
            continue
        df = df.reset_index(level=0, drop=True)
        df.index = pd.DatetimeIndex([ts.tz_localize(None) if getattr(ts, "tzinfo", None) else ts
                                     for ts in df.index])
        bars[sym] = df[["open", "high", "low", "close", "volume"]]
    return bars


def main() -> None:
    ap = argparse.ArgumentParser(description="ETF strategy validation (real bars, simulated fills)")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--profile", default="", help="one profile id, or all four if omitted")
    args = ap.parse_args()

    if not (Config.ALPACA_API_KEY and Config.ALPACA_API_SECRET):
        sys.exit("No Alpaca data credentials — set ALPACA_API_KEY / ALPACA_API_SECRET.")

    bars = fetch_daily_adjusted(UNIVERSE, args.start)
    if "SPY" not in bars:
        sys.exit("Could not fetch SPY bars.")

    profiles = [get_profile(args.profile)] if args.profile else [get_profile(p) for p in PROFILE_IDS]
    sleeves = [Sleeve(trend), Sleeve(relmom)]
    v = run_strategy_validation(bars, profiles, sleeves, synthetic=False)
    print("# ETF strategy validation — REAL BARS (simulated fills)\n")
    print(render_markdown(v))


if __name__ == "__main__":
    main()
