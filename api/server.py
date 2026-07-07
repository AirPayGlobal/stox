"""
FastAPI dashboard + engine control.

    uvicorn api.server:app --host 0.0.0.0 --port 8000

Endpoints (HTTP basic auth):
    GET  /              dashboard UI
    GET  /api/status    engine + risk + signals snapshot
    GET  /api/trades    today's closed trades
    POST /api/start     start engine (?dry_run=true for signals-only)
    POST /api/stop      stop engine
"""
from __future__ import annotations

import base64
import binascii
import os
import secrets
import threading

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse

from config import Config
from engine import TradingEngine
from utils.logger import get_logger

logger = get_logger("api")

app = FastAPI(title="STOX Options", docs_url=None, redoc_url=None)

_engine: TradingEngine | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


APP_VERSION = "2.2.0"


def _start_engine(dry_run: bool = False) -> tuple[bool, str]:
    global _engine, _thread
    with _lock:
        if _thread and _thread.is_alive() and _engine and _engine.running:
            return False, "engine already running"
        _engine = TradingEngine(dry_run=dry_run)
        _thread = threading.Thread(target=_engine.run, daemon=True)
        _thread.start()
    return True, "started"


@app.on_event("startup")
def _autostart() -> None:
    """Default state is RUNNING: start the engine when the server boots."""
    if not Config.ENGINE_AUTOSTART:
        logger.info("ENGINE_AUTOSTART=false — waiting for manual start")
        return
    if not Config.ALPACA_API_KEY or not Config.ALPACA_API_SECRET:
        logger.warning("Autostart skipped: Alpaca API keys not configured")
        return
    ok, msg = _start_engine(dry_run=Config.ENGINE_AUTOSTART_DRY)
    logger.info(f"Engine autostart: {msg} (dry_run={Config.ENGINE_AUTOSTART_DRY})")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": 'Basic realm="stox"'},
    )


def _auth(request: Request) -> str:
    # Custom Basic-auth parser instead of fastapi.security.HTTPBasic, which
    # decodes credentials as ASCII and therefore rejects any password with
    # non-ASCII characters before it can even be compared. Browsers send
    # UTF-8 (RFC 7617); fall back to latin-1 for older clients. Configured
    # values are stripped — trailing whitespace/newlines are a common
    # copy-paste artifact in hosting dashboards, not part of the password.
    scheme, _, param = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "basic" or not param.strip():
        raise _unauthorized()
    try:
        raw = base64.b64decode(param.strip())
    except (binascii.Error, ValueError):
        raise _unauthorized()
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        decoded = raw.decode("latin-1")
    username, sep, password = decoded.partition(":")
    if not sep:
        raise _unauthorized()

    user_ok = secrets.compare_digest(
        username.encode("utf-8"), Config.DASHBOARD_USER.strip().encode("utf-8")
    )
    pass_ok = secrets.compare_digest(
        password.encode("utf-8"), Config.DASHBOARD_PASS.strip().encode("utf-8")
    )
    if not (user_ok and pass_ok):
        raise _unauthorized()
    return username


@app.get("/healthz")
def healthz():
    """Unauthenticated liveness/version probe."""
    return {
        "ok": True,
        "app": "stox-options",
        "version": APP_VERSION,
        "state_dir": Config.STATE_DIR,  # "/data" = persistent volume in use
    }


@app.get("/")
def index(_: str = Depends(_auth)):
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/status")
def api_status(_: str = Depends(_auth)):
    if _engine is None:
        return {"running": False, "mode": Config.ALPACA_MODE, "message": "engine not started"}
    return _engine.status()


@app.get("/api/trades")
def api_trades(_: str = Depends(_auth)):
    if _engine is None:
        return []
    return [vars(t) for t in _engine.book.closed_today()]


@app.post("/api/start")
def api_start(dry_run: bool = False, _: str = Depends(_auth)):
    ok, message = _start_engine(dry_run=dry_run)
    if ok:
        logger.info(f"Engine started via API (dry_run={dry_run})")
    return {"ok": ok, "message": message, "dry_run": dry_run}


def _book():
    """Engine's live book when running, else the persisted book from disk."""
    if _engine is not None:
        return _engine.book
    from trading.positions import PositionBook

    return PositionBook()


@app.get("/api/report")
def api_report(days: int = 30, _: str = Depends(_auth)):
    from reporting import period_report

    return period_report(_book(), max(1, min(days, 365)))


@app.get("/api/report/daily")
def api_report_daily(date: str = "", _: str = Depends(_auth)):
    from reporting import daily_report

    return daily_report(_book(), date or None)


@app.get("/api/report/export")
def api_report_export(days: int = 90, _: str = Depends(_auth)):
    from fastapi.responses import PlainTextResponse

    from reporting import trades_csv

    csv_text = trades_csv(_book(), max(1, min(days, 365)))
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="stox-trades-{days}d.csv"'},
    )


@app.post("/api/backtest")
def api_backtest(
    strategy: str = "all",
    days: int = 30,
    equity: float = 100_000.0,
    symbols: str = "",
    _: str = Depends(_auth),
):
    """Run a backtest server-side and return the results summary. Synchronous:
    expect a few seconds per symbol (data fetch + simulation)."""
    if strategy not in ("orb", "sweep", "swing", "both", "all"):
        return {"error": f"unknown strategy '{strategy}'"}
    days = max(5, min(days, 365))
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] or Config.UNDERLYINGS
    from backtest.run_backtest import run_backtest

    try:
        return run_backtest(syms, days, equity, strategy)
    except Exception as exc:
        logger.error(f"Backtest failed: {exc}", exc_info=True)
        return {"error": str(exc)}


@app.post("/api/reset-day")
def api_reset_day(_: str = Depends(_auth)):
    """Clear the day's governor state — releases a stuck halt/protect lock."""
    if _engine is None:
        return {"ok": False, "message": "engine not started"}
    _engine.risk.reset()
    logger.info("Day governor reset via API")
    return {"ok": True}


@app.post("/api/stop")
def api_stop(_: str = Depends(_auth)):
    if _engine is None or not _engine.running:
        return {"ok": False, "message": "engine not running"}
    _engine.stop()
    logger.info("Engine stopped via API")
    return {"ok": True}
