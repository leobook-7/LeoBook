# fs_live_streamer.py: Continuous live score streaming from Flashscore ALL tab.
# Part of LeoBook Modules — Flashscore
#
# Functions: _propagate_status_updates(), _purge_stale_live_scores(),
#            _review_pending_backlog(), live_score_streamer()

"""
Live Score Streamer v3
Scrapes the Flashscore ALL tab every 60 seconds using its own browser context.
Extracts live, finished, postponed, cancelled, and FRO match statuses.
Saves results to SQLite and upserts to Supabase.
"""

import asyncio
import os
import re
from datetime import datetime as dt, timedelta
from playwright.async_api import Playwright

from Data.Access.db_helpers import (
    save_live_score_entry, log_audit_event, evaluate_market_outcome,
    transform_streamer_match_to_schedule, save_schedule_entry, _get_conn,
)
from Data.Access.league_db import query_all, update_prediction, upsert_fixture
from Data.Access.sync_manager import SyncManager
from Core.Browser.site_helpers import fs_universal_popup_dismissal
from Core.Utils.constants import NAVIGATION_TIMEOUT, WAIT_FOR_LOAD_STATE_TIMEOUT
from Core.Intelligence.selector_manager import SelectorManager
from Core.Intelligence.aigo_suite import AIGOSuite
from Modules.Flashscore.fs_extractor import extract_all_matches, expand_all_leagues as ensure_content_expanded

STREAM_INTERVAL = 60
FLASHSCORE_URL = "https://www.flashscore.com/football/"
_STREAMER_HEARTBEAT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Data', 'Store', '.streamer_heartbeat'
)
_last_push_sig = None
_missed_cycles = {}

EXPAND_DROPDOWN_JS = """
(selector) => {
    const btn = document.querySelector(selector);
    if (btn) { btn.click(); return true; }
    return false;
}
"""


def _is_streamer_alive() -> bool:
    try:
        if os.path.exists(_STREAMER_HEARTBEAT_FILE):
            mtime = dt.fromtimestamp(os.path.getmtime(_STREAMER_HEARTBEAT_FILE))
            return (dt.now() - mtime) < timedelta(minutes=30)
    except Exception:
        pass
    return False


def _touch_heartbeat():
    try:
        os.makedirs(os.path.dirname(_STREAMER_HEARTBEAT_FILE), exist_ok=True)
        with open(_STREAMER_HEARTBEAT_FILE, 'w') as f:
            f.write(dt.now().isoformat())
    except Exception:
        pass


def _parse_match_start(date_val, time_val):
    if not date_val or not time_val:
        return None
    m = re.match(r'^(\d{2})\.(\d{2})\.(\d{4})$', str(date_val))
    if m:
        date_val = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    try:
        return dt.fromisoformat(f"{date_val}T{time_val}:00")
    except Exception:
        return None


