"""
Live Factor Exposure Monitor
=============================
Computes real-time portfolio-level factor exposures and emits structured
daily logs.  Designed to run at end-of-day as part of the bot's risk
management pipeline.

Factors tracked
---------------
  Beta            — rolling 60-day portfolio beta vs SPY
  Sector exposure — % of total weight per GICS sector
  Momentum tilt   — average 12-1 month return of holdings vs SPY

Concentration alert
-------------------
If 3+ positions share the same sector AND their pairwise 30-day return
correlation exceeds 0.7, an alert is appended to the dashboard and
max_new_in_sector is set to 1.

Bear-regime beta reduction
--------------------------
trim_for_beta_target() returns an ordered list of symbols to trim
(highest beta first) until portfolio beta <= target_beta.
In BEAR regime, target_beta defaults to 0.5.

Caching
-------
All yfinance calls are cached with a 4-hour TTL using an in-process
dict so repeated calls within the same session are free.

Usage
-----
  from analysis.factor_monitor import get_factor_dashboard, trim_for_beta_target, run_eod_factor_check
"""
from __future__ import annotations

import json
import os
import time
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from analysis.sector_rotation import SYMBOL_TO_SECTOR, SECTOR_ETF_NAMES

logger = logging.getLogger("factor_monitor")

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_FACTOR_LOG = os.path.join(_LOG_DIR, "factor_monitor.json")

# ---------------------------------------------------------------------------
# yfinance cache  (4-hour TTL)
# ---------------------------------------------------------------------------

_YF_CACHE: dict[str, tuple[pd.DataFrame, float]] = {}
_YF_TTL = 4 * 3600  # seconds


