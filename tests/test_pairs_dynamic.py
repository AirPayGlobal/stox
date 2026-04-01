"""
Unit tests for analysis/pairs_dynamic.py
=========================================
Covers:
    1. KalmanHedgeRatio convergence
    2. KalmanHedgeRatio single-step update / history length
    3. test_cointegration (ADF wrapper) — cointegrated spread
    4. test_cointegration — non-stationary (random walk) spread
    5. calculate_kalman_zscore shape and stationarity
    6. 3-consecutive-week drop logic in run_weekly_cointegration_check
    7. run_weekly_cointegration_check resets failure counter on pass
    8. screen_pairs_dynamic returns correct signal keys and valid signal values
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Make sure the project root is on sys.path when running tests directly
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from analysis.pairs_dynamic import (
    KalmanHedgeRatio,
    calculate_kalman_zscore,
    run_weekly_cointegration_check,
    screen_pairs_dynamic,
    test_cointegration,
)
from analysis.pairs_trading import PairSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cointegrated_prices(
    n: int = 250,
    true_beta: float = 1.5,
    seed: int = 42,
) -> tuple[pd.Series, pd.Series]:
    """
    Generate two price series that are cointegrated by construction:
        log_A = true_beta * log_B + stationary_error
    """
    rng = np.random.default_rng(seed)
    log_b = np.cumsum(rng.normal(0, 0.01, n))               # random walk
    error = rng.normal(0, 0.02, n)                           # stationary noise
    log_a = true_beta * log_b + error
    prices_a = pd.Series(np.exp(log_a))
    prices_b = pd.Series(np.exp(log_b))
    return prices_a, prices_b


def _make_random_walk_prices(
    n: int = 250,
    seed: int = 99,
) -> tuple[pd.Series, pd.Series]:
    """
    Two independent random walks — NOT cointegrated.
    """
    rng = np.random.default_rng(seed)
    prices_a = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.01, n))))
    prices_b = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.01, n))))
    return prices_a, prices_b


def _make_data_dict(
    sym_a: str,
    sym_b: str,
    prices_a: pd.Series,
    prices_b: pd.Series,
) -> dict[str, pd.DataFrame]:
    return {
        sym_a: pd.DataFrame({"close": prices_a}),
        sym_b: pd.DataFrame({"close": prices_b}),
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestKalmanHedgeRatioConvergence(unittest.TestCase):
    """Test 1 — The Kalman filter converges close to the true beta."""

    def test_converges_to_true_beta(self):
        true_beta = 1.5
        prices_a, prices_b = _make_cointegrated_prices(
            n=500, true_beta=true_beta, seed=0
        )
        log_a = np.log(prices_a)
        log_b = np.log(prices_b)

        kf = KalmanHedgeRatio(Q=1e-5)
        kf.fit(log_a, log_b)

        estimated = kf.get_hedge_ratio()
        # After 500 observations the filter should be within 0.2 of the true beta
        self.assertAlmostEqual(estimated, true_beta, delta=0.2,
            msg=f"Expected beta ≈ {true_beta}, got {estimated:.4f}")


class TestKalmanUpdateAndHistory(unittest.TestCase):
    """Test 2 — update() returns a float; history length matches data length."""

    def test_update_returns_float_and_history_length(self):
        n = 150
        prices_a, prices_b = _make_cointegrated_prices(n=n, seed=7)
        log_a = np.log(prices_a)
        log_b = np.log(prices_b)

        kf = KalmanHedgeRatio()
        kf.fit(log_a, log_b)

        history = kf.get_hedge_history()
        self.assertIsInstance(history, pd.Series)
        self.assertEqual(len(history), n,
            f"History length {len(history)} != data length {n}")

        # Single-step update
        new_beta = kf.update(float(log_a.iloc[-1]), float(log_b.iloc[-1]))
        self.assertIsInstance(new_beta, float)
        self.assertFalse(np.isnan(new_beta), "update() returned NaN")
        # History grows by one
        self.assertEqual(len(kf.get_hedge_history()), n + 1)


class TestADFCointegrated(unittest.TestCase):
    """Test 3 — test_cointegration detects a genuinely cointegrated pair."""

    def test_cointegrated_pair_detected(self):
        prices_a, prices_b = _make_cointegrated_prices(n=300, seed=10)
        result = test_cointegration(prices_a, prices_b)

        self.assertIn("p_value", result)
        self.assertIn("is_cointegrated", result)
        self.assertIn("adf_stat", result)
        self.assertIn("critical_5pct", result)
        self.assertTrue(
            result["is_cointegrated"],
            f"Expected cointegration, p_value={result['p_value']:.4f}",
        )
        self.assertLess(result["p_value"], 0.05)


class TestADFNonStationary(unittest.TestCase):
    """Test 4 — test_cointegration returns is_cointegrated=False for two independent RWs."""

    def test_non_cointegrated_pair(self):
        prices_a, prices_b = _make_random_walk_prices(n=250, seed=55)
        result = test_cointegration(prices_a, prices_b)

        self.assertFalse(
            result["is_cointegrated"],
            f"Expected NOT cointegrated, but p_value={result['p_value']:.4f}",
        )
        self.assertGreaterEqual(result["p_value"], 0.05)


class TestKalmanZscore(unittest.TestCase):
    """Test 5 — calculate_kalman_zscore shape and basic properties."""

    def test_zscore_shape_and_range(self):
        n = 300
        prices_a, prices_b = _make_cointegrated_prices(n=n, seed=3)
        window = 60

        kf = KalmanHedgeRatio()
        z = calculate_kalman_zscore(prices_a, prices_b, kf, window=window)

        self.assertIsInstance(z, pd.Series)
        self.assertEqual(len(z), n)

        # Values before first full window should be NaN
        self.assertTrue(z.iloc[:window - 1].isna().all(),
            "Expected NaNs before warm-up period")

        valid = z.dropna()
        self.assertGreater(len(valid), 0, "No valid z-score values produced")

        # For a well-behaved stationary spread, most z-scores should be in [-5, 5]
        extreme = (valid.abs() > 5).mean()
        self.assertLess(extreme, 0.05,
            f"Too many extreme z-scores: {extreme:.1%}")


class TestCointegrationDropLogic(unittest.TestCase):
    """Test 6 — Pair is dropped after 3 consecutive ADF failures."""

    def test_drop_after_three_failures(self):
        # Use non-cointegrated data so ADF will fail
        prices_a, prices_b = _make_random_walk_prices(n=250, seed=77)
        sym_a, sym_b = "AAA", "BBB"
        data = _make_data_dict(sym_a, sym_b, prices_a, prices_b)

        with tempfile.TemporaryDirectory() as tmp:
            coint_log = os.path.join(tmp, "pairs_cointegration.json")

            # Patch the log file path and the ADF function to always fail
            with patch("analysis.pairs_dynamic._COINT_LOG", coint_log), \
                 patch("analysis.pairs_dynamic.test_cointegration",
                       return_value={
                           "p_value": 0.5,
                           "is_cointegrated": False,
                           "adf_stat": -1.0,
                           "critical_5pct": -2.86,
                       }):

                pairs = [(sym_a, sym_b)]

                # Week 1 — 1st failure, still active
                res1 = run_weekly_cointegration_check(pairs, data)
                self.assertIn([sym_a, sym_b], res1["active_pairs"])
                self.assertEqual(res1["dropped_pairs"], [])

                # Week 2 — 2nd failure, still active
                res2 = run_weekly_cointegration_check(pairs, data)
                self.assertIn([sym_a, sym_b], res2["active_pairs"])
                self.assertEqual(res2["dropped_pairs"], [])

                # Week 3 — 3rd failure, should be dropped
                res3 = run_weekly_cointegration_check(pairs, data)
                self.assertIn([sym_a, sym_b], res3["dropped_pairs"])
                self.assertNotIn([sym_a, sym_b], res3["active_pairs"])


class TestCointegrationResetOnPass(unittest.TestCase):
    """Test 7 — Failure counter resets to 0 when ADF passes."""

    def test_reset_counter_on_pass(self):
        prices_a, prices_b = _make_cointegrated_prices(n=250, seed=20)
        sym_a, sym_b = "CCC", "DDD"
        data = _make_data_dict(sym_a, sym_b, prices_a, prices_b)

        with tempfile.TemporaryDirectory() as tmp:
            coint_log = os.path.join(tmp, "pairs_cointegration.json")

            fail_result = {
                "p_value": 0.5,
                "is_cointegrated": False,
                "adf_stat": -1.0,
                "critical_5pct": -2.86,
            }
            pass_result = {
                "p_value": 0.01,
                "is_cointegrated": True,
                "adf_stat": -4.5,
                "critical_5pct": -2.86,
            }

            with patch("analysis.pairs_dynamic._COINT_LOG", coint_log):
                pairs = [(sym_a, sym_b)]

                # Two failures
                with patch("analysis.pairs_dynamic.test_cointegration",
                           return_value=fail_result):
                    run_weekly_cointegration_check(pairs, data)
                    run_weekly_cointegration_check(pairs, data)

                # One pass — counter should reset, pair should still be active
                with patch("analysis.pairs_dynamic.test_cointegration",
                           return_value=pass_result):
                    res = run_weekly_cointegration_check(pairs, data)

                self.assertIn([sym_a, sym_b], res["active_pairs"])
                self.assertEqual(res["dropped_pairs"], [])

                pair_str = f"{sym_a}/{sym_b}"
                self.assertEqual(
                    res["results"][pair_str]["consecutive_failures"], 0,
                    "consecutive_failures should be 0 after a pass",
                )


class TestScreenPairsDynamic(unittest.TestCase):
    """Test 8 — screen_pairs_dynamic returns well-formed signal dicts."""

    def test_signal_dict_structure_and_valid_values(self):
        # Build a strongly mean-reverting spread to reliably trigger a signal
        n = 300
        rng = np.random.default_rng(42)
        log_b = np.cumsum(rng.normal(0, 0.01, n))
        # AR(1) spread with phi=0.8 — clearly stationary, mean-reverting
        error = np.zeros(n)
        for t in range(1, n):
            error[t] = 0.8 * error[t - 1] + rng.normal(0, 0.015)
        log_a = 1.4 * log_b + error

        prices_a = pd.Series(np.exp(log_a))
        prices_b = pd.Series(np.exp(log_b))

        sym_a, sym_b = "EEE", "FFF"
        data = _make_data_dict(sym_a, sym_b, prices_a, prices_b)

        # Patch hedge log so we don't write real files during tests
        with tempfile.TemporaryDirectory() as tmp:
            hedge_log = os.path.join(tmp, "pairs_hedge_ratios.json")
            with patch("analysis.pairs_dynamic._HEDGE_LOG", hedge_log):
                # Force a z-score that triggers a signal by patching calculate_kalman_zscore
                fake_z = pd.Series(
                    [float("nan")] * 59 + [2.5] * (n - 59),
                    index=range(n),
                )
                with patch("analysis.pairs_dynamic.calculate_kalman_zscore",
                           return_value=fake_z):
                    results = screen_pairs_dynamic(
                        data, active_pairs=[(sym_a, sym_b)]
                    )

        self.assertGreater(len(results), 0, "Expected at least one signal")
        sig = results[0]

        required_keys = {
            "symbol_a", "symbol_b", "signal", "z_score",
            "hedge_ratio", "entry_cost_estimate", "exit_cost_estimate",
        }
        self.assertEqual(required_keys, required_keys & sig.keys(),
            f"Missing keys: {required_keys - sig.keys()}")

        valid_signals = {
            PairSignal.LONG_A_SHORT_B,
            PairSignal.LONG_B_SHORT_A,
            PairSignal.EXIT,
            PairSignal.STOP,
        }
        self.assertIn(sig["signal"], valid_signals,
            f"Unexpected signal value: {sig['signal']}")

        self.assertIsInstance(sig["z_score"], float)
        self.assertFalse(np.isnan(sig["z_score"]), "z_score should not be NaN")
        self.assertGreater(sig["entry_cost_estimate"], 0)
        self.assertGreater(sig["exit_cost_estimate"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
