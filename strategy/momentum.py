"""
Shared daily-momentum helpers for the ETF strategies.

Pure functions on adjusted (total-return) daily close series. No look-ahead: the
caller passes only bars up to and including the decision date. These implement
the frozen definitions in docs/strategies/ETF_TREND_V1.md and
ETF_RELATIVE_MOMENTUM_V1.md — do not change them without a new strategy version.
"""
from __future__ import annotations

import math

import pandas as pd

TRADING_DAYS = 252


def total_return(closes: pd.Series, lookback: int) -> float | None:
    """Total return over `lookback` completed bars, or None if too short."""
    if len(closes) < lookback + 1:
        return None
    past = float(closes.iloc[-1 - lookback])
    if past <= 0:
        return None
    return float(closes.iloc[-1]) / past - 1.0


def ann_vol(closes: pd.Series, lookback: int) -> float | None:
    """Annualized volatility of daily returns over `lookback` bars."""
    if len(closes) < lookback + 1:
        return None
    rets = closes.pct_change().dropna().iloc[-lookback:]
    if len(rets) < 2:
        return None
    sd = float(rets.std())
    return sd * math.sqrt(TRADING_DAYS) if sd > 0 else None


def vol_normalized_return(closes: pd.Series, lookback: int, skip: int = 0) -> float | None:
    """Return over `lookback` (optionally skipping the most recent `skip` bars),
    divided by annualized vol over the same span. None if not computable."""
    if skip:
        if len(closes) < lookback + skip + 1:
            return None
        closes = closes.iloc[: len(closes) - skip]
    r = total_return(closes, lookback)
    v = ann_vol(closes, lookback)
    if r is None or v is None or v == 0:
        return None
    return r / v


def combined_trend_score(closes: pd.Series, lookbacks: tuple) -> float | None:
    """Average of the vol-normalized returns across `lookbacks`. None if none
    of the horizons are computable."""
    scores = [s for lb in lookbacks if (s := vol_normalized_return(closes, lb)) is not None]
    if not scores:
        return None
    return sum(scores) / len(scores)


def inverse_vol_weights(vols: dict) -> dict:
    """Risk-parity-ish weights ∝ 1/vol, normalized to sum 1. Symbols with an
    unusable vol are dropped."""
    inv = {s: 1.0 / v for s, v in vols.items() if v and v > 0}
    total = sum(inv.values())
    if total <= 0:
        return {}
    return {s: w / total for s, w in inv.items()}


def cap_and_scale(weights: dict, max_weight: float, max_gross: float) -> dict:
    """Cap each weight at `max_weight`, then scale so the sum ≤ `max_gross`.
    Remainder is implicit cash."""
    capped = {s: min(w, max_weight) for s, w in weights.items()}
    gross = sum(capped.values())
    if gross > max_gross and gross > 0:
        scale = max_gross / gross
        capped = {s: w * scale for s, w in capped.items()}
    return {s: w for s, w in capped.items() if w > 1e-9}


def weekly_rebalance_dates(index: pd.DatetimeIndex) -> list:
    """Last available session of each ISO week (the 'Friday close' decision)."""
    df = pd.Series(index, index=index)
    keys = index.to_series().dt.isocalendar()
    out = []
    for _, grp in df.groupby([keys["year"], keys["week"]]):
        out.append(grp.iloc[-1])
    return sorted(out)


def monthly_rebalance_dates(index: pd.DatetimeIndex) -> list:
    """Last available session of each calendar month (month-end decision)."""
    df = pd.Series(index, index=index)
    out = []
    for _, grp in df.groupby([index.year, index.month]):
        out.append(grp.iloc[-1])
    return sorted(out)
