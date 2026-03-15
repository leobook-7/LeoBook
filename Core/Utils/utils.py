# utils.py: General-purpose utility functions and system helpers.
# Part of LeoBook Core — Utilities
#
# Classes: RotatingSegmentLogger, BatchProcessor
# Functions: log_error_state(), capture_debug_snapshot()

"""
Utilities Module
General-purpose utility functions and classes for the LeoBook system.
Responsible for error logging, rotating segment logging, batch processing,
and system utilities.

CRITICAL: All timestamps use now_ng() from Core/Utils/constants.py.
Never hardcode timezone offsets here. TZ_NG is the single source of truth.
"""

import asyncio
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable, List, Optional, TypeVar

from playwright.async_api import Page

T = TypeVar('T')

# All logs live under Data/Logs/ — inside Data/ for Supabase sync consistency.
# Path resolves relative to this file: Core/Utils/ → up 2 → repo root → Data/Logs/
_UTILS_ROOT   = Path(__file__).parent.parent.parent   # repo root
LOG_DIR       = _UTILS_ROOT / "Data" / "Logs"
ERROR_LOG_DIR = LOG_DIR / "Error"
DEBUG_LOG_DIR = LOG_DIR / "Debug"

AUTH_DIR = Path("Data/Auth")


# ── Segment path helpers ───────────────────────────────────────────────────────

def _get_segment_dir(log_category: str, now) -> Path:
    """Build the hierarchical directory path for a log segment.

    Structure: Data/Logs/{category}/YYYY/MM/WXX/DD/
    ISO week number keeps weekly grouping trivial.
    """
    _, iso_week, _ = now.isocalendar()
    return (
        LOG_DIR / log_category
        / str(now.year)
        / f"{now.month:02d}"
        / f"W{iso_week:02d}"
        / f"{now.day:02d}"
    )


def _segment_filename(prefix: str, now) -> str:
    """Segment filename encodes segment start time: prefix_HHMMSS.log"""
    return f"{prefix}_{now.strftime('%H%M%S')}.log"


# ── RotatingSegmentLogger ──────────────────────────────────────────────────────

