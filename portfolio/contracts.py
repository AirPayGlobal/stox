"""
UI/API contract builders for the multi-strategy stage (blueprint §8).

These assemble the JSON shapes the four institutional views consume — Command
Centre, Strategy Registry, Portfolio Lab, Research Leaderboard — from what
actually exists today (the strategy registry and the capital profiles). No
strategy has passed validation, so live portfolio sections are honestly empty
placeholders rather than invented numbers. Everything is permanently PAPER.
"""
from __future__ import annotations

from portfolio.profiles import PROFILE_IDS, PROFILES, get_profile
from strategy.registry import as_dicts


def _profile_dict(p) -> dict:
    return {
        "id": p.id, "capital": p.capital,
        "sleeve_budgets": dict(p.sleeve_budgets),
        "cash_reserve_pct": p.cash_reserve_pct,
        "max_symbol_weight": p.max_symbol_weight,
        "max_gross_exposure": p.max_gross_exposure,
        "max_positions": p.max_positions,
        "min_order_notional": p.min_order_notional,
        "fractional": p.fractional,
        "allowed_asset_classes": sorted(p.allowed_asset_classes),
    }


def profiles_contract() -> dict:
    return {"profiles": [_profile_dict(PROFILES[pid]) for pid in PROFILE_IDS]}


def command_centre(profile_id: str = "paper_10000", book=None, prices=None) -> dict:
    """Portfolio Command Centre (§8). `book` is an optional live PortfolioBook."""
    p = get_profile(profile_id)
    strategies = as_dicts()
    by_state: dict[str, list] = {}
    for s in strategies:
        by_state.setdefault(s["lifecycle"], []).append(s["id"])

    account = {"market_value": None, "cash": None, "gross_exposure": None,
               "net_positions": {}, "sleeve_attribution": {}}
    if book is not None:
        prices = prices or {}
        account["market_value"] = round(book.market_value(prices), 2)
        account["net_positions"] = book.net_positions()
        account["sleeve_attribution"] = {
            sid: {"market_value": round(sb.market_value(prices), 2),
                  "realized_pnl": round(sb.realized_pnl, 2),
                  "cash": round(sb.cash, 2)}
            for sid, sb in book.sleeves.items()
        }
    return {
        "state": "PAPER",
        "profile": _profile_dict(p),
        "sleeves_by_lifecycle": by_state,
        "tradeable_sleeves": [s["id"] for s in strategies if s["tradeable"]],
        "account": account,
        "blocks": [],                 # populated live by the risk engine
        "note": ("No active portfolio session — no strategy has passed validation, "
                 "so nothing is tradeable yet."),
    }


def strategy_registry_contract() -> dict:
    """Strategy Registry (§8): lifecycle cards."""
    order = {"PAPER": 0, "OOS": 1, "BACKTEST": 2, "REGISTERED": 3,
             "HYPOTHESIS": 4, "PAUSED": 5, "RETIRED": 6}
    cards = sorted(as_dicts(), key=lambda s: (order.get(s["lifecycle"], 9), s["id"]))
    return {"strategies": cards}


def portfolio_lab_contract() -> dict:
    """Portfolio Lab (§8): compare the four capital profiles + sleeve budgets."""
    return {
        "profiles": [_profile_dict(PROFILES[pid]) for pid in PROFILE_IDS],
        "sleeve_risk_contribution": None,   # filled once sleeves run
        "correlation_matrix": None,
        "overlap_by_symbol": None,
        "note": "Risk-contribution / correlation populate once sleeves produce paper history.",
    }


def research_leaderboard_contract() -> dict:
    """Research Leaderboard (§8): default sort by robustness, NOT raw P&L."""
    rows = []
    for s in as_dicts():
        rows.append({
            "id": s["id"], "lifecycle": s["lifecycle"],
            "asset_class": s["asset_class"], "cadence": s["cadence"],
            "data_ready": s["data_ready"],
            "evidence": s["evidence"],
            "status_reason": s["status_reason"],
            "robustness_score": None,        # computed once a validated result exists
            "median_fold": None, "worst_fold": None,
            "cost_survival": None, "max_drawdown": None, "tail_concentration": None,
            "data_warnings": ([] if s["data_ready"] == "yes"
                              else ["data blocked/partial — see DATA_AVAILABILITY_AUDIT"]),
        })
    # Retired first is not the point; sort by data readiness then id (stable, honest).
    rows.sort(key=lambda r: (r["lifecycle"] != "PAPER", r["id"]))
    return {"default_sort": "robustness_score", "rows": rows,
            "note": "Raw total P&L is never the default ranking (§8)."}
