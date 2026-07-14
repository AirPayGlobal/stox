import numpy as np
import pandas as pd
import pytest

from analysis.indicators import relative_volume
from analysis.signals import Signal, SignalContext, generate_signal
from config import Config


def make_session(closes, volume=10_000):
    idx = pd.date_range("2026-07-06 09:30", periods=len(closes), freq="5min",
                        tz="America/New_York")
    closes = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({
        "open": closes.shift(1).fillna(closes.iloc[0]),
        "high": closes + 0.10,
        "low": closes - 0.10,
        "close": closes,
        "volume": volume,
    })


UP = list(np.linspace(500, 506, 20))


@pytest.fixture(autouse=True)
def filters_on(monkeypatch):
    monkeypatch.setattr(Config, "ORB_FILTER_VWAP", True)
    monkeypatch.setattr(Config, "ORB_FILTER_RVOL", True)
    monkeypatch.setattr(Config, "ORB_FILTER_OR_ATR", True)


def test_uptrend_passes_vwap_filter():
    # Rising session: price > vwap, slope > 0 -> aligned.
    assert generate_signal(make_session(UP)).signal == Signal.LONG


def test_vwap_alignment_branch_directly():
    from analysis.signals import _filter_reason

    # LONG below VWAP -> blocked; LONG above VWAP but slope down -> blocked.
    assert _filter_reason(Signal.LONG, 100.0, 101.0, +0.5, 1.0, None) == "vwap_alignment"
    assert _filter_reason(Signal.LONG, 102.0, 101.0, -0.1, 1.0, None) == "vwap_alignment"
    # Aligned LONG passes.
    assert _filter_reason(Signal.LONG, 102.0, 101.0, +0.1, 1.0, None) is None
    # SHORT mirror.
    assert _filter_reason(Signal.SHORT, 102.0, 101.0, -0.5, 1.0, None) == "vwap_alignment"
    assert _filter_reason(Signal.SHORT, 100.0, 101.0, -0.5, 1.0, None) is None


def test_rvol_filter_blocks_quiet_tape():
    ctx = SignalContext(rvol=0.8)   # 80% of normal volume
    result = generate_signal(make_session(UP), ctx)
    assert result.signal == Signal.FLAT
    assert "rvol" in result.details["filtered"]


def test_rvol_filter_passes_active_tape():
    ctx = SignalContext(rvol=1.6)
    assert generate_signal(make_session(UP), ctx).signal == Signal.LONG


def test_or_atr_filter_blocks_narrow_range():
    # OR size here is ~0.5; with daily ATR 10 the ratio is 0.05 << 0.30.
    ctx = SignalContext(daily_atr=10.0)
    result = generate_signal(make_session(UP), ctx)
    assert result.signal == Signal.FLAT
    assert "or/atr" in result.details["filtered"]


def test_or_atr_filter_passes_normal_range():
    # ratio ~0.5/1.0 = 0.5, inside [0.30, 1.00]
    ctx = SignalContext(daily_atr=1.0)
    assert generate_signal(make_session(UP), ctx).signal == Signal.LONG


def test_missing_context_skips_data_filters():
    # No ctx at all: RVOL and OR/ATR filters must not block.
    assert generate_signal(make_session(UP), None).signal == Signal.LONG


def test_filters_can_be_disabled(monkeypatch):
    monkeypatch.setattr(Config, "ORB_FILTER_RVOL", False)
    ctx = SignalContext(rvol=0.5)
    assert generate_signal(make_session(UP), ctx).signal == Signal.LONG


def test_relative_volume_math():
    today = pd.Series([100, 100, 100])            # 300 so far
    prior = [pd.Series([50] * 13), pd.Series([150] * 13)]  # same-time avg = 300
    assert relative_volume(today, prior) == 1.0
    assert relative_volume(today, []) is None
    assert relative_volume(pd.Series([], dtype=float), prior) is None
