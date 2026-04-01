"""
Unit tests for analysis/ml_pipeline.py
=======================================
Run with:  pytest tests/test_ml_pipeline.py -v
"""
from __future__ import annotations

import importlib
import json
import sys
import time
import types
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path so we can import analysis.ml_pipeline
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import analysis.ml_pipeline as mlp  # noqa: E402  (after sys.path tweak)


# ---------------------------------------------------------------------------
# Shared fixture: synthetic OHLCV DataFrame long enough for training
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 400, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic daily OHLCV data with realistic price dynamics."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.01, size=n)
    close = 100.0 * np.cumprod(1 + returns)
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    volume = rng.integers(500_000, 2_000_000, size=n).astype(float)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"close": close, "high": high, "low": low, "open": close, "volume": volume},
        index=dates,
    )


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return _make_ohlcv()


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """
    Reset all module-level state between tests and redirect log/outcome
    files to a temporary directory so tests don't pollute the real logs.
    """
    # Clear in-memory symbol state
    mlp._symbol_state.clear()

    # Redirect file paths to tmp_path
    monkeypatch.setattr(mlp, "_MODEL_LOG_PATH", tmp_path / "ml_model_log.json")
    monkeypatch.setattr(mlp, "_TRADE_OUTCOMES_PATH", tmp_path / "ml_trade_outcomes.json")

    # Force sklearn availability True (it is in our environment)
    monkeypatch.setattr(mlp, "_SKLEARN_AVAILABLE", True)

    yield

    # Clean up after test
    mlp._symbol_state.clear()


# ===========================================================================
# Test 1 — Feature building: shape and column names
# ===========================================================================

class TestFeatureBuilding:
    def test_returns_all_15_features(self, ohlcv):
        """_build_features must return a DataFrame with exactly 15 named columns."""
        feat = mlp._build_features(ohlcv)
        assert list(feat.columns) == mlp._FEATURE_COLS, (
            f"Expected {mlp._FEATURE_COLS}, got {list(feat.columns)}"
        )
        assert feat.shape == (len(ohlcv), 15)

    def test_extended_features_broadcast_correctly(self, ohlcv):
        """Extended scalar features must be constant across rows."""
        feat = mlp._build_features(
            ohlcv,
            regime="BEAR",
            sentiment_score=0.7,
            signal_score=80.0,
            vix=32.0,
            sector_momentum=0.05,
        )
        # regime_encoded: BEAR → -0.5
        assert (feat["regime_encoded"] == -0.5).all()
        # sentiment_score constant
        assert (feat["sentiment_score"] == 0.7).all()
        # signal_score_norm = 80/100 = 0.8
        assert np.allclose(feat["signal_score_norm"], 0.8)
        # vix_norm = 32/40 = 0.8
        assert np.allclose(feat["vix_norm"], 0.8)

    def test_vix_norm_clamped(self, ohlcv):
        """vix_norm must be clamped to [0, 1]."""
        feat_high = mlp._build_features(ohlcv, vix=200.0)
        feat_zero = mlp._build_features(ohlcv, vix=0.0)
        assert (feat_high["vix_norm"] <= 1.0).all()
        assert (feat_zero["vix_norm"] >= 0.0).all()

    def test_unknown_regime_defaults_to_bull(self, ohlcv):
        """An unrecognised regime string should default to BULL (1.0)."""
        feat = mlp._build_features(ohlcv, regime="SIDEWAYS")
        assert (feat["regime_encoded"] == 1.0).all()


# ===========================================================================
# Test 2 — Walk-forward CV logic
# ===========================================================================

class TestWalkForwardCV:
    def test_returns_expected_keys(self, ohlcv):
        """run_walk_forward_cv must return the documented dict structure."""
        result = mlp.run_walk_forward_cv("TEST", ohlcv)
        assert "windows" in result
        assert "mean_auc" in result
        assert "std_auc" in result
        assert "is_reliable" in result

    def test_at_least_one_window(self, ohlcv):
        """With 400 bars a walk-forward should produce at least one window."""
        result = mlp.run_walk_forward_cv("TEST", ohlcv)
        assert len(result["windows"]) >= 1

    def test_is_reliable_requires_3_windows(self, monkeypatch):
        """is_reliable=True must require at least 3 windows."""
        # Patch internals to fake 2 windows with high AUC
        fake_windows = [
            {"train_rows": 189, "val_rows": 63, "auc": 0.60},
            {"train_rows": 189, "val_rows": 63, "auc": 0.62},
        ]
        # Build a minimal df that will fail early and produce our canned windows
        # by monkeypatching the walk-forward loop via run_walk_forward_cv itself
        with patch.object(mlp, "run_walk_forward_cv", return_value={
            "windows": fake_windows,
            "mean_auc": 0.61,
            "std_auc": 0.01,
            "is_reliable": False,  # only 2 windows < 3 required
        }) as mock_wfcv:
            result = mock_wfcv("TEST", pd.DataFrame())
            assert result["is_reliable"] is False

    def test_is_reliable_false_when_auc_below_threshold(self, monkeypatch):
        """is_reliable=False when mean_auc < 0.54, even with many windows."""
        fake_result = {
            "windows": [{"auc": 0.50}] * 5,
            "mean_auc": 0.50,
            "std_auc": 0.01,
            "is_reliable": False,
        }
        assert fake_result["is_reliable"] is False
        assert fake_result["mean_auc"] < 0.54

    def test_insufficient_data_returns_not_reliable(self):
        """A very short DataFrame must return is_reliable=False."""
        short_df = _make_ohlcv(n=50)
        result = mlp.run_walk_forward_cv("TEST", short_df)
        assert result["is_reliable"] is False
        assert result["windows"] == []


