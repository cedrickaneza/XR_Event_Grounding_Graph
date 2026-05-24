import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.layer4_validation import Layer4Inputs, run_layer4_validation


def test_layer4_writes_thesis_records_and_uses_only_prior_effects_for_preconditions(tmp_path: Path) -> None:
    steps_path = tmp_path / "step_records.jsonl"
    predicates_path = tmp_path / "predicates.jsonl"
    constraints_path = tmp_path / "inferred_constraints.csv"
    config_path = tmp_path / "thesis_rules.yaml"
    output_path = tmp_path / "validation_records.jsonl"

    config_path.write_text(json.dumps({"validation": {"tau_acc": 0.7, "tau_unc": 0.35}}), encoding="utf-8")
    _write_jsonl(
        steps_path,
        [
            {"id": "s1", "index": 1},
            {"id": "s2", "index": 2},
            {"id": "s3", "index": 3},
            {"id": "s4", "index": 4},
        ],
    )
    _write_jsonl(
        predicates_path,
        [
            _predicate("p1", "s1", "hasAction", ["s1", "install"]),
            _predicate("p2", "s2", "hasAction", ["s2", "install"]),
            _predicate("p3", "s3", "hasAction", ["s3", "install"]),
            _predicate("p4", "s4", "hasAction", ["s4", "install"]),
            _predicate("p5", "s4", "usesTool", ["s4", "driver"]),
        ],
    )
    _write_constraints_csv(
        constraints_path,
        [
            _constraint("c1", "s1", "produces", "expected_effect", ["s1", "installed", "base", "workspace"]),
            _constraint("c2", "s2", "requires", "inferred_precondition", ["s2", "installed", "base", "workspace"]),
            _constraint("c3", "s2", "produces", "expected_effect", ["s2", "installed", "bracket", "base"]),
            _constraint("c4", "s3", "requires", "inferred_precondition", ["s3", "installed", "cover", "bracket"]),
            _constraint("c5", "s3", "produces", "expected_effect", ["s3", "installed", "cover", "bracket"]),
            _constraint("c6", "s4", "requires", "inferred_precondition", ["s4", "installed", "cover", "bracket"]),
            _constraint("c7", "s4", "requiresTool", "required_tool", ["s4", "driver"]),
        ],
    )

    result = run_layer4_validation(
        Layer4Inputs(
            step_records_path=steps_path,
            predicates_path=predicates_path,
            constraints_path=constraints_path,
            output_path=output_path,
            config_path=config_path,
        )
    )

    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    by_step = {record["step_id"]: record for record in records}
    assert result["validation_records"] == 4
    assert result["validation_config_path"] == str(config_path)
    assert result["warnings"] == 0
    assert result["tau_acc"] == 0.7
    assert result["tau_unc"] == 0.35
    assert (tmp_path / "step_validations.csv").exists()
    assert (tmp_path / "explanation_traces.json").exists()

    assert by_step["s2"]["status"] == "accepted"
    assert by_step["s2"]["dependency_support"] == [
        {
            "required_condition": {"name": "installed", "args": ["base", "workspace"]},
            "supporting_effect": {
                "type": "previous_produced_effect",
                "constraint_id": "c1",
                "step_id": "s1",
                "producer_status": "accepted",
                "provisional": False,
                "args": ["s1", "installed", "base", "workspace"],
                "condition": {"name": "installed", "args": ["base", "workspace"]},
            },
        }
    ]

    assert by_step["s3"]["status"] == "rejected"
    assert by_step["s3"]["missing_requirements"][0]["constraint_id"] == "c4"
    assert by_step["s3"]["missing_requirements"][0]["support"] is None
    assert by_step["s4"]["status"] == "uncertain"
    assert {item["constraint_id"] for item in by_step["s4"]["supported_requirements"]} == {"c7"}
    assert {item["constraint_id"] for item in by_step["s4"]["missing_requirements"]} == {"c6"}
    assert by_step["s1"]["evidence_constraints"][0]["support"] == {
        "type": "same_step_constraint",
        "notes": "Constraint observed in the step.",
    }

    traces = json.loads((tmp_path / "explanation_traces.json").read_text(encoding="utf-8"))
    trace = next(item for item in traces if item["step_id"] == "s2")
    assert set(trace) == {
        "step_id",
        "predicate_evidence",
        "constraint_evidence",
        "incompatibility_evidence",
        "dependency_evidence",
        "missing_requirements",
        "invalidated_effects",
        "produced_effect_lifecycle",
        "warnings",
        "diagnostics",
        "status",
        "confidence",
    }
    assert trace["dependency_evidence"] == by_step["s2"]["dependency_support"]
    assert trace["constraint_evidence"][0]["support"] == {
        "type": "same_step_constraint",
        "notes": "Constraint observed in the step.",
    }


