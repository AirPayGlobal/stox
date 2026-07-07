from config import Config
from trading.risk import RiskManager


def make_rm() -> RiskManager:
    rm = RiskManager()
    rm.start_day()
    return rm


def test_can_open_normally():
    rm = make_rm()
    ok, why = rm.can_open(pnl=0.0, open_positions=0)
    assert ok, why


def test_target_hit_does_not_stop_trading():
    rm = make_rm()
    ok, why = rm.can_open(pnl=Config.DAILY_PROFIT_TARGET, open_positions=0)
    assert ok, why
    assert rm.state.target_hit
    assert not rm.must_flatten()


def test_profit_floor_ratchets_with_peak():
    rm = make_rm()
    rm.update_governor(pnl=10_000)  # peak +10k
    # floor = max(5000*0.7, 10000*(1-0.3)) = 7000
    assert rm.profit_floor() == 7_000
    rm.update_governor(pnl=15_000)  # peak +15k -> floor 10500
    assert rm.profit_floor() == 10_500
    # Peak never ratchets down.
    rm.update_governor(pnl=12_000)
    assert rm.state.peak_pnl == 15_000


def test_giveback_floor_blocks_entries_in_hold_mode(monkeypatch):
    monkeypatch.setattr(Config, "PROTECT_MODE", "hold")
    rm = make_rm()
    rm.update_governor(pnl=10_000)   # target hit, peak +10k, floor +7k
    ok, _ = rm.can_open(pnl=10_000, open_positions=0)
    assert ok
    ok, why = rm.can_open(pnl=6_900, open_positions=0)  # +6.9k <= floor
    assert not ok
    assert "profit protection" in why
    # Hold mode: open positions are NOT force-closed.
    assert not rm.must_flatten()
    # Sticky even if P&L bounces back above the floor.
    ok, _ = rm.can_open(pnl=12_000, open_positions=0)
    assert not ok


def test_giveback_floor_flattens_in_flatten_mode(monkeypatch):
    monkeypatch.setattr(Config, "PROTECT_MODE", "flatten")
    rm = make_rm()
    rm.update_governor(pnl=10_000)
    rm.update_governor(pnl=6_900)   # hits the +7k floor
    assert rm.state.protect_locked
    assert rm.must_flatten()
    assert rm.flatten_reason() == "PROTECT"


def test_floor_at_exact_target():
    rm = make_rm()
    rm.update_governor(pnl=Config.DAILY_PROFIT_TARGET)
    # At the target both floor terms coincide: keep 70% of the target.
    assert rm.profit_floor() == Config.DAILY_PROFIT_TARGET * Config.PROFIT_FLOOR_PCT


def test_max_loss_halts_and_flattens():
    rm = make_rm()
    ok, why = rm.can_open(pnl=-Config.DAILY_MAX_LOSS, open_positions=0)
    assert not ok
    assert "loss" in why
    assert rm.must_flatten()
    assert rm.flatten_reason() == "HALT"


def test_small_loss_does_not_halt():
    # Regression: junk-position noise used to false-halt below the threshold.
    rm = make_rm()
    ok, why = rm.can_open(pnl=-517.0, open_positions=0)
    assert ok, why
    assert not rm.state.loss_halted


def test_max_concurrent_positions():
    rm = make_rm()
    ok, why = rm.can_open(pnl=0.0, open_positions=Config.MAX_CONCURRENT_POSITIONS)
    assert not ok
    assert "concurrent" in why


def test_max_trades_per_day():
    rm = make_rm()
    for _ in range(Config.MAX_TRADES_PER_DAY):
        rm.record_open()
    ok, why = rm.can_open(pnl=0.0, open_positions=0)
    assert not ok
    assert "trades per day" in why


def test_sizing_respects_risk_cap():
    rm = make_rm()
    equity, premium = 100_000, 2.50
    qty = rm.contracts_for(equity, premium)
    assert qty >= 1
    max_loss_at_stop = qty * premium * 100 * Config.STOP_LOSS_PCT
    assert max_loss_at_stop <= equity * Config.RISK_PER_TRADE_PCT
    outlay = qty * premium * 100
    assert outlay <= equity * Config.MAX_POSITION_PCT


def test_sizing_zero_when_unaffordable():
    rm = make_rm()
    assert rm.contracts_for(equity=1_000, premium=50.0) == 0
    assert rm.contracts_for(equity=100_000, premium=0.0) == 0


def test_reset_clears_locks():
    rm = make_rm()
    rm.state.protect_locked = True
    rm.state.loss_halted = True
    rm.reset()
    ok, _ = rm.can_open(pnl=0.0, open_positions=0)
    assert ok
    assert not rm.must_flatten()
