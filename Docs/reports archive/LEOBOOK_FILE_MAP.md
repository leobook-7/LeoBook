# LeoBook — Chapter & Page File Dependency Map

> **Version**: 9.1 · **Last Updated**: 2026-03-15
> Reflects post-modularisation structure. All files ≤500 lines.
> Previous version: 8.0 (pre-split monoliths)

---

## Structural Changes (v8 → v9)

| Old File | New Location(s) |
|---|---|
| `Scripts/enrich_leagues.py` (1680L) | `Modules/Flashscore/fs_league_enricher.py` + 3 sub-modules + shim at original path |
| `Scripts/football_logos.py` (804L) | `Modules/Assets/football_logos.py` + `Modules/Assets/logo_downloader.py` + shim |
| `Leo.py` (799L) | `Leo.py` (469L) + `Core/System/pipeline.py` (NEW) |
| `Data/Access/league_db.py` (1458L) | `league_db.py` (1092L) + `league_db_schema.py` (NEW) |
| `Data/Access/db_helpers.py` (903L) | `db_helpers.py` (596L) + `market_evaluator.py` (NEW) + `paper_trade_helpers.py` (NEW) |
| `Data/Access/gap_scanner.py` (785L) | `gap_scanner.py` (424L) + `gap_models.py` (NEW) |
| `Data/Access/sync_manager.py` (699L) | `sync_manager.py` (470L) + `sync_schema.py` (NEW) |
| `Core/Intelligence/rl/trainer.py` (979L) | `trainer.py` (832L) + `trainer_io.py` (NEW) + `trainer_phases.py` (NEW) |
| `Scripts/build_search_dict.py` (835L) | `build_search_dict.py` (589L) + `search_dict_llm.py` (NEW) |

---

## Startup — DB Initialization & Push-Only Sync

**Trigger**: `python Leo.py` (automatic before every run)

| File | Role |
|---|---|
| `Leo.py` | Entry point — routes to `Core/System/pipeline.py` |
| `Core/System/pipeline.py` | `run_startup_sync()` |
| `Data/Access/league_db.py` | `init_db()` — creates all SQLite tables if missing |
| `Data/Access/league_db_schema.py` | Schema DDL, migrations, `get_connection()` |
| `Data/Access/db_helpers.py` | `init_csvs()` |
| `Data/Access/sync_manager.py` | `SyncManager.sync_on_startup()` |
| `Data/Access/sync_schema.py` | `TABLE_CONFIG`, `SUPABASE_SCHEMA`, `_COL_REMAP`, `_ALLOWED_COLS` |
| `Data/Access/supabase_client.py` | Supabase connection factory |
| `Core/System/lifecycle.py` | `log_state()`, `parse_args()`, `state` |
| `Data/Store/leobook.db` | SQLite database |

---

## Prologue P1 — Data Readiness: Leagues & Teams

**Trigger**: `python Leo.py --prologue --page 1`

| File | Role |
|---|---|
| `Core/System/pipeline.py` | `run_prologue_p1()` |
| `Core/System/data_readiness.py` | `check_leagues_ready()` |
| `Data/Access/league_db.py` | `get_connection()`, `get_leagues_with_gaps()` |
| `Modules/Flashscore/fs_league_enricher.py` | Auto-remediation: `main(limit=100)` |
| `Data/Access/gap_scanner.py` | `GapScanner` |
| `Data/Access/gap_models.py` | `ColumnSpec`, `ColumnGap`, `GapReport` |
| `Data/Store/leagues.json` | Expected league count |
| `Data/Access/db_helpers.py` | `log_audit_event()` |

---

## Prologue P2 — Data Readiness: Historical Seasons

**Trigger**: `python Leo.py --prologue --page 2`

