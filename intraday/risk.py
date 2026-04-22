"""
APEX v4.2 intraday risk management.

Key rules:
- Hard stop:        -2% from entry
- Daily loss limit: -1.5% NAV → halt all new entries
- Max exposure:     15% NAV across all open positions
- Circuit breaker:  3 consecutive stops → halt for the day
- Entry window:     skip first 5 min (9:30-9:35 AM); no new entries after time-stop zone
- Time stop:        exit position by 12:30 PM if not up ≥0.5%
- EOD close:        hard close all positions at 3:55 PM ET
- VIX regime:       reduce sizes 40% when VIX > 28; suspend when VIX > 35
- CAS-tiered sizing: 4% NAV (CAS ≥85), 2.5% NAV (CAS 70-84)
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
        self._consecutive_stops: int = 0
        self._halted: bool = False        # circuit breaker triggered

    def initialize(self, equity: float) -> None:
        if not self._initialized:
            self._session_start_equity = equity
            self._initialized = True
            logger.info("Intraday session start equity: $%.2f", equity)

    def reset(self) -> None:
        """Reset at end of trading day."""
        self._initialized = False
        self._session_start_equity = 0.0
        self._consecutive_stops = 0
        self._halted = False

    def record_stop(self) -> None:
        """Call when a position is closed via stop loss."""
        self._consecutive_stops += 1
        if self._consecutive_stops >= Config.APEX_CONSECUTIVE_STOP_HALT:
            self._halted = True
            logger.warning(
                "Circuit breaker: %d consecutive stops — halting all new entries for the day",
                self._consecutive_stops,
            )

    def record_win(self) -> None:
        """Reset consecutive stop counter on a profitable close."""
        self._consecutive_stops = 0

    @property
    def circuit_breaker_active(self) -> bool:
        return self._halted

    # ------------------------------------------------------------------ Time checks

    def is_eod_close_time(self) -> bool:
        """True when it is time to close all positions (3:55 PM ET default)."""
        now_et = datetime.now(tz=_ET)
        h, m = Config.INTRADAY_CLOSE_BY_HOUR, Config.INTRADAY_CLOSE_BY_MINUTE
        return now_et.hour > h or (now_et.hour == h and now_et.minute >= m)

    def is_market_hours(self) -> bool:
        """True during regular trading hours (9:30 AM – 4:00 PM ET)."""
        now_et = datetime.now(tz=_ET)
        after_open = now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 30)
        before_close = now_et.hour < 16
        return after_open and before_close

    def is_entry_allowed_time(self) -> bool:
        """
        True if the clock is past the open skip window and before the time-stop cutoff.
        APEX: never buy in the first 5 min (9:30-9:35 AM); no new entries after 12:30 PM.
        """
        now_et = datetime.now(tz=_ET)
        h, m = now_et.hour, now_et.minute
        minutes_since_open = (h - 9) * 60 + m - 30
        if minutes_since_open < Config.APEX_ENTRY_SKIP_OPEN_MIN:
            return False
        # No new entries at or after time-stop hour
        ts_h, ts_m = Config.APEX_TIME_STOP_HOUR, Config.APEX_TIME_STOP_MINUTE
        if h > ts_h or (h == ts_h and m >= ts_m):
            return False
        return True

    def is_time_stop_zone(self) -> bool:
        """True at or after 12:30 PM ET — open positions that haven't moved get exited."""
        now_et = datetime.now(tz=_ET)
        h, m = now_et.hour, now_et.minute
        ts_h, ts_m = Config.APEX_TIME_STOP_HOUR, Config.APEX_TIME_STOP_MINUTE
        return h > ts_h or (h == ts_h and m >= ts_m)

    def is_dynamic_stop_time(self) -> bool:
        """True after 10:30 AM ET — use dynamic VWAP-based stop instead of hard stop only."""
        now_et = datetime.now(tz=_ET)
        return now_et.hour > 10 or (now_et.hour == 10 and now_et.minute >= 30)

    # ------------------------------------------------------------------ Loss / exposure checks

    def daily_loss_exceeded(self, current_equity: float) -> bool:
        """True if the 1.5% NAV daily loss limit has been hit."""
        if not self._initialized or self._session_start_equity <= 0:
            return False
        pnl_pct = (current_equity - self._session_start_equity) / self._session_start_equity
        if pnl_pct < -Config.INTRADAY_MAX_DAILY_LOSS_PCT:
            logger.warning(
                "Daily loss limit hit: P&L=%.2f%% threshold=%.2f%%",
                pnl_pct * 100,
                Config.INTRADAY_MAX_DAILY_LOSS_PCT * 100,
            )
            return True
        return False

    def gross_exposure_ok(self, open_position_values: list[float], equity: float) -> bool:
        """True if adding another position keeps total gross exposure ≤ 15% NAV."""
        if equity <= 0:
            return False
        total_exposure = sum(open_position_values)
        exposure_pct = total_exposure / equity
        if exposure_pct >= Config.APEX_MAX_GROSS_EXPOSURE:
            logger.debug(
                "Gross exposure cap: %.1f%% of NAV (limit %.1f%%)",
                exposure_pct * 100, Config.APEX_MAX_GROSS_EXPOSURE * 100,
            )
            return False
        return True

    # ------------------------------------------------------------------ Position sizing

    def position_size_for_cas(
        self,
        equity: float,
        entry_price: float,
        cas_score: float,
        vix_level: float = 20.0,
    ) -> int:
        """
        CAS-tiered half-Kelly position sizing with VIX regime adjustment.

        CAS ≥ 85 → 4% NAV; CAS 70-84 → 2.5% NAV; <70 → 0 (no trade).
        VIX > 35 → 0 (system suspended). VIX > 28 → 40% size reduction.
        """
        if entry_price <= 0 or equity <= 0:
            return 0
        if vix_level >= Config.APEX_VIX_SUSPEND:
            logger.warning("VIX=%.1f ≥ %.1f — system suspended", vix_level, Config.APEX_VIX_SUSPEND)
            return 0

        if cas_score >= Config.APEX_STRONG_BUY_CAS:
            size_pct = Config.APEX_STRONG_BUY_SIZE_PCT
        elif cas_score >= Config.APEX_MIN_CAS:
            size_pct = Config.APEX_BUY_SIZE_PCT
        else:
            return 0

        # VIX regime reduction
        if vix_level >= Config.APEX_VIX_REDUCE:
            size_pct *= 0.60   # 40% reduction
            logger.debug("VIX=%.1f ≥ %.1f — position size reduced 40%%", vix_level, Config.APEX_VIX_REDUCE)

        capital_per_trade = equity * size_pct
        shares = int(capital_per_trade / entry_price)
        return max(1, shares) if shares >= 1 else 0

    def position_size(self, equity: float, entry_price: float) -> int:
        """Legacy fixed-pct sizing (used as fallback when CAS score not available)."""
        if entry_price <= 0 or equity <= 0:
            return 0
        capital_per_trade = equity * Config.INTRADAY_POSITION_PCT
        shares = int(capital_per_trade / entry_price)
        return max(1, shares) if shares >= 1 else 0

    def effective_stop_pct(self, vix_level: float = 20.0) -> float:
        """Return the effective stop % for current VIX regime."""
        if vix_level >= Config.APEX_VIX_REDUCE:
            return Config.APEX_VIX_TIGHTEN_STOP   # tighter stop in high-VIX environments
        return Config.APEX_HARD_STOP_PCT

    def can_open_position(self, open_count: int, current_equity: float) -> bool:
        """Return True if a new position can be opened (count + loss + circuit breaker checks)."""
        if self._halted:
            logger.debug("Circuit breaker active — no new entries")
            return False
        if open_count >= Config.INTRADAY_MAX_POSITIONS:
            logger.debug("Position cap reached (%d/%d)", open_count, Config.INTRADAY_MAX_POSITIONS)
            return False
        if self.daily_loss_exceeded(current_equity):
            return False
        if self.is_eod_close_time():
            logger.debug("EOD — no new positions")
            return False
        if not self.is_entry_allowed_time():
            logger.debug("Outside entry time window — no new positions")
            return False
        return True
