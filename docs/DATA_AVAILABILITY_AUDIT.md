# Data-Availability Audit — Multi-Strategy Blueprint

*Snapshot: 2026-08-31. Probed against the live Alpaca data account attached to
STOX. Purpose: determine which blueprint strategies are actually buildable with
the data we have, before committing to the architecture. Facts below are from
direct API probes, not assumptions.*

## 1. What Alpaca gives us (probed)

**Daily bars** (`StockHistoricalDataClient`, `TimeFrame.Day`):

| Fact | Finding |
|---|---|
| Earliest daily bar | **2016-01-04** for every ETF probed (SPY, QQQ, IWM, EFA, EEM, IEF, TLT, GLD, DBC) |
| History length | ~**10.6 years** (2016-01-04 → present) |
| Adjustment | `RAW`, `SPLIT`, `ALL` all supported → **total-return (dividend+split) adjusted bars available** |
| Cash / T-bill proxies | `BIL`, `SHV`, `SHY` from 2016; `SGOV` only from 2020-05-28 |

**Intraday bars:** 1-minute history ~138 sessions (used for the fib validation).
Not relevant to the daily/weekly/monthly strategies in the blueprint.

**Key point:** the 2016-01-04 floor is Alpaca's data horizon, **not** the ETFs'
real launch dates (SPY 1993, EEM 2003, etc.). Alpaca simply does not serve
pre-2016 history on this account. There is no pre-2016 data to "respect launch
dates" against — everything is uniformly truncated to 2016.

## 2. Implication for §7.2 (the ≥15-year / 2008–09 requirement)

**Not satisfiable from Alpaca alone.** The blueprint asks for ≥15 years including
2008–09, 2020, and 2022. We have **2016→now**, which includes 2020 (COVID) and
2022 (rate-hike bear) but **excludes the 2008–09 GFC** — precisely the period
where trend-following earns its "crisis alpha" reputation.

Consequences:
- A trend/momentum backtest on Alpaca data covers ~10 years dominated by a long
  bull market + two drawdowns. That is enough for a *preliminary* walk-forward
  read but **cannot test the crisis-participation thesis** that is trend's main
  economic rationale.
- Options if the 15-year bar is firm: add a **secondary long-history adjusted
  daily source** (e.g. Tiingo, Stooq, Norgate) used **only** for the research
  backtest, kept strictly separate from the Alpaca live/paper path, with a
  documented dataset fingerprint. This is a build decision, not a detail.

## 3. Buildability by strategy

| Blueprint strategy | Data required | Status with our data |
|---|---|---|
| **ETF_TREND_V1** (#1) | adjusted daily bars + cash return | ✅ **Buildable** on 2016→now. Limitation: no 2008–09; label it. |
| **ETF_RELATIVE_MOMENTUM_V1** (#2) | adjusted daily bars, PIT eligibility | ✅ **Buildable** on 2016→now (all candidate ETFs exist since 2016, so the PIT-eligibility concern is largely moot in-window). Same history limitation. |
| **STOCK_QVM_V1** (#3) | point-in-time fundamentals, filing dates, historical constituents, delistings | ❌ **Blocked** — Alpaca does not provide point-in-time fundamentals or historical index membership. Needs a paid vendor (Compustat/CRSP/Sharadar-grade). |
| **PEAD_LONG_V1** (#4) | PIT estimates + actuals + exact release timestamps | ❌ **Blocked** — no earnings-surprise/timestamp data on this account. |
| **PAIRS_DISTANCE_V1** (#5) | PIT industry, borrow/shortability, borrow cost | ❌ **Blocked** — no borrow/shortability data; paper shorting realism is limited. |
| **LIQUID_SWING_REVERSAL_V1** (#6) | adjusted daily bars + realistic spread/slippage | ✅ Buildable (shadow-only per blueprint). |

## 4. Recommendation

1. **v1 is realistically ETF_TREND_V1 + ETF_RELATIVE_MOMENTUM_V1 only.** Both are
   buildable now on Alpaca 2016→present with total-return-adjusted bars and a
   `BIL`/`SHY` cash leg. Everything data-gated (#3–5) stays REGISTERED/blocked
   until a fundamentals/constituents/borrow vendor is procured — that is a
   budget decision for the owner, not something code can solve.
2. **Decide the history question before the OOS run.** Either (a) accept a
   ~10-year Alpaca sample and *explicitly label* the missing GFC in every report,
   or (b) add a secondary long-history daily source for research only. My
   recommendation: start with (a) for the preliminary walk-forward (cheap, no new
   dependency), and treat (b) as a follow-up if the strategies clear the
   preliminary bar and the crisis-behaviour test becomes decision-relevant.
3. **Cash leg = `BIL`** (T-bill ETF, full 2016 history) for the trend strategy's
   "allocate to cash" branch; `SHY` is an acceptable alternative. Avoid `SGOV`
   for the backtest (only 2020+).
4. **No synthetic pre-launch / pre-2016 history** may be silently inserted
   (blueprint §3, §6.6). Since Alpaca serves nothing pre-2016, the backtest
   window is simply 2016→now unless a labelled secondary source is added.

## 5. How these facts were obtained

Direct probes via `StockHistoricalDataClient.get_stock_bars` with
`start=2004-01-01` for each symbol and each `Adjustment` mode; the earliest
returned `timestamp` is the reported floor. Credentials were used read-only and
were not written to any committed file.
