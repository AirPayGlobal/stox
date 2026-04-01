"""
ML Pipeline — Upgraded Signal Booster
======================================
Drop-in replacement for ``analysis/ml_signals.py`` with an extended feature set,
walk-forward cross-validation, monthly retraining on real trade outcomes,
hyperparameter tuning, and structured JSON logging.

Public API (matches ml_signals.py):
  is_ml_approved(symbol, df, min_prob)  -> bool
  get_ml_probability(symbol, df)        -> Optional[float]

Extended API:
  update_model_with_trade_outcomes(symbol, closed_trades) -> None
  get_dynamic_threshold(regime)                           -> float
  run_walk_forward_cv(symbol, df)                         -> dict
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("ml_pipeline")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR = _REPO_ROOT / "logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

_MODEL_LOG_PATH = _LOGS_DIR / "ml_model_log.json"
_TRADE_OUTCOMES_PATH = _LOGS_DIR / "ml_trade_outcomes.json"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MIN_SAMPLES = 150       # rows needed before model trains
FORWARD_DAYS = 5        # predict N-day forward profitability
MIN_WIN_PCT = 0.005     # label=1 if forward return > 0.5%
_CACHE_TTL = 86_400     # 24-h per-symbol model cache (seconds)
_RETRAIN_TTL = 30 * 86_400  # monthly retrain interval (seconds)
_NEW_OUTCOMES_TRIGGER = 20  # retrain after this many new trade-outcome rows

# Walk-forward window (approximate trading days)
_WF_TRAIN_MONTHS = 9
_WF_VAL_MONTHS = 3

# Regime encoding for the extended feature set
_REGIME_MAP: dict[str, float] = {
    "BULL": 1.0,
    "RANGING": 0.5,
    "HIGH_VOL": -1.0,
    "BEAR": -0.5,
}

# Dynamic threshold by regime
_DYNAMIC_THRESHOLDS: dict[str, float] = {
    "BULL": 0.52,
    "RANGING": 0.52,
    "HIGH_VOL": 0.65,
    "BEAR": 0.58,
}

# Hyperparameter search grid
_HP_GRID: list[dict] = [
    {"max_depth": md, "n_estimators": ne, "min_samples_leaf": msl}
    for md in [3, 5, 8]
    for ne in [100, 200]
    for msl in [5, 10, 20]
]

# Extended feature column names (order matters — must stay stable)
_FEATURE_COLS = [
    # --- original 10 ---
    "rsi",
    "macd_hist",
    "bb_pos",
    "ema_fast_pct",
    "ema_slow_pct",
    "ema_trend_pct",
    "vol_ratio",
    "atr_ratio",
    "mom5",
    "mom20",
    # --- new 5 ---
    "regime_encoded",
    "sentiment_score",
    "signal_score_norm",
    "vix_norm",
    "sector_momentum",
]

# ---------------------------------------------------------------------------
# Per-symbol in-memory state
# ---------------------------------------------------------------------------
# {symbol: {"model": Pipeline, "ts": float, "last_retrain_ts": float,
#           "auc": float, "suspended": bool, "n_outcomes_at_retrain": int}}
_symbol_state: dict[str, dict] = {}

_SKLEARN_AVAILABLE: Optional[bool] = None


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log_event(event: str, symbol: str, **kwargs) -> None:
    """Append a structured JSON line to logs/ml_model_log.json."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "module": "ml_pipeline",
        "event": event,
        "symbol": symbol,
        **kwargs,
    }
    try:
        with _MODEL_LOG_PATH.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("ml_pipeline: could not write to log: %s", exc)


# ---------------------------------------------------------------------------
# sklearn availability guard
# ---------------------------------------------------------------------------

