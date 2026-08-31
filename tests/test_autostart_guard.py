"""Auto-start and manual-start must never live-trade a retired strategy."""
import api.server as srv
from config import Config


def test_autostart_forces_signals_only_for_retired(monkeypatch):
    calls = {}

    def fake_start(dry_run=False):
        calls["dry_run"] = dry_run
        return True, "started"

    monkeypatch.setattr(srv, "_start_engine", fake_start)
    monkeypatch.setattr(Config, "ENGINE_AUTOSTART", True)
    monkeypatch.setattr(Config, "ENGINE_AUTOSTART_DRY", False)
    monkeypatch.setattr(Config, "ALPACA_API_KEY", "k")
    monkeypatch.setattr(Config, "ALPACA_API_SECRET", "s")
    monkeypatch.setattr(Config, "STRATEGY", "fib")  # retired

    srv._autostart()
    assert calls["dry_run"] is True  # forced signals-only despite AUTOSTART_DRY=false


def test_manual_live_start_refused_for_retired(monkeypatch):
    started = {"called": False}
    monkeypatch.setattr(srv, "_start_engine",
                        lambda dry_run=False: started.__setitem__("called", True) or (True, "x"))
    monkeypatch.setattr(Config, "STRATEGY", "fib")

    res = srv.api_start(dry_run=False, _="user")
    assert res["ok"] is False
    assert "RETIRED" in res["message"]
    assert started["called"] is False       # never reached _start_engine

    # Signals-only (dry_run) is allowed.
    res2 = srv.api_start(dry_run=True, _="user")
    assert started["called"] is True
