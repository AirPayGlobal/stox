"""
Portfolio-level backtester — simulates the full bot across multiple symbols
simultaneously, respecting position limits, capital allocation, and risk rules.

Usage:
    python backtest/portfolio_backtest.py                    # default 1yr
    python backtest/portfolio_backtest.py --days 730         # 2 years
    python backtest/portfolio_backtest.py --output results.json
"""
from __future__ import annotations

import json
import sys
import os
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from analysis.indicators import add_all_indicators
from analysis.signals import Signal, BUY_THRESHOLD, SELL_THRESHOLD, generate_signal
from config import Config
from data.fetcher import fetch_batch
from utils.logger import get_logger

logger = get_logger("portfolio_backtest")


@dataclass
class SimTrade:
    symbol: str
    entry_date: str
    exit_date: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    shares: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""


@dataclass
class PortfolioBacktestResult:
    period: str = ""
    initial_capital: float = 0.0
    final_equity: float = 0.0
    total_return: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_holding_days: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trades: list[SimTrade] = field(default_factory=list)
    by_symbol: dict = field(default_factory=dict)
    parameters: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"  PORTFOLIO BACKTEST — {self.period}",
            f"{'='*60}",
            f"  Initial Capital  : ${self.initial_capital:,.2f}",
            f"  Final Equity     : ${self.final_equity:,.2f}",
            f"  Total Return     : {self.total_return:.2%}",
            f"  Total Trades     : {self.total_trades}",
            f"  Win Rate         : {self.win_rate:.1%}",
            f"  Profit Factor    : {self.profit_factor:.2f}x",
            f"  Max Drawdown     : {self.max_drawdown:.2%}",
            f"  Sharpe Ratio     : {self.sharpe_ratio:.2f}",
            f"  Avg Trade P&L    : ${self.avg_trade_pnl:,.2f}",
            f"  Avg Holding Days : {self.avg_holding_days:.1f}",
            f"{'='*60}",
        ]
        return "\n".join(lines)


def _align_dataframes(data: dict[str, pd.DataFrame]) -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame]]:
    """Find common date range and reindex all dataframes."""
    all_dates = set()
    for df in data.values():
        all_dates.update(df.index)
    common_dates = sorted(all_dates)
    index = pd.DatetimeIndex(common_dates)
    aligned = {}
    for sym, df in data.items():
        aligned[sym] = df.reindex(index, method="ffill").dropna()
    return index, aligned


