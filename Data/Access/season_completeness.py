# season_completeness.py: tracks match coverage per league per season.
# Part of LeoBook Data — Access Layer (Quality Control)

import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from Data.Access.league_db import get_connection, init_db

logger = logging.getLogger(__name__)

class SeasonCompletenessTracker:
    """
    Calculates and persists data coverage metrics per league/season.
    Ensures historical completeness and monitors live season progress.
    """

    @classmethod
    def _ensure_table(cls):
        """Create the season_completeness table if it doesn't exist."""
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS season_completeness (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                league_id TEXT NOT NULL,
                season TEXT NOT NULL,
                total_expected_matches INTEGER,
                total_scanned_matches INTEGER,
                finished_matches INTEGER,
                scheduled_matches INTEGER,
                live_matches INTEGER,
                postponed_matches INTEGER,
                canceled_matches INTEGER,
                season_status TEXT DEFAULT 'ACTIVE',
                completeness_pct REAL,
                progress_pct REAL,
                last_verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(league_id, season)
            )
        """)
        conn.commit()

    @classmethod
    def compute_for_league(cls, league_id: str, season: str, conn=None):
        """Compute coverage metrics for a single league/season."""
        cls._ensure_table()
        local_conn = conn or get_connection()
        
        # 1. Aggregate match counts from schedules
        stats = local_conn.execute("""
            SELECT 
                COUNT(*) as total_scanned,
                SUM(CASE WHEN match_status IN ('FINISHED', 'finished', 'completed') THEN 1 ELSE 0 END) as finished,
                SUM(CASE WHEN match_status IN ('SCHEDULED', 'scheduled', '') OR match_status IS NULL THEN 1 ELSE 0 END) as scheduled,
                SUM(CASE WHEN match_status IN ('LIVE', 'IN_PROGRESS', 'live') THEN 1 ELSE 0 END) as live,
                SUM(CASE WHEN match_status IN ('POSTPONED', 'postponed') THEN 1 ELSE 0 END) as postponed,
                SUM(CASE WHEN match_status IN ('CANCELED', 'canceled') THEN 1 ELSE 0 END) as canceled
            FROM schedules
            WHERE league_id = ? AND season = ?
        """, (league_id, season)).fetchone()
        
        if not stats or stats["total_scanned"] == 0:
            return None

        # 2. Determine/Fetch expected matches
        total_expected = cls._get_expected_matches(league_id, season, stats["total_scanned"], conn=local_conn)
        
        total_scanned = stats["total_scanned"]
        finished = stats["finished"] or 0
        scheduled = stats["scheduled"] or 0
        live = stats["live"] or 0
        postponed = stats["postponed"] or 0
        canceled = stats["canceled"] or 0
        
        completeness_pct = round((total_scanned / total_expected) * 100, 2) if total_expected > 0 else 0
        progress_pct = round(((finished + postponed + canceled) / total_expected) * 100, 2) if total_expected > 0 else 0
        
        # 3. Determine status
        status = "ACTIVE"
        if completeness_pct >= 99.0 and scheduled == 0 and live == 0:
            status = "COMPLETED"
        elif completeness_pct < 80.0 and status != "COMPLETED":
            status = "INCOMPLETE"

        # 4. Upsert into season_completeness
        local_conn.execute("""
            INSERT INTO season_completeness (
                league_id, season, total_expected_matches, total_scanned_matches,
                finished_matches, scheduled_matches, live_matches, postponed_matches,
                canceled_matches, season_status, completeness_pct, progress_pct, last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(league_id, season) DO UPDATE SET
                total_expected_matches = excluded.total_expected_matches,
                total_scanned_matches = excluded.total_scanned_matches,
                finished_matches = excluded.finished_matches,
                scheduled_matches = excluded.scheduled_matches,
                live_matches = excluded.live_matches,
                postponed_matches = excluded.postponed_matches,
                canceled_matches = excluded.canceled_matches,
                season_status = excluded.season_status,
                completeness_pct = excluded.completeness_pct,
                progress_pct = excluded.progress_pct,
                last_verified_at = excluded.last_verified_at
        """, (
            league_id, season, total_expected, total_scanned, 
            finished, scheduled, live, postponed, canceled,
            status, completeness_pct, progress_pct, datetime.now().isoformat()
        ))
        
        if not conn:
            local_conn.commit()
        return status

    @classmethod
    def _get_expected_matches(cls, league_id: str, season: str, scanned_count: int, conn=None) -> int:
        """
        Heuristic for calculating total matches expected in a round-robin season.
        Formula: teams * (teams - 1)
        """
        local_conn = conn or get_connection()
        # 1. Existing manual override or previous calculation
        row = local_conn.execute("SELECT total_expected_matches FROM season_completeness WHERE league_id=? AND season=?", (league_id, season)).fetchone()
        if row and row["total_expected_matches"]:
            return row["total_expected_matches"]
            
        # 2. Calculate from teams in league
        # We need a way to count teams associated with this league.
        # Currently team.league_ids is a JSON array in SQLite.
        team_count_row = local_conn.execute("SELECT COUNT(*) as cnt FROM teams WHERE league_ids LIKE ?", (f'%"{league_id}"%',)).fetchone()
        team_count = team_count_row["cnt"] if team_count_row else 0
        
        if team_count >= 4:
            # Standard Round-Robin (Home + Away)
            expected = team_count * (team_count - 1)
            # Sanity check: if scanned is much higher, use scanned (could be playoffs/split)
            if scanned_count > expected:
                return scanned_count
            return expected
            
        # Fallback to scanned_count if heuristic is unreliable
        return scanned_count or 1 

    @classmethod
    def bulk_compute_all(cls):
        """Iterate through all unique league+season pairs and update metrics."""
        cls._ensure_table()
        conn = get_connection()
        pairs = conn.execute("SELECT DISTINCT league_id, season FROM schedules WHERE league_id IS NOT NULL AND season IS NOT NULL").fetchall()
        
        count = 0
        for p in pairs:
            cls.compute_for_league(p["league_id"], p["season"], conn=conn)
            count += 1
            if count % 100 == 0:
                conn.commit()
                logger.debug(f"[Completeness] Progress: {count} seasons updated")
            
        conn.commit()
        logger.info(f"[Completeness] Bulk computed {count} league-season combinations.")
        return count
        
    @classmethod
    def get_season_progress(cls, league_id: str, season: str) -> Dict[str, Any]:
        """Fetch status dictionary for UI consumption."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM season_completeness WHERE league_id=? AND season=?", (league_id, season)).fetchone()
        if row:
            return dict(row)
        return {"season_status": "UNKNOWN", "progress_pct": 0}
