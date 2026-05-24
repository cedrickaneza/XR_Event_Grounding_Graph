from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "23_evaluate_graph_traceability.py"
SPEC = importlib.util.spec_from_file_location("evaluation4", SCRIPT_PATH)
evaluation4 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = evaluation4
SPEC.loader.exec_module(evaluation4)


def node(node_id: str, node_type: str, **props) -> dict:
    return {"id": node_id, "type": node_type, "properties": props}


def edge(source: str, target: str, edge_type: str, **props) -> dict:
    return {"source": source, "target": target, "type": edge_type, "properties": props}


def validation(step_id: str, index: int, status: str = "accepted", **extra) -> dict:
    row = {
        "step_id": step_id,
        "index": index,
        "status": status,
        "confidence": 0.9,
        "evidence_predicates": [],
        "evidence_constraints": [],
        "supported_requirements": [],
        "missing_requirements": [],
        "dependency_support": [],
    }
    row.update(extra)
    return row


def req(constraint_id: str = "c_req", step_id: str = "s1") -> dict:
    return {
        "constraint_id": constraint_id,
        "name": "requires",
        "args": [step_id, "installed", "base", "workspace"],
        "conf": 0.9,
        "rule_id": "rule_req",
    }


def base_graph() -> dict:
    return {
        "schema_version": "1.0",
        "graph_name": "g",
        "nodes": [
            node("Step::s0", "Step", step_id="s0", index=0, status="accepted", confidence=0.9),
            node("Step::s1", "Step", step_id="s1", index=1, status="accepted", confidence=0.9),
        ],
        "edges": [edge("Step::s0", "Step::s1", "NEXT")],
    }


def test_next_edges_follow_increasing_step_index() -> None:
    rows, check = evaluation4.evaluate_order_preservation(base_graph())
    assert check.status == "PASS"
    assert rows[0]["status"] == "PASS"


def test_out_of_order_next_edge_fails() -> None:
    graph = base_graph()
    graph["edges"] = [edge("Step::s1", "Step::s0", "NEXT")]
    _, check = evaluation4.evaluate_order_preservation(graph)
    assert check.status == "FAIL"


def test_depends_on_edge_corresponds_to_dependency_support() -> None:
    graph = base_graph()
    graph["nodes"].extend(
        [
            node("Constraint::c_req", "Constraint", constraint_id="c_req", name="requires", args=["s1", "installed", "base", "workspace"]),
            node("Constraint::c_prod", "Constraint", constraint_id="c_prod", name="produces", args=["s0", "installed", "base", "workspace"]),
        ]
    )
    graph["edges"].extend(
        [
            edge("Step::s1", "Step::s0", "DEPENDS_ON", required_condition={"name": "installed", "args": ["base", "workspace"]}),
            edge("Step::s1", "Constraint::c_req", "REQUIRES"),
            edge("Step::s0", "Constraint::c_prod", "PRODUCES"),
        ]
    )
    validations = [
        validation("s0", 0),
        validation(
            "s1",
            1,
            dependency_support=[
                {
                    "required_condition": {"name": "installed", "args": ["base", "workspace"]},
                    "supporting_effect": {"step_id": "s0", "constraint_id": "c_prod"},
                }
            ],
        ),
    ]
    rows, check = evaluation4.evaluate_dependency_grounding(graph, validations)
    assert check.status == "PASS"
    assert rows[0]["has_validation_dependency_support"] is True


def test_depends_on_edge_targeting_rejected_support_fails() -> None:
    graph = base_graph()
    graph["nodes"][0]["properties"]["status"] = "rejected"
    graph["edges"].append(edge("Step::s1", "Step::s0", "DEPENDS_ON", required_condition={"name": "installed", "args": ["base", "workspace"]}))
    validations = [validation("s0", 0, "rejected"), validation("s1", 1)]
    _, check = evaluation4.evaluate_rejected_step_isolation_graph(graph, validations)
    assert check.status == "FAIL"


