from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_provisional_dependencies.py"
SPEC = importlib.util.spec_from_file_location("provisional_dependency_audit", SCRIPT_PATH)
audit_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = audit_module
SPEC.loader.exec_module(audit_module)


def _write_run(
    tmp_path: Path,
    *,
    support_provisional: bool = True,
    edge_provisional: bool = True,
    include_edge: bool = True,
) -> tuple[Path, Path]:
    reasoning_root = tmp_path / "reasoning_layers"
    graph_root = tmp_path / "procedural_reasoning_graph"
    reasoning_dir = reasoning_root / "run"
    graph_dir = graph_root / "run"
    reasoning_dir.mkdir(parents=True)
    graph_dir.mkdir(parents=True)

    validations = [
        {"step_id": "s1", "status": "uncertain", "dependency_support": []},
        {
            "step_id": "s2",
            "status": "uncertain",
            "dependency_support": [
                {
                    "supporting_effect": {
                        "step_id": "s1",
                        "provisional": support_provisional,
                    }
                }
            ],
        },
    ]
    validation_text = "\n".join(json.dumps(record) for record in validations) + "\n"
    (reasoning_dir / "validation_records.jsonl").write_text(validation_text, encoding="utf-8")

    edges = []
    if include_edge:
        edges.append({
            "source": "Step::s2",
            "target": "Step::s1",
            "type": "DEPENDS_ON",
            "properties": {"provisional": edge_provisional},
        })
    (graph_dir / "procedural_reasoning_graph.json").write_text(
        json.dumps({"nodes": [], "edges": edges}),
        encoding="utf-8",
    )
    return reasoning_root, graph_root


def test_audit_accepts_provisional_uncertain_dependency(tmp_path: Path) -> None:
    reasoning_root, graph_root = _write_run(tmp_path)

    summary = audit_module.audit_provisional_dependencies(reasoning_root, graph_root)

    assert summary.runs == 1
    assert summary.uncertain_supports == 1
    assert summary.provisional_edges == 1
    assert summary.issues == ()


def test_audit_rejects_non_provisional_uncertain_dependency(tmp_path: Path) -> None:
    reasoning_root, graph_root = _write_run(
        tmp_path,
        support_provisional=False,
        edge_provisional=False,
    )

    summary = audit_module.audit_provisional_dependencies(reasoning_root, graph_root)

    assert any("validation support" in issue for issue in summary.issues)
    assert any("lacks provisional=true" in issue for issue in summary.issues)


def test_audit_rejects_missing_uncertain_dependency_edge(tmp_path: Path) -> None:
    reasoning_root, graph_root = _write_run(tmp_path, include_edge=False)

    summary = audit_module.audit_provisional_dependencies(reasoning_root, graph_root)

    assert any("missing DEPENDS_ON" in issue for issue in summary.issues)