def _propagate_status_updates(live_matches, resolved_matches, force_finished_ids=None):
    """Propagate live scores and resolved results into fixtures and predictions tables."""
    conn = _get_conn()
    resolved_matches = resolved_matches or []
    force_finished_ids = force_finished_ids or set()
    live_ids = {m['fixture_id'] for m in live_matches}
    live_map = {m['fixture_id']: m for m in live_matches}
    resolved_ids = {m['fixture_id'] for m in resolved_matches}
    resolved_map = {m['fixture_id']: m for m in resolved_matches}
    now = dt.now()
    now_iso = now.isoformat()

    NO_SCORE_STATUSES = {'cancelled', 'postponed', 'fro', 'abandoned'}

    # --- Update fixtures (schedules) ---
    sched_rows = query_all(conn, 'fixtures')
    sched_updates = []
    existing_sched_ids = set()

    for row in sched_rows:
        fid = row.get('fixture_id', '')
        existing_sched_ids.add(fid)
        updates = {}

        if fid in live_ids:
            lm = live_map[fid]
            if str(row.get('match_status', '')).lower() != 'live':
                updates['match_status'] = 'live'
            if lm.get('home_score') and str(lm['home_score']) != str(row.get('home_score')):
                updates['home_score'] = lm['home_score']
                updates['away_score'] = lm['away_score']

        elif fid in resolved_ids:
            rm = resolved_map[fid]
            terminal_status = rm.get('status', 'finished')
            if str(row.get('match_status', '')).lower() != terminal_status:
                updates['match_status'] = terminal_status
                if terminal_status in NO_SCORE_STATUSES:
                    updates['home_score'] = ''
                    updates['away_score'] = ''
                else:
                    updates['home_score'] = rm.get('home_score', row.get('home_score', ''))
                    updates['away_score'] = rm.get('away_score', row.get('away_score', ''))

        # Safety: 2.5hr rule
        if str(row.get('match_status', '')).lower() == 'live':
            match_start = _parse_match_start(row.get('date', ''), row.get('time', ''))
            if match_start and now > match_start + timedelta(minutes=150):
                updates['match_status'] = 'finished'
                if fid in live_ids:
                    live_ids.discard(fid)
                    live_matches = [m for m in live_matches if m['fixture_id'] != fid]

        if updates:
            updates['last_updated'] = now_iso
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            vals = list(updates.values()) + [fid]
            conn.execute(f"UPDATE fixtures SET {set_clause} WHERE fixture_id = ?", vals)
            row.update(updates)
            sched_updates.append(dict(row))

    # Add missing matches to fixtures
    new_sched_entries = []
    for m in live_matches + resolved_matches:
        fid = m.get('fixture_id')
        if fid and fid not in existing_sched_ids:
            new_entry = transform_streamer_match_to_schedule(m)
            save_schedule_entry(new_entry)
            new_sched_entries.append(new_entry)
            sched_updates.append(new_entry)

    if new_sched_entries:
        print(f"   [Streamer] Discovery: Found {len(new_sched_entries)} new matches. Adding them.")

    conn.commit()

    # --- Update predictions ---
    pred_rows = query_all(conn, 'predictions')
    pred_updates = []

    for row in pred_rows:
        fid = row.get('fixture_id', '')
        cur_status = str(row.get('status', '')).lower()
        updates = {}

        if fid in live_ids:
            lm = live_map[fid]
            if cur_status != 'live':
                updates['status'] = 'live'
            h_score = lm.get('home_score')
            a_score = lm.get('away_score')
            if h_score is not None and str(h_score) != str(row.get('home_score')):
                updates['home_score'] = h_score
            if a_score is not None and str(a_score) != str(row.get('away_score')):
                updates['away_score'] = a_score

        elif fid in resolved_ids or fid in force_finished_ids:
            terminal_status = resolved_map[fid].get('status', 'finished') if fid in resolved_ids else 'finished'
            if cur_status != terminal_status:
                updates['status'] = terminal_status
                if fid in resolved_ids:
                    rm = resolved_map[fid]
                    if rm.get('home_score') is not None:
                        updates['home_score'] = rm['home_score']
                    if rm.get('away_score') is not None:
                        updates['away_score'] = rm['away_score']
                    updates['actual_score'] = f"{rm.get('home_score', '')}-{rm.get('away_score', '')}"
                else:
                    updates['actual_score'] = f"{row.get('home_score', '')}-{row.get('away_score', '')}"

                if terminal_status not in NO_SCORE_STATUSES:
                    oc = evaluate_market_outcome(
                        row.get('prediction', ''),
                        str(updates.get('home_score', row.get('home_score', ''))),
                        str(updates.get('away_score', row.get('away_score', ''))),
                        row.get('home_team', ''),
                        row.get('away_team', ''),
                    )
                    if oc:
                        updates['outcome_correct'] = oc

        # Safety: 2.5hr rule for predictions
        if cur_status == 'live':
            match_start = _parse_match_start(row.get('date', ''), row.get('match_time', ''))
            if match_start and now > match_start + timedelta(minutes=150):
                updates['status'] = 'finished'
                oc = evaluate_market_outcome(
                    row.get('prediction', ''),
                    str(row.get('home_score', '')),
                    str(row.get('away_score', '')),
                    row.get('home_team', ''),
                    row.get('away_team', ''),
                )
                if oc:
                    updates['outcome_correct'] = oc

        if updates:
            update_prediction(conn, fid, updates)
            row.update(updates)
            pred_updates.append(dict(row))

    return sched_updates, pred_updates


