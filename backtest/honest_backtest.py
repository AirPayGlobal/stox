"""
Honest Backtesting Engine
=========================
Extends the portfolio backtester with realistic cost modeling and
walk-forward validation so that reported performance figures reflect
what a real account would have experienced.

Cost model
----------
  Slippage : SLIPPAGE_PCT applied at both entry and exit.
             entry_fill = close * (1 + slippage)
             exit_fill  = close * (1 - slippage)
  Commission: COMMISSION_PER_SHARE per share per side (entry + exit).

Walk-forward validation
-----------------------
  run_walk_forward() slides a rolling window month-by-month across the
  requested date range, trains indicators on `train_months` and evaluates
  on the following `val_months`.  Results expose gross vs net metrics so
  the "backtest inflation" introduced by ignoring costs is explicit.

No forward-looking bias: indicators for each simulated day are computed
only on df.iloc[:idx+1] — no future data leaks.

Usage
-----
  from backtest.honest_backtest import run_honest_backtest, run_walk_forward, BacktestConfig
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from analysis.indicators import add_all_indicators
from analysis.signals import Signal, BUY_THRESHOLD, SELL_THRESHOLD, generate_signal
from config import Config
from data.fetcher import fetch_batch
from utils.logger import get_logger

logger = get_logger("honest_backtest")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLIPPAGE_PCT: float = 0.001          # 0.1% each way
COMMISSION_PER_SHARE: float = 0.01   # $0.01 per share per side


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    slippage_pct: float = SLIPPAGE_PCT
    commission_per_share: float = COMMISSION_PER_SHARE
    initial_capital: float = field(default_factory=lambda: Config.INITIAL_CAPITAL)
    max_position_pct: float = field(default_factory=lambda: Config.MAX_POSITION_PCT)
    stop_loss_pct: float = field(default_factory=lambda: Config.STOP_LOSS_PCT)
    max_open_positions: int = field(default_factory=lambda: Config.MAX_OPEN_POSITIONS)


# ---------------------------------------------------------------------------
# Extended SimTrade — tracks gross and net separately
# ---------------------------------------------------------------------------

@dataclass
class HonestSimTrade:
    symbol: str
    trade_id: str
    entry_date: str
    exit_date: str = ""
    entry_price_mid: float = 0.0      # unadjusted close price at entry
    exit_price_mid: float = 0.0       # unadjusted close price at exit
    entry_fill: float = 0.0           # actual fill with slippage
    exit_fill: float = 0.0            # actual fill with slippage
    shares: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    exit_reason: str = ""
    # Cost accounting
    entry_commission: float = 0.0
    exit_commission: float = 0.0
    slippage_cost: float = 0.0        # total slippage drag (both sides, absolute $)
    commission_cost: float = 0.0      # total commissions (both sides)
    # P&L
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    gross_pnl_pct: float = 0.0
    net_pnl_pct: float = 0.0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class HonestBacktestResult:
    period: str = ""
    config: dict = field(default_factory=dict)

    # Gross (no costs) metrics
    gross_initial_capital: float = 0.0
    gross_final_equity: float = 0.0
    gross_total_return: float = 0.0
    gross_win_rate: float = 0.0
    gross_profit_factor: float = 0.0
    gross_sharpe: float = 0.0
    gross_sortino: float = 0.0
    gross_calmar: float = 0.0
    gross_max_drawdown: float = 0.0
    gross_var_95: float = 0.0
    gross_annual_return: float = 0.0

    # Net (with costs) metrics
    net_initial_capital: float = 0.0
    net_final_equity: float = 0.0
    net_total_return: float = 0.0
    net_win_rate: float = 0.0
    net_profit_factor: float = 0.0
    net_sharpe: float = 0.0
    net_sortino: float = 0.0
    net_calmar: float = 0.0
    net_max_drawdown: float = 0.0
    net_var_95: float = 0.0
    net_annual_return: float = 0.0

    # Aggregate cost summary
    total_trades: int = 0
    total_slippage_cost: float = 0.0
    total_commission_cost: float = 0.0
    total_cost_drag: float = 0.0      # gross_return - net_return (percentage points)

    equity_curve_gross: list[float] = field(default_factory=list)
    equity_curve_net: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    by_symbol: dict = field(default_factory=dict)

    def inflation_report(self) -> str:
        lines = [
            f"\n{'='*70}",
            f"  HONEST BACKTEST — INFLATION REPORT — {self.period}",
            f"{'='*70}",
            f"  {'Metric':<28} {'Gross (no costs)':>18} {'Net (with costs)':>18}",
            f"  {'-'*64}",
            f"  {'Total Return':<28} {self.gross_total_return:>17.2%} {self.net_total_return:>17.2%}",
            f"  {'Annual Return':<28} {self.gross_annual_return:>17.2%} {self.net_annual_return:>17.2%}",
            f"  {'Sharpe Ratio':<28} {self.gross_sharpe:>17.2f} {self.net_sharpe:>17.2f}",
            f"  {'Sortino Ratio':<28} {self.gross_sortino:>17.2f} {self.net_sortino:>17.2f}",
            f"  {'Calmar Ratio':<28} {self.gross_calmar:>17.2f} {self.net_calmar:>17.2f}",
            f"  {'Max Drawdown':<28} {self.gross_max_drawdown:>17.2%} {self.net_max_drawdown:>17.2%}",
            f"  {'VaR 95%':<28} {self.gross_var_95:>17.2%} {self.net_var_95:>17.2%}",
            f"  {'Win Rate':<28} {self.gross_win_rate:>17.1%} {self.net_win_rate:>17.1%}",
            f"  {'Profit Factor':<28} {self.gross_profit_factor:>17.2f} {self.net_profit_factor:>17.2f}",
            f"  {'-'*64}",
            f"  {'Total Trades':<28} {self.total_trades:>36}",
            f"  {'Total Slippage Cost':<28} ${self.total_slippage_cost:>35,.2f}",
            f"  {'Total Commission Cost':<28} ${self.total_commission_cost:>35,.2f}",
            f"  {'Cost Drag (pp)':<28} {self.total_cost_drag:>36.2%}",
            f"{'='*70}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Walk-forward window result
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardWindow:
    window_start: str
    window_end: str
    train_start: str
    train_end: str
    val_start: str
    val_end: str
    gross_return: float
    net_return: float
    sharpe_gross: float
    sharpe_net: float
    slippage_drag: float              # gross_return - net_return
    trades_in_window: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_metrics(pnl_series: list[float], equity_curve: list[float], initial_capital: float, n_trading_days: int) -> dict:
    """Derive annualised performance metrics from a list of per-trade P&L values."""
    if not pnl_series or not equity_curve:
        return {
            "win_rate": 0.0, "profit_factor": 0.0, "sharpe": 0.0,
            "sortino": 0.0, "calmar": 0.0, "max_drawdown": 0.0,
            "var_95": 0.0, "annual_return": 0.0,
            "total_return": 0.0, "final_equity": initial_capital,
        }

    final_equity = equity_curve[-1]
    total_return = (final_equity - initial_capital) / initial_capital

    years = max(n_trading_days / 252, 1 / 252)
    annual_return = (1 + total_return) ** (1 / years) - 1

    wins = [p for p in pnl_series if p > 0]
    losses = [p for p in pnl_series if p <= 0]
    win_rate = len(wins) / len(pnl_series) if pnl_series else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Compute equity-curve daily returns for Sharpe/Sortino
    eq = pd.Series(equity_curve)
    daily_rets = eq.pct_change().dropna()

    if len(daily_rets) > 1 and daily_rets.std() > 0:
        sharpe = float((daily_rets.mean() / daily_rets.std()) * (252 ** 0.5))
    else:
        sharpe = 0.0

    downside = daily_rets[daily_rets < 0]
    if len(downside) > 1 and downside.std() > 0:
        sortino = float((daily_rets.mean() / downside.std()) * (252 ** 0.5))
    else:
        sortino = 0.0

    # Max drawdown from equity curve
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    calmar = annual_return / max_dd if max_dd > 0 else float("inf")

    # VaR 95% (daily returns)
    var_95 = float(np.percentile(daily_rets, 5)) if len(daily_rets) >= 20 else 0.0

    return {
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_dd,
        "var_95": var_95,
        "annual_return": annual_return,
        "total_return": total_return,
        "final_equity": final_equity,
    }


def _json_log_trade(trade: HonestSimTrade) -> None:
    """Append a structured JSON trade log entry."""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "honest_backtest_trades.jsonl")
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "module": "honest_backtest",
        "event": "trade_closed",
        "trade_id": trade.trade_id,
        "symbol": trade.symbol,
        "entry_date": trade.entry_date,
        "exit_date": trade.exit_date,
        "shares": trade.shares,
        "exit_reason": trade.exit_reason,
        "gross_pnl": round(trade.gross_pnl, 4),
        "net_pnl": round(trade.net_pnl, 4),
        "gross_pnl_pct": round(trade.gross_pnl_pct, 6),
        "net_pnl_pct": round(trade.net_pnl_pct, 6),
        "slippage_cost": round(trade.slippage_cost, 4),
        "commission_cost": round(trade.commission_cost, 4),
    }
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning(f"Could not write trade log: {exc}")


# ---------------------------------------------------------------------------
# Core simulation engine (shared by both gross and net passes)
# ---------------------------------------------------------------------------

def _simulate(
    indicator_data: dict[str, pd.DataFrame],
    all_dates: list,
    start_idx: int,
    cfg: BacktestConfig,
    apply_costs: bool,
) -> tuple[list[HonestSimTrade], list[float]]:
    """
    Single simulation pass over indicator_data.

    apply_costs=False  → gross pass (mid prices, no commissions)
    apply_costs=True   → net pass (slippage-adjusted fills + commissions)

    Returns (closed_trades, equity_curve).
    """
    equity = cfg.initial_capital
    cash = cfg.initial_capital
    equity_curve: list[float] = [equity]
    open_positions: dict[str, HonestSimTrade] = {}
    closed_trades: list[HonestSimTrade] = []
    trade_counter = 0

    for day_i in range(start_idx, len(all_dates)):
        date = all_dates[day_i]
        date_str = str(date)

        # ---- Check exits ----
        for sym in list(open_positions.keys()):
            if sym not in indicator_data:
                continue
            df = indicator_data[sym]
            if date not in df.index:
                continue
            bar = df.loc[date]
            pos = open_positions[sym]

            exit_mid: Optional[float] = None
            exit_reason: Optional[str] = None

            if bar["low"] <= pos.stop_loss:
                exit_mid = pos.stop_loss
                exit_reason = "stop_loss"
            elif bar["high"] >= pos.take_profit:
                exit_mid = pos.take_profit
                exit_reason = "take_profit"
            else:
                idx = df.index.get_loc(date)
                hist = df.iloc[: idx + 1]
                if len(hist) >= 2:
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2]
                    if latest["ema_fast"] < latest["ema_slow"] and prev["ema_fast"] >= prev["ema_slow"]:
                        exit_mid = float(bar["close"])
                        exit_reason = "signal"

            if exit_mid is not None:
                pos.exit_price_mid = exit_mid
                pos.exit_date = date_str
                pos.exit_reason = exit_reason

                if apply_costs:
                    exit_fill = exit_mid * (1.0 - cfg.slippage_pct)
                    exit_commission = cfg.commission_per_share * pos.shares
                    entry_slippage_cost = (pos.entry_fill - pos.entry_price_mid) * pos.shares
                    exit_slippage_cost = (pos.exit_price_mid - exit_fill) * pos.shares
                    pos.exit_fill = exit_fill
                    pos.exit_commission = exit_commission
                    pos.slippage_cost = entry_slippage_cost + exit_slippage_cost
                    pos.commission_cost = pos.entry_commission + exit_commission

                    gross_pnl = (exit_mid - pos.entry_price_mid) * pos.shares
                    net_pnl = (exit_fill - pos.entry_fill) * pos.shares - pos.commission_cost
                    cash += exit_fill * pos.shares - exit_commission
                else:
                    pos.exit_fill = exit_mid
                    gross_pnl = (exit_mid - pos.entry_price_mid) * pos.shares
                    net_pnl = gross_pnl
                    cash += exit_mid * pos.shares

                pos.gross_pnl = gross_pnl
                pos.net_pnl = net_pnl
                cost_basis = pos.entry_price_mid * pos.shares if pos.entry_price_mid > 0 else 1.0
                pos.gross_pnl_pct = gross_pnl / cost_basis
                pos.net_pnl_pct = net_pnl / cost_basis

                closed_trades.append(pos)
                del open_positions[sym]

                if apply_costs:
                    _json_log_trade(pos)

        # ---- Check entries ----
        if len(open_positions) < cfg.max_open_positions:
            for sym, df in indicator_data.items():
                if sym in open_positions:
                    continue
                if len(open_positions) >= cfg.max_open_positions:
                    break
                if date not in df.index:
                    continue

                idx = df.index.get_loc(date)
                if idx < 50:
                    continue

                # No forward-looking bias: slice up to and including current day
                signal, score = generate_signal(df.iloc[: idx + 1])
                if signal != Signal.BUY:
                    continue

                bar = df.loc[date]
                price_mid = float(bar["close"])
                atr = float(bar["atr"])

                risk_amount = equity * cfg.stop_loss_pct
                stop_distance = max(atr, price_mid * cfg.stop_loss_pct, price_mid * 0.001)
                shares_by_risk = int(risk_amount / stop_distance)
                shares_by_pct = int((equity * cfg.max_position_pct) / price_mid)
                shares = max(min(shares_by_risk, shares_by_pct), 1)

                if apply_costs:
                    entry_fill = price_mid * (1.0 + cfg.slippage_pct)
                    entry_commission = cfg.commission_per_share * shares
                    cost = entry_fill * shares + entry_commission
                else:
                    entry_fill = price_mid
                    entry_commission = 0.0
                    cost = price_mid * shares

                if cost > cash * 0.95:
                    continue

                stop_loss = price_mid - stop_distance
                take_profit = price_mid + (stop_distance * 3)

                trade_counter += 1
                trade_id = f"{sym}_{date_str}_{trade_counter}"

                pos = HonestSimTrade(
                    symbol=sym,
                    trade_id=trade_id,
                    entry_date=date_str,
                    entry_price_mid=price_mid,
                    entry_fill=entry_fill,
                    shares=shares,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    entry_commission=entry_commission,
                )
                open_positions[sym] = pos
                cash -= cost

        # Update equity (mark to market at closing mid price)
        position_value = 0.0
        for sym, pos in open_positions.items():
            df = indicator_data[sym]
            if date in df.index:
                position_value += float(df.loc[date]["close"]) * pos.shares
            else:
                position_value += pos.entry_price_mid * pos.shares

        equity = cash + position_value
        equity_curve.append(equity)

    # Close any remaining open positions at last available price
    for sym, pos in list(open_positions.items()):
        df = indicator_data[sym]
        exit_mid = float(df.iloc[-1]["close"])
        pos.exit_price_mid = exit_mid
        pos.exit_date = str(all_dates[-1])
        pos.exit_reason = "end_of_data"

        if apply_costs:
            exit_fill = exit_mid * (1.0 - cfg.slippage_pct)
            exit_commission = cfg.commission_per_share * pos.shares
            entry_slippage_cost = (pos.entry_fill - pos.entry_price_mid) * pos.shares
            exit_slippage_cost = (pos.exit_price_mid - exit_fill) * pos.shares
            pos.exit_fill = exit_fill
            pos.exit_commission = exit_commission
            pos.slippage_cost = entry_slippage_cost + exit_slippage_cost
            pos.commission_cost = pos.entry_commission + exit_commission
            gross_pnl = (exit_mid - pos.entry_price_mid) * pos.shares
            net_pnl = (exit_fill - pos.entry_fill) * pos.shares - pos.commission_cost
            cash += exit_fill * pos.shares - exit_commission
        else:
            pos.exit_fill = exit_mid
            gross_pnl = (exit_mid - pos.entry_price_mid) * pos.shares
            net_pnl = gross_pnl
            cash += exit_mid * pos.shares

        pos.gross_pnl = gross_pnl
        pos.net_pnl = net_pnl
        cost_basis = pos.entry_price_mid * pos.shares if pos.entry_price_mid > 0 else 1.0
        pos.gross_pnl_pct = gross_pnl / cost_basis
        pos.net_pnl_pct = net_pnl / cost_basis
        closed_trades.append(pos)

        if apply_costs:
            _json_log_trade(pos)

    equity = cash
    equity_curve[-1] = equity

    return closed_trades, equity_curve


# ---------------------------------------------------------------------------
# Public API: run_honest_backtest
# ---------------------------------------------------------------------------

def run_honest_backtest(
    symbols: list[str],
    lookback_days: int = 500,
    cfg: Optional[BacktestConfig] = None,
) -> HonestBacktestResult:
    """
    Run the honest backtest — computes both gross (no costs) and net (with
    costs) metrics in parallel simulation passes over the same data.

    Returns a HonestBacktestResult containing an inflation report.
    """
    if cfg is None:
        cfg = BacktestConfig()

    logger.info(f"[honest_backtest] Fetching {len(symbols)} symbols ({lookback_days} days)...")
    raw_data = fetch_batch(symbols, lookback_days=lookback_days)

    if not raw_data:
        logger.error("[honest_backtest] No data fetched")
        return HonestBacktestResult()

    indicator_data = _prepare_indicators(raw_data)
    if not indicator_data:
        logger.error("[honest_backtest] No symbols had enough indicator data")
        return HonestBacktestResult()

    all_dates = sorted(set().union(*(df.index for df in indicator_data.values())))
    start_idx = max(50, len(all_dates) // 10)
    n_trading_days = len(all_dates) - start_idx

    logger.info(
        f"[honest_backtest] Simulating {n_trading_days} days "
        f"across {len(indicator_data)} symbols (2 passes: gross + net)..."
    )

    # Gross pass (no costs)
    gross_trades, gross_curve = _simulate(indicator_data, all_dates, start_idx, cfg, apply_costs=False)
    # Net pass (full costs)
    net_trades, net_curve = _simulate(indicator_data, all_dates, start_idx, cfg, apply_costs=True)

    gross_pnls = [t.gross_pnl for t in gross_trades]
    net_pnls = [t.net_pnl for t in net_trades]

    gross_m = _compute_metrics(gross_pnls, gross_curve, cfg.initial_capital, n_trading_days)
    net_m = _compute_metrics(net_pnls, net_curve, cfg.initial_capital, n_trading_days)

    total_slippage = sum(t.slippage_cost for t in net_trades)
    total_commission = sum(t.commission_cost for t in net_trades)
    cost_drag = gross_m["total_return"] - net_m["total_return"]

    # Per-symbol stats (net)
    by_symbol: dict[str, dict] = {}
    for t in net_trades:
        sym = t.symbol
        if sym not in by_symbol:
            by_symbol[sym] = {"trades": 0, "wins": 0, "total_net_pnl": 0.0}
        by_symbol[sym]["trades"] += 1
        by_symbol[sym]["total_net_pnl"] += t.net_pnl
        if t.net_pnl > 0:
            by_symbol[sym]["wins"] += 1
    for sym in by_symbol:
        s = by_symbol[sym]
        s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0.0

    period_str = f"{all_dates[start_idx].date()} → {all_dates[-1].date()}"

    result = HonestBacktestResult(
        period=period_str,
        config=asdict(cfg),
        gross_initial_capital=cfg.initial_capital,
        gross_final_equity=gross_m["final_equity"],
        gross_total_return=gross_m["total_return"],
        gross_win_rate=gross_m["win_rate"],
        gross_profit_factor=gross_m["profit_factor"],
        gross_sharpe=gross_m["sharpe"],
        gross_sortino=gross_m["sortino"],
        gross_calmar=gross_m["calmar"],
        gross_max_drawdown=gross_m["max_drawdown"],
        gross_var_95=gross_m["var_95"],
        gross_annual_return=gross_m["annual_return"],
        net_initial_capital=cfg.initial_capital,
        net_final_equity=net_m["final_equity"],
        net_total_return=net_m["total_return"],
        net_win_rate=net_m["win_rate"],
        net_profit_factor=net_m["profit_factor"],
        net_sharpe=net_m["sharpe"],
        net_sortino=net_m["sortino"],
        net_calmar=net_m["calmar"],
        net_max_drawdown=net_m["max_drawdown"],
        net_var_95=net_m["var_95"],
        net_annual_return=net_m["annual_return"],
        total_trades=len(net_trades),
        total_slippage_cost=total_slippage,
        total_commission_cost=total_commission,
        total_cost_drag=cost_drag,
        equity_curve_gross=gross_curve,
        equity_curve_net=net_curve,
        trades=[asdict(t) for t in net_trades],
        by_symbol=by_symbol,
    )

    logger.info(f"[honest_backtest] Done. {len(net_trades)} trades, cost drag={cost_drag:.2%}")
    return result


# ---------------------------------------------------------------------------
# Public API: run_walk_forward
# ---------------------------------------------------------------------------

def run_walk_forward(
    symbols: list[str],
    start_date: date,
    end_date: date,
    train_months: int = 9,
    val_months: int = 3,
    cfg: Optional[BacktestConfig] = None,
) -> list[WalkForwardWindow]:
    """
    Sliding walk-forward validation.

    For each window the training period is used only for indicator
    warm-up; evaluation metrics are computed on the validation slice.

    Returns a list of WalkForwardWindow, one per rolling step.
    """
    if cfg is None:
        cfg = BacktestConfig()

    windows: list[WalkForwardWindow] = []

    # Build window boundaries: step by 1 month
    window_start = start_date
    total_months = train_months + val_months

    while True:
        val_start = _add_months(window_start, train_months)
        val_end = _add_months(window_start, total_months)

        if val_end > end_date:
            break

        logger.info(
            f"[walk_forward] Window {window_start} → {val_end} "
            f"(train {window_start}→{val_start}, val {val_start}→{val_end})"
        )

        # Total lookback in days for this window
        lookback_days = (val_end - window_start).days + 30  # buffer

        raw_data = fetch_batch(symbols, lookback_days=lookback_days)
        if not raw_data:
            logger.warning("[walk_forward] No data, skipping window")
            window_start = _add_months(window_start, 1)
            continue

        indicator_data = _prepare_indicators(raw_data)
        if not indicator_data:
            window_start = _add_months(window_start, 1)
            continue

        all_dates = sorted(set().union(*(df.index for df in indicator_data.values())))

        # Filter to validation slice only for metrics
        ws_ts = pd.Timestamp(window_start)
        vs_ts = pd.Timestamp(val_start)
        ve_ts = pd.Timestamp(val_end)

        # Determine start_idx: the first index that falls within validation range,
        # but allow indicator warm-up from the full window start
        full_start_idx = max(50, next(
            (i for i, d in enumerate(all_dates) if d >= ws_ts), 50
        ))
        val_start_idx = next(
            (i for i, d in enumerate(all_dates) if d >= vs_ts), len(all_dates)
        )
        val_end_idx = next(
            (i for i, d in enumerate(all_dates) if d > ve_ts), len(all_dates)
        )

        if val_start_idx >= val_end_idx:
            window_start = _add_months(window_start, 1)
            continue

        # Simulate the whole window (for state carry-over) then slice metrics
        gross_trades_all, gross_curve_all = _simulate(
            indicator_data, all_dates, full_start_idx, cfg, apply_costs=False
        )
        net_trades_all, net_curve_all = _simulate(
            indicator_data, all_dates, full_start_idx, cfg, apply_costs=True
        )

        # Filter trades that closed within the validation period
        def in_val(exit_date_str: str) -> bool:
            try:
                ts = pd.Timestamp(exit_date_str)
                return vs_ts <= ts <= ve_ts
            except Exception:
                return False

        gross_val = [t for t in gross_trades_all if in_val(t.exit_date)]
        net_val = [t for t in net_trades_all if in_val(t.exit_date)]

        # Slice equity curves to the validation range
        gc_slice = gross_curve_all[val_start_idx - full_start_idx: val_end_idx - full_start_idx + 1]
        nc_slice = net_curve_all[val_start_idx - full_start_idx: val_end_idx - full_start_idx + 1]

        n_days_val = val_end_idx - val_start_idx

        gross_m = _compute_metrics(
            [t.gross_pnl for t in gross_val],
            gc_slice if len(gc_slice) >= 2 else gross_curve_all,
            cfg.initial_capital,
            n_days_val,
        )
        net_m = _compute_metrics(
            [t.net_pnl for t in net_val],
            nc_slice if len(nc_slice) >= 2 else net_curve_all,
            cfg.initial_capital,
            n_days_val,
        )

        win = WalkForwardWindow(
            window_start=str(window_start),
            window_end=str(val_end),
            train_start=str(window_start),
            train_end=str(val_start),
            val_start=str(val_start),
            val_end=str(val_end),
            gross_return=gross_m["total_return"],
            net_return=net_m["total_return"],
            sharpe_gross=gross_m["sharpe"],
            sharpe_net=net_m["sharpe"],
            slippage_drag=gross_m["total_return"] - net_m["total_return"],
            trades_in_window=len(net_val),
        )
        windows.append(win)
        window_start = _add_months(window_start, 1)

    _print_walk_forward_summary(windows, train_months, val_months)
    return windows


# ---------------------------------------------------------------------------
# Walk-forward helpers
# ---------------------------------------------------------------------------

def _add_months(d: date, months: int) -> date:
    """Add calendar months to a date, clamping to month-end."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    day = min(d.day, last_day)
    return date(year, month, day)


