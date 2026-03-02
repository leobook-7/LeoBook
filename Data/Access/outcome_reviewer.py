# outcome_reviewer.py: Post-match results extraction and accuracy reporting.
# Part of LeoBook Data — Access Layer
#
# Functions: _load_schedule_db(), get_predictions_to_review(), smart_parse_datetime(),
#            save_single_outcome(), sync_schedules_to_predictions(),
#            process_review_task_offline(), process_review_task_browser() (+4 more)

"""
Outcome Reviewer Module
Core review processing and outcome analysis system.
All data persisted to leobook.db via league_db.py.
"""

import asyncio
import os
import re
import uuid
import pytz
import pandas as pd
from datetime import datetime as dt, timedelta
from typing import List, Dict, Any, Optional

from playwright.async_api import Playwright
from Core.Intelligence.aigo_suite import AIGOSuite

# --- CONFIGURATION ---
BATCH_SIZE = 10
LOOKBACK_LIMIT = 5000
ENRICHMENT_CONCURRENCY = 10
PRODUCTION_MODE = True
MAX_RETRIES = 3
HEALTH_CHECK_INTERVAL = 300
ERROR_THRESHOLD = 10
VERSION = "2.6.0"
COMPATIBLE_MODELS = ["2.5", "2.6"]

# --- IMPORTS ---
from .db_helpers import (
    save_team_entry, save_region_league_entry,
    evaluate_market_outcome, log_audit_event,
    get_all_schedules, update_prediction_status, _get_conn,
)
from Data.Access.league_db import (
    query_all, upsert_prediction, update_prediction,
    upsert_fb_match, upsert_accuracy_report,
)
from .sync_manager import SyncManager
from Core.Intelligence.selector_manager import SelectorManager
from Core.Intelligence.selector_db import log_selector_failure
from Core.Utils.constants import NAVIGATION_TIMEOUT


def _load_schedule_db() -> Dict[str, Dict]:
    """Loads fixtures from SQLite into a dict for quick lookups."""
    conn = _get_conn()
    rows = query_all(conn, 'fixtures')
    return {r['fixture_id']: r for r in rows if r.get('fixture_id')}


def get_predictions_to_review() -> List[Dict]:
    """
    Reads predictions from SQLite and returns matches that are in the past
    (Africa/Lagos timezone) and still have a 'pending' status.
    """
    conn = _get_conn()
    rows = query_all(conn, 'predictions', "status = 'pending'")

    if not rows:
        return []

    # Convert to DataFrame for date filtering
    df = pd.DataFrame(rows).fillna('')

    def parse_dt_row(row):
        try:
            d_str = row.get('date') or row.get('Date')
            t_str = row.get('match_time')
            if not d_str or not t_str or t_str == 'N/A':
                return pd.NaT
            return dt.strptime(f"{d_str} {t_str}", "%d.%m.%Y %H:%M")
        except Exception:
            return pd.NaT

    df['scheduled_dt'] = df.apply(parse_dt_row, axis=1)
    df = df.dropna(subset=['scheduled_dt'])

    lagos_tz = pytz.timezone('Africa/Lagos')
    now_lagos = dt.now(lagos_tz)
    df['scheduled_dt'] = df['scheduled_dt'].apply(
        lambda x: lagos_tz.localize(x) if x.tzinfo is None else x
    )

    completion_cutoff = now_lagos - timedelta(hours=2, minutes=30)
    to_review_df = df[df['scheduled_dt'] < completion_cutoff]

    skipped = len(df[df['scheduled_dt'] < now_lagos]) - len(to_review_df)
    if skipped > 0:
        print(f"   [Filter] Skipped {skipped} matches still possibly in progress (<2.5h old).")

    if len(to_review_df) > LOOKBACK_LIMIT:
        to_review_df = to_review_df.tail(LOOKBACK_LIMIT)

    return to_review_df.to_dict('records')