def _review_pending_backlog():
    """Scan predictions for 'pending' entries and resolve using finished fixtures."""
    conn = _get_conn()
    preds = query_all(conn, 'predictions', "status = 'pending'")
    if not preds:
        return []

    scheds = {r['fixture_id']: r for r in query_all(conn, 'fixtures') if r.get('fixture_id')}
    updates_list = []

    for p in preds:
        fid = p.get('fixture_id')
        if fid in scheds:
            s = scheds[fid]
            s_status = str(s.get('match_status', '')).lower()
            h_score = str(s.get('home_score', '')).strip()
            a_score = str(s.get('away_score', '')).strip()

            if s_status in ('finished', 'aet', 'pen') and h_score.isdigit() and a_score.isdigit():
                upd = {
                    'status': 'finished',
                    'home_score': h_score,
                    'away_score': a_score,
                    'actual_score': f"{h_score}-{a_score}",
                }
                oc = evaluate_market_outcome(
                    p.get('prediction', ''), h_score, a_score,
                    p.get('home_team', ''), p.get('away_team', ''),
                )
                if oc:
                    upd['outcome_correct'] = oc

                update_prediction(conn, fid, upd)
                p.update(upd)
                updates_list.append(dict(p))
                print(f"   [Streamer-Review] Resolved: {p.get('home_team')} vs {p.get('away_team')} -> {upd['actual_score']}")

    if updates_list:
        print(f"   [Streamer-Review] Resolved {len(updates_list)} pending backlog predictions.")

    return updates_list


def _purge_stale_live_scores(current_live_ids: set, resolved_ids: set):
    """Remove fixtures from live_scores that are no longer live."""
    global _missed_cycles
    conn = _get_conn()
    existing_rows = query_all(conn, 'live_scores')
    if not existing_rows:
        return set(), set()

    existing_ids = {r.get('fixture_id', '') for r in existing_rows}
    stale_potential = existing_ids - (current_live_ids | resolved_ids)

    for fid in (current_live_ids | resolved_ids):
        _missed_cycles[fid] = 0
    for fid in stale_potential:
        _missed_cycles[fid] = _missed_cycles.get(fid, 0) + 1

    purged_for_misses = {fid for fid, count in _missed_cycles.items() if count >= 3 and fid in existing_ids}
    purged_for_resolution = existing_ids & resolved_ids
    final_stale_ids = purged_for_misses | purged_for_resolution

    if final_stale_ids:
        placeholders = ",".join(["?"] * len(final_stale_ids))
        conn.execute(f"DELETE FROM live_scores WHERE fixture_id IN ({placeholders})", list(final_stale_ids))
        conn.commit()
        for fid in final_stale_ids:
            _missed_cycles.pop(fid, None)

    return final_stale_ids, purged_for_misses


async def _click_all_tab(page) -> bool:
    try:
        all_tab_sel = await SelectorManager.get_selector_auto(page, "fs_home_page", "all_tab")
        if not all_tab_sel:
            return True
        tab = page.locator(all_tab_sel)
        if not await tab.is_visible(timeout=3000):
            return True
        cls = await tab.get_attribute("class") or ""
        if "selected" in cls:
            return True
        print(f"   [Streamer] ALL tab not selected, clicking...")
        await page.click(all_tab_sel, force=True, timeout=3000)
        await asyncio.sleep(0.5)
        return True
    except Exception as e:
        print(f"   [Streamer] Error verifying ALL tab: {e}")
    return False


