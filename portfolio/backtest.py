"""
Portfolio backtest driver — steps rebalance dates through the SAME
portfolio.engine.rebalance() path the paper engine uses, so backtest and paper
behave identically (blueprint §6, §7).

Decisions are made at a rebalance date using only bars up to that date; fills
happen at the NEXT session's close (no same-bar signal/fill). Between rebalances,
holdings are held and the equity curve is marked at each session's close.

Strategies here are RESEARCH sleeves — the driver passes an explicit
`sleeve_tradeable` map so the plumbing runs even though the registry keeps the
strategies un-tradeable until validated. This driver does not enable live trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from portfolio.book import PortfolioBook
from portfolio.engine import rebalance
from strategy.momentum import monthly_rebalance_dates, weekly_rebalance_dates


@dataclass
class Sleeve:
    module: object          # strategy module with generate_intents + CADENCE
    config: dict = field(default_factory=dict)

    @property
    def strategy_id(self) -> str:
        return self.module.STRATEGY_ID

    @property
    def cadence(self) -> str:
        return self.module.CADENCE


@dataclass
class BacktestResult:
    equity: pd.Series                    # daily portfolio market value
    book: object
    n_rebalances: int
    n_fills: int
    profile_id: str
    scenario: str

    def daily_returns(self) -> pd.Series:
        return self.equity.pct_change().dropna()


def _closes_on(bars_by_symbol: dict, day) -> dict:
    out = {}
    for sym, bars in bars_by_symbol.items():
        if day in bars.index:
            out[sym] = float(bars.loc[day, "close"])
    return out


def run_portfolio_backtest(bars_by_symbol: dict, profile, sleeves: list,
                           scenario: str = "base") -> BacktestResult:
    """Run one capital profile over the shared rebalance path."""
    book = PortfolioBook(profile.id, {s.strategy_id: profile.budget_capital(s.strategy_id)
                                      for s in sleeves})
    # Union trading calendar across symbols.
    all_days = sorted(set().union(*[set(b.index) for b in bars_by_symbol.values()]))
    idx = pd.DatetimeIndex(all_days)
    weekly = set(weekly_rebalance_dates(idx))
    monthly = set(monthly_rebalance_dates(idx))
    sched = {"weekly": weekly, "monthly": monthly}

    current: dict[str, list] = {s.strategy_id: [] for s in sleeves}
    tradeable = {s.strategy_id: True for s in sleeves}
    processed: set = set()
    equity_vals, equity_idx = [], []
    n_reb = n_fill = 0

    for i, day in enumerate(all_days):
        prices_today = _closes_on(bars_by_symbol, day)
        scheduled = [s for s in sleeves if day in sched[s.cadence]]
        for s in scheduled:
            current[s.strategy_id] = s.module.generate_intents(day, bars_by_symbol, s.config)
        # Execute at the NEXT session's close (no same-bar fill).
        if scheduled and i + 1 < len(all_days):
            fill_prices = _closes_on(bars_by_symbol, all_days[i + 1])
            res = rebalance(profile, book, current, fill_prices, scenario=scenario,
                            sleeve_tradeable=tradeable, processed_keys=processed,
                            ts=pd.Timestamp(day).isoformat())
            n_reb += 1
            n_fill += len(res.fills)
        equity_vals.append(book.market_value(prices_today))
        equity_idx.append(day)

    return BacktestResult(pd.Series(equity_vals, index=pd.DatetimeIndex(equity_idx)),
                          book, n_reb, n_fill, profile.id, scenario)
