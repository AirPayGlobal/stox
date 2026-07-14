from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.htf import completed_bars, resample_bars
from analysis.signals import Signal
from analysis.sweeps import (
    find_fvg,
    level_sweep,
    overnight_range,
    prev_day_level_sweep,
    rr_target,
    session_range,
    stop_distance_ok,
    sweep_reclaim,
)
from config import Config

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


# ------------------------------------------------------------ overnight range
def _ext_session():
    """Prev-day RTH + post-market + today's pre-market + today's RTH bars."""
    rows = []
    # prev day RTH (high 105, low 100) — must NOT count toward the ON range
    rows += [("2026-07-02 10:00", 102, 105, 100, 103)]
    rows += [("2026-07-02 15:55", 103, 104, 102, 103)]
    # overnight/post + pre-market (high 104.5, low 98.5) — the ON range
    rows += [("2026-07-02 18:00", 103, 104.5, 102.5, 103.5)]
    rows += [("2026-07-06 07:00", 103, 103.5, 98.5, 99.0)]
    # today's RTH — must NOT count
    rows += [("2026-07-06 10:00", 99, 110, 97, 108)]
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz=ET) for t, *_ in rows])
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 100} for _, o, h, l, c in rows],
        index=idx,
    )


def test_overnight_range_uses_only_extended_window():
    rng = overnight_range(_ext_session(), pd.Timestamp("2026-07-06").date())
    assert rng == (104.5, 98.5)


def test_overnight_range_premarket_only_fallback():
    df = _ext_session()
    today_pre = df[df.index >= pd.Timestamp("2026-07-06 07:00", tz=ET)]
    rng = overnight_range(today_pre.iloc[:1], pd.Timestamp("2026-07-06").date())
    assert rng == (103.5, 98.5)


def test_overnight_range_empty_when_no_prior_bars():
    df = _ext_session()
    rth_only_today = df[df.index >= pd.Timestamp("2026-07-06 10:00", tz=ET)]
    assert overnight_range(rth_only_today, pd.Timestamp("2026-07-06").date()) is None


def test_session_range_premarket_window():
    # 04:00-09:30 window must exclude the prev-day 18:00 bar (high 104.5)
    rng = session_range(_ext_session(), pd.Timestamp("2026-07-06").date(), "04:00-09:30")
    assert rng == (103.5, 98.5)


def test_session_range_spanning_midnight():
    # 18:00-08:00 spans midnight: includes the PRIOR CALENDAR DAY's evening
    # bar and today's pre-market bar.
    idx = pd.DatetimeIndex([
        pd.Timestamp("2026-07-05 18:30", tz=ET),   # prior evening, high 104.5
        pd.Timestamp("2026-07-06 07:00", tz=ET),   # pre-market, low 98.5
        pd.Timestamp("2026-07-06 10:00", tz=ET),   # RTH — must be excluded
    ])
    df = pd.DataFrame(
        {"open": [103, 103, 99], "high": [104.5, 103.5, 110],
         "low": [102.5, 98.5, 97], "close": [103.5, 99, 108], "volume": 100},
        index=idx,
    )
    rng = session_range(df, pd.Timestamp("2026-07-06").date(), "18:00-08:00")
    assert rng == (104.5, 98.5)


def test_session_range_empty_window_and_bad_format():
    df = _ext_session()
    day = pd.Timestamp("2026-07-06").date()
    assert session_range(df, day, "01:00-02:00") is None   # no bars in window
    assert session_range(df, day, "garbage") is None       # malformed


def test_overnight_low_sweep_reclaim_is_long():
    # 5-min bar dips below the ON low 98.5 and closes back above, bullish
    df = candles([(99.0, 99.4, 98.2, 99.3)], freq="5min")
    sig = level_sweep(df, level_high=104.5, level_low=98.5, kind="overnight_range")
    assert sig is not None
    assert sig.direction == Signal.LONG
    assert sig.kind == "overnight_range"
    assert sig.extreme == 98.2


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


def test_stop_distance_band():
    spot = 700.0
    # defaults: min 0.15% (1.05), max 1.0% (7.00)
    assert not stop_distance_ok(spot, spot - 0.50)    # too tight — noise
    assert stop_distance_ok(spot, spot - 2.00)        # tradeable
    assert stop_distance_ok(spot, spot + 5.00)        # tradeable (short side)
    assert not stop_distance_ok(spot, spot - 11.48)   # swing-sized wick — skip
    # boundaries
    assert stop_distance_ok(spot, spot - spot * Config.SWEEP_MAX_STOP_PCT)
    assert not stop_distance_ok(spot, spot - spot * Config.SWEEP_MAX_STOP_PCT * 1.01)


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
