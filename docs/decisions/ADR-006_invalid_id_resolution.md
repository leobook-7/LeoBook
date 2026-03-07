# ADR-006: Invalid ID Resolution

## Status: Accepted

## Context:
Malformed or placeholder Flashscore IDs (e.g., `UNKNOWN_TEAM_123`) were causing enrichment failures and duplicate rows. Manual resolution was slow and error-prone.

## Decision:
Implement an automated Invalid ID Resolution pipeline. Includes pattern-based detection, local dictionary merging, and a `Priority 1` enrichment queue for automated Flashscore search/resolution.

## Consequences:
- **Deduplication**: Automatic merging of placeholder rows into valid rows, updating all dependent fixture/schedule records.
- **Autonomy**: High-priority `enrichment_queue` drain cycle uses Playwright to find correct IDs for names without human input.
- **Data Health**: Prevents "phantom" data from polluting the prediction pipeline.

## Files Affected:
- [Core/System/gap_resolver.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/System/gap_resolver.py)
- [Core/System/data_quality.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/System/data_quality.py)
- [Scripts/enrich_leagues.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Scripts/enrich_leagues.py)
