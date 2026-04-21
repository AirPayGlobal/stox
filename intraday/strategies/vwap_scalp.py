"""
VWAP Mean-Reversion Scalp strategy.

Logic:
- Calculate session VWAP.
- Long when price is significantly below VWAP (oversold deviation) with RSI < 40.
- Short when price is significantly above VWAP with RSI > 65.
- Volume confirmation required (signal bar > 1.5× avg).
- Skip within 30 minutes of open (too volatile) or close (spread risk).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone, timedelta
from typing import Optional

import pandas as pd

from intraday.indicators import add_intraday_indicators
from utils.logger import get_logger

logger = get_logger("intraday.strategies.vwap_scalp")

_ET = timezone(timedelta(hours=-4))


@dataclass
class VWAPSignal:
    symbol: str
    side: str           # "buy" | "sell"
    entry_price: float
    stop_loss: float
    take_profit: float
    deviation_pct: float  # how far from VWAP (signed)
    score: float          # 0-100 confidence


def generate_signal(symbol: str, df: pd.DataFrame) -> Optional[VWAPSignal]:
    """
    Generate a VWAP mean-reversion scalp signal.

    Returns a VWAPSignal or None if conditions are not met.
    """
    if df is None or df.empty or len(df) < 10:
        return None

    try:
        df = add_intraday_indicators(df)

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(latest["close"])
        vwap = float(latest["vwap"])
        rsi_val = float(latest["rsi"]) if pd.notna(latest.get("rsi", float("nan"))) else 50.0
        volume = float(latest["volume"])

        if vwap <= 0:
            return None

        deviation = (close - vwap) / vwap  # signed

        # Volume check
        avg_volume = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
        if avg_volume <= 0:
            avg_volume = 1.0
        volume_ratio = volume / avg_volume

        if volume_ratio < 1.5:
            return None

        # Time filter: skip first 30 min and last 30 min
        last_ts = df.index[-1]
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC").tz_convert("America/New_York")
        else:
            last_ts = last_ts.tz_convert("America/New_York")

        hour = last_ts.hour
        minute = last_ts.minute
        total_minutes_since_open = (hour - 9) * 60 + minute - 30
        total_minutes_to_close = (16 - hour) * 60 - minute

        if total_minutes_since_open < 30:
            return None
        if total_minutes_to_close < 30:
            return None

        # ---- Long setup: price well below VWAP, oversold ----
        if deviation < -0.005 and rsi_val < 40:
            # Confirm price is holding (not still falling): prev bar low not exceeded
            if float(latest["close"]) < float(prev["low"]):
                return None  # still falling

            entry = close
            stop_loss = round(close * 0.995, 2)         # 0.5% below entry
            take_profit = round(vwap, 2)                 # mean reversion to VWAP

            # Score: larger deviation + lower RSI = better
            deviation_score = min(20.0, abs(deviation) * 2000.0)  # 0.5% → 10pts, 1% → 20pts
            rsi_score = min(15.0, (40.0 - rsi_val) * 0.5)
            score = min(100.0, 50.0 + deviation_score + rsi_score)

            logger.debug(
                "VWAP LONG %s: close=%.2f vwap=%.2f dev=%.3f%% RSI=%.1f vol_ratio=%.1f score=%.1f",
                symbol, close, vwap, deviation * 100, rsi_val, volume_ratio, score,
            )
            return VWAPSignal(
                symbol=symbol,
                side="buy",
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                deviation_pct=round(deviation * 100, 3),
                score=score,
            )

        # ---- Short setup: price well above VWAP, overbought ----
        if deviation > 0.008 and rsi_val > 65:
            # Confirm price is not still surging: prev bar high not exceeded
            if float(latest["close"]) > float(prev["high"]):
                return None  # still climbing

            entry = close
            stop_loss = round(close * 1.005, 2)          # 0.5% above entry
            take_profit = round(vwap, 2)                  # mean reversion to VWAP

            deviation_score = min(20.0, abs(deviation) * 2000.0)
            rsi_score = min(15.0, (rsi_val - 65.0) * 0.5)
            score = min(100.0, 50.0 + deviation_score + rsi_score)

            logger.debug(
                "VWAP SHORT %s: close=%.2f vwap=%.2f dev=%.3f%% RSI=%.1f vol_ratio=%.1f score=%.1f",
                symbol, close, vwap, deviation * 100, rsi_val, volume_ratio, score,
            )
            return VWAPSignal(
                symbol=symbol,
                side="sell",
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                deviation_pct=round(deviation * 100, 3),
                score=score,
            )

    except Exception as exc:
        logger.warning("VWAP generate_signal(%s) error: %s", symbol, exc)

    return None
