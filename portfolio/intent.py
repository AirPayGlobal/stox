"""
StrategyIntent — the only thing a strategy produces.

Per the multi-strategy blueprint (§6.1): strategies never place orders. Each
strategy emits versioned target intents; the allocator, risk engine, and
execution engine own everything downstream. An intent is a *target*, not an
order — "hold this fraction of my sleeve budget in this symbol", with the
provenance needed for attribution and audit.

A strategy's output for one rebalance is a *set* of intents (one per symbol it
wants to hold); the unallocated remainder is implicitly cash.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StrategyIntent:
    strategy_id: str
    version: str
    config_hash: str
    signal_ts: str          # ISO — when the decision was made
    data_cutoff: str        # ISO — last data timestamp used (no look-ahead past this)
    symbol: str
    target_weight: float    # fraction of the SLEEVE's budget capital, [0, 1] (long-only v1)
    horizon: str            # "weekly" | "monthly" | ...
    rebalance_deadline: str = ""     # ISO — act by this time or the intent expires
    reason_codes: tuple = ()         # e.g. ("trend_positive", "vol_ok")
    features: dict = field(default_factory=dict)  # small snapshot for audit/reporting
    confidence: float = 0.0          # REPORTING ONLY — never used for sizing
    invalidation: str = ""           # human-readable invalidation condition
    expiry: str = ""                 # ISO — hard expiry

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("intent.strategy_id required")
        if not self.symbol:
            raise ValueError("intent.symbol required")
        if not (0.0 <= self.target_weight <= 1.0):
            raise ValueError(f"target_weight must be in [0,1], got {self.target_weight}")
        if not self.config_hash:
            raise ValueError("intent.config_hash required (traceability)")


def validate_intent_set(intents: list[StrategyIntent]) -> None:
    """A sleeve's intents for one rebalance must be internally consistent:
    one symbol at most once, and total target weight <= 1 (remainder = cash)."""
    if not intents:
        return
    sid = intents[0].strategy_id
    seen: set[str] = set()
    total = 0.0
    for it in intents:
        if it.strategy_id != sid:
            raise ValueError("intent set mixes strategies")
        if it.symbol in seen:
            raise ValueError(f"duplicate symbol {it.symbol} in intent set")
        seen.add(it.symbol)
        total += it.target_weight
    if total > 1.0 + 1e-9:
        raise ValueError(f"intent set weights sum to {total:.4f} > 1.0")
