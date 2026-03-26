"""
Human-in-the-loop approval queue for IPO trades.

Flow
----
1. Bot detects IPO opportunity → calls submit() instead of placing order
2. Entry saved to data/pending_approvals.json with 60-min expiry
3. Dashboard polls /api/pending-trades and shows Accept / Decline buttons
4. On approve  → execute_pending() places the bracket order
5. On decline  → entry marked declined, skipped forever
6. On timeout  → main loop calls auto_execute_expired() which places the order
               automatically (same as if user clicked Approve)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

_STORE = Path(__file__).parent.parent / "data" / "pending_approvals.json"
_APPROVAL_WINDOW_MINUTES = 60


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load() -> dict[str, dict]:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    _STORE.write_text(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def submit(
    symbol: str,
    shares: int,
    price: float,
    stop_loss: float,
    take_profit: float,
    score: int,
    headline: str = "",
    trade_type: str = "IPO",
) -> str:
    """
    Add a trade to the pending approval queue.
    Returns the approval ID.
    """
    queue = _load()

    # Skip if already pending for this symbol
    for entry in queue.values():
        if entry["symbol"] == symbol and entry["status"] == "pending":
            logger.info(f"Approval already pending for {symbol}, skipping duplicate")
            return entry["id"]

    now = datetime.now(timezone.utc)
    approval_id = str(uuid.uuid4())[:8]

    queue[approval_id] = {
        "id": approval_id,
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "score": score,
        "headline": headline,
        "trade_type": trade_type,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=_APPROVAL_WINDOW_MINUTES)).isoformat(),
        "status": "pending",   # pending | approved | declined | auto_executed | expired
    }

    _save(queue)
    logger.info(
        f"IPO trade queued for approval: {symbol} x{shares} @ ${price:.2f} "
        f"[id={approval_id}] — auto-executes in {_APPROVAL_WINDOW_MINUTES}min"
    )
    return approval_id


def get_pending() -> list[dict]:
    """Return all pending (not yet decided or expired) approval entries."""
    queue = _load()
    now = datetime.now(timezone.utc)
    pending = []
    for entry in queue.values():
        if entry["status"] != "pending":
            continue
        expires = datetime.fromisoformat(entry["expires_at"])
        minutes_left = max(0, int((expires - now).total_seconds() / 60))
        pending.append({**entry, "minutes_left": minutes_left})
    pending.sort(key=lambda x: x["created_at"])
    return pending


def approve(approval_id: str) -> Optional[dict]:
    """Mark an entry as approved. Returns the entry dict to execute, or None."""
    queue = _load()
    if approval_id not in queue:
        return None
    entry = queue[approval_id]
    if entry["status"] != "pending":
        return None
    entry["status"] = "approved"
    entry["decided_at"] = datetime.now(timezone.utc).isoformat()
    _save(queue)
    logger.info(f"Trade approved by user: {entry['symbol']} [id={approval_id}]")
    return entry


def decline(approval_id: str) -> bool:
    """Mark an entry as declined."""
    queue = _load()
    if approval_id not in queue:
        return False
    queue[approval_id]["status"] = "declined"
    queue[approval_id]["decided_at"] = datetime.now(timezone.utc).isoformat()
    _save(queue)
    logger.info(f"Trade declined by user: {queue[approval_id]['symbol']} [id={approval_id}]")
    return True


def get_expired() -> list[dict]:
    """Return entries that have passed their expiry and are still pending."""
    queue = _load()
    now = datetime.now(timezone.utc)
    expired = []
    for entry in queue.values():
        if entry["status"] != "pending":
            continue
        if datetime.fromisoformat(entry["expires_at"]) <= now:
            expired.append(entry)
    return expired


def mark_executed(approval_id: str, auto: bool = False) -> None:
    queue = _load()
    if approval_id in queue:
        queue[approval_id]["status"] = "auto_executed" if auto else "approved"
        queue[approval_id]["executed_at"] = datetime.now(timezone.utc).isoformat()
        _save(queue)
