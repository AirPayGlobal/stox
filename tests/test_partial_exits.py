"""
Tests for trading/partial_exits.py

Run with:
    pytest tests/test_partial_exits.py -v
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Minimal Trade stub so we don't need to import the whole portfolio module
# ---------------------------------------------------------------------------
@dataclass
class _Trade:
    symbol: str
    side: str
    shares: int
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    order_id: str = ""
    opened_at: str = "2026-01-01T00:00:00"
    closed_at: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    status: str = "OPEN"
    high_water_mark: float = 0.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_log(tmp_path, monkeypatch):
    """Redirect the partial-exits log file to a temp directory."""
    log_file = str(tmp_path / "partial_exits.json")
    monkeypatch.setattr("trading.partial_exits._LOG_FILE", log_file)
    return log_file


@pytest.fixture()
def manager(tmp_log):
    from trading.partial_exits import PartialExitManager
    return PartialExitManager()


@pytest.fixture()
def trade_100():
    """100-share trade entered at $100."""
    return _Trade(symbol="AAPL", side="BUY", shares=100, entry_price=100.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTier1:
    """Tier-1 fires at +8%."""

    def test_no_action_below_threshold(self, manager, trade_100):
        actions = manager.check_exits("AAPL", trade_100, current_price=107.0, high_water_mark=107.0)
        assert actions == [], "Should not fire below 8% gain"

    def test_fires_at_exact_threshold(self, manager, trade_100):
        actions = manager.check_exits("AAPL", trade_100, current_price=108.0, high_water_mark=108.0)
        assert len(actions) == 1
        act = actions[0]
        assert act.reason == "PARTIAL_1"
        assert act.symbol == "AAPL"

    def test_tier1_share_count(self, manager, trade_100):
        """Tier-1 should sell ≈33 shares (floor of 100 * 0.33)."""
        actions = manager.check_exits("AAPL", trade_100, current_price=110.0, high_water_mark=110.0)
        assert actions[0].shares_to_sell == math.floor(100 * 0.33)

    def test_tier1_does_not_refire(self, manager, trade_100):
        """Once Tier-1 is recorded it must not fire again."""
        actions = manager.check_exits("AAPL", trade_100, current_price=110.0, high_water_mark=110.0)
        assert len(actions) == 1
        manager.record_partial("AAPL", actions[0].shares_to_sell, 110.0, 0.10)

        # Same price — should be silent
        actions2 = manager.check_exits("AAPL", trade_100, current_price=110.0, high_water_mark=110.0)
        assert actions2 == [], "Tier-1 must not re-fire after being recorded"


class TestTier2:
    """Tier-2 fires at +15% after Tier-1 has fired."""

    def _arm_tier1(self, manager, trade):
        actions = manager.check_exits(trade.symbol, trade, 110.0, 110.0)
        manager.record_partial(trade.symbol, actions[0].shares_to_sell, 110.0, 0.10)

    def test_tier2_fires_after_tier1(self, manager, trade_100):
        self._arm_tier1(manager, trade_100)
        # 115.1 avoids the float representation of 115.0/100.0 - 1.0 < 0.15
        actions = manager.check_exits("AAPL", trade_100, current_price=115.1, high_water_mark=115.1)
        assert len(actions) == 1
        assert actions[0].reason == "PARTIAL_2"

    def test_tier2_share_count(self, manager, trade_100):
        self._arm_tier1(manager, trade_100)
        actions = manager.check_exits("AAPL", trade_100, current_price=116.0, high_water_mark=116.0)
        assert actions[0].shares_to_sell == math.floor(100 * 0.33)

    def test_tier2_does_not_fire_before_tier1(self, manager, trade_100):
        """At +15% with tier1 NOT yet fired, only PARTIAL_1 should come back."""
        actions = manager.check_exits("AAPL", trade_100, current_price=116.0, high_water_mark=116.0)
        assert actions[0].reason == "PARTIAL_1"


class TestTrailingStop:
    """After both tiers fire, a 7% trailing stop on the remaining shares."""

    def _arm_both_tiers(self, manager, trade):
        # Tier 1
        a1 = manager.check_exits(trade.symbol, trade, 110.0, 110.0)
        manager.record_partial(trade.symbol, a1[0].shares_to_sell, 110.0, 0.10)
        # Tier 2
        a2 = manager.check_exits(trade.symbol, trade, 116.0, 116.0)
        manager.record_partial(trade.symbol, a2[0].shares_to_sell, 116.0, 0.16)

    def test_trail_stop_fires(self, manager, trade_100):
        self._arm_both_tiers(manager, trade_100)
        # HWM = 120, current = 120 * 0.93 = 111.6 → should trigger
        hwm = 120.0
        trail_floor = hwm * 0.93
        actions = manager.check_exits("AAPL", trade_100, current_price=trail_floor - 0.01, high_water_mark=hwm)
        assert len(actions) == 1
        assert actions[0].reason == "TRAIL_STOP"

    def test_trail_stop_does_not_fire_above_floor(self, manager, trade_100):
        self._arm_both_tiers(manager, trade_100)
        hwm = 120.0
        trail_floor = hwm * 0.93
        actions = manager.check_exits("AAPL", trade_100, current_price=trail_floor + 0.50, high_water_mark=hwm)
        assert actions == []

    def test_trail_stop_remaining_shares(self, manager, trade_100):
        """Remaining shares after 33%+33% = 34 (floor of 100*0.34)."""
        self._arm_both_tiers(manager, trade_100)
        hwm = 120.0
        actions = manager.check_exits("AAPL", trade_100, current_price=hwm * 0.92, high_water_mark=hwm)
        # Remaining = 100 - floor(33) - floor(33) = 34
        expected_remaining = 100 - math.floor(100 * 0.33) - math.floor(100 * 0.33)
        assert actions[0].shares_to_sell == expected_remaining


class TestBreakEven:
    """Break-even floor fires when HWM > entry*1.03 and price falls back to entry."""

    def _arm_both_tiers(self, manager, trade):
        a1 = manager.check_exits(trade.symbol, trade, 110.0, 110.0)
        manager.record_partial(trade.symbol, a1[0].shares_to_sell, 110.0, 0.10)
        a2 = manager.check_exits(trade.symbol, trade, 116.0, 116.0)
        manager.record_partial(trade.symbol, a2[0].shares_to_sell, 116.0, 0.16)

    def test_break_even_fires(self, manager, trade_100):
        self._arm_both_tiers(manager, trade_100)
        # HWM > 103, price back at entry
        hwm = 106.0   # > entry * 1.03
        actions = manager.check_exits("AAPL", trade_100, current_price=100.0, high_water_mark=hwm)
        assert len(actions) == 1
        assert actions[0].reason == "BREAK_EVEN"

    def test_break_even_does_not_fire_if_hwm_too_low(self, manager, trade_100):
        self._arm_both_tiers(manager, trade_100)
        hwm = 102.0   # < entry * 1.03
        actions = manager.check_exits("AAPL", trade_100, current_price=100.0, high_water_mark=hwm)
        assert actions == []


class TestPnlTracking:
    """Realised and unrealised P&L helpers."""

    def test_realised_pnl_accumulates(self, manager, trade_100):
        manager._ensure("AAPL", 100)
        manager.record_partial("AAPL", 33, 110.0, 0.10)
        manager.record_partial("AAPL", 33, 116.0, 0.16)
        pnl = manager.get_realised_pnl("AAPL")
        assert pnl > 0

    def test_unrealised_pnl(self, manager, trade_100):
        manager._ensure("AAPL", 100)
        manager.record_partial("AAPL", 33, 110.0, 0.10)
        manager.record_partial("AAPL", 33, 116.0, 0.16)
        # 34 shares remain; current price 120 vs entry 100 → +20 each
        upnl = manager.get_unrealised_pnl("AAPL", 120.0, 100.0)
        remaining = 100 - 33 - 33
        assert abs(upnl - remaining * 20.0) < 0.01

    def test_reset_clears_state(self, manager, trade_100):
        manager._ensure("AAPL", 100)
        manager.record_partial("AAPL", 33, 110.0, 0.10)
        manager.reset("AAPL")
        assert manager.get_realised_pnl("AAPL") == 0.0

    def test_log_file_written(self, manager, trade_100, tmp_log):
        manager._ensure("AAPL", 100)
        manager.record_partial("AAPL", 33, 110.0, 0.10)
        assert os.path.exists(tmp_log)
        with open(tmp_log) as fh:
            lines = [l for l in fh if l.strip()]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["symbol"] == "AAPL"
        assert rec["shares"] == 33
        assert rec["price"] == 110.0


class TestEdgeCases:
    """Edge cases and guard rails."""

    def test_none_trade_returns_empty(self, manager):
        actions = manager.check_exits("AAPL", None, 110.0, 110.0)
        assert actions == []

    def test_zero_price_returns_empty(self, manager, trade_100):
        actions = manager.check_exits("AAPL", trade_100, 0.0, 0.0)
        assert actions == []

    def test_get_remaining_no_sales(self, manager):
        assert manager.get_remaining_shares("NEWCO", 50) == 50

    def test_minimum_one_share_sold(self, manager):
        """With a 1-share position, Tier-1 must still sell at least 1 share."""
        trade = _Trade(symbol="TINY", side="BUY", shares=1, entry_price=100.0)
        actions = manager.check_exits("TINY", trade, 110.0, 110.0)
        assert actions[0].shares_to_sell >= 1

    def test_record_partial_unknown_symbol_warns(self, manager, caplog):
        """Calling record_partial before _ensure logs a warning instead of crashing."""
        import logging
        with caplog.at_level(logging.WARNING, logger="trading.partial_exits"):
            manager.record_partial("GHOST", 10, 100.0, 0.05)
        assert any("GHOST" in r.message for r in caplog.records)
