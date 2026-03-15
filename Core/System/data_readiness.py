# data_readiness.py: Pre-flight data completeness checks for Leo.py Prologue.
# Part of LeoBook Core — System
#
# Functions: check_leagues_ready(), check_seasons_ready(), check_rl_ready()
# Called by Prologue P1-P3 to gate pipeline execution.

import os
import json
import logging
import asyncio
import time
from typing import Tuple, Dict, Optional

from Core.Utils.constants import now_ng
from Data.Access.league_db import init_db, query_all

logger = logging.getLogger(__name__)

# Increment this when the stats dict schema changes.
# Causes stale caches written by an old schema to be ignored on read.
_CACHE_SCHEMA_VERSION = "9.3"

# Path to leagues.json (source of truth for expected leagues)
_LEAGUES_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Config', 'leagues.json'
)


def _get_expected_league_count() -> int:
    """Count leagues defined in leagues.json."""
    try:
        with open(_LEAGUES_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return len(data)
        elif isinstance(data, dict):
            return sum(len(v) if isinstance(v, list) else 1 for v in data.values())
    except Exception:
        pass
    return 0


def invalidate_cache(gate_id: str):
    """Clear a specific gate cache for forced re-scan."""
    from Data.Access.league_db import init_db
    conn = init_db()
    conn.execute("DELETE FROM readiness_cache WHERE gate_id = ?", (gate_id,))
    conn.commit()
    logger.info(f"[Cache] Invalidated gate: {gate_id}")

def update_cache(gate_id: str, is_ready: bool, details: Dict):
    """Persist check results to the materialized cache table."""
    from Data.Access.league_db import init_db
    from Core.Utils.constants import now_ng
    details_with_version = {**details, "_schema_version": _CACHE_SCHEMA_VERSION}
    conn = init_db()
    conn.execute("""
        INSERT OR REPLACE INTO readiness_cache (gate_id, is_ready, details, updated_at)
        VALUES (?, ?, ?, ?)
    """, (gate_id, 1 if is_ready else 0, json.dumps(details_with_version), now_ng().isoformat()))
    conn.commit()

def _read_cache(gate_id: str) -> Optional[Tuple[bool, Dict]]:
    """Internal: Reads from readiness_cache if not bypassed.
    Returns None if the cache is missing, bypassed, or uses an old schema version."""
    # Check if --bypass-cache is in CLI
    import sys
    if '--bypass-cache' in sys.argv:
        return None
        
    from Data.Access.league_db import init_db
    conn = init_db()
    try:
        row = conn.execute("SELECT is_ready, details FROM readiness_cache WHERE gate_id = ?", (gate_id,)).fetchone()
        if row:
            details = json.loads(row[1])
            # Invalidate cache if written by an older schema version
            if details.get("_schema_version") != _CACHE_SCHEMA_VERSION:
                conn.execute("DELETE FROM readiness_cache WHERE gate_id = ?", (gate_id,))
                conn.commit()
                logger.info(f"[Cache] Stale schema for gate '{gate_id}' — cleared, will re-scan.")
                return None
            return bool(row[0]), details
    except Exception:
        pass
    return None

def check_leagues_ready(conn=None) -> Tuple[bool, Dict]:
    """Check if leagues >= 90% of leagues.json AND teams >= 5 per processed league.
    READS FROM CACHE FIRST. Original scan logic is the fallback.
    """
    cached = _read_cache('PROLOGUE_P1')
    if cached:
        is_ready, stats = cached
        print(f"  [P1 ✓] (Cached) Readiness: {'READY' if is_ready else 'NOT READY'}")
        return is_ready, stats

    # FALLBACK: Original O(N) scan logic
    conn = conn or init_db()
    expected = _get_expected_league_count()
    actual_leagues = conn.execute("SELECT COUNT(*) FROM leagues").fetchone()[0]
    processed = conn.execute("SELECT COUNT(*) FROM leagues WHERE processed = 1").fetchone()[0]
    team_count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]

    threshold = int(expected * 0.9) if expected > 0 else 1000
    leagues_ok = actual_leagues >= threshold
    teams_per_league = (team_count / max(processed, 1)) if processed > 0 else 0
    teams_ok = teams_per_league >= 5 or team_count >= 5000

    # NEW: Invalid ID Check (v7.1 Extension)
    from Core.System.data_quality import InvalidIDScanner
    from Core.System.gap_resolver import InvalidIDResolver
    
    print("  [P1] Validating Flashscore IDs...")
    league_invalids = InvalidIDScanner.scan_invalid_ids("leagues", "fs_league_id")
    invalid_rate = (len(league_invalids) / max(actual_leagues, 1)) * 100
    
    if invalid_rate > 5:
        print(f"  [P1] Invalid ID rate ({invalid_rate:.1f}%) above 5%. Running Resolver...")
        InvalidIDResolver.attempt_local_resolution("leagues", league_invalids)
        InvalidIDResolver.stage_invalid_ids("leagues", league_invalids)
        
        # Re-evaluate
        league_invalids = InvalidIDScanner.scan_invalid_ids("leagues", "fs_league_id")
        invalid_rate = (len(league_invalids) / max(actual_leagues, 1)) * 100
    
    ids_ok = invalid_rate <= 5

    is_ready = leagues_ok and teams_ok and ids_ok
    stats = {
        "expected_leagues": expected, "actual_leagues": actual_leagues,
        "threshold": threshold, "processed_leagues": processed,
        "team_count": team_count, "teams_per_league": round(teams_per_league, 1),
        "invalid_id_rate": round(invalid_rate, 1),
        "leagues_ok": leagues_ok, "teams_ok": teams_ok, "ids_ok": ids_ok, "ready": is_ready,
    }

    if is_ready:
        print(f"  [P1 ✓] Leagues: {actual_leagues}/{expected}, Teams: {team_count}, IDs: OK ({invalid_rate:.1f}%)")
    else:
        reasons = []
        if not leagues_ok: reasons.append(f"Leagues < {threshold}")
        if not teams_ok: reasons.append(f"Teams/League < 5")
        if not ids_ok: reasons.append(f"Invalid IDs > 5% ({invalid_rate:.1f}%)")
        print(f"  [P1 ✗] NOT READY: {', '.join(reasons)}")

    # Update cache after scan
    update_cache('PROLOGUE_P1', is_ready, stats)
    return is_ready, stats

