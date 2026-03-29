"""
Alpaca broker client — account info, order placement, position management.
"""
from __future__ import annotations

from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType
from alpaca.common.exceptions import APIError

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

_trading_client: Optional[TradingClient] = None


def get_trading_client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(
            api_key=Config.ALPACA_API_KEY,
            secret_key=Config.ALPACA_API_SECRET,
            paper=(Config.ALPACA_MODE == "paper"),
        )
    return _trading_client


def validate_credentials() -> tuple[bool, str]:
    """
    Test that the configured API key/secret are accepted by Alpaca.
    Returns (True, summary) on success or (False, error_message) on failure.
    Call this once on bot startup before entering the trading loop.
    """
    try:
        account = get_trading_client().get_account()
        equity = float(account.equity)
        return True, f"equity=${equity:,.2f} account_id={account.id}"
    except APIError as exc:
        if exc.status_code in (401, 403):
            return False, (
                "Invalid API key or secret (HTTP 403). "
                "Check ALPACA_API_KEY / ALPACA_API_SECRET in your .env file. "
                "Make sure you are using Paper keys when ALPACA_MODE=paper."
            )
        return False, f"Alpaca API error (HTTP {exc.status_code}): {exc}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


def get_account() -> dict:
    """Return account details as a plain dict."""
    account = get_trading_client().get_account()
    return {
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "portfolio_value": float(account.portfolio_value),
        "daytrade_count": account.daytrade_count,
        "pattern_day_trader": account.pattern_day_trader,
    }


def get_positions() -> dict[str, dict]:
    """Return open positions keyed by symbol."""
    positions = get_trading_client().get_all_positions()
    return {
        p.symbol: {
            "qty": float(p.qty),
            "avg_entry": float(p.avg_entry_price),
            "market_value": float(p.market_value),
            "unrealised_pl": float(p.unrealized_pl),
            "unrealised_plpc": float(p.unrealized_plpc),
            "side": p.side.value,
        }
        for p in positions
    }


def place_bracket_order(
    symbol: str,
    qty: float,
    stop_loss_price: float,
    take_profit_price: float,
) -> Optional[str]:
    """
    Submit a market BUY order with attached stop-loss and take-profit legs.
    Uses GTC (Good Till Cancelled) so bracket legs persist overnight —
    DAY orders would leave positions unprotected after market close.
    Returns the order ID on success, None on failure.
    """
    try:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class="bracket",
            stop_loss=StopLossRequest(stop_price=round(stop_loss_price, 2)),
            take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
        )
        order = get_trading_client().submit_order(request)
        logger.info(
            f"BUY {qty} {symbol} | SL={stop_loss_price:.2f} TP={take_profit_price:.2f} | order={order.id}"
        )
        return str(order.id)
    except Exception as exc:
        logger.error(f"Failed to place order for {symbol}: {exc}")
        return None


def place_short_order(symbol: str, qty: int) -> Optional[str]:
    """
    Short-sell a stock (sell shares we don't own).
    Returns the order ID on success, None on failure.
    """
    try:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = get_trading_client().submit_order(request)
        logger.info(f"SHORT {qty} {symbol} | order={order.id}")
        return str(order.id)
    except Exception as exc:
        logger.error(f"Failed to short {symbol}: {exc}")
        return None


def cover_short_order(symbol: str, qty: int) -> Optional[str]:
    """
    Buy to cover an existing short position.
    Returns the order ID on success, None on failure.
    """
    try:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = get_trading_client().submit_order(request)
        logger.info(f"COVER {qty} {symbol} | order={order.id}")
        return str(order.id)
    except Exception as exc:
        logger.error(f"Failed to cover short {symbol}: {exc}")
        return None


def place_long_order(symbol: str, qty: int) -> Optional[str]:
    """Simple market BUY (no bracket). Used for pairs long leg."""
    try:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = get_trading_client().submit_order(request)
        logger.info(f"LONG {qty} {symbol} | order={order.id}")
        return str(order.id)
    except Exception as exc:
        logger.error(f"Failed to place long order for {symbol}: {exc}")
        return None


def close_position(symbol: str) -> bool:
    """Close an open position for the given symbol."""
    try:
        get_trading_client().close_position(symbol)
        logger.info(f"Closed position: {symbol}")
        return True
    except Exception as exc:
        logger.error(f"Failed to close {symbol}: {exc}")
        return False


def close_all_positions() -> None:
    """Emergency close of all open positions."""
    get_trading_client().close_all_positions(cancel_orders=True)
    logger.warning("Closed all positions.")


def cancel_all_orders() -> None:
    """Cancel all open orders."""
    get_trading_client().cancel_orders()
    logger.info("Cancelled all open orders.")


def get_pending_symbols() -> set[str]:
    """Return set of symbols with open/pending orders."""
    try:
        orders = get_trading_client().get_orders()
        return {o.symbol for o in orders if o.status in ("new", "partially_filled", "accepted", "pending_new")}
    except Exception as exc:
        logger.error(f"Failed to fetch pending orders: {exc}")
        return set()


def is_market_open() -> bool:
    """Check if the US stock market is currently open."""
    clock = get_trading_client().get_clock()
    return clock.is_open


def get_filled_exit_price(symbol: str, after_iso: str) -> tuple[Optional[float], str]:
    """
    Scan recent closed orders to find the fill price of a bracket exit
    (take-profit limit-sell or stop-loss stop-sell) for a long position.

    Returns (fill_price, status) where status is one of:
        'TOOK_PROFIT'  — limit sell filled (price reached TP)
        'STOPPED'      — stop sell filled  (price hit SL)
        'CLOSED'       — filled sell of unknown type
    Returns (None, '') if no filled sell order is found.
    """
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from datetime import datetime as _dt

        try:
            after_dt = _dt.fromisoformat(after_iso)
        except Exception:
            after_dt = None

        orders = get_trading_client().get_orders(
            filter=GetOrdersRequest(
                status="closed",
                symbols=[symbol],
                limit=20,
                after=after_dt,
            )
        )
        for order in sorted(orders, key=lambda o: str(o.filled_at or ""), reverse=True):
            if str(order.status).lower() != "filled":
                continue
            if "sell" not in str(order.side).lower():
                continue
            price = float(order.filled_avg_price or 0)
            if price <= 0:
                continue
            otype = str(
                getattr(order, "order_type", None) or getattr(order, "type", "")
            ).lower()
            if "limit" in otype:
                return price, "TOOK_PROFIT"
            if "stop" in otype:
                return price, "STOPPED"
            return price, "CLOSED"
    except Exception as exc:
        logger.debug(f"get_filled_exit_price({symbol}): {exc}")
    return None, ""
