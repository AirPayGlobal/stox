"""
Staged partial-exit manager for long positions.

Exit tiers (applied to the *original* share count):
  Tier 1    — sell 33% at +8% gain   (fires once)
  Tier 2    — sell 33% at +15% gain  (fires once)
  Trail     — remaining 34% trailed with 7% stop from high-water mark
  Break-even— once HWM > entry * 1.03, floor trailing 34% at entry price
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

_LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "logs", "partial_exits.json"
)

# ---------------------------------------------------------------------------
# Tier configuration
# ---------------------------------------------------------------------------
_TIER1_GAIN_PCT   = 0.08    # +8%
_TIER2_GAIN_PCT   = 0.15    # +15%
_TIER1_SELL_FRAC  = 0.33    # 33% of original position
_TIER2_SELL_FRAC  = 0.33    # another 33%
_TRAIL_FRAC       = 0.34    # remaining 34%
_TRAIL_STOP_PCT   = 0.07    # 7% trailing stop on the tail
_BE_TRIGGER_PCT   = 0.03    # HWM must be > entry * 1.03 before floor activates


@dataclass
class PartialExitAction:
    symbol: str
    shares_to_sell: int   # exact number of shares to sell
    reason: str           # "PARTIAL_1" | "PARTIAL_2" | "TRAIL_STOP" | "BREAK_EVEN"
    trigger_pct: float    # gain % that triggered this action
    estimated_price: float


class PartialExitManager:
    """
    Manages staged partial exits for open long positions.

    State per symbol stored in ``_state`` dict:
        {
            symbol: {
                "tier1_fired":      bool,
                "tier2_fired":      bool,
                "original_shares":  int,
                "realised_pnl":     float,
            }
        }

    Usage inside main.py's ``_check_exits``:

        actions = partial_exit_manager.check_exits(
            symbol, trade, current_price, hwm
        )
        for action in actions:
            oid = close_position_partial(action.symbol, action.shares_to_sell)
            if oid:
                partial_exit_manager.record_partial(
                    action.symbol,
                    action.shares_to_sell,
                    action.estimated_price,
                    action.trigger_pct,
                )
    """

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def _ensure(self, symbol: str, original_shares: int) -> dict:
        """Return (and lazily initialise) the per-symbol state bucket."""
        if symbol not in self._state:
            self._state[symbol] = {
                "tier1_fired":     False,
                "tier2_fired":     False,
                "original_shares": original_shares,
                "realised_pnl":    0.0,
            }
        elif self._state[symbol]["original_shares"] == 0:
            self._state[symbol]["original_shares"] = original_shares
        return self._state[symbol]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def check_exits(
        self,
        symbol: str,
        trade,
        current_price: float,
        high_water_mark: float,
    ) -> list[PartialExitAction]:
        """
        Evaluate partial-exit tiers for one symbol.

        Parameters
        ----------
        symbol          : ticker
        trade           : Trade dataclass from portfolio.py (needs .entry_price, .shares)
        current_price   : latest market price
        high_water_mark : highest observed price since entry (caller maintains this)

        Returns a list of PartialExitAction objects (may be empty).
        Only one action is returned per call to avoid double-firing.
        """
        if trade is None or current_price <= 0 or trade.entry_price <= 0:
            return []

        entry          = trade.entry_price
        original_shares = trade.shares
        state          = self._ensure(symbol, original_shares)

        actions: list[PartialExitAction] = []
        gain_pct = (current_price / entry) - 1.0

        # ---- Tier 1: +8% ------------------------------------------------
        if not state["tier1_fired"] and gain_pct >= _TIER1_GAIN_PCT:
            shares_to_sell = max(1, math.floor(original_shares * _TIER1_SELL_FRAC))
            actions.append(
                PartialExitAction(
                    symbol=symbol,
                    shares_to_sell=shares_to_sell,
                    reason="PARTIAL_1",
                    trigger_pct=gain_pct,
                    estimated_price=current_price,
                )
            )
            return actions   # one action per call; caller records and re-enters

        # ---- Tier 2: +15% -----------------------------------------------
        if (
            state["tier1_fired"]
            and not state["tier2_fired"]
            and gain_pct >= _TIER2_GAIN_PCT
        ):
            shares_to_sell = max(1, math.floor(original_shares * _TIER2_SELL_FRAC))
            actions.append(
                PartialExitAction(
                    symbol=symbol,
                    shares_to_sell=shares_to_sell,
                    reason="PARTIAL_2",
                    trigger_pct=gain_pct,
                    estimated_price=current_price,
                )
            )
            return actions

        # ---- Trailing 34% tail ------------------------------------------
        # Only evaluated once both tiers have fired (or if we skip straight here
        # because shares are small enough that Tier 1/2 consumed everything).
        if state["tier1_fired"] and state["tier2_fired"]:
            remaining = self.get_remaining_shares(symbol, original_shares)
            if remaining <= 0:
                return []

            hwm = high_water_mark if high_water_mark >= entry else entry

            # Break-even floor: once HWM crossed entry * 1.03, never close below entry
            if hwm >= entry * (1 + _BE_TRIGGER_PCT) and current_price <= entry:
                actions.append(
                    PartialExitAction(
                        symbol=symbol,
                        shares_to_sell=remaining,
                        reason="BREAK_EVEN",
                        trigger_pct=gain_pct,
                        estimated_price=current_price,
                    )
                )
                return actions

            # 7% trailing stop from HWM
            trail_floor = hwm * (1 - _TRAIL_STOP_PCT)
            if current_price < trail_floor:
                actions.append(
                    PartialExitAction(
                        symbol=symbol,
                        shares_to_sell=remaining,
                        reason="TRAIL_STOP",
                        trigger_pct=gain_pct,
                        estimated_price=current_price,
                    )
                )
                return actions

        return actions

    def record_partial(
        self,
        symbol: str,
        shares_sold: int,
        price: float,
        pct_gain: float,
    ) -> None:
        """
        Update in-memory state and append to the JSON log after a partial fill.
        Must be called by the caller immediately after the broker confirms the sell.
        """
        state = self._state.get(symbol)
        if state is None:
            logger.warning(f"record_partial called for unknown symbol {symbol}")
            return

        entry = 0.0
        # Derive entry from original shares and current realised PnL is unavailable here;
        # the caller passes pct_gain so we can back-calculate.
        # pnl = shares_sold * price * pct_gain / (1 + pct_gain)  — approximation
        if pct_gain > -1:
            entry_approx = price / (1 + pct_gain)
            realised = (price - entry_approx) * shares_sold
        else:
            realised = 0.0

        state["realised_pnl"] += realised

        # Mark tier flags
        original = state["original_shares"]
        sold_so_far = original - self.get_remaining_shares(symbol, original)
        # After recording *this* sale, the total sold will be sold_so_far + shares_sold
        total_sold_after = sold_so_far + shares_sold
        tier1_threshold = math.floor(original * _TIER1_SELL_FRAC)
        tier2_threshold = math.floor(original * (_TIER1_SELL_FRAC + _TIER2_SELL_FRAC))

        if not state["tier1_fired"] and total_sold_after >= tier1_threshold:
            state["tier1_fired"] = True
            logger.info(f"{symbol}: Tier-1 partial exit recorded ({shares_sold} shares @ ${price:.2f})")
        elif state["tier1_fired"] and not state["tier2_fired"] and total_sold_after >= tier2_threshold:
            state["tier2_fired"] = True
            logger.info(f"{symbol}: Tier-2 partial exit recorded ({shares_sold} shares @ ${price:.2f})")
        else:
            logger.info(f"{symbol}: Tail partial exit recorded ({shares_sold} shares @ ${price:.2f})")

        # Persist to log
        remaining_after = max(0, original - total_sold_after)
        self._append_log(
            symbol=symbol,
            tier="PARTIAL_1" if not state["tier2_fired"] else "PARTIAL_2",
            shares=shares_sold,
            price=price,
            realised_pnl=state["realised_pnl"],
            unrealised_shares_remaining=remaining_after,
        )

    def _append_log(
        self,
        symbol: str,
        tier: str,
        shares: int,
        price: float,
        realised_pnl: float,
        unrealised_shares_remaining: int,
    ) -> None:
        """Append one record to logs/partial_exits.json (newline-delimited JSON)."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "tier": tier,
            "shares": shares,
            "price": price,
            "realised_pnl": round(realised_pnl, 4),
            "unrealised_shares_remaining": unrealised_shares_remaining,
        }
        try:
            with open(_LOG_FILE, "a") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.error(f"Could not write partial_exits.json: {exc}")

    def get_remaining_shares(self, symbol: str, original_shares: int) -> int:
        """
        Return how many shares of the *original* position are still held
        after accounting for all recorded partial exits.

        This is computed from the log file so it survives restarts.
        """
        sold = self._sold_from_log(symbol)
        return max(0, original_shares - sold)

    def _sold_from_log(self, symbol: str) -> int:
        """Sum shares sold for this symbol from the on-disk log."""
        total = 0
        if not os.path.exists(_LOG_FILE):
            return total
        try:
            with open(_LOG_FILE) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if rec.get("symbol") == symbol:
                            total += int(rec.get("shares", 0))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.error(f"Could not read partial_exits.json: {exc}")
        return total

    def get_realised_pnl(self, symbol: str) -> float:
        """Return total realised P&L for this symbol from in-memory state."""
        state = self._state.get(symbol)
        if state is None:
            return 0.0
        return state["realised_pnl"]

    def get_unrealised_pnl(
        self,
        symbol: str,
        current_price: float,
        entry_price: float,
    ) -> float:
        """Return unrealised P&L for the *remaining* shares of this symbol."""
        state = self._state.get(symbol)
        if state is None or entry_price <= 0:
            return 0.0
        remaining = self.get_remaining_shares(symbol, state["original_shares"])
        return (current_price - entry_price) * remaining

    def reset(self, symbol: str) -> None:
        """Remove all in-memory state for a symbol after a full exit."""
        if symbol in self._state:
            del self._state[symbol]
            logger.info(f"PartialExitManager: reset state for {symbol}")
