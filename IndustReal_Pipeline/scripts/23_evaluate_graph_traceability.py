"""Evaluation 4: procedural graph traceability.

This evaluator checks whether the procedural_reasoning_graph exposes the
validation evidence as inspectable graph structure. It is not a Layer 3
coverage or Layer 4 behavior evaluation.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CLIP_RESULT_ID = "raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1"
STATUSES = ("PASS", "FAIL", "WARNING", "SKIPPED")
REQUIRED_GRAPH_FILES = (
    "procedural_reasoning_graph.json",
    "procedural_reasoning_graph_nodes.csv",
    "procedural_reasoning_graph_edges.csv",
)
REQUIRED_REASONING_FILES = (
    "validation_records.jsonl",
    "step_validations.csv",
    "explanation_traces.json",
    "effect_history_diagnostics.csv",
)


@dataclass
class CheckResult:
    check_id: str
    check_name: str
    category: str
    status: str
    severity: str
    artifact: str
    message: str
    evidence_file: str = ""


@dataclass
class EvaluationContext:
    project_root: Path
    clip_result_id: str
    reasoning_dir: Path
    graph_dir: Path
    output_dir: Path
    strict: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: JSONL row is not an object")
            rows.append(value)
    return rows


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def parse_props(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else {}


def node_id_to_step_id(node_id: str) -> str:
    return node_id.removeprefix("Step::")


def condition_key(condition: Any) -> str:
    if not isinstance(condition, dict):
        return ""
    return json.dumps({"name": condition.get("name"), "args": list(condition.get("args", []) or [])}, sort_keys=True)


def constraint_condition_key(item: dict[str, Any]) -> str:
    args = item.get("args", [])
    if isinstance(args, str):
        args = json.loads(args) if args else []
    args = list(args or [])
    if item.get("name") == "requiresTool":
        return condition_key({"name": "requiresTool", "args": args[1:]})
    return condition_key({"name": args[1] if len(args) > 1 else item.get("name"), "args": args[2:]})


def graph_indexes(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = list(graph.get("nodes", []) or [])
    edges = list(graph.get("edges", []) or [])
    nodes_by_id = {str(node.get("id")): node for node in nodes}
    props_by_id = {node_id: parse_props(node.get("properties")) for node_id, node in nodes_by_id.items()}
    out_edges: dict[tuple[str, str], list[dict[str, Any]]] = {}
    in_edges: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for edge in edges:
        out_edges.setdefault((str(edge.get("source")), str(edge.get("type"))), []).append(edge)
        in_edges.setdefault((str(edge.get("target")), str(edge.get("type"))), []).append(edge)
    return {
        "nodes": nodes,
        "edges": edges,
        "nodes_by_id": nodes_by_id,
        "props_by_id": props_by_id,
        "out_edges": out_edges,
        "in_edges": in_edges,
        "step_nodes": [node for node in nodes if node.get("type") == "Step"],
        "constraint_nodes": [node for node in nodes if node.get("type") == "Constraint"],
        "predicate_nodes": [node for node in nodes if node.get("type") == "Predicate"],
        "rule_nodes": [node for node in nodes if node.get("type") == "Rule"],
    }


def validation_indexes(validations: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(row.get("step_id")): row for row in validations}


def dependency_keys(validations: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    keys = set()
    for record in validations:
        later = str(record.get("step_id"))
        for dep in record.get("dependency_support", []) or []:
            support = dep.get("supporting_effect") if isinstance(dep, dict) else {}
            earlier = str((support or {}).get("step_id") or "")
            if earlier:
                keys.add((later, earlier, condition_key(dep.get("required_condition"))))
    return keys


def evaluate_order_preservation(graph: dict[str, Any]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    step_props = {
        str(node.get("id")): idx["props_by_id"].get(str(node.get("id")), {})
        for node in idx["step_nodes"]
    }
    sorted_steps = sorted(
        [(node_id, props.get("index")) for node_id, props in step_props.items()],
        key=lambda item: (int(item[1]) if item[1] is not None else -1, item[0]),
    )
    expected = {(a[0], b[0]) for a, b in zip(sorted_steps, sorted_steps[1:])}
    next_edges = [edge for edge in idx["edges"] if edge.get("type") == "NEXT"]
    seen = Counter((str(edge.get("source")), str(edge.get("target"))) for edge in next_edges)
    rows = []
    failures = 0
    for edge in next_edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        source_index = step_props.get(source, {}).get("index")
        target_index = step_props.get(target, {}).get("index")
        ok = source_index is not None and target_index is not None and int(target_index) == int(source_index) + 1
        duplicate = seen[(source, target)] > 1
        expected_edge = (source, target) in expected
        status = "PASS" if ok and not duplicate and expected_edge else "FAIL"
        if status == "FAIL":
            failures += 1
        rows.append(
            {
                "edge_source": source,
                "edge_target": target,
                "source_index": source_index,
                "target_index": target_index,
                "expected_edge": expected_edge,
                "duplicate": duplicate,
                "status": status,
                "notes": "NEXT follows validation index" if status == "PASS" else "missing index, duplicate, or out-of-order NEXT edge",
            }
        )
    missing = expected - set(seen)
    for source, target in sorted(missing):
        failures += 1
        rows.append(
            {
                "edge_source": source,
                "edge_target": target,
                "source_index": step_props.get(source, {}).get("index"),
                "target_index": step_props.get(target, {}).get("index"),
                "expected_edge": True,
                "duplicate": False,
                "status": "FAIL",
                "notes": "expected NEXT edge missing",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "SKIPPED")
    return rows, CheckResult("E4.1", "Order preservation", "graph_order", status, "critical", "procedural_reasoning_graph_edges.csv", f"{len(rows)} NEXT edge checks; {failures} failures.", "order_preservation_results.csv")


def evaluate_dependency_grounding(graph: dict[str, Any], validations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    validation_by_step = validation_indexes(validations)
    dep_keys = dependency_keys(validations)
    status_by_step = {step: str(row.get("status")) for step, row in validation_by_step.items()}
    rows = []
    failures = 0
    for edge in [edge for edge in idx["edges"] if edge.get("type") == "DEPENDS_ON"]:
        props = parse_props(edge.get("properties"))
        later_step = node_id_to_step_id(str(edge.get("source")))
        earlier_step = node_id_to_step_id(str(edge.get("target")))
        required_key = condition_key(props.get("required_condition"))
        has_validation_dep = (later_step, earlier_step, required_key) in dep_keys
        later_node = str(edge.get("source"))
        earlier_node = str(edge.get("target"))
        has_requires = bool(idx["out_edges"].get((later_node, "REQUIRES")) or idx["out_edges"].get((later_node, "HAS_CONSTRAINT")))
        has_produces = bool(idx["out_edges"].get((earlier_node, "PRODUCES")))
        rejected_support = status_by_step.get(earlier_step) == "rejected"
        ok = has_validation_dep and has_requires and has_produces and not rejected_support
        if not ok:
            failures += 1
        rows.append(
            {
                "dependent_step_id": later_step,
                "support_step_id": earlier_step,
                "required_condition": required_key,
                "has_validation_dependency_support": has_validation_dep,
                "dependent_has_requirement_edge": has_requires,
                "support_has_produces_edge": has_produces,
                "support_status": status_by_step.get(earlier_step, ""),
                "rejected_support": rejected_support,
                "status": "PASS" if ok else "FAIL",
                "notes": "DEPENDS_ON grounded in validation support" if ok else "DEPENDS_ON lacks validation, requirement, produced effect, or uses rejected support",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "SKIPPED")
    return rows, CheckResult("E4.2", "Dependency grounding", "graph_dependencies", status, "critical", "procedural_reasoning_graph_edges.csv,validation_records.jsonl", f"{len(rows)} DEPENDS_ON edges checked; {failures} failures.", "dependency_grounding_results.csv")


def requirement_refs(validations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    for record in validations:
        for field in ("supported_requirements", "missing_requirements", "tool_requirements", "safety_requirements"):
            for item in record.get(field, []) or []:
                if item.get("name") in {"requires", "requiresTool", "requiresSafety"}:
                    refs.append({"step_id": record.get("step_id"), "field": field, **item})
    return refs


def evaluate_requirement_visibility(graph: dict[str, Any], validations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    nodes_by_id = idx["nodes_by_id"]
    edge_pairs = {(str(e.get("source")), str(e.get("target")), str(e.get("type"))) for e in idx["edges"]}
    rows = []
    failures = 0
    for req in requirement_refs(validations):
        cid = str(req.get("constraint_id") or "")
        node_id = f"Constraint::{cid}"
        step_node = f"Step::{req.get('step_id')}"
        props = idx["props_by_id"].get(node_id, {})
        has_node = node_id in nodes_by_id
        has_edge = (step_node, node_id, "REQUIRES") in edge_pairs or (step_node, node_id, "HAS_CONSTRAINT") in edge_pairs
        args_preserved = bool(props.get("args"))
        name_preserved = props.get("name") == req.get("name")
        ok = has_node and has_edge and args_preserved and name_preserved
        if not ok:
            failures += 1
        rows.append(
            {
                "step_id": req.get("step_id"),
                "constraint_id": cid,
                "requirement_name": req.get("name"),
                "source_field": req.get("field"),
                "has_constraint_node": has_node,
                "has_step_requirement_edge": has_edge,
                "name_preserved": name_preserved,
                "args_preserved": args_preserved,
                "status": "PASS" if ok else "FAIL",
                "notes": "requirement visible in graph" if ok else "requirement node, edge, name, or args missing",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "SKIPPED")
    return rows, CheckResult("E4.3", "Requirement visibility", "graph_constraints", status, "critical", "procedural_reasoning_graph_nodes.csv,procedural_reasoning_graph_edges.csv", f"{len(rows)} requirements checked; {failures} failures.", "requirement_visibility_results.csv")


def evaluate_missing_requirement_visibility(graph: dict[str, Any], validations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    rows = []
    missing_count = 0
    visible = 0
    for record in validations:
        for item in record.get("missing_requirements", []) or []:
            missing_count += 1
            cid = str(item.get("constraint_id") or "")
            node_id = f"Constraint::{cid}"
            props = idx["props_by_id"].get(node_id, {})
            is_visible = node_id in idx["nodes_by_id"] and props.get("support_status") == "missing"
            visible += int(is_visible)
            rows.append(
                {
                    "step_id": record.get("step_id"),
                    "constraint_id": cid,
                    "requirement_name": item.get("name"),
                    "has_graph_constraint_node": node_id in idx["nodes_by_id"],
                    "support_status": props.get("support_status", ""),
                    "visible_as_missing": is_visible,
                    "status": "PASS" if is_visible else "WARNING",
                    "notes": "missing requirement materialized as graph constraint" if is_visible else "missing requirement remains visible in validation record; graph materialization incomplete",
                }
            )
    if not missing_count:
        status = "SKIPPED"
        message = "No missing requirements in validation records."
    elif visible == missing_count:
        status = "PASS"
        message = f"All {missing_count} missing requirements are visible as graph constraints."
    else:
        status = "WARNING"
        message = f"{visible}/{missing_count} missing requirements visible in graph; validation records retain the full evidence."
    return rows, CheckResult("E4.4", "Missing requirement visibility", "graph_constraints", status, "warning", "validation_records.jsonl,procedural_reasoning_graph_nodes.csv", message, "missing_requirement_visibility_results.csv")


def evaluate_evidence_traceability(graph: dict[str, Any], validations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    failures = 0
    rows = []
    for record in validations:
        step_id = str(record.get("step_id"))
        node_id = f"Step::{step_id}"
        props = idx["props_by_id"].get(node_id, {})
        evidence_predicates = record.get("evidence_predicates", []) or []
        evidence_constraints = record.get("evidence_constraints", []) or []
        has_status_conf = props.get("status") not in (None, "") and props.get("confidence") not in (None, "")
        has_pred_edge = bool(idx["out_edges"].get((node_id, "HAS_PREDICATE")))
        has_constraint_edge = bool(idx["out_edges"].get((node_id, "HAS_CONSTRAINT")))
        pred_needed = bool(evidence_predicates)
        constraint_needed = bool(evidence_constraints)
        ok = has_status_conf and (not pred_needed or has_pred_edge) and (not constraint_needed or has_constraint_edge)
        if not ok:
            failures += 1
        rows.append(
            {
                "step_id": step_id,
                "step_index": props.get("index", ""),
                "status_value": props.get("status", ""),
                "confidence_value": props.get("confidence", ""),
                "predicate_evidence_count": len(evidence_predicates),
                "constraint_evidence_count": len(evidence_constraints),
                "has_predicate_edges": has_pred_edge,
                "has_constraint_edges": has_constraint_edge,
                "status": "PASS" if ok else "FAIL",
                "notes": "step evidence traceable" if ok else "status/confidence or evidence edges missing",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "SKIPPED")
    return rows, CheckResult("E4.5", "Evidence traceability", "graph_evidence", status, "critical", "procedural_reasoning_graph_nodes.csv,procedural_reasoning_graph_edges.csv", f"{len(rows)} Step nodes checked; {failures} failures.", "evidence_traceability_results.csv")


def evaluate_rule_provenance(graph: dict[str, Any]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    edge_pairs = {(str(e.get("source")), str(e.get("target")), str(e.get("type"))) for e in idx["edges"]}
    failures = 0
    rows = []
    for node in idx["constraint_nodes"]:
        node_id = str(node.get("id"))
        props = idx["props_by_id"].get(node_id, {})
        rule_id = props.get("rule_id")
        if not rule_id:
            continue
        rule_node = f"Rule::{rule_id}"
        has_rule_node = rule_node in idx["nodes_by_id"]
        has_edge = (node_id, rule_node, "DERIVED_FROM") in edge_pairs
        ok = has_rule_node and has_edge
        if not ok:
            failures += 1
        rows.append(
            {
                "constraint_node_id": node_id,
                "constraint_id": props.get("constraint_id", ""),
                "rule_id": rule_id,
                "has_rule_node": has_rule_node,
                "has_derived_from_edge": has_edge,
                "status": "PASS" if ok else "FAIL",
                "notes": "constraint links to rule provenance" if ok else "rule provenance edge missing",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "SKIPPED")
    return rows, CheckResult("E4.6", "Rule provenance", "graph_provenance", status, "warning", "procedural_reasoning_graph_nodes.csv,procedural_reasoning_graph_edges.csv", f"{len(rows)} rule-provenance constraints checked; {failures} failures.", "rule_provenance_results.csv")


def evaluate_rejected_step_isolation_graph(graph: dict[str, Any], validations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    status_by_step = {str(r.get("step_id")): str(r.get("status")) for r in validations}
    rows = []
    failures = 0
    for edge in [e for e in idx["edges"] if e.get("type") == "DEPENDS_ON"]:
        support_step = node_id_to_step_id(str(edge.get("target")))
        rejected = status_by_step.get(support_step) == "rejected"
        if rejected:
            failures += 1
        rows.append(
            {
                "dependent_step_id": node_id_to_step_id(str(edge.get("source"))),
                "support_step_id": support_step,
                "support_status": status_by_step.get(support_step, ""),
                "edge_type": "DEPENDS_ON",
                "status": "FAIL" if rejected else "PASS",
                "notes": "rejected step supports dependency" if rejected else "support step is not rejected",
            }
        )
    for node in idx["constraint_nodes"]:
        props = idx["props_by_id"].get(str(node.get("id")), {})
        step_id = ""
        for edge in idx["in_edges"].get((str(node.get("id")), "PRODUCES"), []):
            step_id = node_id_to_step_id(str(edge.get("source")))
        if step_id and status_by_step.get(step_id) == "rejected":
            lifecycle = props.get("effect_lifecycle_status", "")
            ok = lifecycle == "inactive_rejected"
            if not ok:
                failures += 1
            rows.append(
                {
                    "dependent_step_id": "",
                    "support_step_id": step_id,
                    "support_status": "rejected",
                    "edge_type": "PRODUCES",
                    "status": "PASS" if ok else "FAIL",
                    "notes": f"rejected produced effect lifecycle={lifecycle}",
                }
            )
    status = "FAIL" if failures else "PASS"
    return rows, CheckResult("E4.7", "Rejected-step isolation", "graph_dependencies", status, "critical", "procedural_reasoning_graph_edges.csv,effect_history_diagnostics.csv", f"{len(rows)} rejected-step graph checks; {failures} failures.", "rejected_step_isolation_graph_results.csv")


def evaluate_provisional_dependency_visibility(graph: dict[str, Any], validations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    status_by_step = {str(r.get("step_id")): str(r.get("status")) for r in validations}
    rows = []
    failures = 0
    applicable = 0
    for edge in [e for e in idx["edges"] if e.get("type") == "DEPENDS_ON"]:
        support_step = node_id_to_step_id(str(edge.get("target")))
        if status_by_step.get(support_step) != "uncertain":
            continue
        applicable += 1
        props = parse_props(edge.get("properties"))
        ok = props.get("provisional") is True
        if not ok:
            failures += 1
        rows.append(
            {
                "dependent_step_id": node_id_to_step_id(str(edge.get("source"))),
                "support_step_id": support_step,
                "support_status": "uncertain",
                "provisional_property": props.get("provisional", ""),
                "status": "PASS" if ok else "FAIL",
                "notes": "uncertain support marked provisional" if ok else "uncertain support lacks provisional=true",
            }
        )
    status = "FAIL" if failures else ("PASS" if applicable else "SKIPPED")
    message = f"{applicable} uncertain-support dependencies checked; {failures} failures." if applicable else "No DEPENDS_ON edge targets an uncertain support step in this graph."
    return rows, CheckResult("E4.8", "Provisional dependency visibility", "graph_dependencies", status, "warning", "procedural_reasoning_graph_edges.csv", message, "provisional_dependency_results.csv")


def evaluate_effect_invalidation_visibility(graph: dict[str, Any]) -> tuple[list[dict[str, Any]], CheckResult]:
    idx = graph_indexes(graph)
    out_edges = idx["out_edges"]
    failures = 0
    rows = []
    invalidated_nodes = [node for node in idx["constraint_nodes"] if idx["props_by_id"].get(str(node.get("id")), {}).get("effect_lifecycle_status") == "invalidated"]
    for node in invalidated_nodes:
        node_id = str(node.get("id"))
        props = idx["props_by_id"].get(node_id, {})
        invalidated_by = props.get("invalidated_by_constraint_id", "")
        invalidation_edges = out_edges.get((node_id, "INVALIDATED_BY"), [])
        linked_targets = [str(edge.get("target")) for edge in invalidation_edges]
        expected_target = f"Constraint::{invalidated_by}" if invalidated_by else ""
        has_expected = expected_target in linked_targets
        invalidating_has_step = bool(idx["in_edges"].get((expected_target, "PRODUCES")))
        ok = has_expected and invalidating_has_step
        if not ok:
            failures += 1
        rows.append(
            {
                "invalidated_constraint_node_id": node_id,
                "invalidated_constraint_id": props.get("constraint_id", ""),
                "effect_lifecycle_status": props.get("effect_lifecycle_status", ""),
                "invalidated_by_constraint_id": invalidated_by,
                "linked_invalidating_constraint_node_id": expected_target if has_expected else "",
                "invalidating_constraint_has_producing_step": invalidating_has_step,
                "status": "PASS" if ok else "FAIL",
                "notes": "invalidated effect linked to invalidating produced effect" if ok else "INVALIDATED_BY link or invalidating Step connection missing",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "SKIPPED")
    return rows, CheckResult("E4.9", "Effect invalidation visibility", "graph_lifecycle", status, "critical", "procedural_reasoning_graph_nodes.csv,procedural_reasoning_graph_edges.csv", f"{len(rows)} invalidated effects checked; {failures} failures.", "effect_invalidation_graph_results.csv")


def detect_missing_data(ctx: EvaluationContext) -> list[dict[str, str]]:
    missing = []
    for name in REQUIRED_GRAPH_FILES:
        path = ctx.graph_dir / name
        if not path.exists():
            missing.append({"path": str(path), "why_needed": f"required procedural graph artifact: {name}"})
    for name in REQUIRED_REASONING_FILES:
        path = ctx.reasoning_dir / name
        if not path.exists():
            missing.append({"path": str(path), "why_needed": f"required Layer 4 cross-check artifact: {name}"})
    return missing


def write_missing_data_report(ctx: EvaluationContext, missing: list[dict[str, str]]) -> None:
    lines = [
        "# Evaluation 4 Missing Data Report",
        "",
        f"- Selected graph: `{ctx.clip_result_id}`",
        f"- Graph directory: `{ctx.graph_dir}`",
        f"- Reasoning directory: `{ctx.reasoning_dir}`",
        "",
        "## Missing Paths",
        "",
    ]
    lines.extend(f"- `{item['path']}`: {item['why_needed']}" for item in missing)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "missing_data_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def inventory_rows(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = list(graph.get("nodes", []) or [])
    edges = list(graph.get("edges", []) or [])
    rows = []
    for node_type, count in sorted(Counter(str(n.get("type")) for n in nodes).items()):
        rows.append({"item_type": "node", "graph_type": node_type, "count": count})
    for edge_type, count in sorted(Counter(str(e.get("type")) for e in edges).items()):
        rows.append({"item_type": "edge", "graph_type": edge_type, "count": count})
    return rows


def write_neo4j_views(ctx: EvaluationContext) -> None:
    graph_name = f"procedural_reasoning_graph::{ctx.clip_result_id}"
    views = [
        (
            "A. Step Status And Temporal Order",
            "Shows validated Step nodes in sequence with NEXT edges only.",
            f"""MATCH path = (s1:Step)-[:NEXT]->(s2:Step)
