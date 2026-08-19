# STOX Options — System Overview

*A complete, self-contained description of the STOX intraday options trading
engine as it exists today, written so an external reviewer (human or model)
can critique the design, the strategy, and the risk model without access to
the repository. Nothing here is aspirational — it describes the code that is
actually running.*

**Date of this snapshot:** 2026-08-19
**Language/stack:** Python 3.11 · pandas/numpy · FastAPI + uvicorn · alpaca-py · pytest
**~5,460 lines of Python · 121 passing unit tests**
**Deployment:** Railway (Docker), persistent volume at `/data`, single always-on process

---

## 1. What this system is

An automated engine that **buys intraday options** (long premium — calls and
puts, no spreads, no short premium) on liquid index ETFs (**SPY and QQQ**),
holding for minutes to at most a session. Expiry is **0–1 DTE** (same-day or
next-day). The stated business goal is "$5,000/day in profit consistently" on
a ~$100k account. **That goal has not been achieved live.** The system's real
current value is as a disciplined, instrumented harness for testing whether
any intraday options edge survives contact with real fills.

It is **not** a swing trader, does not hold overnight (everything flattens at
15:50 ET), and does not trade single stocks (they were removed after the
rebuild).

---

## 2. History / how we got here (important context for a reviewer)

The repo began as a stock-swing bot and was rebuilt into an intraday options
engine. Three strategies have been tried **live** and each failed once the
sample grew past ~20 trades:

1. **ORB (opening-range breakout)** — momentum: buy calls when price breaks
   above the first 15 minutes' high, puts below the low. Failed live.
2. **Sweep (liquidity-sweep reversal)** — systemized from trader-influencer
   transcripts: price sweeps an obvious prior high/low then reverses; trade the
   reversal with an underlying-level stop beyond the wick and a 2R target.
   Backtested ~+$30k, went **breakeven-to-−$1,281 over 26 live trades.**
3. **Swing** — 4H multi-day sweep-reclaim. Backtest-only, never run live.

**Diagnosis common to ORB and sweep:** using per-trade MFE/MAE instrumentation
we found they **entered at reversals** — they bought right as the move
exhausted. Both are "counter-move" or "chase" entries.

**Current strategy — Fib gold-zone pullback** — was designed specifically to
have the *opposite* flaw: it enters **with** an established trend on a
retracement, at a favorable price. It is the current production default.

**The governing discipline (stated explicitly so a reviewer can hold us to
it):** a strong backtest is a hypothesis, not proof. Simulated option fills are
labeled an *upper bound*. Nothing gets real money until it has survived a
**live paper trial** and the live record has been compared against the backtest
over a meaningful sample. This rule is the direct lesson of the sweep failure.

---

## 3. The current strategy: Fib gold-zone pullback (`analysis/fib.py`)

### Thesis
After an impulse leg (a fast directional move), price commonly retraces to the
0.5–0.618 Fibonacci "gold zone" of that leg before continuing in the trend
direction. Enter with the trend on the pullback; stop where the trend is
invalidated; target continuation.

### Mechanics (pure function: bars in → signal out, unit-tested)
Runs on **1-minute** bars, one session at a time.

1. **Fractal pivots.** A bar is a pivot high/low if it is the strict extreme of
   the `FIB_PIVOT_K = 2` bars on each side. (A pivot is therefore only
   confirmable 2 bars after it occurs — an inherent 2-bar lag.)

2. **Active leg = the dominant swing of a recent window.** Within the last
   `FIB_LOOKBACK_BARS = 45` bars, take the **highest** high-pivot and the
   **lowest** low-pivot. The order of those two extremes sets direction:
   - top occurs *after* bottom → up-impulse → **LONG bias** (buy calls)
   - bottom occurs *after* top → down-impulse → **SHORT bias** (buy puts)

   > **This is the key design choice and the most recent fix.** The previous
   > version anchored on the *last two pivots*, so the instant a pullback
   > printed its own minor pivot, the detected leg flipped direction and the
   > setup vanished — the deep (0.618) entries the strategy most wants were the
   > ones most often missed. Anchoring on the window's swing extremes means a
   > shallow pullback (neither a new high nor a new low) does **not** move the
   > anchors, so the setup survives the whole retracement. This single change
   > took backtested trade frequency from ~1/day to ~7–9/day (see §8).

