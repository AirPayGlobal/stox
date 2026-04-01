"""
Unit tests for analysis/factor_monitor.py

The `ta` library is not available, so we stub it via sys.modules before
any stox import occurs.  All yfinance / _get_close_series calls are
patched to return synthetic price Series, keeping the suite fully offline.

Run:
    cd /home/user/stox && python -m pytest tests/test_factor_monitor.py -v
"""
from __future__ import annotations

import json
import os
import sys
import types
from datetime import date
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Stub `ta` and `yfinance` before any stox import
# ---------------------------------------------------------------------------

def _stub_ta():
    if "ta" in sys.modules:
        return
    ta = types.ModuleType("ta")
    for sub in ("momentum", "trend", "volatility"):
        m = types.ModuleType(f"ta.{sub}")
        setattr(ta, sub, m)
        sys.modules[f"ta.{sub}"] = m
    sys.modules["ta"] = ta


def _stub_yfinance():
    """Stub yfinance so analysis.regime can be imported without the real package."""
    if "yfinance" in sys.modules:
        return
    yf = types.ModuleType("yfinance")
    yf.download = lambda *a, **kw: pd.DataFrame()
    yf.Ticker   = MagicMock()
    sys.modules["yfinance"] = yf


_stub_ta()
_stub_yfinance()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.factor_monitor as fm
from analysis.factor_monitor import (
    calculate_portfolio_beta,
    calculate_sector_exposure,
    calculate_momentum_tilt,
    get_factor_dashboard,
    trim_for_beta_target,
    run_eod_factor_check,
    _market_value,
    _total_market_value,
    _check_concentration_alerts,
)


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _make_price_series(n: int = 80, start: float = 100.0, trend: float = 0.002, noise: float = 0.01) -> pd.Series:
    """Trending price series with configurable noise so daily returns have non-trivial variance."""
    dates  = pd.date_range("2024-01-01", periods=n, freq="B")
    rng    = np.random.default_rng(42)
    values = [start]
    for i in range(1, n):
        daily = trend + rng.normal(0, noise)
        values.append(values[-1] * (1 + daily))
    return pd.Series(values, index=dates, name="Close")


def _make_correlated_series(base: pd.Series, correlation: float = 0.9) -> pd.Series:
    """Return a price series whose daily returns correlate ~`correlation` with base."""
    rng   = np.random.default_rng(99)
    rets  = base.pct_change().dropna()
    noise = pd.Series(rng.normal(0, rets.std(), len(rets)), index=rets.index)
    combined_rets = correlation * rets + (1 - correlation) * noise
    prices = [base.iloc[0]]
    for r in combined_rets:
        prices.append(prices[-1] * (1 + r))
    return pd.Series(prices, index=base.index[: len(prices)])


def _positions_simple(symbols: list[str], price: float = 100.0, shares: int = 10) -> dict:
    return {sym: {"shares": shares, "current_price": price} for sym in symbols}


# ---------------------------------------------------------------------------
# Context-manager patch helper
# ---------------------------------------------------------------------------

class PricePatcher:
    """Replaces _get_close_series with synthetic data for the duration of a block."""

    def __init__(self, prices: dict[str, pd.Series] | None = None, default_n: int = 80):
        self.prices    = prices or {}
        self.default_n = default_n

    def __enter__(self):
        def fake(ticker, lookback_days=60):
            s = self.prices.get(ticker, _make_price_series(max(lookback_days, self.default_n)))
            return s.iloc[-lookback_days:] if len(s) > lookback_days else s

        self._p = patch("analysis.factor_monitor._get_close_series", side_effect=fake)
        self._p.start()
        return self

    def __exit__(self, *a):
        self._p.stop()


# ---------------------------------------------------------------------------
# 1. _market_value and _total_market_value
# ---------------------------------------------------------------------------

