"""
Position book: tracks the engine's open trades (with entry time, stop and
target levels) and the closed-trade history. Persisted to JSON so a restart
mid-session doesn't orphan open positions.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Config
from utils.logger import get_logger

logger = get_logger("positions")

ET = ZoneInfo("America/New_York")


@dataclass
class Trade:
    symbol: str            # OCC option symbol
    underlying: str
    direction: str         # "LONG" (calls) | "SHORT" (puts)
    qty: int
    entry_premium: float   # per share (x100 per contract)
    stop_premium: float
    target_premium: float
    opened_at: str         # ISO, ET
    order_id: str = ""
    status: str = "OPEN"   # OPEN | TP | SL | UL_TP | UL_SL | TIME | FLATTEN | SIGNAL | HALT
    exit_premium: float = 0.0
    closed_at: str = ""
    pnl: float = 0.0
    strategy: str = "orb"        # "orb" | "sweep"
    stop_underlying: float = 0.0    # 0 = premium-based exits only
    target_underlying: float = 0.0

    def unrealized(self, mark: float) -> float:
        return (mark - self.entry_premium) * 100 * self.qty

    def minutes_open(self) -> float:
        opened = datetime.fromisoformat(self.opened_at)
        return (datetime.now(ET) - opened).total_seconds() / 60


@dataclass
class Book:
    open_trades: list = field(default_factory=list)
    closed_trades: list = field(default_factory=list)


class PositionBook:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(Config.STATE_DIR, "trades.json")
        self.book = Book()
        self._load()

    # ------------------------------------------------------------ Mutations
    def open(
        self,
        symbol: str,
        underlying: str,
        direction: str,
        qty: int,
        entry_premium: float,
        order_id: str = "",
        strategy: str = "orb",
        stop_underlying: float = 0.0,
        target_underlying: float = 0.0,
    ) -> Trade:
        if strategy == "sweep":
            # Exits are driven by underlying levels; the premium stop is only
            # a wide disaster backstop and there is no premium target.
            stop_premium = round(entry_premium * (1 - Config.SWEEP_DISASTER_STOP_PCT), 2)
            target_premium = round(entry_premium * 1000, 2)
        else:
            stop_premium = round(entry_premium * (1 - Config.STOP_LOSS_PCT), 2)
            target_premium = round(entry_premium * (1 + Config.TAKE_PROFIT_PCT), 2)
        trade = Trade(
            symbol=symbol,
            underlying=underlying,
            direction=direction,
            qty=qty,
            entry_premium=entry_premium,
            stop_premium=stop_premium,
            target_premium=target_premium,
            opened_at=datetime.now(ET).isoformat(),
            order_id=order_id,
            strategy=strategy,
            stop_underlying=round(stop_underlying, 2),
            target_underlying=round(target_underlying, 2),
        )
        self.book.open_trades.append(trade)
        self._save()
        logger.info(
            f"Opened {direction} {qty}x {symbol} @ ${entry_premium:.2f} "
            f"(stop ${trade.stop_premium:.2f} / target ${trade.target_premium:.2f})"
        )
        return trade

    def close(self, symbol: str, exit_premium: float, status: str) -> Trade | None:
        for trade in list(self.book.open_trades):
            if trade.symbol == symbol:
                trade.exit_premium = exit_premium
                trade.closed_at = datetime.now(ET).isoformat()
                trade.status = status
                trade.pnl = round((exit_premium - trade.entry_premium) * 100 * trade.qty, 2)
                self.book.open_trades.remove(trade)
                self.book.closed_trades.append(trade)
                self._save()
                logger.info(f"Closed {symbol} @ ${exit_premium:.2f} [{status}] P&L ${trade.pnl:+,.2f}")
                return trade
        return None

    # ------------------------------------------------------------ Queries
    @property
    def open_trades(self) -> list[Trade]:
        return self.book.open_trades

    def open_for(self, underlying: str) -> list[Trade]:
        return [t for t in self.book.open_trades if t.underlying == underlying]

    def closed_today(self) -> list[Trade]:
        today = datetime.now(ET).date().isoformat()
        return [t for t in self.book.closed_trades if t.closed_at[:10] == today]

    def consecutive_losses(self, underlying: str) -> int:
        """Length of the current losing streak on `underlying` today."""
        streak = 0
        for trade in reversed(self.closed_today()):
            if trade.underlying != underlying:
                continue
            if trade.pnl < 0:
                streak += 1
            else:
                break
        return streak

    def last_close_time(self, underlying: str) -> tuple[datetime, float] | None:
        """(close time, pnl) of the most recent closed trade on `underlying`
        today, or None."""
        for trade in reversed(self.closed_today()):
            if trade.underlying == underlying:
                return datetime.fromisoformat(trade.closed_at), trade.pnl
        return None

    def last_loss_time(self, underlying: str) -> datetime | None:
        """Close time of the most recent trade on `underlying` today, if it
        was a loss (a winning close resets the cooldown)."""
        for trade in reversed(self.closed_today()):
            if trade.underlying != underlying:
                continue
            if trade.pnl < 0:
                return datetime.fromisoformat(trade.closed_at)
            return None
        return None

    def realized_today(self) -> float:
        return sum(t.pnl for t in self.closed_today())

    def summary(self) -> dict:
        closed = self.closed_today()
        wins = [t for t in closed if t.pnl > 0]
        return {
            "open_positions": len(self.book.open_trades),
            "closed_today": len(closed),
            "wins_today": len(wins),
            "win_rate_today": round(len(wins) / len(closed), 2) if closed else 0.0,
            "realized_today": round(self.realized_today(), 2),
        }

    # ------------------------------------------------------------ Persistence
    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(
                    {
                        "open_trades": [asdict(t) for t in self.book.open_trades],
                        "closed_trades": [asdict(t) for t in self.book.closed_trades[-500:]],
                    },
                    f,
                    indent=2,
                )
        except OSError as exc:
            logger.warning(f"Could not persist position book: {exc}")

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                raw = json.load(f)
            self.book.open_trades = [Trade(**t) for t in raw.get("open_trades", [])]
            self.book.closed_trades = [Trade(**t) for t in raw.get("closed_trades", [])]
            if self.book.open_trades:
                logger.info(f"Restored {len(self.book.open_trades)} open trade(s) from disk")
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f"Could not load position book: {exc}")
