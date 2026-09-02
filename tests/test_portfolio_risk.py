from portfolio.allocator import AccountOrder
from portfolio.profiles import get_profile
from portfolio.risk import RiskContext, RiskLimits, evaluate

TREND, RELMOM = "ETF_TREND_V1", "ETF_RELATIVE_MOMENTUM_V1"


def _ctx(**over):
    kw = dict(profile=get_profile("paper_10000"), prices={"SPY": 100.0, "QQQ": 200.0},
              sleeve_tradeable={TREND: True, RELMOM: True})
    kw.update(over)
    return RiskContext(**kw)


def _order(symbol="SPY", net=10.0, deltas=None):
    return AccountOrder(symbol, net, 100.0, deltas or {TREND: net})


def test_stale_data_halts_everything():
    d = evaluate([_order()], _ctx(data_age_seconds=999.0))
    assert d.halted and "stale_data" in d.halt
    assert d.allowed_orders == []


def test_recon_mismatch_and_inflight_halt():
    assert "reconciliation" in evaluate([_order()], _ctx(recon_mismatch=True)).halt
    assert "inflight_orders" in evaluate([_order()], _ctx(inflight_orders={"x"})).halt


def test_duplicate_intent_batch_halts():
    assert "duplicate_intent" in evaluate([_order()], _ctx(duplicate_intent=True)).halt


def test_portfolio_drawdown_blocks_buys_but_allows_sells():
    ctx = _ctx(portfolio_drawdown=0.20)     # > 0.15 halt threshold
    buy = evaluate([_order("SPY", 10.0)], ctx)
    assert buy.allowed_orders == [] and "SPY" in buy.rejected_orders
    sell = evaluate([_order("SPY", -10.0)], ctx)
    assert len(sell.allowed_orders) == 1    # sells still allowed


def test_sleeve_pause_isolates_one_sleeve():
    # Order driven by two sleeves; pause RELMOM -> only TREND's delta survives.
    o = _order("SPY", 30.0, {TREND: 20.0, RELMOM: 10.0})
    ctx = _ctx(sleeve_drawdown={RELMOM: 0.25})   # > 0.20 sleeve pause
    d = evaluate([o], ctx)
    assert RELMOM in d.paused_sleeves and TREND not in d.paused_sleeves
    assert len(d.allowed_orders) == 1
    assert d.allowed_orders[0].sleeve_deltas == {TREND: 20.0}
    assert round(d.allowed_orders[0].net_delta, 4) == 20.0


def test_non_tradeable_sleeve_paused():
    o = _order("SPY", 10.0, {TREND: 10.0})
    d = evaluate([o], _ctx(sleeve_tradeable={TREND: False}))
    assert TREND in d.paused_sleeves
    assert d.rejected_orders.get("SPY")     # nothing left to trade


def test_position_symbol_cap_rejects():
    # net buy notional above the 25% symbol cap ($2500) -> rejected.
    o = _order("SPY", 30.0, {TREND: 30.0})   # 30*100 = $3000 > $2500
    d = evaluate([o], _ctx())
    assert "SPY" in d.rejected_orders


def test_clean_order_passes():
    d = evaluate([_order("SPY", 10.0, {TREND: 10.0})], _ctx())
    assert len(d.allowed_orders) == 1 and not d.halted
