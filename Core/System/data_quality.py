# data_quality.py: Scans database for missing or malformed data.
# Part of LeoBook Core — System (Data Integrity)

import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from Data.Access.league_db import get_connection

logger = logging.getLogger(__name__)

class DataQualityScanner:
    """
    Scans database tables for NULLs, empty strings, and malformed data.
    Classifies gaps into resolution categories.
    """

    SUPABASE_BASE_URL = "https://wvasmffspxkdbyxrrhzc.supabase.co/storage/v1/object/public"

    @classmethod
    def scan_table(cls, table_name: str) -> List[Dict[str, Any]]:
        """
        Perform a row-by-row scan of a table to identify data gaps.
        Returns a list of gap dictionaries.
        """
        conn = get_connection()
        rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        column_names = [d[0] for d in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
        
        gaps = []
        for row in rows:
            row_dict = dict(row)
            row_id = row_dict.get("fixture_id") or row_dict.get("team_id") or row_dict.get("league_id") or str(row_dict.get("id"))
            
            for col in column_names:
                val = row_dict.get(col)
                if cls._is_gap(table_name, col, val, row_dict):
                    classification = cls.classify_gap(table_name, col, row_dict)
                    if classification != "DEFERRED":
                        gaps.append({
                            "table": table_name,
                            "row_id": row_id,
                            "column": col,
                            "value": val,
                            "classification": classification,
                            "lookup_key": cls._build_lookup_key(table_name, row_dict)
                        })
        return gaps

    @staticmethod
    def _is_gap(table: str, col: str, val: Any, row: Dict) -> bool:
        """Logic to determine if a value constitutes a 'gap'."""
        if val is None or (isinstance(val, str) and val.strip() == ""):
            # Special case for scores: only gaps if match is finished
            if table == "schedules" and col in ("home_score", "away_score"):
                status = str(row.get("match_status", "")).upper()
                return status in ("FINISHED", "COMPLETED")
            return True
            
        # Crest URL validation
        if col in ("crest", "home_crest", "away_crest", "home_crest_url", "away_crest_url"):
            if isinstance(val, str) and not val.startswith(DataQualityScanner.SUPABASE_BASE_URL):
                return True
                
        return False

    @staticmethod
    def classify_gap(table: str, col: str, row: Dict) -> str:
        """Classify a gap based on its resolution path."""
        if table == "leagues":
            if col == "country_code": return "IMMEDIATE"
            if col == "region_flag": return "DERIVABLE"
            if col in ("fs_league_id", "crest", "current_season"): return "STAGE_ENRICHMENT"
            
        if table == "teams":
            if col == "country_code": return "IMMEDIATE"
            if col == "crest": return "STAGE_ENRICHMENT"
            if col in ("city", "stadium"): return "DEFERRED"

        if table == "schedules":
            if col in ("time", "match_link"): return "STAGE_ENRICHMENT"
            if col in ("home_score", "away_score"): return "STAGE_ENRICHMENT"

        # Default for crest-related fields
        if "crest" in str(col):
            return "STAGE_ENRICHMENT"

        return "DEFERRED"

    @staticmethod
    def _build_lookup_key(table: str, row: Dict) -> Dict[str, Any]:
        """Extract available identifiers for future enrichment lookup."""
        if table == "leagues":
            return {"league_id": row.get("league_id"), "url": row.get("url"), "name": row.get("name")}
        if table == "teams":
            return {"team_id": row.get("team_id"), "name": row.get("name"), "country": row.get("country")}
        if table == "schedules":
            return {
                "fixture_id": row.get("fixture_id"),
                "league_id": row.get("league_id"),
                "season": row.get("season"),
                "home": row.get("home_team_name"),
                "away": row.get("away_team_name")
            }
        return {"id": row.get("id")}

    @classmethod
    def produce_gap_report(cls) -> str:
        """Scan all relevant tables and write a JSON report."""
        report = {
            "timestamp": datetime.now().isoformat(),
            "tables": {}
        }
        
        invalid_crests = 0
        for table in ("leagues", "teams", "schedules"):
            gaps = cls.scan_table(table)
            report["tables"][table] = {
                "total_gaps": len(gaps),
                "details": gaps
            }
            invalid_crests += sum(1 for g in gaps if "crest" in g["column"])

        report["summary"] = {
            "invalid_crest_urls": invalid_crests,
            "total_overall_gaps": sum(t["total_gaps"] for t in report["tables"].values())
        }

        filename = f"Data/Store/data_quality_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4)
            
        logger.info(f"[Scanner] Gap report written to {filename}")
        return filename

class InvalidIDScanner:
    """Detects placeholders, malformed, and duplicate Flashscore IDs."""
    
    # regex: all caps, underscores, no numbers (e.g. ESTONIA_ESILIIGA)
    PLACEHOLDER_PATTERN = r"^[A-Z_]+$"

    @classmethod
    def scan_invalid_ids(cls, table: str, id_column: str) -> List[Dict[str, Any]]:
        """
        Scan a table for invalid IDs (NULL, placeholder, malformed, duplicate).
        Returns a list of rows with invalid IDs and their lookup context.
        """
        import re
        from Data.Access.league_db import get_connection
        conn = get_connection()
        
        # We need all columns for the lookup context
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        
        invalid_rows = []
        seen_ids = {} # For duplicate detection across the table
        
        for row in rows:
            row_dict = dict(row)
            val = row_dict.get(id_column)
            is_invalid = False
            reason = None
            
            # 1. NULL or Empty String
            if val is None or (isinstance(val, str) and val.strip() == ""):
                is_invalid = True
                reason = "NULL_OR_EMPTY"
            
            # 2. Placeholder Pattern (ALL_CAPS_UNDERSCORES, no digits)
            elif isinstance(val, str) and re.match(cls.PLACEHOLDER_PATTERN, val):
                is_invalid = True
                reason = "PLACEHOLDER_PATTERN"
            
            # 3. Starts with UNKNOWN
            elif isinstance(val, str) and val.upper().startswith("UNKNOWN"):
                is_invalid = True
                reason = "UNKNOWN_PREFIX"
                
            # 4. Length Constraints (Flashscore IDs are usually 5-15 chars)
            elif isinstance(val, str) and (len(val) < 3 or len(val) > 50):
                is_invalid = True
                reason = "MALFORMED_LENGTH"
                
            # 5. Duplicate Detection (Only if value is otherwise valid)
            if val and not is_invalid:
                if val in seen_ids:
                    is_invalid = True
                    reason = "DUPLICATE_ID"
                else:
                    seen_ids[val] = True
            
            if is_invalid:
                invalid_rows.append({
                    "table": table,
                    "row_id": str(row_dict.get("id") or row_dict.get("league_id") or row_dict.get("team_id")),
                    "column": id_column,
                    "invalid_value": val,
                    "reason": reason,
                    "lookup_key": cls._build_lookup_context(table, row_dict)
                })
        
        return invalid_rows

    @staticmethod
    def _build_lookup_context(table: str, row: Dict) -> Dict[str, Any]:
        """Produce a JSON-serializable dictionary for re-enrichment search."""
        if table == "leagues":
            return {
                "league_name": row.get("name"),
                "country_code": row.get("country_code"),
                "country_name": row.get("country_name"),
                "season": row.get("season"),
                "region": row.get("region")
            }
        if table == "teams":
            return {
                "team_name": row.get("name"),
                "country_code": row.get("country_code"),
                "league_id": row.get("league_id"),
                "crest": row.get("crest")
            }
        return {}
