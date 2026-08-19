"""
Fib validation runner: turns simulator output into the reports the validation
standard requires — execution-scenario comparison, result slices, robustness
(tail-dependence) stats, per-filter effect, and walk-forward out-of-sample
windows — and renders a Markdown report.

All results are SIMULATED. When run on synthetic bars the report is labelled
ILLUSTRATIVE and must not be read as a validation of the live edge; the same
functions run on real bars (in an environment with market-data access) produce
the authoritative numbers.

CLI:  python -m backtest.validation --days 120 --equity 100000 [--synthetic]
"""
from __future__ import annotations

import argparse
import statistics
from contextlib import contextmanager

from backtest.fib_sim import run_fib_sim
from config import Config

FILTER_FLAGS = {
    "trend": "FIB_FILTER_TREND",
    "vol_regime": "FIB_FILTER_VOL_REGIME",
    "time_of_day": "FIB_FILTER_TOD",
    "liquidity": "FIB_FILTER_LIQUIDITY",
    "confirm": "FIB_FILTER_CONFIRM",
}


@contextmanager
def _config(**overrides):
    old = {k: getattr(Config, k) for k in overrides}
    for k, v in overrides.items():
        setattr(Config, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(Config, k, v)


def _daily(trades: list[dict]) -> list[float]:
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["pnl"]
    return [by_day[d] for d in sorted(by_day)]


def _max_drawdown(daily: list[float]) -> float:
    peak = cum = 0.0
    mdd = 0.0
    for d in daily:
        cum += d
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    daily = _daily(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    total = sum(t["pnl"] for t in trades)
    return {
        "trades": len(trades),
        "trading_days": len(daily),
        "win_rate": round(len(wins) / len(trades), 3),
        "total_pnl": round(total, 0),
        "expectancy": round(total / len(trades), 2),
        "avg_day": round(statistics.mean(daily), 0),
        "median_day": round(statistics.median(daily), 0),
        "best_day": round(max(daily), 0),
        "worst_day": round(min(daily), 0),
        "max_drawdown": round(_max_drawdown(daily), 0),
        "days_at_target": sum(1 for d in daily if d >= Config.DAILY_PROFIT_TARGET),
    }


def robustness(trades: list[dict]) -> dict:
    """Tail-dependence: how much of the P&L rides on a few exceptional days."""
    daily = sorted(_daily(trades))
    if not daily:
        return {}
    total = sum(daily)
    n_tail = max(1, len(daily) // 20)  # worst 5%
    top = sorted(daily, reverse=True)
    return {
        "total_pnl": round(total, 0),
        "median_day": round(statistics.median(daily), 0),
        "worst_5pct_mean": round(statistics.mean(daily[:n_tail]), 0),
        "pnl_ex_best1": round(total - sum(top[:1]), 0),
        "pnl_ex_best3": round(total - sum(top[:3]), 0),
        "pnl_ex_best5": round(total - sum(top[:5]), 0),
        "best_day_share": round(top[0] / total, 3) if total else None,
    }


def _slice(trades: list[dict], key: str, buckets=None) -> dict:
    out: dict[str, list] = {}
    for t in trades:
        out.setdefault(str(t.get(key)), []).append(t)
    labels = buckets or sorted(out)
    return {lbl: summarize(out.get(lbl, [])) for lbl in labels if lbl in out}


def _spread_bucket(t: dict) -> str:
    s = t.get("spread_pct", 0.0)
    return "tight(<5%)" if s < 0.05 else "medium(5-10%)" if s < 0.10 else "wide(>=10%)"


def slices(trades: list[dict]) -> dict:
    for t in trades:
        t["_spread_bucket"] = _spread_bucket(t)
    return {
        "by_symbol": _slice(trades, "symbol"),
        "by_dte": _slice(trades, "dte_bucket"),
        "by_time_of_day": _slice(trades, "tod_bucket"),
        "by_spread_bucket": _slice(trades, "_spread_bucket"),
        "by_trend_regime": _slice(trades, "regime_trend"),
        "by_vol_regime": _slice(trades, "regime_vol"),
    }


def scenario_comparison(bars, equity: float, dte: int = 0) -> dict:
    return {
        scen: summarize(run_fib_sim(bars, equity, scenario=scen, dte=dte).trades)
        for scen in ("baseline", "optimistic", "base", "conservative")
    }


def filter_effects(bars, equity: float, scenario: str = "base", dte: int = 0) -> dict:
    """Each filter's independent effect vs no filters (same scenario)."""
    base = summarize(run_fib_sim(bars, equity, scenario=scenario, dte=dte).trades)
    out = {"none": base}
    for name, flag in FILTER_FLAGS.items():
        with _config(**{flag: True}):
            out[name] = summarize(run_fib_sim(bars, equity, scenario=scenario, dte=dte).trades)
    return out


def walk_forward(bars, equity: float, folds: int = 4, scenario: str = "base",
                 dte: int = 0) -> list[dict]:
    """Contiguous out-of-sample folds. Parameters are FROZEN (no optimisation),
    so this measures stability across unseen periods, not a fitted curve."""
    all_days = sorted(set().union(*[set(str(d) for d in b.index.date) for b in bars.values()]))
    if len(all_days) < folds:
        folds = max(1, len(all_days))
    size = len(all_days) // folds
    windows = []
    for i in range(folds):
        lo = i * size
        hi = len(all_days) if i == folds - 1 else (i + 1) * size
        days = set(all_days[lo:hi])
        sub = {s: b[b.index.to_series().dt.date.astype(str).isin(days)] for s, b in bars.items()}
        summ = summarize(run_fib_sim(sub, equity, scenario=scenario, dte=dte).trades)
        summ["window"] = f"{all_days[lo]}..{all_days[hi - 1]}"
        windows.append(summ)
    return windows


def run_validation(bars, equity: float = 100_000.0, dte: int = 0,
                   synthetic: bool = False) -> dict:
    base_run = run_fib_sim(bars, equity, scenario="base", dte=dte)
    return {
        "synthetic": synthetic,
        "equity": equity,
        "dte": dte,
        "config_hash": base_run.trades[0]["config_hash"] if base_run.trades else None,
        "strategy_version": Config.STRATEGY_VERSION,
        "scenarios": scenario_comparison(bars, equity, dte),
        "robustness_base": robustness(base_run.trades),
        "slices_base": slices(base_run.trades),
        "filter_effects": filter_effects(bars, equity, "base", dte),
        "walk_forward_base": walk_forward(bars, equity, scenario="base", dte=dte),
        "dte_compare": {
            "0DTE": summarize(run_fib_sim(bars, equity, scenario="base", dte=0).trades),
            "1DTE": summarize(run_fib_sim(bars, equity, scenario="base", dte=1).trades),
        },
    }


# ------------------------------------------------------------------ rendering
def _row(label: str, s: dict) -> str:
    if not s.get("trades"):
        return f"| {label} | 0 | — | — | — | — | — | — |"
    return (f"| {label} | {s['trades']} | {s['win_rate']*100:.0f}% | "
            f"${s['total_pnl']:,.0f} | ${s['expectancy']:,.0f} | ${s['median_day']:,.0f} | "
            f"${s['max_drawdown']:,.0f} | {s['days_at_target']} |")


_HDR = "| | Trades | Win% | Total P&L | Exp/trade | Median day | MaxDD | Days≥tgt |\n|---|---|---|---|---|---|---|---|"


def render_markdown(v: dict) -> str:
    L = []
    L.append("## Execution-scenario comparison (base strategy, no optional filters)\n")
    L.append(_HDR)
    for scen in ("baseline", "optimistic", "base", "conservative"):
        L.append(_row(scen, v["scenarios"][scen]))
    L.append("\n*baseline = the old fixed $0.02 half-spread (comparison only). "
             "Entries never fill at mid except the diagnostic 'mid' scenario.*\n")

    r = v["robustness_base"]
    if r:
        L.append("## Tail dependence (base scenario)\n")
        L.append(f"- Total P&L: ${r['total_pnl']:,.0f}")
        L.append(f"- Median day: ${r['median_day']:,.0f}; worst-5% mean day: ${r['worst_5pct_mean']:,.0f}")
        L.append(f"- P&L excluding best 1 / 3 / 5 days: ${r['pnl_ex_best1']:,.0f} / "
                 f"${r['pnl_ex_best3']:,.0f} / ${r['pnl_ex_best5']:,.0f}")
        if r.get("best_day_share") is not None:
            L.append(f"- Single best day = {r['best_day_share']*100:.0f}% of total P&L\n")

    L.append("## Walk-forward (frozen params, contiguous unseen folds; base scenario)\n")
    L.append(_HDR)
    for w in v["walk_forward_base"]:
        L.append(_row(w.get("window", "?"), w))
    L.append("")

    L.append("## Filter effects (base scenario; each filter alone vs none)\n")
    L.append(_HDR)
    for name, s in v["filter_effects"].items():
        L.append(_row(name, s))
    L.append("\n*A filter earns 'enable' only if it improves out-of-sample "
             "expectancy/drawdown here — not merely total P&L.*\n")

    L.append("## 0DTE vs 1DTE (base scenario)\n")
    L.append(_HDR)
    for lbl, s in v["dte_compare"].items():
        L.append(_row(lbl, s))
    L.append("")

    sl = v["slices_base"]
    titles = {
        "by_symbol": "By symbol", "by_dte": "By DTE",
        "by_time_of_day": "By time of day", "by_spread_bucket": "By spread bucket",
        "by_trend_regime": "By trend regime", "by_vol_regime": "By volatility regime",
    }
    for key, title in titles.items():
        if not sl.get(key):
            continue
        L.append(f"## {title} (base scenario)\n")
        L.append(_HDR)
        for lbl, s in sl[key].items():
            L.append(_row(lbl, s))
        L.append("")
    return "\n".join(L)


def _cli() -> None:
    p = argparse.ArgumentParser(description="Fib validation runner")
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--equity", type=float, default=100_000)
    p.add_argument("--dte", type=int, default=0)
    p.add_argument("--synthetic", action="store_true",
                   help="use deterministic synthetic bars (no market data)")
    args = p.parse_args()

    if args.synthetic or not (Config.ALPACA_API_KEY and Config.ALPACA_API_SECRET):
        from backtest.synth import synth_history
        bars = {"SPY": synth_history("SPY", args.days, 550.0),
                "QQQ": synth_history("QQQ", args.days, 480.0)}
        synthetic = True
    else:
        from data.market_data import get_intraday_bars
        bars = {s: get_intraday_bars(s, minutes=Config.FIB_BAR_MINUTES, lookback_days=args.days)
                for s in Config.UNDERLYINGS}
        synthetic = False

    v = run_validation(bars, args.equity, args.dte, synthetic)
    header = ("# Fib validation — SYNTHETIC / ILLUSTRATIVE (no market data)\n"
              if synthetic else "# Fib validation — SIMULATED fills on real bars\n")
    print(header)
    print(render_markdown(v))


if __name__ == "__main__":
    _cli()
