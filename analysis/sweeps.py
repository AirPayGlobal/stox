"""
Liquidity-sweep reversal detection ("manipulation candle" setups).

Systemized from trader transcripts; the mechanical core they all share:

  1. Sweep-and-reclaim on a higher timeframe: a candle trades BELOW the
     previous candle's low but CLOSES back above it (and closes bullish)
     -> LONG. Mirror image -> SHORT. The sweep wick is the stop; the
     target is a fixed reward:risk multiple of the stop distance.
  2. The same pattern against the PREVIOUS DAY's high/low ("session
     liquidity"), detected on intraday bars.
  3. Optional entry refinement: instead of entering on the reclaim close,
     wait for a retracement into the manipulation candle (fair value gap
     if one exists, else the candle midpoint) for a better price with the
     same stop — improving the realized RR.

Everything here is pure (DataFrame in, signal out) so it is unit-testable
and shared between the live engine and the backtester.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from analysis.signals import Signal


@dataclass
class SweepSignal:
    direction: Signal      # LONG (swept a low, reclaimed) | SHORT (mirror)
    kind: str              # "htf_candle" | "prev_day_level"
    swept_level: float     # the low/high that was swept
    extreme: float         # wick extreme of the sweep candle -> stop basis
    close: float           # reclaim close (reference entry)
    candle_high: float
    candle_low: float
    candle_ts: str         # ISO timestamp of the signal candle (dedupe key)

    @property
    def midpoint(self) -> float:
        return (self.candle_high + self.candle_low) / 2


def sweep_reclaim(htf_df: pd.DataFrame, trend_filter: bool = False) -> SweepSignal | None:
    """
    Check the last COMPLETED higher-timeframe candle against the one before
    it. `trend_filter` additionally requires the swept candle to have closed
    against the signal (a down candle before a bullish reclaim), i.e. the
    sweep really was a manipulation of a move in the other direction.
    """
    if len(htf_df) < 2:
        return None
    p = htf_df.iloc[-2]
    c = htf_df.iloc[-1]

    bullish = c["low"] < p["low"] and c["close"] > c["open"] and c["close"] > p["low"]
    bearish = c["high"] > p["high"] and c["close"] < c["open"] and c["close"] < p["high"]
    if trend_filter:
        bullish = bullish and p["close"] < p["open"]
        bearish = bearish and p["close"] > p["open"]

    ts = htf_df.index[-1].isoformat()
    if bullish:
        return SweepSignal(
            Signal.LONG, "htf_candle", float(p["low"]), float(c["low"]),
            float(c["close"]), float(c["high"]), float(c["low"]), ts,
        )
    if bearish:
        return SweepSignal(
            Signal.SHORT, "htf_candle", float(p["high"]), float(c["high"]),
            float(c["close"]), float(c["high"]), float(c["low"]), ts,
        )
    return None


def prev_day_level_sweep(
    today_df: pd.DataFrame, prev_day_high: float, prev_day_low: float
) -> SweepSignal | None:
    """
    Same reclaim logic on the last completed intraday bar, against the
    previous session's high/low instead of the previous candle.
    """
    if today_df.empty:
        return None
    c = today_df.iloc[-1]
    ts = today_df.index[-1].isoformat()

    if c["low"] < prev_day_low and c["close"] > prev_day_low and c["close"] > c["open"]:
        return SweepSignal(
            Signal.LONG, "prev_day_level", float(prev_day_low), float(c["low"]),
            float(c["close"]), float(c["high"]), float(c["low"]), ts,
        )
    if c["high"] > prev_day_high and c["close"] < prev_day_high and c["close"] < c["open"]:
        return SweepSignal(
            Signal.SHORT, "prev_day_level", float(prev_day_high), float(c["high"]),
            float(c["close"]), float(c["high"]), float(c["low"]), ts,
        )
    return None


def find_fvg(df: pd.DataFrame, direction: Signal, lookback: int = 20) -> tuple[float, float] | None:
    """
    Most recent fair value gap (3-candle imbalance) in `direction`.
    Bullish FVG at bar i: low[i] > high[i-2] — returns (zone_low, zone_high).
    """
    n = len(df)
    for i in range(n - 1, max(1, n - lookback), -1):
        if direction == Signal.LONG and df["low"].iloc[i] > df["high"].iloc[i - 2]:
            return float(df["high"].iloc[i - 2]), float(df["low"].iloc[i])
        if direction == Signal.SHORT and df["high"].iloc[i] < df["low"].iloc[i - 2]:
            return float(df["high"].iloc[i]), float(df["low"].iloc[i - 2])
    return None


def rr_target(entry: float, stop: float, rr: float) -> float:
    """Target price at `rr` times the stop distance (sign-aware)."""
    return entry + rr * (entry - stop)
