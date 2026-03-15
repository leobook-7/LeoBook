# asset_manager.py: Supabase Storage asset sync — team crests, league crests, region flags.
# Part of LeoBook Data Access Layer
#
# Functions: sync_team_assets(), sync_league_assets(), sync_region_flags()
# Called by: Leo.py (--assets) | Core/System/pipeline.py
# Assets: Data/Store/flag-icons/ (SVGs), Data/Store/logos/ (team logos), Data/Store/crests/

import os
import json
import logging
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from Data.Access.supabase_client import get_supabase_client
from Data.Access.league_db import DB_DIR

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
PROJECT_ROOT = Path(__file__).parent.parent.parent
STORE_DIR   = PROJECT_ROOT / "Data" / "Store"
FLAG_ICONS_DIR = STORE_DIR / "flag-icons"      # SVG flags (moved from Modules/Assets/flag-icons-main/flags/)
COUNTRY_JSON   = STORE_DIR / "country.json"    # Country code map

# Manual overrides: Flashscore region name → ISO code
# For sub-national entities and naming mismatches not in country.json
REGION_TO_ISO_OVERRIDES = {
    "ENGLAND": "gb-eng",
    "SCOTLAND": "gb-sct",
    "WALES": "gb-wls",
    "NORTHERN IRELAND": "gb-nir",
    "IVORY COAST": "ci",
    "DR CONGO": "cd",
    "ESWATINI": "sz",
    "UNITED ARAB EMIRATES": "ae",
    "SOUTH KOREA": "kr",
    "NORTH MACEDONIA": "mk",
    "TRINIDAD AND TOBAGO": "tt",
    "BOSNIA AND HERZEGOVINA": "ba",
    "WORLD": "un",
    "EUROPE": "eu",
    "AFRICA": "af",           # Uses Afghanistan flag as placeholder — will use generic
    "SOUTH AMERICA": "br",    # Placeholder — no continent flag
    "NORTH & CENTRAL AMERICA": "us",  # Placeholder
    "AUSTRALIA & OCEANIA": "au",
    "OCEANIA": "au",
    "MACAO": "mo",
    "SEYCHELLES": "sc",
    "SIERRA LEONE": "sl",
    "MAURITIUS": "mu",
    "RWANDA": "rw",
    "BURUNDI": "bi",
    "CHAD": "td",
    "GUINEA": "gn",
    "LIBYA": "ly",
    "KUWAIT": "kw",
    "FIJI": "fj",
    "BOTSWANA": "bw",
    "BURKINA FASO": "bf",
    "TURKEY": "tr",
    "WAL": "gb-wls",           # Flashscore stores Wales country_code as 'wal'
    "GB-WAL": "gb-wls",        # alternate form
    "WALESNM": "gb-wls",       # any other variant stored in region column
}

def _build_region_to_iso_map() -> dict:
    """Builds a region name → ISO code mapping from country.json + overrides."""
    mapping = dict(REGION_TO_ISO_OVERRIDES)  # Start with overrides

    if COUNTRY_JSON.exists():
        with open(COUNTRY_JSON, 'r', encoding='utf-8') as f:
            countries = json.load(f)
        for entry in countries:
            name_upper = entry['name'].upper()
            if name_upper not in mapping:
                mapping[name_upper] = entry['code']
    else:
        logger.warning(f"[!] country.json not found at {COUNTRY_JSON}")

    return mapping


def download_image(url: str, save_path: Path) -> bool:
    """Downloads an image from a URL and saves it temporarily."""
    if not url or url.lower() in ["unknown", "unknown url", "none"]:
        return False

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        with open(save_path, 'wb') as f:
            f.write(response.content)
        return True
    except Exception as e:
        logger.error(f"[x] Error downloading {url}: {e}")
        return False

def upload_to_supabase(storage_client, bucket_name: str, file_path: Path, remote_filename: str):
    """Uploads a file to Supabase storage bucket."""
    try:
        with open(file_path, 'rb') as f:
            res = storage_client.from_(bucket_name).upload(
                path=remote_filename,
                file=f,
                file_options={"cache-control": "3600", "upsert": "true"}
            )
            logger.info(f"[+] Uploaded {remote_filename} → {bucket_name}")
            return res
    except Exception as e:
        logger.error(f"[x] Error uploading {remote_filename} to {bucket_name}: {e}")
        return None

def ensure_bucket_exists(storage_client, bucket_name: str):
    """Checks if a bucket exists, creates it if it doesn't."""
    try:
        buckets = storage_client.list_buckets()
        bucket_names = [b.name for b in buckets]
        if bucket_name not in bucket_names:
            logger.info(f"[*] Bucket '{bucket_name}' not found. Creating...")
            storage_client.create_bucket(bucket_name, options={"public": True})
            logger.info(f"[+] Bucket '{bucket_name}' created.")
        else:
            logger.info(f"[*] Bucket '{bucket_name}' exists.")
        return True
    except Exception as e:
        logger.error(f"[x] Error ensuring bucket '{bucket_name}': {e}")
        return False