def test_layer4_remove_invalidates_active_installed_effect_but_preserves_history(tmp_path: Path) -> None:
    steps_path = tmp_path / "step_records.jsonl"
    predicates_path = tmp_path / "predicates.jsonl"
    constraints_path = tmp_path / "inferred_constraints.csv"
    config_path = tmp_path / "thesis_rules.yaml"
    output_path = tmp_path / "validation_records.jsonl"

    config_path.write_text(json.dumps({"validation": {"tau_acc": 0.7, "tau_unc": 0.35}}), encoding="utf-8")
    _write_jsonl(
        steps_path,
        [
            {"id": "install_wheel", "index": 1},
            {"id": "remove_wheel", "index": 2},
            {"id": "later_needs_wheel", "index": 3},
        ],
    )
    _write_jsonl(
        predicates_path,
        [
            _predicate("p1", "install_wheel", "hasAction", ["install_wheel", "install"]),
            _predicate("p2", "remove_wheel", "hasAction", ["remove_wheel", "remove"]),
            _predicate("p3", "later_needs_wheel", "hasAction", ["later_needs_wheel", "install"]),
        ],
    )
    _write_constraints_csv(
        constraints_path,
        [
            _constraint("c_install_effect", "install_wheel", "produces", "expected_effect", ["install_wheel", "installed", "front_wheel_assy", "front_chassis"]),
            _constraint("c_remove_req", "remove_wheel", "requires", "inferred_precondition", ["remove_wheel", "installed", "front_wheel_assy", "front_chassis"]),
            _constraint("c_remove_effect", "remove_wheel", "produces", "expected_effect", ["remove_wheel", "removed", "front_wheel_assy", "front_chassis"]),
            _constraint("c_later_req", "later_needs_wheel", "requires", "inferred_precondition", ["later_needs_wheel", "installed", "front_wheel_assy", "front_chassis"]),
        ],
    )

    run_layer4_validation(
        Layer4Inputs(
            step_records_path=steps_path,
            predicates_path=predicates_path,
            constraints_path=constraints_path,
            output_path=output_path,
            config_path=config_path,
        )
    )

    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    by_step = {record["step_id"]: record for record in records}
    assert by_step["remove_wheel"]["status"] == "accepted"
    assert by_step["remove_wheel"]["dependency_support"][0]["supporting_effect"]["step_id"] == "install_wheel"
    assert by_step["remove_wheel"]["invalidated_effects"] == [
        {
            "condition": {"name": "installed", "args": ["front_wheel_assy", "front_chassis"]},
            "produced_by_step_id": "install_wheel",
            "produced_by_constraint_id": "c_install_effect",
            "producer_status": "accepted",
            "invalidated_by_step_id": "remove_wheel",
            "invalidated_by_effect": {"name": "removed", "args": ["front_wheel_assy", "front_chassis"]},
            "invalidated_by_constraint_id": "c_remove_effect",
        }
    ]
    assert by_step["install_wheel"]["produced_effect_lifecycle"] == [
        {
            "type": "previous_produced_effect",
            "constraint_id": "c_install_effect",
            "step_id": "install_wheel",
            "producer_status": "accepted",
            "provisional": False,
            "args": ["install_wheel", "installed", "front_wheel_assy", "front_chassis"],
            "condition": {"name": "installed", "args": ["front_wheel_assy", "front_chassis"]},
            "conf": 0.9,
            "effect_lifecycle_status": "invalidated",
            "invalidated_by_constraint_id": "c_remove_effect",
        }
    ]
    assert by_step["remove_wheel"]["produced_effect_lifecycle"][0]["effect_lifecycle_status"] == "active"
    assert by_step["later_needs_wheel"]["status"] == "rejected"
    assert by_step["later_needs_wheel"]["missing_requirements"][0]["constraint_id"] == "c_later_req"
    diagnostics = list(csv.DictReader((tmp_path / "effect_history_diagnostics.csv").open(newline="", encoding="utf-8")))
    assert any(row["event"] == "produced_effect" and row["condition_name"] == "installed" for row in diagnostics)
    assert any(row["event"] == "invalidated_effect" and row["condition_name"] == "installed" for row in diagnostics)


