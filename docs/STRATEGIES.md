# Strategy notes: systemizing the trader transcripts

This documents how the transcribed trader strategies were turned into the
`sweep` strategy (`analysis/sweeps.py` + engine integration), what was
deliberately left out, and why.

## What the transcripts actually describe

Stripped of jargon, all five speakers describe variations of ONE pattern —
the liquidity-sweep reversal ("manipulation candle"):

> Price takes out an obvious prior low (or high) — the previous 4-hour
> candle's low, the previous session's low — then *closes back* in the
> opposite direction. Trade the reversal with a stop beyond the sweep wick
> and a target at 1:2 risk:reward.

| Transcript claim | Implementation |
|---|---|
| "Sweep below the previous four-hour candle's low, then close higher → trade higher" | `sweep_reclaim()`: last completed HTF candle trades below previous candle's low AND closes bullish AND closes back above the swept level (mirror for shorts). `SWEEP_TIMEFRAME_MINUTES` selects the timeframe (default 60-min — the intraday analog; 240 for true 4H, but that fires ~0–1 times/day and mostly outside a day-trading window). |
| "Session highs and lows … high or low of the day is pretty much in" | `prev_day_level_sweep()`: same reclaim logic against the previous day's high/low, checked on completed intraday bars (`SWEEP_PREV_DAY_LEVELS=true`). |
| "Wait for those highs and lows to sweep, then take a fair value gap reversal, target 1–2 RR" | `find_fvg()` detects 3-candle imbalances. With `SWEEP_ENTRY=retrace`, after a signal the engine waits for a pullback into the reclaim leg's FVG (or the manipulation candle's midpoint if no FVG) before entering — same stop, better price, higher realized RR. Setups expire after `SWEEP_RETRACE_EXPIRY_MIN` and are invalidated if the stop level is hit first. |
| "Stop loss right above [the wick]" | Stop = the sweep candle's wick extreme, tracked on the **underlying** price. Exits `UL_SL`/`UL_TP` in the engine. |
| "Go for a two to one" | Target = entry ± `SWEEP_RR` × stop distance (default 2.0). |
| "Entry at the close of the four-hour candle … or bring your entry higher [into the candle] for better risk reward" | `SWEEP_ENTRY=close` (default) vs `SWEEP_ENTRY=retrace`. |
| Lux Algo "trend signal filter" (previous bar same direction) | `SWEEP_TREND_FILTER`: requires the swept candle to have closed *against* the signal (a down candle before a bullish reclaim) — i.e. the sweep really manipulated a move in the other direction. Off by default; toggle and backtest. |
| "Rectangle setup" (anchored range, strength/weakness, imbalance, fresh range) | **Not implemented as a separate strategy.** Its mechanical core is the same building blocks: "anchored by a liquidity sweep" = our reclaim, "imbalance" = our FVG, "weakness/wick" = our failed-close test. The remaining parts (key levels, "good price position", "fresh" ranges) are discretionary judgments the speaker never defines precisely enough to code without inventing rules he didn't state. |

## How it maps to options

The transcripts are futures/forex-framed (points, pips). Here the signal
fires on the underlying (SPY/QQQ) and the trade is a 0–1 DTE call (LONG
sweep) or put (SHORT sweep), selected and sized exactly like the ORB
strategy — but exits are driven by the **underlying** hitting the stop/target
levels, because that's how the transcribed strategies define risk. A wide
premium stop (`SWEEP_DISASTER_STOP_PCT`, −60%) remains as a backstop, and
everything still flattens at `FLATTEN_TIME`.

Sizing uses delta to translate the underlying stop distance into expected
option loss: `risk/contract ≈ |delta| × stop_distance × 100`, capped at
`RISK_PER_TRADE_PCT` of equity.

## Honest caveats

- These are **influencer strategies, not verified edges**. "You will become
  a profitable trader" is a marketing claim; none of the speakers present
  audited results. The value of systemizing them is that they now CAN be
  tested: `python backtest/run_backtest.py --strategy sweep --days 90`.
- The pattern has a real economic story (stop-hunts through obvious levels
  reverting) but also a well-known failure mode: in a genuine breakout the
  "sweep" doesn't reclaim, or reclaims and then continues through your stop.
  The win rate will not be what the videos imply.
- Backtest first, then paper trade. If the sweep strategy underperforms ORB
  over a meaningful sample, turn it off (`STRATEGY=orb`) rather than
  romanticizing it.