def _check_sklearn() -> bool:
    global _SKLEARN_AVAILABLE
    if _SKLEARN_AVAILABLE is None:
        try:
            import sklearn  # noqa: F401
            _SKLEARN_AVAILABLE = True
        except ImportError:
            logger.warning("scikit-learn not installed — ml_pipeline disabled")
            _SKLEARN_AVAILABLE = False
    return _SKLEARN_AVAILABLE


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def _build_features(
    df: pd.DataFrame,
    *,
    regime: Optional[str] = None,
    sentiment_score: float = 0.0,
    signal_score: float = 50.0,
    vix: float = 20.0,
    sector_momentum: float = 0.0,
) -> pd.DataFrame:
    """
    Compute all 15 normalised features from OHLCV data plus optional
    contextual scalars.  Returns a DataFrame with columns == _FEATURE_COLS.

    Extended scalars are broadcast as constants across the full index so that
    the DataFrame can be used for both training (row-per-bar) and inference
    (last bar only).
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    out = pd.DataFrame(index=df.index)

    # --- RSI (normalised 0-1) ---
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = (100 - 100 / (1 + rs)) / 100

    # --- MACD histogram / price ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = (macd - sig) / close.replace(0, np.nan)

    # --- Bollinger Band position (0=lower, 1=upper) ---
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_range = (2 * std20).replace(0, np.nan)
    out["bb_pos"] = (close - (sma20 - std20)) / bb_range

    # --- EMA spreads ---
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    safe = close.replace(0, np.nan)
    out["ema_fast_pct"] = (close - ema9) / safe
    out["ema_slow_pct"] = (close - ema21) / safe
    out["ema_trend_pct"] = (close - ema50) / safe

    # --- Volume ratio vs 20-day average ---
    vol_avg = volume.rolling(20).mean().replace(0, np.nan)
    out["vol_ratio"] = volume / vol_avg

    # --- ATR / price ---
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr_ratio"] = tr.rolling(14).mean() / safe

    # --- Momentum ---
    out["mom5"] = close.pct_change(5)
    out["mom20"] = close.pct_change(20)

    # --- Extended features (scalars broadcast to full index) ---
    out["regime_encoded"] = _REGIME_MAP.get(regime or "BULL", 1.0)
    out["sentiment_score"] = float(sentiment_score)
    out["signal_score_norm"] = float(signal_score) / 100.0
    out["vix_norm"] = float(np.clip(vix / 40.0, 0.0, 1.0))
    out["sector_momentum"] = float(sector_momentum)

    return out[_FEATURE_COLS]


# ---------------------------------------------------------------------------
# Trade outcome persistence
# ---------------------------------------------------------------------------

def _load_trade_outcomes() -> list[dict]:
    if not _TRADE_OUTCOMES_PATH.exists():
        return []
    try:
        with _TRADE_OUTCOMES_PATH.open() as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def _save_trade_outcomes(outcomes: list[dict]) -> None:
    try:
        with _TRADE_OUTCOMES_PATH.open("w") as fh:
            json.dump(outcomes, fh, indent=2)
    except OSError as exc:
        logger.warning("ml_pipeline: could not save trade outcomes: %s", exc)


def _build_features_from_outcome(trade: dict) -> Optional[dict]:
    """
    Convert a closed-trade dict to a flat feature row compatible with
    _FEATURE_COLS.  Returns None if the dict is malformed.
    """
    try:
        return {
            # Price-pattern features are not available from trade dicts;
            # fill with 0.0 so the row is structurally valid but de-weighted
            # by the RandomForest via ensemble averaging with price rows.
            "rsi": 0.0,
            "macd_hist": 0.0,
            "bb_pos": 0.5,
            "ema_fast_pct": 0.0,
            "ema_slow_pct": 0.0,
            "ema_trend_pct": 0.0,
            "vol_ratio": 1.0,
            "atr_ratio": 0.0,
            "mom5": 0.0,
            "mom20": 0.0,
            # Extended features from trade metadata
            "regime_encoded": _REGIME_MAP.get(trade.get("regime", "BULL"), 1.0),
            "sentiment_score": float(trade.get("sentiment_score", 0.0)),
            "signal_score_norm": float(trade.get("signal_score", 50.0)) / 100.0,
            "vix_norm": float(np.clip(trade.get("vix_at_entry", 20.0) / 40.0, 0.0, 1.0)),
            "sector_momentum": 0.0,  # not available in trade dict
            "label": int(float(trade.get("pnl_pct", 0.0)) > MIN_WIN_PCT),
        }
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Hyperparameter tuning
# ---------------------------------------------------------------------------

def _tune_hyperparams(X: np.ndarray, y: np.ndarray) -> dict:
    """
    Grid-search over _HP_GRID using TimeSeriesSplit(n_splits=3).
    Returns the best hyperparameter dict and its mean CV AUC.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score

    tscv = TimeSeriesSplit(n_splits=3)
    best_params: dict = _HP_GRID[0]
    best_auc: float = 0.0

    for params in _HP_GRID:
        clf = RandomForestClassifier(
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
            **params,
        )
        try:
            scores = cross_val_score(clf, X, y, cv=tscv, scoring="roc_auc", error_score=0.0)
            mean_auc = float(scores.mean())
        except Exception:
            mean_auc = 0.0

        if mean_auc > best_auc:
            best_auc = mean_auc
            best_params = params

    return {"params": best_params, "cv_auc": best_auc}


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------

