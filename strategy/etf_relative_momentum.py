"""
ETF_RELATIVE_MOMENTUM_V1 — cross-sectional leadership (intent producer).

Implements the frozen spec docs/strategies/ETF_RELATIVE_MOMENTUM_V1.md. Monthly:
rank eligible ETFs by the average of vol-normalized 12-1 and 6-1 total returns;
take the top N, but hold one only if its own absolute trend score is positive;
equal-risk (inverse-vol) among the selected, 35% cap, 90% gross, rest cash.
Emits StrategyIntent targets — it never places orders.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from portfolio.intent import StrategyIntent
from strategy.momentum import (
    ann_vol,
    cap_and_scale,
    combined_trend_score,
    inverse_vol_weights,
    vol_normalized_return,
)

STRATEGY_ID = "ETF_RELATIVE_MOMENTUM_V1"
VERSION = "v1"

DEFAULTS = {
    "universe": ("SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC"),
    "top_n": 3,
    "skip": 21,              # 'minus-1-month' — skip the most recent ~21 sessions
    "abs_lookbacks": (21, 63, 126, 252),   # absolute-trend gate (same as ETF_TREND)
    "vol_lookback": 63,
    "max_weight": 0.35,
    "max_gross": 0.90,
}


def config_hash(config: dict) -> str:
    keys = ("universe", "top_n", "skip", "abs_lookbacks", "vol_lookback", "max_weight", "max_gross")
    blob = "|".join(f"{k}={config.get(k, DEFAULTS[k])}" for k in keys)
    return hashlib.sha256((VERSION + "|" + blob).encode()).hexdigest()[:12]


def _relative_score(closes, skip) -> float | None:
    s12 = vol_normalized_return(closes, 252, skip=skip)
    s6 = vol_normalized_return(closes, 126, skip=skip)
    parts = [s for s in (s12, s6) if s is not None]
    return sum(parts) / len(parts) if parts else None


def target_weights(as_of, bars_by_symbol: dict, config: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(config or {})}
    rel, vols, abs_ok = {}, {}, {}
    for sym in cfg["universe"]:
        bars = bars_by_symbol.get(sym)
        if bars is None or bars.empty:
            continue
        closes = bars.loc[:as_of, "close"]
        r = _relative_score(closes, cfg["skip"])
        if r is None:
            continue
        rel[sym] = r
        vols[sym] = ann_vol(closes, cfg["vol_lookback"])
        abs_score = combined_trend_score(closes, cfg["abs_lookbacks"])
        abs_ok[sym] = abs_score is not None and abs_score > 0
    # Rank by relative score, take top_n, keep only those passing the absolute gate.
    ranked = sorted(rel, key=lambda s: rel[s], reverse=True)[: cfg["top_n"]]
    selected = {s: vols[s] for s in ranked if abs_ok.get(s) and vols.get(s)}
    weights = inverse_vol_weights(selected)
    return cap_and_scale(weights, cfg["max_weight"], cfg["max_gross"])


def generate_intents(as_of, bars_by_symbol: dict, config: dict | None = None) -> list:
    cfg = {**DEFAULTS, **(config or {})}
    ch = config_hash(cfg)
    ts = pd.Timestamp(as_of).isoformat()
    weights = target_weights(as_of, bars_by_symbol, cfg)
    return [
        StrategyIntent(
            strategy_id=STRATEGY_ID, version=VERSION, config_hash=ch,
            signal_ts=ts, data_cutoff=ts, symbol=sym, target_weight=round(w, 6),
            horizon="monthly", reason_codes=("relative_strength", "abs_momentum_ok"),
        )
        for sym, w in weights.items()
    ]
