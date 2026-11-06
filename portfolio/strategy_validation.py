"""
Validation runner for the ETF portfolio strategies (blueprint §7).

Produces the required reports from equity curves: risk/return metrics, execution
scenario comparison, walk-forward folds (frozen params -> stability, not fitting),
tail-dependence, and benchmarks (cash + SPY buy-and-hold). Renders Markdown.

Results are SIMULATED (scenario fills on real or synthetic bars). Win rate is not
an approval metric; the pre-stated gates (§7.6) live in the strategy specs.
"""
from __future__ import annotations

import math

import pandas as pd

from portfolio.backtest import run_portfolio_backtest

ANN = 252


def metrics_from_equity(equity: pd.Series) -> dict:
    if equity is None or len(equity) < 3 or equity.iloc[0] <= 0:
        return {"n": 0}
    rets = equity.pct_change().dropna()
    years = len(equity) / ANN
    total = equity.iloc[-1] / equity.iloc[0] - 1.0
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    vol = float(rets.std()) * math.sqrt(ANN) if len(rets) > 1 else 0.0
    downside = rets[rets < 0]
    dstd = float(downside.std()) * math.sqrt(ANN) if len(downside) > 1 else 0.0
    sharpe = (float(rets.mean()) * ANN) / vol if vol > 0 else 0.0
    sortino = (float(rets.mean()) * ANN) / dstd if dstd > 0 else 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(-dd.min())
    calmar = cagr / max_dd if max_dd > 0 else 0.0
    return {
        "n": len(equity), "total_return": round(total, 4), "cagr": round(cagr, 4),
        "vol": round(vol, 4), "sharpe": round(sharpe, 2), "sortino": round(sortino, 2),
        "max_drawdown": round(max_dd, 4), "calmar": round(calmar, 2),
        "final_equity": round(float(equity.iloc[-1]), 2),
    }


def buy_hold_equity(bars: pd.DataFrame, capital: float) -> pd.Series:
    c = bars["close"]
    return capital * (c / c.iloc[0])


def tail_dependence(equity: pd.Series) -> dict:
    rets = equity.pct_change().dropna()
    if rets.empty:
        return {}
    best = rets.sort_values(ascending=False)
    out = {"total_return": round(equity.iloc[-1] / equity.iloc[0] - 1, 4)}
    for k in (1, 3, 5, 10):
        drop = set(best.index[:k])
        kept = (1 + rets[~rets.index.isin(drop)]).prod() - 1
        out[f"ex_best{k}"] = round(float(kept), 4)
    return out


