import pytest

from strategy.registry import (
    LIFECYCLE,
    REGISTRY,
    StrategyRecord,
    as_dicts,
    get,
    is_retired,
    is_tradeable,
    retired,
    tradeable,
)


def test_legacy_option_strategies_are_retired():
    for sid in ("fib", "orb", "sweep"):
        assert is_retired(sid), f"{sid} must be retired"
        assert get(sid).lifecycle == "RETIRED"
        assert get(sid).status_reason  # a reason is recorded


def test_retired_strategies_are_not_tradeable():
    assert not any(r.tradeable for r in retired())
    assert not is_tradeable("fib")


def test_no_strategy_is_tradeable_yet():
    # Nothing has passed validation to PAPER, so nothing may trade.
    assert tradeable() == []


def test_etf_strategies_registered_not_tradeable():
    for sid in ("ETF_TREND_V1", "ETF_RELATIVE_MOMENTUM_V1"):
        r = get(sid)
        assert r.lifecycle == "REGISTERED"
        assert not r.tradeable            # registered != tradeable
        assert r.evidence.endswith(".md")  # points at its pre-registration spec


def test_unknown_strategy_is_neither_retired_nor_tradeable():
    assert not is_retired("does_not_exist")
    assert not is_tradeable("does_not_exist")


def test_invalid_lifecycle_rejected():
    with pytest.raises(ValueError):
        StrategyRecord(id="x", name="x", version="1", lifecycle="BOGUS",
                       asset_class="etf", cadence="weekly", rationale="", status_reason="")


def test_as_dicts_serializable_shape():
    ds = as_dicts()
    assert {d["id"] for d in ds} >= {"fib", "orb", "sweep", "ETF_TREND_V1"}
    for d in ds:
        assert d["lifecycle"] in LIFECYCLE
        assert isinstance(d["eligible_profiles"], list)
        assert "tradeable" in d
