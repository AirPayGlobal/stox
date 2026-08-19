# Fib Gold-Zone Pullback — Validation Report

> **STATUS: framework + SYNTHETIC illustrative pass. NOT a live result and NOT
> a validation of a real edge.** The numbers in §4 were produced on
> deterministic *synthetic* bars (a seeded random walk) because the environment
> that generated this report had no market-data credentials. They exist only to
> exercise the machinery and illustrate the decision rule. Authoritative numbers
> require re-running the harness on real SPY/QQQ bars — see §6. Every result here
> is **simulated** (Black-Scholes option marks); none is live performance, and
> no profitability claim is made.

## 1. Purpose

Establish whether the Fib strategy's backtest edge survives **realistic
execution friction** and **full production constraints**, before any decision to
commit capital. This directly answers the review concern that the strategy's
prior backtest (a) evaluated every 1-minute bar while the live engine scans every
5 minutes, (b) assumed a single fixed \$0.02 half-spread, (c) omitted the daily
governor and other production limits, and (d) leaned on a few exceptional days.

## 2. What changed (method)

The validation harness (`backtest/fib_sim.py`, `backtest/validation.py`) runs the
Fib strategy under **engine parity**:

- **Scan-cadence parity** — entries evaluated every `FIB_SCAN_BARS` bars (5 by
  default = the live 5-minute cadence), not every bar.
- **Completed-bar logic** — the decision at a scan bar uses only bars up to it.
- **Account-level daily governor** — profit-protection floor, daily max-loss
  halt, `MAX_TRADES_PER_DAY`, `MAX_CONCURRENT_POSITIONS` across *both* symbols,
  per-symbol cooldowns and consecutive-loss cutoff, the `FIB_MAX_HOLD_MINUTES`
  time stop, and the `FLATTEN_TIME` end-of-day flatten.
- **Realistic execution** — the single fixed spread is replaced by named
  scenarios (`backtest/execution.py`) modelling bid/ask spread, slippage,
  delayed fills, and unfilled/partial fills. Deterministic (seeded per order).
  **Mid-price fills are never used** except the explicit `mid` diagnostic.

All new behaviour is config-driven and defaults to production-safe settings. The
optional selectivity filters (§5) all default **OFF**.

## 3. Execution scenarios

| Scenario | Spread crossed | Slippage | Delay | No-fill / partial | Purpose |
|---|---|---|---|---|---|
| `baseline` | flat \$0.02/side | none | none | never | reproduce the *old* assumption for comparison |
| `optimistic` | ½ modelled spread | none | none | never | best plausible fills |
| `base` | full ½-spread | \$0.01 | 1 bar | ~3% / 5% | realistic central estimate |
| `conservative` | full ½-spread | \$0.03 | 1 bar | ~8% / 10% | stress case |
| `mid` | none (fills at mid) | none | none | never | **diagnostic only** |

The modelled spread widens for cheaper contracts (a \$0.20 option is
proportionally wider than a \$5 option), which is where 0DTE friction bites most.

## 4. Illustrative results — SYNTHETIC DATA (90 sessions, SPY+QQQ, \$100k)

> Reminder: synthetic random-walk bars. Read the **shape**, not the magnitudes.

**Execution-scenario comparison (base strategy, filters off):**

| | Trades | Win% | Total P&L | Exp/trade | Median day | MaxDD |
|---|---|---|---|---|---|---|
| baseline | 825 | 50% | \$118,188 | \$143 | \$1,066 | \$3,576 |
| optimistic | 832 | 51% | \$142,298 | \$171 | \$1,256 | \$1,915 |
| base | 780 | 45% | \$48,031 | \$62 | \$404 | \$9,708 |
| conservative | 669 | 40% | −\$38,130 | −\$57 | −\$872 | \$46,732 |

**The single most important finding is structural, not numerical:** moving from
the old fixed-spread assumption (`baseline`) to a realistic (`base`) and then
stressed (`conservative`) execution model collapses a large positive result to
roughly breakeven and then to a loss. A thin-edge, high-frequency profile is
**dominated by execution assumptions** — exactly the review's concern.

**Tail dependence (base):** single best day ≈ 9% of total; P&L excluding the best
5 days still positive on synthetic data. (The real question is whether that holds
on real bars, where the prior study showed one day = up to 53% of a month.)

**Walk-forward (frozen params, 4 contiguous unseen folds, base):** expectancy per
trade drifts \$139 → \$99 → −\$25 → \$27 across folds — i.e. **not stable** even
on synthetic data. Stability across unseen periods is the bar; this run does not
clear it.

**0DTE vs 1DTE (base):** 0DTE ≈ +\$48k vs 1DTE ≈ −\$67k on synthetic bars —
consistent with 1DTE carrying more premium/vega risk for this hold horizon.

**Filter effects (each alone vs none, base):** `liquidity` marginally improved
expectancy (\$62 → \$69) while cutting trades; `trend` and `confirm` cut trades
and *reduced* expectancy here; `vol_regime` and `time_of_day` were no-ops because
their bands default to wide-open (they require explicit parameters to bite). **No
filter earned "enable" on this run.**

## 5. Decision rule (how we will read the real report)

A filter or the strategy itself is judged on **out-of-sample expectancy and
drawdown**, never on total P&L alone:

- **Continue paper testing** if, on real bars, the `base` scenario keeps a
  positive per-trade expectancy that survives the walk-forward folds *and* the
  removal of the best 3–5 days, and `conservative` is not catastrophic.
- **Revise** (tighten `MAX_SPREAD_PCT`/liquidity filter, cut frequency, restrict
  regimes/ToD) if `base` is marginal and a *specific* filter improves OOS
  expectancy and drawdown on unseen folds.
- **Retire** if `base` is ≤ 0 or only `optimistic` is positive, or the edge does
  not survive the walk-forward folds.

## 6. How to produce the authoritative numbers

In an environment with Alpaca market-data credentials:

```bash
python -m backtest.validation --days 120 --equity 100000        # real bars
python -m backtest.validation --days 120 --synthetic            # this illustrative run
```

The real run prints the same tables under a "SIMULATED fills on real bars"
header. Compare `base`/`conservative` to `baseline`, check the walk-forward
folds, and apply §5.

## 7. Recommendation

**Provisional: CONTINUE PAPER TESTING — do not commit capital, do not enable any
optional filter yet.** The harness demonstrates the strategy is highly sensitive
to execution assumptions and that its edge is not obviously stable across unseen
windows (on synthetic data it is not). That is a caution flag, not a verdict.

The next gate is a **real-bar run of this harness** (§6) plus **live paper trades**
compared against it via the existing `/api/compare` view. Concretely:

1. Run the real-bar validation; read `base` vs `conservative` and the folds.
2. Collect ≥ 20–30 live paper fib trades; confirm the live fill quality matches
   the `base` scenario's assumptions (spread %, delay, fill rate) — if real fills
   look more like `conservative`, weight that scenario.
3. Only if `base` survives §5 on both fronts should sizing or capital be discussed.

Decisions requiring sign-off before flipping any switch: enabling any
`FIB_FILTER_*`, tightening `MAX_SPREAD_PCT`, or changing `FIB_SCAN_BARS`. None are
enabled by this work.
