"""
STOX Dashboard API
==================
FastAPI backend that exposes account data, positions, trades, portfolio
metrics, and bot control endpoints. In production it also serves the
compiled React SPA from dashboard/dist/.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.bot_manager import bot_manager
from config import Config
from trading.alpaca_client import get_account, get_positions, place_bracket_order
from trading.portfolio import Portfolio
from trading.approval_queue import get_pending, approve, decline, mark_executed
from utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(title="STOX Dashboard", docs_url=None, redoc_url=None)

_DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
_DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "stox")


# ------------------------------------------------------------------ Auth
# Uses Bearer token (base64 user:pass) to avoid browser intercepting 401s

import base64

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

@app.get("/health")
def health() -> dict:
    """Railway health check — no auth required."""
    return {"status": "ok"}


@app.get("/api/account")
def account(_: str = Depends(verify)) -> dict[str, Any]:
    try:
        return get_account()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/positions")
def positions(_: str = Depends(verify)) -> dict[str, Any]:
    try:
        return get_positions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/trades")
def trades(_: str = Depends(verify)) -> dict[str, Any]:
    p = Portfolio()
    return {"trades": [asdict(t) for t in reversed(p.trades)]}


@app.get("/api/summary")
def summary(_: str = Depends(verify)) -> dict[str, Any]:
    return Portfolio().summary()


@app.get("/api/equity-curve")
def equity_curve(_: str = Depends(verify)) -> dict[str, Any]:
    p = Portfolio()
    return {"snapshots": [asdict(s) for s in p.snapshots]}


@app.get("/api/bot/status")
def bot_status(_: str = Depends(verify)) -> dict[str, Any]:
    return bot_manager.get_status()


@app.post("/api/bot/start")
def bot_start(dry_run: bool = False, _: str = Depends(verify)) -> dict[str, Any]:
    try:
        return bot_manager.start(dry_run=dry_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/bot/stop")
def bot_stop(_: str = Depends(verify)) -> dict[str, Any]:
    return bot_manager.stop()


@app.get("/api/pending-trades")
def pending_trades(_: str = Depends(verify)) -> dict[str, Any]:
    """List IPO trades awaiting human approval."""
    return {"trades": get_pending()}


@app.post("/api/pending-trades/{approval_id}/approve")
def approve_trade(approval_id: str, _: str = Depends(verify)) -> dict[str, Any]:
    """Approve an IPO trade and place the bracket order immediately."""
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
            Portfolio().open_trade(
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
    if not decline(approval_id):
        raise HTTPException(status_code=404, detail="Approval not found or already decided")
    return {"message": "Trade declined"}


@app.get("/api/logs")
def get_logs(_: str = Depends(verify), lines: int = 100) -> dict[str, Any]:
    from datetime import datetime
    from pathlib import Path
    log_dir = Path(__file__).parent.parent / "logs"
    log_file = log_dir / f"{datetime.now():%Y-%m-%d}.log"
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
        return FileResponse(str(_DIST / "index.html"))
else:
    logger.warning(
        "React build not found at dashboard/dist. "
        "Run `npm run build` inside the dashboard/ directory."
    )
