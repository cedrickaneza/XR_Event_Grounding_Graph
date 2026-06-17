from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

from src.layer3_inference import Layer3Inputs, run_layer3_inference
from src.layer4_validation import Layer4Inputs, run_layer4_validation


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "24_evaluate_symbolic_input_degradation.py"
SPEC = importlib.util.spec_from_file_location("evaluation5", SCRIPT_PATH)
evaluation5 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = evaluation5
SPEC.loader.exec_module(evaluation5)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def make_context(tmp_path: Path) -> evaluation5.EvaluationContext:
    return evaluation5.EvaluationContext(
        project_root=tmp_path,
        clip_result_id="clip",
        reasoning_dir=tmp_path / "results" / "reasoning_layers" / "clip",
        output_dir=tmp_path / "docs" / "reasoning_layers" / "Evaluation5",
    )


def write_config(path: Path) -> None:
    config = {
        "predicate_vocabulary": {
            "hasAction": {"arity": 2},
            "usesObject": {"arity": 2},
            "isA": {"arity": 2},
            "hasInstallTarget": {"arity": 2},
            "requiresInstalledBefore": {"arity": 3},
            "requires": {"arity": 4},
            "produces": {"arity": 4},
            "incompatibleAction": {"arity": 3},
        },
        "defaults": {"threshold": 0.7, "aggregation": "min"},
        "validation": {"tau_acc": 0.7, "tau_unc": 0.35},
        "rules": [
            {
                "id": "requires_target",
                "type": "inferred_precondition",
                "threshold": 0.7,
                "antecedents": [
                    {"name": "hasAction", "args": ["?s", "install"]},
                    {"name": "usesObject", "args": ["?s", "?component"]},
                    {"name": "isA", "args": ["?component", "Component"]},
                    {
                        "name": "requiresInstalledBefore",
                        "args": ["?component", "?target", "?support"],
                    },
                ],
                "constraints": [
                    {
                        "name": "requires",
                        "kind": "inferred_precondition",
                        "args": ["?s", "installed", "?target", "?support"],
                    }
                ],
            },
            {
                "id": "produces_install",
                "type": "expected_effect",
                "threshold": 0.7,
                "antecedents": [
                    {"name": "hasAction", "args": ["?s", "install"]},
                    {"name": "usesObject", "args": ["?s", "?component"]},
                    {"name": "isA", "args": ["?component", "Component"]},
                    {"name": "hasInstallTarget", "args": ["?component", "?target"]},
                ],
                "constraints": [
                    {
                        "name": "produces",
                        "kind": "expected_effect",
                        "args": ["?s", "installed", "?component", "?target"],
                    }
                ],
            },
            {
                "id": "error_incompatible",
                "type": "compatibility",
                "threshold": 0.7,
                "antecedents": [
                    {"name": "hasAction", "args": ["?s", "error"]},
                    {"name": "usesObject", "args": ["?s", "?object"]},
                ],
                "constraints": [
                    {
                        "name": "incompatibleAction",
                        "kind": "compatibility",
                        "args": ["?s", "?object", "error"],
                    }
                ],
            },
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")


def predicate(pid: str, step: str, name: str, args: list[str]) -> dict:
    return {"id": pid, "step_id": step, "name": name, "args": args, "conf": 1.0}


def write_complete_fixture(ctx: evaluation5.EvaluationContext) -> list[dict]:
    write_config(ctx.config_path)
    write_jsonl(
        ctx.reasoning_dir / "step_records.jsonl",
        [{"id": "s0", "index": 0}, {"id": "s1", "index": 1}, {"id": "s2", "index": 2}],
    )
    write_jsonl(
        ctx.reasoning_dir / "predicates.jsonl",
        [
            predicate("p0a", "s0", "hasAction", ["s0", "install"]),
            predicate("p0o", "s0", "usesObject", ["s0", "base"]),
            predicate("p0t", "s0", "isA", ["base", "Component"]),
            predicate("p0i", "s0", "hasInstallTarget", ["base", "workspace"]),
            predicate("p1a", "s1", "hasAction", ["s1", "install"]),
            predicate("p1o", "s1", "usesObject", ["s1", "part"]),
            predicate("p1t", "s1", "isA", ["part", "Component"]),
            predicate("p1i", "s1", "hasInstallTarget", ["part", "base"]),
            predicate("p1r", "s1", "requiresInstalledBefore", ["part", "base", "workspace"]),
            predicate("p2a", "s2", "hasAction", ["s2", "install"]),
            predicate("p2o", "s2", "usesObject", ["s2", "child"]),
            predicate("p2t", "s2", "isA", ["child", "Component"]),
            predicate("p2i", "s2", "hasInstallTarget", ["child", "part"]),
            predicate("p2r", "s2", "requiresInstalledBefore", ["child", "part", "base"]),
        ],
    )
    run_layer3_inference(
        Layer3Inputs(
            step_records_path=ctx.reasoning_dir / "step_records.jsonl",
            predicates_path=ctx.reasoning_dir / "predicates.jsonl",
            rules_path=ctx.config_path,
            output_path=ctx.reasoning_dir / "inferred_constraints.csv",
        )
    )
    run_layer4_validation(
        Layer4Inputs(
            step_records_path=ctx.reasoning_dir / "step_records.jsonl",
            predicates_path=ctx.reasoning_dir / "predicates.jsonl",
            constraints_path=ctx.reasoning_dir / "inferred_constraints.csv",
            rule_coverage_path=ctx.reasoning_dir / "rule_coverage_diagnostics.csv",
            output_path=ctx.reasoning_dir / "validation_records.jsonl",
            config_path=ctx.config_path,
        )
    )
    return evaluation5.load_jsonl(ctx.reasoning_dir / "validation_records.jsonl")


def test_lowering_confidence_changes_accepted_status(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    baseline = write_complete_fixture(ctx)
    result = evaluation5.scenario_confidence(ctx, baseline, 0.35)
    assert result.status == "PASS"
    assert result.baseline_status == "accepted"
    assert result.perturbed_status in {"uncertain", "rejected"}


def test_removing_required_support_creates_missing_requirement(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    baseline = write_complete_fixture(ctx)
    result = evaluation5.scenario_missing_support(ctx, baseline)
    assert result.status == "PASS"
    assert result.details["missing_requirement_count"] > 0
    assert result.details["dependency_support_removed"] is True


def test_removing_required_support_does_not_preserve_acceptance(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    baseline = write_complete_fixture(ctx)
    result = evaluation5.scenario_missing_support(ctx, baseline)
    assert result.baseline_status == "accepted"
    assert result.perturbed_status != "accepted"


def test_replacing_object_type_is_conservative(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    baseline = write_complete_fixture(ctx)
    result = evaluation5.scenario_incompatible_object(ctx, baseline)
    assert result.status == "PASS"
    assert result.perturbed_status in {"uncertain", "rejected"}
    assert result.details["corrupted_input_visible_in_trace"] is True


def test_injecting_hard_incompatibility_rejects_step(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    baseline = write_complete_fixture(ctx)
    result = evaluation5.scenario_error_action(ctx, baseline)
    assert result.status == "PASS"
    assert result.perturbed_status == "rejected"
    assert result.details["incompatibility_visible"] is True


def test_rejected_perturbed_step_does_not_support_later_dependencies(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    baseline = write_complete_fixture(ctx)
    result = evaluation5.scenario_error_action(ctx, baseline)
    assert result.details["later_supported_step_ids"] == []
    assert result.details["rejected_support_violations"] == []


def test_removing_produced_effect_downgrades_later_dependencies(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    baseline = write_complete_fixture(ctx)
    result = evaluation5.scenario_removed_effect(ctx, baseline)
    assert result.status == "PASS"
    assert result.details["affected_steps"]
    assert all(row["support_removed"] for row in result.details["affected_steps"])
    assert all(row["perturbed_status"] != "accepted" for row in result.details["affected_steps"])


def test_perturbed_outputs_preserve_explanation_traces(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    baseline = write_complete_fixture(ctx)
    results = [
        evaluation5.scenario_confidence(ctx, baseline, 0.35),
        evaluation5.scenario_missing_support(ctx, baseline),
        evaluation5.scenario_incompatible_object(ctx, baseline),
        evaluation5.scenario_error_action(ctx, baseline),
        evaluation5.scenario_removed_effect(ctx, baseline),
    ]
    assert all(result.details["trace_preserved"] is True for result in results)


def test_missing_required_baseline_artifacts_create_report(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_config(ctx.config_path)
    result = evaluation5.evaluate(ctx)
    assert result["missing"]
    assert (ctx.output_dir / "missing_data_report.md").exists()


def test_skipped_used_when_no_suitable_candidate_exists(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_config(ctx.config_path)
    ctx.reasoning_dir.mkdir(parents=True)
    write_jsonl(ctx.reasoning_dir / "step_records.jsonl", [{"id": "s0", "index": 0}])
    write_jsonl(ctx.reasoning_dir / "predicates.jsonl", [])
    write_csv(
        ctx.reasoning_dir / "inferred_constraints.csv",
        [],
        [
            "constraint_id", "step_id", "name", "kind", "args", "conf", "rule_id",
            "rule_type", "threshold", "aggregation", "evidence_predicate_ids", "status",
        ],
    )
    write_csv(
        ctx.reasoning_dir / "rule_coverage_diagnostics.csv",
        [],
        [
            "step_id", "step_index", "action_name", "object_args", "predicate_count",
            "matched_rule_count", "produced_constraint_count", "has_expected_effect",
            "has_requirement", "has_incompatibility", "has_meaningful_evidence",
            "has_rule_coverage", "warning_code", "warning_message",
            "evidence_predicates", "suggested_fix",
        ],
    )
    baseline = [{"step_id": "s0", "index": 0, "status": "uncertain", "dependency_support": []}]
    result = evaluation5.scenario_confidence(ctx, baseline, 0.35)
    assert result.status == "SKIPPED"


def test_report_explains_each_perturbation_in_detail(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_complete_fixture(ctx)
    evaluation5.evaluate(ctx)
    report = (ctx.output_dir / "evaluation5_report.md").read_text(encoding="utf-8")
    assert "## Detailed Perturbations" in report
    assert "all symbolic predicates attached to the selected accepted step" in report
    assert "the earlier `produces(...)` constraint" in report
    assert "Object substitution:" in report
    assert "`hasAction(step, error)` predicate was injected" in report
    assert "Per-step consequences:" in report
    assert "evidence/perturbation_inputs/" in report