def _yf_download(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """
    Fetch historical price data via yfinance with a 4-hour in-process cache.
    Returns a DataFrame with a 'Close' column (or empty DataFrame on failure).
    """
    cache_key = f"{ticker}|{period}|{interval}"
    if cache_key in _YF_CACHE:
        df_cached, ts = _YF_CACHE[cache_key]
        if time.time() - ts < _YF_TTL:
            return df_cached

    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df.empty:
            logger.warning(f"[factor_monitor] yfinance returned no data for {ticker}")
            return pd.DataFrame()
        # Normalise column names — yfinance returns capitalised names
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        _YF_CACHE[cache_key] = (df, time.time())
        return df
    except Exception as exc:
        logger.error(f"[factor_monitor] yfinance fetch failed for {ticker}: {exc}")
        return pd.DataFrame()


def _get_close_series(ticker: str, lookback_days: int) -> pd.Series:
    """Return a daily close price Series for `ticker` over `lookback_days`."""
    period = f"{max(lookback_days + 30, 90)}d"
    df = _yf_download(ticker, period=period, interval="1d")
    if df.empty:
        return pd.Series(dtype=float)
    col = "Close" if "Close" in df.columns else df.columns[0]
    series = df[col].dropna()
    if len(series) > lookback_days:
        series = series.iloc[-lookback_days:]
    return series


# ---------------------------------------------------------------------------
# Beta calculation
# ---------------------------------------------------------------------------

def _symbol_beta(symbol: str, spy_rets: pd.Series, lookback_days: int = 60) -> float:
    """Compute rolling beta of a single symbol vs SPY over lookback_days."""
    sym_prices = _get_close_series(symbol, lookback_days + 5)
    if sym_prices.empty or len(sym_prices) < 10:
        return 1.0  # fallback: assume market-like

    sym_rets = sym_prices.pct_change().dropna()

    # Align on common dates
    combined = pd.concat([sym_rets, spy_rets], axis=1, join="inner").dropna()
    combined.columns = ["sym", "spy"]

    if len(combined) < 10:
        return 1.0

    cov_matrix = combined.cov()
    spy_var = combined["spy"].var()
    if spy_var == 0:
        return 1.0
    return float(cov_matrix.loc["sym", "spy"] / spy_var)


def calculate_portfolio_beta(positions: dict, lookback_days: int = 60) -> float:
    """
    Calculate rolling portfolio beta vs SPY.

    positions: dict mapping symbol -> dict with at least {"market_value": float}
               or {"shares": int, "current_price": float}.

    Returns weighted average portfolio beta.
    """
    if not positions:
        return 0.0

    spy_prices = _get_close_series("SPY", lookback_days + 5)
    if spy_prices.empty:
        logger.warning("[factor_monitor] Could not fetch SPY data; returning beta=1.0")
        return 1.0

    spy_rets = spy_prices.pct_change().dropna()

    total_value = _total_market_value(positions)
    if total_value <= 0:
        return 0.0

    weighted_beta = 0.0
    for sym, pos_info in positions.items():
        mv = _market_value(pos_info)
        if mv <= 0:
            continue
        weight = mv / total_value
        beta = _symbol_beta(sym, spy_rets, lookback_days)
        weighted_beta += weight * beta

    return float(weighted_beta)


# ---------------------------------------------------------------------------
# Sector exposure
# ---------------------------------------------------------------------------

def calculate_sector_exposure(positions: dict) -> dict[str, float]:
    """
    Calculate % of portfolio weight allocated to each sector.

    Returns dict mapping sector ETF symbol (e.g. 'XLK') to weight fraction.
    Unknown symbols are grouped under 'UNKNOWN'.
    """
    if not positions:
        return {}

    total_value = _total_market_value(positions)
    if total_value <= 0:
        return {}

    sector_values: dict[str, float] = {}
    for sym, pos_info in positions.items():
        mv = _market_value(pos_info)
        sector = SYMBOL_TO_SECTOR.get(sym, "UNKNOWN")
        sector_values[sector] = sector_values.get(sector, 0.0) + mv

    return {s: v / total_value for s, v in sector_values.items()}


# ---------------------------------------------------------------------------
# Momentum tilt
# ---------------------------------------------------------------------------

def calculate_momentum_tilt(positions: dict) -> float:
    """
    12-1 month momentum tilt vs SPY.

    For each holding, compute (12-month return) - (1-month return).
    Return the portfolio-weighted average minus the same metric for SPY.
    Positive value = portfolio tilted toward momentum winners.
    """
    if not positions:
        return 0.0

    spy_mom = _momentum_12_1("SPY")
    total_value = _total_market_value(positions)
    if total_value <= 0:
        return 0.0

    weighted_mom = 0.0
    for sym, pos_info in positions.items():
        mv = _market_value(pos_info)
        if mv <= 0:
            continue
        weight = mv / total_value
        mom = _momentum_12_1(sym)
        weighted_mom += weight * mom

    return float(weighted_mom - spy_mom)


def _momentum_12_1(symbol: str) -> float:
    """12-1 month return: 12-month lookback minus most recent month."""
    prices = _get_close_series(symbol, 280)  # ~252 + buffer
    if len(prices) < 60:
        return 0.0
    ret_12 = float(prices.iloc[-1] / prices.iloc[0] - 1) if prices.iloc[0] != 0 else 0.0
    # Exclude the most recent month (~21 trading days)
    skip = min(21, len(prices) - 2)
    ret_1 = float(prices.iloc[-1] / prices.iloc[-skip - 1] - 1) if prices.iloc[-skip - 1] != 0 else 0.0
    return ret_12 - ret_1


# ---------------------------------------------------------------------------
# Concentration alerts
# ---------------------------------------------------------------------------

def _check_concentration_alerts(positions: dict) -> tuple[list[str], int]:
    """
    Detect sector concentration: 3+ positions in same sector with
    pairwise 30-day correlation > 0.7.

    Returns (alerts list, max_new_in_sector: 1 if alert triggered else None).
    """
    alerts: list[str] = []
    max_new_in_sector: int = 0  # 0 means no restriction

    if len(positions) < 3:
        return alerts, max_new_in_sector

    # Group symbols by sector
    sector_syms: dict[str, list[str]] = {}
    for sym in positions:
        sector = SYMBOL_TO_SECTOR.get(sym, "UNKNOWN")
        sector_syms.setdefault(sector, []).append(sym)

    for sector, syms in sector_syms.items():
        if len(syms) < 3:
            continue

        # Build 30-day return matrix for these symbols
        returns_dict: dict[str, pd.Series] = {}
        for sym in syms:
            prices = _get_close_series(sym, 35)
            if not prices.empty and len(prices) >= 10:
                returns_dict[sym] = prices.pct_change().dropna()

        if len(returns_dict) < 3:
            continue

        ret_df = pd.DataFrame(returns_dict).dropna()
        if len(ret_df) < 10:
            continue

        corr_matrix = ret_df.corr()

        # Check all pairs
        triggered = False
        cols = corr_matrix.columns.tolist()
        for i in range(len(cols)):
            if triggered:
                break
            for j in range(i + 1, len(cols)):
                corr_val = corr_matrix.iloc[i, j]
                if corr_val > 0.7:
                    triggered = True
                    break

        if triggered:
            sector_name = SECTOR_ETF_NAMES.get(sector, sector)
            alert_msg = (
                f"CONCENTRATION ALERT: {len(syms)} positions in {sector_name} "
                f"({sector}) with pairwise correlation > 0.70 — "
                f"symbols: {', '.join(syms)}"
            )
            alerts.append(alert_msg)
            logger.warning(f"[factor_monitor] {alert_msg}")
            max_new_in_sector = 1

    return alerts, max_new_in_sector


# ---------------------------------------------------------------------------
# Factor dashboard
# ---------------------------------------------------------------------------

def get_factor_dashboard(positions: dict, equity: float) -> dict:
    """
    Build a complete factor snapshot for the current portfolio.

    positions: dict mapping symbol -> {"shares": int, "current_price": float}
               or {"market_value": float}
    equity:    total portfolio equity (cash + positions)

    Returns a dict with beta, sector_exposure, momentum_tilt, alerts,
    max_new_in_sector, and position_count.
    """
    beta = calculate_portfolio_beta(positions)
    sector_exposure = calculate_sector_exposure(positions)
    momentum_tilt = calculate_momentum_tilt(positions)
    alerts, max_new_in_sector = _check_concentration_alerts(positions)

    # Top-heavy position alerts
    total_value = _total_market_value(positions)
    position_alerts: list[str] = []
    if total_value > 0:
        for sym, pos_info in positions.items():
            mv = _market_value(pos_info)
            weight = mv / total_value
            if weight > 0.15:
                position_alerts.append(
                    f"OVERWEIGHT: {sym} is {weight:.1%} of portfolio (limit 15%)"
                )

    all_alerts = alerts + position_alerts

    dashboard = {
        "as_of": datetime.utcnow().isoformat(),
        "equity": equity,
        "position_count": len(positions),
        "beta": round(beta, 4),
        "sector_exposure": {k: round(v, 4) for k, v in sector_exposure.items()},
        "momentum_tilt": round(momentum_tilt, 4),
        "alerts": all_alerts,
        "max_new_in_sector": max_new_in_sector if max_new_in_sector else None,
        "regime_beta_target": None,  # populated by trim_for_beta_target caller
    }

    return dashboard


# ---------------------------------------------------------------------------
# Beta trimming
# ---------------------------------------------------------------------------

def trim_for_beta_target(
    positions: dict,
    target_beta: float,
    current_beta: float,
) -> list[str]:
    """
    Return an ordered list of symbols to trim (highest individual beta first)
    until estimated portfolio beta drops to or below target_beta.

    In BEAR regime, the recommended target is 0.5.

    Note: this is a greedy approximation — it ranks symbols by their
    individual beta contribution and removes them one at a time.
    The caller is responsible for actually executing the trims.

    Returns an empty list if current_beta <= target_beta.
    """
    if current_beta <= target_beta or not positions:
        return []

    total_value = _total_market_value(positions)
    if total_value <= 0:
        return []

    # Fetch SPY returns once
    spy_prices = _get_close_series("SPY", 65)
    if spy_prices.empty:
        logger.warning("[factor_monitor] No SPY data — cannot compute trim list")
        return []
    spy_rets = spy_prices.pct_change().dropna()

    # Build weighted beta contributions
    contributions: list[tuple[str, float, float]] = []  # (symbol, beta, weight)
    for sym, pos_info in positions.items():
        mv = _market_value(pos_info)
        if mv <= 0:
            continue
        weight = mv / total_value
        beta = _symbol_beta(sym, spy_rets)
        contributions.append((sym, beta, weight))

    # Sort by beta descending (trim highest beta first)
    contributions.sort(key=lambda x: x[1], reverse=True)

    trim_list: list[str] = []
    simulated_beta = current_beta
    remaining_weight = 1.0

    for sym, beta, weight in contributions:
        if simulated_beta <= target_beta:
            break
        # Removing this position removes its weighted contribution;
        # the remaining weights are renormalised.
        contribution = beta * weight
        simulated_beta = (simulated_beta - contribution) / (1.0 - weight) if (1.0 - weight) > 0 else 0.0
        remaining_weight -= weight
        trim_list.append(sym)
        logger.info(
            f"[factor_monitor] Trim candidate: {sym} (beta={beta:.2f}, "
            f"weight={weight:.1%}) → est. portfolio beta after trim={simulated_beta:.2f}"
        )

    return trim_list


# ---------------------------------------------------------------------------
# End-of-day check
# ---------------------------------------------------------------------------

def run_eod_factor_check(positions: dict, equity: float) -> dict:
    """
    Fetch all factor data, build the dashboard, append to the daily JSON
    log, and return the dashboard dict.

    This is the main entry point for scheduled end-of-day execution.
    """
    logger.info(f"[factor_monitor] Running EOD factor check for {len(positions)} positions...")
    dashboard = get_factor_dashboard(positions, equity)

    # Determine bear regime to set beta target
    try:
        from analysis.regime import detect_regime, Regime
        regime = detect_regime()
        if regime == Regime.BEAR:
            trim = trim_for_beta_target(positions, target_beta=0.5, current_beta=dashboard["beta"])
            dashboard["regime_beta_target"] = 0.5
            if trim:
                dashboard["bear_regime_trim_candidates"] = trim
                dashboard["alerts"].append(
                    f"BEAR REGIME: recommend trimming {trim} to reach beta <= 0.50"
                )
    except Exception as exc:
        logger.warning(f"[factor_monitor] Regime check skipped: {exc}")

    # Append to daily log
    _write_factor_log(dashboard)

    logger.info(
        f"[factor_monitor] EOD complete: beta={dashboard['beta']:.2f} "
        f"alerts={len(dashboard['alerts'])}"
    )
    return dashboard


def _write_factor_log(dashboard: dict) -> None:
    """Append a single-line JSON entry to logs/factor_monitor.json."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    log_entry = {
        "date": date.today().isoformat(),
        "beta": dashboard.get("beta"),
        "sector_exposure": dashboard.get("sector_exposure", {}),
        "momentum_tilt": dashboard.get("momentum_tilt"),
        "alerts": dashboard.get("alerts", []),
    }
    try:
        with open(_FACTOR_LOG, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as exc:
        logger.error(f"[factor_monitor] Could not write factor log: {exc}")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _market_value(pos_info) -> float:
    """
    Extract market value from a position dict.
    Supports {"market_value": float} or {"shares": int, "current_price": float}.
    """
    if isinstance(pos_info, dict):
        if "market_value" in pos_info:
            return float(pos_info["market_value"])
        if "shares" in pos_info and "current_price" in pos_info:
            return float(pos_info["shares"]) * float(pos_info["current_price"])
        # Fallback: try numeric value directly
        if "qty" in pos_info and "current_price" in pos_info:
            return float(pos_info["qty"]) * float(pos_info["current_price"])
    if isinstance(pos_info, (int, float)):
        return float(pos_info)
    return 0.0


def _total_market_value(positions: dict) -> float:
    return sum(_market_value(v) for v in positions.values())
