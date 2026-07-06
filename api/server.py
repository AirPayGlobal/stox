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

import os
import secrets
import threading

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config import Config
from engine import TradingEngine
from utils.logger import get_logger

logger = get_logger("api")

app = FastAPI(title="STOX Options", docs_url=None, redoc_url=None)
security = HTTPBasic()

_engine: TradingEngine | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    user_ok = secrets.compare_digest(credentials.username, Config.DASHBOARD_USER)
    pass_ok = secrets.compare_digest(credentials.password, Config.DASHBOARD_PASS)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


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
    global _engine, _thread
    with _lock:
        if _thread and _thread.is_alive():
            return {"ok": False, "message": "engine already running"}
        _engine = TradingEngine(dry_run=dry_run)
        _thread = threading.Thread(target=_engine.run, daemon=True)
        _thread.start()
    logger.info(f"Engine started via API (dry_run={dry_run})")
    return {"ok": True, "dry_run": dry_run}


@app.post("/api/stop")
def api_stop(_: str = Depends(_auth)):
    if _engine is None or not _engine.running:
        return {"ok": False, "message": "engine not running"}
    _engine.stop()
    logger.info("Engine stopped via API")
    return {"ok": True}