def test_layer4_remove_with_uncertain_prior_install_is_uncertain_and_provisional(tmp_path: Path) -> None:
    steps_path = tmp_path / "step_records.jsonl"
    predicates_path = tmp_path / "predicates.jsonl"
    constraints_path = tmp_path / "inferred_constraints.csv"
    config_path = tmp_path / "thesis_rules.yaml"
    output_path = tmp_path / "validation_records.jsonl"

    config_path.write_text(json.dumps({"validation": {"tau_acc": 0.7, "tau_unc": 0.35}}), encoding="utf-8")
    _write_jsonl(steps_path, [{"id": "install_wheel", "index": 1}, {"id": "remove_wheel", "index": 2}])
    _write_jsonl(
        predicates_path,
        [
            _predicate("p1", "install_wheel", "hasAction", ["install_wheel", "install"], conf=0.5),
            _predicate("p2", "remove_wheel", "hasAction", ["remove_wheel", "remove"]),
        ],
    )
    _write_constraints_csv(
        constraints_path,
        [
            _constraint("c_install_effect", "install_wheel", "produces", "expected_effect", ["install_wheel", "installed", "front_wheel_assy", "front_chassis"], conf=0.5),
            _constraint("c_remove_req", "remove_wheel", "requires", "inferred_precondition", ["remove_wheel", "installed", "front_wheel_assy", "front_chassis"]),
            _constraint("c_remove_effect", "remove_wheel", "produces", "expected_effect", ["remove_wheel", "removed", "front_wheel_assy", "front_chassis"]),
        ],
    )

    run_layer4_validation(
        Layer4Inputs(
            step_records_path=steps_path,
            predicates_path=predicates_path,
            constraints_path=constraints_path,
            output_path=output_path,
            config_path=config_path,
        )
    )

    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    by_step = {record["step_id"]: record for record in records}
    assert by_step["install_wheel"]["status"] == "uncertain"
    assert by_step["remove_wheel"]["status"] == "uncertain"
    support = by_step["remove_wheel"]["dependency_support"][0]["supporting_effect"]
    assert support["step_id"] == "install_wheel"
    assert support["producer_status"] == "uncertain"
    assert support["provisional"] is True


