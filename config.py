"""
Central configuration — reads from environment variables / .env file.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Alpaca credentials
    ALPACA_API_KEY: str = os.getenv("ALPACA_API_KEY", "")
    ALPACA_API_SECRET: str = os.getenv("ALPACA_API_SECRET", "")
    ALPACA_MODE: str = os.getenv("ALPACA_MODE", "paper")  # "paper" | "live"

    # Alpaca base URLs
    @classmethod
    def alpaca_base_url(cls) -> str:
        if cls.ALPACA_MODE == "live":
            return "https://api.alpaca.markets"
        return "https://paper-api.alpaca.markets"

    # Portfolio / Risk settings
    INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "10000"))
    # BASE_CAPITAL: the amount the bot is authorised to trade with.
    # Profits above this level accumulate as excess cash and are NOT reinvested.
    # Set this to your actual deposit amount. Withdraw profits manually from Alpaca
    # whenever equity - BASE_CAPITAL exceeds your desired withdrawal threshold.
    BASE_CAPITAL: float = float(os.getenv("BASE_CAPITAL", "100000"))
    # PROFIT_WITHDRAWAL_ALERT_PCT: log a withdrawal alert when withdrawable
    # profit exceeds this fraction of BASE_CAPITAL (default 10% = $10K on $100K).
    PROFIT_WITHDRAWAL_ALERT_PCT: float = float(os.getenv("PROFIT_WITHDRAWAL_ALERT_PCT", "0.10"))
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.05"))   # max single position = 5% of equity
    MIN_POSITION_PCT: float = float(os.getenv("MIN_POSITION_PCT", "0.01"))   # don't enter if buying power < 1% of equity
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "20"))     # max concurrent open positions
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "0.03"))
    TAKE_PROFIT_PCT: float = float(os.getenv("TAKE_PROFIT_PCT", "0.30"))   # emergency ceiling only; trailing stop is primary exit
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))

    # Technical indicator parameters
    EMA_FAST: int = 9
    EMA_SLOW: int = 21
    EMA_TREND: int = 50
    RSI_PERIOD: int = 14
    RSI_OVERSOLD: float = 30.0
    RSI_OVERBOUGHT: float = 75.0
    MACD_FAST: int = 12
    MACD_SLOW: int = 26
    MACD_SIGNAL: int = 9
    BB_PERIOD: int = 20
    BB_STD: float = 2.0

    # Universe of stocks to scan (S&P 500 large-caps)
    WATCHLIST: list = [
        # Tech (XLK)
        "AAPL", "MSFT", "NVDA", "AVGO", "CSCO", "TXN", "ADBE", "QCOM", "CRM", "ACN",
        # Communication (XLC)
        "GOOGL", "META", "AMZN",
        # Financials (XLF)
        "JPM", "BAC", "V", "MA", "BLK", "BRK.B",
        # Healthcare (XLV)
        "UNH", "JNJ", "LLY", "ABBV", "MRK", "ABT", "TMO", "DHR", "AMGN", "BMY",
        # Consumer Staples (XLP)
        "PG", "KO", "PEP", "COST", "WMT", "PM",
        # Consumer Discretionary (XLY)
        "HD", "MCD", "NKE", "SBUX", "TSLA",
        # Industrials (XLI)
        "RTX", "UPS", "HON", "DE", "CAT", "GE", "ETN", "EMR",
        # Energy (XLE)
        "XOM", "CVX", "SLB", "COP", "EOG", "OXY",
        # Utilities (XLU)
        "NEE", "SO", "DUK", "AEP", "D",
        # Materials (XLB)
        "LIN", "APD", "SHW", "NEM", "FCX",
        # Real Estate (XLRE)
        "AMT", "PLD", "EQIX", "PSA", "O", "CCI",
    ]

    # Multi-timeframe confirmation
    WEEKLY_CONFIRM_REQUIRED: bool = os.getenv("WEEKLY_CONFIRM_REQUIRED", "true").lower() != "false"

    # Short selling
    SHORT_SELLING_ENABLED: bool   = os.getenv("SHORT_SELLING_ENABLED", "false").lower() == "true"
    SHORT_MAX_POSITIONS: int      = int(os.getenv("SHORT_MAX_POSITIONS", "3"))
    SHORT_SECTOR_BOTTOM_N: int    = int(os.getenv("SHORT_SECTOR_BOTTOM_N", "3"))    # short only bottom N sectors
    SHORT_MIN_SENTIMENT: float    = float(os.getenv("SHORT_MIN_SENTIMENT", "-2.0")) # composite score must be this negative

    # SEC 13F smart money tracker
    THIRTEEN_F_ENABLED: bool      = os.getenv("THIRTEEN_F_ENABLED", "true").lower() != "false"
    THIRTEEN_F_BOOST_SCALE: float = float(os.getenv("THIRTEEN_F_BOOST_SCALE", "2.0"))  # pts per 13F unit

    # Sector rotation
    SECTOR_TOP_N: int = int(os.getenv("SECTOR_TOP_N", "8"))           # only buy in top N sectors by 3-month momentum
    MAX_POSITIONS_PER_SECTOR: int = int(os.getenv("MAX_POSITIONS_PER_SECTOR", "3"))  # cap per GICS sector to limit concentration

    # Kelly Criterion position sizing
    KELLY_MIN_TRADES: int = int(os.getenv("KELLY_MIN_TRADES", "20"))          # min closed trades before Kelly activates
    KELLY_MIN_FRACTION: float = float(os.getenv("KELLY_MIN_FRACTION", "0.01")) # floor: never size below 1% of equity

    # Volatility-targeted position sizing
    VOL_TARGET_ANNUAL: float = float(os.getenv("VOL_TARGET_ANNUAL", "0.15"))          # 15% annualised portfolio vol target
    VOL_TARGET_PER_POSITION: float = float(os.getenv("VOL_TARGET_PER_POSITION", "0.01"))  # 1% vol budget per position

    # Trailing stop — tiered: tighter when gains are small, looser as the trend develops
    TRAILING_STOP_PCT: float      = float(os.getenv("TRAILING_STOP_PCT",      "0.04"))  # gain < MID_TRIGGER:  4% trail
    TRAILING_STOP_MID_PCT: float  = float(os.getenv("TRAILING_STOP_MID_PCT",  "0.05"))  # gain < HIGH_TRIGGER: 5% trail
    TRAILING_STOP_HIGH_PCT: float = float(os.getenv("TRAILING_STOP_HIGH_PCT", "0.07"))  # gain >= HIGH_TRIGGER: 7% trail
    TRAILING_MID_TRIGGER: float   = float(os.getenv("TRAILING_MID_TRIGGER",   "0.08"))  # loosen trail at +8% gain
    TRAILING_HIGH_TRIGGER: float  = float(os.getenv("TRAILING_HIGH_TRIGGER",  "0.18"))  # loosen trail again at +18% gain
    BREAK_EVEN_TRIGGER_PCT: float = float(os.getenv("BREAK_EVEN_TRIGGER_PCT", "0.05"))  # once up 5%, never close below entry

    # Earnings blackout
    EARNINGS_BLACKOUT_DAYS: int = int(os.getenv("EARNINGS_BLACKOUT_DAYS", "2"))  # skip entry within N days of earnings

    # Correlation limit
    MAX_POSITION_CORRELATION: float = float(os.getenv("MAX_POSITION_CORRELATION", "0.85"))  # skip if r > this with any open position

    # Market filters
    VIX_THRESHOLD: float = float(os.getenv("VIX_THRESHOLD", "30"))   # skip buys when VIX > this (regime handles 25-30 via sizing)
    MIN_SENTIMENT_SCORE: float = float(os.getenv("MIN_SENTIMENT_SCORE", "-0.2"))  # legacy news-only filter
    MIN_COMPOSITE_SENTIMENT: float = float(os.getenv("MIN_COMPOSITE_SENTIMENT", "-3.0"))  # composite 4-source filter

    # IPO tracking
    IPO_MIN_DAYS: int = int(os.getenv("IPO_MIN_DAYS", "5"))              # quarantine days before trading
    IPO_POSITION_SCALE: float = float(os.getenv("IPO_POSITION_SCALE", "0.5"))   # 50% of normal size
    IPO_STOP_LOSS_PCT: float = float(os.getenv("IPO_STOP_LOSS_PCT", "0.04"))    # 4% stop (wider than normal 2%)

    # Pairs trading / statistical arbitrage
    PAIRS_MAX_POSITIONS: int   = int(os.getenv("PAIRS_MAX_POSITIONS", "2"))       # max simultaneous pair trades
    PAIRS_POSITION_PCT: float  = float(os.getenv("PAIRS_POSITION_PCT", "0.03"))   # 3% of equity per leg (6% total)
    PAIRS_WINDOW: int          = int(os.getenv("PAIRS_WINDOW", "60"))             # rolling window for z-score (days)
    PAIRS_ENTRY_ZSCORE: float  = float(os.getenv("PAIRS_ENTRY_ZSCORE", "2.0"))   # enter when |z| > this
    PAIRS_EXIT_ZSCORE: float   = float(os.getenv("PAIRS_EXIT_ZSCORE", "0.5"))    # exit when |z| < this
    PAIRS_STOP_ZSCORE: float   = float(os.getenv("PAIRS_STOP_ZSCORE", "3.5"))    # stop loss when |z| > this

    # --- Tier 3: Volatility Regime ---
    REGIME_FILTER_ENABLED: bool = os.getenv("REGIME_FILTER_ENABLED", "true").lower() != "false"

    # --- Tier 3: ML Signal Booster ---
    ML_SIGNAL_ENABLED: bool  = os.getenv("ML_SIGNAL_ENABLED", "true").lower() != "false"
    ML_MIN_PROBABILITY: float = float(os.getenv("ML_MIN_PROBABILITY", "0.52"))  # block entries below this confidence

    # --- Tier 3: Dynamic Universe ---
    DYNAMIC_UNIVERSE_ENABLED: bool = os.getenv("DYNAMIC_UNIVERSE_ENABLED", "true").lower() != "false"
    DYNAMIC_UNIVERSE_TOP_N: int    = int(os.getenv("DYNAMIC_UNIVERSE_TOP_N", "10"))  # max extra symbols per scan

    # ------------------------------------------------------------------ StoxDaily (intraday)
    DAILY_ALPACA_API_KEY: str = os.getenv("DAILY_ALPACA_API_KEY", "")
    DAILY_ALPACA_API_SECRET: str = os.getenv("DAILY_ALPACA_API_SECRET", "")
    DAILY_ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"

    INTRADAY_ENABLED: bool = os.getenv("INTRADAY_ENABLED", "false").lower() == "true"
    INTRADAY_SCAN_INTERVAL: int = int(os.getenv("INTRADAY_SCAN_INTERVAL", "60"))
    INTRADAY_ORB_MINUTES: int = int(os.getenv("INTRADAY_ORB_MINUTES", "15"))
    INTRADAY_MAX_POSITIONS: int = int(os.getenv("INTRADAY_MAX_POSITIONS", "5"))
    INTRADAY_POSITION_PCT: float = float(os.getenv("INTRADAY_POSITION_PCT", "0.15"))
    INTRADAY_STOP_PCT: float = float(os.getenv("INTRADAY_STOP_PCT", "0.005"))
    INTRADAY_TARGET_MULT: float = float(os.getenv("INTRADAY_TARGET_MULT", "2.0"))
    INTRADAY_CLOSE_BY_HOUR: int = int(os.getenv("INTRADAY_CLOSE_BY_HOUR", "15"))
    INTRADAY_CLOSE_BY_MINUTE: int = int(os.getenv("INTRADAY_CLOSE_BY_MINUTE", "55"))  # APEX: 3:55 PM hard close
    INTRADAY_CAPITAL: float = float(os.getenv("INTRADAY_CAPITAL", "25000"))
    INTRADAY_MAX_DAILY_LOSS_PCT: float = float(os.getenv("INTRADAY_MAX_DAILY_LOSS_PCT", "0.015"))  # APEX: 1.5% NAV

    # APEX v4.2 — Composite Alpha Score engine parameters
    APEX_HARD_STOP_PCT: float = float(os.getenv("APEX_HARD_STOP_PCT", "0.02"))           # 2% hard stop from entry
    APEX_TARGET1_PCT: float = float(os.getenv("APEX_TARGET1_PCT", "0.03"))                # +3% take-profit target 1
    APEX_TARGET2_PCT: float = float(os.getenv("APEX_TARGET2_PCT", "0.05"))                # +5% take-profit target 2
    APEX_MIN_CAS: float = float(os.getenv("APEX_MIN_CAS", "58.0"))                        # minimum CAS to enter (calibrated for available data — no options flow)
    APEX_STRONG_BUY_CAS: float = float(os.getenv("APEX_STRONG_BUY_CAS", "75.0"))         # strong buy threshold
    APEX_STRONG_BUY_SIZE_PCT: float = float(os.getenv("APEX_STRONG_BUY_SIZE_PCT", "0.04"))  # 4% NAV for strong buy
    APEX_BUY_SIZE_PCT: float = float(os.getenv("APEX_BUY_SIZE_PCT", "0.025"))             # 2.5% NAV for standard buy
    APEX_MAX_GROSS_EXPOSURE: float = float(os.getenv("APEX_MAX_GROSS_EXPOSURE", "0.15"))  # 15% NAV max total exposure
    APEX_CONSECUTIVE_STOP_HALT: int = int(os.getenv("APEX_CONSECUTIVE_STOP_HALT", "3"))   # halt after N consecutive stops
    APEX_VIX_SUSPEND: float = float(os.getenv("APEX_VIX_SUSPEND", "35.0"))               # suspend system above this VIX
    APEX_VIX_REDUCE: float = float(os.getenv("APEX_VIX_REDUCE", "28.0"))                 # reduce sizes 40% above this VIX
    APEX_VIX_TIGHTEN_STOP: float = float(os.getenv("APEX_VIX_TIGHTEN_STOP", "0.015"))   # tighter stop when VIX > REDUCE
    APEX_MIN_GAP_PCT: float = float(os.getenv("APEX_MIN_GAP_PCT", "0.025"))              # minimum pre-market gap for catalyst
    APEX_MIN_ATR_PCT: float = float(os.getenv("APEX_MIN_ATR_PCT", "0.02"))               # minimum ATR% (daily range filter)
    APEX_TIME_STOP_HOUR: int = int(os.getenv("APEX_TIME_STOP_HOUR", "12"))               # exit non-moving positions after
    APEX_TIME_STOP_MINUTE: int = int(os.getenv("APEX_TIME_STOP_MINUTE", "30"))           # this time (12:30 PM ET)
    APEX_TIME_STOP_MIN_GAIN: float = float(os.getenv("APEX_TIME_STOP_MIN_GAIN", "0.005"))  # must be up 0.5% to hold past noon
    APEX_ENTRY_SKIP_OPEN_MIN: int = int(os.getenv("APEX_ENTRY_SKIP_OPEN_MIN", "5"))      # skip first N minutes after open
    APEX_MAX_SPREAD_PCT: float = float(os.getenv("APEX_MAX_SPREAD_PCT", "0.0015"))        # max bid/ask spread (0.15%) — reject illiquid entries
    APEX_NEWS_CACHE_MINUTES: int = int(os.getenv("APEX_NEWS_CACHE_MINUTES", "15"))        # re-fetch news every N minutes
    APEX_NEWS_HOURS_LOOKBACK: int = int(os.getenv("APEX_NEWS_HOURS_LOOKBACK", "24"))      # scan last N hours of news

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