def smart_parse_datetime(dt_str: str):
    """Attempts to parse date/time in various formats."""
    dt_str = dt_str.strip()
    if len(dt_str) > 10 and not dt_str[0].isdigit():
        dt_str = " ".join(dt_str.split()[1:])

    if len(dt_str) == 15 and dt_str[10].isdigit():
        dt_str = dt_str[:10] + " " + dt_str[10:]

    try:
        parts = dt_str.split()
        if len(parts) == 2:
            d_part, t_part = parts
            return d_part, t_part
    except Exception:
        pass
    return None, None


def save_single_outcome(match_data: Dict, new_status: str):
    """Atomic update of a prediction outcome in SQLite."""
    conn = _get_conn()
    row_id_key = 'ID' if 'ID' in match_data else 'fixture_id'
    target_id = match_data.get(row_id_key)

    if not target_id:
        return

    try:
        updates = {
            'status': new_status,
            'actual_score': match_data.get('actual_score', ''),
            'last_updated': dt.now().isoformat(),
        }

        if 'home_score' in match_data and 'away_score' in match_data:
            updates['actual_score'] = f"{match_data['home_score']}-{match_data['away_score']}"
            updates['home_score'] = match_data['home_score']
            updates['away_score'] = match_data['away_score']

        if new_status in ['reviewed', 'finished']:
            # Look up the prediction to evaluate outcome
            row = conn.execute(
                "SELECT prediction, home_team, away_team FROM predictions WHERE fixture_id = ?",
                (target_id,)
            ).fetchone()

            if row:
                prediction = row['prediction']
                home_team = row['home_team']
                away_team = row['away_team']
                actual_score = updates.get('actual_score', '')

                score_match = re.match(r'(\d+)\s*-\s*(\d+)', actual_score or '')
                if score_match:
                    h_core, a_core = score_match.group(1), score_match.group(2)
                    res = evaluate_market_outcome(prediction, h_core, a_core, home_team, away_team)
                    updates['outcome_correct'] = res if res else '0'

                    # Immediate cloud sync
                    print(f"      [Cloud] Immediate sync for {target_id}...")
                    full_row = dict(conn.execute(
                        "SELECT * FROM predictions WHERE fixture_id = ?", (target_id,)
                    ).fetchone())
                    full_row.update(updates)
                    asyncio.create_task(SyncManager().batch_upsert('predictions', [full_row]))
                else:
                    print(f"      [Eval Skip] Cannot parse score '{actual_score}' for {target_id}")

        update_prediction(conn, target_id, updates)

        if new_status == 'reviewed' and target_id:
            _sync_outcome_to_site_registry(target_id, match_data)

    except Exception as e:
        print(f"    [Health] save_error (high): Failed to save outcome: {e}")


def sync_schedules_to_predictions():
    """Ensures all entries in fixtures exist in predictions."""
    conn = _get_conn()
    schedules = query_all(conn, 'fixtures')
    pred_ids = {r['fixture_id'] for r in query_all(conn, 'predictions') if r.get('fixture_id')}

    added_count = 0
    for s in schedules:
        fid = s.get('fixture_id')
        if fid and fid not in pred_ids:
            new_pred = {
                'fixture_id': fid,
                'date': s.get('date'),
                'match_time': s.get('time', s.get('match_time')),
                'region_league': s.get('region_league'),
                'home_team': s.get('home_team_name', s.get('home_team')),
                'away_team': s.get('away_team_name', s.get('away_team')),
                'home_team_id': s.get('home_team_id'),
                'away_team_id': s.get('away_team_id'),
                'prediction': 'PENDING',
                'confidence': 'Low',
                'status': s.get('match_status', 'pending'),
                'match_link': s.get('match_link', s.get('url')),
                'actual_score': f"{s.get('home_score', '')}-{s.get('away_score', '')}" if s.get('home_score') else 'N/A',
            }
            upsert_prediction(conn, new_pred)
            added_count += 1

    if added_count > 0:
        print(f"  [Sync] Added {added_count} missing entries from schedules to predictions.")


