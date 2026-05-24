"""Helpers for importing procedural_reasoning_graph outputs into Neo4j."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any


GRAPH_NAME = "procedural_reasoning_graph"
NODE_CSV = "procedural_reasoning_graph_nodes.csv"
EDGE_CSV = "procedural_reasoning_graph_edges.csv"
GRAPH_JSON = "procedural_reasoning_graph.json"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
STEP_STATUS_LABELS = {
    "accepted": "StepAccepted",
    "uncertain": "StepUncertain",
    "rejected": "StepRejected",
}


def load_procedural_graph(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir():
        json_path = path / GRAPH_JSON
        if json_path.exists():
            return _load_graph_json(json_path)
        return _load_graph_csvs(path / NODE_CSV, path / EDGE_CSV)
    if path.suffix.lower() == ".json":
        return _load_graph_json(path)
    raise ValueError(f"Expected a graph directory or {GRAPH_JSON}: {path}")


def normalize_graph(graph: dict[str, Any], graph_name: str | None = None) -> dict[str, list[dict[str, Any]]]:
    name = graph_name or str(graph.get("graph_name") or GRAPH_NAME)
    schema_version = graph.get("schema_version")
    return {
        "nodes": [_normalize_node(node, name, schema_version) for node in list(graph.get("nodes", []) or [])],
        "edges": [_normalize_edge(edge, name) for edge in list(graph.get("edges", []) or [])],
    }


def constraint_cyphers(node_types: list[str]) -> list[str]:
    cyphers = []
    for node_type in sorted(set(node_types)):
        label = neo4j_identifier(node_type)
        cyphers.append(
            f"CREATE CONSTRAINT prg_{label.lower()}_graph_prg_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE (n.graph_name, n.prg_id) IS UNIQUE"
        )
    return cyphers


def legacy_constraint_drop_cyphers(node_types: list[str]) -> list[str]:
    cyphers = []
    for node_type in sorted(set(node_types)):
        label = neo4j_identifier(node_type)
        cyphers.append(f"DROP CONSTRAINT prg_{label.lower()}_prg_id IF EXISTS")
    cyphers.append("DROP CONSTRAINT prg_common_prg_id IF EXISTS")
    return cyphers


def clear_graph_cypher() -> str:
    return (
        "MATCH (n {graph_name: $graph_name}) "
        "DETACH DELETE n"
    )


def node_import_cypher(node_type: str) -> str:
    label = neo4j_identifier(node_type)
    status_label_setters = ""
    if label == "Step":
        status_label_setters = (
            "REMOVE n:StepAccepted:StepUncertain:StepRejected "
            "FOREACH (_ IN CASE WHEN r.props.status = 'accepted' THEN [1] ELSE [] END | SET n:StepAccepted) "
            "FOREACH (_ IN CASE WHEN r.props.status = 'uncertain' THEN [1] ELSE [] END | SET n:StepUncertain) "
            "FOREACH (_ IN CASE WHEN r.props.status = 'rejected' THEN [1] ELSE [] END | SET n:StepRejected) "
        )
    return (
        f"UNWIND $rows AS r "
        f"MERGE (n:{label} {{graph_name: r.props.graph_name, prg_id: r.id}}) "
        "SET n += r.props "
        f"{status_label_setters}"
    )


def edge_import_cypher(edge_type: str) -> str:
    rel_type = neo4j_identifier(edge_type)
    return (
        "UNWIND $rows AS r "
        "MATCH (a {graph_name: r.graph_name, prg_id: r.source}) "
        "MATCH (b {graph_name: r.graph_name, prg_id: r.target}) "
        f"MERGE (a)-[rel:{rel_type} {{prg_edge_key: r.edge_key}}]->(b) "
        "SET rel += r.props"
    )


def grouped_by_type(rows: list[dict[str, Any]], type_key: str = "type") -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[type_key]), []).append(row)
    return dict(sorted(grouped.items()))


def neo4j_identifier(value: str) -> str:
    text = str(value or "")
    if not _IDENTIFIER_RE.match(text):
        raise ValueError(f"Unsafe Neo4j identifier: {value!r}")
    return text


def neo4j_props(properties: dict[str, Any]) -> dict[str, Any]:
    return {key: _neo4j_value(value) for key, value in properties.items() if value is not None}


def _normalize_node(node: dict[str, Any], graph_name: str, schema_version: Any = None) -> dict[str, Any]:
    node_id = str(node["id"])
    node_type = neo4j_identifier(str(node["type"]))
    props = dict(node.get("properties", {}) or {})
    if schema_version is not None:
        props.setdefault("schema_version", schema_version)
    props.update(
        {
            "prg_id": node_id,
            "node_type": node_type,
            "graph_name": graph_name,
        }
    )
    return {
        "id": node_id,
        "type": node_type,
        "props": neo4j_props(props),
        "labels": _neo4j_labels_for_node(node_type, props),
    }


def _normalize_edge(edge: dict[str, Any], graph_name: str) -> dict[str, Any]:
    edge_type = neo4j_identifier(str(edge["type"]))
    source = str(edge["source"])
    target = str(edge["target"])
    props = dict(edge.get("properties", {}) or {})
    edge_key = _edge_key(source, target, edge_type, props)
    props.update(
        {
            "prg_edge_key": edge_key,
            "edge_type": edge_type,
            "graph_name": graph_name,
        }
    )
    return {
        "source": source,
        "target": target,
        "type": edge_type,
        "graph_name": graph_name,
        "edge_key": edge_key,
        "props": neo4j_props(props),
    }


def _neo4j_labels_for_node(node_type: str, props: dict[str, Any]) -> list[str]:
    labels = [node_type]
    if node_type == "Step":
        status_label = STEP_STATUS_LABELS.get(str(props.get("status") or "").lower())
        if status_label:
            labels.append(status_label)
    return labels


def _edge_key(source: str, target: str, edge_type: str, properties: dict[str, Any]) -> str:
    payload = json.dumps(properties, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{source}|{edge_type}|{target}|{payload}"


def _neo4j_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return list(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_graph_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_graph_csvs(nodes_path: Path, edges_path: Path) -> dict[str, Any]:
    if not nodes_path.exists() or not edges_path.exists():
        raise FileNotFoundError(f"Missing {NODE_CSV} or {EDGE_CSV}")
    return {
        "graph_name": GRAPH_NAME,
        "nodes": [_parse_csv_node(row) for row in _read_csv(nodes_path)],
        "edges": [_parse_csv_edge(row) for row in _read_csv(edges_path)],
    }


def _parse_csv_node(row: dict[str, str]) -> dict[str, Any]:
    return {"id": row["id"], "type": row["type"], "properties": _parse_properties(row.get("properties"))}


def _parse_csv_edge(row: dict[str, str]) -> dict[str, Any]:
    return {
        "source": row["source"],
        "target": row["target"],
        "type": row["type"],
        "properties": _parse_properties(row.get("properties")),
    }


def _parse_properties(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Graph properties must decode to an object")
    return parsed


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
