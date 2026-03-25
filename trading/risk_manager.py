"""
Risk management: position sizing, stop-loss/take-profit calculation,
daily loss limits, drawdown protection.
"""
from __future__ import annotations

from utils.logger import get_logger
from config import Config

logger = get_logger(__name__)


class RiskManager:
    """
    Enforces risk rules before any trade is executed.

    Conservative compounding approach:
    - Fixed fractional position sizing (% of current equity)
    - ATR-based stop-loss placement
    - 3:1 reward-to-risk ratio for take-profit
    - Daily loss circuit-breaker
    - Max concurrent position limit
    """

    def __init__(self) -> None:
        self._daily_start_equity: float = 0.0
        self._trades_today: int = 0

    def set_day_start_equity(self, equity: float) -> None:
        """Call once at market open with current equity."""
        self._daily_start_equity = equity
        self._trades_today = 0
        logger.info(f"Day start equity: ${equity:,.2f}")

    def daily_loss_exceeded(self, current_equity: float) -> bool:
        """Return True if the daily drawdown limit has been breached."""
        if self._daily_start_equity <= 0:
            return False
        loss_pct = (self._daily_start_equity - current_equity) / self._daily_start_equity
        if loss_pct >= Config.MAX_DAILY_LOSS_PCT:
            logger.warning(
                f"Daily loss limit hit: {loss_pct:.1%} >= {Config.MAX_DAILY_LOSS_PCT:.1%}. "
                "Halting trading for today."
            )
            return True
        return False

    def max_positions_reached(self, open_count: int) -> bool:
        """Return True if the max open position limit is reached."""
        if open_count >= Config.MAX_OPEN_POSITIONS:
            logger.debug(f"Max positions reached ({open_count}/{Config.MAX_OPEN_POSITIONS})")
            return True
        return False

    def calculate_position_size(
        self,
        equity: float,
        price: float,
        atr: float,
    ) -> tuple[int, float, float]:
        """
        Calculate position size using volatility-adjusted fixed-fraction sizing.

        Uses 1R = 1 ATR for stop-loss distance. Position size is capped
        at MAX_POSITION_PCT of equity regardless.

        Returns (shares, stop_loss_price, take_profit_price).
        """
        # Risk amount per trade: fixed % of equity
        risk_amount = equity * Config.STOP_LOSS_PCT

        # ATR-based stop distance (1 ATR below entry)
        stop_distance = max(atr, price * Config.STOP_LOSS_PCT)

        # Shares: risk_amount / stop_distance
        shares_by_risk = risk_amount / stop_distance

        # Cap at max position % of equity
        max_shares_by_pct = (equity * Config.MAX_POSITION_PCT) / price

        shares = int(min(shares_by_risk, max_shares_by_pct))
        shares = max(shares, 1)  # always trade at least 1 share

        # Ensure stop_distance is sane (at least 0.1% of price)
        stop_distance = max(stop_distance, price * 0.001)
        stop_loss = price - stop_distance
        take_profit = price + (stop_distance * 3)  # 3:1 R:R

        # Validate
        if stop_loss <= 0 or stop_loss >= price or take_profit <= price:
            logger.warning(f"Invalid SL/TP: price={price} SL={stop_loss} TP={take_profit}")
            stop_loss = price * 0.98
            take_profit = price * 1.06

        logger.debug(
            f"Sizing: equity={equity:.0f} price={price:.2f} atr={atr:.2f} "
            f"shares={shares} SL={stop_loss:.2f} TP={take_profit:.2f}"
        )
        return shares, stop_loss, take_profit

    def record_trade(self) -> None:
        self._trades_today += 1

    @property
    def trades_today(self) -> int:
        return self._trades_today
