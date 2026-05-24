"""Evaluation 2: Layer 3 constraint inference coverage.

This evaluator measures how Layer 3 transforms symbolic predicates into
procedural constraints. It intentionally does not evaluate Layer 4 validation
behavior, rejected-step perturbations, or perception accuracy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


STATUSES = ("PASS", "FAIL", "WARNING", "SKIPPED")
REQUIRED_REASONING_FILES = ("step_records.jsonl", "predicates.jsonl", "inferred_constraints.csv")
OPTIONAL_REASONING_FILES = (
    "rule_coverage_diagnostics.csv",
    "validation_records.jsonl",
    "step_validations.csv",
    "explanation_traces.json",
)
PROVENANCE_FIELDS = ("rule_id", "rule_source", "rule_type", "source_rule", "provenance")


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
class ClipInput:
    clip_result_id: str
    path: Path
    requested: bool = False


@dataclass
class EvaluationContext:
    project_root: Path
    results_root: Path
    output_dir: Path
    clip_result_ids: list[str]
    all_available: bool = False
    strict: bool = False
    restore_preserved: bool = False
    download_missing: bool = False
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def preserved_tarball(self) -> Path:
        return self.project_root / "results" / "preserved_tmp" / "raw_cad_dataset__all_test_clips.tar.gz"

    @property
    def preserved_restore_target(self) -> Path:
        return Path("/tmp/industreal_pilot/results/raw_cad_dataset/raw_cad_dataset__all_test_clips")


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


def parse_json_value(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def parse_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def is_valid_confidence(value: Any) -> bool:
    number = parse_confidence(value)
    return number is not None and 0.0 <= number <= 1.0


def normalize_name(value: Any) -> str:
    return str(value or "").strip()


def lower_name(value: Any) -> str:
    return normalize_name(value).lower()


def is_required_file_set(path: Path) -> bool:
    return path.is_dir() and all((path / name).exists() for name in REQUIRED_REASONING_FILES)


def discover_clip_inputs(ctx: EvaluationContext) -> tuple[list[ClipInput], list[dict[str, str]], list[dict[str, str]]]:
    missing: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    inputs: list[ClipInput] = []

    if ctx.all_available and ctx.results_root.exists():
        for child in sorted(p for p in ctx.results_root.iterdir() if p.is_dir()):
            if is_required_file_set(child):
                inputs.append(ClipInput(child.name, child, requested=False))
            else:
                missing_files = [name for name in REQUIRED_REASONING_FILES if not (child / name).exists()]
                skipped.append(
                    {
                        "clip_result_id": child.name,
                        "path": str(child),
                        "reason": "missing required Evaluation 2 files",
                        "missing_files": ", ".join(missing_files),
                    }
                )

    for clip_result_id in ctx.clip_result_ids:
        path = ctx.results_root / clip_result_id
        if is_required_file_set(path):
            if not any(item.path == path for item in inputs):
                inputs.append(ClipInput(clip_result_id, path, requested=True))
            continue
        missing_files = [name for name in REQUIRED_REASONING_FILES if not (path / name).exists()]
        missing.append(
            {
                "clip_result_id": clip_result_id,
                "path": str(path),
                "reason": "requested clip/result folder is missing required artifacts",
                "missing_files": ", ".join(missing_files) if path.exists() else "folder missing",
            }
        )

    return inputs, missing, skipped


def restore_preserved_outputs(ctx: EvaluationContext) -> str:
    if ctx.preserved_restore_target.exists():
        return f"Preserved upstream result folder already exists: {ctx.preserved_restore_target}"
    if not ctx.preserved_tarball.exists():
        return f"Preserved tarball not found: {ctx.preserved_tarball}"
    ctx.preserved_restore_target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ctx.preserved_tarball, "r:gz") as archive:
        archive.extractall(ctx.preserved_restore_target.parent)
    return f"Restored {ctx.preserved_tarball} to {ctx.preserved_restore_target.parent}"


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
    completed = subprocess.run(command, cwd=ctx.project_root, text=True, capture_output=True, check=False)
    return (
        f"Command: {' '.join(command)}\n"
        f"Return code: {completed.returncode}\n"
        f"STDOUT:\n{completed.stdout[-4000:]}\nSTDERR:\n{completed.stderr[-4000:]}"
    )


def metadata_from_steps(clip_result_id: str, steps: list[dict[str, Any]]) -> dict[str, str]:
    first = steps[0] if steps else {}
    return {
        "clip_result_id": clip_result_id,
        "run_id": str(first.get("run_id") or ""),
        "mode": str(first.get("mode") or ""),
        "archive_name": str(first.get("archive_name") or ""),
        "clip": str(first.get("clip") or clip_result_id),
    }


def step_action(step: dict[str, Any]) -> str:
    action = step.get("action")
    if isinstance(action, dict):
        return lower_name(action.get("name") or action.get("event_type"))
    return lower_name(step.get("action_name") or step.get("event_type"))


def step_object_args(step: dict[str, Any]) -> list[str]:
    objects = step.get("objects")
    if not isinstance(objects, list):
        return []
    values: list[str] = []
    for obj in objects:
        if isinstance(obj, dict):
            value = obj.get("type") or obj.get("label") or obj.get("id")
            if value:
                values.append(str(value))
    return values


def predicate_evidence_by_step(predicates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for predicate in predicates:
        step_id = str(predicate.get("step_id") or "")
        if not step_id:
            continue
        item = evidence.setdefault(
            step_id,
            {
                "count": 0,
                "names": set(),
                "has_action": False,
                "has_object_or_tool": False,
            },
        )
        item["count"] += 1
        name = lower_name(predicate.get("name"))
        item["names"].add(name)
        if name in {"hasaction", "stephasaction"}:
            item["has_action"] = True
        if name in {"usesobject", "usestool", "actson"}:
            item["has_object_or_tool"] = True
    return evidence


def constraints_by_step(constraints: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    by_step: dict[str, list[dict[str, str]]] = {}
    for constraint in constraints:
        by_step.setdefault(str(constraint.get("step_id") or ""), []).append(constraint)
    return by_step


def constraint_args(row: dict[str, Any]) -> list[Any]:
    parsed = parse_json_value(row.get("args"), [])
    return parsed if isinstance(parsed, list) else []


def is_incompatibility_constraint(row: dict[str, Any]) -> bool:
    name = lower_name(row.get("name"))
    kind = lower_name(row.get("kind") or row.get("constraint_kind"))
    return name in {"incompatibleaction", "incompatibility"} or "incompat" in name or "compat" in kind


def constraint_count_summary(
    clip_result_id: str,
    constraints: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    grouped: dict[tuple[str, str], list[float]] = {}
    invalid_confidence = False
    confidence_rows: list[dict[str, Any]] = []
    for row in constraints:
        name = normalize_name(row.get("name")) or "unknown"
        kind = normalize_name(row.get("kind") or row.get("constraint_kind")) or "unknown"
        conf_value = row.get("conf", row.get("confidence"))
        conf = parse_confidence(conf_value)
        valid = is_valid_confidence(conf_value)
        invalid_confidence = invalid_confidence or not valid
        confidence_rows.append(
            {
                "clip_result_id": clip_result_id,
                "constraint_id": row.get("constraint_id") or row.get("id") or "",
                "constraint_name": name,
                "constraint_kind": kind,
                "confidence": "" if conf is None else conf,
                "is_numeric": conf is not None,
                "in_range": valid,
                "status": "PASS" if valid else "FAIL",
                "message": "confidence is numeric and in [0, 1]" if valid else f"invalid confidence {conf_value!r}",
            }
        )
        if valid and conf is not None:
            grouped.setdefault((name, kind), []).append(conf)
        else:
            grouped.setdefault((name, kind), [])
    type_rows: list[dict[str, Any]] = []
    for (name, kind), confidences in sorted(grouped.items()):
        count = sum(1 for row in constraints if normalize_name(row.get("name")) == name and normalize_name(row.get("kind") or row.get("constraint_kind")) == kind)
        type_rows.append(
            {
                "clip_result_id": clip_result_id,
                "constraint_name": name,
                "constraint_kind": kind,
                "count": count,
                "confidence_min": min(confidences) if confidences else "",
                "confidence_mean": statistics.fmean(confidences) if confidences else "",
                "confidence_max": max(confidences) if confidences else "",
            }
        )
    return type_rows, confidence_rows, invalid_confidence


def provenance_rows_for_clip(clip_result_id: str, constraints: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in constraints:
        present_fields = [field for field in PROVENANCE_FIELDS if row.get(field) not in (None, "")]
        rows.append(
            {
                "clip_result_id": clip_result_id,
                "constraint_id": row.get("constraint_id") or row.get("id") or "",
                "constraint_name": row.get("name") or "",
                "constraint_kind": row.get("kind") or row.get("constraint_kind") or "",
                "has_rule_provenance": bool(present_fields),
                "rule_id": row.get("rule_id") or "",
                "rule_source": row.get("rule_source") or row.get("source_rule") or "",
                "provenance_fields": ", ".join(present_fields),
                "status": "PASS" if present_fields else "WARNING",
                "message": "constraint links to rule provenance" if present_fields else "no rule provenance field populated",
            }
        )
    return rows


def summarize_rule_coverage(
    clip_result_id: str,
    diagnostics: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in diagnostics:
        grouped.setdefault(str(row.get("action_name") or "unknown"), []).append(row)
    rows: list[dict[str, Any]] = []
    warning_counts: dict[str, int] = {}
    matched_distribution: dict[str, int] = {}
    produced_distribution: dict[str, int] = {}
    covered = 0
    uncovered = 0
    unsupported_actions: set[str] = set()
    for row in diagnostics:
        has_rule = parse_bool(row.get("has_rule_coverage"))
        if has_rule:
            covered += 1
        elif has_rule is False:
            uncovered += 1
        warning = str(row.get("warning_code") or "")
        if warning:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
            unsupported_actions.add(str(row.get("action_name") or "unknown"))
        matched = str(parse_int(row.get("matched_rule_count")))
        produced = str(parse_int(row.get("produced_constraint_count")))
        matched_distribution[matched] = matched_distribution.get(matched, 0) + 1
        produced_distribution[produced] = produced_distribution.get(produced, 0) + 1
    for action, action_rows in sorted(grouped.items()):
        action_warnings = sorted({str(row.get("warning_code") or "") for row in action_rows if row.get("warning_code")})
        rows.append(
            {
                "clip_result_id": clip_result_id,
                "action_name": action,
                "step_count": len(action_rows),
                "covered_step_count": sum(1 for row in action_rows if parse_bool(row.get("has_rule_coverage")) is True),
                "uncovered_step_count": sum(1 for row in action_rows if parse_bool(row.get("has_rule_coverage")) is False),
                "warning_codes": ", ".join(action_warnings),
                "matched_rule_count_sum": sum(parse_int(row.get("matched_rule_count")) for row in action_rows),
                "produced_constraint_count_sum": sum(parse_int(row.get("produced_constraint_count")) for row in action_rows),
            }
        )
    summary = {
        "covered_step_count": covered,
        "uncovered_step_count": uncovered,
        "warning_counts": warning_counts,
        "unsupported_actions": sorted(unsupported_actions),
        "matched_rule_count_distribution": matched_distribution,
        "produced_constraint_count_distribution": produced_distribution,
    }
    return rows, summary


def remove_semantics_rows(
    clip_result_id: str,
    steps: list[dict[str, Any]],
    constraints: list[dict[str, str]],
    diagnostics: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_step = constraints_by_step(constraints)
    diagnostics_by_step = {str(row.get("step_id") or ""): row for row in diagnostics}
    remove_steps = [step for step in steps if step_action(step) == "remove"]
    rows: list[dict[str, Any]] = []
    contradictory = 0
    for step in remove_steps:
        step_id = str(step.get("id") or "")
        step_constraints = by_step.get(step_id, [])
        requires_installed = []
        produces_removed = []
        for row in step_constraints:
            args = constraint_args(row)
            if lower_name(row.get("name")) == "requires" and len(args) > 1 and lower_name(args[1]) == "installed":
                requires_installed.append(row)
            if lower_name(row.get("name")) == "produces" and len(args) > 1 and lower_name(args[1]) == "removed":
                produces_removed.append(row)
        diag = diagnostics_by_step.get(step_id, {})
        has_rule_coverage = parse_bool(diag.get("has_rule_coverage"))
        warning_code = str(diag.get("warning_code") or "")
        has_requires = bool(requires_installed)
        has_produces = bool(produces_removed)
        notes: list[str] = []
        if not has_requires:
            notes.append("missing remove requires(... installed ...) constraint")
        if not has_produces:
            notes.append("missing remove produces(... removed ...) constraint")
        if warning_code == "no_applicable_rule":
            notes.append("remove step reported as no_applicable_rule")
        if has_rule_coverage is False and (has_requires or has_produces):
            contradictory += 1
            notes.append("contradictory: remove constraints exist but diagnostics report no rule coverage")
        rows.append(
            {
                "clip_result_id": clip_result_id,
                "step_id": step_id,
                "step_index": step.get("index", ""),
                "action_name": "remove",
                "object_args": json.dumps(step_object_args(step)),
                "has_requires_installed": has_requires,
                "has_produces_removed": has_produces,
                "produced_constraint_count": len(step_constraints),
                "has_rule_coverage": "" if has_rule_coverage is None else has_rule_coverage,
                "warning_code": warning_code,
                "notes": "; ".join(notes),
            }
        )
    summary = {
        "remove_step_count": len(remove_steps),
        "remove_requires_installed_count": sum(1 for row in rows if row["has_requires_installed"]),
        "remove_produces_removed_count": sum(1 for row in rows if row["has_produces_removed"]),
        "remove_no_applicable_rule_count": sum(1 for row in rows if row["warning_code"] == "no_applicable_rule"),
        "remove_missing_expected_constraint_count": sum(
            1 for row in rows if not row["has_requires_installed"] or not row["has_produces_removed"]
        ),
        "contradictory_remove_rule_coverage_count": contradictory,
    }
    return rows, summary


def evaluate_clip(clip_input: ClipInput) -> dict[str, Any]:
    path = clip_input.path
    steps = load_jsonl(path / "step_records.jsonl")
    predicates = load_jsonl(path / "predicates.jsonl")
    constraints = load_csv(path / "inferred_constraints.csv")
    diagnostics = load_csv(path / "rule_coverage_diagnostics.csv") if (path / "rule_coverage_diagnostics.csv").exists() else []

    meta = metadata_from_steps(clip_input.clip_result_id, steps)
    pred_evidence = predicate_evidence_by_step(predicates)
    steps_with_predicates = sum(1 for step in steps if str(step.get("id") or "") in pred_evidence)
    steps_with_meaningful = sum(
        1
        for step in steps
        if (item := pred_evidence.get(str(step.get("id") or ""))) and item["has_action"] and item["has_object_or_tool"]
    )

    names = [lower_name(row.get("name")) for row in constraints]
    requires_count = names.count("requires")
    produces_count = names.count("produces")
    requires_tool_count = names.count("requirestool")
    requires_safety_count = names.count("requiressafety")
    incompatibility_count = sum(1 for row in constraints if is_incompatibility_constraint(row))

    type_rows, confidence_rows, invalid_confidence = constraint_count_summary(clip_input.clip_result_id, constraints)
    provenance_rows = provenance_rows_for_clip(clip_input.clip_result_id, constraints)
    rule_rows, rule_summary = summarize_rule_coverage(clip_input.clip_result_id, diagnostics)
    remove_rows, remove_summary = remove_semantics_rows(clip_input.clip_result_id, steps, constraints, diagnostics)

    warning_count = sum(rule_summary["warning_counts"].values())
    coverage_row = {
        **meta,
        "step_count": len(steps),
        "predicate_count": len(predicates),
        "steps_with_predicate_evidence": steps_with_predicates,
        "steps_with_meaningful_action_object_evidence": steps_with_meaningful,
        "requires_count": requires_count,
        "produces_count": produces_count,
        "requires_tool_count": requires_tool_count,
        "requires_safety_count": requires_safety_count,
        "incompatibility_count": incompatibility_count,
        "remove_step_count": remove_summary["remove_step_count"],
        "remove_requires_installed_count": remove_summary["remove_requires_installed_count"],
        "remove_produces_removed_count": remove_summary["remove_produces_removed_count"],
        "unsupported_action_count": len(rule_summary["unsupported_actions"]),
        "unsupported_actions": ", ".join(rule_summary["unsupported_actions"]),
        "rule_coverage_warning_count": warning_count,
        "rule_coverage_warnings": json.dumps(rule_summary["warning_counts"], sort_keys=True),
    }

    return {
        "clip_result_id": clip_input.clip_result_id,
        "input_dir": str(path),
        "metadata": meta,
        "coverage_row": coverage_row,
        "constraint_type_rows": type_rows,
        "rule_coverage_rows": rule_rows,
        "remove_rows": remove_rows,
        "provenance_rows": provenance_rows,
        "confidence_rows": confidence_rows,
        "counts": {
            "steps": len(steps),
            "predicates": len(predicates),
            "constraints": len(constraints),
            "steps_with_predicate_evidence": steps_with_predicates,
            "steps_with_meaningful_action_object_evidence": steps_with_meaningful,
            "constraint_names": {name: names.count(name) for name in sorted(set(names))},
            "rule_coverage": rule_summary,
            "remove_semantics": remove_summary,
            "constraints_with_rule_provenance": sum(1 for row in provenance_rows if row["has_rule_provenance"]),
            "invalid_confidence_count": sum(1 for row in confidence_rows if row["status"] == "FAIL"),
        },
        "issues": {
            "invalid_confidence": invalid_confidence,
            "remove_missing_expected_constraint": remove_summary["remove_missing_expected_constraint_count"] > 0,
            "contradictory_remove_rule_coverage": remove_summary["contradictory_remove_rule_coverage_count"] > 0,
            "missing_rule_coverage_diagnostics": not bool(diagnostics),
        },
    }


def make_checks(
    clip_results: list[dict[str, Any]],
    missing: list[dict[str, str]],
    skipped: list[dict[str, str]],
) -> list[CheckResult]:
    checks: list[CheckResult] = []

    def add(check_id: str, name: str, category: str, status: str, severity: str, artifact: str, message: str, evidence: str = "") -> None:
        checks.append(CheckResult(check_id, name, category, status, severity, artifact, message, evidence))

    evaluated_count = len(clip_results)
    if evaluated_count:
        add("E2.1", "Evaluation inputs readable", "inputs", "PASS", "critical", "step_records.jsonl,predicates.jsonl,inferred_constraints.csv", f"{evaluated_count} clip/result folders evaluated.", "evidence/evaluation2_results.json")
    else:
        add("E2.1", "Evaluation inputs readable", "inputs", "FAIL", "critical", "results/reasoning_layers", "No readable Evaluation 2 input folders were found.", "missing_data_report.md")

    if missing:
        add("E2.2", "Requested clips available", "inputs", "WARNING", "warning", "results/reasoning_layers", f"{len(missing)} requested clip/result folders are missing required artifacts.", "missing_data_report.md")
    else:
        add("E2.2", "Requested clips available", "inputs", "PASS", "warning", "results/reasoning_layers", "No requested clip/result folders were missing.", "")

    if skipped:
        add("E2.3", "All-available discovery", "inputs", "WARNING", "warning", "results/reasoning_layers", f"{len(skipped)} local folders were skipped because required files were incomplete.", "missing_data_report.md")
    else:
        add("E2.3", "All-available discovery", "inputs", "PASS" if evaluated_count else "SKIPPED", "warning", "results/reasoning_layers", "No incomplete local folders were skipped.", "")

    total_constraints = sum(result["counts"]["constraints"] for result in clip_results)
    add("E2.4", "Constraint inference coverage computed", "layer3", "PASS" if clip_results else "SKIPPED", "critical", "inferred_constraints.csv", f"{total_constraints} inferred constraints summarized across {evaluated_count} clip(s).", "evaluation2_constraint_coverage.csv")

    invalid_conf = sum(result["counts"]["invalid_confidence_count"] for result in clip_results)
    add("E2.5", "Confidence values valid", "layer3", "FAIL" if invalid_conf else ("PASS" if clip_results else "SKIPPED"), "critical", "inferred_constraints.csv", f"{invalid_conf} constraints have missing, non-numeric, or out-of-range confidence values.", "confidence_validation_results.csv")

    provenance_total = sum(result["counts"]["constraints_with_rule_provenance"] for result in clip_results)
    add("E2.6", "Rule provenance present", "layer3", "PASS" if provenance_total == total_constraints and total_constraints else ("WARNING" if total_constraints else "SKIPPED"), "warning", "inferred_constraints.csv", f"{provenance_total} of {total_constraints} constraints expose rule provenance fields.", "constraint_provenance_results.csv")

    missing_diag = [result["clip_result_id"] for result in clip_results if result["issues"]["missing_rule_coverage_diagnostics"]]
    add("E2.7", "Rule coverage diagnostics summarized", "diagnostics", "WARNING" if missing_diag else ("PASS" if clip_results else "SKIPPED"), "warning", "rule_coverage_diagnostics.csv", f"{len(missing_diag)} clip(s) lack rule_coverage_diagnostics.csv." if missing_diag else "Rule coverage diagnostics were summarized where available.", "rule_coverage_summary.csv")

    remove_missing = sum(result["counts"]["remove_semantics"]["remove_missing_expected_constraint_count"] for result in clip_results)
    contradictory = sum(result["counts"]["remove_semantics"]["contradictory_remove_rule_coverage_count"] for result in clip_results)
    if contradictory:
        status = "FAIL"
        message = f"{contradictory} remove step(s) have contradictory remove-rule coverage diagnostics."
    elif remove_missing:
        status = "WARNING"
        message = f"{remove_missing} remove step(s) do not expose both requires(... installed ...) and produces(... removed ...)."
    else:
        status = "PASS" if clip_results else "SKIPPED"
        message = "Remove semantics were detected for all observed remove steps, or no remove steps were present."
    add("E2.8", "Remove-action semantics covered", "layer3", status, "critical" if contradictory else "warning", "inferred_constraints.csv,rule_coverage_diagnostics.csv", message, "remove_semantics_coverage.csv")

    incompat_total = sum(result["coverage_row"]["incompatibility_count"] for result in clip_results)
    add("E2.9", "Natural incompatibility coverage reported", "layer3", "PASS" if clip_results else "SKIPPED", "warning", "inferred_constraints.csv", f"{incompat_total} incompatibility constraints observed. Zero is acceptable for Evaluation 2 natural clips.", "evaluation2_constraint_coverage.csv")

    return checks


def write_missing_data_report(
    ctx: EvaluationContext,
    missing: list[dict[str, str]],
    skipped: list[dict[str, str]],
    notes: list[str],
) -> None:
    if not missing and not skipped:
        return
    preserved_status = "available" if ctx.preserved_tarball.exists() else "not found"
    lines = [
        "# Evaluation 2 Missing Data Report",
        "",
        "Evaluation 2 did not find every requested or discoverable local reasoning output.",
        "",
        f"- Results root: `{ctx.results_root}`",
        f"- Preserved tarball: `{ctx.preserved_tarball}` ({preserved_status})",
        "- Automatic restore/download is disabled unless `--restore-preserved` or `--download-missing` is passed.",
        "",
    ]
    if missing:
        lines.extend(["## Missing Requested Clips", ""])
        for item in missing:
            lines.append(f"- `{item['clip_result_id']}` at `{item['path']}`: {item['missing_files']}")
        lines.append("")
    if skipped:
        lines.extend(["## Skipped Local Folders", ""])
        for item in skipped:
            lines.append(f"- `{item['clip_result_id']}` at `{item['path']}`: {item['missing_files']}")
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "Missing clips are reported as a data-availability limitation, not as evidence that Layer 3 failed.",
            "If preserved upstream outputs are needed, restore them explicitly with `--restore-preserved`.",
            "If source archives are missing, use `--download-missing`; this delegates to the existing dataset batch runner.",
            "",
        ]
    )
    if notes:
        lines.extend(["## Restore/Download Log", ""])
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "missing_data_report.md").write_text("\n".join(lines), encoding="utf-8")


def write_readme(ctx: EvaluationContext) -> None:
    text = """# Evaluation 2: Constraint Inference Coverage

