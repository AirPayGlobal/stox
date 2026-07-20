import pytest

from analysis.exits import effective_stop, fixed_target_active
from config import Config


def test_defaults_off(monkeypatch):
    monkeypatch.setattr(Config, "ORB_BREAKEVEN_TRIGGER_PCT", 0.0)
    monkeypatch.setattr(Config, "ORB_TRAIL_TRIGGER_PCT", 0.0)
    # No management: stop unchanged, fixed target active.
    assert effective_stop(entry=1.00, base_stop=0.70, mfe=1.40) == 0.70
    assert fixed_target_active() is True


def test_breakeven_raises_stop_to_entry(monkeypatch):
    monkeypatch.setattr(Config, "ORB_BREAKEVEN_TRIGGER_PCT", 0.25)
    monkeypatch.setattr(Config, "ORB_TRAIL_TRIGGER_PCT", 0.0)
    # Below the trigger: stop unchanged.
    assert effective_stop(1.00, 0.70, mfe=1.20) == 0.70
    # MFE crossed +25%: stop raised to entry (1.00).
    assert effective_stop(1.00, 0.70, mfe=1.25) == 1.00


def test_trailing_follows_peak_and_disables_fixed_target(monkeypatch):
    monkeypatch.setattr(Config, "ORB_BREAKEVEN_TRIGGER_PCT", 0.0)
    monkeypatch.setattr(Config, "ORB_TRAIL_TRIGGER_PCT", 0.30)
    monkeypatch.setattr(Config, "ORB_TRAIL_PCT", 0.20)
    assert fixed_target_active() is False
    # Below trail trigger: stop unchanged.
    assert effective_stop(1.00, 0.70, mfe=1.20) == 0.70
    # MFE at +50% (peak 1.50): trail 20% below -> 1.20.
    assert effective_stop(1.00, 0.70, mfe=1.50) == pytest.approx(1.20)
    # MFE at +200% (peak 3.00): trail -> 2.40 (winner runs).
    assert effective_stop(1.00, 0.70, mfe=3.00) == pytest.approx(2.40)


def test_trailing_never_lowers_stop(monkeypatch):
    monkeypatch.setattr(Config, "ORB_TRAIL_TRIGGER_PCT", 0.30)
    monkeypatch.setattr(Config, "ORB_TRAIL_PCT", 0.20)
    # Peak barely over trigger: trailed stop (1.31*0.8=1.048) exceeds base.
    assert effective_stop(1.00, 0.70, mfe=1.31) > 0.70
