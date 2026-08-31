import numpy as np
import pandas as pd

from portfolio.backtest import Sleeve
from portfolio.profiles import get_profile
from portfolio.strategy_validation import (
    buy_hold_equity,
    metrics_from_equity,
    render_markdown,
    run_strategy_validation,
    tail_dependence,
    walk_forward,
)
from strategy import etf_trend as trend


def _bars(drift, n=350, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(drift, 0.008, n)))
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1000}, index=idx)


def _universe():
    return {"SPY": _bars(0.0008, seed=1), "QQQ": _bars(0.001, seed=2),
            "IWM": _bars(0.0005, seed=3), "TLT": _bars(-0.0006, seed=4),
            "GLD": _bars(0.0003, seed=5), "DBC": _bars(-0.0002, seed=6),
            "EEM": _bars(0.0002, seed=7), "IEF": _bars(0.0001, seed=8),
            "EFA": _bars(0.0004, seed=9)}


def test_metrics_from_equity_basic():
    eq = pd.Series([100, 110, 121], index=pd.bdate_range("2024-01-01", periods=3))
    m = metrics_from_equity(eq)
    assert m["total_return"] == 0.21 and m["max_drawdown"] == 0.0
    assert metrics_from_equity(pd.Series([100]))["n"] == 0


def test_max_drawdown_detected():
    eq = pd.Series([100, 120, 90, 130], index=pd.bdate_range("2024-01-01", periods=4))
    m = metrics_from_equity(eq)
    assert round(m["max_drawdown"], 4) == 0.25       # 120 -> 90


def test_tail_dependence_removes_best_days():
    eq = pd.Series([100, 101, 200, 202], index=pd.bdate_range("2024-01-01", periods=4))
    t = tail_dependence(eq)
    assert t["ex_best1"] < t["total_return"]


def test_buy_hold_benchmark():
    bars = _bars(0.001, seed=1)
    bh = buy_hold_equity(bars, 10000.0)
    assert round(bh.iloc[0], 2) == 10000.0


def test_walk_forward_folds():
    eq = pd.Series(np.linspace(100, 130, 260), index=pd.bdate_range("2023-01-02", periods=260))
    wf = walk_forward(eq, folds=4)
    assert len(wf) == 4 and all("window" in w for w in wf)


def test_run_strategy_validation_and_render():
    bars = _universe()
    profiles = [get_profile("paper_10000")]
    v = run_strategy_validation(bars, profiles, [Sleeve(trend)], synthetic=True)
    md = render_markdown(v)
    assert "Execution scenarios" in md and "Benchmarks" in md and "Walk-forward" in md
    assert v["profiles"][0]["scenarios"]["conservative"]["final_equity"] <= \
           v["profiles"][0]["scenarios"]["ideal"]["final_equity"]