| File | Role |
|---|---|
| `Core/System/pipeline.py` | `run_prologue_p2()` |
| `Core/System/data_readiness.py` | `check_seasons_ready()` — Job A (gate) + Job B (RL tier) |
| `Data/Access/season_completeness.py` | `SeasonCompletenessTracker`, `CUP_FORMAT` status, `get_data_richness_score()` |
| `Data/Access/league_db.py` | `get_leagues_missing_seasons()` |
| `Modules/Flashscore/fs_league_enricher.py` | Auto-remediation: `main(num_seasons=2)` |
| `Data/Access/gap_scanner.py` | `GapScanner` |
| `Data/Access/gap_models.py` | `GapReport` |
| `Data/Access/db_helpers.py` | `log_audit_event()` |

**P2 Output (v9.1):**
```
[Seasons] N league-seasons computed
  - COMPLETED:   N (0 mismatches)      ← Job A gate
  - ACTIVE:      N (avg X% coverage)
  - CUP_FORMAT:  (excluded from gate)  ← never phantom-COMPLETED
[History] N leagues have prior season data
  - RL tier: RULE_ENGINE / PARTIAL / FULL  ← Job B informational
```

---

## Prologue P3 — RL Adapter Check (currently disabled)

**Trigger**: `python Leo.py --prologue --page 3`

| File | Role |
|---|---|
| `Core/System/pipeline.py` | `run_prologue_p3()` — prints skip message |
| `Core/System/data_readiness.py` | `check_rl_ready()` (dormant) |
| `Core/Intelligence/rl/trainer.py` | `RLTrainer` (would be called on remediation) |

---

## Chapter 1 Page 1 — URL Resolution & Odds Harvesting

**Trigger**: `python Leo.py --chapter 1 --page 1`

| File | Role |
|---|---|
| `Core/System/pipeline.py` | `run_chapter_1_p1(p)` |
| `Modules/FootballCom/fb_manager.py` | `run_odds_harvesting(p)` |
| `Modules/FootballCom/odds_extractor.py` | Per-market odds extraction |
| `Modules/FootballCom/navigator.py` | Page navigation, login, balance |
| `Modules/FootballCom/booker/booking_code.py` | Booking code extraction |
| `Modules/FootballCom/booker/placement.py` | Bet placement logic |
| `Data/Access/league_db.py` | `upsert_fb_match()`, `upsert_match_odds_batch()` |
| `Data/Access/sync_schema.py` | `TABLE_CONFIG` |
| `Data/Store/leobook.db` | `fb_matches`, `match_odds` written |
| `Data/Store/ranked_markets_likelihood_updated_with_team_ou.json` | Market likelihood data |
| `Data/Access/db_helpers.py` | `log_audit_event()` |

---

## Chapter 1 Page 2 — Predictions (Rule Engine + RL Ensemble)

**Trigger**: `python Leo.py --chapter 1 --page 2`

| File | Role |
|---|---|
| `Core/System/pipeline.py` | `run_chapter_1_p2()` |
| `Core/Intelligence/prediction_pipeline.py` | `run_predictions()` |
| `Core/Intelligence/rule_engine.py` | Rule Engine logic |
| `Core/Intelligence/rule_engine_manager.py` | Engine registry + default selection |
| `Core/Intelligence/ensemble.py` | `EnsembleEngine.merge(data_richness_score=...)` |
| `Core/Intelligence/rl/trainer.py` | `RLTrainer` — `__init__`, `train_step`, `train_from_fixtures` |
| `Core/Intelligence/rl/trainer_phases.py` | Phase 1/2/3 reward functions (mixin) |
| `Core/Intelligence/rl/trainer_io.py` | Save/load/checkpoint management (mixin) |
| `Core/Intelligence/rl/feature_encoder.py` | Feature vector construction |
| `Core/Intelligence/rl/market_space.py` | 30-dim action space |
| `Core/Intelligence/progressive_backtester.py` | Progressive backtest runner |
| `Core/Intelligence/selector_manager.py` | CSS selector registry |
| `Data/Access/league_db.py` | `upsert_prediction()`, `computed_standings()` |
| `Data/Access/league_db_schema.py` | `computed_standings()` SQL |
| `Data/Access/season_completeness.py` | `get_data_richness_score()` → scales `W_neural` |
| `Data/Access/metadata_linker.py` | Links fixture metadata |
| `Data/Store/leobook.db` | `schedules`, `predictions`, `match_odds` |
| `Data/Store/models/leobook_base.pth` | Trained RL model weights |
| `Data/Store/models/checkpoints/` | Phase checkpoint files |
| `Core/System/scheduler.py` | `TaskScheduler` |
| `Core/System/guardrails.py` | Pre-bet checks, staircase |
| `Data/Access/db_helpers.py` | `log_audit_event()` |

