"""
ML Signal Booster
==================
Trains a RandomForestClassifier per-symbol on historical OHLCV features to
estimate the probability that a BUY candidate will be profitable 5 days forward.

Feature set (all normalised to remove price-scale effects):
  rsi            — 14-period RSI ÷ 100
  macd_hist      — MACD histogram ÷ price
  bb_pos         — Bollinger Band position (0 = lower band, 1 = upper)
  ema_fast_pct   — (close - EMA9)  ÷ close
  ema_slow_pct   — (close - EMA21) ÷ close
  ema_trend_pct  — (close - EMA50) ÷ close
  vol_ratio      — volume ÷ 20-day avg volume
  atr_ratio      — ATR14 ÷ close
  mom5           — 5-day price momentum
  mom20          — 20-day price momentum

Label: 1 if close[t+5] > close[t] × (1 + MIN_WIN_PCT), else 0

Model lifecycle:
  • Warm-up: returns 0.5 (neutral) until MIN_SAMPLES training rows available
  • Training: on-demand when model is stale (>24 h) or missing
  • Inference: predict_proba on the latest bar's features
  • Cache: per-symbol (model, timestamp) dict; 24-hour TTL

Requires: scikit-learn (optional — gracefully disabled if not installed)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("ml_signals")

# ---- tunables ----------------------------------------------------------------
MIN_SAMPLES     = 150    # rows needed before model trains
FORWARD_DAYS    = 5      # predict N-day forward profitability
MIN_WIN_PCT     = 0.005  # label=1 if forward return > 0.5%
_CACHE_TTL      = 86_400 # 24 h per-symbol model cache

# per-symbol cache: {symbol: (pipeline, fit_ts)}
_model_cache: dict = {}

_SKLEARN_AVAILABLE: Optional[bool] = None  # checked once at first call


def _check_sklearn() -> bool:
    global _SKLEARN_AVAILABLE
    if _SKLEARN_AVAILABLE is None:
        try:
            import sklearn  # noqa: F401
            _SKLEARN_AVAILABLE = True
        except ImportError:
            logger.warning("scikit-learn not installed — ML signal booster disabled")
            _SKLEARN_AVAILABLE = False
    return _SKLEARN_AVAILABLE


# ---- feature engineering -----------------------------------------------------

def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute normalised technical features from OHLCV data.
    All columns are price-scale-independent floats.
    """
    close  = df["close"].astype(float)
    high   = df["high"].astype(float)
    low    = df["low"].astype(float)
    volume = df["volume"].astype(float)

    out = pd.DataFrame(index=df.index)

    # RSI (normalised to 0–1)
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    out["rsi"] = (100 - 100 / (1 + rs)) / 100

    # MACD histogram / price
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = (macd - sig) / close.replace(0, np.nan)

    # Bollinger Band position  (0 = lower band, 1 = upper band)
    sma20    = close.rolling(20).mean()
    std20    = close.rolling(20).std()
    bb_range = (2 * std20).replace(0, np.nan)
    out["bb_pos"] = (close - (sma20 - std20)) / bb_range

    # EMA spreads
    ema9  = close.ewm(span=9,  adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    safe  = close.replace(0, np.nan)
    out["ema_fast_pct"]  = (close - ema9)  / safe
    out["ema_slow_pct"]  = (close - ema21) / safe
    out["ema_trend_pct"] = (close - ema50) / safe

    # Volume ratio vs 20-day average
    vol_avg = volume.rolling(20).mean().replace(0, np.nan)
    out["vol_ratio"] = volume / vol_avg

    # ATR / price
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    out["atr_ratio"] = tr.rolling(14).mean() / safe

    # Momentum
    out["mom5"]  = close.pct_change(5)
    out["mom20"] = close.pct_change(20)

    return out


# ---- model training ----------------------------------------------------------

def _train_model(symbol: str, df: pd.DataFrame):
    """
    Train (or retrain) a RandomForest pipeline for the given symbol.
    Returns the fitted sklearn Pipeline, or None if training fails.
    """
    if not _check_sklearn():
        return None

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    features = _build_features(df)
    close    = df["close"].astype(float)

    # Label: 1 if price is higher FORWARD_DAYS later by more than MIN_WIN_PCT
    fwd_ret = close.shift(-FORWARD_DAYS) / close - 1
    labels  = (fwd_ret > MIN_WIN_PCT).astype(int)

    combined = features.copy()
    combined["label"] = labels
    combined = combined.dropna()

    # Remove the last FORWARD_DAYS rows — they have no reliable label yet
    if len(combined) > FORWARD_DAYS:
        combined = combined.iloc[:-FORWARD_DAYS]

    if len(combined) < MIN_SAMPLES:
        logger.debug(
            f"ML {symbol}: only {len(combined)} samples (need {MIN_SAMPLES}) — skipping"
        )
        return None

    X = combined.drop(columns="label").values
    y = combined["label"].values

    # Guard: skip if only one class present (degenerate data)
    if len(np.unique(y)) < 2:
        logger.debug(f"ML {symbol}: single-class labels — skipping")
        return None

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(
            n_estimators=100,
            max_depth=5,
            min_samples_leaf=10,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )),
    ])
    # Store feature names for inference alignment
    feature_cols = list(combined.drop(columns="label").columns)
    model.fit(X, y)
    model.feature_names = feature_cols  # type: ignore[attr-defined]

    logger.info(
        f"ML: trained {symbol} on {len(combined)} samples "
        f"(pos_rate={y.mean():.1%})"
    )
    return model


