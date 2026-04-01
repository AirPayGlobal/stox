"""
Pairs Trading — Dynamic Cointegration + Kalman-Filtered Hedge Ratios
=====================================================================
Extends ``analysis/pairs_trading.py`` with two key upgrades:

1. **ADF cointegration testing** — weekly health check that drops pairs
   whose spreads are no longer stationary (3 consecutive failures).

2. **Kalman-filtered hedge ratio** — replaces the fixed OLS window beta
   with a state-space estimate that adapts as the relationship drifts.
   Implemented from scratch with numpy (no pykalman dependency).

Public API
----------
    from analysis.pairs_dynamic import (
        KalmanHedgeRatio,
        test_cointegration,
        run_weekly_cointegration_check,
        calculate_kalman_zscore,
        screen_pairs_dynamic,
    )
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

from analysis.pairs_trading import PAIRS, PairSignal
from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Filesystem paths for persistent state
# ---------------------------------------------------------------------------

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

_COINT_LOG   = os.path.join(_LOG_DIR, "pairs_cointegration.json")
_HEDGE_LOG   = os.path.join(_LOG_DIR, "pairs_hedge_ratios.json")

# ---------------------------------------------------------------------------
# Kalman Filter Hedge Ratio
# ---------------------------------------------------------------------------

class KalmanHedgeRatio:
    """
    Online Kalman filter for a time-varying hedge ratio (scalar random walk).

    Observation model:  log_A[t] = beta * log_B[t] + eps,  eps ~ N(0, R)
    State transition:   beta[t]  = beta[t-1] + w,          w   ~ N(0, Q)

    Parameters
    ----------
    Q : float
        Transition noise variance (process noise).  Default 1e-5.
    R : float or None
        Observation noise variance.  If None it is estimated from the
        OLS residual variance on the first 60 observations.
    """

    def __init__(self, Q: float = 1e-5, R: Optional[float] = None) -> None:
        self.Q = Q
        self._R_override = R

        # State
        self._beta: float = 1.0
        self._P: float = 1.0          # state covariance
        self._R: float = R if R is not None else 1.0

        self._beta_history: list[float] = []

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ols_beta(log_a: np.ndarray, log_b: np.ndarray) -> float:
        """OLS slope of log_a ~ log_b (no intercept for simplicity)."""
        denom = float(np.dot(log_b, log_b))
        if denom == 0.0:
            return 1.0
        return float(np.dot(log_b, log_a) / denom)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fit(self, log_a: pd.Series, log_b: pd.Series) -> None:
        """
        Initialise state with OLS on the first 60 observations, then run
        the Kalman filter over the full series to build beta history.

        Parameters
        ----------
        log_a, log_b : pd.Series
            Log-price series (must share the same index and length >= 2).
        """
        a = np.asarray(log_a, dtype=float)
        b = np.asarray(log_b, dtype=float)
        n = len(a)
        if n < 2:
            raise ValueError("fit() requires at least 2 observations")

        init_obs = min(60, n)
        self._beta = self._ols_beta(a[:init_obs], b[:init_obs])
        self._P    = 1.0

        # Estimate R from OLS residuals on the warm-up window if not supplied
        if self._R_override is None:
            resid = a[:init_obs] - self._beta * b[:init_obs]
            var   = float(np.var(resid)) if len(resid) > 1 else 1.0
            self._R = max(var, 1e-8)   # guard against zero variance
        else:
            self._R = self._R_override

        self._beta_history = []
        for t in range(n):
            self.update(float(a[t]), float(b[t]))

    def update(self, log_a_t: float, log_b_t: float) -> float:
        """
        Perform one Kalman predict+update step.

        Parameters
        ----------
        log_a_t, log_b_t : float
            Current log-prices.

        Returns
        -------
        float
            Updated hedge ratio beta.
        """
        # --- Predict ---
        beta_pred = self._beta
        P_pred    = self._P + self.Q

        # --- Update ---
        h        = log_b_t                    # observation coefficient
        residual = log_a_t - beta_pred * h
        S        = P_pred * h ** 2 + self._R
        if S == 0.0:
            S = 1e-10
        K = P_pred * h / S

        self._beta = beta_pred + K * residual
        self._P    = (1.0 - K * h) * P_pred

        self._beta_history.append(self._beta)
        return self._beta

    def get_hedge_ratio(self) -> float:
        """Return the current (latest) hedge ratio estimate."""
        return self._beta

    def get_hedge_history(self) -> pd.Series:
        """Return the full time-series of hedge ratio estimates as a pd.Series."""
        return pd.Series(self._beta_history, dtype=float)


# ---------------------------------------------------------------------------
# ADF Cointegration Test
# ---------------------------------------------------------------------------

def test_cointegration(
    prices_a: pd.Series,
    prices_b: pd.Series,
) -> dict:
    """
    Run an Augmented Dickey-Fuller test on the log-price spread
    ``log(A) - hedge_ratio * log(B)`` to assess cointegration.

    The hedge ratio is estimated via OLS on the full window.

    Parameters
    ----------
    prices_a, prices_b : pd.Series
        Raw price series (positive values expected).

    Returns
    -------
    dict with keys:
        p_value          : float
        is_cointegrated  : bool  (True when p_value < 0.05)
        adf_stat         : float
        critical_5pct    : float
    """
    log_a = np.log(prices_a.astype(float))
    log_b = np.log(prices_b.astype(float))

    # OLS hedge ratio (full window)
    denom = float(np.dot(log_b, log_b))
    beta  = float(np.dot(log_b, log_a) / denom) if denom != 0 else 1.0

    spread = log_a - beta * log_b

    try:
        result = adfuller(spread.dropna(), autolag="AIC")
    except Exception as exc:
        logger.warning("ADF test failed: %s", exc)
        return {
            "p_value":         1.0,
            "is_cointegrated": False,
            "adf_stat":        float("nan"),
            "critical_5pct":   float("nan"),
        }

    adf_stat      = float(result[0])
    p_value       = float(result[1])
    critical_5pct = float(result[4].get("5%", float("nan")))

    return {
        "p_value":         p_value,
        "is_cointegrated": p_value < 0.05,
        "adf_stat":        adf_stat,
        "critical_5pct":   critical_5pct,
    }


# ---------------------------------------------------------------------------
# Persistent cointegration state helpers
# ---------------------------------------------------------------------------

def _load_coint_state() -> dict:
    """Load the persisted cointegration failure-count state from JSON."""
    if not os.path.exists(_COINT_LOG):
        return {}
    try:
        with open(_COINT_LOG) as fh:
            raw = json.load(fh)
        # We only care about the ``state`` sub-key written by this module
        return raw.get("state", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _save_coint_event(event_payload: dict) -> None:
    """
    Append-write a cointegration check event to ``logs/pairs_cointegration.json``.

    The file contains a JSON object at the top level with:
        ``state``  — per-pair consecutive-failure counters
        ``events`` — list of historical check records
    """
    existing: dict = {}
    if os.path.exists(_COINT_LOG):
        try:
            with open(_COINT_LOG) as fh:
                existing = json.load(fh)
        except (json.JSONDecodeError, OSError):
            existing = {}

    events = existing.get("events", [])
    events.append(event_payload)

    existing["events"] = events
    existing["state"]  = event_payload.get("state", existing.get("state", {}))

    with open(_COINT_LOG, "w") as fh:
        json.dump(existing, fh, indent=2, default=str)


# ---------------------------------------------------------------------------
# Weekly cointegration check
# ---------------------------------------------------------------------------

def run_weekly_cointegration_check(
    pairs: list[tuple[str, str]],
    data: dict[str, pd.DataFrame],
) -> dict:
    """
    Run ADF cointegration tests for all supplied pairs and update the
    persistent failure-count state.  A pair is dropped from the active set
    after **3 consecutive** ADF failures (p_value >= 0.05).

    Parameters
    ----------
    pairs : list of (symbol_a, symbol_b) tuples
    data  : price DataFrames keyed by symbol; each must have a ``"close"`` column

    Returns
    -------
    dict with keys:
        active_pairs  : list of [str, str]
        dropped_pairs : list of [str, str]
        results       : {pair_str: {p_value, is_cointegrated, adf_stat,
                                    critical_5pct, consecutive_failures}}
    """
    # Load existing per-pair failure counters  {pair_str: int}
    state: dict[str, int] = _load_coint_state()

    active_pairs:  list[list[str]] = []
    dropped_pairs: list[list[str]] = []
    results: dict[str, dict] = {}

    for sym_a, sym_b in pairs:
        pair_str = f"{sym_a}/{sym_b}"

        if sym_a not in data or sym_b not in data:
            logger.warning("Cointegration check: missing data for %s", pair_str)
            # Keep pair active but skip the test
            active_pairs.append([sym_a, sym_b])
            results[pair_str] = {
                "p_value":              None,
                "is_cointegrated":      None,
                "adf_stat":             None,
                "critical_5pct":        None,
                "consecutive_failures": state.get(pair_str, 0),
                "skipped":              True,
            }
            continue

        prices_a = data[sym_a]["close"].dropna()
        prices_b = data[sym_b]["close"].dropna()
        aligned  = pd.concat([prices_a, prices_b], axis=1).dropna()

        if len(aligned) < 30:
            logger.warning(
                "Cointegration check: insufficient data for %s (%d rows)",
                pair_str, len(aligned),
            )
            active_pairs.append([sym_a, sym_b])
            results[pair_str] = {
                "p_value":              None,
                "is_cointegrated":      None,
                "adf_stat":             None,
                "critical_5pct":        None,
                "consecutive_failures": state.get(pair_str, 0),
                "skipped":              True,
            }
            continue

        coint = test_cointegration(aligned.iloc[:, 0], aligned.iloc[:, 1])

        if coint["is_cointegrated"]:
            # Reset failure counter on a pass
            state[pair_str] = 0
        else:
            state[pair_str] = state.get(pair_str, 0) + 1

        consecutive = state[pair_str]

        results[pair_str] = {
            **coint,
            "consecutive_failures": consecutive,
        }

        if consecutive >= 3:
            logger.warning(
                "Dropping pair %s — failed ADF for %d consecutive weeks",
                pair_str, consecutive,
            )
            dropped_pairs.append([sym_a, sym_b])
        else:
            active_pairs.append([sym_a, sym_b])

    # Persist event
    event = {
        "ts":           datetime.now(timezone.utc).isoformat(),
        "module":       "pairs_dynamic",
        "event":        "cointegration_check",
        "active_pairs": active_pairs,
        "dropped_pairs": dropped_pairs,
        "results":      results,
        "state":        state,
    }
    _save_coint_event(event)

    logger.info(
        "Cointegration check complete — active: %d, dropped: %d",
        len(active_pairs), len(dropped_pairs),
    )
    return {
        "active_pairs":  active_pairs,
        "dropped_pairs": dropped_pairs,
        "results":       results,
    }


# ---------------------------------------------------------------------------
# Dynamic z-score using Kalman residuals
# ---------------------------------------------------------------------------

def calculate_kalman_zscore(
    prices_a: pd.Series,
    prices_b: pd.Series,
    kalman: KalmanHedgeRatio,
    window: int = 60,
) -> pd.Series:
    """
    Compute the rolling z-score of the spread, where the hedge ratio at
    each time step is taken from the Kalman filter (not a fixed OLS value).

    The Kalman filter is *re-fitted* from scratch inside this function so
    that the residuals are computed with the state that existed at each
    point in time (i.e. no look-ahead bias).

    Parameters
    ----------
    prices_a, prices_b : pd.Series — raw price series
    kalman             : KalmanHedgeRatio — instance (will be re-fitted)
    window             : int — rolling window for mean/std of residuals

    Returns
    -------
    pd.Series — z-scores aligned to prices_a.index
    """
    log_a = np.log(prices_a.astype(float))
    log_b = np.log(prices_b.astype(float))

    # Fit the Kalman filter over the full series (builds beta history)
    kalman.fit(log_a, log_b)

    betas    = kalman.get_hedge_history()
    log_a_arr = np.asarray(log_a)
    log_b_arr = np.asarray(log_b)

    n = min(len(log_a_arr), len(betas))
    residuals = log_a_arr[:n] - betas.values[:n] * log_b_arr[:n]
    spread    = pd.Series(residuals, index=prices_a.index[:n])

    rolling_mean = spread.rolling(window).mean()
    rolling_std  = spread.rolling(window).std().replace(0.0, np.nan)
    z_series     = (spread - rolling_mean) / rolling_std

    return z_series


# ---------------------------------------------------------------------------
# Hedge ratio drift logging
# ---------------------------------------------------------------------------

def _log_hedge_drift(
    pair_str: str,
    beta_history: pd.Series,
) -> None:
    """
    Append a hedge ratio drift record to ``logs/pairs_hedge_ratios.json``.

    Parameters
    ----------
    pair_str     : str      — e.g. "MSFT/GOOGL"
    beta_history : pd.Series — full Kalman beta history for this pair
    """
    if len(beta_history) == 0:
        return

    current_beta    = float(beta_history.iloc[-1])
    # Approximate 7-day and 30-day look-backs (assumes daily bars)
    beta_7d_change  = (
        float(current_beta - beta_history.iloc[-7])
        if len(beta_history) >= 7 else float("nan")
    )
    beta_30d_change = (
        float(current_beta - beta_history.iloc[-30])
        if len(beta_history) >= 30 else float("nan")
    )

    record = {
        "ts":             datetime.now(timezone.utc).isoformat(),
        "pair":           pair_str,
        "current_beta":   current_beta,
        "beta_7d_change": beta_7d_change,
        "beta_30d_change": beta_30d_change,
    }

    existing: list = []
    if os.path.exists(_HEDGE_LOG):
        try:
            with open(_HEDGE_LOG) as fh:
                existing = json.load(fh)
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, OSError):
            existing = []

    existing.append(record)
    with open(_HEDGE_LOG, "w") as fh:
        json.dump(existing, fh, indent=2, default=str)


# ---------------------------------------------------------------------------
# Integrated signal generation
# ---------------------------------------------------------------------------

# Transaction cost constants
_SLIPPAGE_PCT = 0.001   # 0.1% slippage on each fill
_COMMISSION   = 0.01    # $0.01 per share commission


def _entry_cost(price: float, qty: int) -> float:
    """Estimated one-way cost for entering a position."""
    return price * qty * _SLIPPAGE_PCT + qty * _COMMISSION


def _exit_cost(price: float, qty: int) -> float:
    """Estimated one-way cost for exiting a position (same structure)."""
    return price * qty * _SLIPPAGE_PCT + qty * _COMMISSION


def screen_pairs_dynamic(
    data: dict[str, pd.DataFrame],
    active_pairs: Optional[list[tuple[str, str]]] = None,
) -> list[dict]:
    """
    Screen pairs for entry/exit signals using Kalman-filtered z-scores.
    Returns a list of signal dicts in the same shape as ``screen_pairs()``
    plus extra Kalman-specific fields and transaction cost estimates.

    Parameters
    ----------
    data         : price DataFrames keyed by symbol
    active_pairs : pairs to evaluate; defaults to full PAIRS list from
                   ``pairs_trading.py`` when None

    Returns
    -------
    list of dicts, each containing:
        symbol_a, symbol_b, signal, z_score, hedge_ratio,
        entry_cost_estimate, exit_cost_estimate
    """
    pairs_to_use: list[tuple[str, str]] = (
        [tuple(p) for p in active_pairs]  # type: ignore[misc]
        if active_pairs is not None
        else PAIRS
    )

    entry_z = Config.PAIRS_ENTRY_ZSCORE
    exit_z  = Config.PAIRS_EXIT_ZSCORE
    stop_z  = Config.PAIRS_STOP_ZSCORE
    window  = Config.PAIRS_WINDOW

    signals: list[dict] = []

    for sym_a, sym_b in pairs_to_use:
        if sym_a not in data or sym_b not in data:
            continue

        prices_a = data[sym_a]["close"].dropna()
        prices_b = data[sym_b]["close"].dropna()

        aligned = pd.concat([prices_a, prices_b], axis=1).dropna()
        aligned.columns = ["a", "b"]

        min_rows = window + 5
        if len(aligned) < min_rows:
            logger.debug(
                "Skipping %s/%s — only %d rows (need %d)",
                sym_a, sym_b, len(aligned), min_rows,
            )
            continue

        kalman = KalmanHedgeRatio(Q=1e-5)
        try:
            z_series = calculate_kalman_zscore(
                aligned["a"], aligned["b"], kalman, window=window
            )
        except Exception as exc:
            logger.warning("Kalman z-score failed for %s/%s: %s", sym_a, sym_b, exc)
            continue

        z = float(z_series.iloc[-1]) if not z_series.empty else float("nan")
        if np.isnan(z):
            continue

        beta      = kalman.get_hedge_ratio()
        pair_str  = f"{sym_a}/{sym_b}"

        # Log hedge ratio drift after every screen pass
        try:
            _log_hedge_drift(pair_str, kalman.get_hedge_history())
        except Exception as exc:
            logger.debug("Hedge drift log failed for %s: %s", pair_str, exc)

        # Determine signal
        price_a = float(aligned["a"].iloc[-1])
        price_b = float(aligned["b"].iloc[-1])
        leg_budget = Config.INITIAL_CAPITAL * Config.PAIRS_POSITION_PCT
        qty_a  = max(1, int(leg_budget / price_a))
        qty_b  = max(1, int(leg_budget / price_b))
        e_cost = _entry_cost(price_a, qty_a) + _entry_cost(price_b, qty_b)
        x_cost = _exit_cost(price_a, qty_a)  + _exit_cost(price_b, qty_b)

        if abs(z) > stop_z:
            sig = PairSignal.STOP
        elif abs(z) < exit_z:
            sig = PairSignal.EXIT
        elif z > entry_z:
            sig = PairSignal.LONG_B_SHORT_A   # A expensive vs B
        elif z < -entry_z:
            sig = PairSignal.LONG_A_SHORT_B   # B expensive vs A
        else:
            continue  # HOLD — no actionable signal

        logger.info(
            "Dynamic pairs signal: %s z=%.3f beta=%.4f → %s",
            pair_str, z, beta, sig,
        )

        signals.append({
            "symbol_a":             sym_a,
            "symbol_b":             sym_b,
            "signal":               sig,
            "z_score":              z,
            "hedge_ratio":          beta,
            "entry_cost_estimate":  e_cost,
            "exit_cost_estimate":   x_cost,
        })

    return signals