WHERE s1.graph_name = "{graph_name}" AND s2.graph_name = s1.graph_name
RETURN path;""",
            "Supports order preservation and Step status visibility.",
            "eval04_A_step_status_temporal_order.png",
            "Procedural Step nodes ordered by NEXT edges, with validation status shown on each Step.",
        ),
        (
            "B. Dependency View",
            "Shows temporal order and inferred procedural dependency support between steps.",
            f"""MATCH path = (s1:Step)-[:NEXT|DEPENDS_ON]->(s2:Step)
WHERE s1.graph_name = "{graph_name}" AND s2.graph_name = s1.graph_name
RETURN path;""",
            "Supports dependency grounding and rejected-step isolation.",
            "eval04_B_step_dependencies.png",
            "Step sequence with DEPENDS_ON links exposing dependency support between validated steps.",
        ),
        (
            "C. Constraint Evidence View",
            "Shows Step nodes connected to requirement, produced-effect, and incompatibility constraints.",
            f"""MATCH path = (s:Step)-[:REQUIRES|PRODUCES|HAS_CONSTRAINT]->(c:Constraint)
WHERE s.graph_name = "{graph_name}"
  AND c.graph_name = s.graph_name
  AND (c.name IN ["requires", "requiresTool", "requiresSafety", "produces", "incompatibleAction"])