**Season-Aware RL (v9.1):** `EnsembleEngine.get_richness_score(league_id)` is called per match.
`W_neural = W_neural_base × data_richness_score` (0.0–1.0, cached 6h).

---

## Chapter 1 Page 3 — Recommendations & Final Sync

**Trigger**: `python Leo.py --chapter 1 --page 3`

| File | Role |
|---|---|
| `Core/System/pipeline.py` | `run_chapter_1_p3()` |
| `Scripts/recommend_bets.py` | `get_recommendations()` |
| `Data/Store/ranked_markets_likelihood_updated_with_team_ou.json` | Market likelihood data |
| `Data/Access/sync_manager.py` | `run_full_sync()` |
| `Data/Access/sync_schema.py` | `TABLE_CONFIG`, `_COL_REMAP` |
| `Data/Access/league_db.py` | `get_connection()` |
| `Data/Store/leobook.db` | `predictions` read |
| `Data/Access/db_helpers.py` | `log_audit_event()` |

---

## Chapter 2 Page 1 — Automated Booking

**Trigger**: `python Leo.py --chapter 2 --page 1`
**Gate**: `Core/System/guardrails.py` — `run_all_pre_bet_checks()` must pass.

| File | Role |
|---|---|
| `Core/System/pipeline.py` | `run_chapter_2_p1(p)` |
| `Core/System/guardrails.py` | `run_all_pre_bet_checks()`, `StaircaseTracker` |
| `Modules/FootballCom/fb_manager.py` | `run_automated_booking(p)` |
| `Modules/FootballCom/booker/placement.py` | Bet placement + confirm |
| `Modules/FootballCom/booker/booking_code.py` | Booking code extraction |
| `Modules/FootballCom/navigator.py` | Login, navigation |
| `Data/Access/league_db.py` | Updates `fb_matches.booking_status` |
| `Data/Access/sync_manager.py` | `run_full_sync()` post-booking |
| `Data/Access/db_helpers.py` | `log_audit_event()` |
| `Data/Store/leobook.db` | `fb_matches`, `predictions`, `audit_log` |

---

## Chapter 2 Page 2 — Funds & Withdrawal Check

**Trigger**: `python Leo.py --chapter 2 --page 2`

| File | Role |
|---|---|
| `Core/System/pipeline.py` | `run_chapter_2_p2(p)` |
| `Core/System/withdrawal_checker.py` | `check_triggers()`, `execute_withdrawal()` |
| `Modules/FootballCom/navigator.py` | `extract_balance()` |
| `Data/Access/sync_manager.py` | `run_full_sync()` |
| `Data/Access/db_helpers.py` | `log_audit_event()` |
| `Data/Store/leobook.db` | `audit_log` updated |

---

## Utility Commands

### `--sync` / `--pull`
| File | Role |
|---|---|
| `Data/Access/sync_manager.py` | `SyncManager`, `run_full_sync()`, `batch_pull()` |
| `Data/Access/sync_schema.py` | `TABLE_CONFIG`, `SUPABASE_SCHEMA`, `_COL_REMAP` |
| `Data/Access/supabase_client.py` | Connection |
| `Data/Access/league_db.py` | All tables |