@AIGOSuite.aigo_retry(max_retries=2, delay=30.0, use_aigo=False)
async def live_score_streamer(playwright: Playwright, user_data_dir: str = None):
    """
    Main streaming loop v3.2 (Mobile Optimized).
    - Headless browser with iPhone 12 emulation.
    - 60s extraction interval.
    - SQLite persistence + Supabase sync.
    - Recycles browser every 3 cycles.
    """
    print(f"\n   [Streamer] Mobile Live Score Streamer v3.2 starting (Headless, 60s, isolation={'ON' if user_data_dir else 'OFF'})...")
    log_audit_event("STREAMER_START", f"Mobile live score streamer v3.2 initialized (Isolation: {bool(user_data_dir)}).")

    global _last_push_sig
    RECYCLE_INTERVAL = 3
    cycle = 0
    sync = SyncManager()

    while True:
        browser = None
        context = None
        try:
            print(f"   [Streamer] Starting fresh browser session (Cycle {cycle + 1})...")
            iphone_12 = {k: v for k, v in playwright.devices['iPhone 12'].items()
                         if k != 'default_browser_type'}

            if user_data_dir:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir, headless=True,
                    args=["--disable-dev-shm-usage", "--no-sandbox"],
                    **iphone_12, timezone_id="Africa/Lagos",
                )
                page = context.pages[0] if context.pages else await context.new_page()
            else:
                browser = await playwright.chromium.launch(
                    headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"],
                )
                context = await browser.new_context(**iphone_12, timezone_id="Africa/Lagos")
                page = await context.new_page()

            print("   [Streamer] Navigating to Flashscore (Mobile view, up to 3 mins)...")
            await page.goto(FLASHSCORE_URL, timeout=NAVIGATION_TIMEOUT, wait_until="domcontentloaded")

            try:
                sport_sel = SelectorManager.get_selector_strict("fs_home_page", "sport_container")
                await page.wait_for_selector(sport_sel, timeout=60000)
            except Exception:
                print("   [Streamer] Warning: sportName container not found, proceeding anyway...")

            await asyncio.sleep(2)
            await fs_universal_popup_dismissal(page, "fs_home_page")
            await _click_all_tab(page)
            await ensure_content_expanded(page)

            session_cycle = 0
            while session_cycle < RECYCLE_INTERVAL:
                cycle += 1
                session_cycle += 1
                _touch_heartbeat()
                now_ts = dt.now().strftime("%H:%M:%S")

                try:
                    all_matches = await extract_all_matches(page, label="Streamer")

                    LIVE_STATUSES = {'live', 'halftime', 'break', 'penalties', 'extra_time'}
                    RESOLVED_STATUSES = {'finished', 'cancelled', 'postponed', 'fro', 'abandoned'}

                    live_matches = [m for m in all_matches if m.get('status') in LIVE_STATUSES]
                    resolved_matches = [m for m in all_matches if m.get('status') in RESOLVED_STATUSES]
                    current_live_ids = {m['fixture_id'] for m in live_matches}
                    current_resolved_ids = {m['fixture_id'] for m in resolved_matches}

                    final_stale_ids, force_finished_ids = _purge_stale_live_scores(current_live_ids, current_resolved_ids)
                    if final_stale_ids:
                        print(f"   [Streamer] Purged {len(final_stale_ids)} stale matches.")

                    if live_matches or resolved_matches or force_finished_ids:
                        msg = f"   [Streamer] Upserting {len(live_matches)} live"
                        if resolved_matches: msg += f" + {len(resolved_matches)} resolved"
                        if force_finished_ids: msg += f" + {len(force_finished_ids)} force-finished"
                        print(msg + " entries.")

                        for m in live_matches:
                            save_live_score_entry(m)

                        sched_upd, pred_upd = _propagate_status_updates(
                            live_matches, resolved_matches, force_finished_ids=force_finished_ids
                        )
                        print(f"   [Streamer] Propagation: {len(sched_upd)} schedules, {len(pred_upd)} predictions.")

                        current_sig = (frozenset(current_live_ids), len(sched_upd), len(pred_upd))
                        if current_sig == _last_push_sig:
                            print(f"   [Streamer] Cycle {cycle} @ {now_ts}: {len(live_matches)} Live | {len(resolved_matches)} Res | {len(all_matches)} Total (no delta)")
                        else:
                            _last_push_sig = current_sig
                            if sync.supabase:
                                print(f"   [Streamer] Pushing to Supabase...")
                                if live_matches: await sync.batch_upsert('live_scores', live_matches)
                                if pred_upd: await sync.batch_upsert('predictions', pred_upd)
                                if sched_upd: await sync.batch_upsert('schedules', sched_upd)
                                if final_stale_ids:
                                    try:
                                        sync.supabase.table('live_scores').delete().in_('fixture_id', list(final_stale_ids)).execute()
                                    except Exception as e:
                                        print(f"   [Streamer] Supabase delete warning: {e}")
                            print(f"   [Streamer] Cycle {cycle} @ {now_ts}: {len(live_matches)} Live | {len(resolved_matches)} Res | {len(all_matches)} Total")
                    else:
                        _propagate_status_updates([], [])
                        print(f"   [Streamer] {now_ts} -- No active matches (Cycle {cycle}).")

                    if cycle % 5 == 0:
                        backlog_upds = _review_pending_backlog()
                        if backlog_upds and sync.supabase:
                            print(f"   [Streamer] Pushing {len(backlog_upds)} backlog resolutions...")
                            await sync.batch_upsert('predictions', backlog_upds)

                    await asyncio.sleep(STREAM_INTERVAL)

                except Exception as e:
                    if "Target crashed" in str(e) or "Page crashed" in str(e):
                        print(f"   [Streamer] CRITICAL: Browser crashed in cycle {cycle}. Recycling...")
                        break
                    else:
                        print(f"   [Streamer] Extraction Error cycle {cycle}: {e}")
                        await asyncio.sleep(STREAM_INTERVAL)

            print(f"   [Streamer] Recycling browser session...")

        except Exception as e:
            print(f"   [Streamer] Loop Error: {e}. Retrying in 10s...")
            await asyncio.sleep(10)
        finally:
            if context:
                try: await context.close()
                except: pass
            if browser:
                try: await browser.close()
                except: pass

    print("   [Streamer] Streamer stopped.")