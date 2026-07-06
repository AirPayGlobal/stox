"""
Liquidity-sweep reversal detection ("manipulation candle" setups).

Systemized from trader transcripts; the mechanical core they all share:

  1. Sweep-and-reclaim on a higher timeframe: a candle trades BELOW the
     previous candle's low but CLOSES back above it (and closes bullish)
     -> LONG. Mirror image -> SHORT. The sweep wick is the stop; the
     target is a fixed reward:risk multiple of the stop distance.
  2. The same pattern against the PREVIOUS DAY's high/low ("session
     liquidity"), detected on intraday bars.
  3. The same pattern against the OVERNIGHT/PRE-MARKET range (prior 16:00
     ET close through today's 09:30 open) — the "accumulation range" of
     the AMD / Power-of-3 model; its sweep is the "manipulation" leg.
  4. Optional entry refinement: instead of entering on the reclaim close,
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
    kind: str              # "htf_candle" | "prev_day_level" | "overnight_range"
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


def level_sweep(
    today_df: pd.DataFrame, level_high: float, level_low: float, kind: str
) -> SweepSignal | None:
    """
    Reclaim logic on the last completed intraday bar against an arbitrary
    pair of liquidity levels: trade below `level_low` but close back above
    it (bullish) -> LONG; mirror against `level_high` -> SHORT.
    """
    if today_df.empty:
        return None
    c = today_df.iloc[-1]
    ts = today_df.index[-1].isoformat()

    if c["low"] < level_low and c["close"] > level_low and c["close"] > c["open"]:
        return SweepSignal(
            Signal.LONG, kind, float(level_low), float(c["low"]),
            float(c["close"]), float(c["high"]), float(c["low"]), ts,
        )
    if c["high"] > level_high and c["close"] < level_high and c["close"] < c["open"]:
        return SweepSignal(
            Signal.SHORT, kind, float(level_high), float(c["high"]),
            float(c["close"]), float(c["high"]), float(c["low"]), ts,
        )
    return None


def prev_day_level_sweep(
    today_df: pd.DataFrame, prev_day_high: float, prev_day_low: float
) -> SweepSignal | None:
    """Reclaim against the previous session's high/low."""
    return level_sweep(today_df, prev_day_high, prev_day_low, "prev_day_level")


def overnight_range(ext_df: pd.DataFrame, session_date) -> tuple[float, float] | None:
    """
    (high, low) of the overnight/pre-market session preceding `session_date`:
    bars from the prior trading day's 16:00 ET close through today's 09:30
    open. `ext_df` must contain extended-hours bars (rth_only=False). If no
    prior day exists in the data, falls back to today's pre-market bars
    alone. Returns None when there are no bars in the window.
    """
    if ext_df.empty:
        return None
    tz = ext_df.index.tz
    open_ts = pd.Timestamp(session_date).tz_localize(tz) + pd.Timedelta(hours=9, minutes=30)
    prior = ext_df[ext_df.index < open_ts]
    if prior.empty:
        return None

    prev_days = {d for d in prior.index.date if d < session_date}
    if prev_days:
        close_ts = pd.Timestamp(max(prev_days)).tz_localize(tz) + pd.Timedelta(hours=16)
        seg = prior[prior.index >= close_ts]
    else:
        seg = prior  # only today's pre-market available
    if seg.empty:
        return None
    return float(seg["high"].max()), float(seg["low"].min())


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


def session_range(
    ext_df: pd.DataFrame, session_date, window: str
) -> tuple[float, float] | None:
    """
    (high, low) of bars within an ET time window preceding `session_date`'s
    RTH open. `window` is "HH:MM-HH:MM"; a start later than the end spans
    midnight (start on the prior calendar day). Returns None when the window
    holds no bars (e.g. hours our data feed doesn't cover).
    """
    try:
        start_s, end_s = window.split("-")
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
    except ValueError:
        return None
    if ext_df.empty:
        return None

    tz = ext_df.index.tz
    day = pd.Timestamp(session_date).tz_localize(tz)
    end_ts = day + pd.Timedelta(hours=eh, minutes=em)
    start_ts = day + pd.Timedelta(hours=sh, minutes=sm)
    if start_ts >= end_ts:
        start_ts -= pd.Timedelta(days=1)

    seg = ext_df[(ext_df.index >= start_ts) & (ext_df.index < end_ts)]
    if seg.empty:
        return None
    return float(seg["high"].max()), float(seg["low"].min())


def rr_target(entry: float, stop: float, rr: float) -> float:
    """Target price at `rr` times the stop distance (sign-aware)."""
    return entry + rr * (entry - stop)
