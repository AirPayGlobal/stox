"""
Intraday portfolio tracker — persisted to /data/daily_portfolio.json.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from utils.logger import get_logger

logger = get_logger("intraday.portfolio")

_DAILY_PORTFOLIO_FILE = os.environ.get("DAILY_PORTFOLIO_FILE", "/data/daily_portfolio.json")


@dataclass
class IntradayTrade:
    symbol: str
    side: str               # "buy" | "sell"
    shares: int
    entry_price: float
    stop_loss: float
    take_profit: float
    strategy: str           # "ORB" | "VWAP" | "GAP_GO" | "EMA"
    order_id: Optional[str] = None
    status: str = "OPEN"    # "OPEN" | "CLOSED" | "STOPPED" | "TOOK_PROFIT" | "EOD_CLOSE"
    entry_time: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0


class IntradayPortfolio:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trades: list[IntradayTrade] = []
        self._load()

    # ------------------------------------------------------------------ I/O

    def _load(self) -> None:
        try:
            if os.path.exists(_DAILY_PORTFOLIO_FILE):
                with open(_DAILY_PORTFOLIO_FILE) as f:
                    data = json.load(f)
                self._trades = [IntradayTrade(**t) for t in data.get("trades", [])]
                logger.info("Loaded %d intraday trades from disk", len(self._trades))
        except Exception as exc:
            logger.warning("Failed to load daily_portfolio.json: %s", exc)
            self._trades = []

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(_DAILY_PORTFOLIO_FILE), exist_ok=True)
            with open(_DAILY_PORTFOLIO_FILE, "w") as f:
                json.dump({"trades": [asdict(t) for t in self._trades]}, f, indent=2, default=str)
        except Exception as exc:
            logger.error("Failed to save daily_portfolio.json: %s", exc)

    # ------------------------------------------------------------------ Trades

    @property
    def trades(self) -> list[IntradayTrade]:
        with self._lock:
            return list(self._trades)

    def open_symbols(self) -> set[str]:
        with self._lock:
            return {t.symbol for t in self._trades if t.status == "OPEN"}

    def get_open_trade(self, symbol: str) -> Optional[IntradayTrade]:
        with self._lock:
            for t in reversed(self._trades):
                if t.symbol == symbol and t.status == "OPEN":
                    return t
            return None

    def open_trade(
        self,
        symbol: str,
        side: str,
        shares: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        strategy: str,
        order_id: Optional[str] = None,
    ) -> IntradayTrade:
        trade = IntradayTrade(
            symbol=symbol,
            side=side,
            shares=shares,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=strategy,
            order_id=order_id,
        )
        with self._lock:
            self._trades.append(trade)
            self._save()
        logger.info(
            "Opened intraday trade: %s %s x%d @ %.2f [%s]",
            side.upper(), symbol, shares, entry_price, strategy,
        )
        return trade

    def close_trade(
        self,
        symbol: str,
        exit_price: float,
        status: str = "CLOSED",
    ) -> Optional[IntradayTrade]:
        with self._lock:
            for trade in reversed(self._trades):
                if trade.symbol == symbol and trade.status == "OPEN":
                    trade.status = status
                    trade.exit_time = datetime.utcnow().isoformat()
                    trade.exit_price = exit_price
                    if trade.side == "buy":
                        trade.pnl = (exit_price - trade.entry_price) * trade.shares
                        trade.pnl_pct = (exit_price / trade.entry_price) - 1.0
                    else:
                        trade.pnl = (trade.entry_price - exit_price) * trade.shares
                        trade.pnl_pct = (trade.entry_price / exit_price) - 1.0
                    self._save()
                    logger.info(
                        "Closed intraday trade: %s @ %.2f P&L=$%.2f (%.2f%%) [%s]",
                        symbol, exit_price, trade.pnl, trade.pnl_pct * 100, status,
                    )
                    return trade
        return None

    # ------------------------------------------------------------------ Summary

    def today_summary(self) -> dict:
        from datetime import date
        today = date.today().isoformat()
        today_trades = [
            t for t in self._trades
            if t.entry_time and t.entry_time[:10] == today
        ]
        closed = [t for t in today_trades if t.status != "OPEN"]
        open_trades = [t for t in today_trades if t.status == "OPEN"]
        total_pnl = sum(t.pnl for t in closed)
        wins = [t for t in closed if t.pnl > 0]
        return {
            "date": today,
            "total_trades": len(closed),
            "open_trades": len(open_trades),
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": len(wins) / len(closed) if closed else 0.0,
            "total_pnl": round(total_pnl, 2),
        }

    def all_trades_dict(self) -> list[dict]:
        with self._lock:
            return [asdict(t) for t in reversed(self._trades)]
