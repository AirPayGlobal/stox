"""
Portfolio rebalance orchestrator (blueprint §6, §7.3).

One function runs a rebalance end to end and is shared by the backtest and the
paper engine so their behaviour is identical:

    intents -> allocator (net targets, caps, bands)
            -> risk engine (system/portfolio/sleeve/position)
            -> execution (scenario fills, partial/unfilled, delay)
            -> deterministic attribution back to sleeve books

Strategies are never trusted to place orders; nothing here enables a strategy or
runs an optimization. `sleeve_tradeable` defaults to the strategy registry, so a
strategy that has not passed validation is paused by the risk engine — callers
may override only for testing the plumbing.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from portfolio import allocator as alloc
from portfolio.execution import simulate_equity_fill
from portfolio.risk import RiskContext, RiskDecision, evaluate


def intent_batch_key(profile_id: str, intents_by_sleeve: dict) -> str:
    """Stable key for one rebalance's intent batch (restart-safe dedupe)."""
    parts = [profile_id]
    for sid in sorted(intents_by_sleeve):
        for it in sorted(intents_by_sleeve[sid], key=lambda x: x.symbol):
            parts.append(f"{sid}|{it.symbol}|{it.target_weight}|{it.signal_ts}|{it.config_hash}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


@dataclass
class RebalanceResult:
    batch_key: str = ""
    halt: dict = field(default_factory=dict)
    paused_sleeves: dict = field(default_factory=dict)
    rejected_orders: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)      # dicts: symbol, side, qty, price, outcome
    net_positions: dict = field(default_factory=dict)

    @property
    def halted(self) -> bool:
        return bool(self.halt)


def _sleeve_tradeable_map(sids) -> dict:
    from strategy.registry import is_tradeable
    return {sid: is_tradeable(sid) for sid in sids}


def rebalance(profile, book, intents_by_sleeve: dict, prices: dict, *,
              scenario: str = "base", data_age_seconds: float | None = None,
              portfolio_drawdown: float = 0.0, sleeve_drawdown: dict | None = None,
              sleeve_paused: dict | None = None, sleeve_tradeable: dict | None = None,
              inflight_orders: set | None = None, recon_mismatch: bool = False,
              processed_keys: set | None = None, ts: str = "") -> RebalanceResult:
    """Execute one rebalance for `book` (a PortfolioBook) under `profile`."""
    batch_key = intent_batch_key(profile.id, intents_by_sleeve)
    res = RebalanceResult(batch_key=batch_key)

    duplicate = bool(processed_keys) and batch_key in processed_keys

    orders = alloc.plan(profile, book.sleeves, intents_by_sleeve, prices)

    sids = {sid for o in orders for sid in o.sleeve_deltas} | set(intents_by_sleeve)
    tradeable = sleeve_tradeable if sleeve_tradeable is not None else _sleeve_tradeable_map(sids)
    ctx = RiskContext(
        profile=profile, prices=prices, data_age_seconds=data_age_seconds,
        inflight_orders=inflight_orders or set(), recon_mismatch=recon_mismatch,
        duplicate_intent=duplicate, portfolio_drawdown=portfolio_drawdown,
        sleeve_paused=sleeve_paused or {}, sleeve_tradeable=tradeable,
        sleeve_drawdown=sleeve_drawdown or {},
    )
    decision: RiskDecision = evaluate(orders, ctx)
    res.halt = decision.halt
    res.paused_sleeves = decision.paused_sleeves
    res.rejected_orders = decision.rejected_orders
    if decision.halted:
        res.net_positions = book.net_positions()
        return res

    for o in decision.allowed_orders:
        side = "BUY" if o.net_delta > 0 else "SELL"
        key = f"{profile.id}|{o.symbol}|{ts}|{side}"
        fill = simulate_equity_fill(
            side, o.ref_price, abs(o.net_delta), scenario, key,
            fractional=profile.fractional, min_notional=profile.min_order_notional,
        )
        if fill.outcome == "UNFILLED":
            res.rejected_orders.setdefault(o.symbol, "unfilled")
            continue
        signed_qty = fill.qty if side == "BUY" else -fill.qty
        book.apply_attributed(o.symbol, o.sleeve_deltas, signed_qty, fill.price, total_fee=0.0)
        res.fills.append({
            "symbol": o.symbol, "side": side, "qty": fill.qty, "price": fill.price,
            "outcome": fill.outcome, "friction_bps": fill.friction_bps,
        })

    if processed_keys is not None:
        processed_keys.add(batch_key)
    res.net_positions = book.net_positions()
    return res