class RotatingSegmentLogger:
    """Drop-in replacement for Tee.

    Adds:
    - Per-line timestamp injection using now_ng() — the single source of
      truth for all LeoBook timestamps (Core/Utils/constants.py).
    - Auto-rotation on 10 MB size OR hour boundary (whichever first).
    - Rotation check fires before writing the next line — no mid-line splits.
    - Hierarchical folder: Data/Logs/{category}/YYYY/MM/WXX/DD/
    - Background Supabase Storage upload on each segment close.
    - SQLite log_segments metadata tracking.

    Usage (identical to Tee):
        logger = RotatingSegmentLogger(
            original_stdout, category="Terminal", prefix="leo_session"
        )
        sys.stdout = logger
        sys.stderr = logger
        # At process exit:
        logger.close_segment()
    """

    MAX_SEGMENT_SIZE: int = 10 * 1024 * 1024   # 10 MB default

    def __init__(
        self,
        *passthrough_streams,
        category: str = "Terminal",
        prefix: str = "leo_session",
    ):
        self._streams   = passthrough_streams   # original stdout/stderr
        self._category  = category
        self._prefix    = prefix
        self._lock      = threading.Lock()
        self._file: Optional[object]  = None
        self._path: Optional[Path]    = None
        self._size: int               = 0
        self._hour: int               = -1
        self._open_segment()

    # ── Public interface ───────────────────────────────────────────────────────

    def write(self, text: str) -> None:
        """Write text to console + log file with timestamp injection."""
        if not text:
            return

        stamped = self._inject_timestamps(text)

        # Write to passthrough streams (console) — never block on file I/O
        for stream in self._streams:
            try:
                stream.write(stamped)
                stream.flush()
            except Exception:
                pass

        # Rotate if needed, then write to segment file
        with self._lock:
            self._rotate_if_needed()
            if self._file:
                try:
                    self._file.write(stamped)
                    self._file.flush()
                    self._size += len(stamped.encode("utf-8", errors="replace"))
                except Exception:
                    pass

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:
                pass
        with self._lock:
            if self._file:
                try:
                    self._file.flush()
                except Exception:
                    pass

    def close_segment(self) -> None:
        """Close the current segment and trigger upload. Call at process exit."""
        with self._lock:
            self._close_current(upload=True)

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _now():
        """Current time via now_ng() — the single source of truth.

        Timezone is defined in Core/Utils/constants.py TZ_NG.
        Changing TZ_NG there automatically updates all log timestamps.
        No timezone logic lives here.
        """
        from Core.Utils.constants import now_ng
        return now_ng()

    def _inject_timestamps(self, text: str) -> str:
        """Prepend [YYYY-MM-DD HH:MM:SS TZ_NG_NAME] to each non-blank line.

        Uses now_ng() + TZ_NG_NAME from constants.py — no hardcoded timezone.
        Blank lines pass through unmodified to preserve visual formatting.
        """
        from Core.Utils.constants import now_ng, TZ_NG_NAME
        now_str = now_ng().strftime("%Y-%m-%d %H:%M:%S")
        prefix  = f"[{now_str} {TZ_NG_NAME}] "

        lines  = text.split("\n")
        result = []
        for i, line in enumerate(lines):
            # Last element after split is always "" if text ends with \n — keep it
            is_trailing_empty = (i == len(lines) - 1 and line == "")
            if line.strip() == "" or is_trailing_empty:
                result.append(line)
            else:
                result.append(f"{prefix}{line}")
        return "\n".join(result)

    def _rotate_if_needed(self) -> None:
        """Check rotation conditions. Must be called inside self._lock."""
        now           = self._now()
        size_exceeded = self._size >= self.MAX_SEGMENT_SIZE
        hour_changed  = (now.hour != self._hour)

        if size_exceeded or hour_changed:
            self._close_current(upload=True)
            self._open_segment(now)

    def _open_segment(self, now=None) -> None:
        """Open a new log segment. Call inside self._lock (or from __init__)."""
        now     = now or self._now()
        seg_dir = _get_segment_dir(self._category, now)
        seg_dir.mkdir(parents=True, exist_ok=True)

        path         = seg_dir / _segment_filename(self._prefix, now)
        self._file   = open(path, "a", encoding="utf-8", buffering=1)
        self._path   = path
        self._size   = path.stat().st_size if path.exists() else 0
        self._hour   = now.hour

        # Register in SQLite metadata — non-blocking background thread
        threading.Thread(
            target=_register_log_segment,
            args=(str(path), self._category, now.isoformat()),
            daemon=True,
        ).start()

    def _close_current(self, upload: bool = True) -> None:
        """Close file handle and optionally trigger upload.
        Must be called inside self._lock.
        """
        if self._file is None:
            return
        path = self._path
        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass
        finally:
            self._file = None
            self._path = None
            self._size = 0

        if upload and path and path.exists():
            threading.Thread(
                target=_upload_log_segment,
                args=(path, self._category),
                daemon=True,
                name=f"LogUpload-{path.name}",
            ).start()


# ── Module-level helpers (called from background daemon threads) ───────────────

def _register_log_segment(path: str, category: str, started_at: str) -> None:
    """Record a new segment in the SQLite log_segments table."""
    try:
        from Data.Access.league_db import get_connection
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS log_segments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                path        TEXT NOT NULL UNIQUE,
                category    TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                closed_at   TEXT,
                size_bytes  INTEGER DEFAULT 0,
                uploaded    INTEGER DEFAULT 0,
                remote_path TEXT
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO log_segments (path, category, started_at) "
            "VALUES (?, ?, ?)",
            (path, category, started_at),
        )
        conn.commit()
    except Exception:
        pass   # Never crash logging due to DB unavailability


def _upload_log_segment(path: Path, category: str) -> None:
    """Upload a completed segment to Supabase Storage 'logs' bucket.

    Silently skips if Supabase is unavailable — segment stays on disk
    and will be swept by LogSync on the next --sync run.
    Timestamps via now_ng() — no hardcoded timezone.
    """
    try:
        from Data.Access.supabase_client import get_supabase_client
        from Data.Access.league_db import get_connection
        from Core.Utils.constants import now_ng

        client = get_supabase_client()
        if not client:
            return

        # Remote path mirrors local structure relative to Data/Logs/
        try:
            remote_path = str(path.relative_to(LOG_DIR)).replace("\\", "/")
        except ValueError:
            remote_path = path.name

        storage = client.storage
        try:
            buckets = storage.list_buckets()
            if not any(b.name == "logs" for b in buckets):
                storage.create_bucket("logs", options={"public": False})
        except Exception:
            pass

        size = path.stat().st_size
        with open(path, "rb") as f:
            storage.from_("logs").upload(
                path=remote_path,
                file=f,
                file_options={"cache-control": "3600", "upsert": "true"},
            )

        conn = get_connection()
        conn.execute(
            "UPDATE log_segments "
            "SET uploaded=1, remote_path=?, closed_at=?, size_bytes=? "
            "WHERE path=?",
            (remote_path, now_ng().isoformat(), size, str(path)),
        )
        conn.commit()

    except Exception:
        pass   # Never crash the main process due to upload failure


