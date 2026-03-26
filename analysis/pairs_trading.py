"""
Pairs Trading / Statistical Arbitrage
======================================
Trades the mean-reverting spread between two historically cointegrated stocks.

Theory
------
Two stocks can move together long-term (cointegrated) even while their
short-term ratio fluctuates around a stable mean. When the ratio stretches
beyond 2 standard deviations (z-score > 2), it tends to snap back — this
reversion is the tradeable edge.

Direction
---------
  z > +ENTRY_Z  → pair A is expensive vs B:  SHORT A  / LONG  B
  z < -ENTRY_Z  → pair B is expensive vs A:  SHORT B  / LONG  A
  |z| < EXIT_Z  → spread has reverted:       CLOSE both legs
  |z| > STOP_Z  → spread is diverging:       STOP LOSS, close both legs

Spread calculation
------------------
  log_spread = log(price_A) - hedge_ratio × log(price_B)
  hedge_ratio = OLS slope of log(A) ~ log(B) over WINDOW days
  z_score     = (log_spread - rolling_mean) / rolling_std

Predefined pairs
----------------
Only well-known, fundamentally linked pairs are traded to reduce the risk
of spurious cointegration.  Weekly cointegration checks can add/remove pairs
dynamically (handled by auto_optimize.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Predefined pairs  (both symbols must be in Config.WATCHLIST or fetched)
# ---------------------------------------------------------------------------

PAIRS: list[tuple[str, str]] = [
    ("MSFT",  "GOOGL"),   # Mega-cap tech / cloud
    ("AAPL",  "MSFT"),    # Mega-cap tech
    ("GOOGL", "META"),    # Ad-revenue giants
    ("XOM",   "CVX"),     # Integrated energy majors
    ("JPM",   "BAC"),     # US money-center banks
    ("V",     "MA"),      # Payment network duopoly
    ("KO",    "PEP"),     # Consumer staples / cola
    ("COST",  "WMT"),     # Discount retail giants
    ("JNJ",   "ABT"),     # Diversified healthcare
    ("ABBV",  "MRK"),     # Large-cap pharma
    ("AMGN",  "BMY"),     # Biotech / pharma
    ("TXN",   "QCOM"),    # Semiconductor design
    ("ADBE",  "CRM"),     # Enterprise SaaS
]


# ---------------------------------------------------------------------------
# Z-score engine
# ---------------------------------------------------------------------------

def _hedge_ratio(log_a: pd.Series, log_b: pd.Series) -> float:
    """OLS hedge ratio: slope of log_a ~ log_b."""
    if len(log_a) < 20:
        return 1.0
    coeffs = np.polyfit(log_b, log_a, 1)
    return float(coeffs[0])


def calculate_zscore(
    prices_a: pd.Series,
    prices_b: pd.Series,
    window: int = None,
) -> tuple[pd.Series, float]:
    """
    Compute the rolling z-score of the log-price spread.

    Returns (z_series, current_hedge_ratio).
    """
    window = window or Config.PAIRS_WINDOW
    log_a = np.log(prices_a)
    log_b = np.log(prices_b)

    # Rolling hedge ratio (recalculate every window bars)
    beta = _hedge_ratio(log_a.iloc[-window:], log_b.iloc[-window:])

    spread = log_a - beta * log_b
    mean   = spread.rolling(window).mean()
    std    = spread.rolling(window).std().replace(0, np.nan)
    z      = (spread - mean) / std

    return z, beta


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

class PairSignal:
    LONG_A_SHORT_B = "LONG_A_SHORT_B"   # A cheap vs B  → buy A, sell B
    LONG_B_SHORT_A = "LONG_B_SHORT_A"   # B cheap vs A  → buy B, sell A
    EXIT           = "EXIT"              # z-score reverted → close
    STOP           = "STOP"             # z-score diverging → stop loss
    HOLD           = "HOLD"


def generate_pair_signal(
    symbol_a: str,
    symbol_b: str,
    data: dict[str, pd.DataFrame],
    open_pair: Optional[dict] = None,
) -> tuple[str, float, float]:
    """
    Generate a pairs signal for (symbol_a, symbol_b).

    Parameters
    ----------
    data      : price DataFrames keyed by symbol
    open_pair : existing pair position dict (or None if no open position)

    Returns
    -------
    (signal, z_score, hedge_ratio)
    """
    if symbol_a not in data or symbol_b not in data:
        return PairSignal.HOLD, 0.0, 1.0

    df_a = data[symbol_a]["close"].dropna()
    df_b = data[symbol_b]["close"].dropna()

    # Align on common index
    aligned = pd.concat([df_a, df_b], axis=1).dropna()
    if len(aligned) < Config.PAIRS_WINDOW + 5:
        return PairSignal.HOLD, 0.0, 1.0

    z_series, beta = calculate_zscore(aligned.iloc[:, 0], aligned.iloc[:, 1])
    z = float(z_series.iloc[-1])

    if np.isnan(z):
        return PairSignal.HOLD, 0.0, beta

    entry_z = Config.PAIRS_ENTRY_ZSCORE
    exit_z  = Config.PAIRS_EXIT_ZSCORE
    stop_z  = Config.PAIRS_STOP_ZSCORE

    if open_pair:
        # Manage existing position
        direction = open_pair.get("direction")
        if abs(z) > stop_z:
            logger.warning(
                f"Pairs STOP: {symbol_a}/{symbol_b} z={z:.2f} "
                f"exceeds stop {stop_z} — diverging"
            )
            return PairSignal.STOP, z, beta

        if abs(z) < exit_z:
            logger.info(
                f"Pairs EXIT: {symbol_a}/{symbol_b} z={z:.2f} "
                f"reverted to mean (|z| < {exit_z})"
            )
            return PairSignal.EXIT, z, beta

        return PairSignal.HOLD, z, beta

    else:
        # Look for new entry
        if z > entry_z:
            logger.info(
                f"Pairs signal: {symbol_a}/{symbol_b} z={z:.2f} > {entry_z} "
                f"→ SHORT {symbol_a} / LONG {symbol_b}"
            )
            return PairSignal.LONG_B_SHORT_A, z, beta

        if z < -entry_z:
            logger.info(
                f"Pairs signal: {symbol_a}/{symbol_b} z={z:.2f} < -{entry_z} "
                f"→ LONG {symbol_a} / SHORT {symbol_b}"
            )
            return PairSignal.LONG_A_SHORT_B, z, beta

        return PairSignal.HOLD, z, beta


def screen_pairs(
    data: dict[str, pd.DataFrame],
    open_pairs: list[dict],
) -> list[tuple[str, str, str, float, float]]:
    """
    Scan all defined pairs for entry / exit signals.

    Returns list of (symbol_a, symbol_b, signal, z_score, hedge_ratio).
    """
    open_keys = {(p["symbol_a"], p["symbol_b"]) for p in open_pairs}
    results = []

    for sym_a, sym_b in PAIRS:
        existing = next(
            (p for p in open_pairs
             if (p["symbol_a"], p["symbol_b"]) == (sym_a, sym_b)
             or (p["symbol_a"], p["symbol_b"]) == (sym_b, sym_a)),
            None,
        )
        signal, z, beta = generate_pair_signal(sym_a, sym_b, data, existing)
        if signal != PairSignal.HOLD:
            results.append((sym_a, sym_b, signal, z, beta))

    return results


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def pair_position_sizes(
    equity: float,
    price_long: float,
    price_short: float,
) -> tuple[int, int]:
    """
    Dollar-neutral sizing: equal dollar exposure on each leg.

    Each leg = PAIRS_POSITION_PCT × equity.
    Returns (qty_long, qty_short).
    """
    leg_budget = equity * Config.PAIRS_POSITION_PCT
    qty_long  = max(1, int(leg_budget / price_long))
    qty_short = max(1, int(leg_budget / price_short))
    return qty_long, qty_short
