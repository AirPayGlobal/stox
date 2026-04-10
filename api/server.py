"""
STOX Dashboard API
==================
FastAPI backend that exposes account data, positions, trades, portfolio
metrics, and bot control endpoints. In production it also serves the
compiled React SPA from dashboard/dist/.
"""
from __future__ import annotations

# ------------------------------------------------------------------ Fast boot
# Only stdlib + fastapi are imported here so the app object and the /health
# endpoint are available immediately when Uvicorn starts.  All heavy project
# imports (trading clients, config, ML modules, etc.) are deferred to the
# individual route handlers that actually need them.

import base64
import os
import json
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="STOX Dashboard", docs_url=None, redoc_url=None)

# ------------------------------------------------------------------ Health
# Registered first — before any other initialisation — so Railway's
# healthcheck gets a response even if later imports are slow.

@app.get("/health")
def health() -> dict:
    """Railway health check — no auth required."""
    return {"status": "ok"}


# ------------------------------------------------------------------ Lazy helpers

def _bot_manager():
    from api.bot_manager import bot_manager as _bm
    return _bm

def _config():
    from config import Config as _cfg
    return _cfg

def _logger():
    from utils.logger import get_logger as _gl
    return _gl(__name__)

_portfolio_cache: dict = {"instance": None}

def _portfolio():
    """Return a cached Portfolio instance, reloading from disk only once per process."""
    if _portfolio_cache["instance"] is None:
        from trading.portfolio import Portfolio
        _portfolio_cache["instance"] = Portfolio()
    return _portfolio_cache["instance"]


# ------------------------------------------------------------------ Auto-start
# Deferred to startup event so /health is responsive before the bot's
# heavy imports (ML, yfinance, sklearn) begin loading in the background thread.

@app.on_event("startup")
async def _auto_start_bot() -> None:
    import asyncio
    await asyncio.sleep(1)
    # Restore persisted settings before starting the bot
    saved = _load_settings()
    if saved:
        cfg = _config()
        for k, v in saved.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    _bot_manager().start(dry_run=False)

_DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
_DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "stox")


# ------------------------------------------------------------------ Auth
# Uses Bearer token (base64 user:pass) to avoid browser intercepting 401s

