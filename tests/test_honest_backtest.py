"""
Unit tests for backtest/honest_backtest.py

The `ta` library is not available in the test environment, so we stub
`ta`, `ta.momentum`, `ta.trend`, and `ta.volatility` before any stox
module is imported.  Indicator columns are injected into synthetic
DataFrames using pure pandas / numpy arithmetic so the core backtest
logic can be exercised end-to-end.

Run:
    cd /home/user/stox && python -m pytest tests/test_honest_backtest.py -v
"""
from __future__ import annotations

import json
import os
import sys
import types
from dataclasses import asdict
from datetime import date
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Stub `ta` before any stox import touches it
# ---------------------------------------------------------------------------

def _stub_ta():
    ta = types.ModuleType("ta")
    for sub in ("momentum", "trend", "volatility"):
        m = types.ModuleType(f"ta.{sub}")
        setattr(ta, sub, m)
        sys.modules[f"ta.{sub}"] = m
    sys.modules["ta"] = ta

_stub_ta()

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Synthetic indicator builder (pure pandas / numpy)
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add the indicator columns expected by generate_signal without ta."""
    df = df.copy()
    df["ema_fast"]   = _ema(df["close"], 9)
    df["ema_slow"]   = _ema(df["close"], 21)
    df["ema_trend"]  = _ema(df["close"], 50)
    # RSI (Wilder)
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)
    # MACD
    ema12 = _ema(df["close"], 12)
    ema26 = _ema(df["close"], 26)
    macd  = ema12 - ema26
    signal = _ema(macd, 9)
    df["macd"]        = macd
    df["macd_signal"] = signal
    df["macd_hist"]   = macd - signal
    # Bollinger Bands
    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std
    df["bb_mid"]   = bb_mid
    bb_range = df["bb_upper"] - df["bb_lower"]
    df["bb_pct"]   = (df["close"] - df["bb_lower"]) / bb_range.replace(0, np.nan)
    # ATR
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift()).abs()
    lcp = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"]        = tr.rolling(14).mean()
    df["volume_sma"] = df["volume"].rolling(20).mean()
    return df


def _make_trending_df(n: int = 250, base_price: float = 100.0, trend: float = 0.003) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    rng   = np.random.default_rng(42)
    close = [base_price * (1 + trend) ** i + rng.uniform(-0.2, 0.2) for i in range(n)]
    close = [max(c, 1.0) for c in close]
    return pd.DataFrame(
        {
            "open":   [c * 0.998 for c in close],
            "high":   [c * 1.005 for c in close],
            "low":    [c * 0.995 for c in close],
            "close":  close,
            "volume": [1_000_000 + int(rng.integers(0, 500_000)) for _ in range(n)],
        },
        index=dates,
    )


def _make_indicator_df(n: int = 250) -> pd.DataFrame:
    df  = _make_trending_df(n)
    idf = _add_indicators(df)
    idf = idf.dropna(subset=["ema_fast", "ema_slow", "ema_trend", "rsi", "macd_hist", "atr"])
    return idf


# ---------------------------------------------------------------------------
# Patch add_all_indicators and generate_signal at the backtest module level
# ---------------------------------------------------------------------------

def _patched_add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    return _add_indicators(df)


from analysis.signals import Signal

def _patched_generate_signal(df: pd.DataFrame):
    """Minimal signal generator that fires BUY on EMA fast > slow crossover."""
    df2 = _add_indicators(df)
    df2 = df2.dropna(subset=["ema_fast", "ema_slow", "ema_trend", "rsi", "macd_hist", "bb_pct"])
    if len(df2) < 2:
        return Signal.HOLD, 0
    latest = df2.iloc[-1]
    prev   = df2.iloc[-2]
    if (latest["ema_fast"] > latest["ema_slow"] and
            prev["ema_fast"] <= prev["ema_slow"] and
            latest["close"] > latest["ema_trend"] and
            30 <= latest["rsi"] <= 75):
        return Signal.BUY, 60
    return Signal.HOLD, 0


# ---------------------------------------------------------------------------
# Now import the module under test (ta stub is already in sys.modules)
# ---------------------------------------------------------------------------

with (
    patch("analysis.indicators.add_all_indicators", _patched_add_all_indicators),
    patch("analysis.signals.add_all_indicators",    _patched_add_all_indicators),
):
    from backtest.honest_backtest import (
        BacktestConfig,
        HonestBacktestResult,
        HonestSimTrade,
        SLIPPAGE_PCT,
        COMMISSION_PER_SHARE,
        _compute_metrics,
        _simulate,
        _prepare_indicators,
        _add_months,
        run_honest_backtest,
    )


# ---------------------------------------------------------------------------
# 1. BacktestConfig
# ---------------------------------------------------------------------------

class TestBacktestConfig:
    def test_default_slippage(self):
        cfg = BacktestConfig()
        assert cfg.slippage_pct == SLIPPAGE_PCT

    def test_default_commission(self):
        cfg = BacktestConfig()
        assert cfg.commission_per_share == COMMISSION_PER_SHARE

    def test_custom_values(self):
        cfg = BacktestConfig(
            slippage_pct=0.002,
            commission_per_share=0.005,
            initial_capital=50_000.0,
            max_position_pct=0.03,
            stop_loss_pct=0.015,
            max_open_positions=10,
        )
        assert cfg.slippage_pct == 0.002
        assert cfg.initial_capital == 50_000.0
        assert cfg.max_open_positions == 10

    def test_dataclass_serialisable(self):
        cfg = BacktestConfig()
        d = asdict(cfg)
        assert "slippage_pct" in d
        assert "commission_per_share" in d


# ---------------------------------------------------------------------------
# 2. Cost arithmetic
# ---------------------------------------------------------------------------

class TestCostArithmetic:
    def test_entry_fill_above_mid(self):
        mid = 100.0
        assert mid * (1 + 0.001) == pytest.approx(100.1)

    def test_exit_fill_below_mid(self):
        mid = 110.0
        assert mid * (1 - 0.001) == pytest.approx(109.89)

    def test_net_pnl_less_than_gross_on_winner(self):
        entry_mid = 100.0
        exit_mid  = 110.0
        shares    = 100
        slippage  = 0.001
        commission = 0.01

        entry_fill = entry_mid * (1 + slippage)
        exit_fill  = exit_mid  * (1 - slippage)
        gross_pnl  = (exit_mid  - entry_mid) * shares
        net_pnl    = (exit_fill - entry_fill) * shares - commission * shares * 2

        assert net_pnl < gross_pnl
        assert gross_pnl == pytest.approx(1000.0)

    def test_net_pnl_worse_on_loser(self):
        entry_mid = 100.0
        exit_mid  = 95.0
        shares    = 50
        slippage  = 0.001
        commission = 0.01

        entry_fill = entry_mid * (1 + slippage)
        exit_fill  = exit_mid  * (1 - slippage)
        gross_pnl  = (exit_mid  - entry_mid) * shares
        net_pnl    = (exit_fill - entry_fill) * shares - commission * shares * 2

        assert net_pnl < gross_pnl
        assert gross_pnl < 0

    def test_round_trip_cost_formula(self):
        """Total cost = slippage (entry + exit) + commissions (entry + exit)."""
        entry_mid = 100.0
        exit_mid  = 105.0
        shares    = 200
        slippage  = 0.001
        commission = 0.01

        entry_fill = entry_mid * (1 + slippage)
        exit_fill  = exit_mid  * (1 - slippage)
        gross_pnl  = (exit_mid  - entry_mid) * shares
        net_pnl    = (exit_fill - entry_fill) * shares - commission * shares * 2

        slip_cost = (entry_fill - entry_mid) * shares + (exit_mid - exit_fill) * shares
        comm_cost = commission * shares * 2
        assert abs(gross_pnl - net_pnl - slip_cost - comm_cost) < 1e-9


# ---------------------------------------------------------------------------
# 3. _compute_metrics
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def test_empty_input_returns_zeros(self):
        m = _compute_metrics([], [], 10_000.0, 252)
        assert m["win_rate"] == 0.0
        assert m["sharpe"] == 0.0

    def test_all_winners_profit_factor_inf(self):
        pnls   = [100.0, 200.0, 150.0]
        equity = [10_000.0, 10_100.0, 10_300.0, 10_450.0]
        m = _compute_metrics(pnls, equity, 10_000.0, 252)
        assert m["win_rate"] == pytest.approx(1.0)
        assert m["profit_factor"] == float("inf")

    def test_max_drawdown_correct(self):
        equity = [10_000.0, 11_000.0, 9_500.0, 10_200.0]
        pnls   = [100.0, -150.0, 50.0]
        m = _compute_metrics(pnls, equity, 10_000.0, 200)
        expected_dd = (11_000.0 - 9_500.0) / 11_000.0
        assert m["max_drawdown"] == pytest.approx(expected_dd, rel=1e-4)

    def test_sharpe_positive_for_steady_uptrend(self):
        equity = [10_000.0 * (1.001 ** i) for i in range(200)]
        pnls   = [10.0] * 50
        m = _compute_metrics(pnls, equity, 10_000.0, 200)
        assert m["sharpe"] > 0

    def test_var_95_negative_for_volatile_series(self):
        rng    = np.random.default_rng(7)
        rets   = rng.normal(0.001, 0.02, 252)
        equity = [10_000.0]
        for r in rets:
            equity.append(equity[-1] * (1 + r))
        m = _compute_metrics([50.0] * 30, equity, 10_000.0, 252)
        assert m["var_95"] < 0


# ---------------------------------------------------------------------------
# 4. _simulate — cost ordering guarantees
# ---------------------------------------------------------------------------

class TestSimulate:
    def _build_data(self):
        idf           = _make_indicator_df(250)
        all_dates     = list(idf.index)
        indicator_data = {"SYNTH": idf}
        return indicator_data, all_dates

    def test_net_equity_lte_gross(self):
        data, dates = self._build_data()
        cfg = BacktestConfig(initial_capital=10_000.0, max_open_positions=5)

        with patch("backtest.honest_backtest.generate_signal", side_effect=_patched_generate_signal):
            with patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators):
                gross_trades, gross_curve = _simulate(data, dates, 60, cfg, apply_costs=False)
                net_trades,   net_curve   = _simulate(data, dates, 60, cfg, apply_costs=True)

        assert net_curve[-1] <= gross_curve[-1] + 0.01

    def test_net_pnl_le_gross_per_trade(self):
        data, dates = self._build_data()
        cfg = BacktestConfig(initial_capital=10_000.0)

        with patch("backtest.honest_backtest.generate_signal", side_effect=_patched_generate_signal):
            with patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators):
                gross_trades, _ = _simulate(data, dates, 60, cfg, apply_costs=False)
                net_trades,   _ = _simulate(data, dates, 60, cfg, apply_costs=True)

        for g, n in zip(gross_trades, net_trades):
            assert n.net_pnl <= g.gross_pnl + 1e-6

    def test_slippage_cost_nonnegative(self):
        data, dates = self._build_data()
        cfg = BacktestConfig(initial_capital=10_000.0)

        with patch("backtest.honest_backtest.generate_signal", side_effect=_patched_generate_signal):
            with patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators):
                net_trades, _ = _simulate(data, dates, 60, cfg, apply_costs=True)

        for t in net_trades:
            assert t.slippage_cost >= 0.0

    def test_commission_cost_nonnegative(self):
        data, dates = self._build_data()
        cfg = BacktestConfig(initial_capital=10_000.0)

        with patch("backtest.honest_backtest.generate_signal", side_effect=_patched_generate_signal):
            with patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators):
                net_trades, _ = _simulate(data, dates, 60, cfg, apply_costs=True)

        for t in net_trades:
            assert t.commission_cost >= 0.0

    def test_no_forward_looking_bias(self):
        """generate_signal is called with monotonically growing slice lengths."""
        data, dates = self._build_data()
        cfg = BacktestConfig(initial_capital=10_000.0)
        slices_seen: list[int] = []

        def tracking_signal(df_slice):
            slices_seen.append(len(df_slice))
            return _patched_generate_signal(df_slice)

        with patch("backtest.honest_backtest.generate_signal", side_effect=tracking_signal):
            with patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators):
                _simulate(data, dates, 60, cfg, apply_costs=False)

        assert all(s > 0 for s in slices_seen)
        for i in range(1, len(slices_seen)):
            assert slices_seen[i] >= slices_seen[i - 1], (
                f"Slice shrank at index {i}: {slices_seen[i - 1]} → {slices_seen[i]}"
            )


# ---------------------------------------------------------------------------
# 5. run_honest_backtest — end-to-end with mocked fetcher
# ---------------------------------------------------------------------------

class TestRunHonestBacktest:
    def _fetch(self, symbols, **kwargs):
        return {sym: _make_trending_df(300) for sym in symbols}

    def _patches(self):
        return [
            patch("backtest.honest_backtest.fetch_batch",          side_effect=self._fetch),
            patch("backtest.honest_backtest.add_all_indicators",   _patched_add_all_indicators),
            patch("backtest.honest_backtest.generate_signal",      _patched_generate_signal),
            patch("analysis.signals.add_all_indicators",           _patched_add_all_indicators),
        ]

    def test_returns_result_type(self):
        with (
            patch("backtest.honest_backtest.fetch_batch",        side_effect=self._fetch),
            patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators),
            patch("backtest.honest_backtest.generate_signal",    _patched_generate_signal),
        ):
            result = run_honest_backtest(["AAPL", "MSFT"], lookback_days=300)
        assert isinstance(result, HonestBacktestResult)

    def test_gross_return_gte_net_return(self):
        with (
            patch("backtest.honest_backtest.fetch_batch",        side_effect=self._fetch),
            patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators),
            patch("backtest.honest_backtest.generate_signal",    _patched_generate_signal),
        ):
            result = run_honest_backtest(["AAPL", "MSFT"], lookback_days=300)
        assert result.gross_total_return >= result.net_total_return - 1e-9

    def test_cost_drag_nonnegative(self):
        with (
            patch("backtest.honest_backtest.fetch_batch",        side_effect=self._fetch),
            patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators),
            patch("backtest.honest_backtest.generate_signal",    _patched_generate_signal),
        ):
            result = run_honest_backtest(["AAPL"], lookback_days=300)
        assert result.total_cost_drag >= -1e-9

    def test_slippage_and_commission_nonneg(self):
        with (
            patch("backtest.honest_backtest.fetch_batch",        side_effect=self._fetch),
            patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators),
            patch("backtest.honest_backtest.generate_signal",    _patched_generate_signal),
        ):
            result = run_honest_backtest(["AAPL", "MSFT"], lookback_days=300)
        assert result.total_slippage_cost    >= 0.0
        assert result.total_commission_cost  >= 0.0

    def test_inflation_report_has_key_metrics(self):
        with (
            patch("backtest.honest_backtest.fetch_batch",        side_effect=self._fetch),
            patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators),
            patch("backtest.honest_backtest.generate_signal",    _patched_generate_signal),
        ):
            result = run_honest_backtest(["AAPL"], lookback_days=300)
        report = result.inflation_report()
        for token in ("Total Return", "Sharpe", "Cost Drag", "Gross", "Net"):
            assert token in report, f"'{token}' missing from inflation report"

    def test_equity_curves_same_length(self):
        with (
            patch("backtest.honest_backtest.fetch_batch",        side_effect=self._fetch),
            patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators),
            patch("backtest.honest_backtest.generate_signal",    _patched_generate_signal),
        ):
            result = run_honest_backtest(["AAPL", "MSFT"], lookback_days=300)
        assert len(result.equity_curve_gross) == len(result.equity_curve_net)

    def test_trades_list_is_json_serialisable(self):
        with (
            patch("backtest.honest_backtest.fetch_batch",        side_effect=self._fetch),
            patch("backtest.honest_backtest.add_all_indicators", _patched_add_all_indicators),
            patch("backtest.honest_backtest.generate_signal",    _patched_generate_signal),
        ):
            result = run_honest_backtest(["AAPL"], lookback_days=300)
        json.dumps(result.trades, default=str)   # must not raise


# ---------------------------------------------------------------------------
# 6. _add_months helper
# ---------------------------------------------------------------------------

class TestAddMonths:
    def test_simple_three_month_increment(self):
        assert _add_months(date(2024, 1, 15), 3) == date(2024, 4, 15)

    def test_year_rollover(self):
        assert _add_months(date(2024, 11, 1), 3) == date(2025, 2, 1)

    def test_feb_end_clamping_leap_year(self):
        result = _add_months(date(2024, 1, 31), 1)
        assert result.month == 2
        assert result.day <= 29   # 2024 is a leap year

    def test_twelve_months_equals_one_year(self):
        assert _add_months(date(2024, 3, 10), 12) == date(2025, 3, 10)