def check_seasons_ready(conn=None, min_seasons: int = 2) -> Tuple[bool, Dict]:
    """
    PROLOGUE P2: Data Quality & Season Completeness.
    Runs DataQualityScanner, GapResolver (IMMEDIATE), and SeasonCompletenessTracker.
    """
    cached = _read_cache('PROLOGUE_P2')
    if cached:
        is_ready, stats = cached
        print(f"  [P2 ✓] (Cached) Readiness: {'READY' if is_ready else 'NOT READY'}")
        return is_ready, stats

    from Core.System.data_quality import DataQualityScanner
    from Core.System.gap_resolver import GapResolver
    from Data.Access.season_completeness import SeasonCompletenessTracker

    print("  [P2] Running Data Quality Scan & Gap Resolution...")
    
    # 1. Immediate fixes
    resolver_stats = GapResolver.resolve_immediate()
    
    # 2. Scan for remaining gaps
    all_gaps = []
    for table in ("leagues", "teams", "schedules"):
        all_gaps.extend(DataQualityScanner.scan_table(table))
    
    # 3. Stage for re-enrichment
    staged_count = GapResolver.stage_enrichment(all_gaps)
    
    # 4. Refresh Season Completeness
    print("  [P2] Computing Season Completeness Metrics...")
    total_computed = SeasonCompletenessTracker.bulk_compute_all()
    
    # 5. Evaluate Gate logic
    conn = conn or init_db()
    
    # Critical gap: fs_league_id missing
    critical_gaps = [g for g in all_gaps if g["column"] == "fs_league_id"]
    critical_count = len(critical_gaps)
    
    # Season coverage threshold: FAIL ONLY if COMPLETED seasons have verified mismatch
    completeness_stats = conn.execute("""
        SELECT 
            COUNT(*) as total_seasons,
            SUM(CASE WHEN season_status = 'COMPLETED' THEN 1 ELSE 0 END) as completed_count,
            SUM(CASE WHEN season_status = 'COMPLETED' AND total_scanned_matches < total_expected_matches THEN 1 ELSE 0 END) as completed_mismatch_count,
            SUM(CASE WHEN season_status = 'ACTIVE' THEN 1 ELSE 0 END) as active_count,
            AVG(CASE WHEN season_status = 'ACTIVE' THEN completeness_pct ELSE NULL END) as active_avg_comp
        FROM season_completeness
    """).fetchone()

    total_seasons = completeness_stats[0] or 0
    completed_count = completeness_stats[1] or 0
    completed_mismatch = completeness_stats[2] or 0
    active_count = completeness_stats[3] or 0
    active_avg_comp = completeness_stats[4] or 0

    # ── Job A: Internal consistency (gate — blocks if broken) ────────────────
    # Same as before: no critical fs_league_id gaps, no completed mismatches.
    # CUP_FORMAT seasons are excluded — they cannot mismatch by design.
    is_ready = critical_count == 0 and completed_mismatch == 0

    # ── Job B: Historical depth (informational — never blocks) ───────────────
    # Count leagues that have at least 1 prior season beyond the current one.
    # This is a richness metric for the ensemble, not a pipeline gate.
    depth_row = conn.execute("""
        SELECT
            COUNT(DISTINCT league_id) as leagues_with_history,
            AVG(prior_season_count) as avg_prior_seasons
        FROM (
            SELECT league_id, COUNT(*) as prior_season_count
            FROM season_completeness
            WHERE season_status IN ('COMPLETED', 'ACTIVE')
              AND season_status != 'CUP_FORMAT'
              AND finished_matches >= 20
              AND season != COALESCE(
                  (SELECT current_season FROM leagues l
                   WHERE l.league_id = season_completeness.league_id LIMIT 1),
                  ''
              )
            GROUP BY league_id
        )
    """).fetchone()

    leagues_with_history = (
        depth_row["leagues_with_history"]
        if hasattr(depth_row, "keys") else depth_row[0]
    ) if depth_row else 0
    avg_prior_seasons = (
        round(depth_row["avg_prior_seasons"] or 0, 1)
        if hasattr(depth_row, "keys") else round(depth_row[1] or 0, 1)
    ) if depth_row else 0.0

    # Determine RL readiness tier (informational only)
    total_active_leagues = active_count or 0
    history_pct = (leagues_with_history / max(total_active_leagues, 1)) * 100

    if history_pct >= 50:
        rl_tier = "FULL"         # RL operates at full weight for majority of leagues
    elif history_pct >= 20:
        rl_tier = "PARTIAL"      # RL partially active — Rule Engine dominates
    else:
        rl_tier = "RULE_ENGINE"  # Pure Rule Engine — no meaningful RL history yet

    stats = {
        "critical_gaps": critical_count,
        "total_gaps_staged": staged_count,
        "immediates_fixed": resolver_stats["fixed"],
        "immediates_derived": resolver_stats["derived"],
        "total_seasons": total_seasons,
        "completed_seasons": completed_count,
        "completed_mismatch": completed_mismatch,
        "active_seasons": active_count,
        "active_avg_comp": round(active_avg_comp, 1),
        "leagues_with_history": leagues_with_history,
        "avg_prior_seasons": avg_prior_seasons,
        "history_pct": round(history_pct, 1),
        "rl_tier": rl_tier,
        "ready": is_ready,
    }

    print("  ============================================================")
    print("    PROLOGUE P2: Data Quality & Season Completeness")
    print("  ============================================================")
    print(f"    [Leagues] FIXED {resolver_stats['fixed']} nulls, DERIVED {resolver_stats['derived']} flags")
    print(f"    [Queue]   {staged_count} items staged for re-enrichment ({critical_count} CRITICAL)")
    print(f"    [Seasons] {total_seasons} league-seasons computed")
    print(f"      - COMPLETED:   {completed_count} ({completed_mismatch} mismatches)")
    print(f"      - ACTIVE:      {active_count} (avg {round(active_avg_comp, 1)}% coverage)")
    print(f"      - CUP_FORMAT:  (excluded from gate)")
    print(f"    [History] {leagues_with_history} leagues have prior season data")
    print(f"      - Avg prior seasons: {avg_prior_seasons}")
    print(f"      - RL tier: {rl_tier}  "
          f"({'Full RL weight' if rl_tier == 'FULL' else 'Rule Engine dominant' if rl_tier == 'RULE_ENGINE' else 'Partial RL weight'})")

    if is_ready:
        print(f"    [P2 \u2713] READY \u2014 {critical_count} critical gaps | "
              f"{completed_count} seasons COMPLETED | {active_count} ACTIVE | "
              f"RL tier: {rl_tier}")
    else:
        print(f"    [P2 \u2717] NOT READY \u2014 {critical_count} critical gaps | "
              f"{completed_mismatch} completed mismatches")
    print("  ============================================================")

    update_cache('PROLOGUE_P2', is_ready, stats)
    return is_ready, stats

