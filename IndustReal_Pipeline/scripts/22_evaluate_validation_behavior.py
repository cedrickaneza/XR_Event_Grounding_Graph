"""Evaluation 3: Layer 4 validation and effect-lifecycle behavior.

This evaluator is intentionally targeted. It evaluates the selected
IndustReal clip used by the thesis Evaluation 3 section and checks Layer 4
validation behavior, not Layer 3 coverage or perception accuracy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.layer4_validation import Layer4Inputs, run_layer4_validation  # noqa: E402


STATUSES = ("PASS", "FAIL", "WARNING", "SKIPPED")
DEFAULT_CLIP_RESULT_ID = "raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1"
REQUIRED_FILES = (
    "step_records.jsonl",
    "predicates.jsonl",
    "inferred_constraints.csv",
    "rule_coverage_diagnostics.csv",
    "validation_records.jsonl",
    "step_validations.csv",
    "explanation_traces.json",
    "effect_history_diagnostics.csv",
)
GRAPH_FILES = (
    "procedural_reasoning_graph.json",
    "procedural_reasoning_graph_nodes.csv",
    "procedural_reasoning_graph_edges.csv",
)


@dataclass
class CheckResult:
    check_id: str
    check_name: str
    scenario: str
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
    graph_dir: Path | None
    output_dir: Path
    strict: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def config_path(self) -> Path:
        return self.project_root / "config" / "thesis_rules.yaml"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            rows.append(value)
    return rows


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def parse_conf(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def lower(value: Any) -> str:
    return str(value or "").strip().lower()


def constraint_args(row: dict[str, Any]) -> list[Any]:
    parsed = parse_json_value(row.get("args"), [])
    return parsed if isinstance(parsed, list) else []


def condition_from_constraint_ref(ref: dict[str, Any]) -> dict[str, Any]:
    if isinstance(ref.get("condition"), dict):
        return ref["condition"]
    args = ref.get("args") if isinstance(ref.get("args"), list) else []
    if ref.get("name") == "requiresTool":
        return {"name": "requiresTool", "args": args[1:]}
    return {"name": args[1] if len(args) > 1 else ref.get("name"), "args": args[2:]}


def condition_key(condition: dict[str, Any]) -> tuple[Any, ...]:
    return (condition.get("name"), *list(condition.get("args", []) or []))


def is_requirement_constraint(row: dict[str, Any]) -> bool:
    return row.get("name") in {"requires", "requiresSafety", "requiresTool"}


def is_incompatibility_constraint(row: dict[str, Any]) -> bool:
    return lower(row.get("name")) == "incompatibleaction" or "incompat" in lower(row.get("status") or row.get("kind"))


def step_index_map(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(row.get("step_id") or row.get("id")): row.get("index") for row in records if row.get("step_id") or row.get("id")}


def trace_by_step(traces: Any) -> dict[str, dict[str, Any]]:
    if isinstance(traces, dict):
        values = traces.values()
    elif isinstance(traces, list):
        values = traces
    else:
        values = []
    return {str(item.get("step_id") or item.get("trace_id")): item for item in values if isinstance(item, dict)}


def lifecycle_by_constraint(validations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for record in validations:
        for effect in record.get("produced_effect_lifecycle", []) or []:
            cid = effect.get("constraint_id")
            if cid:
                out[str(cid)] = effect
    return out


def lifecycle_by_step(validations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for record in validations:
        out[str(record.get("step_id"))] = list(record.get("produced_effect_lifecycle", []) or [])
    return out


def constraints_by_step(constraints: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in constraints:
        grouped.setdefault(str(row.get("step_id") or ""), []).append(row)
    return grouped


def constraint_step_index_map(
    constraints: list[dict[str, Any]],
    validations: list[dict[str, Any]],
) -> dict[str, int]:
    index_by_step = {str(row.get("step_id")): int(row.get("index") or 0) for row in validations}
    return {
        str(row.get("constraint_id")): index_by_step.get(str(row.get("step_id")), 0)
        for row in constraints
        if row.get("constraint_id")
    }


def support_step_id(item: dict[str, Any]) -> str:
    support = item.get("supporting_effect") if "supporting_effect" in item else item.get("support")
    return str((support or {}).get("step_id") or "")


def support_constraint_id(item: dict[str, Any]) -> str:
    support = item.get("supporting_effect") if "supporting_effect" in item else item.get("support")
    return str((support or {}).get("constraint_id") or "")


def load_thresholds(path: Path) -> tuple[float, float]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        config = json.loads(text)
    else:
        config = yaml.safe_load(text)
    validation = config.get("validation", {}) if isinstance(config, dict) else {}
    return float(validation["tau_acc"]), float(validation["tau_unc"])


def detect_missing_data(ctx: EvaluationContext) -> list[dict[str, str]]:
    missing = []
    if not ctx.reasoning_dir.exists():
        missing.append({"path": str(ctx.reasoning_dir), "why_needed": "selected Evaluation 3 reasoning output directory"})
        return missing
    for name in REQUIRED_FILES:
        path = ctx.reasoning_dir / name
        if not path.exists():
            missing.append({"path": str(path), "why_needed": f"required Evaluation 3 Layer 4 artifact: {name}"})
    return missing


def write_missing_data_report(ctx: EvaluationContext, missing: list[dict[str, str]]) -> None:
    lines = [
        "# Evaluation 3 Missing Data Report",
        "",
        "Evaluation 3 could not run all required Layer 4 validation-behavior checks because selected-clip artifacts are missing.",
        "",
        f"- Selected clip/result ID: `{ctx.clip_result_id}`",
        f"- Reasoning directory: `{ctx.reasoning_dir}`",
        "",
        "## Missing Paths",
        "",
    ]
    for item in missing:
        lines.append(f"- `{item['path']}`: {item['why_needed']}")
    lines.extend(
        [
            "",
            "Missing required Layer 4 artifacts are failures in strict mode. Optional graph files are not required for Evaluation 3.",
            "",
        ]
    )
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "missing_data_report.md").write_text("\n".join(lines), encoding="utf-8")


def evaluate_requirement_support(
    constraints: list[dict[str, Any]],
    validations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[CheckResult]]:
    validation_by_step = {str(row.get("step_id")): row for row in validations}
    lifecycle = lifecycle_by_constraint(validations)
    status_by_step = {str(row.get("step_id")): str(row.get("status")) for row in validations}
    constraint_index = constraint_step_index_map(constraints, validations)
    rows: list[dict[str, Any]] = []
    failures = 0
    for constraint in constraints:
        if not is_requirement_constraint(constraint):
            continue
        step_id = str(constraint.get("step_id"))
        record = validation_by_step.get(step_id, {})
        cid = str(constraint.get("constraint_id") or "")
        supported = next((item for item in record.get("supported_requirements", []) or [] if str(item.get("constraint_id")) == cid), None)
        missing = next((item for item in record.get("missing_requirements", []) or [] if str(item.get("constraint_id")) == cid), None)
        support = (supported or {}).get("support") or {}
        support_cid = str(support.get("constraint_id") or "")
        support_lifecycle = lifecycle.get(support_cid, {})
        support_is_rejected = status_by_step.get(str(support.get("step_id"))) == "rejected" or support.get("producer_status") == "rejected"
        invalidated_by = str(support_lifecycle.get("invalidated_by_constraint_id") or "")
        invalidated_before_requirement = (
            support_lifecycle.get("effect_lifecycle_status") == "invalidated"
            and invalidated_by
            and constraint_index.get(invalidated_by, 10**9) < int(record.get("index") or 0)
        )
        support_is_invalidated = support_lifecycle.get("effect_lifecycle_status") == "invalidated"
        if supported and not support:
            failures += 1
            notes = "supported requirement lacks support evidence"
        elif supported and (support_is_rejected or invalidated_before_requirement):
            failures += 1
            notes = "supported requirement uses inactive support"
        elif missing:
            notes = "missing requirement recorded"
        elif supported:
            notes = "active support recorded"
        else:
            failures += 1
            notes = "requirement is neither supported nor missing"
        args = constraint_args(constraint)
        rows.append(
            {
                "step_id": step_id,
                "step_index": record.get("index", ""),
                "requirement_name": args[1] if len(args) > 1 else constraint.get("name"),
                "requirement_args": json.dumps(args[2:], ensure_ascii=False),
                "support_status": "supported" if supported else ("missing" if missing else "unrecorded"),
                "supporting_step_id": support.get("step_id", ""),
                "supporting_constraint_id": support_cid,
                "support_is_active": bool(supported and not support_is_rejected and not invalidated_before_requirement),
                "support_is_rejected": support_is_rejected,
                "support_is_invalidated": support_is_invalidated,
                "notes": notes,
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "SKIPPED")
    message = f"{len(rows)} requirements inspected; {failures} inconsistent requirement-support records."
    return rows, [CheckResult("E3.1", "Requirement support behavior", "Requirement support", status, "critical", "validation_records.jsonl", message, "requirement_support_results.csv")]


def evaluate_incompatibility(
    constraints: list[dict[str, Any]],
    validations: list[dict[str, Any]],
    traces: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[CheckResult]]:
    validation_by_step = {str(row.get("step_id")): row for row in validations}
    rows: list[dict[str, Any]] = []
    failures = 0
    for constraint in constraints:
        if not is_incompatibility_constraint(constraint):
            continue
        step_id = str(constraint.get("step_id"))
        record = validation_by_step.get(step_id, {})
        trace = traces.get(step_id, {})
        cid = str(constraint.get("constraint_id") or "")
        trace_text = json.dumps(trace, sort_keys=True)
        rejected = record.get("status") == "rejected"
        trace_contains = cid in trace_text or "incompatibleAction" in trace_text
        exposed = bool(record.get("incompatibilities"))
        if not (rejected and exposed and trace_contains):
            failures += 1
        args = constraint_args(constraint)
        rows.append(
            {
                "step_id": step_id,
                "step_index": record.get("index", ""),
                "action_name": args[2] if len(args) > 2 else "",
                "object_args": json.dumps(args[1:2], ensure_ascii=False),
                "incompatibility_constraint_id": cid,
                "validation_status": record.get("status", ""),
                "rejected_due_to_incompatibility": rejected and exposed,
                "trace_contains_incompatibility": trace_contains,
                "notes": "hard incompatibility rejected" if rejected else "incompatibility did not reject step",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "WARNING")
    message = f"{len(rows)} incompatibility constraints inspected; {failures} were not rejected with trace evidence."
    return rows, [CheckResult("E3.2", "Hard incompatibility rejection", "Hard incompatibility", status, "critical", "inferred_constraints.csv,validation_records.jsonl", message, "incompatibility_results.csv")]


def evaluate_rejected_isolation(
    validations: list[dict[str, Any]],
    graph_edges: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[CheckResult]]:
    rejected_steps = {str(row.get("step_id")): row for row in validations if row.get("status") == "rejected"}
    used_support: dict[str, str] = {}
    for record in validations:
        for support in record.get("dependency_support", []) or []:
            sid = support_step_id(support)
            if sid in rejected_steps:
                used_support[sid] = str(record.get("step_id"))
    graph_offenders: dict[str, str] = {}
    if graph_edges:
        for edge in graph_edges:
            edge_type = edge.get("type") or edge.get(":TYPE")
            if edge_type != "DEPENDS_ON":
                continue
            target = str(edge.get("target") or edge.get(":END_ID") or "").removeprefix("Step::")
            source = str(edge.get("source") or edge.get(":START_ID") or "").removeprefix("Step::")
            if target in rejected_steps:
                graph_offenders[target] = source
    rows: list[dict[str, Any]] = []
    failures = 0
    lifecycles = lifecycle_by_step(validations)
    for step_id, record in rejected_steps.items():
        effects = lifecycles.get(step_id, [])
        inactive = [item for item in effects if item.get("effect_lifecycle_status") == "inactive_rejected"]
        offending = used_support.get(step_id) or graph_offenders.get(step_id) or ""
        ok = len(inactive) == len(effects) and not offending
        if not ok:
            failures += 1
        rows.append(
            {
                "rejected_step_id": step_id,
                "rejected_step_index": record.get("index", ""),
                "produced_effect_count": len(effects),
                "inactive_rejected_effect_count": len(inactive),
                "used_as_later_support": bool(offending),
                "offending_later_step_id": offending,
                "status": "PASS" if ok else "FAIL",
                "notes": "rejected effects isolated" if ok else "rejected effect remained active or was used as support",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "SKIPPED")
    message = f"{len(rows)} rejected steps inspected; {failures} rejected-step isolation violations."
    return rows, [CheckResult("E3.3", "Rejected-step isolation", "Rejected-step isolation", status, "critical", "validation_records.jsonl,effect_history_diagnostics.csv", message, "rejected_step_isolation_results.csv")]


def evaluate_removal_invalidation(
    constraints: list[dict[str, Any]],
    validations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[CheckResult]]:
    by_step = constraints_by_step(constraints)
    validation_by_step = {str(row.get("step_id")): row for row in validations}
    lifecycle = lifecycle_by_constraint(validations)
    later_support_uses: dict[str, list[int]] = {}
    for record in validations:
        record_index = int(record.get("index") or 0)
        for support in record.get("dependency_support", []) or []:
            cid = support_constraint_id(support)
            if cid:
                later_support_uses.setdefault(cid, []).append(record_index)
    rows: list[dict[str, Any]] = []
    failures = 0
    remove_constraints = [row for row in constraints if row.get("name") == "produces" and len(constraint_args(row)) > 1 and constraint_args(row)[1] == "removed"]
    for remove in remove_constraints:
        args = constraint_args(remove)
        step_id = str(remove.get("step_id"))
        component = args[2] if len(args) > 2 else ""
        target = args[3] if len(args) > 3 else ""
        step_constraints = by_step.get(step_id, [])
        has_requires = any(c.get("name") == "requires" and constraint_args(c)[1:] == ["installed", component, target] for c in step_constraints if len(constraint_args(c)) >= 4)
        has_produces = True
        record = validation_by_step.get(step_id, {})
        support = {}
        for item in record.get("dependency_support", []) or []:
            cond = item.get("required_condition", {})
            if condition_key(cond) == ("installed", component, target):
                support = item.get("supporting_effect", {}) or {}
                break
        prior_cid = str(support.get("constraint_id") or "")
        prior_effect = lifecycle.get(prior_cid, {})
        lifecycle_status = prior_effect.get("effect_lifecycle_status", "")
        invalidated_by = prior_effect.get("invalidated_by_constraint_id", "")
        remove_index = int(record.get("index") or 0)
        reused_later = bool(prior_cid and any(index > remove_index for index in later_support_uses.get(prior_cid, [])))
        ok = has_requires and has_produces and bool(support) and lifecycle_status == "invalidated" and invalidated_by == remove.get("constraint_id") and not reused_later
        if record.get("status") == "rejected":
            ok = has_requires and has_produces
        if not ok:
            failures += 1
        rows.append(
            {
                "remove_step_id": step_id,
                "remove_step_index": record.get("index", ""),
                "component": component,
                "target": target,
                "has_requires_installed": has_requires,
                "has_produces_removed": has_produces,
                "prior_installed_support_step_id": support.get("step_id", ""),
                "prior_installed_support_constraint_id": prior_cid,
                "installed_effect_lifecycle_status": lifecycle_status,
                "invalidated_by_constraint_id": invalidated_by,
                "invalidated_effect_used_later": reused_later,
                "status": "PASS" if ok else "FAIL",
                "notes": "removal invalidated prior installed effect" if ok else "removal invalidation evidence incomplete or inconsistent",
            }
        )
    status = "FAIL" if failures else ("PASS" if rows else "WARNING")
    message = f"{len(rows)} removal effects inspected; {failures} invalidation inconsistencies."
    return rows, [CheckResult("E3.4", "Removal invalidation", "Removal invalidation", status, "critical", "inferred_constraints.csv,validation_records.jsonl,effect_history_diagnostics.csv", message, "removal_invalidation_results.csv")]


def perturb_confidence(ctx: EvaluationContext, validations: list[dict[str, Any]], tau_acc: float, tau_unc: float) -> tuple[list[dict[str, Any]], list[CheckResult], dict[str, Any]]:
    target = next(
        (
            row for row in validations
            if row.get("status") == "accepted"
            and row.get("dependency_support")
            and not row.get("missing_requirements")
            and not row.get("incompatibilities")
        ),
        None,
    )
    if not target:
        row = {
            "step_id": "",
            "step_index": "",
            "baseline_status": "",
            "perturbed_status": "",
            "baseline_confidence": "",
            "perturbed_confidence": "",
            "tau_acc": tau_acc,
            "tau_unc": tau_unc,
            "perturbation_applied_to": "",
            "trace_updated": False,
            "status": "SKIPPED",
            "notes": "no accepted step with dependency support was available for confidence perturbation",
        }
        return [row], [CheckResult("E3.5", "Reduced-confidence perturbation", "Reduced confidence", "SKIPPED", "skipped", "perturbations/reduced_confidence", row["notes"], "reduced_confidence_results.csv")], {}

    pert_dir = ctx.output_dir / "perturbations" / "reduced_confidence"
    pert_dir.mkdir(parents=True, exist_ok=True)
    for name in ("step_records.jsonl", "predicates.jsonl", "inferred_constraints.csv", "rule_coverage_diagnostics.csv"):
        shutil.copyfile(ctx.reasoning_dir / name, pert_dir / name)
    target_step_id = str(target.get("step_id"))
    pert_conf = round((tau_acc + tau_unc) / 2.0, 4)

    rows = load_csv(pert_dir / "inferred_constraints.csv")
    fields = list(rows[0].keys()) if rows else []
    changed_constraints = []
    for row in rows:
        if str(row.get("step_id")) == target_step_id and row.get("name") != "incompatibleAction":
            row["conf"] = str(pert_conf)
            changed_constraints.append(row.get("constraint_id") or "")
    write_csv(pert_dir / "inferred_constraints.csv", rows, fields)

    run_result = run_layer4_validation(
        Layer4Inputs(
            step_records_path=pert_dir / "step_records.jsonl",
            predicates_path=pert_dir / "predicates.jsonl",
            constraints_path=pert_dir / "inferred_constraints.csv",
            rule_coverage_path=pert_dir / "rule_coverage_diagnostics.csv",
            output_path=pert_dir / "validation_records.jsonl",
            config_path=ctx.config_path,
        )
    )
    perturbed = load_jsonl(pert_dir / "validation_records.jsonl")
    pert_record = next((row for row in perturbed if str(row.get("step_id")) == target_step_id), {})
    trace = load_json(pert_dir / "explanation_traces.json")
    trace_text = json.dumps(trace)
    ok = target.get("status") == "accepted" and pert_record.get("status") == "uncertain" and tau_unc <= pert_conf < tau_acc
    rejected_badly = pert_record.get("status") == "rejected"
    status = "PASS" if ok else "FAIL"
    notes = "accepted step became uncertain after confidence reduction" if ok else "perturbation did not produce expected accepted-to-uncertain transition"
    if rejected_badly:
        notes = "perturbation unexpectedly rejected the step"
    result_row = {
        "step_id": target_step_id,
        "step_index": target.get("index", ""),
        "baseline_status": target.get("status", ""),
        "perturbed_status": pert_record.get("status", ""),
        "baseline_confidence": target.get("confidence", ""),
        "perturbed_confidence": pert_record.get("confidence", ""),
        "tau_acc": tau_acc,
        "tau_unc": tau_unc,
        "perturbation_applied_to": ";".join(changed_constraints),
        "trace_updated": str(pert_conf) in trace_text,
        "status": status,
        "notes": notes,
    }
    return [result_row], [CheckResult("E3.5", "Reduced-confidence perturbation", "Reduced confidence", status, "critical", "perturbations/reduced_confidence", notes, "reduced_confidence_results.csv")], run_result


def evaluate(ctx: EvaluationContext) -> dict[str, Any]:
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = ctx.output_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    missing = detect_missing_data(ctx)
    if missing:
        write_missing_data_report(ctx, missing)
        checks = [
            CheckResult("E3.0", "Required selected-clip artifacts present", "Inputs", "FAIL", "critical", "reasoning_dir", f"{len(missing)} required artifacts are missing.", "missing_data_report.md")
        ]
        result = {"evaluation": "Evaluation 3: Step validation and effect-lifecycle behavior", "timestamp": ctx.timestamp, "clip_result_id": ctx.clip_result_id, "missing": missing, "checks": [c.__dict__ for c in checks]}
        write_csv(ctx.output_dir / "evaluation3_summary.csv", [c.__dict__ for c in checks], ["check_id", "check_name", "scenario", "status", "severity", "artifact", "message", "evidence_file"])
        write_json(evidence_dir / "evaluation3_results.json", result)
        write_readme(ctx)
        return result

    steps = load_jsonl(ctx.reasoning_dir / "step_records.jsonl")
    constraints = load_csv(ctx.reasoning_dir / "inferred_constraints.csv")
    validations = load_jsonl(ctx.reasoning_dir / "validation_records.jsonl")
    traces = trace_by_step(load_json(ctx.reasoning_dir / "explanation_traces.json"))
    effect_history = load_csv(ctx.reasoning_dir / "effect_history_diagnostics.csv")
    graph_edges = load_csv(ctx.graph_dir / "procedural_reasoning_graph_edges.csv") if ctx.graph_dir and (ctx.graph_dir / "procedural_reasoning_graph_edges.csv").exists() else None
    tau_acc, tau_unc = load_thresholds(ctx.config_path)

    checks: list[CheckResult] = []
    req_rows, req_checks = evaluate_requirement_support(constraints, validations)
    inc_rows, inc_checks = evaluate_incompatibility(constraints, validations, traces)
    iso_rows, iso_checks = evaluate_rejected_isolation(validations, graph_edges)
    rem_rows, rem_checks = evaluate_removal_invalidation(constraints, validations)
    red_rows, red_checks, perturbation_run = perturb_confidence(ctx, validations, tau_acc, tau_unc)
    checks.extend(req_checks + inc_checks + iso_checks + rem_checks + red_checks)

    status_distribution = Counter(str(row.get("status") or "") for row in validations)
    lifecycle_distribution = Counter()
    for record in validations:
        for effect in record.get("produced_effect_lifecycle", []) or []:
            lifecycle_distribution[str(effect.get("effect_lifecycle_status") or "")] += 1

    write_csv(ctx.output_dir / "requirement_support_results.csv", req_rows, ["step_id", "step_index", "requirement_name", "requirement_args", "support_status", "supporting_step_id", "supporting_constraint_id", "support_is_active", "support_is_rejected", "support_is_invalidated", "notes"])
    write_csv(ctx.output_dir / "incompatibility_results.csv", inc_rows, ["step_id", "step_index", "action_name", "object_args", "incompatibility_constraint_id", "validation_status", "rejected_due_to_incompatibility", "trace_contains_incompatibility", "notes"])
    write_csv(ctx.output_dir / "rejected_step_isolation_results.csv", iso_rows, ["rejected_step_id", "rejected_step_index", "produced_effect_count", "inactive_rejected_effect_count", "used_as_later_support", "offending_later_step_id", "status", "notes"])
    write_csv(ctx.output_dir / "removal_invalidation_results.csv", rem_rows, ["remove_step_id", "remove_step_index", "component", "target", "has_requires_installed", "has_produces_removed", "prior_installed_support_step_id", "prior_installed_support_constraint_id", "installed_effect_lifecycle_status", "invalidated_by_constraint_id", "invalidated_effect_used_later", "status", "notes"])
    write_csv(ctx.output_dir / "reduced_confidence_results.csv", red_rows, ["step_id", "step_index", "baseline_status", "perturbed_status", "baseline_confidence", "perturbed_confidence", "tau_acc", "tau_unc", "perturbation_applied_to", "trace_updated", "status", "notes"])
    write_csv(ctx.output_dir / "status_distribution.csv", [{"status": key, "count": value} for key, value in sorted(status_distribution.items())], ["status", "count"])
    write_csv(ctx.output_dir / "effect_lifecycle_summary.csv", [{"effect_lifecycle_status": key, "count": value} for key, value in sorted(lifecycle_distribution.items())], ["effect_lifecycle_status", "count"])
    write_csv(ctx.output_dir / "evaluation3_summary.csv", [c.__dict__ for c in checks], ["check_id", "check_name", "scenario", "status", "severity", "artifact", "message", "evidence_file"])

    sampled = {
        "requirements": req_rows[:10],
        "incompatibilities": inc_rows,
        "rejected_isolation": iso_rows,
        "removal_invalidation": rem_rows,
        "reduced_confidence": red_rows,
    }
    write_json(evidence_dir / "sampled_records.json", sampled)
    write_json(evidence_dir / "loaded_artifact_counts.json", {"steps": len(steps), "constraints": len(constraints), "validations": len(validations), "effect_history_rows": len(effect_history), "graph_edges": len(graph_edges or [])})

    result = {
        "evaluation": "Evaluation 3: Step validation and effect-lifecycle behavior",
        "timestamp": ctx.timestamp,
        "clip_result_id": ctx.clip_result_id,
        "mode": "od_plus_psr_error_hints",
        "input_directories": {"reasoning_dir": str(ctx.reasoning_dir), "graph_dir": str(ctx.graph_dir) if ctx.graph_dir else ""},
        "thresholds": {"tau_acc": tau_acc, "tau_unc": tau_unc},
        "status_distribution": dict(sorted(status_distribution.items())),
        "effect_lifecycle_summary": dict(sorted(lifecycle_distribution.items())),
        "checks": [c.__dict__ for c in checks],
        "scenario_rows": {"requirement_support": req_rows, "incompatibility": inc_rows, "rejected_step_isolation": iso_rows, "removal_invalidation": rem_rows, "reduced_confidence": red_rows},
        "perturbation_run": perturbation_run,
    }
    write_json(evidence_dir / "evaluation3_results.json", result)
    write_readme(ctx)
    write_report(ctx, result)
    return result


def write_readme(ctx: EvaluationContext) -> None:
    text = f"""# Evaluation 3: Step Validation And Effect-Lifecycle Behavior

