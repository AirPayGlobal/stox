"""
9/20 EMA Scalp strategy.

Logic:
- 9 EMA crosses above 20 EMA on 5-min chart → long.
- 9 EMA crosses below 20 EMA → short.
- Entry on pullback to 9 EMA after the crossover.
- Stop below 20 EMA (long) / above 20 EMA (short).
- Target 1.5× risk.
- Filter: only trade when price is above/below VWAP in the direction of the trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from intraday.indicators import add_intraday_indicators
from utils.logger import get_logger

logger = get_logger("intraday.strategies.ema_scalp")


@dataclass
class EMAScalpSignal:
    symbol: str
    side: str           # "buy" | "sell"
    entry_price: float
    stop_loss: float
    take_profit: float
    ema9: float
    ema20: float
    score: float        # 0-100 confidence


def generate_signal(symbol: str, df: pd.DataFrame) -> Optional[EMAScalpSignal]:
    """
    Generate an EMA scalp signal from 5-min bars.
    Returns an EMAScalpSignal or None if conditions are not met.
    """
    if df is None or df.empty or len(df) < 25:
        return None

    try:
        df = add_intraday_indicators(df)

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]

        close = float(latest["close"])
        ema9_now = float(latest["ema9"])
        ema20_now = float(latest["ema20"])
        ema9_prev = float(prev["ema9"])
        ema20_prev = float(prev["ema20"])
        vwap = float(latest["vwap"])
        rsi_val = float(latest["rsi"]) if pd.notna(latest.get("rsi", float("nan"))) else 50.0
        volume = float(latest["volume"])

        if ema9_now <= 0 or ema20_now <= 0 or vwap <= 0:
            return None

        # Volume check
        avg_volume = float(df["volume"].tail(20).mean())
        if avg_volume <= 0:
            avg_volume = 1.0
        volume_ratio = volume / avg_volume

        # Time filter: skip first 15 min and last 30 min
        last_ts = df.index[-1]
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC").tz_convert("America/New_York")
        else:
            last_ts = last_ts.tz_convert("America/New_York")

        minutes_since_open = (last_ts.hour - 9) * 60 + last_ts.minute - 30
        minutes_to_close = (16 - last_ts.hour) * 60 - last_ts.minute
        if minutes_since_open < 15 or minutes_to_close < 30:
            return None

        # Detect crossover: EMA9 crossed above EMA20 in the last 1-3 bars
        cross_up = (ema9_now > ema20_now) and (ema9_prev <= ema20_prev)
        cross_down = (ema9_now < ema20_now) and (ema9_prev >= ema20_prev)

        # Alternatively: EMA9 > EMA20 and price pulled back to EMA9 (within 0.3%)
        long_trend = ema9_now > ema20_now
        short_trend = ema9_now < ema20_now
        pullback_to_ema9_long = long_trend and (close <= ema9_now * 1.003) and (close >= ema9_now * 0.997)
        pullback_to_ema9_short = short_trend and (close >= ema9_now * 0.997) and (close <= ema9_now * 1.003)

        # ---- Long setup ----
        if (cross_up or pullback_to_ema9_long) and close > vwap and rsi_val < 70:
            entry = close
            stop_loss = round(ema20_now * 0.998, 2)  # just below 20 EMA
            risk = entry - stop_loss
            if risk <= 0 or risk > entry * 0.02:  # cap risk at 2%
                return None
            take_profit = round(entry + risk * 1.5, 2)

            cross_boost = 15.0 if cross_up else 0.0
            vol_boost = min(10.0, (volume_ratio - 1.0) * 5.0)
            rsi_boost = max(0.0, min(10.0, (55.0 - rsi_val) / 3.0))
            score = min(100.0, 55.0 + cross_boost + vol_boost + rsi_boost)

            logger.debug(
                "EMA SCALP LONG %s: ema9=%.2f ema20=%.2f RSI=%.1f score=%.1f",
                symbol, ema9_now, ema20_now, rsi_val, score,
            )
            return EMAScalpSignal(
                symbol=symbol,
                side="buy",
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                ema9=round(ema9_now, 2),
                ema20=round(ema20_now, 2),
                score=score,
            )

        # ---- Short setup ----
        if (cross_down or pullback_to_ema9_short) and close < vwap and rsi_val > 30:
            entry = close
            stop_loss = round(ema20_now * 1.002, 2)  # just above 20 EMA
            risk = stop_loss - entry
            if risk <= 0 or risk > entry * 0.02:
                return None
            take_profit = round(entry - risk * 1.5, 2)

            cross_boost = 15.0 if cross_down else 0.0
            vol_boost = min(10.0, (volume_ratio - 1.0) * 5.0)
            rsi_boost = max(0.0, min(10.0, (rsi_val - 45.0) / 3.0))
            score = min(100.0, 55.0 + cross_boost + vol_boost + rsi_boost)

            logger.debug(
                "EMA SCALP SHORT %s: ema9=%.2f ema20=%.2f RSI=%.1f score=%.1f",
                symbol, ema9_now, ema20_now, rsi_val, score,
            )
            return EMAScalpSignal(
                symbol=symbol,
                side="sell",
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                ema9=round(ema9_now, 2),
                ema20=round(ema20_now, 2),
                score=score,
            )

    except Exception as exc:
        logger.warning("EMAScalp generate_signal(%s) error: %s", symbol, exc)

    return None
