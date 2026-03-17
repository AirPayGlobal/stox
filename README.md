# STOX — Algorithmic Stock Trading Bot

A Python-based algorithmic trading bot for US stocks (NYSE/NASDAQ) via the **Alpaca** brokerage API.

## Strategy

**EMA + RSI + MACD + Bollinger Bands** — multi-indicator confluence:

| Indicator | Role |
|-----------|------|
| EMA 9 / 21 | Fast trend direction & crossover entry/exit signals |
| EMA 50 | Long-term trend filter (only trade in uptrends) |
| RSI 14 | Momentum filter — avoid overbought entries (40–65 zone) |
| MACD (12/26/9) | Momentum confirmation — histogram turning positive |
| Bollinger Bands (20, 2σ) | Volatility context — price position within bands |
| ATR 14 | Volatility-adjusted position sizing & stop-loss distance |

### Entry (BUY)
All conditions are scored (0–100). A score ≥ 60 triggers a buy:
- Price above 50-EMA (macro uptrend)
- Fast EMA above or crossing above slow EMA
- RSI between 40 and 65
- MACD histogram positive or turning positive
- Price below upper Bollinger Band

### Exit
- **Stop-loss**: 1× ATR below entry price (broker bracket order)
- **Take-profit**: 3× ATR above entry price (3:1 reward/risk)
- **Signal exit**: SELL score ≥ 60 (EMA death cross, overbought RSI, etc.)

## Risk Management (Conservative Compounding)

| Parameter | Default | Description |
|-----------|---------|-------------|
| Max position size | 5% of equity | Per stock |
| Stop-loss | 1× ATR (≈ 2%) | Per trade |
| Take-profit | 3× ATR (≈ 6%) | 3:1 R:R ratio |
| Max open positions | 10 | Concurrent |
| Daily loss limit | 5% | Halts trading for the day |
| Profit reinvestment | 100% | Full compounding |

## Project Structure

```
stox/
├── main.py                    # Bot entry point / scheduler
├── config.py                  # All settings (reads from .env)
├── requirements.txt
├── .env.example               # Copy to .env and add your keys
│
├── data/
│   └── fetcher.py             # Alpaca market data (OHLCV bars)
│
├── analysis/
│   ├── indicators.py          # EMA, RSI, MACD, Bollinger Bands, ATR
│   └── signals.py             # BUY/SELL/HOLD signal scoring engine
│
├── strategy/
│   ├── base_strategy.py       # Abstract base class
│   └── ema_rsi_macd.py        # Main strategy implementation
│
├── trading/
│   ├── alpaca_client.py       # Alpaca broker API wrapper
│   ├── risk_manager.py        # Position sizing, daily loss limits
│   └── portfolio.py           # Trade tracking & performance metrics
│
├── backtest/
│   ├── engine.py              # Bar-by-bar backtesting engine
│   └── run_backtest.py        # CLI backtest runner
│
└── utils/
    └── logger.py              # Logging (console + daily file)
```

## Quickstart

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure API keys
```bash
cp .env.example .env
# Edit .env — add your Alpaca API key and secret
```

Get free API keys at [alpaca.markets](https://alpaca.markets) (paper trading is free).

### 3. Run a backtest first (no API keys needed for data)
```bash
# Backtest 10 stocks with 500 days of history
python backtest/run_backtest.py AAPL MSFT NVDA GOOGL AMZN --days 500

# Backtest entire watchlist
python backtest/run_backtest.py
```

### 4. Paper trade (safe — no real money)
Ensure `ALPACA_MODE=paper` in your `.env`, then:
```bash
python main.py
```

### 5. Dry-run mode (scan signals only, no orders)
```bash
python main.py --dry-run
```

### 6. Go live (when ready)
Set `ALPACA_MODE=live` in `.env` and run:
```bash
python main.py
```

> **Warning**: Only switch to live mode after validating the strategy with paper trading for at least 30 days.

## Capital Growth Model

With conservative compounding:
- **All profits are reinvested** — position sizes grow as equity grows
- **3:1 reward/risk** means you can lose 3 out of 4 trades and still break even
- **Daily loss limit** protects capital during adverse market conditions
- **ATR-based sizing** automatically reduces position sizes in volatile markets

## Watchlist

The default watchlist contains 45 large-cap S&P 500 stocks. Customise via `Config.WATCHLIST` in `config.py` or set in `.env`.

## Logs

- Console: real-time output
- File: `logs/YYYY-MM-DD.log` (daily rotation)
- Portfolio: `logs/portfolio.json` (persisted trade history)
