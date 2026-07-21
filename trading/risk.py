"""
Risk management: position sizing and the daily governor.

The governor operates on the ENGINE's own day P&L — realized P&L from the
engine's closed trades plus unrealized P&L on the option positions it
manages — NOT total account equity. Total equity can be contaminated by
positions the engine never opened (leftover shares, corporate-action
artifacts), whose fluctuation would otherwise false-trigger the target or
the loss halt.

  * reaching DAILY_PROFIT_TARGET arms a trailing profit floor (trading
    continues; falling back to the floor banks the day)
  * day P&L <= -DAILY_MAX_LOSS is a hard halt (flatten + stop)
  * hard caps on trades/day and concurrent positions

Sizing uses the engine's tradable equity (account equity minus the value
of unmanaged positions) so junk in the account can't inflate position size.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import Config
from utils.logger import get_logger

ET = ZoneInfo("America/New_York")

logger = get_logger("risk")


@dataclass
class DayState:
    day: date = field(default_factory=date.today)
    trades_opened: int = 0
    target_hit: bool = False       # armed profit protection (trading continues)
    peak_pnl: float = 0.0
    protect_locked: bool = False   # giveback floor hit -> day banked
    loss_halted: bool = False


class RiskManager:
    """Day state is persisted so a restart mid-session keeps the same trade
    count, peak P&L, and governor locks."""

    def __init__(self) -> None:
        self.state = DayState()
        # Drawdown-breaker baseline: trades closed before this ISO timestamp
        # are excluded from the rolling drawdown. Set by reset() so a
        # deliberate strategy switch starts the equity curve fresh. Survives
        # day rollover (it is multi-day, not a daily value).
        self.dd_reset_at: str | None = None
        self._path = os.path.join(Config.STATE_DIR, "day_state.json")
        self._load()

    # ------------------------------------------------------------ Day lifecycle
    def start_day(self) -> None:
        self.state = DayState()
        logger.info(
            f"Day started | target=+${Config.DAILY_PROFIT_TARGET:,.0f} "
            f"max_loss=-${Config.DAILY_MAX_LOSS:,.0f}"
        )
        self._save()

    def ensure_today(self) -> None:
        if self.state.day != date.today():
            self.start_day()

    def reset(self) -> None:
        """Clear the day's governor state AND rebaseline the drawdown breaker
        from now (releases a stale daily halt and a drawdown halt inherited
        from a prior strategy/regime)."""
        self.state = DayState()
        self.dd_reset_at = datetime.now(ET).isoformat()
        self._save()
        logger.info("Day governor reset + drawdown baseline rebaselined")

    # ------------------------------------------------------------ Governor
    def profit_floor(self) -> float:
        """Trailing floor under day P&L once the target has been hit."""
        return max(
            Config.DAILY_PROFIT_TARGET * Config.PROFIT_FLOOR_PCT,
            self.state.peak_pnl * (1 - Config.PROFIT_GIVEBACK_PCT),
        )

    def update_governor(self, pnl: float) -> None:
        """Re-evaluate protection/halt state from the engine's day P&L.
        Locks are sticky for the day."""
        s = self.state

        if pnl > s.peak_pnl:
            s.peak_pnl = pnl
            self._save()
        if not s.target_hit and pnl >= Config.DAILY_PROFIT_TARGET:
            s.target_hit = True
            logger.info(
                f"🎯 DAILY TARGET REACHED: +${pnl:,.2f} — trading continues, "
                f"profit protection armed (floor +${self.profit_floor():,.0f})"
            )
            self._save()
        if s.target_hit and not s.protect_locked and pnl <= self.profit_floor():
            s.protect_locked = True
            action = (
                "banking the day (flattening)"
                if Config.PROTECT_MODE == "flatten"
                else "no new trades; open positions run to their own exits"
            )
            logger.info(
                f"🔒 PROFIT PROTECTION: day P&L ${pnl:,.2f} fell to the floor "
                f"(+${self.profit_floor():,.0f}) — {action}"
            )
            self._save()
        if not s.loss_halted and pnl <= -Config.DAILY_MAX_LOSS:
            s.loss_halted = True
            logger.warning(f"🛑 DAILY MAX LOSS HIT: ${pnl:,.2f} — halting for the day")
            self._save()

    def can_open(self, pnl: float, open_positions: int) -> tuple[bool, str]:
        self.update_governor(pnl)
        s = self.state
        if s.loss_halted:
            return False, "daily max loss reached"
        if s.protect_locked:
            return False, "profit protection banked the day"
        if s.trades_opened >= Config.MAX_TRADES_PER_DAY:
            return False, "max trades per day reached"
        if open_positions >= Config.MAX_CONCURRENT_POSITIONS:
            return False, "max concurrent positions reached"
        return True, ""

    def must_flatten(self) -> bool:
        if self.state.loss_halted:
            return True
        # In "hold" mode the floor only blocks new entries; open positions
        # keep running to their own stops/targets.
        return self.state.protect_locked and Config.PROTECT_MODE == "flatten"

    def flatten_reason(self) -> str:
        if self.state.loss_halted:
            return "HALT"
        if self.state.protect_locked and Config.PROTECT_MODE == "flatten":
            return "PROTECT"
        return "FLATTEN"

    def record_open(self) -> None:
        self.state.trades_opened += 1
        self._save()

    # ------------------------------------------------------------ Persistence
    def _save(self) -> None:
        try:
            os.makedirs(Config.STATE_DIR, exist_ok=True)
            data = asdict(self.state)
            data["day"] = self.state.day.isoformat()
            data["dd_reset_at"] = self.dd_reset_at  # multi-day, survives rollover
            with open(self._path, "w") as f:
                json.dump(data, f)
        except OSError as exc:
            logger.warning(f"Could not persist day state: {exc}")

    def _load(self) -> None:
        try:
            with open(self._path) as f:
                raw = json.load(f)
            # dd_reset_at is multi-day: load it regardless of the day rollover.
            self.dd_reset_at = raw.get("dd_reset_at")
            if raw.get("day") == date.today().isoformat():
                self.state = DayState(
                    day=date.today(),
                    trades_opened=int(raw.get("trades_opened", 0)),
                    target_hit=bool(raw.get("target_hit", False)),
                    peak_pnl=float(raw.get("peak_pnl", 0.0)),
                    protect_locked=bool(raw.get("protect_locked", False)),
                    loss_halted=bool(raw.get("loss_halted", False)),
                )
                logger.info(f"Restored day state | trades={self.state.trades_opened}")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f"Could not load day state: {exc}")

    # ------------------------------------------------------------ Sizing
    def contracts_for(self, equity: float, premium: float) -> int:
        """
        Number of contracts such that:
          * loss at the stop (premium * STOP_LOSS_PCT) <= RISK_PER_TRADE_PCT of equity
          * total premium outlay <= MAX_POSITION_PCT of equity
          * qty <= MAX_CONTRACTS
        `equity` is the engine's tradable equity (junk positions excluded).
        """
        if premium <= 0 or equity <= 0:
            return 0
        cost_per_contract = premium * 100
        risk_per_contract = cost_per_contract * Config.STOP_LOSS_PCT

        by_risk = int((equity * Config.RISK_PER_TRADE_PCT) // risk_per_contract)
        by_outlay = int((equity * Config.MAX_POSITION_PCT) // cost_per_contract)
        return max(0, min(by_risk, by_outlay, Config.MAX_CONTRACTS))

    def contracts_for_underlying_stop(
        self, equity: float, premium: float, delta: float | None, stop_distance: float
    ) -> int:
        """
        Sizing for trades whose stop is an UNDERLYING price level: expected
        option loss at the stop ~= |delta| * stop_distance per share. When
        the feed returns no greeks, assume ATM delta (0.5) — the premium-based
        fallback ignored the stop distance and oversized wide-stop trades.
        """
        if premium <= 0 or equity <= 0 or stop_distance <= 0:
            return 0
        if delta is None:
            delta = 0.5

        risk_per_contract = min(abs(delta) * stop_distance, premium) * 100
        if risk_per_contract <= 0:
            return 0
        by_risk = int((equity * Config.RISK_PER_TRADE_PCT) // risk_per_contract)
        by_outlay = int((equity * Config.MAX_POSITION_PCT) // (premium * 100))
        return max(0, min(by_risk, by_outlay, Config.MAX_CONTRACTS))

    # ------------------------------------------------------------ Introspection
    def snapshot(self, pnl: float) -> dict:
        s = self.state
        return {
            "day": s.day.isoformat(),
            "day_pnl": round(pnl, 2),
            "peak_pnl": round(s.peak_pnl, 2),
            "profit_target": Config.DAILY_PROFIT_TARGET,
            "max_loss": Config.DAILY_MAX_LOSS,
            "trades_opened": s.trades_opened,
            "target_hit": s.target_hit,
            "profit_floor": round(self.profit_floor(), 2) if s.target_hit else None,
            "protect_locked": s.protect_locked,
            "protect_mode": Config.PROTECT_MODE,
            "loss_halted": s.loss_halted,
        }
