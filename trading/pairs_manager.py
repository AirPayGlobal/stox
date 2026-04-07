"""
Pairs Position Manager
=======================
Persists open pair positions to data/pairs_positions.json and provides
helpers for opening, closing, and querying them.

Each pair position tracks both legs (long + short) as a single unit.
P&L = long_leg_pnl + short_leg_pnl.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

import os

_STORE = Path(
    os.environ.get(
        "PAIRS_FILE",
        str(Path(__file__).parent.parent / "data" / "pairs_positions.json"),
    )
)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:
            pass
    return []


def _save(positions: list[dict]) -> None:
    _STORE.write_text(json.dumps(positions, indent=2, default=str))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_open_pairs() -> list[dict]:
    """Return all currently open pair positions."""
    return [p for p in _load() if p["status"] == "open"]


def get_all_pairs(limit: int = 50) -> list[dict]:
    """Return all pair positions (open + closed), newest first."""
    return list(reversed(_load()))[:limit]


def open_pair(
    symbol_a: str,
    symbol_b: str,
    direction: str,          # "LONG_A_SHORT_B" or "LONG_B_SHORT_A"
    symbol_long: str,
    symbol_short: str,
    qty_long: int,
    qty_short: int,
    price_long: float,
    price_short: float,
    hedge_ratio: float,
    z_score: float,
    order_long_id: str = "",
    order_short_id: str = "",
) -> dict:
    positions = _load()
    pair_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "pair_id":       pair_id,
        "symbol_a":      symbol_a,
        "symbol_b":      symbol_b,
        "direction":     direction,
        "symbol_long":   symbol_long,
        "symbol_short":  symbol_short,
        "qty_long":      qty_long,
        "qty_short":     qty_short,
        "price_long":    price_long,
        "price_short":   price_short,
        "hedge_ratio":   hedge_ratio,
        "entry_z":       z_score,
        "order_long_id":  order_long_id,
        "order_short_id": order_short_id,
        "opened_at":     now,
        "closed_at":     None,
        "exit_z":        None,
        "pnl":           None,
        "pnl_pct":       None,
        "status":        "open",
        "close_reason":  None,
    }

    positions.append(entry)
    _save(positions)

    logger.info(
        f"Pair opened [{pair_id}]: LONG {qty_long}×{symbol_long} @ ${price_long:.2f} "
        f"/ SHORT {qty_short}×{symbol_short} @ ${price_short:.2f} "
        f"| z={z_score:.2f} β={hedge_ratio:.3f}"
    )
    return entry


def close_pair(
    pair_id: str,
    price_long_exit: float,
    price_short_exit: float,
    exit_z: float,
    reason: str = "MEAN_REVERSION",
) -> Optional[dict]:
    positions = _load()
    for p in positions:
        if p["pair_id"] == pair_id and p["status"] == "open":
            long_pnl  = (price_long_exit  - p["price_long"])  * p["qty_long"]
            short_pnl = (p["price_short"] - price_short_exit) * p["qty_short"]
            total_pnl = long_pnl + short_pnl

            cost_basis = p["price_long"] * p["qty_long"] + p["price_short"] * p["qty_short"]
            pnl_pct = total_pnl / cost_basis if cost_basis else 0.0

            p.update({
                "status":       "closed",
                "closed_at":    datetime.now(timezone.utc).isoformat(),
                "exit_z":       exit_z,
                "pnl":          round(total_pnl, 2),
                "pnl_pct":      round(pnl_pct, 4),
                "close_reason": reason,
            })
            _save(positions)

            logger.info(
                f"Pair closed [{pair_id}]: PnL=${total_pnl:.2f} ({pnl_pct:.2%}) "
                f"z={exit_z:.2f} [{reason}]"
            )
            return p

    logger.warning(f"Pair {pair_id} not found or already closed")
    return None


def pairs_summary() -> dict:
    """Return aggregate performance stats for all closed pairs."""
    all_pairs = _load()
    closed = [p for p in all_pairs if p["status"] == "closed" and p["pnl"] is not None]
    open_  = [p for p in all_pairs if p["status"] == "open"]

    winners = [p for p in closed if p["pnl"] > 0]
    losers  = [p for p in closed if p["pnl"] <= 0]
    total_pnl = sum(p["pnl"] for p in closed)
    win_rate  = len(winners) / len(closed) if closed else 0.0

    return {
        "open_pairs":    len(open_),
        "closed_pairs":  len(closed),
        "win_rate":      win_rate,
        "total_pnl":     total_pnl,
        "avg_win":       sum(p["pnl"] for p in winners) / len(winners) if winners else 0.0,
        "avg_loss":      sum(p["pnl"] for p in losers)  / len(losers)  if losers  else 0.0,
    }