### `--enrich-leagues` — Flashscore Enrichment
| File | Role |
|---|---|
| `Modules/Flashscore/fs_league_enricher.py` | Orchestrator — `main()`, `enrich_single_league()`, CLI |
| `Modules/Flashscore/fs_league_extractor.py` | JS strings, `extract_tab()`, `_backfill_schedule_crests()`, season helpers |
| `Modules/Flashscore/fs_league_hydration.py` | `_wait_for_page_hydration()`, `_scroll_to_load()`, `_expand_show_more()` |
| `Modules/Flashscore/fs_league_images.py` | `_download_image()`, `upload_crest_to_supabase()`, executor |
| `Scripts/enrich_leagues.py` | One-line shim → `fs_league_enricher.main` |
| `Data/Access/league_db.py` | `upsert_league()`, `upsert_team()`, `bulk_upsert_fixtures()` |
| `Data/Access/gap_scanner.py` | `GapScanner` — international league suppression built in |
| `Data/Access/gap_models.py` | `GapReport`, `ColumnSpec`, `ColumnGap` |
| `Data/Access/db_helpers.py` | `propagate_crest_urls()`, `fill_all_country_codes()` |
| `Data/Store/leagues.json` | League seed data |
| `Modules/Assets/asset_manager.py` | Crest upload to Supabase Storage |

**Enrichment final pass order (v9.1):**
1. `GapResolver.resolve_immediate()`
2. `SeasonCompletenessTracker.bulk_compute_all()`
3. `propagate_crest_urls()`
4. `fill_all_country_codes(conn)`
5. **Final Supabase sync** (before gap scan)
6. Post-enrichment gap scan
7. Summary print

### `--train-rl`
| File | Role |
|---|---|
| `Core/Intelligence/rl/trainer.py` | `RLTrainer` — core class |
| `Core/Intelligence/rl/trainer_phases.py` | Phase 1/2/3 reward functions (mixin) |
| `Core/Intelligence/rl/trainer_io.py` | Save/load/checkpoint (mixin) |
| `Core/Intelligence/rl/feature_encoder.py` | Feature vector construction |
| `Core/Intelligence/rl/market_space.py` | 30-dim action space |
| `Core/Intelligence/rule_engine.py` | Expert policy for Phase 1 |
| `Data/Access/league_db.py` | `computed_standings()`, fixture queries |
| `Data/Store/models/` | Checkpoint output |

### `--backtest-rl`
| File | Role |
|---|---|
| `Core/Intelligence/rl/backtest.py` | `WalkForwardBacktester` |
| `Core/Intelligence/rl/trainer.py` | Training loop per window |
| `Core/Intelligence/progressive_backtester.py` | Progressive backtest |
| `Data/Access/league_db.py` | Historical fixture queries |

### `--diagnose-rl`
| File | Role |
|---|---|
| `Scripts/rl_diagnose.py` | RL diagnosis report |
| `Core/Intelligence/rl/trainer.py` | Model loading |
| `Core/Intelligence/rl/feature_encoder.py` | Feature construction |
| `Data/Store/models/` | Model weights |

### `--assets`
| File | Role |
|---|---|
| `Modules/Assets/asset_manager.py` | `sync_team_assets()`, `sync_league_assets()`, `sync_region_flags()` |
| `Data/Access/supabase_client.py` | Storage client |
| `Data/Access/league_db.py` | `get_connection()`, `DB_DIR` ← **import from here, not db_helpers** |
| `Data/Store/crests/` | Local crest images |
| `Modules/Assets/flag-icons-main/` | SVG flag library (171 SVGs uploaded, 1,234 leagues) |

**Buckets:** `flags` (SVG icons), `team-crests` (PNG logos), `league-crests` (PNG logos)

