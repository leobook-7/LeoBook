# ADR-001: Supervisor-Worker Pattern

## Status: Accepted

## Context:
The monolithic `while True` loop in `Leo.py` was difficult to maintain, test, and recover from failures. A single crash in one chapter could halt the entire orchestrator, and state was lost between restarts unless manually handled.

## Decision:
Implement a Supervisor-Worker pattern. `Leo.py` now instantiates a `Supervisor` that manages isolated `BaseWorker` subclasses for each pipeline phase (Prologue, Chapter 1, Chapter 2).

## Consequences:
- **Isolation**: Failures in one chapter do not crash the orchestrator.
- **Persistence**: System state is saved to a SQLite `system_state` table after every worker execution.
- **Retries**: Each worker implements its own retry logic and `on_failure` hooks.
- **Maintainability**: New pipeline phases can be added as modular workers.

## Files Affected:
- [Leo.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Leo.py)
- [Core/System/supervisor.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/System/supervisor.py)
- [Core/System/worker_base.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/System/worker_base.py)
- [Core/System/pipeline_workers.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/System/pipeline_workers.py)
