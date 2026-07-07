"""
STOX Options — intraday trading engine.

Loop (every Config.LOOP_SECONDS while the market is open):
  1. mark open positions and fire exits — premium target/stop (ORB trades),
     underlying-level target/stop (sweep trades), time stop, signal
     reversal, end-of-day flatten, loss-halt flatten
  2. re-evaluate the daily governor (profit target lock, max-loss halt)
  3. check pending retracement setups (sweep strategy, SWEEP_ENTRY=retrace)
  4. every Config.SCAN_SECONDS, scan the underlyings for entries

Two strategies (Config.STRATEGY: "orb" | "sweep" | "both"):
  * orb   — opening-range-breakout momentum confluence (analysis/signals.py);
            exits on premium (+TAKE_PROFIT_PCT / -STOP_LOSS_PCT) and time
  * sweep — liquidity-sweep reversal (analysis/sweeps.py): higher-timeframe
            sweep-and-reclaim candles and previous-day high/low sweeps;
            stop beyond the sweep wick, target at SWEEP_RR times the risk,
            both defined on the UNDERLYING price

Day trading only: every position is closed by Config.FLATTEN_TIME ET.
"""
from __future__ import annotations

import time
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from analysis.htf import completed_bars, resample_bars
from analysis.signals import Signal, generate_signal
from analysis.sweeps import (
    SweepSignal,
    find_fvg,
    level_sweep,
    overnight_range,
    prev_day_level_sweep,
    rr_target,
    session_range,
    sweep_reclaim,
)
from config import Config
from data.market_data import get_intraday_bars, get_latest_price, get_today_bars
from data.options_data import _parse_occ, get_option_mid
from options.contracts import select_contract
from trading.broker import (
    buy_option,
    close_option_position,
    get_account,
    get_option_positions,
    is_market_open,
)
from trading.positions import PositionBook
from trading.risk import RiskManager
from utils.logger import get_logger

logger = get_logger("engine")

ET = ZoneInfo("America/New_York")


def _parse_hhmm(s: str) -> dtime:
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))


