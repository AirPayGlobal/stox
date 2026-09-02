import pandas as pd

from backtest.regime import (
    in_window,
    realized_vol,
    tod_bucket,
    trend_state,
    vol_bucket,
)
from backtest.synth import synth_day, synth_history

ET = "America/New_York"


def _bars(closes):
    idx = pd.date_range("2026-07-06 09:30", periods=len(closes), freq="1min", tz=ET)
    c = pd.Series(closes, dtype=float, index=idx)
    return pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05,
                         "close": c, "volume": 1000}, index=idx)


def test_trend_state_up_down_flat():
    assert trend_state(_bars([100 + i * 0.2 for i in range(40)]), span=20) == "up"
    assert trend_state(_bars([100 - i * 0.2 for i in range(40)]), span=20) == "down"
    assert trend_state(_bars([100.0] * 40), span=20) == "flat"


def test_realized_vol_higher_for_choppier_series():
    calm = _bars([100 + i * 0.01 for i in range(60)])
    wild = _bars([100 + (5 if i % 2 else -5) for i in range(60)])
    assert realized_vol(wild) > realized_vol(calm)


def test_vol_bucket_thresholds():
    assert vol_bucket(0.05) == "low"
    assert vol_bucket(0.15) == "normal"
    assert vol_bucket(0.30) == "high"


def test_tod_buckets():
    def ts(h, m):
        return pd.Timestamp(f"2026-07-06 {h:02d}:{m:02d}", tz=ET)
    assert tod_bucket(ts(9, 45)) == "open"
    assert tod_bucket(ts(10, 30)) == "morning"
    assert tod_bucket(ts(12, 0)) == "lunch"
    assert tod_bucket(ts(14, 0)) == "afternoon"
    assert tod_bucket(ts(15, 30)) == "close"


def test_in_window_normal_and_midnight_spanning():
    ts = pd.Timestamp("2026-07-06 12:00", tz=ET)
    assert in_window(ts, "11:30-13:30")
    assert not in_window(ts, "13:30-15:00")
    assert not in_window(ts, "")
    night = pd.Timestamp("2026-07-06 01:00", tz=ET)
    assert in_window(night, "18:00-02:00")   # spans midnight


def test_synth_is_deterministic_and_shaped():
    a = synth_day("SPY", "2026-07-06")
    b = synth_day("SPY", "2026-07-06")
    assert a.equals(b)
    assert list(a.columns) == ["open", "high", "low", "close", "volume"]
    assert (a["high"] >= a["low"]).all()
    hist = synth_history("QQQ", 5)
    assert hist.index.normalize().nunique() == 5