This folder contains reproducible evidence for thesis Evaluation 2. The purpose is to measure how Layer 3 enriches symbolic predicates into procedural constraints across selected reasoning outputs.

Evaluation 2 is about Layer 3 constraint inference coverage. It does not evaluate perception accuracy, step segmentation quality, CAD-to-image alignment, or Layer 4 validation behavior. Natural clips are allowed to have zero observed incompatibility constraints or zero rejected cases; those are reported as zero coverage rather than treated as failures.

## How To Run

```powershell
.venv\\Scripts\\python.exe scripts\\21_evaluate_constraint_inference_coverage.py --project-root . --results-root results\\reasoning_layers --output-dir docs\\reasoning_layers\\Evaluation2 --all-available --strict
```

For a specific clip/result folder:

```powershell
.venv\\Scripts\\python.exe scripts\\21_evaluate_constraint_inference_coverage.py --project-root . --results-root results\\reasoning_layers --output-dir docs\\reasoning_layers\\Evaluation2 --clip-result-id raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1 --strict
```

Use `--restore-preserved` to restore preserved upstream outputs from `results\\preserved_tmp\\raw_cad_dataset__all_test_clips.tar.gz` when available. Use `--download-missing` only when the existing IndustReal dataset runner should be allowed to fetch missing source archives.