def run_portfolio_backtest(
    symbols: list[str],
    lookback_days: int = 500,
    initial_capital: float = None,
) -> PortfolioBacktestResult:
    if initial_capital is None:
        initial_capital = Config.INITIAL_CAPITAL

    logger.info(f"Fetching data for {len(symbols)} symbols ({lookback_days} days)...")
    raw_data = fetch_batch(symbols, lookback_days=lookback_days)

    if not raw_data:
        logger.error("No data fetched")
        return PortfolioBacktestResult()

    # Prepare indicator data for each symbol
    indicator_data = {}
    for sym, df in raw_data.items():
        idf = add_all_indicators(df)
        idf = idf.dropna(subset=["ema_fast", "ema_slow", "ema_trend", "rsi", "macd_hist", "atr"])
        if len(idf) >= 60:
            indicator_data[sym] = idf

    if not indicator_data:
        logger.error("No symbols had enough data after indicator warmup")
        return PortfolioBacktestResult()

    # Find common date range
    all_dates = sorted(set().union(*(df.index for df in indicator_data.values())))
    start_idx = max(50, len(all_dates) // 10)  # skip first 10% for warmup

    equity = initial_capital
    cash = initial_capital
    equity_curve = [equity]
    open_positions: dict[str, SimTrade] = {}
    closed_trades: list[SimTrade] = []

    logger.info(f"Simulating {len(all_dates) - start_idx} trading days across {len(indicator_data)} symbols...")

    for day_i in range(start_idx, len(all_dates)):
        date = all_dates[day_i]
        date_str = str(date)

        # --- Check exits for open positions ---
        for sym in list(open_positions.keys()):
            if sym not in indicator_data:
                continue
            df = indicator_data[sym]
            if date not in df.index:
                continue
            bar = df.loc[date]
            pos = open_positions[sym]

            exit_price = None
            exit_reason = None

            if bar["low"] <= pos.stop_loss:
                exit_price = pos.stop_loss
                exit_reason = "stop_loss"
            elif bar["high"] >= pos.take_profit:
                exit_price = pos.take_profit
                exit_reason = "take_profit"
            else:
                # Check signal-based exit
                hist = df.loc[:date]
                if len(hist) >= 2:
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2]
                    if latest["ema_fast"] < latest["ema_slow"] and prev["ema_fast"] >= prev["ema_slow"]:
                        exit_price = bar["close"]
                        exit_reason = "signal"

            if exit_price is not None:
                pnl = (exit_price - pos.entry_price) * pos.shares
                pos.exit_price = exit_price
                pos.exit_date = date_str
                pos.pnl = pnl
                pos.pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
                pos.exit_reason = exit_reason
                cash += exit_price * pos.shares
                closed_trades.append(pos)
                del open_positions[sym]

        # --- Check entries ---
        if len(open_positions) < Config.MAX_OPEN_POSITIONS:
            for sym, df in indicator_data.items():
                if sym in open_positions:
                    continue
                if len(open_positions) >= Config.MAX_OPEN_POSITIONS:
                    break
                if date not in df.index:
                    continue

                idx = df.index.get_loc(date)
                if idx < 50:
                    continue

                signal, score = generate_signal(df.iloc[:idx + 1])
                if signal != Signal.BUY:
                    continue

                bar = df.loc[date]
                price = bar["close"]
                atr = bar["atr"]

                # Position sizing
                risk_amount = equity * Config.STOP_LOSS_PCT
                stop_distance = max(atr, price * Config.STOP_LOSS_PCT, price * 0.001)
                shares_by_risk = int(risk_amount / stop_distance)
                shares_by_pct = int((equity * Config.MAX_POSITION_PCT) / price)
                shares = max(min(shares_by_risk, shares_by_pct), 1)

                cost = price * shares
                if cost > cash * 0.95:
                    continue

                stop_loss = price - stop_distance
                take_profit = price + (stop_distance * 3)

                pos = SimTrade(
                    symbol=sym,
                    entry_date=date_str,
                    entry_price=price,
                    shares=shares,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
                open_positions[sym] = pos
                cash -= cost

        # Update equity
        position_value = 0.0
        for sym, pos in open_positions.items():
            df = indicator_data[sym]
            if date in df.index:
                position_value += df.loc[date]["close"] * pos.shares
            else:
                position_value += pos.entry_price * pos.shares

        equity = cash + position_value
        equity_curve.append(equity)

    # Close remaining positions at last price
    for sym, pos in list(open_positions.items()):
        df = indicator_data[sym]
        exit_price = df.iloc[-1]["close"]
        pnl = (exit_price - pos.entry_price) * pos.shares
        pos.exit_price = exit_price
        pos.exit_date = str(all_dates[-1])
        pos.pnl = pnl
        pos.pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        pos.exit_reason = "end_of_data"
        cash += exit_price * pos.shares
        closed_trades.append(pos)

    equity = cash

    # Compute metrics
    winners = [t for t in closed_trades if t.pnl > 0]
    losers = [t for t in closed_trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))

    returns = pd.Series([t.pnl_pct for t in closed_trades]) if closed_trades else pd.Series([0.0])
    sharpe = (returns.mean() / returns.std()) * (252 ** 0.5) if returns.std() > 0 else 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    # Per-symbol stats
    by_symbol = {}
    for t in closed_trades:
        if t.symbol not in by_symbol:
            by_symbol[t.symbol] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        by_symbol[t.symbol]["trades"] += 1
        by_symbol[t.symbol]["total_pnl"] += t.pnl
        if t.pnl > 0:
            by_symbol[t.symbol]["wins"] += 1

    for sym in by_symbol:
        s = by_symbol[sym]
        s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0

    # Avg holding days
    holding_days = []
    for t in closed_trades:
        try:
            entry = pd.Timestamp(t.entry_date)
            exit_ = pd.Timestamp(t.exit_date)
            holding_days.append((exit_ - entry).days)
        except Exception:
            pass

    result = PortfolioBacktestResult(
        period=f"{all_dates[start_idx].date()} → {all_dates[-1].date()}",
        initial_capital=initial_capital,
        final_equity=equity,
        total_return=(equity - initial_capital) / initial_capital,
        total_trades=len(closed_trades),
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=len(winners) / len(closed_trades) if closed_trades else 0,
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        max_drawdown=max_dd,
        sharpe_ratio=sharpe,
        avg_trade_pnl=sum(t.pnl for t in closed_trades) / len(closed_trades) if closed_trades else 0,
        avg_holding_days=sum(holding_days) / len(holding_days) if holding_days else 0,
        equity_curve=equity_curve,
        trades=[asdict(t) for t in closed_trades],
        by_symbol=by_symbol,
        parameters={
            "BUY_THRESHOLD": BUY_THRESHOLD,
            "SELL_THRESHOLD": SELL_THRESHOLD,
            "STOP_LOSS_PCT": Config.STOP_LOSS_PCT,
            "TAKE_PROFIT_PCT": Config.TAKE_PROFIT_PCT,
            "MAX_POSITION_PCT": Config.MAX_POSITION_PCT,
            "MAX_OPEN_POSITIONS": Config.MAX_OPEN_POSITIONS,
            "RSI_OVERSOLD": Config.RSI_OVERSOLD,
            "RSI_OVERBOUGHT": Config.RSI_OVERBOUGHT,
            "EMA_FAST": Config.EMA_FAST,
            "EMA_SLOW": Config.EMA_SLOW,
            "EMA_TREND": Config.EMA_TREND,
        },
    )

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Portfolio-level backtest")
    parser.add_argument("--days", type=int, default=500, help="Lookback days")
    parser.add_argument("--output", type=str, default="", help="Output JSON file")
    parser.add_argument("--symbols", type=int, default=20, help="Number of watchlist symbols to test")
    args = parser.parse_args()

    symbols = Config.WATCHLIST[:args.symbols]
    result = run_portfolio_backtest(symbols, lookback_days=args.days)
    print(result.summary())

    if args.output:
        with open(args.output, "w") as f:
            json.dump(asdict(result), f, indent=2, default=str)
        print(f"\nResults saved to {args.output}")
