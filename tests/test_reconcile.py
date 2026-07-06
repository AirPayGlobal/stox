import engine as eng
from trading.positions import PositionBook


def make_engine(tmp_path, monkeypatch, broker_positions, mid=1.00):
    monkeypatch.setattr(eng, "get_option_positions", lambda: broker_positions)
    monkeypatch.setattr(eng, "get_option_mid", lambda symbol: mid)
    e = eng.TradingEngine(dry_run=True)
    e.book = PositionBook(path=str(tmp_path / "trades.json"))
    return e


def test_adopts_orphaned_broker_position(tmp_path, monkeypatch):
    broker = {"SPY260706C00751000": {"qty": 50, "avg_entry": 0.83,
                                     "current_price": 0.70, "unrealized_pl": -650.0}}
    e = make_engine(tmp_path, monkeypatch, broker)
    e.reconcile_with_broker()

    assert len(e.book.open_trades) == 1
    t = e.book.open_trades[0]
    assert t.symbol == "SPY260706C00751000"
    assert t.underlying == "SPY"
    assert t.direction == "LONG"
    assert t.qty == 50
    assert t.entry_premium == 0.83
    assert t.stop_premium > 0  # exits are armed


def test_adopted_put_is_short_direction(tmp_path, monkeypatch):
    broker = {"QQQ260706P00720000": {"qty": 10, "avg_entry": 1.20,
                                     "current_price": 1.10, "unrealized_pl": -100.0}}
    e = make_engine(tmp_path, monkeypatch, broker)
    e.reconcile_with_broker()
    assert e.book.open_trades[0].direction == "SHORT"
    assert e.book.open_trades[0].underlying == "QQQ"


def test_known_position_not_duplicated(tmp_path, monkeypatch):
    broker = {"SPY260706C00751000": {"qty": 50, "avg_entry": 0.83,
                                     "current_price": 0.85, "unrealized_pl": 100.0}}
    e = make_engine(tmp_path, monkeypatch, broker)
    e.book.open("SPY260706C00751000", "SPY", "LONG", 50, 0.83)
    e.reconcile_with_broker()
    assert len(e.book.open_trades) == 1


def test_book_ghost_closed_as_external(tmp_path, monkeypatch):
    e = make_engine(tmp_path, monkeypatch, broker_positions={}, mid=0.55)
    e.book.open("SPY260706C00751000", "SPY", "LONG", 50, 0.65)
    e.reconcile_with_broker()
    assert len(e.book.open_trades) == 0
    closed = e.book.book.closed_trades[-1]
    assert closed.status == "EXTERNAL"
    assert closed.exit_premium == 0.55