def _train_model(
    symbol: str,
    df: pd.DataFrame,
    extra_outcome_rows: Optional[list[dict]] = None,
) -> Optional[object]:
    """
    Train a RandomForest pipeline.  Optionally merges trade-outcome feature
    rows with the price-pattern rows before fitting.

    Returns fitted sklearn Pipeline or None on failure.
    """
    if not _check_sklearn():
        return None

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    features = _build_features(df)
    close = df["close"].astype(float)

    fwd_ret = close.shift(-FORWARD_DAYS) / close - 1
    labels = (fwd_ret > MIN_WIN_PCT).astype(int)

    combined = features.copy()
    combined["label"] = labels
    combined = combined.dropna()

    if len(combined) > FORWARD_DAYS:
        combined = combined.iloc[:-FORWARD_DAYS]

    # Merge trade-outcome rows if provided
    if extra_outcome_rows:
        outcome_df = pd.DataFrame(
            [r for r in extra_outcome_rows if r is not None]
        )
        if not outcome_df.empty:
            # Align columns; fill any missing feature cols with 0
            for col in _FEATURE_COLS:
                if col not in outcome_df.columns:
                    outcome_df[col] = 0.0
            outcome_df = outcome_df[_FEATURE_COLS + ["label"]]
            combined = pd.concat([combined, outcome_df], ignore_index=True)

    if len(combined) < MIN_SAMPLES:
        logger.debug(
            "ML %s: only %d samples (need %d) — skipping", symbol, len(combined), MIN_SAMPLES
        )
        return None

    X = combined[_FEATURE_COLS].values.astype(float)
    y = combined["label"].values.astype(int)

    if len(np.unique(y)) < 2:
        logger.debug("ML %s: single-class labels — skipping", symbol)
        return None

    tune_result = _tune_hyperparams(X, y)
    best_params = tune_result["params"]
    cv_auc = tune_result["cv_auc"]

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                RandomForestClassifier(
                    class_weight="balanced",
                    random_state=42,
                    n_jobs=-1,
                    **best_params,
                ),
            ),
        ]
    )
    model.fit(X, y)
    model.feature_names = _FEATURE_COLS  # type: ignore[attr-defined]

    # Feature importance (top 5)
    importances = model.named_steps["clf"].feature_importances_
    top5 = sorted(
        zip(_FEATURE_COLS, importances.tolist()),
        key=lambda t: t[1],
        reverse=True,
    )[:5]

    logger.info(
        "ML: trained %s on %d samples (pos_rate=%.1f%% cv_auc=%.3f params=%s)",
        symbol,
        len(combined),
        float(y.mean()) * 100,
        cv_auc,
        best_params,
    )

    _log_event(
        "retrain",
        symbol,
        n_samples=len(combined),
        pos_rate=round(float(y.mean()), 4),
        cv_auc=round(cv_auc, 4),
        best_hyperparams=best_params,
    )
    _log_event(
        "feature_importance",
        symbol,
        top5=[{"feature": f, "importance": round(imp, 5)} for f, imp in top5],
    )

    return model


