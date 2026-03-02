# sync_manager.py: Bi-directional sync between local SQLite and Supabase.
# Part of LeoBook Data — Access Layer
#
# Classes: SyncManager
# Functions: run_full_sync()

import logging
import re
import pandas as pd
import numpy as np
from tqdm import tqdm
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from Data.Access.supabase_client import get_supabase_client
from Data.Access.league_db import get_connection, init_db, query_all
from Core.Intelligence.aigo_suite import AIGOSuite


logger = logging.getLogger(__name__)

# SQLite table -> Supabase table mapping
# local_table: SQLite table name
# remote_table: Supabase table name
# key: primary key / conflict field
TABLE_CONFIG = {
    'predictions':      {'local_table': 'predictions',      'remote_table': 'predictions',      'key': 'fixture_id'},
    'schedules':        {'local_table': 'fixtures',         'remote_table': 'schedules',        'key': 'fixture_id'},
    'teams':            {'local_table': 'teams',            'remote_table': 'teams',            'key': 'team_id'},
    'region_league':    {'local_table': 'leagues',          'remote_table': 'region_league',    'key': 'league_id'},
    'standings':        {'local_table': 'standings',        'remote_table': 'standings',        'key': 'standings_key'},
    'fb_matches':       {'local_table': 'fb_matches',       'remote_table': 'fb_matches',       'key': 'site_match_id'},
    'profiles':         {'local_table': 'profiles',         'remote_table': 'profiles',         'key': 'id'},
    'custom_rules':     {'local_table': 'custom_rules',     'remote_table': 'custom_rules',     'key': 'id'},
    'rule_executions':  {'local_table': 'rule_executions',  'remote_table': 'rule_executions',  'key': 'id'},
    'accuracy_reports': {'local_table': 'accuracy_reports', 'remote_table': 'accuracy_reports', 'key': 'report_id'},
    'audit_log':        {'local_table': 'audit_log',        'remote_table': 'audit_log',        'key': 'id'},
    'live_scores':      {'local_table': 'live_scores',      'remote_table': 'live_scores',      'key': 'fixture_id'},
    'countries':        {'local_table': 'countries',        'remote_table': 'countries',        'key': 'code'},
}

# Column renames: SQLite column -> Supabase column (where they differ)
_COL_RENAME_PUSH = {
    'fixtures': {
        'time': 'match_time',
        'home_team_name': 'home_team',
        'away_team_name': 'away_team',
        'url': 'match_link',
    },
    'leagues': {
        'name': 'league',
        'crest': 'league_crest',
        'url': 'league_url',
    },
    'teams': {
        'name': 'team_name',
        'crest': 'team_crest',
        'url': 'team_url',
    },
}

# Reverse: Supabase column -> SQLite column (for pull)
_COL_RENAME_PULL = {
    'fixtures': {v: k for k, v in _COL_RENAME_PUSH.get('fixtures', {}).items()},
    'leagues': {v: k for k, v in _COL_RENAME_PUSH.get('leagues', {}).items()},
    'teams': {v: k for k, v in _COL_RENAME_PUSH.get('teams', {}).items()},
}