def _get_model(symbol: str, df: pd.DataFrame):
    """
    Return a cached or freshly trained model.
    If the provided df is too short for training, fetches 1 year of history
    from yfinance so the model can be trained even when the scan lookback is
    short (e.g. lookback_days=100 in main scan).
    Returns None only when sklearn is missing or data has a single class.
    """
    global _model_cache
    now = time.time()

    cached = _model_cache.get(symbol)
    if cached is not None:
        model, ts = cached
        if (now - ts) < _CACHE_TTL:
            return model

    # If the provided df is too short, try fetching a full year via yfinance
    train_df = df
    if len(df) < MIN_SAMPLES + FORWARD_DAYS + 30:
        try:
            import yfinance as yf
            hist = yf.download(symbol, period="2y", interval="1d",
                               progress=False, auto_adjust=True)
            if not hist.empty:
                hist.columns = [c.lower() for c in hist.columns]
                train_df = hist
        except Exception:
            pass  # fall back to original df

    model = _train_model(symbol, train_df)
    if model is not None:
        _model_cache[symbol] = (model, now)
    return model


# ---- public API --------------------------------------------------------------

def get_ml_probability(symbol: str, df: pd.DataFrame) -> float:
    """
    Returns the probability (0.0–1.0) that a BUY on this symbol will yield
    a profitable trade within FORWARD_DAYS days.

    Returns None when:
      • scikit-learn is not installed
      • insufficient training history (warm-up period)

    Returns 0.5 (neutral) when inference fails for any reason.
    """
    model = _get_model(symbol, df)
    if model is None:
        return None  # warm-up sentinel — caller should fail open

    try:
        features  = _build_features(df)
        latest    = features.iloc[[-1]]

        feat_cols = getattr(model, "feature_names", list(features.columns))
        available = [c for c in feat_cols if c in latest.columns]
        if len(available) < len(feat_cols):
            return None  # missing features — fail open

        row = latest[available].values
        if np.isnan(row).any():
            return None

        prob = float(model.predict_proba(row)[0][1])
        return prob

    except Exception as exc:
        logger.debug(f"ML inference error for {symbol}: {exc}")
        return None


def is_ml_approved(symbol: str, df: pd.DataFrame, min_prob: float) -> bool:
    """
    Returns True if the ML model approves the entry (probability >= min_prob).
    Fails OPEN (returns True) during warm-up or on any error — never blocks
    a trade just because the model hasn't trained yet.
    """
    prob = get_ml_probability(symbol, df)

    if prob is None:
        # Warm-up or missing sklearn — don't penalise the candidate
        logger.debug(f"ML {symbol}: warmup/unavailable — PASS (fail open)")
        return True

    approved = prob >= min_prob
    logger.info(
        f"ML {symbol}: p={prob:.3f} threshold={min_prob:.2f} → "
        f"{'PASS' if approved else 'BLOCK'}"
    )
    return approved
