"""Auto-start / manual-start guard: retired strategies trade on PAPER (forward
test) but never with real money (ALPACA_MODE=live)."""
import api.server as srv
from config import Config


def _patch_start(monkeypatch):
    calls = {}
    monkeypatch.setattr(srv, "_start_engine",
                        lambda dry_run=False: (calls.__setitem__("dry_run", dry_run) or (True, "started")))
    monkeypatch.setattr(Config, "ENGINE_AUTOSTART", True)
    monkeypatch.setattr(Config, "ENGINE_AUTOSTART_DRY", False)
    monkeypatch.setattr(Config, "ALPACA_API_KEY", "k")
    monkeypatch.setattr(Config, "ALPACA_API_SECRET", "s")
    return calls


def test_autostart_paper_trades_retired_strategy(monkeypatch):
    calls = _patch_start(monkeypatch)
    monkeypatch.setattr(Config, "STRATEGY", "positive")   # orb+sweep+fib, all retired
    monkeypatch.setattr(Config, "ALPACA_MODE", "paper")
    srv._autostart()
    assert calls["dry_run"] is False      # paper forward-test actually trades


def test_autostart_live_forces_signals_only_for_retired(monkeypatch):
    calls = _patch_start(monkeypatch)
    monkeypatch.setattr(Config, "STRATEGY", "positive")
    monkeypatch.setattr(Config, "ALPACA_MODE", "live")
    srv._autostart()
    assert calls["dry_run"] is True       # real money + retired -> signals only


def test_manual_paper_start_allowed_for_retired(monkeypatch):
    started = {"called": False}
    monkeypatch.setattr(srv, "_start_engine",
                        lambda dry_run=False: (started.__setitem__("called", True) or (True, "x")))
    monkeypatch.setattr(Config, "STRATEGY", "positive")
    monkeypatch.setattr(Config, "ALPACA_MODE", "paper")
    res = srv.api_start(dry_run=False, _="user")
    assert started["called"] is True and res["ok"] is True


def test_manual_live_start_refused_for_retired(monkeypatch):
    started = {"called": False}
    monkeypatch.setattr(srv, "_start_engine",
                        lambda dry_run=False: (started.__setitem__("called", True) or (True, "x")))
    monkeypatch.setattr(Config, "STRATEGY", "positive")
    monkeypatch.setattr(Config, "ALPACA_MODE", "live")
    res = srv.api_start(dry_run=False, _="user")
    assert res["ok"] is False and "RETIRED" in res["message"]
    assert started["called"] is False
