# Evaluation 3 Report: Step Validation And Effect-Lifecycle Behavior

- Evaluated clip/result ID: `raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`
- Mode: `od_plus_psr_error_hints`
- Timestamp: `2026-05-20T09:01:30+00:00`
- Reasoning directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\results\reasoning_layers\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`
- Graph directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\results\procedural_reasoning_graph\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`

## Validation Status Distribution

| Status | Count |
| --- | ---: |
| accepted | 18 |
| rejected | 6 |
| uncertain | 10 |

## Effect Lifecycle Summary

| Lifecycle status | Count |
| --- | ---: |
| active | 20 |
| inactive_rejected | 4 |
| invalidated | 8 |

## Scenario Summary

| Scenario | Status | Message | Evidence |
| --- | --- | --- | --- |
| Requirement support | PASS | 45 requirements inspected; 0 inconsistent requirement-support records. | `requirement_support_results.csv` |
| Hard incompatibility | PASS | 2 incompatibility constraints inspected; 0 were not rejected with trace evidence. | `incompatibility_results.csv` |
| Rejected-step isolation | PASS | 6 rejected steps inspected; 0 rejected-step isolation violations. | `rejected_step_isolation_results.csv` |
| Removal invalidation | PASS | 10 removal effects inspected; 0 invalidation inconsistencies. | `removal_invalidation_results.csv` |
| Reduced confidence | PASS | accepted step became uncertain after confidence reduction | `reduced_confidence_results.csv` |

## Requirement Support Summary

- Requirements inspected: 45
- Supported: 27
- Missing: 18

## Incompatibility Summary

- Incompatibility cases inspected: 2
- Rejected due to incompatibility: 2

## Rejected-Step Isolation Summary

- Rejected steps inspected: 6
- Isolation violations: 0

## Removal Invalidation Summary

- Removal effects inspected: 10
- Invalidation failures: 0

## Reduced-Confidence Perturbation Summary

- Perturbed rows: 1
- Result statuses: {'PASS': 1}

## Warnings, Failures, And Skipped Checks

- None.

## Thesis Interpretation

The generated evidence is suitable for filling Table \ref{tab:evaluation-validation-response}: it separates baseline Layer 4 behavior from the controlled reduced-confidence perturbation and records the exact CSV/JSON evidence for each scenario.

Status totals: PASS=5, FAIL=0, WARNING=0, SKIPPED=0.
