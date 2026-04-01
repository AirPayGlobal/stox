"""
Thin helper for partial position closes via Alpaca.

Exposes a single public function used by PartialExitManager's callers in main.py:

    from trading.alpaca_partial import close_position_partial

    order_id = close_position_partial("AAPL", 50)
"""
from __future__ import annotations

from typing import Optional

from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.common.exceptions import APIError

from trading.alpaca_client import get_trading_client
from utils.logger import get_logger

logger = get_logger(__name__)


def close_position_partial(symbol: str, shares: int) -> Optional[str]:
    """
    Place a market SELL order for *shares* shares (partial position close).

    This does NOT close the full position — it sells exactly the requested
    number of shares so the remainder continues to be held.

    Parameters
    ----------
    symbol : ticker symbol (e.g. "AAPL")
    shares : number of shares to sell (must be > 0)

    Returns
    -------
    Order ID string on success, None on failure.
    """
    if shares <= 0:
        logger.warning(f"close_position_partial: shares must be > 0, got {shares} for {symbol}")
        return None

    try:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=shares,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = get_trading_client().submit_order(request)
        logger.info(f"Partial SELL {shares} {symbol} | order={order.id}")
        return str(order.id)
    except APIError as exc:
        logger.error(f"close_position_partial API error for {symbol} ({shares} shares): {exc}")
        return None
    except Exception as exc:
        logger.error(f"close_position_partial unexpected error for {symbol}: {exc}")
        return None
