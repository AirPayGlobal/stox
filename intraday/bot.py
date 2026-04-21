"""
StoxDaily — Intraday day-trading bot.

Runs a 60-second scan loop during market hours. Executes 4 strategies:
  ORB (Opening Range Breakout), VWAP scalp, Gap & Go, 9/20 EMA scalp.
Closes all positions by 3:45 PM ET.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import Config
from intraday.client import (
    get_account,
    get_positions,
    close_position,
    close_all_positions,
    place_bracket_order,
    is_market_open,
    minutes_to_close,
)
from intraday.data import fetch_bars_batch, get_prev_close
from intraday.indicators import add_intraday_indicators
from intraday.portfolio import IntradayPortfolio
from intraday.risk import IntradayRiskManager
from intraday.universe import INTRADAY_UNIVERSE
from intraday.strategies.orb import generate_signal as orb_signal
from intraday.strategies.vwap_scalp import generate_signal as vwap_signal
from intraday.strategies.gap_go import generate_signal as gap_go_signal
from intraday.strategies.ema_scalp import generate_signal as ema_signal
from utils.logger import get_logger

logger = get_logger("intraday.bot")

_ET = timezone(timedelta(hours=-4))


class IntradayBot:
    def __init__(self) -> None:
        self.portfolio = IntradayPortfolio()
        self.risk = IntradayRiskManager()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._dry_run = False
        self._status: dict = {"running": False, "dry_run": False, "last_scan": None, "error": None}
        self._prev_closes: dict[str, float] = {}

    # ------------------------------------------------------------------ Control

    def start(self, dry_run: bool = False) -> dict:
        if self._running:
            return {"status": "already_running"}
        self._dry_run = dry_run
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="intraday-bot")
        self._thread.start()
        self._running = True
        self._status.update({"running": True, "dry_run": dry_run, "error": None})
        logger.info("StoxDaily started (dry_run=%s)", dry_run)
        return {"status": "started", "dry_run": dry_run}

    def stop(self) -> dict:
        self._stop_event.set()
        self._running = False
        self._status["running"] = False
        logger.info("StoxDaily stopping")
        return {"status": "stopped"}

    def get_status(self) -> dict:
        try:
            acct = get_account()
            positions = get_positions()
        except Exception:
            acct = {}
            positions = {}
        return {
            **self._status,
            "open_positions": len(positions),
            "account": acct,
            "today": self.portfolio.today_summary(),
        }

    # ------------------------------------------------------------------ Main loop

    def _run_loop(self) -> None:
        logger.info("StoxDaily scan loop starting")
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.error("IntradayBot tick error: %s", exc, exc_info=True)
                self._status["error"] = str(exc)
            self._stop_event.wait(timeout=Config.INTRADAY_SCAN_INTERVAL)
        logger.info("StoxDaily scan loop stopped")
        self._running = False
        self._status["running"] = False

    def _tick(self) -> None:
        if not is_market_open():
            # Reset session state at end of day
            if not self.risk.is_market_hours():
                self.risk.reset()
            return

        acct = get_account()
        equity = acct.get("equity", 0.0)

        self.risk.initialize(equity)
        self._status["last_scan"] = datetime.utcnow().isoformat()

        # EOD: close everything
        if self.risk.is_eod_close_time():
            logger.info("EOD close time — closing all intraday positions")
            if not self._dry_run:
                closed_count = close_all_positions()
                for sym in list(self.portfolio.open_symbols()):
                    positions = get_positions()
                    if sym not in positions:
                        self.portfolio.close_trade(sym, 0.0, status="EOD_CLOSE")
            else:
                logger.info("[DRY RUN] Would close all positions at EOD")
            return

        # Daily loss limit check
        if self.risk.daily_loss_exceeded(equity):
            logger.warning("Daily loss limit — no new entries for the rest of the session")
            self._manage_exits(equity)
            return

        # Fetch bars for entire universe in one batch call
        bars = fetch_bars_batch(INTRADAY_UNIVERSE, timeframe_minutes=5, lookback_bars=100)

        # Manage existing positions first
        self._manage_exits(equity, bars=bars)

        # Scan for entries if within trading hours
        if self.risk.is_market_hours() and not self.risk.is_eod_close_time():
            self._scan_entries(equity, bars)

    # ------------------------------------------------------------------ Exit management

    def _manage_exits(self, equity: float, bars: Optional[dict] = None) -> None:
        positions = get_positions()
        open_symbols = self.portfolio.open_symbols()

        for symbol in list(open_symbols):
            alpaca_pos = positions.get(symbol)
            if alpaca_pos is None:
                # Position closed externally (stop/TP filled)
                trade = self.portfolio.get_open_trade(symbol)
                if trade:
                    exit_price = trade.stop_loss  # approximate
                    self.portfolio.close_trade(symbol, exit_price, status="CLOSED")
                continue

            trade = self.portfolio.get_open_trade(symbol)
            if not trade:
                continue

            current_price = alpaca_pos.get("avg_entry", trade.entry_price)
            if bars and symbol in bars:
                df = bars[symbol]
                if not df.empty:
                    current_price = float(df["close"].iloc[-1])

            # Check stop loss / take profit manually (bracket orders handle this on Alpaca,
            # but we sync portfolio state)
            if trade.side == "buy":
                if current_price <= trade.stop_loss:
                    logger.info("Stop loss hit: %s @ %.2f", symbol, current_price)
                    if not self._dry_run:
                        close_position(symbol)
                    self.portfolio.close_trade(symbol, current_price, status="STOPPED")
                elif current_price >= trade.take_profit:
                    logger.info("Take profit hit: %s @ %.2f", symbol, current_price)
                    if not self._dry_run:
                        close_position(symbol)
                    self.portfolio.close_trade(symbol, current_price, status="TOOK_PROFIT")
            else:  # short
                if current_price >= trade.stop_loss:
                    logger.info("Stop loss hit (short): %s @ %.2f", symbol, current_price)
                    if not self._dry_run:
                        close_position(symbol)
                    self.portfolio.close_trade(symbol, current_price, status="STOPPED")
                elif current_price <= trade.take_profit:
                    logger.info("Take profit hit (short): %s @ %.2f", symbol, current_price)
                    if not self._dry_run:
                        close_position(symbol)
                    self.portfolio.close_trade(symbol, current_price, status="TOOK_PROFIT")

    # ------------------------------------------------------------------ Entry scanning

    def _scan_entries(self, equity: float, bars: dict) -> None:
        open_symbols = self.portfolio.open_symbols()
        open_count = len(get_positions())

        if not self.risk.can_open_position(open_count, equity):
            return

        # Pre-fetch prev closes once per session
        if not self._prev_closes:
            self._prev_closes = {
                sym: get_prev_close(sym)
                for sym in INTRADAY_UNIVERSE[:20]  # limit API calls
            }

        # Collect all signals across all strategies
        candidates: list[tuple[float, object, str]] = []  # (score, signal, strategy)

        for symbol, df in bars.items():
            if symbol in open_symbols:
                continue
            if df is None or df.empty:
                continue

            # ORB
            try:
                sig = orb_signal(symbol, df, orb_minutes=Config.INTRADAY_ORB_MINUTES)
                if sig:
                    candidates.append((sig.score, sig, "ORB"))
            except Exception:
                pass

            # VWAP scalp
            try:
                sig = vwap_signal(symbol, df)
                if sig:
                    candidates.append((sig.score, sig, "VWAP"))
            except Exception:
                pass

            # Gap & Go
            try:
                prev_close = self._prev_closes.get(symbol, 0.0)
                sig = gap_go_signal(symbol, df, prev_close=prev_close)
                if sig:
                    candidates.append((sig.score, sig, "GAP_GO"))
            except Exception:
                pass

            # EMA scalp
            try:
                sig = ema_signal(symbol, df)
                if sig:
                    candidates.append((sig.score, sig, "EMA"))
            except Exception:
                pass

        # Sort by score descending, take best signals
        candidates.sort(key=lambda x: x[0], reverse=True)

        for score, sig, strategy in candidates:
            # Re-check position count since we may have added in this loop
            current_open = len(self.portfolio.open_symbols())
            if not self.risk.can_open_position(current_open, equity):
                break

            symbol = sig.symbol
            if symbol in self.portfolio.open_symbols():
                continue

            qty = self.risk.position_size(equity, sig.entry_price)
            if qty <= 0:
                continue

            logger.info(
                "[%s] Signal: %s %s qty=%d entry=%.2f SL=%.2f TP=%.2f score=%.1f",
                strategy, sig.side.upper(), symbol, qty,
                sig.entry_price, sig.stop_loss, sig.take_profit, score,
            )

            order_id = None
            if not self._dry_run:
                order_id = place_bracket_order(
                    symbol=symbol,
                    qty=qty,
                    side=sig.side,
                    limit_price=sig.entry_price,
                    stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit,
                )
            else:
                logger.info("[DRY RUN] Would place bracket order: %s %s x%d", sig.side, symbol, qty)
                order_id = "dry-run"

            if order_id:
                self.portfolio.open_trade(
                    symbol=symbol,
                    side=sig.side,
                    shares=qty,
                    entry_price=sig.entry_price,
                    stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit,
                    strategy=strategy,
                    order_id=order_id,
                )


# Module-level singleton
intraday_bot = IntradayBot()
