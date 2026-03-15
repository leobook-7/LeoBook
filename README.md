# LeoBook

**Developer**: Materialless LLC
**Chief Engineer**: Emenike Chinenye James
**Powered by**: Rule Engine + Neural RL Stairway Engine · Gemini Multi-Key (AIGO browser assistant + search enrichment only)
**Architecture**: v9.1 "Stairway Engine" (All files ≤500 lines · Fully Modular · Season-Aware RL Weighting)

---

## What Is LeoBook?

LeoBook is an **autonomous sports prediction and betting system** with two halves:

| Component     | Tech                               | Purpose                                                                                                                              |
| ------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `Leo.py`      | Python 3.12 + Playwright + PyTorch | Autonomous data extraction, **Rule Engine + Neural RL prediction** (no LLM), odds harvesting, automated bet placement, and dynamic task scheduling |
| `leobookapp/` | Flutter/Dart                       | Cross-platform dashboard with "Telegram-grade" UI density, Liquid Glass aesthetics, and real-time streaming                          |

**Leo.py** is an **autonomous orchestrator** powered by a **Supervisor-Worker Pattern** (`Core/System/supervisor.py`). Chapter/page execution functions live in `Core/System/pipeline.py`. The system enforces **Data Readiness Gates** (Prologue P1-P3) with **materialized readiness cache** for O(1) checks. **Data Quality & Season Completeness** are tracked autonomously with `CUP_FORMAT` and `data_richness_score` protecting the pipeline from phantom season completions and over-relying on the RL model before sufficient history exists. Cloud sync uses **watermark-based delta detection**.

For the complete file inventory and step-by-step execution trace, see [docs/LeoBook_Technical_Master_Report.md](docs/LeoBook_Technical_Master_Report.md).

---

## System Architecture (v9.1 — Fully Modular)

```
Leo.py (Entry Point — 469 lines)
├── Core/System/pipeline.py (Chapter/Page execution functions)
│   ├── Startup: Push-Only Sync → Supabase (auto-bootstrap)
│   ├── Prologue (Materialized Readiness Gates):
│   │   ├── P1: Quantity & ID Gate (O(1) lookup)
│   │   ├── P2: History & Quality Gate — Job A (blocks) + Job B (RL tier)
│   │   └── P3: AI Readiness Gate (O(1) lookup)
│   ├── Chapter 1 (Prediction Pipeline v9.0):
│   │   ├── Ch1 P1: URL Resolution & Direct Odds Harvesting
│   │   ├── Ch1 P2: Predictions (30-dim Stairway Engine: Rule + RL, season-aware)
│   │   └── Ch1 P3: Recommendations & Final Chapter Sync (Odds 1.20–4.00)
│   └── Chapter 2 (Betting Automation):
│       ├── Ch2 P1: Automated Booking
│       └── Ch2 P2: Funds & Withdrawal Check
└── Live Streamer: Isolated parallel task (60s updates + outcome review)
```

### Key Subsystems

- **Autonomous Task Scheduler**: Manages recurring tasks (Weekly enrichment, Monday 2:26am) and time-sensitive predictions (day-before match).
- **Data Readiness Gates**: Automated pre-flight checks with **Auto-Remediation** (30-minute timeout). P2 now has two jobs: Job A (internal consistency gate) + Job B (RL tier reporting: RULE_ENGINE / PARTIAL / FULL).
- **Season Completeness**: `CUP_FORMAT` status eliminates phantom COMPLETED seasons from cup finals/super cups. `data_richness_score` per league measures prior season depth.
- **Season-Aware RL Weighting**: `data_richness_score` [0.0, 1.0] scales `W_neural` dynamically. 0 prior seasons → `W_neural = 0.0` (pure Rule Engine). 3+ seasons → `W_neural = 0.3` (full configured weight). Score cached for 6h.
- **Standings VIEW**: High-performance standings computed directly from `schedules` via Postgres UNION ALL views. Zero storage, always fresh.
- **Data Leak Guard**: Max 1 prediction per team per week. Prevents predicting future matches before prerequisite results are known.
- **Neural RL Engine** (`Core/Intelligence/rl/`): v9.1 "Stairway Engine" using a **30-dimensional action space** and **Poisson-grounded imitation learning**. 3-phase PPO training split across `trainer.py`, `trainer_phases.py`, `trainer_io.py`.

### Core Modules

- **`Core/Intelligence/`** — AI engine (rule-based prediction, **neural RL engine**, adaptive learning, AIGO self-healing)
- **`Core/System/`** — **Task Scheduler**, **Data Readiness Checker**, **Bet Safety Guardrails**, lifecycle, withdrawal
- **`Modules/Flashscore/`** — Schedule extraction (`fs_league_enricher.py`), live score streaming, match data processing
- **`Modules/FootballCom/`** — Betting platform automation (login, odds, booking, withdrawal)
- **`Modules/Assets/`** — Asset sync: team crests, league crests, region flags (171 SVGs, 1,234 leagues)
- **`Data/Access/`** — **Computed Standings**, Supabase sync, season completeness, outcome review
- **`Scripts/`** — Shims + CLI tools (recommendation engine, search dictionary builder)
- **`leobookapp/`** — Flutter dashboard (Liquid Glass + Proportional Scaling)