class SyncManager:
    """Manages bi-directional sync between local SQLite and Supabase."""

    def __init__(self):
        self.supabase = get_supabase_client()
        self.conn = init_db()
        if not self.supabase:
            logger.warning("[!] SyncManager initialized without Supabase connection. Sync disabled.")

    async def sync_on_startup(self):
        """Pull remote changes and push local changes for all configured tables."""
        if not self.supabase:
            return

        logger.info("Starting bi-directional sync on startup...")
        print("   [PROLOGUE] Bi-Directional Sync -- comparing local SQLite vs Supabase timestamps...")

        for table_key, config in TABLE_CONFIG.items():
            await self._sync_table(table_key, config)

    async def _sync_table(self, table_key: str, config: Dict):
        """Sync a single table using timestamp-based delta detection."""
        local_table = config['local_table']
        remote_table = config['remote_table']
        key_field = config['key']

        logger.info(f"  Syncing {local_table} <-> {remote_table}...")

        # 1. Fetch Remote Metadata (ID + last_updated)
        try:
            remote_meta = await self._fetch_remote_metadata(remote_table, key_field)
        except Exception as e:
            logger.error(f"    [x] Failed to fetch remote metadata for {remote_table}: {e}")
            return

        # 2. Load Local Data from SQLite
        try:
            local_rows = query_all(self.conn, local_table)
            if not local_rows:
                local_rows = []
            local_meta = {str(r.get(key_field, '')): r.get('last_updated', '') for r in local_rows if r.get(key_field)}
        except Exception as e:
            logger.error(f"    [x] Failed to query local {local_table}: {e}")
            return

        # 3. Delta Detection (Latest Wins)
        all_keys = set(local_meta.keys()) | set(remote_meta.keys())

        def normalize_ts(ts):
            if not ts or ts in ('None', 'nan', ''):
                return '1970-01-01T00:00:00'
            try:
                return pd.to_datetime(ts, utc=True).strftime('%Y-%m-%dT%H:%M:%S')
            except Exception:
                return '1970-01-01T00:00:00'

        to_push_ids = []
        to_pull_ids = []

        for key in all_keys:
            local_ts = normalize_ts(local_meta.get(key, ''))
            remote_ts = normalize_ts(remote_meta.get(key, ''))

            if local_ts > remote_ts or (local_ts != '1970-01-01T00:00:00' and remote_ts == '1970-01-01T00:00:00'):
                to_push_ids.append(key)
            elif remote_ts > local_ts or (remote_ts != '1970-01-01T00:00:00' and local_ts == '1970-01-01T00:00:00'):
                to_pull_ids.append(key)

        logger.info(f"    Delta: {len(to_push_ids)} to push, {len(to_pull_ids)} to pull.")

        if to_push_ids and to_pull_ids:
            print(f"   [{remote_table}] Bi-directional: {len(to_push_ids)} local->remote, {len(to_pull_ids)} remote->local")
        elif to_push_ids:
            print(f"   [{remote_table}] Push: {len(to_push_ids)} rows local->remote")
        elif to_pull_ids:
            print(f"   [{remote_table}] Pull: {len(to_pull_ids)} rows remote->local")
        else:
            print(f"   [{remote_table}] OK: Already in sync")

        # 4. Pull Operations
        if to_pull_ids:
            await self._pull_updates(local_table, remote_table, key_field, to_pull_ids)

        # 5. Push Operations
        if to_push_ids:
            rows_to_push = [r for r in local_rows if str(r.get(key_field, '')) in set(to_push_ids)]
            await self.batch_upsert(table_key, rows_to_push)
            await self._verify_sync_parity(table_key, to_push_ids)

    async def _fetch_remote_metadata(self, table_name: str, key_field: str) -> Dict[str, str]:
        """Fetch all ID:last_updated pairs from Supabase."""
        remote_map = {}
        batch_size = 1000
        offset = 0

        while True:
            try:
                res = self.supabase.table(table_name).select(f"{key_field},last_updated").range(offset, offset + batch_size - 1).execute()
                rows = res.data
                if not rows:
                    break
                for r in rows:
                    k = r.get(key_field)
                    if k:
                        remote_map[str(k)] = r.get('last_updated', '')
                if len(rows) < batch_size:
                    break
                offset += batch_size
            except Exception as e:
                logger.error(f"      [x] Metadata fetch error at offset {offset}: {e}")
                break

        return remote_map

    async def _pull_updates(self, local_table: str, remote_table: str,
                            key_field: str, ids: List[str]):
        """Fetch rows from Supabase and upsert into local SQLite."""
        if not ids:
            return

        logger.info(f"    Pulling {len(ids)} rows from remote...")

        pulled_data = []
        batch_size = 200
        pbar = tqdm(total=len(ids), desc=f"    Pulling {remote_table}", unit="row")
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            res = self.supabase.table(remote_table).select("*").in_(key_field, batch_ids).execute()
            pulled_data.extend(res.data)
            pbar.update(len(batch_ids))
        pbar.close()

        if not pulled_data:
            return

        # Apply column renames (Supabase -> SQLite)
        rename_map = _COL_RENAME_PULL.get(local_table, {})
        # Also handle over_2_5 -> over_2_5 (already correct in SQLite)

        for row in pulled_data:
            # Rename columns
            renamed = {}
            for k, v in row.items():
                new_k = rename_map.get(k, k)
                renamed[new_k] = v
            
            # Handle over_2_5 normalization
            if 'over_2.5' in renamed:
                renamed['over_2_5'] = renamed.pop('over_2.5')

            # Date normalization (PostgreSQL YYYY-MM-DD -> keep as-is for SQLite)
            # SQLite stores dates as text, no conversion needed

            # Get columns that exist in the local table
            table_cols = [c[1] for c in self.conn.execute(f"PRAGMA table_info({local_table})").fetchall()]
            filtered = {k: v for k, v in renamed.items() if k in table_cols and v is not None}

            if not filtered or key_field not in filtered:
                continue

            cols = list(filtered.keys())
            placeholders = ", ".join([f":{c}" for c in cols])
            col_str = ", ".join(cols)
            updates = ", ".join([f"{c} = excluded.{c}" for c in cols if c != key_field])

            try:
                self.conn.execute(
                    f"INSERT INTO {local_table} ({col_str}) VALUES ({placeholders}) "
                    f"ON CONFLICT({key_field}) DO UPDATE SET {updates}",
                    filtered,
                )
            except Exception as e:
                logger.warning(f"      [Pull] Failed to upsert row: {e}")

        self.conn.commit()
        logger.info(f"    [SUCCESS] Pulled {len(pulled_data)} rows into {local_table}.")

    async def batch_upsert(self, table_key: str, data: List[Dict[str, Any]]):
        """Upsert a batch of data to Supabase with strict cleaning."""
        if not self.supabase or not data:
            return

        conf = TABLE_CONFIG.get(table_key)
        if not conf:
            return

        local_table = conf['local_table']
        remote_table = conf['remote_table']
        conflict_key = conf['key']
        rename_map = _COL_RENAME_PUSH.get(local_table, {})

        cleaned_data = []
        for row in data:
            clean = {}
            for k, v in row.items():
                # Skip internal SQLite columns not in Supabase
                if k in ('id', 'rowid', 'hq_crest', 'processed'):
                    continue

                # Apply column renames
                out_key = rename_map.get(k, k)

                if v in ('', 'N/A', None, 'None', 'none', 'nan', 'NaN', 'null', 'NULL'):
                    clean[out_key] = None
                elif isinstance(v, str) and re.match(r"^\[.*\]$", v.strip()):
                    clean[out_key] = None
                else:
                    val = v
                    # CSV date format (DD.MM.YYYY) -> DB (YYYY-MM-DD)
                    if out_key in ['date', 'date_updated', 'last_extracted'] and isinstance(val, str):
                        match_full = re.match(r'^(\d{2})\.(\d{2})\.(\d{4})$', val)
                        if match_full:
                            d, m, y = match_full.groups()
                            val = f"{y}-{m}-{d}"
                        elif not re.match(r'^\d{4}-\d{2}-\d{2}', val):
                            val = None

                    if out_key == 'over_2.5' or out_key == 'over_2_5':
                        clean['over_2_5'] = val
                    else:
                        clean[out_key] = val

            # Timestamp normalization
            now_iso = datetime.utcnow().isoformat()
            for ts in ['last_updated', 'date_updated', 'last_extracted', 'created_at']:
                if ts in clean:
                    if not clean[ts] or not re.match(r'^\d{4}-\d{2}-\d{2}', str(clean[ts])):
                        clean[ts] = now_iso
            if 'last_updated' not in clean:
                clean['last_updated'] = now_iso

            # Remove auto-increment id if present (Supabase handles this)
            if 'id' in clean and (not clean['id'] or str(clean['id']).isdigit()):
                del clean['id']

            cleaned_data.append(clean)

        # Deduplicate
        keys = [k.strip() for k in conflict_key.split(',')]
        seen = set()
        deduped = []
        for row in cleaned_data:
            if all(row.get(k) not in (None, '') for k in keys):
                kv = tuple(row.get(k) for k in keys)
                if kv not in seen:
                    seen.add(kv)
                    deduped.append(row)

        if not deduped:
            return

        try:
            api_batch_size = 1000
            pbar = tqdm(total=len(deduped), desc=f"    Pushing {remote_table}", unit="row")
            for i in range(0, len(deduped), api_batch_size):
                batch = deduped[i:i + api_batch_size]
                self.supabase.table(remote_table).upsert(batch, on_conflict=conflict_key).execute()
                pbar.update(len(batch))
            pbar.close()
            logger.info(f"    [SYNC] Upserted {len(deduped)} rows to {remote_table}.")
        except Exception as e:
            pbar.close()
            print(f"    [x] Upsert failed for {remote_table}: {e}")
            logger.error(f"    [x] Upsert failed: {e}")

    async def _verify_sync_parity(self, table_key: str, pushed_ids: List[str], sample_size: int = 10):
        """Pick a sample and verify parity between local SQLite and remote Supabase."""
        if not pushed_ids:
            return

        conf = TABLE_CONFIG[table_key]
        local_table = conf['local_table']
        remote_table = conf['remote_table']
        key_field = conf['key']

        sample_ids = pushed_ids[:sample_size] if len(pushed_ids) <= sample_size else np.random.choice(pushed_ids, sample_size, replace=False).tolist()

        logger.info(f"    Verifying parity for {len(sample_ids)} sample rows...")

        try:
            # Fetch remote sample
            res = self.supabase.table(remote_table).select("*").in_(key_field, sample_ids).execute()
            remote_rows = {str(r[key_field]): r for r in res.data}

            # Fetch local sample from SQLite
            placeholders = ",".join(["?"] * len(sample_ids))
            local_data = self.conn.execute(
                f"SELECT * FROM {local_table} WHERE {key_field} IN ({placeholders})",
                sample_ids,
            ).fetchall()
            local_rows = {str(dict(r)[key_field]): dict(r) for r in local_data}

            mismatches = 0
            for uid in sample_ids:
                l_row = local_rows.get(uid)
                r_row = remote_rows.get(uid)

                if not r_row:
                    logger.warning(f"      [Parity Fail] ID {uid} missing from remote!")
                    mismatches += 1
                    continue

                l_ts = (l_row or {}).get('last_updated', '')
                r_ts = r_row.get('last_updated', '')

                try:
                    dt_l = datetime.fromisoformat(l_ts.replace('Z', '+00:00')) if l_ts else None
                    dt_r = datetime.fromisoformat(r_ts.replace('Z', '+00:00')) if r_ts else None
                    if dt_l and dt_r:
                        if dt_r < dt_l and abs((dt_l - dt_r).total_seconds()) > 1:
                            logger.warning(f"      [Parity Warning] ID {uid} timestamp mismatch!")
                            mismatches += 1
                except (ValueError, TypeError):
                    if r_ts < l_ts and r_ts[:19] != l_ts[:19]:
                        mismatches += 1

            if mismatches > 0:
                logger.error(f"    [PARITY ERROR] {mismatches} mismatches in {remote_table}.")
            else:
                logger.info(f"    [PARITY OK] {remote_table} sample verified.")

        except Exception as e:
            logger.error(f"    [x] Parity verification failed: {e}")


