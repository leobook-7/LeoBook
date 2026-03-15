# log_sync.py: Upload unsynced log segments from Data/Logs/ to Supabase Storage.
# Part of LeoBook Data — Access Layer
#
# Classes: LogSync
# Usage:
#   LogSync().push()   → uploads any log_segments rows where uploaded=0
#   Called by: Leo.py --sync (as part of run_full_sync)

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
LOGS_DIR     = PROJECT_ROOT / "Data" / "Logs"
BUCKET_NAME  = "logs"


class LogSync:
    """Upload unsynced log segments to Supabase Storage.

    Mirrors the ModelSync pattern. Called during --sync to catch any
    segments that failed to upload during the session (e.g. no internet,
    crashed process, Supabase timeout).
    """

    def __init__(self):
        from Data.Access.supabase_client import get_supabase_client
        self.supabase = get_supabase_client()

    def push(self, dry_run: bool = False) -> int:
        """Upload all unsynced log segments.

        Returns:
            Number of segments successfully uploaded.
        """
        if not self.supabase:
            logger.warning("[LogSync] No Supabase client — skipping log sync")
            return 0

        from Data.Access.league_db import get_connection
        conn = get_connection()

        # Ensure table exists (may not exist on a fresh install)
        try:
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
            conn.commit()
        except Exception:
            pass

        rows = conn.execute("""
            SELECT id, path, category
            FROM log_segments
            WHERE uploaded = 0
            ORDER BY started_at ASC
        """).fetchall()

        if not rows:
            logger.info("[LogSync] No unsynced log segments.")
            return 0

        logger.info("[LogSync] Uploading %d unsynced segments...", len(rows))
        self._ensure_bucket()

        uploaded = 0
        for row in rows:
            row_id    = row["id"]   if hasattr(row, "keys") else row[0]
            path_str  = row["path"] if hasattr(row, "keys") else row[1]
            path      = Path(path_str)

            if not path.exists():
                # File gone — mark as uploaded so we don't retry forever
                conn.execute(
                    "UPDATE log_segments SET uploaded=1 WHERE id=?", (row_id,)
                )
                conn.commit()
                continue

            try:
                remote_path = self._build_remote_path(path)

                if not dry_run:
                    with open(path, "rb") as f:
                        self.supabase.storage.from_(BUCKET_NAME).upload(
                            path=remote_path,
                            file=f,
                            file_options={"cache-control": "3600", "upsert": "true"}
                        )

                    size = path.stat().st_size
                    conn.execute("""
                        UPDATE log_segments
                        SET uploaded=1, remote_path=?, size_bytes=?
                        WHERE id=?
                    """, (remote_path, size, row_id))
                    conn.commit()

                logger.info("[LogSync] ✓ %s → logs/%s", path.name, remote_path)
                uploaded += 1

            except Exception as e:
                logger.warning("[LogSync] Failed to upload %s: %s", path.name, e)

        logger.info("[LogSync] Segments synced: %d/%d", uploaded, len(rows))
        return uploaded

    def _ensure_bucket(self) -> None:
        try:
            buckets = self.supabase.storage.list_buckets()
            if not any(b.name == BUCKET_NAME for b in buckets):
                self.supabase.storage.create_bucket(
                    BUCKET_NAME, options={"public": False}
                )
                logger.info("[LogSync] Created 'logs' bucket")
        except Exception as e:
            logger.warning("[LogSync] Could not ensure bucket: %s", e)

    @staticmethod
    def _build_remote_path(path: Path) -> str:
        """Build remote path relative to Data/Logs/."""
        try:
            return str(path.relative_to(LOGS_DIR)).replace("\\", "/")
        except ValueError:
            return path.name
