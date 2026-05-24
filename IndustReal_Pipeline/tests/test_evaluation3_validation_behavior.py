from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "22_evaluate_validation_behavior.py"
SPEC = importlib.util.spec_from_file_location("evaluation3", SCRIPT_PATH)
evaluation3 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = evaluation3
SPEC.loader.exec_module(evaluation3)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def constraint(cid: str, step_id: str, name: str, args: list, kind: str = "inferred_precondition") -> dict:
    return {
        "constraint_id": cid,
        "step_id": step_id,
        "name": name,
        "kind": kind,
        "args": json.dumps(args),
        "conf": "1.0",
    }


def validation(step_id: str, status: str, **extra) -> dict:
    row = {
        "step_id": step_id,
        "index": int(step_id[-1]) if step_id[-1].isdigit() else 0,
        "status": status,
        "confidence": 1.0,
        "supported_requirements": [],
        "missing_requirements": [],
        "dependency_support": [],
        "incompatibilities": [],
        "produced_effect_lifecycle": [],
    }
    row.update(extra)
    return row


def support(requirement_id: str = "req1", producer_step: str = "s0", producer_constraint: str = "prod0") -> dict:
    return {
        "constraint_id": requirement_id,
        "name": "requires",
        "kind": "inferred_precondition",
        "args": ["s1", "installed", "base", "workspace"],
        "support": {
            "type": "previous_produced_effect",
            "step_id": producer_step,
            "constraint_id": producer_constraint,
            "condition": {"name": "installed", "args": ["base", "workspace"]},
            "producer_status": "accepted",
        },
    }


def test_requirement_support_detection() -> None:
    constraints = [constraint("req1", "s1", "requires", ["s1", "installed", "base", "workspace"])]
    validations = [
        validation("s0", "accepted", produced_effect_lifecycle=[{"constraint_id": "prod0", "effect_lifecycle_status": "active"}]),
        validation("s1", "accepted", supported_requirements=[support()], dependency_support=[{"supporting_effect": support()["support"]}]),
    ]
    rows, checks = evaluation3.evaluate_requirement_support(constraints, validations)
    assert checks[0].status == "PASS"
    assert rows[0]["support_status"] == "supported"
    assert rows[0]["supporting_step_id"] == "s0"


def test_missing_requirement_detection() -> None:
    req = {"constraint_id": "req1", "name": "requires", "args": ["s1", "installed", "base", "workspace"], "support": None}
    constraints = [constraint("req1", "s1", "requires", ["s1", "installed", "base", "workspace"])]
    rows, checks = evaluation3.evaluate_requirement_support(constraints, [validation("s1", "uncertain", missing_requirements=[req])])
    assert checks[0].status == "PASS"
    assert rows[0]["support_status"] == "missing"


def test_hard_incompatibility_rejection() -> None:
    constraints = [constraint("inc1", "s1", "incompatibleAction", ["s1", "base", "error"], "compatibility")]
    validations = [validation("s1", "rejected", incompatibilities=[{"constraint_id": "inc1"}])]
    traces = {"s1": {"incompatibility_evidence": [{"constraint_id": "inc1", "name": "incompatibleAction"}]}}
    rows, checks = evaluation3.evaluate_incompatibility(constraints, validations, traces)
    assert checks[0].status == "PASS"
    assert rows[0]["rejected_due_to_incompatibility"] is True


def test_incompatibility_not_rejected_fails() -> None:
    constraints = [constraint("inc1", "s1", "incompatibleAction", ["s1", "base", "error"], "compatibility")]
    validations = [validation("s1", "accepted", incompatibilities=[{"constraint_id": "inc1"}])]
    traces = {"s1": {"incompatibility_evidence": [{"constraint_id": "inc1", "name": "incompatibleAction"}]}}
    _, checks = evaluation3.evaluate_incompatibility(constraints, validations, traces)
    assert checks[0].status == "FAIL"


def test_rejected_step_isolation() -> None:
    validations = [
        validation("s1", "rejected", produced_effect_lifecycle=[{"constraint_id": "prod1", "effect_lifecycle_status": "inactive_rejected"}]),
        validation("s2", "accepted"),
    ]
    rows, checks = evaluation3.evaluate_rejected_isolation(validations)
    assert checks[0].status == "PASS"
    assert rows[0]["inactive_rejected_effect_count"] == 1


def test_rejected_support_violation() -> None:
    validations = [
        validation("s1", "rejected", produced_effect_lifecycle=[{"constraint_id": "prod1", "effect_lifecycle_status": "inactive_rejected"}]),
        validation("s2", "accepted", dependency_support=[{"supporting_effect": {"step_id": "s1", "constraint_id": "prod1"}}]),
    ]
    rows, checks = evaluation3.evaluate_rejected_isolation(validations)
    assert checks[0].status == "FAIL"
    assert rows[0]["used_as_later_support"] is True


