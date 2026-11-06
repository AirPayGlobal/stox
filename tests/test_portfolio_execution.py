import pytest

from portfolio.execution import SCENARIOS, get_scenario, simulate_equity_fill


def test_scenarios_present():
    assert set(SCENARIOS) == {"ideal", "base", "conservative"}
    with pytest.raises(ValueError):
        get_scenario("nope")


def test_ideal_fills_at_reference_full():
    f = simulate_equity_fill("BUY", 100.0, 10.0, "ideal", "k")
    assert f.outcome == "FILLED" and f.price == 100.0 and f.qty == 10.0


def test_buyer_never_beats_reference_off_ideal():
    for sc in ("base", "conservative"):
        f = simulate_equity_fill("BUY", 100.0, 10.0, sc, "seed")
        if f.outcome != "UNFILLED":
            assert f.price >= 100.0
    # seller never sells above reference
    f = simulate_equity_fill("SELL", 100.0, 10.0, "conservative", "seed")
    if f.outcome != "UNFILLED":
        assert f.price <= 100.0


def test_friction_orders_ideal_base_conservative():
    def buy(sc):
        return simulate_equity_fill("BUY", 100.0, 10.0, sc, "seedX").price
    assert buy("ideal") <= buy("base") <= buy("conservative")


def test_determinism():
    a = simulate_equity_fill("BUY", 100.0, 5.0, "conservative", "same")
    b = simulate_equity_fill("BUY", 100.0, 5.0, "conservative", "same")
    assert a == b


def test_min_notional_makes_tiny_order_unfilled():
    # 0.05 shares * $100 = $5 < $10 min -> capacity/rounding unfilled.
    f = simulate_equity_fill("BUY", 100.0, 0.05, "base", "k", fractional=True, min_notional=10.0)
    assert f.outcome == "UNFILLED"


def test_whole_share_rounding():
    f = simulate_equity_fill("BUY", 100.0, 3.9, "ideal", "k", fractional=False)
    assert f.qty == 3.0


def test_partial_and_unfilled_reachable():
    outs = {simulate_equity_fill("BUY", 100.0, 10.0, "conservative", f"k{i}").outcome
            for i in range(300)}
    assert "UNFILLED" in outs and "PARTIAL" in outs and "FILLED" in outs