### `--logos`
| File | Role |
|---|---|
| `Modules/Assets/football_logos.py` | `download_all_logos()`, `download_all_countries()` |
| `Modules/Assets/logo_downloader.py` | `_PlaywrightPool`, `_download_country()`, `_download_league_zip()` |
| `Scripts/football_logos.py` | One-line shim |

### `--data-quality`
| File | Role |
|---|---|
| `Core/System/data_quality.py` | `DataQualityScanner`, `InvalidIDScanner` |
| `Core/System/gap_resolver.py` | `GapResolver` |
| `Data/Access/season_completeness.py` | `SeasonCompletenessTracker` — `CUP_FORMAT`, `get_data_richness_score()` |
| `Data/Access/db_helpers.py` | `fill_all_country_codes()` — Pass 1 (name lookup) + Pass 2 (club cross-ref) |
| `Data/Access/gap_scanner.py` | `GapScanner` |
| `Data/Access/gap_models.py` | `GapReport` |

### `--streamer`
| File | Role |
|---|---|
| `Modules/Flashscore/fs_live_streamer.py` | `live_score_streamer()` |
| `Modules/Flashscore/fs_extractor.py` | Score extraction |
| `Data/Access/league_db.py` | `upsert_live_score()` |
| `Data/Access/sync_manager.py` | Delta sync |

### `--recommend`
| File | Role |
|---|---|
| `Scripts/recommend_bets.py` | `get_recommendations()` |
| `Data/Store/ranked_markets_likelihood_updated_with_team_ou.json` | Market likelihood data |
| `Data/Access/league_db.py` | `predictions` + `match_odds` queries |

### `--rule-engine`
| File | Role |
|---|---|
| `Core/Intelligence/rule_engine_manager.py` | `RuleEngineManager` |
| `Core/Intelligence/progressive_backtester.py` | `run_progressive_backtest()` |
| `Core/Intelligence/rule_engine.py` | Rule logic |

### `--paper-summary`
| File | Role |
|---|---|
| `Data/Access/paper_trade_helpers.py` | `get_paper_trading_summary()` |
| `Data/Access/db_helpers.py` | Re-exports for backward compat |
| `Data/Access/league_db.py` | `paper_trades` queries |

### `--build-search-dict`
| File | Role |
|---|---|
| `Scripts/build_search_dict.py` | `main()`, DB write operations |
| `Scripts/search_dict_llm.py` | `query_llm_for_metadata()`, `_build_prompt()`, `_call_llm()` |

---

## Autonomous Supervisor Loop (`python Leo.py`)

| File | Role |
|---|---|
| `Core/System/supervisor.py` | `Supervisor.run()` — master cycle |
| `Core/System/pipeline.py` | All chapter/page execution functions |
| `Core/System/lifecycle.py` | State machine, logging, args |
| `Core/System/scheduler.py` | `TaskScheduler` |
| `Core/System/guardrails.py` | Safety gates, staircase tracker |
| `Core/System/data_readiness.py` | P1-P3 gates |
| `Core/System/gap_resolver.py` | Continuous gap resolution |
| `Core/System/withdrawal_checker.py` | Withdrawal triggers |

---

## Shared Infrastructure (used by every chapter)