@AIGOSuite.aigo_retry(max_retries=3, delay=2.0, use_aigo=False)
async def run_full_sync(session_name: str = "Periodic"):
    """Wrapper to sync ALL tables with audit logging and AIGO protection."""
    from Data.Access.db_helpers import log_audit_event
    logger.info(f"Starting global full sync [{session_name}]...")

    manager = SyncManager()

    success_count = 0
    fail_count = 0
    errors = []

    for table_key, config in TABLE_CONFIG.items():
        try:
            await manager._sync_table(table_key, config)
            success_count += 1
        except Exception as e:
            logger.error(f"    [Sync Fatal] {table_key}: {e}")
            fail_count += 1
            errors.append(f"{table_key}: {str(e)}")

    status = "success" if fail_count == 0 else "partial_failure" if success_count > 0 else "failed"
    msg = f"Full Chapter Sync ({session_name}): {success_count} passed, {fail_count} failed."
    if errors:
        msg += f" Errors: {'; '.join(errors[:3])}"

    try:
        log_audit_event(event_type="SYSTEM_SYNC", description=msg, status=status)
    except Exception as e:
        logger.error(f"Failed to log audit event for sync: {e}")

    if fail_count > 0:
        print(f"\n[!] Sync Warning: {fail_count} tables failed. AIGO fallback may be required.")
        return False

    return True