---

## Supported Betting Markets

1X2 · Double Chance · Draw No Bet · BTTS · Over/Under · Goal Ranges · Correct Score · Clean Sheet · Asian Handicap · Combo Bets · Team O/U

---

## Project Structure

```
LeoBook/
├── Leo.py                     # Entry point (469 lines)
├── Core/
│   ├── System/
│   │   ├── pipeline.py        # Chapter/page execution functions (NEW v9.1)
│   │   ├── supervisor.py
│   │   ├── guardrails.py
│   │   ├── scheduler.py
│   │   ├── data_readiness.py  # P2 reports RL tier (v9.1)
│   │   ├── data_quality.py
│   │   ├── gap_resolver.py
│   │   └── withdrawal_checker.py
│   ├── Intelligence/
│   │   ├── prediction_pipeline.py
│   │   ├── ensemble.py        # data_richness_score RL weighting (v9.1)
│   │   ├── rule_engine.py
│   │   ├── rule_engine_manager.py
│   │   └── rl/
│   │       ├── trainer.py
│   │       ├── trainer_phases.py  # NEW v9.1 — reward functions
│   │       ├── trainer_io.py      # NEW v9.1 — save/load/checkpoint
│   │       ├── feature_encoder.py
│   │       └── market_space.py
│   └── Utils/
│       └── constants.py
├── Modules/
│   ├── Flashscore/
│   │   ├── fs_league_enricher.py   # NEW v9.1 (was Scripts/enrich_leagues.py)
│   │   ├── fs_league_extractor.py  # NEW v9.1
│   │   ├── fs_league_hydration.py  # NEW v9.1
│   │   ├── fs_league_images.py     # NEW v9.1
│   │   ├── fs_live_streamer.py
│   │   ├── fs_extractor.py
│   │   └── fs_utils.py
│   ├── FootballCom/
│   │   ├── fb_manager.py
│   │   ├── navigator.py
│   │   ├── odds_extractor.py
│   │   └── booker/
│   │       ├── placement.py
│   │       └── booking_code.py
│   └── Assets/
│       ├── asset_manager.py        # Reads from SQLite (v9.1 — CSV removed)
│       ├── football_logos.py       # NEW v9.1 (was Scripts/football_logos.py)
│       └── logo_downloader.py      # NEW v9.1
├── Data/
│   ├── Access/
│   │   ├── league_db.py            # Façade (1092 lines)
│   │   ├── league_db_schema.py     # NEW v9.1 — schema + migrations
│   │   ├── db_helpers.py           # 596 lines
│   │   ├── market_evaluator.py     # NEW v9.1
│   │   ├── paper_trade_helpers.py  # NEW v9.1
│   │   ├── gap_scanner.py          # 424 lines
│   │   ├── gap_models.py           # NEW v9.1 — ColumnSpec, GapReport
│   │   ├── sync_manager.py         # 470 lines
│   │   ├── sync_schema.py          # NEW v9.1 — TABLE_CONFIG, _COL_REMAP
│   │   ├── season_completeness.py  # CUP_FORMAT + data_richness_score (v9.1)
│   │   ├── supabase_client.py
│   │   ├── metadata_linker.py
│   │   └── outcome_reviewer.py
│   └── Store/
│       ├── leobook.db
│       ├── leagues.json
│       ├── country.json
│       ├── ranked_markets_likelihood_updated_with_team_ou.json
│       └── models/
└── Scripts/
    ├── enrich_leagues.py           # Shim → Modules/Flashscore/fs_league_enricher
    ├── football_logos.py           # Shim → Modules/Assets/football_logos
    ├── recommend_bets.py
    ├── build_search_dict.py        # 589 lines
    ├── search_dict_llm.py          # NEW v9.1
    └── rl_diagnose.py
```

---

## LeoBook App (Flutter)

The app implements a **Telegram-inspired high-density aesthetic** optimized for visual clarity and real-time data response.

- **Proportional Scaling System** — Custom system ensures perfect parity across all device sizes.
- **Computed Standings** — The app queries the `computed_standings` VIEW for live-accurate tables.
- **Liquid Glass UI** — Premium frosted-glass design with micro-radii (14dp).
- **4-Tab Match System** — Real-time 2.5hr status propagation and Supabase streaming.

---

## Quick Start (v9.1)

### Backend (Leo.py)

