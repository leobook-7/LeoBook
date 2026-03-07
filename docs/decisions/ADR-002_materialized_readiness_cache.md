# ADR-002: Materialized Readiness Cache

## Status: Accepted

## Context:
Checking Data Readiness Gates (P1-P3) required O(N) database scans (leagues, teams, fixtures) at the start of every cycle. As the database grew, this introduced significant latency (up to 30-60 seconds) before prediction began.

## Decision:
Implement a materialized `readiness_cache` table. Gate results are persisted after scan. Subsequence checks perform O(1) lookups from this table.

## Consequences:
- **Speed**: Orchestrator startup and gate checks are now instantaneous (milliseconds).
- **Consistency**: The system has a memoized state of "readiness" that persists across restarts.
- **Manual Control**: Added `--bypass-cache` flag to force a full re-scan when needed.

## Files Affected:
- [Core/System/data_readiness.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/System/data_readiness.py)
- [Data/Access/db_helpers.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Data/Access/db_helpers.py)
- [Leo.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Leo.py)
