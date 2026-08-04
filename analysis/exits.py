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


def managed_stop(
    entry: float,
    base_stop: float,
    mfe: float,
    breakeven_trigger: float,
    trail_trigger: float,
    trail_pct: float,
) -> float:
    """
    The stop actually in force given break-even and trailing management,
    parameterized so ORB and sweep can pass their own thresholds. `mfe` is
    the highest mark seen so far. Never lowers the base stop.
    """
    stop = base_stop
    if breakeven_trigger > 0 and mfe >= entry * (1 + breakeven_trigger):
        stop = max(stop, entry)
    if trail_trigger > 0 and mfe >= entry * (1 + trail_trigger):
        stop = max(stop, mfe * (1 - trail_pct))
    return stop


def effective_stop(entry: float, base_stop: float, mfe: float) -> float:
    """ORB premium stop with ORB-configured management."""
    return managed_stop(
        entry, base_stop, mfe,
        Config.ORB_BREAKEVEN_TRIGGER_PCT,
        Config.ORB_TRAIL_TRIGGER_PCT,
        Config.ORB_TRAIL_PCT,
    )


def sweep_trail_stop(entry: float, mfe: float) -> float | None:
    """
    Premium trailing stop for sweep trades — captures gamma spikes that
    would otherwise round-trip because the underlying target sits far away.
    Returns the trailing stop premium once armed, else None (disabled or
    not yet triggered). No break-even (sweep's underlying stop already cuts
    losers tight).
    """
    if Config.SWEEP_TRAIL_TRIGGER_PCT <= 0:
        return None
    if mfe < entry * (1 + Config.SWEEP_TRAIL_TRIGGER_PCT):
        return None
    return mfe * (1 - Config.SWEEP_TRAIL_PCT)


def fixed_target_active() -> bool:
    """The fixed take-profit is disabled while trailing is enabled, so the
    trail (not a fixed cap) governs the upside."""
    return Config.ORB_TRAIL_TRIGGER_PCT <= 0
