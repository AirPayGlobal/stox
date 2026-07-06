"""
Backtest the intraday options strategies on historical bars.

    python backtest/run_backtest.py --days 60 --equity 250000
    python backtest/run_backtest.py SPY QQQ --days 90 --strategy sweep

Strategies (--strategy orb|sweep|both, default = Config.STRATEGY):
  * orb   — opening-range momentum, premium-based exits
  * sweep — liquidity-sweep reversal (HTF sweep-and-reclaim + previous-day
            high/low sweeps), underlying-level stop/target at SWEEP_RR

Option marks are SIMULATED with Black-Scholes off the underlying's intraday
bars (same-day expiry, IV from recent realized volatility). Real 0DTE
fills include spread, slippage and IV crush that this model only
approximates — treat results as an upper bound, and validate in paper
trading before believing any number here.
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from analysis.htf import completed_bars, resample_bars
from analysis.signals import Signal, generate_signal
from analysis.sweeps import (
    level_sweep,
    overnight_range,
    prev_day_level_sweep,
    rr_target,
    sweep_reclaim,
)
from backtest.bs import bs_price
from config import Config
from data.market_data import get_intraday_bars

EXPIRY_ET = dtime(16, 0)
SPREAD_COST = 0.02  # assumed half-spread paid per side, in premium dollars


def realized_iv(day_bars: pd.DataFrame, annualize: float = 252 * 78) -> float:
    """Rough IV proxy: annualized stdev of 5-min log returns, floored."""
    rets = day_bars["close"].pct_change().dropna()
    if len(rets) < 5:
        return 0.20
    return max(float(rets.std() * math.sqrt(annualize)), 0.10)


def t_to_expiry_years(ts: pd.Timestamp) -> float:
    expiry = ts.replace(hour=EXPIRY_ET.hour, minute=EXPIRY_ET.minute)
    return max((expiry - ts).total_seconds(), 0) / (365 * 24 * 3600)


def _session_times() -> tuple[dtime, dtime, dtime]:
    return (
        dtime(*map(int, Config.ENTRY_START.split(":"))),
        dtime(*map(int, Config.ENTRY_CUTOFF.split(":"))),
        dtime(*map(int, Config.FLATTEN_TIME.split(":"))),
    )


def _open_synthetic(symbol, ts, direction, spot, iv, equity, stop_ul=None, target_ul=None):
    """Price a synthetic ATM-ish contract and size it. Returns trade or None."""
    opt_type = "call" if direction == Signal.LONG else "put"
    strike = math.ceil(spot) if opt_type == "call" else math.floor(spot)
    t = t_to_expiry_years(ts)
    entry = bs_price(spot, strike, t, iv, opt_type) + SPREAD_COST
    if entry < 0.10:
        return None

    if stop_ul is not None:
        # Risk per contract = premium decay down to the option's value at the stop.
        prem_at_stop = bs_price(stop_ul, strike, t, iv, opt_type)
        risk_per_contract = max(entry - prem_at_stop, 0.05) * 100
    else:
        risk_per_contract = entry * 100 * Config.STOP_LOSS_PCT

    qty = min(
        int((equity * Config.RISK_PER_TRADE_PCT) // risk_per_contract),
        int((equity * Config.MAX_POSITION_PCT) // (entry * 100)),
        Config.MAX_CONTRACTS,
    )
    if qty < 1:
        return None

    return {
        "symbol": symbol,
        "date": ts.date().isoformat(),
        "opened": ts,
        "direction": direction.value,
        "type": opt_type,
        "strike": strike,
        "qty": qty,
        "entry": entry,
        "stop": entry * (1 - Config.STOP_LOSS_PCT),
        "target": entry * (1 + Config.TAKE_PROFIT_PCT),
        "stop_ul": stop_ul,
        "target_ul": target_ul,
    }


def _close_synthetic(trade, spot, ts, iv, reason):
    mark = bs_price(spot, trade["strike"], t_to_expiry_years(ts), iv, trade["type"])
    exit_premium = max(mark - SPREAD_COST, 0.01)
    trade["pnl"] = (exit_premium - trade["entry"]) * 100 * trade["qty"]
    trade["exit_reason"] = reason
    return trade


def _loss_discipline_blocked(trades: list[dict], ts) -> bool:
    """Engine parity: post-loss cooldown + consecutive-loss cutoff."""
    streak = 0
    for t in reversed(trades):
        if t["pnl"] < 0:
            streak += 1
        else:
            break
    if streak >= Config.MAX_CONSECUTIVE_LOSSES:
        return True
    if trades and trades[-1]["pnl"] < 0:
        minutes = (ts - trades[-1]["closed_ts"]).total_seconds() / 60
        if minutes < Config.LOSS_COOLDOWN_MINUTES:
            return True
    return False


def simulate_day_orb(symbol: str, day_bars: pd.DataFrame, equity: float) -> list[dict]:
    """Opening-range momentum: premium-based exits (engine parity)."""
    trades: list[dict] = []
    open_trade: dict | None = None
    iv = realized_iv(day_bars)
    entry_start, entry_cutoff, flatten = _session_times()

    for i in range(6, len(day_bars)):
        ts = day_bars.index[i]
        window = day_bars.iloc[: i + 1]
        spot = float(window["close"].iloc[-1])

        if open_trade:
            mark = bs_price(
                spot, open_trade["strike"], t_to_expiry_years(ts), iv, open_trade["type"]
            )
            minutes_open = (ts - open_trade["opened"]).total_seconds() / 60
            reason = None
            if ts.time() >= flatten:
                reason = "FLATTEN"
            elif mark >= open_trade["target"]:
                reason = "TP"
            elif mark <= open_trade["stop"]:
                reason = "SL"
            elif minutes_open >= Config.MAX_HOLD_MINUTES:
                reason = "TIME"
            if reason:
                closed = _close_synthetic(open_trade, spot, ts, iv, reason)
                closed["closed_ts"] = ts
                trades.append(closed)
                open_trade = None
            continue

        if not (entry_start <= ts.time() <= entry_cutoff):
            continue
        if _loss_discipline_blocked(trades, ts):
            continue
        result = generate_signal(window)
        if result.signal == Signal.FLAT:
            continue
        open_trade = _open_synthetic(symbol, ts, result.signal, spot, iv, equity)

    return trades


def simulate_day_sweep(
    symbol: str,
    prev_day_bars: pd.DataFrame | None,
    day_bars: pd.DataFrame,
    equity: float,
    onr: tuple[float, float] | None = None,
) -> list[dict]:
    """Liquidity-sweep reversal: underlying-level stop/target (engine parity,
    SWEEP_ENTRY=close mode). `onr` is the overnight/pre-market (high, low)."""
    trades: list[dict] = []
    open_trade: dict | None = None
    iv = realized_iv(day_bars)
    entry_start, entry_cutoff, flatten = _session_times()
    acted: set[str] = set()

    combined = (
        pd.concat([prev_day_bars, day_bars]) if prev_day_bars is not None else day_bars
    )
    prev_high = float(prev_day_bars["high"].max()) if prev_day_bars is not None else None
    prev_low = float(prev_day_bars["low"].min()) if prev_day_bars is not None else None

    for i in range(1, len(day_bars)):
        ts = day_bars.index[i]
        spot = float(day_bars["close"].iloc[i])

        if open_trade:
            reason = None
            if ts.time() >= flatten:
                reason = "FLATTEN"
            elif open_trade["direction"] == "LONG":
                if spot <= open_trade["stop_ul"]:
                    reason = "UL_SL"
                elif spot >= open_trade["target_ul"]:
                    reason = "UL_TP"
            else:
                if spot >= open_trade["stop_ul"]:
                    reason = "UL_SL"
                elif spot <= open_trade["target_ul"]:
                    reason = "UL_TP"
            if reason:
                closed = _close_synthetic(open_trade, spot, ts, iv, reason)
                closed["closed_ts"] = ts
                trades.append(closed)
                open_trade = None
            continue

        if not (entry_start <= ts.time() <= entry_cutoff):
            continue
        if _loss_discipline_blocked(trades, ts):
            continue

        # Decision at the close of bar i — completed bars only.
        asof = ts + timedelta(minutes=Config.BAR_MINUTES)
        htf = resample_bars(combined[combined.index <= ts], Config.SWEEP_TIMEFRAME_MINUTES)
        htf = completed_bars(htf, Config.SWEEP_TIMEFRAME_MINUTES, asof)
        sig = sweep_reclaim(htf.tail(2), trend_filter=Config.SWEEP_TREND_FILTER)
        if sig is None and Config.SWEEP_PREV_DAY_LEVELS and prev_high is not None:
            sig = prev_day_level_sweep(day_bars.iloc[: i + 1], prev_high, prev_low)
        if sig is None and Config.SWEEP_OVERNIGHT_RANGE and onr is not None:
            sig = level_sweep(day_bars.iloc[: i + 1], onr[0], onr[1], "overnight_range")
        if sig is None:
            continue
        dedupe = f"{sig.kind}|{sig.candle_ts}"
        if dedupe in acted:
            continue
        acted.add(dedupe)

        stop = sig.extreme
        if abs(spot - stop) < 0.01:
            continue
        target = rr_target(spot, stop, Config.SWEEP_RR)
        open_trade = _open_synthetic(
            symbol, ts, sig.direction, spot, iv, equity, stop_ul=stop, target_ul=target
        )

    return trades


def run(symbols: list[str], days: int, equity: float, strategy: str) -> None:
    strategies = ["orb", "sweep"] if strategy == "both" else [strategy]
    all_trades: list[dict] = []

    for symbol in symbols:
        print(f"Fetching {days} days of {Config.BAR_MINUTES}-min bars for {symbol}…")
        bars_ext = get_intraday_bars(symbol, lookback_days=days, rth_only=False)
        if bars_ext.empty:
            print(f"  no data for {symbol} — skipping")
            continue
        bars = bars_ext.between_time("09:30", "16:00")
        day_groups = [(d, g) for d, g in bars.groupby(bars.index.date)]
        for idx, (day, day_bars) in enumerate(day_groups):
            prev_bars = day_groups[idx - 1][1] if idx > 0 else None
            if "orb" in strategies:
                for t in simulate_day_orb(symbol, day_bars, equity):
                    t["strategy"] = "orb"
                    all_trades.append(t)
            if "sweep" in strategies:
                onr = overnight_range(bars_ext, day) if Config.SWEEP_OVERNIGHT_RANGE else None
                for t in simulate_day_sweep(symbol, prev_bars, day_bars, equity, onr=onr):
                    t["strategy"] = "sweep"
                    all_trades.append(t)

    if not all_trades:
        print("No trades generated.")
        return

    df = pd.DataFrame(all_trades)
    print("\n========== BACKTEST RESULTS (simulated option marks) ==========")
    print(f"Symbols          : {', '.join(symbols)}")
    print(f"Account equity   : ${equity:,.0f}")
    for strat in strategies:
        sub = df[df["strategy"] == strat]
        if sub.empty:
            print(f"\n--- {strat.upper()}: no trades ---")
            continue
        daily = sub.groupby("date")["pnl"].sum()
        wins = sub[sub["pnl"] > 0]
        target = Config.DAILY_PROFIT_TARGET
        print(f"\n--- {strat.upper()} ---")
        print(f"Trading days     : {daily.shape[0]}")
        print(f"Trades           : {len(sub)}  (avg {len(sub)/daily.shape[0]:.1f}/day)")
        print(f"Win rate         : {len(wins)/len(sub):.0%}")
        print(f"Total P&L        : ${sub['pnl'].sum():+,.0f}")
        print(f"Avg daily P&L    : ${daily.mean():+,.0f}   (median ${daily.median():+,.0f})")
        print(f"Best / worst day : ${daily.max():+,.0f} / ${daily.min():+,.0f}")
        print(f"Daily P&L stdev  : ${daily.std():,.0f}")
        print(
            f"Days >= +${target:,.0f} : {(daily >= target).sum()} of {daily.shape[0]} "
            f"({(daily >= target).mean():.0%})"
        )
        print("Exit reasons     : " + ", ".join(
            f"{r}={n}" for r, n in sub["exit_reason"].value_counts().items()
        ))
    print(
        "\n⚠ Simulated fills (Black-Scholes, fixed IV proxy, "
        f"${SPREAD_COST:.02f} half-spread). Paper trade before trusting this."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the options strategies")
    parser.add_argument("symbols", nargs="*", default=None)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--equity", type=float, default=100_000)
    parser.add_argument(
        "--strategy", choices=["orb", "sweep", "both"], default=Config.STRATEGY
    )
    args = parser.parse_args()
    symbols = [s.upper() for s in args.symbols] or Config.UNDERLYINGS
    run(symbols, args.days, args.equity, args.strategy)


if __name__ == "__main__":
    main()
