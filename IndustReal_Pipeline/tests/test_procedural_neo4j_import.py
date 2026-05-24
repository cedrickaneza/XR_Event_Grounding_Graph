from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.procedural_neo4j_import import (
    clear_graph_cypher,
    edge_import_cypher,
    legacy_constraint_drop_cyphers,
    load_procedural_graph,
    neo4j_identifier,
    normalize_graph,
    node_import_cypher,
)


def test_loads_csv_graph_and_normalizes_nested_properties(tmp_path: Path) -> None:
    nodes_path = tmp_path / "procedural_reasoning_graph_nodes.csv"
    edges_path = tmp_path / "procedural_reasoning_graph_edges.csv"
    with open(nodes_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "type", "properties"])
        writer.writeheader()
        writer.writerow(
            {
                "id": "Step::a",
                "type": "Step",
                "properties": json.dumps({"step_id": "a", "args": ["base", "workspace"], "window": [70.9, 71.2]}),
            }
        )
        writer.writerow(
            {
                "id": "Constraint::c",
                "type": "Constraint",
                "properties": json.dumps({"support": {"type": "same_step_constraint"}}),
            }
        )
    with open(edges_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "type", "properties"])
        writer.writeheader()
        writer.writerow(
            {
                "source": "Step::a",
                "target": "Constraint::c",
                "type": "HAS_CONSTRAINT",
                "properties": json.dumps({"required_condition": {"name": "installed"}}),
            }
        )

    graph = load_procedural_graph(tmp_path)
    normalized = normalize_graph(graph)

    step = normalized["nodes"][0]
    constraint = normalized["nodes"][1]
    edge = normalized["edges"][0]
    assert step["props"]["args"] == ["base", "workspace"]
    assert json.loads(step["props"]["window"]) == [70.9, 71.2]
    assert json.loads(constraint["props"]["support"]) == {"type": "same_step_constraint"}
    assert json.loads(edge["props"]["required_condition"]) == {"name": "installed"}
    assert edge["props"]["edge_type"] == "HAS_CONSTRAINT"


def test_normalize_graph_carries_graph_metadata_to_nodes() -> None:
    normalized = normalize_graph(
        {
            "schema_version": "1.0",
            "graph_name": "procedural_reasoning_graph",
            "nodes": [{"id": "Step::s1", "type": "Step", "properties": {"step_id": "s1"}}],
            "edges": [],
        }
    )

    props = normalized["nodes"][0]["props"]
    assert props["graph_name"] == "procedural_reasoning_graph"
    assert props["schema_version"] == "1.0"
    assert props["node_type"] == "Step"
    assert props["prg_id"] == "Step::s1"
    assert normalized["nodes"][0]["labels"] == ["Step"]


def test_normalize_graph_derives_step_status_labels() -> None:
    normalized = normalize_graph(
        {
            "schema_version": "1.0",
            "graph_name": "procedural_reasoning_graph",
            "nodes": [
                {"id": "Step::a", "type": "Step", "properties": {"step_id": "a", "status": "accepted"}},
                {"id": "Step::u", "type": "Step", "properties": {"step_id": "u", "status": "uncertain"}},
                {"id": "Step::r", "type": "Step", "properties": {"step_id": "r", "status": "rejected"}},
                {"id": "Constraint::c", "type": "Constraint", "properties": {"status": "rejected"}},
            ],
            "edges": [],
        }
    )

    labels_by_id = {node["id"]: node["labels"] for node in normalized["nodes"]}
    assert labels_by_id["Step::a"] == ["Step", "StepAccepted"]
    assert labels_by_id["Step::u"] == ["Step", "StepUncertain"]
    assert labels_by_id["Step::r"] == ["Step", "StepRejected"]
    assert labels_by_id["Constraint::c"] == ["Constraint"]


def test_rejects_unsafe_neo4j_identifiers() -> None:
    assert neo4j_identifier("HAS_CONSTRAINT") == "HAS_CONSTRAINT"
    with pytest.raises(ValueError):
        neo4j_identifier("Bad Label")


def test_import_cyphers_use_semantic_and_status_node_labels() -> None:
    assert "MERGE (n:Step {graph_name: r.props.graph_name, prg_id: r.id})" in node_import_cypher("Step")
    assert "ProceduralReasoningGraphNode" not in node_import_cypher("Step")
    assert "REMOVE n:StepAccepted:StepUncertain:StepRejected" in node_import_cypher("Step")
    assert "SET n:StepAccepted" in node_import_cypher("Step")
    assert "SET n:StepUncertain" in node_import_cypher("Step")
    assert "SET n:StepRejected" in node_import_cypher("Step")
    assert "StepAccepted" not in node_import_cypher("Constraint")
    assert "MATCH (a {graph_name: r.graph_name, prg_id: r.source})" in edge_import_cypher("DEPENDS_ON")
    assert "MATCH (b {graph_name: r.graph_name, prg_id: r.target})" in edge_import_cypher("DEPENDS_ON")
    assert "[rel:DEPENDS_ON" in edge_import_cypher("DEPENDS_ON")
    assert clear_graph_cypher() == "MATCH (n {graph_name: $graph_name}) DETACH DELETE n"


def test_legacy_constraint_drop_cyphers_are_scoped_to_known_constraint_names() -> None:
    cyphers = legacy_constraint_drop_cyphers(["Step", "Rule"])
    assert "DROP CONSTRAINT prg_step_prg_id IF EXISTS" in cyphers
    assert "DROP CONSTRAINT prg_rule_prg_id IF EXISTS" in cyphers
    assert "DROP CONSTRAINT prg_common_prg_id IF EXISTS" in cyphers