def test_layer4_remove_rejected_without_active_installed_support(tmp_path: Path) -> None:
    steps_path = tmp_path / "step_records.jsonl"
    predicates_path = tmp_path / "predicates.jsonl"
    constraints_path = tmp_path / "inferred_constraints.csv"
    config_path = tmp_path / "thesis_rules.yaml"
    output_path = tmp_path / "validation_records.jsonl"

    config_path.write_text(json.dumps({"validation": {"tau_acc": 0.7, "tau_unc": 0.35}}), encoding="utf-8")
    _write_jsonl(steps_path, [{"id": "remove_wheel", "index": 1}])
    _write_jsonl(predicates_path, [_predicate("p1", "remove_wheel", "hasAction", ["remove_wheel", "remove"])])
    _write_constraints_csv(
        constraints_path,
        [
            _constraint("c_remove_req", "remove_wheel", "requires", "inferred_precondition", ["remove_wheel", "installed", "front_wheel_assy", "front_chassis"]),
            _constraint("c_remove_effect", "remove_wheel", "produces", "expected_effect", ["remove_wheel", "removed", "front_wheel_assy", "front_chassis"]),
        ],
    )

    run_layer4_validation(
        Layer4Inputs(
            step_records_path=steps_path,
            predicates_path=predicates_path,
            constraints_path=constraints_path,
            output_path=output_path,
            config_path=config_path,
        )
    )

    record = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["status"] == "rejected"
    assert record["missing_requirements"][0]["constraint_id"] == "c_remove_req"


def test_layer4_remove_rejected_when_prior_install_was_rejected(tmp_path: Path) -> None:
    steps_path = tmp_path / "step_records.jsonl"
    predicates_path = tmp_path / "predicates.jsonl"
    constraints_path = tmp_path / "inferred_constraints.csv"
    config_path = tmp_path / "thesis_rules.yaml"
    output_path = tmp_path / "validation_records.jsonl"

    config_path.write_text(json.dumps({"validation": {"tau_acc": 0.7, "tau_unc": 0.35}}), encoding="utf-8")
    _write_jsonl(steps_path, [{"id": "install_wheel", "index": 1}, {"id": "remove_wheel", "index": 2}])
    _write_jsonl(
        predicates_path,
        [
            _predicate("p1", "install_wheel", "hasAction", ["install_wheel", "install"]),
            _predicate("p2", "remove_wheel", "hasAction", ["remove_wheel", "remove"]),
        ],
    )
    _write_constraints_csv(
        constraints_path,
        [
            _constraint("c_install_req", "install_wheel", "requires", "inferred_precondition", ["install_wheel", "installed", "missing_base", "workspace"]),
            _constraint("c_install_effect", "install_wheel", "produces", "expected_effect", ["install_wheel", "installed", "front_wheel_assy", "front_chassis"]),
            _constraint("c_remove_req", "remove_wheel", "requires", "inferred_precondition", ["remove_wheel", "installed", "front_wheel_assy", "front_chassis"]),
            _constraint("c_remove_effect", "remove_wheel", "produces", "expected_effect", ["remove_wheel", "removed", "front_wheel_assy", "front_chassis"]),
        ],
    )

    run_layer4_validation(
        Layer4Inputs(
            step_records_path=steps_path,
            predicates_path=predicates_path,
            constraints_path=constraints_path,
            output_path=output_path,
            config_path=config_path,
        )
    )

    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    by_step = {record["step_id"]: record for record in records}
    assert by_step["install_wheel"]["status"] == "rejected"
    assert by_step["remove_wheel"]["status"] == "rejected"
    assert by_step["remove_wheel"]["dependency_support"] == []


