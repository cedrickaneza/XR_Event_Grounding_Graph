# Evaluation 2: Constraint Inference Coverage

This folder contains reproducible evidence for thesis Evaluation 2. The purpose is to measure how Layer 3 enriches symbolic predicates into procedural constraints across selected reasoning outputs.

Evaluation 2 is about Layer 3 constraint inference coverage. It does not evaluate perception accuracy, step segmentation quality, CAD-to-image alignment, or Layer 4 validation behavior. Natural clips are allowed to have zero observed incompatibility constraints or zero rejected cases; those are reported as zero coverage rather than treated as failures.

## How To Run

```powershell
.venv\Scripts\python.exe scripts\21_evaluate_constraint_inference_coverage.py --project-root . --results-root results\reasoning_layers --output-dir docs\reasoning_layers\Evaluation2 --all-available --strict
```

For a specific clip/result folder:

```powershell
.venv\Scripts\python.exe scripts\21_evaluate_constraint_inference_coverage.py --project-root . --results-root results\reasoning_layers --output-dir docs\reasoning_layers\Evaluation2 --clip-result-id raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1 --strict
```

Use `--restore-preserved` to restore preserved upstream outputs from `results\preserved_tmp\raw_cad_dataset__all_test_clips.tar.gz` when available. Use `--download-missing` only when the existing IndustReal dataset runner should be allowed to fetch missing source archives.

## Required Inputs

Each evaluated reasoning output folder must contain:

- `step_records.jsonl`
- `predicates.jsonl`
- `inferred_constraints.csv`

Optional but recommended:

- `rule_coverage_diagnostics.csv`
- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`

## Generated Outputs

- `evaluation2_report.md`
- `evaluation2_constraint_coverage.csv`
- `constraint_type_counts.csv`
- `rule_coverage_summary.csv`
- `remove_semantics_coverage.csv`
- `constraint_provenance_results.csv`
- `confidence_validation_results.csv`
- `evaluation2_summary.csv`
- `evidence/evaluation2_results.json`
- `missing_data_report.md` only when requested clips are missing or incomplete folders are skipped.

## Status Semantics

- `PASS`: the check satisfied its expected condition.
- `FAIL`: a critical implementation or artifact problem was found.
- `WARNING`: evidence is usable, but an important limitation or coverage gap was observed.
- `SKIPPED`: no applicable local evidence was available for that check.
