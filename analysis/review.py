"""
Weekly Performance Review
=========================
Analyzes live trade history from portfolio.json and generates
a JSON report with metrics and parameter recommendations.

Usage:
    python analysis/review.py                  # print report
    python analysis/review.py --output report.json  # save JSON
"""
from __future__ import annotations

import json
import os
import sys
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from analysis.signals import BUY_THRESHOLD, SELL_THRESHOLD
from utils.logger import get_logger

logger = get_logger("review")

PORTFOLIO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "portfolio.json"
)


def load_portfolio() -> dict:
    if not os.path.exists(PORTFOLIO_FILE):
        return {"trades": [], "snapshots": []}
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def analyze_trades(trades: list[dict], days: int = 7) -> dict:
    """Analyze recent closed trades."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    all_closed = [t for t in trades if t.get("status") != "OPEN" and t.get("closed_at")]
    recent = [t for t in all_closed if t["closed_at"] >= cutoff]

    def _stats(trade_list):
        if not trade_list:
            return {"count": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "total_pnl": 0, "avg_pnl": 0, "profit_factor": 0,
                    "avg_pnl_pct": 0, "biggest_win": 0, "biggest_loss": 0}
        wins = [t for t in trade_list if (t.get("pnl") or 0) > 0]
        losses = [t for t in trade_list if (t.get("pnl") or 0) <= 0]
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        pnls = [t.get("pnl", 0) for t in trade_list]
        pnl_pcts = [t.get("pnl_pct", 0) for t in trade_list if t.get("pnl_pct") is not None]
        return {
            "count": len(trade_list),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trade_list),
            "total_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / len(trade_list),
            "avg_pnl_pct": sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "biggest_win": max(pnls) if pnls else 0,
            "biggest_loss": min(pnls) if pnls else 0,
        }

    # Per-symbol breakdown
    by_symbol = {}
    for t in all_closed:
        sym = t["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(t)

    symbol_stats = {}
    for sym, sym_trades in by_symbol.items():
        symbol_stats[sym] = _stats(sym_trades)

    # Exit reason analysis
    exit_reasons = {}
    for t in all_closed:
        reason = t.get("status", "UNKNOWN")
        if reason not in exit_reasons:
            exit_reasons[reason] = {"count": 0, "total_pnl": 0}
        exit_reasons[reason]["count"] += 1
        exit_reasons[reason]["total_pnl"] += t.get("pnl", 0)

    return {
        "all_time": _stats(all_closed),
        "recent": _stats(recent),
        "by_symbol": symbol_stats,
        "exit_reasons": exit_reasons,
        "recent_days": days,
    }


def generate_recommendations(stats: dict, snapshots: list[dict]) -> list[dict]:
    """Generate parameter adjustment recommendations based on performance."""
    recs = []
    all_time = stats["all_time"]
    recent = stats["recent"]

    # Need at least 10 trades for meaningful recommendations
    if all_time["count"] < 20:
        recs.append({
            "parameter": "N/A",
            "action": "wait",
            "reason": f"Only {all_time['count']} closed trades — need 20+ for reliable analysis",
            "priority": "low",
        })
        return recs

    # 1. Win rate analysis
    if all_time["win_rate"] < 0.40:
        recs.append({
            "parameter": "BUY_THRESHOLD",
            "current": BUY_THRESHOLD,
            "recommended": min(BUY_THRESHOLD + 5, 65),
            "action": "increase",
            "reason": f"Win rate {all_time['win_rate']:.0%} is below 40% — signals too loose",
            "priority": "high",
        })
    elif all_time["win_rate"] > 0.65 and all_time["count"] > 5:
        recs.append({
            "parameter": "BUY_THRESHOLD",
            "current": BUY_THRESHOLD,
            "recommended": max(BUY_THRESHOLD - 5, 40),
            "action": "decrease",
            "reason": f"Win rate {all_time['win_rate']:.0%} is high — can afford more entries",
            "priority": "medium",
        })

    # 2. Profit factor
    if all_time["profit_factor"] < 1.0 and all_time["count"] >= 10:
        recs.append({
            "parameter": "STOP_LOSS_PCT",
            "current": Config.STOP_LOSS_PCT,
            "recommended": Config.STOP_LOSS_PCT * 1.25,
            "action": "widen",
            "reason": f"Profit factor {all_time['profit_factor']:.2f}x < 1 — stops too tight, getting whipsawed",
            "priority": "high",
        })

    # 3. Position sizing
    if all_time["avg_pnl_pct"] > 0.03 and all_time["win_rate"] > 0.5:
        recs.append({
            "parameter": "MAX_POSITION_PCT",
            "current": Config.MAX_POSITION_PCT,
            "recommended": min(Config.MAX_POSITION_PCT * 1.25, 0.10),
            "action": "increase",
            "reason": f"Strong avg return {all_time['avg_pnl_pct']:.1%} + good win rate — can size up",
            "priority": "medium",
        })
    elif all_time["avg_pnl_pct"] < -0.01:
        recs.append({
            "parameter": "MAX_POSITION_PCT",
            "current": Config.MAX_POSITION_PCT,
            "recommended": max(Config.MAX_POSITION_PCT * 0.75, 0.02),
            "action": "decrease",
            "reason": f"Negative avg return {all_time['avg_pnl_pct']:.1%} — reduce risk per trade",
            "priority": "high",
        })

    # 4. Worst-performing symbols
    for sym, sym_stats in stats["by_symbol"].items():
        if sym_stats["count"] >= 3 and sym_stats["win_rate"] < 0.25:
            recs.append({
                "parameter": "WATCHLIST",
                "action": "remove",
                "symbol": sym,
                "reason": f"{sym}: {sym_stats['win_rate']:.0%} win rate over {sym_stats['count']} trades, total P&L ${sym_stats['total_pnl']:.2f}",
                "priority": "medium",
            })

    # 5. Stop-loss vs take-profit hit ratio
    exit_reasons = stats["exit_reasons"]
    stopped = exit_reasons.get("STOPPED", {}).get("count", 0)
    took_profit = exit_reasons.get("TOOK_PROFIT", {}).get("count", 0)
    if stopped > 0 and took_profit > 0:
        ratio = stopped / (stopped + took_profit)
        if ratio > 0.7:
            recs.append({
                "parameter": "STOP_LOSS_PCT",
                "current": Config.STOP_LOSS_PCT,
                "recommended": Config.STOP_LOSS_PCT * 1.5,
                "action": "widen",
                "reason": f"{ratio:.0%} of exits are stop-losses — stops too tight for this volatility",
                "priority": "high",
            })

    # 6. Equity curve trend (from snapshots)
    if len(snapshots) >= 5:
        recent_snaps = snapshots[-5:]
        equities = [s["equity"] for s in recent_snaps]
        if all(equities[i] < equities[i - 1] for i in range(1, len(equities))):
            recs.append({
                "parameter": "STRATEGY",
                "action": "reduce_exposure",
                "reason": "5 consecutive declining equity snapshots — consider reducing position count",
                "priority": "high",
            })

    if not recs:
        recs.append({
            "parameter": "N/A",
            "action": "hold",
            "reason": "Performance is healthy — no changes recommended",
            "priority": "low",
        })

    return recs


def run_review(days: int = 7) -> dict:
    portfolio = load_portfolio()
    trades = portfolio.get("trades", [])
    snapshots = portfolio.get("snapshots", [])

    stats = analyze_trades(trades, days=days)
    recommendations = generate_recommendations(stats, snapshots)

    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "review_period_days": days,
        "current_parameters": {
            "BUY_THRESHOLD": BUY_THRESHOLD,
            "SELL_THRESHOLD": SELL_THRESHOLD,
            "STOP_LOSS_PCT": Config.STOP_LOSS_PCT,
            "TAKE_PROFIT_PCT": Config.TAKE_PROFIT_PCT,
            "MAX_POSITION_PCT": Config.MAX_POSITION_PCT,
            "MAX_OPEN_POSITIONS": Config.MAX_OPEN_POSITIONS,
            "RSI_OVERSOLD": Config.RSI_OVERSOLD,
            "RSI_OVERBOUGHT": Config.RSI_OVERBOUGHT,
        },
        "stats": stats,
        "recommendations": recommendations,
    }

    return report


def print_report(report: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  WEEKLY PERFORMANCE REVIEW")
    print(f"  Generated: {report['generated_at']}")
    print(f"{'='*60}")

    at = report["stats"]["all_time"]
    print(f"\n  All-Time ({at['count']} trades):")
    print(f"    Win Rate       : {at['win_rate']:.1%}")
    print(f"    Total P&L      : ${at['total_pnl']:,.2f}")
    print(f"    Profit Factor  : {at['profit_factor']:.2f}x")
    print(f"    Avg Trade      : ${at['avg_pnl']:,.2f} ({at['avg_pnl_pct']:.2%})")
    print(f"    Biggest Win    : ${at['biggest_win']:,.2f}")
    print(f"    Biggest Loss   : ${at['biggest_loss']:,.2f}")

    rc = report["stats"]["recent"]
    print(f"\n  Last {report['review_period_days']} Days ({rc['count']} trades):")
    print(f"    Win Rate       : {rc['win_rate']:.1%}")
    print(f"    Total P&L      : ${rc['total_pnl']:,.2f}")

    print(f"\n  Recommendations:")
    for r in report["recommendations"]:
        icon = "🔴" if r["priority"] == "high" else "🟡" if r["priority"] == "medium" else "🟢"
        print(f"    {icon} [{r['parameter']}] {r['action']}: {r['reason']}")
        if "recommended" in r:
            print(f"       Current: {r['current']} → Recommended: {r['recommended']}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly performance review")
    parser.add_argument("--days", type=int, default=7, help="Review period in days")
    parser.add_argument("--output", type=str, default="", help="Save report as JSON")
    args = parser.parse_args()

    report = run_review(days=args.days)
    print_report(report)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Report saved to {args.output}")