| File | Role |
|---|---|
| `Core/Utils/constants.py` | `now_ng()`, timezone |
| `Core/Intelligence/aigo_suite.py` | `@aigo_retry`, AIGO logging |
| `Data/Access/league_db.py` | All SQLite operations |
| `Data/Access/league_db_schema.py` | Schema, `get_connection()`, `init_db()` |
| `Data/Access/db_helpers.py` | `log_audit_event()`, `init_csvs()` |
| `Data/Access/supabase_client.py` | Supabase connection singleton |
| `Data/Store/leobook.db` | The database |
| `Data/Store/leagues.json` | League seed data (1,281 leagues) |
| `Data/Store/country.json` | ISO country codes + flag paths (271 countries) |
| `Data/Store/ranked_markets_likelihood_updated_with_team_ou.json` | Market likelihood data |
| `.env` | `GROK_API_KEY`, `GEMINI_API_KEY`, `FB_PHONE`, `FB_PASSWORD`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` |

---

## Bugs Fixed During Modularisation (2026-03-14/15)

| # | Bug | File Fixed | Fix |
|---|---|---|---|
| 1 | `DB_DIR` imported from `db_helpers` — never existed there | `Modules/Assets/asset_manager.py` | Import from `Data.Access.league_db` |
| 2 | `ranked_markets_likelihood_updated_with_team_ou.json` moved to `docs/` then `root` | `Data/Store/` | Moved to `Data/Store/`, path refs updated in `recommend_bets.py` + `odds_extractor.py` |
| 3 | `home_team_name`/`away_team_name` silently NULL in Supabase | `Data/Access/sync_manager.py` | Added to `_COL_REMAP` |
| 4 | Empty string overwrites real `country_code` (concurrent workers) | `Data/Access/league_db.py` | `NULLIF` guard in `upsert_team()` |
| 5 | `country_code` false-positive gaps on international leagues | `Data/Access/gap_scanner.py` | `_is_international_league()` suppression |
| 6 | All 1,716 teams missing `country_code` (international-only DB state) | `Data/Access/db_helpers.py` | `fill_national_team_country_codes()` + `fill_club_team_country_codes()` |
| 7 | 118 phantom COMPLETED seasons (fallback sets expected=scanned for <4-team leagues) | `Data/Access/season_completeness.py` | `CUP_FORMAT` status — 244 cup competitions correctly classified, 0 phantom completions |
| 8 | P2 gate had no concept of historical depth — always passed READY on single-season DB | `Core/System/data_readiness.py` | P2 split into Job A (consistency gate) + Job B (RL tier: RULE_ENGINE / PARTIAL / FULL) |
| 9 | RL weight fixed regardless of history depth | `Core/Intelligence/ensemble.py` | `data_richness_score` scales `W_neural`: 0 prior seasons → 0.0, 3+ seasons → 0.3 |
| 10 | Final Supabase sync ran after gap scan — Supabase stale during post-enrichment report | `Scripts/enrich_leagues.py` | Final sync moved before `[GapScan]` print |
| 11 | `sync_team_assets` + `sync_league_assets` read from deleted CSV files → crash | `Modules/Assets/asset_manager.py` | Rewritten to read from SQLite teams/leagues tables |
| 12 | `sync_region_flags` read `region_league.csv`, missed 1,160 domestic leagues | `Modules/Assets/asset_manager.py` | Rewritten to read SQLite — 1,234 leagues updated, 171 SVGs uploaded |
| 13 | 6 stale local crest paths in teams table (files deleted post-upload) | `Data/Store/leobook.db` | NULLed via direct SQL, synced to Supabase |
| 14 | 6 Welsh leagues had no flag (`country_code=wal`, SVG is `gb-wls`) | `Modules/Assets/asset_manager.py` | Added `WAL → gb-wls` to `REGION_TO_ISO_OVERRIDES` |

---

## Commit History (2026-03-14/15)

| Hash | Description |
|---|---|
| `0ed8e43` | Modularisation complete — all 12 prompts, 6/6 smoke tests green |
| `f883c23` | Fix `asset_manager` `DB_DIR` import + restore `ranked_markets` json to root |
| `228f1a6` | Move `ranked_markets` json to `Data/Store/` |
| `88097f0` | Country code resolution — national teams + clubs |
| `702e1bf` | `sync_region_flags` rewrite — 1,234 leagues updated, 171 SVGs |
| `4708795` | `sync_team_assets` + `sync_league_assets` rewritten to SQLite |
| `d60d1c1` | Season-aware RL weighting + `CUP_FORMAT` completeness fix |
| DB-only | Stale crest NULL + sync (no code change) |

---

*LeoBook Engineering — Materialless LLC · 2026-03-15 · v9.1*
