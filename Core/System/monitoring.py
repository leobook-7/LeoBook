# monitoring.py: monitoring.py: Chapter 3 - Chief Engineer Oversight System
# Part of LeoBook Core — System
#
# Functions: run_chapter_3_oversight(), perform_health_check(), _count_predictions_for_date(), _get_bet_success_rate(), generate_oversight_report()

import os
from datetime import datetime as dt
from pathlib import Path
from Core.System.lifecycle import state
from Data.Access.db_helpers import log_audit_event, _get_conn
from Data.Access.league_db import query_all

async def run_chapter_3_oversight():
    """
    Chapter 3: Chief Engineer Monitoring.
    Runs health checks, generates report, logs to Supabase.
    """
    print("\n   [Chapter 3] Chief Engineer performing oversight...")
    
    health_status = perform_health_check()
    report = generate_oversight_report(health_status)

    # Print report to console
    print(f"\n{report}")

    # Persist report to Supabase via audit log
    log_audit_event("OVERSIGHT_REPORT", report, status="success")

    return health_status

def perform_health_check():
    """Checks various system components for issues."""
    issues = []
    
    # 1. Check Data Store integrity
    store_path = Path("Data/Store")
    if not store_path.exists():
        issues.append("❌ Data store directory missing.")
    else:
        db_path = store_path / "leobook.db"
        if db_path.exists():
            import time
            mtime = os.path.getmtime(db_path)
            if (time.time() - mtime) > 86400:
                issues.append("Warning: leobook.db hasn't been updated in 24h.")
        else:
            issues.append("Critical: leobook.db missing.")

    # 2. Check Error Log
    error_count = len(state.get("error_log", []))
    if error_count > 0:
        issues.append(f"⚠️ {error_count} errors logged this cycle.")

    # 3. Check Balance Stagnation
    if state.get("current_balance", 0) <= 0:
         issues.append("⚠️ Account balance is zero or unknown.")

    # 4. Prediction Volume (new v2.8)
    today_str = dt.now().strftime("%Y-%m-%d")
    today_preds = _count_predictions_for_date(today_str)
    if today_preds < 5:
        issues.append(f"⚠️ Low prediction volume today: {today_preds} (expected ≥5).")

    # 5. Bet Success Rate (new v2.8)
    success_rate = _get_bet_success_rate()
    if success_rate is not None and success_rate < 50.0:
        issues.append(f"⚠️ Bet placement success rate is low: {success_rate:.0f}%.")

    return issues if issues else ["✅ System is healthy and operational."]

def _count_predictions_for_date(date_str: str) -> int:
    """Count predictions for a given date from SQLite."""
    try:
        conn = _get_conn()
        rows = query_all(conn, 'predictions')
        return sum(1 for r in rows if str(r.get('date', '')).startswith(date_str))
    except Exception:
        return 0

def _get_bet_success_rate() -> float | None:
    """Calculate today's bet placement success rate from audit_log table."""
    try:
        conn = _get_conn()
        today_str = dt.now().strftime("%Y-%m-%d")
        rows = query_all(conn, 'audit_log', f"event_type = 'BET_PLACEMENT' AND timestamp LIKE '{today_str}%'")
        if not rows:
            return None
        total = len(rows)
        successful = sum(1 for r in rows if str(r.get('status', '')).lower() == 'success')
        return (successful / total) * 100 if total > 0 else None
    except Exception:
        return None

def generate_oversight_report(health_status):
    """Formats the oversight findings into a readable string."""
    status_summary = "\n".join(health_status)
    
    report = (
        f"═══ Chief Engineer Oversight Report ═══\n"
        f"Cycle Count: #{state.get('cycle_count', 0)}\n"
        f"Uptime: {dt.now() - state.get('cycle_start_time', dt.now())}\n"
        f"Current Balance: ₦{state.get('current_balance', 0):,.2f}\n"
        f"Booked: {state.get('booked_this_cycle', 0)}\n"
        f"Failed: {state.get('failed_this_cycle', 0)}\n\n"
        f"Health Check:\n{status_summary}"
    )
    return report
