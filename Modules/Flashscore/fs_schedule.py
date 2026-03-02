# fs_schedule.py: Daily match list extraction for Flashscore.
# Part of LeoBook Modules — Flashscore
#
# Delegates to fs_extractor for ALL-tab extraction. Handles DB save + cloud sync.

from datetime import datetime as dt
from playwright.async_api import Page
from Data.Access.db_helpers import (
    save_schedule_batch, save_team_entry, save_region_league_entry
)
from Data.Access.sync_manager import SyncManager
from Modules.Flashscore.fs_extractor import expand_all_leagues, extract_all_matches


async def extract_matches_from_page(page: Page) -> list:
    """
    Extracts ALL matches from the ALL tab, expands collapsed leagues first.
    Saves schedule entries + teams locally via SQLite upsert.
    """
    print("    [Extractor] Extracting match data from ALL tab...")

    expanded = await expand_all_leagues(page)
    if expanded:
        print(f"    [Extractor] Bulk-expanded {expanded} collapsed leagues.")

    matches = await extract_all_matches(page, label="Extractor")

    if matches:
        print(f"    [Extractor] Pairings complete. Saving {len(matches)} fixtures, teams and leagues...")

        now = dt.now().isoformat()
        schedule_rows = []
        team_rows = []
        rl_rows = []

        seen_teams = set()
        seen_leagues = set()

        for m in matches:
            # 1. Schedule Data
            schedule_rows.append({
                'fixture_id': m.get('fixture_id'),
                'date': m.get('date') or 'Unknown',
                'match_time': m.get('match_time') or 'Unknown',
                'region_league': m.get('region_league') or 'Unknown',
                'league_id': m.get('league_id') or 'Unknown',
                'home_team': m.get('home_team') or 'Unknown',
                'away_team': m.get('away_team') or 'Unknown',
                'home_team_id': m.get('home_team_id') or 'Unknown',
                'away_team_id': m.get('away_team_id') or 'Unknown',
                'home_score': m.get('home_score', ''),
                'away_score': m.get('away_score', ''),
                'match_status': m.get('status') or 'scheduled',
                'match_link': m.get('match_link') or 'Unknown',
                'league_stage': m.get('league_stage') or 'Unknown',
                'last_updated': now
            })

            # 2. League Data
            rl_name = m.get('region_league', 'Unknown')
            if rl_name not in seen_leagues:
                seen_leagues.add(rl_name)
                region = rl_name.split(' - ')[0] if ' - ' in rl_name else 'Unknown'
                league = rl_name.split(' - ')[1] if ' - ' in rl_name else rl_name

                l_url = m.get('league_url', '')
                league_id = 'unknown'
                if l_url and '/football/' in l_url:
                    slug_parts = l_url.split('/football/')[-1].strip('/').split('/')
                    if len(slug_parts) >= 2:
                        league_id = f"{slug_parts[0]}_{slug_parts[1]}".upper().replace('-', '_')
                    elif len(slug_parts) == 1:
                        league_id = slug_parts[0].upper().replace('-', '_')

                if league_id == 'unknown' or not league_id:
                    league_id = rl_name.replace(' ', '_').replace('-', '_').upper()

                rl_rows.append({
                    'league_id': league_id,
                    'region': region or 'Unknown',
                    'league': league or 'Unknown',
                    'league_url': m.get('league_url') or 'Unknown',
                    'league_crest': m.get('league_crest') or 'Unknown',
                    'region_flag': m.get('region_flag') or 'Unknown',
                    'date_updated': now,
                    'last_updated': now
                })

            # 3. Team Data
            for prefix in ['home', 'away']:
                tid = m.get(f'{prefix}_team_id')
                if tid and tid not in seen_teams:
                    seen_teams.add(tid)
                    team_rows.append({
                        'team_id': tid,
                        'team_name': m.get(f'{prefix}_team') or 'Unknown',
                        'league_ids': league_id,
                        'team_crest': m.get(f'{prefix}_crest') or 'Unknown',
                        'team_url': m.get(f'{prefix}_team_url') or 'Unknown',
                        'last_updated': now
                    })

        # Save to SQLite via db_helpers
        save_schedule_batch(schedule_rows)
        for t in team_rows:
            save_team_entry(t)
        for rl in rl_rows:
            save_region_league_entry(rl)

        print(f"    [Extractor] Saved {len(schedule_rows)} fixtures, {len(team_rows)} teams, and {len(rl_rows)} leagues.")

        # Cloud sync
        sync = SyncManager()
        if sync.supabase:
            print(f"    [Cloud] Synchronizing metadata to Supabase...")
            await sync.batch_upsert('schedules', schedule_rows)
            await sync.batch_upsert('teams', team_rows)
            await sync.batch_upsert('region_league', rl_rows)
            print(f"    [SUCCESS] Multi-table synchronization complete.")

    return matches
