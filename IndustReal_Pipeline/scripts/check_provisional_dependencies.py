#!/usr/bin/env python3
"""Audit provisional DEPENDS_ON edges across reasoning-layer result folders."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditSummary:
    runs: int
    dependency_supports: int
    uncertain_supports: int
    depends_on_edges: int
    provisional_edges: int
    issues: tuple[str, ...]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _step_id(node_id: object) -> str:
    value = str(node_id or "")
    return value.removeprefix("Step::")


def audit_provisional_dependencies(
    reasoning_root: Path,
    graph_root: Path,
) -> AuditSummary:
    issues: list[str] = []
    run_count = 0
    dependency_support_count = 0
    uncertain_support_count = 0
    depends_on_edge_count = 0
    provisional_edge_count = 0

    reasoning_dirs = sorted(path for path in reasoning_root.iterdir() if path.is_dir())
    graph_dir_names = {path.name for path in graph_root.iterdir() if path.is_dir()}
    reasoning_dir_names = {path.name for path in reasoning_dirs}

    for extra_name in sorted(graph_dir_names - reasoning_dir_names):
        issues.append(f"{extra_name}: graph folder has no matching reasoning folder")

    for reasoning_dir in reasoning_dirs:
        run_count += 1
        run_name = reasoning_dir.name
        validation_path = reasoning_dir / "validation_records.jsonl"
        graph_path = graph_root / run_name / "procedural_reasoning_graph.json"

        if not validation_path.is_file():
            issues.append(f"{run_name}: missing validation_records.jsonl")
            continue
        if not graph_path.is_file():
            issues.append(f"{run_name}: missing procedural_reasoning_graph.json")
            continue

        validations = _read_jsonl(validation_path)
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        status_by_step = {
            str(record.get("step_id") or ""): str(record.get("status") or "")
            for record in validations
        }
        dependency_edges: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for edge in graph.get("edges", []):
            if edge.get("type") != "DEPENDS_ON":
                continue
            depends_on_edge_count += 1
            source = _step_id(edge.get("source"))
            target = _step_id(edge.get("target"))
            dependency_edges.setdefault((source, target), []).append(edge)
            properties = edge.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            provisional = properties.get("provisional")
            if provisional is True:
                provisional_edge_count += 1
            if status_by_step.get(target) == "uncertain" and provisional is not True:
                issues.append(
                    f"{run_name}: DEPENDS_ON {source} -> {target} targets an "
                    "uncertain step but lacks provisional=true"
                )

        for record in validations:
            dependent_step = str(record.get("step_id") or "")
            supports = record.get("dependency_support")
            if not isinstance(supports, list):
                trace = record.get("trace")
                supports = trace.get("dependency_evidence", []) if isinstance(trace, dict) else []

            for dependency in supports:
                if not isinstance(dependency, dict):
                    continue
                supporting_effect = dependency.get("supporting_effect")
                if not isinstance(supporting_effect, dict):
                    continue
                support_step = str(supporting_effect.get("step_id") or "")
                if not support_step:
                    continue

                dependency_support_count += 1
                if status_by_step.get(support_step) != "uncertain":
                    continue

                uncertain_support_count += 1
                if supporting_effect.get("provisional") is not True:
                    issues.append(
                        f"{run_name}: validation support {dependent_step} <- "
                        f"{support_step} lacks provisional=true"
                    )

                matching_edges = dependency_edges.get((dependent_step, support_step), [])
                if not matching_edges:
                    issues.append(
                        f"{run_name}: missing DEPENDS_ON {dependent_step} -> "
                        f"{support_step} for uncertain support"
                    )
                elif not any(
                    isinstance(edge.get("properties"), dict)
                    and edge["properties"].get("provisional") is True
                    for edge in matching_edges
                ):
                    issues.append(
                        f"{run_name}: DEPENDS_ON {dependent_step} -> "
                        f"{support_step} is not provisional"
                    )

    return AuditSummary(
        runs=run_count,
        dependency_supports=dependency_support_count,
        uncertain_supports=uncertain_support_count,
        depends_on_edges=depends_on_edge_count,
        provisional_edges=provisional_edge_count,
        issues=tuple(issues),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reasoning-root",
        type=Path,
        default=Path("results/reasoning_layers"),
    )
    parser.add_argument(
        "--graph-root",
        type=Path,
        default=Path("results/procedural_reasoning_graph"),
    )
    args = parser.parse_args()

    summary = audit_provisional_dependencies(args.reasoning_root, args.graph_root)
    print(json.dumps({
        "runs": summary.runs,
        "dependency_supports": summary.dependency_supports,
        "uncertain_supports": summary.uncertain_supports,
        "depends_on_edges": summary.depends_on_edges,
        "provisional_edges": summary.provisional_edges,
        "issues": list(summary.issues),
    }, indent=2))
    return 1 if summary.issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
