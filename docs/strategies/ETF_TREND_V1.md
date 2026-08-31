# Pre-Registration — `ETF_TREND_V1`

**Diversified ETF time-series momentum / trend (long/cash)**

> **This is a frozen pre-registration (blueprint §4A, §7.1).** It is committed
> BEFORE any result is viewed. Any change to universe, features, parameters,
> rules, costs, or gates creates a **new version** (`ETF_TREND_V2`) and a fresh
> untouched test. Registry state: **REGISTERED** (not yet built, not tradeable).
> Config hash: *(set at implementation)*.

## 1. Hypothesis & economic rationale

Liquid markets exhibit persistent medium-term time-series momentum: an asset
with positive risk-adjusted trailing returns tends to continue positive over the
next weeks–months. Rationale is behavioural (under-reaction, herding) and
structural (risk transfer to trend followers). Holding only assets in positive
trend, and sitting in cash otherwise, should participate in sustained up-trends
and side-step deep drawdowns. **This is a new STOX hypothesis, not a reproduction
of any published futures/long–short result.**

## 2. Universe & point-in-time eligibility

Starting universe: `SPY, QQQ, IWM, EFA, EEM, IEF, TLT, GLD, DBC`. Cash leg:
`BIL` (T-bill ETF; `SHY` alternative). Every symbol must pass current tradability
and fractional-share checks at each rebalance. **No synthetic pre-launch history.**
Per `docs/DATA_AVAILABILITY_AUDIT.md`, Alpaca serves all of these from
2016-01-04, so the test window is **2016-01-04 → present**; the missing 2008–09
period **must be labelled** in every report (the crisis-participation thesis is
therefore untested in v1).

## 3. Data

Adjusted (total-return, `Adjustment.ALL`) daily bars; corporate actions implicit
in the adjustment; a cash/T-bill return series from `BIL`. Dataset fingerprint
(source, symbol set, date range, adjustment mode, row counts) is recorded at run
time and stored with results.

## 4. Signal (frozen)

At each **weekly** rebalance (decision at Friday close):
1. For each ETF compute completed-bar total returns over ~**21, 63, 126, 252**
   trading days (≈1, 3, 6, 12 months).
2. Volatility-normalize each horizon: divide the horizon return by trailing
   realized volatility (stdev of daily returns over the same window,
   annualized).
3. Average the four normalized scores → the ETF's combined trend score.
4. **Hold** an ETF only when its combined score is **> 0**; otherwise its risk
   budget goes to cash (`BIL`).

No look-ahead: only bars with timestamp ≤ the Friday close are used.

## 5. Sizing (frozen)

Inverse-volatility weights across held ETFs (weight ∝ 1/realized-vol), then:
- **25%** maximum target weight per ETF;
- **90%** maximum portfolio gross exposure; **no leverage**;
- **10%** operational cash buffer (in addition to trend-driven cash);
- weights re-normalized after caps; residual to cash.

## 6. Execution (frozen)

Targets generated after Friday's close; paper orders submitted in the **next
regular session** using cost-aware limit orders. **No same-bar signal/fill.**
Target-change bands suppress trades smaller than a fixed notional/weight delta to
avoid churn. Executed through the shared portfolio execution path (Phase 1).

## 7. Exit (frozen)

At the next scheduled weekly rebalance when the target changes (score turns
non-positive, or weights shift past the band). **No tight intraday price stop** —
portfolio/sleeve drawdown is handled by the risk engine, not per-trade stops.

## 8. Cost assumptions (frozen, per §7.3)

Reported under every execution scenario:
- **ideal (diagnostic only, never for approval):** next-open, zero cost;
- **base:** conservative ETF spread + fees + 1-session entry delay + missed/partial fills;
- **conservative:** wider spread/slippage, delayed entry, adverse-gap on rebalance;
- **capacity/rounding:** the actual paper capital, fractional eligibility, $10
  minimum order, and cash drag for each of the four profiles.

## 9. Capital profiles

Runs in all four (`paper_500 … paper_50000`), long-only, no leverage/options.
Small profiles net targets to few fractional positions; rounding/cash-drag is
reported, not hidden.

## 10. Metrics & pass/fail gates (frozen, per §7.5–7.6)

Primary metrics: CAGR, annualized vol, Sharpe, Sortino, Calmar, max drawdown +
duration + recovery, median/worst walk-forward fold, worst rolling 1/3/12-month,
turnover + cost components, gross vs net edge, beta/correlation to SPY and the
other sleeve, regime performance, tail results (best days removed), bootstrap CIs,
Deflated Sharpe + recorded trials. **Win rate is diagnostic only.**

**Advance to extended paper observation only if ALL hold:**
1. Aggregate OOS **base** performance positive after all modeled costs.
2. **Conservative** scenario non-negative in aggregate (or loss small enough that
   a pre-stated portfolio benefit still holds). "Only ideal is positive" = **fail**.
3. Most walk-forward folds positive, **median fold positive**, no unexplained
   deterioration (the fib failure pattern).
4. Not dependent on a few days/symbols/one regime.
5. Drawdown + time-under-water within the pre-registered sleeve budget.
6. Improves the portfolio's OOS risk/return or drawdown after costs.
7. Survives multiple-testing adjustment (Deflated Sharpe) for all attempted variants.
8. Beats cash and a simple passive benchmark at comparable risk (§7.4).

Failure → **Revise** (new version) or **Retired**. Post-hoc profitable slices are
new hypotheses, never rescue filters.

## 11. Validation design (frozen)

Expanding walk-forward: ≥ (available) initial training window, 1-year untouched
validation folds rolled forward, a final recent holdout opened **once**. Overlapping
weekly holding labels embargoed at fold boundaries. Trials in this family are
counted and recorded (this is trial #1).

## 12. Primary failure mode

Whipsaw in directionless markets; long stretches lagging SPY buy-and-hold; the
untested GFC regime; and a ~10-year sample that may be too short for confident
Deflated-Sharpe significance.
