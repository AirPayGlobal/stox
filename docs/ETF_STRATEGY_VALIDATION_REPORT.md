# ETF Strategy Validation Report — `ETF_TREND_V1` + `ETF_RELATIVE_MOMENTUM_V1`

> **STATUS: real-bar backtest complete. Result is POSITIVE but does NOT clear the
> pre-stated benchmark gate.** Adjusted daily bars, 2016-01-04 → 2026-08-31 (138
> months, ~2,680 sessions), SPY+8 ETFs. Fills are simulated via the equity
> execution scenarios; bars are real. No parameters were tuned to these results
> (frozen specs in `docs/strategies/`). No paper capital is allocated and no
> strategy advances to a tradeable state on the strength of this report.

## 1. Headline (base execution, realistic costs)

| Portfolio (base) | CAGR | Sharpe | MaxDD | Calmar | Conservative CAGR |
|---|---|---|---|---|---|
| Combined, paper_500 | 3.2% | 0.56 | 14.8% | 0.22 | 1.9% |
| Combined, paper_2500 | 5.2% | 0.79 | 11.6% | 0.44 | 3.8% |
| Combined, paper_10000 | 5.5% | 0.82 | 12.3% | 0.45 | 4.3% |
| Combined, paper_50000 | 5.6% | 0.83 | 12.3% | 0.45 | 4.3% |
| **SPY buy & hold (benchmark)** | **15.2%** | **0.89** | **33.8%** | 0.45 | — |
| Cash | 0.0% | 0.00 | 0.0% | — | — |

**Read this carefully:** the strategies are **genuinely positive after costs** and
**far less volatile than the market** (drawdown ~12% vs SPY's 34%). But over this
sample they **underperform SPY buy-and-hold on both absolute return (≈1/3 of it)
and risk-adjusted return (Sharpe 0.82 vs 0.89).** Their only edge is drawdown.

## 2. Sleeve attribution (paper_10000, base)

| Sleeve | CAGR | Sharpe | MaxDD | Calmar |
|---|---|---|---|---|
| Trend alone | 5.6% | **0.89** | 10.8% | 0.52 |
| Relative-momentum alone | 6.0% | 0.75 | 15.9% | 0.38 |
| Combined | 5.5% | 0.82 | 12.3% | 0.45 |

**Trend alone matches SPY's Sharpe (0.89) at a third of the drawdown** (Calmar 0.52
vs SPY 0.45) — the most defensible result here. Relative-momentum is weaker
risk-adjusted (0.75) and drags the blend down. The two are correlated (both are
trend exposure), so combining them does not diversify much.

## 3. Against the pre-stated gates (§7.6)

| Gate | Result |
|---|---|
| 1. base positive after costs | ✅ all profiles positive |
| 2. conservative non-negative | ✅ all positive (1.9–4.3%) |
| 3. most/median walk-forward folds positive, no unexplained deterioration | ✅ all four folds positive every profile; middle folds soft (0.4–1.9%) but not negative — the fib failure pattern is absent |
| 4. not dependent on a few days/regime | ✅ removing the best 10 days keeps ~2/3 of the return (ex-best-10 52.5% of a 77.5% total) |
| 5. drawdown within budget | ✅ ~12% base, ~15% conservative |
| 6/8. beats cash **and a simple passive benchmark at comparable risk** | ❌ **fails vs SPY** — lower absolute return and lower Sharpe; only drawdown is better |
| 7. survives multiple-testing adjustment (Deflated Sharpe) | ◑ **computed; passes at scale** — DSR **0.96** at paper_10000/50000 (≥0.95), **0.81 / 0.95** at paper_500 / paper_2500; trend-alone DSR 0.99. But the trial family is narrow (see below), so this is a **lower-bound** deflation, not the full correction |

Six of the eight gates pass. The **benchmark gate (6/8) fails**. Gate 7 is now
computed and passes for the larger profiles, but with the caveat below.

### Deflated Sharpe (gate 7) detail

DSR = P(true Sharpe > the multiple-testing threshold), after adjusting for
return skew/kurtosis and track length (Bailey & López de Prado):

| Result | DSR | vs-zero PSR | verdict (0.95) |
|---|---|---|---|
| Combined, paper_10000 | 0.96 | 1.00 | PASS |
| Combined, paper_50000 | 0.96 | 1.00 | PASS |
| Combined, paper_2500 | 0.95 | 0.99 | borderline |
| Combined, paper_500 | 0.81 | 0.96 | FAIL (rounding/min-notional drag) |
| Trend alone, paper_10000 | 0.99 | 1.00 | PASS |

**What this does and does not say.** It says the ETF edge at $10k+ is unlikely to
be pure luck-under-multiple-testing *within this run* — a modestly encouraging
signal that the Sharpe is not noise. It does **not** rescue the benchmark
failure. And the deflation is **optimistic**: the trial family here is only the
12 profile×scenario variants of one strategy — highly correlated, low-variance,
so a lenient threshold (~0.28 annual Sharpe). The honest multiple-testing count
should include the whole research program (fib, ORB, sweep and their variants),
which would raise the bar. Treat gate 7 as **provisionally passed at scale, not
conclusively.**

## 4. Why this sample is the wrong test for trend

Per `docs/DATA_AVAILABILITY_AUDIT.md`, Alpaca serves only 2016→present — a period
that is **overwhelmingly a US-equity bull market and omits 2008–09**. Trend-following
earns its reputation in exactly the crises this window excludes; any cash-rotating,
low-drawdown strategy will underperform buy-and-hold in a decade-long bull run. So
this is a **structurally adverse, underpowered test** of the trend thesis, not a
verdict on it. The result is consistent with the literature (trend = lower return,
much lower drawdown, crisis participation) — but the crisis participation is
untestable on this data.

## 5. Recommendation

**Do NOT allocate paper capital and do NOT advance either strategy to a tradeable
lifecycle state yet.** They are not broken — they are positive, low-drawdown, and
stable across folds — but they do not clear the benchmark gate on this sample, and
the one regime that would justify them (a crisis) is absent from the data.

Concretely:
1. **Keep both REGISTERED.** Record this backtest outcome in the registry (done).
   No curve-fitting to beat SPY — a post-hoc winning variant is a new hypothesis,
   not a rescue (§7.6).
2. **Get the decisive data.** Procure a secondary long-history adjusted daily
   source (Tiingo/Stooq/Norgate) covering 2000–02 and 2008–09, used for research
   only, before any allocation decision. That is the test that matters for trend.
3. **Deflated Sharpe is now implemented** (`portfolio/research_stats.py`) and the
   result passes at $10k+ within this run — but on a narrow, correlated trial
   family. Before trusting it, widen the trial count to the whole research
   program (fib/ORB/sweep + variants) so the multiple-testing bar is honest.
4. **Optional, safe:** run both in **shadow paper** (no capital) so live-vs-backtest
   divergence can be tracked via the portfolio Command Centre while the above is
   sorted. Shadow mode allocates nothing.
5. **If a standalone sleeve is ever promoted, it should be trend-alone**, not the
   blend — it is the only variant that matches the benchmark risk-adjusted while
   cutting drawdown by two-thirds.

## 6. How to reproduce

With Alpaca data credentials in the environment:
`python -m portfolio.validate_etf --start 2016-01-01`. Bars are real; fills are simulated; every result is labelled accordingly.
