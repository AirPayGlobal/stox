"""
Immutable paper-capital profiles (blueprint §5, §6.5).

Each profile fixes the account size and the rules the allocator/risk engine
enforce: allowed asset classes, fractional rules, minimum order notional,
maximum positions, per-sleeve budgets, exposure caps, and cash reserve. Every
backtest runs all four profiles on the same signals with realistic capital
rounding, so small accounts reveal cost/rounding distortion.

These are PAPER profiles. No profile permits real money, options, or leverage
in v1. Sleeve budgets are fixed (no return-maximizing selector — §6.3); a sleeve
with no valid targets simply leaves its budget in cash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

# Strategies that can actually be built now (per docs/DATA_AVAILABILITY_AUDIT.md).
# QVM / PEAD / Pairs are data-blocked, so no budget is allocated to them yet.
TREND = "ETF_TREND_V1"
RELMOM = "ETF_RELATIVE_MOMENTUM_V1"


@dataclass(frozen=True)
class CapitalProfile:
    id: str
    capital: float
    allowed_asset_classes: frozenset
    fractional: bool
    min_order_notional: float
    max_positions: int
    sleeve_budgets: Mapping[str, float]   # strategy_id -> fixed budget weight
    max_symbol_weight: float              # cap on any single symbol / total capital
    max_gross_exposure: float             # cap on invested / total capital
    cash_reserve_pct: float               # always-held cash

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        budget_sum = sum(self.sleeve_budgets.values())
        if budget_sum > 1.0 - self.cash_reserve_pct + 1e-9:
            raise ValueError(
                f"{self.id}: sleeve budgets {budget_sum:.2f} + reserve "
                f"{self.cash_reserve_pct:.2f} exceed 1.0"
            )
        if not (0.0 < self.max_gross_exposure <= 1.0):
            raise ValueError("max_gross_exposure must be in (0,1]")
        # Freeze the mapping so a profile cannot be mutated at runtime.
        object.__setattr__(self, "sleeve_budgets", MappingProxyType(dict(self.sleeve_budgets)))

    def budget_capital(self, strategy_id: str) -> float:
        """Dollar budget for a sleeve (0 if it has no budget in this profile)."""
        return self.capital * self.sleeve_budgets.get(strategy_id, 0.0)

    def permits(self, strategy_id: str) -> bool:
        return strategy_id in self.sleeve_budgets


_PROFILES = [
    CapitalProfile(
        id="paper_500", capital=500.0, allowed_asset_classes=frozenset({"etf"}),
        fractional=True, min_order_notional=10.0, max_positions=3,
        sleeve_budgets={TREND: 0.60, RELMOM: 0.30},
        max_symbol_weight=0.35, max_gross_exposure=0.90, cash_reserve_pct=0.10,
    ),
    CapitalProfile(
        id="paper_2500", capital=2500.0, allowed_asset_classes=frozenset({"etf"}),
        fractional=True, min_order_notional=15.0, max_positions=6,
        sleeve_budgets={TREND: 0.50, RELMOM: 0.35},
        max_symbol_weight=0.35, max_gross_exposure=0.90, cash_reserve_pct=0.10,
    ),
    CapitalProfile(
        id="paper_10000", capital=10000.0, allowed_asset_classes=frozenset({"etf"}),
        fractional=True, min_order_notional=25.0, max_positions=12,
        # QVM budget (0.25) and PEAD (0.10) are data-blocked -> left as cash for now.
        sleeve_budgets={TREND: 0.35, RELMOM: 0.25},
        max_symbol_weight=0.25, max_gross_exposure=0.90, cash_reserve_pct=0.05,
    ),
    CapitalProfile(
        id="paper_50000", capital=50000.0, allowed_asset_classes=frozenset({"etf"}),
        fractional=True, min_order_notional=50.0, max_positions=25,
        sleeve_budgets={TREND: 0.30, RELMOM: 0.20},
        max_symbol_weight=0.25, max_gross_exposure=0.90, cash_reserve_pct=0.05,
    ),
]

PROFILES: dict[str, CapitalProfile] = {p.id: p for p in _PROFILES}
PROFILE_IDS = tuple(p.id for p in _PROFILES)


def get_profile(profile_id: str) -> CapitalProfile:
    if profile_id not in PROFILES:
        raise ValueError(f"unknown capital profile {profile_id!r}; choose {PROFILE_IDS}")
    return PROFILES[profile_id]
