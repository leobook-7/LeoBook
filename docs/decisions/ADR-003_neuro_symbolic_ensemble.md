# ADR-003: Neuro-Symbolic Ensemble

## Status: Accepted

## Context:
Relying purely on a Rule Engine (Symbolic) limited adaptation to complex patterns. Relying purely on RL (Neural) introduced "black box" risk and potential hallucinations on low-confidence data.

## Decision:
Implement a Neuro-Symbolic Ensemble. Predictions are merged using a weighted average of Rule Engine and RL outputs.

## Consequences:
- **Accuracy**: Combines logical safeguards with neural pattern matching.
- **Safety**: Fallback to 100% Rule Engine if RL confidence is < 0.3.
- **Tunability**: Per-league weight overrides via `ensemble_weights.json`.

## Files Affected:
- [Core/Intelligence/ensemble.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/Intelligence/ensemble.py)
- [Core/Intelligence/prediction_pipeline.py](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Core/Intelligence/prediction_pipeline.py)
- [Config/ensemble_weights.json](file:///c:/Users/Admin/Desktop/ProProjection/LeoBook/Config/ensemble_weights.json)
