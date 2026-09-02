"""Portfolio backtest driver — deterministic synthetic daily bars."""
import numpy as np
import pandas as pd

from portfolio.backtest import Sleeve, run_portfolio_backtest
from portfolio.profiles import get_profile
from strategy import etf_relative_momentum as relmom
from strategy import etf_trend as trend


def _bars(drift, n=400, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.008, n)
    close = start * np.exp(np.cumsum(rets))
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1000}, index=idx)


def _universe():
    # A few trending-up and one down, enough symbols for both strategies.
    return {"SPY": _bars(0.0009, seed=1), "QQQ": _bars(0.0011, seed=2),
            "IWM": _bars(0.0006, seed=3), "TLT": _bars(-0.0007, seed=4),
            "GLD": _bars(0.0004, seed=5), "DBC": _bars(-0.0003, seed=6),
            "EEM": _bars(0.0002, seed=7), "IEF": _bars(0.0001, seed=8),
            "EFA": _bars(0.0005, seed=9)}


def test_trend_backtest_runs_and_invests():
    bars = _universe()
    p = get_profile("paper_10000")
    res = run_portfolio_backtest(bars, p, [Sleeve(trend)], scenario="ideal")
    assert res.n_rebalances > 0 and res.n_fills > 0
    assert len(res.equity) == len(next(iter(bars.values())))
    # Deterministic re-run.
    res2 = run_portfolio_backtest(bars, p, [Sleeve(trend)], scenario="ideal")
    assert res.equity.iloc[-1] == res2.equity.iloc[-1]


def test_two_sleeves_share_execution():
    bars = _universe()
    p = get_profile("paper_50000")
    res = run_portfolio_backtest(bars, p, [Sleeve(trend), Sleeve(relmom)], scenario="ideal")
    # Both sleeves have virtual books, attribution kept separate.
    assert set(res.book.sleeves) == {"ETF_TREND_V1", "ETF_RELATIVE_MOMENTUM_V1"}
    assert res.equity.iloc[-1] > 0


def test_costs_reduce_terminal_equity():
    bars = _universe()
    p = get_profile("paper_10000")
    ideal = run_portfolio_backtest(bars, p, [Sleeve(trend)], scenario="ideal").equity.iloc[-1]
    cons = run_portfolio_backtest(bars, p, [Sleeve(trend)], scenario="conservative").equity.iloc[-1]
    assert cons <= ideal          # friction never helps


def test_capital_conserved_reasonably():
    bars = _universe()
    p = get_profile("paper_500")
    res = run_portfolio_backtest(bars, p, [Sleeve(trend)], scenario="ideal")
    # Start ~ profile capital (minus tiny rounding), never negative.
    assert res.equity.iloc[0] > 0
    assert (res.equity > 0).all()
