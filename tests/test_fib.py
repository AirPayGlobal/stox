import pandas as pd
import pytest

from analysis.fib import find_pivots, fib_signal, stop_distance_ok
from analysis.signals import Signal
from config import Config

ET = "America/New_York"


def bars(closes):
    idx = pd.date_range("2026-07-06 09:30", periods=len(closes), freq="1min", tz=ET)
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": c.shift(1).fillna(c.iloc[0]), "high": c + 0.05,
         "low": c - 0.05, "close": c, "volume": 1000},
        index=idx,
    )


@pytest.fixture(autouse=True)
def small_k(monkeypatch):
    monkeypatch.setattr(Config, "FIB_PIVOT_K", 2)
    monkeypatch.setattr(Config, "FIB_MIN_RANGE_PCT", 0.0)


# clean single-bar low@100 (idx4) then high@110 (idx9), retrace to 104.5 (idx12)
UP = [104, 103, 102, 101, 100, 101, 103, 105, 107, 110, 108, 106, 104.5]
# clean single-bar high@110 (idx4) then low@100 (idx9), retrace to 105.5 (idx12)
DOWN = [106, 107, 108, 109, 110, 109, 107, 105, 103, 100, 102, 104, 105.5]


def test_pivots_found():
    piv = find_pivots(bars(UP), k=2)
    kinds = [p.kind for p in piv]
    assert "H" in kinds and "L" in kinds


def test_long_signal_in_gold_zone():
    sig = fib_signal(bars(UP))
    assert sig is not None
    assert sig.direction == Signal.LONG
    assert sig.stop < 104.5 < sig.target          # stop below, target above
    assert sig.entry_lo <= 104.5 <= sig.entry_hi   # price in the gold zone
    assert abs(sig.target - 110.05) < 0.1 and abs(sig.stop - 99.95) < 0.1


def test_short_signal_in_gold_zone():
    sig = fib_signal(bars(DOWN))
    assert sig is not None
    assert sig.direction == Signal.SHORT
    assert sig.target < 105.5 < sig.stop           # target below, stop above
    assert sig.entry_lo <= 105.5 <= sig.entry_hi


def test_no_signal_when_price_outside_zone():
    # Same up-leg but only a shallow retrace to 108 (above the 0.5 level ~105).
    shallow = UP[:-1] + [108.0]
    assert fib_signal(bars(shallow)) is None


def test_min_range_filter_blocks_tiny_legs(monkeypatch):
    monkeypatch.setattr(Config, "FIB_MIN_RANGE_PCT", 0.05)  # need ~5% legs
    tiny = [100, 99.8, 99.6, 99.8, 100.2, 100.6, 100.4, 100.2, 100.1]
    assert fib_signal(bars(tiny)) is None


def test_touch_entry_fires_on_wick_into_zone():
    # Up-leg, then a bar whose LOW wicks into the gold zone (~104) but CLOSES
    # back above it (~106). The old close-in-band rule missed this; the touch
    # rule catches it.
    df = bars(UP[:-1] + [106.0])
    df.iloc[-1, df.columns.get_loc("low")] = 104.0   # wick down into the zone
    sig = fib_signal(df)
    assert sig is not None and sig.direction == Signal.LONG


def test_too_few_bars():
    assert fib_signal(bars([100, 101, 102])) is None


# --- The rewrite's whole point: the setup survives the pullback ---------------
# Origin low @100 (idx3), impulse high @110 (idx7), then a pullback that itself
# prints a pivot low @104 (idx10) inside the gold zone, then a bar still in the
# zone. The OLD "last two pivots" rule saw L@10 as the newest pivot and flipped
# the leg to a short; the swing-anchored rule keeps the long alive.
PULLBACK = [103, 102, 101, 100, 102, 105, 108, 110, 108, 105, 104, 104.5, 104.8]


def test_setup_survives_pullback_pivot():
    piv = find_pivots(bars(PULLBACK), k=2)
    kinds = [p.kind for p in piv]
    assert kinds.count("L") >= 2 and "H" in kinds   # the pullback did print an L
    sig = fib_signal(bars(PULLBACK))
    assert sig is not None
    assert sig.direction == Signal.LONG             # not flipped to SHORT
    assert abs(sig.stop - 99.95) < 0.1              # anchored on the origin low
    assert abs(sig.target - 110.05) < 0.1           # not the pullback low


def test_invalidated_when_price_breaks_origin():
    # Same structure but the last bar closes below the origin low — trend gone.
    broken = PULLBACK[:-1] + [99.0]
    assert fib_signal(bars(broken)) is None


def test_stop_distance_band(monkeypatch):
    monkeypatch.setattr(Config, "FIB_MIN_STOP_PCT", 0.001)   # >= $0.55 on $550
    monkeypatch.setattr(Config, "FIB_MAX_STOP_PCT", 0.010)   # <= $5.50 on $550
    assert stop_distance_ok(550.0, 548.0)    # $2.00 — in band
    assert not stop_distance_ok(550.0, 549.9)  # $0.10 — too tight
    assert not stop_distance_ok(550.0, 540.0)  # $10.0 — too wide