def check_rl_ready() -> Tuple[bool, Dict]:
    """Check if RL model and adapters are trained."""
    cached = _read_cache('PROLOGUE_P3')
    if cached:
        is_ready, stats = cached
        print(f"  [P3 ✓] (Cached) Readiness: {'READY' if is_ready else 'NOT READY'}")
        return is_ready, stats

    # FALLBACK: Original FS check
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Data', 'Store', 'models')
    base_model = os.path.join(models_dir, 'leobook_base.pth')
    registry_file = os.path.join(models_dir, 'adapter_registry.json')

    has_base = os.path.exists(base_model)
    has_registry = os.path.exists(registry_file)
    adapter_count = 0
    if has_registry:
        try:
            with open(registry_file, 'r') as f: reg = json.load(f)
            adapter_count = len(reg.get('leagues', {}))
        except Exception: pass

    is_ready = has_base and has_registry and adapter_count > 0
    stats = {"has_base_model": has_base, "has_registry": has_registry, "adapter_count": adapter_count, "ready": is_ready}

    if is_ready:
        print(f"  [P3 ✓] RL: Base model + {adapter_count} adapters")
    else:
        print(f"  [P3 ✗] RL NOT READY")

    update_cache('PROLOGUE_P3', is_ready, stats)
    return is_ready, stats


