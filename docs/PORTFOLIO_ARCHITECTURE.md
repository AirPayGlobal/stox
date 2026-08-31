# Multi-Strategy Portfolio Architecture (Phase 1)

*Status: Phase 1 foundation implemented and unit-tested. No strategy is
implemented, enabled, or run; nothing trades real money. This is the operating
system the validated strategies will plug into.*

## Data flow (one rebalance)

```
strategy sleeves            portfolio.engine.rebalance()
      │  emit
      ▼
StrategyIntent[]  ──►  allocator.plan()  ──►  risk.evaluate()  ──►  execution  ──►  attribution
 (targets, not      (net targets, caps,     (system/portfolio/    (scenario      (deterministic
  orders)            bands, rounding)         sleeve/position)      fills)         back to sleeve books)
```

A strategy produces **target intents**, never orders. The allocator nets them
into one account order per symbol under the capital profile's caps. The risk
engine filters those orders through four levels. Execution simulates realistic
fills. Fills are attributed back to each sleeve's virtual book so P&L stays
traceable even when sleeves share a position.

## Modules

| Module | Role | Blueprint |
|---|---|---|
| `strategy/registry.py` | Lifecycle registry; source of truth for what may trade. Fib/ORB/Sweep RETIRED; ETF strategies REGISTERED. | §6.1, §8, §9 |
| `portfolio/intent.py` | `StrategyIntent` — versioned target with provenance; confidence is reporting-only. | §6.1 |
| `portfolio/profiles.py` | Four immutable paper-capital profiles (500/2500/10000/50000) with fixed sleeve budgets and caps. | §5, §6.5 |
| `portfolio/preregistration.py` | Content-hashed pre-registration artifact + dataset fingerprint. | §7.1 |
| `portfolio/book.py` | Virtual sleeve books + deterministic fill attribution; restart-safe persistence. | §6.2 |
| `portfolio/allocator.py` | Net targets → account orders; symbol/gross/cash/position caps, bands, rounding. Fixed budgets, no return-chasing selector. | §6.3 |
| `portfolio/risk.py` | Four-level risk engine (system → portfolio → sleeve → position). | §6.4 |
| `portfolio/execution.py` | ETF execution scenarios (ideal/base/conservative), partial/unfilled, rounding, min notional. | §7.3 |
| `portfolio/engine.py` | `rebalance()` — end-to-end orchestration shared by backtest and paper. | §6, §7.3 |
| `portfolio/contracts.py` | UI/API contract builders for the four §8 views. | §8 |

## Key invariants (enforced + tested)

- **Strategies never place orders** — they only emit `StrategyIntent`s.
- **Nothing is tradeable until validated.** Sleeve tradeability comes from the
  registry; the ETF strategies are REGISTERED (not PAPER), so the risk engine
  pauses them and the engine trades nothing. (Tests override this only to
  exercise the plumbing.)
- **Shared positions execute once**, with P&L still attributable per sleeve.
- **A sleeve stop pauses only that sleeve**; a system fault halts everything;
  portfolio drawdown blocks new buys but not sells.
- **No mid-price fills** except the explicit `ideal` diagnostic scenario.
- **Restart-safe**: sleeve books persist; a duplicate intent batch is detected
  and halted after a restart.

## API contracts (§8, PAPER-only)

`/api/portfolio/profiles`, `/api/portfolio/command-centre`,
`/api/portfolio/registry`, `/api/portfolio/lab`, `/api/research/leaderboard`.
Live sections are empty placeholders until a strategy passes validation — no
invented numbers. `/api/start` refuses to live-trade a retired strategy; boot
auto-starts signals-only when the configured strategy is retired.

## What Phase 1 deliberately does NOT do

- No strategy logic (`ETF_TREND_V1` / `ETF_RELATIVE_MOMENTUM_V1` are docs-only
  pre-registrations — `docs/strategies/`).
- No optimization or parameter search.
- No wiring to a live data feed for a portfolio run.

## Next (Phase 2 — separate, on approval)

1. Implement `ETF_TREND_V1` and `ETF_RELATIVE_MOMENTUM_V1` as intent producers.
2. Feed real daily bars (Alpaca 2016→now; see `docs/DATA_AVAILABILITY_AUDIT.md`)
   into `rebalance()` across all four profiles under the full validation
   protocol (§7), with the final holdout opened once.
3. Only a strategy that clears the §7.6 gates moves to a tradeable (PAPER)
   lifecycle state — a deliberate, reviewed decision, never automatic.
