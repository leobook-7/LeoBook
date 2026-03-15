# lifecycle.py: lifecycle.py: Global state management, CLI parsing, and application lifecycle control.
# Part of LeoBook Core — System
#
# Functions: log_state(), log_audit_state(), setup_terminal_logging(), parse_args()

import os
import sys
import argparse
import uuid
from pathlib import Path
from datetime import datetime as dt
from Core.Utils.constants import DEFAULT_STAKE
from Core.Utils.utils import RotatingSegmentLogger

_current_dir = Path(__file__).parent.absolute()
LOG_DIR = _current_dir.parent.parent / "Data" / "Logs"

state = {
    "cycle_start_time": None, 
    "cycle_count": 0,
    "current_chapter": "Startup",
    "last_action": "Init",
    "next_expected": "Startup Checks",
    "why_this_step": "System initialization",
    "expected_outcome": "Ready to start",
    "ai_server_ready": False,
    "llm_needed_for_this_cycle": False, 
    "pending_count": 0,
    "booked_this_cycle": 0,
    "failed_this_cycle": 0,
    "current_balance": 0.0,
    "last_win_amount": 5000.0 * DEFAULT_STAKE, # Scalable
    "error_log": []
}

def log_state(chapter=None, action=None, next_step=None, why=None, expect=None):
    """Updates and prints the current system state."""
    global state
    if chapter: state["current_chapter"] = chapter
    if action: state["last_action"] = action
    if next_step: state["next_expected"] = next_step
    if why: state["why_this_step"] = why
    if expect: state["expected_outcome"] = expect
    
    print(f"   [STATE] {state['current_chapter']} | Done: {state['last_action']} | Next: {state['next_expected']} | Why: {state['why_this_step']}")

def log_audit_state(chapter: str, action: str, details: str = ""):
    """Central state logger — prints to console and appends to audit_log.csv"""
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"[{timestamp}] [STATE] {chapter} | Action: {action} | {details}"
    print(message)
    
    from Data.Access.db_helpers import append_to_csv
    append_to_csv("audit_log.csv", {
        "id": str(uuid.uuid4()),
        "timestamp": timestamp,
        "event_type": "STATE",
        "description": f"{chapter} - {action} - {details}",
        "balance_before": "",
        "balance_after": "",
        "stake": "",
        "status": "INFO"
    })

def setup_terminal_logging(args) -> tuple:
    """Set up rotating segment logging to Data/Logs/Terminal/.

    Returns (logger_instance, original_stdout, original_stderr) so
    Leo.py can restore streams and call logger.close_segment() on exit.
    """
    if args:
        os.environ["PLAYWRIGHT_TIMEOUT"] = "3600000"

    # Determine session prefix from CLI args
    prefix = "leo_session"
    if args:
        if getattr(args, 'sync', False):             prefix = "leo_sync"
        elif getattr(args, 'recommend', False):      prefix = "leo_recommend"
        elif getattr(args, 'accuracy', False):       prefix = "leo_accuracy"
        elif getattr(args, 'search_dict', False):    prefix = "leo_search"
        elif getattr(args, 'review', False):         prefix = "leo_review"
        elif getattr(args, 'rule_engine', False):    prefix = "leo_rule_engine"
        elif getattr(args, 'streamer', False):       prefix = "leo_streamer"
        elif getattr(args, 'prologue', False):       prefix = "leo_prologue"
        elif getattr(args, 'chapter', None):         prefix = f"leo_chapter{args.chapter}"
        elif getattr(args, 'assets', False):         prefix = "leo_assets"
        elif getattr(args, 'logos', False):          prefix = "leo_logos"
        elif getattr(args, 'enrich_leagues', False): prefix = "leo_enrich"
        elif getattr(args, 'dry_run', False):        prefix = "leo_dry_run"
        elif getattr(args, 'train_rl', False):       prefix = f"leo_train_rl_p{getattr(args, 'phase', 1)}"
        elif getattr(args, 'data_quality', False):   prefix = "leo_data_quality"

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    logger = RotatingSegmentLogger(
        original_stdout,
        category="Terminal",
        prefix=prefix,
    )

    sys.stdout = logger
    sys.stderr = logger

    # Print session header — first timestamped line in the segment
    from Core.Utils.constants import now_ng
    print(f"{'='*60}")
    print(f"  LeoBook Session Start | prefix={prefix}")
    print(f"  Started: {now_ng().strftime('%Y-%m-%d %H:%M:%S WAT')}")
    print(f"{'='*60}")

    return logger, original_stdout, original_stderr

