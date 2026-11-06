# Pre-Registration — `ETF_RELATIVE_MOMENTUM_V1`

**ETF cross-sectional momentum / relative strength (long/cash)**

> **Frozen pre-registration (blueprint §4B, §7.1)**, committed BEFORE any result.
> Any change to universe, features, parameters, rules, costs, or gates creates a
> **new version** and a fresh untouched test. Registry state: **REGISTERED** (not
> built, not tradeable). Config hash: *(set at implementation)*.

## 1. Hypothesis & economic rationale

Across a cross-section of liquid segments, recent relative winners tend to
outperform recent relative losers over the following weeks–months (cross-sectional
momentum). Rationale is behavioural under-reaction and flows toward leadership.
Requiring **positive absolute momentum** as a gate avoids holding the "best of a
falling market." **New STOX hypothesis, not a reproduction of a published long–short
portfolio** — the long-only, gated ETF form needs its own validation and can crash
in sharp reversals.

## 2. Universe & point-in-time eligibility

A fixed set of broad-market and sector ETFs (starting set: `SPY, QQQ, IWM, EFA,
EEM, TLT, IEF, GLD, DBC`, extensible to liquid SPDR sectors `XLK, XLF, XLE, XLV,
XLI, XLY, XLP, XLU, XLB, XLRE` subject to the data audit). Eligibility calendar is
**point-in-time**: an ETF cannot be selected before it exists in the data. Per
`docs/DATA_AVAILABILITY_AUDIT.md` the effective window is **2016→present**; any
sector ETF with a later data start is eligible only from its first bar. Cash leg:
`BIL`.

## 3. Data

Total-return-adjusted (`Adjustment.ALL`) daily bars; cash return from `BIL`.
Dataset fingerprint recorded with results.

## 4. Signal (frozen)

At **month-end** (decision at the last session's close):
1. For each eligible ETF compute **12-minus-1-month** and **6-minus-1-month**
   total returns (skip the most recent 21 sessions to avoid short-term reversal).
2. Volatility-normalize each and average → relative-strength score.
3. Rank; **select the top 2–3** ETFs.
4. A selected ETF is held **only if its own absolute trend score is > 0** (same
   volatility-normalized combined score as `ETF_TREND_V1` §4). Unused allocation
   → cash (`BIL`).

## 5. Sizing (frozen)

**Equal-risk** across the selected ETFs (inverse-vol), not equal-dollar:
- **35%** maximum target weight per ETF;
- **90%** maximum sleeve gross exposure; no leverage;
- residual to cash.

## 6. Execution (frozen)

Rebalance in the **first regular session after month-end**; cost-aware limit
orders; **target bands** to avoid tiny trades; no same-bar signal/fill. Shared
portfolio execution path (Phase 1).

## 7. Exit (frozen)

An ETF is dropped when it falls out of the selected rank, fails the absolute
momentum gate, or becomes non-tradable — actioned at the next monthly rebalance.
No per-trade price stop; risk engine governs drawdown.

## 8. Cost assumptions (frozen)

Same four scenarios as `ETF_TREND_V1` §8 (ideal-diagnostic / base / conservative /
capacity-rounding), reported for all four capital profiles.

## 9. Capital profiles

All four profiles, long-only. At `paper_500` the top-2–3 selection plus the
ETF_TREND sleeve are **netted** to a handful of fractional positions; rounding and
cash drag are reported.

## 10. Metrics & pass/fail gates

Identical metric set and the **same 8 pre-stated gates** as `ETF_TREND_V1` §10
(OOS base positive after costs; conservative non-negative; median/most folds
positive with no unexplained deterioration; not tail/regime/symbol dependent;
drawdown within budget; improves portfolio OOS profile; survives Deflated Sharpe;
beats cash + passive benchmark). Win rate diagnostic only. Momentum-specific
attention to **crash months** (sharp reversals) in the worst-fold and
tail-removal analysis.

## 11. Validation design

Expanding walk-forward with 1-year untouched folds and a single final holdout;
monthly holding labels embargoed at fold boundaries; trials counted (trial #1 in
this family). Because rebalances are monthly, the ~10-year window yields ~120
decisions — **flagged as a small decision count** for significance testing.

## 12. Primary failure mode

Momentum crashes (violent leadership reversals), concentration in correlated
sectors, and — given only ~120 monthly rebalances on Alpaca data — limited
statistical power. Correlation/overlap with `ETF_TREND_V1` must be reported so the
allocator does not double-count the same trend exposure.
