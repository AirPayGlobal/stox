from config import Config
from trading.risk import RiskManager


def make_rm(equity=100_000.0) -> RiskManager:
    rm = RiskManager()
    rm.start_day(equity)
    return rm


def test_can_open_normally():
    rm = make_rm()
    ok, why = rm.can_open(equity=100_000, open_positions=0)
    assert ok, why


def test_profit_target_locks_day():
    rm = make_rm(100_000)
    equity_after_target = 100_000 + Config.DAILY_PROFIT_TARGET
    ok, why = rm.can_open(equity=equity_after_target, open_positions=0)
    assert not ok
    assert "target" in why
    # Sticky: even if P&L drops back below the target, stay locked.
    ok, _ = rm.can_open(equity=100_000 + 100, open_positions=0)
    assert not ok


def test_max_loss_halts_and_flattens():
    rm = make_rm(100_000)
    ok, why = rm.can_open(equity=100_000 - Config.DAILY_MAX_LOSS, open_positions=0)
    assert not ok
    assert "loss" in why
    assert rm.must_flatten()


def test_max_concurrent_positions():
    rm = make_rm()
    ok, why = rm.can_open(equity=100_000, open_positions=Config.MAX_CONCURRENT_POSITIONS)
    assert not ok
    assert "concurrent" in why


def test_max_trades_per_day():
    rm = make_rm()
    for _ in range(Config.MAX_TRADES_PER_DAY):
        rm.record_open()
    ok, why = rm.can_open(equity=100_000, open_positions=0)
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


def test_new_day_resets_state():
    rm = make_rm(100_000)
    rm.state.target_locked = True
    rm.start_day(120_000)
    ok, _ = rm.can_open(equity=120_000, open_positions=0)
    assert ok
