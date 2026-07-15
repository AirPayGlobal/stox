"""
Intraday directional signal for the underlying.

The score (0-100) measures confluence of five components on intraday bars.
A direction fires only when BOTH hold, otherwise the signal is FLAT:
  * price has actually broken the opening range in that direction (hard
    gate — without it, drifting chop can accumulate enough soft points)
  * total score >= Config.SIGNAL_THRESHOLD

Components (long side shown; short side is the mirror image):
  * price above session VWAP ................ 25
  * price above the opening-range high ...... 25
  * EMA9 above EMA21 ......................... 20
  * net upward move over the last 3 bars .... 15
  * RSI not already overbought (< 70) ....... 15
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from analysis.indicators import ema, opening_range, rsi, session_vwap
from config import Config


class Signal(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass
class SignalResult:
    signal: Signal
    score: int              # score of the winning direction
    long_score: int
    short_score: int
    price: float
    details: dict = field(default_factory=dict)


@dataclass
class SignalContext:
    """Cross-day context for the entry filters. Fields left as None simply
    disable the corresponding filter for that scan (missing data must never
    silently block trading)."""

    daily_atr: float | None = None   # for the opening-range quality band
    rvol: float | None = None        # relative volume vs same time of day


def _filter_reason(
    direction: Signal,
    price: float,
    vwap_now: float,
    vwap_slope: float,
    or_size: float,
    ctx: SignalContext | None,
    break_vol_ratio: float | None = None,
) -> str | None:
    """First failed filter, or None if the setup passes all enabled filters."""
    if Config.ORB_FILTER_VWAP:
        aligned = (
            (direction == Signal.LONG and price > vwap_now and vwap_slope > 0)
            or (direction == Signal.SHORT and price < vwap_now and vwap_slope < 0)
        )
        if not aligned:
            return "vwap_alignment"
    if Config.ORB_FILTER_RVOL and ctx is not None and ctx.rvol is not None:
        if ctx.rvol < Config.RVOL_MIN:
            return f"rvol {ctx.rvol:.2f} < {Config.RVOL_MIN}"
    if Config.ORB_FILTER_OR_ATR and ctx is not None and ctx.daily_atr:
        ratio = or_size / ctx.daily_atr
        if not (Config.OR_ATR_MIN <= ratio <= Config.OR_ATR_MAX):
            return f"or/atr {ratio:.2f} outside [{Config.OR_ATR_MIN}, {Config.OR_ATR_MAX}]"
    if Config.ORB_FILTER_BREAK_VOLUME and break_vol_ratio is not None:
        if break_vol_ratio < Config.BREAK_VOLUME_MULT:
            return f"break volume {break_vol_ratio:.2f}x < {Config.BREAK_VOLUME_MULT}x"
    return None


MIN_BARS = 6  # need the opening range plus a few bars of trend


def generate_signal(
    session_df: pd.DataFrame, ctx: SignalContext | None = None
) -> SignalResult:
    """
    `session_df` — one session of intraday bars (open/high/low/close/volume),
    oldest first. Uses only completed bars. `ctx` supplies cross-day data for
    the optional entry filters (RVOL, opening-range quality).
    """
    if len(session_df) < MIN_BARS:
        return SignalResult(Signal.FLAT, 0, 0, 0, price=0.0)

    close = session_df["close"]
    price = float(close.iloc[-1])

    vwap_series = session_vwap(session_df)
    vwap = float(vwap_series.iloc[-1])
    vwap_slope = float(vwap_series.iloc[-1] - vwap_series.iloc[-4])
    or_high, or_low = opening_range(session_df, Config.OPENING_RANGE_MINUTES)
    ema_fast = float(ema(close, 9).iloc[-1])
    ema_slow = float(ema(close, 21).iloc[-1])
    rsi_val = float(rsi(close, 14).iloc[-1])
    move3 = float(close.iloc[-1] - close.iloc[-4])

    long_score = (
        (25 if price > vwap else 0)
        + (25 if price > or_high else 0)
        + (20 if ema_fast > ema_slow else 0)
        + (15 if move3 > 0 else 0)
        + (15 if rsi_val < 70 else 0)
    )
    short_score = (
        (25 if price < vwap else 0)
        + (25 if price < or_low else 0)
        + (20 if ema_fast < ema_slow else 0)
        + (15 if move3 < 0 else 0)
        + (15 if rsi_val > 30 else 0)
    )

    details = {
        "price": round(price, 2),
        "vwap": round(vwap, 2),
        "or_high": round(or_high, 2),
        "or_low": round(or_low, 2),
        "ema9": round(ema_fast, 2),
        "ema21": round(ema_slow, 2),
        "rsi": round(rsi_val, 1),
    }

    if ctx is not None:
        if ctx.rvol is not None:
            details["rvol"] = round(ctx.rvol, 2)
        if ctx.daily_atr:
            details["or_atr"] = round((or_high - or_low) / ctx.daily_atr, 2)

    threshold = Config.SIGNAL_THRESHOLD
    broke_high = price > or_high
    broke_low = price < or_low
    direction = Signal.FLAT
    if broke_high and long_score >= threshold and long_score > short_score:
        direction = Signal.LONG
    elif broke_low and short_score >= threshold and short_score > long_score:
        direction = Signal.SHORT

    if direction != Signal.FLAT:
        break_vol_ratio = None
        vols = session_df["volume"]
        lb = Config.BREAK_VOLUME_LOOKBACK
        if len(vols) >= lb + 1:
            prior_avg = float(vols.iloc[-(lb + 1):-1].mean())
            if prior_avg > 0:
                break_vol_ratio = float(vols.iloc[-1]) / prior_avg
                details["break_vol"] = round(break_vol_ratio, 2)
        reason = _filter_reason(
            direction, price, vwap, vwap_slope, or_high - or_low, ctx, break_vol_ratio
        )
        if reason:
            details["filtered"] = reason
            return SignalResult(
                Signal.FLAT, max(long_score, short_score),
                long_score, short_score, price, details,
            )
        score = long_score if direction == Signal.LONG else short_score
        return SignalResult(direction, score, long_score, short_score, price, details)
    return SignalResult(Signal.FLAT, max(long_score, short_score), long_score, short_score, price, details)