def _sync_outcome_to_site_registry(fixture_id: str, match_data: Dict):
    """Updates fb_matches when a prediction is reviewed."""
    conn = _get_conn()
    try:
        actual_score = match_data.get('actual_score', '')
        prediction = match_data.get('prediction', '')
        home_team = match_data.get('home_team', '')
        away_team = match_data.get('away_team', '')

        res = evaluate_market_outcome(prediction, actual_score, "", home_team, away_team)
        if not res:
            return

        outcome_status = "WON" if res == '1' else "LOST"

        updated = conn.execute(
            "UPDATE fb_matches SET status = ?, last_updated = ? WHERE fixture_id = ?",
            (outcome_status, dt.now().isoformat(), str(fixture_id))
        ).rowcount
        conn.commit()

        if updated > 0:
            print(f"    [Sync] Updated {updated} records in fb_matches to {outcome_status}")

    except Exception as e:
        print(f"    [Sync Error] Failed to sync outcome: {e}")


def process_review_task_offline(match: Dict) -> Optional[Dict]:
    """Review a prediction by reading its result from fixtures (no browser)."""
    schedule_db = _load_schedule_db()
    fixture_id = match.get('fixture_id')
    schedule = schedule_db.get(fixture_id, {})

    match_status = str(schedule.get('match_status', '')).upper()
    home_score = str(schedule.get('home_score', '')).strip()
    away_score = str(schedule.get('away_score', '')).strip()

    has_valid_scores = home_score.isdigit() and away_score.isdigit()

    if match_status in ('FINISHED', 'AET', 'PEN') and has_valid_scores:
        match['home_score'] = home_score
        match['away_score'] = away_score
        match['actual_score'] = f"{home_score}-{away_score}"
        save_single_outcome(match, 'finished')
        print(f"    [Result] {match.get('home_team')} {match['actual_score']} {match.get('away_team')}")
        return match
    elif match_status == 'POSTPONED':
        save_single_outcome(match, 'match_postponed')
        return None
    elif match_status == 'CANCELED':
        save_single_outcome(match, 'canceled')
        return None
    return None


async def process_review_task_browser(page, match: Dict) -> Optional[Dict]:
    """Review a prediction by visiting the match page (Browser fallback)."""
    match_link = match.get('match_link')
    if not match_link:
        return None

    try:
        print(f"      [Fallback] Visiting {match.get('home_team')} vs {match.get('away_team')}...")
        await page.goto(match_link, timeout=NAVIGATION_TIMEOUT)
        await page.wait_for_load_state("networkidle")

        final_score = await get_final_score(page)
        if final_score and '-' in final_score:
            match['actual_score'] = final_score
            h_score, a_score = final_score.split('-')
            match['home_score'] = h_score
            match['away_score'] = a_score
            save_single_outcome(match, 'finished')
            print(f"    [Result-B] {match.get('home_team')} {final_score} {match.get('away_team')}")
            return match
        elif final_score == "Match_POSTPONED":
            save_single_outcome(match, 'match_postponed')
        elif final_score == "ARCHIVED":
            print(f"      [!] Match {match.get('fixture_id')} appears deleted or archived. Flagging.")
            save_single_outcome(match, 'manual_review_needed')
    except Exception as e:
        print(f"      [Fallback Error] {e}")

    return None


async def get_league_url(page):
    """Extracts the league URL from the match page."""
    try:
        league_link_sel = "a[href*='/football/'][href$='/']"
        league_link = page.locator(league_link_sel).first
        LEAGUE_TIMEOUT = 10000
        href = await league_link.get_attribute('href', timeout=LEAGUE_TIMEOUT)
        if href:
            return href
    except Exception:
        pass
    return ""


