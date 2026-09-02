# Fib Gold-Zone Pullback — Validation Report

> **STATUS: REAL-BAR validation complete. Result is NEGATIVE.** Option marks are
> still Black-Scholes (simulated), but the bars are real SPY/QQQ 1-minute data
> (138 sessions, 2026-02-02 … 2026-08-19) and the run enforces full engine
> parity. Every result is **simulated fills on real bars**, not live
> performance. No profitability claim is made — the finding is that the prior
> positive backtest does **not** survive realistic execution and production
> constraints.

## 1. Purpose

Establish whether the Fib strategy's backtest edge survives **realistic
execution friction** and **full production constraints**, before any decision to
commit capital. This answers the review concern that the prior backtest (a)
evaluated every 1-minute bar while the live engine scans every 5 minutes, (b)
assumed a single fixed \$0.02 half-spread, (c) omitted the daily governor and
other production limits, and (d) leaned on a few exceptional days.

## 2. Method — engine parity

The harness (`backtest/fib_sim.py`, `backtest/validation.py`) runs Fib under the
same constraints the live engine enforces: 5-minute scan cadence, completed-bar
signals, account-level daily governor (profit floor + max-loss halt), trade and
concurrency caps across both symbols, cooldowns, the time stop, and end-of-day
flatten. Execution friction is modelled by named scenarios
(`backtest/execution.py`): spread, slippage, delayed fills, and unfilled/partial
fills. **Mid-price fills are never used** except the explicit `mid` diagnostic.

Run: `python -m backtest.validation --days 200 --equity 100000` (real bars).
Switches were **left unchanged** for the primary run (all `FIB_FILTER_*` OFF,
`MAX_SPREAD_PCT` 0.10, cadence 5-min). config_hash `80c3ab582c52`.

## 3. Execution scenarios

| Scenario | Spread crossed | Slippage | Delay | No-fill / partial | Purpose |
|---|---|---|---|---|---|
| `baseline` | flat \$0.02/side | none | none | never | reproduce the *old* assumption |
| `optimistic` | ½ modelled spread | none | none | never | best plausible fills |
| `base` | full ½-spread | \$0.01 | 1 bar | ~3% / 5% | realistic central estimate |
| `conservative` | full ½-spread | \$0.03 | 1 bar | ~8% / 10% | stress case |
| `mid` | none (fills at mid) | — | — | — | **diagnostic only** |

## 4. Results — REAL BARS (138 sessions, SPY+QQQ, \$100k)

**Execution-scenario comparison (base strategy, filters off):**

| | Trades | Win% | Total P&L | Exp/trade | Median day | MaxDD |
|---|---|---|---|---|---|---|
| baseline (old \$0.02 assumption) | 1055 | 41% | +\$42,092 | +\$40 | −\$12 | \$11,272 |
| optimistic | 1077 | 42% | +\$100,823 | +\$94 | +\$422 | \$7,759 |
| **base (realistic)** | 1003 | 37% | **−\$30,775** | **−\$31** | −\$658 | \$42,289 |
| **conservative (stress)** | 856 | 31% | **−\$133,803** | **−\$156** | −\$1,580 | \$136,593 |

**Two findings, and the first does not depend on the friction model:**

1. **Engine parity alone gutted the edge.** Under the *same* old \$0.02-spread
   assumption, applying the 5-minute cadence + governor + caps on real bars gives
   **+\$40/trade at 41% win** — versus the ~+\$169/trade the prior every-bar,
   no-governor backtester reported. Most of the original number was cadence
   overcount and un-modelled constraints, not edge.
2. **Realistic friction flips it negative.** `base` = **−\$31/trade**,
   `conservative` = **−\$156/trade**. Only the optimistic-fill assumption stays
   positive.

**Walk-forward (frozen params, 4 contiguous unseen folds, base):** expectancy per
trade **+\$15 → −\$32 → −\$56 → −\$60** — negative in three of four folds and
deteriorating. Fails the stability bar.

