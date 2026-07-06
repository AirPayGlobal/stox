"""
Options chain and quote data via Alpaca.

Two sources are combined:
  * the trading API's contract listing (strikes, expiries, open interest)
  * the options data API's chain snapshots (bid/ask, greeks, IV)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from config import Config
from utils.logger import get_logger

logger = get_logger("options_data")

_option_client = None
_trading_client = None


def _data_client():
    global _option_client
    if _option_client is None:
        from alpaca.data.historical.option import OptionHistoricalDataClient

        _option_client = OptionHistoricalDataClient(
            Config.ALPACA_API_KEY, Config.ALPACA_API_SECRET
        )
    return _option_client


def _trading():
    global _trading_client
    if _trading_client is None:
        from alpaca.trading.client import TradingClient

        _trading_client = TradingClient(
            Config.ALPACA_API_KEY,
            Config.ALPACA_API_SECRET,
            paper=Config.ALPACA_MODE != "live",
        )
    return _trading_client


@dataclass
class OptionQuote:
    """A tradeable contract with everything selection/sizing needs."""

    symbol: str
    underlying: str
    option_type: str  # "call" | "put"
    strike: float
    expiry: date
    bid: float
    ask: float
    delta: float | None
    implied_vol: float | None
    open_interest: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else 1.0


def nearest_expiry(underlying: str, max_dte: int | None = None) -> date | None:
    """Earliest active expiry within `max_dte` days (0 = today)."""
    from alpaca.trading.requests import GetOptionContractsRequest

    max_dte = Config.MAX_DTE if max_dte is None else max_dte
    today = date.today()
    try:
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            expiration_date_gte=today,
            expiration_date_lte=today + timedelta(days=max_dte),
            limit=100,
        )
        resp = _trading().get_option_contracts(req)
        contracts = resp.option_contracts or []
    except Exception as exc:
        logger.error(f"Expiry lookup failed for {underlying}: {exc}")
        return None

    expiries = sorted({c.expiration_date for c in contracts})
    return expiries[0] if expiries else None


def get_chain(
    underlying: str,
    option_type: str,
    expiry: date,
    strike_lo: float,
    strike_hi: float,
) -> list[OptionQuote]:
    """Snapshot quotes + greeks for one side of the chain near the money."""
    from alpaca.data.requests import OptionChainRequest
    from alpaca.trading.enums import ContractType
    from alpaca.trading.requests import GetOptionContractsRequest

    # Open interest comes from the contract listing.
    oi: dict[str, int] = {}
    try:
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            expiration_date=expiry,
            type=ContractType.CALL if option_type == "call" else ContractType.PUT,
            strike_price_gte=str(strike_lo),
            strike_price_lte=str(strike_hi),
            limit=500,
        )
        resp = _trading().get_option_contracts(req)
        for c in resp.option_contracts or []:
            oi[c.symbol] = int(c.open_interest or 0)
    except Exception as exc:
        logger.warning(f"Open-interest listing failed for {underlying}: {exc}")

    try:
        chain_req = OptionChainRequest(
            underlying_symbol=underlying,
            expiration_date=expiry,
            type=option_type,
            strike_price_gte=strike_lo,
            strike_price_lte=strike_hi,
        )
        snapshots = _data_client().get_option_chain(chain_req)
    except Exception as exc:
        logger.error(f"Chain snapshot failed for {underlying}: {exc}")
        return []

    out: list[OptionQuote] = []
    for sym, snap in snapshots.items():
        quote = getattr(snap, "latest_quote", None)
        if quote is None:
            continue
        greeks = getattr(snap, "greeks", None)
        strike, opt_type = _parse_occ(sym)
        if strike is None:
            continue
        out.append(
            OptionQuote(
                symbol=sym,
                underlying=underlying,
                option_type=opt_type,
                strike=strike,
                expiry=expiry,
                bid=float(quote.bid_price or 0),
                ask=float(quote.ask_price or 0),
                delta=float(greeks.delta) if greeks and greeks.delta is not None else None,
                implied_vol=(
                    float(snap.implied_volatility)
                    if getattr(snap, "implied_volatility", None) is not None
                    else None
                ),
                open_interest=oi.get(sym, 0),
            )
        )
    return out


def get_option_mid(symbol: str) -> float | None:
    """Latest mid price for one contract (used to mark open positions)."""
    from alpaca.data.requests import OptionLatestQuoteRequest

    try:
        req = OptionLatestQuoteRequest(symbol_or_symbols=symbol)
        q = _data_client().get_option_latest_quote(req)[symbol]
        bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
        if ask <= 0:
            return None
        return (bid + ask) / 2
    except Exception as exc:
        logger.error(f"Option quote failed for {symbol}: {exc}")
        return None


def _parse_occ(symbol: str) -> tuple[float | None, str]:
    """
    Parse strike and type from an OCC symbol, e.g. SPY260706C00620000
    -> (620.0, "call").
    """
    try:
        body = symbol[-15:]  # YYMMDD + C/P + 8-digit strike
        opt_type = "call" if body[6].upper() == "C" else "put"
        strike = int(body[7:]) / 1000.0
        return strike, opt_type
    except (ValueError, IndexError):
        return None, ""
