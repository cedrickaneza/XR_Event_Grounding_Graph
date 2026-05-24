import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.layer3_inference import Layer3Inputs, run_layer3_inference
from src.layer3_reasoning_adapter import (
    DEFAULT_CSV_DIR,
    DEFAULT_DOMAIN_CONFIG_PATH,
    DEFAULT_PREDICATE_CONFIG_PATH,
    AdapterInputs,
    build_reasoning_adapter_outputs,
)


SAMPLE_CLIP_RESULT_ID = "raw_cad_dataset__all_test_clips::od_only::test_p1::03_assy_0_1"


def test_ontology_config_emits_generic_class_facts_and_type_defaults(tmp_path: Path) -> None:
    output_dir = tmp_path / "reasoning"
    adapter_result = build_reasoning_adapter_outputs(
        AdapterInputs(
            csv_dir=DEFAULT_CSV_DIR,
            run_id="test",
            output_dir=output_dir,
            clip_result_id=SAMPLE_CLIP_RESULT_ID,
            predicate_config_path=DEFAULT_PREDICATE_CONFIG_PATH,
            domain_config_path=DEFAULT_DOMAIN_CONFIG_PATH,
        )
    )

    predicates = _read_jsonl(output_dir / "predicates.jsonl")
    steps = _read_jsonl(output_dir / "step_records.jsonl")
    is_a = {tuple(item["args"]) for item in predicates if item["name"] == "isA"}
    labels = {tuple(item["args"]) for item in predicates if item["name"] == "hasLabel"}
    required_tools = {tuple(item["args"]) for item in predicates if item["name"] == "hasRequiredTool"}
    time_windows = {
        item["source_event_id"].rsplit("::", 1)[-1]: item["time_window"]
        for item in steps
    }
    has_time_window = {
        item["step_id"].rsplit("::", 1)[-1]: tuple(item["args"][1:])
        for item in predicates
        if item["name"] == "hasTimeWindow"
    }

    assert adapter_result["step_records"] == 11
    assert adapter_result["adapter_config_path"] is not None
    assert time_windows["event_0"]["start_s"] == 70.9
    assert time_windows["event_0"]["end_s"] == 118.7
    assert time_windows["event_1"]["end_s"] == 118.7
    assert time_windows["event_2"]["end_s"] == 118.7
    assert has_time_window["event_0"] == (70.9, 118.7)
    assert ("base", "Base") in is_a
    assert ("base", "base") not in is_a
    assert ("base", "base") in labels
    assert ("front_chassis", "Chassis") in is_a
    assert ("rear_chassis", "Chassis") in is_a
    assert ("front_chassis_pin", "ChassisPin") in is_a
    assert ("front_rear_chassis_pin", "ChassisPin") in is_a
    assert ("rear_rear_chassis_pin", "ChassisPin") in is_a
    assert ("front_bracket_screw", "Screw") in is_a
    assert ("front_bracket_screw", "Fastener") in is_a
    assert ("front_bracket_screw", "screwdriver") in required_tools

    constraints_path = output_dir / "inferred_constraints.csv"
    result = run_layer3_inference(
        Layer3Inputs(
            step_records_path=output_dir / "step_records.jsonl",
            predicates_path=output_dir / "predicates.jsonl",
            rules_path=DEFAULT_PREDICATE_CONFIG_PATH,
            output_path=constraints_path,
        )
    )
    constraints = _read_constraints(constraints_path)
    diagnostics = _read_constraints(output_dir / "rule_coverage_diagnostics.csv")

    assert result["constraints_by_rule"]["effect_install_component_on_target"] >= 10
    assert result["constraints_by_rule"]["precondition_remove_requires_component_installed"] == 1
    assert result["constraints_by_rule"]["effect_remove_component_from_target"] == 1
    assert result["rule_coverage_warnings"] == 0
    assert result["constraints_by_rule"]["implicit_domain_required_condition"] == 3
    assert result["constraints_by_rule"]["safety_domain_requirement"] == 3
    assert result["constraints_by_rule"]["tool_domain_requirement"] == 1
    assert any(
        row["name"] == "requiresTool" and json.loads(row["args"]) == [row["step_id"], "screwdriver"]
        for row in constraints
    )
    remove_diag = next(row for row in diagnostics if row["action_name"] == "remove")
    assert remove_diag["warning_code"] == ""
    assert remove_diag["has_rule_coverage"] == "true"
    assert remove_diag["produced_constraint_count"] == "2"
    assert any(
        row["rule_id"] == "precondition_remove_requires_component_installed"
        and row["name"] == "requires"
        and json.loads(row["args"]) == [row["step_id"], "installed", "front_wheel_assy", "front_chassis"]
        for row in constraints
    )
    assert any(
        row["rule_id"] == "effect_remove_component_from_target"
        and row["name"] == "produces"
        and json.loads(row["args"]) == [row["step_id"], "removed", "front_wheel_assy", "front_chassis"]
        for row in constraints
    )
    install_diag = next(row for row in diagnostics if row["action_name"] == "install" and int(row["produced_constraint_count"]) > 0)
    assert install_diag["warning_code"] == ""


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_constraints(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
