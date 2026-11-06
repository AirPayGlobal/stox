from portfolio.allocator import plan
from portfolio.book import PortfolioBook
from portfolio.intent import StrategyIntent
from portfolio.profiles import get_profile

TREND, RELMOM = "ETF_TREND_V1", "ETF_RELATIVE_MOMENTUM_V1"


def _it(sid, symbol, w):
    return StrategyIntent(sid, "v1", "h", "2026-08-28T16:00", "2026-08-28T16:00",
                          symbol, w, "weekly")


def _books(profile):
    return PortfolioBook(profile.id, {s: profile.budget_capital(s)
                                      for s in profile.sleeve_budgets}).sleeves


def test_basic_targets_become_orders():
    p = get_profile("paper_10000")   # trend 3500, relmom 2500; symbol cap 25% = $2500
    prices = {"SPY": 100.0, "QQQ": 200.0}
    # weights kept under the per-symbol cap so this tests plain mechanics.
    intents = {TREND: [_it(TREND, "SPY", 0.5)], RELMOM: [_it(RELMOM, "QQQ", 0.8)]}
    orders = {o.symbol: o for o in plan(p, _books(p), intents, prices)}
    assert round(orders["SPY"].net_delta, 4) == 17.5    # 3500*0.5/100
    assert round(orders["QQQ"].net_delta, 4) == 10.0    # 2500*0.8/200


def test_shared_symbol_nets_into_one_order():
    p = get_profile("paper_10000")
    prices = {"SPY": 100.0}
    # trend 0.4*3500=1400 + relmom 0.4*2500=1000 = 2400 (< $2500 symbol cap).
    intents = {TREND: [_it(TREND, "SPY", 0.4)], RELMOM: [_it(RELMOM, "SPY", 0.4)]}
    orders = plan(p, _books(p), intents, prices)
    assert len(orders) == 1
    o = orders[0]
    assert round(o.net_delta, 4) == 24.0                 # 2400/100, one netted order
    assert set(o.sleeve_deltas) == {TREND, RELMOM}       # attribution preserved


def test_symbol_weight_cap_enforced():
    p = get_profile("paper_10000")   # max_symbol_weight 0.25 -> $2500 cap
    prices = {"SPY": 100.0}
    # Both sleeves fully into SPY would be $6000; cap to $2500 -> 25 shares.
    intents = {TREND: [_it(TREND, "SPY", 1.0)], RELMOM: [_it(RELMOM, "SPY", 1.0)]}
    o = plan(p, _books(p), intents, prices)[0]
    assert round(o.net_delta, 4) == 25.0


def test_gross_and_cash_reserve_cap():
    p = get_profile("paper_500")     # gross<=90%, reserve 10% -> <= $450 invested
    prices = {"SPY": 100.0, "QQQ": 100.0, "IWM": 100.0}
    intents = {TREND: [_it(TREND, "SPY", 0.5), _it(TREND, "QQQ", 0.5)],
               RELMOM: [_it(RELMOM, "IWM", 1.0)]}
    orders = plan(p, _books(p), intents, prices)
    invested = sum(abs(o.net_delta) * o.ref_price for o in orders)
    assert invested <= 450.0 + 1e-6


def test_min_order_notional_band_suppresses_tiny_trades():
    p = get_profile("paper_500")     # min order $10
    prices = {"SPY": 100.0}
    # target weight 0.01 of 300 budget = $3 -> below $10 band -> no order.
    intents = {TREND: [_it(TREND, "SPY", 0.01)]}
    assert plan(p, _books(p), intents, prices) == []


def test_rebalance_down_produces_sell():
    p = get_profile("paper_10000")
    prices = {"SPY": 100.0}
    books = _books(p)
    books[TREND].apply_fill("SPY", 35.0, 100.0)   # currently holding 35
    # New target weight 0 -> sell all 35.
    orders = plan(p, books, {TREND: []}, prices)
    assert len(orders) == 1 and round(orders[0].net_delta, 4) == -35.0


def test_max_positions_keeps_largest():
    p = get_profile("paper_500")     # max_positions 3
    prices = {s: 100.0 for s in ("SPY", "QQQ", "IWM", "EEM", "TLT")}
    intents = {TREND: [_it(TREND, s, 0.2) for s in ("SPY", "QQQ", "IWM", "EEM", "TLT")]}
    orders = plan(p, _books(p), intents, prices)
    assert len(orders) <= 3
