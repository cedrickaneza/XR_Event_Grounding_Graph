import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.procedural_reasoning_graph import (
    ProceduralReasoningGraphInputs,
    build_procedural_reasoning_graph,
)


def test_builds_procedural_reasoning_graph_from_validation_records(tmp_path: Path) -> None:
    validations_path = tmp_path / "validation_records.jsonl"
    step_records_path = tmp_path / "step_records.jsonl"
    output_dir = tmp_path / "graph"
    _write_jsonl(
        step_records_path,
        [
            {
                "id": "s1",
                "clip_result_id": "run::od_only::test_p1::03_assy_0_1",
                "run_id": "run",
                "mode": "od_only",
                "archive_name": "test_p1",
                "clip": "03_assy_0_1",
                "action": {"name": "install", "event_type": "INSTALL", "description": "Install base"},
                "objects": [{"id": "component::base", "label": "base", "type": "base"}],
            },
            {
                "id": "s2",
                "clip_result_id": "run::od_only::test_p1::03_assy_0_1",
                "run_id": "run",
                "mode": "od_only",
                "archive_name": "test_p1",
                "clip": "03_assy_0_1",
                "action": {"name": "install", "event_type": "INSTALL", "description": "Install bracket"},
                "objects": [{"id": "component::bracket", "label": "bracket", "type": "bracket"}],
            },
        ],
    )
    _write_jsonl(
        validations_path,
        [
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s1",
                "source_event_id": "event_1",
                "index": 1,
                "status": "accepted",
                "confidence": 0.9,
                "conf": 0.9,
                "evidence_predicates": [
                    _predicate("p1", "s1", "hasAction", ["s1", "install"]),
                    _predicate("p2", "s1", "usesObject", ["s1", "base"]),
                    _predicate("p3", "s1", "isA", ["base", "Base"]),
                ],
                "evidence_constraints": [
                    _constraint("c1", "produces", "expected_effect", ["s1", "installed", "base", "workspace"])
                ],
                "produced_effects": [
                    _constraint("c1", "produces", "expected_effect", ["s1", "installed", "base", "workspace"])
                ],
                "supported_requirements": [],
                "missing_requirements": [],
                "dependency_support": [],
                "incompatibilities": [],
                "tool_requirements": [],
                "safety_requirements": [],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s2",
                "source_event_id": "event_2",
                "index": 2,
                "status": "uncertain",
                "confidence": 0.8,
                "conf": 0.8,
                "evidence_predicates": [
                    _predicate("p4", "s2", "hasAction", ["s2", "install"]),
                    _predicate("p5", "s2", "usesObject", ["s2", "bracket"]),
                ],
                "evidence_constraints": [
                    _constraint("c2", "requires", "inferred_precondition", ["s2", "installed", "base", "workspace"]),
                    _constraint("c3", "produces", "expected_effect", ["s2", "installed", "bracket", "base"]),
                ],
                "produced_effects": [
                    _constraint("c3", "produces", "expected_effect", ["s2", "installed", "bracket", "base"])
                ],
                "supported_requirements": [
                    {
                        **_constraint("c2", "requires", "inferred_precondition", ["s2", "installed", "base", "workspace"]),
                        "support": {
                            "type": "previous_produced_effect",
                            "constraint_id": "c1",
                            "step_id": "s1",
                            "args": ["s1", "installed", "base", "workspace"],
                            "condition": {"name": "installed", "args": ["base", "workspace"]},
                        },
                    }
                ],
                "missing_requirements": [],
                "dependency_support": [
                    {
                        "required_condition": {"name": "installed", "args": ["base", "workspace"]},
                        "supporting_effect": {
                            "type": "previous_produced_effect",
                            "constraint_id": "c1",
                            "step_id": "s1",
                            "args": ["s1", "installed", "base", "workspace"],
                            "condition": {"name": "installed", "args": ["base", "workspace"]},
                        },
                    }
                ],
                "incompatibilities": [],
                "tool_requirements": [],
                "safety_requirements": [],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s3",
                "source_event_id": "event_3",
                "index": 3,
                "status": "uncertain",
                "confidence": 0.9,
                "conf": 0.9,
                "evidence_predicates": [_predicate("p6", "s3", "hasAction", ["s3", "remove"])],
                "evidence_constraints": [],
                "warnings": [{"warning_code": "no_applicable_rule", "action_name": "remove"}],
                "diagnostics": {
                    "rule_coverage": {
                        "has_rule_coverage": False,
                        "matched_rule_count": 0,
                        "produced_constraint_count": 0,
                        "has_expected_effect": False,
                        "action_name": "remove",
                    }
                },
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
        ],
    )

    result = build_procedural_reasoning_graph(
        ProceduralReasoningGraphInputs(
            validations_path=validations_path,
            output_dir=output_dir,
            step_records_path=step_records_path,
        )
    )

    graph = json.loads((output_dir / "procedural_reasoning_graph.json").read_text(encoding="utf-8"))
    assert graph["graph_name"] == "procedural_reasoning_graph"
    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    step = nodes_by_id["Step::s1"]["properties"]
    predicate = nodes_by_id["Predicate::p2"]["properties"]
    constraint = nodes_by_id["Constraint::c2"]["properties"]
    rule = nodes_by_id["Rule::rule_inferred_precondition"]["properties"]
    entity = nodes_by_id["Entity::base"]["properties"]
    source = next(node["properties"] for node in graph["nodes"] if node["type"] == "Source")
    assert step["display_name"] == "Step 1"
    assert step["display_label"] == "Step 1 [accepted]"
    assert step["short_id"] == "event_1"
    assert step["clip_result_id"] == "run::od_only::test_p1::03_assy_0_1"
    assert step["archive_name"] == "test_p1"
    assert step["clip"] == "03_assy_0_1"
    assert step["action_name"] == "install"
    assert step["action_event_type"] == "INSTALL"
    assert step["action_description"] == "Install base"
    assert step["object_labels"] == ["base"]
    assert step["object_summary"] == "base"
    assert "conf" not in step
    unsupported_step = nodes_by_id["Step::s3"]["properties"]
    assert unsupported_step["warning_count"] == 1
    assert unsupported_step["warnings"][0]["warning_code"] == "no_applicable_rule"
    assert unsupported_step["has_rule_coverage"] is False
    assert unsupported_step["produced_constraint_count"] == 0
    assert unsupported_step["unsupported_action"] is True
    assert unsupported_step["unsupported_action_name"] == "remove"
    assert predicate["display_name"] == "usesObject"
    assert predicate["display_label"] == "usesObject(s1, base)"
    assert constraint["display_name"] == "requires installed"
    assert constraint["display_label"] == "requires installed(base, workspace) [supported]"
    assert rule["display_name"] == "rule_inferred_precondition"
    assert entity["display_name"] == "base"
    assert source["display_name"] == "test:test.csv"
    assert result["node_counts"]["Step"] == 3
    assert result["node_counts"]["Rule"] == 2
    assert result["edge_counts"]["NEXT"] == 2
    assert result["edge_counts"]["DEPENDS_ON"] == 1
    assert result["edge_counts"]["PRODUCES"] == 2
    assert result["edge_counts"]["REQUIRES"] == 1
    assert result["edge_counts"]["SUPPORTED_BY"] == 1
    assert result["step_status_counts"] == {"accepted": 1, "uncertain": 2}
    assert (output_dir / "procedural_reasoning_graph_nodes.csv").exists()
    assert (output_dir / "procedural_reasoning_graph_edges.csv").exists()
    with open(output_dir / "procedural_reasoning_graph_nodes.csv", newline="", encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
    csv_step = next(row for row in csv_rows if row["id"] == "Step::s1")
    csv_step_props = json.loads(csv_step["properties"])
    assert csv_step_props["display_name"] == "Step 1"
    assert csv_step_props["display_label"] == "Step 1 [accepted]"


def test_graph_exposes_remove_semantics_and_invalidated_effects(tmp_path: Path) -> None:
    validations_path = tmp_path / "validation_records.jsonl"
    output_dir = tmp_path / "graph"
    installed_effect = _constraint("c_install", "produces", "expected_effect", ["s1", "installed", "wheel", "hub"])
    remove_requires = _constraint("c_remove_requires", "requires", "inferred_precondition", ["s2", "installed", "wheel", "hub"])
    remove_effect = _constraint("c_removed", "produces", "expected_effect", ["s2", "removed", "wheel", "hub"])
    rejected_effect = _constraint("c_rejected_install", "produces", "expected_effect", ["s4", "installed", "axle", "hub"])
    _write_jsonl(
        validations_path,
        [
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s1",
                "source_event_id": "event_1",
                "index": 1,
                "status": "accepted",
                "confidence": 0.9,
                "evidence_predicates": [_predicate("p1", "s1", "hasAction", ["s1", "install"])],
                "evidence_constraints": [installed_effect],
                "produced_effects": [installed_effect],
                "supported_requirements": [],
                "missing_requirements": [],
                "dependency_support": [],
                "invalidated_effects": [],
                "produced_effect_lifecycle": [
                    {
                        "constraint_id": "c_install",
                        "step_id": "s1",
                        "condition": {"name": "installed", "args": ["wheel", "hub"]},
                        "effect_lifecycle_status": "invalidated",
                        "invalidated_by_constraint_id": "c_removed",
                    }
                ],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s2",
                "source_event_id": "event_2",
                "index": 2,
                "status": "accepted",
                "confidence": 0.9,
                "evidence_predicates": [_predicate("p2", "s2", "hasAction", ["s2", "remove"])],
                "evidence_constraints": [remove_requires, remove_effect],
                "produced_effects": [remove_effect],
                "supported_requirements": [
                    {
                        **remove_requires,
                        "support": {
                            "type": "previous_produced_effect",
                            "constraint_id": "c_install",
                            "step_id": "s1",
                            "args": ["s1", "installed", "wheel", "hub"],
                            "condition": {"name": "installed", "args": ["wheel", "hub"]},
                            "producer_status": "accepted",
                            "provisional": False,
                        },
                    }
                ],
                "missing_requirements": [],
                "dependency_support": [
                    {
                        "required_condition": {"name": "installed", "args": ["wheel", "hub"]},
                        "supporting_effect": {
                            "type": "previous_produced_effect",
                            "constraint_id": "c_install",
                            "step_id": "s1",
                            "args": ["s1", "installed", "wheel", "hub"],
                            "condition": {"name": "installed", "args": ["wheel", "hub"]},
                            "producer_status": "accepted",
                            "provisional": False,
                        },
                    }
                ],
                "invalidated_effects": [
                    {
                        "condition": {"name": "installed", "args": ["wheel", "hub"]},
                        "produced_by_step_id": "s1",
                        "produced_by_constraint_id": "c_install",
                        "invalidated_by_step_id": "s2",
                        "invalidated_by_effect": {"name": "removed", "args": ["wheel", "hub"]},
                        "invalidated_by_constraint_id": "c_removed",
                    }
                ],
                "produced_effect_lifecycle": [
                    {
                        "constraint_id": "c_removed",
                        "step_id": "s2",
                        "condition": {"name": "removed", "args": ["wheel", "hub"]},
                        "effect_lifecycle_status": "active",
                        "invalidated_by_constraint_id": None,
                    }
                ],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s3",
                "source_event_id": "event_3",
                "index": 3,
                "status": "rejected",
                "confidence": 0.2,
                "evidence_predicates": [_predicate("p3", "s3", "hasAction", ["s3", "install"])],
                "evidence_constraints": [_constraint("c_after_remove_requires", "requires", "inferred_precondition", ["s3", "installed", "wheel", "hub"])],
                "produced_effects": [],
                "supported_requirements": [],
                "missing_requirements": [_constraint("c_after_remove_requires", "requires", "inferred_precondition", ["s3", "installed", "wheel", "hub"])],
                "dependency_support": [],
                "invalidated_effects": [],
                "produced_effect_lifecycle": [],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s4",
                "source_event_id": "event_4",
                "index": 4,
                "status": "rejected",
                "confidence": 0.2,
                "evidence_predicates": [_predicate("p4", "s4", "hasAction", ["s4", "install"])],
                "evidence_constraints": [rejected_effect],
                "produced_effects": [rejected_effect],
                "supported_requirements": [],
                "missing_requirements": [],
                "dependency_support": [],
                "invalidated_effects": [],
                "produced_effect_lifecycle": [
                    {
                        "constraint_id": "c_rejected_install",
                        "step_id": "s4",
                        "condition": {"name": "installed", "args": ["axle", "hub"]},
                        "effect_lifecycle_status": "inactive_rejected",
                        "invalidated_by_constraint_id": None,
                    }
                ],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s5",
                "source_event_id": "event_5",
                "index": 5,
                "status": "rejected",
                "confidence": 0.2,
                "evidence_predicates": [_predicate("p5", "s5", "hasAction", ["s5", "remove"])],
                "evidence_constraints": [_constraint("c_rejected_support_requires", "requires", "inferred_precondition", ["s5", "installed", "axle", "hub"])],
                "produced_effects": [],
                "supported_requirements": [],
                "missing_requirements": [_constraint("c_rejected_support_requires", "requires", "inferred_precondition", ["s5", "installed", "axle", "hub"])],
                "dependency_support": [],
                "invalidated_effects": [],
                "produced_effect_lifecycle": [],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
        ],
    )

    build_procedural_reasoning_graph(
        ProceduralReasoningGraphInputs(validations_path=validations_path, output_dir=output_dir)
    )

    graph = json.loads((output_dir / "procedural_reasoning_graph.json").read_text(encoding="utf-8"))
    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    edges = graph["edges"]
    remove_step = nodes_by_id["Step::s2"]["properties"]
    assert remove_step["invalidates_effect_count"] == 1
    assert remove_step["invalidated_effects"][0]["condition"] == {"name": "installed", "args": ["wheel", "hub"]}
    assert nodes_by_id["Constraint::c_install"]["properties"]["effect_lifecycle_status"] == "invalidated"
    assert nodes_by_id["Constraint::c_install"]["properties"]["invalidated_by_constraint_id"] == "c_removed"
    assert nodes_by_id["Constraint::c_removed"]["properties"]["effect_lifecycle_status"] == "active"
    assert nodes_by_id["Constraint::c_rejected_install"]["properties"]["effect_lifecycle_status"] == "inactive_rejected"
    assert nodes_by_id["Constraint::c_remove_requires"]["properties"]["display_name"] == "requires installed"
    assert nodes_by_id["Constraint::c_removed"]["properties"]["display_name"] == "produces removed"
    assert _has_edge(edges, "Step::s2", "Constraint::c_remove_requires", "REQUIRES")
    assert _has_edge(edges, "Step::s2", "Constraint::c_removed", "PRODUCES")
    assert _has_edge(edges, "Constraint::c_install", "Constraint::c_removed", "INVALIDATED_BY")
    depends_on_install = _edge(edges, "Step::s2", "Step::s1", "DEPENDS_ON")
    assert depends_on_install["properties"]["provisional"] is False
    assert not _has_edge(edges, "Step::s3", "Step::s1", "DEPENDS_ON")
    assert not _has_edge(edges, "Step::s5", "Step::s4", "DEPENDS_ON")


def test_graph_marks_uncertain_dependency_support_as_provisional(tmp_path: Path) -> None:
    validations_path = tmp_path / "validation_records.jsonl"
    output_dir = tmp_path / "graph"
    _write_jsonl(
        validations_path,
        [
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s1",
                "source_event_id": "event_1",
                "index": 1,
                "status": "uncertain",
                "confidence": 0.5,
                "evidence_constraints": [_constraint("c_install", "produces", "expected_effect", ["s1", "installed", "wheel", "hub"])],
                "produced_effects": [_constraint("c_install", "produces", "expected_effect", ["s1", "installed", "wheel", "hub"])],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s2",
                "source_event_id": "event_2",
                "index": 2,
                "status": "uncertain",
                "confidence": 0.5,
                "evidence_constraints": [_constraint("c_requires", "requires", "inferred_precondition", ["s2", "installed", "wheel", "hub"])],
                "supported_requirements": [_constraint("c_requires", "requires", "inferred_precondition", ["s2", "installed", "wheel", "hub"])],
                "dependency_support": [
                    {
                        "required_condition": {"name": "installed", "args": ["wheel", "hub"]},
                        "supporting_effect": {
                            "type": "previous_produced_effect",
                            "constraint_id": "c_install",
                            "step_id": "s1",
                            "condition": {"name": "installed", "args": ["wheel", "hub"]},
                            "producer_status": "uncertain",
                            "provisional": True,
                        },
                    }
                ],
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
        ],
    )

    build_procedural_reasoning_graph(
        ProceduralReasoningGraphInputs(validations_path=validations_path, output_dir=output_dir)
    )

    graph = json.loads((output_dir / "procedural_reasoning_graph.json").read_text(encoding="utf-8"))
    assert _edge(graph["edges"], "Step::s2", "Step::s1", "DEPENDS_ON")["properties"]["provisional"] is True


def test_builds_graph_with_custom_graph_name(tmp_path: Path) -> None:
    validations_path = tmp_path / "validation_records.jsonl"
    output_dir = tmp_path / "graph"
    _write_jsonl(
        validations_path,
        [
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s1",
                "source_event_id": "event_1",
                "index": 1,
                "status": "accepted",
                "confidence": 0.9,
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            }
        ],
    )

    result = build_procedural_reasoning_graph(
        ProceduralReasoningGraphInputs(
            validations_path=validations_path,
            output_dir=output_dir,
            graph_name="procedural_reasoning_graph::clip_a",
        )
    )

    graph = json.loads((output_dir / "procedural_reasoning_graph.json").read_text(encoding="utf-8"))
    assert result["graph_name"] == "procedural_reasoning_graph::clip_a"
    assert graph["graph_name"] == "procedural_reasoning_graph::clip_a"


