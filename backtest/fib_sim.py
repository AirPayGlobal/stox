"""
Engine-parity, account-level Fib simulator with research-grade trade records.

This is the heart of the paper-trading validation upgrade. Unlike the legacy
per-symbol `simulate_day_fib`, it enforces the SAME production constraints the
live engine applies, so a backtest number cannot be inflated by behaviour the
engine would never allow:

  * entry-scan CADENCE parity (every FIB_SCAN_BARS bars, not every bar)
  * completed-bar signal logic (decision uses bars up to the scan bar)
  * account-level daily governor: profit-protection floor + daily max-loss halt
  * MAX_TRADES_PER_DAY and MAX_CONCURRENT_POSITIONS across BOTH symbols
  * per-symbol loss/win cooldowns and consecutive-loss cutoff
  * FIB_MAX_HOLD_MINUTES time stop and the FLATTEN_TIME end-of-day flatten
  * realistic execution via backtest.execution scenarios (spread, slippage,
    delayed fills, unfilled/partial) — never mid unless the 'mid' diagnostic

Option marks are still simulated with Black-Scholes (no options chain here), so
results remain SIMULATED, not live. Every trade emits a research record with
the fields needed to slice the results (see backtest/validation.py).

No market-data or broker access — callers pass the bars in.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import time as dtime

import pandas as pd

from analysis.fib import fib_signal, stop_distance_ok
from analysis.fib_filters import assess_filters, blocked_by
from analysis.signals import Signal
from backtest.bs import bs_delta, bs_price
from backtest.execution import get_scenario, modelled_spread, simulate_fill
from backtest.regime import realized_vol, tod_bucket, trend_state, vol_bucket
from config import Config

EXPIRY_ET = dtime(16, 0)


# ------------------------------------------------------------------ config hash
def config_hash() -> str:
    """Short stable hash of the parameters that affect fib results — recorded on
    every trade so a result set is traceable to the exact configuration."""
    keys = [
        "STRATEGY_VERSION", "FIB_BAR_MINUTES", "FIB_PIVOT_K", "FIB_ENTRY_LOW",
        "FIB_ENTRY_HIGH", "FIB_MIN_RANGE_PCT", "FIB_LOOKBACK_BARS",
        "FIB_MIN_STOP_PCT", "FIB_MAX_STOP_PCT", "FIB_MAX_HOLD_MINUTES",
        "FIB_SCAN_BARS", "RISK_PER_TRADE_PCT", "MAX_POSITION_PCT", "MAX_CONTRACTS",
        "DAILY_PROFIT_TARGET", "DAILY_MAX_LOSS", "PROFIT_FLOOR_PCT",
        "PROFIT_GIVEBACK_PCT", "PROTECT_MODE", "MAX_TRADES_PER_DAY",
        "MAX_CONCURRENT_POSITIONS", "LOSS_COOLDOWN_MINUTES", "WIN_COOLDOWN_MINUTES",
        "MAX_CONSECUTIVE_LOSSES", "ENTRY_START", "ENTRY_CUTOFF", "FLATTEN_TIME",
        "FIB_FILTER_TREND", "FIB_FILTER_VOL_REGIME", "FIB_FILTER_TOD",
        "FIB_FILTER_LIQUIDITY", "FIB_FILTER_CONFIRM",
    ]
    blob = "|".join(f"{k}={getattr(Config, k)}" for k in keys)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ------------------------------------------------------------------ helpers
def _parse_hhmm(s: str) -> dtime:
    h, m = map(int, s.split(":"))
    return dtime(h, m)


def _iv(day_bars: pd.DataFrame) -> float:
    rets = day_bars["close"].pct_change().dropna()
    if len(rets) < 5:
        return 0.20
    return max(float(rets.std() * math.sqrt(252 * 390)), 0.10)


def _t_years(ts: pd.Timestamp, dte: int) -> float:
    expiry = (ts + pd.Timedelta(days=dte)).replace(
        hour=EXPIRY_ET.hour, minute=EXPIRY_ET.minute, second=0, microsecond=0
    )
    return max((expiry - ts).total_seconds(), 0) / (365 * 24 * 3600)


@dataclass
class _Open:
    symbol: str
    underlying: str
    direction: object
    opt_type: str
    strike: float
    qty: int
    entry: float
    stop_ul: float
    target_ul: float
    opened_ts: pd.Timestamp
    dte: int
    planned_risk: float
    rec: dict
    opt_mfe: float = 0.0
    opt_mae: float = 0.0
    ul_mfe: float = 0.0
    ul_mae: float = 0.0


@dataclass
class RunResult:
    trades: list = field(default_factory=list)   # research records for FILLED/closed trades
    attempts: list = field(default_factory=list)  # every signal, incl. blocked/unfilled
    scenario: str = ""
    dte: int = 0


# ------------------------------------------------------------------ per-day sim
def _simulate_day(
    day: str,
    bars_by_symbol: dict[str, pd.DataFrame],
    equity: float,
    scenario: str,
    dte: int,
    result: RunResult,
) -> None:
    entry_start = _parse_hhmm(Config.ENTRY_START)
    entry_cutoff = _parse_hhmm(Config.ENTRY_CUTOFF)
    flatten = _parse_hhmm(Config.FLATTEN_TIME)
    k = Config.FIB_PIVOT_K
    sc = get_scenario(scenario)

    # Shared minute grid across symbols for this day.
    grid = sorted(set().union(*[set(b.index) for b in bars_by_symbol.values()]))
    iv = {s: _iv(b) for s, b in bars_by_symbol.items()}

    # Per-day account state (governor parity).
    day_pnl = 0.0
    peak_pnl = 0.0
    trades_opened = 0
    target_hit = False
    protect_locked = False
    loss_halted = False
    open_positions: list[_Open] = []
    acted: set[str] = set()
    last_close: dict[str, tuple[pd.Timestamp, float]] = {}
    streak: dict[str, int] = {}
    bars_since_scan = 0

    def profit_floor() -> float:
        return max(Config.DAILY_PROFIT_TARGET * Config.PROFIT_FLOOR_PCT,
                   peak_pnl * (1 - Config.PROFIT_GIVEBACK_PCT))

    for gi, ts in enumerate(grid):
        # ---- update governor from realized+unrealized day P&L ----
        unreal = 0.0
        for op in open_positions:
            b = bars_by_symbol.get(op.symbol)
            if b is None or ts not in b.index:
                continue
            spot = float(b.loc[ts, "close"])
            mark = bs_price(spot, op.strike, _t_years(ts, op.dte), iv[op.symbol], op.opt_type)
            unreal += (mark - op.entry) * 100 * op.qty
        cur_pnl = day_pnl + unreal
        peak_pnl = max(peak_pnl, cur_pnl)
        if not target_hit and cur_pnl >= Config.DAILY_PROFIT_TARGET:
            target_hit = True
        if target_hit and not protect_locked and cur_pnl <= profit_floor():
            protect_locked = True
        if not loss_halted and cur_pnl <= -Config.DAILY_MAX_LOSS:
            loss_halted = True

        force_flatten = loss_halted or ts.time() >= flatten or (
            protect_locked and Config.PROTECT_MODE == "flatten"
        )

        # ---- manage / exit open positions every bar ----
        for op in list(open_positions):
            b = bars_by_symbol.get(op.symbol)
            if b is None or ts not in b.index:
                continue
            spot = float(b.loc[ts, "close"])
            hi = float(b.loc[ts, "high"])
            lo = float(b.loc[ts, "low"])
            mark = bs_price(spot, op.strike, _t_years(ts, op.dte), iv[op.symbol], op.opt_type)
            op.opt_mfe = max(op.opt_mfe, mark)
            op.opt_mae = min(op.opt_mae, mark)
            if op.direction == Signal.LONG:
                op.ul_mfe = max(op.ul_mfe, hi)
                op.ul_mae = min(op.ul_mae, lo)
            else:
                op.ul_mfe = min(op.ul_mfe or spot, lo)
                op.ul_mae = max(op.ul_mae, hi)

            reason = None
            if force_flatten:
                reason = "HALT" if loss_halted else (
                    "PROTECT" if protect_locked and Config.PROTECT_MODE == "flatten" else "FLATTEN"
                )
            elif op.direction == Signal.LONG:
                if lo <= op.stop_ul:
                    reason = "UL_SL"
                elif hi >= op.target_ul:
                    reason = "UL_TP"
            else:
                if hi >= op.stop_ul:
                    reason = "UL_SL"
                elif lo <= op.target_ul:
                    reason = "UL_TP"
            if reason is None and Config.FIB_MAX_HOLD_MINUTES:
                if (ts - op.opened_ts).total_seconds() / 60 >= Config.FIB_MAX_HOLD_MINUTES:
                    reason = "TIME"
            if reason is None and mark <= op.entry * (1 - Config.SWEEP_DISASTER_STOP_PCT):
                reason = "SL"
            if reason:
                _close_position(op, ts, spot, mark, reason, sc, scenario, result)
                day_pnl += op.rec["pnl"]
                last_close[op.symbol] = (ts, op.rec["pnl"])
                streak[op.symbol] = streak.get(op.symbol, 0) + 1 if op.rec["pnl"] < 0 else 0
                open_positions.remove(op)

        # ---- entry scan on cadence ----
        bars_since_scan += 1
        if force_flatten or bars_since_scan < Config.FIB_SCAN_BARS:
            continue
        bars_since_scan = 0
        if not (entry_start <= ts.time() <= entry_cutoff):
            continue
        if protect_locked or loss_halted:
            continue
        if trades_opened >= Config.MAX_TRADES_PER_DAY:
            continue

        for symbol, b in bars_by_symbol.items():
            if len(open_positions) >= Config.MAX_CONCURRENT_POSITIONS:
                break
            if ts not in b.index:
                continue
            if any(op.symbol == symbol for op in open_positions):
                continue
            if streak.get(symbol, 0) >= Config.MAX_CONSECUTIVE_LOSSES:
                continue
            lc = last_close.get(symbol)
            if lc is not None:
                cd = Config.LOSS_COOLDOWN_MINUTES if lc[1] < 0 else Config.WIN_COOLDOWN_MINUTES
                if (ts - lc[0]).total_seconds() / 60 < cd:
                    continue

            window = b.loc[:ts]
            if len(window) < 2 * k + 3:
                continue
            sig = fib_signal(window)
            if sig is None or sig.key in acted:
                continue
            if not stop_distance_ok(float(window["close"].iloc[-1]), sig.stop):
                continue

            attempt = _try_entry(
                symbol, ts, gi, grid, b, sig, iv[symbol], equity, dte, sc, scenario, window
            )
            result.attempts.append(attempt["record"])
            if attempt["opened"] is not None:
                acted.add(sig.key)
                trades_opened += 1
                open_positions.append(attempt["opened"])

    # Any straggler still open (shouldn't happen after flatten) -> close at last bar.
    for op in open_positions:
        b = bars_by_symbol[op.symbol]
        ts = b.index[-1]
        spot = float(b.iloc[-1]["close"])
        mark = bs_price(spot, op.strike, _t_years(ts, op.dte), iv[op.symbol], op.opt_type)
        _close_position(op, ts, spot, mark, "FLATTEN", sc, scenario, result)


def _try_entry(symbol, ts, gi, grid, b, sig, iv, equity, dte, sc, scenario, window) -> dict:
    from trading.risk import RiskManager

    spot = float(window["close"].iloc[-1])
    direction = sig.direction
    opt_type = "call" if direction == Signal.LONG else "put"
    strike = math.ceil(spot) if opt_type == "call" else math.floor(spot)
    t = _t_years(ts, dte)
    premium = bs_price(spot, strike, t, iv, opt_type)
    delta = bs_delta(spot, strike, t, iv, opt_type)
    spread = modelled_spread(premium, sc) if premium > 0 else 0.0
    spread_pct = spread / premium if premium > 0 else 0.0

    confirm_close = None
    if gi + 1 < len(grid) and grid[gi + 1] in b.index:
        confirm_close = float(b.loc[grid[gi + 1], "close"])

    filters = assess_filters(
        direction, window, ts, entry_spread_pct=spread_pct,
        touch_close=spot, confirm_close=confirm_close,
    )
    blocker = blocked_by(filters)

    stop_distance = abs(spot - sig.stop)
    rm = RiskManager.__new__(RiskManager)  # sizing only; no state/persistence
    qty = rm.contracts_for_underlying_stop(equity, premium, abs(delta), stop_distance)

    rec = _base_record(symbol, ts, spot, sig, opt_type, strike, dte, premium, delta,
                       spread, spread_pct, window, filters, scenario)
    rec["intended_entry"] = round(premium, 2)
    rec["requested_qty"] = qty

    if blocker:
        rec.update(order_outcome="BLOCKED_FILTER", blocked_by=blocker)
        return {"record": rec, "opened": None}
    if qty < 1 or premium < 0.10:
        rec.update(order_outcome="NO_SIZE")
        return {"record": rec, "opened": None}

    # Execution: fill BUY delay_bars later at that bar's mid.
    fill_i = min(gi + sc.delay_bars, len(grid) - 1)
    fill_ts = grid[fill_i]
    fill_spot = float(b.loc[fill_ts, "close"]) if fill_ts in b.index else spot
    fill_mid = bs_price(fill_spot, strike, _t_years(fill_ts, dte), iv, opt_type)
    fill = simulate_fill("BUY", fill_mid, premium, scenario, f"{symbol}|{ts.isoformat()}|BUY", qty)
    rec.update(
        fill_delay_bars=fill.delay_bars, order_outcome=fill.outcome,
        sim_fill=fill.price, filled_qty=fill.qty,
    )
    if fill.outcome == "UNFILLED":
        return {"record": rec, "opened": None}

    planned_risk = min(abs(delta) * stop_distance, premium) * 100 * fill.qty
    op = _Open(
        symbol=symbol, underlying=symbol, direction=direction, opt_type=opt_type,
        strike=strike, qty=fill.qty, entry=fill.price, stop_ul=sig.stop,
        target_ul=sig.target, opened_ts=fill_ts, dte=dte,
        planned_risk=round(planned_risk, 2), rec=rec,
        opt_mfe=fill.price, opt_mae=fill.price,
        ul_mfe=spot, ul_mae=spot,
    )
    rec["planned_risk"] = op.planned_risk
    return {"record": rec, "opened": op}


def _base_record(symbol, ts, spot, sig, opt_type, strike, dte, premium, delta,
                 spread, spread_pct, window, filters, scenario) -> dict:
    bid = round(max(premium - spread / 2, 0.0), 2)
    ask = round(premium + spread / 2, 2)
    return {
        "strategy_version": Config.STRATEGY_VERSION,
        "config_hash": config_hash(),
        "scenario": scenario,
        "symbol": symbol,
        "signal_ts": ts.isoformat(),
        "date": ts.date().isoformat(),
        "underlying_price": round(spot, 2),
        "direction": direction_str(sig.direction),
        "opt_type": opt_type,
        "strike": strike,
        "dte": dte,
        "dte_bucket": f"{dte}DTE",
        "contract_bid": bid,
        "contract_ask": ask,
        "spread_pct": round(spread_pct, 4),
        "delta": round(delta, 3),
        "stop_ul": sig.stop,
        "target_ul": sig.target,
        "regime_trend": trend_state(window, Config.FIB_TREND_EMA),
        "regime_vol": vol_bucket(realized_vol(window)),
        "tod_bucket": tod_bucket(ts),
        "filters": {f.name: {"enabled": f.enabled, "pass": f.passed, "value": f.value}
                    for f in filters},
        # populated later:
        "order_outcome": "PENDING", "sim_fill": 0.0, "filled_qty": 0,
        "fill_delay_bars": 0, "exit_reason": "", "exit_premium": 0.0,
        "pnl": 0.0, "realized_r": 0.0, "planned_risk": 0.0,
        "opt_mfe": 0.0, "opt_mae": 0.0, "ul_mfe": 0.0, "ul_mae": 0.0,
    }


def _close_position(op: _Open, ts, spot, mark, reason, sc, scenario, result: RunResult) -> None:
    fill = simulate_fill("SELL", mark, op.entry, scenario, f"{op.symbol}|{ts.isoformat()}|SELL", op.qty)
    exit_premium = fill.price
    pnl = round((exit_premium - op.entry) * 100 * op.qty, 2)
    rec = op.rec
    rec.update(
        exit_reason=reason,
        exit_ts=ts.isoformat(),
        exit_premium=exit_premium,
        pnl=pnl,
        realized_r=round(pnl / op.planned_risk, 2) if op.planned_risk > 0 else 0.0,
        opt_mfe=round(op.opt_mfe, 2),
        opt_mae=round(op.opt_mae, 2),
        ul_mfe=round(op.ul_mfe, 2),
        ul_mae=round(op.ul_mae, 2),
    )
    result.trades.append(rec)


def direction_str(direction) -> str:
    return "LONG" if direction == Signal.LONG else "SHORT"


# ------------------------------------------------------------------ entrypoint
def run_fib_sim(
    bars_by_symbol: dict[str, pd.DataFrame],
    equity: float = 100_000.0,
    scenario: str | None = None,
    dte: int = 0,
) -> RunResult:
    """Run the engine-parity fib simulation across all provided symbols/days.

    bars_by_symbol: {symbol: 1-min RTH DataFrame spanning the period}.
    Returns a RunResult with per-trade research records and every attempt.
    """
    scenario = (scenario or Config.EXEC_SCENARIO).lower()
    get_scenario(scenario)  # validate early
    result = RunResult(scenario=scenario, dte=dte)

    # Group each symbol's bars by calendar day, then simulate day by day.
    per_symbol_days = {
        s: {str(d): g for d, g in b.groupby(b.index.date)}
        for s, b in bars_by_symbol.items()
    }
    all_days = sorted(set().union(*[set(d) for d in per_symbol_days.values()]))
    for day in all_days:
        day_bars = {
            s: per_symbol_days[s][day]
            for s in bars_by_symbol
            if day in per_symbol_days[s] and not per_symbol_days[s][day].empty
        }
        if day_bars:
            _simulate_day(day, day_bars, equity, scenario, dte, result)
    return result