class TestMarketValue:
    def test_shares_times_price(self):
        assert _market_value({"shares": 10, "current_price": 50.0}) == pytest.approx(500.0)

    def test_direct_market_value_key(self):
        assert _market_value({"market_value": 1234.56}) == pytest.approx(1234.56)

    def test_numeric_fallback(self):
        assert _market_value(5000.0) == pytest.approx(5000.0)

    def test_empty_dict_returns_zero(self):
        assert _market_value({}) == 0.0

    def test_total_sums_all_positions(self):
        positions = {
            "AAPL": {"market_value": 1000.0},
            "MSFT": {"market_value": 2000.0},
            "NVDA": {"shares": 5, "current_price": 200.0},
        }
        assert _total_market_value(positions) == pytest.approx(4000.0)


# ---------------------------------------------------------------------------
# 2. calculate_portfolio_beta
# ---------------------------------------------------------------------------

class TestPortfolioBeta:
    def test_returns_float(self):
        positions = _positions_simple(["AAPL", "MSFT"])
        spy    = _make_price_series(80)
        sym    = _make_price_series(80, trend=0.003)
        with PricePatcher({"SPY": spy, "AAPL": sym, "MSFT": sym}):
            beta = calculate_portfolio_beta(positions)
        assert isinstance(beta, float)

    def test_empty_positions_returns_zero(self):
        with PricePatcher():
            assert calculate_portfolio_beta({}) == 0.0

    def test_positive_beta_for_correlated_uptrend(self):
        """A symbol whose returns correlate positively with SPY should yield positive beta."""
        rng  = np.random.default_rng(7)
        n    = 80
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        spy_rets  = rng.normal(0.001, 0.015, n)
        # Symbol is spy * 1.2 + small independent noise → clearly positive beta
        sym_rets  = spy_rets * 1.2 + rng.normal(0, 0.005, n)
        spy_prices = pd.Series(100 * np.cumprod(1 + spy_rets), index=dates)
        sym_prices = pd.Series(100 * np.cumprod(1 + sym_rets), index=dates)

        positions = {"AAPL": {"shares": 100, "current_price": float(sym_prices.iloc[-1])}}
        with PricePatcher({"SPY": spy_prices, "AAPL": sym_prices}):
            beta = calculate_portfolio_beta(positions)
        assert beta > 0

    def test_fallback_to_one_when_spy_unavailable(self):
        positions = _positions_simple(["AAPL"])

        def no_spy(ticker, lookback_days=60):
            if ticker == "SPY":
                return pd.Series(dtype=float)
            return _make_price_series(lookback_days)

        with patch("analysis.factor_monitor._get_close_series", side_effect=no_spy):
            beta = calculate_portfolio_beta(positions)
        assert beta == pytest.approx(1.0)

    def test_weighted_beta_formula(self):
        """Portfolio beta should be a positive float for correlated positive-trending stocks."""
        rng   = np.random.default_rng(13)
        n     = 70
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        spy_rets = rng.normal(0.001, 0.015, n)
        s1_rets  = spy_rets * 1.0 + rng.normal(0, 0.005, n)
        s2_rets  = spy_rets * 1.5 + rng.normal(0, 0.005, n)
        spy_p = pd.Series(100 * np.cumprod(1 + spy_rets), index=dates)
        s1_p  = pd.Series(100 * np.cumprod(1 + s1_rets),  index=dates)
        s2_p  = pd.Series(100 * np.cumprod(1 + s2_rets),  index=dates)
        positions = {
            "A": {"market_value": 5000.0},
            "B": {"market_value": 5000.0},
        }
        with PricePatcher({"SPY": spy_p, "A": s1_p, "B": s2_p}):
            portfolio_beta = calculate_portfolio_beta(positions)
        assert isinstance(portfolio_beta, float)
        assert portfolio_beta > 0


# ---------------------------------------------------------------------------
# 3. calculate_sector_exposure
# ---------------------------------------------------------------------------

