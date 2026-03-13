# LeoBook Developer RuleBook v8.1

> **This document is LAW.** Every developer and AI agent working on LeoBook MUST follow these rules without exception. Violations will break the system.

---

## 1. First Principles

Before writing ANY code, ask in this exact order:

1. **Question** — Is this feature/change actually needed? What problem does it solve?
2. **Delete** — Can existing code be removed instead of adding more?
3. **Simplify** — What is the simplest possible implementation?
4. **Accelerate** — Can this run concurrently or be parallelized?
5. **Automate** — Can Leo.py orchestrate this without human intervention?

**Summary of First Principles Thinking** - **Question** every requirements and make it less dumb and **delete** those that are dumb and useless(no "incase-we-need-it" ideology), then **simplify**, **accelerate** and **automate** all those that remain. This is MUSK do, throughout the entire **LeoBook** codebase — local and cloud.

---

## 2. Backend Architecture (Python)

### 2.1 Leo.py Is an Autonomous Orchestrator

- **`Leo.py` is an AUTONOMOUS ORCHESTRATOR** — it contains ZERO business logic.
- It is powered by a **Supervisor-Worker Pattern** (`Core/System/supervisor.py`).
- Isolated chapter workers (`Core/System/pipeline_workers.py`) handle execution, failure recovery, and state persistence.
- Every script MUST be callable via `Leo.py` CLI flags.
- **Cycle Control**: The `Supervisor` decides when to wake up based on `scheduler`. Monolithic `while True` loops are forbidden.

### 2.2 Startup Bootstrapping (MANDATORY)

Every entry point (`main()`) MUST call `await run_startup_sync()`. This function ensures:
1. SQLite → Supabase **push-only sync** (only local rows modified since last watermark are pushed — ZERO reads from Supabase).
2. Auto-bootstrap: if local DB is empty, pulls all data from Supabase (one-time).
3. Local database and Supabase table existence.
Operations MUST NOT start (including live streamer) until startup sync completes successfully.

**Manual recovery**: `python Leo.py --pull` pulls ALL data from Supabase → local SQLite.

**Sync runs ONLY in Ch1 P3** (Final Sync). Ch1 P1 and Ch1 P2 do NOT sync — sync is consolidated to end of pipeline.

### 2.3 Data Readiness Gates (Prologue)

Leo.py operates via three sequential gates to ensure data integrity:

1. **Prologue P1 (Quantity & ID Gate)**: Leagues >= 90% coverage AND Teams >= 3 per league (v7.1). Validates Flashscore IDs — fails if invalid ID rate > 5%. GapScanner feeds this gate: P1 fails if `critical` gap count across `leagues` or `teams` tables exceeds threshold.
2. **Prologue P2 (History & Quality Gate)**: Minimum 2+ distinct seasons of fixture data per league.
    - **Logic**: Gate passes if 0 critical gaps AND 0 completed season mismatches. **ACTIVE seasons never block the gate.**
    - GapScanner feeds this gate: `schedules` table gaps at `critical` severity are reported per `(league_id, season)` pair. Only completed seasons with critical gaps block P2.
3. **Prologue P3 (AI Gate)**: RL Adapters must be trained and ready.

**Readiness Cache (Materialized)**:
- Gate checks MUST read from `readiness_cache` in the DB for O(1) lookup.
- The cache is updated after every successful scan or via `--bypass-cache` for forced re-scan.

**Auto-Remediation**: If a gate fails, Leo.py triggers the relevant enrichment or training script automatically (`auto_remediate`) with a **30-minute timeout**. If remediation exceeds the budget, the system logs a warning and proceeds with available data. The pipeline is never blocked indefinitely by auto-remediation.

### 2.4 Pipeline Structure (v8.0)

