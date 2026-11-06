from portfolio.book import PortfolioBook
from portfolio.contracts import (
    command_centre,
    portfolio_lab_contract,
    profiles_contract,
    research_leaderboard_contract,
    strategy_registry_contract,
)


def test_profiles_contract_lists_four():
    c = profiles_contract()
    assert [p["id"] for p in c["profiles"]] == \
        ["paper_500", "paper_2500", "paper_10000", "paper_50000"]


def test_command_centre_paper_and_empty_by_default():
    c = command_centre("paper_10000")
    assert c["state"] == "PAPER"
    assert c["tradeable_sleeves"] == []          # nothing validated yet
    assert "RETIRED" in c["sleeves_by_lifecycle"]
    assert c["account"]["market_value"] is None  # no live session


def test_command_centre_with_live_book():
    book = PortfolioBook("paper_10000", {"ETF_TREND_V1": 3500.0})
    book.apply_attributed("SPY", {"ETF_TREND_V1": 10.0}, 10.0, 100.0)
    c = command_centre("paper_10000", book=book, prices={"SPY": 105.0})
    assert c["account"]["net_positions"]["SPY"] == 10.0
    assert c["account"]["sleeve_attribution"]["ETF_TREND_V1"]["market_value"] is not None


def test_registry_contract_orders_and_includes_retired():
    c = strategy_registry_contract()
    ids = [s["id"] for s in c["strategies"]]
    assert {"fib", "orb", "sweep", "ETF_TREND_V1"} <= set(ids)


def test_lab_contract_shape():
    c = portfolio_lab_contract()
    assert len(c["profiles"]) == 4
    assert "correlation_matrix" in c


def test_leaderboard_not_ranked_by_pnl():
    c = research_leaderboard_contract()
    assert c["default_sort"] == "robustness_score"
    assert all("robustness_score" in r for r in c["rows"])
    # data-blocked strategies carry a warning
    qvm = [r for r in c["rows"] if r["id"] == "fib"]
    assert qvm and "data_warnings" in qvm[0]
