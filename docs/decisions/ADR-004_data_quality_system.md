# ADR-004: Data Quality System

## Status: Accepted

## Context:
Missing metadata (country codes, flags) and malformed data were causing silent failures or degraded prediction quality. No structured reporting existed for "data health".

## Decision:
Implement a centralized Data Quality system with a column-level scanner, gap classification, and structured reporting.

## Consequences:
- **Visibility**: `Leo.py --data-quality` produces a detailed JSON health report.
- **Automated Fixes**: Gaps are classified as `IMMEDIATE` (auto-fixed), `DERIVABLE`, or `STAGE_ENRICHMENT`.
- **Integrity**: Ensures critical fields match source-of-truth expectations before Chapter 1 begins.

## Files Affected:
- [Core/System/data_quality.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/System/data_quality.py)
- [Leo.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Leo.py)
- [Data/Store/data_quality_report_{timestamp}.json](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Data/Store/)
