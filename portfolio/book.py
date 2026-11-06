"""
Virtual sleeve books + deterministic fill/P&L attribution (blueprint §6.2).

Each strategy sleeve keeps a *virtual* position and cash book as if it traded
its own budget alone. The allocator nets sleeve targets into one account order
per symbol (so two sleeves never trade against each other externally), execution
fills it once, and the fill is attributed back to the sleeves deterministically.

Attribution rule (documented and testable):
  * net = sum of sleeves' requested share deltas for the symbol.
  * If net != 0: each sleeve gets `delta_i * filled_qty / net`. This conserves
    shares (Σ attributed = filled_qty) and automatically crosses opposite-sign
    sleeves internally (a seller sleeve's negative delta nets against buyers).
  * If net == 0 (fully offsetting): sleeves cross internally at the reference
    price — each gets its full requested delta, no external fill needed.
  * Fees are split in proportion to |attributed shares|.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class Position:
    shares: float = 0.0
    avg_cost: float = 0.0


@dataclass
class SleeveBook:
    strategy_id: str
    budget_capital: float
    cash: float = 0.0
    positions: dict = field(default_factory=dict)   # symbol -> Position
    realized_pnl: float = 0.0
    fees_paid: float = 0.0

    def __post_init__(self) -> None:
        if not self.cash and not self.positions:
            self.cash = self.budget_capital

    def apply_fill(self, symbol: str, qty: float, price: float, fee: float = 0.0) -> None:
        """qty > 0 buy, qty < 0 sell. Updates cash, position, realized P&L."""
        pos = self.positions.get(symbol, Position())
        if qty >= 0:
            new_shares = pos.shares + qty
            pos.avg_cost = ((pos.shares * pos.avg_cost) + qty * price) / new_shares if new_shares else 0.0
            pos.shares = new_shares
            self.cash -= qty * price + fee
        else:
            sold = -qty
            self.realized_pnl += sold * (price - pos.avg_cost)
            pos.shares -= sold
            self.cash += sold * price - fee
            if abs(pos.shares) < 1e-9:
                pos.shares, pos.avg_cost = 0.0, 0.0
        self.fees_paid += fee
        if abs(pos.shares) < 1e-9:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = pos

    def market_value(self, prices: dict) -> float:
        return self.cash + sum(p.shares * prices.get(s, p.avg_cost) for s, p in self.positions.items())

    def invested(self, prices: dict) -> float:
        return sum(abs(p.shares) * prices.get(s, p.avg_cost) for s, p in self.positions.items())

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "budget_capital": self.budget_capital,
            "cash": self.cash, "realized_pnl": self.realized_pnl, "fees_paid": self.fees_paid,
            "positions": {s: {"shares": p.shares, "avg_cost": p.avg_cost}
                          for s, p in self.positions.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SleeveBook":
        sb = cls(d["strategy_id"], d["budget_capital"], d["cash"],
                 {s: Position(**p) for s, p in d.get("positions", {}).items()},
                 d.get("realized_pnl", 0.0), d.get("fees_paid", 0.0))
        return sb


def attribute(deltas: dict, filled_qty: float, ref_price: float) -> dict:
    """Split an executed net fill back to sleeves by requested delta.
    `deltas` = {strategy_id: requested_share_delta}. Returns {strategy_id: qty}."""
    net = sum(deltas.values())
    if abs(net) < 1e-12:
        # Fully offsetting -> internal cross at ref price; each sleeve fully filled.
        return {sid: d for sid, d in deltas.items() if abs(d) > 1e-12}
    scale = filled_qty / net
    return {sid: d * scale for sid, d in deltas.items() if abs(d) > 1e-12}


class PortfolioBook:
    """All sleeve books for one capital profile, plus persistence for restart
    recovery. The 'real account' position in any symbol is the sum across
    sleeves (netting is what the allocator executes)."""

    def __init__(self, profile_id: str, sleeve_capital: dict | None = None,
                 path: str | None = None) -> None:
        self.profile_id = profile_id
        self.path = path
        self.sleeves: dict[str, SleeveBook] = {}
        if sleeve_capital:
            for sid, cap in sleeve_capital.items():
                self.sleeves[sid] = SleeveBook(sid, cap)
        if path and os.path.exists(path):
            self._load()

    def net_positions(self) -> dict:
        out: dict[str, float] = {}
        for sb in self.sleeves.values():
            for sym, p in sb.positions.items():
                out[sym] = out.get(sym, 0.0) + p.shares
        return {s: q for s, q in out.items() if abs(q) > 1e-9}

    def apply_attributed(self, symbol: str, deltas: dict, filled_qty: float,
                         price: float, total_fee: float = 0.0) -> dict:
        """Attribute one symbol's net fill to sleeves and update their books.
        Returns the per-sleeve attributed quantities."""
        attributed = attribute(deltas, filled_qty, price)
        gross = sum(abs(q) for q in attributed.values()) or 1.0
        for sid, qty in attributed.items():
            fee = total_fee * abs(qty) / gross
            self.sleeves[sid].apply_fill(symbol, qty, price, fee)
        if self.path:
            self._save()
        return attributed

    def market_value(self, prices: dict) -> float:
        return sum(sb.market_value(prices) for sb in self.sleeves.values())

    # ---- persistence (restart recovery) ----
    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump({"profile_id": self.profile_id,
                           "sleeves": {s: sb.to_dict() for s, sb in self.sleeves.items()}}, f, indent=2)
        except OSError:
            pass

    def _load(self) -> None:
        try:
            with open(self.path) as f:
                raw = json.load(f)
            self.profile_id = raw.get("profile_id", self.profile_id)
            self.sleeves = {s: SleeveBook.from_dict(d) for s, d in raw.get("sleeves", {}).items()}
        except (OSError, ValueError, TypeError):
            pass
