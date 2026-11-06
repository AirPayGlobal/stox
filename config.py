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
    UNDERLYINGS: list = _list("UNDERLYINGS", "SPY,QQQ")

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
    # STRATEGY: "fib" (Fibonacci-retracement pullback, current), "orb", or
    # "sweep" (both legacy — net-negative over 20+ live trades, kept for
    # backtest comparison only). Fib is the new approach: a with-trend
    # pullback entry into the 0.5-0.618 gold zone, addressing the
    # entered-at-reversals flaw that sank orb and sweep. Validate in the
    # backtester before trusting live.
    STRATEGY: str = os.getenv("STRATEGY", "fib").lower()
    # Diagnostic: flip every LONG<->SHORT signal. For TESTING whether the
    # inverse has an edge (backtest it) — not a money button. Costs and exit
    # asymmetry mean a losing strategy rarely inverts into a winning one.
    INVERT_SIGNALS: bool = _b("INVERT_SIGNALS", False)
    BAR_MINUTES: int = _i("BAR_MINUTES", 5)
    OPENING_RANGE_MINUTES: int = _i("OPENING_RANGE_MINUTES", 15)
    SIGNAL_THRESHOLD: int = _i("SIGNAL_THRESHOLD", 70)       # score 0-100

    # ------------------------------------------------------------ Fib pullback
    FIB_BAR_MINUTES: int = _i("FIB_BAR_MINUTES", 1)         # 1-minute structure
    FIB_PIVOT_K: int = _i("FIB_PIVOT_K", 2)                 # fractal half-width (lag)
    FIB_ENTRY_LOW: float = _f("FIB_ENTRY_LOW", 0.50)        # gold zone shallow edge
    FIB_ENTRY_HIGH: float = _f("FIB_ENTRY_HIGH", 0.618)     # gold zone deep edge
    FIB_MIN_RANGE_PCT: float = _f("FIB_MIN_RANGE_PCT", 0.0007)  # min leg size / spot
    FIB_LOOKBACK_BARS: int = _i("FIB_LOOKBACK_BARS", 45)   # window for the active swing
    # Fib's own stop-distance band (was borrowing sweep's 60-min bounds, which
    # filtered out most 1-min legs). Distance ~= 0.5 x leg range.
    FIB_MIN_STOP_PCT: float = _f("FIB_MIN_STOP_PCT", 0.0005)  # ~ $0.28 on SPY
    FIB_MAX_STOP_PCT: float = _f("FIB_MAX_STOP_PCT", 0.012)   # ~ $6.6 on SPY
    FIB_MAX_HOLD_MINUTES: int = _i("FIB_MAX_HOLD_MINUTES", 45)  # time stop (0 = off)

    # ------------------------------------------------------------ Fib validation harness
    # PAPER/BACKTEST ONLY — none of these change how live orders are placed.
    # They exist to test whether the fib backtest edge survives realistic
    # execution friction and full production constraints.
    STRATEGY_VERSION: str = os.getenv("STRATEGY_VERSION", "fib-2.0")
    # Entry-scan cadence in BARS, for backtest/engine parity. The live engine
    # scans every SCAN_SECONDS (300s = 5 x 1-min bars); the backtester must use
    # the same cadence or it overcounts touches that live never sees.
    FIB_SCAN_BARS: int = _i("FIB_SCAN_BARS", 5)
    # Execution scenario for the simulator: baseline | optimistic | base |
    # conservative | mid. "baseline" reproduces the old fixed $0.02 half-spread
    # (kept for comparison); "mid" fills at mid-price and is DIAGNOSTIC ONLY.
    EXEC_SCENARIO: str = os.getenv("EXEC_SCENARIO", "base").lower()
    # Optional selectivity filters — ALL DEFAULT OFF. Enable only after a filter
    # demonstrably improves out-of-sample expectancy (see docs/FIB_VALIDATION_REPORT.md).
    FIB_FILTER_TREND: bool = _b("FIB_FILTER_TREND", False)      # entry aligned with EMA trend
    FIB_TREND_EMA: int = _i("FIB_TREND_EMA", 20)
    FIB_FILTER_VOL_REGIME: bool = _b("FIB_FILTER_VOL_REGIME", False)  # realized-vol band
    FIB_VOL_MIN: float = _f("FIB_VOL_MIN", 0.0)                 # annualized, e.g. 0.08
    FIB_VOL_MAX: float = _f("FIB_VOL_MAX", 5.0)
    FIB_FILTER_TOD: bool = _b("FIB_FILTER_TOD", False)         # time-of-day eligibility
    FIB_TOD_BLOCK: str = os.getenv("FIB_TOD_BLOCK", "")        # ET window to BLOCK, e.g. 11:30-13:30
    FIB_FILTER_LIQUIDITY: bool = _b("FIB_FILTER_LIQUIDITY", False)   # tighter entry-spread gate
    FIB_MAX_ENTRY_SPREAD_PCT: float = _f("FIB_MAX_ENTRY_SPREAD_PCT", 0.08)
    FIB_FILTER_CONFIRM: bool = _b("FIB_FILTER_CONFIRM", False)  # require a post-touch confirmation bar

    # ---- Operational safeguards (paper + live entry gating) ----
    MAX_QUOTE_AGE_SECONDS: int = _i("MAX_QUOTE_AGE_SECONDS", 120)  # block entries on staler data
    BLOCK_ON_UNMANAGED_SHARES: bool = _b("BLOCK_ON_UNMANAGED_SHARES", True)
    BLOCK_ON_RECON_MISMATCH: bool = _b("BLOCK_ON_RECON_MISMATCH", True)

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
    # Premium trailing stop for sweep — banks a gamma spike before it
    # round-trips (live MFE showed losers peaking +20% to +200% first).
    # Default OFF; backtest before enabling.
    SWEEP_TRAIL_TRIGGER_PCT: float = _f("SWEEP_TRAIL_TRIGGER_PCT", 0.0)
    SWEEP_TRAIL_PCT: float = _f("SWEEP_TRAIL_PCT", 0.25)
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