```
Startup Sync: Push-only (local → Supabase, auto-bootstrap if empty)
Task Scheduler: Execute pending tasks (Weekly Enrichment, predictions)
Prologue (Data Gates):
    P1: League/Team Thresholds (90% / 5 teams) — fed by GapScanner
    P2: Historical Data Check (2+ Seasons) — fed by GapScanner
    P3: AI RL Adapter Readiness (Phase Auto-Detection)
Chapter 1:
    P1: URL Resolution & Odds Harvesting (v9.0 — Direct Harvesting, no login)
    P2: Prediction Pipeline (30-dim Stairway Engine — Rule Engine + Poisson RL)
        - DATA LEAK GUARD: Max 1 prediction per team per week.
          This prevents the model from using future match data to predict
          earlier matches. A team's next match can only be predicted once
          their most recent match result is known.
        - Surplus matches are queued as 'day_before_predict' tasks.
    P3: Final Recommendations & Sync (Stairway Gate: 1.20–4.00 odds)
Chapter 2:
    P1: Automated Booking (Football.com)
    P2: Funds & Withdrawal Check
Live Streamer: Isolated parallel task — Live Scores + Outcome Review + Accuracy Reports
```

### 2.5 Standings Table Is FORBIDDEN

- **Rule**: No persistent `standings` table allowed in SQLite or Supabase.
- **Implementation**: Standings MUST be computed on-the-fly via the `computed_standings` VIEW in Supabase or `computed_standings()` in `league_db.py`.
- **Reasoning**: Ensures zero-latency source-of-truth accuracy and removes redundant sync overhead.

### 2.6 File Headers (MANDATORY)

Every Python file MUST have this header format:

```python
# filename.py: One-line description of what this file does.
# Part of LeoBook <Component> — <Sub-component>
#
# Functions: func1(), func2(), func3()
# Called by: Leo.py (Chapter X Page Y) | other_module.py
```

### 2.7 No Dead Code

- No commented-out code blocks
- No unused imports
- No functions that are never called

### 2.8 Concurrency Rules

- **Max Concurrency**: strictly limited by `MAX_CONCURRENCY` in `.env`.
- **Sequential Integrity**: Inside each match worker, steps must remain SEQUENTIAL.
- **SQLite WAL**: Handles concurrent access. Never use manual locks for DB operations.
- **Live Streamer Isolation**: Streamer runs in its own Playwright instance with an isolated user data directory.

### 2.9 Timezone Consistency (Africa/Lagos)

- **Rule**: Every timestamp MUST use the Nigerian timezone (**Africa/Lagos**, UTC+1).
- **Tooling**: Use `Core.Utils.constants.now_ng()` for all time operations.
- **Rationale**: Football.com operates exclusively in Nigeria/Ghana (WAT timezone). Developer location is WAT. Cross-league timezone normalization (UTC/CET for European leagues) is planned but deferred to avoid complexity during current testing phase.
- **Edge case**: European daylight saving transitions may cause 1-hour misalignment in match time parsing during DST transition weeks.

### 2.10 High-Velocity Data Ingestion (Selective Enrichment)

- **Rule**: When dealing with massive datasets (>1,000 leagues), developers and agents SHOULD use **selective enrichment** via range limits and season targeting.
- **Implementation**:
    - Use `--limit START-END` to process specific chunks of the league list.
    - Use `--season N` to target the most recent historical season (N=1) rather than multiple seasons at once.
- **Reasoning**: Prevents memory exhaustion in constrained environments (e.g., Codespaces) and allows for distributed processing if multiple LeoBook instances are run in parallel.

### 2.11 Selector Compliance (Zero Hardcoded Selectors)

- **Rule**: ALL CSS selectors used for web scraping MUST be defined in `Config/knowledge.json` and accessed via `Core.Intelligence.selector_manager.SelectorManager`. **Zero hardcoded selectors** in Python or JavaScript code files.
- **Implementation**:
    - Define selectors under the appropriate context key in `knowledge.json` (e.g., `fs_league_page`, `fs_match_page`).
    - In Python, use `selector_mgr.get_all_selectors_for_context(CONTEXT)` to retrieve the full selector dict.
    - In JS evaluation, pass the selectors dict as an argument and reference keys (e.g., `s.breadcrumb_links`, `s.match_link`).