def parse_args():
    """
    Unified CLI for LeoBook. Leo.py is the single entry point.

    Usage examples:
      python Leo.py                       # Full cycle (Prologue → Ch1 → Ch2 → Ch3, loop)
      python Leo.py --prologue            # All prologue pages only
      python Leo.py --prologue --page 1   # Prologue Page 1 only (Sync + Review)
      python Leo.py --chapter 1           # Full Chapter 1
      python Leo.py --chapter 1 --page 2  # Ch1 Page 2 only (Odds Harvesting)
      python Leo.py --sync                # Force full cloud sync
      python Leo.py --recommend           # Generate recommendations only
      python Leo.py --accuracy            # Print accuracy report
    """
    parser = argparse.ArgumentParser(
        description="LeoBook Prediction System — Unified Orchestrator (v3.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python Leo.py                            Full cycle (loop)
  python Leo.py --prologue                 All prologue pages (P1+P2)
  python Leo.py --prologue --page 1        Prologue P1: Cloud Handshake & Review
  python Leo.py --prologue --page 2        Prologue P2: Accuracy & Sync
  python Leo.py --chapter 1                Full Chapter 1 (Extraction → Odds → Sync)
  python Leo.py --chapter 1 --page 1       Ch1 P1: URL Resolution & Odds Harvesting
  python Leo.py --chapter 1 --page 2       Ch1 P2: Predictions (Rule Engine + RL Ensemble)
  python Leo.py --chapter 1 --page 3       Ch1 P3: Final Sync & Recommendations
  python Leo.py --chapter 2                Full Chapter 2 (Booking & Withdrawal)
  python Leo.py --chapter 2 --page 1       Ch2 P1: Automated Booking
  python Leo.py --chapter 2 --page 2       Ch2 P2: Funds & Withdrawal Check
  python Leo.py --sync                     Force watermark-based cloud sync
  python Leo.py --recommend                Generate and display recommendations only
  python Leo.py --accuracy                 Print accuracy report only
  python Leo.py --search-dict              Rebuild the search dictionary from SQLite
  python Leo.py --review                   Run outcome review process only
  python Leo.py --rule-engine              Show default rule engine info (combine with --list, --set-default, --backtest)
  python Leo.py --rule-engine --list       List all saved rule engines
  python Leo.py --rule-engine --backtest   Progressive backtest default engine
  python Leo.py --rule-engine --backtest --id ENGINE_ID   Backtest a specific engine
  python Leo.py --rule-engine --backtest --from-date 2025-08-01   Set start date
  python Leo.py --rule-engine --set-default "James' Law"   Set engine as default
  python Leo.py --assets                   Sync all team and league assets
  python Leo.py --assets --limit 10         Sync assets with a limit
  python Leo.py --logos                     Download all football team logo packs
  python Leo.py --logos --limit 5           Download first 5 league logo packs
  python Leo.py --enrich-leagues            Extract Flashscore league pages -> SQLite
  python Leo.py --enrich-leagues --limit 5  Extract first 5 unprocessed leagues
  python Leo.py --enrich-leagues --limit 501-1000  Extract leagues 501 through 1000
  python Leo.py --enrich-leagues --reset    Reset and extract all leagues
  python Leo.py --enrich-leagues --seasons 2 Extract last 2 seasons per league
  python Leo.py --enrich-leagues --season 1  Extract only the most recent past season
  python Leo.py --enrich-leagues --all-seasons Extract all available seasons
  python Leo.py --train-rl               Train RL model (current season, default)
  python Leo.py --train-rl --resume      Resume from latest checkpoint (current season)
  python Leo.py --train-rl --train-season all --cold   Full cold retrain, all seasons oldest→newest
  python Leo.py --train-rl --train-season 1            Most recent past season only
  python Leo.py --train-rl --train-season 2            Two seasons ago
  python Leo.py --train-rl --train-season 2024/2025    Explicit season label
  python Leo.py --train-rl --phase 1     Phase 1: Imitation Learning (Warm-Start)
  python Leo.py --train-rl --phase 2     Phase 2: PPO Fine-tuning with KL Penalty
  python Leo.py --train-rl --phase 3     Phase 3: Adapter Fine-tuning (Frozen Trunk)
  python Leo.py --train-rl --cold        Skip Phase 1 (Control Group)
  python Leo.py --train-rl --league ID   Fine-tune a specific league adapter
  python Leo.py --data-quality           Run diagnostics and immediate gap fixes
  python Leo.py --season-completeness    Refresh and print match coverage report
  python Leo.py --diagnose-rl            Inspect RL decisions for next 5 upcoming fixtures
  python Leo.py --diagnose-rl --all-played --top 3   Last 3 completed matches
  python Leo.py --diagnose-rl --fixture FIXTURE_ID   Deep-dive on a specific match
  python Leo.py --diagnose-rl --checkpoint Data/Store/models/checkpoints/phase1_day038.pth
        """
    )
    # --- Granular Chapter / Page Selection ---
    parser.add_argument('--prologue', action='store_true',
                       help='Run all Prologue pages (P1+P2)')
    parser.add_argument('--chapter', type=int, choices=[1, 2, 3], metavar='N',
                       help='Run a specific chapter (1, 2, or 3)')
    parser.add_argument('--page', type=int, choices=[1, 2, 3], metavar='N',
                       help='Run a specific page within --prologue or --chapter')

    # --- Utility Commands ---
    parser.add_argument('--sync', action='store_true',
                       help='Force push-only sync (local → Supabase)')
    parser.add_argument('--pull', action='store_true',
                       help='Pull ALL data from Supabase → local SQLite (bootstrap/recovery)')
    parser.add_argument('--reset-sync', type=str, metavar='TABLE',
                       help='Reset sync watermark for a specific table (e.g. schedules, teams)')
    parser.add_argument('--recommend', action='store_true',
                       help='Generate and display recommendations only')
    parser.add_argument('--accuracy', action='store_true',
                       help='Print accuracy report only')
    parser.add_argument('--search-dict', action='store_true',
                       help='Rebuild the search dictionary from SQLite')
    parser.add_argument('--review', action='store_true',
                       help='Run outcome review process only')
    parser.add_argument('--streamer', action='store_true',
                       help='Run the live score streamer independently')
    parser.add_argument('--assets', action='store_true',
                       help='Sync team and league assets (crests/logos) to Supabase Storage')
    parser.add_argument('--limit', type=str, metavar='N or START-END',
                       help='Limit items processed. Single number (5) or range (501-1000)')
    parser.add_argument('--logos', action='store_true',
                       help='Download football team logo packs from football-logos.cc')
    parser.add_argument('--enrich-leagues', action='store_true',
                       help='Extract Flashscore league pages -> SQLite')
    parser.add_argument('--reset-leagues', action='store_true',
                       help='Reset all leagues to unprocessed (use with --enrich-leagues)')
    parser.add_argument('--seasons', type=int, default=0, metavar='N',
                       help='Number of past seasons to extract (use with --enrich-leagues)')
    parser.add_argument('--season', type=int, default=None, metavar='N',
                       help='Target a specific Nth past season only (e.g., 1 = most recent) (use with --enrich-leagues)')
    parser.add_argument('--all-seasons', action='store_true',
                       help='Extract all available seasons (use with --enrich-leagues)')
    parser.add_argument('--upgrade-crests', action='store_true',
                        help='Upgrade team crests to high-quality logos from Modules/Assets/logos')
    parser.add_argument('--dry-run', action='store_true',
                        help='Perform a dry-run of the pipeline without executing actions')
    parser.add_argument('--bypass-cache', action='store_true',
                        help='Bypass the readiness cache and force fresh data scans')
    parser.add_argument('--data-quality', action='store_true',
                        help='Run DataQualityScanner -> GapResolver IMMEDIATE -> stage STAGE_ENRICHMENT')
    parser.add_argument('--season-completeness', action='store_true',
                        help='Refresh and print season completeness metrics')
    parser.add_argument('--set-expected-matches', type=str, nargs=3, metavar=('LEAGUE_ID', 'SEASON', 'COUNT'),
                        help='Manual override for expected matches in a season')

    # --- RL Training ---
    parser.add_argument('--train-rl', action='store_true',
                        help='Train/retrain the RL model from historical fixtures')
    parser.add_argument('--phase', type=int, choices=[1, 2, 3], default=1,
                        help='Training phase (1: Imitation, 2: PPO+KL, 3: Adapters)')
    parser.add_argument('--cold', action='store_true',
                        help='Skip Phase 1 (cold start control group)')
    parser.add_argument('--league', type=str, metavar='ID',
                        help='Fine-tune a specific league adapter (use with --train-rl)')
    parser.add_argument('--resume', action='store_true',
                        help='Resume training from latest checkpoint (use with --train-rl)')
    # ── Season-aware RL training ──────────────────────────────────────────────
    # Controls which season's fixtures are used as the training window.
    # "current" (default): per-league season start via leagues.current_season join.
    # "all": all available seasons oldest-first. Use with --cold for full retraining.
    # Integer string (e.g. "1"): past season by offset — 1 = most recent past,
    #   2 = two seasons ago, etc. Matches the --season N convention in --enrich-leagues.
    # Explicit label (e.g. "2024/2025" or "2025"): target a specific season directly.
    parser.add_argument('--train-season', dest='train_season', type=str, default='current',
                        metavar='SEASON',
                        help=(
                            'Season scope for RL training. '
                            '"current" (default) = each league\'s live season start. '
                            '"all" = all seasons oldest→newest (use with --cold). '
                            'N (int) = past season offset: 1=most recent past, 2=two seasons ago. '
                            'Label = explicit season string, e.g. "2024/2025" or "2025".'
                        ))

    # --- RL Backtest ---
    parser.add_argument('--backtest-rl', action='store_true',
                        help='Walk-forward RL backtest on historical data')
    parser.add_argument('--bt-train-days', type=int, default=60, metavar='N',
                        help='Rolling train window in days (default: 60)')
    parser.add_argument('--bt-start', type=str, default='2026-01-01', metavar='DATE',
                        help='First eval date YYYY-MM-DD (default: 2026-01-01)')
    parser.add_argument('--bt-end', type=str, default=None, metavar='DATE',
                        help='Last eval date YYYY-MM-DD (default: today)')
    parser.add_argument('--bt-output', type=str, default='Data/Log/backtest_report.txt',
                        metavar='PATH',
                        help='Path to write backtest report (default: Data/Log/backtest_report.txt)')

    # --- Paper Trading ---
    parser.add_argument('--paper-summary', action='store_true',
                         help='Print paper trading log summary')

    # --- RL Diagnostics ---
    parser.add_argument('--diagnose-rl', action='store_true',
                         help='Inspect per-match RL decisions (30-dim action probs, EV, Kelly, Gate)')
    parser.add_argument('--all-played', action='store_true',
                         help='Diagnose recent played matches (use with --diagnose-rl)')
    parser.add_argument('--top', type=int, default=5, metavar='N',
                         help='Number of fixtures to diagnose (use with --diagnose-rl, default: 5)')
    parser.add_argument('--fixture', type=str, metavar='ID',
                         help='Specific fixture_id to diagnose (use with --diagnose-rl)')
    parser.add_argument('--checkpoint', type=str, metavar='PATH',
                         help='Path to .pth checkpoint (use with --diagnose-rl)')

    # --- Model Sync (Supabase Storage) ---
    parser.add_argument('--push-models', action='store_true',
                        help='Upload Data/Store/models/ → Supabase Storage')
    parser.add_argument('--pull-models', action='store_true',
                        help='Download Supabase Storage → Data/Store/models/')
    parser.add_argument('--skip-large', action='store_true',
                        help='Skip syncing files > 50MB (during push)')
    parser.add_argument('--all-checkpoints', action='store_true',
                        help='Force sync all files in checkpoints/ folder (default: False)')

    parser.add_argument('--rule-engine', action='store_true',
                       help='Show default rule engine info (combine with --list, --set-default, --backtest)')
    parser.add_argument('--backtest', action='store_true',
                       help='Run progressive backtest (use with --rule-engine)')
    parser.add_argument('--list', action='store_true',
                       help='List all saved rule engines (use with --rule-engine)')
    parser.add_argument('--set-default', type=str, metavar='NAME',
                       help='Set a rule engine as default by name or ID (use with --rule-engine)')
    parser.add_argument('--id', type=str, metavar='ENGINE_ID',
                       help='Target a specific engine by ID (use with --rule-engine --backtest)')
    parser.add_argument('--from-date', type=str, metavar='DATE',
                       help='Start date for backtest YYYY-MM-DD (use with --rule-engine --backtest)')
    parser.add_argument('--date', type=str, nargs='+', metavar='DATE',
                       help='Specific date(s) to process (DD.MM.YYYY)')

    # --- Validation ---
    args = parser.parse_args()
    if args.page and not args.prologue and args.chapter is None:
        parser.error("--page requires --prologue or --chapter")
    if args.list and not args.rule_engine:
        parser.error("--list requires --rule-engine")
    if args.set_default and not args.rule_engine:
        parser.error("--set-default requires --rule-engine")
    if args.backtest and not args.rule_engine:
        parser.error("--backtest requires --rule-engine")
    if args.league and not args.train_rl:
        parser.error("--league requires --train-rl")
    if args.season is not None and not args.enrich_leagues:
        parser.error("--season requires --enrich-leagues")
    # --train-season is only meaningful with --train-rl, but we allow it to pass
    # silently without --train-rl (it will simply be ignored) to avoid breaking
    # compound invocations. Emit a warning only.
    if args.train_season != 'current' and not args.train_rl:
        print(f"  [Warning] --train-season={args.train_season!r} has no effect without --train-rl")

    # Parse --limit: supports single int ("5") or range ("501-1000")
    args._limit_offset = 0
    args._limit_count = None
    if args.limit:
        if '-' in args.limit and not args.limit.startswith('-'):
            parts = args.limit.split('-')
            if len(parts) == 2:
                try:
                    start = int(parts[0])
                    end = int(parts[1])
                    if start < 1 or end < start:
                        parser.error("--limit range must be START-END where START >= 1 and END >= START")
                    args._limit_offset = start - 1  # Convert 1-indexed to 0-indexed offset
                    args._limit_count = end - start + 1
                except ValueError:
                    parser.error("--limit range must be integers, e.g., 501-1000")
            else:
                parser.error("--limit range format: START-END (e.g., 501-1000)")
        else:
            try:
                args._limit_count = int(args.limit)
            except ValueError:
                parser.error("--limit must be an integer or range (e.g., 5 or 501-1000)")

    return args
