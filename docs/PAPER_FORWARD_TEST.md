# Paper forward-test: the `positive` set (orb + sweep + fib)

A zero-money forward test of the orb+sweep+fib combo on the **Alpaca paper
account**, so real fills (real spreads, real slippage) can be compared against
the backtest — the one honest test the simulators can only approximate.

> This runs RETIRED strategies deliberately, for evaluation only. The engine
> permits it **only on the paper account**; with `ALPACA_MODE=live` the same
> config is forced to signals-only (no real-money orders). See the guard in
> `api/server.py` (`_autostart`, `/api/start`).

## What it does

- `STRATEGY=positive` makes the live engine scan **orb, sweep and fib together**
  (swing is excluded — it's negative even in the backtest).
- All three share the account-level governor, caps, cooldowns and safeguards.
- Trades are tagged by strategy, so the dashboard's **Live vs backtest** panel
  (`/api/compare?strategy=positive`) lays the live paper record beside the
  realistic (`base`) backtest and flags drift.

## Deploy (Railway env — paper account only)

Requires this branch merged/deployed (PR #43). Then set:

```
STRATEGY=positive
ALPACA_MODE=paper            # MUST be paper — live is blocked for retired sets
ENGINE_AUTOSTART=true
ENGINE_AUTOSTART_DRY=false   # actually place paper orders
UNDERLYINGS=SPY,QQQ
```

Use **paper** Alpaca API keys (the `PK...` key is a paper key). On boot the log
will read: *"PAPER forward-test: STRATEGY 'positive' includes RETIRED
['orb','sweep','fib'], trading the PAPER account (real paper fills, NO real
money)."*

## Reading the result (after ~2–4 weeks / 20–30+ trades per strategy)

Open **Live vs backtest** and compare `positive` (and each strategy alone):

- The **base** backtest already says this is roughly breakeven-to-negative; the
  **conservative** backtest says −$80k (60d) / −$200k (120d). The forward test
  tells us which end reality sits at.
- Watch **expectancy/trade** and the **drift verdict**. If live tracks the
  `base` backtest and stays positive, that's a real (surprising) signal. If it
  tracks `conservative` or worse — the expected outcome — the combo is confirmed
  dead and we stop.
- **orb** is the one to watch: its backtest edge is entirely spread-dependent,
  so its live paper fills are the fastest tell.

## Stopping

Set `STRATEGY` back to a non-retired value (or `ENGINE_AUTOSTART_DRY=true`) to
stop. Nothing here can ever touch real money — the live-money guard is
unconditional.