def _slugify(name: str) -> str:
    """Convert a name to a safe lowercase filename slug."""
    import re
    return re.sub(r'[^a-z0-9_]', '_', name.lower().strip())[:80]


def _build_public_url(client, bucket: str, remote_name: str) -> Optional[str]:
    """Construct the public Supabase Storage URL for an uploaded file."""
    try:
        result = client.storage.from_(bucket).get_public_url(remote_name)
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return result.get("publicURL") or result.get("publicUrl")
    except Exception as e:
        logger.warning("[Assets] Could not build public URL for %s/%s: %s",
                       bucket, remote_name, e)
    return None


def sync_team_assets(limit: Optional[int] = None):
    """Sync team crests from SQLite teams table to Supabase storage.

    Reads teams.crest from SQLite (set by the enricher after upload).
    Only re-uploads crests that are local file paths (not yet on Supabase).
    Skips teams whose crest is already a Supabase URL.
    """
    from Data.Access.league_db import get_connection

    client = get_supabase_client()
    if not client:
        logger.warning("[Assets] No Supabase client — skipping team assets sync")
        return

    storage = client.storage
    ensure_bucket_exists(storage, "team-crests")

    conn = get_connection()
    query = """
        SELECT team_id, name, crest
        FROM teams
        WHERE crest IS NOT NULL
          AND crest != ''
          AND crest NOT LIKE 'http%'
    """
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    logger.info(f"[Assets] Team crests to sync: {len(rows)}")

    if not rows:
        logger.info("[Assets] All team crests already on Supabase or none present.")
        return

    temp_dir = Path("temp_assets_teams")
    temp_dir.mkdir(exist_ok=True)
    synced = 0

    for row in rows:
        team_id = row["team_id"] if hasattr(row, "keys") else row[0]
        name    = row["name"]    if hasattr(row, "keys") else row[1]
        crest   = row["crest"]   if hasattr(row, "keys") else row[2]

        if not crest or not os.path.exists(crest):
            continue

        slug = _slugify(name or str(team_id))
        remote_name = f"{slug}.png"
        local_path  = Path(crest)

        result = upload_to_supabase(storage, "team-crests", local_path, remote_name)
        if result:
            supabase_url = _build_public_url(client, "team-crests", remote_name)
            if supabase_url:
                conn.execute(
                    "UPDATE teams SET crest = ? WHERE team_id = ?",
                    (supabase_url, team_id)
                )
                synced += 1

    conn.commit()
    logger.info(f"[Assets] Team crests synced: {synced}/{len(rows)}")

    if temp_dir.exists():
        try:
            temp_dir.rmdir()
        except Exception:
            pass

def sync_league_assets(limit: Optional[int] = None):
    """Sync league crests from SQLite leagues table to Supabase storage.

    Reads leagues.crest from SQLite. Only uploads crests that are local
    file paths not yet on Supabase. Skips rows already with Supabase URLs.
    """
    from Data.Access.league_db import get_connection

    client = get_supabase_client()
    if not client:
        logger.warning("[Assets] No Supabase client — skipping league assets sync")
        return

    storage = client.storage
    ensure_bucket_exists(storage, "league-crests")

    conn = get_connection()
    query = """
        SELECT league_id, name, crest
        FROM leagues
        WHERE crest IS NOT NULL
          AND crest != ''
          AND crest NOT LIKE 'http%'
    """
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    logger.info(f"[Assets] League crests to sync: {len(rows)}")

    if not rows:
        logger.info("[Assets] All league crests already on Supabase or none present.")
        return

    temp_dir = Path("temp_assets_leagues")
    temp_dir.mkdir(exist_ok=True)
    synced = 0

    for row in rows:
        league_id = row["league_id"] if hasattr(row, "keys") else row[0]
        name      = row["name"]      if hasattr(row, "keys") else row[1]
        crest     = row["crest"]     if hasattr(row, "keys") else row[2]

        if not crest or not os.path.exists(crest):
            continue

        slug = _slugify(name or str(league_id))
        remote_name = f"{slug}.png"
        local_path  = Path(crest)

        result = upload_to_supabase(storage, "league-crests", local_path, remote_name)
        if result:
            supabase_url = _build_public_url(client, "league-crests", remote_name)
            if supabase_url:
                conn.execute(
                    "UPDATE leagues SET crest = ? WHERE league_id = ?",
                    (supabase_url, league_id)
                )
                synced += 1

    conn.commit()
    logger.info(f"[Assets] League crests synced: {synced}/{len(rows)}")

    if temp_dir.exists():
        try:
            temp_dir.rmdir()
        except Exception:
            pass