class TradingEngine:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.risk = RiskManager()
        self.book = PositionBook()
        self.running = False
        self.last_scan = 0.0
        self.last_reconcile = 0.0
        self.last_signals: dict[str, dict] = {}
        self.last_equity: float = 0.0
        # sweep bookkeeping
        self._acted_sweeps: set[str] = set()          # candle dedupe keys
        self.pending: dict[str, dict] = {}            # underlying -> retrace setup
        mode = "DRY RUN" if dry_run else Config.ALPACA_MODE.upper()
        logger.info(
            f"Engine ready | mode={mode} | strategy={Config.STRATEGY} | "
            f"underlyings={','.join(Config.UNDERLYINGS)}"
        )

    # ================================================================= Loop
    def run(self) -> None:
        self.running = True
        logger.info("Engine loop started")
        if not self.dry_run:
            try:
                self.reconcile_with_broker()
            except Exception as exc:
                logger.error(f"Broker reconciliation failed: {exc}", exc_info=True)
        while self.running:
            try:
                self.tick()
            except Exception as exc:
                logger.error(f"Tick failed: {exc}", exc_info=True)
            time.sleep(Config.LOOP_SECONDS)

    def stop(self) -> None:
        self.running = False

    def reconcile_with_broker(self) -> None:
        """
        Align the position book with the broker's actual option positions.
        Runs at engine start so a restart that lost (or never had) state
        cannot leave positions unmanaged:
          * broker position unknown to the book -> ADOPT it (broker average
            entry as the premium basis) so stops/flatten/halt govern it
          * book trade the broker no longer holds -> close it as EXTERNAL
        """
        broker_positions = get_option_positions()
        known = {t.symbol for t in self.book.open_trades}

        for symbol, pos in broker_positions.items():
            if symbol in known:
                continue
            strike, opt_type = _parse_occ(symbol)
            if strike is None:
                logger.warning(f"Unrecognized option symbol at broker: {symbol}")
                continue
            underlying = symbol[:-15]
            direction = "LONG" if opt_type == "call" else "SHORT"
            self.book.open(
                symbol, underlying, direction, pos["qty"], pos["avg_entry"],
                strategy="orb",  # premium-based exits govern adopted trades
            )
            logger.warning(
                f"ADOPTED orphaned broker position: {pos['qty']}x {symbol} "
                f"@ ${pos['avg_entry']:.2f} — now managed"
            )

        for trade in list(self.book.open_trades):
            if trade.symbol not in broker_positions:
                mark = get_option_mid(trade.symbol) or trade.entry_premium
                self.book.close(trade.symbol, mark, "EXTERNAL")
                logger.warning(
                    f"Book trade {trade.symbol} not held at broker — closed as EXTERNAL"
                )

    def tick(self) -> None:
        if not is_market_open():
            return

        # Periodic broker re-sync: adopts positions the book doesn't know
        # (e.g. opened by an old container during a deploy cutover) so they
        # come under stop/flatten management within minutes, not never.
        if not self.dry_run and time.time() - self.last_reconcile >= Config.RECONCILE_SECONDS:
            self.last_reconcile = time.time()
            try:
                self.reconcile_with_broker()
            except Exception as exc:
                logger.error(f"Periodic reconciliation failed: {exc}")

        account = get_account()
        equity = self.last_equity = account["equity"]
        self.risk.ensure_today(equity)

        now_et = datetime.now(ET).time()
        in_flatten_window = now_et >= _parse_hhmm(Config.FLATTEN_TIME)

        self.manage_positions(flatten_all=in_flatten_window or self.risk.must_flatten())
        self.risk.update_governor(equity)

        if in_flatten_window:
            self.pending.clear()
            return

        self.check_pending(equity)

        if time.time() - self.last_scan >= Config.SCAN_SECONDS:
            self.last_scan = time.time()
            self.scan_for_entries(account)

    # ================================================================= Exits
    def manage_positions(self, flatten_all: bool = False) -> None:
        ul_prices: dict[str, float | None] = {}
        for trade in list(self.book.open_trades):
            mark = get_option_mid(trade.symbol)
            if mark is None:
                continue

            reason = None
            if flatten_all:
                reason = self.risk.flatten_reason()
            elif trade.stop_underlying or trade.target_underlying:
                if trade.underlying not in ul_prices:
                    ul_prices[trade.underlying] = get_latest_price(trade.underlying)
                reason = self._underlying_exit(trade, ul_prices[trade.underlying], mark)
            else:
                reason = self._premium_exit(trade, mark)

            if reason:
                self._close(trade.symbol, mark, reason)

    def _premium_exit(self, trade, mark: float) -> str | None:
        if mark >= trade.target_premium:
            return "TP"
        if mark <= trade.stop_premium:
            return "SL"
        if trade.minutes_open() >= Config.MAX_HOLD_MINUTES:
            return "TIME"
        if self._signal_reversed(trade):
            return "SIGNAL"
        return None

    def _underlying_exit(self, trade, price: float | None, mark: float) -> str | None:
        if price is not None:
            if trade.direction == "LONG":
                if trade.stop_underlying and price <= trade.stop_underlying:
                    return "UL_SL"
                if trade.target_underlying and price >= trade.target_underlying:
                    return "UL_TP"
            else:
                if trade.stop_underlying and price >= trade.stop_underlying:
                    return "UL_SL"
                if trade.target_underlying and price <= trade.target_underlying:
                    return "UL_TP"
        # Disaster backstop on premium (fills/gaps the level check can miss).
        if mark <= trade.stop_premium:
            return "SL"
        return None

    def _signal_reversed(self, trade) -> bool:
        cached = self.last_signals.get(f"{trade.underlying}·orb")
        if not cached:
            return False
        sig = cached.get("signal")
        return (
            (trade.direction == "LONG" and sig == Signal.SHORT.value)
            or (trade.direction == "SHORT" and sig == Signal.LONG.value)
        )

    def _close(self, symbol: str, mark: float, reason: str) -> None:
        if self.dry_run:
            logger.info(f"[DRY RUN] Would close {symbol} @ ~${mark:.2f} [{reason}]")
            self.book.close(symbol, mark, reason)
            return
        if close_option_position(symbol):
            self.book.close(symbol, mark, reason)

    # ================================================================= Entries
    def scan_for_entries(self, account: dict) -> None:
        now_et = datetime.now(ET).time()
        if not (_parse_hhmm(Config.ENTRY_START) <= now_et <= _parse_hhmm(Config.ENTRY_CUTOFF)):
            return

        equity = account["equity"]
        for underlying in Config.UNDERLYINGS:
            ok, why = self.risk.can_open(equity, len(self.book.open_trades))
            if not ok:
                logger.info(f"Not scanning further: {why}")
                return

            if Config.STRATEGY in ("orb", "both"):
                self._scan_orb(underlying, equity)
            if Config.STRATEGY in ("sweep", "both"):
                self._scan_sweep(underlying, equity)

    def _entry_blocked(self, underlying: str) -> str | None:
        """Loss discipline: cooldown after a stop-out, and a hard per-day
        cutoff after consecutive losers on the same underlying."""
        streak = self.book.consecutive_losses(underlying)
        if streak >= Config.MAX_CONSECUTIVE_LOSSES:
            return f"{streak} consecutive losses — done with {underlying} today"
        last_loss = self.book.last_loss_time(underlying)
        if last_loss is not None:
            elapsed = (datetime.now(ET) - last_loss).total_seconds() / 60
            if elapsed < Config.LOSS_COOLDOWN_MINUTES:
                return (
                    f"loss cooldown ({elapsed:.0f}/{Config.LOSS_COOLDOWN_MINUTES} min)"
                )
        return None

    # ------------------------------------------------------------ ORB momentum
    def _scan_orb(self, underlying: str, equity: float) -> None:
        bars = get_today_bars(underlying)
        result = generate_signal(bars)
        self.last_signals[f"{underlying}·orb"] = {
            "signal": result.signal.value,
            "score": result.score,
            **result.details,
            "at": datetime.now(ET).isoformat(timespec="seconds"),
        }
        if result.signal == Signal.FLAT or self.book.open_for(underlying):
            return
        blocked = self._entry_blocked(underlying)
        if blocked:
            logger.info(f"{underlying} orb entry blocked: {blocked}")
            return
        self._enter(
            underlying,
            result.signal,
            spot=result.price,
            strategy="orb",
            note=f"score={result.score}",
        )

    # ------------------------------------------------------------ Sweep reversal
    def _scan_sweep(self, underlying: str, equity: float) -> None:
        # Extended-hours bars: the overnight/pre-market range needs them;
        # everything else uses the RTH subset.
        bars_ext = get_intraday_bars(underlying, lookback_days=4, rth_only=False)
        if bars_ext.empty:
            return
        bars = bars_ext.between_time("09:30", "16:00")
        if bars.empty:
            return
        today = datetime.now(ET).date()
        today_bars = bars[bars.index.date == today]
        if today_bars.empty:
            return
        spot = float(today_bars["close"].iloc[-1])

        sig = self._detect_sweep(bars, today_bars, bars_ext)
        cache_key = f"{underlying}·sweep"
        if sig is None:
            self.last_signals[cache_key] = {
                "signal": "FLAT",
                "score": 0,
                "price": round(spot, 2),
                "at": datetime.now(ET).isoformat(timespec="seconds"),
            }
            return

        dedupe = f"{underlying}|{sig.kind}|{sig.candle_ts}"
        self.last_signals[cache_key] = {
            "signal": sig.direction.value,
            "score": 100,
            "kind": sig.kind,
            "price": round(spot, 2),
            "swept": round(sig.swept_level, 2),
            "stop": round(sig.extreme, 2),
            "at": datetime.now(ET).isoformat(timespec="seconds"),
        }
        if dedupe in self._acted_sweeps:
            return
        if self.book.open_for(underlying) or underlying in self.pending:
            return
        blocked = self._entry_blocked(underlying)
        if blocked:
            logger.info(f"{underlying} sweep entry blocked: {blocked}")
            return
        if abs(spot - sig.extreme) < spot * Config.SWEEP_MIN_STOP_PCT:
            logger.info(
                f"{underlying} sweep skipped: stop too tight "
                f"({abs(spot - sig.extreme):.2f} < {spot * Config.SWEEP_MIN_STOP_PCT:.2f})"
            )
            return
        self._acted_sweeps.add(dedupe)

        if Config.SWEEP_ENTRY == "retrace":
            self._queue_retrace(underlying, sig, today_bars)
        else:
            stop = sig.extreme
            target = rr_target(spot, stop, Config.SWEEP_RR)
            self._enter(
                underlying, sig.direction, spot=spot, strategy="sweep",
                stop_underlying=stop, target_underlying=target,
                note=f"{sig.kind} swept={sig.swept_level:.2f}",
            )

    def _detect_sweep(self, bars, today_bars, bars_ext=None) -> SweepSignal | None:
        now = datetime.now(ET)
        htf = resample_bars(bars, Config.SWEEP_TIMEFRAME_MINUTES)
        htf = completed_bars(htf, Config.SWEEP_TIMEFRAME_MINUTES, now)
        sig = sweep_reclaim(htf.tail(2), trend_filter=Config.SWEEP_TREND_FILTER)
        if sig:
            return sig

        completed_today = completed_bars(today_bars, Config.BAR_MINUTES, now)
        if Config.SWEEP_PREV_DAY_LEVELS:
            prev_days = bars[bars.index.date < now.date()]
            if not prev_days.empty:
                last_day = prev_days[prev_days.index.date == prev_days.index.date[-1]]
                sig = prev_day_level_sweep(
                    completed_today,
                    prev_day_high=float(last_day["high"].max()),
                    prev_day_low=float(last_day["low"].min()),
                )
                if sig:
                    return sig
        if Config.SWEEP_OVERNIGHT_RANGE and bars_ext is not None:
            if Config.SWEEP_SESSION_WINDOW:
                rng = session_range(bars_ext, now.date(), Config.SWEEP_SESSION_WINDOW)
                kind = "session_range"
            else:
                rng = overnight_range(bars_ext, now.date())
                kind = "overnight_range"
            if rng:
                return level_sweep(completed_today, rng[0], rng[1], kind)
        return None

    # ------------------------------------------------------------ Retrace entries
    def _queue_retrace(self, underlying: str, sig: SweepSignal, today_bars) -> None:
        """
        Instead of entering at the reclaim close, wait for price to pull back
        into the manipulation candle — the FVG of the reclaim leg if one
        exists, else the candle midpoint — with the same stop (better RR).
        """
        fvg = find_fvg(today_bars, sig.direction)
        if sig.direction == Signal.LONG:
            trigger = fvg[1] if fvg and sig.candle_low < fvg[1] < sig.close else sig.midpoint
        else:
            trigger = fvg[0] if fvg and sig.close < fvg[0] < sig.candle_high else sig.midpoint
        self.pending[underlying] = {
            "direction": sig.direction,
            "trigger": trigger,
            "stop": sig.extreme,
            "kind": sig.kind,
            "expires": datetime.now(ET) + timedelta(minutes=Config.SWEEP_RETRACE_EXPIRY_MIN),
        }
        logger.info(
            f"Retrace setup queued: {underlying} {sig.direction.value} "
            f"trigger={trigger:.2f} stop={sig.extreme:.2f} ({sig.kind})"
        )

    def check_pending(self, equity: float) -> None:
        for underlying, setup in list(self.pending.items()):
            if datetime.now(ET) >= setup["expires"]:
                logger.info(f"Retrace setup expired: {underlying}")
                del self.pending[underlying]
                continue
            price = get_latest_price(underlying)
            if price is None:
                continue
            direction = setup["direction"]
            stop = setup["stop"]
            # Setup invalidated if the stop level is breached before entry.
            if (direction == Signal.LONG and price <= stop) or (
                direction == Signal.SHORT and price >= stop
            ):
                logger.info(f"Retrace setup invalidated (stop hit first): {underlying}")
                del self.pending[underlying]
                continue
            triggered = (direction == Signal.LONG and price <= setup["trigger"]) or (
                direction == Signal.SHORT and price >= setup["trigger"]
            )
            if not triggered:
                continue
            del self.pending[underlying]
            ok, why = self.risk.can_open(equity, len(self.book.open_trades))
            if not ok:
                logger.info(f"Retrace triggered but cannot open: {why}")
                continue
            blocked = self._entry_blocked(underlying)
            if blocked:
                logger.info(f"Retrace triggered but blocked: {blocked}")
                continue
            target = rr_target(price, stop, Config.SWEEP_RR)
            self._enter(
                underlying, direction, spot=price, strategy="sweep",
                stop_underlying=stop, target_underlying=target,
                note=f"retrace {setup['kind']}",
            )

    # ------------------------------------------------------------ Order placement
    def _enter(
        self,
        underlying: str,
        direction: Signal,
        spot: float,
        strategy: str,
        stop_underlying: float = 0.0,
        target_underlying: float = 0.0,
        note: str = "",
    ) -> None:
        contract = select_contract(underlying, direction, spot)
        if contract is None:
            return

        premium = contract.ask  # assume paying the offer on a market order
        equity = self.last_equity
        if strategy == "sweep":
            qty = self.risk.contracts_for_underlying_stop(
                equity, premium, contract.delta, abs(spot - stop_underlying)
            )
        else:
            qty = self.risk.contracts_for(equity, premium)
        if qty < 1:
            logger.info(f"{underlying}: sized to 0 contracts — skipping ({note})")
            return

        logger.info(
            f"ENTRY [{strategy}] {direction.value} {underlying} | {note} | "
            f"{qty}x {contract.symbol} @ ~${premium:.2f} "
            f"(delta={contract.delta}, OI={contract.open_interest})"
            + (
                f" | UL stop={stop_underlying:.2f} target={target_underlying:.2f}"
                if stop_underlying
                else ""
            )
        )

        if self.dry_run:
            logger.info(f"[DRY RUN] Would BUY {qty}x {contract.symbol}")
            self.book.open(
                contract.symbol, underlying, direction.value, qty, premium,
                strategy=strategy, stop_underlying=stop_underlying,
                target_underlying=target_underlying,
            )
            self.risk.record_open()
            return

        order_id = buy_option(contract.symbol, qty)
        if order_id:
            self.book.open(
                contract.symbol, underlying, direction.value, qty, premium, order_id,
                strategy=strategy, stop_underlying=stop_underlying,
                target_underlying=target_underlying,
            )
            self.risk.record_open()

    # ================================================================= Status
    def status(self) -> dict:
        equity = self.last_equity
        return {
            "running": self.running,
            "dry_run": self.dry_run,
            "mode": Config.ALPACA_MODE,
            "strategy": Config.STRATEGY,
            "equity": equity,
            "risk": self.risk.snapshot(equity) if equity else {},
            "book": self.book.summary(),
            "signals": self.last_signals,
            "pending": {
                u: {
                    "direction": p["direction"].value,
                    "trigger": round(p["trigger"], 2),
                    "stop": round(p["stop"], 2),
                    "expires": p["expires"].isoformat(timespec="seconds"),
                }
                for u, p in self.pending.items()
            },
            "open_trades": [
                {
                    "symbol": t.symbol,
                    "underlying": t.underlying,
                    "strategy": t.strategy,
                    "direction": t.direction,
                    "qty": t.qty,
                    "entry": t.entry_premium,
                    "stop": t.stop_premium,
                    "target": t.target_premium,
                    "stop_underlying": t.stop_underlying,
                    "target_underlying": t.target_underlying,
                    "opened_at": t.opened_at,
                }
                for t in self.book.open_trades
            ],
        }
