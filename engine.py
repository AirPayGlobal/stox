"""
STOX Options — intraday trading engine.

Loop (every Config.LOOP_SECONDS while the market is open):
  1. mark open positions and fire exits (target / stop / time stop /
     signal reversal / end-of-day flatten / loss-halt flatten)
  2. re-evaluate the daily governor (profit target lock, max-loss halt)
  3. every Config.SCAN_SECONDS, scan the underlyings for entries:
     signal -> contract selection -> risk sizing -> market order

Day trading only: every position is closed by Config.FLATTEN_TIME ET.
"""
from __future__ import annotations

import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from analysis.signals import Signal, generate_signal
from config import Config
from data.market_data import get_today_bars
from data.options_data import get_option_mid
from options.contracts import select_contract
from trading.broker import (
    buy_option,
    close_option_position,
    get_account,
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
        self.last_signals: dict[str, dict] = {}
        self.last_equity: float = 0.0
        mode = "DRY RUN" if dry_run else Config.ALPACA_MODE.upper()
        logger.info(f"Engine ready | mode={mode} | underlyings={','.join(Config.UNDERLYINGS)}")

    # ================================================================= Loop
    def run(self) -> None:
        self.running = True
        logger.info("Engine loop started")
        while self.running:
            try:
                self.tick()
            except Exception as exc:
                logger.error(f"Tick failed: {exc}", exc_info=True)
            time.sleep(Config.LOOP_SECONDS)

    def stop(self) -> None:
        self.running = False

    def tick(self) -> None:
        if not is_market_open():
            return

        account = get_account()
        equity = self.last_equity = account["equity"]
        self.risk.ensure_today(equity)

        now_et = datetime.now(ET).time()
        in_flatten_window = now_et >= _parse_hhmm(Config.FLATTEN_TIME)

        self.manage_positions(flatten_all=in_flatten_window or self.risk.must_flatten())
        self.risk.update_governor(equity)

        if in_flatten_window:
            return
        if time.time() - self.last_scan >= Config.SCAN_SECONDS:
            self.last_scan = time.time()
            self.scan_for_entries(account)

    # ================================================================= Exits
    def manage_positions(self, flatten_all: bool = False) -> None:
        for trade in list(self.book.open_trades):
            mark = get_option_mid(trade.symbol)
            if mark is None:
                continue

            reason = None
            if flatten_all:
                reason = "HALT" if self.risk.must_flatten() else "FLATTEN"
            elif mark >= trade.target_premium:
                reason = "TP"
            elif mark <= trade.stop_premium:
                reason = "SL"
            elif trade.minutes_open() >= Config.MAX_HOLD_MINUTES:
                reason = "TIME"
            elif self._signal_reversed(trade):
                reason = "SIGNAL"

            if reason:
                self._close(trade.symbol, mark, reason)

    def _signal_reversed(self, trade) -> bool:
        cached = self.last_signals.get(trade.underlying)
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

            bars = get_today_bars(underlying)
            result = generate_signal(bars)
            self.last_signals[underlying] = {
                "signal": result.signal.value,
                "score": result.score,
                "long_score": result.long_score,
                "short_score": result.short_score,
                **result.details,
                "at": datetime.now(ET).isoformat(timespec="seconds"),
            }
            if result.signal == Signal.FLAT:
                continue
            if self.book.open_for(underlying):
                continue  # one position per underlying

            self._enter(underlying, result, equity)

    def _enter(self, underlying: str, result, equity: float) -> None:
        contract = select_contract(underlying, result.signal, result.price)
        if contract is None:
            return

        premium = contract.ask  # assume paying the offer on a market order
        qty = self.risk.contracts_for(equity, premium)
        if qty < 1:
            logger.info(
                f"{underlying}: sized to 0 contracts (premium ${premium:.2f}, "
                f"equity ${equity:,.0f}) — skipping"
            )
            return

        logger.info(
            f"ENTRY {result.signal.value} {underlying} score={result.score} | "
            f"{qty}x {contract.symbol} @ ~${premium:.2f} "
            f"(delta={contract.delta}, OI={contract.open_interest})"
        )

        if self.dry_run:
            logger.info(f"[DRY RUN] Would BUY {qty}x {contract.symbol}")
            self.book.open(contract.symbol, underlying, result.signal.value, qty, premium)
            self.risk.record_open()
            return

        order_id = buy_option(contract.symbol, qty)
        if order_id:
            self.book.open(
                contract.symbol, underlying, result.signal.value, qty, premium, order_id
            )
            self.risk.record_open()

    # ================================================================= Status
    def status(self) -> dict:
        equity = self.last_equity
        return {
            "running": self.running,
            "dry_run": self.dry_run,
            "mode": Config.ALPACA_MODE,
            "equity": equity,
            "risk": self.risk.snapshot(equity) if equity else {},
            "book": self.book.summary(),
            "signals": self.last_signals,
            "open_trades": [
                {
                    "symbol": t.symbol,
                    "underlying": t.underlying,
                    "direction": t.direction,
                    "qty": t.qty,
                    "entry": t.entry_premium,
                    "stop": t.stop_premium,
                    "target": t.target_premium,
                    "opened_at": t.opened_at,
                }
                for t in self.book.open_trades
            ],
        }
