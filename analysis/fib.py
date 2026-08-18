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


def fib_signal(bars: pd.DataFrame) -> FibSignal | None:
    """
    `bars` — one session of 1-minute bars (oldest first). Returns a pullback
    signal if the latest completed bar sits in the gold zone of the most
    recent impulse leg, else None.
    """
    k = Config.FIB_PIVOT_K
    if len(bars) < 2 * k + 3:
        return None

    pivots = find_pivots(bars, k)
    if len(pivots) < 2:
        return None

    # The impulse leg is between the last two opposite-type pivots.
    last, prev = pivots[-1], pivots[-2]
    if last.kind == prev.kind:
        return None

    price = float(bars["close"].iloc[-1])
    lo = Config.FIB_ENTRY_LOW      # 0.5
    hi = Config.FIB_ENTRY_HIGH     # 0.618

    if last.kind == "H" and prev.kind == "L":
        swing_hi, swing_lo = last.price, prev.price
        rng = swing_hi - swing_lo
        if rng <= 0 or rng < price * Config.FIB_MIN_RANGE_PCT:
            return None
        # Gold zone (retracement down from the high): [0.618, 0.5] of the leg.
        zone_lo = swing_hi - hi * rng      # deeper (0.618)
        zone_hi = swing_hi - lo * rng      # shallower (0.5)
        if zone_lo <= price <= zone_hi:
            return FibSignal(
                Signal.LONG, round(zone_lo, 2), round(zone_hi, 2),
                stop=round(swing_lo, 2), target=round(swing_hi, 2),
                swing_hi=round(swing_hi, 2), swing_lo=round(swing_lo, 2),
                key=f"{prev.idx}-{last.idx}",
            )
    elif last.kind == "L" and prev.kind == "H":
        swing_hi, swing_lo = prev.price, last.price
        rng = swing_hi - swing_lo
        if rng <= 0 or rng < price * Config.FIB_MIN_RANGE_PCT:
            return None
        # Gold zone (retracement up from the low): [0.5, 0.618] of the leg.
        zone_lo = swing_lo + lo * rng      # shallower (0.5)
        zone_hi = swing_lo + hi * rng      # deeper (0.618)
        if zone_lo <= price <= zone_hi:
            return FibSignal(
                Signal.SHORT, round(zone_lo, 2), round(zone_hi, 2),
                stop=round(swing_hi, 2), target=round(swing_lo, 2),
                swing_hi=round(swing_hi, 2), swing_lo=round(swing_lo, 2),
                key=f"{prev.idx}-{last.idx}",
            )
    return None
