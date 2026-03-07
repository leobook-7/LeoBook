# gap_resolver.py: Resolves data gaps via local lookups or enrichment staging.
# Part of LeoBook Core — System (Data Integrity)

import json
import logging
from typing import List, Dict, Any, Optional
from Data.Access.league_db import get_connection, init_db
from Core.System.data_quality import DataQualityScanner

logger = logging.getLogger(__name__)

class GapResolver:
    """
    Handles immediate SQL-based fixes and populates the enrichment_queue for browser tasks.
    """

    @classmethod
    def _ensure_queue_table(cls):
        """Create the enrichment_queue table if it doesn't exist."""
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                row_id TEXT NOT NULL,
                column_name TEXT NOT NULL,
                lookup_key TEXT NOT NULL,
                priority INTEGER DEFAULT 5,
                status TEXT DEFAULT 'PENDING',
                attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            )
        """)
        conn.commit()

    @classmethod
    def resolve_immediate(cls):
        """Perform all non-external fixes (Lookups against countries table)."""
        conn = get_connection()
        stats = {"fixed": 0, "derived": 0}

        # 1. leagues.country_code via countries lookup
        res = conn.execute("""
            UPDATE leagues
            SET country_code = (
                SELECT code FROM countries 
                WHERE countries.name = leagues.continent -- Fallback check for region/country mismatches
                   OR countries.name = (SELECT name FROM leagues l2 WHERE l2.id = leagues.id)
            )
            WHERE (country_code IS NULL OR country_code = '')
        """)
        stats["fixed"] += res.rowcount

        # 2. leagues.region_flag from countries
        res = conn.execute("""
            UPDATE leagues
            SET region_flag = (
                SELECT flag_1x1 FROM countries WHERE countries.code = leagues.country_code
            )
            WHERE (region_flag IS NULL OR region_flag = '') AND country_code IS NOT NULL
        """)
        stats["derived"] += res.rowcount

        # 3. teams.country_code via countries
        # Simple name lookup for now; fuzzy matching can be added later if needed
        res = conn.execute("""
            UPDATE teams
            SET country_code = (
                SELECT code FROM countries WHERE countries.name = teams.country
            )
            WHERE (country_code IS NULL OR country_code = '') AND country IS NOT NULL
        """)
        stats["fixed"] += res.rowcount

        conn.commit()
        logger.info(f"[GapResolver] Fixed {stats['fixed']} missing codes, derived {stats['derived']} flags.")
        return stats

    @classmethod
    def stage_enrichment(cls, gaps: List[Dict[str, Any]]):
        """Push STAGE_ENRICHMENT gaps to the enrichment_queue table."""
        cls._ensure_queue_table()
        conn = get_connection()
        
        staged_count = 0
        for gap in gaps:
            if gap["classification"] != "STAGE_ENRICHMENT":
                continue
                
            priority = cls._determine_priority(gap["column"])
            
            conn.execute("""
                INSERT OR IGNORE INTO enrichment_queue (table_name, row_id, column_name, lookup_key, priority)
                VALUES (?, ?, ?, ?, ?)
            """, (
                gap["table"],
                gap["row_id"],
                gap["column"],
                json.dumps(gap["lookup_key"]),
                priority
            ))
            staged_count += 1
            
        conn.commit()
        logger.info(f"[GapResolver] Staged {staged_count} gaps for re-enrichment.")
        return staged_count

        logger.info(f"[GapResolver] Staged {staged_count} gaps for re-enrichment.")
        return staged_count

    @staticmethod
    def _determine_priority(column: str) -> int:
        """Priority mapping: 1=Critical, 5=Normal, 10=Low"""
        if column == "fs_league_id": return 1
        if column == "team_id": return 1
        if column in ("time", "match_link"): return 2
        if "crest" in column: return 3
        if column == "current_season": return 3
        return 5

class InvalidIDResolver:
    """Handles resolution and staging of placeholder/invalid IDs."""

    @classmethod
    def attempt_local_resolution(cls, table: str, invalid_rows: List[Dict[str, Any]]):
        """Cross-checks local sources (leagues.json, duplicates) to fix IDs before staging."""
        import sqlite3
        conn = get_connection()
        fixed_count = 0
        
        # Indexed seed data for O(1) lookups
        leagues_seed = cls._load_leagues_seed()
        
        for row in invalid_rows:
            new_id = None
            lookup = row["lookup_key"]
            old_val = row["invalid_value"]
            
            # Path A: leagues.json lookup (leagues only)
            if table == "leagues":
                name = lookup.get("league_name")
                country = lookup.get("country_name")
                if name and country:
                    key = f"{country}:{name}".lower()
                    if key in leagues_seed:
                        new_id = leagues_seed[key].get("fs_league_id")

            # Path B: Duplicate match with valid ID elsewhere in the same table
            if not new_id:
                new_id = cls._find_duplicate_valid_id(table, row)

            if new_id:
                try:
                    # IMMEDIATE fix: Update the record
                    conn.execute(
                        f"UPDATE {table} SET {row['column']} = ? WHERE id = ?",
                        (new_id, row["row_id"])
                    )
                    fixed_count += 1
                except sqlite3.IntegrityError:
                    # Duplicate row detected (valid row already exists)
                    logger.warning(f"[ID Resolver] Duplicate {table} '{new_id}' detected. Merging and deleting invalid row.")
                    cls._merge_dependencies(table, old_val, new_id, conn=conn)
                    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row["row_id"],))
                    fixed_count += 1

        conn.commit()
        if fixed_count > 0:
            logger.info(f"[ID Resolver] Resolved {fixed_count} {table} IDs locally.")
        return fixed_count

    @classmethod
    def _merge_dependencies(cls, table: str, old_id: str, new_id: str, conn=None):
        """Update foreign keys in schedules when a row is deleted due to deduplication."""
        if not old_id or old_id == new_id: return
        local_conn = conn or get_connection()
        if table == "teams":
            local_conn.execute("UPDATE schedules SET home_team_id = ? WHERE home_team_id = ?", (new_id, old_id))
            local_conn.execute("UPDATE schedules SET away_team_id = ? WHERE away_team_id = ?", (new_id, old_id))
        # Note: leagues table primarily uses internal league_id, so we skip league-id merge here 
        # unless fs_league_id is used for joins.

    @classmethod
    def stage_invalid_ids(cls, table: str, invalid_rows: List[Dict[str, Any]]):
        """Push unresolved invalid IDs to the enrichment_queue with Priority 1."""
        GapResolver._ensure_queue_table()
        conn = get_connection()
        staged_count = 0
        
        for row in invalid_rows:
            # Re-check current value in DB to ensure it wasn't fixed locally in this session
            current = conn.execute(f"SELECT {row['column']} FROM {table} WHERE id = ?", (row["row_id"],)).fetchone()
            if current:
                curr_val = current[0]
                # Logic to check if curr_val is still "invalid"
                import re
                is_still_invalid = (
                    curr_val is None or 
                    curr_val == "" or 
                    re.match(r"^[A-Z_]+$", str(curr_val)) or 
                    str(curr_val).upper().startswith("UNKNOWN")
                )
                if not is_still_invalid:
                    continue

            conn.execute("""
                INSERT OR IGNORE INTO enrichment_queue (table_name, row_id, column_name, lookup_key, priority)
                VALUES (?, ?, ?, ?, ?)
            """, (
                row["table"],
                row["row_id"],
                row["column"],
                json.dumps(row["lookup_key"]),
                1 # CRITICAL priority
            ))
            staged_count += 1
            
        conn.commit()
        return staged_count

    @staticmethod
    def _load_leagues_seed() -> Dict[str, Dict]:
        """Load leagues.json and index by country:name."""
        import os
        # Match the path from Leo.py/data_readiness.py
        p = os.path.join(os.path.dirname(__file__), "..", "..", "Data", "Store", "leagues.json")
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {f"{l.get('country')}:{l.get('name')}".lower(): l for l in data}
        except Exception:
            return {}

    @staticmethod
    def _find_duplicate_valid_id(table: str, row: Dict) -> Optional[str]:
        """Check if another row with the same name/country has a valid ID."""
        conn = get_connection()
        lookup = row["lookup_key"]
        import re
        
        name = lookup.get("league_name") or lookup.get("team_name")
        country = lookup.get("country_code")
        if not name or not country: return None
        
        # Fetch candidate ids for the same name/country
        col = row["column"]
        candidates = conn.execute(
            f"SELECT DISTINCT {col} FROM {table} WHERE (name = ? OR ? IS NULL) AND country_code = ? AND {col} IS NOT NULL",
            (name, name, country)
        ).fetchall()
        
        for (c_id,) in candidates:
            if not c_id: continue
            # Check if candidate is valid (not a placeholder)
            if not re.match(r"^[A-Z_]+$", str(c_id)) and not str(c_id).upper().startswith("UNKNOWN"):
                return c_id
        
        return None
