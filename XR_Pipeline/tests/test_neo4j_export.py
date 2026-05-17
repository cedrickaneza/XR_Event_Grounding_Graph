"""Tests for Neo4j CSV export helpers."""
import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.neo4j_export import export_assembly_neo4j_csvs


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_assembly_export_writes_nodes_edges_and_external_refs():
    graph = {
        "session_id": "session_test",
        "nodes": [
            {
                "node_id": "obj_trk_0001",
                "node_type": "object",
                "track_id": "trk_0001",
                "class_label": "blue_lego",
            },
            {
                "node_id": "sub_0001",
                "node_type": "subtask",
                "template_name": "hold_part",
                "status": "achieved",
            },
        ],
        "edges": [
            {
                "edge_id": "edge_0001",
                "edge_type": "involves",
                "source": "sub_0001",
                "target": "obj_trk_0001",
                "role": "patient",
            },
            {
                "edge_id": "edge_0002",
                "edge_type": "evidence_for",
                "source": "op_0001",
                "target": "sub_0001",
            },
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        counts = export_assembly_neo4j_csvs(graph, out)
        nodes = _read_csv(out / "nodes_assembly.csv")
        edges = _read_csv(out / "edges_assembly.csv")

    assert counts == {"nodes_assembly": 3, "edges_assembly": 2}
    assert {n["node_id"] for n in nodes} == {"obj_trk_0001", "sub_0001", "op_0001"}
    assert all(n["assembly_id:ID(AssemblyNode)"].startswith("session_test:") for n in nodes)

    external = next(n for n in nodes if n["node_id"] == "op_0001")
    assert external["node_type"] == "external_ref"

    obj = next(n for n in nodes if n["node_id"] == "obj_trk_0001")
    obj_props = json.loads(obj["properties_json"])
    assert obj_props["track_id"] == "trk_0001"
    assert obj_props["class_label"] == "blue_lego"

    assert {e[":TYPE"] for e in edges} == {"INVOLVES", "EVIDENCE_FOR"}
    assert all(e[":START_ID(AssemblyNode)"].startswith("session_test:") for e in edges)

    involves = next(e for e in edges if e["edge_type"] == "involves")
    assert json.loads(involves["properties_json"]) == {"role": "patient"}