# ---------------------------------------------------------------------------
# Walk-forward cross-validation
# ---------------------------------------------------------------------------

def run_walk_forward_cv(symbol: str, df: pd.DataFrame) -> dict:
    """
    12-month rolling walk-forward cross-validation.

    Train window: ~9 months (~189 trading days)
    Validation window: ~3 months (~63 trading days)
    Slide: 1 month (~21 trading days)

    Returns:
        {
            "windows": [{"train_rows": int, "val_rows": int, "auc": float}, ...],
            "mean_auc": float,
            "std_auc": float,
            "is_reliable": bool,
        }
    """
    if not _check_sklearn():
        return {"windows": [], "mean_auc": 0.0, "std_auc": 0.0, "is_reliable": False}

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    features = _build_features(df)
    close = df["close"].astype(float)
    fwd_ret = close.shift(-FORWARD_DAYS) / close - 1
    labels = (fwd_ret > MIN_WIN_PCT).astype(int)

    combined = features.copy()
    combined["label"] = labels
    combined = combined.dropna()
    if len(combined) > FORWARD_DAYS:
        combined = combined.iloc[:-FORWARD_DAYS]

    X = combined[_FEATURE_COLS].values.astype(float)
    y = combined["label"].values.astype(int)

    if len(X) < MIN_SAMPLES or len(np.unique(y)) < 2:
        return {"windows": [], "mean_auc": 0.0, "std_auc": 0.0, "is_reliable": False}

    # Approximate trading-day counts for window sizes
    _TRAIN_DAYS = int(_WF_TRAIN_MONTHS * 21)   # ~189
    _VAL_DAYS = int(_WF_VAL_MONTHS * 21)        # ~63
    _SLIDE_DAYS = 21                             # 1 month

    windows: list[dict] = []
    n = len(X)

    start = 0
    while True:
        train_end = start + _TRAIN_DAYS
        val_end = train_end + _VAL_DAYS

        if val_end > n:
            break

        X_train, y_train = X[start:train_end], y[start:train_end]
        X_val, y_val = X[train_end:val_end], y[train_end:val_end]

        if len(np.unique(y_train)) < 2 or len(np.unique(y_val)) < 2:
            start += _SLIDE_DAYS
            continue

        model = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    RandomForestClassifier(
                        n_estimators=100,
                        max_depth=5,
                        min_samples_leaf=10,
                        class_weight="balanced",
                        random_state=42,
                        n_jobs=-1,
                    ),
                ),
            ]
        )
        try:
            model.fit(X_train, y_train)
            proba = model.predict_proba(X_val)[:, 1]
            auc = float(roc_auc_score(y_val, proba))
        except Exception:
            auc = 0.5

        windows.append(
            {"train_rows": int(train_end - start), "val_rows": int(_VAL_DAYS), "auc": round(auc, 4)}
        )
        start += _SLIDE_DAYS

    if not windows:
        return {"windows": [], "mean_auc": 0.0, "std_auc": 0.0, "is_reliable": False}

    aucs = [w["auc"] for w in windows]
    mean_auc = float(np.mean(aucs))
    std_auc = float(np.std(aucs))
    is_reliable = mean_auc >= 0.54 and len(windows) >= 3

    logger.info(
        "WF-CV %s: %d windows mean_auc=%.3f std=%.3f reliable=%s",
        symbol,
        len(windows),
        mean_auc,
        std_auc,
        is_reliable,
    )
    return {
        "windows": windows,
        "mean_auc": round(mean_auc, 4),
        "std_auc": round(std_auc, 4),
        "is_reliable": is_reliable,
    }


