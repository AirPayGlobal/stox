from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import Config
from reporting import rolling_drawdown
from trading.positions import PositionBook

ET = ZoneInfo("America/New_York")


_seq = [0]


def book_with_pnls(tmp_path, pnls):
    """Closed trades with the given pnls, one per day, oldest first. Each call
    uses a fresh file so books don't accumulate across cases."""
    _seq[0] += 1
    book = PositionBook(path=str(tmp_path / f"trades_{_seq[0]}.json"))
    for i, pnl in enumerate(pnls):
        sym = f"SPY2607{i:02d}C00500000"
        book.open(sym, "SPY", "LONG", 1, 1.00)
        t = book.close(sym, 1.00 + pnl / 100, "TP" if pnl > 0 else "SL")
        when = datetime.now(ET) - timedelta(days=len(pnls) - i)
        t.closed_at = when.isoformat()
    return book


def test_no_drawdown_when_rising(tmp_path):
    dd = rolling_drawdown(book_with_pnls(tmp_path, [100, 100, 100]), 30)
    assert dd["peak"] == 300
    assert dd["current"] == 300
    assert dd["drawdown"] == 0


def test_drawdown_measures_giveback_from_peak(tmp_path):
    # +600 peak, then -400 -> current +200, drawdown 400.
    dd = rolling_drawdown(book_with_pnls(tmp_path, [600, -200, -200]), 30)
    assert dd["peak"] == 600
    assert dd["current"] == 200
    assert dd["drawdown"] == 400


def test_no_phantom_drawdown_before_first_profit(tmp_path):
    # Straight losses: peak stays 0, drawdown = magnitude of losses.
    dd = rolling_drawdown(book_with_pnls(tmp_path, [-100, -100]), 30)
    assert dd["peak"] == 0
    assert dd["drawdown"] == 200


def test_engine_breaker_states(tmp_path, monkeypatch):
    import engine as eng

    monkeypatch.setattr(Config, "DRAWDOWN_BASE", 100_000.0)
    monkeypatch.setattr(Config, "DRAWDOWN_REDUCE_PCT", 0.04)  # $4,000
    monkeypatch.setattr(Config, "DRAWDOWN_HALT_PCT", 0.06)    # $6,000
    e = eng.TradingEngine(dry_run=True)

    # peak +10,000 then give back 4,500 -> REDUCED (>= 4k, < 6k)
    e.book = book_with_pnls(tmp_path, [10_000, -4_500])
    e._update_drawdown()
    assert e._dd_state == "REDUCED"

    # give back 7,000 -> HALTED
    e.book = book_with_pnls(tmp_path, [10_000, -7_000])
    e._update_drawdown()
    assert e._dd_state == "HALTED"

    # only 2,000 off peak -> OK
    e.book = book_with_pnls(tmp_path, [10_000, -2_000])
    e._update_drawdown()
    assert e._dd_state == "OK"
