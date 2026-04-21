"""
Opening Range Breakout (ORB) strategy.

Logic:
- Compute the opening range (high/low) from the first orb_minutes of trading.
- Signal long breakout when price closes above the range high with volume and RSI support.
- Signal short breakout when price closes below the range low.
- Only generate signals in the first 2 hours of trading (before 11:30 AM ET).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone, timedelta
from typing import Optional

import pandas as pd

from intraday.indicators import add_intraday_indicators, opening_range
from utils.logger import get_logger

logger = get_logger("intraday.strategies.orb")

_ET = timezone(timedelta(hours=-4))


@dataclass
class ORBSignal:
    symbol: str
    side: str           # "buy" | "sell"
    entry_price: float
    stop_loss: float
    take_profit: float
    range_high: float
    range_low: float
    score: float        # 0-100 confidence


def generate_signal(
    symbol: str,
    df: pd.DataFrame,
    orb_minutes: int = 15,
) -> Optional[ORBSignal]:
    """
    Generate an ORB signal for the given symbol from intraday bars.

    Returns an ORBSignal or None if conditions are not met.
    """
    if df is None or df.empty or len(df) < 4:
        return None

    try:
        # Need at least orb_minutes / bar_size bars to form the range
        # Infer bar size in minutes from index spacing
        if len(df.index) >= 2:
            bar_size_td = df.index[1] - df.index[0]
            bar_size_min = bar_size_td.total_seconds() / 60.0
        else:
            bar_size_min = 5.0

        min_bars_needed = max(1, int(orb_minutes / bar_size_min))
        if len(df) < min_bars_needed + 1:
            return None

        # Compute indicators
        df = add_intraday_indicators(df)

        # Current bar
        latest = df.iloc[-1]
        close = float(latest["close"])
        volume = float(latest["volume"])
        rsi_val = float(latest["rsi"]) if pd.notna(latest.get("rsi", float("nan"))) else 50.0

        # Opening range
        range_high, range_low = opening_range(df, minutes=orb_minutes)
        if range_high == 0.0 and range_low == 0.0:
            return None

        range_size = range_high - range_low
        if range_size <= 0:
            return None

        # Skip if range is too wide (> 3% of entry) — indicates choppy open
        if range_size / range_high > 0.03:
            return None

        # Only signal in first 2 hours (before 11:30 AM ET)
        last_ts = df.index[-1]
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC").tz_convert("America/New_York")
        else:
            last_ts = last_ts.tz_convert("America/New_York")

        cutoff_hour, cutoff_minute = 11, 30
        if last_ts.hour > cutoff_hour or (last_ts.hour == cutoff_hour and last_ts.minute >= cutoff_minute):
            return None

        # Volume metrics
        avg_volume = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
        if avg_volume <= 0:
            avg_volume = 1.0
        volume_ratio = volume / avg_volume

        # ---- Long setup ----
        if close > range_high and volume_ratio > 1.0 and 40 <= rsi_val <= 70:
            entry = round(range_high + 0.01, 2)
            # Stop: range_low, or range_high - 0.5 * range_size if range is wide
            raw_stop = range_low if (range_size / range_high) <= 0.015 else range_high - 0.5 * range_size
            stop_loss = round(max(raw_stop, entry * 0.97), 2)  # floor at 3% below entry
            take_profit = round(entry + range_size * 1.5, 2)

            # Score
            volume_boost = min(15.0, (volume_ratio - 1.0) * 10.0)
            rsi_boost = max(0.0, min(10.0, (rsi_val - 40.0) / 3.0))
            score = min(100.0, 60.0 + volume_boost + rsi_boost)

            logger.debug(
                "ORB LONG %s: close=%.2f > range_high=%.2f vol_ratio=%.1f RSI=%.1f score=%.1f",
                symbol, close, range_high, volume_ratio, rsi_val, score,
            )
            return ORBSignal(
                symbol=symbol,
                side="buy",
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                range_high=range_high,
                range_low=range_low,
                score=score,
            )

        # ---- Short setup ----
        if close < range_low and volume_ratio > 1.0 and 30 <= rsi_val <= 60:
            entry = round(range_low - 0.01, 2)
            raw_stop = range_high if (range_size / range_low) <= 0.015 else range_low + 0.5 * range_size
            stop_loss = round(min(raw_stop, entry * 1.03), 2)  # ceiling at 3% above entry
            take_profit = round(entry - range_size * 1.5, 2)

            volume_boost = min(15.0, (volume_ratio - 1.0) * 10.0)
            rsi_boost = max(0.0, min(10.0, (60.0 - rsi_val) / 3.0))
            score = min(100.0, 60.0 + volume_boost + rsi_boost)

            logger.debug(
                "ORB SHORT %s: close=%.2f < range_low=%.2f vol_ratio=%.1f RSI=%.1f score=%.1f",
                symbol, close, range_low, volume_ratio, rsi_val, score,
            )
            return ORBSignal(
                symbol=symbol,
                side="sell",
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                range_high=range_high,
                range_low=range_low,
                score=score,
            )

    except Exception as exc:
        logger.warning("ORB generate_signal(%s) error: %s", symbol, exc)

    return None
