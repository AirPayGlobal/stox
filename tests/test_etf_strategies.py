"""ETF strategy intent producers — deterministic synthetic daily bars."""
import numpy as np
import pandas as pd

from strategy import etf_relative_momentum as relmom
from strategy import etf_trend as trend
from strategy.momentum import (
    combined_trend_score,
    inverse_vol_weights,
    monthly_rebalance_dates,
    weekly_rebalance_dates,
)


def _series(drift, n=300, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.008, n)
    close = start * np.exp(np.cumsum(rets))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"close": close}, index=idx)


def test_combined_score_sign_follows_trend():
    up = _series(0.001, seed=1)["close"]
    down = _series(-0.001, seed=2)["close"]
    assert combined_trend_score(up, (21, 63, 126, 252)) > 0
    assert combined_trend_score(down, (21, 63, 126, 252)) < 0


def test_inverse_vol_weights_favour_low_vol():
    w = inverse_vol_weights({"A": 0.10, "B": 0.20})
    assert w["A"] > w["B"] and abs(sum(w.values()) - 1.0) < 1e-9


def test_trend_holds_only_positive_and_caps():
    bars = {"UP1": _series(0.001, seed=1), "UP2": _series(0.0012, seed=3),
            "DN": _series(-0.001, seed=2)}
    cfg = {"universe": ("UP1", "UP2", "DN")}
    w = trend.target_weights(bars["UP1"].index[-1], bars, cfg)
    assert "DN" not in w                     # negative trend -> cash
    assert set(w) <= {"UP1", "UP2"}
    assert all(v <= 0.25 + 1e-9 for v in w.values())
    assert sum(w.values()) <= 0.90 + 1e-9


def test_trend_generate_intents_shape():
    bars = {"UP1": _series(0.001, seed=1), "DN": _series(-0.001, seed=2)}
    its = trend.generate_intents(bars["UP1"].index[-1], bars, {"universe": ("UP1", "DN")})
    assert all(it.strategy_id == "ETF_TREND_V1" for it in its)
    assert all(0 < it.target_weight <= 0.25 for it in its)
    assert its[0].config_hash == trend.config_hash({"universe": ("UP1", "DN")})


def test_relmom_selects_top_and_requires_absolute_gate():
    # Three up-trends of different strength + one down-trend.
    bars = {"A": _series(0.0016, seed=4), "B": _series(0.0012, seed=5),
            "C": _series(0.0008, seed=6), "D": _series(-0.0012, seed=7)}
    cfg = {"universe": ("A", "B", "C", "D"), "top_n": 3}
    w = relmom.target_weights(bars["A"].index[-1], bars, cfg)
    assert "D" not in w                      # fails absolute gate
    assert len(w) <= 3
    assert all(v <= 0.35 + 1e-9 for v in w.values())


def test_relmom_all_down_holds_cash():
    bars = {"A": _series(-0.001, seed=8), "B": _series(-0.0012, seed=9)}
    w = relmom.target_weights(bars["A"].index[-1], bars, {"universe": ("A", "B")})
    assert w == {}                           # nothing passes the gate -> all cash


def test_rebalance_date_helpers():
    idx = pd.bdate_range("2024-01-01", periods=90)
    wk = weekly_rebalance_dates(idx)
    mo = monthly_rebalance_dates(idx)
    assert len(wk) > len(mo) >= 3            # more weeks than months
    assert all(d in idx for d in wk + mo)