- **Reasoning**: Flashscore frequently changes class names and DOM structure. Centralizing selectors in one JSON file makes updates fast and auditable.

### 2.12 Data Quality & Invalid ID Resolution

- **Scanner**: `Data/Access/gap_scanner.py` (`GapScanner`) scans all three core tables — `leagues`, `teams`, `schedules` — at the individual cell level. Every gap is tracked to its originating `(league_id, season)` pair so enrichment is surgical, not wholesale.

- **Gap Severity Tiers** (canonical — used by GapScanner, Prologue gates, enrichment queue, and all documentation):
    - `critical` — blocks match resolution or prediction pipeline. Prologue P1/P2 will fail. Must fix before pipeline runs.
    - `important` — degrades app UX or crest display (e.g. missing team crests, `match_status`, `fs_league_id`). Should fix; does not block pipeline.
    - `enrichable` — nice to have; can be back-filled without a browser session (e.g. `time`, `league_stage`, `region_url`). Fix opportunistically.

- **Gap detection rules**:
    - `NULL` or empty string `""` → gap on all column types.
    - URL columns (`crest`, `home_crest`, `away_crest`, `region_url`) → any value not starting with `http` is a gap, including local paths (`Data/Store/...`).
    - Score columns (`home_score`, `away_score`) → NULL is allowed for `match_status = scheduled`; not a gap.

- **Crest URL integrity**:
    - Team and league crests MUST be Supabase Storage public URLs in all three tables. Local paths are never synced to Supabase DB.
    - `_backfill_schedule_crests()` runs immediately after every crest upload batch inside `extract_tab()` to propagate the Supabase URL from `teams.crest` into `schedules.home_crest` / `schedules.away_crest`.
    - `propagate_crest_urls()` runs before every 20% sync checkpoint AND at the end of every enrichment run as a final safety pass.

- **Invalid ID Detection**:
    - Patterns: `^[A-Z_]+$`, `UNKNOWN_*`, empty, or < 8 chars for teams.
    - Duplicates: Valid IDs overwrite placeholders; multiple valid rows are merged (dependency re-linking).

- **`enrichment_queue` priorities** (unchanged):
    - `Priority 1 (CRITICAL)`: Invalid IDs blocking remediation.
    - `Priority 5 (NORMAL)`: Missing metadata.
    - `Priority 10 (DEFERRED)`: Old season gaps.

- **CLI flags for gap-driven enrichment**:
    - `python -m Scripts.enrich_leagues` — default mode; runs GapScanner first, enriches only leagues/seasons with detected gaps.
    - `python -m Scripts.enrich_leagues --scan-only` — print gap report and exit without modifying data. Use for monitoring.
    - `python -m Scripts.enrich_leagues --min-severity critical` — only fix pipeline-blocking gaps.
    - `python -m Scripts.enrich_leagues --min-severity enrichable` — fix everything including minor columns.

### 2.13 Neuro-Symbolic Ensemble (Intelligence v8.0 "Stairway Engine")

- **Rule**: Predictions MUST combine Rule Engine (Symbolic) and RL (Neural) signals over a **30-dimensional action space**.
- **Action Space**: Defined in `Core/Intelligence/rl/market_space.py` (Single Source of Truth). Includes 1X2, DC, OU (1.5, 2.5, 3.5), BTTS, and no_bet.
- **Expert Signal**: Phase 1 uses a **Poisson-grounded signal** derived from xG (1.20 home / 0.82 away multiplier) as the ground truth for imitation learning.
- **Stairway Gate**: Final output must pass the odds gate (1.20 ≤ odds ≤ 4.00) and an EV-positive check before recommendation.
- **Weights**: Default `W_symbolic=0.7`, `W_neural=0.3`. Overridable per-league in `ensemble_weights.json`.
- **Symbolic Baseline**: If `RL_Conf < 0.3` or model failure, fallback to `100% Rule Engine`. The system MUST NOT place bets based purely on low-confidence neural signals.
- **Phase guidance**: The Rule Engine is the reliability backbone throughout Phase 1. RL weights should only be increased beyond 0.3 after Phase 2 completes and RL accuracy on odds-grounded data is verified to exceed the Rule Engine baseline on high-volume days (≥ 200 matches).