This folder contains reproducible evidence for thesis Evaluation 3. It evaluates Layer 4 validation behavior: requirement support, missing requirements, hard incompatibility rejection, rejected-step isolation, removal invalidation, and threshold-sensitive reduced-confidence behavior.

Evaluation 3 is not a Layer 3 constraint coverage evaluation and is not a perception-accuracy evaluation.

## Selected Clip

- Clip/result ID: `{ctx.clip_result_id}`
- Clip: `08_assy_0_1`
- Mode: `od_plus_psr_error_hints`

This clip is selected because Evaluation 2 showed it contains requirements, produced effects, tool requirements, safety requirements, removal effects, and incompatibility constraints needed to exercise Layer 4 behavior.

## How To Run

```powershell
.venv\\Scripts\\python.exe scripts\\22_evaluate_validation_behavior.py --project-root . --clip-result-id {ctx.clip_result_id} --reasoning-dir results\\reasoning_layers\\{ctx.clip_result_id} --graph-dir results\\procedural_reasoning_graph\\{ctx.clip_result_id} --output-dir docs\\reasoning_layers\\Evaluation3 --strict
```

## Required Inputs

- `step_records.jsonl`
- `predicates.jsonl`
- `inferred_constraints.csv`
- `rule_coverage_diagnostics.csv`
- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`
- `effect_history_diagnostics.csv`

Graph outputs are optional supplementary evidence only.

## Generated Outputs

- `evaluation3_report.md`
- `evaluation3_summary.csv`
- `requirement_support_results.csv`
- `incompatibility_results.csv`
- `rejected_step_isolation_results.csv`
- `removal_invalidation_results.csv`
- `reduced_confidence_results.csv`
- `status_distribution.csv`
- `effect_lifecycle_summary.csv`
- `evidence/evaluation3_results.json`
- `missing_data_report.md` only when required data is missing.

## Scenarios

1. Requirement support: supported `requires(...)` constraints must use active previous produced effects; unsupported requirements must be recorded as missing.
2. Hard incompatibility: `incompatibleAction(...)` must reject the affected step and appear in trace evidence.
3. Rejected-step isolation: rejected produced effects must remain historical but inactive and must not support later steps.
4. Removal invalidation: `produces(removed, component, target)` must invalidate a prior active `installed(component, target)` effect.
5. Reduced confidence: a copied perturbation lowers selected confidence values below `tau_acc` but above `tau_unc` and reruns Layer 4.
"""
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "README.md").write_text(text, encoding="utf-8")


def write_report(ctx: EvaluationContext, result: dict[str, Any]) -> None:
    checks = result["checks"]
    status_counts = {status: sum(1 for row in checks if row["status"] == status) for status in STATUSES}
    lines = [
        "# Evaluation 3 Report: Step Validation And Effect-Lifecycle Behavior",
        "",
        f"- Evaluated clip/result ID: `{ctx.clip_result_id}`",
        "- Mode: `od_plus_psr_error_hints`",
        f"- Timestamp: `{ctx.timestamp}`",
        f"- Reasoning directory: `{ctx.reasoning_dir}`",
        f"- Graph directory: `{ctx.graph_dir}`",
        "",
        "## Validation Status Distribution",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status, count in result["status_distribution"].items():
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "## Effect Lifecycle Summary", "", "| Lifecycle status | Count |", "| --- | ---: |"])
    for status, count in result["effect_lifecycle_summary"].items():
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "## Scenario Summary", "", "| Scenario | Status | Message | Evidence |", "| --- | --- | --- | --- |"])
    for check in checks:
        lines.append(f"| {check['scenario']} | {check['status']} | {check['message']} | `{check['evidence_file']}` |")
    rows = result["scenario_rows"]
    lines.extend(
        [
            "",
            "## Requirement Support Summary",
            "",
            f"- Requirements inspected: {len(rows['requirement_support'])}",
            f"- Supported: {sum(1 for row in rows['requirement_support'] if row['support_status'] == 'supported')}",
            f"- Missing: {sum(1 for row in rows['requirement_support'] if row['support_status'] == 'missing')}",
            "",
            "## Incompatibility Summary",
            "",
            f"- Incompatibility cases inspected: {len(rows['incompatibility'])}",
            f"- Rejected due to incompatibility: {sum(1 for row in rows['incompatibility'] if row['rejected_due_to_incompatibility'])}",
            "",
            "## Rejected-Step Isolation Summary",
            "",
            f"- Rejected steps inspected: {len(rows['rejected_step_isolation'])}",
            f"- Isolation violations: {sum(1 for row in rows['rejected_step_isolation'] if row['status'] == 'FAIL')}",
            "",
            "## Removal Invalidation Summary",
            "",
            f"- Removal effects inspected: {len(rows['removal_invalidation'])}",
            f"- Invalidation failures: {sum(1 for row in rows['removal_invalidation'] if row['status'] == 'FAIL')}",
            "",
            "## Reduced-Confidence Perturbation Summary",
            "",
            f"- Perturbed rows: {len(rows['reduced_confidence'])}",
            f"- Result statuses: {dict(Counter(row['status'] for row in rows['reduced_confidence']))}",
            "",
            "## Warnings, Failures, And Skipped Checks",
            "",
        ]
    )
    issues = [row for row in checks if row["status"] != "PASS"]
    if issues:
        for row in issues:
            lines.append(f"- {row['status']}: {row['check_name']}: {row['message']}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Thesis Interpretation",
            "",
            "The generated evidence is suitable for filling Table \\ref{tab:evaluation-validation-response}: it separates baseline Layer 4 behavior from the controlled reduced-confidence perturbation and records the exact CSV/JSON evidence for each scenario.",
            "",
            f"Status totals: PASS={status_counts['PASS']}, FAIL={status_counts['FAIL']}, WARNING={status_counts['WARNING']}, SKIPPED={status_counts['SKIPPED']}.",
            "",
        ]
    )
    (ctx.output_dir / "evaluation3_report.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--clip-result-id", default=DEFAULT_CLIP_RESULT_ID)
    parser.add_argument("--reasoning-dir", type=Path, default=None)
    parser.add_argument("--graph-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/reasoning_layers/Evaluation3"))
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
