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
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "10"))
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "0.02"))
    TAKE_PROFIT_PCT: float = float(os.getenv("TAKE_PROFIT_PCT", "0.06"))
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

    # Trailing stop
    TRAILING_STOP_PCT: float = float(os.getenv("TRAILING_STOP_PCT", "0.06"))   # close if price drops 6% from peak

    # Earnings blackout
    EARNINGS_BLACKOUT_DAYS: int = int(os.getenv("EARNINGS_BLACKOUT_DAYS", "2"))  # skip entry within N days of earnings

    # Correlation limit
    MAX_POSITION_CORRELATION: float = float(os.getenv("MAX_POSITION_CORRELATION", "0.7"))  # skip if r > this with any open position

    # Market filters
    VIX_THRESHOLD: float = float(os.getenv("VIX_THRESHOLD", "25"))   # skip buys when VIX > this
    MIN_SENTIMENT_SCORE: float = float(os.getenv("MIN_SENTIMENT_SCORE", "-0.2"))  # legacy news-only filter
    MIN_COMPOSITE_SENTIMENT: float = float(os.getenv("MIN_COMPOSITE_SENTIMENT", "-3.0"))  # composite 4-source filter

    # IPO tracking
    IPO_MIN_DAYS: int = int(os.getenv("IPO_MIN_DAYS", "5"))              # quarantine days before trading
    IPO_POSITION_SCALE: float = float(os.getenv("IPO_POSITION_SCALE", "0.5"))   # 50% of normal size
    IPO_STOP_LOSS_PCT: float = float(os.getenv("IPO_STOP_LOSS_PCT", "0.04"))    # 4% stop (wider than normal 2%)

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