async def auto_remediate(check: str, conn=None, timeout_minutes: int = 30) -> bool:
    """Auto-fix data readiness failures with a time budget.

    Args:
        check: 'leagues', 'seasons', or 'rl'
        timeout_minutes: Max time to spend on remediation (default 30).

    Returns:
        True if remediation completed, False if timed out or failed.
    """
    if check == "leagues":
        print("  [AUTO] Running league enrichment...")
        try:
            from Scripts.enrich_leagues import main as run_enricher
            await asyncio.wait_for(run_enricher(), timeout=timeout_minutes * 60)
            return True
        except asyncio.TimeoutError:
            print(f"  [AUTO] Enrichment exceeded {timeout_minutes}min budget -- proceeding with available data.")
            return False
        except Exception as e:
            logger.error(f"  [AUTO] League enrichment failed: {e}")
            print(f"  [AUTO] Failed: {e}")
            return False

    elif check == "seasons":
        print("  [AUTO] Running historical season enrichment (2 seasons)...")
        try:
            from Scripts.enrich_leagues import main as run_enricher
            await asyncio.wait_for(run_enricher(num_seasons=2), timeout=timeout_minutes * 60)
            return True
        except asyncio.TimeoutError:
            print(f"  [AUTO] Season enrichment exceeded {timeout_minutes}min budget -- proceeding with available data.")
            return False
        except Exception as e:
            logger.error(f"  [AUTO] Season enrichment failed: {e}")
            print(f"  [AUTO] Failed: {e}")
            return False

    elif check == "rl":
        print("  [AUTO] Running RL training...")
        try:
            from Core.Intelligence.rl.trainer import RLTrainer
            trainer = RLTrainer()
            trainer.train_from_fixtures()
            print("  [AUTO] RL training complete")
            return True
        except Exception as e:
            logger.error(f"  [AUTO] RL training failed: {e}")
            print(f"  [AUTO] RL training failed: {e}")
            return False

    return False
