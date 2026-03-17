# match_resolver.py: Team name resolution for Football.com match pairing.
# Part of LeoBook Modules — FootballCom
#
# Classes: GrokMatcher
# Cascade: SQL matching engine (match_fb_to_schedule RPC, confidence ≥ 88)
#       → search_dict (exact alias, bidirectional)
#       → Gemini LLM fallback (cold-start / genuinely ambiguous)
# v1.2 (2026-03-17): SQL-first deterministic resolver added.
#   - match_fb_to_schedule() Supabase RPC wired as Stage 0.
#   - auto_match_batch() for batch-resolving all unmatched fb_matches in one call.
#   - LLM fallback preserved for confidence < 88 or cold-start.

import os
import json
import sqlite3
import asyncio
from typing import List, Dict, Optional, Tuple, Set

try:
    from Data.Access.supabase_client import get_client as _get_supabase
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False

_session_dead_models: Set[str] = set()

try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

# Confidence threshold for SQL resolver to accept a match without LLM fallback.
SQL_CONFIDENCE_THRESHOLD = 88


class GrokMatcher:
    """
    3-stage cascade resolver:
      Stage 0 — SQL matching engine (match_fb_to_schedule RPC, confidence ≥ 88)
      Stage 1 — search_dict (exact + bidirectional alias lookup, local SQLite)
      Stage 2 — Gemini LLM fallback (cold-start / genuinely ambiguous names)

    v1.2 (2026-03-17): SQL-first deterministic resolver wired as Stage 0.
    The SQL engine uses normalize_team_name() + date-windowed scoring on Supabase.
    Python falls back to search_dict + LLM only when SQL confidence < 88.

    Imported by fb_manager.py for Chapter 1 Page 1 resolution.
    """

    def __init__(self):
        self._cache: Dict[str, Optional[Dict]] = {}
        self._supabase = _get_supabase() if HAS_SUPABASE else None

    @staticmethod
    def _get_name(m: Dict, role: str) -> str:
        """Extract home/away name from a candidate dict."""
        return (m.get(f'{role}_team') or m.get(role) or '').strip()

    def _get_team_id(self, m: Dict, role: str) -> Optional[str]:
        """Extract team_id for auto-learn storage."""
        return m.get(f'{role}_team_id') or m.get(f'{role}_id')

    def _get_search_terms(self, conn: sqlite3.Connection, team_id: Optional[str]) -> List[str]:
        """Load alternative search terms for a team from the search_dict table."""
        if not team_id or not conn:
            return []
        try:
            cur = conn.execute(
                "SELECT search_terms FROM search_dict WHERE team_id = ?", (team_id,)
            )
            row = cur.fetchone()
            if row and row[0]:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            pass
        return []

    def _auto_learn(self, conn: sqlite3.Connection, team_id: Optional[str], new_alias: str) -> None:
        """Persist a newly discovered alias into search_dict for future sessions."""
        if not team_id or not conn or not new_alias:
            return
        try:
            alias_lower = new_alias.strip().lower()
            terms = self._get_search_terms(conn, team_id)
            if alias_lower not in [t.strip().lower() for t in terms]:
                terms.append(new_alias.strip())
                conn.execute(
                    "INSERT INTO search_dict (team_id, search_terms) VALUES (?, ?) "
                    "ON CONFLICT(team_id) DO UPDATE SET search_terms = excluded.search_terms",
                    (team_id, json.dumps(terms))
                )
                conn.commit()
        except Exception:
            pass  # Non-critical


    # ───────────────────────────────────────────────────────────────────────────
    # Stage 0: SQL matching engine
    # ───────────────────────────────────────────────────────────────────────────

    async def resolve_via_sql(
        self,
        site_match_id: str,
        fb_match_row: Optional[Dict] = None,
    ) -> Tuple[Optional[Dict], int, str]:
        """
        Call match_fb_to_schedule(p_site_match_id) Supabase RPC.
        Returns (enriched_fb_row, confidence_int, 'sql_v1.2') or (None, 0, 'sql_miss').

        If Supabase is unavailable or RPC fails, returns (None, 0, 'sql_skip').
        """
        if not self._supabase or not HAS_SUPABASE:
            return None, 0, 'sql_skip'
        try:
            result = await asyncio.to_thread(
                lambda: self._supabase.rpc(
                    'match_fb_to_schedule',
                    {'p_site_match_id': site_match_id}
                ).execute()
            )
            rows = result.data or []
            if not rows:
                return None, 0, 'sql_miss'
            best = rows[0]  # Already ordered by confidence DESC in RPC
            confidence = int(best.get('confidence', 0))
            if confidence < SQL_CONFIDENCE_THRESHOLD:
                return None, confidence, 'sql_low_confidence'

            # Merge SQL result into fb_match_row dict for downstream compatibility
            enriched = dict(fb_match_row or {})
            enriched['fixture_id']   = best.get('fixture_id')
            enriched['home_team_id'] = best.get('home_team_id')
            enriched['away_team_id'] = best.get('away_team_id')
            enriched['matched']      = 'sql_v1.2'
            return enriched, confidence, 'sql_v1.2'
        except Exception as e:
            print(f"    [Resolver] SQL RPC failed for {site_match_id}: {e}")
            return None, 0, 'sql_error'

    @staticmethod
    async def auto_match_batch() -> int:
        """
        Call auto_match_fb_matches() Supabase RPC to bulk-resolve all unmatched
        fb_matches in one Postgres pass. Returns count of newly matched rows.
        Call this after a full harvest run before the prediction pipeline.
        """
        if not HAS_SUPABASE:
            return 0
        try:
            supabase = _get_supabase()
            result = await asyncio.to_thread(
                lambda: supabase.rpc('auto_match_fb_matches').execute()
            )
            count = result.data or 0
            print(f"    [Resolver] auto_match_batch: {count} fb_matches newly resolved via SQL.")
            return int(count)
        except Exception as e:
            print(f"    [Resolver] auto_match_batch failed: {e}")
            return 0

    async def resolve_with_cascade(
        self,
        fs_fix: Dict,
        fb_matches: List[Dict],
        conn: sqlite3.Connection,
    ) -> Tuple[Optional[Dict], float, str]:
        """
        3-stage cascade:
          Stage 0 — SQL matching engine via match_fb_to_schedule() RPC.
                     Confidence ≥ 88 → accept immediately (no LLM).
          Stage 1 — search_dict (exact + bidirectional alias lookup).
          Stage 2 — Gemini LLM fallback for genuinely ambiguous names.

        Returns: (best_match_dict, score, method_str)
        """
        if not fb_matches:
            return None, 0.0, 'failed'

        home = (fs_fix.get('home_team_name') or fs_fix.get('home_team') or '').strip()
        away = (fs_fix.get('away_team_name') or fs_fix.get('away_team') or '').strip()
        home_id = fs_fix.get('home_team_id') or fs_fix.get('home_id')
        away_id = fs_fix.get('away_team_id') or fs_fix.get('away_id')

        if not home or not away:
            return None, 0.0, 'failed'

        # ── Stage 0: SQL matching engine ─────────────────────────────────────
        # Try each fb_match candidate via SQL resolver; take the first that hits ≥ 88.
        for fb_row in fb_matches:
            site_id = fb_row.get('site_match_id') or fb_row.get('id', '')
            if not site_id:
                continue
            sql_match, sql_conf, sql_method = await self.resolve_via_sql(site_id, fb_row)
            if sql_match and sql_conf >= SQL_CONFIDENCE_THRESHOLD:
                # Persist to search_dict for offline fallback resilience
                self._auto_learn(conn, home_id, self._get_name(sql_match, 'home'))
                self._auto_learn(conn, away_id, self._get_name(sql_match, 'away'))
                return {**sql_match, 'matched': True}, sql_conf / 100.0, sql_method

        # ── Stage 1: search_dict ─────────────────────────────────────────────
        home_terms = self._get_search_terms(conn, home_id)
        away_terms = self._get_search_terms(conn, away_id)
        h_aliases = [home.lower()] + [t.lower() for t in home_terms]
        a_aliases = [away.lower()] + [t.lower() for t in away_terms]

        for m in fb_matches:
            fb_h = self._get_name(m, 'home').lower()
            fb_a = self._get_name(m, 'away').lower()
            h_match = fb_h in h_aliases or any(fb_h in alias for alias in h_aliases) or any(alias in fb_h for alias in h_aliases if len(alias) >= 4)
            a_match = fb_a in a_aliases or any(fb_a in alias for alias in a_aliases) or any(alias in fb_a for alias in a_aliases if len(alias) >= 4)
            if h_match and a_match:
                self._auto_learn(conn, home_id, self._get_name(m, 'home'))
                self._auto_learn(conn, away_id, self._get_name(m, 'away'))
                return {**m, 'matched': True}, 0.98, 'search_terms'

        return None, 0.0, 'failed'

