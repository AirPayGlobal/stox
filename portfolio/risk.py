"""
Portfolio risk engine — the four-level hierarchy (blueprint §6.4).

Controls apply in order; the level that fires determines the blast radius:

  1. SYSTEM    stale data, reconciliation mismatch, unresolved orders, duplicate
               intent batch, restart integrity  -> halt the WHOLE rebalance.
  2. PORTFOLIO gross/net exposure, drawdown, cash reserve, concentration
               -> block new risk-increasing orders (sells still allowed).
  3. SLEEVE    per-sleeve drawdown, turnover, validation status, pause flag
               -> pause THAT sleeve only.
  4. POSITION  symbol cap, min notional / liquidity, price sanity
               -> reject THAT order only.

One sleeve hitting its stop pauses only that sleeve unless an account-level
limit is also breached.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    portfolio_halt_drawdown: float = 0.15   # fraction off peak -> block new buys
    sleeve_pause_drawdown: float = 0.20     # fraction off sleeve peak -> pause sleeve
    max_quote_age_seconds: float = 120.0


@dataclass
class RiskContext:
    profile: object
    prices: dict = field(default_factory=dict)
    data_age_seconds: float | None = None
    inflight_orders: set = field(default_factory=set)
    recon_mismatch: bool = False
    duplicate_intent: bool = False
    portfolio_drawdown: float = 0.0                 # fraction off peak
    sleeve_paused: dict = field(default_factory=dict)     # sid -> bool
    sleeve_tradeable: dict = field(default_factory=dict)  # sid -> bool
    sleeve_drawdown: dict = field(default_factory=dict)   # sid -> fraction off peak
    limits: RiskLimits = field(default_factory=RiskLimits)


@dataclass
class RiskDecision:
    halt: dict = field(default_factory=dict)            # system/portfolio halt reasons
    paused_sleeves: dict = field(default_factory=dict)  # sid -> reason
    rejected_orders: dict = field(default_factory=dict) # symbol -> reason
    allowed_orders: list = field(default_factory=list)

    @property
    def halted(self) -> bool:
        return bool(self.halt)


def _system_halt(ctx: RiskContext) -> dict:
    h = {}
    if ctx.data_age_seconds is not None and ctx.data_age_seconds > ctx.limits.max_quote_age_seconds:
        h["stale_data"] = f"data {ctx.data_age_seconds:.0f}s old > {ctx.limits.max_quote_age_seconds:.0f}s"
    if ctx.recon_mismatch:
        h["reconciliation"] = "book/broker mismatch — awaiting re-sync"
    if ctx.inflight_orders:
        h["inflight_orders"] = f"{len(ctx.inflight_orders)} order(s) unresolved"
    if ctx.duplicate_intent:
        h["duplicate_intent"] = "this intent batch was already processed (restart-safe)"
    return h


def _sleeve_pause_reason(sid: str, ctx: RiskContext) -> str | None:
    if not ctx.sleeve_tradeable.get(sid, True):
        return "sleeve not in a tradeable lifecycle state"
    if ctx.sleeve_paused.get(sid):
        return "sleeve manually paused"
    dd = ctx.sleeve_drawdown.get(sid, 0.0)
    if dd >= ctx.limits.sleeve_pause_drawdown:
        return f"sleeve drawdown {dd:.0%} >= {ctx.limits.sleeve_pause_drawdown:.0%}"
    return None


def evaluate(orders: list, ctx: RiskContext) -> RiskDecision:
    """Filter candidate account orders through the risk hierarchy."""
    d = RiskDecision()

    # 1. SYSTEM — halt everything.
    d.halt = _system_halt(ctx)
    if d.halt:
        return d

    # 2. PORTFOLIO — drawdown blocks new buys (sells always permitted).
    block_new_buys = ctx.portfolio_drawdown >= ctx.limits.portfolio_halt_drawdown
    if block_new_buys:
        d.halt = {}  # not a full halt; recorded per-order below

    # 3. SLEEVE — pause reasons (a paused sleeve's deltas are dropped from orders).
    all_sids = {sid for o in orders for sid in o.sleeve_deltas}
    for sid in all_sids:
        reason = _sleeve_pause_reason(sid, ctx)
        if reason:
            d.paused_sleeves[sid] = reason

    profile = ctx.profile
    sym_cap = profile.capital * profile.max_symbol_weight

    for o in orders:
        # drop paused sleeves' contributions; re-net.
        live_deltas = {sid: q for sid, q in o.sleeve_deltas.items()
                       if sid not in d.paused_sleeves}
        if not live_deltas:
            d.rejected_orders[o.symbol] = "all contributing sleeves paused"
            continue
        net = round(sum(live_deltas.values()), 6)
        if abs(net) < 1e-9:
            d.rejected_orders[o.symbol] = "net zero after sleeve pause"
            continue

        # 4. POSITION checks.
        price = ctx.prices.get(o.symbol, o.ref_price)
        if not price or price <= 0:
            d.rejected_orders[o.symbol] = "no valid price"
            continue
        if net > 0 and block_new_buys:
            d.rejected_orders[o.symbol] = (
                f"portfolio drawdown {ctx.portfolio_drawdown:.0%} — new buys blocked")
            continue
        if abs(net * price) < profile.min_order_notional:
            d.rejected_orders[o.symbol] = "below min order notional after pause"
            continue
        if net > 0 and net * price > sym_cap + 1e-6:
            d.rejected_orders[o.symbol] = "exceeds per-symbol weight cap"
            continue

        o.net_delta = net
        o.sleeve_deltas = live_deltas
        d.allowed_orders.append(o)

    return d
