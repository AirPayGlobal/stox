# STOX — Intraday Options Trading Engine

A ground-up rebuild of STOX as an **intraday options daytrading system** on the
Alpaca brokerage API. It trades near-dated (0–1 DTE) calls and puts on highly
liquid underlyings (SPY/QQQ by default), holds nothing overnight, and is built
around a **daily P&L governor**: a configurable daily profit target and a hard
daily loss limit.

## ⚠️ Read this first — the $5,000/day goal

The engine has a `DAILY_PROFIT_TARGET` (default $5,000): once the day's P&L
reaches it, the engine stops opening trades and locks in the day. **That is a
risk-management cap, not a promise.** No strategy — this one included — can
produce $5,000/day *consistently*. Options daytrading is high variance; most
retail options daytraders lose money, and there will be losing days (the
`DAILY_MAX_LOSS` circuit breaker exists precisely because of that).

The arithmetic also matters. With the default risk of 1% of equity per trade
and a max of 12 trades/day, a $5,000 **average** day implies roughly a
**$250,000–$500,000 account** having an exceptional edge (≈1–2% account growth
per day, which no fund on earth sustains). On a $25k–$50k account, a $5,000
daily target means risking a large share of the account every single day — a
fast path to ruin. Set `DAILY_PROFIT_TARGET` to something proportionate to
your capital (a common intraday framing is 0.5–1% of equity per day), and:

1. **Paper trade first** (`ALPACA_MODE=paper`) for at least 30 trading days.
2. Only go live if the paper results, including losing streaks, are acceptable.
3. Note US **pattern-day-trader rules** require ≥ $25,000 equity to daytrade.

## Strategies

Two independent signal strategies (`STRATEGY=orb|sweep|both`):

1. **ORB momentum** — opening-range breakout with VWAP/EMA/RSI confluence
   scoring, premium-based exits (+50% / −30% / time stop). Described below.
2. **Sweep reversal** — liquidity-sweep-and-reclaim ("manipulation candle"):
   a higher-timeframe candle sweeps the previous candle's low (or the
   previous day's low) and closes back above it → buy calls; mirror image →
   buy puts. Stop beyond the sweep wick, target at 2× the risk, both tracked
   on the **underlying** price. Optional retracement entry into the fair
   value gap for better RR. Systemized from trader transcripts — see
   [docs/STRATEGIES.md](docs/STRATEGIES.md) for the full mapping and caveats.

## How it trades

```
every 5 min (09:45–15:00 ET)                     every 30 s
┌───────────────────────────────┐    ┌──────────────────────────────────┐
│ 1. 5-min bars for SPY/QQQ     │    │ mark open positions (live quotes)│
│ 2. signal score 0–100:        │    │  · take-profit  +50% premium     │
│    VWAP / opening range /     │    │  · stop-loss    −30% premium     │
│    EMA9-21 / momentum / RSI   │    │  · time stop    90 min           │
│ 3. score ≥ 70 → LONG or SHORT │    │  · signal reversal               │
│ 4. pick contract: nearest     │    │  · 15:50 ET → flatten everything │
│    expiry ≤1 DTE, ~0.45 Δ,    │    │  · daily-loss halt → flatten     │
│    liquidity-filtered         │    └──────────────────────────────────┘
│ 5. size so stop-loss ≤ 1% of  │
│    equity → market buy        │    daily governor (always on)
└───────────────────────────────┘    · P&L ≥ target  → no new trades
                                     · P&L ≤ −max    → halt + flatten
                                     · caps: 12 trades/day, 3 concurrent
```

- **LONG signal → buy calls, SHORT signal → buy puts.** Defined-risk (long
  premium only — the most you can lose per trade is the premium, and the stop
  cuts it at −30%).
- Alpaca doesn't support bracket orders on options, so stops/targets are
  enforced by the engine's 30-second management loop.
- Everything is closed by 15:50 ET — no overnight gap risk.

## Project structure

