# LeoBook Algorithm & Codebase Reference
 
> **Version**: 8.1.0 · **Last Updated**: 2026-03-10 · **Architecture**: Autonomous High-Velocity Architecture (Supervisor-Worker + Neuro-Symbolic Ensemble + Safety Guardrails v1.0)

This document maps the **execution flow** of [Leo.py](Leo.py) to specific files and functions.

---

## Autonomous Orchestration (v7.1)

Leo.py is an **autonomous orchestrator** powered by a **Supervisor-Worker Pattern** (`Core/System/supervisor.py`). It replaces the monolithic loop with isolated, stateful workers.

```
Leo.py (Orchestrator) v7.3
├── Supervisor (System Control):
│   └── System State Persistence (SQLite system_state table)
├── Startup (Bootstrap):
│   └── Push-Only Sync → Supabase (auto-bootstrap if local DB empty)
├── Chapter Workers (Isolated Execution):
│   ├── P1 Worker: Prologue P1
│   ├── P2 Worker: Prologue P2
│   ├── C1 Worker: Chapter 1 (Resolution, Ensemble Predict, Sync)
│   └── C2 Worker: Chapter 2 (Guardrails, Booking, Funds)
├── Prologue (Materialized Readiness Cache):
│   ├── P1: Quantity & ID Gate (O(1) lookup)
│   ├── P2: History & Quality Gate (O(1) lookup)
│   └── P3: AI Readiness (O(1) lookup)
└── Live Streamer: Isolated parallel task (60s updates + outcome review)
```

---

## Data Readiness Gates & Auto-Remediation

**Readiness Cache**: Gates perform O(1) reads from `readiness_cache`. The O(N) scan logic is reserved for `--bypass-cache` or post-remediation updates.

1. **Gate P1 (Quantity & ID)**: Checks coverage and data quality.
   - **Thresholds**: 90% league coverage AND teams >= 3 per league. Validates IDs (fail if >5% invalid).
   - **Remediation**: Triggers `auto_remediate("leagues")`.
2. **Gate P2 (History & Quality)**: Checks fixture coverage.
   - **Logic**: Pass if 0 critical gaps AND 0 completed season mismatches. **ACTIVE seasons never block.**
   - **Remediation**: Triggers `auto_remediate("seasons")`.
3. **Gate P3 (AI)**: RL adapters trained.
   - **Remediation**: Triggers `auto_remediate("rl")`.

---

## Autonomous Task Scheduler

**Objective**: Event-driven execution of business-critical maintenance and time-sensitive predictions.

Handled by `TaskScheduler` ([scheduler.py](Core/System/scheduler.py)), supporting:

1. **Weekly Enrichment**: Scheduled every Monday at 2:26 AM. Triggers `enrich_leagues.py --weekly` (lightweight mode, `MAX_SHOW_MORE=2`).
2. **Day-Before Predictions**: When a team has multiple matches in a week, only the first is processed immediately. Subsequent matches are added to the scheduler as `day_before_predict` tasks to ensure the RL engine has the absolute latest H2H/Outcome data before predicting the next game.
3. **Dynamic Sleep**: Leo.py calculates the `next_wake_time` after every cycle. If the next task is 2 hours away, it sleeps for 2 hours. If no tasks are pending, it defaults to the `LEO_CYCLE_WAIT_HOURS` config.

---

## Data Leak Guard (Max 1 Prediction/Team/Week)

**Purpose**: Prevent data leakage in RL inference. This is NOT a business frequency cap — it is a technical safeguard.

- The prediction model is built on recent form (last 10 matches). Predicting a team's future match before their most recent pending match resolves would create a data leak — the result of match N influences match N+1's prediction.
- If Team A plays on Monday and Thursday:
  - Monday match: Predicted during Monday's cycle.
  - Thursday match: Scheduled as `day_before_predict`. On Wednesday (24h before), the Scheduler wakes Leo to predict using Monday's result for fresh form encoding.
- **Enforcement**: At the team-prediction layer in Chapter 1 P2. Surplus matches are queued by the Scheduler, not discarded.

---

## Computed Standings (Postgres VIEW)

**Objective**: Eliminate sync latency and redundant storage by calculating standings dynamically.

1. **Supabase**: A Postgres VIEW `computed_standings` performs a complex `UNION ALL` + `RANK()` over the `fixtures` table.
2. **SQLite**: The `computed_standings()` helper in [league_db.py](Data/Access/league_db.py) performs a mirrored SQL query locally.
The standalone `standings` table has been deprecated and removed.

---

## Neural RL Engine (`Core/Intelligence/rl/`)

**Architecture**: SharedTrunk + LoRA league adapters + league-conditioned team adapters.

- **Primary Reward**: Prediction accuracy.
- **Constraint**: Same team produces different predictions in different competitions.
- **Cold-Start**: New leagues/teams get a generic adapter; the model defaults to conservative predictions.
- **Fine-Tune Threshold**: After 50+ matches, an adapter becomes eligible for fine-tuning.
- **Training**: Chronological day-by-day walk-through using only historical data (future dates excluded). PPO with composite rewards and clipped gradients.

---

## Neuro-Symbolic Ensemble

**Objective**: Combine rule-based transparency with neural-based pattern recognition.

Handled by `EnsembleEngine` ([ensemble.py](Core/Intelligence/ensemble.py)), predictions are merged using a weighted confidence-logits formula:

### 1. The Merger Formula
$$Final\_Logits = (W_s \times Rule\_Conf \times Rule\_Logits) + (W_n \times RL\_Conf \times RL\_Logits)$$
Where:
- $W_s, W_n$: Weights for Symbolic and Neural engines respectively.
- $Rule\_Conf, RL\_Conf$: Confidence scores (0.0 - 1.0) from each engine.

### 2. Default Configuration
- **Weights**: Default $W_s = 0.7$, $W_n = 0.3$.
- **Overrides**: Weights can be tuned per-league via `ensemble_weights.json`.

### 3. Symbolic Baseline Guarantee
- **Symbolic Fallback**: If $RL\_Conf < 0.3$ or neural inference fails, the system triggers `fallback_to_symbolic`, returning **100% Rule Engine output**.
- **Reasoning**: Neural models can "hallucinate" high confidence on OOD (Out-Of-Distribution) data. The Rule Engine provides a physical/logical baseline that neural signals must augment, not replace, when uncertain.
- **Circuit Breaker**: SearchDict LLM enrichment skips remaining batches if all providers (Gemini + Grok) are offline.

---

## Safety Guardrails (v1.0)

**Enforcement**: Handled by `Core/System/guardrails.py`. Chapter 2 cannot proceed to execution (`place_multi_bet_from_codes`) unless all 6 gates pass:

1. **Kill Switch**: Blocks if `STOP_BETTING` file exists.
2. **Dry-Run**: Blocks if `--dry-run` flag is active.
3. **Daily Loss Limit**: Blocks if today's losses ≥ ₦5,000.
4. **Balance Sanity**: Blocks if Football.com balance < ₦500.
5. **Max Stake Cap**: Caps any single bet to the current Stairway step (₦1,000 to ₦2,048,000).
6. **Staircase Tracker**: Enforces the 1-7 compounding logic with SQLite persistence.

---

*Last updated: March 10, 2026 (v8.1.0 — Supervisor-Worker + Neuro-Symbolic Ensemble + Safety Guardrails v1.0)*
*LeoBook Engineering Team — Materialless LLC*
