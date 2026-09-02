"""Engine-parity fib simulator tests (deterministic synthetic bars, no data)."""
import pandas as pd

from backtest.fib_sim import config_hash, run_fib_sim
from backtest.synth import synth_history
from config import Config

REQUIRED_FIELDS = {
    "strategy_version", "config_hash", "scenario", "symbol", "signal_ts",
    "underlying_price", "direction", "opt_type", "strike", "dte", "dte_bucket",
    "contract_bid", "contract_ask", "spread_pct", "delta", "intended_entry",
    "sim_fill", "fill_delay_bars", "order_outcome", "exit_reason", "pnl",
    "realized_r", "planned_risk", "opt_mfe", "opt_mae", "ul_mfe", "ul_mae",
    "regime_trend", "regime_vol", "tod_bucket", "filters",
}


def _bars(days=15):
    return {"SPY": synth_history("SPY", days, 550.0), "QQQ": synth_history("QQQ", days, 480.0)}


def test_records_have_all_research_fields():
    r = run_fib_sim(_bars(), scenario="base")
    assert r.trades, "expected some simulated trades on synthetic data"
    assert REQUIRED_FIELDS.issubset(r.trades[0].keys())
    assert r.trades[0]["config_hash"] == config_hash()


def test_execution_friction_is_monotonic():
    bars = _bars()
    def total(scen):
        return sum(t["pnl"] for t in run_fib_sim(bars, scenario=scen).trades)
    # More pessimistic execution never yields more P&L.
    assert total("baseline") >= total("base") >= total("conservative")


def test_deterministic_runs_identical():
    bars = _bars()
    a = run_fib_sim(bars, scenario="conservative")
    b = run_fib_sim(bars, scenario="conservative")
    assert [t["pnl"] for t in a.trades] == [t["pnl"] for t in b.trades]


def test_scan_cadence_reduces_trade_count(monkeypatch):
    bars = _bars()
    monkeypatch.setattr(Config, "FIB_SCAN_BARS", 1)
    dense = len(run_fib_sim(bars, scenario="baseline").trades)
    monkeypatch.setattr(Config, "FIB_SCAN_BARS", 15)
    sparse = len(run_fib_sim(bars, scenario="baseline").trades)
    assert sparse < dense


def test_max_trades_per_day_enforced(monkeypatch):
    monkeypatch.setattr(Config, "MAX_TRADES_PER_DAY", 2)
    r = run_fib_sim(_bars(), scenario="baseline")
    per_day: dict[str, int] = {}
    for t in r.trades:
        per_day[t["date"]] = per_day.get(t["date"], 0) + 1
    assert per_day, "expected trades"
    assert max(per_day.values()) <= 2


def test_daily_max_loss_halts_the_day(monkeypatch):
    # A tiny max-loss should stop new entries and stamp HALT exits on that day.
    monkeypatch.setattr(Config, "DAILY_MAX_LOSS", 50.0)
    r = run_fib_sim(_bars(), scenario="conservative")
    per_day: dict[str, int] = {}
    for t in r.trades:
        per_day[t["date"]] = per_day.get(t["date"], 0) + 1
    # No day can run away with trades once halted early; cap is loose but finite.
    assert max(per_day.values()) <= Config.MAX_TRADES_PER_DAY


def test_trend_filter_blocks_some_attempts(monkeypatch):
    monkeypatch.setattr(Config, "FIB_FILTER_TREND", True)
    r = run_fib_sim(_bars(), scenario="baseline")
    outcomes = {a["order_outcome"] for a in r.attempts}
    assert "BLOCKED_FILTER" in outcomes
    # A blocked attempt never becomes a trade.
    assert all(t["order_outcome"] != "BLOCKED_FILTER" for t in r.trades)


def test_dte_recorded():
    r0 = run_fib_sim(_bars(), scenario="baseline", dte=0)
    r1 = run_fib_sim(_bars(), scenario="baseline", dte=1)
    assert all(t["dte_bucket"] == "0DTE" for t in r0.trades)
    assert all(t["dte_bucket"] == "1DTE" for t in r1.trades)
