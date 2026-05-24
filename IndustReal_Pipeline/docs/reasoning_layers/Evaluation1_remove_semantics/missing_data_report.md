# Evaluation 1 Missing Data Report

Evaluation 1 did not find all required local artifacts.

- Run ID: `raw_cad_dataset__all_test_clips`
- Clip/result ID: `raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1`
- Preserved tarball: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\results\preserved_tmp\raw_cad_dataset__all_test_clips.tar.gz` (available)

## Missing Paths

### `\tmp\industreal_pilot\results\raw_cad_dataset\raw_cad_dataset__all_test_clips`
- Why needed: preserved upstream per-clip outputs; useful when adapter inputs must be regenerated
- Restoration/regeneration: restore the preserved tarball when available, or regenerate the upstream Neo4j/reasoning artifacts with the existing pipeline scripts.
- Download: supported only through the existing dataset batch runner with `--download-missing`; this evaluator does not download by default.

## Suggested Commands

```powershell
.venv\Scripts\python.exe scripts\20_evaluate_pipeline_artifact_correctness.py --restore-preserved
.venv\Scripts\python.exe scripts\12_export_neo4j_csv.py
.venv\Scripts\python.exe scripts\14_build_layer3_reasoning_adapter.py --clip-result-id raw_cad_dataset__all_test_clips::od_only::test_p1::03_assy_0_1 --output-dir results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1
.venv\Scripts\python.exe scripts\15_run_layer3_inference.py --step-records results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\step_records.jsonl --predicates results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\predicates.jsonl --output results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\inferred_constraints.csv
.venv\Scripts\python.exe scripts\16_run_layer4_validation.py --step-records results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\step_records.jsonl --predicates results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\predicates.jsonl --constraints results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\inferred_constraints.csv --output results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\validation_records.jsonl
.venv\Scripts\python.exe scripts\17_build_procedural_reasoning_graph.py --validations results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\validation_records.jsonl --step-records results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\step_records.jsonl --output-dir results\procedural_reasoning_graph\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1
```
