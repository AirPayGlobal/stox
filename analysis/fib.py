"""
Fibonacci-retracement pullback strategy (1-minute).

Systemized from the "gold zone" scalping method:

  1. Find the most recent impulse leg on 1-minute bars — a swing low → swing
     high (up) or swing high → swing low (down), detected as fractal pivots.
     A fresh impulse making a new local extreme is the break of structure.
  2. Wait for price to RETRACE into the 0.5–0.618 zone of that leg (the
     "gold zone"), in the trend's direction — a pullback entry, not a chase.
  3. Enter with the trend: LONG (calls) on an up-leg pullback, SHORT (puts)
     on a down-leg pullback.
  4. Stop at the fib 1.0 level (the leg's origin — trend invalidated there).
     Target the impulse extreme (continuation). Entry at ~0.618 gives ~1:1.6
     reward:risk. Exits are on the UNDERLYING price (like the sweep book).

Pure functions (bars in, signal out) so the engine and backtester share
identical logic and it is unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from analysis.signals import Signal
from config import Config


@dataclass
class Pivot:
    idx: int
    price: float
    kind: str  # "H" | "L"


@dataclass
class FibSignal:
    direction: Signal      # LONG (up-leg pullback) | SHORT (down-leg pullback)
    entry_lo: float        # gold-zone price bounds
    entry_hi: float
    stop: float            # fib 1.0 — leg origin
    target: float          # impulse extreme — continuation
    swing_hi: float
    swing_lo: float
    key: str               # dedupe key (the two pivot bar positions)


def find_pivots(bars: pd.DataFrame, k: int) -> list[Pivot]:
    """Fractal pivots: a high/low that is the strict extreme of the k bars on
    each side. Only bars with k neighbours each side can be pivots."""
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    n = len(bars)
    pivots: list[Pivot] = []
    for i in range(k, n - k):
        hi_win = highs[i - k : i + k + 1]
        lo_win = lows[i - k : i + k + 1]
        if highs[i] == hi_win.max() and (hi_win == highs[i]).sum() == 1:
            pivots.append(Pivot(i, float(highs[i]), "H"))
        elif lows[i] == lo_win.min() and (lo_win == lows[i]).sum() == 1:
            pivots.append(Pivot(i, float(lows[i]), "L"))
    return pivots


def stop_distance_ok(spot: float, stop: float) -> bool:
    """Fib's own tradeable stop-distance band (it used to borrow the sweep
    strategy's 60-minute bounds, which threw out most 1-minute legs). The stop
    sits at the leg origin, so the distance is ~0.5x the impulse range."""
    dist = abs(spot - stop)
    return (
        max(0.01, spot * Config.FIB_MIN_STOP_PCT)
        <= dist
        <= spot * Config.FIB_MAX_STOP_PCT
    )


def _swing_extremes(pivots: list[Pivot], window_start: int) -> tuple[Pivot, Pivot] | None:
    """Anchor the active impulse on the dominant swing in the recent window:
    the highest H pivot and the lowest L pivot. This is what makes the setup
    survive the pullback — a shallow retracement prints a minor pivot, but that
    pivot is neither a new high nor a new low, so the fib anchors don't move
    (the old 'last two pivots' rule flipped the leg the instant the pullback
    printed a pivot, killing the setup before price reached the gold zone)."""
    highs = [p for p in pivots if p.kind == "H" and p.idx >= window_start]
    lows = [p for p in pivots if p.kind == "L" and p.idx >= window_start]
    if not highs or not lows:
        return None
    top = max(highs, key=lambda p: p.price)
    bottom = min(lows, key=lambda p: p.price)
    return top, bottom


def fib_signal(bars: pd.DataFrame) -> FibSignal | None:
    """
    `bars` — one session of 1-minute bars (oldest first). Anchors the active
    impulse leg on the dominant swing of the recent window and returns a
    pullback signal whenever the latest bar wicks into that leg's 0.5-0.618
    gold zone (with the trend, origin not yet broken), else None.
    """
    k = Config.FIB_PIVOT_K
    if len(bars) < 2 * k + 3:
        return None

    pivots = find_pivots(bars, k)
    ext = _swing_extremes(pivots, max(0, len(bars) - Config.FIB_LOOKBACK_BARS))
    if ext is None:
        return None
    top, bottom = ext

    price = float(bars["close"].iloc[-1])
    bar_low = float(bars["low"].iloc[-1])
    bar_high = float(bars["high"].iloc[-1])
    lo = Config.FIB_ENTRY_LOW      # 0.5
    hi = Config.FIB_ENTRY_HIGH     # 0.618
    swing_hi, swing_lo = top.price, bottom.price
    rng = swing_hi - swing_lo
    if rng <= 0 or rng < price * Config.FIB_MIN_RANGE_PCT:
        return None
    key = f"{bottom.idx}-{top.idx}"

    if top.idx > bottom.idx:
        # Up-impulse (low launched the move to the high) -> LONG the pullback.
        # Gold zone (retracement down from the high): [0.618, 0.5] of the leg.
        zone_lo = swing_hi - hi * rng      # deeper (0.618)
        zone_hi = swing_hi - lo * rng      # shallower (0.5)
        # TOUCH entry: the bar wicked at least to the 0.5 level without breaking
        # the origin, and price is still in the trend. A close-inside-the-band
        # rule almost never fires on 1-min bars.
        if bar_low <= zone_hi and bar_low > swing_lo and price > swing_lo:
            return FibSignal(
                Signal.LONG, round(zone_lo, 2), round(zone_hi, 2),
                stop=round(swing_lo, 2), target=round(swing_hi, 2),
                swing_hi=round(swing_hi, 2), swing_lo=round(swing_lo, 2),
                key=key,
            )
    else:
        # Down-impulse (high launched the move to the low) -> SHORT the pullback.
        # Gold zone (retracement up from the low): [0.5, 0.618] of the leg.
        zone_lo = swing_lo + lo * rng      # shallower (0.5)
        zone_hi = swing_lo + hi * rng      # deeper (0.618)
        if bar_high >= zone_lo and bar_high < swing_hi and price < swing_hi:
            return FibSignal(
                Signal.SHORT, round(zone_lo, 2), round(zone_hi, 2),
                stop=round(swing_hi, 2), target=round(swing_lo, 2),
                swing_hi=round(swing_hi, 2), swing_lo=round(swing_lo, 2),
                key=key,
            )
    return None