def verify(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            decoded = base64.b64decode(auth[7:]).decode()
            username, password = decoded.split(":", 1)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    elif auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            username, password = decoded.split(":", 1)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    ok_user = secrets.compare_digest(username.encode(), _DASHBOARD_USER.encode())
    ok_pass = secrets.compare_digest(password.encode(), _DASHBOARD_PASS.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return username


# ------------------------------------------------------------------ API

@app.get("/api/market-status")
def market_status(_: str = Depends(verify)) -> dict[str, Any]:
    """Return whether the US market is currently open."""
    try:
        from trading.alpaca_client import is_market_open
        return {"is_open": is_market_open()}
    except Exception:
        return {"is_open": None}


@app.get("/api/account")
def account(_: str = Depends(verify)) -> dict[str, Any]:
    try:
        from trading.alpaca_client import get_account
        data = get_account()
        cfg = _config()
        base = cfg.BASE_CAPITAL
        equity = data.get("equity", 0)
        cash_balance = data.get("cash", 0)
        data["base_capital"] = base
        data["unrealised_growth"] = equity - base           # total equity gain (open + closed), may be negative
        data["withdrawable_cash"] = max(0.0, cash_balance - base)  # actual idle cash above base — safe to withdraw
        data["withdrawal_alert"] = data["withdrawable_cash"] >= base * cfg.PROFIT_WITHDRAWAL_ALERT_PCT
        return data
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


_SETTINGS_FILE = Path(os.environ.get("SETTINGS_FILE", "/data/settings.json"))
_EDITABLE_SETTINGS = {"BASE_CAPITAL", "PROFIT_WITHDRAWAL_ALERT_PCT"}


def _load_settings() -> dict:
    try:
        if _SETTINGS_FILE.exists():
            return json.loads(_SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_settings(patch: dict) -> dict:
    current = _load_settings()
    current.update(patch)
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(json.dumps(current, indent=2))
    return current


@app.get("/api/settings")
def get_settings(_: str = Depends(verify)) -> dict:
    cfg = _config()
    return {
        "BASE_CAPITAL": _load_settings().get("BASE_CAPITAL", cfg.BASE_CAPITAL),
        "PROFIT_WITHDRAWAL_ALERT_PCT": _load_settings().get(
            "PROFIT_WITHDRAWAL_ALERT_PCT", cfg.PROFIT_WITHDRAWAL_ALERT_PCT
        ),
    }


@app.patch("/api/settings")
async def patch_settings(request: Request, _: str = Depends(verify)) -> dict:
    body = await request.json()
    patch = {k: v for k, v in body.items() if k in _EDITABLE_SETTINGS}
    if not patch:
        raise HTTPException(status_code=400, detail="No valid settings fields provided")
    # Validate types
    if "BASE_CAPITAL" in patch:
        val = float(patch["BASE_CAPITAL"])
        if val < 1000:
            raise HTTPException(status_code=400, detail="BASE_CAPITAL must be at least $1,000")
        patch["BASE_CAPITAL"] = val
    if "PROFIT_WITHDRAWAL_ALERT_PCT" in patch:
        val = float(patch["PROFIT_WITHDRAWAL_ALERT_PCT"])
        if not (0.01 <= val <= 1.0):
            raise HTTPException(status_code=400, detail="PROFIT_WITHDRAWAL_ALERT_PCT must be between 1% and 100%")
        patch["PROFIT_WITHDRAWAL_ALERT_PCT"] = val
    saved = _save_settings(patch)
    # Apply to live config so the running bot picks it up immediately
    cfg = _config()
    for k, v in patch.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return saved


@app.get("/api/positions")
def positions(_: str = Depends(verify)) -> dict[str, Any]:
    try:
        from trading.alpaca_client import get_positions
        from analysis.earnings_calendar import days_to_earnings
        result = get_positions()

        # Enrich with portfolio take_profit/stop_loss and earnings date
        port = _portfolio()
        for symbol, pos in result.items():
            trade = port.get_open_trade(symbol)
            pos["take_profit"] = trade.take_profit if trade else None
            pos["stop_loss"]   = trade.stop_loss   if trade else None
            try:
                dte = days_to_earnings(symbol)
                pos["days_to_earnings"] = dte
            except Exception:
                pos["days_to_earnings"] = None

        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/trades")
def trades(_: str = Depends(verify)) -> dict[str, Any]:
    from trading.portfolio import Portfolio
    p = _portfolio()
    return {"trades": [asdict(t) for t in reversed(p.trades)]}


@app.get("/api/summary")
def summary(_: str = Depends(verify)) -> dict[str, Any]:
    from trading.portfolio import Portfolio
    return _portfolio().summary()


@app.get("/api/equity-curve")
def equity_curve(_: str = Depends(verify)) -> dict[str, Any]:
    from trading.alpaca_client import get_portfolio_history
    from trading.portfolio import Portfolio
    # Prefer Alpaca portfolio history (full trail from account creation)
    snapshots = get_portfolio_history(period="1M", timeframe="1D")
    if not snapshots:
        # Fall back to locally recorded snapshots
        p = _portfolio()
        snapshots = [asdict(s) for s in p.snapshots]
    return {"snapshots": snapshots}


@app.get("/api/bot/status")
def bot_status(_: str = Depends(verify)) -> dict[str, Any]:
    return _bot_manager().get_status()


@app.post("/api/bot/start")
def bot_start(dry_run: bool = False, _: str = Depends(verify)) -> dict[str, Any]:
    try:
        return _bot_manager().start(dry_run=dry_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/bot/stop")
def bot_stop(_: str = Depends(verify)) -> dict[str, Any]:
    return _bot_manager().stop()


@app.get("/api/pairs")
def pairs(_: str = Depends(verify)) -> dict[str, Any]:
    """Return open and recent closed pair positions + summary stats."""
    from trading.pairs_manager import get_all_pairs, pairs_summary
    return {"pairs": get_all_pairs(limit=30), "summary": pairs_summary()}


@app.get("/api/sentiment/{symbol}")
def sentiment(symbol: str, _: str = Depends(verify)) -> dict[str, Any]:
    """Return the 4-source composite sentiment breakdown for a symbol."""
    try:
        from analysis.sentiment_engine import get_composite_sentiment
        return get_composite_sentiment(symbol.upper())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/pending-trades")
def pending_trades(_: str = Depends(verify)) -> dict[str, Any]:
    """List IPO trades awaiting human approval."""
    from trading.approval_queue import get_pending
    return {"trades": get_pending()}


@app.post("/api/pending-trades/{approval_id}/approve")
def approve_trade(approval_id: str, _: str = Depends(verify)) -> dict[str, Any]:
    """Approve an IPO trade and place the bracket order immediately."""
    from trading.alpaca_client import place_bracket_order
    from trading.approval_queue import approve, mark_executed
    from trading.portfolio import Portfolio
    entry = approve(approval_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Approval not found or already decided")
    try:
        order_id = place_bracket_order(
            symbol=entry["symbol"],
            qty=entry["shares"],
            stop_loss_price=entry["stop_loss"],
            take_profit_price=entry["take_profit"],
        )
        if order_id:
            _portfolio().open_trade(
                symbol=entry["symbol"],
                shares=entry["shares"],
                entry_price=entry["price"],
                stop_loss=entry["stop_loss"],
                take_profit=entry["take_profit"],
                order_id=order_id,
            )
            mark_executed(approval_id)
        return {"message": f"Approved and ordered: {entry['symbol']}", "order_id": order_id}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/pending-trades/{approval_id}/decline")
def decline_trade(approval_id: str, _: str = Depends(verify)) -> dict[str, Any]:
    """Decline an IPO trade."""
    from trading.approval_queue import decline
    if not decline(approval_id):
        raise HTTPException(status_code=404, detail="Approval not found or already decided")
    return {"message": "Trade declined"}


@app.get("/api/market")
def market_data(_: str = Depends(verify)) -> dict[str, Any]:
    """
    Real-time market snapshot: VIX, SPY/QQQ/IWM, sector ETFs, open positions
    + top watchlist prices, and the current bot filter state.
    Data sourced from yfinance (15-min delayed).
    """
    import yfinance as yf
    from analysis.regime import get_regime_detail
    from analysis.sector_rotation import SECTOR_ETF_NAMES

    result: dict[str, Any] = {
        "indices": {},
        "vix": None,
        "sectors": [],
        "regime": None,
        "watchlist_snapshot": [],
        "filter_state": {},
    }

    # --- Major indices + VIX ---
    try:
        raw = yf.download(
            ["SPY", "QQQ", "IWM", "^VIX"],
            period="2d", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker",
        )
        for sym in ["SPY", "QQQ", "IWM", "^VIX"]:
            try:
                c = raw[sym]["Close"]
                prev = float(c.iloc[-2])
                curr = float(c.iloc[-1])
                entry = {"price": round(curr, 2), "change_pct": round((curr / prev - 1) * 100, 2)}
                if sym == "^VIX":
                    result["vix"] = entry
                else:
                    result["indices"][sym] = entry
            except Exception:
                continue
    except Exception as exc:
        _logger().debug(f"Index fetch failed: {exc}")

    # --- Sector ETFs ---
    try:
        etf_syms = list(SECTOR_ETF_NAMES.keys())
        etf_raw = yf.download(
            etf_syms, period="2d", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker",
        )
        sectors = []
        for sym in etf_syms:
            try:
                c = etf_raw[sym]["Close"]
                prev = float(c.iloc[-2])
                curr = float(c.iloc[-1])
                sectors.append({
                    "symbol": sym,
                    "name": SECTOR_ETF_NAMES[sym],
                    "price": round(curr, 2),
                    "change_pct": round((curr / prev - 1) * 100, 2),
                })
            except Exception:
                continue
        sectors.sort(key=lambda x: x["change_pct"], reverse=True)
        result["sectors"] = sectors
    except Exception as exc:
        _logger().debug(f"Sector ETF fetch failed: {exc}")

    # --- Regime ---
    try:
        result["regime"] = get_regime_detail()
    except Exception:
        pass

    # --- Watchlist snapshot: open positions first, then top 10 watchlist ---
    try:
        from trading.portfolio import Portfolio
        from config import Config
        p = _portfolio()
        open_syms = [t.symbol for t in p.trades if t.status == "OPEN"]
        snap_syms = list(dict.fromkeys(open_syms + Config.WATCHLIST[:10]))
        snap_raw = yf.download(
            snap_syms, period="2d", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker",
        )
        snap = []
        for sym in snap_syms:
            try:
                if len(snap_syms) == 1:
                    c = snap_raw["Close"]
                else:
                    c = snap_raw[sym]["Close"]
                prev = float(c.iloc[-2])
                curr = float(c.iloc[-1])
                snap.append({
                    "symbol": sym,
                    "price": round(curr, 2),
                    "change_pct": round((curr / prev - 1) * 100, 2),
                    "is_open": sym in open_syms,
                })
            except Exception:
                continue
        snap.sort(key=lambda x: (not x["is_open"], -abs(x["change_pct"])))
        result["watchlist_snapshot"] = snap
    except Exception as exc:
        _logger().debug(f"Watchlist snapshot failed: {exc}")

    # --- Current filter state summary ---
    try:
        from config import Config
        from trading.portfolio import Portfolio
        vix_val = result["vix"]["price"] if result["vix"] else None
        reg = result["regime"].get("regime") if result["regime"] else None
        result["filter_state"] = {
            "vix_value":     vix_val,
            "vix_threshold": Config.VIX_THRESHOLD,
            "vix_blocking":  vix_val is not None and vix_val > Config.VIX_THRESHOLD,
            "regime":        reg,
            "regime_sizing": {
                "BULL": "100%", "RANGING": "60%",
                "HIGH_VOL": "blocked", "BEAR": "50%",
            }.get(reg or "", "100%"),
            "ml_enabled":    Config.ML_SIGNAL_ENABLED,
            "ml_min_prob":   Config.ML_MIN_PROBABILITY,
            "short_enabled": Config.SHORT_SELLING_ENABLED,
            "weekly_req":    Config.WEEKLY_CONFIRM_REQUIRED,
            "sector_top_n":  Config.SECTOR_TOP_N,
            "kelly_active":  _portfolio().summary().get("total_trades", 0) >= Config.KELLY_MIN_TRADES,
        }
    except Exception:
        pass

    return result


@app.get("/api/review")
def performance_review(days: int = 30, _: str = Depends(verify)) -> dict[str, Any]:
    """Run a performance review and return JSON for report generation."""
    from analysis.review import run_review
    try:
        return run_review(days=days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/analytics")
def analytics(_: str = Depends(verify)) -> dict[str, Any]:
    """Portfolio risk metrics: Sharpe, Sortino, drawdown, VaR, equity curve."""
    from analysis.risk_analytics import compute_analytics
    from trading.portfolio import Portfolio
    p = _portfolio()
    return compute_analytics(portfolio=p)


@app.get("/api/regime")
def regime(_: str = Depends(verify)) -> dict[str, Any]:
    """Current market regime (BULL / RANGING / HIGH_VOL / BEAR) with metrics."""
    from analysis.regime import get_regime_detail
    return get_regime_detail()


@app.get("/api/features")
def features(_: str = Depends(verify)) -> dict[str, Any]:
    """
    Live status of every implemented feature across all tiers.
    Returns config values, enabled state, and live metrics where available.
    """
    from config import Config
    from trading.portfolio import Portfolio
    p = _portfolio()
    summary = p.summary()
    closed_count = summary.get("total_trades", 0)

    # Live VIX
    vix_val = None
    try:
        from analysis.market_filter import get_vix
        vix_val = round(get_vix(), 1)
    except Exception:
        pass

    # Live regime
    regime_val = None
    regime_detail = {}
    try:
        from analysis.regime import get_regime_detail
        regime_detail = get_regime_detail()
        regime_val = regime_detail.get("regime")
    except Exception:
        pass

    # Sector top-4
    top_sectors = []
    try:
        from analysis.sector_rotation import get_sector_rankings
        rankings = get_sector_rankings()
        top_sectors = [etf for etf, _, rank in rankings if rank <= Config.SECTOR_TOP_N]
    except Exception:
        pass

    # Open pairs count
    open_pairs_count = 0
    try:
        from trading.pairs_manager import get_open_pairs
        open_pairs_count = len(get_open_pairs())
    except Exception:
        pass

    # Open shorts count
    open_shorts = 0
    try:
        open_shorts = sum(1 for t in p.trades if t.side == "SHORT" and t.status == "OPEN")
    except Exception:
        pass

    kelly_active = closed_count >= Config.KELLY_MIN_TRADES

    return {
        "tiers": [
            {
                "tier": 1,
                "label": "Core Filters & Risk",
                "features": [
                    {
                        "name": "VIX Filter",
                        "description": "Blocks all new long entries when market fear is elevated",
                        "enabled": True,
                        "status": "blocking" if (vix_val and vix_val > Config.VIX_THRESHOLD) else "active",
                        "live": f"VIX {vix_val}" if vix_val else None,
                        "config": f"threshold > {Config.VIX_THRESHOLD}",
                    },
                    {
                        "name": "News Sentiment Filter",
                        "description": "Scores Alpaca news headlines before allowing a buy",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": f"min score {Config.MIN_SENTIMENT_SCORE}",
                    },
                    {
                        "name": "News Opportunity Scanner",
                        "description": "Discovers buy candidates outside watchlist from breaking news",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": "24h window · min score 2 · top 10",
                    },
                    {
                        "name": "IPO Tracker",
                        "description": "Detects new listings, quarantines them, then signals momentum entries",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": f"{Config.IPO_MIN_DAYS}d quarantine · {int(Config.IPO_POSITION_SCALE*100)}% size · {int(Config.IPO_STOP_LOSS_PCT*100)}% SL",
                    },
                    {
                        "name": "IPO Human Approval",
                        "description": "60-minute window to accept/decline IPO trades before auto-execution",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": "60 min window · auto-executes on timeout",
                    },
                    {
                        "name": "Trailing Stops",
                        "description": "Locks in profits by closing when price falls from its peak",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": f"{int(Config.TRAILING_STOP_PCT*100)}% trail from peak",
                    },
                    {
                        "name": "Earnings Blackout",
                        "description": "Blocks new entries near earnings dates to avoid gap risk",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": f"no entry within {Config.EARNINGS_BLACKOUT_DAYS}d of earnings",
                    },
                    {
                        "name": "Correlation Limit",
                        "description": "Prevents loading up on highly correlated positions",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": f"max r = {Config.MAX_POSITION_CORRELATION}",
                    },
                    {
                        "name": "Multi-Source Sentiment",
                        "description": "Options flow + analyst ratings + insider buying + retail contrarian",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": f"4 sources · min composite {Config.MIN_COMPOSITE_SENTIMENT}",
                    },
                    {
                        "name": "Sector Rotation",
                        "description": "Only buys in the top N sectors by 3-month momentum",
                        "enabled": True,
                        "status": "active",
                        "live": ", ".join(top_sectors) if top_sectors else None,
                        "config": f"top {Config.SECTOR_TOP_N} of 11 SPDR sectors",
                    },
                    {
                        "name": "Kelly Criterion Sizing",
                        "description": "Optimal position sizing based on historical win rate and R-ratio",
                        "enabled": True,
                        "status": "active" if kelly_active else "warmup",
                        "live": f"{closed_count}/{Config.KELLY_MIN_TRADES} trades" if not kelly_active else f"active ({closed_count} trades)",
                        "config": f"half-Kelly · min {Config.KELLY_MIN_TRADES} trades · floor {int(Config.KELLY_MIN_FRACTION*100)}%",
                    },
                    {
                        "name": "Pairs Trading",
                        "description": "Dollar-neutral stat-arb on 13 cointegrated pairs (MSFT/GOOGL, XOM/CVX…)",
                        "enabled": True,
                        "status": "active",
                        "live": f"{open_pairs_count} open pair{'s' if open_pairs_count != 1 else ''}",
                        "config": f"z-entry {Config.PAIRS_ENTRY_ZSCORE} · z-exit {Config.PAIRS_EXIT_ZSCORE} · {Config.PAIRS_MAX_POSITIONS} max",
                    },
                ],
            },
            {
                "tier": 2,
                "label": "Advanced Intelligence",
                "features": [
                    {
                        "name": "Short Selling",
                        "description": "Shorts SELL signals confirmed by weekly chart + bottom sector + negative sentiment",
                        "enabled": Config.SHORT_SELLING_ENABLED,
                        "status": "active" if Config.SHORT_SELLING_ENABLED else "disabled",
                        "live": f"{open_shorts} open short{'s' if open_shorts != 1 else ''}" if Config.SHORT_SELLING_ENABLED else None,
                        "config": f"max {Config.SHORT_MAX_POSITIONS} shorts · bottom {Config.SHORT_SECTOR_BOTTOM_N} sectors",
                    },
                    {
                        "name": "SEC 13F Tracker",
                        "description": "Monitors quarterly filings from 8 top hedge funds via EDGAR",
                        "enabled": Config.THIRTEEN_F_ENABLED,
                        "status": "active" if Config.THIRTEEN_F_ENABLED else "disabled",
                        "live": "Berkshire · Renaissance · Citadel · Two Sigma + 4 more",
                        "config": f"boost ×{Config.THIRTEEN_F_BOOST_SCALE} · 7d cache",
                    },
                    {
                        "name": "Multi-Timeframe Confirmation",
                        "description": "Daily BUY must be confirmed by weekly chart (10w EMA + RSI + MACD)",
                        "enabled": Config.WEEKLY_CONFIRM_REQUIRED,
                        "status": "active" if Config.WEEKLY_CONFIRM_REQUIRED else "disabled",
                        "live": None,
                        "config": "10w EMA · RSI 35–75 · MACD positive",
                    },
                ],
            },
            {
                "tier": 3,
                "label": "Predictive Intelligence",
                "features": [
                    {
                        "name": "Volatility Regime",
                        "description": "Adapts position sizing and strategy bias to market conditions",
                        "enabled": Config.REGIME_FILTER_ENABLED,
                        "status": "active" if Config.REGIME_FILTER_ENABLED else "disabled",
                        "live": regime_val,
                        "config": "BULL 1× · RANGING 0.6× · HIGH_VOL block · BEAR 0.5×",
                    },
                    {
                        "name": "ML Signal Booster",
                        "description": "RandomForest classifier predicts 5-day profitability per symbol",
                        "enabled": Config.ML_SIGNAL_ENABLED,
                        "status": "active" if Config.ML_SIGNAL_ENABLED else "disabled",
                        "live": None,
                        "config": f"min p={Config.ML_MIN_PROBABILITY} · 150 sample warmup · 24h model cache",
                    },
                    {
                        "name": "Dynamic Universe",
                        "description": "Daily screens 140 extended large/mid-caps for momentum breakouts",
                        "enabled": Config.DYNAMIC_UNIVERSE_ENABLED,
                        "status": "active" if Config.DYNAMIC_UNIVERSE_ENABLED else "disabled",
                        "live": f"top {Config.DYNAMIC_UNIVERSE_TOP_N} added to scan" if Config.DYNAMIC_UNIVERSE_ENABLED else None,
                        "config": "above 50-EMA · RSI 40–65 · 4w breakout · volume surge",
                    },
                    {
                        "name": "Risk Analytics",
                        "description": "Daily Sharpe, Sortino, Calmar, max drawdown, VaR tracking",
                        "enabled": True,
                        "status": "active",
                        "live": None,
                        "config": "recorded at market close · 90d equity curve",
                    },
                ],
            },
        ]
    }


@app.get("/api/logs")
def get_logs(_: str = Depends(verify), lines: int = 100) -> dict[str, Any]:
    from pathlib import Path
    log_file = Path(__file__).parent.parent / "logs" / "stox.log"
    if not log_file.exists():
        return {"lines": []}
    with open(log_file) as f:
        all_lines = f.readlines()
    return {"lines": [l.rstrip() for l in all_lines[-lines:]]}


# ------------------------------------------------------------------ SPA

_DIST = Path(__file__).parent.parent / "dashboard" / "dist"

if _DIST.exists():
    # Serve static assets (JS/CSS bundles) without auth
    _assets = _DIST / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        """Serve the React SPA for all non-API routes."""
        return FileResponse(
            str(_DIST / "index.html"),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
else:
    _logger().warning(
        "React build not found at dashboard/dist. "
        "Run `npm run build` inside the dashboard/ directory."
    )
