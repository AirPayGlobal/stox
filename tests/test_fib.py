import pandas as pd
import pytest

from analysis.fib import find_pivots, fib_signal
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


def test_too_few_bars():
    assert fib_signal(bars([100, 101, 102])) is None
