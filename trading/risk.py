"""
Risk management: position sizing and the daily governor.

The governor is what makes this a *day*trading system rather than a slot
machine:
  * once day P&L >= DAILY_PROFIT_TARGET, no new trades — the day is done
  * once day P&L <= -DAILY_MAX_LOSS, trading halts and everything is flattened
  * hard caps on trades/day and concurrent positions
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from config import Config
from utils.logger import get_logger

logger = get_logger("risk")


@dataclass
class DayState:
    day: date = field(default_factory=date.today)
    start_equity: float = 0.0
    trades_opened: int = 0
    target_locked: bool = False
    loss_halted: bool = False


class RiskManager:
    def __init__(self) -> None:
        self.state = DayState()

    # ------------------------------------------------------------ Day lifecycle
    def start_day(self, equity: float) -> None:
        self.state = DayState(start_equity=equity)
        logger.info(
            f"Day started | equity=${equity:,.2f} "
            f"target=+${Config.DAILY_PROFIT_TARGET:,.0f} "
            f"max_loss=-${Config.DAILY_MAX_LOSS:,.0f}"
        )

    def ensure_today(self, equity: float) -> None:
        if self.state.day != date.today() or self.state.start_equity == 0:
            self.start_day(equity)

    def day_pnl(self, equity: float) -> float:
        return equity - self.state.start_equity

    # ------------------------------------------------------------ Governor
    def update_governor(self, equity: float) -> None:
        """Re-evaluate target/loss locks. Locks are sticky for the day."""
        pnl = self.day_pnl(equity)
        if not self.state.target_locked and pnl >= Config.DAILY_PROFIT_TARGET:
            self.state.target_locked = True
            logger.info(f"🎯 DAILY TARGET HIT: +${pnl:,.2f} — locking in, no new trades today")
        if not self.state.loss_halted and pnl <= -Config.DAILY_MAX_LOSS:
            self.state.loss_halted = True
            logger.warning(f"🛑 DAILY MAX LOSS HIT: ${pnl:,.2f} — halting for the day")

    def can_open(self, equity: float, open_positions: int) -> tuple[bool, str]:
        self.update_governor(equity)
        s = self.state
        if s.loss_halted:
            return False, "daily max loss reached"
        if s.target_locked:
            return False, "daily profit target reached"
        if s.trades_opened >= Config.MAX_TRADES_PER_DAY:
            return False, "max trades per day reached"
        if open_positions >= Config.MAX_CONCURRENT_POSITIONS:
            return False, "max concurrent positions reached"
        return True, ""

    def must_flatten(self) -> bool:
        return self.state.loss_halted

    def record_open(self) -> None:
        self.state.trades_opened += 1

    # ------------------------------------------------------------ Sizing
    def contracts_for(self, equity: float, premium: float) -> int:
        """
        Number of contracts such that:
          * loss at the stop (premium * STOP_LOSS_PCT) <= RISK_PER_TRADE_PCT of equity
          * total premium outlay <= MAX_POSITION_PCT of equity
          * qty <= MAX_CONTRACTS
        """
        if premium <= 0 or equity <= 0:
            return 0
        cost_per_contract = premium * 100
        risk_per_contract = cost_per_contract * Config.STOP_LOSS_PCT

        by_risk = int((equity * Config.RISK_PER_TRADE_PCT) // risk_per_contract)
        by_outlay = int((equity * Config.MAX_POSITION_PCT) // cost_per_contract)
        return max(0, min(by_risk, by_outlay, Config.MAX_CONTRACTS))

    # ------------------------------------------------------------ Introspection
    def snapshot(self, equity: float) -> dict:
        s = self.state
        return {
            "day": s.day.isoformat(),
            "start_equity": s.start_equity,
            "day_pnl": round(self.day_pnl(equity), 2),
            "profit_target": Config.DAILY_PROFIT_TARGET,
            "max_loss": Config.DAILY_MAX_LOSS,
            "trades_opened": s.trades_opened,
            "target_locked": s.target_locked,
            "loss_halted": s.loss_halted,
        }