RETURN path;""",
            "Supports requirement visibility, produced-effect visibility, and incompatibility traceability.",
            "eval04_C_constraint_evidence.png",
            "Graph view of Step-to-Constraint evidence, including requirements, produced effects, and incompatibilities.",
        ),
        (
            "D. Effect Lifecycle And Invalidation",
            "Shows invalidated produced effects and the later removal effects that invalidated them.",
            f"""MATCH path =
  (s1:Step)-[:PRODUCES]->(c1:Constraint)
  -[:INVALIDATED_BY]->(c2:Constraint)<-[:PRODUCES]-(s2:Step)
WHERE s1.graph_name = "{graph_name}" AND s2.graph_name = s1.graph_name
RETURN path;""",
            "Supports effect invalidation visibility and produced-effect lifecycle traceability.",
            "eval04_D_effect_lifecycle_invalidation.png",
            "Produced-effect constraints linked by INVALIDATED_BY relations to later removal effects.",
        ),
        (
            "E. Full Representative Trace",
            "Shows one compact local explanation neighborhood around a selected Step.",
            f"""MATCH (s:Step)
WHERE s.graph_name = "{graph_name}" AND s.index = 17
OPTIONAL MATCH p1 = (s)-[:HAS_PREDICATE|HAS_CONSTRAINT|REQUIRES|PRODUCES]->()
OPTIONAL MATCH p2 = (s)-[:DEPENDS_ON]->(:Step)
OPTIONAL MATCH p3 = (s)-[:HAS_CONSTRAINT]->(:Constraint)-[:DERIVED_FROM|SUPPORTED_BY|HAS_ENTITY]->()
RETURN p1, p2, p3;""",
            "Supports the claim that a reader can start from a Step and follow predicates, constraints, rules, dependencies, and entities.",
            "eval04_E_full_representative_trace_step17.png",
            "Compact explanation neighborhood for a representative Step, showing evidence and provenance links.",
        ),
    ]
    lines = ["# Evaluation 4 Neo4j Screenshot Views", ""]
    for title, purpose, query, claim, filename, caption in views:
        lines.extend(
            [
                f"## {title}",
                "",
                f"Purpose: {purpose}",
                "",
                "```cypher",
                query,
                "```",
                "",
                f"Thesis claim supported: {claim}",
                "",
                f"Suggested screenshot filename: `{filename}`",
                "",
                f"Suggested caption: {caption}",
                "",
            ]
        )
    (ctx.output_dir / "neo4j_views.md").write_text("\n".join(lines), encoding="utf-8")


def evaluate(ctx: EvaluationContext) -> dict[str, Any]:
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = ctx.output_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    missing = detect_missing_data(ctx)
    if missing:
        write_missing_data_report(ctx, missing)
        checks = [CheckResult("E4.0", "Required graph and validation artifacts present", "inputs", "FAIL", "critical", "graph_dir,reasoning_dir", f"{len(missing)} required artifacts missing.", "missing_data_report.md")]
        result = {"evaluation": "Evaluation 4: Procedural graph traceability", "timestamp": ctx.timestamp, "clip_result_id": ctx.clip_result_id, "missing": missing, "checks": [c.__dict__ for c in checks]}
        write_csv(ctx.output_dir / "evaluation4_summary.csv", [c.__dict__ for c in checks], ["check_id", "check_name", "category", "status", "severity", "artifact", "message", "evidence_file"])
        write_json(evidence_dir / "evaluation4_results.json", result)
        write_readme(ctx)
        return result

    graph = load_json(ctx.graph_dir / "procedural_reasoning_graph.json")
    validations = load_jsonl(ctx.reasoning_dir / "validation_records.jsonl")
    load_csv(ctx.graph_dir / "procedural_reasoning_graph_nodes.csv")
    load_csv(ctx.graph_dir / "procedural_reasoning_graph_edges.csv")
    load_csv(ctx.reasoning_dir / "step_validations.csv")
    load_json(ctx.reasoning_dir / "explanation_traces.json")
    effect_history = load_csv(ctx.reasoning_dir / "effect_history_diagnostics.csv")

    checks: list[CheckResult] = []
    order_rows, check = evaluate_order_preservation(graph); checks.append(check)
    dep_rows, check = evaluate_dependency_grounding(graph, validations); checks.append(check)
    req_rows, check = evaluate_requirement_visibility(graph, validations); checks.append(check)
    missing_req_rows, check = evaluate_missing_requirement_visibility(graph, validations); checks.append(check)
    evidence_rows, check = evaluate_evidence_traceability(graph, validations); checks.append(check)
    rule_rows, check = evaluate_rule_provenance(graph); checks.append(check)
    rejected_rows, check = evaluate_rejected_step_isolation_graph(graph, validations); checks.append(check)
    provisional_rows, check = evaluate_provisional_dependency_visibility(graph, validations); checks.append(check)
    invalidation_rows, check = evaluate_effect_invalidation_visibility(graph); checks.append(check)

    inventory = inventory_rows(graph)
    write_csv(ctx.output_dir / "order_preservation_results.csv", order_rows, ["edge_source", "edge_target", "source_index", "target_index", "expected_edge", "duplicate", "status", "notes"])
    write_csv(ctx.output_dir / "dependency_grounding_results.csv", dep_rows, ["dependent_step_id", "support_step_id", "required_condition", "has_validation_dependency_support", "dependent_has_requirement_edge", "support_has_produces_edge", "support_status", "rejected_support", "status", "notes"])
    write_csv(ctx.output_dir / "requirement_visibility_results.csv", req_rows, ["step_id", "constraint_id", "requirement_name", "source_field", "has_constraint_node", "has_step_requirement_edge", "name_preserved", "args_preserved", "status", "notes"])
    write_csv(ctx.output_dir / "missing_requirement_visibility_results.csv", missing_req_rows, ["step_id", "constraint_id", "requirement_name", "has_graph_constraint_node", "support_status", "visible_as_missing", "status", "notes"])
    write_csv(ctx.output_dir / "evidence_traceability_results.csv", evidence_rows, ["step_id", "step_index", "status_value", "confidence_value", "predicate_evidence_count", "constraint_evidence_count", "has_predicate_edges", "has_constraint_edges", "status", "notes"])
    write_csv(ctx.output_dir / "rule_provenance_results.csv", rule_rows, ["constraint_node_id", "constraint_id", "rule_id", "has_rule_node", "has_derived_from_edge", "status", "notes"])
    write_csv(ctx.output_dir / "rejected_step_isolation_graph_results.csv", rejected_rows, ["dependent_step_id", "support_step_id", "support_status", "edge_type", "status", "notes"])
    write_csv(ctx.output_dir / "provisional_dependency_results.csv", provisional_rows, ["dependent_step_id", "support_step_id", "support_status", "provisional_property", "status", "notes"])
    write_csv(ctx.output_dir / "effect_invalidation_graph_results.csv", invalidation_rows, ["invalidated_constraint_node_id", "invalidated_constraint_id", "effect_lifecycle_status", "invalidated_by_constraint_id", "linked_invalidating_constraint_node_id", "invalidating_constraint_has_producing_step", "status", "notes"])
    write_csv(ctx.output_dir / "graph_inventory.csv", inventory, ["item_type", "graph_type", "count"])
    write_csv(ctx.output_dir / "evaluation4_summary.csv", [c.__dict__ for c in checks], ["check_id", "check_name", "category", "status", "severity", "artifact", "message", "evidence_file"])
    write_neo4j_views(ctx)

    node_counts = {row["graph_type"]: row["count"] for row in inventory if row["item_type"] == "node"}
    edge_counts = {row["graph_type"]: row["count"] for row in inventory if row["item_type"] == "edge"}
    step_status_counts = Counter(str(v.get("status")) for v in validations)
    result = {
        "evaluation": "Evaluation 4: Procedural graph traceability",
        "timestamp": ctx.timestamp,
        "clip_result_id": ctx.clip_result_id,
        "input_directories": {"graph_dir": str(ctx.graph_dir), "reasoning_dir": str(ctx.reasoning_dir)},
        "graph_name": graph.get("graph_name"),
        "node_counts": node_counts,
        "edge_counts": edge_counts,
        "step_status_distribution": dict(sorted(step_status_counts.items())),
        "effect_history_rows": len(effect_history),
        "checks": [c.__dict__ for c in checks],
        "details": {
            "order_preservation": order_rows,
            "dependency_grounding": dep_rows,
            "requirement_visibility": req_rows,
            "missing_requirement_visibility": missing_req_rows,
            "evidence_traceability": evidence_rows,
            "rule_provenance": rule_rows,
            "rejected_step_isolation": rejected_rows,
            "provisional_dependency_visibility": provisional_rows,
            "effect_invalidation_visibility": invalidation_rows,
        },
    }
    write_json(evidence_dir / "evaluation4_results.json", result)
    write_readme(ctx)
    write_report(ctx, result)
    return result


def write_readme(ctx: EvaluationContext) -> None:
    text = f"""# Evaluation 4: Procedural Graph Traceability

