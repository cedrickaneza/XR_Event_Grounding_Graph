# Evaluation 1: Pipeline Artifact Correctness

This folder contains reproducible evidence for thesis Evaluation 1. The purpose is to verify that the reasoning-layer artifact chain is inspectable stage by stage: adapter outputs, Layer 3 constraints, Layer 4 validation records, explanation traces, and the procedural reasoning graph.

This evaluation is artifact-based and reasoning-focused. It does not evaluate low-level perception, object detection, step segmentation, or CAD-to-image alignment.

## How To Run

```powershell
.venv\Scripts\python.exe scripts\20_evaluate_pipeline_artifact_correctness.py --project-root . --run-id raw_cad_dataset__all_test_clips --clip-result-id raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1 --output-dir docs\reasoning_layers\Evaluation1 --strict
```

Use `--restore-preserved` to restore preserved upstream `/tmp` outputs from `results/preserved_tmp/raw_cad_dataset__all_test_clips.tar.gz` when available. Downloads are never attempted unless `--download-missing` is passed.

## Required Inputs

- `nodes_events.csv` from the upstream Neo4j-style CSV export.
- `step_records.jsonl` and `predicates.jsonl` from the reasoning adapter.
- `inferred_constraints.csv` from Layer 3.
- `rule_coverage_diagnostics.csv` from Layer 3 rule coverage diagnostics.
- `validation_records.jsonl`, `step_validations.csv`, and `explanation_traces.json` from Layer 4.
- `procedural_reasoning_graph.json`, `procedural_reasoning_graph_nodes.csv`, and `procedural_reasoning_graph_edges.csv` from the graph builder.

When the procedural graph is rebuilt, pass `--step-records` to `scripts\17_build_procedural_reasoning_graph.py` so Step nodes include source metadata such as `clip_result_id`, `run_id`, `mode`, `archive_name`, and `clip`.

## Generated Outputs

- `evaluation1_report.md`
- `evaluation1_summary.csv`
- `artifact_inventory.csv`
- `schema_validation_results.csv`
- `reference_integrity_results.csv`
- `order_consistency_results.csv`
- `dependency_rule_results.csv`
- `rule_coverage_diagnostics.csv`
- `evidence/evaluation1_results.json`
- `missing_data_report.md` only when required data is missing.

## Status Semantics

- `PASS`: the check satisfied its expected condition.
- `FAIL`: a critical artifact or consistency condition failed.
- `WARNING`: evidence is usable, but an important caveat was found.
- `SKIPPED`: the current artifact contract does not expose enough information to evaluate that check, or no applicable rows exist.
