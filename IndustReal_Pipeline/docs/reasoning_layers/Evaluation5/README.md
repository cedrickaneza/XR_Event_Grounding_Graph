# Evaluation 5: Symbolic Input Degradation

Evaluation 5 checks whether the reasoning layer degrades conservatively and traceably when already-symbolic evidence is deliberately made incomplete, low-confidence, contradictory, or semantically wrong.

This is not a real perception robustness benchmark. It does not test computer vision, object detection, action recognition, raw-video interpretation, or recovery from perception errors. Correctness against expert judgement is also outside this evaluation's scope.

## Selected Clip

- Clip/result ID: `raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`
- Reason: it contains accepted, uncertain, and rejected steps, dependencies, incompatibilities, removal actions, invalidated effects, and produced-effect lifecycle evidence.

## How To Run

```powershell
.venv\Scripts\python.exe scripts\24_evaluate_symbolic_input_degradation.py --project-root . --clip-result-id raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1 --reasoning-dir results\reasoning_layers\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1 --output-dir docs\reasoning_layers\Evaluation5 --strict
```

## Required Inputs

- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`
- `effect_history_diagnostics.csv`
- `step_records.jsonl`
- `predicates.jsonl`
- `inferred_constraints.csv`
- `rule_coverage_diagnostics.csv`
- `config/thesis_rules.yaml`

## Generated Outputs

- `evaluation5_report.md`
- `evaluation5_summary.csv`
- one CSV per perturbation scenario
- status-transition, conservative-degradation, trace-preservation, and dependency CSVs
- `evidence/evaluation5_results.json`
- `evidence/baseline_snapshot.json`
- exact perturbed inputs under `evidence/perturbation_inputs/`
- complete rerun outputs under `evidence/perturbation_outputs/`
- `missing_data_report.md` only when required data is missing or malformed.
