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
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.05"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "20"))
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "0.02"))
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
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B",
        "UNH", "JNJ", "V", "XOM", "JPM", "PG", "MA", "HD", "CVX", "LLY",
        "ABBV", "MRK", "AVGO", "COST", "PEP", "KO", "TMO", "BAC", "WMT",
        "CSCO", "ACN", "MCD", "ABT", "DHR", "CRM", "NEE", "TXN", "ADBE",
        "NKE", "PM", "LIN", "BMY", "RTX", "QCOM", "AMGN", "UPS", "SBUX",
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
    SECTOR_TOP_N: int = int(os.getenv("SECTOR_TOP_N", "4"))           # only buy in top N sectors by 3-month momentum

    # Kelly Criterion position sizing
    KELLY_MIN_TRADES: int = int(os.getenv("KELLY_MIN_TRADES", "20"))          # min closed trades before Kelly activates
    KELLY_MIN_FRACTION: float = float(os.getenv("KELLY_MIN_FRACTION", "0.01")) # floor: never size below 1% of equity

    # Trailing stop — tiered: tighter when gains are small, looser as the trend develops
    TRAILING_STOP_PCT: float      = float(os.getenv("TRAILING_STOP_PCT",      "0.04"))  # gain < MID_TRIGGER:  4% trail
    TRAILING_STOP_MID_PCT: float  = float(os.getenv("TRAILING_STOP_MID_PCT",  "0.05"))  # gain < HIGH_TRIGGER: 5% trail
    TRAILING_STOP_HIGH_PCT: float = float(os.getenv("TRAILING_STOP_HIGH_PCT", "0.07"))  # gain >= HIGH_TRIGGER: 7% trail
    TRAILING_MID_TRIGGER: float   = float(os.getenv("TRAILING_MID_TRIGGER",   "0.08"))  # loosen trail at +8% gain
    TRAILING_HIGH_TRIGGER: float  = float(os.getenv("TRAILING_HIGH_TRIGGER",  "0.18"))  # loosen trail again at +18% gain
    BREAK_EVEN_TRIGGER_PCT: float = float(os.getenv("BREAK_EVEN_TRIGGER_PCT", "0.03"))  # once up 3%, never close below entry

    # Earnings blackout
    EARNINGS_BLACKOUT_DAYS: int = int(os.getenv("EARNINGS_BLACKOUT_DAYS", "2"))  # skip entry within N days of earnings

    # Correlation limit
    MAX_POSITION_CORRELATION: float = float(os.getenv("MAX_POSITION_CORRELATION", "0.7"))  # skip if r > this with any open position

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

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