def test_removal_invalidation() -> None:
    constraints = [
        constraint("req1", "s1", "requires", ["s1", "installed", "base", "workspace"]),
        constraint("rem1", "s1", "produces", ["s1", "removed", "base", "workspace"], "expected_effect"),
    ]
    validations = [
        validation("s0", "accepted", produced_effect_lifecycle=[{"constraint_id": "prod0", "effect_lifecycle_status": "invalidated", "invalidated_by_constraint_id": "rem1"}]),
        validation("s1", "accepted", dependency_support=[{"required_condition": {"name": "installed", "args": ["base", "workspace"]}, "supporting_effect": {"step_id": "s0", "constraint_id": "prod0"}}]),
    ]
    rows, checks = evaluation3.evaluate_removal_invalidation(constraints, validations)
    assert checks[0].status == "PASS"
    assert rows[0]["installed_effect_lifecycle_status"] == "invalidated"


def test_invalidated_effect_reused_later_fails() -> None:
    constraints = [
        constraint("req1", "s1", "requires", ["s1", "installed", "base", "workspace"]),
        constraint("rem1", "s1", "produces", ["s1", "removed", "base", "workspace"], "expected_effect"),
    ]
    validations = [
        validation("s0", "accepted", produced_effect_lifecycle=[{"constraint_id": "prod0", "effect_lifecycle_status": "invalidated", "invalidated_by_constraint_id": "rem1"}]),
        validation("s1", "accepted", dependency_support=[{"required_condition": {"name": "installed", "args": ["base", "workspace"]}, "supporting_effect": {"step_id": "s0", "constraint_id": "prod0"}}]),
        validation("s2", "accepted", dependency_support=[{"required_condition": {"name": "installed", "args": ["base", "workspace"]}, "supporting_effect": {"step_id": "s0", "constraint_id": "prod0"}}]),
    ]
    rows, checks = evaluation3.evaluate_removal_invalidation(constraints, validations)
    assert checks[0].status == "FAIL"
    assert rows[0]["invalidated_effect_used_later"] is True


def test_reduced_confidence_perturbation(tmp_path: Path) -> None:
    clip_dir = tmp_path / "results" / "reasoning_layers" / "clip"
    write_jsonl(clip_dir / "step_records.jsonl", [{"id": "s0", "index": 0}, {"id": "s1", "index": 1}])
    write_jsonl(
        clip_dir / "predicates.jsonl",
        [
            {"id": "p0", "step_id": "s0", "name": "hasAction", "args": ["s0", "install"], "conf": 1.0},
            {"id": "p1", "step_id": "s1", "name": "hasAction", "args": ["s1", "install"], "conf": 1.0},
        ],
    )
    write_csv(
        clip_dir / "inferred_constraints.csv",
        [
            constraint("prod0", "s0", "produces", ["s0", "installed", "base", "workspace"], "expected_effect"),
            constraint("req1", "s1", "requires", ["s1", "installed", "base", "workspace"]),
            constraint("prod1", "s1", "produces", ["s1", "installed", "part", "base"], "expected_effect"),
        ],
        ["constraint_id", "step_id", "name", "kind", "args", "conf"],
    )
    write_csv(
        clip_dir / "rule_coverage_diagnostics.csv",
        [
            {"step_id": "s0", "step_index": "0", "action_name": "install", "object_args": "[]", "matched_rule_count": "1", "produced_constraint_count": "1", "has_rule_coverage": "true", "warning_code": ""},
            {"step_id": "s1", "step_index": "1", "action_name": "install", "object_args": "[]", "matched_rule_count": "2", "produced_constraint_count": "2", "has_rule_coverage": "true", "warning_code": ""},
        ],
        ["step_id", "step_index", "action_name", "object_args", "matched_rule_count", "produced_constraint_count", "has_rule_coverage", "warning_code"],
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "thesis_rules.yaml").write_text("validation:\n  tau_acc: 0.7\n  tau_unc: 0.35\n", encoding="utf-8")
    ctx = evaluation3.EvaluationContext(
        project_root=tmp_path,
        clip_result_id="clip",
        reasoning_dir=clip_dir,
        graph_dir=None,
        output_dir=tmp_path / "docs" / "reasoning_layers" / "Evaluation3",
    )
    baseline = [
        validation("s0", "accepted"),
        validation("s1", "accepted", dependency_support=[{"supporting_effect": {"step_id": "s0", "constraint_id": "prod0"}}]),
    ]
    rows, checks, _ = evaluation3.perturb_confidence(ctx, baseline, 0.7, 0.35)
    assert checks[0].status == "PASS"
    assert rows[0]["baseline_status"] == "accepted"
    assert rows[0]["perturbed_status"] == "uncertain"


def test_missing_selected_clip(tmp_path: Path) -> None:
    ctx = evaluation3.EvaluationContext(
        project_root=tmp_path,
        clip_result_id="missing_clip",
        reasoning_dir=tmp_path / "missing_clip",
        graph_dir=None,
        output_dir=tmp_path / "docs" / "reasoning_layers" / "Evaluation3",
    )
    result = evaluation3.evaluate(ctx)
    assert result["checks"][0]["status"] == "FAIL"
    assert (ctx.output_dir / "missing_data_report.md").exists()