This folder contains reproducible evidence for thesis Evaluation 4. It checks whether the procedural reasoning graph exposes the reasoning trace behind validation outcomes through Step, Predicate, Constraint, Rule, Entity, and relationship structure.

Evaluation 4 evaluates graph traceability. It does not re-evaluate Layer 3 coverage, Layer 4 validation correctness, perception accuracy, or dataset-wide graph coverage.

## Selected Graph

- Clip/result ID: `{ctx.clip_result_id}`
- Graph directory: `{ctx.graph_dir}`
- Reasoning directory: `{ctx.reasoning_dir}`

The selected graph matches Evaluation 3 because it contains accepted, uncertain, and rejected steps; dependency support; incompatibilities; removal actions; invalidated effects; and produced-effect lifecycle information.

## How To Run

```powershell
.venv\\Scripts\\python.exe scripts\\23_evaluate_graph_traceability.py --project-root . --clip-result-id {ctx.clip_result_id} --reasoning-dir results\\reasoning_layers\\{ctx.clip_result_id} --graph-dir results\\procedural_reasoning_graph\\{ctx.clip_result_id} --output-dir docs\\reasoning_layers\\Evaluation4 --strict
```

## Required Inputs

- `procedural_reasoning_graph.json`
- `procedural_reasoning_graph_nodes.csv`
- `procedural_reasoning_graph_edges.csv`
- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`
- `effect_history_diagnostics.csv`

## Generated Outputs

- `evaluation4_report.md`
- `evaluation4_summary.csv`
- detailed per-check CSV files
- `graph_inventory.csv`
- `neo4j_views.md`
- `evidence/evaluation4_results.json`
- `missing_data_report.md` only when required inputs are missing.
"""
    (ctx.output_dir / "README.md").write_text(text, encoding="utf-8")


def write_report(ctx: EvaluationContext, result: dict[str, Any]) -> None:
    status_counts = {status: sum(1 for c in result["checks"] if c["status"] == status) for status in STATUSES}
    lines = [
        "# Evaluation 4 Report: Procedural Graph Traceability",
        "",
        f"- Evaluated graph: `{result.get('graph_name')}`",
        f"- Clip/result ID: `{ctx.clip_result_id}`",
        f"- Timestamp: `{ctx.timestamp}`",
        f"- Graph directory: `{ctx.graph_dir}`",
        f"- Reasoning directory: `{ctx.reasoning_dir}`",
        "",
        "## Node Type Distribution",
        "",
        "| Node type | Count |",
        "| --- | ---: |",
    ]
    for key, value in sorted(result["node_counts"].items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Edge Type Distribution", "", "| Edge type | Count |", "| --- | ---: |"])
    for key, value in sorted(result["edge_counts"].items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Step Status Distribution", "", "| Status | Count |", "| --- | ---: |"])
    for key, value in sorted(result["step_status_distribution"].items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Traceability Checks", "", "| Check | Status | Message | Evidence |", "| --- | --- | --- | --- |"])
    for check in result["checks"]:
        lines.append(f"| {check['check_name']} | {check['status']} | {check['message']} | `{check['evidence_file']}` |")
    lines.extend(
        [
            "",
            "## Neo4j Views",
            "",
            "Screenshot-oriented Cypher queries are listed in `neo4j_views.md`. They split the evidence into temporal order, dependency, constraint evidence, effect lifecycle, and compact representative trace views.",
            "",
            "## Limitations",
            "",
            "This evaluation checks graph traceability against the exported artifacts for one representative clip. It does not evaluate perception, dataset-wide graph coverage, or whether every possible Neo4j visualization layout will be visually readable without manual styling.",
            "",
            f"Status totals: PASS={status_counts['PASS']}, FAIL={status_counts['FAIL']}, WARNING={status_counts['WARNING']}, SKIPPED={status_counts['SKIPPED']}.",
            "",
        ]
    )
    (ctx.output_dir / "evaluation4_report.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--clip-result-id", default=DEFAULT_CLIP_RESULT_ID)
    parser.add_argument("--reasoning-dir", type=Path, default=None)
    parser.add_argument("--graph-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/reasoning_layers/Evaluation4"))
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def build_context(args: argparse.Namespace) -> EvaluationContext:
    project_root = args.project_root.resolve()
    reasoning_dir = args.reasoning_dir or Path("results") / "reasoning_layers" / args.clip_result_id
    graph_dir = args.graph_dir or Path("results") / "procedural_reasoning_graph" / args.clip_result_id
    output_dir = args.output_dir
    return EvaluationContext(
        project_root=project_root,
        clip_result_id=args.clip_result_id,
        reasoning_dir=(project_root / reasoning_dir).resolve() if not reasoning_dir.is_absolute() else reasoning_dir,
        graph_dir=(project_root / graph_dir).resolve() if not graph_dir.is_absolute() else graph_dir,
        output_dir=(project_root / output_dir).resolve() if not output_dir.is_absolute() else output_dir,
        strict=args.strict,
    )


def main(argv: list[str] | None = None) -> int:
    ctx = build_context(parse_args(argv))
    result = evaluate(ctx)
    if ctx.strict:
        critical_failures = [row for row in result.get("checks", []) if row.get("status") == "FAIL" and row.get("severity") == "critical"]
        if critical_failures:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
