# Evaluation 4: Procedural Graph Traceability

This folder contains reproducible evidence for thesis Evaluation 4. It checks whether the procedural reasoning graph exposes the reasoning trace behind validation outcomes through Step, Predicate, Constraint, Rule, Entity, and relationship structure.

Evaluation 4 evaluates graph traceability. It does not re-evaluate Layer 3 coverage, Layer 4 validation correctness, perception accuracy, or dataset-wide graph coverage.

## Selected Graph

- Clip/result ID: `raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`
- Graph directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\results\procedural_reasoning_graph\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`
- Reasoning directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\results\reasoning_layers\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`

The selected graph matches Evaluation 3 because it contains accepted, uncertain, and rejected steps; dependency support; incompatibilities; removal actions; invalidated effects; and produced-effect lifecycle information.

## How To Run

```powershell
.venv\Scripts\python.exe scripts\23_evaluate_graph_traceability.py --project-root . --clip-result-id raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1 --reasoning-dir results\reasoning_layers\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1 --graph-dir results\procedural_reasoning_graph\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1 --output-dir docs\reasoning_layers\Evaluation4 --strict
```

## Required Inputs

- `procedural_reasoning_graph.json`
- `procedural_reasoning_graph_nodes.csv`
- `procedural_reasoning_graph_edges.csv`
- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`
- `effect_history_diagnostics.csv`

## Generated Outputs

- `evaluation4_report.md`
- `evaluation4_summary.csv`
- detailed per-check CSV files
- `graph_inventory.csv`
- `neo4j_views.md`
- `evidence/evaluation4_results.json`
- `missing_data_report.md` only when required inputs are missing.
