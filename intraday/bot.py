"""
StoxDaily — APEX v4.2 intraday day-trading bot.

Long-only tech momentum strategy using the Composite Alpha Score (CAS) engine.
Scans the APEX tech universe every 60 seconds during market hours.
Closes all positions by 3:55 PM ET; applies time stop at 12:30 PM.

Key execution rules (from APEX v4.2 spec):
- Never enter in the first 5 minutes after open (9:30-9:35 AM)
- Entry trigger: price > VWAP, volume > 1.5x avg, ATR% > 2%
- Hard stop: -2% from entry; dynamic VWAP stop after 10:30 AM
- Take-profit 1: +3% | Take-profit 2: +5% (managed in exit loop)
- Time stop at 12:30 PM: exit positions up < 0.5%
- Circuit breaker: 3 consecutive stops → halt new entries for the day
- VIX > 35: suspend system; VIX > 28: reduce sizes 40%
"""
from __future__ import annotations

import threading
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
)
from intraday.data import fetch_bars_batch, get_prev_close, fetch_snapshots_batch, fetch_news_batch
from intraday.indicators import add_intraday_indicators
from intraday.portfolio import IntradayPortfolio
from intraday.risk import IntradayRiskManager
from intraday.universe import APEX_UNIVERSE, REGIME_REFERENCE
from intraday.strategies.apex import generate_signal as apex_signal
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
        self._snapshots: dict[str, dict] = {}          # refreshed every scan
        self._news_cache: dict[str, list[str]] = {}    # refreshed every APEX_NEWS_CACHE_MINUTES
        self._news_cache_time: Optional[datetime] = None

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
        logger.info("StoxDaily APEX v4.2 started (dry_run=%s)", dry_run)
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
            "circuit_breaker": self.risk.circuit_breaker_active,
        }

    # ------------------------------------------------------------------ Main loop

    def _run_loop(self) -> None:
        logger.info("StoxDaily APEX scan loop starting")
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
            if not self.risk.is_market_hours():
                self.risk.reset()
                self._prev_closes = {}
                self._snapshots = {}
                self._news_cache = {}
                self._news_cache_time = None
            return

        acct = get_account()
        equity = acct.get("equity", 0.0)
        self.risk.initialize(equity)
        self._status["last_scan"] = datetime.utcnow().isoformat()

        # EOD hard close — all positions out by 3:55 PM
        if self.risk.is_eod_close_time():
            logger.info("EOD close time — closing all intraday positions")
            if not self._dry_run:
                close_all_positions()
                for sym in list(self.portfolio.open_symbols()):
                    positions = get_positions()
                    if sym not in positions:
                        self.portfolio.close_trade(sym, 0.0, status="EOD_CLOSE")
            else:
                logger.info("[DRY RUN] Would close all positions at EOD")
            return

        # Halt all new entries if daily loss limit or circuit breaker active
        if self.risk.daily_loss_exceeded(equity) or self.risk.circuit_breaker_active:
            logger.warning("Risk halt active — managing exits only")
            self._manage_exits(equity)
            return

        # Fetch bars for entire universe + regime references in one batch call
        all_symbols = list(dict.fromkeys(APEX_UNIVERSE + REGIME_REFERENCE))
        bars = fetch_bars_batch(all_symbols, timeframe_minutes=5, lookback_bars=100)

        # Manage existing positions (stops, TP, time stop, dynamic VWAP stop)
        self._manage_exits(equity, bars=bars)

        # Scan for new entries only if within the allowed entry window
        if self.risk.is_market_hours() and self.risk.is_entry_allowed_time():
            self._scan_entries(equity, bars)

    # ------------------------------------------------------------------ Exit management

    def _manage_exits(self, equity: float, bars: Optional[dict] = None) -> None:
        positions = get_positions()

        for symbol in list(self.portfolio.open_symbols()):
            alpaca_pos = positions.get(symbol)
            if alpaca_pos is None:
                # Bracket order was filled by Alpaca (stop or TP hit externally)
                trade = self.portfolio.get_open_trade(symbol)
                if trade:
                    if trade.pnl_pct < 0:
                        self.risk.record_stop()
                    else:
                        self.risk.record_win()
                    self.portfolio.close_trade(symbol, trade.stop_loss, status="CLOSED")
                continue

            trade = self.portfolio.get_open_trade(symbol)
            if not trade:
                continue

            # Resolve current price from live bars or Alpaca position data
            current_price = float(alpaca_pos.get("current_price", trade.entry_price) or trade.entry_price)
            if bars and symbol in bars:
                df = bars[symbol]
                if not df.empty:
                    current_price = float(df["close"].iloc[-1])

            gain_pct = (current_price - trade.entry_price) / trade.entry_price if trade.entry_price > 0 else 0.0

            # ---- Time stop: exit by 12:30 PM if not up ≥0.5% (opportunity cost rule) ----
            if self.risk.is_time_stop_zone() and gain_pct < Config.APEX_TIME_STOP_MIN_GAIN:
                logger.info(
                    "Time stop: %s gain=%.2f%% < %.2f%% at noon",
                    symbol, gain_pct * 100, Config.APEX_TIME_STOP_MIN_GAIN * 100,
                )
                if not self._dry_run:
                    close_position(symbol)
                self.risk.record_stop() if gain_pct < 0 else self.risk.record_win()
                self.portfolio.close_trade(symbol, current_price, status="TIME_STOP")
                continue

            # ---- Hard stop: -2% from entry ----
            if current_price <= trade.stop_loss:
                logger.info("Hard stop hit: %s @ %.2f (entry=%.2f SL=%.2f)", symbol, current_price, trade.entry_price, trade.stop_loss)
                if not self._dry_run:
                    close_position(symbol)
                self.risk.record_stop()
                self.portfolio.close_trade(symbol, current_price, status="STOPPED")
                continue

            # ---- Dynamic VWAP stop after 10:30 AM (exit if below VWAP and losing) ----
            if self.risk.is_dynamic_stop_time() and bars and symbol in bars:
                df = bars[symbol]
                if not df.empty:
                    df_ind = add_intraday_indicators(df)
                    vwap_now = float(df_ind["vwap"].iloc[-1])
                    if vwap_now > 0 and current_price < vwap_now and gain_pct < 0:
                        logger.info(
                            "Dynamic VWAP stop: %s price=%.2f < vwap=%.2f gain=%.2f%%",
                            symbol, current_price, vwap_now, gain_pct * 100,
                        )
                        if not self._dry_run:
                            close_position(symbol)
                        self.risk.record_stop()
                        self.portfolio.close_trade(symbol, current_price, status="STOPPED")
                        continue

            # ---- Take-profit 2: +5% — full exit ----
            target_2 = trade.target_2 if trade.target_2 > 0 else trade.entry_price * (1.0 + Config.APEX_TARGET2_PCT)
            if current_price >= target_2:
                logger.info("TP2 hit: %s @ %.2f (+%.1f%%)", symbol, current_price, gain_pct * 100)
                if not self._dry_run:
                    close_position(symbol)
                self.risk.record_win()
                self.portfolio.close_trade(symbol, current_price, status="TOOK_PROFIT")
                continue

            # ---- Take-profit 1: +3% — bracket order handles this; sync portfolio state ----
            if trade.take_profit > 0 and current_price >= trade.take_profit:
                logger.info("TP1 hit: %s @ %.2f (+%.1f%%)", symbol, current_price, gain_pct * 100)
                if not self._dry_run:
                    close_position(symbol)
                self.risk.record_win()
                self.portfolio.close_trade(symbol, current_price, status="TOOK_PROFIT")

    # ------------------------------------------------------------------ Entry scanning

    def _scan_entries(self, equity: float, bars: dict) -> None:
        open_symbols = self.portfolio.open_symbols()
        open_count = len(get_positions())

        if not self.risk.can_open_position(open_count, equity):
            return

        # ---- Snapshots: fetch every scan (provides spread filter + prev_close) ----
        self._snapshots = fetch_snapshots_batch(APEX_UNIVERSE)
        # Populate prev_closes from snapshot data (faster than individual API calls)
        for sym, snap in self._snapshots.items():
            pc = snap.get("prev_close", 0.0)
            if pc > 0:
                self._prev_closes[sym] = pc
        # Fall back to individual calls for any symbols not in snapshots
        for sym in APEX_UNIVERSE:
            if sym not in self._prev_closes:
                self._prev_closes[sym] = get_prev_close(sym)

        # ---- News: refresh on TTL (default every 15 minutes) ----
        now = datetime.utcnow()
        news_stale = (
            self._news_cache_time is None
            or (now - self._news_cache_time).total_seconds() > Config.APEX_NEWS_CACHE_MINUTES * 60
        )
        if news_stale:
            self._news_cache = fetch_news_batch(APEX_UNIVERSE, hours=Config.APEX_NEWS_HOURS_LOOKBACK)
            self._news_cache_time = now
            logger.debug("News cache refreshed for %d symbols", len(self._news_cache))

        # QQQ bars for macro regime factor
        qqq_df = bars.get("QQQ")

        # Current open position values for gross exposure check
        positions = get_positions()
        open_values = [
            float(p.get("market_value", 0.0) or 0.0)
            for p in positions.values()
        ]

        # Score all candidates with APEX CAS engine
        candidates: list[tuple[float, object]] = []

        for symbol in APEX_UNIVERSE:
            if symbol in open_symbols:
                continue
            df = bars.get(symbol)
            if df is None or df.empty:
                continue
            try:
                sig = apex_signal(
                    symbol=symbol,
                    df=df,
                    prev_close=self._prev_closes.get(symbol, 0.0),
                    qqq_df=qqq_df,
                    snapshot=self._snapshots.get(symbol),
                    news_headlines=self._news_cache.get(symbol),
                )
                if sig:
                    candidates.append((sig.cas_score, sig))
            except Exception as exc:
                logger.debug("APEX signal error %s: %s", symbol, exc)

        # Best CAS scores first
        candidates.sort(key=lambda x: x[0], reverse=True)

        for cas_score, sig in candidates:
            current_open = len(self.portfolio.open_symbols())
            if not self.risk.can_open_position(current_open, equity):
                break

            symbol = sig.symbol
            if symbol in self.portfolio.open_symbols():
                continue

            if not self.risk.gross_exposure_ok(open_values, equity):
                logger.info("Gross exposure cap reached — no more entries this scan")
                break

            qty = self.risk.position_size_for_cas(equity, sig.entry_price, cas_score)
            if qty <= 0:
                continue

            tier = "STRONG BUY" if cas_score >= Config.APEX_STRONG_BUY_CAS else "BUY"
            logger.info(
                "[APEX %s] %s qty=%d entry=%.2f SL=%.2f TP1=%.2f TP2=%.2f CAS=%.1f "
                "(A=%.1f B=%.1f C=%.1f D=%.1f)",
                tier, symbol, qty, sig.entry_price, sig.stop_loss,
                sig.take_profit, sig.target_2, cas_score,
                sig.factor_catalyst, sig.factor_momentum,
                sig.factor_technical, sig.factor_regime,
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
                logger.info(
                    "[DRY RUN] Would place bracket: %s x%d SL=%.2f TP=%.2f CAS=%.1f",
                    symbol, qty, sig.stop_loss, sig.take_profit, cas_score,
                )
                order_id = "dry-run"

            if order_id:
                self.portfolio.open_trade(
                    symbol=symbol,
                    side=sig.side,
                    shares=qty,
                    entry_price=sig.entry_price,
                    stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit,
                    strategy="APEX",
                    order_id=order_id,
                    cas_score=sig.cas_score,
                    target_2=sig.target_2,
                )
                open_values.append(sig.entry_price * qty)


# Module-level singleton
intraday_bot = IntradayBot()
