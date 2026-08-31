"""
ETF_TREND_V1 — diversified long/cash time-series momentum (intent producer).

Implements the frozen spec docs/strategies/ETF_TREND_V1.md. Weekly: score each
ETF by the average vol-normalized return over ~1/3/6/12 months; hold only
positive-score ETFs at inverse-vol weights (25% cap, 90% gross, rest cash).
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
)

STRATEGY_ID = "ETF_TREND_V1"
VERSION = "v1"

DEFAULTS = {
    "lookbacks": (21, 63, 126, 252),
    "universe": ("SPY", "QQQ", "IWM", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC"),
    "vol_lookback": 63,      # for inverse-vol sizing
    "max_weight": 0.25,
    "max_gross": 0.90,
}


def config_hash(config: dict) -> str:
    keys = ("lookbacks", "universe", "vol_lookback", "max_weight", "max_gross")
    blob = "|".join(f"{k}={config.get(k, DEFAULTS[k])}" for k in keys)
    return hashlib.sha256((VERSION + "|" + blob).encode()).hexdigest()[:12]


def target_weights(as_of, bars_by_symbol: dict, config: dict | None = None) -> dict:
    """Return {symbol: target_weight} (fraction of the sleeve budget) using only
    bars up to `as_of`. Positive-score ETFs only, inverse-vol, capped/scaled."""
    cfg = {**DEFAULTS, **(config or {})}
    scores, vols = {}, {}
    for sym in cfg["universe"]:
        bars = bars_by_symbol.get(sym)
        if bars is None or bars.empty:
            continue
        closes = bars.loc[:as_of, "close"]
        score = combined_trend_score(closes, cfg["lookbacks"])
        if score is None or score <= 0:
            continue                       # hold only positive trend; else cash
        v = ann_vol(closes, cfg["vol_lookback"])
        if v:
            scores[sym], vols[sym] = score, v
    weights = inverse_vol_weights(vols)
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
            horizon="weekly", reason_codes=("trend_positive",),
        )
        for sym, w in weights.items()
    ]