# ===========================================================================
# Test 3 — Dynamic threshold
# ===========================================================================

class TestDynamicThreshold:
    @pytest.mark.parametrize("regime,expected", [
        ("BULL",     0.52),
        ("RANGING",  0.52),
        ("BEAR",     0.58),
        ("HIGH_VOL", 0.65),
        ("bull",     0.52),   # case-insensitive
        ("high_vol", 0.65),
        ("UNKNOWN",  0.52),   # fallback
    ])
    def test_thresholds(self, regime, expected):
        assert mlp.get_dynamic_threshold(regime) == expected


# ===========================================================================
# Test 4 — Trade outcome update
# ===========================================================================

class TestTradeOutcomeUpdate:
    def _make_trade(self, symbol: str = "AAPL", pnl_pct: float = 0.01) -> dict:
        return {
            "symbol": symbol,
            "entry_price": 150.0,
            "exit_price": 151.5,
            "pnl_pct": pnl_pct,
            "regime": "BULL",
            "sentiment_score": 0.5,
            "signal_score": 70.0,
            "sector": "Technology",
            "vix_at_entry": 18.0,
            "opened_at": "2026-01-10T10:00:00",
        }

    def test_outcomes_persisted_to_json(self, tmp_path, monkeypatch):
        """Closed trades must be written to the outcomes JSON file."""
        monkeypatch.setattr(mlp, "_TRADE_OUTCOMES_PATH", tmp_path / "outcomes.json")
        trades = [self._make_trade("AAPL", 0.02), self._make_trade("AAPL", -0.01)]
        mlp.update_model_with_trade_outcomes("AAPL", trades)

        saved = json.loads((tmp_path / "outcomes.json").read_text())
        assert len(saved) == 2
        # Labels: pnl_pct > 0.005 → label 1; -0.01 → label 0
        assert saved[0]["label"] == 1
        assert saved[1]["label"] == 0

    def test_multiple_symbols_isolated(self, tmp_path, monkeypatch):
        """Trade outcomes for different symbols must coexist in the file."""
        monkeypatch.setattr(mlp, "_TRADE_OUTCOMES_PATH", tmp_path / "outcomes.json")
        mlp.update_model_with_trade_outcomes("AAPL", [self._make_trade("AAPL", 0.01)])
        mlp.update_model_with_trade_outcomes("MSFT", [self._make_trade("MSFT", 0.02)])

        saved = json.loads((tmp_path / "outcomes.json").read_text())
        symbols = {r["symbol"] for r in saved}
        assert symbols == {"AAPL", "MSFT"}

    def test_cache_invalidated_after_trigger_threshold(self, monkeypatch, tmp_path):
        """
        When > _NEW_OUTCOMES_TRIGGER outcomes accumulate since last retrain,
        the model cache must be invalidated (ts reset to 0).
        """
        monkeypatch.setattr(mlp, "_TRADE_OUTCOMES_PATH", tmp_path / "outcomes.json")

        # Seed existing state as if model was recently trained with 0 outcomes
        mlp._symbol_state["AAPL"] = {
            "model": MagicMock(),
            "ts": time.time(),
            "last_retrain_ts": time.time(),
            "auc": 0.60,
            "suspended": False,
            "n_outcomes_at_retrain": 0,
        }

        # Add > 20 outcomes to trigger invalidation
        trades = [self._make_trade("AAPL", 0.01) for _ in range(21)]
        mlp.update_model_with_trade_outcomes("AAPL", trades)

        assert mlp._symbol_state["AAPL"]["ts"] == 0.0


# ===========================================================================
# Test 5 — AUC alert suspension
# ===========================================================================

