"""Evaluation 1: pipeline artifact correctness.

This evaluator checks the thesis Evaluation 1 artifact chain without requiring
Neo4j. It reads local CSV/JSON/JSONL files, writes human and machine-readable
evidence, and exits non-zero in strict mode for critical failures.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


STATUSES = {"PASS", "FAIL", "WARNING", "SKIPPED"}
VALIDATION_STATUSES = {"accepted", "uncertain", "rejected"}
CHECKS = [
    ("E1.1", "Step records produced", "adapter"),
    ("E1.2", "Predicate records produced", "adapter"),
    ("E1.3", "Layer 3 constraints produced", "layer3"),
    ("E1.4", "Layer 4 validation records produced", "layer4"),
    ("E1.5", "Explanation traces produced", "layer4"),
    ("E1.6", "Graph export produced", "graph"),
    ("E1.7", "Input order preserved", "graph"),
    ("E1.8", "Rejected-step dependency rule respected", "graph"),
    ("E1.9", "Rule coverage diagnostics", "diagnostics"),
]


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
class InventoryRow:
    artifact_name: str
    path: str
    type: str
    role_in_pipeline: str
    exists: bool
    size_bytes: int
    record_count: int | str
    notes: str = ""


@dataclass
class EvaluationContext:
    project_root: Path
    run_id: str
    clip_result_id: str
    neo4j_dir: Path
    reasoning_dir: Path
    graph_dir: Path
    output_dir: Path
    upstream_result_dir: Path
    preserved_tarball: Path
    strict: bool = False
    download_missing: bool = False
    restore_preserved: bool = False
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: JSONL record is not an object")
            records.append(value)
    return records


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def count_records(path: Path) -> int | str:
    if not path.exists():
        return ""
    try:
        if path.suffix == ".jsonl":
            return len(load_jsonl(path))
        if path.suffix == ".json":
            value = load_json(path)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                if "nodes" in value and "edges" in value:
                    return len(value.get("nodes", [])) + len(value.get("edges", []))
                return len(value)
        if path.suffix == ".csv":
            return len(load_csv(path))
    except Exception as exc:  # pragma: no cover - inventory must be best effort.
        return f"unreadable: {exc}"
    return ""


def is_confidence(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0.0 <= number <= 1.0


def validate_required_fields(
    records: Iterable[dict[str, Any]],
    required_fields: Iterable[str],
    artifact: str,
    id_field: str = "id",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records):
        record_id = str(record.get(id_field) or record.get("step_id") or index)
        for field_name in required_fields:
            present = field_name in record and record.get(field_name) not in (None, "")
            rows.append(
                {
                    "artifact": artifact,
                    "record_id": record_id,
                    "validation_type": "required_field",
                    "field": field_name,
                    "status": "PASS" if present else "FAIL",
                    "message": "present" if present else "missing required field",
                }
            )
    return rows


def validate_confidence_range(
    records: Iterable[dict[str, Any]],
    field_name: str,
    artifact: str,
    id_field: str = "id",
    required: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records):
        record_id = str(record.get(id_field) or record.get("step_id") or index)
        if field_name not in record or record.get(field_name) in (None, ""):
            if required:
                rows.append(
                    {
                        "artifact": artifact,
                        "record_id": record_id,
                        "validation_type": "confidence_range",
                        "field": field_name,
                        "status": "FAIL",
                        "message": "missing confidence field",
                    }
                )
            continue
        ok = is_confidence(record.get(field_name))
        rows.append(
            {
                "artifact": artifact,
                "record_id": record_id,
                "validation_type": "confidence_range",
                "field": field_name,
                "status": "PASS" if ok else "FAIL",
                "message": "in [0, 1]" if ok else f"invalid confidence {record.get(field_name)!r}",
            }
        )
    return rows


def validate_references(
    records: Iterable[dict[str, Any]],
    field_name: str,
    valid_ids: set[str],
    artifact: str,
    target_artifact: str,
    id_field: str = "id",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records):
        record_id = str(record.get(id_field) or index)
        ref = record.get(field_name)
        ok = ref in valid_ids
        rows.append(
            {
                "source_artifact": artifact,
                "source_id": record_id,
                "reference_field": field_name,
                "target_artifact": target_artifact,
                "target_id": str(ref),
                "status": "PASS" if ok else "FAIL",
                "message": "reference resolved" if ok else "dangling reference",
            }
        )
    return rows


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


def copy_if_different(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def row_id(row: dict[str, Any], candidates: Iterable[str]) -> str:
    for key in candidates:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def parse_properties(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def unprefix_step_node_id(node_id: str) -> str:
    return node_id[len("Step::") :] if node_id.startswith("Step::") else node_id


def detect_missing_data(ctx: EvaluationContext) -> list[dict[str, str]]:
    required = [
        (
            ctx.neo4j_dir / "nodes_events.csv",
            "upstream input steps for checking adapter step coverage",
        ),
        (ctx.reasoning_dir / "step_records.jsonl", "adapter step records"),
        (ctx.reasoning_dir / "predicates.jsonl", "adapter predicate records"),
        (ctx.reasoning_dir / "inferred_constraints.csv", "Layer 3 inferred constraints"),
        (ctx.reasoning_dir / "rule_coverage_diagnostics.csv", "Layer 3 rule coverage diagnostics"),
        (ctx.reasoning_dir / "validation_records.jsonl", "Layer 4 validation records"),
        (ctx.reasoning_dir / "explanation_traces.json", "Layer 4 explanation traces"),
        (ctx.graph_dir / "procedural_reasoning_graph.json", "procedural graph JSON"),
        (ctx.graph_dir / "procedural_reasoning_graph_nodes.csv", "procedural graph nodes"),
        (ctx.graph_dir / "procedural_reasoning_graph_edges.csv", "procedural graph edges"),
    ]
    missing = [
        {"path": str(path), "why_needed": why}
        for path, why in required
        if not path.exists()
    ]
    if not ctx.upstream_result_dir.exists():
        missing.append(
            {
                "path": str(ctx.upstream_result_dir),
                "why_needed": "preserved upstream per-clip outputs; useful when adapter inputs must be regenerated",
            }
        )
    return missing


def restore_preserved_outputs(ctx: EvaluationContext) -> str:
    if ctx.upstream_result_dir.exists():
        return f"Upstream result folder already exists: {ctx.upstream_result_dir}"
    if not ctx.preserved_tarball.exists():
        return f"Preserved tarball not found: {ctx.preserved_tarball}"
    target_parent = ctx.upstream_result_dir.parent
    target_parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ctx.preserved_tarball, "r:gz") as archive:
        archive.extractall(target_parent)
    return f"Restored {ctx.preserved_tarball} to {target_parent}"


def maybe_download_missing(ctx: EvaluationContext) -> str:
    if not ctx.download_missing:
        return "Download not requested."
    command = [
        sys.executable,
        str(ctx.project_root / "scripts" / "11_run_oracle_dataset_batch.py"),
        "--config",
        str(ctx.project_root / "configs" / "raw_cad_dataset.json"),
        "--download-missing",
    ]
    completed = subprocess.run(
        command,
        cwd=ctx.project_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return (
        f"Command: {' '.join(command)}\n"
        f"Return code: {completed.returncode}\n"
        f"STDOUT:\n{completed.stdout[-4000:]}\nSTDERR:\n{completed.stderr[-4000:]}"
    )


def write_missing_data_report(ctx: EvaluationContext, missing: list[dict[str, str]], notes: list[str]) -> None:
    preserved_status = "available" if ctx.preserved_tarball.exists() else "not found"
    lines = [
        "# Evaluation 1 Missing Data Report",
        "",
        "Evaluation 1 did not find all required local artifacts.",
        "",
        f"- Run ID: `{ctx.run_id}`",
        f"- Clip/result ID: `{ctx.clip_result_id}`",
        f"- Preserved tarball: `{ctx.preserved_tarball}` ({preserved_status})",
        "",
        "## Missing Paths",
        "",
    ]
    for item in missing:
        path = item["path"]
        reason = item["why_needed"]
        lines.extend(
            [
                f"### `{path}`",
                f"- Why needed: {reason}",
                "- Restoration/regeneration: restore the preserved tarball when available, or regenerate the upstream Neo4j/reasoning artifacts with the existing pipeline scripts.",
                "- Download: supported only through the existing dataset batch runner with `--download-missing`; this evaluator does not download by default.",
                "",
            ]
        )
    lines.extend(
        [
            "## Suggested Commands",
            "",
            "```powershell",
            ".venv\\Scripts\\python.exe scripts\\20_evaluate_pipeline_artifact_correctness.py --restore-preserved",
            ".venv\\Scripts\\python.exe scripts\\12_export_neo4j_csv.py",
            ".venv\\Scripts\\python.exe scripts\\14_build_layer3_reasoning_adapter.py --clip-result-id raw_cad_dataset__all_test_clips::od_only::test_p1::03_assy_0_1 --output-dir results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1",
            ".venv\\Scripts\\python.exe scripts\\15_run_layer3_inference.py --step-records results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\step_records.jsonl --predicates results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\predicates.jsonl --output results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\inferred_constraints.csv",
            ".venv\\Scripts\\python.exe scripts\\16_run_layer4_validation.py --step-records results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\step_records.jsonl --predicates results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\predicates.jsonl --constraints results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\inferred_constraints.csv --output results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\validation_records.jsonl",
            ".venv\\Scripts\\python.exe scripts\\17_build_procedural_reasoning_graph.py --validations results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\validation_records.jsonl --step-records results\\reasoning_layers\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\\step_records.jsonl --output-dir results\\procedural_reasoning_graph\\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1",
            "```",
            "",
        ]
    )
    if notes:
        lines.extend(["## Restoration/Download Log", ""])
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "missing_data_report.md").write_text("\n".join(lines), encoding="utf-8")


def artifact_inventory(ctx: EvaluationContext) -> list[InventoryRow]:
    artifacts = [
        ("nodes_events.csv", ctx.neo4j_dir / "nodes_events.csv", "csv", "upstream input steps"),
        ("step_records.jsonl", ctx.reasoning_dir / "step_records.jsonl", "jsonl", "adapter step records"),
        ("predicates.jsonl", ctx.reasoning_dir / "predicates.jsonl", "jsonl", "adapter symbolic evidence"),
        ("inferred_constraints.csv", ctx.reasoning_dir / "inferred_constraints.csv", "csv", "Layer 3 constraints"),
        ("rule_coverage_diagnostics.csv", ctx.reasoning_dir / "rule_coverage_diagnostics.csv", "csv", "Layer 3 rule coverage diagnostics"),
        ("validation_records.jsonl", ctx.reasoning_dir / "validation_records.jsonl", "jsonl", "Layer 4 validation records"),
        ("step_validations.csv", ctx.reasoning_dir / "step_validations.csv", "csv", "Layer 4 tabular validation view"),
        ("explanation_traces.json", ctx.reasoning_dir / "explanation_traces.json", "json", "Layer 4 explanations"),
        ("procedural_reasoning_graph.json", ctx.graph_dir / "procedural_reasoning_graph.json", "json", "graph export"),
        ("procedural_reasoning_graph_nodes.csv", ctx.graph_dir / "procedural_reasoning_graph_nodes.csv", "csv", "graph node export"),
        ("procedural_reasoning_graph_edges.csv", ctx.graph_dir / "procedural_reasoning_graph_edges.csv", "csv", "graph edge export"),
    ]
    rows: list[InventoryRow] = []
    for name, path, file_type, role in artifacts:
        rows.append(
            InventoryRow(
                artifact_name=name,
                path=str(path),
                type=file_type,
                role_in_pipeline=role,
                exists=path.exists(),
                size_bytes=path.stat().st_size if path.exists() else 0,
                record_count=count_records(path),
            )
        )
    return rows


def evaluate(ctx: EvaluationContext) -> dict[str, Any]:
    output = ctx.output_dir
    evidence_dir = output / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "events": ctx.neo4j_dir / "nodes_events.csv",
        "steps": ctx.reasoning_dir / "step_records.jsonl",
        "predicates": ctx.reasoning_dir / "predicates.jsonl",
        "constraints": ctx.reasoning_dir / "inferred_constraints.csv",
        "rule_coverage": ctx.reasoning_dir / "rule_coverage_diagnostics.csv",
        "validations": ctx.reasoning_dir / "validation_records.jsonl",
        "traces": ctx.reasoning_dir / "explanation_traces.json",
        "graph_json": ctx.graph_dir / "procedural_reasoning_graph.json",
        "graph_nodes": ctx.graph_dir / "procedural_reasoning_graph_nodes.csv",
        "graph_edges": ctx.graph_dir / "procedural_reasoning_graph_edges.csv",
    }

    check_results: list[CheckResult] = []
    schema_rows: list[dict[str, str]] = []
    reference_rows: list[dict[str, str]] = []
    order_rows: list[dict[str, str]] = []
    dependency_rows: list[dict[str, str]] = []
    counts: dict[str, Any] = {}
    details: dict[str, Any] = {}

    def add_check(check_id: str, status: str, severity: str, artifact: str, message: str, evidence_file: str = "") -> None:
        name, category = next((name, cat) for cid, name, cat in CHECKS if cid == check_id)
        check_results.append(CheckResult(check_id, name, category, status, severity, artifact, message, evidence_file))

    events = load_csv(paths["events"]) if paths["events"].exists() else []
    steps = load_jsonl(paths["steps"]) if paths["steps"].exists() else []
    predicates = load_jsonl(paths["predicates"]) if paths["predicates"].exists() else []
    constraints = load_csv(paths["constraints"]) if paths["constraints"].exists() else []
    rule_coverage = load_csv(paths["rule_coverage"]) if paths["rule_coverage"].exists() else []
    validations = load_jsonl(paths["validations"]) if paths["validations"].exists() else []
    traces = load_json(paths["traces"]) if paths["traces"].exists() else []
    graph = load_json(paths["graph_json"]) if paths["graph_json"].exists() else {}
    graph_nodes = load_csv(paths["graph_nodes"]) if paths["graph_nodes"].exists() else []
    graph_edges = load_csv(paths["graph_edges"]) if paths["graph_edges"].exists() else []

    if isinstance(traces, dict):
        trace_list = list(traces.values()) if all(isinstance(v, dict) for v in traces.values()) else [traces]
    elif isinstance(traces, list):
        trace_list = [t for t in traces if isinstance(t, dict)]
    else:
        trace_list = []

    counts.update(
        {
            "events": len(events),
            "step_records": len(steps),
            "predicates": len(predicates),
            "constraints": len(constraints),
            "rule_coverage_diagnostics": len(rule_coverage),
            "validation_records": len(validations),
            "explanation_traces": len(trace_list),
            "graph_nodes": len(graph_nodes),
            "graph_edges": len(graph_edges),
        }
    )

    step_ids = {str(step.get("id")) for step in steps if step.get("id")}
    step_source_event_ids = {str(step.get("source_event_id")) for step in steps if step.get("source_event_id")}
    expected_events = [
        row for row in events if row.get("clip_result_id") == ctx.clip_result_id or not ctx.clip_result_id
    ]
    if not expected_events and steps:
        adapter_source_clip_ids = {str(step.get("clip_result_id")) for step in steps if step.get("clip_result_id")}
        if adapter_source_clip_ids:
            expected_events = [row for row in events if str(row.get("clip_result_id")) in adapter_source_clip_ids]
    expected_event_ids = {row_id(row, ["event_id:ID(AssemblyEvent)", "event_id", "id"]) for row in expected_events}
    expected_event_ids.discard("")
    missing_steps = sorted(expected_event_ids - step_source_event_ids)
    extra_steps = sorted(step_source_event_ids - expected_event_ids) if expected_event_ids else []
    details["step_records"] = {
        "expected_input_steps": len(expected_event_ids),
        "step_records": len(steps),
        "missing_source_event_ids": missing_steps[:100],
        "extra_source_event_ids": extra_steps[:100],
    }
    write_json(evidence_dir / "step_record_coverage.json", details["step_records"])
    if not paths["steps"].exists() or not paths["events"].exists():
        add_check("E1.1", "FAIL", "critical", "step_records.jsonl/nodes_events.csv", "Required step coverage artifacts are missing.", "missing_data_report.md")
    elif missing_steps:
        add_check("E1.1", "FAIL", "critical", "step_records.jsonl", f"{len(missing_steps)} input steps lack step records.", "evidence/step_record_coverage.json")
    elif extra_steps:
        add_check("E1.1", "WARNING", "warning", "step_records.jsonl", f"All input steps are represented; {len(extra_steps)} extra step records were found.", "evidence/step_record_coverage.json")
    else:
        add_check("E1.1", "PASS", "critical", "step_records.jsonl", f"{len(steps)} step records cover {len(expected_event_ids)} input steps.", "evidence/step_record_coverage.json")

    schema_rows.extend(validate_required_fields(steps, ["id", "record_type", "source_event_id", "index"], "step_records.jsonl"))
    predicate_required = ["id", "step_id", "name", "args", "conf", "source"]
    schema_rows.extend(validate_required_fields(predicates, predicate_required, "predicates.jsonl"))
    schema_rows.extend(validate_confidence_range(predicates, "conf", "predicates.jsonl", required=True))
    reference_rows.extend(validate_references(predicates, "step_id", step_ids, "predicates.jsonl", "step_records.jsonl"))
    predicates_by_step: dict[str, int] = {}
    for predicate in predicates:
        predicates_by_step[str(predicate.get("step_id"))] = predicates_by_step.get(str(predicate.get("step_id")), 0) + 1
    steps_without_predicates = sorted(step_id for step_id in step_ids if predicates_by_step.get(step_id, 0) == 0)
    predicate_failures = [r for r in schema_rows if r["artifact"] == "predicates.jsonl" and r["status"] == "FAIL"]
    predicate_ref_failures = [r for r in reference_rows if r["source_artifact"] == "predicates.jsonl" and r["status"] == "FAIL"]
    details["predicates"] = {
        "predicates": len(predicates),
        "steps_without_predicates": steps_without_predicates[:100],
        "dangling_predicate_step_references": len(predicate_ref_failures),
        "schema_failures": len(predicate_failures),
    }
    write_json(evidence_dir / "predicate_validation_details.json", details["predicates"])
    if not paths["predicates"].exists():
        add_check("E1.2", "FAIL", "critical", "predicates.jsonl", "Predicate artifact is missing.", "missing_data_report.md")
    elif predicate_failures or predicate_ref_failures:
        add_check("E1.2", "FAIL", "critical", "predicates.jsonl", f"{len(predicate_failures)} schema/confidence failures and {len(predicate_ref_failures)} dangling step references.", "evidence/predicate_validation_details.json")
    elif steps_without_predicates:
        add_check("E1.2", "WARNING", "warning", "predicates.jsonl", f"{len(steps_without_predicates)} step records have no predicates.", "evidence/predicate_validation_details.json")
    else:
        add_check("E1.2", "PASS", "critical", "predicates.jsonl", f"{len(predicates)} predicates reference valid step records.", "evidence/predicate_validation_details.json")

    constraint_columns = set(constraints[0].keys()) if constraints else set()
    expected_constraint_columns = {"constraint_id", "step_id", "name", "kind", "args", "conf", "rule_id", "status"}
    for column in expected_constraint_columns:
        schema_rows.append(
            {
                "artifact": "inferred_constraints.csv",
                "record_id": "header",
                "validation_type": "required_column",
                "field": column,
                "status": "PASS" if column in constraint_columns else "FAIL",
                "message": "present" if column in constraint_columns else "missing required column",
            }
        )
    constraint_schema_failures = [r for r in schema_rows if r["artifact"] == "inferred_constraints.csv" and r["status"] == "FAIL"]
    for row in constraints:
        row["id"] = row.get("constraint_id", "")
    schema_rows.extend(validate_confidence_range(constraints, "conf", "inferred_constraints.csv", id_field="constraint_id", required=True))
    reference_rows.extend(validate_references(constraints, "step_id", step_ids, "inferred_constraints.csv", "step_records.jsonl", id_field="constraint_id"))
    constraint_ref_failures = [r for r in reference_rows if r["source_artifact"] == "inferred_constraints.csv" and r["status"] == "FAIL"]
    constraint_summary: dict[str, int] = {}
    for row in constraints:
        key = row.get("name") or row.get("kind") or "unknown"
        constraint_summary[key] = constraint_summary.get(key, 0) + 1
    details["constraints"] = {"summary_by_name": constraint_summary, "rows": len(constraints)}
    if paths["rule_coverage"].exists():
        copy_if_different(paths["rule_coverage"], output / "rule_coverage_diagnostics.csv")
    rule_coverage_by_step = {str(row.get("step_id")): row for row in rule_coverage if row.get("step_id")}
    uncovered_rule_steps = [
        row for row in rule_coverage
        if row.get("warning_code") == "no_applicable_rule"
    ]
    accepted_uncovered = []
    validation_by_step_for_coverage = {str(item.get("step_id")): item for item in validations if item.get("step_id")}
    for row in uncovered_rule_steps:
        validation = validation_by_step_for_coverage.get(str(row.get("step_id")), {})
        if validation.get("status") == "accepted":
            accepted_uncovered.append(row)
    for column in [
        "step_id",
        "action_name",
        "predicate_count",
        "matched_rule_count",
        "produced_constraint_count",
        "has_rule_coverage",
        "warning_code",
        "warning_message",
    ]:
        schema_rows.append(
            {
                "artifact": "rule_coverage_diagnostics.csv",
                "record_id": "header",
                "validation_type": "required_column",
                "field": column,
                "status": "PASS" if (not rule_coverage or column in set(rule_coverage[0].keys())) else "FAIL",
                "message": "present" if (not rule_coverage or column in set(rule_coverage[0].keys())) else "missing required column",
            }
        )
    details["constraints"]["rule_coverage_warnings"] = len(uncovered_rule_steps)
    details["constraints"]["accepted_uncovered_steps"] = len(accepted_uncovered)
    write_json(evidence_dir / "constraint_summary.json", details["constraints"])
    if not paths["constraints"].exists():
        add_check("E1.3", "FAIL", "critical", "inferred_constraints.csv", "Layer 3 constraints artifact is missing.", "missing_data_report.md")
    elif predicates and constraints == []:
        add_check("E1.3", "FAIL", "critical", "inferred_constraints.csv", "No constraints were produced even though predicates are available.", "evidence/constraint_summary.json")
    elif constraint_schema_failures or constraint_ref_failures:
        add_check("E1.3", "FAIL", "critical", "inferred_constraints.csv", f"{len(constraint_schema_failures)} schema failures and {len(constraint_ref_failures)} dangling step references.", "evidence/constraint_summary.json")
    elif accepted_uncovered:
        add_check("E1.3", "FAIL", "critical", "rule_coverage_diagnostics.csv", f"{len(accepted_uncovered)} uncovered rule steps are still accepted without qualification.", "rule_coverage_diagnostics.csv")
    else:
        coverage_note = f"; {len(uncovered_rule_steps)} no-applicable-rule diagnostics recorded" if uncovered_rule_steps else ""
        add_check("E1.3", "PASS", "critical", "inferred_constraints.csv", f"{len(constraints)} constraints produced; names: {constraint_summary}{coverage_note}.", "evidence/constraint_summary.json")

    schema_rows.extend(validate_required_fields(validations, ["step_id", "status"], "validation_records.jsonl", id_field="step_id"))
    schema_rows.extend(validate_confidence_range(validations, "confidence", "validation_records.jsonl", id_field="step_id", required=False))
    validation_step_ids = {str(v.get("step_id")) for v in validations if v.get("step_id")}
    missing_validations = sorted(step_ids - validation_step_ids)
    extra_validations = sorted(validation_step_ids - step_ids)
    invalid_statuses = sorted({str(v.get("status")) for v in validations if v.get("status") not in VALIDATION_STATUSES})
    validation_indices = [v.get("index") for v in validations if v.get("index") is not None]
    indices_ordered = validation_indices == sorted(validation_indices) if validation_indices else None
    reference_rows.extend(validate_references(validations, "step_id", step_ids, "validation_records.jsonl", "step_records.jsonl", id_field="step_id"))
    details["validations"] = {
        "missing_validation_step_ids": missing_validations[:100],
        "extra_validation_step_ids": extra_validations[:100],
        "invalid_statuses": invalid_statuses,
        "indices_ordered": indices_ordered,
    }
    write_json(evidence_dir / "validation_record_details.json", details["validations"])
    validation_schema_failures = [r for r in schema_rows if r["artifact"] == "validation_records.jsonl" and r["status"] == "FAIL"]
    validation_warning_failures = []
    for row in uncovered_rule_steps:
        validation = validation_by_step_for_coverage.get(str(row.get("step_id")), {})
        warnings = validation.get("warnings", []) if isinstance(validation.get("warnings"), list) else []
        if not any(item.get("warning_code") == "no_applicable_rule" for item in warnings if isinstance(item, dict)):
            validation_warning_failures.append(row)
    if not paths["validations"].exists():
        add_check("E1.4", "FAIL", "critical", "validation_records.jsonl", "Validation artifact is missing.", "missing_data_report.md")
    elif missing_validations or invalid_statuses or validation_schema_failures or validation_warning_failures:
        add_check("E1.4", "FAIL", "critical", "validation_records.jsonl", f"{len(missing_validations)} steps lack validation records; invalid statuses: {invalid_statuses}; {len(validation_warning_failures)} uncovered steps lack validation warnings.", "evidence/validation_record_details.json")
    elif indices_ordered is False:
        add_check("E1.4", "FAIL", "critical", "validation_records.jsonl", "Validation indices are present but not ordered.", "evidence/validation_record_details.json")
    elif extra_validations:
        add_check("E1.4", "WARNING", "warning", "validation_records.jsonl", f"All steps have validations; {len(extra_validations)} extra validation records found.", "evidence/validation_record_details.json")
    else:
        add_check("E1.4", "PASS", "critical", "validation_records.jsonl", f"{len(validations)} validation records include statuses.", "evidence/validation_record_details.json")

    trace_by_step = {str(t.get("step_id") or t.get("trace_id")): t for t in trace_list}
    validation_trace_missing: list[str] = []
    trace_rows: list[dict[str, str]] = []
    trace_fields = [
        "predicate_evidence",
        "constraint_evidence",
        "missing_requirements",
        "incompatibility_evidence",
        "dependency_evidence",
        "status",
        "confidence",
    ]
    for validation in validations:
        step_id = str(validation.get("step_id"))
        trace = validation.get("trace") if isinstance(validation.get("trace"), dict) else trace_by_step.get(step_id)
        if not trace:
            validation_trace_missing.append(step_id)
            continue
        for field_name in trace_fields:
            status = "PASS" if field_name in trace else "SKIPPED"
            trace_rows.append(
                {
                    "artifact": "explanation_traces.json",
                    "record_id": step_id,
                    "validation_type": "trace_field",
                    "field": field_name,
                    "status": status,
                    "message": "present" if status == "PASS" else "not produced by current trace contract",
                }
            )
    schema_rows.extend(trace_rows)
    details["traces"] = {
        "trace_records": len(trace_list),
        "validation_decisions_without_trace": validation_trace_missing[:100],
        "skipped_optional_fields": sorted({r["field"] for r in trace_rows if r["status"] == "SKIPPED"}),
    }
    write_json(evidence_dir / "trace_validation_details.json", details["traces"])
    if not paths["traces"].exists():
        add_check("E1.5", "FAIL", "critical", "explanation_traces.json", "Explanation trace artifact is missing.", "missing_data_report.md")
    elif validation_trace_missing:
        add_check("E1.5", "FAIL", "critical", "explanation_traces.json", f"{len(validation_trace_missing)} validation decisions lack trace information.", "evidence/trace_validation_details.json")
    elif any(r["status"] == "SKIPPED" for r in trace_rows):
        add_check("E1.5", "WARNING", "warning", "explanation_traces.json", "Trace information exists; some optional trace fields are not produced by the current contract.", "evidence/trace_validation_details.json")
    else:
        add_check("E1.5", "PASS", "critical", "explanation_traces.json", "Validation decisions include trace information.", "evidence/trace_validation_details.json")

    graph_required = {"schema_version", "graph_name", "nodes", "edges"}
    for field_name in graph_required:
        schema_rows.append(
            {
                "artifact": "procedural_reasoning_graph.json",
                "record_id": "root",
                "validation_type": "required_field",
                "field": field_name,
                "status": "PASS" if isinstance(graph, dict) and field_name in graph else "FAIL",
                "message": "present" if isinstance(graph, dict) and field_name in graph else "missing graph root field",
            }
        )
    node_ids = {row_id(row, ["id", ":ID"]) for row in graph_nodes}
    node_ids.discard("")
    step_nodes = [row for row in graph_nodes if row.get("type") == "Step"]
    graph_edge_failures = []
    for edge in graph_edges:
        source = row_id(edge, ["source", ":START_ID"])
        target = row_id(edge, ["target", ":END_ID"])
        status = "PASS" if source in node_ids and target in node_ids else "FAIL"
        if status == "FAIL":
            graph_edge_failures.append(edge)
        reference_rows.append(
            {
                "source_artifact": "procedural_reasoning_graph_edges.csv",
                "source_id": f"{source}->{target}",
                "reference_field": "source,target",
                "target_artifact": "procedural_reasoning_graph_nodes.csv",
                "target_id": f"{source},{target}",
                "status": status,
                "message": "edge endpoints resolved" if status == "PASS" else "dangling graph edge",
            }
        )
    graph_schema_failures = [r for r in schema_rows if r["artifact"] == "procedural_reasoning_graph.json" and r["status"] == "FAIL"]
    graph_warning_failures = []
    graph_step_props_by_step = {}
    for node in step_nodes:
        props = parse_properties(node.get("properties"))
        graph_step_props_by_step[str(props.get("step_id") or unprefix_step_node_id(row_id(node, ["id", ":ID"])))] = props
    for row in uncovered_rule_steps:
        props = graph_step_props_by_step.get(str(row.get("step_id")), {})
        if not props or props.get("warning_count", 0) == 0 or props.get("has_rule_coverage") is not False:
            graph_warning_failures.append(row)
    details["graph"] = {"step_nodes": len(step_nodes), "dangling_edges": len(graph_edge_failures), "uncovered_steps_missing_graph_warnings": len(graph_warning_failures)}
    write_json(evidence_dir / "graph_export_details.json", details["graph"])
    graph_files_exist = all(paths[key].exists() for key in ["graph_json", "graph_nodes", "graph_edges"])
    if not graph_files_exist:
        add_check("E1.6", "FAIL", "critical", "procedural_reasoning_graph.*", "One or more graph export files are missing.", "missing_data_report.md")
    elif graph_schema_failures or graph_edge_failures or graph_warning_failures:
        add_check("E1.6", "FAIL", "critical", "procedural_reasoning_graph.*", f"{len(graph_schema_failures)} graph schema failures, {len(graph_edge_failures)} dangling edges, and {len(graph_warning_failures)} uncovered steps missing graph warnings.", "evidence/graph_export_details.json")
    elif len(step_nodes) < len(validations):
        add_check("E1.6", "FAIL", "critical", "procedural_reasoning_graph_nodes.csv", f"Graph has {len(step_nodes)} Step nodes for {len(validations)} validation records.", "evidence/graph_export_details.json")
    else:
        add_check("E1.6", "PASS", "critical", "procedural_reasoning_graph.*", f"Graph export contains {len(graph_nodes)} nodes and {len(graph_edges)} edges.", "evidence/graph_export_details.json")

    validation_index_by_step = {str(v.get("step_id")): v.get("index") for v in validations if v.get("step_id") is not None}
    node_index_by_id: dict[str, Any] = {}
    for node in graph_nodes:
        if node.get("type") != "Step":
            continue
        props = parse_properties(node.get("properties"))
        step_id = str(props.get("step_id") or unprefix_step_node_id(row_id(node, ["id", ":ID"])))
        index_value = props.get("validation_index", props.get("index", validation_index_by_step.get(step_id)))
        node_index_by_id[row_id(node, ["id", ":ID"])] = index_value
    next_edges = [edge for edge in graph_edges if (edge.get("type") or edge.get(":TYPE")) == "NEXT"]
    order_failures = []
    order_skipped = []
    for edge in next_edges:
        source = row_id(edge, ["source", ":START_ID"])
        target = row_id(edge, ["target", ":END_ID"])
        source_index = node_index_by_id.get(source)
        target_index = node_index_by_id.get(target)
        if source_index is None or target_index is None:
            status = "SKIPPED"
            message = "missing validation index/order information"
            order_skipped.append(edge)
        else:
            try:
                ok = int(target_index) == int(source_index) + 1
            except (TypeError, ValueError):
                ok = False
            status = "PASS" if ok else "FAIL"
            message = "NEXT follows adjacent validation index" if ok else "NEXT edge is out of order"
            if not ok:
                order_failures.append(edge)
        order_rows.append(
            {
                "edge_source": source,
                "edge_target": target,
                "edge_type": "NEXT",
                "source_index": source_index,
                "target_index": target_index,
                "status": status,
                "message": message,
            }
        )
    if not next_edges:
        add_check("E1.7", "SKIPPED", "skipped", "procedural_reasoning_graph_edges.csv", "No NEXT edges were present to evaluate.", "order_consistency_results.csv")
    elif order_failures:
        add_check("E1.7", "FAIL", "critical", "procedural_reasoning_graph_edges.csv", f"{len(order_failures)} NEXT edges violate validation order.", "order_consistency_results.csv")
    elif order_skipped:
        add_check("E1.7", "SKIPPED", "skipped", "procedural_reasoning_graph_nodes.csv", "Graph does not expose enough index information for all NEXT edges.", "order_consistency_results.csv")
    else:
        add_check("E1.7", "PASS", "critical", "procedural_reasoning_graph_edges.csv", f"{len(next_edges)} NEXT edges follow validation order.", "order_consistency_results.csv")

    status_by_step = {str(v.get("step_id")): str(v.get("status")) for v in validations if v.get("step_id")}
    depends_edges = [edge for edge in graph_edges if (edge.get("type") or edge.get(":TYPE")) == "DEPENDS_ON"]
    dependency_failures = []
    provisional_dependencies = []
    for edge in depends_edges:
        source_node = row_id(edge, ["source", ":START_ID"])
        target_node = row_id(edge, ["target", ":END_ID"])
        dependent_step = unprefix_step_node_id(source_node)
        support_step = unprefix_step_node_id(target_node)
        support_status = status_by_step.get(support_step)
        props = parse_properties(edge.get("properties"))
        provisional = bool(props.get("provisional"))
        if support_status == "rejected":
            status = "FAIL"
            message = "later step depends on rejected earlier step"
            dependency_failures.append(edge)
        elif support_status == "uncertain" or provisional:
            status = "WARNING"
            message = "dependency is provisional or supported by an uncertain step"
            provisional_dependencies.append(edge)
        else:
            status = "PASS"
            message = "dependency support is not rejected"
        dependency_rows.append(
            {
                "dependent_step": dependent_step,
                "support_step": support_step,
                "support_status": support_status or "",
                "edge_type": "DEPENDS_ON",
                "provisional": str(provisional).lower(),
                "status": status,
                "message": message,
            }
        )
    if dependency_failures:
        add_check("E1.8", "FAIL", "critical", "procedural_reasoning_graph_edges.csv", f"{len(dependency_failures)} DEPENDS_ON edges are supported by rejected steps.", "dependency_rule_results.csv")
    elif provisional_dependencies:
        add_check("E1.8", "WARNING", "warning", "procedural_reasoning_graph_edges.csv", f"{len(provisional_dependencies)} provisional/uncertain dependencies found; no rejected-step support.", "dependency_rule_results.csv")
    else:
        add_check("E1.8", "PASS", "critical", "procedural_reasoning_graph_edges.csv", f"{len(depends_edges)} DEPENDS_ON edges avoid rejected-step support.", "dependency_rule_results.csv")

    if uncovered_rule_steps:
        add_check(
            "E1.9",
            "WARNING",
            "warning",
            "rule_coverage_diagnostics.csv",
            _rule_coverage_warning_message(uncovered_rule_steps),
            "rule_coverage_diagnostics.csv",
        )

    inventory = artifact_inventory(ctx)
    write_csv(output / "artifact_inventory.csv", [row.__dict__ for row in inventory], ["artifact_name", "path", "type", "role_in_pipeline", "exists", "size_bytes", "record_count", "notes"])
    write_csv(output / "schema_validation_results.csv", schema_rows, ["artifact", "record_id", "validation_type", "field", "status", "message"])
    write_csv(output / "reference_integrity_results.csv", reference_rows, ["source_artifact", "source_id", "reference_field", "target_artifact", "target_id", "status", "message"])
    write_csv(output / "order_consistency_results.csv", order_rows, ["edge_source", "edge_target", "edge_type", "source_index", "target_index", "status", "message"])
    write_csv(output / "dependency_rule_results.csv", dependency_rows, ["dependent_step", "support_step", "support_status", "edge_type", "provisional", "status", "message"])
    write_csv(output / "evaluation1_summary.csv", [row.__dict__ for row in check_results], ["check_id", "check_name", "category", "status", "severity", "artifact", "message", "evidence_file"])

    result = {
        "evaluation": "Evaluation 1: Pipeline artifact correctness",
        "timestamp": ctx.timestamp,
        "run_id": ctx.run_id,
        "clip_result_id": ctx.clip_result_id,
        "paths": {key: str(path) for key, path in paths.items()},
        "counts": counts,
        "checks": [row.__dict__ for row in check_results],
        "details": details,
    }
    write_json(evidence_dir / "evaluation1_results.json", result)
    write_report(ctx, check_results, counts, inventory)
    write_readme(ctx)
    return result


def write_readme(ctx: EvaluationContext) -> None:
    text = f"""# Evaluation 1: Pipeline Artifact Correctness