```bash
# Setup
pip install -r requirements.txt
pip install -r requirements-rl.txt  # Core RL/AI dependencies
playwright install chromium
bash .devcontainer/setup.sh         # Auto-config system environment

# Execution
python Leo.py              # Autonomous Orchestrator (Full dynamic cycle)
python Leo.py --sync        # Push local changes to Supabase
python Leo.py --pull        # Pull ALL from Supabase → local SQLite (recovery)
python Leo.py --prologue    # Data readiness check (P1-P3)
python Leo.py --chapter 1   # Prediction pipeline (Odds → Predict → Sync)
python Leo.py --chapter 2   # Betting automation
python Leo.py --review      # Outcome review (Finished matches)
python Leo.py --recommend   # Recommendations generation
python Leo.py --streamer    # Standalone Live Multi-Tasker (Scores/Review/Reports)
python Leo.py --data-quality             # Gap scan + Invalid ID resolution + Completeness init
python Leo.py --season-completeness      # Show summary of league-season coverage (CUP_FORMAT/ACTIVE/COMPLETED)
python Leo.py --bypass-cache             # Skip readiness_cache for O(N) gate scan
python Leo.py --set-expected-matches <id> <season> <num>  # Manual override for P2 logic
python Leo.py --enrich-leagues           # Smart gap scan (only leagues with missing data)
python Leo.py --enrich-leagues --limit 5  # Gap scan first 5 leagues
python Leo.py --enrich-leagues --seasons 2  # Extract last 2 seasons (builds RL history)
python Leo.py --enrich-leagues --reset   # Full reset: re-enrich ALL leagues
python Leo.py --assets       # Sync team/league crests + region flags to Supabase
python Leo.py --logos        # Download football logo packs
python Leo.py --train-rl     # Chronological RL model training
python Leo.py --rule-engine --backtest  # Progressive backtest with default engine
python Leo.py --dry-run      # Full pipeline in dry-run mode (no real bets)
python Leo.py --help         # Comprehensive CLI command catalog

# Flags-only sync (without team/league crest sync)
python -m Modules.Assets.asset_manager --flags
```

#### Emergency Controls

```bash
# Create kill switch (immediately halts all betting)
echo stop > STOP_BETTING

# Remove kill switch (resume betting)
del STOP_BETTING

# Check stairway state
python -c "from Core.System.guardrails import StaircaseTracker; print(StaircaseTracker().status())"
```

---

## Environment Variables

| Variable                   | Purpose                                             |
| -------------------------- | --------------------------------------------------- |
| `GEMINI_API_KEY`           | Multi-key rotation for AI analysis                  |
| `GROK_API_KEY`             | Grok API key (search dict LLM enrichment)           |
| `SUPABASE_URL`             | Supabase endpoint                                   |
| `SUPABASE_SERVICE_KEY`     | Backend service key (Admin)                         |
| `FB_PHONE` / `FB_PASSWORD` | Betting platform credentials                        |
| `LEO_CYCLE_WAIT_HOURS`     | Default sleep between autonomous tasks (default: 6) |
| `KILL_SWITCH_FILE`         | Path to kill switch file (default: `STOP_BETTING`)  |
| `MIN_BALANCE_BEFORE_BET`   | Minimum balance before betting (default: ₦500)      |
| `DAILY_LOSS_LIMIT`         | Max daily loss before halt (default: ₦5,000)        |
| `STAIRWAY_SEED`            | Step 1 stake amount (default: ₦1,000)               |

---

## Documentation

| Document | Purpose |
| -------- | ------- |
| [docs/RULEBOOK.md](docs/RULEBOOK.md) | **MANDATORY** — Engineering standards & philosophy |
| [docs/PROJECT_STAIRWAY.md](docs/PROJECT_STAIRWAY.md) | Capital compounding strategy — the "why" behind LeoBook |
| [docs/LeoBook_Technical_Master_Report.md](docs/LeoBook_Technical_Master_Report.md) | File inventory, execution flow, safety guardrails, observability |
| [docs/leobook_algorithm.md](docs/leobook_algorithm.md) | Algorithm reference (Rule Engine + Neural RL) |
| [docs/AIGO_Learning_Guide.md](docs/AIGO_Learning_Guide.md) | Self-healing extraction pipeline |
| [docs/SUPABASE_SETUP.md](docs/SUPABASE_SETUP.md) | Supabase schema, storage buckets, deployed views |
| [docs/RL_First_Principles_Data_Audit.md](docs/RL_First_Principles_Data_Audit.md) | RL data readiness audit + tier advancement guide |
| [docs/reports archive/LEOBOOK_FILE_MAP.md](docs/reports%20archive/LEOBOOK_FILE_MAP.md) | Chapter/page file dependency map (v9.1) |

---

*Last updated: 2026-03-15 — v9.1 "Stairway Engine" — Fully modular, season-aware RL, 14 bugs fixed*
*LeoBook Engineering Team — Materialless LLC*
