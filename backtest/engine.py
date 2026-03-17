"""
Backtesting engine — runs a strategy against historical data
and records every simulated trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from analysis.indicators import add_all_indicators
from analysis.signals import Signal, generate_signal
from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestTrade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    stop_loss: float
    take_profit: float
    pnl: float
    pnl_pct: float
    exit_reason: str  # "take_profit" | "stop_loss" | "signal" | "end_of_data"


@dataclass
class BacktestResult:
    symbol: str
    trades: list[BacktestTrade] = field(default_factory=list)
    initial_capital: float = Config.INITIAL_CAPITAL
    final_equity: float = 0.0
    equity_curve: list[float] = field(default_factory=list)

    @property
    def total_return(self) -> float:
        return (self.final_equity - self.initial_capital) / self.initial_capital

    @property
    def win_rate(self) -> float:
        wins = [t for t in self.trades if t.pnl > 0]
        return len(wins) / len(self.trades) if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for v in self.equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def sharpe_ratio(self) -> float:
        """Approximate annualised Sharpe ratio from trade returns."""
        if len(self.trades) < 2:
            return 0.0
        returns = pd.Series([t.pnl_pct for t in self.trades])
        if returns.std() == 0:
            return 0.0
        return (returns.mean() / returns.std()) * (252 ** 0.5)

    def summary(self) -> str:
        lines = [
            f"\n{'='*55}",
            f"  BACKTEST: {self.symbol}",
            f"{'='*55}",
            f"  Initial Capital  : ${self.initial_capital:,.2f}",
            f"  Final Equity     : ${self.final_equity:,.2f}",
            f"  Total Return     : {self.total_return:.2%}",
            f"  Total Trades     : {len(self.trades)}",
            f"  Win Rate         : {self.win_rate:.1%}",
            f"  Profit Factor    : {self.profit_factor:.2f}x",
            f"  Max Drawdown     : {self.max_drawdown:.2%}",
            f"  Sharpe Ratio     : {self.sharpe_ratio:.2f}",
            f"{'='*55}",
        ]
        return "\n".join(lines)


class BacktestEngine:
    """
    Single-symbol event-driven backtester.

    Simulates bar-by-bar signal evaluation and bracket order execution
    (stop-loss and take-profit checked on each subsequent bar).
    """

    def __init__(self, initial_capital: float = Config.INITIAL_CAPITAL) -> None:
        self.initial_capital = initial_capital

    def run(self, symbol: str, df: pd.DataFrame) -> BacktestResult:
        """
        Run backtest on a single symbol.

        Args:
            symbol: ticker symbol (for reporting)
            df: raw OHLCV DataFrame with at least 100 bars
        """
        result = BacktestResult(symbol=symbol, initial_capital=self.initial_capital)
        df = add_all_indicators(df)
        df = df.dropna(subset=["ema_fast", "ema_slow", "ema_trend", "rsi", "macd_hist", "atr"])

        if len(df) < 60:
            logger.warning(f"{symbol}: Not enough data for backtest ({len(df)} bars)")
            return result

        equity = self.initial_capital
        result.equity_curve.append(equity)
        in_position = False
        entry_price = 0.0
        stop_loss = 0.0
        take_profit = 0.0
        shares = 0
        entry_date = ""

        for i in range(50, len(df)):
            bar = df.iloc[i]
            date_str = str(df.index[i])

            if in_position:
                # Check stop-loss (hit intraday low)
                if bar["low"] <= stop_loss:
                    exit_price = stop_loss
                    pnl = (exit_price - entry_price) * shares
                    equity += pnl
                    result.trades.append(
                        BacktestTrade(
                            symbol=symbol,
                            entry_date=entry_date,
                            exit_date=date_str,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=shares,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            pnl=pnl,
                            pnl_pct=pnl / (entry_price * shares),
                            exit_reason="stop_loss",
                        )
                    )
                    in_position = False

                # Check take-profit (hit intraday high)
                elif bar["high"] >= take_profit:
                    exit_price = take_profit
                    pnl = (exit_price - entry_price) * shares
                    equity += pnl
                    result.trades.append(
                        BacktestTrade(
                            symbol=symbol,
                            entry_date=entry_date,
                            exit_date=date_str,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=shares,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            pnl=pnl,
                            pnl_pct=pnl / (entry_price * shares),
                            exit_reason="take_profit",
                        )
                    )
                    in_position = False

                else:
                    # Check signal-based exit
                    signal, _ = generate_signal(df.iloc[: i + 1])
                    if signal == Signal.SELL:
                        exit_price = bar["close"]
                        pnl = (exit_price - entry_price) * shares
                        equity += pnl
                        result.trades.append(
                            BacktestTrade(
                                symbol=symbol,
                                entry_date=entry_date,
                                exit_date=date_str,
                                entry_price=entry_price,
                                exit_price=exit_price,
                                shares=shares,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                pnl=pnl,
                                pnl_pct=pnl / (entry_price * shares),
                                exit_reason="signal",
                            )
                        )
                        in_position = False

            else:
                # Look for entry
                signal, score = generate_signal(df.iloc[: i + 1])
                if signal == Signal.BUY:
                    price = bar["close"]
                    atr = bar["atr"]
                    risk_amount = equity * Config.STOP_LOSS_PCT
                    stop_distance = max(atr, price * Config.STOP_LOSS_PCT)
                    shares_by_risk = int(risk_amount / stop_distance)
                    shares_by_pct = int((equity * Config.MAX_POSITION_PCT) / price)
                    shares = max(min(shares_by_risk, shares_by_pct), 1)

                    if price * shares > equity:
                        shares = max(int(equity / price), 1)

                    entry_price = price
                    stop_loss = price - stop_distance
                    take_profit = price + (stop_distance * 3)
                    entry_date = date_str
                    in_position = True

            result.equity_curve.append(equity)

        # Close any open position at last bar price
        if in_position:
            exit_price = df.iloc[-1]["close"]
            pnl = (exit_price - entry_price) * shares
            equity += pnl
            result.trades.append(
                BacktestTrade(
                    symbol=symbol,
                    entry_date=entry_date,
                    exit_date=str(df.index[-1]),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    shares=shares,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    pnl=pnl,
                    pnl_pct=pnl / (entry_price * shares),
                    exit_reason="end_of_data",
                )
            )

        result.final_equity = equity
        logger.info(f"Backtest {symbol}: {len(result.trades)} trades | return={result.total_return:.2%}")
        return result