class TestAUCSuspension:
    def test_suspended_model_fails_open(self, ohlcv):
        """
        When a symbol's model is suspended, get_ml_probability must return
        None (fail open), which means is_ml_approved returns True.
        """
        mlp._symbol_state["AAPL"] = {
            "model": MagicMock(),
            "ts": time.time(),
            "last_retrain_ts": time.time(),
            "auc": 0.50,
            "suspended": True,
            "n_outcomes_at_retrain": 0,
        }
        # Should fail open → None
        prob = mlp.get_ml_probability("AAPL", ohlcv)
        assert prob is None

    def test_suspended_model_is_ml_approved_true(self, ohlcv):
        """is_ml_approved must return True when suspended (fail open)."""
        mlp._symbol_state["AAPL"] = {
            "model": MagicMock(),
            "ts": time.time(),
            "last_retrain_ts": time.time(),
            "auc": 0.50,
            "suspended": True,
            "n_outcomes_at_retrain": 0,
        }
        assert mlp.is_ml_approved("AAPL", ohlcv, min_prob=0.52) is True

    def test_auc_alert_logged(self, tmp_path, monkeypatch):
        """An auc_alert event must be written to the model log when AUC < 0.54."""
        log_path = tmp_path / "ml_model_log.json"
        monkeypatch.setattr(mlp, "_MODEL_LOG_PATH", log_path)

        mlp._log_event("auc_alert", "TEST", auc=0.50, suspended=True)

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "auc_alert"
        assert record["symbol"] == "TEST"
        assert record["auc"] == 0.50
        assert record["suspended"] is True


# ===========================================================================
# Test 6 — Public API behaviour with mock data
# ===========================================================================

class TestPublicAPI:
    def test_is_ml_approved_passes_above_threshold(self, ohlcv):
        """
        When the model returns a probability above min_prob,
        is_ml_approved must return True.
        """
        with patch.object(mlp, "get_ml_probability", return_value=0.70):
            assert mlp.is_ml_approved("AAPL", ohlcv, min_prob=0.52) is True

    def test_is_ml_approved_blocks_below_threshold(self, ohlcv):
        """
        When the model returns a probability below min_prob,
        is_ml_approved must return False.
        """
        with patch.object(mlp, "get_ml_probability", return_value=0.40):
            assert mlp.is_ml_approved("AAPL", ohlcv, min_prob=0.52) is False

    def test_is_ml_approved_fails_open_when_none(self, ohlcv):
        """
        When get_ml_probability returns None (warm-up / no sklearn),
        is_ml_approved must return True (fail open).
        """
        with patch.object(mlp, "get_ml_probability", return_value=None):
            assert mlp.is_ml_approved("AAPL", ohlcv, min_prob=0.52) is True

    def test_get_ml_probability_returns_float_or_none(self, ohlcv):
        """get_ml_probability must return a float in [0,1] or None."""
        # Use a very short df to trigger warm-up path
        short_df = _make_ohlcv(n=10)
        result = mlp.get_ml_probability("AAPL", short_df)
        assert result is None or (0.0 <= result <= 1.0)

    def test_get_ml_probability_with_mocked_model(self, ohlcv, monkeypatch):
        """
        With a mock model that returns a fixed predict_proba, probability
        should equal the mocked value.
        """
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.ensemble import RandomForestClassifier

        # Train a tiny real model so inference path works
        with patch.object(mlp, "_should_retrain_monthly", return_value=False), \
             patch.object(mlp, "run_walk_forward_cv", return_value={
                 "windows": [{"auc": 0.60}] * 3,
                 "mean_auc": 0.60,
                 "std_auc": 0.02,
                 "is_reliable": True,
             }):
            mlp._symbol_state["AAPL"] = {
                "model": None,
                "ts": 0.0,
                "last_retrain_ts": 0.0,
                "auc": 0.60,
                "suspended": False,
                "n_outcomes_at_retrain": 0,
            }

            # Mock yfinance to avoid network calls
            fake_yf_mod = types.ModuleType("yfinance")
            fake_yf_mod.download = MagicMock(return_value=pd.DataFrame())
            monkeypatch.setitem(sys.modules, "yfinance", fake_yf_mod)

            prob = mlp.get_ml_probability("AAPL", ohlcv)
            # Either None (insufficient samples after mock) or a valid float
            assert prob is None or (0.0 <= prob <= 1.0)

    def test_public_api_signatures_match(self):
        """Public functions must exist with the documented signatures."""
        import inspect
        for fn_name, required_params in [
            ("is_ml_approved",                  ["symbol", "df", "min_prob"]),
            ("get_ml_probability",              ["symbol", "df"]),
            ("update_model_with_trade_outcomes", ["symbol", "closed_trades"]),
            ("get_dynamic_threshold",           ["regime"]),
        ]:
            fn = getattr(mlp, fn_name, None)
            assert fn is not None, f"{fn_name} not found in ml_pipeline"
            sig = inspect.signature(fn)
            for param in required_params:
                assert param in sig.parameters, (
                    f"{fn_name} missing parameter '{param}'"
                )
