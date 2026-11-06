"""End-to-end rebalance orchestration (plumbing only; no real strategy)."""
from portfolio.book import PortfolioBook
from portfolio.engine import intent_batch_key, rebalance
from portfolio.intent import StrategyIntent
from portfolio.profiles import get_profile

TREND, RELMOM = "ETF_TREND_V1", "ETF_RELATIVE_MOMENTUM_V1"


def _it(sid, symbol, w):
    return StrategyIntent(sid, "v1", "hash1", "2026-08-28T16:00", "2026-08-28T16:00",
                          symbol, w, "weekly")


def _book(profile):
    return PortfolioBook(profile.id, {s: profile.budget_capital(s) for s in profile.sleeve_budgets})


def _tradeable():
    return {TREND: True, RELMOM: True}   # override registry for plumbing tests


def test_full_rebalance_fills_and_updates_books():
    p = get_profile("paper_10000")
    book = _book(p)
    intents = {TREND: [_it(TREND, "SPY", 0.5)], RELMOM: [_it(RELMOM, "QQQ", 0.5)]}
    res = rebalance(p, book, intents, {"SPY": 100.0, "QQQ": 200.0},
                    scenario="ideal", sleeve_tradeable=_tradeable())
    assert not res.halted
    assert {f["symbol"] for f in res.fills} == {"SPY", "QQQ"}
    assert book.net_positions()["SPY"] > 0
    assert book.sleeves[TREND].positions["SPY"].shares > 0


def test_shared_symbol_executes_once_attributes_both():
    p = get_profile("paper_10000")
    book = _book(p)
    intents = {TREND: [_it(TREND, "SPY", 0.4)], RELMOM: [_it(RELMOM, "SPY", 0.4)]}
    res = rebalance(p, book, intents, {"SPY": 100.0}, scenario="ideal",
                    sleeve_tradeable=_tradeable())
    spy_fills = [f for f in res.fills if f["symbol"] == "SPY"]
    assert len(spy_fills) == 1                       # executed once
    assert book.sleeves[TREND].positions["SPY"].shares > 0
    assert book.sleeves[RELMOM].positions["SPY"].shares > 0


def test_stale_data_halts_no_fills():
    p = get_profile("paper_10000")
    book = _book(p)
    intents = {TREND: [_it(TREND, "SPY", 0.5)]}
    res = rebalance(p, book, intents, {"SPY": 100.0}, scenario="ideal",
                    data_age_seconds=999.0, sleeve_tradeable=_tradeable())
    assert res.halted and res.fills == []
    assert book.net_positions() == {}


def test_duplicate_intent_batch_halts():
    p = get_profile("paper_10000")
    book = _book(p)
    intents = {TREND: [_it(TREND, "SPY", 0.5)]}
    key = intent_batch_key(p.id, intents)
    res = rebalance(p, book, intents, {"SPY": 100.0}, scenario="ideal",
                    processed_keys={key}, sleeve_tradeable=_tradeable())
    assert "duplicate_intent" in res.halt


def test_registry_pauses_unvalidated_sleeve_by_default():
    # Without the test override, ETF strategies are REGISTERED (not tradeable),
    # so the risk engine pauses them and nothing trades.
    p = get_profile("paper_10000")
    book = _book(p)
    intents = {TREND: [_it(TREND, "SPY", 0.5)]}
    res = rebalance(p, book, intents, {"SPY": 100.0}, scenario="ideal")
    assert TREND in res.paused_sleeves
    assert res.fills == []


def test_processed_keys_records_batch():
    p = get_profile("paper_10000")
    book = _book(p)
    seen: set = set()
    intents = {TREND: [_it(TREND, "SPY", 0.5)]}
    rebalance(p, book, intents, {"SPY": 100.0}, scenario="ideal",
              processed_keys=seen, sleeve_tradeable=_tradeable())
    assert intent_batch_key(p.id, intents) in seen
