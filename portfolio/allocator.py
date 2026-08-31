"""
Central portfolio allocator (blueprint §6.3).

Turns each sleeve's target intents into ONE account-level order per symbol,
enforcing the capital profile's rules. It does NOT pick winners or size by recent
P&L — sleeve budgets are fixed; when caps bind, targets scale down in proportion
to the request (preserving budget ratios). This deliberately avoids a
return-maximizing selector (which would add a second overfitting problem).

Enforced, in order:
  1. per-symbol account weight cap
  2. gross exposure cap AND cash reserve (whichever binds)
  3. max positions (keep the largest targets, drop the rest to cash)
  4. per-sleeve target shares -> deltas vs current holdings -> net per symbol
  5. minimum-order-notional band on the NET trade (suppress churn)
  6. fractional / whole-share rounding
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AccountOrder:
    symbol: str
    net_delta: float            # net shares to trade at the account level (+buy / -sell)
    ref_price: float
    sleeve_deltas: dict         # strategy_id -> requested share delta (for attribution)


def _round(shares: float, fractional: bool) -> float:
    return round(shares, 6) if fractional else float(int(shares))


def plan(profile, sleeve_books: dict, intents_by_sleeve: dict, prices: dict) -> list[AccountOrder]:
    """Return the account orders to execute this rebalance."""
    cap = profile.capital

    # 1. target notional per (sleeve, symbol) from intents (permitted sleeves only).
    tgt: dict[tuple, float] = {}
    for sid, intents in intents_by_sleeve.items():
        if not profile.permits(sid):
            continue
        budget = profile.budget_capital(sid)
        for it in intents:
            if it.symbol not in prices:
                continue
            tgt[(sid, it.symbol)] = tgt.get((sid, it.symbol), 0.0) + it.target_weight * budget

    # Symbols currently held but no longer targeted -> implicit target 0 (sell).
    for sid, sb in sleeve_books.items():
        for sym in sb.positions:
            tgt.setdefault((sid, sym), 0.0)

    # 2. per-symbol account weight cap.
    sym_total: dict[str, float] = {}
    for (sid, sym), notional in tgt.items():
        sym_total[sym] = sym_total.get(sym, 0.0) + notional
    sym_cap = cap * profile.max_symbol_weight
    for sym, total in sym_total.items():
        if total > sym_cap and total > 0:
            scale = sym_cap / total
            for key in list(tgt):
                if key[1] == sym:
                    tgt[key] *= scale
            sym_total[sym] = sym_cap

    # 3. gross exposure + cash reserve (whichever is tighter).
    allowed = cap * min(profile.max_gross_exposure, 1.0 - profile.cash_reserve_pct)
    gross = sum(v for v in sym_total.values() if v > 0)
    if gross > allowed and gross > 0:
        scale = allowed / gross
        for key in tgt:
            tgt[key] *= scale
        sym_total = {s: v * scale for s, v in sym_total.items()}

    # 4. max positions: keep the largest target symbols, zero the rest.
    held_or_targeted = [s for s, v in sym_total.items() if v > 0]
    if len(held_or_targeted) > profile.max_positions:
        keep = set(sorted(held_or_targeted, key=lambda s: sym_total[s], reverse=True)
                   [: profile.max_positions])
        for key in list(tgt):
            if key[1] not in keep:
                tgt[key] = 0.0

    # 5-6. deltas vs current holdings -> net per symbol; band + rounding.
    current: dict[tuple, float] = {}
    for sid, sb in sleeve_books.items():
        for sym, pos in sb.positions.items():
            current[(sid, sym)] = pos.shares

    net_by_symbol: dict[str, dict] = {}
    for (sid, sym), notional in tgt.items():
        price = prices.get(sym)
        if not price:
            continue
        target_shares = _round(notional / price, profile.fractional)
        cur = current.get((sid, sym), 0.0)
        delta = _round(target_shares - cur, profile.fractional)
        if abs(delta) < 1e-9:
            continue
        net_by_symbol.setdefault(sym, {})[sid] = delta

    orders: list[AccountOrder] = []
    for sym, sleeve_deltas in net_by_symbol.items():
        net = _round(sum(sleeve_deltas.values()), profile.fractional)
        price = prices[sym]
        if abs(net * price) < profile.min_order_notional:
            continue                      # target-change band: skip churn
        orders.append(AccountOrder(sym, net, price, sleeve_deltas))
    return orders
