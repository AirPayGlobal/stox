"""
STOX Options — central configuration.

Every operational knob lives here and can be overridden via environment
variables / a .env file. Dollar amounts are USD.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _list(name: str, default: str) -> list:
    return [s.strip().upper() for s in os.getenv(name, default).split(",") if s.strip()]


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Config:
    # ------------------------------------------------------------ Broker
    ALPACA_API_KEY: str = os.getenv("ALPACA_API_KEY", "")
    ALPACA_API_SECRET: str = os.getenv("ALPACA_API_SECRET", "")
    ALPACA_MODE: str = os.getenv("ALPACA_MODE", "paper")  # "paper" | "live"

    # ------------------------------------------------------------ Universe
    # Highly liquid underlyings with daily/near-dated option expiries.
    UNDERLYINGS: list = _list("UNDERLYINGS", "SPY,QQQ")

    # ------------------------------------------------------------ Daily governor
    # The engine stops opening trades once day P&L >= target, and halts
    # completely (flattening everything) once day loss <= -max loss.
    DAILY_PROFIT_TARGET: float = _f("DAILY_PROFIT_TARGET", 5000.0)
    DAILY_MAX_LOSS: float = _f("DAILY_MAX_LOSS", 2500.0)
    MAX_TRADES_PER_DAY: int = _i("MAX_TRADES_PER_DAY", 12)
    MAX_CONCURRENT_POSITIONS: int = _i("MAX_CONCURRENT_POSITIONS", 3)

    # ------------------------------------------------------------ Position sizing
    # Risk per trade is the amount lost if the stop-loss fires (premium *
    # STOP_LOSS_PCT), capped at RISK_PER_TRADE_PCT of account equity.
    RISK_PER_TRADE_PCT: float = _f("RISK_PER_TRADE_PCT", 0.01)
    MAX_POSITION_PCT: float = _f("MAX_POSITION_PCT", 0.10)   # max premium outlay / equity
    MAX_CONTRACTS: int = _i("MAX_CONTRACTS", 50)

    # ------------------------------------------------------------ Exits
    TAKE_PROFIT_PCT: float = _f("TAKE_PROFIT_PCT", 0.50)     # +50% on premium
    STOP_LOSS_PCT: float = _f("STOP_LOSS_PCT", 0.30)         # -30% on premium
    MAX_HOLD_MINUTES: int = _i("MAX_HOLD_MINUTES", 90)       # time stop

    # ------------------------------------------------------------ Contract selection
    MAX_DTE: int = _i("MAX_DTE", 1)                          # 0 = same-day expiry only
    TARGET_DELTA: float = _f("TARGET_DELTA", 0.45)           # |delta| to aim for
    MIN_OPEN_INTEREST: int = _i("MIN_OPEN_INTEREST", 100)
    MAX_SPREAD_PCT: float = _f("MAX_SPREAD_PCT", 0.10)       # (ask-bid)/mid
    MIN_BID: float = _f("MIN_BID", 0.10)

    # ------------------------------------------------------------ Signals
    # STRATEGY: "orb" (opening-range momentum), "sweep" (liquidity-sweep
    # reversal), or "both".
    STRATEGY: str = os.getenv("STRATEGY", "both").lower()
    BAR_MINUTES: int = _i("BAR_MINUTES", 5)
    OPENING_RANGE_MINUTES: int = _i("OPENING_RANGE_MINUTES", 15)
    SIGNAL_THRESHOLD: int = _i("SIGNAL_THRESHOLD", 70)       # score 0-100

    # ------------------------------------------------------------ Sweep strategy
    SWEEP_TIMEFRAME_MINUTES: int = _i("SWEEP_TIMEFRAME_MINUTES", 60)
    SWEEP_RR: float = _f("SWEEP_RR", 2.0)                    # reward:risk target
    SWEEP_TREND_FILTER: bool = _b("SWEEP_TREND_FILTER", False)
    SWEEP_PREV_DAY_LEVELS: bool = _b("SWEEP_PREV_DAY_LEVELS", True)
    SWEEP_ENTRY: str = os.getenv("SWEEP_ENTRY", "close").lower()  # "close" | "retrace"
    SWEEP_RETRACE_EXPIRY_MIN: int = _i("SWEEP_RETRACE_EXPIRY_MIN", 60)
    SWEEP_DISASTER_STOP_PCT: float = _f("SWEEP_DISASTER_STOP_PCT", 0.60)

    # ------------------------------------------------------------ Session (ET)
    ENTRY_START: str = os.getenv("ENTRY_START", "09:45")     # no entries before
    ENTRY_CUTOFF: str = os.getenv("ENTRY_CUTOFF", "15:00")   # no entries after
    FLATTEN_TIME: str = os.getenv("FLATTEN_TIME", "15:50")   # close everything

    # ------------------------------------------------------------ Engine
    LOOP_SECONDS: int = _i("LOOP_SECONDS", 30)               # position-management tick
    SCAN_SECONDS: int = _i("SCAN_SECONDS", 300)              # entry-scan cadence

    # ------------------------------------------------------------ Dashboard
    DASHBOARD_USER: str = os.getenv("DASHBOARD_USER", "admin")
    DASHBOARD_PASS: str = os.getenv("DASHBOARD_PASS", "changeme")

    # ------------------------------------------------------------ Misc
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    STATE_DIR: str = os.getenv("STATE_DIR", "logs")
