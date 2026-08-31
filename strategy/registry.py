"""
Strategy registry — the auditable lifecycle record for every STOX strategy.

Per the multi-strategy blueprint (§6.1, §8, §9 Phase 0): strategies move through
a fixed lifecycle, and a strategy's state here is the single source of truth for
whether the engine may trade it. Retired strategies keep their failure reason on
record and must never be auto-traded again.

This is code-defined (so git history gives the immutable audit the blueprint
asks for). `as_dicts()` feeds the future Strategy Registry UI / API.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Fixed lifecycle. A strategy advances left-to-right through research gates;
# PAUSED/RETIRED are terminal-ish states off the main path.
LIFECYCLE = (
    "HYPOTHESIS",   # idea only
    "REGISTERED",   # pre-registration committed (spec frozen), not yet built
    "BACKTEST",     # implemented; in-sample results exist
    "OOS",          # passed walk-forward / out-of-sample
    "PAPER",        # eligible for paper allocation
    "PAUSED",       # temporarily halted (no allocation)
    "RETIRED",      # failed validation or superseded; never auto-traded
)

# States in which the live engine is permitted to place (paper) orders for a
# strategy. Research/retired states are NOT tradeable.
TRADEABLE_STATES = frozenset({"PAPER"})


@dataclass(frozen=True)
class StrategyRecord:
    id: str
    name: str
    version: str
    lifecycle: str
    asset_class: str          # "options" | "etf" | "equity"
    cadence: str              # "intraday" | "weekly" | "monthly" | "quarterly"
    rationale: str            # one-line economic/behavioural thesis
    status_reason: str        # why it is in its current lifecycle state
    evidence: str = ""        # path/link to the validating (or failing) report
    data_ready: str = "unknown"   # "yes" | "partial" | "blocked"
    eligible_profiles: tuple = ()  # capital profiles it may run in once validated
    config_hash: str | None = None

    def __post_init__(self) -> None:
        if self.lifecycle not in LIFECYCLE:
            raise ValueError(f"{self.id}: invalid lifecycle {self.lifecycle!r}")

    @property
    def tradeable(self) -> bool:
        return self.lifecycle in TRADEABLE_STATES


_RECORDS: list[StrategyRecord] = [
    # ---- Retired (failed validation). Kept auditable; never auto-traded. ----
    StrategyRecord(
        id="fib", name="Fib gold-zone pullback", version="fib-2.0",
        lifecycle="RETIRED", asset_class="options", cadence="intraday",
        rationale="With-trend pullback into the 0.5-0.618 retracement of an impulse leg.",
        status_reason=(
            "Real-bar validation (138 SPY/QQQ sessions, engine parity) is net-negative: "
            "base -$31/trade, conservative -$156/trade, walk-forward folds deteriorating "
            "(+$15 -> -$60). Prior +$132k/120d backtest was a cadence + friction artifact."
        ),
        evidence="docs/FIB_VALIDATION_REPORT.md", data_ready="yes",
    ),
    StrategyRecord(
        id="orb", name="Opening-range breakout", version="orb-1.0",
        lifecycle="RETIRED", asset_class="options", cadence="intraday",
        rationale="Momentum breakout of the first 15-minute range.",
        status_reason="Net-negative live over 20+ trades; MFE data showed entries at reversals.",
        evidence="docs/STRATEGIES.md", data_ready="yes",
    ),
    StrategyRecord(
        id="sweep", name="Liquidity-sweep reversal", version="sweep-1.0",
        lifecycle="RETIRED", asset_class="options", cadence="intraday",
        rationale="Reversal after price sweeps an obvious prior high/low and reclaims it.",
        status_reason=(
            "~+$30k backtest went breakeven-to--$1,281 over 26 live trades; entered at reversals."
        ),
        evidence="docs/STRATEGIES.md", data_ready="yes",
    ),
    # ---- Registered (pre-registration committed; not yet built). ----
    StrategyRecord(
        id="ETF_TREND_V1", name="Diversified ETF time-series momentum / trend",
        version="v1", lifecycle="REGISTERED", asset_class="etf", cadence="weekly",
        rationale="Persistent medium-term trends across distinct liquid markets; long/cash.",
        status_reason=(
            "Backtested on real bars 2016-2026: positive after costs (base ~5.6% CAGR, "
            "Sharpe 0.89, MaxDD ~11%) but does NOT beat SPY buy-and-hold on this sample "
            "(the pre-stated benchmark gate). Sample omits 2008-09 crisis where trend "
            "earns its edge; Deflated-Sharpe not yet computed. Not advanced to tradeable."
        ),
        evidence="docs/ETF_STRATEGY_VALIDATION_REPORT.md", data_ready="partial",
        eligible_profiles=("paper_500", "paper_2500", "paper_10000", "paper_50000"),
    ),
    StrategyRecord(
        id="ETF_RELATIVE_MOMENTUM_V1", name="ETF cross-sectional momentum / relative strength",
        version="v1", lifecycle="REGISTERED", asset_class="etf", cadence="monthly",
        rationale="Hold the strongest liquid segments, gated by positive absolute momentum.",
        status_reason=(
            "Backtested on real bars 2016-2026: positive after costs (~6% CAGR, Sharpe "
            "0.75) but below SPY buy-and-hold and weaker than the trend sleeve; correlated "
            "with trend so adds little diversification. Not advanced to tradeable."
        ),
        evidence="docs/ETF_STRATEGY_VALIDATION_REPORT.md", data_ready="partial",
        eligible_profiles=("paper_500", "paper_2500", "paper_10000", "paper_50000"),
    ),
]

REGISTRY: dict[str, StrategyRecord] = {r.id: r for r in _RECORDS}


def get(strategy_id: str) -> StrategyRecord | None:
    return REGISTRY.get(strategy_id)


def is_retired(strategy_id: str) -> bool:
    r = REGISTRY.get(strategy_id)
    return r is not None and r.lifecycle == "RETIRED"


def is_tradeable(strategy_id: str) -> bool:
    """True only if the strategy is registered AND in a tradeable lifecycle state.
    An unknown id is not tradeable."""
    r = REGISTRY.get(strategy_id)
    return r is not None and r.tradeable


def retired() -> list[StrategyRecord]:
    return [r for r in _RECORDS if r.lifecycle == "RETIRED"]


def tradeable() -> list[StrategyRecord]:
    return [r for r in _RECORDS if r.tradeable]


def as_dicts() -> list[dict]:
    out = []
    for r in _RECORDS:
        d = r.__dict__.copy()
        d["tradeable"] = r.tradeable
        d["eligible_profiles"] = list(r.eligible_profiles)
        out.append(d)
    return out
