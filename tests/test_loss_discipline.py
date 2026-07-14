from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import Config
from trading.positions import PositionBook

ET = ZoneInfo("America/New_York")


def make_book(tmp_path) -> PositionBook:
    return PositionBook(path=str(tmp_path / "trades.json"))


def close_trade(book, underlying, pnl_sign, minutes_ago=0):
    sym = f"{underlying}260706C00500000"
    book.open(sym, underlying, "LONG", 1, entry_premium=1.00)
    exit_premium = 1.10 if pnl_sign > 0 else 0.90
    trade = book.close(sym, exit_premium, "TP" if pnl_sign > 0 else "SL")
    if minutes_ago:
        trade.closed_at = (datetime.now(ET) - timedelta(minutes=minutes_ago)).isoformat()
    return trade


def test_consecutive_losses_counts_streak(tmp_path):
    book = make_book(tmp_path)
    close_trade(book, "SPY", -1)
    close_trade(book, "SPY", -1)
    close_trade(book, "QQQ", -1)   # other underlying must not count
    assert book.consecutive_losses("SPY") == 2
    assert book.consecutive_losses("QQQ") == 1


def test_winner_resets_streak_and_cooldown(tmp_path):
    book = make_book(tmp_path)
    close_trade(book, "SPY", -1)
    close_trade(book, "SPY", -1)
    close_trade(book, "SPY", +1)
    assert book.consecutive_losses("SPY") == 0
    assert book.last_loss_time("SPY") is None


def test_last_loss_time_set_after_loss(tmp_path):
    book = make_book(tmp_path)
    close_trade(book, "SPY", -1)
    assert book.last_loss_time("SPY") is not None
    assert book.last_loss_time("QQQ") is None


def test_engine_entry_blocked_by_streak_and_cooldown(tmp_path, monkeypatch):
    import engine as eng

    monkeypatch.setattr(Config, "MAX_CONSECUTIVE_LOSSES", 3)
    monkeypatch.setattr(Config, "LOSS_COOLDOWN_MINUTES", 30)
    e = eng.TradingEngine(dry_run=True)
    e.book = make_book(tmp_path)

    # Fresh underlying: not blocked
    assert e._entry_blocked("SPY") is None

    # One recent loss: cooldown blocks
    close_trade(e.book, "SPY", -1)
    assert "loss cooldown" in e._entry_blocked("SPY")

    # Loss older than the cooldown: allowed again
    close_trade(e.book, "QQQ", -1, minutes_ago=45)
    assert e._entry_blocked("QQQ") is None

    # Three consecutive losses: done for the day even after cooldown expires
    for _ in range(3):
        close_trade(e.book, "IWM", -1, minutes_ago=60)
    assert "consecutive" in e._entry_blocked("IWM")


def test_win_cooldown_blocks_instant_reentry(tmp_path, monkeypatch):
    import engine as eng

    monkeypatch.setattr(Config, "WIN_COOLDOWN_MINUTES", 10)
    e = eng.TradingEngine(dry_run=True)
    e.book = make_book(tmp_path)

    # A take-profit seconds ago: short win-cooldown blocks the re-entry.
    close_trade(e.book, "SPY", +1)
    assert "win cooldown" in e._entry_blocked("SPY")

    # A win older than the win-cooldown: trading again.
    close_trade(e.book, "QQQ", +1, minutes_ago=15)
    assert e._entry_blocked("QQQ") is None
