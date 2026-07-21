"""
Performance reporting over the persisted trade log.

Everything is computed from PositionBook's closed trades (logs/trades.json
on the state volume), so reports survive restarts and cover every strategy
the engine has traded. All dates are ET (trades are stamped in ET).
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, timedelta

from trading.positions import PositionBook, Trade


def _bucket_stats(trades: list[Trade]) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    rs = sorted(t.realized_r for t in trades if t.planned_risk > 0)
    out = {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0.0,
        "pnl": round(sum(t.pnl for t in trades), 2),
        "expectancy": round(sum(t.pnl for t in trades) / len(trades), 2) if trades else 0.0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "largest_win": round(max((t.pnl for t in wins), default=0.0), 2),
        "largest_loss": round(min((t.pnl for t in losses), default=0.0), 2),
        "profit_factor": (
            round(gross_win / gross_loss, 2) if gross_loss > 0
            else (None if not wins else float("inf"))
        ),
    }
    if rs:
        out["avg_r"] = round(sum(rs) / len(rs), 2)
        out["median_r"] = round(rs[len(rs) // 2], 2)
    return out


def _trade_quality(trades: list[Trade]) -> dict:
    """
    Entry/exit-quality diagnostics from MFE/MAE. Computed only over trades
    that recorded an MFE (recent trades); older trades logged 0 and are
    excluded. MFE% is peak favorable mark vs entry premium.
      immediate_reversal_rate: share with < 5% MFE (entered at a reversal)
      recoverable_loss_rate  : losers whose MFE reached >= 15% (a breakeven
                               or trail stop could have scratched them)
      avg_loser_mfe_pct      : how far the average loser went green first
    """
    tracked = [t for t in trades if t.mfe_premium > 0 and t.entry_premium > 0]
    if not tracked:
        return {}
    def mfe_pct(t):
        return (t.mfe_premium - t.entry_premium) / t.entry_premium
    losers = [t for t in tracked if t.pnl < 0]
    return {
        "sample": len(tracked),
        "immediate_reversal_rate": round(
            sum(1 for t in tracked if mfe_pct(t) < 0.05) / len(tracked), 3
        ),
        "recoverable_loss_rate": round(
            sum(1 for t in losers if mfe_pct(t) >= 0.15) / len(losers), 3
        ) if losers else 0.0,
        "avg_loser_mfe_pct": round(
            sum(mfe_pct(t) for t in losers) / len(losers), 3
        ) if losers else 0.0,
    }


def _concentration(trades: list[Trade]) -> dict:
    """How dependent is the profit on a few outliers?"""
    win_pnls = sorted((t.pnl for t in trades if t.pnl > 0), reverse=True)
    gross_profit = sum(win_pnls)
    if gross_profit <= 0:
        return {"largest_win_pct_of_profit": None, "top5_wins_pct_of_profit": None}
    return {
        "largest_win_pct_of_profit": round(win_pnls[0] / gross_profit, 3),
        "top5_wins_pct_of_profit": round(sum(win_pnls[:5]) / gross_profit, 3),
    }


def _sanitize(stats: dict) -> dict:
    # JSON can't carry Infinity — report it as the string "inf".
    if stats.get("profit_factor") == float("inf"):
        stats["profit_factor"] = "inf"
    return stats


def trades_since(book: PositionBook, days: int) -> list[Trade]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [t for t in book.book.closed_trades if t.closed_at[:10] >= cutoff]


def rolling_drawdown(book: PositionBook, days: int) -> dict:
    """
    Give-back of realized P&L from its running peak over the trailing window.
    peak is the best cumulative P&L reached (never below 0 — no phantom
    drawdown before the first profit); drawdown = peak - current, >= 0.
    """
    trades = sorted(trades_since(book, days), key=lambda t: t.closed_at)
    cum = 0.0
    peak = 0.0
    for t in trades:
        cum += t.pnl
        peak = max(peak, cum)
    return {"peak": round(peak, 2), "current": round(cum, 2), "drawdown": round(peak - cum, 2)}


def period_report(book: PositionBook, days: int = 30) -> dict:
    trades = trades_since(book, days)
    if not trades:
        return {"days": days, "trades": 0, "message": "no closed trades in period"}

    # ---- daily rows with cumulative P&L and max drawdown
    by_day: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_day[t.closed_at[:10]].append(t)

    daily_rows = []
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for day in sorted(by_day):
        day_trades = by_day[day]
        pnl = sum(t.pnl for t in day_trades)
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        wins = sum(1 for t in day_trades if t.pnl > 0)
        daily_rows.append(
            {
                "date": day,
                "trades": len(day_trades),
                "win_rate": round(wins / len(day_trades), 3),
                "pnl": round(pnl, 2),
                "cumulative": round(cumulative, 2),
            }
        )

    def group(key_fn) -> dict:
        groups: dict[str, list[Trade]] = defaultdict(list)
        for t in trades:
            groups[key_fn(t)].append(t)
        return {k: _sanitize(_bucket_stats(v)) for k, v in sorted(groups.items())}

    exit_reasons: dict[str, dict] = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in trades:
        exit_reasons[t.status]["count"] += 1
        exit_reasons[t.status]["pnl"] = round(exit_reasons[t.status]["pnl"] + t.pnl, 2)

    daily_pnls = [r["pnl"] for r in daily_rows]
    return {
        "days": days,
        "from": daily_rows[0]["date"],
        "to": daily_rows[-1]["date"],
        "totals": _sanitize(_bucket_stats(trades)),
        "trading_days": len(daily_rows),
        "green_days": sum(1 for p in daily_pnls if p > 0),
        "best_day": round(max(daily_pnls), 2),
        "worst_day": round(min(daily_pnls), 2),
        "avg_day": round(sum(daily_pnls) / len(daily_pnls), 2),
        "max_drawdown": round(max_drawdown, 2),
        "concentration": _concentration(trades),
        "trade_quality": _trade_quality(trades),
        "daily": daily_rows,
        "per_strategy": group(lambda t: t.strategy or "orb"),
        "per_underlying": group(lambda t: t.underlying),
        "per_hour": group(lambda t: t.opened_at[11:13] + ":00"),
        "exit_reasons": dict(exit_reasons),
    }


def daily_report(book: PositionBook, day_iso: str | None = None) -> dict:
    day_iso = day_iso or date.today().isoformat()
    trades = [t for t in book.book.closed_trades if t.closed_at[:10] == day_iso]
    if not trades:
        return {"date": day_iso, "trades": 0, "message": "no closed trades"}
    return {
        "date": day_iso,
        "totals": _sanitize(_bucket_stats(trades)),
        "per_strategy": {
            s: _sanitize(_bucket_stats([t for t in trades if (t.strategy or "orb") == s]))
            for s in sorted({t.strategy or "orb" for t in trades})
        },
        "trades": [
            {
                "symbol": t.symbol,
                "underlying": t.underlying,
                "strategy": t.strategy,
                "direction": t.direction,
                "qty": t.qty,
                "entry": t.entry_premium,
                "exit": t.exit_premium,
                "reason": t.status,
                "pnl": t.pnl,
                "opened_at": t.opened_at,
                "closed_at": t.closed_at,
            }
            for t in trades
        ],
    }


CSV_FIELDS = [
    "closed_at", "opened_at", "symbol", "underlying", "strategy", "direction",
    "qty", "entry_premium", "exit_premium", "stop_premium", "target_premium",
    "stop_underlying", "target_underlying", "status", "pnl",
    "planned_risk", "realized_r", "mfe_premium", "mae_premium", "spread_at_entry",
]


def trades_csv(book: PositionBook, days: int = 90) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for t in trades_since(book, days):
        writer.writerow(vars(t))
    return buf.getvalue()
