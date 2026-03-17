"""
Portfolio tracker: records all trades, computes performance metrics,
and shows capital growth over time.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

_PORTFOLIO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "logs", "portfolio.json"
)


@dataclass
class Trade:
    symbol: str
    side: str           # "BUY" | "SELL"
    shares: int
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    order_id: str = ""
    opened_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    closed_at: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    status: str = "OPEN"  # OPEN | CLOSED | STOPPED | TOOK_PROFIT


@dataclass
class PortfolioSnapshot:
    timestamp: str
    equity: float
    cash: float
    open_positions: int
    total_trades: int
    winning_trades: int
    total_pnl: float


class Portfolio:
    """
    Tracks all trades and portfolio snapshots for performance analysis.
    Data is persisted to a JSON file so it survives restarts.
    """

    def __init__(self) -> None:
        self.trades: list[Trade] = []
        self.snapshots: list[PortfolioSnapshot] = []
        self._load()

    # ------------------------------------------------------------------ I/O
    def _load(self) -> None:
        os.makedirs(os.path.dirname(_PORTFOLIO_FILE), exist_ok=True)
        if os.path.exists(_PORTFOLIO_FILE):
            try:
                with open(_PORTFOLIO_FILE) as f:
                    data = json.load(f)
                self.trades = [Trade(**t) for t in data.get("trades", [])]
                self.snapshots = [
                    PortfolioSnapshot(**s) for s in data.get("snapshots", [])
                ]
                logger.info(f"Loaded {len(self.trades)} trades from portfolio file")
            except Exception as exc:
                logger.error(f"Could not load portfolio file: {exc}")

    def save(self) -> None:
        try:
            with open(_PORTFOLIO_FILE, "w") as f:
                json.dump(
                    {
                        "trades": [asdict(t) for t in self.trades],
                        "snapshots": [asdict(s) for s in self.snapshots],
                    },
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.error(f"Could not save portfolio: {exc}")

    # ---------------------------------------------------------------- Trades
    def open_trade(
        self,
        symbol: str,
        shares: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        order_id: str = "",
    ) -> Trade:
        trade = Trade(
            symbol=symbol,
            side="BUY",
            shares=shares,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            order_id=order_id,
        )
        self.trades.append(trade)
        self.save()
        logger.info(f"Opened trade: {symbol} x{shares} @ {entry_price:.2f}")
        return trade

    def close_trade(
        self,
        symbol: str,
        exit_price: float,
        status: str = "CLOSED",
    ) -> Optional[Trade]:
        for trade in reversed(self.trades):
            if trade.symbol == symbol and trade.status == "OPEN":
                trade.exit_price = exit_price
                trade.closed_at = datetime.utcnow().isoformat()
                trade.status = status
                trade.pnl = (exit_price - trade.entry_price) * trade.shares
                trade.pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
                self.save()
                logger.info(
                    f"Closed {symbol}: PnL=${trade.pnl:.2f} ({trade.pnl_pct:.2%}) [{status}]"
                )
                return trade
        logger.warning(f"No open trade found for {symbol} to close")
        return None

    # --------------------------------------------------------------- Metrics
    def take_snapshot(
        self,
        equity: float,
        cash: float,
        open_positions: int,
    ) -> None:
        closed = [t for t in self.trades if t.status != "OPEN"]
        winners = [t for t in closed if t.pnl and t.pnl > 0]
        total_pnl = sum(t.pnl for t in closed if t.pnl is not None)
        snap = PortfolioSnapshot(
            timestamp=datetime.utcnow().isoformat(),
            equity=equity,
            cash=cash,
            open_positions=open_positions,
            total_trades=len(closed),
            winning_trades=len(winners),
            total_pnl=total_pnl,
        )
        self.snapshots.append(snap)
        self.save()

    def summary(self) -> dict:
        closed = [t for t in self.trades if t.status != "OPEN"]
        winners = [t for t in closed if t.pnl and t.pnl > 0]
        losers = [t for t in closed if t.pnl and t.pnl <= 0]
        total_pnl = sum(t.pnl for t in closed if t.pnl is not None)
        win_rate = len(winners) / len(closed) if closed else 0
        avg_win = (
            sum(t.pnl for t in winners) / len(winners) if winners else 0
        )
        avg_loss = (
            sum(t.pnl for t in losers) / len(losers) if losers else 0
        )
        profit_factor = (
            abs(sum(t.pnl for t in winners) / sum(t.pnl for t in losers))
            if losers and sum(t.pnl for t in losers) != 0
            else float("inf")
        )
        return {
            "total_trades": len(closed),
            "open_trades": len([t for t in self.trades if t.status == "OPEN"]),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
        }

    def print_summary(self) -> None:
        s = self.summary()
        print("\n" + "=" * 50)
        print("  PORTFOLIO SUMMARY")
        print("=" * 50)
        print(f"  Total Closed Trades : {s['total_trades']}")
        print(f"  Open Trades         : {s['open_trades']}")
        print(f"  Win Rate            : {s['win_rate']:.1%}")
        print(f"  Total P&L           : ${s['total_pnl']:,.2f}")
        print(f"  Avg Win             : ${s['avg_win']:,.2f}")
        print(f"  Avg Loss            : ${s['avg_loss']:,.2f}")
        print(f"  Profit Factor       : {s['profit_factor']:.2f}x")
        print("=" * 50 + "\n")
