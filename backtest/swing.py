"""
Swing backtest: the sweep-reclaim strategy on its NATIVE timeframe.

4-hour candles, entries at the reclaim close, positions held ACROSS DAYS
(no end-of-day flatten) until the underlying hits the wick stop, the
SWING_RR target, a SWING_MAX_HOLD_DAYS time stop, or the contract nears
expiry. Contracts are simulated ~SWING_DTE-day options priced with
Black-Scholes, so multi-day theta decay and overnight gaps are in the
numbers.

Backtest-only for now — this exists to answer whether the hybrid
(day + swing) idea earns before any live implementation.
"""
from __future__ import annotations

import math
from datetime import timedelta

import pandas as pd

from analysis.htf import completed_bars, resample_bars
from analysis.signals import Signal
from analysis.sweeps import rr_target, sweep_reclaim
from backtest.bs import bs_price
from config import Config

SPREAD_COST = 0.05  # assumed half-spread per side for 14-DTE contracts


def _trailing_iv(bars: pd.DataFrame, i: int, lookback_bars: int = 130) -> float:
    """Annualized realized vol of bar returns over ~10 trading days."""
    rets = bars["close"].iloc[max(0, i - lookback_bars): i + 1].pct_change().dropna()
    if len(rets) < 10:
        return 0.20
    per_day = (390 / Config.SWING_BAR_MINUTES)
    return max(float(rets.std() * math.sqrt(252 * per_day)), 0.10)


def simulate_swing(symbol: str, bars: pd.DataFrame, equity: float) -> list[dict]:
    """
    `bars`: multi-day RTH bars at Config.SWING_BAR_MINUTES resolution.
    Returns closed trades in the same dict shape as the day simulations.
    """
    trades: list[dict] = []
    open_trade: dict | None = None
    acted: set[str] = set()

    for i in range(1, len(bars)):
        ts = bars.index[i]
        spot = float(bars["close"].iloc[i])

        if open_trade:
            t_years = max((open_trade["expiry"] - ts).total_seconds(), 0) / (365 * 24 * 3600)
            days_held = (ts - open_trade["opened"]).total_seconds() / 86400
            reason = None
            if open_trade["direction"] == "LONG":
                if spot <= open_trade["stop_ul"]:
                    reason = "UL_SL"
                elif spot >= open_trade["target_ul"]:
                    reason = "UL_TP"
            else:
                if spot >= open_trade["stop_ul"]:
                    reason = "UL_SL"
                elif spot <= open_trade["target_ul"]:
                    reason = "UL_TP"
            if reason is None and days_held >= Config.SWING_MAX_HOLD_DAYS:
                reason = "TIME"
            if reason is None and t_years * 365 < 2:
                reason = "EXPIRY"  # roll/close before the final decay cliff
            if reason:
                mark = bs_price(
                    spot, open_trade["strike"], t_years, open_trade["iv"], open_trade["type"]
                )
                exit_premium = max(mark - SPREAD_COST, 0.01)
                open_trade["pnl"] = (
                    (exit_premium - open_trade["entry"]) * 100 * open_trade["qty"]
                )
                open_trade["exit_reason"] = reason
                open_trade["hold_days"] = round(days_held, 1)
                trades.append(open_trade)
                open_trade = None
            continue

        # ---- detection on completed native-timeframe candles
        htf = resample_bars(bars.iloc[: i + 1], Config.SWING_TIMEFRAME_MINUTES)
        htf = completed_bars(
            htf, Config.SWING_TIMEFRAME_MINUTES,
            ts + timedelta(minutes=Config.SWING_BAR_MINUTES),
        )
        sig = sweep_reclaim(htf.tail(2), trend_filter=Config.SWEEP_TREND_FILTER)
        if sig is None or sig.candle_ts in acted:
            continue
        acted.add(sig.candle_ts)

        stop = sig.extreme
        if abs(spot - stop) < 0.01:
            continue
        target = rr_target(spot, stop, Config.SWING_RR)

        opt_type = "call" if sig.direction == Signal.LONG else "put"
        strike = math.ceil(spot) if opt_type == "call" else math.floor(spot)
        expiry = ts.normalize() + timedelta(days=Config.SWING_DTE, hours=16)
        t_years = (expiry - ts).total_seconds() / (365 * 24 * 3600)
        iv = _trailing_iv(bars, i)
        entry = bs_price(spot, strike, t_years, iv, opt_type) + SPREAD_COST
        if entry < 0.20:
            continue

        prem_at_stop = bs_price(stop, strike, t_years, iv, opt_type)
        risk_per_contract = max(entry - prem_at_stop, 0.05) * 100
        qty = min(
            int((equity * Config.RISK_PER_TRADE_PCT) // risk_per_contract),
            int((equity * Config.MAX_POSITION_PCT) // (entry * 100)),
            Config.MAX_CONTRACTS,
        )
        if qty < 1:
            continue

        open_trade = {
            "symbol": symbol,
            "date": ts.date().isoformat(),
            "opened": ts,
            "direction": sig.direction.value,
            "type": opt_type,
            "strike": strike,
            "qty": qty,
            "entry": entry,
            "iv": iv,
            "expiry": expiry,
            "stop_ul": stop,
            "target_ul": target,
        }

    return trades