async def get_final_score(page):
    """Extracts the final score. Returns 'Error' if not found."""
    try:
        status_selector = SelectorManager.get_selector("fs_match_page", "meta_match_status") or "div.fixedHeaderDuel__detailStatus"
        try:
            status_text = await page.locator(status_selector).first.inner_text(timeout=30000)
            ERROR_PAGE_SEL = "div.errorMessage"
            if await page.locator(ERROR_PAGE_SEL).is_visible():
                return "ARCHIVED"

            error_header = page.get_by_text("Error:", exact=True)
            error_message = page.get_by_text("The requested page can't be displayed. Please try again later.")

            if "postponed" in status_text.lower():
                return "Match_POSTPONED"

            if (await error_header.is_visible()) and (await error_message.is_visible()):
                return "ARCHIVED"

        except Exception:
            status_text = "finished"

        if "finished" not in status_text.lower() and "aet" not in status_text.lower() and "pen" not in status_text.lower() and "fro" not in status_text.lower():
            return "NOT_FINISHED"

        # Tier 1: data-testid + class selectors
        try:
            home_score_t = await page.locator('.detailScore__home, [data-testid="wcl-matchRowScore"][data-side="1"]').first.inner_text(timeout=2000)
            away_score_t = await page.locator('.detailScore__away, [data-testid="wcl-matchRowScore"][data-side="2"]').first.inner_text(timeout=2000)
            tier1_score = f"{home_score_t.strip()}-{away_score_t.strip()}"
            if tier1_score.replace('-', '').isdigit():
                return tier1_score
        except Exception:
            pass

        # Tier 2: Legacy CSS selectors
        home_score_sel = SelectorManager.get_selector("fs_match_page", "header_score_home") or "div.detailScore__wrapper > span:nth-child(1)"
        away_score_sel = SelectorManager.get_selector("fs_match_page", "header_score_away") or "div.detailScore__wrapper > span:nth-child(3)"
        try:
            home_score = await page.locator(home_score_sel).first.inner_text(timeout=3000)
            away_score = await page.locator(away_score_sel).first.inner_text(timeout=3000)
            final_score = f"{home_score.strip() if home_score else ''}-{away_score.strip() if away_score else ''}"
            if '-' in final_score and final_score.replace('-', '').isdigit():
                return final_score
        except Exception as sel_fail:
            failed_key = "header_score_away" if "nth-child(3)" in str(sel_fail) or "away" in str(sel_fail).lower() else "header_score_home"
            log_selector_failure("fs_match_page", failed_key, str(sel_fail))

        # Tier 3: JS heuristic
        try:
            heuristic_score = await page.evaluate("""() => {
                const home = document.querySelector('.detailScore__home, [data-testid="wcl-matchRowScore"][data-side="1"]');
                const away = document.querySelector('.detailScore__away, [data-testid="wcl-matchRowScore"][data-side="2"]');
                if (home && away) return home.innerText.trim() + '-' + away.innerText.trim();
                const spans = Array.from(document.querySelectorAll('span, div'));
                const scorePattern = /^(\\d+)\\s*-\\s*(\\d+)$/;
                for (const s of spans) {
                    if (scorePattern.test(s.innerText.trim())) return s.innerText.trim();
                }
                return null;
            }""")
            if heuristic_score:
                print(f"      [AIGO HEALED] Extracted score via heuristics: {heuristic_score}")
                return heuristic_score
        except Exception:
            pass

        return "Error"

    except Exception as e:
        print(f"    [Health] score_extraction_error (medium): Failed to extract score: {e}")
        return "Error"


def update_region_league_url(region_league: str, url: str):
    """Updates the url for a region_league."""
    if not region_league or not url or " - " not in region_league:
        return

    if url.startswith('/'):
        url = f"https://www.flashscore.com{url}"

    region, league_name = region_league.split(" - ", 1)

    save_region_league_entry({
        'league_id': f"{region}_{league_name}".replace(' ', '_').replace('-', '_').upper(),
        'region': region.strip(),
        'league': league_name.strip(),
        'league_url': url,
    })


