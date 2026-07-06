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


APP_VERSION = "2.1.0"


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
    return {"ok": True, "app": "stox-options", "version": APP_VERSION}


@app.get("/authz-debug")
def authz_debug():
    """
    TEMPORARY credential-configuration diagnostic. Reveals whether the
    running container actually received custom DASHBOARD_USER/PASS values,
    WITHOUT exposing the password itself (only its length). Remove once the
    login issue is resolved.
    """
    user = Config.DASHBOARD_USER.strip()
    pw = Config.DASHBOARD_PASS.strip()
    return {
        "configured_user": user,
        "user_is_default": user == "admin",
        "password_length": len(pw),
        "password_is_default": pw == "changeme",
        "password_ascii_only": pw.isascii(),
        "raw_password_had_surrounding_whitespace": pw != Config.DASHBOARD_PASS,
        "env_var_DASHBOARD_USER_present": "DASHBOARD_USER" in os.environ,
        "env_var_DASHBOARD_PASS_present": "DASHBOARD_PASS" in os.environ,
    }


@app.get("/authz-debug/check")
def authz_debug_check(u: str = "", p: str = ""):
    """
    TEMPORARY: compare attempted credentials against the configured ones and
    say WHICH field mismatches, without revealing the stored values.
    Usage: /authz-debug/check?u=myuser&p=mypass
    """
    exp_user = Config.DASHBOARD_USER.strip()
    exp_pw = Config.DASHBOARD_PASS.strip()
    return {
        "username_matches": secrets.compare_digest(u.encode(), exp_user.encode()),
        "password_matches": secrets.compare_digest(p.encode(), exp_pw.encode()),
        "you_typed_user_length": len(u),
        "expected_user_length": len(exp_user),
        "you_typed_password_length": len(p),
        "expected_password_length": len(exp_pw),
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
