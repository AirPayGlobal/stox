import pandas as pd

from backtest.synth import synth_history
from backtest.validation import (
    filter_effects,
    render_markdown,
    robustness,
    run_validation,
    scenario_comparison,
    slices,
    summarize,
    walk_forward,
)
from config import Config


def _bars(days=20):
    return {"SPY": synth_history("SPY", days, 550.0), "QQQ": synth_history("QQQ", days, 480.0)}


def _trades():
    from backtest.fib_sim import run_fib_sim
    return run_fib_sim(_bars(), scenario="base").trades


def test_summarize_shapes_and_drawdown():
    fake = [
        {"date": "2026-01-01", "pnl": 100.0}, {"date": "2026-01-01", "pnl": -30.0},
        {"date": "2026-01-02", "pnl": -50.0}, {"date": "2026-01-03", "pnl": 200.0},
    ]
    s = summarize(fake)
    assert s["trades"] == 4 and s["trading_days"] == 3
    assert s["total_pnl"] == 220.0
    assert s["max_drawdown"] == 50.0          # peak 70 -> 20
    assert summarize([]) == {"trades": 0}


def test_robustness_flags_tail_dependence():
    trades = [{"date": f"2026-01-{d:02d}", "pnl": 10.0} for d in range(1, 20)]
    trades.append({"date": "2026-02-01", "pnl": 5000.0})  # one monster day
    r = robustness(trades)
    assert r["pnl_ex_best1"] < r["total_pnl"]
    assert r["best_day_share"] > 0.9


def test_scenario_comparison_monotonic_total():
    sc = scenario_comparison(_bars(), 100_000)
    assert sc["baseline"]["total_pnl"] >= sc["base"]["total_pnl"] >= sc["conservative"]["total_pnl"]


def test_slices_cover_required_dimensions():
    sl = slices(_trades())
    for key in ("by_symbol", "by_dte", "by_time_of_day", "by_spread_bucket",
                "by_trend_regime", "by_vol_regime"):
        assert key in sl


def test_filter_effects_includes_none_and_all_filters():
    fe = filter_effects(_bars(), 100_000, "base")
    assert "none" in fe
    for name in ("trend", "vol_regime", "time_of_day", "liquidity", "confirm"):
        assert name in fe


def test_walk_forward_folds_are_contiguous_and_labelled():
    wf = walk_forward(_bars(30), 100_000, folds=3)
    assert len(wf) == 3
    assert all("window" in w for w in wf)


def test_render_markdown_runs_and_labels():
    v = run_validation(_bars(20), 100_000, synthetic=True)
    md = render_markdown(v)
    assert "Execution-scenario comparison" in md
    assert "Walk-forward" in md
    assert "0DTE vs 1DTE" in md
    assert v["synthetic"] is True