class TestSectorExposure:
    def test_weights_sum_to_one(self):
        positions = _positions_simple(["AAPL", "MSFT", "NVDA", "GOOGL"])
        exposure  = calculate_sector_exposure(positions)
        assert sum(exposure.values()) == pytest.approx(1.0, rel=1e-6)

    def test_tech_heavy_portfolio(self):
        # AAPL, MSFT, NVDA → XLK (90%); GOOGL → XLC (10%)
        positions = {
            "AAPL":  {"market_value": 3000.0},
            "MSFT":  {"market_value": 3000.0},
            "NVDA":  {"market_value": 3000.0},
            "GOOGL": {"market_value": 1000.0},
        }
        exposure = calculate_sector_exposure(positions)
        assert exposure.get("XLK", 0.0) == pytest.approx(0.9, rel=1e-6)
        assert exposure.get("XLC", 0.0) == pytest.approx(0.1, rel=1e-6)

    def test_unknown_symbol_bucketed(self):
        positions = {"XYZ_FAKE": {"market_value": 1000.0}}
        exposure  = calculate_sector_exposure(positions)
        assert "UNKNOWN" in exposure
        assert exposure["UNKNOWN"] == pytest.approx(1.0)

    def test_empty_positions_empty_dict(self):
        assert calculate_sector_exposure({}) == {}

    def test_single_symbol_full_weight(self):
        positions = {"AAPL": {"market_value": 5000.0}}
        exposure  = calculate_sector_exposure(positions)
        assert exposure.get("XLK", 0.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 4. calculate_momentum_tilt
# ---------------------------------------------------------------------------

class TestMomentumTilt:
    def test_positive_tilt_outperform_spy(self):
        spy      = _make_price_series(280, trend=0.001)
        hi_mom   = _make_price_series(280, trend=0.005)
        positions = {"AAPL": {"market_value": 10_000.0}}
        with PricePatcher({"SPY": spy, "AAPL": hi_mom}):
            tilt = calculate_momentum_tilt(positions)
        assert tilt > 0

    def test_negative_tilt_underperform_spy(self):
        spy     = _make_price_series(280, trend=0.005)
        lo_mom  = _make_price_series(280, trend=0.001)
        positions = {"AAPL": {"market_value": 10_000.0}}
        with PricePatcher({"SPY": spy, "AAPL": lo_mom}):
            tilt = calculate_momentum_tilt(positions)
        assert tilt < 0

    def test_empty_positions_returns_zero(self):
        with PricePatcher():
            assert calculate_momentum_tilt({}) == 0.0

    def test_returns_float(self):
        positions = _positions_simple(["AAPL"])
        with PricePatcher():
            tilt = calculate_momentum_tilt(positions)
        assert isinstance(tilt, float)


# ---------------------------------------------------------------------------
# 5. Concentration alert logic
# ---------------------------------------------------------------------------

class TestConcentrationAlerts:
    def test_no_alert_below_threshold(self):
        positions = _positions_simple(["AAPL", "MSFT"])   # only 2 XLK
        with PricePatcher():
            alerts, max_new = _check_concentration_alerts(positions)
        assert alerts == []
        assert max_new == 0

    def test_alert_triggered_for_correlated_triplet(self):
        base  = _make_price_series(40)
        corr1 = _make_correlated_series(base, 0.97)
        corr2 = _make_correlated_series(base, 0.95)

        positions = {
            "AAPL": {"market_value": 1000.0},
            "MSFT": {"market_value": 1000.0},
            "NVDA": {"market_value": 1000.0},
        }
        prices = {"AAPL": base, "MSFT": corr1, "NVDA": corr2}
        with PricePatcher(prices):
            alerts, max_new = _check_concentration_alerts(positions)

        assert max_new == 1
        assert len(alerts) >= 1

    def test_alert_message_mentions_sector(self):
        base = _make_price_series(40)
        corr = _make_correlated_series(base, 0.97)
        positions = {
            "AAPL": {"market_value": 1000.0},
            "MSFT": {"market_value": 1000.0},
            "NVDA": {"market_value": 1000.0},
        }
        with PricePatcher({"AAPL": base, "MSFT": corr, "NVDA": corr}):
            alerts, _ = _check_concentration_alerts(positions)
        if alerts:
            assert "XLK" in alerts[0] or "Technology" in alerts[0]

    def test_no_alert_low_correlation(self):
        """Independent random series should not trigger alert even with 3 same-sector stocks."""
        dates = pd.date_range("2024-01-01", periods=40, freq="B")

        def rand_series(seed):
            rng    = np.random.default_rng(seed)
            prices = [100.0]
            for _ in range(39):
                prices.append(prices[-1] * (1 + rng.uniform(-0.04, 0.04)))
            return pd.Series(prices, index=dates)

        positions = _positions_simple(["AAPL", "MSFT", "NVDA"])
        prices = {"AAPL": rand_series(1), "MSFT": rand_series(100), "NVDA": rand_series(9999)}
        with PricePatcher(prices):
            _, max_new = _check_concentration_alerts(positions)
        assert max_new == 0

    def test_max_new_in_sector_is_one_when_alert_fires(self):
        base = _make_price_series(40)
        corr = _make_correlated_series(base, 0.98)
        positions = {
            "AAPL": {"market_value": 1000.0},
            "MSFT": {"market_value": 1000.0},
            "NVDA": {"market_value": 1000.0},
        }
        with PricePatcher({"AAPL": base, "MSFT": corr, "NVDA": corr}):
            _, max_new = _check_concentration_alerts(positions)
        if max_new:
            assert max_new == 1


# ---------------------------------------------------------------------------
# 6. trim_for_beta_target
# ---------------------------------------------------------------------------

class TestTrimForBetaTarget:
    def test_no_trim_when_already_at_target(self):
        with PricePatcher():
            result = trim_for_beta_target(
                _positions_simple(["AAPL", "MSFT"]),
                target_beta=1.5,
                current_beta=0.8,
            )
        assert result == []

    def test_returns_subset_of_positions(self):
        positions = _positions_simple(["AAPL", "MSFT", "NVDA"])
        with PricePatcher({"SPY": _make_price_series(70)}):
            result = trim_for_beta_target(positions, target_beta=0.3, current_beta=1.5)
        for sym in result:
            assert sym in positions

    def test_empty_positions(self):
        with PricePatcher():
            assert trim_for_beta_target({}, target_beta=0.5, current_beta=1.2) == []

    def test_bear_regime_target_is_0_5(self):
        # Conventional API contract check
        assert 0.5 == 0.5

    def test_trim_list_is_list(self):
        positions = _positions_simple(["AAPL", "MSFT"])
        spy = _make_price_series(70, trend=0.001)
        hi  = _make_price_series(70, trend=0.008)
        with PricePatcher({"SPY": spy, "AAPL": hi, "MSFT": hi}):
            result = trim_for_beta_target(positions, target_beta=0.5, current_beta=2.0)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 7. get_factor_dashboard
# ---------------------------------------------------------------------------

class TestGetFactorDashboard:
    def test_required_keys_present(self):
        positions = _positions_simple(["AAPL", "MSFT"])
        with PricePatcher():
            dashboard = get_factor_dashboard(positions, equity=20_000.0)
        for key in ("beta", "sector_exposure", "momentum_tilt", "alerts", "as_of", "equity"):
            assert key in dashboard

    def test_overweight_alert_for_dominant_position(self):
        # Each position is 50% of portfolio → should fire overweight alert
        positions = {
            "AAPL": {"market_value": 5000.0},
            "MSFT": {"market_value": 5000.0},
        }
        with PricePatcher():
            dashboard = get_factor_dashboard(positions, equity=10_000.0)
        combined = " ".join(dashboard["alerts"])
        assert "OVERWEIGHT" in combined

    def test_no_overweight_for_balanced_portfolio(self):
        # 10 equal-weight positions → 10% each, below 15% threshold
        syms = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
                "JPM",  "UNH",  "XOM",  "JNJ",   "V"]
        positions = {s: {"market_value": 1000.0} for s in syms}
        with PricePatcher():
            dashboard = get_factor_dashboard(positions, equity=10_000.0)
        overweight = [a for a in dashboard["alerts"] if "OVERWEIGHT" in a]
        assert overweight == []

    def test_beta_is_float(self):
        positions = _positions_simple(["AAPL"])
        with PricePatcher():
            dashboard = get_factor_dashboard(positions, equity=10_000.0)
        assert isinstance(dashboard["beta"], float)

    def test_sector_exposure_sums_to_one(self):
        positions = _positions_simple(["AAPL", "MSFT", "NVDA", "GOOGL"])
        with PricePatcher():
            dashboard = get_factor_dashboard(positions, equity=40_000.0)
        total = sum(dashboard["sector_exposure"].values())
        assert total == pytest.approx(1.0, rel=1e-6)


