"""
Dynamic Universe Scanner
=========================
Expands the static WATCHLIST by screening a broader ~140-stock extended
universe daily for momentum breakout conditions:

  ✓ Price above 50-day EMA        (uptrend confirmation)
  ✓ RSI 40–65                     (momentum, not overbought)
  ✓ Price above 4-week high       (breakout signal)   +2.0 pts
  ✓ Volume > 1.5× 20-day average  (institutional participation) +1.5 pts

Top-N candidates by score are merged into the daily scan universe alongside
the static WATCHLIST. Results cached 4 hours to limit API calls.
"""
from __future__ import annotations

import logging
import time
from typing import List

import numpy as np
import pandas as pd
import yfinance as yf

from config import Config

logger = logging.getLogger("universe")

# Extended universe beyond Config.WATCHLIST (~140 liquid large/mid-caps)
_EXTENDED = [
    # Technology
    "AMD", "INTC", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "ORCL", "IBM",
    "NOW", "SNOW", "DDOG", "ZS", "CRWD", "PANW", "NET", "UBER", "LYFT",
    "SPOT", "RBLX", "COIN", "SQ", "PYPL", "SHOP", "TTD", "TWLO",
    # Healthcare
    "ISRG", "EW", "SYK", "BSX", "MDT", "DXCM", "ALGN", "HOLX",
    "BIIB", "REGN", "VRTX", "MRNA", "GILD", "ILMN", "IQV",
    # Financials
    "GS", "MS", "C", "WFC", "BLK", "SCHW", "AXP", "USB", "PNC", "TFC", "COF",
    "SPGI", "MCO", "ICE", "CME", "CBOE", "AON", "MMC",
    # Consumer Discretionary
    "TGT", "LOW", "TJX", "ROST", "DG", "DLTR", "YUM", "DPZ", "CMG",
    "ABNB", "BKNG", "EXPE", "MAR", "HLT", "MGM",
    # Industrials
    "HON", "MMM", "GE", "EMR", "ETN", "PH", "ROK", "ITW",
    "LMT", "NOC", "GD", "BA", "CAT", "DE", "PCAR",
    # Energy
    "SLB", "HAL", "OXY", "EOG", "DVN", "MPC", "VLO", "PSX",
    # Materials / Utilities
    "FCX", "NEM", "APD", "SHW", "NUE", "AWK", "DUK", "SO",
    # Real Estate
    "AMT", "PLD", "EQIX", "CCI", "SPG", "AVB",
]

_cache: dict = {"candidates": [], "ts": 0.0}
_CACHE_TTL = 4 * 3600  # 4 hours


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    series = 100 - 100 / (1 + rs)
    val = series.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0


def _screen_symbol(df: pd.DataFrame, sym: str) -> float:
    """
    Score a single symbol on breakout criteria. Returns 0.0 if it fails
    the baseline filters (above EMA50, RSI in range).
    """
    if len(df) < 55:
        return 0.0

    close  = df["Close"].astype(float)
    high   = df["High"].astype(float)
    volume = df["Volume"].astype(float)

    ema50    = close.ewm(span=50, adjust=False).mean()
    vol_avg  = volume.rolling(20).mean()
    high_4w  = high.rolling(20).max().shift(1)  # 4-week high, prior day
    rsi_val  = _rsi(close)

    above_ema50 = float(close.iloc[-1]) > float(ema50.iloc[-1])
    rsi_ok      = 40 <= rsi_val <= 65

    if not (above_ema50 and rsi_ok):
        return 0.0

    score = 0.0
    if float(close.iloc[-1]) > float(high_4w.iloc[-1]):   score += 2.0   # breakout
    if float(volume.iloc[-1]) > 1.5 * float(vol_avg.iloc[-1]): score += 1.5  # volume surge
    score += max(0, (rsi_val - 50) / 50)  # mild RSI momentum bonus

    return score


def screen_dynamic_universe(top_n: int = 10) -> List[str]:
    """
    Screen _EXTENDED for breakout candidates not already in Config.WATCHLIST.
    Returns up to top_n symbols, sorted by score. Results cached 4 hours.
    """
    global _cache
    now = time.time()
    if _cache["candidates"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["candidates"]

    watchlist_set = set(Config.WATCHLIST)
    symbols = [s for s in _EXTENDED if s not in watchlist_set]

    if not symbols:
        return []

    try:
        # Batch download — much faster than per-symbol calls
        raw = yf.download(
            symbols,
            period="3mo",
            interval="1d",
            group_by="ticker",
            progress=False,
            auto_adjust=True,
            threads=True,
        )
    except Exception as exc:
        logger.warning(f"Dynamic universe download failed: {exc}")
        return []

    scored: list[tuple[float, str]] = []

    for sym in symbols:
        try:
            # yfinance multi-ticker layout: columns are (field, symbol)
            if hasattr(raw.columns, "levels") and sym in raw.columns.get_level_values(0):
                df = raw[sym].dropna(how="all")
            elif len(symbols) == 1:
                df = raw.dropna(how="all")
            else:
                continue

            s = _screen_symbol(df, sym)
            if s > 0:
                scored.append((s, sym))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [sym for _, sym in scored[:top_n]]

    _cache = {"candidates": result, "ts": now}
    if result:
        logger.info(
            f"Dynamic universe: {len(result)} breakout candidates: "
            + ", ".join(result)
        )
    return result


def get_full_universe() -> List[str]:
    """
    Return Config.WATCHLIST + dynamic breakout candidates (deduplicated).
    Falls back to WATCHLIST-only if dynamic scan is disabled or fails.
    """
    if not Config.DYNAMIC_UNIVERSE_ENABLED:
        return list(Config.WATCHLIST)

    dynamic = screen_dynamic_universe(top_n=Config.DYNAMIC_UNIVERSE_TOP_N)
    combined = list(Config.WATCHLIST)
    for sym in dynamic:
        if sym not in combined:
            combined.append(sym)
    return combined
