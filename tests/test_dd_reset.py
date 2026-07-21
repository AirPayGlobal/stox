from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import Config
from reporting import rolling_drawdown
from trading.positions import PositionBook

ET = ZoneInfo("America/New_York")

_seq = [0]


def book_with_pnls(tmp_path, pnls, start_days_ago=5):
    _seq[0] += 1
    b = PositionBook(path=str(tmp_path / f"t{_seq[0]}.json"))
    base = datetime.now(ET) - timedelta(days=start_days_ago)
    for i, pnl in enumerate(pnls):
        sym = f"SPY2607{i:02d}C00500000"
        b.open(sym, "SPY", "LONG", 1, 1.00)
        t = b.close(sym, 1.00 + pnl / 100, "TP" if pnl > 0 else "SL")
        t.closed_at = (base + timedelta(hours=i)).isoformat()
    return b, base


def test_reset_marker_excludes_prior_trades(tmp_path):
    # +10,000 peak then -9,000 give-back -> drawdown 9,000 without a reset.
    b, base = book_with_pnls(tmp_path, [10_000, -9_000])
    full = rolling_drawdown(b, 30)
    assert full["drawdown"] == 9_000

    # Reset marker after both trades -> nothing counts -> no drawdown.
    marker = (base + timedelta(days=1)).isoformat()
    after = rolling_drawdown(b, 30, since_iso=marker)
    assert after["drawdown"] == 0
    assert after["peak"] == 0


def test_reset_persists_and_engine_clears_halt(tmp_path, monkeypatch):
    import engine as eng

    monkeypatch.setattr(Config, "DRAWDOWN_BASE", 100_000.0)
    monkeypatch.setattr(Config, "DRAWDOWN_HALT_PCT", 0.06)  # $6,000
    e = eng.TradingEngine(dry_run=True)
    e.risk.dd_reset_at = None  # start from a clean baseline
    b, _ = book_with_pnls(tmp_path, [12_000, -9_500])  # $9,500 give-back
    e.book = b

    e._update_drawdown()
    assert e._dd_state == "HALTED"

    # Reset rebaselines the drawdown; the halt clears on the next evaluation.
    e.risk.reset()
    e._update_drawdown()
    assert e._dd_state == "OK"
    assert e.risk.dd_reset_at is not None