# ── Error / Debug snapshot helpers ─────────────────────────────────────────────

async def log_error_state(page: Page, context_label: str, error: Exception):
    """Captures page state on error into Data/Logs/Error/YYYY/MM/WXX/DD/."""
    from Core.Utils.constants import now_ng
    now = now_ng()
    _, iso_week, _ = now.isocalendar()
    day_dir = (
        ERROR_LOG_DIR
        / str(now.year)
        / f"{now.month:02d}"
        / f"W{iso_week:02d}"
        / f"{now.day:02d}"
    )
    day_dir.mkdir(parents=True, exist_ok=True)

    timestamp     = now.strftime("%Y%m%d_%H%M%S")
    base_filename = f"{context_label}_{timestamp}"
    print(f"  [CRITICAL ERROR] Logging state for '{context_label}'. "
          f"See Data/Logs/Error/")
    try:
        with open(day_dir / f"{base_filename}.txt", "w", encoding="utf-8") as f:
            f.write(f"Error Context: {context_label}\n")
            f.write(f"Timestamp:     {now.isoformat()}\n\n")
            traceback.print_exc(file=f)
            f.write(f"\n--- Error Message ---\n{error}")
        if page and not page.is_closed():
            await page.screenshot(
                path=day_dir / f"{base_filename}.png", full_page=True
            )
            with open(day_dir / f"{base_filename}.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
    except Exception as log_e:
        print(f"    [Logger Failure] Could not write error state: {log_e}")


async def capture_debug_snapshot(page: Page, label: str, info_text: str = ""):
    """Captures debug snapshot into Data/Logs/Debug/YYYY/MM/WXX/DD/."""
    from Core.Utils.constants import now_ng
    now = now_ng()
    _, iso_week, _ = now.isocalendar()
    day_dir = (
        DEBUG_LOG_DIR
        / str(now.year)
        / f"{now.month:02d}"
        / f"W{iso_week:02d}"
        / f"{now.day:02d}"
    )
    day_dir.mkdir(parents=True, exist_ok=True)

    timestamp     = now.strftime("%Y%m%d_%H%M%S")
    safe_label    = label.replace(" ", "_").replace("/", "-").replace(":", "")[:50]
    base_filename = f"{safe_label}_{timestamp}"
    try:
        with open(day_dir / f"{base_filename}.txt", "w", encoding="utf-8") as f:
            f.write(f"Context:   {label}\n"
                    f"Timestamp: {now.isoformat()}\n\nInfo:\n{info_text}")
        if page and not page.is_closed():
            try:
                await page.screenshot(path=day_dir / f"{base_filename}.png")
                with open(
                    day_dir / f"{base_filename}.html", "w", encoding="utf-8"
                ) as f:
                    f.write(await page.content())
                print(f"    [Debug Saved] {base_filename}")
            except Exception as e:
                print(f"    [Debug Capture Fail] Screen/HTML: {e}")
    except Exception as e:
        print(f"    [Debug Failure] Could not write debug snapshot: {e}")


# ── BatchProcessor ─────────────────────────────────────────────────────────────

class BatchProcessor:
    def __init__(self, max_concurrent: int = None):
        from Core.Utils.constants import MAX_CONCURRENCY
        self.semaphore = asyncio.Semaphore(max_concurrent or MAX_CONCURRENCY)

    async def _worker(self, func: Callable, item: T, *args, **kwargs):  # type: ignore
        async with self.semaphore:
            return await func(item, *args, **kwargs)

    async def run_batch(self, items: List[T], func: Callable, *args, **kwargs):
        tasks = [self._worker(func, item, *args, **kwargs) for item in items]
        return await asyncio.gather(*tasks)


def parse_date_robust(date_str: str) -> str:
    """Parse date string robustly, supporting YYYY-MM-DD or DD.MM.YYYY."""
    from datetime import datetime as _dt
    if not date_str:
        raise ValueError("Empty date string")
    date_str = date_str.strip()
    try:
        if "-" in date_str:
            return _dt.strptime(date_str, "%Y-%m-%d")
        return _dt.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        raise ValueError(
            f"Could not parse date '{date_str}'. Expected YYYY-MM-DD or DD.MM.YYYY"
        )