@AIGOSuite.aigo_retry(max_retries=2, delay=5.0)
async def run_review_process(p: Optional[Playwright] = None):
    """Orchestrates the outcome review process."""
    print("\n   [Prologue] Starting Prediction Review Engine...")
    try:
        to_review = get_predictions_to_review()
        if not to_review:
            print("   [Info] No pending predictions found for review.")
            return

        print(f"   [Info] Processing {len(to_review)} predictions for outcome review...")
        to_review = to_review[:LOOKBACK_LIMIT]

        processed_matches = []
        needs_browser = []

        for m in to_review:
            result = process_review_task_offline(m)
            if result:
                processed_matches.append(result)
            else:
                needs_browser.append(m)

        if needs_browser and p:
            now = dt.now()
            eligible = []
            for m in needs_browser:
                try:
                    d_str = m.get('date', '')
                    t_str = m.get('match_time', '') or m.get('time', '')
                    if '.' in d_str:
                        parts = d_str.split('.')
                        d_str = f"{parts[2]}-{parts[1]}-{parts[0]}"
                    ko = dt.strptime(f"{d_str} {t_str}", "%Y-%m-%d %H:%M")
                    if now - ko >= timedelta(hours=2):
                        eligible.append(m)
                except Exception:
                    eligible.append(m)

            skipped = len(needs_browser) - len(eligible)
            if skipped:
                print(f"   [Info] Skipped {skipped} future/in-progress matches from browser fallback.")

            if eligible:
                print(f"   [Info] Triggering Browser Fallback for {len(eligible)} unresolved reviews...")
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()

                for m in eligible:
                    result = await process_review_task_browser(page, m)
                    if result:
                        processed_matches.append(result)

                await browser.close()

        if processed_matches:
            print(f"\n   [SUCCESS] Reviewed {len(processed_matches)} match outcomes.")
            try:
                from Core.Intelligence.learning_engine import LearningEngine
                updated_weights = LearningEngine.update_weights()
                print(f"   [Learning] Updated weights for {len(updated_weights)-1} leagues.")
            except Exception as e:
                print(f"   [Learning] Weight update skipped: {e}")
        else:
            print("\n   [Info] All predictions still pending.")

    except Exception as e:
        print(f"   [CRITICAL] Outcome review failed: {e}")


async def run_accuracy_generation():
    """Aggregates performance metrics from predictions for the last 24h."""
    conn = _get_conn()

    print("\n   [ACCURACY] Generating performance metrics (Last 24h)...")
    try:
        rows = query_all(conn, 'predictions')
        if not rows:
            print("   [ACCURACY] No predictions found.")
            return

        df = pd.DataFrame(rows).fillna('')
        if df.empty:
            return

        lagos_tz = pytz.timezone('Africa/Lagos')
        now_lagos = dt.now(lagos_tz)
        yesterday_lagos = now_lagos - timedelta(days=1)

        def parse_updated(ts):
            try:
                dt_obj = pd.to_datetime(ts)
                if dt_obj.tzinfo is None:
                    return lagos_tz.localize(dt_obj)
                return dt_obj.astimezone(lagos_tz)
            except Exception:
                return pd.NaT

        df['updated_dt'] = df['last_updated'].apply(parse_updated)
        df_24h = df[(df['updated_dt'] >= yesterday_lagos) & (df['status'].isin(['reviewed', 'finished']))].copy()

        if df_24h.empty:
            print("   [ACCURACY] No predictions reviewed in the last 24h.")
            return

        volume = len(df_24h)
        correct_count = (df_24h['outcome_correct'] == '1').sum()
        win_rate = (correct_count / volume) * 100 if volume > 0 else 0

        total_return = 0
        for _, row in df_24h.iterrows():
            try:
                odds = float(row.get('odds', 0))
                if odds <= 0:
                    odds = 2.0
                if row['outcome_correct'] == '1':
                    total_return += (odds - 1)
                else:
                    total_return -= 1
            except Exception:
                pass

        return_pct = (total_return / volume) * 100 if volume > 0 else 0

        report_row = {
            'report_id': str(uuid.uuid4())[:8],
            'timestamp': now_lagos.isoformat(),
            'volume': volume,
            'win_rate': round(win_rate, 2),
            'return_pct': round(return_pct, 2),
            'period': 'last_24h',
        }

        upsert_accuracy_report(conn, report_row)
        log_audit_event('ACCURACY_REPORT', f"Metrics: Vol={volume}, WR={win_rate:.1f}%, ROI={return_pct:.1f}%")

        sync = SyncManager()
        if sync.supabase:
            await sync.batch_upsert('accuracy_reports', [report_row])

    except Exception as e:
        print(f"   [ACCURACY ERROR] {e}")


async def start_review():
    """Legacy entry point."""
    await run_review_process()
