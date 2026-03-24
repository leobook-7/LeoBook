# prediction_pipeline.py: Pure DB-driven prediction pipeline for Chapter 1 Page 2.
# Part of LeoBook Core — Intelligence
#
# Functions: get_weekly_fixtures(), compute_team_form(), compute_h2h(),
#            build_rule_engine_input(), run_predictions(), apply_smart_scheduling()
# Called by: Leo.py (Chapter 1 Page 2)

"""
V7 Prediction Pipeline — Zero browser, pure computation.
All data sourced from the schedules table (populated by weekly enrichment).
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional

from Data.Access.league_db import init_db, computed_standings
from Data.Access.db_helpers import save_prediction
from Core.Intelligence.rule_engine import RuleEngine
from Core.Intelligence.rl.inference import RLPredictor
from Core.Intelligence.ensemble import EnsembleEngine
from Core.Utils.constants import now_ng

logger = logging.getLogger(__name__)
NIGERIA_TZ = ZoneInfo("Africa/Lagos")


def _schedule_to_match_dict(row: Dict) -> Dict:
    """Convert a schedules table row into the match dict format RuleEngine/TagGenerator expects.

    TagGenerator._parse_match_result expects:
        home: str, away: str, score: str (e.g. "2-1"), winner: str ("Home"/"Away"/"Draw"),
        date: str, fixture_id: str
    """
    home_score = row.get("home_score")
    away_score = row.get("away_score")

    # Build score string
    if home_score is not None and away_score is not None:
        try:
            hs, as_ = int(home_score), int(away_score)
            score = f"{hs}-{as_}"
            if hs > as_:
                winner = "Home"
            elif as_ > hs:
                winner = "Away"
            else:
                winner = "Draw"
        except (ValueError, TypeError):
            score = "0-0"
            winner = "Draw"
    else:
        score = "0-0"
        winner = "Draw"

    return {
        "home": row.get("home_team_name", ""),
        "away": row.get("away_team_name", ""),
        "score": score,
        "winner": winner,
        "date": row.get("date", ""),
        "fixture_id": row.get("fixture_id", ""),
    }


def get_weekly_fixtures(conn=None, days: int = 7) -> List[Dict]:
    """Query schedules for the next N days of scheduled (unplayed) matches.

    Returns list of schedule row dicts for matches that haven't been played yet.
    """
    conn = conn or init_db()
    now = now_ng()
    today_str = now.strftime("%Y-%m-%d")  # Must match DB date format (YYYY-MM-DD)

    # Scan next 7 days (including today)
    date_strings = []
    for i in range(8):
        target = now + timedelta(days=i)
        date_strings.append(target.strftime("%Y-%m-%d")) # Unified v7 Format: YYYY-MM-DD

    placeholders = ",".join(["?"] * len(date_strings))
    rows = conn.execute(
        f"""SELECT
               -- ROOT CAUSE 2 FIX: Prefer fixture-specific name over global teams.name.
               -- teams.name is polluted by multi-league upserts (wrong transliteration / alias).
               -- schedules.home_team_name is stored at scrape time for THAT fixture, so it is
               -- the most accurate name for this specific matchup.
               COALESCE(NULLIF(s.home_team_name, ''), h.name) AS home_team_name,
               COALESCE(NULLIF(s.away_team_name, ''), a.name) AS away_team_name,
               s.*
            FROM schedules s
            LEFT JOIN teams h ON s.home_team_id = h.team_id
            LEFT JOIN teams a ON s.away_team_id = a.team_id
            WHERE s.date IN ({placeholders})
              AND (s.match_status IS NULL OR s.match_status = 'scheduled' OR s.match_status = '')
            ORDER BY s.date, s.time""",
        date_strings,
    ).fetchall()

    return [dict(r) for r in rows]


def compute_team_form(conn, team_id: str, limit: int = 10) -> List[Dict]:
    """Get last N completed matches for a team from the schedules table.

    Searches where team is either home or away and a result exists.
    Returns in RuleEngine-compatible format.
    """
    rows = conn.execute(
        """SELECT * FROM schedules
           WHERE (home_team_id = ? OR away_team_id = ?)
             AND home_score IS NOT NULL AND away_score IS NOT NULL
             AND home_score != '' AND away_score != ''
           ORDER BY date DESC
           LIMIT ?""",
        (team_id, team_id, limit),
    ).fetchall()

    return [_schedule_to_match_dict(dict(r)) for r in rows]


def compute_h2h(conn, home_team_id: str, away_team_id: str, limit: int = 10) -> List[Dict]:
    """Get direct head-to-head matches between two teams from schedules.

    Either team can be home or away in historical matchups.
    Returns in RuleEngine-compatible format.
    """
    rows = conn.execute(
        """SELECT * FROM schedules
           WHERE ((home_team_id = ? AND away_team_id = ?)
               OR (home_team_id = ? AND away_team_id = ?))
             AND home_score IS NOT NULL AND away_score IS NOT NULL
             AND home_score != '' AND away_score != ''
           ORDER BY date DESC
           LIMIT ?""",
        (home_team_id, away_team_id, away_team_id, home_team_id, limit),
    ).fetchall()

    return [_schedule_to_match_dict(dict(r)) for r in rows]


def build_rule_engine_input(conn, fixture) -> Dict[str, Any]:
    """Assemble the h2h_data + standings dict that RuleEngine.analyze expects.

    Args:
        conn: SQLite connection
        fixture: A schedule row dict for the fixture to predict

    Returns:
        Dict with {"h2h_data": {...}, "standings": [...], "real_odds": {...}}
    """
    fixture_id = fixture.get("fixture_id", "")
    home_team_id = fixture.get("home_team_id", "")
    away_team_id = fixture.get("away_team_id", "")
    home_team = fixture.get("home_team_name", "")
    away_team = fixture.get("away_team_name", "")
    league_id = fixture.get("league_id", "")
    country_league = fixture.get("country_league", "")
    season = fixture.get("season", "")

    # 1. Team form (last 10 completed matches per team)
    home_form = compute_team_form(conn, home_team_id, limit=10)
    away_form = compute_team_form(conn, away_team_id, limit=10)

    # 2. Direct H2H
    h2h = compute_h2h(conn, home_team_id, away_team_id, limit=10)

    # 3. Standings (computed on-the-fly from schedules)
    standings = []
    if league_id:
        standings = computed_standings(conn=conn, league_id=league_id, season=season)

    # 4. Fetch real harvested odds for this fixture
    # We map (market_id, exact_outcome, line) to our internal action keys.
    real_odds: Dict[str, float] = {}
    if fixture_id:
        from Core.Intelligence.rl.market_space import ACTIONS
        rows = conn.execute(
            "SELECT market_id, exact_outcome, line, odds_value FROM match_odds WHERE fixture_id = ?",
            (fixture_id,)
        ).fetchall()
        
        # Build lookup for harvested odds
        harvested_map = {}
        for r in rows:
            # Key format: market_id|outcome|line (normalized)
            l_str = str(r["line"]) if r["line"] else ""
            key = f"{r['market_id']}|{r['exact_outcome'].lower()}|{l_str}"
            harvested_map[key] = r["odds_value"]

        # Map ACTIONS to harvested odds
        for action in ACTIONS:
            a_key = action["key"]
            if a_key == "no_bet": continue
            
            # Action spec
            m_id = action.get("market_id")
            out  = action.get("outcome", "").lower()
            line = str(action.get("line")) if action.get("line") is not None else ""
            
            # Simple mapping logic: action "outcome" usually matches start of "exact_outcome"
            # or is exactly the same. We try a few variations.
            target_key = f"{m_id}|{out}|{line}"
            if target_key in harvested_map:
                real_odds[a_key] = harvested_map[target_key]
            else:
                # Fallback: exact_outcome starts with outcome (e.g. "Over 1.5" starts with "Over")
                for h_key, h_val in harvested_map.items():
                    h_mid, h_out, h_line = h_key.split("|")
                    if h_mid == m_id and h_line == line:
                         if h_out == out or h_out.startswith(out):
                             real_odds[a_key] = h_val
                             break

    if real_odds:
        logger.debug(f"    [Ch1P2] Real odds loaded for fixture {fixture_id}: {len(real_odds)} markets")
    elif fixture_id:
        logger.warning(f"    [Ch1P2] No real odds found for fixture {fixture_id} — will use synthetic fallback")

    h2h_data = {
        "home_team": home_team,
        "away_team": away_team,
        "country_league": country_league or "GLOBAL",
        "home_last_10_matches": home_form,
        "away_last_10_matches": away_form,
        "head_to_head": h2h,
    }

    return {"h2h_data": h2h_data, "standings": standings, "real_odds": real_odds}


def _get_existing_prediction_ids(conn) -> set:
    """Get fixture_ids of already-predicted matches to avoid duplicates."""
    try:
        rows = conn.execute("SELECT fixture_id FROM predictions WHERE fixture_id IS NOT NULL").fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


async def run_predictions(conn=None, fixtures: List[Dict] = None, scheduler=None) -> List[Dict]:
    """Main prediction loop — pure DB computation, zero browser.

    Args:
        conn: SQLite connection (optional)
        fixtures: Pre-fetched list of fixture dicts (optional, fetched if None)
        scheduler: TaskScheduler for smart scheduling (optional)

    Returns:
        List of generated prediction dicts
    """
    conn = conn or init_db()

    if fixtures is None:
        fixtures = get_weekly_fixtures(conn)

    if not fixtures:
        print("    [Predictions] No scheduled fixtures found for the next 7 days.")
        return []

    # Filter out already-predicted
    existing_ids = _get_existing_prediction_ids(conn)
    new_fixtures = [f for f in fixtures if f.get("fixture_id") not in existing_ids]

    if not new_fixtures:
        print("    [Predictions] All fixtures already predicted.")
        return []

    # Filter out past matches (already started today)
    now = now_ng()
    today_str = now.strftime("%Y-%m-%d")  # Must match DB date format
    now_time = now.time()

    eligible = []
    for f in new_fixtures:
        if f.get("date") == today_str:
            time_str = f.get("time", "")
            try:
                match_time = datetime.strptime(time_str, "%H:%M").time()
                if match_time > now_time:
                    eligible.append(f)
            except (ValueError, TypeError):
                eligible.append(f)  # Keep if time unparseable
        else:
            eligible.append(f)

    if not eligible:
        print("    [Predictions] No eligible fixtures (all already started or predicted).")
        return []

    print(f"    [Predictions] Processing {len(eligible)} fixtures (pure DB computation)...")

    predictions_made = []
    skipped = 0

    for fixture in eligible:
        fixture_id = fixture.get("fixture_id", "unknown")
        home = fixture.get("home_team_name", "?")
        away = fixture.get("away_team_name", "?")

        try:
            # Build input from DB
            vision_data = build_rule_engine_input(conn, fixture)
            real_odds = vision_data.get("real_odds", {})

            # Data quality gate: need at least 3 form matches per team
            home_form_n = len(vision_data["h2h_data"]["home_last_10_matches"])
            away_form_n = len(vision_data["h2h_data"]["away_last_10_matches"])

            if home_form_n < 3 or away_form_n < 3:
                skipped += 1
                continue

            # Run symbolic prediction
            rule_prediction = RuleEngine.analyze(vision_data, live_odds=real_odds)

            # --- Semantic Rule Engine Upgrade ---
            from Core.Intelligence.rule_engine_manager import SemanticRuleEngine
            engine = SemanticRuleEngine()
            rule_output = engine.choose_market(fixture, rule_prediction.get("xg_home", 0.0), rule_prediction.get("xg_away", 0.0))

            # Run neural prediction
            rl_predictor = RLPredictor.get_instance()
            rl_prediction = rl_predictor.predict(
                vision_data,
                fs_league_id=fixture.get("league_id", "GLOBAL"),
                home_team_id=fixture.get("home_team_id", ""),
                away_team_id=fixture.get("away_team_id", "")
            )

            # Ensemble Merge — scale W_neural by data richness for this league
            richness = EnsembleEngine.get_richness_score(
                fixture.get("league_id", "GLOBAL"),
                current_season=fixture.get("season", ""),
            )
            merged = EnsembleEngine.merge(
                rule_logits=rule_prediction.get("raw_scores", {"home": 1.0, "draw": 1.0, "away": 1.0}),
                rule_conf=rule_prediction.get("market_reliability", 50) / 100.0,
                rl_logits=rl_prediction.get("rl_action_probs"),
                rl_conf=rl_prediction.get("ml_confidence"),
                league_id=fixture.get("league_id", "GLOBAL"),
                data_richness_score=richness,
            )

            # Integrate merged data into final prediction
            # We keep Rule Engine's structural fields but update confidence and add ensemble metadata
            prediction = rule_prediction.copy()
            prediction["ensemble_path"] = merged["path"]
            prediction["ensemble_weights"] = merged["weights"]
            prediction["market_reliability"] = round(merged["confidence"] * 100, 1)
            prediction["rule_explanation"] = rule_output["explanation"]
            prediction["override_reason"] = rule_output.get("override_reason")
            prediction["chosen_market"] = rule_output["chosen_market"]
            prediction["market_id"] = rule_output["market_id"]
            prediction["statistical_edge"] = rule_output["statistical_edge"]


            # Use RuleEngine's top score as the pure model suggestion since we removed Poisson
            prediction["pure_model_suggestion"] = "Poisson Top Score: " + rule_prediction.get("best_score", "1-1")

            # ── Bug 2 fix: Attach chosen market odds to prediction ──────────
            # real_odds maps action keys (e.g. "home_win", "over_2_5") to odds values.
            # chosen_market is the human-readable label (e.g. "1X2 - Home").
            # We need to find the matching odds from real_odds using market_id + outcome.
            chosen_odds = ""
            if real_odds and prediction.get("chosen_market"):
                cm = prediction["chosen_market"]
                mid = str(prediction.get("market_id", ""))
                # Strategy 1: direct key match from real_odds
                # real_odds keys are action keys like "home_win", "dc_1x", "over_2_5"
                for rk, rv in real_odds.items():
                    chosen_odds = str(rv)
                    # We want the FIRST match for the chosen market_id
                    # Check if this action's market_id matches
                    try:
                        from Core.Intelligence.rl.market_space import ACTIONS
                        matching_action = next(
                            (a for a in ACTIONS if a["key"] == rk and str(a.get("market_id")) == mid),
                            None
                        )
                        if matching_action:
                            chosen_odds = str(rv)
                            break
                    except Exception:
                        break
                # Strategy 2: fallback — look up match_odds directly for chosen_market
                if not chosen_odds and fixture_id and mid:
                    try:
                        odds_row = conn.execute(
                            "SELECT odds_value FROM match_odds WHERE fixture_id = ? AND market_id = ? LIMIT 1",
                            (fixture_id, mid)
                        ).fetchone()
                        if odds_row:
                            chosen_odds = str(odds_row[0])
                    except Exception:
                        pass
            prediction["odds"] = chosen_odds

            # Update confidence label based on merged confidence
            conf = merged["confidence"]
            if conf > 0.75: prediction["confidence"] = "Very High"
            elif conf > 0.60: prediction["confidence"] = "High"
            elif conf > 0.45: prediction["confidence"] = "Medium"
            else: prediction["confidence"] = "Low"

            p_type = prediction.get("type", "SKIP")
            if p_type == "SKIP":
                skipped += 1
                continue

            # Record reference data
            h2h_ids = [m.get("fixture_id", "") for m in vision_data["h2h_data"]["head_to_head"] if m.get("fixture_id")]
            home_form_ids = [m.get("fixture_id", "") for m in vision_data["h2h_data"]["home_last_10_matches"] if m.get("fixture_id")]
            away_form_ids = [m.get("fixture_id", "") for m in vision_data["h2h_data"]["away_last_10_matches"] if m.get("fixture_id")]

            prediction["h2h_fixture_ids"] = h2h_ids
            prediction["form_fixture_ids"] = home_form_ids + away_form_ids
            prediction["standings_snapshot"] = vision_data["standings"]

            # Build match_data for save_prediction
            match_data = {
                "fixture_id": fixture_id,
                "date": fixture.get("date", ""),
                "match_time": fixture.get("time", ""),
                "country_league": fixture.get("country_league", ""),
                "home_team": home,
                "away_team": away,
                "home_team_id": fixture.get("home_team_id", ""),
                "away_team_id": fixture.get("away_team_id", ""),
                "match_link": fixture.get("match_link", ""),
            }

            save_prediction(match_data, prediction)
            predictions_made.append({**match_data, **prediction})
            print(f"      [✓] {home} vs {away} → {p_type} ({prediction.get('confidence', '?')})")




        except Exception as e:
            logger.error(f"      [✗] Prediction failed for {home} vs {away}: {e}")
            skipped += 1

    # Apply smart scheduling if scheduler provided
    if scheduler and predictions_made:
        predictions_made = apply_smart_scheduling(predictions_made, scheduler, conn)

    print(f"\n    [Predictions] Done: {len(predictions_made)} predictions, {skipped} skipped.")
    return predictions_made


def apply_smart_scheduling(predictions: List[Dict], scheduler, conn=None) -> List[Dict]:
    """Enforce max 1 prediction per team per week.

    If a team appears in multiple fixtures, keep the earliest and schedule the rest
    as 'day_before_predict' tasks in the scheduler.
    """
    from Core.Utils.constants import now_ng

    team_seen = {}  # team_id -> earliest fixture date
    keep = []
    deferred = 0

    # Sort by date to ensure earliest is processed first
    sorted_preds = sorted(predictions, key=lambda p: p.get("date", ""))

    for pred in sorted_preds:
        home_id = pred.get("home_team_id", "")
        away_id = pred.get("away_team_id", "")

        # Check if either team already has a prediction this week
        home_conflict = home_id in team_seen
        away_conflict = away_id in team_seen

        if home_conflict or away_conflict:
            # Defer this prediction — schedule as day_before_predict
            try:
                fixture_date = pred.get("date", "")
                scheduler.schedule_day_before_predict(
                    fixture_id=pred.get("fixture_id", ""),
                    match_date=fixture_date,
                )
                deferred += 1
            except Exception as e:
                logger.warning(f"    [SmartSched] Failed to schedule deferred prediction: {e}")
            continue

        # Mark teams as seen
        if home_id:
            team_seen[home_id] = pred.get("date", "")
        if away_id:
            team_seen[away_id] = pred.get("date", "")
        keep.append(pred)

    if deferred > 0:
        print(f"    [SmartSched] Kept {len(keep)}, deferred {deferred} to scheduler (1-per-team-per-week).")

    return keep
