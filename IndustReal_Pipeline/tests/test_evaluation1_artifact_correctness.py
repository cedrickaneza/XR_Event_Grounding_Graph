from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "20_evaluate_pipeline_artifact_correctness.py"
SPEC = importlib.util.spec_from_file_location("evaluation1", SCRIPT_PATH)
evaluation1 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = evaluation1
SPEC.loader.exec_module(evaluation1)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def make_context(tmp_path: Path) -> evaluation1.EvaluationContext:
    project_root = tmp_path
    return evaluation1.EvaluationContext(
        project_root=project_root,
        run_id="run",
        clip_result_id="clip_result",
        neo4j_dir=project_root / "neo4j",
        reasoning_dir=project_root / "reasoning",
        graph_dir=project_root / "graph",
        output_dir=project_root / "docs" / "reasoning_layers" / "Evaluation1",
        upstream_result_dir=project_root / "tmp" / "run",
        preserved_tarball=project_root / "results" / "preserved_tmp" / "run.tar.gz",
    )


def write_minimal_artifacts(ctx: evaluation1.EvaluationContext) -> None:
    write_csv(
        ctx.neo4j_dir / "nodes_events.csv",
        [
            {
                "event_id:ID(AssemblyEvent)": "event_0",
                "clip_result_id": "clip_result",
            },
            {
                "event_id:ID(AssemblyEvent)": "event_1",
                "clip_result_id": "clip_result",
            },
        ],
        ["event_id:ID(AssemblyEvent)", "clip_result_id"],
    )
    steps = [
        {"id": "step_0", "record_type": "step_segment", "source_event_id": "event_0", "index": 0},
        {"id": "step_1", "record_type": "step_segment", "source_event_id": "event_1", "index": 1},
    ]
    write_jsonl(ctx.reasoning_dir / "step_records.jsonl", steps)
    predicates = [
        {"id": "pred_0", "step_id": "step_0", "name": "hasAction", "args": ["step_0", "install"], "conf": 1.0, "source": {"file": "nodes_events.csv"}},
        {"id": "pred_1", "step_id": "step_1", "name": "hasAction", "args": ["step_1", "install"], "conf": 0.8, "source": {"file": "nodes_events.csv"}},
    ]
    write_jsonl(ctx.reasoning_dir / "predicates.jsonl", predicates)
    write_csv(
        ctx.reasoning_dir / "inferred_constraints.csv",
        [
            {"constraint_id": "c0", "step_id": "step_0", "name": "produces", "kind": "expected_effect", "args": "[]", "conf": "1.0", "rule_id": "r0", "status": "inferred"},
            {"constraint_id": "c1", "step_id": "step_1", "name": "requires", "kind": "inferred_precondition", "args": "[]", "conf": "0.8", "rule_id": "r1", "status": "inferred"},
        ],
        ["constraint_id", "step_id", "name", "kind", "args", "conf", "rule_id", "status"],
    )
    write_csv(
        ctx.reasoning_dir / "rule_coverage_diagnostics.csv",
        [
            {"step_id": "step_0", "step_index": "0", "action_name": "install", "object_args": "[\"base\"]", "predicate_count": "1", "matched_rule_count": "1", "produced_constraint_count": "1", "has_expected_effect": "true", "has_requirement": "false", "has_incompatibility": "false", "has_meaningful_evidence": "true", "has_rule_coverage": "true", "warning_code": "", "warning_message": "", "evidence_predicates": "[]", "suggested_fix": ""},
            {"step_id": "step_1", "step_index": "1", "action_name": "install", "object_args": "[\"bracket\"]", "predicate_count": "1", "matched_rule_count": "1", "produced_constraint_count": "1", "has_expected_effect": "false", "has_requirement": "true", "has_incompatibility": "false", "has_meaningful_evidence": "true", "has_rule_coverage": "true", "warning_code": "", "warning_message": "", "evidence_predicates": "[]", "suggested_fix": ""},
        ],
        ["step_id", "step_index", "action_name", "object_args", "predicate_count", "matched_rule_count", "produced_constraint_count", "has_expected_effect", "has_requirement", "has_incompatibility", "has_meaningful_evidence", "has_rule_coverage", "warning_code", "warning_message", "evidence_predicates", "suggested_fix"],
    )
    validations = [
        {"step_id": "step_0", "status": "accepted", "index": 0, "confidence": 1.0, "trace": {"step_id": "step_0", "status": "accepted", "confidence": 1.0, "predicate_evidence": [], "constraint_evidence": [], "missing_requirements": [], "incompatibility_evidence": [], "dependency_evidence": []}},
        {"step_id": "step_1", "status": "accepted", "index": 1, "confidence": 0.8, "trace": {"step_id": "step_1", "status": "accepted", "confidence": 0.8, "predicate_evidence": [], "constraint_evidence": [], "missing_requirements": [], "incompatibility_evidence": [], "dependency_evidence": []}},
    ]
    write_jsonl(ctx.reasoning_dir / "validation_records.jsonl", validations)
    write_json(ctx.reasoning_dir / "explanation_traces.json", [row["trace"] for row in validations])
    write_csv(ctx.reasoning_dir / "step_validations.csv", [{"step_id": "step_0"}, {"step_id": "step_1"}], ["step_id"])
    graph_nodes = [
        {"id": "Step::step_0", "type": "Step", "properties": json.dumps({"step_id": "step_0", "index": 0})},
        {"id": "Step::step_1", "type": "Step", "properties": json.dumps({"step_id": "step_1", "index": 1})},
    ]
    graph_edges = [
        {"source": "Step::step_0", "target": "Step::step_1", "type": "NEXT", "properties": "{}"},
        {"source": "Step::step_1", "target": "Step::step_0", "type": "DEPENDS_ON", "properties": json.dumps({"provisional": False})},
    ]
    write_json(ctx.graph_dir / "procedural_reasoning_graph.json", {"schema_version": "1.0", "graph_name": "procedural_reasoning_graph", "nodes": graph_nodes, "edges": graph_edges})
    write_csv(ctx.graph_dir / "procedural_reasoning_graph_nodes.csv", graph_nodes, ["id", "type", "properties"])
    write_csv(ctx.graph_dir / "procedural_reasoning_graph_edges.csv", graph_edges, ["source", "target", "type", "properties"])
    ctx.upstream_result_dir.mkdir(parents=True, exist_ok=True)