```
stox/
├── main.py                # engine entry point (--dry-run, --once)
├── engine.py              # intraday loop: entries, exits, governor
├── config.py              # every knob, overridable via .env
├── check_auth.py          # credential + options-permission diagnostic
├── analysis/
│   ├── indicators.py      # EMA, RSI, VWAP, ATR, opening range (pure pandas)
│   └── signals.py         # LONG/SHORT/FLAT confluence scoring
├── data/
│   ├── market_data.py     # intraday stock bars (ET, RTH only)
│   └── options_data.py    # chains, snapshots, greeks, quotes
├── options/
│   └── contracts.py       # expiry/delta/liquidity contract selection
├── trading/
│   ├── broker.py          # Alpaca orders, positions, account
│   ├── risk.py            # sizing + daily governor
│   └── positions.py       # position book, persisted to logs/trades.json
├── backtest/
│   ├── bs.py              # Black-Scholes pricer
│   └── run_backtest.py    # strategy simulation on historical bars
├── api/
│   ├── server.py          # FastAPI dashboard + start/stop control
│   └── static/index.html  # live dashboard (P&L vs target, positions…)
└── tests/                 # pure-logic unit tests (no network needed)
```

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env        # add your Alpaca keys; enable OPTIONS on the account
python check_auth.py        # verify trading, data, and options permissions
```

**Backtest** (simulated option marks — see the caveat it prints):

```bash
python backtest/run_backtest.py --days 60 --equity 100000
```

**Dry run** (full pipeline, real signals and contract selection, no orders):

```bash
python main.py --dry-run
```

**Paper trade** (`ALPACA_MODE=paper` in `.env` — orders go to Alpaca's paper
account):

```bash
python main.py
```

**Dashboard** (watch P&L vs target live, stop/restart the engine):

```bash
uvicorn api.server:app --host 0.0.0.0 --port 8000
# open http://localhost:8000  (basic auth: DASHBOARD_USER / DASHBOARD_PASS)
```

The engine **starts automatically when the server boots** (default state is
running). Set `ENGINE_AUTOSTART=false` to require pressing ▶ Start instead,
or `ENGINE_AUTOSTART_DRY=true` to auto-start in signals-only dry-run mode.
Day P&L baseline, trade counts, and governor locks persist across restarts.

**Tests**:

```bash
pytest
```

## Configuration

All knobs live in `.env` (see `.env.example`). The ones that matter most:

| Variable | Default | Meaning |
|---|---|---|
| `DAILY_PROFIT_TARGET` | 5000 | Stop opening trades once day P&L ≥ this |
| `DAILY_MAX_LOSS` | 2500 | Flatten + halt once day P&L ≤ −this |
| `RISK_PER_TRADE_PCT` | 0.01 | Max loss at the stop per trade (fraction of equity) |
| `MAX_CONCURRENT_POSITIONS` | 3 | Open positions cap |
| `MAX_TRADES_PER_DAY` | 12 | Trade count cap |
| `UNDERLYINGS` | SPY,QQQ | Symbols scanned |
| `MAX_DTE` | 1 | 0 = same-day expiry only |
| `TAKE_PROFIT_PCT` / `STOP_LOSS_PCT` | 0.50 / 0.30 | Exit levels on premium |
| `FLATTEN_TIME` | 15:50 | Everything closed by this ET time |

## What was rebuilt and why

The previous app was a 30-minute-cadence **stock** swing bot. This rebuild:

- trades **options** (calls/puts) intraday with strict day-boundary discipline;
- adds the **daily profit-target / max-loss governor** the old app lacked;
- fixes the old timezone bug (schedules were hardcoded UTC; everything is now
  computed in `America/New_York`);
- drops the fragile `ta`/`pandas-ta` dependency (indicators are ~40 lines of
  pandas);
- sizes positions off the **actual loss at the stop**, not notional;
- persists the position book so a restart mid-session doesn't orphan trades;
- ships unit tests for all pure logic and a single-file dashboard with no
  Node/Vite build step.