def walk_forward(equity: pd.Series, folds: int = 4) -> list:
    if len(equity) < folds * 2:
        folds = max(1, len(equity) // 2)
    size = len(equity) // folds
    out = []
    for i in range(folds):
        lo = i * size
        hi = len(equity) if i == folds - 1 else (i + 1) * size
        seg = equity.iloc[lo:hi]
        m = metrics_from_equity(seg / seg.iloc[0] * 100.0) if len(seg) > 2 else {"n": 0}
        m["window"] = f"{seg.index[0].date()}..{seg.index[-1].date()}" if len(seg) else "-"
        out.append(m)
    return out


def _profile_runs(bars: dict, profile, sleeves: list) -> dict:
    return {s: run_portfolio_backtest(bars, profile, sleeves, s)
            for s in ("ideal", "base", "conservative")}


def validate_profile(bars: dict, profile, sleeves: list, runs: dict | None = None,
                     trial_sharpes: list | None = None) -> dict:
    from portfolio.research_stats import deflated_sharpe_ratio

    runs = runs or _profile_runs(bars, profile, sleeves)
    scen = {s: metrics_from_equity(r.equity) for s, r in runs.items()}
    base = runs["base"]
    spy = metrics_from_equity(buy_hold_equity(bars["SPY"], profile.capital)) if "SPY" in bars else {}
    cash = {"cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "final_equity": profile.capital}
    out = {
        "profile": profile.id, "capital": profile.capital,
        "scenarios": scen,
        "walk_forward_base": walk_forward(base.equity),
        "tail_base": tail_dependence(base.equity),
        "benchmarks": {"SPY_buy_hold": spy, "cash": cash},
        "n_rebalances": base.n_rebalances, "n_fills": base.n_fills,
    }
    if trial_sharpes is not None:
        out["deflated_sharpe"] = deflated_sharpe_ratio(base.daily_returns(), trial_sharpes)
    return out


def run_strategy_validation(bars: dict, profiles: list, sleeves: list,
                            synthetic: bool = False) -> dict:
    from portfolio.research_stats import per_period_sharpe

    # Run each (profile, scenario) backtest once; the family of per-period
    # Sharpes across them is the multiple-testing trial set for deflation.
    per_profile = {p.id: _profile_runs(bars, p, sleeves) for p in profiles}
    trials = [sr for runs in per_profile.values() for r in runs.values()
              if (sr := per_period_sharpe(r.daily_returns())) is not None]
    return {
        "synthetic": synthetic,
        "sleeves": [s.strategy_id for s in sleeves],
        "n_trials": len(trials),
        "profiles": [validate_profile(bars, p, sleeves, per_profile[p.id], trials)
                     for p in profiles],
    }


# ------------------------------------------------------------------ rendering
def _mrow(label, m):
    if not m or not m.get("n", 1):
        return f"| {label} | — | — | — | — | — |"
    return (f"| {label} | {m.get('cagr',0)*100:.1f}% | {m.get('sharpe',0):.2f} | "
            f"{m.get('max_drawdown',0)*100:.1f}% | {m.get('calmar',0):.2f} | "
            f"${m.get('final_equity',0):,.0f} |")


_H = "| | CAGR | Sharpe | MaxDD | Calmar | Final |\n|---|---|---|---|---|---|"


def render_markdown(v: dict) -> str:
    L = [f"*Sleeves: {', '.join(v['sleeves'])}. "
         f"{'SYNTHETIC / ILLUSTRATIVE' if v['synthetic'] else 'Simulated fills on real bars'}.*\n"]
    for pr in v["profiles"]:
        L.append(f"## {pr['profile']} (${pr['capital']:,.0f}) — "
                 f"{pr['n_rebalances']} rebalances, {pr['n_fills']} fills\n")
        L.append("### Execution scenarios")
        L.append(_H)
        for s in ("ideal", "base", "conservative"):
            L.append(_mrow(s, pr["scenarios"][s]))
        L.append("\n*ideal = diagnostic only (no costs); never used for approval.*\n")
        L.append("### Benchmarks (base)")
        L.append(_H)
        L.append(_mrow("SPY buy&hold", pr["benchmarks"]["SPY_buy_hold"]))
        L.append(_mrow("cash", pr["benchmarks"]["cash"]))
        L.append("\n### Walk-forward folds (base, frozen params)")
        L.append(_H)
        for w in pr["walk_forward_base"]:
            L.append(_mrow(w.get("window", "?"), w))
        d = pr.get("deflated_sharpe")
        if d:
            verdict = "PASS" if d["dsr"] >= 0.95 else "FAIL"
            L.append(f"\n### Deflated Sharpe (base, gate 7)\n"
                     f"- DSR **{d['dsr']:.2f}** (P true Sharpe > multiple-testing threshold) "
                     f"[{verdict} at 0.95] · vs-zero PSR {d['psr_vs_zero']:.2f} · "
                     f"deflated threshold {d['sr0_annual']:.2f} annual Sharpe · "
                     f"{d['n_trials']} trials\n")
        t = pr["tail_base"]
        if t:
            L.append(f"\n### Tail dependence (base)\n"
                     f"- total {t['total_return']*100:.1f}% · ex-best-1 {t.get('ex_best1',0)*100:.1f}% · "
                     f"ex-best-3 {t.get('ex_best3',0)*100:.1f}% · ex-best-5 {t.get('ex_best5',0)*100:.1f}% · "
                     f"ex-best-10 {t.get('ex_best10',0)*100:.1f}%\n")
    return "\n".join(L)