def test_uncertain_support_dependency_requires_provisional_true() -> None:
    graph = base_graph()
    graph["nodes"][0]["properties"]["status"] = "uncertain"
    graph["edges"].append(edge("Step::s1", "Step::s0", "DEPENDS_ON", provisional=False))
    validations = [validation("s0", 0, "uncertain"), validation("s1", 1)]
    rows, check = evaluation4.evaluate_provisional_dependency_visibility(graph, validations)
    assert check.status == "FAIL"
    assert rows[0]["provisional_property"] is False


def test_requirement_constraints_are_visible_through_graph_edges() -> None:
    graph = base_graph()
    graph["nodes"].append(node("Constraint::c_req", "Constraint", constraint_id="c_req", name="requires", args=["s1", "installed", "base", "workspace"]))
    graph["edges"].append(edge("Step::s1", "Constraint::c_req", "REQUIRES"))
    rows, check = evaluation4.evaluate_requirement_visibility(graph, [validation("s1", 1, supported_requirements=[req()])])
    assert check.status == "PASS"
    assert rows[0]["has_step_requirement_edge"] is True


def test_constraint_with_rule_provenance_links_to_rule_node() -> None:
    graph = {
        "nodes": [
            node("Constraint::c1", "Constraint", constraint_id="c1", name="requires", rule_id="rule_a"),
            node("Rule::rule_a", "Rule", rule_id="rule_a"),
        ],
        "edges": [edge("Constraint::c1", "Rule::rule_a", "DERIVED_FROM")],
    }
    rows, check = evaluation4.evaluate_rule_provenance(graph)
    assert check.status == "PASS"
    assert rows[0]["has_rule_node"] is True


def test_invalidated_produced_effect_has_invalidated_by_edge() -> None:
    graph = {
        "nodes": [
            node("Step::s1", "Step", step_id="s1", index=1, status="accepted", confidence=0.9),
            node("Step::s2", "Step", step_id="s2", index=2, status="accepted", confidence=0.9),
            node("Constraint::c_install", "Constraint", constraint_id="c_install", name="produces", effect_lifecycle_status="invalidated", invalidated_by_constraint_id="c_removed"),
            node("Constraint::c_removed", "Constraint", constraint_id="c_removed", name="produces", effect_lifecycle_status="active"),
        ],
        "edges": [
            edge("Constraint::c_install", "Constraint::c_removed", "INVALIDATED_BY"),
            edge("Step::s2", "Constraint::c_removed", "PRODUCES"),
        ],
    }
    rows, check = evaluation4.evaluate_effect_invalidation_visibility(graph)
    assert check.status == "PASS"
    assert rows[0]["linked_invalidating_constraint_node_id"] == "Constraint::c_removed"


def test_invalidated_by_edge_points_to_expected_invalidating_constraint() -> None:
    graph = {
        "nodes": [
            node("Step::s2", "Step", step_id="s2", index=2, status="accepted", confidence=0.9),
            node("Constraint::c_install", "Constraint", constraint_id="c_install", name="produces", effect_lifecycle_status="invalidated", invalidated_by_constraint_id="c_removed"),
            node("Constraint::c_other", "Constraint", constraint_id="c_other", name="produces", effect_lifecycle_status="active"),
        ],
        "edges": [
            edge("Constraint::c_install", "Constraint::c_other", "INVALIDATED_BY"),
            edge("Step::s2", "Constraint::c_other", "PRODUCES"),
        ],
    }
    rows, check = evaluation4.evaluate_effect_invalidation_visibility(graph)
    assert check.status == "FAIL"
    assert rows[0]["linked_invalidating_constraint_node_id"] == ""


def test_missing_graph_files_produce_missing_data_report(tmp_path: Path) -> None:
    ctx = evaluation4.EvaluationContext(
        project_root=tmp_path,
        clip_result_id="missing",
        reasoning_dir=tmp_path / "reasoning",
        graph_dir=tmp_path / "graph",
        output_dir=tmp_path / "docs" / "Evaluation4",
    )
    result = evaluation4.evaluate(ctx)
    assert result["checks"][0]["status"] == "FAIL"
    assert (ctx.output_dir / "missing_data_report.md").exists()