---

## 3. Frontend Architecture (Flutter/Dart)

### 3.1 Constraints-Based Design (NO HARDCODED VALUES)

**The single most important rule:** Never use fixed `double` values (like `width: 300`) for layout-critical elements.

Use these widgets instead:
- `LayoutBuilder` — adapt widget trees based on parent `maxWidth`
- `Flexible` / `Expanded` — prevent overflow
- `FractionallySizedBox` — size as percentage
- `AspectRatio` — proportions
- `Responsive.sp(context, value)` — scaled spacing

### 3.2 Screens dispatch, Widgets render

Screens are pure dispatchers (`LayoutBuilder` / `Responsive.isDesktop()`). They contain ZERO rendering logic for components.

### 3.3 State Management

- Use `flutter_bloc` / `Cubit` exclusively.
- **NO RIVERPOD, NO GETX.**

---

## 4. Maintenance & Verification

### 4.1 Weekly Enrichment (Monday 2:26 AM)

The scheduler MUST trigger `enrich_leagues.py --weekly` every Monday. This mode is lightweight (`MAX_SHOW_MORE=2`) and focuses on schedule updates and missing metadata.

Before enrichment runs, the scheduler MUST also invoke `enrich_leagues.py --scan-only` and persist the gap report to `readiness_cache`. This gives the Prologue gates an up-to-date gap count without triggering a full enrichment pass.

### 4.2 Before Every Commit

```bash
# Verify v8.0 Autonomous Loop
python Leo.py --help
python -c "from Core.System.scheduler import TaskScheduler; print('[OK]')"
python -c "from Core.System.data_readiness import DataReadinessChecker; print('[OK]')"
python -c "from Data.Access.gap_scanner import GapScanner; print('[OK]')"

# Verify Tier 1 guardrails are live
python -c "from Core.System.guardrails import StaircaseTracker; print('[OK]')"
```

---

## 5. Flutter Design Specification — Liquid Glass

### 5.1 Font: Google Fonts — Lexend

| Level          | Size | Weight          | Spacing | Color     |
| -------------- | ---- | --------------- | ------- | --------- |
| `displayLarge` | 22px | w700 (Bold)     | -1.0    | `#FFFFFF` |
| `titleLarge`   | 15px | w600 (SemiBold) | -0.3    | `#FFFFFF` |
| `titleMedium`  | 13px | w600            | default | `#F1F5F9` |
| `bodyLarge`    | 13px | w400            | default | `#F1F5F9` |
| `bodyMedium`   | 11px | w400            | default | `#64748B` |

### 5.2 Color Palette

#### Brand & Primary
| Token                      | Hex       | Usage                      |
| -------------------------- | --------- | -------------------------- |
| `primary` / `electricBlue` | `#137FEC` | Buttons, active indicators |

#### Glass Tokens (60% translucency default)
| Token             | Hex       | Alpha        |
| ----------------- | --------- | ------------ |
| `glassDark`       | `#1A2332` | 60% (`0x99`) |
| `glassLight`      | `#FFFFFF` | 60%          |
| `glassBorderDark` | `#FFFFFF` | 10%          |

### 5.3 Performance Modes (`GlassSettings`)
| Mode     | Blur | Target            |
| -------- | ---- | ----------------- |
| `full`   | 24σ  | High-end devices  |
| `medium` | 8σ   | Mid-range devices |
| `none`   | 0σ   | Low-end devices   |

---

## 6. 12-Step Problem-Solving Framework

> **MANDATORY** for all failure investigation and resolution. Follow in exact order.

