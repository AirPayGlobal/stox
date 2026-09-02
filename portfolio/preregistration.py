"""
Pre-registration artifact (blueprint §7.1).

Freezes the research plan BEFORE any result is viewed: hypothesis, universe and
eligibility rules, feature/parameter definitions, entry/exit/rebalance rules,
cost assumptions, capital-profile rules, metrics and pass/fail thresholds,
dataset fingerprint and test-period boundaries, and the number of prior trials
in the strategy family. The artifact is content-hashed so any later change is
detectable — an edited plan is a new registration, not a silent tweak.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class DatasetFingerprint:
    source: str                 # e.g. "alpaca"
    symbols: tuple
    start: str                  # ISO date
    end: str                    # ISO date
    adjustment: str             # e.g. "all"
    bar_count: int = 0

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class PreRegistration:
    strategy_id: str
    version: str
    hypothesis: str
    rationale: str
    universe: tuple
    eligibility_rule: str
    features: dict                       # name -> definition
    parameters: dict                     # name -> frozen value
    entry_exit_rebalance: str
    cost_assumptions: dict               # scenario -> description
    capital_profiles: tuple              # profile ids the plan covers
    metrics: tuple                       # metric names reported
    gates: tuple                         # pass/fail gate descriptions
    test_boundaries: dict                # e.g. {"train":"...", "oos_folds":"...", "holdout":"..."}
    prior_trials: int                    # trials in this family before this one
    dataset: DatasetFingerprint | None = None
    registered_at: str = ""

    def config_hash(self) -> str:
        """Stable hash over the frozen plan (dataset fingerprint included)."""
        payload = asdict(self)
        payload.pop("registered_at", None)   # timestamp is metadata, not plan
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]

    def to_json(self) -> str:
        d = asdict(self)
        d["config_hash"] = self.config_hash()
        if self.dataset is not None:
            d["dataset_digest"] = self.dataset.digest()
        return json.dumps(d, indent=2, sort_keys=True, default=str)
