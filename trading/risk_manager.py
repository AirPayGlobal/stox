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

    Position sizing uses Kelly Criterion (half-Kelly) when enough trade
    history exists (≥ KELLY_MIN_TRADES). Falls back to fixed-fraction
    sizing until sufficient history is available.
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

    def has_buying_power(self, available: float, equity: float) -> bool:
        """Return True if there is enough buying power to open at least one more position."""
        min_required = equity * Config.MIN_POSITION_PCT
        if available < min_required:
            logger.info(
                f"Insufficient buying power for new entries: "
                f"${available:,.0f} available, need ≥ ${min_required:,.0f} "
                f"({Config.MIN_POSITION_PCT:.1%} of equity)"
            )
            return False
        return True

    # -------------------------------------------------------------- Kelly sizing

    def _kelly_fraction(
        self,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
    ) -> float:
        """
        Compute the half-Kelly optimal position fraction.

        Full Kelly: f = (W*R - L) / R
          where W = win rate, L = loss rate, R = avg_win / avg_loss

        Half-Kelly is used for safety — same expected return as full Kelly
        but ~half the variance.

        Returns a fraction in [KELLY_MIN_FRACTION, MAX_POSITION_PCT].
        """
        if avg_loss_pct <= 0 or avg_win_pct <= 0 or win_rate <= 0:
            return Config.MAX_POSITION_PCT

        R = avg_win_pct / avg_loss_pct
        W = win_rate
        L = 1.0 - win_rate

        full_kelly = (W * R - L) / R
        half_kelly = full_kelly / 2.0

        # Clamp to safe range
        fraction = max(Config.KELLY_MIN_FRACTION, min(Config.MAX_POSITION_PCT, half_kelly))
        return fraction

    def calculate_position_size(
        self,
        equity: float,
        price: float,
        atr: float,
        win_rate: float = 0.0,
        avg_win_pct: float = 0.0,
        avg_loss_pct: float = 0.0,
        trade_count: int = 0,
    ) -> tuple[int, float, float]:
        """
        Calculate position size.

        When trade_count >= KELLY_MIN_TRADES, uses half-Kelly to set the
        max position fraction. Falls back to fixed MAX_POSITION_PCT otherwise.

        Returns (shares, stop_loss_price, take_profit_price).
        """
        # Decide which max-fraction to use
        if trade_count >= Config.KELLY_MIN_TRADES and win_rate > 0:
            kelly_f = self._kelly_fraction(win_rate, avg_win_pct, avg_loss_pct)
            logger.info(
                f"Kelly sizing: win_rate={win_rate:.1%} "
                f"avg_win={avg_win_pct:.1%} avg_loss={avg_loss_pct:.1%} "
                f"→ half-Kelly={kelly_f:.1%}"
            )
            max_position_pct = kelly_f
        else:
            max_position_pct = Config.MAX_POSITION_PCT
            if trade_count < Config.KELLY_MIN_TRADES:
                logger.debug(
                    f"Kelly inactive: {trade_count}/{Config.KELLY_MIN_TRADES} trades "
                    f"— using fixed {max_position_pct:.1%}"
                )

        # Risk amount per trade (fixed % of equity)
        risk_amount = equity * Config.STOP_LOSS_PCT

        # ATR-based stop distance (1 ATR below entry)
        stop_distance = max(atr, price * Config.STOP_LOSS_PCT)

        # Shares: risk_amount / stop_distance
        shares_by_risk = risk_amount / stop_distance

        # Cap at Kelly/fixed max position % of equity
        max_shares_by_pct = (equity * max_position_pct) / price

        shares = int(min(shares_by_risk, max_shares_by_pct))
        shares = max(shares, 1)

        # Sane stop distance floor
        stop_distance = max(stop_distance, price * 0.001)
        stop_loss = price - stop_distance
        take_profit = price + (stop_distance * 3)  # 3:1 R:R

        if stop_loss <= 0 or stop_loss >= price or take_profit <= price:
            logger.warning(f"Invalid SL/TP: price={price} SL={stop_loss} TP={take_profit}")
            stop_loss = price * 0.98
            take_profit = price * 1.06

        logger.debug(
            f"Sizing: equity={equity:.0f} price={price:.2f} atr={atr:.2f} "
            f"max_pct={max_position_pct:.1%} shares={shares} "
            f"SL={stop_loss:.2f} TP={take_profit:.2f}"
        )
        return shares, stop_loss, take_profit

    def volatility_target_multiplier(self, df: "pd.DataFrame") -> float:
        """
        Returns a multiplier in (0, 1] that scales a Kelly-sized position
        down when the stock's realised vol exceeds the per-position vol budget.

        Formula:
            realized_vol  = annualised std of last 20 daily returns
            target_vol    = Config.VOL_TARGET_PER_POSITION (default 1%)
            multiplier    = min(1.0, target_vol / realized_vol)

        A multiplier of 1.0 means no scaling (vol is at or below target).
        A multiplier of 0.5 means the position is halved (vol is 2× target).
        """
        import pandas as pd
        import numpy as np
        try:
            if df is None or len(df) < 21:
                return 1.0
            returns = df["close"].pct_change().dropna().tail(20)
            if len(returns) < 10:
                return 1.0
            realized_vol = float(returns.std() * np.sqrt(252))
            if realized_vol <= 0:
                return 1.0
            multiplier = min(1.0, Config.VOL_TARGET_PER_POSITION / realized_vol)
            logger.debug(
                f"Vol target: realized={realized_vol:.1%} "
                f"target={Config.VOL_TARGET_PER_POSITION:.1%} "
                f"→ multiplier={multiplier:.2f}"
            )
            return multiplier
        except Exception:
            return 1.0

    def record_trade(self) -> None:
        self._trades_today += 1

    @property
    def trades_today(self) -> int:
        return self._trades_today

