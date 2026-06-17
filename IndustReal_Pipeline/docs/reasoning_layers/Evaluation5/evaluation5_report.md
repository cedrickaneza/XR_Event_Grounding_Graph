# Evaluation 5 Report: Symbolic Input Degradation

- Evaluated clip/result ID: `raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`
- Timestamp: `2026-06-15T12:32:41+00:00`
- Reasoning directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\results\reasoning_layers\raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1`
- Output directory: `D:\Code\XR_Event_Grounding_Graph\IndustReal_Pipeline\docs\reasoning_layers\Evaluation5`

## Scope

This evaluation measures symbolic robustness under controlled degradation. It does not test real perception robustness and does not prove correctness against expert judgement.

## Baseline Status Distribution

| Status | Count |
| --- | ---: |
| accepted | 18 |
| rejected | 6 |
| uncertain | 10 |

## Perturbation Outcomes

| Perturbation | Status | Transition | Conservative | Perturbed distribution |
| --- | --- | --- | --- | --- |
| Lower predicate confidence below threshold | PASS | accepted->uncertain | True | `{'accepted': 17, 'rejected': 10, 'uncertain': 7}` |
| Remove a required support predicate | PASS | accepted->rejected | True | `{'accepted': 1, 'rejected': 33}` |
| Replace an object type with an incompatible type | PASS | accepted->uncertain | True | `{'rejected': 33, 'uncertain': 1}` |
| Inject an explicit error action or incompatibility | PASS | accepted->rejected | True | `{'rejected': 34}` |
| Remove a produced effect used by later steps | PASS | accepted->rejected | True | `{'accepted': 10, 'rejected': 18, 'uncertain': 6}` |

## Detailed Perturbations

### E5.1: Lower predicate confidence below threshold

- Result: **PASS**
- Target step: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_1` (event_1)
- Baseline to perturbed status: `accepted->uncertain`
- What was changed: all symbolic predicates attached to the selected accepted step had their confidence lowered.
- New confidence: `0.175`; this is below `tau_unc`, so the evidence is insufficient for acceptance.
- Changed predicates: 8 records covering `hasAction, hasInstallTarget, hasLabel, hasTimeWindow, isA, requiresInstalledBefore, usesObject`.
- Why this target: the step was accepted and depended on an earlier produced effect, making it suitable for checking whether low-confidence evidence prevents clean acceptance.
- Observed consequence: the target became `uncertain`; the perturbed clip distribution was `{'accepted': 17, 'rejected': 10, 'uncertain': 7}`.
- Diagnostic evidence: the degraded confidence was preserved in the explanation trace (`diagnostic_visible=True`).
- Exact perturbed input: `evidence/perturbation_inputs/confidence_degradation/perturbation.json`
- Complete rerun output: `evidence/perturbation_outputs/confidence_degradation/`

### E5.2: Remove a required support predicate

