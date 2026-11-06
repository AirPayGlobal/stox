from portfolio.book import PortfolioBook, SleeveBook, attribute


def test_sleeve_fill_updates_cash_and_pnl():
    sb = SleeveBook("ETF_TREND_V1", 1000.0)
    sb.apply_fill("SPY", 2.0, 100.0, fee=1.0)      # buy 2 @ 100, $1 fee
    assert round(sb.cash, 2) == 799.0              # 1000 - 200 - 1
    sb.apply_fill("SPY", -2.0, 110.0, fee=1.0)     # sell 2 @ 110
    assert round(sb.realized_pnl, 2) == 20.0       # (110-100)*2
    assert "SPY" not in sb.positions               # flat
    assert round(sb.market_value({"SPY": 110}), 2) == 1018.0  # 1000 + 20 - 2 fees


def test_attribution_conserves_and_splits_by_delta():
    # Two sleeves both want SPY; net fill split proportional to requested delta.
    a = attribute({"A": 6.0, "B": 2.0}, filled_qty=8.0, ref_price=100.0)
    assert round(a["A"], 6) == 6.0 and round(a["B"], 6) == 2.0
    # Partial fill: 4 of 8 requested -> halved pro-rata.
    a2 = attribute({"A": 6.0, "B": 2.0}, filled_qty=4.0, ref_price=100.0)
    assert round(a2["A"], 6) == 3.0 and round(a2["B"], 6) == 1.0
    assert round(sum(a2.values()), 6) == 4.0       # conserves the fill


def test_attribution_internal_cross_when_offsetting():
    # A buys 5, B sells 5 -> net 0 -> internal cross, no external fill needed.
    a = attribute({"A": 5.0, "B": -5.0}, filled_qty=0.0, ref_price=100.0)
    assert a["A"] == 5.0 and a["B"] == -5.0


def test_shared_symbol_executed_once_pnl_attributable():
    pb = PortfolioBook("paper_10000", {"A": 5000.0, "B": 5000.0})
    # Both sleeves want to buy SPY: A wants 30 shares, B wants 10 -> net 40 filled once.
    pb.apply_attributed("SPY", {"A": 30.0, "B": 10.0}, filled_qty=40.0, price=100.0, total_fee=4.0)
    assert pb.net_positions()["SPY"] == 40.0
    assert round(pb.sleeves["A"].positions["SPY"].shares, 6) == 30.0
    assert round(pb.sleeves["B"].positions["SPY"].shares, 6) == 10.0
    # Fee split by |attributed|: A pays 3, B pays 1.
    assert round(pb.sleeves["A"].fees_paid, 6) == 3.0
    assert round(pb.sleeves["B"].fees_paid, 6) == 1.0


def test_restart_recovery(tmp_path):
    path = str(tmp_path / "portfolio.json")
    pb = PortfolioBook("paper_500", {"A": 300.0, "B": 200.0}, path=path)
    pb.apply_attributed("SPY", {"A": 1.0}, filled_qty=1.0, price=100.0, total_fee=0.5)
    # New instance from disk (simulated restart) sees the same positions/cash.
    pb2 = PortfolioBook("paper_500", path=path)
    assert pb2.net_positions()["SPY"] == 1.0
    assert round(pb2.sleeves["A"].cash, 2) == round(pb.sleeves["A"].cash, 2)