## Required Inputs

Each evaluated reasoning output folder must contain:

- `step_records.jsonl`
- `predicates.jsonl`
- `inferred_constraints.csv`

Optional but recommended:

- `rule_coverage_diagnostics.csv`
- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`

## Generated Outputs

- `evaluation2_report.md`
- `evaluation2_constraint_coverage.csv`
- `constraint_type_counts.csv`
- `rule_coverage_summary.csv`
- `remove_semantics_coverage.csv`
- `constraint_provenance_results.csv`
- `confidence_validation_results.csv`
- `evaluation2_summary.csv`
- `evidence/evaluation2_results.json`
- `missing_data_report.md` only when requested clips are missing or incomplete folders are skipped.

## Status Semantics

- `PASS`: the check satisfied its expected condition.
- `FAIL`: a critical implementation or artifact problem was found.
- `WARNING`: evidence is usable, but an important limitation or coverage gap was observed.
- `SKIPPED`: no applicable local evidence was available for that check.
"""
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "README.md").write_text(text, encoding="utf-8")


def write_report(
    ctx: EvaluationContext,
    clip_results: list[dict[str, Any]],
    checks: list[CheckResult],
    missing: list[dict[str, str]],
    skipped: list[dict[str, str]],
) -> None:
    status_counts = {status: sum(1 for row in checks if row.status == status) for status in STATUSES}
    aggregate_names: dict[str, int] = {}
    aggregate_remove = {
        "remove_step_count": 0,
        "remove_requires_installed_count": 0,
        "remove_produces_removed_count": 0,
        "remove_no_applicable_rule_count": 0,
    }
    for result in clip_results:
        for name, count in result["counts"]["constraint_names"].items():
            aggregate_names[name] = aggregate_names.get(name, 0) + count
        for key in aggregate_remove:
            aggregate_remove[key] += result["counts"]["remove_semantics"].get(key, 0)

    lines = [
        "# Evaluation 2 Report: Constraint Inference Coverage",
        "",
        f"- Timestamp: `{ctx.timestamp}`",
        f"- Results root: `{ctx.results_root}`",
        f"- Output directory: `{ctx.output_dir}`",
        f"- Evaluated clips: {len(clip_results)}",
        "",
        "## Evaluated Inputs",
        "",
    ]
    if clip_results:
        for result in clip_results:
            lines.append(f"- `{result['clip_result_id']}` from `{result['input_dir']}`")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Per-Clip Coverage",
            "",
            "| Clip | Predicates | Requires | Produces | Tools | Safety | Incompat. | Remove requires | Remove produces | Warnings |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in clip_results:
        row = result["coverage_row"]
        lines.append(
            f"| {row['clip']} | {row['predicate_count']} | {row['requires_count']} | {row['produces_count']} | "
            f"{row['requires_tool_count']} | {row['requires_safety_count']} | {row['incompatibility_count']} | "
            f"{row['remove_requires_installed_count']} | {row['remove_produces_removed_count']} | {row['rule_coverage_warning_count']} |"
        )
    lines.extend(
        [
            "",
            "## Aggregate Summary",
            "",
            f"- Predicate records: {sum(result['counts']['predicates'] for result in clip_results)}",
            f"- Step records: {sum(result['counts']['steps'] for result in clip_results)}",
            f"- Inferred constraints: {sum(result['counts']['constraints'] for result in clip_results)}",
            f"- Constraint type counts: {dict(sorted(aggregate_names.items()))}",
            "",
            "## Remove-Semantics Summary",
            "",
            f"- Remove steps: {aggregate_remove['remove_step_count']}",
            f"- Remove steps with `requires(... installed ...)`: {aggregate_remove['remove_requires_installed_count']}",
            f"- Remove steps with `produces(... removed ...)`: {aggregate_remove['remove_produces_removed_count']}",
            f"- Remove steps reported as `no_applicable_rule`: {aggregate_remove['remove_no_applicable_rule_count']}",
            "",
            "## Rule Coverage Summary",
            "",
        ]
    )
    for result in clip_results:
        summary = result["counts"]["rule_coverage"]
        lines.append(
            f"- `{result['clip_result_id']}`: covered={summary['covered_step_count']}, "
            f"uncovered={summary['uncovered_step_count']}, warnings={summary['warning_counts']}, "
            f"matched distribution={summary['matched_rule_count_distribution']}, "
            f"produced distribution={summary['produced_constraint_count_distribution']}"
        )
    lines.extend(["", "## Confidence Summary", ""])
    invalid_conf = sum(result["counts"]["invalid_confidence_count"] for result in clip_results)
    lines.append(f"- Invalid confidence values: {invalid_conf}")
    lines.append("- Per-type min/mean/max confidence values are in `constraint_type_counts.csv`.")
    lines.extend(["", "## Missing Data Notes", ""])
    if missing or skipped:
        lines.append(f"- Missing requested folders: {len(missing)}")
        lines.append(f"- Skipped incomplete local folders: {len(skipped)}")
        lines.append("- Details are in `missing_data_report.md`.")
    else:
        lines.append("- No missing requested clips or skipped incomplete local folders were detected.")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "Evaluation 2 reports coverage observed in natural local clips. A clip that lacks incompatibility constraints, rejected steps, or other controlled failure cases is not considered a Layer 3 failure. Controlled perturbations belong to Evaluation 3.",
            "",
            "## Check Summary",
            "",
            "| Check | Status | Message |",
            "| --- | --- | --- |",
        ]
    )
    for check in checks:
        lines.append(f"| {check.check_name} | {check.status} | {check.message} |")
    lines.append("")
    lines.append(
        f"Status totals: PASS={status_counts['PASS']}, FAIL={status_counts['FAIL']}, WARNING={status_counts['WARNING']}, SKIPPED={status_counts['SKIPPED']}."
    )
    lines.append("")
    (ctx.output_dir / "evaluation2_report.md").write_text("\n".join(lines), encoding="utf-8")


def evaluate(ctx: EvaluationContext, notes: list[str] | None = None) -> dict[str, Any]:
    notes = notes or []
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = ctx.output_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    inputs, missing, skipped = discover_clip_inputs(ctx)
    clip_results: list[dict[str, Any]] = []
    load_errors: list[dict[str, str]] = []
    for clip_input in inputs:
        try:
            clip_results.append(evaluate_clip(clip_input))
        except Exception as exc:
            load_errors.append(
                {
                    "clip_result_id": clip_input.clip_result_id,
                    "path": str(clip_input.path),
                    "reason": f"unreadable or malformed required artifact: {exc}",
                    "missing_files": "",
                }
            )

    missing_for_report = missing + load_errors
    write_missing_data_report(ctx, missing_for_report, skipped, notes)

    checks = make_checks(clip_results, missing_for_report, skipped)
    for error in load_errors:
        checks.append(
            CheckResult(
                "E2.10",
                "Malformed artifacts rejected",
                "inputs",
                "FAIL",
                "critical",
                error["path"],
                error["reason"],
                "missing_data_report.md",
            )
        )

    coverage_rows = [result["coverage_row"] for result in clip_results]
    type_rows = [row for result in clip_results for row in result["constraint_type_rows"]]
    rule_rows = [row for result in clip_results for row in result["rule_coverage_rows"]]
    remove_rows = [row for result in clip_results for row in result["remove_rows"]]
    provenance_rows = [row for result in clip_results for row in result["provenance_rows"]]
    confidence_rows = [row for result in clip_results for row in result["confidence_rows"]]

    write_csv(
        ctx.output_dir / "evaluation2_constraint_coverage.csv",
        coverage_rows,
        [
            "clip_result_id",
            "run_id",
            "mode",
            "archive_name",
            "clip",
            "step_count",
            "predicate_count",
            "steps_with_predicate_evidence",
            "steps_with_meaningful_action_object_evidence",
            "requires_count",
            "produces_count",
            "requires_tool_count",
            "requires_safety_count",
            "incompatibility_count",
            "remove_step_count",
            "remove_requires_installed_count",
            "remove_produces_removed_count",
            "unsupported_action_count",
            "unsupported_actions",
            "rule_coverage_warning_count",
            "rule_coverage_warnings",
        ],
    )
    write_csv(
        ctx.output_dir / "constraint_type_counts.csv",
        type_rows,
        ["clip_result_id", "constraint_name", "constraint_kind", "count", "confidence_min", "confidence_mean", "confidence_max"],
    )
    write_csv(
        ctx.output_dir / "rule_coverage_summary.csv",
        rule_rows,
        [
            "clip_result_id",
            "action_name",
            "step_count",
            "covered_step_count",
            "uncovered_step_count",
            "warning_codes",
            "matched_rule_count_sum",
            "produced_constraint_count_sum",
        ],
    )
    write_csv(
        ctx.output_dir / "remove_semantics_coverage.csv",
        remove_rows,
        [
            "clip_result_id",
            "step_id",
            "step_index",
            "action_name",
            "object_args",
            "has_requires_installed",
            "has_produces_removed",
            "produced_constraint_count",
            "has_rule_coverage",
            "warning_code",
            "notes",
        ],
    )
    write_csv(
        ctx.output_dir / "constraint_provenance_results.csv",
        provenance_rows,
        [
            "clip_result_id",
            "constraint_id",
            "constraint_name",
            "constraint_kind",
            "has_rule_provenance",
            "rule_id",
            "rule_source",
            "provenance_fields",
            "status",
            "message",
        ],
    )
    write_csv(
        ctx.output_dir / "confidence_validation_results.csv",
        confidence_rows,
        [
            "clip_result_id",
            "constraint_id",
            "constraint_name",
            "constraint_kind",
            "confidence",
            "is_numeric",
            "in_range",
            "status",
            "message",
        ],
    )
    write_csv(
        ctx.output_dir / "evaluation2_summary.csv",
        [row.__dict__ for row in checks],
        ["check_id", "check_name", "category", "status", "severity", "artifact", "message", "evidence_file"],
    )

    result = {
        "evaluation": "Evaluation 2: Constraint inference coverage",
        "timestamp": ctx.timestamp,
        "results_root": str(ctx.results_root),
        "output_dir": str(ctx.output_dir),
        "evaluated_clip_count": len(clip_results),
        "evaluated_clips": [result["clip_result_id"] for result in clip_results],
        "missing": missing_for_report,
        "skipped": skipped,
        "restore_download_notes": notes,
        "checks": [row.__dict__ for row in checks],
        "clips": clip_results,
    }
    write_json(evidence_dir / "evaluation2_results.json", result)
    write_readme(ctx)
    write_report(ctx, clip_results, checks, missing_for_report, skipped)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--results-root", type=Path, default=Path("results/reasoning_layers"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/reasoning_layers/Evaluation2"))
    parser.add_argument("--clip-result-id", action="append", default=[])
    parser.add_argument("--all-available", action="store_true")
    parser.add_argument("--restore-preserved", action="store_true")
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def build_context(args: argparse.Namespace) -> EvaluationContext:
    project_root = args.project_root.resolve()
    results_root = args.results_root
    output_dir = args.output_dir
    return EvaluationContext(
        project_root=project_root,
        results_root=(project_root / results_root).resolve() if not results_root.is_absolute() else results_root,
        output_dir=(project_root / output_dir).resolve() if not output_dir.is_absolute() else output_dir,
        clip_result_ids=list(args.clip_result_id),
        all_available=args.all_available,
        strict=args.strict,
        restore_preserved=args.restore_preserved,
        download_missing=args.download_missing,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ctx = build_context(args)
    notes: list[str] = []
    if ctx.restore_preserved:
        notes.append(restore_preserved_outputs(ctx))
    if ctx.download_missing:
        notes.append(maybe_download_missing(ctx))
    if not ctx.all_available and not ctx.clip_result_ids:
        ctx.clip_result_ids = ["raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1"]
    result = evaluate(ctx, notes)
    if ctx.strict:
        critical_failures = [
            row for row in result["checks"]
            if row["status"] == "FAIL" and row["severity"] == "critical"
        ]
        if critical_failures:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