def checks_by_name(result: dict) -> dict[str, dict]:
    return {row["check_name"]: row for row in result["checks"]}


def test_missing_artifact_detection(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    missing = evaluation1.detect_missing_data(ctx)
    missing_paths = {Path(item["path"]).name for item in missing}
    assert "nodes_events.csv" in missing_paths
    assert "step_records.jsonl" in missing_paths
    assert "procedural_reasoning_graph_edges.csv" in missing_paths


def test_valid_artifacts_pass_core_checks(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    result = evaluation1.evaluate(ctx)
    checks = checks_by_name(result)
    assert checks["Step records produced"]["status"] == "PASS"
    assert checks["Predicate records produced"]["status"] == "PASS"
    assert checks["Input order preserved"]["status"] == "PASS"
    assert checks["Rejected-step dependency rule respected"]["status"] == "PASS"


def test_evaluation1_accepts_uncovered_step_when_warning_reaches_validation_and_graph(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    write_csv(
        ctx.reasoning_dir / "rule_coverage_diagnostics.csv",
        [
            {"step_id": "step_0", "step_index": "0", "action_name": "install", "object_args": "[\"base\"]", "predicate_count": "1", "matched_rule_count": "1", "produced_constraint_count": "1", "has_expected_effect": "true", "has_requirement": "false", "has_incompatibility": "false", "has_meaningful_evidence": "true", "has_rule_coverage": "true", "warning_code": "", "warning_message": "", "evidence_predicates": "[]", "suggested_fix": ""},
            {"step_id": "step_1", "step_index": "1", "action_name": "remove", "object_args": "[\"front_wheel_assy\"]", "predicate_count": "2", "matched_rule_count": "0", "produced_constraint_count": "0", "has_expected_effect": "false", "has_requirement": "false", "has_incompatibility": "false", "has_meaningful_evidence": "true", "has_rule_coverage": "false", "warning_code": "no_applicable_rule", "warning_message": "Step has predicate evidence but no Layer 3 rule produced constraints.", "evidence_predicates": "[]", "suggested_fix": "Add an explicit rule for this action or treat it as unsupported in the domain model."},
        ],
        ["step_id", "step_index", "action_name", "object_args", "predicate_count", "matched_rule_count", "produced_constraint_count", "has_expected_effect", "has_requirement", "has_incompatibility", "has_meaningful_evidence", "has_rule_coverage", "warning_code", "warning_message", "evidence_predicates", "suggested_fix"],
    )
    validations = [
        {"step_id": "step_0", "status": "accepted", "index": 0, "confidence": 1.0, "trace": {"step_id": "step_0", "status": "accepted", "confidence": 1.0, "predicate_evidence": [], "constraint_evidence": [], "missing_requirements": [], "incompatibility_evidence": [], "dependency_evidence": [], "warnings": [], "diagnostics": {}}},
        {"step_id": "step_1", "status": "uncertain", "index": 1, "confidence": 0.8, "warnings": [{"warning_code": "no_applicable_rule"}], "diagnostics": {"rule_coverage": {"has_rule_coverage": False}}, "has_rule_coverage": False, "trace": {"step_id": "step_1", "status": "uncertain", "confidence": 0.8, "predicate_evidence": [], "constraint_evidence": [], "missing_requirements": [], "incompatibility_evidence": [], "dependency_evidence": [], "warnings": [{"warning_code": "no_applicable_rule"}], "diagnostics": {"rule_coverage": {"has_rule_coverage": False}}}},
    ]
    write_jsonl(ctx.reasoning_dir / "validation_records.jsonl", validations)
    graph_nodes = [
        {"id": "Step::step_0", "type": "Step", "properties": json.dumps({"step_id": "step_0", "index": 0})},
        {"id": "Step::step_1", "type": "Step", "properties": json.dumps({"step_id": "step_1", "index": 1, "warning_count": 1, "has_rule_coverage": False})},
    ]
    write_csv(ctx.graph_dir / "procedural_reasoning_graph_nodes.csv", graph_nodes, ["id", "type", "properties"])
    result = evaluation1.evaluate(ctx)
    checks = checks_by_name(result)
    assert checks["Layer 3 constraints produced"]["status"] == "PASS"
    assert checks["Layer 4 validation records produced"]["status"] == "PASS"
    assert checks["Graph export produced"]["status"] == "PASS"
    assert checks["Rule coverage diagnostics"]["status"] == "WARNING"
    assert checks["Rule coverage diagnostics"]["message"] == "One unsupported removal action was detected and reported with a rule-coverage warning."


def test_invalid_step_records_report_missing_input_step(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    write_jsonl(ctx.reasoning_dir / "step_records.jsonl", [{"id": "step_0", "record_type": "step_segment", "source_event_id": "event_0", "index": 0}])
    result = evaluation1.evaluate(ctx)
    assert checks_by_name(result)["Step records produced"]["status"] == "FAIL"


def test_predicates_referencing_nonexistent_step_fail(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    write_jsonl(
        ctx.reasoning_dir / "predicates.jsonl",
        [{"id": "pred_bad", "step_id": "missing_step", "name": "hasAction", "args": [], "conf": 0.5, "source": {}}],
    )
    result = evaluation1.evaluate(ctx)
    assert checks_by_name(result)["Predicate records produced"]["status"] == "FAIL"


def test_invalid_confidence_values_fail(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    write_jsonl(
        ctx.reasoning_dir / "predicates.jsonl",
        [{"id": "pred_bad", "step_id": "step_0", "name": "hasAction", "args": [], "conf": 1.5, "source": {}}],
    )
    result = evaluation1.evaluate(ctx)
    assert checks_by_name(result)["Predicate records produced"]["status"] == "FAIL"


def test_validation_records_missing_status_fail(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    write_jsonl(
        ctx.reasoning_dir / "validation_records.jsonl",
        [{"step_id": "step_0", "index": 0, "trace": {"step_id": "step_0"}}, {"step_id": "step_1", "status": "accepted", "index": 1, "trace": {"step_id": "step_1"}}],
    )
    result = evaluation1.evaluate(ctx)
    assert checks_by_name(result)["Layer 4 validation records produced"]["status"] == "FAIL"


def test_graph_edges_referencing_nonexistent_nodes_fail(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    write_csv(
        ctx.graph_dir / "procedural_reasoning_graph_edges.csv",
        [{"source": "Step::step_0", "target": "Step::missing", "type": "NEXT", "properties": "{}"}],
        ["source", "target", "type", "properties"],
    )
    result = evaluation1.evaluate(ctx)
    assert checks_by_name(result)["Graph export produced"]["status"] == "FAIL"


def test_next_edges_that_violate_order_fail(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    write_csv(
        ctx.graph_dir / "procedural_reasoning_graph_edges.csv",
        [{"source": "Step::step_1", "target": "Step::step_0", "type": "NEXT", "properties": "{}"}],
        ["source", "target", "type", "properties"],
    )
    result = evaluation1.evaluate(ctx)
    assert checks_by_name(result)["Input order preserved"]["status"] == "FAIL"


def test_depends_on_edges_from_rejected_steps_fail(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_minimal_artifacts(ctx)
    validations = [
        {"step_id": "step_0", "status": "rejected", "index": 0, "confidence": 0.1, "trace": {"step_id": "step_0"}},
        {"step_id": "step_1", "status": "accepted", "index": 1, "confidence": 0.8, "trace": {"step_id": "step_1"}},
    ]
    write_jsonl(ctx.reasoning_dir / "validation_records.jsonl", validations)
    result = evaluation1.evaluate(ctx)
    assert checks_by_name(result)["Rejected-step dependency rule respected"]["status"] == "FAIL"
