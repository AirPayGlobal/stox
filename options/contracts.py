"""
Contract selection: turn a directional signal into one tradeable contract.

Selection rules:
  * nearest expiry within Config.MAX_DTE
  * calls for LONG, puts for SHORT
  * pass liquidity filters (min bid, min open interest, max spread)
  * |delta| closest to Config.TARGET_DELTA; if the feed returns no greeks,
    fall back to the strike closest to the money
"""
from __future__ import annotations

from data.options_data import OptionQuote, get_chain, nearest_expiry
from analysis.signals import Signal
from config import Config
from utils.logger import get_logger

logger = get_logger("contracts")

# How far around the spot price to request strikes (fraction of spot).
STRIKE_WINDOW_PCT = 0.03


def passes_liquidity(q: OptionQuote) -> bool:
    return (
        q.bid >= Config.MIN_BID
        and q.ask > q.bid
        and q.open_interest >= Config.MIN_OPEN_INTEREST
        and q.spread_pct <= Config.MAX_SPREAD_PCT
    )


def pick_contract(candidates: list[OptionQuote], spot: float) -> OptionQuote | None:
    """Pure selection logic over an already-fetched chain slice."""
    liquid = [q for q in candidates if passes_liquidity(q)]
    if not liquid:
        return None

    with_delta = [q for q in liquid if q.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda q: abs(abs(q.delta) - Config.TARGET_DELTA))
    return min(liquid, key=lambda q: abs(q.strike - spot))


def select_contract(underlying: str, direction: Signal, spot: float) -> OptionQuote | None:
    """Fetch the chain near the money and pick the contract to trade."""
    if direction == Signal.FLAT:
        return None
    option_type = "call" if direction == Signal.LONG else "put"

    expiry = nearest_expiry(underlying)
    if expiry is None:
        logger.warning(f"No expiry within {Config.MAX_DTE} DTE for {underlying}")
        return None

    window = spot * STRIKE_WINDOW_PCT
    chain = get_chain(
        underlying,
        option_type,
        expiry,
        strike_lo=spot - window,
        strike_hi=spot + window,
    )
    if not chain:
        logger.warning(f"Empty chain for {underlying} {option_type} {expiry}")
        return None

    contract = pick_contract(chain, spot)
    if contract is None:
        logger.info(
            f"No {underlying} {option_type} passed liquidity filters "
            f"({len(chain)} candidates)"
        )
    return contract
