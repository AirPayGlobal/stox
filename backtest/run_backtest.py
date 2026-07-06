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
    session_range,
    sweep_reclaim,
)


def _range_levels(bars_ext, day):
    """Overnight or configured session window levels for `day`."""
    if not Config.SWEEP_OVERNIGHT_RANGE:
        return None
    if Config.SWEEP_SESSION_WINDOW:
        return session_range(bars_ext, day, Config.SWEEP_SESSION_WINDOW)
    return overnight_range(bars_ext, day)
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


def _resolve_strategies(strategy: str) -> list[str]:
    if strategy == "both":
        return ["orb", "sweep"]
    if strategy == "all":
        return ["orb", "sweep", "swing"]
    return [strategy]


def _stats(trades: list[dict]) -> dict:
    """JSON-safe summary of one strategy's simulated trades."""
    df = pd.DataFrame(trades)
    daily = df.groupby("date")["pnl"].sum()
    wins = df[df["pnl"] > 0]
    target = Config.DAILY_PROFIT_TARGET
    out = {
        "trades": len(df),
        "trading_days": int(daily.shape[0]),
        "win_rate": round(len(wins) / len(df), 3),
        "total_pnl": round(float(df["pnl"].sum()), 0),
        "avg_daily_pnl": round(float(daily.mean()), 0),
        "median_daily_pnl": round(float(daily.median()), 0),
        "best_day": round(float(daily.max()), 0),
        "worst_day": round(float(daily.min()), 0),
        "daily_stdev": round(float(daily.std()), 0) if daily.shape[0] > 1 else 0.0,
        "days_at_target": int((daily >= target).sum()),
        "exit_reasons": {str(k): int(v) for k, v in df["exit_reason"].value_counts().items()},
    }
    if "hold_days" in df.columns:
        out["avg_hold_days"] = round(float(df["hold_days"].mean()), 1)
    return out


def run_backtest(symbols: list[str], days: int, equity: float, strategy: str) -> dict:
    """Run the simulation and return a JSON-safe results dict."""
    from backtest.swing import simulate_swing

    strategies = _resolve_strategies(strategy)
    by_strategy: dict[str, list[dict]] = {s: [] for s in strategies}

    for symbol in symbols:
        if "orb" in strategies or "sweep" in strategies:
            bars_ext = get_intraday_bars(symbol, lookback_days=days, rth_only=False)
            if not bars_ext.empty:
                bars = bars_ext.between_time("09:30", "16:00")
                day_groups = [(d, g) for d, g in bars.groupby(bars.index.date)]
                for idx, (day, day_bars) in enumerate(day_groups):
                    prev_bars = day_groups[idx - 1][1] if idx > 0 else None
                    if "orb" in strategies:
                        by_strategy["orb"] += simulate_day_orb(symbol, day_bars, equity)
                    if "sweep" in strategies:
                        onr = _range_levels(bars_ext, day)
                        by_strategy["sweep"] += simulate_day_sweep(
                            symbol, prev_bars, day_bars, equity, onr=onr
                        )
        if "swing" in strategies:
            swing_bars = get_intraday_bars(
                symbol, minutes=Config.SWING_BAR_MINUTES, lookback_days=days
            )
            if not swing_bars.empty:
                by_strategy["swing"] += simulate_swing(symbol, swing_bars, equity)

    return {
        "symbols": symbols,
        "days_requested": days,
        "equity": equity,
        "daily_profit_target": Config.DAILY_PROFIT_TARGET,
        "strategies": {
            s: (_stats(trades) if trades else {"trades": 0})
            for s, trades in by_strategy.items()
        },
        "caveat": (
            "Simulated fills (Black-Scholes marks, realized-IV proxy, assumed "
            "half-spread). Real fills include slippage and IV shifts this model "
            "only approximates — treat as an upper bound."
        ),
    }


def print_results(res: dict) -> None:
    print("\n========== BACKTEST RESULTS (simulated option marks) ==========")
    print(f"Symbols          : {', '.join(res['symbols'])}")
    print(f"Account equity   : ${res['equity']:,.0f}")
    for strat, s in res["strategies"].items():
        if not s.get("trades"):
            print(f"\n--- {strat.upper()}: no trades ---")
            continue
        print(f"\n--- {strat.upper()} ---")
        print(f"Trading days     : {s['trading_days']}")
        print(f"Trades           : {s['trades']}")
        print(f"Win rate         : {s['win_rate']:.0%}")
        print(f"Total P&L        : ${s['total_pnl']:+,.0f}")
        print(f"Avg daily P&L    : ${s['avg_daily_pnl']:+,.0f} (median ${s['median_daily_pnl']:+,.0f})")
        print(f"Best / worst day : ${s['best_day']:+,.0f} / ${s['worst_day']:+,.0f}")
        print(f"Days >= target   : {s['days_at_target']} of {s['trading_days']}")
        if "avg_hold_days" in s:
            print(f"Avg hold         : {s['avg_hold_days']} days")
        print("Exit reasons     : " + ", ".join(f"{k}={v}" for k, v in s["exit_reasons"].items()))
    print(f"\n⚠ {res['caveat']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the options strategies")
    parser.add_argument("symbols", nargs="*", default=None)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--equity", type=float, default=100_000)
    parser.add_argument(
        "--strategy",
        choices=["orb", "sweep", "swing", "both", "all"],
        default=Config.STRATEGY,
    )
    args = parser.parse_args()
    symbols = [s.upper() for s in args.symbols] or Config.UNDERLYINGS
    print_results(run_backtest(symbols, args.days, args.equity, args.strategy))


if __name__ == "__main__":
    main()