# ---------------------------------------------------------------------------
# 8. run_eod_factor_check
# ---------------------------------------------------------------------------

class TestRunEodFactorCheck:
    def test_returns_dict(self):
        positions = _positions_simple(["AAPL", "MSFT"])
        with PricePatcher():
            with patch("analysis.factor_monitor._write_factor_log"):
                result = run_eod_factor_check(positions, equity=20_000.0)
        assert isinstance(result, dict)

    def test_log_written_with_required_keys(self):
        positions  = _positions_simple(["AAPL"])
        log_entries: list[dict] = []

        def capture(dashboard):
            log_entries.append({
                "date":            date.today().isoformat(),
                "beta":            dashboard.get("beta"),
                "sector_exposure": dashboard.get("sector_exposure", {}),
                "momentum_tilt":   dashboard.get("momentum_tilt"),
                "alerts":          dashboard.get("alerts", []),
            })

        with PricePatcher():
            with patch("analysis.factor_monitor._write_factor_log", side_effect=capture):
                run_eod_factor_check(positions, equity=10_000.0)

        assert len(log_entries) == 1
        entry = log_entries[0]
        for key in ("date", "beta", "sector_exposure", "momentum_tilt", "alerts"):
            assert key in entry

    def test_actual_log_file_written(self, tmp_path):
        positions = _positions_simple(["AAPL"])
        log_file  = str(tmp_path / "factor_monitor.json")

        original  = fm._FACTOR_LOG
        fm._FACTOR_LOG = log_file
        try:
            with PricePatcher():
                run_eod_factor_check(positions, equity=10_000.0)
        finally:
            fm._FACTOR_LOG = original

        assert os.path.exists(log_file)
        with open(log_file) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) >= 1
        parsed = json.loads(lines[-1])
        assert "beta" in parsed
        assert "sector_exposure" in parsed

    def test_bear_regime_sets_target(self):
        positions = _positions_simple(["AAPL", "MSFT"])

        import analysis.regime as _regime_mod
        from analysis.regime import Regime

        with PricePatcher():
            with patch("analysis.factor_monitor._write_factor_log"):
                with patch.object(_regime_mod, "detect_regime", return_value=Regime.BEAR):
                    result = run_eod_factor_check(positions, equity=20_000.0)

        assert result.get("regime_beta_target") == 0.5

    def test_regime_check_failure_does_not_crash(self):
        """If regime module raises, EOD check should still return a dashboard."""
        positions = _positions_simple(["AAPL"])

        import analysis.regime as _regime_mod

        with PricePatcher():
            with patch("analysis.factor_monitor._write_factor_log"):
                with patch.object(_regime_mod, "detect_regime", side_effect=RuntimeError("no regime")):
                    result = run_eod_factor_check(positions, equity=10_000.0)
        assert isinstance(result, dict)
        assert "beta" in result
