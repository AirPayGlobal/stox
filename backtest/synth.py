"""
Deterministic synthetic intraday bars for OFFLINE testing and illustrative
report generation.

This exists so the validation harness and its tests can run with NO market-data
credentials. Data produced here is a seeded random walk with an intraday drift
regime per day — it is NOT real market data and must never be presented as a
validation result. Any report built from it is labelled "SYNTHETIC / ILLUSTRATIVE".
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

ET = "America/New_York"


def _seed(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:4], "big")


def synth_day(symbol: str, day: str, base_price: float = 550.0,
              regime: str = "auto") -> pd.DataFrame:
    """One RTH session (09:30-16:00 ET) of 1-minute OHLCV bars.

    regime: 'trend_up' | 'trend_down' | 'chop' | 'auto' (derived from the seed).
    Deterministic in (symbol, day)."""
    rng = np.random.default_rng(_seed(symbol, day, regime))
    idx = pd.date_range(f"{day} 09:30", f"{day} 15:59", freq="1min", tz=ET)
    n = len(idx)

    if regime == "auto":
        regime = ["trend_up", "trend_down", "chop", "chop"][_seed(symbol, day) % 4]
    drift = {"trend_up": 0.00015, "trend_down": -0.00015, "chop": 0.0}[regime]
    vol = 0.0006 + 0.0004 * rng.random()  # per-bar sigma, varies day to day

    rets = rng.normal(drift, vol, n)
    close = base_price * np.exp(np.cumsum(rets))
    openp = np.concatenate([[base_price], close[:-1]])
    wig = np.abs(rng.normal(0, vol, n)) * close
    high = np.maximum(openp, close) + wig
    low = np.minimum(openp, close) - wig
    volume = rng.integers(500, 5000, n)
    return pd.DataFrame(
        {"open": openp, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def synth_history(symbol: str, days: int, base_price: float = 550.0,
                  end: str = "2026-08-15") -> pd.DataFrame:
    """`days` consecutive weekday sessions of 1-minute bars (deterministic)."""
    end_ts = pd.Timestamp(end)
    sessions = pd.bdate_range(end=end_ts, periods=days)
    frames = [synth_day(symbol, d.strftime("%Y-%m-%d"), base_price) for d in sessions]
    return pd.concat(frames)
