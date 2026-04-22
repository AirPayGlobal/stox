"""
Alpaca trading + data client for StoxDaily (intraday) credentials.
Uses DAILY_ALPACA_API_KEY / DAILY_ALPACA_API_SECRET from Config.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data import StockHistoricalDataClient
try:
    from alpaca.data.historical import NewsClient as _NewsClient
    _NEWS_AVAILABLE = True
except ImportError:
    _NEWS_AVAILABLE = False

from config import Config
from utils.logger import get_logger

logger = get_logger("intraday.client")

_ET = timezone(timedelta(hours=-4))  # EDT; good enough for trading hours (handles EST too at -5)

_trading_client: Optional[TradingClient] = None
_data_client: Optional[StockHistoricalDataClient] = None
_news_client = None
_lock = threading.Lock()


def _f(val, default: float = 0.0) -> float:
    return float(val) if val is not None else default


def get_trading_client() -> TradingClient:
    global _trading_client
    with _lock:
        if _trading_client is None:
            _trading_client = TradingClient(
                api_key=Config.DAILY_ALPACA_API_KEY,
                secret_key=Config.DAILY_ALPACA_API_SECRET,
                paper=True,
            )
    return _trading_client


def get_data_client() -> StockHistoricalDataClient:
    global _data_client
    with _lock:
        if _data_client is None:
            _data_client = StockHistoricalDataClient(
                api_key=Config.DAILY_ALPACA_API_KEY,
                secret_key=Config.DAILY_ALPACA_API_SECRET,
            )
    return _data_client


def get_news_client():
    """Return a NewsClient singleton, or None if the package doesn't support it."""
    global _news_client
    if not _NEWS_AVAILABLE:
        return None
    with _lock:
        if _news_client is None:
            try:
                _news_client = _NewsClient(
                    api_key=Config.DAILY_ALPACA_API_KEY,
                    secret_key=Config.DAILY_ALPACA_API_SECRET,
                )
            except Exception as exc:
                logger.warning("NewsClient init failed: %s", exc)
                return None
    return _news_client


def get_account() -> dict:
    """Return account details plus daily P&L as a plain dict."""
    try:
        client = get_trading_client()
        acct = client.get_account()
        equity = _f(acct.equity)
        last_equity = _f(acct.last_equity)
        return {
            "equity": equity,
            "cash": _f(acct.cash),
            "buying_power": _f(acct.buying_power),
            "daily_pnl": equity - last_equity,
        }
    except Exception as exc:
        logger.error("get_account failed: %s", exc)
        return {"equity": 0.0, "cash": 0.0, "buying_power": 0.0, "daily_pnl": 0.0}


def get_positions() -> dict[str, dict]:
    """Return open positions keyed by symbol."""
    try:
        client = get_trading_client()
        raw = client.get_all_positions()
        result = {}
        for pos in raw:
            result[pos.symbol] = {
                "qty": float(pos.qty),
                "avg_entry": _f(pos.avg_entry_price),
                "market_value": _f(pos.market_value),
                "unrealized_pl": _f(pos.unrealized_pl),
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
            }
        return result
    except Exception as exc:
        logger.error("get_positions failed: %s", exc)
        return {}


def close_position(symbol: str) -> bool:
    """Close an open position for the given symbol. Returns True on success."""
    try:
        client = get_trading_client()
        client.close_position(symbol)
        logger.info("Closed position: %s", symbol)
        return True
    except Exception as exc:
        logger.error("close_position(%s) failed: %s", symbol, exc)
        return False


def close_all_positions() -> int:
    """Close all open positions. Returns the count closed."""
    try:
        positions = get_positions()
        count = 0
        for symbol in list(positions.keys()):
            if close_position(symbol):
                count += 1
        logger.info("Closed %d positions at EOD", count)
        return count
    except Exception as exc:
        logger.error("close_all_positions failed: %s", exc)
        return 0


def place_bracket_order(
    symbol: str,
    qty: int,
    side: str,
    limit_price: float,
    stop_loss: float,
    take_profit: float,
) -> Optional[str]:
    """
    Place a bracket (limit + stop-loss + take-profit) order.
    Returns order_id on success, None on failure.
    """
    try:
        client = get_trading_client()
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            order_class=OrderClass.BRACKET,
            stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
        )
        order = client.submit_order(req)
        logger.info(
            "Bracket order placed: %s %s %d @ %.2f SL=%.2f TP=%.2f id=%s",
            side.upper(), symbol, qty, limit_price, stop_loss, take_profit, order.id,
        )
        return str(order.id)
    except Exception as exc:
        logger.error(
            "place_bracket_order(%s %s %d) failed: %s", side, symbol, qty, exc
        )
        return None


def place_market_order(symbol: str, qty: int, side: str) -> Optional[str]:
    """
    Place a market order. Returns order_id on success, None on failure.
    """
    try:
        client = get_trading_client()
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(req)
        logger.info("Market order placed: %s %s %d id=%s", side.upper(), symbol, qty, order.id)
        return str(order.id)
    except Exception as exc:
        logger.error("place_market_order(%s %s %d) failed: %s", side, symbol, qty, exc)
        return None


def is_market_open() -> bool:
    """Return True if the US market is currently open."""
    try:
        client = get_trading_client()
        clock = client.get_clock()
        return bool(clock.is_open)
    except Exception as exc:
        logger.error("is_market_open failed: %s", exc)
        return False


def minutes_to_close() -> float:
    """Return minutes until 4 PM ET (market close)."""
    now_et = datetime.now(tz=_ET)
    close_today = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    delta = (close_today - now_et).total_seconds() / 60.0
    return delta
