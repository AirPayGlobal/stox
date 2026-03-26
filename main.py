"""
STOX — Algorithmic Stock Trading Bot
=====================================
Main entry point. Runs the live (or paper) trading loop.

Usage:
    python main.py              # run live bot
    python main.py --dry-run    # scan signals only, no orders placed
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import schedule

from config import Config
from data.fetcher import fetch_batch
from analysis.signals import screen_universe, Signal
from trading.alpaca_client import (
    get_account,
    get_positions,
    get_pending_symbols,
    is_market_open,
    place_bracket_order,
    close_position,
    close_all_positions,
    validate_credentials,
)
from trading.risk_manager import RiskManager
from trading.portfolio import Portfolio
from strategy.ema_rsi_macd import EmaRsiMacdStrategy
from analysis.market_filter import is_vix_too_high, is_sentiment_negative
from analysis.news_scanner import find_news_catalysts, apply_news_boost
from analysis.ipo_tracker import (
    register_new_ipos,
    get_tradeable_ipos,
    generate_ipo_signal,
    ipo_position_size,
)
from trading.approval_queue import submit as queue_approval, get_expired, mark_executed
from utils.logger import get_logger

logger = get_logger("main")


def print_banner() -> None:
    print("""
╔══════════════════════════════════════════════╗
║          STOX — Algorithmic Trading Bot      ║
║  Strategy : EMA + RSI + MACD + Bollinger     ║
║  Market   : US Stocks (NYSE / NASDAQ)        ║
║  Mode     : Conservative Compounding         ║
╚══════════════════════════════════════════════╝
""")


class TradingBot:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.strategy = EmaRsiMacdStrategy()
        self.risk = RiskManager()
        self.portfolio = Portfolio()
        self._running = True

        mode = "DRY RUN" if dry_run else Config.ALPACA_MODE.upper()
        logger.info(f"Bot initialised | mode={mode} | strategy={self.strategy.name}")

    # ----------------------------------------------------------------- Loop
    def morning_setup(self) -> None:
        """Called once at market open."""
        logger.info("=== MARKET OPEN — Morning Setup ===")
        try:
            account = get_account()
            equity = account["equity"]
            self.risk.set_day_start_equity(equity)
            logger.info(
                f"Account: equity=${equity:,.2f} cash=${account['cash']:,.2f} "
                f"buying_power=${account['buying_power']:,.2f}"
            )
            positions = get_positions()
            self.portfolio.take_snapshot(
                equity=equity,
                cash=account["cash"],
                open_positions=len(positions),
            )
        except Exception as exc:
            logger.error(f"Morning setup failed: {exc}")

    def scan_and_trade(self) -> None:
        """Main scan: fetch data → generate signals → execute trades."""
        if not is_market_open():
            logger.info("Market is closed — skipping scan.")
            return

        try:
            account = get_account()
            equity = account["equity"]
            cash = account["cash"]
        except Exception as exc:
            logger.error(f"Could not fetch account info: {exc}")
            return

        # Daily loss circuit-breaker
        if self.risk.daily_loss_exceeded(equity):
            logger.warning("Daily loss limit hit — no new trades today.")
            return

        # VIX filter — skip all new entries during high-fear market conditions
        if is_vix_too_high():
            logger.warning("VIX filter active — no new BUY entries this scan.")
            return

        open_positions = get_positions()
        pending_symbols = get_pending_symbols()
        open_symbols = set(open_positions.keys()) | pending_symbols

        # Check exits first
        self._check_exits(open_positions, equity)

        # Refresh open positions count after exits
        open_positions = get_positions()

        if self.risk.max_positions_reached(len(open_positions)):
            logger.info("Max positions reached — not opening new trades.")
            return

        # --- Auto-execute expired IPO approvals (user didn't respond in 60 min) ---
        try:
            self._auto_execute_expired_approvals()
        except Exception as exc:
            logger.warning(f"Auto-execute check failed: {exc}")

        # --- IPO discovery: register new listings from news ---
        try:
            new_ipos = register_new_ipos(hours=48)
            if new_ipos:
                logger.info(f"New IPOs detected and quarantined: {new_ipos}")
        except Exception as exc:
            logger.warning(f"IPO registration failed: {exc}")

        # --- News catalyst discovery (symbols not necessarily in watchlist) ---
        news_symbols = []
        try:
            catalysts = find_news_catalysts(hours=24, min_score=2, max_results=10)
            news_symbols = [
                sym for sym, score, headline in catalysts
                if sym not in open_symbols and sym not in Config.WATCHLIST
            ]
            if news_symbols:
                logger.info(
                    f"News scanner discovered {len(news_symbols)} new symbols: "
                    + ", ".join(
                        f"{sym}(score={sc:.1f})"
                        for sym, sc, _ in catalysts
                        if sym in news_symbols
                    )
                )
        except Exception as exc:
            logger.warning(f"News catalyst scan failed: {exc}")

        # Fetch latest data for watchlist + any news-discovered symbols
        scan_symbols = Config.WATCHLIST + news_symbols
        logger.info(f"Scanning {len(scan_symbols)} symbols ({len(news_symbols)} from news)...")
        data = fetch_batch(scan_symbols, lookback_days=100)

        # Screen for signals
        candidates = screen_universe(data)
        buy_candidates = [
            (sym, sig, score)
            for sym, sig, score in candidates
            if sig == Signal.BUY and sym not in open_symbols
        ]

        # Re-rank by boosting technical scores with news sentiment
        if buy_candidates:
            buy_candidates = apply_news_boost(buy_candidates)

        logger.info(f"Buy candidates: {len(buy_candidates)}")

        for symbol, signal, score in buy_candidates:
            if self.risk.max_positions_reached(len(get_positions())):
                break

            # News sentiment filter — skip symbols with recent negative headlines
            if is_sentiment_negative(symbol):
                continue

            df = data[symbol]
            from analysis.indicators import add_all_indicators
            df_ind = add_all_indicators(df).dropna()
            if df_ind.empty:
                continue

            latest = df_ind.iloc[-1]
            price = float(latest["close"])
            atr = float(latest["atr"])

            shares, stop_loss, take_profit = self.risk.calculate_position_size(
                equity=equity,
                price=price,
                atr=atr,
            )

            # Ensure we have enough cash
            cost = price * shares
            if cost > cash * 0.95:
                logger.info(f"Insufficient cash for {symbol} (need ${cost:.0f}, have ${cash:.0f})")
                continue

            logger.info(
                f"Signal: BUY {symbol} | score={score} | "
                f"x{shares} @ ${price:.2f} | SL=${stop_loss:.2f} TP=${take_profit:.2f}"
            )

            if not self.dry_run:
                order_id = place_bracket_order(
                    symbol=symbol,
                    qty=shares,
                    stop_loss_price=stop_loss,
                    take_profit_price=take_profit,
                )
                if order_id:
                    self.portfolio.open_trade(
                        symbol=symbol,
                        shares=shares,
                        entry_price=price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        order_id=order_id,
                    )
                    self.risk.record_trade()
                    cash -= cost
            else:
                logger.info(f"[DRY RUN] Would place: BUY {shares} {symbol} @ ${price:.2f}")

        # --- IPO pass: trade mature IPOs with lightweight momentum signal ---
        if not self.risk.max_positions_reached(len(get_positions())):
            self._trade_ipos(open_symbols, equity, cash)

    def _trade_ipos(self, open_symbols: set, equity: float, cash: float) -> None:
        """
        Separate trading pass for recently listed IPOs.
        Uses a lightweight momentum signal instead of the full indicator suite.
        """
        tradeable = get_tradeable_ipos()
        if not tradeable:
            return

        from data.fetcher import fetch_bars
        logger.info(f"Checking {len(tradeable)} mature IPOs: {tradeable}")

        for symbol in tradeable:
            if self.risk.max_positions_reached(len(get_positions())):
                break
            if symbol in open_symbols:
                continue
            if is_sentiment_negative(symbol):
                continue

            try:
                df = fetch_bars(symbol, lookback_days=30)
                if df.empty or len(df) < 6:
                    continue

                signal, score = generate_ipo_signal(df)
                if signal != "BUY":
                    continue

                price = float(df["close"].iloc[-1])
                shares, stop_loss, take_profit = ipo_position_size(equity, price)
                cost = price * shares

                if cost > cash * 0.95:
                    logger.info(f"IPO {symbol}: insufficient cash (need ${cost:.0f})")
                    continue

                logger.info(
                    f"IPO Signal: BUY {symbol} | score={score} | "
                    f"x{shares} @ ${price:.2f} | SL=${stop_loss:.2f} TP=${take_profit:.2f}"
                )

                if not self.dry_run:
                    # Queue for human approval (60 min window before auto-execute)
                    from analysis.news_scanner import _fetch_articles
                    articles = _fetch_articles(symbols=[symbol], hours=24, limit=1)
                    headline = articles[0].headline if articles else ""
                    queue_approval(
                        symbol=symbol,
                        shares=shares,
                        price=price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        score=score,
                        headline=headline,
                        trade_type="IPO",
                    )
                else:
                    logger.info(f"[DRY RUN] IPO: Would queue approval for {shares} {symbol} @ ${price:.2f}")

            except Exception as exc:
                logger.error(f"IPO trade error for {symbol}: {exc}")

    def _execute_approved(self, entry: dict, auto: bool = False) -> None:
        """Place a bracket order for an approved (or auto-expired) IPO trade."""
        symbol = entry["symbol"]
        label = "Auto-executing" if auto else "Executing approved"
        logger.info(f"{label} IPO trade: {symbol} x{entry['shares']} @ ${entry['price']:.2f}")
        order_id = place_bracket_order(
            symbol=symbol,
            qty=entry["shares"],
            stop_loss_price=entry["stop_loss"],
            take_profit_price=entry["take_profit"],
        )
        if order_id:
            self.portfolio.open_trade(
                symbol=symbol,
                shares=entry["shares"],
                entry_price=entry["price"],
                stop_loss=entry["stop_loss"],
                take_profit=entry["take_profit"],
                order_id=order_id,
            )
            self.risk.record_trade()
            mark_executed(entry["id"], auto=auto)

    def _auto_execute_expired_approvals(self) -> None:
        """Auto-execute any IPO trades whose 60-min approval window has elapsed."""
        expired = get_expired()
        if not expired:
            return
        open_positions = get_positions()
        for entry in expired:
            symbol = entry["symbol"]
            if symbol in open_positions:
                mark_executed(entry["id"], auto=True)
                continue
            if self.risk.max_positions_reached(len(get_positions())):
                break
            self._execute_approved(entry, auto=True)

    def _check_exits(self, open_positions: dict, equity: float) -> None:
        """
        Signal-based exit check for existing positions.
        Bracket orders handle SL/TP automatically on the broker side;
        this function handles signal-based exits (e.g. death cross).
        """
        from data.fetcher import fetch_bars
        for symbol, pos in open_positions.items():
            try:
                df = fetch_bars(symbol, lookback_days=100)
                if df.empty:
                    continue
                signal, score = self.strategy.generate_signal(df)
                if signal == Signal.SELL:
                    logger.info(f"Exit signal for {symbol} (score={score})")
                    if not self.dry_run:
                        if close_position(symbol):
                            from data.fetcher import fetch_latest_price
                            exit_price = fetch_latest_price(symbol) or pos["avg_entry"]
                            self.portfolio.close_trade(symbol, exit_price, status="SIGNAL_EXIT")
                    else:
                        logger.info(f"[DRY RUN] Would close {symbol}")
            except Exception as exc:
                logger.error(f"Exit check error for {symbol}: {exc}")

    def eod_summary(self) -> None:
        """End-of-day summary."""
        logger.info("=== END OF DAY SUMMARY ===")
        try:
            account = get_account()
            positions = get_positions()
            self.portfolio.take_snapshot(
                equity=account["equity"],
                cash=account["cash"],
                open_positions=len(positions),
            )
            self.portfolio.print_summary()
        except Exception as exc:
            logger.error(f"EoD summary failed: {exc}")

    # ----------------------------------------------------------------- Run
    def start(self) -> None:
        print_banner()
        logger.info("Scheduling trading jobs...")

        # Market open setup at 9:31 AM ET
        schedule.every().monday.at("13:31").do(self.morning_setup)  # UTC
        schedule.every().tuesday.at("13:31").do(self.morning_setup)
        schedule.every().wednesday.at("13:31").do(self.morning_setup)
        schedule.every().thursday.at("13:31").do(self.morning_setup)
        schedule.every().friday.at("13:31").do(self.morning_setup)

        # Scan every 10 minutes during market hours
        schedule.every(10).minutes.do(self.scan_and_trade)

        # End-of-day summary at 4:05 PM ET
        schedule.every().monday.at("20:05").do(self.eod_summary)
        schedule.every().tuesday.at("20:05").do(self.eod_summary)
        schedule.every().wednesday.at("20:05").do(self.eod_summary)
        schedule.every().thursday.at("20:05").do(self.eod_summary)
        schedule.every().friday.at("20:05").do(self.eod_summary)

        logger.info("Bot is running. Press Ctrl+C to stop.")

        # Run once immediately on startup
        self.scan_and_trade()

        try:
            while self._running:
                schedule.run_pending()
                time.sleep(30)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.eod_summary()


def main() -> None:
    parser = argparse.ArgumentParser(description="STOX Algorithmic Trading Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan for signals and log them, but place no real orders",
    )
    args = parser.parse_args()

    if not Config.ALPACA_API_KEY or not Config.ALPACA_API_SECRET:
        print(
            "\nERROR: Alpaca API keys not set.\n"
            "Copy .env.example to .env and add your keys from https://alpaca.markets\n"
        )
        sys.exit(1)

    ok, msg = validate_credentials()
    if not ok:
        print(f"\nERROR: Alpaca authentication failed — {msg}")
        print("Run:  python check_auth.py  for a full diagnostic.\n")
        sys.exit(1)

    bot = TradingBot(dry_run=args.dry_run)
    bot.start()


if __name__ == "__main__":
    main()
