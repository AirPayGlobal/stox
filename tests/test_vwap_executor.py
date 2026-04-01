"""
Tests for trading/vwap_executor.py

Run with:
    pytest tests/test_vwap_executor.py -v
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_df(n: int = 30, base: float = 100.0) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame."""
    return pd.DataFrame(
        {
            "open":   [base] * n,
            "high":   [base + 1.0] * n,
            "low":    [base - 1.0] * n,
            "close":  [base] * n,
            "volume": [1_000_000] * n,
        }
    )


@pytest.fixture()
def tmp_log(tmp_path, monkeypatch):
    log_file = str(tmp_path / "vwap_fill_stats.json")
    monkeypatch.setattr("trading.vwap_executor._FILL_STATS_FILE", log_file)
    return log_file


@pytest.fixture()
def mock_client():
    """Return a mock TradingClient injected into alpaca_client."""
    client = MagicMock()
    with patch("trading.vwap_executor.get_trading_client", return_value=client):
        yield client


@pytest.fixture()
def executor(tmp_log, mock_client):
    from trading.vwap_executor import VWAPExecutor
    return VWAPExecutor(buffer_pct=0.0005)


# ---------------------------------------------------------------------------
# calculate_vwap tests
# ---------------------------------------------------------------------------

class TestCalculateVwap:
    def test_uniform_price_and_volume(self):
        from trading.vwap_executor import calculate_vwap
        df = _make_df(20, base=50.0)
        # typical price = (51+49+50)/3 = 50, so VWAP = 50
        vwap = calculate_vwap(df, lookback_bars=20)
        assert abs(vwap - 50.0) < 0.01

    def test_lookback_respected(self):
        from trading.vwap_executor import calculate_vwap
        df = _make_df(40, base=100.0)
        # All bars identical so result should still be ~100
        vwap20 = calculate_vwap(df, lookback_bars=20)
        vwap40 = calculate_vwap(df, lookback_bars=40)
        assert abs(vwap20 - vwap40) < 0.01

    def test_zero_volume_returns_zero(self):
        from trading.vwap_executor import calculate_vwap
        df = _make_df(10, base=100.0)
        df["volume"] = 0
        assert calculate_vwap(df, lookback_bars=10) == 0.0

    def test_missing_column_raises(self):
        from trading.vwap_executor import calculate_vwap
        df = _make_df(10).drop(columns=["volume"])
        with pytest.raises(KeyError):
            calculate_vwap(df)

    def test_typical_price_weighting(self):
        """VWAP should weight high-volume bars more heavily."""
        from trading.vwap_executor import calculate_vwap
        # Two bars: first bar typical=100, second bar typical=200, higher volume
        df = pd.DataFrame({
            "high":   [101.0, 201.0],
            "low":    [99.0,  199.0],
            "close":  [100.0, 200.0],
            "volume": [1,     9],      # 90% of volume at 200
        })
        vwap = calculate_vwap(df, lookback_bars=2)
        # Expected ≈ (100*1 + 200*9) / 10 = 190
        assert abs(vwap - 190.0) < 0.01


# ---------------------------------------------------------------------------
# Price helper tests
# ---------------------------------------------------------------------------

class TestPriceHelpers:
    def test_entry_limit_above_vwap(self, executor):
        price = executor.get_entry_limit_price(100.0)
        assert price > 100.0
        assert abs(price - 100.05) < 0.01   # 0.05% buffer → 100.05

    def test_exit_limit_below_vwap(self, executor):
        price = executor.get_exit_limit_price(100.0)
        assert price < 100.0
        assert abs(price - 99.95) < 0.01

    def test_prices_rounded_to_cents(self, executor):
        price = executor.get_entry_limit_price(123.456789)
        assert price == round(price, 2)


# ---------------------------------------------------------------------------
# Order placement tests
# ---------------------------------------------------------------------------

