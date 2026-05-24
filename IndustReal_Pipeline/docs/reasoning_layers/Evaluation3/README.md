# Evaluation 3: Step Validation And Effect-Lifecycle Behavior

This folder contains reproducible evidence for thesis Evaluation 3. It evaluates Layer 4 validation behavior: requirement support, missing requirements, hard incompatibility rejection, rejected-step isolation, removal invalidation, and threshold-sensitive reduced-confidence behavior.

Evaluation 3 is not a Layer 3 constraint coverage evaluation and is not a perception-accuracy evaluation.

## Selected Clip

- Clip/result ID: `raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`
- Clip: `08_assy_0_1`
- Mode: `od_plus_psr_error_hints`

This clip is selected because Evaluation 2 showed it contains requirements, produced effects, tool requirements, safety requirements, removal effects, and incompatibility constraints needed to exercise Layer 4 behavior.

## How To Run

```powershell
.venv\Scripts\python.exe scripts\22_evaluate_validation_behavior.py --project-root . --clip-result-id raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1 --reasoning-dir results\reasoning_layers\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1 --graph-dir results\procedural_reasoning_graph\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1 --output-dir docs\reasoning_layers\Evaluation3 --strict
```

## Required Inputs

- `step_records.jsonl`
- `predicates.jsonl`
- `inferred_constraints.csv`
- `rule_coverage_diagnostics.csv`
- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`
- `effect_history_diagnostics.csv`

Graph outputs are optional supplementary evidence only.

## Generated Outputs

- `evaluation3_report.md`
- `evaluation3_summary.csv`
- `requirement_support_results.csv`
- `incompatibility_results.csv`
- `rejected_step_isolation_results.csv`
- `removal_invalidation_results.csv`
- `reduced_confidence_results.csv`
- `status_distribution.csv`
- `effect_lifecycle_summary.csv`
- `evidence/evaluation3_results.json`
- `missing_data_report.md` only when required data is missing.

## Scenarios

1. Requirement support: supported `requires(...)` constraints must use active previous produced effects; unsupported requirements must be recorded as missing.
2. Hard incompatibility: `incompatibleAction(...)` must reject the affected step and appear in trace evidence.
3. Rejected-step isolation: rejected produced effects must remain historical but inactive and must not support later steps.
4. Removal invalidation: `produces(removed, component, target)` must invalidate a prior active `installed(component, target)` effect.
5. Reduced confidence: a copied perturbation lowers selected confidence values below `tau_acc` but above `tau_unc` and reruns Layer 4.
