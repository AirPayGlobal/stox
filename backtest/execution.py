"""
Deterministic execution-cost simulation for the Fib validation harness.

The original backtester assumed a single fixed $0.02 half-spread per side and
always filled. Real 0DTE/1DTE option fills pay a variable bid/ask spread, take
slippage, can be delayed, and sometimes do not fill at all. This module models
those effects as named *scenarios* so a backtest can be stress-tested:

    baseline      reproduces the old fixed $0.02 half-spread, always fills
                  (kept only so new scenarios can be compared against it)
    optimistic    tight spread, minimal slippage, near-instant fills
    base          realistic spread from a premium/DTE model, ~full half-spread
                  crossed + slippage, occasional 1-bar delay, rare no-fill
    conservative  full spread crossed + extra slippage, more delay, more
                  no-fills/partials on wide or cheap contracts
    mid           DIAGNOSTIC ONLY — fills at mid-price. Never a default.

Everything is deterministic: randomness is derived by hashing a per-order key,
so a given (symbol, timestamp, scenario) always yields the same fill. That
makes the whole harness reproducible and unit-testable with no market data.

There is NO market-data or broker access here — callers pass in the mid price
(e.g. a Black-Scholes mark) and this module returns the friction to apply.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecScenario:
    name: str
    # Modelled full spread = clamp(spread_pct * premium, min_abs, premium) per share.
    spread_pct: float          # spread as a fraction of premium (cheap options wider)
    min_spread_abs: float      # floor on the modelled spread, $/share
    cross: float               # fraction of the HALF-spread actually paid on entry/exit
    slippage_abs: float        # extra $/share paid beyond the spread (adverse)
    delay_bars: int            # bars between signal and fill attempt
    unfilled_prob: float       # P(no fill), scaled up when the spread is wide
    partial_prob: float        # P(partial fill)
    fixed_half_spread: float | None = None  # baseline shortcut: pay this flat, always fill
    fills_at_mid: bool = False              # diagnostic 'mid' scenario only
    tick: float = 0.01


# The modelled spread widens for cheap contracts (spread_pct of premium) with an
# absolute floor. cross/slippage/delay/no-fill escalate optimistic->conservative.
SCENARIOS: dict[str, ExecScenario] = {
    "baseline": ExecScenario(
        "baseline", spread_pct=0.0, min_spread_abs=0.0, cross=0.0,
        slippage_abs=0.0, delay_bars=0, unfilled_prob=0.0, partial_prob=0.0,
        fixed_half_spread=0.02,
    ),
    "optimistic": ExecScenario(
        "optimistic", spread_pct=0.04, min_spread_abs=0.02, cross=0.5,
        slippage_abs=0.0, delay_bars=0, unfilled_prob=0.0, partial_prob=0.0,
    ),
    "base": ExecScenario(
        "base", spread_pct=0.06, min_spread_abs=0.03, cross=1.0,
        slippage_abs=0.01, delay_bars=1, unfilled_prob=0.03, partial_prob=0.05,
    ),
    "conservative": ExecScenario(
        "conservative", spread_pct=0.10, min_spread_abs=0.05, cross=1.0,
        slippage_abs=0.03, delay_bars=1, unfilled_prob=0.08, partial_prob=0.10,
    ),
    "mid": ExecScenario(
        "mid", spread_pct=0.0, min_spread_abs=0.0, cross=0.0,
        slippage_abs=0.0, delay_bars=0, unfilled_prob=0.0, partial_prob=0.0,
        fills_at_mid=True,
    ),
}

# Above this modelled-spread-to-premium ratio a contract is "wide" and the
# no-fill / partial probabilities are doubled (thin, hard-to-fill quotes).
WIDE_SPREAD_RATIO = 0.12


@dataclass(frozen=True)
class Fill:
    outcome: str          # "FILLED" | "PARTIAL" | "UNFILLED"
    price: float          # filled premium per share (0.0 if unfilled)
    qty: int              # filled contracts (<= requested; 0 if unfilled)
    delay_bars: int       # bars waited before the fill attempt
    spread_pct: float     # modelled full spread / premium at the fill
    friction: float       # $/share paid vs mid (spread cross + slippage)


def get_scenario(name: str) -> ExecScenario:
    if name not in SCENARIOS:
        raise ValueError(
            f"unknown execution scenario {name!r}; choose from {sorted(SCENARIOS)}"
        )
    return SCENARIOS[name]


def _rand01(key: str) -> float:
    """Deterministic uniform in [0, 1) from a string key."""
    h = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def modelled_spread(premium: float, sc: ExecScenario) -> float:
    """Full bid/ask spread in $/share this scenario assumes for `premium`."""
    if sc.fixed_half_spread is not None:
        return sc.fixed_half_spread * 2
    if sc.fills_at_mid:
        return 0.0
    return min(max(sc.spread_pct * premium, sc.min_spread_abs), max(premium, sc.tick))


def simulate_fill(
    side: str,
    mid: float,
    premium_ref: float,
    scenario: str,
    key: str,
    qty: int,
) -> Fill:
    """
    Simulate a marketable order.

    side        "BUY" (entry) or "SELL" (exit) — friction is always adverse.
    mid         the mid/fair price at the fill bar ($/share; e.g. a BS mark).
    premium_ref reference premium for spread sizing (usually the entry premium).
    scenario    scenario name (see SCENARIOS).
    key         stable per-order key (symbol|timestamp|side) for determinism.
    qty         requested contracts.

    Returns a Fill. Entries never fill better than mid unless the explicit
    'mid' diagnostic scenario is selected.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    sc = get_scenario(scenario)
    spread = modelled_spread(premium_ref, sc)
    spread_pct = spread / premium_ref if premium_ref > 0 else 0.0

    # No-fill / partial draws (skipped for baseline/mid which always fill full).
    outcome, filled_qty = "FILLED", qty
    if sc.unfilled_prob or sc.partial_prob:
        scale = 2.0 if spread_pct >= WIDE_SPREAD_RATIO else 1.0
        u = _rand01(key + "|fill")
        p_unfilled = min(sc.unfilled_prob * scale, 0.95)
        p_partial = sc.partial_prob * scale
        if u < p_unfilled:
            return Fill("UNFILLED", 0.0, 0, sc.delay_bars, spread_pct, 0.0)
        if u < p_unfilled + p_partial and qty >= 2:
            outcome, filled_qty = "PARTIAL", max(1, qty // 2)

    if sc.fills_at_mid:
        return Fill(outcome, round(mid, 2), filled_qty, 0, 0.0, 0.0)

    # Friction: cross a fraction of the half-spread, plus adverse slippage.
    # Baseline pays a flat half-spread to reproduce the original backtester.
    if sc.fixed_half_spread is not None:
        friction = sc.fixed_half_spread
    else:
        friction = sc.cross * (spread / 2) + sc.slippage_abs
    price = mid + friction if side == "BUY" else max(mid - friction, 0.01)
    return Fill(outcome, round(price, 2), filled_qty, sc.delay_bars, spread_pct, round(friction, 4))
