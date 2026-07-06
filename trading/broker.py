"""
Alpaca trading API wrapper (account, clock, option orders, positions).

Alpaca does not support bracket orders on options, so stops/targets are
enforced by the engine (see engine.py), not by the broker.
"""
from __future__ import annotations

from config import Config
from utils.logger import get_logger

logger = get_logger("broker")

_client = None


def _trading():
    global _client
    if _client is None:
        from alpaca.trading.client import TradingClient

        _client = TradingClient(
            Config.ALPACA_API_KEY,
            Config.ALPACA_API_SECRET,
            paper=Config.ALPACA_MODE != "live",
        )
    return _client


def validate_credentials() -> tuple[bool, str]:
    try:
        acct = _trading().get_account()
        return True, f"Authenticated — account {acct.account_number} ({Config.ALPACA_MODE})"
    except Exception as exc:
        return False, str(exc)


def get_account() -> dict:
    acct = _trading().get_account()
    return {
        "equity": float(acct.equity),
        "cash": float(acct.cash),
        "buying_power": float(acct.buying_power),
        "options_level": int(getattr(acct, "options_trading_level", 0) or 0),
    }


def is_market_open() -> bool:
    try:
        return bool(_trading().get_clock().is_open)
    except Exception as exc:
        logger.error(f"Clock fetch failed: {exc}")
        return False


def get_option_positions() -> dict[str, dict]:
    """Open option positions keyed by OCC symbol."""
    out: dict[str, dict] = {}
    for pos in _trading().get_all_positions():
        if str(getattr(pos, "asset_class", "")).endswith("option") or len(pos.symbol) > 12:
            out[pos.symbol] = {
                "qty": abs(int(float(pos.qty))),
                "avg_entry": float(pos.avg_entry_price),
                "current_price": float(pos.current_price or 0),
                "unrealized_pl": float(pos.unrealized_pl or 0),
            }
    return out


def buy_option(symbol: str, qty: int) -> str | None:
    """Market-buy `qty` contracts. Returns the order id, or None on failure."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    try:
        order = _trading().submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
        )
        logger.info(f"Order submitted: BUY {qty}x {symbol} (id={order.id})")
        return str(order.id)
    except Exception as exc:
        logger.error(f"Buy order failed for {symbol}: {exc}")
        return None


def close_option_position(symbol: str) -> bool:
    """Close an entire option position at market."""
    try:
        _trading().close_position(symbol)
        logger.info(f"Close submitted for {symbol}")
        return True
    except Exception as exc:
        logger.error(f"Close failed for {symbol}: {exc}")
        return False


def close_all_option_positions() -> None:
    for symbol in list(get_option_positions()):
        close_option_position(symbol)
