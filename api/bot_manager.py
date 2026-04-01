"""
BotManager — runs TradingBot in a background thread so the FastAPI server
stays responsive while the bot is executing scans and placing orders.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

import schedule

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


class BotManager:
    def __init__(self) -> None:
        self._bot = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.status: str = "stopped"   # stopped | starting | running | stopping | error
        self.dry_run: bool = False
        self.started_at: Optional[str] = None
        self.error_msg: Optional[str] = None

    def start(self, dry_run: bool = False) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"ok": False, "message": "Bot is already running"}

            schedule.clear()
            self.dry_run = dry_run
            self.status = "starting"
            self.started_at = datetime.utcnow().isoformat()
            self.error_msg = None

            def _run() -> None:
                try:
                    from main import TradingBot
                    self._bot = TradingBot(dry_run=dry_run)
                    self.status = "running"
                    self._bot.start()
                except Exception as exc:
                    self.status = "error"
                    self.error_msg = str(exc)
                    logger.error(f"Bot crashed: {exc}")
                finally:
                    if self.status not in ("error", "stopping"):
                        self.status = "stopped"
                    elif self.status == "stopping":
                        self.status = "stopped"

            self._thread = threading.Thread(target=_run, daemon=True, name="trading-bot")
            self._thread.start()
            mode = "dry-run" if dry_run else Config.ALPACA_MODE
            logger.info(f"Bot thread started (mode={mode})")
            return {"ok": True, "message": f"Bot started in {mode} mode"}

    def stop(self) -> dict:
        with self._lock:
            if not self._bot or not (self._thread and self._thread.is_alive()):
                return {"ok": False, "message": "Bot is not running"}
            self._bot._running = False
            self.status = "stopping"
            logger.info("Bot stop requested")
            return {"ok": True, "message": "Bot is stopping (may take up to 30 s)"}

    def get_status(self) -> dict:
        running = self._thread is not None and self._thread.is_alive()
        # Auto-correct stale status if thread has exited
        if not running and self.status not in ("stopped", "error"):
            self.status = "stopped"
        return {
            "status": self.status,
            "running": running,
            "dry_run": self.dry_run,
            "mode": Config.ALPACA_MODE,
            "started_at": self.started_at if running else None,
            "error": self.error_msg,
        }


# Module-level singleton shared by the FastAPI app
bot_manager = BotManager()

# NOTE: auto-start is triggered via FastAPI's startup event in server.py
# so the HTTP server (and /health) is ready before the bot's heavy imports begin.
