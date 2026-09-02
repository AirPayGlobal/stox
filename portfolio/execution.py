"""
Shared equity/ETF execution simulation (blueprint §7.3).

The same path is used by the backtest and the paper engine so their fills match.
Scenarios model ETF-scale friction (spreads in basis points, not option
dollars):

    ideal         fill at the reference price, no cost, always full.
                  DIAGNOSTIC ONLY — never used for approval.
    base          tight ETF spread + small slippage + 1-session delay,
                  rare unfilled / occasional partial.
    conservative  wider spread, more slippage, an adverse gap, delayed entry,
                  more unfilled / partial.

Deterministic (seeded per order). Fractional rounding and the minimum order
notional come from the capital profile; an order that rounds below the minimum
is reported UNFILLED (capacity/rounding effect, §7.3).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class EquityExecScenario:
    name: str
    spread_bps: float        # full bid/ask spread, basis points of price
    slippage_bps: float      # adverse slippage beyond half-spread
    adverse_gap_bps: float   # extra adverse move on entry (gap risk)
    delay_days: int
    unfilled_prob: float
    partial_prob: float
    fills_at_ref: bool = False


SCENARIOS: dict[str, EquityExecScenario] = {
    "ideal": EquityExecScenario("ideal", 0, 0, 0, 0, 0.0, 0.0, fills_at_ref=True),
    "base": EquityExecScenario("base", spread_bps=3, slippage_bps=1, adverse_gap_bps=0,
                               delay_days=1, unfilled_prob=0.01, partial_prob=0.02),
    "conservative": EquityExecScenario("conservative", spread_bps=12, slippage_bps=5,
                                       adverse_gap_bps=20, delay_days=1,
                                       unfilled_prob=0.05, partial_prob=0.05),
}


@dataclass(frozen=True)
class EquityFill:
    outcome: str          # FILLED | PARTIAL | UNFILLED
    price: float
    qty: float
    delay_days: int
    friction_bps: float


def get_scenario(name: str) -> EquityExecScenario:
    if name not in SCENARIOS:
        raise ValueError(f"unknown equity execution scenario {name!r}; choose {sorted(SCENARIOS)}")
    return SCENARIOS[name]


def _rand01(key: str) -> float:
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


def simulate_equity_fill(side: str, ref_price: float, qty: float, scenario: str,
                         key: str, fractional: bool = True,
                         min_notional: float = 0.0) -> EquityFill:
    """Simulate a marketable equity order. `side` BUY/SELL, `qty` >= 0 (magnitude)."""
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    sc = get_scenario(scenario)
    q = round(qty, 6) if fractional else float(int(qty))
    if q <= 0:
        return EquityFill("UNFILLED", 0.0, 0.0, sc.delay_days, 0.0)

    # No-fill / partial (skipped for ideal which always fills full).
    if sc.unfilled_prob or sc.partial_prob:
        u = _rand01(key + "|fill")
        if u < sc.unfilled_prob:
            return EquityFill("UNFILLED", 0.0, 0.0, sc.delay_days, 0.0)
        if u < sc.unfilled_prob + sc.partial_prob:
            q = round(q * 0.5, 6) if fractional else float(int(q * 0.5))
            outcome = "PARTIAL" if q > 0 else "UNFILLED"
        else:
            outcome = "FILLED"
    else:
        outcome = "FILLED"
    if q <= 0:
        return EquityFill("UNFILLED", 0.0, 0.0, sc.delay_days, 0.0)

    if sc.fills_at_ref:
        price, friction_bps = ref_price, 0.0
    else:
        friction_bps = sc.spread_bps / 2 + sc.slippage_bps + sc.adverse_gap_bps
        adj = ref_price * friction_bps / 1e4
        price = ref_price + adj if side == "BUY" else max(ref_price - adj, 0.01)

    # Capacity/rounding: too-small notional cannot be placed.
    if price * q < min_notional:
        return EquityFill("UNFILLED", 0.0, 0.0, sc.delay_days, friction_bps)

    return EquityFill(outcome, round(price, 4), q, sc.delay_days, round(friction_bps, 2))