def _print_walk_forward_summary(windows: list[WalkForwardWindow], train_months: int, val_months: int) -> None:
    if not windows:
        print("No walk-forward windows completed.")
        return

    header = (
        f"\n{'='*90}\n"
        f"  WALK-FORWARD SUMMARY  "
        f"(train={train_months}m  val={val_months}m  windows={len(windows)})\n"
        f"{'='*90}\n"
        f"  {'Val Period':<22} {'Gross Ret':>10} {'Net Ret':>10} "
        f"{'Sharpe(G)':>10} {'Sharpe(N)':>10} {'Drag':>8} {'Trades':>7}\n"
        f"  {'-'*82}"
    )
    print(header)
    for w in windows:
        print(
            f"  {w.val_start} → {w.val_end}  "
            f"{w.gross_return:>9.2%} {w.net_return:>9.2%} "
            f"{w.sharpe_gross:>9.2f} {w.sharpe_net:>9.2f} "
            f"{w.slippage_drag:>7.2%} {w.trades_in_window:>7}"
        )

    avg_gross = sum(w.gross_return for w in windows) / len(windows)
    avg_net = sum(w.net_return for w in windows) / len(windows)
    avg_drag = sum(w.slippage_drag for w in windows) / len(windows)
    avg_sharpe_g = sum(w.sharpe_gross for w in windows) / len(windows)
    avg_sharpe_n = sum(w.sharpe_net for w in windows) / len(windows)
    total_trades = sum(w.trades_in_window for w in windows)
    pos_windows = sum(1 for w in windows if w.net_return > 0)

    print(f"  {'-'*82}")
    print(
        f"  {'AVERAGE':<22}  "
        f"{avg_gross:>9.2%} {avg_net:>9.2%} "
        f"{avg_sharpe_g:>9.2f} {avg_sharpe_n:>9.2f} "
        f"{avg_drag:>7.2%} {total_trades:>7}"
    )
    print(f"\n  Profitable windows : {pos_windows}/{len(windows)}")
    print(f"{'='*90}\n")


