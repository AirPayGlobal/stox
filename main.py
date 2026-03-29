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
from analysis.market_filter import is_vix_too_high
from analysis.news_scanner import find_news_catalysts
from analysis.sentiment_engine import is_sentiment_blocked, apply_sentiment_boost, get_composite_sentiment
from analysis.earnings_calendar import is_earnings_blackout, warn_open_positions_near_earnings
from analysis.sector_rotation import is_in_top_sectors, get_sector_rankings
from analysis.timeframe import weekly_confirms_entry, weekly_confirms_short
from analysis.short_signals import screen_short_candidates, short_position_size
from analysis.thirteen_f import get_thirteen_f_score
from analysis.ipo_tracker import (
    register_new_ipos,
    get_tradeable_ipos,
    generate_ipo_signal,
    ipo_position_size,
)
from trading.approval_queue import submit as queue_approval, get_expired, mark_executed
from trading.alpaca_client import place_long_order, place_short_order, cover_short_order
from analysis.pairs_trading import screen_pairs, pair_position_sizes, PairSignal, PAIRS
from trading.pairs_manager import (
    get_open_pairs, open_pair as record_open_pair, close_pair as record_close_pair,
)
from analysis.regime import detect_regime, get_sizing_multiplier, regime_allows_longs, regime_favors_shorts, Regime
from analysis.ml_signals import is_ml_approved
from analysis.universe import get_full_universe
from analysis.risk_analytics import record_equity, compute_analytics
from utils.logger import get_logger

logger = get_logger("main")


def _position_returns(open_positions: dict, data: dict) -> dict:
    """
    Build a dict of {symbol: pd.Series of daily returns} for all open positions
    that have price data available. Used by the correlation check.
    """
    import pandas as pd
    result = {}
    for sym in open_positions:
        if sym in data and not data[sym].empty:
            result[sym] = data[sym]["close"].pct_change().dropna()
    return result