class TestOrderPlacement:
    def _mock_order(self, oid: str = "abc-123"):
        order = MagicMock()
        order.id = oid
        return order

    def test_place_entry_order_returns_id(self, executor, mock_client):
        mock_client.submit_order.return_value = self._mock_order("entry-1")
        oid = executor.place_entry_order("AAPL", 10, vwap=100.0)
        assert oid == "entry-1"

    def test_place_exit_order_returns_id(self, executor, mock_client):
        mock_client.submit_order.return_value = self._mock_order("exit-1")
        oid = executor.place_exit_order("AAPL", 10, vwap=100.0)
        assert oid == "exit-1"

    def test_failed_order_returns_none(self, executor, mock_client):
        mock_client.submit_order.side_effect = Exception("API error")
        oid = executor.place_entry_order("AAPL", 10, vwap=100.0)
        assert oid is None

    def test_order_tracked_in_memory(self, executor, mock_client):
        mock_client.submit_order.return_value = self._mock_order("track-1")
        executor.place_entry_order("MSFT", 5, vwap=200.0)
        assert "track-1" in executor._orders
        meta = executor._orders["track-1"]
        assert meta["symbol"] == "MSFT"
        assert meta["side"] == "buy"
        assert meta["qty"] == 5

    def test_entry_uses_limit_above_vwap(self, executor, mock_client):
        """Verify the actual limit price submitted to Alpaca is above VWAP."""
        from alpaca.trading.requests import LimitOrderRequest
        mock_client.submit_order.return_value = self._mock_order("lp-1")
        executor.place_entry_order("AAPL", 1, vwap=100.0)
        submitted: LimitOrderRequest = mock_client.submit_order.call_args[0][0]
        assert submitted.limit_price > 100.0


# ---------------------------------------------------------------------------
# Stale order cancellation
# ---------------------------------------------------------------------------

class TestStaleCancellation:
    def _place_order(self, executor, mock_client, oid: str = "stale-1"):
        order = MagicMock()
        order.id = oid
        mock_client.submit_order.return_value = order
        return executor.place_entry_order("TSLA", 10, vwap=150.0)

    def test_cancel_after_max_candles(self, executor, mock_client):
        oid = self._place_order(executor, mock_client)
        for _ in range(3):
            executor.increment_scan_count(oid)
        result = executor.check_and_cancel_stale(oid, max_candles=3)
        assert result is True
        mock_client.cancel_order_by_id.assert_called_once_with(oid)

    def test_no_cancel_before_max_candles(self, executor, mock_client):
        oid = self._place_order(executor, mock_client)
        executor.increment_scan_count(oid)
        executor.increment_scan_count(oid)
        result = executor.check_and_cancel_stale(oid, max_candles=3)
        assert result is False

    def test_stale_order_removed_from_tracking(self, executor, mock_client):
        oid = self._place_order(executor, mock_client)
        for _ in range(3):
            executor.increment_scan_count(oid)
        executor.check_and_cancel_stale(oid, max_candles=3)
        assert oid not in executor._orders

    def test_filled_order_not_cancelled(self, executor, mock_client):
        oid = self._place_order(executor, mock_client)
        executor.mark_filled(oid, fill_price=151.0)
        for _ in range(5):
            executor.increment_scan_count(oid)
        result = executor.check_and_cancel_stale(oid, max_candles=3)
        assert result is False


# ---------------------------------------------------------------------------
# Fill-rate statistics
# ---------------------------------------------------------------------------

