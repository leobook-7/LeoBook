# supervisor.py: Orchestrator for the LeoBook autonomous cycle.
# Part of LeoBook Core — System
#
# Classes: Supervisor
# Functions: run_cycle(), dispatch(), capture_state()
# Called by: Leo.py

import logging
import json
import asyncio
import uuid
from datetime import datetime
from typing import Type, Dict, Any, Optional

from Core.Utils.constants import now_ng
from Data.Access.league_db import init_db
from Core.System.worker_base import BaseWorker

logger = logging.getLogger(__name__)

class Supervisor:
    """
    Orchestrates the autonomous cycle and manages worker lifecycles.
    Handles timeout, retries, and state persistence.
    """
    
    def __init__(self):
        self.conn = init_db()
        self._ensure_table()
        self.run_id = str(uuid.uuid4())[:8]
        self.state = {
            "cycle_count": 0,
            "error_log": [],
            "last_run": None,
            "status": "idle"
        }

    def _ensure_table(self):
        """Initialize the system_state SQLite table if it doesn't exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def capture_state(self, key: str, value: Any):
        """Persist a piece of state to the database."""
        self.conn.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), now_ng().isoformat())
        )
        self.conn.commit()

    def get_state(self, key: str, default: Any = None) -> Any:
        """Retrieve a piece of state from the database."""
        row = self.conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        return default

    async def dispatch(self, worker_class: Type[BaseWorker], *args, timeout: int = 1800, max_retries: int = 2, **kwargs) -> bool:
        """
        Instantiates and executes a worker with timeout and retry logic.
        """
        worker = worker_class()
        attempt = 0
        
        while attempt <= max_retries:
            try:
                logger.info(f"[Supervisor] Dispatching {worker.name} (Attempt {attempt+1}/{max_retries+1})")
                async with asyncio.timeout(timeout):
                    success = await worker.execute(*args, **kwargs)
                    if success:
                        return True
                    else:
                        logger.warning(f"[Supervisor] Worker {worker.name} returned False.")
            except asyncio.TimeoutError:
                logger.error(f"[Supervisor] Worker {worker.name} timed out after {timeout} seconds.")
            except Exception as e:
                await worker.on_failure(e)
            
            attempt += 1
            if attempt <= max_retries:
                wait_time = 5 * attempt
                logger.info(f"[Supervisor] Retrying {worker.name} in {wait_time}s...")
                await asyncio.sleep(wait_time)
        
        return False

    async def run_cycle(self, chapters: list):
        """
        Executes a sequence of chapters/workers as a single autonomous loop.
        """
        self.state["status"] = "running"
        self.state["cycle_count"] += 1
        self.capture_state("global_state", self.state)
        
        startTime = now_ng()
        logger.info(f"=== Starting Autonomous Cycle #{self.state['cycle_count']} (ID: {self.run_id}) ===")

        for worker_class in chapters:
            success = await self.dispatch(worker_class)
            if not success:
                logger.error(f"Critical failure in chapter {worker_class.__name__}. Aborting cycle.")
                self.state["status"] = "failed"
                break
        else:
            self.state["status"] = "completed"
            logger.info(f"=== Cycle #{self.state['cycle_count']} Complete ===")

        self.state["last_run"] = now_ng().isoformat()
        self.capture_state("global_state", self.state)