def _is_too_correlated(symbol: str, data: dict, open_returns: dict) -> bool:
    """
    Return True if the candidate symbol's 30-day returns are correlated > MAX_POSITION_CORRELATION
    with any currently open position. Prevents loading up on highly correlated tech names.
    """
    if not open_returns or symbol not in data or data[symbol].empty:
        return False

    import pandas as pd
    candidate_ret = data[symbol]["close"].pct_change().dropna().tail(30)

    for pos_sym, pos_ret in open_returns.items():
        aligned = pd.concat([candidate_ret, pos_ret.tail(30)], axis=1).dropna()
        if len(aligned) < 10:
            continue
        corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if corr > Config.MAX_POSITION_CORRELATION:
            logger.info(
                f"Correlation limit: {symbol} vs {pos_sym} r={corr:.2f} "
                f"> {Config.MAX_POSITION_CORRELATION} — skipping"
            )
            return True
    return False


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

        # --- EXIT MANAGEMENT runs every scan regardless of entry filters ---
        # Fetching positions here so exits always have fresh data.
        open_positions = get_positions()
        pending_symbols = get_pending_symbols()
        open_symbols = set(open_positions.keys()) | pending_symbols
        self._check_exits(open_positions, equity)

        # --- ENTRY FILTERS — early-returns here do NOT skip exits (done above) ---

        # Daily loss circuit-breaker
        if self.risk.daily_loss_exceeded(equity):
            logger.warning("Daily loss limit hit — no new trades today.")
            return

        # VIX filter — skip all new entries during high-fear market conditions
        if is_vix_too_high():
            logger.warning("VIX filter active — no new BUY entries this scan.")
            return

        # Regime detection — log regime and block HIGH_VOL entries
        regime = detect_regime()
        sizing_mult = get_sizing_multiplier()
        logger.info(f"Market regime: {regime.value} (sizing multiplier: {sizing_mult:.1f}x)")
        if Config.REGIME_FILTER_ENABLED and not regime_allows_longs():
            logger.warning(f"Regime {regime.value} — no new long entries this scan.")
            return

        # Refresh positions after exits before evaluating capacity
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

        # Build scan universe: static watchlist + dynamic breakouts + news catalysts
        base_universe = get_full_universe()
        scan_symbols = base_universe + [s for s in news_symbols if s not in base_universe]
        dynamic_extra = len(base_universe) - len(Config.WATCHLIST)
        logger.info(
            f"Scanning {len(scan_symbols)} symbols "
            f"({dynamic_extra} dynamic breakouts, {len(news_symbols)} from news)..."
        )
        data = fetch_batch(scan_symbols, lookback_days=100)

        # Screen for signals
        candidates = screen_universe(data)
        buy_candidates = [
            (sym, sig, score)
            for sym, sig, score in candidates
            if sig == Signal.BUY and sym not in open_symbols
        ]

        # Re-rank using 4-source composite sentiment (options+analyst+insider+retail)
        if buy_candidates:
            buy_candidates = apply_sentiment_boost(buy_candidates)

        logger.info(f"Buy candidates: {len(buy_candidates)}")

        # Log sector rankings once per scan for visibility
        try:
            rankings = get_sector_rankings()
            if rankings:
                logger.info(
                    "Sector rankings: "
                    + " | ".join(f"#{r} {e}" for e, _, r in rankings[:4])
                )
        except Exception:
            pass

        # Pre-compute returns for open positions (used in correlation check)
        open_returns = _position_returns(open_positions, data)

        # Kelly inputs from portfolio history
        port_summary = self.portfolio.summary()
        kelly_kwargs = dict(
            win_rate=port_summary.get("win_rate", 0.0),
            avg_win_pct=port_summary.get("avg_win", 0.0) / max(equity, 1),
            avg_loss_pct=abs(port_summary.get("avg_loss", 0.0)) / max(equity, 1),
            trade_count=port_summary.get("total_trades", 0),
        )

        filter_stats = {"sector": 0, "weekly": 0, "sentiment": 0,
                        "earnings": 0, "correlation": 0, "ml": 0, "passed": 0}

        for symbol, signal, score in buy_candidates:
            if self.risk.max_positions_reached(len(get_positions())):
                break

            # Sector rotation — skip if sector not in top N by momentum
            if not is_in_top_sectors(symbol):
                logger.debug(f"Filter SECTOR: {symbol} not in top-{Config.SECTOR_TOP_N} sectors")
                filter_stats["sector"] += 1
                continue

            # Multi-timeframe: weekly chart must confirm the daily BUY
            # In BEAR regime, skip this filter — regime already cuts size to 50%
            if Config.WEEKLY_CONFIRM_REQUIRED and regime.value != "BEAR":
                if not weekly_confirms_entry(data[symbol], symbol):
                    logger.info(f"Filter WEEKLY: {symbol} — weekly chart not bullish")
                    filter_stats["weekly"] += 1
                    continue

            # 4-source composite sentiment filter (options + analyst + insider + retail)
            if is_sentiment_blocked(symbol):
                logger.info(f"Filter SENTIMENT: {symbol} — composite sentiment too negative")
                filter_stats["sentiment"] += 1
                continue

            # Earnings blackout — skip if reporting in ≤ EARNINGS_BLACKOUT_DAYS
            if is_earnings_blackout(symbol):
                logger.info(f"Filter EARNINGS: {symbol} — earnings blackout active")
                filter_stats["earnings"] += 1
                continue

            # Correlation limit — skip if too correlated with any open position
            if _is_too_correlated(symbol, data, open_returns):
                filter_stats["correlation"] += 1
                continue

            # 13F smart money boost — boost score if top funds are adding
            if Config.THIRTEEN_F_ENABLED:
                try:
                    tf_score = get_thirteen_f_score(symbol)
                    if tf_score != 0:
                        score += int(tf_score * Config.THIRTEEN_F_BOOST_SCALE)
                        logger.info(f"13F boost {symbol}: {tf_score:+d} → adjusted score={score}")
                except Exception:
                    pass

            df = data[symbol]
            from analysis.indicators import add_all_indicators
            df_ind = add_all_indicators(df).dropna()
            if df_ind.empty:
                continue

            # ML signal booster — require minimum probability estimate; fails open during warmup
            if Config.ML_SIGNAL_ENABLED:
                if not is_ml_approved(symbol, df, Config.ML_MIN_PROBABILITY):
                    logger.info(f"Filter ML: {symbol} — below p={Config.ML_MIN_PROBABILITY:.2f}")
                    filter_stats["ml"] += 1
                    continue

            filter_stats["passed"] += 1

            latest = df_ind.iloc[-1]
            price = float(latest["close"])
            atr = float(latest["atr"])

            shares, stop_loss, take_profit = self.risk.calculate_position_size(
                equity=equity,
                price=price,
                atr=atr,
                **kelly_kwargs,
            )

            # Regime sizing multiplier — reduce position size in choppy/bear markets
            if Config.REGIME_FILTER_ENABLED and sizing_mult < 1.0:
                shares = max(1, int(shares * sizing_mult))
                logger.debug(f"Regime {regime.value}: {symbol} shares scaled to {shares}")

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

        # Log filter funnel summary for diagnostics
        if buy_candidates:
            logger.info(
                f"Filter funnel ({len(buy_candidates)} candidates): "
                f"sector={filter_stats['sector']} weekly={filter_stats['weekly']} "
                f"sentiment={filter_stats['sentiment']} earnings={filter_stats['earnings']} "
                f"corr={filter_stats['correlation']} ml={filter_stats['ml']} "
                f"→ passed={filter_stats['passed']}"
            )

        # --- IPO pass: trade mature IPOs with lightweight momentum signal ---
        if not self.risk.max_positions_reached(len(get_positions())):
            self._trade_ipos(open_symbols, equity, cash)

        # --- Short selling pass ---
        try:
            self._scan_shorts(candidates, data, open_symbols, equity, cash)
        except Exception as exc:
            logger.error(f"Short scan error: {exc}")

        # --- Pairs trading: market-neutral stat-arb on cointegrated pairs ---
        try:
            self.scan_pairs(data, equity)
        except Exception as exc:
            logger.error(f"Pairs scan error: {exc}")

    def _scan_shorts(
        self,
        candidates: list,
        data: dict,
        open_symbols: set,
        equity: float,
        cash: float,
    ) -> None:
        """Short-sell high-conviction SELL signals after all filters pass."""
        short_candidates = screen_short_candidates(candidates, data, open_symbols)
        if not short_candidates:
            return

        # Count existing short positions
        existing_shorts = sum(
            1 for t in self.portfolio.trades
            if t.side == "SHORT" and t.status == "OPEN"
        )

        for symbol, score in short_candidates:
            if existing_shorts >= Config.SHORT_MAX_POSITIONS:
                break

            # Composite sentiment must be negative to short
            try:
                from analysis.sentiment_engine import get_composite_sentiment
                sent = get_composite_sentiment(symbol)
                if sent["composite"] > Config.SHORT_MIN_SENTIMENT:
                    logger.info(
                        f"Short skipped {symbol}: sentiment {sent['composite']:+.1f} "
                        f"not negative enough (need < {Config.SHORT_MIN_SENTIMENT})"
                    )
                    continue
            except Exception:
                pass

            if symbol not in data or data[symbol].empty:
                continue

            df_ind = __import__("analysis.indicators", fromlist=["add_all_indicators"]).add_all_indicators(data[symbol]).dropna()
            if df_ind.empty:
                continue

            price = float(df_ind["close"].iloc[-1])
            atr   = float(df_ind["atr"].iloc[-1])
            shares, stop_loss, take_profit = short_position_size(equity, price, atr)
            cost = price * shares

            if cost > cash * 0.95:
                logger.info(f"Short {symbol}: insufficient cash")
                continue

            logger.info(
                f"SHORT signal: {symbol} | score={score} | "
                f"x{shares} @ ${price:.2f} | SL=${stop_loss:.2f} TP=${take_profit:.2f}"
            )

            if not self.dry_run:
                order_id = place_short_order(symbol, shares)
                if order_id:
                    self.portfolio.open_trade(
                        symbol=symbol,
                        shares=shares,
                        entry_price=price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        order_id=order_id,
                        side="SHORT",
                    )
                    self.risk.record_trade()
                    existing_shorts += 1
            else:
                logger.info(f"[DRY RUN] Would SHORT {shares} {symbol} @ ${price:.2f}")

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
            if is_sentiment_blocked(symbol):
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

    def scan_pairs(self, data: dict, equity: float) -> None:
        """
        Pairs trading scan — runs after the main signal loop each cycle.

        1. Fetch data for all pair symbols not already in the main scan.
        2. Check open pair positions for EXIT or STOP signals.
        3. Check for new ENTRY signals (if under max pair positions).
        """
        from data.fetcher import fetch_bars, fetch_latest_price

        # Ensure we have data for all pair symbols
        all_pair_symbols = {sym for pair in PAIRS for sym in pair}
        missing = all_pair_symbols - set(data.keys())
        for sym in missing:
            df = fetch_bars(sym, lookback_days=Config.PAIRS_WINDOW + 10)
            if not df.empty:
                data[sym] = df

        open_pairs = get_open_pairs()
        signals = screen_pairs(data, open_pairs)

        # --- Manage exits first ---
        for sym_a, sym_b, signal, z, beta in signals:
            if signal not in (PairSignal.EXIT, PairSignal.STOP):
                continue

            pair = next(
                (p for p in open_pairs
                 if (p["symbol_a"], p["symbol_b"]) in [(sym_a, sym_b), (sym_b, sym_a)]),
                None,
            )
            if not pair:
                continue

            sym_long  = pair["symbol_long"]
            sym_short = pair["symbol_short"]
            p_long    = fetch_latest_price(sym_long)  or pair["price_long"]
            p_short   = fetch_latest_price(sym_short) or pair["price_short"]
            reason    = "MEAN_REVERSION" if signal == PairSignal.EXIT else "STOP_LOSS"

            logger.info(
                f"Pairs {reason}: closing {pair['pair_id']} "
                f"LONG {sym_long} / SHORT {sym_short} z={z:.2f}"
            )

            if not self.dry_run:
                # Sell long leg, cover short leg simultaneously
                close_position(sym_long)
                cover_short_order(sym_short, pair["qty_short"])
                record_close_pair(pair["pair_id"], p_long, p_short, z, reason)
            else:
                logger.info(f"[DRY RUN] Would close pair {pair['pair_id']}")

        # Reload after potential closures
        open_pairs = get_open_pairs()

        # --- New entries ---
        if len(open_pairs) >= Config.PAIRS_MAX_POSITIONS:
            logger.debug(f"Pairs: max positions ({Config.PAIRS_MAX_POSITIONS}) reached")
            return

        for sym_a, sym_b, signal, z, beta in signals:
            if signal not in (PairSignal.LONG_A_SHORT_B, PairSignal.LONG_B_SHORT_A):
                continue

            if len(get_open_pairs()) >= Config.PAIRS_MAX_POSITIONS:
                break

            # Determine which leg is long, which is short
            if signal == PairSignal.LONG_A_SHORT_B:
                sym_long, sym_short = sym_a, sym_b
            else:
                sym_long, sym_short = sym_b, sym_a

            price_long  = float(data[sym_long]["close"].iloc[-1])
            price_short = float(data[sym_short]["close"].iloc[-1])

            qty_long, qty_short = pair_position_sizes(equity, price_long, price_short)
            cost = price_long * qty_long

            logger.info(
                f"Pairs ENTRY: LONG {qty_long}×{sym_long} @ ${price_long:.2f} "
                f"/ SHORT {qty_short}×{sym_short} @ ${price_short:.2f} "
                f"z={z:.2f} β={beta:.3f}"
            )

            if not self.dry_run:
                oid_long  = place_long_order(sym_long, qty_long)
                oid_short = place_short_order(sym_short, qty_short)

                if oid_long and oid_short:
                    record_open_pair(
                        symbol_a=sym_a,
                        symbol_b=sym_b,
                        direction=signal,
                        symbol_long=sym_long,
                        symbol_short=sym_short,
                        qty_long=qty_long,
                        qty_short=qty_short,
                        price_long=price_long,
                        price_short=price_short,
                        hedge_ratio=beta,
                        z_score=z,
                        order_long_id=oid_long,
                        order_short_id=oid_short,
                    )
                else:
                    logger.warning(f"Pairs entry partial failure: {sym_long}/{sym_short}")
            else:
                logger.info(
                    f"[DRY RUN] Would open pair: "
                    f"LONG {qty_long}×{sym_long} / SHORT {qty_short}×{sym_short}"
                )

    def _check_exits(self, open_positions: dict, equity: float) -> None:
        """
        Exit checks for existing positions:
          1. Trailing stop  — close if price drops > TRAILING_STOP_PCT from peak (long)
                             or rises > TRAILING_STOP_PCT above low (short)
          2. Hard stop/TP   — for shorts managed here since they have no bracket
          3. Signal exit    — close long on SELL signal; close short on BUY signal
          4. Earnings warn  — log alert when earnings < 3 days away
        Bracket orders still handle the hard SL/TP on the broker side for longs.
        """
        from data.fetcher import fetch_bars, fetch_latest_price
        from trading.alpaca_client import cover_short_order

        # Warn about any open positions approaching earnings
        warn_open_positions_near_earnings(list(open_positions.keys()), days_before=3)

        for symbol, pos in open_positions.items():
            try:
                df = fetch_bars(symbol, lookback_days=100)
                if df.empty:
                    continue

                current_price = pos["market_value"] / pos["qty"] if pos["qty"] else float(df["close"].iloc[-1])

                trade = self.portfolio.get_open_trade(symbol)
                is_short = trade and trade.side == "SHORT"

                if is_short:
                    # ---- Short position exit logic ----
                    entry = trade.entry_price
                    qty   = trade.shares

                    # Hard stop loss: price rose above stop level
                    stop_price = trade.stop_loss or entry * (1 + Config.STOP_LOSS_PCT)
                    if current_price >= stop_price:
                        logger.info(
                            f"SHORT stop loss: {symbol} price=${current_price:.2f} "
                            f"stop=${stop_price:.2f}"
                        )
                        if not self.dry_run:
                            if cover_short_order(symbol, qty):
                                self.portfolio.close_trade(symbol, current_price, status="STOPPED")
                        else:
                            logger.info(f"[DRY RUN] Would cover short (stop) {symbol}")
                        continue

                    # Hard take profit: price fell to target
                    tp_price = trade.take_profit or entry * (1 - Config.TAKE_PROFIT_PCT)
                    if tp_price > 0 and current_price <= tp_price:
                        logger.info(
                            f"SHORT take profit: {symbol} price=${current_price:.2f} "
                            f"tp=${tp_price:.2f}"
                        )
                        if not self.dry_run:
                            if cover_short_order(symbol, qty):
                                self.portfolio.close_trade(symbol, current_price, status="TOOK_PROFIT")
                        else:
                            logger.info(f"[DRY RUN] Would cover short (TP) {symbol}")
                        continue

                    # Trailing stop for shorts: high_water_mark repurposed as low_water_mark
                    lwm = trade.high_water_mark if trade.high_water_mark > 0 else entry
                    if current_price < lwm:
                        trade.high_water_mark = current_price
                        self.portfolio.save()
                        lwm = current_price

                    trail_ceiling = lwm * (1 + Config.TRAILING_STOP_PCT)
                    if current_price > trail_ceiling:
                        logger.info(
                            f"SHORT trailing stop: {symbol} price=${current_price:.2f} "
                            f"low=${lwm:.2f} ceiling=${trail_ceiling:.2f}"
                        )
                        if not self.dry_run:
                            if cover_short_order(symbol, qty):
                                self.portfolio.close_trade(symbol, current_price, status="TRAILING_STOP")
                        else:
                            logger.info(f"[DRY RUN] Would cover short (trailing) {symbol}")
                        continue

                    # Signal-based exit for short: cover on BUY signal
                    signal, score = self.strategy.generate_signal(df)
                    if signal == Signal.BUY:
                        logger.info(f"SHORT signal exit for {symbol} — BUY reversal (score={score})")
                        if not self.dry_run:
                            if cover_short_order(symbol, qty):
                                exit_price = fetch_latest_price(symbol) or current_price
                                self.portfolio.close_trade(symbol, exit_price, status="SIGNAL_EXIT")
                        else:
                            logger.info(f"[DRY RUN] Would cover short (signal) {symbol}")

                else:
                    # ---- Long position exit logic ----
                    if trade:
                        # Initialise high_water_mark on first check
                        hwm = trade.high_water_mark if trade.high_water_mark > 0 else trade.entry_price
                        if current_price > hwm:
                            trade.high_water_mark = current_price
                            self.portfolio.save()
                            hwm = current_price

                        trail_floor = hwm * (1 - Config.TRAILING_STOP_PCT)
                        if current_price < trail_floor:
                            logger.info(
                                f"Trailing stop: {symbol} price=${current_price:.2f} "
                                f"peak=${hwm:.2f} floor=${trail_floor:.2f} "
                                f"({Config.TRAILING_STOP_PCT:.0%} trail)"
                            )
                            if not self.dry_run:
                                if close_position(symbol):
                                    self.portfolio.close_trade(symbol, current_price, status="TRAILING_STOP")
                            else:
                                logger.info(f"[DRY RUN] Would trailing-stop {symbol}")
                            continue  # skip signal check for this symbol

                    # --- Signal-based exit (death cross / SELL) ---
                    signal, score = self.strategy.generate_signal(df)
                    if signal == Signal.SELL:
                        logger.info(f"Exit signal for {symbol} (score={score})")
                        if not self.dry_run:
                            if close_position(symbol):
                                exit_price = fetch_latest_price(symbol) or pos["avg_entry"]
                                self.portfolio.close_trade(symbol, exit_price, status="SIGNAL_EXIT")
                        else:
                            logger.info(f"[DRY RUN] Would close {symbol}")

            except Exception as exc:
                logger.error(f"Exit check error for {symbol}: {exc}")

        # --- Reconcile bracket-fired exits ---
        # When Alpaca's bracket TP or SL fills, the position disappears from
        # get_positions() but our local portfolio still shows it as OPEN.
        # Detect these and close them in the local record.
        self._reconcile_broker_exits(open_positions)

    def _reconcile_broker_exits(self, open_positions: dict) -> None:
        """
        Close any trade that is OPEN in our portfolio but no longer present
        in Alpaca's positions — meaning the broker's bracket TP or SL fired.
        Queries Alpaca's order history for the exact fill price; falls back to
        TP/SL proximity heuristic if the order lookup fails.
        """
        from trading.alpaca_client import get_filled_exit_price
        from data.fetcher import fetch_latest_price

        orphaned = [
            t for t in self.portfolio.trades
            if t.status == "OPEN" and t.symbol not in open_positions
        ]

        for trade in orphaned:
            symbol = trade.symbol
            try:
                fill_price, fill_status = get_filled_exit_price(symbol, trade.opened_at)

                if not fill_price:
                    # Fallback: current price vs stored TP/SL levels
                    cur = fetch_latest_price(symbol) or 0.0
                    tp, sl = trade.take_profit, trade.stop_loss
                    if tp > 0 and cur >= tp * 0.95:
                        fill_price, fill_status = tp, "TOOK_PROFIT"
                    elif sl > 0 and cur <= sl * 1.05:
                        fill_price, fill_status = sl, "STOPPED"
                    else:
                        fill_price = cur or trade.entry_price
                        fill_status = "CLOSED"

                logger.info(
                    f"Reconciled broker exit: {symbol} @ ${fill_price:.2f} "
                    f"[{fill_status}] (bracket fired while bot was in filter guard)"
                )
                self.portfolio.close_trade(symbol, fill_price, status=fill_status)

            except Exception as exc:
                logger.error(f"Reconcile error for {symbol}: {exc}")

    def eod_summary(self) -> None:
        """End-of-day summary with risk analytics."""
        logger.info("=== END OF DAY SUMMARY ===")
        try:
            account   = get_account()
            positions = get_positions()
            equity    = account["equity"]

            self.portfolio.take_snapshot(
                equity=equity,
                cash=account["cash"],
                open_positions=len(positions),
            )
            self.portfolio.print_summary()

            # Record equity for risk analytics
            record_equity(equity)

            # Log key risk metrics
            try:
                metrics = compute_analytics(portfolio=self.portfolio)
                parts = []
                if metrics["sharpe"]           is not None: parts.append(f"Sharpe={metrics['sharpe']:.2f}")
                if metrics["sortino"]          is not None: parts.append(f"Sortino={metrics['sortino']:.2f}")
                if metrics["max_drawdown_pct"] is not None: parts.append(f"MaxDD={metrics['max_drawdown_pct']:.1f}%")
                if metrics["win_rate"]         is not None: parts.append(f"WinRate={metrics['win_rate']:.1f}%")
                if metrics["profit_factor"]    is not None: parts.append(f"PF={metrics['profit_factor']:.2f}x")
                if parts:
                    logger.info("Risk metrics: " + " | ".join(parts))
            except Exception as exc:
                logger.debug(f"Risk analytics error: {exc}")

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