| Step              | Action                    | Rule                                    |
| ----------------- | ------------------------- | --------------------------------------- |
| **1. Define**     | What is the problem?      | Focus on understanding — no blame.      |
| **2. Validate**   | Is it really a problem?   | Pause. Does this actually need solving? |
| **3. Expand**     | What else is the problem? | Look for hidden or related issues.      |
| **4. Trace**      | How did it occur?         | Reverse-engineer the timeline.          |
| **5. Brainstorm** | ALL possible solutions.   | No filtering yet.                       |
| **6. Evaluate**   | Best solution right now?  | Consider resources and time.            |
| **7. Decide**     | Commit to the solution.   | No second-guessing.                     |
| **8. Assign**     | Actionable steps.         | Systematic and specific.                |
| **9. Measure**    | Define "solved".          | Expected effects?                       |
| **10. Start**     | Take first action.        | Momentum matters.                       |
| **11. Complete**  | Finish every step.        | No half-measures.                       |
| **12. Review**    | Compare outcomes.         | Repeat if not solved.                   |

---

## 7. Decision-Making Standard

- **Sports Domain Accuracy**: Data MUST match the real-world source of truth.
- **Crest Integrity**: Team crests/logos MUST always be displayed alongside names. Crest columns in `schedules` MUST contain Supabase Storage URLs — never local paths.
- **No Hardcoded Proxy Data**: Never use placeholders (e.g., "WORLD"). Use "Unknown" if missing.
- **Sports-Informed Sorting**: Trust the database `position` column for standings.

---

## 8. Bet Safety Guardrails

> See [PROJECT_STAIRWAY.md](PROJECT_STAIRWAY.md) for the capital strategy and [LeoBook_Technical_Master_Report.md](LeoBook_Technical_Master_Report.md) Section 6 for the full guardrails specification.

All six Tier 1 guardrails are **LIVE as of March 10, 2026**. None may be disabled without an explicit Chief Engineer decision recorded in the audit log.

| Guardrail | Status | Implementation |
| --------- | ------ | -------------- |
| **Dry-Run Mode** | ✅ LIVE | `--dry-run` flag → `guardrails.enable_dry_run()` in `Leo.py`. Logs all intended bets without placing them. Default until full pipeline validation is complete. |
| **Kill Switch** | ✅ LIVE | `STOP_BETTING` flag file check in `fb_manager.py`. Creates an immediate halt of all bet placement when the file is present in the project root. |
| **Max Stake Cap** | ✅ LIVE | `StaircaseTracker.get_max_stake()` in `Core/Pages/placement.py`. Hard ceiling on stake per stair — cannot be overridden at runtime. |
| **Staircase State Machine** | ✅ LIVE | `StaircaseTracker` in `Core/System/guardrails.py`, persisted to SQLite `stairway_state` table. Enforces the one-stair fallback loss rule. State survives process restarts. |
| **Balance Sanity Check** | ✅ LIVE | Blocks all bet placement if account balance < ₦500. |
| **Daily Loss Limit** | ✅ LIVE | Halts all bet placement for the day if cumulative losses reach ₦5,000. Resets at midnight WAT. |

- **Audit Logging** (IMPLEMENTED): Every bet cycle writes to `audit_log` in both SQLite and Supabase.
- **Confidence Gating** (IMPLEMENTED): Predictions below threshold are marked `SKIP` and never progress to betting.

---

## 9. RULEBOOK Enforcement

- **Current**: This document is mandatory reading for all developers and AI agents. Compliance is honour-based.
- **Planned**: Pre-commit hooks and linter rules where automatable (e.g., SelectorManager usage, no hardcoded selectors, file headers).

---

*Last updated: March 12, 2026 (v8.1 — Guardrails all LIVE, GapScanner canonical taxonomy, §2.13 Phase guidance added)*
*Previous: v8.0 — "Stairway Engine" 30-dim RL + Chapter 1 v9.0 + Poisson Expert Signal (March 9, 2026)*
*Authored by: LeoBook Engineering Team — Materialless LLC*