This folder contains reproducible evidence for thesis Evaluation 1. The purpose is to verify that the reasoning-layer artifact chain is inspectable stage by stage: adapter outputs, Layer 3 constraints, Layer 4 validation records, explanation traces, and the procedural reasoning graph.

This evaluation is artifact-based and reasoning-focused. It does not evaluate low-level perception, object detection, step segmentation, or CAD-to-image alignment.

## How To Run

```powershell
.venv\\Scripts\\python.exe scripts\\20_evaluate_pipeline_artifact_correctness.py --project-root . --run-id {ctx.run_id} --clip-result-id {ctx.clip_result_id} --output-dir docs\\reasoning_layers\\Evaluation1 --strict
```

Use `--restore-preserved` to restore preserved upstream `/tmp` outputs from `results/preserved_tmp/{ctx.run_id}.tar.gz` when available. Downloads are never attempted unless `--download-missing` is passed.

## Required Inputs

- `nodes_events.csv` from the upstream Neo4j-style CSV export.
- `step_records.jsonl` and `predicates.jsonl` from the reasoning adapter.
- `inferred_constraints.csv` from Layer 3.
- `rule_coverage_diagnostics.csv` from Layer 3 rule coverage diagnostics.
- `validation_records.jsonl`, `step_validations.csv`, and `explanation_traces.json` from Layer 4.
- `procedural_reasoning_graph.json`, `procedural_reasoning_graph_nodes.csv`, and `procedural_reasoning_graph_edges.csv` from the graph builder.

