# LeoBook Technical Audit — 2026-03-13

**Date:** 2026-03-13 17:52 WAT
**Auditor:** Antigravity
**Codebase root:** `c:\Users\Admin\Desktop\ProProjection\LeoBook`
**Scope:** Read-only audit of 5 priority items (P1, P2, P3, P4, P6)

---

## STATUS DASHBOARD

```
┌─────────────────────────────────────────────────────────────┐
│           LEOBOOK PRIORITY STATUS — 2026-03-13              │
├──────┬──────────────────────────────────┬───────────────────┤
│  P1  │ Key Mismatch Bug                 │ ✅ RESOLVED        │
│  P2a │ Fix 6 (HTTP 400 dead key)        │ ✅ RESOLVED        │
│  P2b │ Quota Exhaustion Resilience      │ ⚠️  PARTIAL        │
│  P3  │ Dead Code Cleanup                │ ✅ RESOLVED        │
│  P4  │ RL Training (Day 50/~730 total)  │ ⏳ RUNNING         │
│  P4b │ LR Scheduler Bug on Resume       │ ❌ UNRESOLVED      │
│  P6  │ Test Suite                       │ ❌ UNRESOLVED      │
│  P6b │ CI/CD Pipeline                   │ ❌ DOES NOT EXIST  │
└──────┴──────────────────────────────────┴───────────────────┘
```

---

## P1 — Key Mismatch Bug

**STATUS: ✅ RESOLVED**

### Audit Q1 — Keys in candidate dicts passed to `_fuzzy_resolve()`