# ---------------------------------------------------------------------------
# Monthly retraining trigger check
# ---------------------------------------------------------------------------

def _should_retrain_monthly(symbol: str) -> bool:
    """
    Returns True if:
      (a) symbol has never been trained, OR
      (b) last retrain was > _RETRAIN_TTL seconds ago, OR
      (c) more than _NEW_OUTCOMES_TRIGGER trade outcomes have accumulated
          since the last retrain.
    """
    state = _symbol_state.get(symbol, {})
    now = time.time()

    if "last_retrain_ts" not in state:
        return True
    if (now - state["last_retrain_ts"]) > _RETRAIN_TTL:
        return True

    all_outcomes = _load_trade_outcomes()
    symbol_outcomes = [o for o in all_outcomes if o.get("symbol") == symbol]
    n_at_retrain = state.get("n_outcomes_at_retrain", 0)
    if (len(symbol_outcomes) - n_at_retrain) > _NEW_OUTCOMES_TRIGGER:
        return True

    return False


# ---------------------------------------------------------------------------
# Model retrieval (cache + retrain orchestration)
# ---------------------------------------------------------------------------

def _get_model(symbol: str, df: pd.DataFrame) -> Optional[object]:
    """
    Returns a trained model for inference, handling:
      - 24-h inference cache (fast path)
      - Monthly retrain with trade outcomes
      - yfinance fallback when df is too short
      - AUC suspension flag
    """
    now = time.time()
    state = _symbol_state.get(symbol, {})

    # Fast path: valid cached model and not due for monthly retrain
    if state.get("model") is not None:
        cache_age = now - state.get("ts", 0)
        if cache_age < _CACHE_TTL and not _should_retrain_monthly(symbol):
            return state["model"]

    # Extend df via yfinance if too short for training
    train_df = df
    if len(df) < MIN_SAMPLES + FORWARD_DAYS + 30:
        try:
            import yfinance as yf

            hist = yf.download(symbol, period="2y", interval="1d", progress=False, auto_adjust=True)
            if not hist.empty:
                hist.columns = [c.lower() for c in hist.columns]
                train_df = hist
        except Exception:
            pass

    # Load trade outcomes for this symbol
    all_outcomes = _load_trade_outcomes()
    symbol_outcomes = [o for o in all_outcomes if o.get("symbol") == symbol]
    outcome_feature_rows = [_build_features_from_outcome(t) for t in symbol_outcomes]

    model = _train_model(symbol, train_df, extra_outcome_rows=outcome_feature_rows)

    # Compute AUC for suspension check via walk-forward CV
    suspended = False
    model_auc = 0.5
    if model is not None:
        wf = run_walk_forward_cv(symbol, train_df)
        model_auc = wf["mean_auc"]
        if model_auc < 0.54:
            suspended = True
            logger.warning(
                "ML %s: AUC=%.3f < 0.54 — model suspended (fail open)", symbol, model_auc
            )
            _log_event("auc_alert", symbol, auc=round(model_auc, 4), suspended=True)

    _symbol_state[symbol] = {
        "model": model,
        "ts": now,
        "last_retrain_ts": now,
        "auc": model_auc,
        "suspended": suspended,
        "n_outcomes_at_retrain": len(symbol_outcomes),
    }
    return model


# ---------------------------------------------------------------------------
# Dynamic threshold
# ---------------------------------------------------------------------------

def get_dynamic_threshold(regime: str) -> float:
    """
    Returns regime-appropriate approval threshold:
      BULL / RANGING : 0.52
      BEAR           : 0.58
      HIGH_VOL       : 0.65
    """
    return _DYNAMIC_THRESHOLDS.get(str(regime).upper(), 0.52)


# ---------------------------------------------------------------------------
# Trade outcome update
# ---------------------------------------------------------------------------

