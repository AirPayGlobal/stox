"""The engine computes and exposes entry guards from safeguard state."""
from config import Config


def _engine(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "STATE_DIR", str(tmp_path))
    from engine import TradingEngine
    return TradingEngine(dry_run=True)


def test_clean_engine_has_no_guards(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    # No unmanaged shares / mismatch / inflight, and data age is unknown offline.
    assert eng.compute_entry_guards() == {}


def test_unmanaged_shares_guard_surfaces(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    eng.unmanaged_stock = {"CVR": {"qty": 100, "market_value": 500, "unrealized_pl": 0}}
    guards = eng.compute_entry_guards()
    assert "unmanaged_shares" in guards
    assert eng.status()["entry_guards"] == guards


def test_recon_mismatch_guard_surfaces(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    eng._recon_mismatch = True
    assert "reconciliation" in eng.compute_entry_guards()


def test_persisted_fib_dedupe_blocks_refire(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    eng._acted_fib.add("SPY|3-9")
    # A fresh engine (simulated restart) still sees the acted key.
    eng2 = _engine(tmp_path, monkeypatch)
    assert "SPY|3-9" in eng2._acted_fib
