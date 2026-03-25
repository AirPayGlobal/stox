# STOX — Algorithmic Trading Bot

## Overview
Algorithmic stock trading bot using EMA + RSI + MACD + Bollinger Bands strategy.
Runs on Railway, trades via Alpaca paper trading API.

## Architecture
- `main.py` — Trading bot with scheduled scan loop
- `api/server.py` — FastAPI dashboard backend
- `dashboard/` — React frontend
- `analysis/` — Indicators + signal generation
- `trading/` — Alpaca client, risk manager, portfolio tracker
- `backtest/` — Backtesting engines (single-symbol + portfolio)
- `config.py` — All tunable parameters

## Weekly Optimization Agent

When running as a scheduled weekly agent, follow this workflow:

### Step 1: Run Performance Review
```bash
cd /home/user/stox && python analysis/review.py --days 7 --output /tmp/review.json
```
Read the output. Key metrics to evaluate:
- **Win rate** < 40%: signals too loose, raise BUY_THRESHOLD
- **Win rate** > 65%: can afford more entries, lower BUY_THRESHOLD
- **Profit factor** < 1.0: losing money, widen stops or tighten entries
- **Profit factor** > 2.0: strategy working well, consider sizing up

### Step 2: Run Backtest with Current Parameters
```bash
python backtest/portfolio_backtest.py --days 365 --symbols 20 --output /tmp/backtest_current.json
```

### Step 3: Apply Recommendations
Based on the review output, modify parameters in these files:
- `config.py` — RSI thresholds, position sizing, stop/take-profit percentages
- `analysis/signals.py` — BUY_THRESHOLD, SELL_THRESHOLD, scoring weights
- `config.py` WATCHLIST — Remove consistently losing symbols

### Step 4: Backtest New Parameters
Run the backtest again to verify the changes improve Sharpe ratio and profit factor.

### Step 5: Deploy
```bash
git add -A
git commit -m "Weekly optimization: [describe changes]"
git push origin claude/stock-trading-bot-QeUGQ
```
Railway auto-deploys on push.

### Rules
- Never change more than 2-3 parameters at once
- Never increase MAX_POSITION_PCT above 10%
- Never decrease STOP_LOSS_PCT below 1%
- Always verify backtest Sharpe ratio improves before deploying
- If win rate drops below 30%, revert to previous parameters
- Log all changes and reasoning in commit messages