def test_builds_graph_with_short_step_display_labels(tmp_path: Path) -> None:
    validations_path = tmp_path / "validation_records.jsonl"
    output_dir = tmp_path / "graph"
    _write_jsonl(
        validations_path,
        [
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s0",
                "source_event_id": "event_0",
                "index": 0,
                "status": "accepted",
                "confidence": 0.9,
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s1",
                "source_event_id": "event_1",
                "index": 1,
                "status": "uncertain",
                "confidence": 0.5,
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
            {
                "schema_version": "thesis_layer4_validation.v1",
                "step_id": "s2",
                "source_event_id": "event_2",
                "index": 2,
                "status": "rejected",
                "confidence": 0.2,
                "trace": {"predicate_evidence": [], "constraint_evidence": [], "dependency_evidence": []},
            },
        ],
    )

    result = build_procedural_reasoning_graph(
        ProceduralReasoningGraphInputs(
            validations_path=validations_path,
            output_dir=output_dir,
            short_labels=True,
        )
    )

    graph = json.loads((output_dir / "procedural_reasoning_graph.json").read_text(encoding="utf-8"))
    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    assert nodes_by_id["Step::s0"]["properties"]["display_label"] == "S0 [A]"
    assert nodes_by_id["Step::s1"]["properties"]["display_label"] == "S1 [U]"
    assert nodes_by_id["Step::s2"]["properties"]["display_label"] == "S2 [R]"
    assert nodes_by_id["Step::s0"]["properties"]["display_name"] == "Step 0"
    assert result["short_labels"] is True


def _has_edge(edges: list[dict[str, object]], source: str, target: str, edge_type: str) -> bool:
    return any(edge["source"] == source and edge["target"] == target and edge["type"] == edge_type for edge in edges)


def _edge(edges: list[dict[str, object]], source: str, target: str, edge_type: str) -> dict[str, object]:
    return next(edge for edge in edges if edge["source"] == source and edge["target"] == target and edge["type"] == edge_type)


def _predicate(predicate_id: str, step_id: str, name: str, args: list[object]) -> dict[str, object]:
    return {
        "predicate_id": predicate_id,
        "step_id": step_id,
        "name": name,
        "args": args,
        "conf": 0.9,
        "source": {"type": "test", "file": "test.csv", "fields": ["a"]},
    }


def _constraint(constraint_id: str, name: str, kind: str, args: list[object]) -> dict[str, object]:
    return {
        "constraint_id": constraint_id,
        "name": name,
        "kind": kind,
        "args": args,
        "conf": 0.9,
        "rule_id": f"rule_{kind}",
        "support": {"type": "same_step_constraint", "notes": "Constraint observed in the step."},
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
