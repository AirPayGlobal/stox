from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.htf import completed_bars, resample_bars
from analysis.signals import Signal
from analysis.sweeps import (
    find_fvg,
    prev_day_level_sweep,
    rr_target,
    sweep_reclaim,
)

ET = ZoneInfo("America/New_York")


def candles(rows, start="2026-07-06 09:30", freq="60min"):
    """rows: list of (open, high, low, close) tuples."""
    idx = pd.date_range(start=start, periods=len(rows), freq=freq, tz=ET)
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 1000} for o, h, l, c in rows],
        index=idx,
    )


# ------------------------------------------------------------ sweep_reclaim
def test_bullish_sweep_reclaim():
    df = candles([
        (101, 102, 100, 100.5),        # previous candle, low = 100
        (100.5, 101.5, 99.0, 101.2),   # sweeps below 100, closes back above, bullish
    ])
    sig = sweep_reclaim(df)
    assert sig is not None
    assert sig.direction == Signal.LONG
    assert sig.swept_level == 100.0
    assert sig.extreme == 99.0         # wick low -> stop basis


def test_bearish_sweep_reclaim():
    df = candles([
        (100, 101, 99.5, 100.5),       # previous candle, high = 101
        (100.5, 102.0, 99.8, 99.9),    # sweeps above 101, closes back below, bearish
    ])
    sig = sweep_reclaim(df)
    assert sig is not None
    assert sig.direction == Signal.SHORT
    assert sig.extreme == 102.0


def test_no_signal_without_sweep():
    df = candles([
        (100, 101, 100, 100.5),
        (100.5, 101.5, 100.2, 101.0),  # never traded below previous low
    ])
    assert sweep_reclaim(df) is None


def test_no_signal_when_close_stays_below():
    df = candles([
        (101, 102, 100, 100.5),
        (100.5, 100.8, 99.0, 99.5),    # swept but CLOSED below -> continuation, not reclaim
    ])
    assert sweep_reclaim(df) is None


def test_trend_filter_requires_opposing_prev_candle():
    up_prev = candles([
        (100, 102, 100, 101.5),        # previous candle closed UP
        (101.5, 102, 99.9, 101.8),     # sweeps its low, closes bullish
    ])
    assert sweep_reclaim(up_prev, trend_filter=False) is not None
    assert sweep_reclaim(up_prev, trend_filter=True) is None


# ------------------------------------------------------------ prev-day levels
def test_prev_day_low_sweep():
    df = candles(
        [(100, 100.5, 98.8, 100.2)],   # dips below prev day low 99, closes back above
        freq="5min",
    )
    sig = prev_day_level_sweep(df, prev_day_high=105.0, prev_day_low=99.0)
    assert sig is not None
    assert sig.direction == Signal.LONG
    assert sig.kind == "prev_day_level"


def test_prev_day_high_break_no_reclaim_is_not_signal():
    df = candles([(104, 106, 103.9, 105.5)], freq="5min")  # breaks and HOLDS above
    assert prev_day_level_sweep(df, prev_day_high=105.0, prev_day_low=99.0) is None


# ------------------------------------------------------------ FVG
def test_bullish_fvg_detected():
    df = candles([
        (100, 100.5, 99.5, 100.2),
        (100.2, 101.5, 100.1, 101.4),
        (101.4, 102.5, 101.0, 102.3),  # low 101.0 > candle-0 high 100.5 -> gap
    ], freq="5min")
    zone = find_fvg(df, Signal.LONG)
    assert zone == (100.5, 101.0)


def test_no_fvg_in_tight_range():
    df = candles([
        (100, 100.5, 99.5, 100.2),
        (100.2, 100.6, 99.8, 100.3),
        (100.3, 100.7, 99.9, 100.4),
    ], freq="5min")
    assert find_fvg(df, Signal.LONG) is None


# ------------------------------------------------------------ RR / resample
def test_rr_target_long_and_short():
    assert rr_target(entry=100.0, stop=99.0, rr=2.0) == 102.0
    assert rr_target(entry=100.0, stop=101.0, rr=2.0) == 98.0


def test_resample_aligns_to_0930():
    idx = pd.date_range("2026-07-06 09:30", periods=24, freq="5min", tz=ET)
    df = pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}, index=idx
    )
    htf = resample_bars(df, 60)
    assert htf.index[0].time().isoformat() == "09:30:00"
    assert htf.index[1].time().isoformat() == "10:30:00"
    assert htf["volume"].iloc[0] == 1200  # 12 five-minute bars


def test_completed_bars_drops_forming_candle():
    idx = pd.date_range("2026-07-06 09:30", periods=3, freq="60min", tz=ET)
    df = pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}, index=idx
    )
    # At 11:45 the 11:30 candle is still forming.
    asof = datetime(2026, 7, 6, 11, 45, tzinfo=ET)
    done = completed_bars(df, 60, asof)
    assert len(done) == 2