def update_model_with_trade_outcomes(symbol: str, closed_trades: list[dict]) -> None:
    """
    Persist new closed-trade dicts for ``symbol`` to
    ``logs/ml_trade_outcomes.json`` and optionally trigger an immediate
    retrain if more than _NEW_OUTCOMES_TRIGGER new outcomes have accumulated.

    Each trade dict must contain:
        symbol, entry_price, exit_price, pnl_pct, regime, sentiment_score,
        signal_score, sector, vix_at_entry, opened_at

    Label: 1 if pnl_pct > MIN_WIN_PCT (0.005), else 0.
    """
    if not closed_trades:
        return

    existing = _load_trade_outcomes()
    n_before = len([o for o in existing if o.get("symbol") == symbol])

    # Attach symbol field if not present and append
    for trade in closed_trades:
        record = dict(trade)
        record.setdefault("symbol", symbol)
        record["label"] = int(float(record.get("pnl_pct", 0.0)) > MIN_WIN_PCT)
        existing.append(record)

    _save_trade_outcomes(existing)

    n_after = len([o for o in existing if o.get("symbol") == symbol])
    logger.info(
        "ml_pipeline: %s trade outcomes stored (+%d, total=%d)",
        symbol,
        n_after - n_before,
        n_after,
    )

    # Clear cached model so next inference call retrains with new outcomes
    if symbol in _symbol_state:
        state = _symbol_state[symbol]
        n_at_retrain = state.get("n_outcomes_at_retrain", 0)
        if (n_after - n_at_retrain) > _NEW_OUTCOMES_TRIGGER:
            logger.info(
                "ml_pipeline: %s triggering retrain — %d new outcomes",
                symbol,
                n_after - n_at_retrain,
            )
            # Force cache expiry so _get_model retrains on next call
            state["ts"] = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_ml_probability(symbol: str, df: pd.DataFrame) -> Optional[float]:
    """
    Returns the probability (0.0–1.0) that a BUY on this symbol will be
    profitable within FORWARD_DAYS days.

    Returns None when:
      - scikit-learn not installed
      - insufficient training data (warm-up)
      - inference row contains NaN

    Fails open (returns None) when the model is suspended due to low AUC.
    """
    state = _symbol_state.get(symbol, {})

    # If model is suspended, fail open
    if state.get("suspended", False):
        logger.debug("ML %s: suspended — fail open", symbol)
        return None

    model = _get_model(symbol, df)
    if model is None:
        return None

    # Re-read state after potential retrain
    state = _symbol_state.get(symbol, {})
    if state.get("suspended", False):
        return None

    try:
        features = _build_features(df)
        latest = features.iloc[[-1]]

        feat_cols = getattr(model, "feature_names", _FEATURE_COLS)
        available = [c for c in feat_cols if c in latest.columns]
        if len(available) < len(feat_cols):
            return None

        row = latest[available].values.astype(float)
        if np.isnan(row).any():
            return None

        prob = float(model.predict_proba(row)[0][1])
        return prob

    except Exception as exc:
        logger.debug("ML inference error for %s: %s", symbol, exc)
        return None


def is_ml_approved(symbol: str, df: pd.DataFrame, min_prob: float) -> bool:
    """
    Returns True if the ML model approves the entry (probability >= min_prob).

    Fails OPEN (returns True) in all of these cases:
      - scikit-learn not installed
      - model in warm-up (< MIN_SAMPLES rows)
      - model suspended due to AUC < 0.54
      - any inference error
    """
    prob = get_ml_probability(symbol, df)

    if prob is None:
        logger.debug("ML %s: warmup/unavailable/suspended — PASS (fail open)", symbol)
        return True

    approved = prob >= min_prob
    logger.info(
        "ML %s: p=%.3f threshold=%.2f → %s",
        symbol,
        prob,
        min_prob,
        "PASS" if approved else "BLOCK",
    )
    return approved
