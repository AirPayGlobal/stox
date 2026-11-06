import pytest

from backtest.execution import (
    SCENARIOS,
    get_scenario,
    modelled_spread,
    simulate_fill,
)


def test_all_named_scenarios_exist():
    assert set(SCENARIOS) == {"baseline", "optimistic", "base", "conservative", "mid"}


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        get_scenario("nope")


def test_baseline_reproduces_fixed_half_spread():
    # Old behaviour: entry = mid + 0.02, exit = mid - 0.02, always fills full.
    buy = simulate_fill("BUY", 1.00, 1.00, "baseline", "k1", 10)
    sell = simulate_fill("SELL", 1.00, 1.00, "baseline", "k2", 10)
    assert buy.outcome == "FILLED" and buy.qty == 10
    assert buy.price == 1.02 and sell.price == 0.98


def test_entries_never_fill_below_mid_except_mid_scenario():
    for sc in ("optimistic", "base", "conservative"):
        f = simulate_fill("BUY", 2.00, 2.00, sc, "k", 10)
        if f.outcome != "UNFILLED":
            assert f.price >= 2.00          # buyer never does better than mid
    mid = simulate_fill("BUY", 2.00, 2.00, "mid", "k", 10)
    assert mid.price == 2.00                # only the diagnostic scenario fills at mid


def test_friction_escalates_optimistic_to_conservative():
    def buy_price(sc):
        return simulate_fill("BUY", 2.00, 2.00, sc, "seed-const", 10).price
    assert buy_price("optimistic") <= buy_price("base") <= buy_price("conservative")


def test_spread_wider_for_cheaper_premium():
    # As a fraction of premium, a $0.20 option is wider than a $5 option.
    sc = get_scenario("base")
    cheap = modelled_spread(0.20, sc) / 0.20
    rich = modelled_spread(5.00, sc) / 5.00
    assert cheap > rich


def test_determinism_same_key_same_fill():
    a = simulate_fill("BUY", 1.50, 1.50, "conservative", "same", 10)
    b = simulate_fill("BUY", 1.50, 1.50, "conservative", "same", 10)
    assert a == b


def test_unfilled_and_partial_are_reachable_and_labelled():
    outcomes = {
        simulate_fill("BUY", 0.15, 0.15, "conservative", f"key{i}", 10).outcome
        for i in range(400)
    }
    assert "UNFILLED" in outcomes
    assert "PARTIAL" in outcomes
    assert "FILLED" in outcomes


def test_partial_fill_reduces_qty():
    for i in range(400):
        f = simulate_fill("BUY", 0.15, 0.15, "conservative", f"p{i}", 10)
        if f.outcome == "PARTIAL":
            assert 1 <= f.qty < 10
            break
    else:
        pytest.fail("no partial fill produced")
