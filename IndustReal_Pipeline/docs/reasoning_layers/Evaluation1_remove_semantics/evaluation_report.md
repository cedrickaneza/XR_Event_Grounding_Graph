# Evaluation 1 Remove Semantics Report

- Run ID: `raw_cad_dataset__all_test_clips`
- Clip/result ID: `raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1`
- Timestamp: `2026-05-17T20:26:54+00:00`
- Remove step: index `9`, `step::raw_cad_dataset__all_test_clips::od_only::test_p1::03_assy_0_1::event_9`
- Remove status: `accepted`

This post-change evidence verifies remove-action semantics after Layer 3 remove rules and Layer 4 active-effect invalidation. The original `docs/reasoning_layers/Evaluation1/` folder remains the unsupported-remove baseline.

## Summary Table

| Check | Status | Message | Evidence |
|---|---:|---|---|
| Remove step node exported | PASS | Remove step step::raw_cad_dataset__all_test_clips::od_only::test_p1::03_assy_0_1::event_9 is present as a Step node. | `procedural_reasoning_graph/procedural_reasoning_graph_nodes.csv` |
| Remove REQUIRES constraint exported | PASS | Remove step exposes requires(installed, front_wheel_assy, front_chassis). | `procedural_reasoning_graph/procedural_reasoning_graph_edges.csv` |
| Remove PRODUCES constraint exported | PASS | Remove step exposes produces(removed, front_wheel_assy, front_chassis). | `procedural_reasoning_graph/procedural_reasoning_graph_edges.csv` |
| Remove depends on active install support | PASS | Remove step has 1 DEPENDS_ON edge(s) to prior support. | `procedural_reasoning_graph/procedural_reasoning_graph_edges.csv` |
| Provisional dependency property exported | PASS | DEPENDS_ON edges carry provisional=true/false; sample support is accepted, so provisional=false. | `procedural_reasoning_graph/procedural_reasoning_graph_edges.csv` |
| Invalidation exposed on Step node | PASS | Remove Step node carries invalidates_effect_count=1 and invalidated_effects details. | `procedural_reasoning_graph/procedural_reasoning_graph_nodes.csv` |
| No later dependency uses invalidated installed effect | PASS | No later DEPENDS_ON edge uses installed(front_wheel_assy, front_chassis) after Step 9 removal. | `procedural_reasoning_graph/procedural_reasoning_graph_edges.csv` |
| No dependency from rejected support | PASS | No DEPENDS_ON edge targets a rejected support step in this graph. | `procedural_reasoning_graph/procedural_reasoning_graph_edges.csv` |
| Remove rule coverage diagnostics | PASS | Remove action is covered by Layer 3 rules and is not reported as no_applicable_rule. | `rule_coverage_diagnostics.csv` |
| Baseline Evaluation1 preserved | PASS | Original Evaluation1 folder remains the before-case evidence; post-change artifacts are in Evaluation1_remove_semantics. | `docs/reasoning_layers/Evaluation1/` |

## Remove Step Evidence

- Layer 3 emits `requires(installed, front_wheel_assy, front_chassis)` and `produces(removed, front_wheel_assy, front_chassis)` for the remove step.
- Layer 4 accepts the remove step because the required installed effect was active and produced by an accepted prior step.
- The graph exports the remove Step node, the REQUIRES edge, the PRODUCES edge, and the DEPENDS_ON edge to the earlier accepted install support.
- The remove Step node exposes `invalidates_effect_count=1` and an `invalidated_effects` property for `installed(front_wheel_assy, front_chassis)`.
- The sample DEPENDS_ON edge has `provisional=false`; tests cover `provisional=true` when the support is uncertain.
- No later graph dependency uses the invalidated `installed(front_wheel_assy, front_chassis)` effect as active support.

## Failures And Warnings

No remove-semantics failures or warnings were found in the post-change sample graph.

## Generated Outputs

- `inferred_constraints.csv`
- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`
- `effect_history_diagnostics.csv`
- `procedural_reasoning_graph/procedural_reasoning_graph.json`
- `procedural_reasoning_graph/procedural_reasoning_graph_nodes.csv`
- `procedural_reasoning_graph/procedural_reasoning_graph_edges.csv`
- `graph_remove_semantics_check.csv`
- `evaluation_summary.csv`
- `evidence/evaluation_results.json`