# ---------------------------------------------------------------------------
# Indicator preparation (shared helper)
# ---------------------------------------------------------------------------

def _prepare_indicators(raw_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Add indicators and drop symbols with insufficient history."""
    indicator_data: dict[str, pd.DataFrame] = {}
    for sym, df in raw_data.items():
        try:
            idf = add_all_indicators(df)
            idf = idf.dropna(subset=["ema_fast", "ema_slow", "ema_trend", "rsi", "macd_hist", "atr"])
            if len(idf) >= 60:
                indicator_data[sym] = idf
        except Exception as exc:
            logger.warning(f"[honest_backtest] Indicator error for {sym}: {exc}")
    return indicator_data


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Honest portfolio backtest with cost modeling")
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--symbols", type=int, default=20)
    parser.add_argument("--output", type=str, default="")
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="Run walk-forward validation instead of single backtest"
    )
    parser.add_argument("--train-months", type=int, default=9)
    parser.add_argument("--val-months", type=int, default=3)
    args = parser.parse_args()

    symbols = Config.WATCHLIST[: args.symbols]

    if args.walk_forward:
        end = date.today()
        start = end - timedelta(days=args.days)
        run_walk_forward(symbols, start, end, args.train_months, args.val_months)
    else:
        result = run_honest_backtest(symbols, lookback_days=args.days)
        print(result.inflation_report())

        if args.output:
            with open(args.output, "w") as f:
                json.dump(asdict(result), f, indent=2, default=str)
            print(f"\nResults saved to {args.output}")
