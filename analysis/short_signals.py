"""
Short Selling Signal Layer
===========================
Generates short-sell candidates from SELL signals, applying additional
filters to ensure only high-conviction shorts are taken.

Short entry criteria (ALL must be satisfied)
---------------------------------------------
  1. Daily SELL signal from EMA/RSI/MACD (score ≥ SELL_THRESHOLD)
  2. Weekly chart also bearish (weekly_confirms_short)
  3. Symbol's sector is in the BOTTOM N sectors by momentum
  4. Composite sentiment score < SHORT_MIN_SENTIMENT (negative)
  5. Symbol is NOT currently held as a long position
  6. VIX not excessively high (> SHORT_MAX_VIX) — avoid crowded shorts
     when everyone is already panic-selling

Position management
--------------------
  - Stop loss  : price rises STOP_LOSS_PCT above entry  (broker stop)
  - Take profit: price drops TAKE_PROFIT_PCT below entry (bracket TP)
  - Trailing stop: low_water_mark — closes if price rises
    TRAILING_STOP_PCT above the lowest price seen since entry

Short P&L direction
-------------------
  P&L = (entry_price − exit_price) × shares
  Win when price falls, lose when price rises.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from config import Config
from analysis.signals import Signal
from analysis.timeframe import weekly_confirms_short
from analysis.sector_rotation import get_sector_rankings, get_symbol_sector
from utils.logger import get_logger

logger = get_logger(__name__)


def get_bottom_sectors(bottom_n: int = None) -> set[str]:
    """Return the set of bottom N sector ETFs by 3-month momentum."""
    n = bottom_n or Config.SHORT_SECTOR_BOTTOM_N
    rankings = get_sector_rankings()
    if not rankings:
        return set()
    total = len(rankings)
    return {etf for etf, _, rank in rankings if rank > (total - n)}


def is_sector_bearish(symbol: str, bottom_n: int = None) -> bool:
    """
    Return True if the symbol's sector is among the worst performers.
    Unknown symbols are allowed through (don't block the short).
    """
    sector = get_symbol_sector(symbol)
    if not sector:
        return True  # unknown = don't block
    bottom = get_bottom_sectors(bottom_n)
    if not bottom:
        return True  # failed to fetch = don't block
    in_bottom = sector in bottom
    if not in_bottom:
        logger.info(f"Short sector filter: {symbol} ({sector}) not in bottom sectors — skipping short")
    return in_bottom


def short_position_size(
    equity: float,
    price: float,
    atr: float,
) -> tuple[int, float, float]:
    """
    Size a short position. Same logic as long but stop is ABOVE entry.

    Returns (shares, stop_loss_price, take_profit_price)
    where stop_loss > entry and take_profit < entry.
    """
    risk_amount   = equity * Config.STOP_LOSS_PCT
    stop_distance = max(atr, price * Config.STOP_LOSS_PCT)
    stop_distance = max(stop_distance, price * 0.001)

    shares_by_risk   = risk_amount / stop_distance
    max_shares_by_pct = (equity * Config.MAX_POSITION_PCT) / price

    shares = max(1, int(min(shares_by_risk, max_shares_by_pct)))

    stop_loss   = price + stop_distance          # stop OUT if price rises
    take_profit = price - (stop_distance * 3)    # 3:1 R:R — TP if price falls

    if take_profit <= 0:
        take_profit = price * 0.94

    return shares, round(stop_loss, 2), round(take_profit, 2)


def screen_short_candidates(
    candidates: list[tuple[str, Signal, int]],
    data: dict[str, pd.DataFrame],
    open_symbols: set[str],
) -> list[tuple[str, int]]:
    """
    Filter the universe scan output for short-sell candidates.

    Parameters
    ----------
    candidates  : output of screen_universe() — (symbol, signal, score)
    data        : price DataFrames keyed by symbol
    open_symbols: symbols already held long or with pending orders

    Returns
    -------
    List of (symbol, score) for confirmed short candidates, score desc.
    """
    if not Config.SHORT_SELLING_ENABLED:
        return []

    shorts = []
    for symbol, signal, score in candidates:
        if signal != Signal.SELL:
            continue
        if symbol in open_symbols:
            continue  # never short something we own long

        df = data.get(symbol)
        if df is None or df.empty:
            continue

        # Weekly chart must also be bearish
        if not weekly_confirms_short(df, symbol):
            continue

        # Sector must be in the weakest N sectors
        if not is_sector_bearish(symbol):
            continue

        shorts.append((symbol, score))

    shorts.sort(key=lambda x: x[1], reverse=True)
    logger.info(f"Short candidates after filters: {len(shorts)}")
    return shorts
