"""
Intraday risk management.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from config import Config
from utils.logger import get_logger

logger = get_logger("intraday.risk")

_ET = timezone(timedelta(hours=-4))


class IntradayRiskManager:
    def __init__(self) -> None:
        self._session_start_equity: float = 0.0
        self._initialized: bool = False

    def initialize(self, equity: float) -> None:
        if not self._initialized:
            self._session_start_equity = equity
            self._initialized = True
            logger.info("Intraday session start equity: $%.2f", equity)

    def reset(self) -> None:
        self._initialized = False
        self._session_start_equity = 0.0

    def is_eod_close_time(self) -> bool:
        """Return True if it is time to close all positions (default 3:45 PM ET)."""
        now_et = datetime.now(tz=_ET)
        close_hour = Config.INTRADAY_CLOSE_BY_HOUR
        close_minute = Config.INTRADAY_CLOSE_BY_MINUTE
        return (now_et.hour > close_hour) or (
            now_et.hour == close_hour and now_et.minute >= close_minute
        )

    def is_market_hours(self) -> bool:
        """Return True during regular trading hours (9:30 AM – 4:00 PM ET)."""
        now_et = datetime.now(tz=_ET)
        after_open = (now_et.hour > 9) or (now_et.hour == 9 and now_et.minute >= 30)
        before_close = now_et.hour < 16
        return after_open and before_close

    def daily_loss_exceeded(self, current_equity: float) -> bool:
        """Return True if daily loss limit has been hit."""
        if not self._initialized or self._session_start_equity <= 0:
            return False
        daily_pnl = current_equity - self._session_start_equity
        pnl_pct = daily_pnl / self._session_start_equity
        if pnl_pct < -Config.INTRADAY_MAX_DAILY_LOSS_PCT:
            logger.warning(
                "Daily loss limit hit: P&L=%.2f%% threshold=%.2f%%",
                pnl_pct * 100,
                Config.INTRADAY_MAX_DAILY_LOSS_PCT * 100,
            )
            return True
        return False

    def position_size(self, equity: float, entry_price: float) -> int:
        """
        Calculate number of shares to buy given equity and entry price.
        Respects INTRADAY_POSITION_PCT and INTRADAY_MAX_POSITIONS.
        Returns 0 if entry_price is invalid.
        """
        if entry_price <= 0 or equity <= 0:
            return 0
        capital_per_trade = equity * Config.INTRADAY_POSITION_PCT
        shares = int(capital_per_trade / entry_price)
        return max(1, shares) if shares >= 1 else 0

    def can_open_position(self, open_count: int, current_equity: float) -> bool:
        """Return True if a new position can be opened."""
        if open_count >= Config.INTRADAY_MAX_POSITIONS:
            logger.debug(
                "Position cap reached (%d/%d)", open_count, Config.INTRADAY_MAX_POSITIONS
            )
            return False
        if self.daily_loss_exceeded(current_equity):
            return False
        if self.is_eod_close_time():
            logger.debug("EOD — no new positions")
            return False
        return True
