"""
Optional selectivity filters for the Fib strategy.

Each filter is a pure, independently measurable predicate. They exist so the
validation harness can answer "does gating on X improve out-of-sample
expectancy?" — NOT to be switched on by faith. All default OFF
(`Config.FIB_FILTER_*`); enabling one is a decision that must be justified by
the report, not the backtest P&L alone.

`assess_filters` evaluates EVERY filter (so the harness can report each one's
effect on trades it would have blocked) and marks which are enabled; the
simulator blocks an entry only when an *enabled* filter fails.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from analysis.signals import Signal
from backtest.regime import in_window, realized_vol, tod_bucket, trend_state
from config import Config


@dataclass
class FilterResult:
    name: str
    enabled: bool
    passed: bool
    value: object          # the measured quantity (for reporting)


def _trend(direction, bars) -> tuple[bool, object]:
    state = trend_state(bars, Config.FIB_TREND_EMA)
    if direction == Signal.LONG:
        return state != "down", state
    return state != "up", state


def _vol(bars) -> tuple[bool, object]:
    v = realized_vol(bars)
    return Config.FIB_VOL_MIN <= v <= Config.FIB_VOL_MAX, round(v, 3)


def _tod(ts) -> tuple[bool, object]:
    bucket = tod_bucket(ts)
    blocked = in_window(ts, Config.FIB_TOD_BLOCK)
    return not blocked, bucket


def _liquidity(entry_spread_pct) -> tuple[bool, object]:
    if entry_spread_pct is None:
        return True, None
    return entry_spread_pct <= Config.FIB_MAX_ENTRY_SPREAD_PCT, round(entry_spread_pct, 4)


def _confirm(direction, touch_close, confirm_close) -> tuple[bool, object]:
    """Post-touch confirmation: the bar AFTER the touch closes back in the
    trend's direction (rejecting a wick that keeps going against us). Pending
    (pass) if the confirming bar isn't available yet."""
    if confirm_close is None or touch_close is None:
        return True, None
    if direction == Signal.LONG:
        return confirm_close >= touch_close, round(confirm_close - touch_close, 2)
    return confirm_close <= touch_close, round(confirm_close - touch_close, 2)


def assess_filters(
    direction,
    bars: pd.DataFrame,
    ts: pd.Timestamp,
    entry_spread_pct: float | None = None,
    touch_close: float | None = None,
    confirm_close: float | None = None,
) -> list[FilterResult]:
    """Evaluate all five filters. Order is stable for reporting."""
    trend_ok, trend_v = _trend(direction, bars)
    vol_ok, vol_v = _vol(bars)
    tod_ok, tod_v = _tod(ts)
    liq_ok, liq_v = _liquidity(entry_spread_pct)
    conf_ok, conf_v = _confirm(direction, touch_close, confirm_close)
    return [
        FilterResult("trend", Config.FIB_FILTER_TREND, trend_ok, trend_v),
        FilterResult("vol_regime", Config.FIB_FILTER_VOL_REGIME, vol_ok, vol_v),
        FilterResult("time_of_day", Config.FIB_FILTER_TOD, tod_ok, tod_v),
        FilterResult("liquidity", Config.FIB_FILTER_LIQUIDITY, liq_ok, liq_v),
        FilterResult("confirm", Config.FIB_FILTER_CONFIRM, conf_ok, conf_v),
    ]


def blocked_by(results: list[FilterResult]) -> str | None:
    """Name of the first ENABLED filter that fails, else None (entry allowed)."""
    for r in results:
        if r.enabled and not r.passed:
            return r.name
    return None