3. **Touch entry.** Fire when the latest bar *wicks* into the 0.5–0.618 zone
   (a close-inside-the-band rule almost never triggers on 1-min bars), with the
   trend intact (origin not broken). Legs smaller than
   `FIB_MIN_RANGE_PCT = 0.0007 × spot` are ignored as noise.

4. **Exits are on the UNDERLYING price** (not the option premium):
   - **Stop** = fib 1.0 level = the leg origin (trend invalidated there).
   - **Target** = the impulse extreme (continuation).
   - Entry near 0.618 gives roughly **1 : 1.6 reward:risk**.
   - Plus a **time stop** `FIB_MAX_HOLD_MINUTES = 45` (a stalled pullback frees
     the slot), a wide premium **disaster backstop** (`SWEEP_DISASTER_STOP_PCT`
     = −60% on the option), and the **15:50 flatten**.

5. **Stop-distance band (fib's own).** The stop distance (≈0.5 × leg range) must
   be within `FIB_MIN_STOP_PCT = 0.0005` … `FIB_MAX_STOP_PCT = 0.012` of spot.
   Too tight → risk-based sizing balloons and slippage dominates; too wide →
   swing-sized stop unreachable intraday. (Previously fib borrowed the sweep
   strategy's 60-minute bounds, which discarded most 1-minute legs — a bug.)

### Position sizing (shared by all strategies)
Contracts are chosen so the loss **if the underlying stop is hit** ≤
`RISK_PER_TRADE_PCT = 0.5%` of tradable equity, translating the underlying stop
distance to option loss via delta (`risk/contract ≈ |delta| × stop_distance ×
100`; ATM delta 0.5 assumed when greeks are missing). Also capped by
`MAX_POSITION_PCT = 10%` premium outlay and `MAX_CONTRACTS = 50`.

### Contract selection (`options/contracts.py`)
`MAX_DTE = 1`, `TARGET_DELTA = 0.45`, `MIN_OPEN_INTEREST = 100`,
`MAX_SPREAD_PCT = 0.10`, `MIN_BID = 0.10`.

---

## 4. Risk & money management (strategy-agnostic — `trading/risk.py`)

All governor logic operates on **ENGINE P&L** (realized on the engine's own
trades today + unrealized on positions it currently manages) and **tradable
equity** (account equity minus the value of positions the engine does *not*
manage). This isolation was added after a false HALT: the account held junk
stock from the old bot, and using raw account equity produced phantom losses.

- **Daily profit governor.** Hitting `DAILY_PROFIT_TARGET = $5,000` does **not**
  stop trading — it arms a **trailing profit floor**:
  `floor = max(target × 0.70, peak × (1 − 0.30))`. If day P&L falls back to the
  floor, the day is banked. `PROTECT_MODE = hold` stops *new* entries but lets
  open trades run to their own exits (`flatten` would close everything).
- **Daily max loss.** `DAILY_MAX_LOSS = $1,500` → flatten + halt for the day.
- **Rolling-drawdown circuit breaker.** Over a trailing `DRAWDOWN_WINDOW_DAYS =
  20`, measure give-back from the equity-curve peak (as % of `DRAWDOWN_BASE =
  $100k`): beyond `4%` → halve new-position size; beyond `6%` → stop opening.
  A `dd_reset_at` marker (persisted) lets "Reset day" rebaseline it.
- **Trade caps.** `MAX_TRADES_PER_DAY = 12`, `MAX_CONCURRENT_POSITIONS = 3`.
- **Loss discipline (per underlying, per day).** `LOSS_COOLDOWN = 30 min` after
  a loser, `WIN_COOLDOWN = 10 min` after a winner, and `MAX_CONSECUTIVE_LOSSES
  = 3` then done for the day on that symbol.

> **Reviewer note — a real interaction effect.** `MAX_TRADES_PER_DAY = 12` and
> the cooldowns were tuned for a low-frequency strategy. The reworked fib wants
> ~7–9 trades/day *per symbol*; these caps and the profit governor will bind
> much harder live than in the backtest (which models neither — see §7).

---

## 5. Engine loop (`engine.py`)

Single-threaded loop, `LOOP_SECONDS = 30`:
1. Periodic **broker reconciliation** every `RECONCILE_SECONDS = 180`: adopt
   option positions the book doesn't know about (orphans from a deploy
   cutover), close book entries the broker no longer holds (`EXTERNAL`), surface
   unmanaged **share** positions (e.g. from an ITM option auto-exercising) as a
   dashboard warning — the engine does not trade shares.
2. **Manage open positions** every tick (30s): mark each, update MFE/MAE, check
   exits (underlying stop/target for fib & sweep; premium exits for ORB), apply
   flatten window.
3. **Scan for new entries** only every `SCAN_SECONDS = 300` (**5 minutes**).

> **⚠ Reviewer note — the biggest live-vs-backtest structural gap.** The fib
> signal is a **1-minute touch** (price wicks into the zone on some bar), but
> the live engine only *looks for entries every 5 minutes*. The backtester
> evaluates the signal on **every 1-minute bar**. Therefore the backtest's
> ~7–9 trades/day is an **overcount** for live: any touch that occurs and
> reverts inside a 5-minute gap is invisible live, and the entry price live is
> whatever the 5-minute sample catches, not the exact touch. This alone could
> explain a large fraction of any future live-vs-backtest divergence. Lowering
> `SCAN_SECONDS` toward 60 for fib is an obvious candidate change but has not
> been made or tested yet.

On boot the engine **auto-starts** (`ENGINE_AUTOSTART = true`) and state
persists to `/data` (trade book, day baseline) so a redeploy doesn't lose the
day.

---

## 6. Backtester (`backtest/run_backtest.py` + `backtest/bs.py`)

Replays historical intraday bars and **simulates** option marks with
Black-Scholes: same-day expiry, IV from a realized-volatility proxy (annualized
stdev of 5-min log returns, floored at 10%), and an **assumed half-spread of
`$0.02` per side** (`SPREAD_COST`). It reproduces engine parity for entries,
underlying exits, the fib time stop, and the loss-discipline cooldowns.

**What the backtester does NOT model (critical for interpreting §8):**
- **The daily profit/loss governor.** It runs every setup. Live, the +$5k
  profit-protection would stop new entries on the big up days, and the −$1,500
  max-loss would halt the big down days. Net effect on fib: it **shaves the fat
  upper tail** the P&L leans on (see concentration in §8) and floors the worst
  days.
- **The 5-minute scan cadence** (§5) — it evaluates every 1-min bar.
- **`MAX_TRADES_PER_DAY` / concurrency caps.**
- **Real fill quality:** the $0.02 half-spread is optimistic for 0DTE,
  especially QQQ and away from ATM; no slippage, no partial fills, no IV crush
  beyond the crude proxy. **Every result is explicitly labeled an upper bound.**

---

## 7. Live-vs-backtest comparison view (`/api/compare`, `reporting.py`)

Because the sweep failure was only caught by manually diffing live trades
against the backtest, that check is now a first-class feature:

- `strategy_live_stats(book, strategy, days)` — live stats in the *same field
  shape* the backtester emits (win rate, expectancy/trade, exits, etc.).
- `live_vs_backtest(live, backtest)` — classifies drift:
  `collecting` (< 20 live trades, refuses to judge), `tracking` (live
  expectancy within ~40% of sim), `diverging` (live negative while sim positive
  — the sweep failure mode, flagged red), `underperforming`.
- Dashboard panel + endpoint run the same strategy through the backtester over
  the same window and lay the two side by side.

**Intended use:** once ~20–30 live paper fib trades exist, this panel tells us
whether the ~$170/trade simulated expectancy is surviving real fills.

---

## 8. Current backtest results — reworked fib (SPY,QQQ · $100k · simulated)

| Window | Trades | Win% | Total P&L | $/trade | Best / Worst day | Days ≥ $5k | Exits (SL / TP / TIME) |
|---|---|---|---|---|---|---|---|
| 30d  | 202 | 44% | +$36,743  | $182 | +$19,607 / −$3,864 | 4/23  | 84 / 76 / 42 |
| 60d  | 381 | 44% | +$56,205  | $148 | +$19,607 / −$3,864 | 5/42  | 163 / 137 / 81 |
| 90d  | 563 | 43% | +$97,110  | $172 | +$19,607 / −$3,864 | 10/62 | 226 / 202 / 135 |
| 120d | 782 | 45% | +$132,002 | $169 | +$19,607 / −$3,864 | 11/84 | 301 / 288 / 193 |

**For comparison, the *pre-rework* fib (SPY only):** 30d 13 trades/+$4,178;
90d 68/+$35,834; 120d 81/+$38,256 — ~65–73% win rate, ~$320–530/trade.

### How to read this (our own honest interpretation)
- **The trade-count fix worked** (~8–15× more trades) and **per-trade
  expectancy is strikingly stable** ($148–$182 across all four windows) — a
  genuine robustness signal, not one lucky window.
- **But the character changed:** win rate fell from ~70% to ~44%. The strategy
  now relies on a favorable R:R across many more, lower-edge trades. **This is
  the most friction-sensitive profile that exists**, and the backtest's $0.02
  half-spread assumption is where such profiles are flattered most. At ~$170
  edge/trade, a few cents of extra real spread per side × the contract count can
  erase it entirely.
- **Concentration:** the **same +$19,607 day** appears in all four windows —
  one historical trend day. In the 30-day window it is **53%** of the entire
  month's P&L (15% even at 120d). The result leans on rare monster days…
- **…which the un-modeled governor would clip** (§6): profit protection stops
  new entries after +$5k, so live cannot realize a +$19,607 day at that size.
- **Only 13–17% of days reach the $5k target** even in this optimistic sim.

**Bottom line we're operating under:** the rework is promising and behaves
correctly, but the +$132k is an upper bound with an unusually large plausible
gap to live reality (friction + governor + 5-min scan). It has earned a **live
paper trial**, not real money. The `/api/compare` view is how we'll adjudicate.

---

## 8b. Paper-trading validation upgrade (current)

A validation harness now tests whether the fib backtest edge survives realistic
execution and full production constraints. **It changes no live-trading
behaviour** — it is measurement infrastructure.

- **Engine-parity simulator** (`backtest/fib_sim.py`): replays fib with the same
  constraints the live engine enforces — 5-minute scan cadence (closing the §5
  gap between the old every-bar backtest and the live engine), account-level
  daily governor (profit floor + max-loss halt), trade/concurrency caps across
  both symbols, cooldowns, time stop, and end-of-day flatten.
- **Execution scenarios** (`backtest/execution.py`): the single fixed \$0.02
  half-spread is replaced by `baseline | optimistic | base | conservative | mid`
  scenarios modelling spread, slippage, delayed fills, and unfilled/partial
  fills. Deterministic. **Mid fills only under the explicit `mid` diagnostic.**
- **Research-grade records + reports** (`backtest/validation.py`): every trade
  records strategy version + config hash, contract bid/ask/spread/delta/DTE,
  intended vs simulated fill + delay + outcome, exit reason, underlying & option
  MFE/MAE, planned risk + realized R, and regime/time-of-day. Reports slice by
  symbol, DTE, time-of-day, spread bucket, and regime; add tail-dependence
  (median day, worst-5%, P&L ex best 1/3/5 days) and **walk-forward** folds with
  frozen params; and compare all execution scenarios and every optional filter.
- **Optional selectivity filters** (`analysis/fib_filters.py`): trend, vol
  regime, time-of-day, liquidity, post-touch confirmation. **All default OFF**;
  enabling one requires out-of-sample proof (see `docs/FIB_VALIDATION_REPORT.md`).
- **Operational safeguards** (`trading/safeguards.py`): entries are blocked on
  stale market data, in-flight/unresolved orders, reconciliation mismatch, or
  unmanaged share positions, each surfaced as a dashboard warning; fib dedupe
  keys persist per day so a restart cannot re-fire a traded signal.

**Revised assumptions vs §6:** execution is no longer a single optimistic
constant, and the backtest now applies the governor, caps, and 5-minute cadence
it previously ignored — so `base`/`conservative` scenario results are far more
representative than the prior upper-bound number.

**Remaining limitations (unchanged or new):** option marks are still
Black-Scholes (no real options chain, no true bid/ask, no IV surface); the
modelled spread is a premium/DTE heuristic, not observed quotes; DTE is
simulated per-run (0 vs 1) rather than chosen from a live chain; and results
generated in a credential-less environment use **synthetic bars** and are
labelled illustrative. Authoritative numbers require running the harness on real
bars. See `docs/FIB_VALIDATION_REPORT.md`.

## 9. Repository map

```
config.py               Central Config — every knob, all env-overridable (§ values above)
engine.py               TradingEngine: loop, entries, exits, reconciliation, P&L
analysis/
  fib.py                CURRENT strategy: pivots, swing-anchored leg, gold-zone touch
  signals.py            ORB signal + optional entry filters (VWAP/RVOL/OR-ATR/volume)
  sweeps.py             Sweep-reclaim, prev-day & overnight-range sweeps, FVG, stop band
  exits.py              managed_stop / effective_stop (ORB) / sweep_trail_stop
  htf.py                Higher-timeframe resample + completed-bar helpers
  indicators.py         ATR, RVOL, VWAP, RSI, etc.
backtest/
  run_backtest.py       Per-day simulators (orb/sweep/swing/fib), stats, CLI + API entry
  bs.py                 Black-Scholes pricer + delta (simulated marks)
  swing.py              4H multi-day sweep-reclaim simulator (backtest-only)
  execution.py          Execution-cost scenarios (baseline/optimistic/base/conservative/mid)
  fib_sim.py            Engine-parity fib simulator + research-grade trade records
  validation.py         Scenario/slice/robustness/walk-forward reports + Markdown
  regime.py             Trend / realized-vol / time-of-day classifiers
  synth.py              Deterministic synthetic bars (offline tests + illustrative report)
analysis/fib_filters.py Optional selectivity filters (all default OFF)
trading/safeguards.py   Entry-block predicates + persisted restart-safe dedupe store
trading/
  positions.py          PositionBook + Trade (planned_risk, realized_r, MFE/MAE), JSON persist
  risk.py               Daily governor, drawdown breaker, sizing, DayState persistence
  broker.py             Alpaca order placement / position close / account
data/
  market_data.py        Intraday/daily/today bars (RTH flag)
  options_data.py       Option chain + quotes
options/contracts.py    Contract selection (DTE/delta/OI/spread/bid filters)
reporting.py            period_report, daily_report, CSV export, trade-quality,
                        concentration, rolling_drawdown, strategy_live_stats, live_vs_backtest
api/
  server.py             FastAPI: dashboard, status, trades, report, backtest, compare, reset-day
  static/index.html     Single-file dashboard (P&L vs target, positions, signals,
                        backtest runner, live-vs-backtest panel, drawdown banner)
main.py                 CLI entry (dry-run / live)
check_auth.py           Verifies Alpaca trading/data/options permissions
docs/STRATEGIES.md      How the trader transcripts were systemized into sweep + fib
Dockerfile, railway.toml  Deploy (shell-form CMD binds $PORT; healthcheck /healthz)
```

---

## 10. Known open questions for a reviewer to weigh in on

1. **Is the reworked fib's edge real or a friction mirage?** At 44% win rate and
   ~$170 simulated edge/trade with $0.02 assumed half-spread, how much real
   0DTE/1DTE SPY & QQQ spread+slippage would flip it negative? Is this profile
   viable at all, or should we prefer fewer, higher-edge trades?
2. **The 5-min scan vs 1-min signal mismatch (§5)** — now *modelled*: the
   validation simulator evaluates entries at the live 5-minute cadence
   (`FIB_SCAN_BARS`), so the backtest no longer overcounts touches. Open
   question remains whether `SCAN_SECONDS` *should* drop to ~60 for fib, and
   what that does to real fill quality — decide from the real-bar report.
3. **Governor interaction (§6/§8).** The profit-protection floor clips exactly
   the monster days the P&L depends on. Is a trailing-floor governor even the
   right meta-strategy for a high-frequency, positive-expectancy grinder, or
   does it hurt more than it helps here?
4. **Concentration.** A single day = up to 53% of a month. How should we think
   about a strategy whose expectancy is real but whose *totals* are tail-driven?
5. **Contract selection for a thin-edge strategy.** `MAX_SPREAD_PCT = 0.10` is
   loose; tightening to ~0.05 defends the edge but cuts trade count. Right call?
6. **Is buying intraday premium the wrong instrument entirely** for a
   pullback-continuation thesis (theta + spread as constant headwinds), vs
   spreads or trading the underlying/futures?
7. **Overfitting risk.** The fib params (lookback 45, pivot-k 2, min-range
   0.0007, stop band) were chosen by reasoning, not optimized to the curve, and
   expectancy is stable across four windows — but is that enough to trust them?

---

*Everything above reflects code on branch `main` (and the active development
branch) as of the snapshot date. Backtest numbers are from the dashboard runner
with simulated Black-Scholes fills and should be treated as an upper bound.*
