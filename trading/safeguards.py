"""
Operational safeguards for paper/live entries.

Pure, testable predicates plus a small persisted dedupe store. The engine calls
`entry_blocks()` before opening anything and refuses to enter while any hard
condition is active, surfacing each as a dashboard warning. `DedupeStore`
prevents the same signal from firing twice across a process restart (the
in-memory acted-key set is empty after a restart, so without this a
still-valid signal could be re-submitted).
"""
from __future__ import annotations

import json
import os
from datetime import date


def data_is_stale(age_seconds: float | None, max_age_seconds: float) -> bool:
    return age_seconds is not None and age_seconds > max_age_seconds


def entry_blocks(
    *,
    data_age_seconds: float | None,
    max_age_seconds: float,
    unmanaged_shares: dict,
    block_on_shares: bool,
    recon_mismatch: bool,
    block_on_recon: bool,
    inflight_orders: set,
) -> dict[str, str]:
    """Return {block_name: human reason} for every active hard block. An empty
    dict means entries are allowed."""
    blocks: dict[str, str] = {}
    if data_is_stale(data_age_seconds, max_age_seconds):
        blocks["stale_data"] = (
            f"market data {data_age_seconds:.0f}s old (> {max_age_seconds:.0f}s) — quotes may be wrong"
        )
    if block_on_shares and unmanaged_shares:
        blocks["unmanaged_shares"] = (
            f"{len(unmanaged_shares)} unmanaged share position(s) at broker — resolve before trading"
        )
    if block_on_recon and recon_mismatch:
        blocks["reconciliation"] = "book/broker mismatch detected — awaiting re-sync"
    if inflight_orders:
        blocks["inflight_orders"] = f"{len(inflight_orders)} order(s) awaiting fill confirmation"
    return blocks


class DedupeStore:
    """Persisted set of signal keys acted on TODAY, so a restart mid-session
    does not re-fire a signal already traded. Only the current day is retained."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._day = date.today().isoformat()
        self._keys: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path) as f:
                raw = json.load(f)
            if raw.get("day") == self._day:
                self._keys = set(raw.get("keys", []))
        except (OSError, ValueError, TypeError):
            pass

    def _roll_day(self) -> None:
        today = date.today().isoformat()
        if today != self._day:
            self._day, self._keys = today, set()

    def __contains__(self, key: str) -> bool:
        self._roll_day()
        return key in self._keys

    def add(self, key: str) -> None:
        self._roll_day()
        self._keys.add(key)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump({"day": self._day, "keys": sorted(self._keys)}, f)
        except OSError:
            pass
