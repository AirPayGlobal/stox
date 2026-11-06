import pandas as pd

from analysis.fib_filters import assess_filters, blocked_by
from analysis.signals import Signal
from config import Config

ET = "America/New_York"


def _bars(closes):
    idx = pd.date_range("2026-07-06 09:30", periods=len(closes), freq="1min", tz=ET)
    c = pd.Series(closes, dtype=float, index=idx)
    return pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05,
                         "close": c, "volume": 1000}, index=idx)


UP = _bars([100 + i * 0.2 for i in range(40)])
TS = pd.Timestamp("2026-07-06 12:00", tz=ET)


def test_all_filters_off_by_default_nothing_blocks():
    res = assess_filters(Signal.LONG, UP, TS, entry_spread_pct=0.30)
    assert all(not r.enabled for r in res)
    assert blocked_by(res) is None          # disabled filters never block


def test_trend_filter_blocks_counter_trend_only_when_enabled(monkeypatch):
    monkeypatch.setattr(Config, "FIB_FILTER_TREND", True)
    # SHORT into an up-trend should be blocked; LONG allowed.
    assert blocked_by(assess_filters(Signal.SHORT, UP, TS)) == "trend"
    assert blocked_by(assess_filters(Signal.LONG, UP, TS)) is None


def test_liquidity_filter_gates_wide_spread(monkeypatch):
    monkeypatch.setattr(Config, "FIB_FILTER_LIQUIDITY", True)
    monkeypatch.setattr(Config, "FIB_MAX_ENTRY_SPREAD_PCT", 0.08)
    assert blocked_by(assess_filters(Signal.LONG, UP, TS, entry_spread_pct=0.20)) == "liquidity"
    assert blocked_by(assess_filters(Signal.LONG, UP, TS, entry_spread_pct=0.05)) is None


def test_time_of_day_block_window(monkeypatch):
    monkeypatch.setattr(Config, "FIB_FILTER_TOD", True)
    monkeypatch.setattr(Config, "FIB_TOD_BLOCK", "11:30-13:30")
    assert blocked_by(assess_filters(Signal.LONG, UP, TS)) == "time_of_day"  # 12:00 blocked
    ok = pd.Timestamp("2026-07-06 14:00", tz=ET)
    assert blocked_by(assess_filters(Signal.LONG, UP, ok)) is None


def test_confirm_filter_requires_trend_continuation(monkeypatch):
    monkeypatch.setattr(Config, "FIB_FILTER_CONFIRM", True)
    # LONG: confirming bar must close >= the touch close.
    blocked = assess_filters(Signal.LONG, UP, TS, touch_close=100.0, confirm_close=99.0)
    assert blocked_by(blocked) == "confirm"
    ok = assess_filters(Signal.LONG, UP, TS, touch_close=100.0, confirm_close=100.5)
    assert blocked_by(ok) is None


def test_filters_measured_even_when_disabled():
    # value is recorded regardless of enabled, so the report can show effect.
    res = {r.name: r for r in assess_filters(Signal.LONG, UP, TS, entry_spread_pct=0.09)}
    assert res["liquidity"].value == 0.09
    assert res["trend"].value in ("up", "down", "flat")
