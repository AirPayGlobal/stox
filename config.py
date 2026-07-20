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
    # Production is isolated to the demonstrated edge (SPY ORB). QQQ and the
    # sweep strategy remain available via env override and in the backtester,
    # but must prove positive expectancy independently before rejoining.
    UNDERLYINGS: list = _list("UNDERLYINGS", "SPY")

    # ------------------------------------------------------------ Daily governor
    # Hitting the profit target does NOT stop trading — it arms profit
    # protection: a trailing floor under day P&L that ratchets up with the
    # day's peak. Only if P&L falls back to the floor does the engine bank
    # the day (flatten + stop). The loss side is a hard halt.
    DAILY_PROFIT_TARGET: float = _f("DAILY_PROFIT_TARGET", 5000.0)
    DAILY_MAX_LOSS: float = _f("DAILY_MAX_LOSS", 1500.0)
    # Floor = max(target * PROFIT_FLOOR_PCT, peak * (1 - PROFIT_GIVEBACK_PCT))
    PROFIT_FLOOR_PCT: float = _f("PROFIT_FLOOR_PCT", 0.70)      # keep >= 70% of target
    PROFIT_GIVEBACK_PCT: float = _f("PROFIT_GIVEBACK_PCT", 0.30)  # give back <= 30% of peak
    # What hitting the floor does:
    #   "hold"    — stop NEW entries; open positions run to their own
    #               stops/targets (extra risk bounded by per-trade stops)
    #   "flatten" — close everything immediately and bank the day
    PROTECT_MODE: str = os.getenv("PROTECT_MODE", "hold").lower()
    MAX_TRADES_PER_DAY: int = _i("MAX_TRADES_PER_DAY", 12)
    MAX_CONCURRENT_POSITIONS: int = _i("MAX_CONCURRENT_POSITIONS", 3)

    # ---- Rolling-drawdown circuit breaker (multi-day equity-curve stop) ----
    # How much realized P&L has been given back from its peak over a trailing
    # window. Beyond REDUCE, new positions are halved; beyond HALT, trading
    # stops until the give-back recovers or the day is reset. This is what
    # stops a losing regime from erasing accumulated gains.
    DRAWDOWN_WINDOW_DAYS: int = _i("DRAWDOWN_WINDOW_DAYS", 20)
    DRAWDOWN_BASE: float = _f("DRAWDOWN_BASE", 100000.0)          # % thresholds are of this
    DRAWDOWN_REDUCE_PCT: float = _f("DRAWDOWN_REDUCE_PCT", 0.04)  # halve size beyond this
    DRAWDOWN_HALT_PCT: float = _f("DRAWDOWN_HALT_PCT", 0.06)      # stop opening beyond this

    # ------------------------------------------------------------ Position sizing
    # Risk per trade is the amount lost if the stop-loss fires (premium *
    # STOP_LOSS_PCT), capped at RISK_PER_TRADE_PCT of account equity.
    # NOTE: env names are prefixed PREMIUM_/MAX_PREMIUM_ deliberately — the
    # old stock bot used STOP_LOSS_PCT/TAKE_PROFIT_PCT/MAX_POSITION_PCT with
    # stock-scale values (2%/6%), and stale copies of those variables in
    # hosting dashboards silently strangled option exits.
    RISK_PER_TRADE_PCT: float = _f("RISK_PER_TRADE_PCT", 0.005)
    MAX_POSITION_PCT: float = _f("MAX_PREMIUM_PCT", 0.10)    # max premium outlay / equity
    MAX_CONTRACTS: int = _i("MAX_CONTRACTS", 50)

    # ------------------------------------------------------------ Exits
    TAKE_PROFIT_PCT: float = _f("PREMIUM_TARGET_PCT", 0.50)  # +50% on premium
    STOP_LOSS_PCT: float = _f("PREMIUM_STOP_PCT", 0.30)      # -30% on premium
    MAX_HOLD_MINUTES: int = _i("MAX_HOLD_MINUTES", 90)       # time stop

    # ---- Exit management (Phase 4): let winners run, cut losers to scratch ----
    # All default OFF (0). Backtest before enabling — these reshape the P&L
    # distribution and must be validated, not assumed. Percentages are of the
    # entry premium; the peak used is the trade's max favorable mark (MFE).
    #   breakeven: once MFE reaches +BE_TRIGGER, raise the stop to entry
    #   trailing:  once MFE reaches +TRAIL_TRIGGER, trail the stop TRAIL_PCT
    #              below the peak; while trailing is enabled the fixed target
    #              is removed so winners can run (the trail becomes the exit)
    ORB_BREAKEVEN_TRIGGER_PCT: float = _f("ORB_BREAKEVEN_TRIGGER_PCT", 0.0)
    ORB_TRAIL_TRIGGER_PCT: float = _f("ORB_TRAIL_TRIGGER_PCT", 0.0)
    ORB_TRAIL_PCT: float = _f("ORB_TRAIL_PCT", 0.20)

    # ------------------------------------------------------------ Loss discipline
    # After a losing close on an underlying, no re-entry for this long...
    LOSS_COOLDOWN_MINUTES: int = _i("LOSS_COOLDOWN_MINUTES", 30)
    # ...and after a WINNING close, a shorter pause — instant re-entry
    # after a take-profit chases an extended move at a worse price.
    WIN_COOLDOWN_MINUTES: int = _i("WIN_COOLDOWN_MINUTES", 10)
    # ...and after this many consecutive losers on one underlying, it is
    # done for the day.
    MAX_CONSECUTIVE_LOSSES: int = _i("MAX_CONSECUTIVE_LOSSES", 3)

    # ------------------------------------------------------------ Contract selection
    MAX_DTE: int = _i("MAX_DTE", 1)                          # 0 = same-day expiry only
    TARGET_DELTA: float = _f("TARGET_DELTA", 0.45)           # |delta| to aim for
    MIN_OPEN_INTEREST: int = _i("MIN_OPEN_INTEREST", 100)
    MAX_SPREAD_PCT: float = _f("MAX_SPREAD_PCT", 0.10)       # (ask-bid)/mid
    MIN_BID: float = _f("MIN_BID", 0.10)

    # ------------------------------------------------------------ Signals
    # STRATEGY: "orb" (opening-range momentum), "sweep" (liquidity-sweep
    # reversal), or "both". Default is orb-only: live results (PF 2.22 vs
    # 0.99) put sweep on the bench until it re-qualifies.
    STRATEGY: str = os.getenv("STRATEGY", "orb").lower()
    # Diagnostic: flip every LONG<->SHORT signal. For TESTING whether the
    # inverse has an edge (backtest it) — not a money button. Costs and exit
    # asymmetry mean a losing strategy rarely inverts into a winning one.
    INVERT_SIGNALS: bool = _b("INVERT_SIGNALS", False)
    BAR_MINUTES: int = _i("BAR_MINUTES", 5)
    OPENING_RANGE_MINUTES: int = _i("OPENING_RANGE_MINUTES", 15)
    SIGNAL_THRESHOLD: int = _i("SIGNAL_THRESHOLD", 70)       # score 0-100

    # ------------------------------------------------------------ ORB entry filters
    # ALL DEFAULT OFF: these are unvalidated against the live track record,
    # which was produced by the unfiltered ORB logic. Test each in isolation
    # in the backtester (Phase 2) and enable via env only if it improves
    # expectancy. Missing data (no RVOL/ATR history) skips the filter rather
    # than blocking trading. NOTE: RVOL_MIN=1.3 suits in-play single stocks;
    # an index ETF hovers near 1.0x, so calibrate before enabling on SPY.
    ORB_FILTER_VWAP: bool = _b("ORB_FILTER_VWAP", False)     # price & slope aligned
    ORB_FILTER_RVOL: bool = _b("ORB_FILTER_RVOL", False)
    RVOL_MIN: float = _f("RVOL_MIN", 1.3)
    RVOL_LOOKBACK_DAYS: int = _i("RVOL_LOOKBACK_DAYS", 10)
    ORB_FILTER_OR_ATR: bool = _b("ORB_FILTER_OR_ATR", False)  # OR size vs daily ATR
    OR_ATR_MIN: float = _f("OR_ATR_MIN", 0.30)
    OR_ATR_MAX: float = _f("OR_ATR_MAX", 1.00)
    # Breakout-candle volume confirmation: the bar that breaks the opening
    # range must trade on volume >= BREAK_VOLUME_MULT x the prior N bars'
    # average — "institutional footprints", weak-volume breaks fail more.
    # Default OFF: test it against the live baseline before enabling.
    ORB_FILTER_BREAK_VOLUME: bool = _b("ORB_FILTER_BREAK_VOLUME", False)
    BREAK_VOLUME_MULT: float = _f("BREAK_VOLUME_MULT", 1.2)
    BREAK_VOLUME_LOOKBACK: int = _i("BREAK_VOLUME_LOOKBACK", 10)

    # ------------------------------------------------------------ Sweep strategy
    SWEEP_TIMEFRAME_MINUTES: int = _i("SWEEP_TIMEFRAME_MINUTES", 60)
    SWEEP_RR: float = _f("SWEEP_RR", 2.0)                    # reward:risk target
    SWEEP_TREND_FILTER: bool = _b("SWEEP_TREND_FILTER", False)
    SWEEP_PREV_DAY_LEVELS: bool = _b("SWEEP_PREV_DAY_LEVELS", True)
    SWEEP_OVERNIGHT_RANGE: bool = _b("SWEEP_OVERNIGHT_RANGE", True)
    # Optional ET time window that redefines the overnight range, e.g.
    # "04:00-09:30" = pre-market only (the London-overlap session). A window
    # spanning midnight ("18:00-02:00") starts on the prior calendar day.
    # Empty = full overnight (prior 16:00 close -> today's 09:30 open).
    SWEEP_SESSION_WINDOW: str = os.getenv("SWEEP_SESSION_WINDOW", "").strip()

    # ------------------------------------------------------------ Swing (backtest-only)
    # 4H-native sweep-reclaim held across days — exists in the backtester to
    # evaluate the hybrid idea BEFORE any live implementation.
    SWING_TIMEFRAME_MINUTES: int = _i("SWING_TIMEFRAME_MINUTES", 240)
    SWING_BAR_MINUTES: int = _i("SWING_BAR_MINUTES", 30)
    SWING_RR: float = _f("SWING_RR", 2.0)
    SWING_MAX_HOLD_DAYS: int = _i("SWING_MAX_HOLD_DAYS", 7)
    SWING_DTE: int = _i("SWING_DTE", 14)                     # contract expiry at entry
    SWEEP_ENTRY: str = os.getenv("SWEEP_ENTRY", "close").lower()  # "close" | "retrace"
    SWEEP_RETRACE_EXPIRY_MIN: int = _i("SWEEP_RETRACE_EXPIRY_MIN", 60)
    SWEEP_DISASTER_STOP_PCT: float = _f("SWEEP_DISASTER_STOP_PCT", 0.60)
    # Skip sweep setups whose wick stop is closer than this fraction of spot:
    # near-zero stop distance lets "1% risk" sizing balloon to the outlay cap,
    # and slippage makes the theoretical risk fictional (backtest artifact #2).
    SWEEP_MIN_STOP_PCT: float = _f("SWEEP_MIN_STOP_PCT", 0.0015)
    # ...and skip setups whose stop is FURTHER than this fraction of spot:
    # a wide HTF wick means a swing-sized stop and a 2R target that is
    # unreachable intraday (live data: -$2k trades chasing +3% targets).
    SWEEP_MAX_STOP_PCT: float = _f("SWEEP_MAX_STOP_PCT", 0.010)

    # ------------------------------------------------------------ Session (ET)
    ENTRY_START: str = os.getenv("ENTRY_START", "09:45")     # no entries before
    ENTRY_CUTOFF: str = os.getenv("ENTRY_CUTOFF", "15:00")   # no entries after
    FLATTEN_TIME: str = os.getenv("FLATTEN_TIME", "15:50")   # close everything

    # ------------------------------------------------------------ Engine
    LOOP_SECONDS: int = _i("LOOP_SECONDS", 30)               # position-management tick
    SCAN_SECONDS: int = _i("SCAN_SECONDS", 300)              # entry-scan cadence
    # Re-sync the book with the broker's actual positions this often while
    # the market is open (adopts orphans opened by e.g. a dying container
    # during a deploy cutover; boot-only reconciliation missed those).
    RECONCILE_SECONDS: int = _i("RECONCILE_SECONDS", 180)
    # Start the engine automatically when the server boots (dashboard Stop
    # still works; set false to require pressing Start).
    ENGINE_AUTOSTART: bool = _b("ENGINE_AUTOSTART", True)
    ENGINE_AUTOSTART_DRY: bool = _b("ENGINE_AUTOSTART_DRY", False)

    # ------------------------------------------------------------ Dashboard
    DASHBOARD_USER: str = os.getenv("DASHBOARD_USER", "admin")
    DASHBOARD_PASS: str = os.getenv("DASHBOARD_PASS", "changeme")

    # ------------------------------------------------------------ Misc
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    # State (trade book, day baseline, logs) must survive redeploys or the
    # engine loses its memory mid-session. If a persistent volume is mounted
    # at /data (Railway convention), use it automatically; STATE_DIR
    # overrides.
    STATE_DIR: str = os.getenv(
        "STATE_DIR",
        "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else "logs",
    )
