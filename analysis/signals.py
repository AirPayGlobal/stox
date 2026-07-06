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


MIN_BARS = 6  # need the opening range plus a few bars of trend


def generate_signal(session_df: pd.DataFrame) -> SignalResult:
    """
    `session_df` — one session of intraday bars (open/high/low/close/volume),
    oldest first. Uses only completed bars.
    """
    if len(session_df) < MIN_BARS:
        return SignalResult(Signal.FLAT, 0, 0, 0, price=0.0)

    close = session_df["close"]
    price = float(close.iloc[-1])

    vwap = float(session_vwap(session_df).iloc[-1])
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

    threshold = Config.SIGNAL_THRESHOLD
    broke_high = price > or_high
    broke_low = price < or_low
    if broke_high and long_score >= threshold and long_score > short_score:
        return SignalResult(Signal.LONG, long_score, long_score, short_score, price, details)
    if broke_low and short_score >= threshold and short_score > long_score:
        return SignalResult(Signal.SHORT, short_score, long_score, short_score, price, details)
    return SignalResult(Signal.FLAT, max(long_score, short_score), long_score, short_score, price, details)
