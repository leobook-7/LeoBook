# ADR-005: Season Completeness Tracking

## Status: Accepted

## Context:
The `PROLOGUE_P2` gate (History) was blocking readiness based on `ACTIVE` seasons, which are incomplete by definition. Furthermore, there was no granular tracking of "percentage coverage" for historical seasons.

## Decision:
Implement a `SeasonCompletenessTracker` that calculates coverage based on (Total Matches / (Total Teams - 1) * Total Teams). Refactor P2 logic to ignore `ACTIVE` seasons for readiness gating.

## Consequences:
- **Logic Correction**: Gate contradictions resolved; active seasons no longer block the pipeline.
- **Observability**: Added `--season-completeness` flag to view coverage metrics.
- **UI Support**: Provides data for season progress bars in the Flutter dashboard.

## Files Affected:
- [Data/Access/season_completeness.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Data/Access/season_completeness.py)
- [Core/System/data_readiness.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/System/data_readiness.py)
- [Data/Access/db_helpers.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Data/Access/db_helpers.py)