class TestFillRateStats:
    def test_empty_log_returns_zeros(self, executor, tmp_log):
        stats = executor.get_fill_rate_stats()
        assert stats["total_orders"] == 0
        assert stats["fill_rate"] == 0.0

    def test_fill_rate_computed_correctly(self, executor, mock_client, tmp_log):
        def make_order(oid):
            o = MagicMock()
            o.id = oid
            return o

        # Place 4 orders, mark 3 filled
        for i in range(4):
            mock_client.submit_order.return_value = make_order(f"ord-{i}")
            oid = executor.place_entry_order("X", 1, vwap=10.0)
            if i < 3:
                executor.mark_filled(oid, fill_price=10.05)
            else:
                for _ in range(3):
                    executor.increment_scan_count(oid)
                executor.check_and_cancel_stale(oid, max_candles=3)

        stats = executor.get_fill_rate_stats()
        assert stats["total_orders"] == 4
        assert stats["filled_orders"] == 3
        assert abs(stats["fill_rate"] - 0.75) < 0.01

    def test_buffer_widens_on_low_fill_rate(self, executor, mock_client, tmp_log):
        """Buffer auto-widens when fill rate < 60%."""
        # Write 10 log records with 4 filled (40%)
        stats_data = [{"filled": True}] * 4 + [{"filled": False}] * 6
        with open(tmp_log, "w") as fh:
            for r in stats_data:
                fh.write(json.dumps(r) + "\n")
        executor.adjust_buffer_if_needed()
        assert executor._buffer_pct == pytest.approx(0.0010, abs=1e-6)

    def test_buffer_stays_narrow_on_good_fill_rate(self, executor, tmp_log):
        stats_data = [{"filled": True}] * 8 + [{"filled": False}] * 2
        with open(tmp_log, "w") as fh:
            for r in stats_data:
                fh.write(json.dumps(r) + "\n")
        executor.adjust_buffer_if_needed()
        assert executor._buffer_pct == pytest.approx(0.0005, abs=1e-6)


# ---------------------------------------------------------------------------
# Pairs leg synchronisation
# ---------------------------------------------------------------------------

class TestPairLegs:
    def _setup_order(self, mock_client, oid):
        o = MagicMock()
        o.id = oid
        return o

    def test_both_legs_submitted(self, executor, mock_client):
        mock_client.submit_order.side_effect = [
            self._setup_order(mock_client, "long-1"),
            self._setup_order(mock_client, "short-1"),
        ]
        result = executor.place_pair_legs("AAPL", 10, "MSFT", 10, 150.0, 200.0)
        assert result["status"] == "filled"
        assert result["long_id"] == "long-1"
        assert result["short_id"] == "short-1"
        assert mock_client.submit_order.call_count == 2

    def test_partial_cancel_when_one_leg_fails(self, executor, mock_client):
        """If the short-leg submit raises, the long leg is unwound."""
        long_order = self._setup_order(mock_client, "long-2")
        mock_client.submit_order.side_effect = [long_order, Exception("short rejected")]
        result = executor.place_pair_legs("AAPL", 10, "MSFT", 10, 150.0, 200.0)
        assert result["status"] == "partial_cancel"

    def test_failed_result_when_both_legs_fail(self, executor, mock_client):
        mock_client.submit_order.side_effect = Exception("API down")
        result = executor.place_pair_legs("AAPL", 10, "MSFT", 10, 150.0, 200.0)
        assert result["status"] == "failed"
        assert result["long_id"] is None
        assert result["short_id"] is None

    def test_peer_ids_cross_tagged(self, executor, mock_client):
        mock_client.submit_order.side_effect = [
            self._setup_order(mock_client, "L"),
            self._setup_order(mock_client, "S"),
        ]
        executor.place_pair_legs("AAPL", 5, "MSFT", 5, 150.0, 200.0)
        assert executor._orders["L"]["pair_peer"] == "S"
        assert executor._orders["S"]["pair_peer"] == "L"

    def test_check_pair_sync_both_filled(self, executor, mock_client):
        # Pre-populate order tracking
        executor._orders["L"] = {
            "symbol": "AAPL", "qty": 5, "side": "buy",
            "submitted_at": "2026-01-01T00:00:00+00:00",
            "scan_count": 0, "filled": False,
            "limit_price": 150.0, "market_close_at_submission": 150.0,
        }
        executor._orders["S"] = {
            "symbol": "MSFT", "qty": 5, "side": "sell",
            "submitted_at": "2026-01-01T00:00:00+00:00",
            "scan_count": 0, "filled": False,
            "limit_price": 200.0, "market_close_at_submission": 200.0,
        }
        filled_order = MagicMock()
        filled_order.status = "filled"
        filled_order.filled_avg_price = "151.0"
        mock_client.get_order_by_id.return_value = filled_order

        result = executor.check_pair_sync("L", "S")
        assert result["status"] == "filled"
