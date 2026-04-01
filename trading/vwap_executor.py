"""
VWAP-based order execution utilities.

Public API:
    from trading.vwap_executor import VWAPExecutor, calculate_vwap

``calculate_vwap`` computes the running VWAP over the last N bars.
``VWAPExecutor`` wraps Alpaca limit orders so entries are placed slightly
*above* VWAP (better fill probability) and exits slightly *below* VWAP
(reduces adverse slippage on the way out).

Order lifecycle
---------------
Every submitted limit order is tracked in memory.  On each bot scan the
caller must call ``increment_scan_count(order_id)`` — if an order is not
filled within ``max_candles`` scans it is automatically cancelled.

Fill statistics are persisted to logs/vwap_fill_stats.json (newline-delimited
JSON) so slippage savings can be reviewed offline.

Pairs leg synchronisation
--------------------------
``place_pair_legs`` submits both legs simultaneously.  If one fills but the
other remains open after 1 scan, the filled leg is unwound via a market
order to keep the book flat.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from trading.alpaca_client import get_trading_client
from utils.logger import get_logger

logger = get_logger(__name__)

_FILL_STATS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "logs", "vwap_fill_stats.json"
)

# Buffer boundaries (configurable but these are the hard limits used in auto-adjust)
_MIN_BUFFER_PCT = 0.0005   # 0.05% — initial default
_WIDE_BUFFER_PCT = 0.0010  # 0.10% — widened when fill rate < 60%
_FILL_RATE_THRESHOLD = 0.60


# ---------------------------------------------------------------------------
# Pure calculation helper
# ---------------------------------------------------------------------------

def calculate_vwap(df: pd.DataFrame, lookback_bars: int = 20) -> float:
    """
    Calculate VWAP over the last ``lookback_bars`` bars.

    Typical price  = (high + low + close) / 3
    VWAP           = cumsum(typical_price * volume) / cumsum(volume)

    Parameters
    ----------
    df            : DataFrame with columns ['high', 'low', 'close', 'volume']
    lookback_bars : number of most-recent bars to include

    Returns
    -------
    Current VWAP as a float.  Returns 0.0 if data is insufficient or volumes
    are all zero.

    Raises
    ------
    KeyError if required columns are missing.
    """
    required = {"high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"calculate_vwap: DataFrame missing columns {missing}")

    window = df.tail(lookback_bars).copy()
    if window.empty:
        return 0.0

    typical = (window["high"] + window["low"] + window["close"]) / 3.0
    cum_vol = window["volume"].sum()
    if cum_vol == 0:
        return 0.0

    return float((typical * window["volume"]).sum() / cum_vol)


# ---------------------------------------------------------------------------
# Executor class
# ---------------------------------------------------------------------------

class VWAPExecutor:
    """
    Place and track VWAP limit orders.

    Parameters
    ----------
    buffer_pct : fractional buffer applied around VWAP.
                 Entries go *above* VWAP; exits go *below*.
                 Defaults to 0.0005 (0.05%).
    """

    def __init__(self, buffer_pct: float = _MIN_BUFFER_PCT) -> None:
        self._buffer_pct = buffer_pct
        # {order_id: {symbol, qty, side, submitted_at, scan_count, filled,
        #             limit_price, market_close_at_submission}}
        self._orders: dict[str, dict] = {}
        os.makedirs(os.path.dirname(_FILL_STATS_FILE), exist_ok=True)

    # ------------------------------------------------------------------
    # Price helpers
    # ------------------------------------------------------------------

    def get_entry_limit_price(self, vwap: float) -> float:
        """Entry limit = VWAP + buffer (willing to pay a touch above VWAP)."""
        return round(vwap * (1 + self._buffer_pct), 2)

    def get_exit_limit_price(self, vwap: float) -> float:
        """Exit limit = VWAP - buffer (expect to receive a touch below VWAP)."""
        return round(vwap * (1 - self._buffer_pct), 2)

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_entry_order(
        self, symbol: str, qty: int, vwap: float
    ) -> Optional[str]:
        """
        Submit a day-limit BUY order at VWAP + buffer.

        Returns the order ID on success, None on failure.
        """
        limit_price = self.get_entry_limit_price(vwap)
        return self._submit_limit(symbol, qty, "buy", limit_price, vwap)

    def place_exit_order(
        self, symbol: str, qty: int, vwap: float
    ) -> Optional[str]:
        """
        Submit a day-limit SELL order at VWAP - buffer.

        Returns the order ID on success, None on failure.
        """
        limit_price = self.get_exit_limit_price(vwap)
        return self._submit_limit(symbol, qty, "sell", limit_price, vwap)

    def _submit_limit(
        self,
        symbol: str,
        qty: int,
        side: str,
        limit_price: float,
        market_close_ref: float,
    ) -> Optional[str]:
        """Internal: submit limit order and register in tracking dict."""
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        try:
            alpaca_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            request = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=alpaca_side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
            )
            order = get_trading_client().submit_order(request)
            oid = str(order.id)
            self._orders[oid] = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "scan_count": 0,
                "filled": False,
                "limit_price": limit_price,
                "market_close_at_submission": market_close_ref,
            }
            logger.info(
                f"VWAP limit {side.upper()} {qty} {symbol} @ {limit_price:.2f} "
                f"(VWAP={market_close_ref:.2f}) | order={oid}"
            )
            return oid
        except Exception as exc:
            logger.error(f"VWAPExecutor._submit_limit {side} {symbol}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------

    def increment_scan_count(self, order_id: str) -> None:
        """
        Increment the scan counter for one order.
        Call once per bot scan for every live VWAP order.
        """
        if order_id in self._orders:
            self._orders[order_id]["scan_count"] += 1

    def check_and_cancel_stale(
        self, order_id: str, max_candles: int = 3
    ) -> bool:
        """
        Cancel the order if it has been pending for >= ``max_candles`` scans
        without being filled.

        Returns True if the order was cancelled in this call, False otherwise.
        """
        meta = self._orders.get(order_id)
        if meta is None or meta["filled"]:
            return False

        if meta["scan_count"] >= max_candles:
            try:
                get_trading_client().cancel_order_by_id(order_id)
                logger.info(
                    f"VWAP stale cancel: {meta['symbol']} order={order_id} "
                    f"after {meta['scan_count']} scans"
                )
                self._append_fill_stat(
                    order_id=order_id,
                    filled=False,
                    fill_price=None,
                )
                del self._orders[order_id]
                return True
            except Exception as exc:
                logger.error(f"VWAPExecutor cancel {order_id}: {exc}")
        return False

    def mark_filled(self, order_id: str, fill_price: float) -> None:
        """
        Record that a VWAP order has been filled.
        Call this when the broker confirms a fill (e.g. via order status poll).
        """
        meta = self._orders.get(order_id)
        if meta is None:
            return
        meta["filled"] = True
        savings = abs(fill_price - meta["market_close_at_submission"]) * meta["qty"]
        logger.info(
            f"VWAP filled: {meta['symbol']} {meta['side']} {meta['qty']} "
            f"@ {fill_price:.2f} | est. slippage saved ≈${savings:.2f}"
        )
        self._append_fill_stat(
            order_id=order_id,
            filled=True,
            fill_price=fill_price,
        )

    # ------------------------------------------------------------------
    # Fill-rate statistics
    # ------------------------------------------------------------------

    def get_fill_rate_stats(self) -> dict:
        """
        Return aggregate fill-rate statistics read from the on-disk log.

        Returns
        -------
        {
            "total_orders":  int,
            "filled_orders": int,
            "fill_rate":     float,   # 0.0 – 1.0
            "cancel_rate":   float,
            "total_slippage_saved": float,
        }
        """
        total = filled = 0
        total_savings = 0.0
        if os.path.exists(_FILL_STATS_FILE):
            try:
                with open(_FILL_STATS_FILE) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            total += 1
                            if rec.get("filled"):
                                filled += 1
                                total_savings += rec.get("estimated_slippage_saved", 0.0)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass

        fill_rate = filled / total if total else 0.0
        return {
            "total_orders": total,
            "filled_orders": filled,
            "fill_rate": round(fill_rate, 4),
            "cancel_rate": round(1 - fill_rate, 4) if total else 0.0,
            "total_slippage_saved": round(total_savings, 2),
        }

    def adjust_buffer_if_needed(self) -> None:
        """
        Widen buffer to 0.10% if the historical fill rate is below 60%.
        Resets to the minimum buffer if fill rate recovers above 60%.
        """
        stats = self.get_fill_rate_stats()
        if stats["total_orders"] < 5:
            return  # not enough data to decide

        if stats["fill_rate"] < _FILL_RATE_THRESHOLD:
            if self._buffer_pct < _WIDE_BUFFER_PCT:
                self._buffer_pct = _WIDE_BUFFER_PCT
                logger.info(
                    f"VWAPExecutor: fill rate {stats['fill_rate']:.0%} < 60% — "
                    f"widening buffer to {_WIDE_BUFFER_PCT:.2%}"
                )
        else:
            if self._buffer_pct > _MIN_BUFFER_PCT:
                self._buffer_pct = _MIN_BUFFER_PCT
                logger.info(
                    f"VWAPExecutor: fill rate {stats['fill_rate']:.0%} recovered — "
                    f"resetting buffer to {_MIN_BUFFER_PCT:.2%}"
                )

    def _append_fill_stat(
        self,
        order_id: str,
        filled: bool,
        fill_price: Optional[float],
    ) -> None:
        """Append one record to logs/vwap_fill_stats.json."""
        meta = self._orders.get(order_id, {})
        market_ref = meta.get("market_close_at_submission", 0.0)
        qty = meta.get("qty", 0)

        if filled and fill_price is not None and market_ref > 0:
            savings = abs(fill_price - market_ref) * qty
        else:
            savings = 0.0

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": meta.get("symbol", ""),
            "side": meta.get("side", ""),
            "limit_price": meta.get("limit_price", 0.0),
            "filled": filled,
            "fill_price": fill_price,
            "estimated_slippage_saved": round(savings, 4),
        }
        try:
            with open(_FILL_STATS_FILE, "a") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.error(f"VWAPExecutor: could not write fill stats: {exc}")

    # ------------------------------------------------------------------
    # Pairs leg synchronisation
    # ------------------------------------------------------------------

    def place_pair_legs(
        self,
        sym_long: str,
        qty_long: int,
        sym_short: str,
        qty_short: int,
        vwap_long: float,
        vwap_short: float,
    ) -> dict:
        """
        Submit both legs of a pairs trade simultaneously as VWAP limit orders.

        If one leg fills and the other remains open after 1 subsequent scan
        (detected by calling ``check_pair_sync`` on the next scan), the unfilled
        leg is cancelled and the filled leg is unwound via a market order.

        Returns
        -------
        {
            "status":   "filled" | "partial_cancel" | "failed",
            "long_id":  str | None,
            "short_id": str | None,
        }
        """
        long_id  = self.place_entry_order(sym_long,  qty_long,  vwap_long)
        short_id = self._submit_limit(
            sym_short, qty_short, "sell",
            self.get_exit_limit_price(vwap_short),
            vwap_short,
        )

        if long_id is None and short_id is None:
            return {"status": "failed", "long_id": None, "short_id": None}

        if long_id is None or short_id is None:
            # One leg failed to even submit — cancel/unwind the submitted one
            submitted_id = long_id or short_id
            self._unwind_leg(submitted_id)
            return {"status": "partial_cancel", "long_id": long_id, "short_id": short_id}

        # Tag both orders so check_pair_sync can link them
        self._orders[long_id]["pair_peer"]  = short_id
        self._orders[short_id]["pair_peer"] = long_id

        return {"status": "filled", "long_id": long_id, "short_id": short_id}

    def check_pair_sync(self, long_id: str, short_id: str) -> dict:
        """
        Called one scan after ``place_pair_legs``.  Checks fill status via
        Alpaca and unwinds any leg that filled without its partner.

        Returns same dict shape as ``place_pair_legs``.
        """
        long_meta  = self._orders.get(long_id,  {})
        short_meta = self._orders.get(short_id, {})

        long_filled  = self._query_filled(long_id)
        short_filled = self._query_filled(short_id)

        if long_filled and short_filled:
            long_meta["filled"]  = True
            short_meta["filled"] = True
            return {"status": "filled", "long_id": long_id, "short_id": short_id}

        if long_filled and not short_filled:
            logger.warning(
                f"Pair sync: long {long_meta.get('symbol')} filled but short "
                f"{short_meta.get('symbol')} did not — unwinding long leg"
            )
            self._cancel_order(short_id)
            self._unwind_leg(long_id)
            return {"status": "partial_cancel", "long_id": long_id, "short_id": short_id}

        if short_filled and not long_filled:
            logger.warning(
                f"Pair sync: short {short_meta.get('symbol')} filled but long "
                f"{long_meta.get('symbol')} did not — unwinding short leg"
            )
            self._cancel_order(long_id)
            self._unwind_leg(short_id)
            return {"status": "partial_cancel", "long_id": long_id, "short_id": short_id}

        # Neither filled yet — normal; caller will retry next scan
        return {"status": "pending", "long_id": long_id, "short_id": short_id}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query_filled(self, order_id: str) -> bool:
        """Ask Alpaca whether an order is filled.  Returns False on error."""
        try:
            order = get_trading_client().get_order_by_id(order_id)
            is_filled = str(order.status).lower() == "filled"
            if is_filled:
                fill_price = float(order.filled_avg_price or 0)
                if fill_price > 0:
                    self.mark_filled(order_id, fill_price)
            return is_filled
        except Exception as exc:
            logger.debug(f"VWAPExecutor._query_filled({order_id}): {exc}")
            return False

    def _cancel_order(self, order_id: str) -> None:
        """Cancel one order; ignore errors (may already be filled/cancelled)."""
        try:
            get_trading_client().cancel_order_by_id(order_id)
            logger.info(f"VWAPExecutor: cancelled order {order_id}")
        except Exception as exc:
            logger.debug(f"VWAPExecutor._cancel_order({order_id}): {exc}")
        self._orders.pop(order_id, None)

    def _unwind_leg(self, order_id: str) -> None:
        """
        Unwind a filled (or partially-filled) leg via a market counter-order.
        For a BUY leg we place a market SELL; for a SELL leg a market BUY.
        """
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        meta = self._orders.get(order_id, {})
        symbol = meta.get("symbol", "")
        qty    = meta.get("qty", 0)
        side   = meta.get("side", "buy")

        if not symbol or qty <= 0:
            return

        counter_side = OrderSide.SELL if side == "buy" else OrderSide.BUY
        try:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=counter_side,
                time_in_force=TimeInForce.DAY,
            )
            order = get_trading_client().submit_order(request)
            logger.info(
                f"VWAPExecutor: unwind {counter_side.value} {qty} {symbol} "
                f"(market) order={order.id}"
            )
        except Exception as exc:
            logger.error(f"VWAPExecutor._unwind_leg {symbol}: {exc}")
        finally:
            self._orders.pop(order_id, None)