def test_layer4_propagates_no_applicable_rule_warning_and_marks_uncertain(tmp_path: Path) -> None:
    steps_path = tmp_path / "step_records.jsonl"
    predicates_path = tmp_path / "predicates.jsonl"
    constraints_path = tmp_path / "inferred_constraints.csv"
    diagnostics_path = tmp_path / "rule_coverage_diagnostics.csv"
    config_path = tmp_path / "thesis_rules.yaml"
    output_path = tmp_path / "validation_records.jsonl"

    config_path.write_text(json.dumps({"validation": {"tau_acc": 0.7, "tau_unc": 0.35}}), encoding="utf-8")
    _write_jsonl(steps_path, [{"id": "s_remove", "index": 9}])
    _write_jsonl(
        predicates_path,
        [
            _predicate("p_action", "s_remove", "hasAction", ["s_remove", "remove"]),
            _predicate("p_object", "s_remove", "usesObject", ["s_remove", "front_wheel_assy"]),
        ],
    )
    _write_constraints_csv(constraints_path, [])
    _write_rule_coverage_csv(
        diagnostics_path,
        [
            {
                "step_id": "s_remove",
                "step_index": 9,
                "action_name": "remove",
                "object_args": ["front_wheel_assy"],
                "predicate_count": 2,
                "matched_rule_count": 0,
                "produced_constraint_count": 0,
                "has_expected_effect": False,
                "has_requirement": False,
                "has_incompatibility": False,
                "has_meaningful_evidence": True,
                "has_rule_coverage": False,
                "warning_code": "no_applicable_rule",
                "warning_message": "Step has predicate evidence but no Layer 3 rule produced constraints.",
                "evidence_predicates": [{"id": "p_action"}, {"id": "p_object"}],
                "suggested_fix": "Add an explicit rule for this action or treat it as unsupported in the domain model.",
            }
        ],
    )

    run_layer4_validation(
        Layer4Inputs(
            step_records_path=steps_path,
            predicates_path=predicates_path,
            constraints_path=constraints_path,
            rule_coverage_path=diagnostics_path,
            output_path=output_path,
            config_path=config_path,
        )
    )

    record = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["status"] == "uncertain"
    assert record["has_rule_coverage"] is False
    assert record["produced_constraint_count"] == 0
    assert record["warnings"][0]["warning_code"] == "no_applicable_rule"
    assert record["trace"]["warnings"] == record["warnings"]
    with open(tmp_path / "step_validations.csv", newline="", encoding="utf-8") as f:
        csv_record = next(csv.DictReader(f))
    assert "no_applicable_rule" in csv_record["warnings"]


def _predicate(predicate_id: str, step_id: str, name: str, args: list[str], conf: float = 0.9) -> dict[str, object]:
    return {"id": predicate_id, "step_id": step_id, "name": name, "args": args, "conf": conf}


def _constraint(
    constraint_id: str,
    step_id: str,
    name: str,
    kind: str,
    args: list[str],
    conf: float = 0.9,
) -> dict[str, object]:
    return {
        "constraint_id": constraint_id,
        "step_id": step_id,
        "name": name,
        "kind": kind,
        "args": args,
        "conf": conf,
        "rule_id": "test_rule",
        "rule_type": kind,
        "threshold": 0.7,
        "aggregation": "min",
        "evidence_predicate_ids": [],
        "status": "inferred",
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _write_constraints_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "constraint_id",
        "step_id",
        "name",
        "kind",
        "args",
        "conf",
        "rule_id",
        "rule_type",
        "threshold",
        "aggregation",
        "evidence_predicate_ids",
        "status",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "args": json.dumps(row["args"]), "evidence_predicate_ids": "[]"})


def _write_rule_coverage_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "step_id",
        "step_index",
        "action_name",
        "object_args",
        "predicate_count",
        "matched_rule_count",
        "produced_constraint_count",
        "has_expected_effect",
        "has_requirement",
        "has_incompatibility",
        "has_meaningful_evidence",
        "has_rule_coverage",
        "warning_code",
        "warning_message",
        "evidence_predicates",
        "suggested_fix",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "object_args": json.dumps(row["object_args"]),
                    "evidence_predicates": json.dumps(row["evidence_predicates"]),
                    "has_expected_effect": str(row["has_expected_effect"]).lower(),
                    "has_requirement": str(row["has_requirement"]).lower(),
                    "has_incompatibility": str(row["has_incompatibility"]).lower(),
                    "has_meaningful_evidence": str(row["has_meaningful_evidence"]).lower(),
                    "has_rule_coverage": str(row["has_rule_coverage"]).lower(),
                }
            )
