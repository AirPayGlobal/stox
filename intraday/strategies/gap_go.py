"""
Gap & Go strategy.

Logic:
- Identify stocks that gapped up/down > 2% from previous close.
- Enter on the first 5-min candle breakout above the opening candle high (long).
- Only trade in the first 90 minutes.
- Volume must be 2× the 20-bar average.
- Target 2:1 R:R; stop at opening candle low.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from intraday.indicators import add_intraday_indicators
from utils.logger import get_logger

logger = get_logger("intraday.strategies.gap_go")


@dataclass
class GapGoSignal:
    symbol: str
    side: str           # "buy" | "sell"
    entry_price: float
    stop_loss: float
    take_profit: float
    gap_pct: float      # gap size as fraction (signed)
    score: float        # 0-100 confidence


def generate_signal(
    symbol: str,
    df: pd.DataFrame,
    prev_close: float,
    min_gap_pct: float = 0.02,
) -> Optional[GapGoSignal]:
    """
    Generate a Gap & Go signal.

    prev_close: previous session's closing price (required).
    Returns a GapGoSignal or None if conditions are not met.
    """
    if df is None or df.empty or len(df) < 3:
        return None
    if prev_close <= 0:
        return None

    try:
        df = add_intraday_indicators(df)

        # Opening candle (first bar of the day)
        open_bar = df.iloc[0]
        latest = df.iloc[-1]

        open_price = float(open_bar["open"])
        open_high = float(open_bar["high"])
        open_low = float(open_bar["low"])

        close = float(latest["close"])
        volume = float(latest["volume"])
        rsi_val = float(latest["rsi"]) if pd.notna(latest.get("rsi", float("nan"))) else 50.0

        gap_pct = (open_price - prev_close) / prev_close

        # Time filter: only trade first 90 minutes
        last_ts = df.index[-1]
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC").tz_convert("America/New_York")
        else:
            last_ts = last_ts.tz_convert("America/New_York")

        minutes_since_open = (last_ts.hour - 9) * 60 + last_ts.minute - 30
        if minutes_since_open > 90 or minutes_since_open < 5:
            return None

        # Volume check
        avg_volume = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
        if avg_volume <= 0:
            avg_volume = 1.0
        volume_ratio = volume / avg_volume

        if volume_ratio < 2.0:
            return None

        # ---- Long Gap & Go ----
        if gap_pct >= min_gap_pct and close > open_high:
            # Only if RSI not already overbought
            if rsi_val > 80:
                return None

            entry = round(open_high + 0.01, 2)
            stop_loss = round(max(open_low, entry * 0.97), 2)
            risk = entry - stop_loss
            if risk <= 0:
                return None
            take_profit = round(entry + risk * 2.0, 2)

            volume_score = min(20.0, (volume_ratio - 2.0) * 5.0)
            gap_score = min(20.0, abs(gap_pct) * 400.0)   # 2% gap → 8pts, 5% → 20pts
            rsi_boost = max(0.0, min(10.0, (70.0 - rsi_val) / 2.0))
            score = min(100.0, 55.0 + volume_score + gap_score + rsi_boost)

            logger.debug(
                "GAP GO LONG %s: gap=%.2f%% vol_ratio=%.1f RSI=%.1f score=%.1f",
                symbol, gap_pct * 100, volume_ratio, rsi_val, score,
            )
            return GapGoSignal(
                symbol=symbol,
                side="buy",
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                gap_pct=round(gap_pct * 100, 2),
                score=score,
            )

        # ---- Short Gap & Go (gap down) ----
        if gap_pct <= -min_gap_pct and close < open_low:
            if rsi_val < 20:
                return None

            entry = round(open_low - 0.01, 2)
            stop_loss = round(min(open_high, entry * 1.03), 2)
            risk = stop_loss - entry
            if risk <= 0:
                return None
            take_profit = round(entry - risk * 2.0, 2)

            volume_score = min(20.0, (volume_ratio - 2.0) * 5.0)
            gap_score = min(20.0, abs(gap_pct) * 400.0)
            rsi_boost = max(0.0, min(10.0, (rsi_val - 30.0) / 2.0))
            score = min(100.0, 55.0 + volume_score + gap_score + rsi_boost)

            logger.debug(
                "GAP GO SHORT %s: gap=%.2f%% vol_ratio=%.1f RSI=%.1f score=%.1f",
                symbol, gap_pct * 100, volume_ratio, rsi_val, score,
            )
            return GapGoSignal(
                symbol=symbol,
                side="sell",
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                gap_pct=round(gap_pct * 100, 2),
                score=score,
            )

    except Exception as exc:
        logger.warning("GapGo generate_signal(%s) error: %s", symbol, exc)

    return None