When the procedural graph is rebuilt, pass `--step-records` to `scripts\\17_build_procedural_reasoning_graph.py` so Step nodes include source metadata such as `clip_result_id`, `run_id`, `mode`, `archive_name`, and `clip`.

## Generated Outputs

- `evaluation1_report.md`
- `evaluation1_summary.csv`
- `artifact_inventory.csv`
- `schema_validation_results.csv`
- `reference_integrity_results.csv`
- `order_consistency_results.csv`
- `dependency_rule_results.csv`
- `rule_coverage_diagnostics.csv`
- `evidence/evaluation1_results.json`
- `missing_data_report.md` only when required data is missing.

## Status Semantics

- `PASS`: the check satisfied its expected condition.
- `FAIL`: a critical artifact or consistency condition failed.
- `WARNING`: evidence is usable, but an important caveat was found.
- `SKIPPED`: the current artifact contract does not expose enough information to evaluate that check, or no applicable rows exist.
"""
    (ctx.output_dir / "README.md").write_text(text, encoding="utf-8")


def _rule_coverage_warning_message(rows: list[dict[str, str]]) -> str:
    action_counts: dict[str, int] = {}
    for row in rows:
        action = str(row.get("action_name") or "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1
    if len(action_counts) == 1:
        action, count = next(iter(action_counts.items()))
        noun = _unsupported_action_noun(action)
        prefix = "One" if count == 1 else str(count)
        verb = "was" if count == 1 else "were"
        return f"{prefix} unsupported {noun} {verb} detected and reported with a rule-coverage warning."
    total = sum(action_counts.values())
    return f"{total} unsupported actions were detected and reported with rule-coverage warnings: {dict(sorted(action_counts.items()))}."


def _unsupported_action_noun(action: str) -> str:
    if action == "remove":
        return "removal action"
    if action:
        return f"{action} action"
    return "action"


def write_report(
    ctx: EvaluationContext,
    check_results: list[CheckResult],
    counts: dict[str, Any],
    inventory: list[InventoryRow],
) -> None:
    status_counts = {status: sum(1 for row in check_results if row.status == status) for status in STATUSES}
    lines = [
        "# Evaluation 1 Report: Pipeline Artifact Correctness",
        "",
        f"- Evaluated run ID: `{ctx.run_id}`",
        f"- Evaluated clip/result ID: `{ctx.clip_result_id}`",
        f"- Timestamp: `{ctx.timestamp}`",
        f"- Neo4j input directory: `{ctx.neo4j_dir}`",
        f"- Reasoning directory: `{ctx.reasoning_dir}`",
        f"- Graph directory: `{ctx.graph_dir}`",
        f"- Output directory: `{ctx.output_dir}`",
        "",
        "## Summary Table",
        "",
        "| Check | Status | Evidence | Message |",
        "| --- | --- | --- | --- |",
    ]
    for row in check_results:
        lines.append(f"| {row.check_name} | {row.status} | `{row.artifact}` | {row.message} |")
    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| Artifact | Count |",
            "| --- | ---: |",
        ]
    )
    for key, value in counts.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Failures And Warnings", ""])
    issues = [row for row in check_results if row.status in {"FAIL", "WARNING", "SKIPPED"}]
    if issues:
        for row in issues:
            lines.append(f"- {row.status}: {row.check_name}: {row.message}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Artifact Inventory",
            "",
            "| Artifact | Exists | Records | Role |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for row in inventory:
        lines.append(f"| `{row.artifact_name}` | {row.exists} | {row.record_count} | {row.role_in_pipeline} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Evaluation 1 checks whether the implemented reasoning pipeline produces inspectable artifacts and whether cross-artifact references remain consistent. The result is suitable for filling the thesis Evaluation 1 table because it maps directly to the eight checks listed in the chapter. It should be interpreted as evidence about reasoning-layer artifact correctness, not as evidence of perception accuracy, object detection quality, step segmentation quality, or CAD-to-image alignment.",
            "",
            f"Status totals: PASS={status_counts['PASS']}, FAIL={status_counts['FAIL']}, WARNING={status_counts['WARNING']}, SKIPPED={status_counts['SKIPPED']}.",
            "",
        ]
    )
    (ctx.output_dir / "evaluation1_report.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--clip-result-id", default="raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1")
    parser.add_argument("--neo4j-dir", type=Path, default=None)
    parser.add_argument("--reasoning-dir", type=Path, default=None)
    parser.add_argument("--graph-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/reasoning_layers/Evaluation1"))
    parser.add_argument("--restore-preserved", action="store_true")
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def load_default_run_id(project_root: Path) -> str:
    config_path = project_root / "config" / "reasoning_adapter.yaml"
    if not config_path.exists():
        return "raw_cad_dataset__all_test_clips"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "raw_cad_dataset__all_test_clips"
    return str(config.get("default_run_id") or "raw_cad_dataset__all_test_clips")


def build_context(args: argparse.Namespace) -> EvaluationContext:
    project_root = args.project_root.resolve()
    run_id = args.run_id or load_default_run_id(project_root)
    clip_result_id = args.clip_result_id
    neo4j_dir = (args.neo4j_dir or Path("results") / "neo4j" / run_id)
    reasoning_dir = args.reasoning_dir or Path("results") / "reasoning_layers" / clip_result_id
    graph_dir = args.graph_dir or Path("results") / "procedural_reasoning_graph" / clip_result_id
    output_dir = args.output_dir
    upstream_result_dir = Path("/tmp/industreal_pilot/results/raw_cad_dataset") / run_id
    preserved_tarball = project_root / "results" / "preserved_tmp" / f"{run_id}.tar.gz"
    return EvaluationContext(
        project_root=project_root,
        run_id=run_id,
        clip_result_id=clip_result_id,
        neo4j_dir=(project_root / neo4j_dir).resolve() if not neo4j_dir.is_absolute() else neo4j_dir,
        reasoning_dir=(project_root / reasoning_dir).resolve() if not reasoning_dir.is_absolute() else reasoning_dir,
        graph_dir=(project_root / graph_dir).resolve() if not graph_dir.is_absolute() else graph_dir,
        output_dir=(project_root / output_dir).resolve() if not output_dir.is_absolute() else output_dir,
        upstream_result_dir=upstream_result_dir,
        preserved_tarball=preserved_tarball,
        strict=args.strict,
        restore_preserved=args.restore_preserved,
        download_missing=args.download_missing,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ctx = build_context(args)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    if ctx.restore_preserved:
        notes.append(restore_preserved_outputs(ctx))
    if ctx.download_missing:
        notes.append(maybe_download_missing(ctx))
    missing = detect_missing_data(ctx)
    if missing:
        write_missing_data_report(ctx, missing, notes)
    result = evaluate(ctx)
    if ctx.strict:
        critical_failures = [
            row for row in result["checks"]
            if row["status"] == "FAIL" and row["severity"] == "critical"
        ]
        skipped_required = [
            row for row in result["checks"]
            if row["status"] == "SKIPPED" and "missing" in row["message"].lower()
        ]
        if critical_failures or skipped_required:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
