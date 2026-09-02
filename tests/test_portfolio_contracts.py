import pytest

from portfolio.intent import StrategyIntent, validate_intent_set
from portfolio.preregistration import DatasetFingerprint, PreRegistration
from portfolio.profiles import PROFILE_IDS, PROFILES, get_profile


def _intent(symbol="SPY", w=0.5, sid="ETF_TREND_V1"):
    return StrategyIntent(
        strategy_id=sid, version="v1", config_hash="abc123",
        signal_ts="2026-08-28T16:00:00", data_cutoff="2026-08-28T16:00:00",
        symbol=symbol, target_weight=w, horizon="weekly",
    )


# ---- StrategyIntent -----------------------------------------------------------
def test_intent_validation():
    with pytest.raises(ValueError):
        _intent(w=1.5)                       # weight out of range
    with pytest.raises(ValueError):
        StrategyIntent("s", "v", "", "t", "t", "SPY", 0.5, "weekly")  # no config_hash
    with pytest.raises(ValueError):
        _intent(symbol="")


def test_intent_set_rejects_dupes_and_overweight():
    validate_intent_set([_intent("SPY", 0.5), _intent("QQQ", 0.4)])   # ok
    with pytest.raises(ValueError):
        validate_intent_set([_intent("SPY", 0.6), _intent("SPY", 0.3)])  # dup symbol
    with pytest.raises(ValueError):
        validate_intent_set([_intent("SPY", 0.7), _intent("QQQ", 0.5)])  # sum > 1
    with pytest.raises(ValueError):
        validate_intent_set([_intent("SPY", 0.5, "A"), _intent("QQQ", 0.4, "B")])  # mixed


def test_confidence_is_not_used_for_weight():
    it = _intent()
    # confidence is a separate, reporting-only field; weight stands alone.
    assert it.confidence == 0.0 and it.target_weight == 0.5


# ---- CapitalProfile -----------------------------------------------------------
def test_four_immutable_profiles():
    assert PROFILE_IDS == ("paper_500", "paper_2500", "paper_10000", "paper_50000")
    p = get_profile("paper_500")
    assert p.capital == 500.0
    assert p.budget_capital("ETF_TREND_V1") == 300.0     # 60% of 500
    assert p.permits("ETF_TREND_V1") and not p.permits("STOCK_QVM_V1")


def test_profile_budgets_within_capital():
    for p in PROFILES.values():
        assert sum(p.sleeve_budgets.values()) + p.cash_reserve_pct <= 1.0 + 1e-9


def test_profile_mapping_is_frozen():
    p = get_profile("paper_2500")
    with pytest.raises(TypeError):
        p.sleeve_budgets["ETF_TREND_V1"] = 0.9   # MappingProxyType -> immutable


def test_unknown_profile_raises():
    with pytest.raises(ValueError):
        get_profile("paper_1000000")


# ---- PreRegistration ----------------------------------------------------------
def test_prereg_hash_stable_and_change_sensitive():
    ds = DatasetFingerprint("alpaca", ("SPY", "QQQ"), "2016-01-04", "2026-08-31", "all", 2680)
    base = dict(
        strategy_id="ETF_TREND_V1", version="v1", hypothesis="h", rationale="r",
        universe=("SPY", "QQQ"), eligibility_rule="pit", features={"mom": "12-1"},
        parameters={"lookbacks": "21/63/126/252"}, entry_exit_rebalance="weekly",
        cost_assumptions={"base": "spread+fees"}, capital_profiles=("paper_500",),
        metrics=("sharpe",), gates=("oos_positive",),
        test_boundaries={"holdout": "last year"}, prior_trials=0, dataset=ds,
    )
    a = PreRegistration(**base)
    b = PreRegistration(**base, registered_at="2026-08-31T00:00:00")
    assert a.config_hash() == b.config_hash()         # timestamp excluded
    c = PreRegistration(**{**base, "parameters": {"lookbacks": "10/20"}})
    assert a.config_hash() != c.config_hash()         # a real change is detected
    assert "config_hash" in a.to_json()