def sync_region_flags(limit: Optional[int] = None):
    """Sync country/region flag SVGs from local flag-icons-main to Supabase.

    Resolves each league's flag ISO code using this priority:
      1. country_code  — domestic leagues (e.g. 'br', 'gb-eng', 'ES')
      2. region        — international/continental leagues (e.g. 'AFRICA')
                         resolved via REGION_TO_ISO_OVERRIDES + country.json

    Uploads each distinct SVG once to Supabase `flags` bucket, then writes
    the public URL back to `leagues.region_flag` in SQLite so the normal
    sync pipeline can push it to Supabase.

    Args:
        limit: Optional cap on number of leagues processed (for testing).
    """
    from Data.Access.league_db import get_connection

    client = get_supabase_client()
    if not client:
        logger.error("[x] No Supabase client — aborting flag sync.")
        return

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    if not supabase_url:
        logger.error("[x] SUPABASE_URL not set — cannot construct public URLs.")
        return

    storage = client.storage
    ensure_bucket_exists(storage, "flags")

    # ── Build ISO code lookup ─────────────────────────────────────────────
    # Combines REGION_TO_ISO_OVERRIDES (handles sub-national + naming mismatches)
    # with country.json (full ISO list).
    region_map = _build_region_to_iso_map()  # already normalises keys to UPPER

    # ── Load all leagues from SQLite ──────────────────────────────────────
    conn = get_connection()
    rows = conn.execute("""
        SELECT league_id, country_code, region, region_flag
        FROM leagues
        WHERE league_id IS NOT NULL
        ORDER BY league_id
    """).fetchall()

    if limit:
        rows = rows[:limit]

    logger.info("[*] Flag sync: %d leagues to process.", len(rows))

    uploaded_svgs: set = set()   # track which ISO codes we've already uploaded
    updated_leagues = 0
    skipped = 0
    not_found: list = []

    for row in rows:
        league_id    = row["league_id"]    if hasattr(row, "keys") else row[0]
        country_code = row["country_code"] if hasattr(row, "keys") else row[1]
        region       = row["region"]       if hasattr(row, "keys") else row[2]

        # ── Resolve ISO code ──────────────────────────────────────────────
        iso_code = None

        # Priority 1: country_code (domestic leagues)
        if country_code and country_code.strip():
            # Normalise: DB stores mixed-case ('ES', 'GB-ENG', 'br')
            # SVG filenames are always lowercase ('es', 'gb-eng', 'br')
            iso_code = country_code.strip().lower()

        # Priority 2: region → override map (international leagues)
        if not iso_code and region and region.strip():
            iso_code = region_map.get(region.strip().upper())

        if not iso_code:
            skipped += 1
            continue

        # ── Locate local SVG ─────────────────────────────────────────────
        # flag-icons-main uses 4x3 aspect ratio for league/country flags
        svg_path = FLAG_ICONS_DIR / "flags" / "4x3" / f"{iso_code}.svg"

        if not svg_path.exists():
            not_found.append(f"{league_id} ({iso_code})")
            skipped += 1
            continue

        # ── Upload SVG (deduplicated — upload each ISO code only once) ────
        remote_name = f"{iso_code}.svg"
        if iso_code not in uploaded_svgs:
            result = upload_to_supabase(storage, "flags", svg_path, remote_name)
            if result:
                uploaded_svgs.add(iso_code)

        # ── Build public URL and write to SQLite ──────────────────────────
        public_url = (
            f"{supabase_url}/storage/v1/object/public/flags/{remote_name}"
        )

        conn.execute(
            "UPDATE leagues SET region_flag = ?, last_updated = ? WHERE league_id = ?",
            (public_url, datetime.now(timezone.utc).isoformat(), league_id)
        )
        updated_leagues += 1

        # Commit in batches of 100 to avoid long-running transactions
        if updated_leagues % 100 == 0:
            conn.commit()
            logger.info("[*] Flag sync progress: %d leagues updated.", updated_leagues)

    # Final commit
    conn.commit()

    logger.info(
        "[✓] Flag sync complete: %d SVGs uploaded, %d leagues updated, %d skipped.",
        len(uploaded_svgs), updated_leagues, skipped
    )

    if not_found:
        logger.warning(
            "[!] SVG not found locally for %d leagues: %s",
            len(not_found), not_found[:20]
        )

    # ── Summary print ─────────────────────────────────────────────────────
    print(f"  [Flags] {len(uploaded_svgs)} SVGs uploaded to Supabase 'flags' bucket")
    print(f"  [Flags] {updated_leagues} leagues updated in SQLite (leagues.region_flag)")
    print(f"  [Flags] {skipped} leagues skipped (no country_code or region resolved)")
    if not_found:
        print(f"  [Flags] {len(not_found)} SVG files missing locally")
    print(f"  [Flags] Run --sync to push region_flag values to Supabase table")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync assets to Supabase Storage.")
    parser.add_argument("--teams", action="store_true", help="Sync team crests")
    parser.add_argument("--leagues", action="store_true", help="Sync league crests")
    parser.add_argument("--flags", action="store_true", help="Sync region flags")
    parser.add_argument("--all", action="store_true", help="Sync all assets")
    parser.add_argument("--limit", type=int, help="Limit items for testing")

    args = parser.parse_args()

    if args.all or args.teams:
        sync_team_assets(limit=args.limit)
    if args.all or args.leagues:
        sync_league_assets(limit=args.limit)
    if args.all or args.flags:
        sync_region_flags()

    if not (args.all or args.teams or args.leagues or args.flags):
        parser.print_help()