- Result: **PASS**
- Target step: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_1` (event_1)
- Baseline to perturbed status: `accepted->rejected`
- What was changed: the earlier `produces(...)` constraint that supplied Layer 4 dependency support was removed from the copied `inferred_constraints.csv`.
- Scope clarification: this scenario removes produced-effect evidence, the closest symbolic dependency input consumed by Layer 4; it does not edit the baseline artifacts.
- Removed producer: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_0` (event_0).
- Removed constraint: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_0::c::effect_install_component_on_target::0_0::produces::step__raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy`.
- Requirement that lost support: `installed(base, workspace)`.
- Observed consequence: the target became `rejected`, gained 1 missing requirement(s), and dependency support was removed (`True`).
- Trace evidence: the missing requirement is visible in the perturbed explanation trace (`True`).
- Exact perturbed input: `evidence/perturbation_inputs/missing_support_predicate/perturbation.json`
- Complete rerun output: `evidence/perturbation_outputs/missing_support_predicate/`

### E5.3: Replace an object type with an incompatible type

- Result: **PASS**
- Target step: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_0` (event_0)
- Baseline to perturbed status: `accepted->uncertain`
- What was changed: the selected step's `usesObject(step, object)` predicate was rewritten to reference a different plausible component already present in the clip.
- Object substitution: `base` -> `front_bracket`.
- Changed predicate: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_0::p::usesObject::step_raw_cad_dataset_all_test_clips_od_plus_psr_error_hints_test_p1_08_assy_0_1_event_0_base`.
- Why this is semantically incompatible: the remaining type, install-target, and domain predicates still describe the original object, so the substituted object no longer forms a coherent rule match.
- Observed consequence: the target became `uncertain` with 1 warning(s) and 0 missing requirement(s).
- Trace evidence: the replacement object is preserved in the explanation trace (`True`).
- Exact perturbed input: `evidence/perturbation_inputs/incompatible_object_type/perturbation.json`
- Complete rerun output: `evidence/perturbation_outputs/incompatible_object_type/`

### E5.4: Inject an explicit error action or incompatibility

- Result: **PASS**
- Target step: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_0` (event_0)
- Baseline to perturbed status: `accepted->rejected`
- What was changed: an additional high-confidence `hasAction(step, error)` predicate was injected while retaining the step's original object evidence.
- Injected predicate: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_0::evaluation5::injected_error`.
- Rule response: the existing compatibility rule inferred `incompatibleAction(step, object, error)`, which Layer 4 treats as a hard violation.
- Observed consequence: the target became `rejected` and incompatibility evidence was visible (`True`).
- Dependency consequence: later steps still supported by the rejected target: `[]`.
- Rejected-support violations: `0`.
- Exact perturbed input: `evidence/perturbation_inputs/injected_error_action/perturbation.json`
- Complete rerun output: `evidence/perturbation_outputs/injected_error_action/`

### E5.5: Remove a produced effect used by later steps

- Result: **PASS**
- Target step: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_10` (event_10)
- Baseline to perturbed status: `accepted->rejected`
- What was changed: one frequently reused `produces(installed, component, target)` constraint was removed before rerunning Layer 4.
- Producer step: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_6` (event_6).
- Removed effect constraint: `step::raw_cad_dataset__all_test_clips::od_plus_psr_error_hints::test_p1::08_assy_0_1::event_6::c::effect_install_component_on_target::0_0::produces::step__raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy`.
- Baseline dependent steps affected: `7`.
- Per-step consequences:
  - `event_7`: `uncertain -> rejected`, support removed=`True`, missing requirements=`3`.
  - `event_10`: `accepted -> rejected`, support removed=`True`, missing requirements=`1`.
  - `event_12`: `accepted -> rejected`, support removed=`True`, missing requirements=`1`.
  - `event_25`: `accepted -> rejected`, support removed=`True`, missing requirements=`1`.
  - `event_27`: `accepted -> rejected`, support removed=`True`, missing requirements=`1`.
  - `event_28`: `accepted -> rejected`, support removed=`True`, missing requirements=`1`.
  - `event_30`: `accepted -> rejected`, support removed=`True`, missing requirements=`1`.
- Rejected-support violations after removal: `0`.
- Trace evidence was preserved for every affected dependent: `True`.
- Exact perturbed input: `evidence/perturbation_inputs/removed_produced_effect/perturbation.json`
- Complete rerun output: `evidence/perturbation_outputs/removed_produced_effect/`


## Conservative Transition Summary

- Conservative transitions: 5
- Accepted remained accepted after direct degradation: 0

## Traceability Summary

- Scenarios with preserved traces: 5 of 5 executed scenarios.
- Rejected-support violations after perturbation: 0.
- Exact perturbations and complete rerun artifacts are stored under `evidence/perturbation_inputs/` and `evidence/perturbation_outputs/`.

## Limitations

The perturbations operate on already-symbolic inputs and use one representative clip. They test conservative reasoning behavior and diagnostic traceability, not perception quality, dataset-wide robustness, or semantic correctness against expert annotations.

Status totals: PASS=5, FAIL=0, WARNING=0, SKIPPED=0.