In `_league_worker()` ([fb_manager.py:252-260](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Modules/FootballCom/fb_manager.py#L252-L260)), candidates are constructed as:

```python
# Lines 252-260 (verbatim)
raw_candidates = [
    m for m in all_page_matches
    if not fix_date or m.get('date', '') == fix_date
] or all_page_matches

candidates = [
    {**m, 'home_team': m.get('home', ''), 'away_team': m.get('away', '')}
    for m in raw_candidates
]
```

Each candidate dict contains **all original keys from `extract_league_matches()`** (which include `home`, `away` and other fields) **plus the aliases** `home_team` and `away_team` injected explicitly.

### Audit Q2 — Keys accessed by `_fuzzy_resolve()`

`_fuzzy_resolve()` ([match_resolver.py:284-331](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Modules/FootballCom/match_resolver.py#L284)) does **not** directly access dict keys. It calls `self._get_name(m, 'home')` and `self._get_name(m, 'away')`, which implement a fallback chain:

```python
# Lines 144-148 (verbatim)
@staticmethod
def _get_name(m: Dict, role: str) -> str:
    """Extract team name from a match dict with key fallback chain."""
    if role == 'home':
        return (m.get('home_team_name') or m.get('home_team') or m.get('home') or m.get('home_id') or '').strip()
    return (m.get('away_team_name') or m.get('away_team') or m.get('away') or m.get('away_id') or '').strip()
```

Fallback chain: `home_team_name` → `home_team` → `home` → `home_id`

### Audit Q3 — Key translation between layers

**Yes.** The fix is in `_league_worker()` at lines 257-260:
```python
candidates = [
    {**m, 'home_team': m.get('home', ''), 'away_team': m.get('away', '')}
    for m in raw_candidates
]
```
The comment above it (lines 248-251) explicitly documents the fix:
> `extract_league_matches()` returns dicts with `'home'`/`'away'` keys, but `GrokMatcher.resolve_with_cascade()` reads `'home_team'`/`'away_team'`. Adding both aliases here means neither side needs to change.

### Audit Q4 — Fix present?

**Yes.** Pattern `{**m, 'home_team': m.get('home', ''), 'away_team': m.get('away', '')}` is present at lines 257-260.

### Verdict: ✅ RESOLVED — key aliasing is applied before any resolver call.

---

## P2 — Gemini Quota Exhaustion Handling

### Audit Q1 — HTTP status codes treated as dead-key in `_ping_key()`

`_ping_key()` ([llm_health_manager.py:375-397](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/Intelligence/llm_health_manager.py#L375)):

```python
# Lines 392-394 (verbatim)
if resp.status_code in (401, 403) or (resp.status_code == 400 and "INVALID_ARGUMENT" in resp.text):
    return "FATAL"
return "OK" if resp.status_code in (200, 429) else "FAIL"
```

Dead-key (FATAL) conditions:
- **HTTP 401** — always FATAL
- **HTTP 403** — always FATAL
- **HTTP 400** + `"INVALID_ARGUMENT"` in response body — FATAL

HTTP 200 and 429 → `"OK"` (key alive)
Anything else → `"FAIL"` (temporary)

### Audit Q2 — HTTP 400 handled as dead-key?

**YES.** Line 392: `(resp.status_code == 400 and "INVALID_ARGUMENT" in resp.text)` → `return "FATAL"`.
**Fix 6 is RESOLVED.**

### Audit Q3 — Key rotation when all keys exhausted

**a. Key count:** Key pool is `GEMINI_API_KEY` (comma-separated). Comments reference "36 keys × 5 models" but the actual count at runtime depends on `.env`. No hardcoded list exists in code — parsed dynamically at `_ping_all()` line 319-323.

**b. When ALL keys exhausted:**

- **Per-minute exhaustion:** `get_next_gemini_key()` returns `""`. Caller (e.g. `_llm_resolve()`) logs `"No available keys for {model}, trying next model..."` and moves to the next model in the chain. If all models in the chain are exhausted, `_llm_resolve()` returns `(None, 0.0)` — the pipeline continues without LLM resolution.
- **Daily exhaustion:** `on_gemini_429()` calls `_model_daily_exhausted[model] = time.time()`. `get_next_gemini_key()` fast-fails with `""` for 24h. `has_chain_capacity()` returns `False`.
- **Fatal key:** `on_gemini_fatal_error()` permanently removes key from `_gemini_active` and `_gemini_keys`. If active pool → 0, `_gemini_active = []` and `has_chain_capacity()` returns `False`.

**No pipeline pause or crash on exhaustion** — it degrades gracefully. Circuit breaker in `build_search_dict.py` checks `health_manager._gemini_active` before each batch and skips remaining work if all providers are dead.

### Audit Q4 — Daily quota tracking distinct from per-minute?

**YES.** `_detect_daily_limit()` (lines 302-313) distinguishes the two:
```python
# Lines 311-313 (verbatim)
if not err_str:
    return False
return "PerDay" in err_str and "limit: 0" in err_str
```
`on_gemini_429()` routes to `_model_daily_exhausted` (24h mark) or per-minute cooldown (65s) based on this detection.

### Audit Q5 — Backoff/retry on 429?

**Yes, partial.** Per-key: 65-second `COOLDOWN_SECONDS` applied per model per key. In `_llm_resolve()`, after on_gemini_429:
- If daily-exhausted → `break` (abandon model)
- If per-minute → `continue` (try next key same model)

No exponential backoff in `_llm_resolve()` itself. Exponential backoff (`min(2^n, 30)`) exists in `build_search_dict.py` and `api_manager.py` per the master report — not verified in this audit.

### Audit Q6 — Codebase search for quota symbols

Searched across all `.py` files:
- `STOP_ON_QUOTA` — **0 matches**
- `quota_exhausted` — **0 matches**
- `daily_quota` — **0 matches** (but `DAILY_QUOTA_WINDOW = 86400` at line 32 is the equivalent)
- `quota_reset` — **0 matches**

The concept exists as `_model_daily_exhausted`, `DAILY_QUOTA_WINDOW`, `_detect_daily_limit`, and `reset_daily_exhaustion()`.

### Verdicts:
- **Fix 6 (HTTP 400):** ✅ RESOLVED — line 392 confirmed.
- **Quota exhaustion resilience:** ⚠️ PARTIAL — daily/per-minute distinction is solid; no pipeline-halt or alerting when all keys exhausted for >24h; no external notification mechanism; no auto-day-rollover trigger.

---

## P3 — Dead Code Cleanup

**STATUS: ✅ RESOLVED (commit `93c2a61`, 2026-03-13)**

### Audit Q1 — `Scripts/enrich_all_schedules.py`

**Does not exist.** `git rm` was executed. Zero matches found in codebase.

### Audit Q2 — `Modules/Flashscore/fs_processor.py`

**Does not exist.** `git rm` was executed.

### Audit Q3 — `Modules/Flashscore/manager.py`

**Does not exist.** `git rm` was executed.

### Audit Q4 — `Core/Browser/Extractors/standings_extractor.py`

**Does not exist.** `git rm` was executed.

### Audit Q5 — Leo.py lines 60-80

```python
# Lines 66-76 (verbatim, current state)
from Data.Access.db_helpers import init_csvs, log_audit_event
from Data.Access.sync_manager import SyncManager, run_full_sync
from Data.Access.league_db import init_db
from Modules.Flashscore.fs_live_streamer import live_score_streamer
from Modules.FootballCom.fb_manager import run_odds_harvesting, run_automated_booking
from Scripts.recommend_bets import get_recommendations
from Core.Intelligence.prediction_pipeline import run_predictions, get_weekly_fixtures
from Scripts.enrich_leagues import main as run_league_enricher
```

`from Scripts.enrich_all_schedules import enrich_all_schedules` **is gone.** No Flashscore dead module imports present.

### Audit Q6 — `TASK_DAY_BEFORE_PREDICT` handler

```python
# Lines 343-350 (verbatim, current state)
elif task.task_type == TASK_DAY_BEFORE_PREDICT:
    fid = task.params.get('fixture_id')
    print(f"  [Scheduler] Day-before prediction for fixture {fid} — "
          f"re-runs prediction_pipeline for this fixture only.")
    # TODO: pass target_fixture_ids=[fid] once run_predictions supports it
    await run_predictions(scheduler=scheduler)
    scheduler.complete_task(task.task_id)
```

`run_flashscore_analysis()` call is gone. Handler now calls `run_predictions()`.

### Verdicts:
- **Dead files deleted:** ✅ YES (all 4, confirmed absent)
- **Leo.py import removed:** ✅ YES
- **TASK_DAY_BEFORE_PREDICT handler fixed:** ✅ YES (TODO inline for target_fixture_ids scoping)

---

## P4 — Phase 1 RL Training Status & Checkpoint Integrity

### Audit Q1 — Checkpoint directory listing

```
Data/Store/models/checkpoints/
├── phase1_day046.pth   521,200,523 bytes
├── phase1_day047.pth   557,765,427 bytes
├── phase1_day048.pth   657,645,179 bytes
├── phase1_day049.pth   718,493,307 bytes
└── phase1_day050.pth    35,814,191 bytes
```

Most recent: **`phase1_day050.pth`** (35 MB — significantly smaller, likely a fresh daily checkpoint vs accumulated registry in days 46-49).
Training is at **Day 50** of the full fixture dataset window.

> [!NOTE]
> "Day 50" = 50 fixture-days processed from historical data (chronological training). This is not a calendar day. The full window spans all dates in `schedules` within 2 seasons — likely ~400-700 fixture-days total.

### Audit Q2 — Checkpoint SAVE logic

```python
# Lines 548-562 (verbatim)
ckpt_data = {
    "day": day_idx + 1,
    "total_days": start_day_idx + len(all_dates),
    "match_date": match_date,
    "model_state": self.model.state_dict(),
    "optimizer_state": self.optimizer.state_dict(),
    "total_matches": total_matches_global,
    "correct_predictions": total_correct_global,
    "phase": active_phase,
    "n_actions": N_ACTIONS,
    "odds_rows_at_save": odds_rows,
    "days_live_at_save": days_live,
}
torch.save(ckpt_data, CHECKPOINT_DIR / f"phase{active_phase}_day{day_idx+1:03d}.pth")
torch.save(ckpt_data, latest_path)
```

**`scheduler.state_dict()` is NOT saved.** The `CosineAnnealingWarmRestarts` scheduler state is absent from `ckpt_data`.

### Audit Q3 — Checkpoint LOAD/RESUME logic

```python
# Lines 419-441 (verbatim)
if resume and not cold and latest_path.exists():
    try:
        ckpt = torch.load(latest_path, map_location=self.device, weights_only=False)
        ...
        self.model.load_state_dict(ckpt["model_state"], strict=False)
        self.optimizer.load_state_dict(ckpt["optimizer_state"])
        start_day_idx = ckpt["day"]
        ...
```

**`scheduler.load_state_dict()` is NOT called.** The scheduler is re-initialized from scratch on every resume.

### Audit Q4 — LR re-reduction bug on resume

**CONFIRMED.** The bug is at lines 443-449:

```python
# Lines 443-449 (verbatim)
# Phase 1 LR reduction: imitation needs 10x lower LR than PPO exploration
original_lrs = []
if active_phase == 1:
    for pg in self.optimizer.param_groups:
        original_lrs.append(pg['lr'])
        pg['lr'] = pg['lr'] * 0.1
    print(f"  [TRAIN] Phase 1 LR reduced 10x for stable imitation (base → {self.optimizer.param_groups[0]['lr']:.2e})")
```

This block runs **every time `train_from_fixtures()` is called**, including on resume. Because `scheduler.state_dict()` is not saved, the LR restored by `optimizer.load_state_dict()` reflects whatever state the CosineAnnealing scheduler left it at — then it is immediately multiplied by 0.1 again. **Each resume applies an additional 10x LR reduction.**

### Audit Q5 — Phase detection

Phase is auto-detected via `check_phase_readiness(conn)` at lines 361-383:
```python
phase_status = check_phase_readiness(conn)
odds_rows  = phase_status["odds_rows"]
days_live  = phase_status["days_live"]
phase2_ready = phase_status["phase2_ready"]   # PHASE2_MIN_ODDS_ROWS + PHASE2_MIN_DAYS_LIVE
phase3_ready = phase_status["phase3_ready"]   # PHASE3_MIN_ODDS_ROWS + PHASE3_MIN_DAYS_LIVE
```
Currently Phase 1 (no live odds data meeting thresholds). The `--phase N` CLI flag is accepted but overridden by auto-detection if auto-detected phase > CLI flag.

### Audit Q6 — Current accuracy from checkpoint

No `training_log.json`, `metrics.csv`, or similar file found. Accuracy is logged to stdout only. From checkpoint metadata: `total_matches` and `correct_predictions` are saved — readable from `phase1_day050.pth` if loaded, but not surfaced in a log file.

### Verdicts:
- **Current training day:** 50 (fixture-days processed)
- **Latest accuracy:** CANNOT CONFIRM (stdout-only, no log file)
- **Scheduler state persisted in checkpoint:** ❌ NO
- **LR re-reduction bug on resume:** ❌ PRESENT — confirmed at lines 443-449

---

## P6 — Test Suite

### Audit Q1 — `tests/` directory at project root

**Does not exist.** No `tests/` directory found.

### Audit Q2 — Python test files (`test_*.py`, `*_test.py`)

**0 results.** No Python test files found anywhere in the repository.

### Audit Q3 — Dart test files

One file found:
- `leobookapp/test/widget_test.dart` (31 lines)

### Audit Q4 — Python test files: content

N/A — none exist.

### Audit Q5 — Dart test files: content

`widget_test.dart` is the **unmodified Flutter scaffold**:
```dart
// Lines 13-29 (verbatim)
void main() {
  testWidgets('Counter increments smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(const LeoBookApp());
    expect(find.text('0'), findsOneWidget);
    expect(find.text('1'), findsNothing);
    await tester.tap(find.byIcon(Icons.add));
    await tester.pump();
    expect(find.text('0'), findsNothing);
    expect(find.text('1'), findsOneWidget);
  });
}
```
Tests a counter widget. **No LeoBook-specific logic tested.** No golden tests. No Cubit/Bloc mocks. This test will fail (LeoBookApp does not have a counter widget).

### Audit Q6 — `pytest.ini` / `pyproject.toml`

Not found. No pytest configuration exists.

### Audit Q7 — GitHub Actions workflows

**No `.github/workflows/` directory found.** CI/CD pipeline does not exist.

### Verdicts:
- **Python test coverage:** ❌ NONE
- **Flutter test coverage:** ❌ NONE (scaffold only, likely broken)
- **CI/CD pipeline:** ❌ DOES NOT EXIST

---

## Changes Since Last Audit (2026-03-12)

| Item | Change | Commit |
|---|---|---|
| P1 Key Mismatch | ✅ Fixed — alias injection in `_league_worker()` | In-place (no separate commit identified) |
| P2a Fix 6 (HTTP 400) | ✅ Fixed — `_ping_key()` now returns FATAL on 400+INVALID_ARGUMENT | In-place |
| P3 Dead Code Cleanup | ✅ Complete — 4 files deleted, Leo.py patched | `93c2a61` (2026-03-13) |
| P4 RL Training | ⏳ Progress — Day 34 → Day **50** | Active background process |
| Rule Engine 30-dim | ✅ Applied in previous session | Conversation `16865d5f` |

---

## Codebase Metrics (Updated)

| Metric | Value |
|--------|-------|
| Python files | ~220 (4 dead files removed) |
| Dead code removed | ~1,874 lines (commit `93c2a61`) |
| RL checkpoint | Phase 1 Day 50, `phase1_latest.pth` |
| Test coverage | 0% (Python), 0% (Flutter — scaffold only) |
| CI/CD | None |
| Leo.py | 785 lines (was 786, import removed) |

---

## Open Action Items

| Priority | Item | Owner |
|---|---|---|
| HIGH | Fix LR scheduler state persistence in `trainer.py` checkpoint | Engineering |
| HIGH | Add `scheduler.state_dict()` to `ckpt_data`, `scheduler.load_state_dict()` on resume | Engineering |
| MED | Implement `target_fixture_ids` in `run_predictions()` | Engineering |
| MED | Add daily exhaustion alerting when all Gemini keys dead >24h | Engineering |
| LOW | Write first Python unit tests (rule engine) | Engineering |
| LOW | Fix broken Flutter scaffold test | Engineering |

*Last updated: 2026-03-13 17:52 WAT*
*LeoBook Engineering Team — Materialless LLC*
