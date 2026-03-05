# fb_manager.py: fb_manager.py: Orchestration layer for Football.com booking process.
# Part of LeoBook Modules — Football.com
#
# Functions: _create_session(), run_odds_harvesting(), run_automated_booking(), run_football_com_booking()

"""
Football.com Orchestrator — Decoupled v2.8
Two exported functions with shared session setup.
"""

import asyncio
from pathlib import Path
from playwright.async_api import Playwright

# Modular Imports
from .fb_setup import get_pending_predictions_by_date
from .fb_session import launch_browser_with_retry
from .fb_url_resolver import resolve_urls
from .navigator import load_or_create_session, extract_balance
from Core.Utils.utils import log_error_state
from Core.System.lifecycle import log_state
from Core.Intelligence.aigo_suite import AIGOSuite


async def _create_session(playwright: Playwright):
    """Full session setup: launch browser, login, extract balance. For bet placement."""
    user_data_dir = Path("Data/Auth/ChromeData_v3").absolute()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    context = await launch_browser_with_retry(playwright, user_data_dir)
    _, page = await load_or_create_session(context)

    from Core.Utils.constants import CURRENCY_SYMBOL
    print(f"  [Balance] Current: {CURRENCY_SYMBOL}{current_balance:.2f}")

    return context, page, current_balance


async def _create_session_no_login(playwright: Playwright):
    """Lightweight session: launch browser, navigate to site. NO login, NO balance.
    Used for URL resolution and odds extraction which are public pages."""
    user_data_dir = Path("Data/Auth/ChromeData_v3").absolute()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    context = await launch_browser_with_retry(playwright, user_data_dir)

    if not context.pages:
        page = await context.new_page()
    else:
        page = context.pages[0]

    current_url = page.url
    if "football.com" not in current_url or current_url == "about:blank":
        await page.goto("https://www.football.com/ng", wait_until='domcontentloaded',
                        timeout=30000)

    return context, page


@AIGOSuite.aigo_retry(max_retries=2, delay=5.0)
async def run_odds_harvesting(playwright: Playwright):
    """
    Chapter 1 Page 1: URL Resolution & Odds Harvesting (V7).
    Uses scheduled fixtures (from enrichment) — NOT predictions.
    Resolves Flashscore → Football.com URLs. Harvests booking codes per match.
    Does NOT place bets. Does NOT require login.
    """
    print("\n--- Running Football.com Odds Harvesting (Chapter 1 P1) ---")

    # V7: Get scheduled fixtures from the schedules table (next 7 days)
    from Core.Intelligence.prediction_pipeline import get_weekly_fixtures
    from Data.Access.league_db import init_db
    conn = init_db()
    weekly_fixtures = get_weekly_fixtures(conn)

    if not weekly_fixtures:
        print("  [Info] No scheduled fixtures found for the next 7 days.")
        return

    # Group fixtures by date
    fixtures_by_date = {}
    for f in weekly_fixtures:
        d_str = f.get('date', '')
        if d_str:
            fixtures_by_date.setdefault(d_str, []).append(f)

    if not fixtures_by_date:
        print("  [Info] No future fixtures found.")
        return

    print(f"  [Fixtures] {len(weekly_fixtures)} matches across {len(fixtures_by_date)} days.")

    max_restarts = 3
    restarts = 0

    while restarts <= max_restarts:
        context = None
        try:
            print(f"  [System] Launching Harvest Session (Restart {restarts}/{max_restarts})...")
            context, page = await _create_session_no_login(playwright)
            log_state(chapter="Ch1 P1", action="Harvesting odds")

            for target_date, day_fixtures in sorted(fixtures_by_date.items()):
                print(f"\n--- Date: {target_date} ({len(day_fixtures)} matches) ---")

                # 1. URL Resolution (Fuzzy match FS → FB)
                matched_urls = await resolve_urls(page, target_date)
                if not matched_urls:
                    continue

                # 2. Odds Selection & Code Extraction
                print(f"  [Ch1 P1] Starting odds discovery for {target_date}...")
                from Modules.FootballCom.booker.booking_code import harvest_booking_codes
                await harvest_booking_codes(page, matched_urls, day_fixtures, target_date)

            break  # Success exit

        except Exception as e:
            is_fatal = "FatalSessionError" in str(type(e)) or "dirty" in str(e).lower()
            if is_fatal and restarts < max_restarts:
                print(f"\n[!!!] FATAL SESSION ERROR: {e}")
                restarts += 1
                if context:
                    await context.close()
                await asyncio.sleep(5)
                continue
            else:
                await log_error_state(None, "harvest_fatal", e)
                print(f"  [CRITICAL] Harvest failed: {e}")
                break
        finally:
            if context:
                try: await context.close()
                except: pass


@AIGOSuite.aigo_retry(max_retries=2, delay=5.0)
async def run_automated_booking(playwright: Playwright):
    """
    Chapter 2 Page 1: Automated Booking.
    Reads harvested codes and places multi-bets. Does NOT harvest.
    """
    print("\n--- Running Automated Booking (Chapter 2A) ---")

    predictions_by_date = await get_pending_predictions_by_date()
    if not predictions_by_date:
        return

    # 1. Pre-fetch booking queue (Decoupling: Fetch THEN Act)
    booking_queue = {}
    print("  [System] Building booking queue from registry...")
    from Modules.FootballCom.fb_url_resolver import get_harvested_matches_for_date
    
    for target_date in sorted(predictions_by_date.keys()):
        harvested = await get_harvested_matches_for_date(target_date)
        if harvested:
            booking_queue[target_date] = harvested
            
    if not booking_queue:
        print("  [System] No harvested matches found for any pending dates. Exiting.")
        return

    max_restarts = 3
    restarts = 0

    while restarts <= max_restarts:
        context = None
        try:
            print(f"  [System] Launching Booking Session (Restart {restarts}/{max_restarts})...")
            context, page, current_balance = await _create_session(playwright)
            log_state(chapter="Chapter 2A", action="Placing bets")

            from Modules.FootballCom.booker.placement import place_multi_bet_from_codes

            for target_date, harvested in booking_queue.items():
                print(f"\n--- Booking Date: {target_date} ---")
                await place_multi_bet_from_codes(page, harvested, current_balance)
                log_state(chapter="Chapter 2A", action="Booking Complete", next_step=f"Processed {target_date}")

            break  # Success exit

        except Exception as e:
            is_fatal = "FatalSessionError" in str(type(e)) or "dirty" in str(e).lower()
            if is_fatal and restarts < max_restarts:
                print(f"\n[!!!] FATAL SESSION ERROR: {e}")
                restarts += 1
                if context:
                    await context.close()
                await asyncio.sleep(5)
                continue
            else:
                await log_error_state(None, "booking_fatal", e)
                print(f"  [CRITICAL] Booking failed: {e}")
                break
        finally:
            if context:
                try: await context.close()
                except: pass


# Backward compat — keep old name pointing to harvesting for any legacy callers
async def run_football_com_booking(playwright: Playwright):
    """Legacy wrapper: runs both harvesting and booking sequentially."""
    await run_odds_harvesting(playwright)
    await run_automated_booking(playwright)