**Tail dependence (base):** total −\$30,775; removing the best 1/3/5 days makes it
*worse* (−\$36k / −\$47k / −\$54k). This is not a few-good-days story — it is a
broad, persistent loss.

**0DTE vs 1DTE (base):** 0DTE −\$31/trade; **1DTE −\$174/trade** — far worse.

**Slices (base) — where the losses concentrate:**

| Dimension | Least bad | Worst |
|---|---|---|
| Time of day | morning **+\$33/trade** (only positive bucket) | afternoon −\$126 |
| Volatility | high-vol **+\$60/trade** | low-vol −\$99 |
| Trend regime | up +\$10 | flat −\$124 |
| Symbol | SPY −\$11 | QQQ −\$50 |
| Spread bucket | medium(5-10%) −\$10 | wide(≥10%) −\$146 |

## 4b. Impact of each proposed switch (real bars; nothing changed)

**Switch 1 — `MAX_SPREAD_PCT` 0.10 → 0.05:** produces **0 trades** in the
simulator. ⚠ This is a **modelling artifact, not a result**: the sim's entry
spread% is the *scenario's assumption* (≥6% of premium in `base`), not a real
quote, so a 5% cap rejects everything. `MAX_SPREAD_PCT` gates on *live* quotes,
which this harness does not have. **Inconclusive — keep 0.10; the real 0.05
question needs live quote data.**

**Switch 2 — scan cadence 5-min → 1-min:** base −\$30,775 → −\$19,544 (still
negative); conservative −\$133,803 → −\$159,032 (worse). 1-min does not rescue
the strategy. **Keep 5-min.**

**Switch 3 — optional filters, each alone (base / conservative expectancy):**

| Filter | base Exp | base MaxDD | conservative Exp |
|---|---|---|---|
| none | −\$31 | \$42,289 | −\$156 |
| trend | −\$50 | \$40,940 | −\$195 |
| confirm | −\$57 | \$57,206 | −\$191 |
| liquidity | **−\$3** | **\$26,342** | 0 trades |

`liquidity` is the only filter that materially helps — it lifts `base` to roughly
**breakeven** (−\$3/trade) and cuts drawdown ~38% — but it does **not** make the
strategy positive, and it starves the conservative case entirely. **No filter
earns "enable."**

## 5. Decision rule (as stated before the run)

> Retire if `base` ≤ 0, or only `optimistic` is positive, or the edge does not
> survive the walk-forward folds.

All three retirement conditions are met: `base` is negative, only `optimistic`
is positive, and the walk-forward folds are negative and deteriorating.

## 6. Recommendation

**RETIRE the strategy as configured. Do not proceed toward capital, and do not
enable any switch.** The prior +\$132k/120-day backtest was an artifact of (1)
evaluating every 1-minute bar instead of the live 5-minute cadence, (2) omitting
the governor/caps, and (3) an optimistic fixed spread. Corrected for all three on
real bars, the edge is negative and unstable across time.

**Honest caveats on magnitude (not direction):** option marks are Black-Scholes,
not a real options chain; the execution spread is a premium-based heuristic, not
observed quotes; so the *size* of the loss is model-dependent. But the direction
is robust — even the old assumption yields only +\$40/trade, and the walk-forward
degrades regardless.

**If any further work is authorised, it should be treated as a NEW hypothesis,
not a rescue of this one.** The slices hint at a narrower setup (morning-only,
higher-volatility, SPY, with the liquidity filter) that is *less* bad — but
selecting on those post-hoc is exactly the curve-fitting the validation standard
forbids. Any such variant must be pre-registered and validated on its own
out-of-sample folds with a real quote/liquidity model before it means anything.
I would not bank on it.

**Decisions for the owner:**
1. Confirm retire (turn `STRATEGY` off / set `ENGINE_AUTOSTART_DRY=true` so the
   engine runs signals-only while a replacement is designed), **or**
2. Authorise a scoped, pre-registered re-test of the narrower setup above with a
   real quote model — as new research, not a continuation.

No PR has been opened. No switch has been changed.
