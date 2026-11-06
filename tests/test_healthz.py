"""The public healthz probe surfaces non-secret runtime config."""
import api.server as srv
from config import Config


def test_healthz_reports_runtime_config(monkeypatch):
    monkeypatch.setattr(Config, "STRATEGY", "positive")
    monkeypatch.setattr(Config, "ALPACA_MODE", "paper")
    h = srv.healthz()
    assert h["ok"] is True
    assert h["strategy"] == "positive"
    assert h["mode"] == "paper"
    assert h["real_money"] is False
    assert set(h["retired_active"]) == {"orb", "sweep", "fib"}
    assert "version" in h and "underlyings" in h


def test_healthz_flags_real_money(monkeypatch):
    monkeypatch.setattr(Config, "STRATEGY", "fib")
    monkeypatch.setattr(Config, "ALPACA_MODE", "live")
    h = srv.healthz()
    assert h["real_money"] is True
    assert h["retired_active"] == ["fib"]     # fib is retired -> signals-only live


def test_healthz_no_secrets():
    h = srv.healthz()
    blob = str(h).lower()
    assert "pass" not in blob and "secret" not in blob and "key" not in blob
