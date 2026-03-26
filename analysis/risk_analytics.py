"""
Portfolio Risk Analytics
=========================
Computes institutional-grade risk metrics from daily equity snapshots:

  Sharpe ratio     — annualised excess return / volatility (rf = 0%)
  Sortino ratio    — annualised return / downside deviation only
  Calmar ratio     — annualised return / max drawdown
  Max drawdown     — peak-to-trough percentage loss
  Value-at-Risk    — historical 95% 1-day VaR (as positive % loss)
  Win rate         — % of closed trades with positive PnL
  Profit factor    — gross profit / gross loss

Daily equity is appended to data/equity_curve.json (one record per day).
Exposed via GET /api/analytics.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("risk_analytics")

_EQUITY_FILE = Path("data/equity_curve.json")


# ---- equity curve I/O --------------------------------------------------------

def record_equity(equity: float) -> None:
    """Append (or update) today's equity to the persistent curve file."""
    _EQUITY_FILE.parent.mkdir(exist_ok=True)

    try:
        records: list = json.loads(_EQUITY_FILE.read_text()) if _EQUITY_FILE.exists() else []
    except Exception:
        records = []

    today = date.today().isoformat()
    if records and records[-1].get("date") == today:
        records[-1]["equity"] = round(equity, 2)
    else:
        records.append({"date": today, "equity": round(equity, 2)})

    _EQUITY_FILE.write_text(json.dumps(records, indent=2))


def _load_equity_series() -> Optional[pd.Series]:
    """Load equity curve as a date-indexed pd.Series, or None if too short."""
    if not _EQUITY_FILE.exists():
        return None
    try:
        records = json.loads(_EQUITY_FILE.read_text())
        if len(records) < 5:
            return None
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df["equity"].astype(float)
    except Exception as exc:
        logger.warning(f"Failed to load equity curve: {exc}")
        return None


# ---- metric helpers ----------------------------------------------------------

def _sharpe(returns: pd.Series, ann: int = 252) -> float:
    std = returns.std()
    return float(returns.mean() / std * np.sqrt(ann)) if std > 0 else 0.0


def _sortino(returns: pd.Series, ann: int = 252) -> float:
    down = returns[returns < 0]
    std  = down.std()
    return float(returns.mean() / std * np.sqrt(ann)) if (not down.empty and std > 0) else 0.0


def _max_drawdown(equity: pd.Series) -> float:
    """Returns max drawdown as a negative fraction (e.g. -0.15 = -15%)."""
    peak = equity.cummax()
    dd   = (equity - peak) / peak
    return float(dd.min())


def _var_historical(returns: pd.Series, confidence: float = 0.95) -> float:
    """1-day VaR at confidence level, returned as a positive loss percentage."""
    if len(returns) < 30:
        return 0.0
    return float(-np.percentile(returns.dropna(), (1 - confidence) * 100))


# ---- public API --------------------------------------------------------------

def compute_analytics(portfolio=None) -> dict[str, Any]:
    """
    Compute all risk metrics. Returns a JSON-serialisable dict.

    Args:
        portfolio: optional Portfolio instance for win-rate / profit-factor.

    Returns dict with keys:
        equity_curve        — list of {date, equity} (last 90 days)
        sharpe              — annualised Sharpe ratio
        sortino             — annualised Sortino ratio
        calmar              — Calmar ratio
        max_drawdown_pct    — max drawdown %
        var_95_pct          — 1-day 95% VaR %
        total_return_pct    — since tracking started
        win_rate            — % winning trades
        profit_factor
        total_trades
        days_tracked
    """
    equity = _load_equity_series()

    metrics: dict[str, Any] = {
        "equity_curve":     [],
        "sharpe":           None,
        "sortino":          None,
        "calmar":           None,
        "max_drawdown_pct": None,
        "var_95_pct":       None,
        "total_return_pct": None,
        "win_rate":         None,
        "profit_factor":    None,
        "total_trades":     0,
        "days_tracked":     0,
    }

    if equity is not None and len(equity) >= 5:
        returns = equity.pct_change().dropna()
        n_days  = len(returns)

        metrics["sharpe"]           = round(_sharpe(returns), 3)
        metrics["sortino"]          = round(_sortino(returns), 3)
        metrics["max_drawdown_pct"] = round(_max_drawdown(equity) * 100, 2)
        metrics["var_95_pct"]       = round(_var_historical(returns) * 100, 2)
        metrics["days_tracked"]     = len(equity)
        metrics["total_return_pct"] = round(
            (float(equity.iloc[-1]) / float(equity.iloc[0]) - 1) * 100, 2
        )

        mdd     = abs(metrics["max_drawdown_pct"]) / 100
        ann_ret = metrics["total_return_pct"] / 100 * (252 / max(n_days, 1))
        metrics["calmar"] = round(ann_ret / mdd, 3) if mdd > 0 else None

        # Equity curve for chart: last 90 points
        curve = equity.tail(90)
        metrics["equity_curve"] = [
            {"date": idx.strftime("%Y-%m-%d"), "equity": round(float(v), 2)}
            for idx, v in curve.items()
        ]

    # Win rate + profit factor from portfolio closed trades
    if portfolio is not None:
        try:
            closed = [t for t in portfolio.trades if t.status != "OPEN"]
            if closed:
                wins   = [t for t in closed if t.pnl is not None and t.pnl > 0]
                losses = [t for t in closed if t.pnl is not None and t.pnl <= 0]
                metrics["win_rate"]   = round(len(wins) / len(closed) * 100, 1)
                metrics["total_trades"] = len(closed)
                g_profit = sum(t.pnl for t in wins)
                g_loss   = abs(sum(t.pnl for t in losses))
                metrics["profit_factor"] = round(g_profit / g_loss, 3) if g_loss > 0 else None
        except Exception as exc:
            logger.debug(f"Portfolio metrics error: {exc}")

    return metrics
