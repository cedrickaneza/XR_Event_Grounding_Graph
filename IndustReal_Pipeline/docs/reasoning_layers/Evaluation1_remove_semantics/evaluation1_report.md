# Evaluation 1 Report: Pipeline Artifact Correctness

- Evaluated run ID: `raw_cad_dataset__all_test_clips`
- Evaluated clip/result ID: `raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1`
- Timestamp: `2026-05-18T09:50:41+00:00`
- Neo4j input directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\results\neo4j\raw_cad_dataset__all_test_clips`
- Reasoning directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\docs\reasoning_layers\Evaluation1_remove_semantics`
- Graph directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\docs\reasoning_layers\Evaluation1_remove_semantics\procedural_reasoning_graph`
- Output directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\docs\reasoning_layers\Evaluation1_remove_semantics`

## Summary Table

| Check | Status | Evidence | Message |
| --- | --- | --- | --- |
| Step records produced | PASS | `step_records.jsonl` | 11 step records cover 11 input steps. |
| Predicate records produced | PASS | `predicates.jsonl` | 106 predicates reference valid step records. |
| Layer 3 constraints produced | PASS | `inferred_constraints.csv` | 28 constraints produced; names: {'produces': 11, 'requires': 13, 'requiresSafety': 3, 'requiresTool': 1}. |
| Layer 4 validation records produced | PASS | `validation_records.jsonl` | 11 validation records include statuses. |
| Explanation traces produced | PASS | `explanation_traces.json` | Validation decisions include trace information. |
| Graph export produced | PASS | `procedural_reasoning_graph.*` | Graph export contains 202 nodes and 526 edges. |
| Input order preserved | PASS | `procedural_reasoning_graph_edges.csv` | 10 NEXT edges follow validation order. |
| Rejected-step dependency rule respected | PASS | `procedural_reasoning_graph_edges.csv` | 10 DEPENDS_ON edges avoid rejected-step support. |

## Counts

| Artifact | Count |
| --- | ---: |
| events | 659 |
| step_records | 11 |
| predicates | 106 |
| constraints | 28 |
| rule_coverage_diagnostics | 11 |
| validation_records | 11 |
| explanation_traces | 11 |
| graph_nodes | 202 |
| graph_edges | 526 |

## Failures And Warnings

- None.

## Artifact Inventory

| Artifact | Exists | Records | Role |
| --- | --- | ---: | --- |
| `nodes_events.csv` | True | 659 | upstream input steps |
| `step_records.jsonl` | True | 11 | adapter step records |
| `predicates.jsonl` | True | 106 | adapter symbolic evidence |
| `inferred_constraints.csv` | True | 28 | Layer 3 constraints |
| `rule_coverage_diagnostics.csv` | True | 11 | Layer 3 rule coverage diagnostics |
| `validation_records.jsonl` | True | 11 | Layer 4 validation records |
| `step_validations.csv` | True | 11 | Layer 4 tabular validation view |
| `explanation_traces.json` | True | 11 | Layer 4 explanations |
| `procedural_reasoning_graph.json` | True | 728 | graph export |
| `procedural_reasoning_graph_nodes.csv` | True | 202 | graph node export |
| `procedural_reasoning_graph_edges.csv` | True | 526 | graph edge export |

## Interpretation

Evaluation 1 checks whether the implemented reasoning pipeline produces inspectable artifacts and whether cross-artifact references remain consistent. The result is suitable for filling the thesis Evaluation 1 table because it maps directly to the eight checks listed in the chapter. It should be interpreted as evidence about reasoning-layer artifact correctness, not as evidence of perception accuracy, object detection quality, step segmentation quality, or CAD-to-image alignment.

Status totals: PASS=8, FAIL=0, WARNING=0, SKIPPED=0.
