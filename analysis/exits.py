"""
Premium exit management, shared by the live engine and the backtester so
both apply identical logic.

Two optional mechanics, both keyed off the trade's peak favorable mark
(MFE) and both default-off:

  * break-even : once MFE reaches entry * (1 + BREAKEVEN_TRIGGER), the stop
    is raised to the entry price — a faded winner scratches instead of
    riding down to the fixed stop.
  * trailing   : once MFE reaches entry * (1 + TRAIL_TRIGGER), the stop
    trails TRAIL_PCT below the peak. While trailing is enabled the fixed
    take-profit is removed so winners can run past it.
"""
from __future__ import annotations

from config import Config


def effective_stop(entry: float, base_stop: float, mfe: float) -> float:
    """The stop actually in force, given break-even and trailing management.
    `mfe` is the highest mark seen so far. Never lowers the base stop."""
    stop = base_stop
    if Config.ORB_BREAKEVEN_TRIGGER_PCT > 0:
        if mfe >= entry * (1 + Config.ORB_BREAKEVEN_TRIGGER_PCT):
            stop = max(stop, entry)
    if Config.ORB_TRAIL_TRIGGER_PCT > 0:
        if mfe >= entry * (1 + Config.ORB_TRAIL_TRIGGER_PCT):
            stop = max(stop, mfe * (1 - Config.ORB_TRAIL_PCT))
    return stop


def fixed_target_active() -> bool:
    """The fixed take-profit is disabled while trailing is enabled, so the
    trail (not a fixed cap) governs the upside."""
    return Config.ORB_TRAIL_TRIGGER_PCT <= 0
