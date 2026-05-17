"""Neo4j CSV export helpers for EGG and assembly graphs."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict
import pandas as pd


def export_neo4j_csvs(graph: Dict, output_dir: Path):
    """Write all Neo4j import CSVs from an EGG graph dict."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # nodes_rooms.csv
    rooms = []
    for r in graph["rooms"]:
        rooms.append({
            "room_id:ID(Room)": r["room_id"],
            "name": r["name"],
            "x:float": r["position"]["x"],
            "y:float": r["position"]["y"],
            "z:float": r["position"]["z"],
            ":LABEL": "Room",
        })
    pd.DataFrame(rooms).to_csv(output_dir / "nodes_rooms.csv", index=False)

    # nodes_objects.csv
    objects = []
    for o in graph["objects"]:
        objects.append({
            "track_id:ID(Object)": o["track_id"],
            "semantic_class": o["semantic_class"],
            "label": o["label"],
            "caption": o.get("caption", ""),
            ":LABEL": "Object",
        })
    pd.DataFrame(objects).to_csv(output_dir / "nodes_objects.csv", index=False)

    # nodes_events.csv
    events = []
    for e in graph["events"]:
        pos = e.get("position", {})
        events.append({
            "event_id:ID(Event)": e["event_id"],
            "event_type": e["event_type"],
            "summary": e.get("summary", ""),
            "start_ts_ns:long": e["start_ts_ns"],
            "end_ts_ns:long": e["end_ts_ns"],
            "pos_x:float": pos.get("x", 0.0),
            "pos_y:float": pos.get("y", 0.0),
            "pos_z:float": pos.get("z", 0.0),
            ":LABEL": "Event",
        })
    pd.DataFrame(events).to_csv(output_dir / "nodes_events.csv", index=False)

    # edges_room_object.csv
    room_obj = []
    for e in graph["room_edges"]:
        room_obj.append({
            ":START_ID(Room)": e["room_id"],
            ":END_ID(Object)": e["track_id"],
            ":TYPE": "CONTAINS",
        })
    pd.DataFrame(room_obj).to_csv(output_dir / "edges_room_object.csv", index=False)

    # edges_event_object.csv
    evt_obj = []
    for e in graph["event_edges"]:
        evt_obj.append({
            ":START_ID(Event)": e["event_id"],
            ":END_ID(Object)": e["track_id"],
            "role": e.get("role", ""),
            "role_description": e.get("role_description", ""),
            ":TYPE": "INVOLVES",
        })
    pd.DataFrame(evt_obj).to_csv(output_dir / "edges_event_object.csv", index=False)

    # edges_before.csv
    before = []
    for e in graph["temporal_edges"]:
        before.append({
            ":START_ID(Event)": e["src_event_id"],
            ":END_ID(Event)": e["dst_event_id"],
            ":TYPE": "BEFORE",
        })
    pd.DataFrame(before).to_csv(output_dir / "edges_before.csv", index=False)

    return {
        "nodes_rooms": len(rooms),
        "nodes_objects": len(objects),
        "nodes_events": len(events),
        "edges_room_object": len(room_obj),
        "edges_event_object": len(evt_obj),
        "edges_before": len(before),
    }


def export_assembly_neo4j_csvs(graph: Dict[str, Any], output_dir: Path):
    """Write Neo4j import CSVs from an assembly graph dict.

    The assembly graph has heterogeneous node/edge payloads, so we keep stable
    Neo4j identifiers in first-class columns and serialize the remaining
    properties into JSON. The direct importer expands those JSON properties back
    onto nodes and relationships.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    session_id = str(graph.get("session_id", "unknown"))
    raw_nodes = list(graph.get("nodes", []))
    raw_edges = list(graph.get("edges", []))

    node_by_id = {str(n["node_id"]): n for n in raw_nodes if n.get("node_id")}
    missing_endpoint_ids = []
    for edge in raw_edges:
        for endpoint_key in ("source", "target"):
            endpoint = str(edge.get(endpoint_key, ""))
            if endpoint and endpoint not in node_by_id and endpoint not in missing_endpoint_ids:
                missing_endpoint_ids.append(endpoint)

    nodes = []
    for node in raw_nodes:
        node_id = str(node["node_id"])
        node_type = str(node.get("node_type", "unknown"))
        nodes.append({
            "assembly_id:ID(AssemblyNode)": _assembly_id(session_id, node_id),
            "node_id": node_id,
            "session_id": session_id,
            "node_type": node_type,
            "properties_json": _properties_json(node, {"node_id", "node_type"}),
            ":LABEL": f"AssemblyNode;{_assembly_label(node_type)}",
        })

    for node_id in missing_endpoint_ids:
        nodes.append({
            "assembly_id:ID(AssemblyNode)": _assembly_id(session_id, node_id),
            "node_id": node_id,
            "session_id": session_id,
            "node_type": "external_ref",
            "properties_json": _properties_json({
                "node_id": node_id,
                "node_type": "external_ref",
                "source": "assembly_edge_endpoint",
            }, {"node_id", "node_type"}),
            ":LABEL": "AssemblyNode;AssemblyExternalRef",
        })

    node_columns = [
        "assembly_id:ID(AssemblyNode)", "node_id", "session_id",
        "node_type", "properties_json", ":LABEL",
    ]
    pd.DataFrame(nodes, columns=node_columns).to_csv(
        output_dir / "nodes_assembly.csv",
        index=False,
    )

    edges = []
    for edge in raw_edges:
        edge_id = str(edge["edge_id"])
        edge_type = str(edge.get("edge_type", "related_to"))
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        edges.append({
            "edge_id:ID(AssemblyEdge)": edge_id,
            "session_id": session_id,
            ":START_ID(AssemblyNode)": _assembly_id(session_id, source),
            ":END_ID(AssemblyNode)": _assembly_id(session_id, target),
            "edge_type": edge_type,
            "properties_json": _properties_json(
                edge,
                {"edge_id", "edge_type", "source", "target"},
            ),
            ":TYPE": _relationship_type(edge_type),
        })

    edge_columns = [
        "edge_id:ID(AssemblyEdge)", "session_id", ":START_ID(AssemblyNode)",
        ":END_ID(AssemblyNode)", "edge_type", "properties_json", ":TYPE",
    ]
    pd.DataFrame(edges, columns=edge_columns).to_csv(
        output_dir / "edges_assembly.csv",
        index=False,
    )

    return {
        "nodes_assembly": len(nodes),
        "edges_assembly": len(edges),
    }


def _assembly_id(session_id: str, node_id: str) -> str:
    return f"{session_id}:{node_id}"


def _assembly_label(node_type: str) -> str:
    parts = [p for p in str(node_type).replace("-", "_").split("_") if p]
    return "Assembly" + "".join(p[:1].upper() + p[1:] for p in parts)


def _relationship_type(edge_type: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in str(edge_type)).upper()
    cleaned = cleaned.strip("_") or "RELATED_TO"
    if cleaned[0].isdigit():
        cleaned = f"REL_{cleaned}"
    return cleaned


def _properties_json(item: Dict[str, Any], excluded: set[str]) -> str:
    props = {
        str(k): _json_safe(v)
        for k, v in item.items()
        if k not in excluded and v is not None
    }
    return json.dumps(props, sort_keys=True, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value if v is not None]
    return value
