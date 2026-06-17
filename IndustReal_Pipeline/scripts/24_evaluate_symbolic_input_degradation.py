"""Evaluation 5: symbolic robustness under controlled input degradation."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.layer3_inference import Layer3Inputs, run_layer3_inference  # noqa: E402
from src.layer4_validation import Layer4Inputs, run_layer4_validation  # noqa: E402


DEFAULT_CLIP_RESULT_ID = (
    "raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1"
)
STATUSES = ("PASS", "FAIL", "WARNING", "SKIPPED")
BASELINE_FILES = (
    "validation_records.jsonl",
    "step_validations.csv",
    "explanation_traces.json",
    "effect_history_diagnostics.csv",
)
SYMBOLIC_FILES = (
    "step_records.jsonl",
    "predicates.jsonl",
    "inferred_constraints.csv",
    "rule_coverage_diagnostics.csv",
)
SCENARIOS = (
    ("E5.1", "confidence_degradation", "Lower predicate confidence below threshold"),
    ("E5.2", "missing_support_predicate", "Remove a required support predicate"),
    ("E5.3", "incompatible_object_type", "Replace an object type with an incompatible type"),
    ("E5.4", "injected_error_action", "Inject an explicit error action or incompatibility"),
    ("E5.5", "removed_produced_effect", "Remove a produced effect used by later steps"),
)


@dataclass
class EvaluationContext:
    project_root: Path
    clip_result_id: str
    reasoning_dir: Path
    output_dir: Path
    strict: bool = False
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def config_path(self) -> Path:
        return self.project_root / "config" / "thesis_rules.yaml"

    @property
    def perturbation_input_dir(self) -> Path:
        return self.output_dir / "evidence" / "perturbation_inputs"

    @property
    def perturbation_output_dir(self) -> Path:
        return self.output_dir / "evidence" / "perturbation_outputs"


@dataclass
class ScenarioResult:
    check_id: str
    scenario_key: str
    perturbation_name: str
    category: str
    status: str
    severity: str
    baseline_step_id: str = ""
    affected_step_id: str = ""
    baseline_status: str = ""
    perturbed_status: str = ""
    status_transition: str = ""
    conservative_transition: bool | str = ""
    artifact: str = ""
    message: str = ""
    evidence_file: str = ""
    details: dict[str, Any] = field(default_factory=dict)


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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value in (None, ""):
        return []
    parsed = json.loads(str(value))
    return list(parsed) if isinstance(parsed, list) else []


def constraint_condition(item: dict[str, Any]) -> dict[str, Any]:
    if isinstance(item.get("condition"), dict):
        return dict(item["condition"])
    args = parse_args_value(item.get("args"))
    if item.get("name") == "requiresTool":
        return {"name": "requiresTool", "args": args[1:]}
    return {
        "name": args[1] if len(args) > 1 else item.get("name"),
        "args": args[2:],
    }


def condition_key(item: dict[str, Any]) -> str:
    return json.dumps(constraint_condition(item), sort_keys=True)


def status_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get("status") or "") for row in rows).items()))


def short_step_id(step_id: Any) -> str:
    value = str(step_id or "")
    return value.rsplit("::", 1)[-1] if value else ""


def format_condition(condition: Any) -> str:
    if not isinstance(condition, dict):
        return ""
    name = str(condition.get("name") or "")
    args = ", ".join(str(arg) for arg in condition.get("args", []) or [])
    return f"{name}({args})"


def validation_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("step_id")): row for row in rows if row.get("step_id")}


def transition_is_conservative(baseline: str, perturbed: str) -> bool:
    rank = {"accepted": 2, "uncertain": 1, "rejected": 0}
    return baseline in rank and perturbed in rank and rank[perturbed] < rank[baseline]


def trace_map(value: Any) -> dict[str, dict[str, Any]]:
    rows = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    return {
        str(row.get("step_id") or row.get("trace_id")): row
        for row in rows
        if isinstance(row, dict) and (row.get("step_id") or row.get("trace_id"))
    }


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
    missing: list[dict[str, str]] = []
    for name in (*BASELINE_FILES, *SYMBOLIC_FILES):
        path = ctx.reasoning_dir / name
        if not path.exists():
            role = "baseline Layer 4 artifact" if name in BASELINE_FILES else "rerunnable symbolic input"
            missing.append({"path": str(path), "why_needed": f"{role}: {name}"})
    if not ctx.config_path.exists():
        missing.append(
            {"path": str(ctx.config_path), "why_needed": "Layer 3 rules and Layer 4 thresholds"}
        )
    return missing


def write_missing_data_report(ctx: EvaluationContext, missing: list[dict[str, str]]) -> None:
    lines = [
        "# Evaluation 5 Missing Data Report",
        "",
        "Evaluation 5 could not execute all controlled symbolic perturbations.",
        "",
        f"- Clip/result ID: `{ctx.clip_result_id}`",
        f"- Reasoning directory: `{ctx.reasoning_dir}`",
        "",
        "## Missing Or Malformed Inputs",
        "",
    ]
    for item in missing:
        lines.append(f"- `{item['path']}`: {item['why_needed']}")
    lines.extend(
        [
            "",
            "Required baseline artifacts fail strict mode. A specific optional perturbation without a suitable candidate is reported as SKIPPED.",
            "",
        ]
    )
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "missing_data_report.md").write_text("\n".join(lines), encoding="utf-8")


def copy_symbolic_inputs(ctx: EvaluationContext, scenario_key: str) -> Path:
    target = ctx.perturbation_input_dir / scenario_key
    target.mkdir(parents=True, exist_ok=True)
    for name in SYMBOLIC_FILES:
        shutil.copyfile(ctx.reasoning_dir / name, target / name)
    return target


def run_perturbed_pipeline(
    ctx: EvaluationContext,
    scenario_key: str,
    input_dir: Path,
    *,
    rerun_layer3: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_dir = ctx.perturbation_output_dir / scenario_key
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("step_records.jsonl", "predicates.jsonl"):
        shutil.copyfile(input_dir / name, output_dir / name)
    if rerun_layer3:
        layer3 = run_layer3_inference(
            Layer3Inputs(
                step_records_path=output_dir / "step_records.jsonl",
                predicates_path=output_dir / "predicates.jsonl",
                rules_path=ctx.config_path,
                output_path=output_dir / "inferred_constraints.csv",
            )
        )
    else:
        for name in ("inferred_constraints.csv", "rule_coverage_diagnostics.csv"):
            shutil.copyfile(input_dir / name, output_dir / name)
        layer3 = {"rerun": False}
    layer4 = run_layer4_validation(
        Layer4Inputs(
            step_records_path=output_dir / "step_records.jsonl",
            predicates_path=output_dir / "predicates.jsonl",
            constraints_path=output_dir / "inferred_constraints.csv",
            rule_coverage_path=output_dir / "rule_coverage_diagnostics.csv",
            output_path=output_dir / "validation_records.jsonl",
            config_path=ctx.config_path,
        )
    )
    return load_jsonl(output_dir / "validation_records.jsonl"), {
        "layer3": layer3,
        "layer4": layer4,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
    }


def dependency_refs(record: dict[str, Any]) -> set[tuple[str, str]]:
    refs = set()
    for item in record.get("dependency_support", []) or []:
        support = item.get("supporting_effect") or {}
        refs.add((str(support.get("constraint_id") or ""), condition_key(item.get("required_condition") or {})))
    return refs


def rejected_steps_used_as_support(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    statuses = {str(row.get("step_id")): str(row.get("status")) for row in rows}
    violations = []
    for row in rows:
        for dependency in row.get("dependency_support", []) or []:
            support = dependency.get("supporting_effect") or {}
            support_step = str(support.get("step_id") or "")
            if statuses.get(support_step) == "rejected":
                violations.append(
                    {
                        "dependent_step_id": str(row.get("step_id") or ""),
                        "support_step_id": support_step,
                        "support_constraint_id": str(support.get("constraint_id") or ""),
                    }
                )
    return violations


def make_skipped(
    check_id: str,
    scenario_key: str,
    name: str,
    message: str,
    evidence_file: str,
) -> ScenarioResult:
    return ScenarioResult(
        check_id=check_id,
        scenario_key=scenario_key,
        perturbation_name=name,
        category="symbolic_degradation",
        status="SKIPPED",
        severity="optional",
        artifact=f"evidence/perturbation_inputs/{scenario_key}",
        message=message,
        evidence_file=evidence_file,
    )


def scenario_confidence(
    ctx: EvaluationContext,
    baseline: list[dict[str, Any]],
    tau_unc: float,
) -> ScenarioResult:
    check_id, key, name = SCENARIOS[0]
    target = next(
        (
            row
            for row in baseline
            if row.get("status") == "accepted"
            and row.get("dependency_support")
            and not row.get("incompatibilities")
        ),
        None,
    )
    if not target:
        return make_skipped(check_id, key, name, "No accepted dependency-supported step exists.", "confidence_degradation_results.csv")
    input_dir = copy_symbolic_inputs(ctx, key)
    predicates = load_jsonl(input_dir / "predicates.jsonl")
    target_id = str(target["step_id"])
    degraded_conf = max(0.0, round(tau_unc / 2.0, 4))
    changed = []
    for predicate in predicates:
        if str(predicate.get("step_id")) == target_id:
            predicate["conf"] = degraded_conf
            changed.append(str(predicate.get("id") or ""))
    if not changed:
        return make_skipped(check_id, key, name, "The accepted target has no predicate evidence.", "confidence_degradation_results.csv")
    write_jsonl(input_dir / "predicates.jsonl", predicates)
    write_json(
        input_dir / "perturbation.json",
        {"target_step_id": target_id, "changed_predicate_ids": changed, "new_confidence": degraded_conf},
    )
    perturbed, run_info = run_perturbed_pipeline(ctx, key, input_dir, rerun_layer3=True)
    record = validation_map(perturbed).get(target_id, {})
    trace = trace_map(load_json(ctx.perturbation_output_dir / key / "explanation_traces.json")).get(target_id, {})
    changed_status = record.get("status") in {"uncertain", "rejected"}
    diagnostic = bool(trace.get("warnings") or trace.get("diagnostics")) and str(degraded_conf) in json.dumps(trace)
    status = "PASS" if changed_status and diagnostic else "FAIL"
    return ScenarioResult(
        check_id, key, name, "confidence", status, "critical", target_id, target_id,
        str(target.get("status")), str(record.get("status") or ""),
        f"{target.get('status')}->{record.get('status')}",
        transition_is_conservative(str(target.get("status")), str(record.get("status"))),
        "predicates.jsonl", "Accepted step degraded after its predicate confidence fell below tau_unc." if status == "PASS" else "Low-confidence evidence did not produce a conservative, traceable decision.",
        "confidence_degradation_results.csv",
        {
            "changed_predicate_ids": changed,
            "new_confidence": degraded_conf,
            "trace_preserved": bool(trace),
            "diagnostic_visible": diagnostic,
            "perturbed_status_distribution": status_distribution(perturbed),
            "run": run_info,
        },
    )


def first_dependency_candidate(
    baseline: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for record in baseline:
        if record.get("status") != "accepted":
            continue
        dependencies = list(record.get("dependency_support", []) or [])
        if dependencies:
            return record, dependencies[0]
    return None


def remove_constraint_row(path: Path, constraint_id: str) -> bool:
    rows = load_csv(path)
    fields = list(rows[0]) if rows else []
    kept = [row for row in rows if str(row.get("constraint_id")) != constraint_id]
    if len(kept) == len(rows):
        return False
    write_csv(path, kept, fields)
    return True


def scenario_missing_support(
    ctx: EvaluationContext,
    baseline: list[dict[str, Any]],
) -> ScenarioResult:
    check_id, key, name = SCENARIOS[1]
    candidate = first_dependency_candidate(baseline)
    if not candidate:
        return make_skipped(check_id, key, name, "No accepted step with produced-effect support exists.", "missing_support_predicate_results.csv")
    target, dependency = candidate
    support = dependency.get("supporting_effect") or {}
    support_id = str(support.get("constraint_id") or "")
    input_dir = copy_symbolic_inputs(ctx, key)
    if not support_id or not remove_constraint_row(input_dir / "inferred_constraints.csv", support_id):
        return make_skipped(check_id, key, name, "The referenced support constraint was not found.", "missing_support_predicate_results.csv")
    write_json(
        input_dir / "perturbation.json",
        {
            "target_step_id": target.get("step_id"),
            "removed_support_constraint_id": support_id,
            "removed_support_step_id": support.get("step_id"),
            "required_condition": dependency.get("required_condition"),
        },
    )
    perturbed, run_info = run_perturbed_pipeline(ctx, key, input_dir, rerun_layer3=False)
    target_id = str(target["step_id"])
    record = validation_map(perturbed).get(target_id, {})
    missing = list(record.get("missing_requirements", []) or [])
    support_removed = support_id not in {cid for cid, _ in dependency_refs(record)}
    trace = trace_map(load_json(ctx.perturbation_output_dir / key / "explanation_traces.json")).get(target_id, {})
    trace_missing = bool(trace.get("missing_requirements"))
    ok = record.get("status") != "accepted" and bool(missing) and support_removed and trace_missing
    return ScenarioResult(
        check_id, key, name, "missing_evidence", "PASS" if ok else "FAIL", "critical",
        target_id, target_id, str(target.get("status")), str(record.get("status") or ""),
        f"{target.get('status')}->{record.get('status')}",
        transition_is_conservative(str(target.get("status")), str(record.get("status"))),
        "inferred_constraints.csv",
        "Required produced-effect support was removed and the requirement became missing." if ok else "Required support removal was not handled conservatively and traceably.",
        "missing_support_predicate_results.csv",
        {
            "removed_support_constraint_id": support_id,
            "removed_support_step_id": support.get("step_id"),
            "required_condition": dependency.get("required_condition"),
            "missing_requirement_count": len(missing),
            "dependency_support_removed": support_removed,
            "trace_preserved": bool(trace),
            "trace_exposes_missing_requirement": trace_missing,
            "perturbed_status_distribution": status_distribution(perturbed),
            "run": run_info,
        },
    )


def scenario_incompatible_object(
    ctx: EvaluationContext,
    baseline: list[dict[str, Any]],
) -> ScenarioResult:
    check_id, key, name = SCENARIOS[2]
    predicates = load_jsonl(ctx.reasoning_dir / "predicates.jsonl")
    accepted = {str(row.get("step_id")): row for row in baseline if row.get("status") == "accepted"}
    uses = [row for row in predicates if row.get("name") == "usesObject" and str(row.get("step_id")) in accepted]
    all_objects = sorted(
        {
            str(parse_args_value(row.get("args"))[1])
            for row in predicates
            if row.get("name") == "usesObject" and len(parse_args_value(row.get("args"))) > 1
        }
    )
    target_predicate = next((row for row in uses if len(parse_args_value(row.get("args"))) > 1), None)
    if not target_predicate or len(all_objects) < 2:
        return make_skipped(check_id, key, name, "No accepted object-use predicate with an alternative object exists.", "incompatible_object_type_results.csv")
    old_object = str(parse_args_value(target_predicate["args"])[1])
    wrong_object = next((item for item in all_objects if item != old_object), "")
    target_id = str(target_predicate["step_id"])
    input_dir = copy_symbolic_inputs(ctx, key)
    changed = load_jsonl(input_dir / "predicates.jsonl")
    for row in changed:
        if str(row.get("id")) == str(target_predicate.get("id")):
            args = parse_args_value(row.get("args"))
            args[1] = wrong_object
            row["args"] = args
    write_jsonl(input_dir / "predicates.jsonl", changed)
    write_json(
        input_dir / "perturbation.json",
        {
            "target_step_id": target_id,
            "predicate_id": target_predicate.get("id"),
            "original_object": old_object,
            "replacement_object": wrong_object,
        },
    )
    perturbed, run_info = run_perturbed_pipeline(ctx, key, input_dir, rerun_layer3=True)
    baseline_record = accepted[target_id]
    record = validation_map(perturbed).get(target_id, {})
    trace = trace_map(load_json(ctx.perturbation_output_dir / key / "explanation_traces.json")).get(target_id, {})
    corrupted_visible = wrong_object in json.dumps(trace)
    diagnostic = bool(record.get("missing_requirements") or record.get("warnings") or record.get("incompatibilities"))
    ok = record.get("status") in {"uncertain", "rejected"} and corrupted_visible and diagnostic
    return ScenarioResult(
        check_id, key, name, "semantic_corruption", "PASS" if ok else "FAIL", "critical",
        target_id, target_id, str(baseline_record.get("status")), str(record.get("status") or ""),
        f"{baseline_record.get('status')}->{record.get('status')}",
        transition_is_conservative(str(baseline_record.get("status")), str(record.get("status"))),
        "predicates.jsonl",
        "Corrupted object evidence produced a conservative decision with inspectable input evidence." if ok else "Semantic corruption did not produce the expected conservative, inspectable response.",
        "incompatible_object_type_results.csv",
        {
            "predicate_id": target_predicate.get("id"),
            "original_object": old_object,
            "replacement_object": wrong_object,
            "missing_requirement_count": len(record.get("missing_requirements", []) or []),
            "warning_count": len(record.get("warnings", []) or []),
            "corrupted_input_visible_in_trace": corrupted_visible,
            "trace_preserved": bool(trace),
            "perturbed_status_distribution": status_distribution(perturbed),
            "run": run_info,
        },
    )


def scenario_error_action(
    ctx: EvaluationContext,
    baseline: list[dict[str, Any]],
) -> ScenarioResult:
    check_id, key, name = SCENARIOS[3]
    dependent_support_steps = {
        str((dep.get("supporting_effect") or {}).get("step_id") or "")
        for row in baseline
        for dep in row.get("dependency_support", []) or []
    }
    target = next(
        (
            row
            for row in baseline
            if row.get("status") == "accepted" and str(row.get("step_id")) in dependent_support_steps
        ),
        None,
    )
    if not target:
        return make_skipped(check_id, key, name, "No accepted step that supports later dependencies exists.", "injected_error_action_results.csv")
    target_id = str(target["step_id"])
    input_dir = copy_symbolic_inputs(ctx, key)
    predicates = load_jsonl(input_dir / "predicates.jsonl")
    object_predicate = next(
        (
            row
            for row in predicates
            if str(row.get("step_id")) == target_id and row.get("name") == "usesObject"
        ),
        None,
    )
    if not object_predicate:
        return make_skipped(check_id, key, name, "The selected accepted step has no usesObject predicate.", "injected_error_action_results.csv")
    predicate_id = f"{target_id}::evaluation5::injected_error"
    predicates.append(
        {
            "schema_version": "evaluation5.v1",
            "record_type": "predicate",
            "id": predicate_id,
            "step_id": target_id,
            "name": "hasAction",
            "predicate_key": "evaluation5_injected_error",
            "category": "controlled_perturbation",
            "args": [target_id, "error"],
            "conf": 1.0,
            "source": {"evaluation": "Evaluation5", "scenario": key},
            "notes": "Controlled hard-incompatibility injection.",
        }
    )
    write_jsonl(input_dir / "predicates.jsonl", predicates)
    write_json(
        input_dir / "perturbation.json",
        {"target_step_id": target_id, "injected_predicate_id": predicate_id, "action": "error"},
    )
    perturbed, run_info = run_perturbed_pipeline(ctx, key, input_dir, rerun_layer3=True)
    record = validation_map(perturbed).get(target_id, {})
    trace = trace_map(load_json(ctx.perturbation_output_dir / key / "explanation_traces.json")).get(target_id, {})
    incompatibility = bool(record.get("incompatibilities")) and "incompatibleAction" in json.dumps(trace)
    later_support = [
        row.get("step_id")
        for row in perturbed
        if any(
            str((dep.get("supporting_effect") or {}).get("step_id")) == target_id
            for dep in row.get("dependency_support", []) or []
        )
    ]
    ok = record.get("status") == "rejected" and incompatibility and not later_support
    return ScenarioResult(
        check_id, key, name, "contradictory_evidence", "PASS" if ok else "FAIL", "critical",
        target_id, target_id, str(target.get("status")), str(record.get("status") or ""),
        f"{target.get('status')}->{record.get('status')}",
        transition_is_conservative(str(target.get("status")), str(record.get("status"))),
        "predicates.jsonl",
        "Injected hard incompatibility rejected the step and its effects no longer support later dependencies." if ok else "Hard incompatibility was not fully enforced or traced.",
        "injected_error_action_results.csv",
        {
            "injected_predicate_id": predicate_id,
            "incompatibility_visible": incompatibility,
            "later_supported_step_ids": later_support,
            "trace_preserved": bool(trace),
            "rejected_support_violations": rejected_steps_used_as_support(perturbed),
            "perturbed_status_distribution": status_distribution(perturbed),
            "run": run_info,
        },
    )


def scenario_removed_effect(
    ctx: EvaluationContext,
    baseline: list[dict[str, Any]],
) -> ScenarioResult:
    check_id, key, name = SCENARIOS[4]
    usage: Counter[str] = Counter()
    support_meta: dict[str, dict[str, Any]] = {}
    for row in baseline:
        for dep in row.get("dependency_support", []) or []:
            support = dep.get("supporting_effect") or {}
            cid = str(support.get("constraint_id") or "")
            if cid:
                usage[cid] += 1
                support_meta[cid] = support
    if not usage:
        return make_skipped(check_id, key, name, "No produced effect supports a later dependency.", "removed_produced_effect_results.csv")
    support_id, baseline_dependent_count = usage.most_common(1)[0]
    support = support_meta[support_id]
    affected_baseline = [
        row
        for row in baseline
        if any(
            str((dep.get("supporting_effect") or {}).get("constraint_id")) == support_id
            for dep in row.get("dependency_support", []) or []
        )
    ]
    input_dir = copy_symbolic_inputs(ctx, key)
    if not remove_constraint_row(input_dir / "inferred_constraints.csv", support_id):
        return make_skipped(check_id, key, name, "The selected produced effect was not found.", "removed_produced_effect_results.csv")
    write_json(
        input_dir / "perturbation.json",
        {
            "removed_effect_constraint_id": support_id,
            "producer_step_id": support.get("step_id"),
            "baseline_dependent_step_ids": [row.get("step_id") for row in affected_baseline],
        },
    )
    perturbed, run_info = run_perturbed_pipeline(ctx, key, input_dir, rerun_layer3=False)
    perturbed_by_step = validation_map(perturbed)
    affected_rows = []
    for baseline_record in affected_baseline:
        step_id = str(baseline_record["step_id"])
        current = perturbed_by_step.get(step_id, {})
        still_supported = support_id in {cid for cid, _ in dependency_refs(current)}
        affected_rows.append(
            {
                "step_id": step_id,
                "baseline_status": baseline_record.get("status"),
                "perturbed_status": current.get("status"),
                "support_removed": not still_supported,
                "missing_requirement_count": len(current.get("missing_requirements", []) or []),
                "conservative_transition": transition_is_conservative(
                    str(baseline_record.get("status")), str(current.get("status"))
                ),
            }
        )
    failures = [
        row
        for row in affected_rows
        if not row["support_removed"]
        or not row["missing_requirement_count"]
        or (
            row["baseline_status"] == "accepted"
            and row["perturbed_status"] == "accepted"
        )
    ]
    traces = trace_map(load_json(ctx.perturbation_output_dir / key / "explanation_traces.json"))
    traces_preserved = all(str(row["step_id"]) in traces for row in affected_rows)
    ok = not failures and traces_preserved
    representative = next(
        (row for row in affected_rows if row["baseline_status"] == "accepted"),
        affected_rows[0],
    )
    return ScenarioResult(
        check_id, key, name, "dependency_lifecycle", "PASS" if ok else "FAIL", "critical",
        str(support.get("step_id") or ""), str(representative["step_id"]),
        str(representative["baseline_status"]), str(representative["perturbed_status"]),
        f"{representative['baseline_status']}->{representative['perturbed_status']}",
        bool(representative["conservative_transition"]),
        "inferred_constraints.csv",
        f"Removed produced effect downgraded {len(affected_rows)} later dependencies." if ok else "A removed effect still supported or silently preserved a dependent decision.",
        "removed_produced_effect_results.csv",
        {
            "removed_effect_constraint_id": support_id,
            "producer_step_id": support.get("step_id"),
            "baseline_dependent_count": baseline_dependent_count,
            "affected_steps": affected_rows,
            "trace_preserved": traces_preserved,
            "rejected_support_violations": rejected_steps_used_as_support(perturbed),
            "perturbed_status_distribution": status_distribution(perturbed),
            "run": run_info,
        },
    )


def scenario_detail_row(result: ScenarioResult) -> dict[str, Any]:
    details = result.details
    return {
        "check_id": result.check_id,
        "perturbation_name": result.perturbation_name,
        "status": result.status,
        "baseline_step_id": result.baseline_step_id,
        "affected_step_id": result.affected_step_id,
        "baseline_status": result.baseline_status,
        "perturbed_status": result.perturbed_status,
        "status_transition": result.status_transition,
        "conservative_transition": result.conservative_transition,
        "missing_requirements_introduced": details.get("missing_requirement_count", 0),
        "incompatibility_introduced": details.get("incompatibility_visible", False),
        "dependency_support_removed": details.get("dependency_support_removed", ""),
        "trace_preserved": details.get("trace_preserved", False),
        "rejected_support_violation_count": len(details.get("rejected_support_violations", [])),
        "message": result.message,
    }


def write_scenario_csvs(ctx: EvaluationContext, results: list[ScenarioResult]) -> None:
    fields = [
        "check_id", "perturbation_name", "status", "baseline_step_id", "affected_step_id",
        "baseline_status", "perturbed_status", "status_transition", "conservative_transition",
        "missing_requirements_introduced", "incompatibility_introduced",
        "dependency_support_removed", "trace_preserved",
        "rejected_support_violation_count", "message",
    ]
    file_by_key = {
        "confidence_degradation": "confidence_degradation_results.csv",
        "missing_support_predicate": "missing_support_predicate_results.csv",
        "incompatible_object_type": "incompatible_object_type_results.csv",
        "injected_error_action": "injected_error_action_results.csv",
        "removed_produced_effect": "removed_produced_effect_results.csv",
    }
    for result in results:
        write_csv(ctx.output_dir / file_by_key[result.scenario_key], [scenario_detail_row(result)], fields)


def write_aggregate_csvs(
    ctx: EvaluationContext,
    baseline: list[dict[str, Any]],
    results: list[ScenarioResult],
) -> None:
    summary_fields = [
        "check_id", "perturbation_name", "category", "status", "severity",
        "baseline_step_id", "affected_step_id", "baseline_status", "perturbed_status",
        "status_transition", "conservative_transition", "artifact", "message", "evidence_file",
    ]
    write_csv(
        ctx.output_dir / "evaluation5_summary.csv",
        [
            {
                "check_id": row.check_id,
                "perturbation_name": row.perturbation_name,
                "category": row.category,
                "status": row.status,
                "severity": row.severity,
                "baseline_step_id": row.baseline_step_id,
                "affected_step_id": row.affected_step_id,
                "baseline_status": row.baseline_status,
                "perturbed_status": row.perturbed_status,
                "status_transition": row.status_transition,
                "conservative_transition": row.conservative_transition,
                "artifact": row.artifact,
                "message": row.message,
                "evidence_file": row.evidence_file,
            }
            for row in results
        ],
        summary_fields,
    )
    transitions = Counter(
        row.status_transition for row in results if row.status_transition
    )
    write_csv(
        ctx.output_dir / "status_transition_matrix.csv",
        [
            {
                "baseline_status": transition.split("->", 1)[0],
                "perturbed_status": transition.split("->", 1)[1],
                "count": count,
            }
            for transition, count in sorted(transitions.items())
        ],
        ["baseline_status", "perturbed_status", "count"],
    )
    write_csv(
        ctx.output_dir / "conservative_degradation_summary.csv",
        [
            {
                "check_id": row.check_id,
                "perturbation_name": row.perturbation_name,
                "status": row.status,
                "status_transition": row.status_transition,
                "conservative_transition": row.conservative_transition,
                "accepted_remained_accepted": (
                    row.baseline_status == "accepted" and row.perturbed_status == "accepted"
                ),
                "message": row.message,
            }
            for row in results
        ],
        [
            "check_id", "perturbation_name", "status", "status_transition",
            "conservative_transition", "accepted_remained_accepted", "message",
        ],
    )
    write_csv(
        ctx.output_dir / "trace_preservation_results.csv",
        [
            {
                "check_id": row.check_id,
                "perturbation_name": row.perturbation_name,
                "affected_step_id": row.affected_step_id,
                "trace_preserved": row.details.get("trace_preserved", False),
                "diagnostic_visible": any(
                    bool(row.details.get(key))
                    for key in (
                        "diagnostic_visible",
                        "trace_exposes_missing_requirement",
                        "corrupted_input_visible_in_trace",
                        "incompatibility_visible",
                    )
                )
                or bool(row.details.get("affected_steps")),
                "status": row.status,
                "message": row.message,
            }
            for row in results
        ],
        [
            "check_id", "perturbation_name", "affected_step_id", "trace_preserved",
            "diagnostic_visible", "status", "message",
        ],
    )
    write_csv(
        ctx.output_dir / "dependency_after_degradation_results.csv",
        [
            {
                "check_id": row.check_id,
                "perturbation_name": row.perturbation_name,
                "producer_step_id": row.details.get("producer_step_id") or row.details.get("removed_support_step_id") or row.baseline_step_id,
                "affected_step_id": row.affected_step_id,
                "dependency_support_removed": row.details.get("dependency_support_removed", ""),
                "later_supported_step_ids": json.dumps(row.details.get("later_supported_step_ids", [])),
                "affected_steps": json.dumps(row.details.get("affected_steps", [])),
                "rejected_support_violation_count": len(row.details.get("rejected_support_violations", [])),
                "status": row.status,
                "message": row.message,
            }
            for row in results
        ],
        [
            "check_id", "perturbation_name", "producer_step_id", "affected_step_id",
            "dependency_support_removed", "later_supported_step_ids", "affected_steps",
            "rejected_support_violation_count", "status", "message",
        ],
    )
    write_json(
        ctx.output_dir / "evidence" / "baseline_snapshot.json",
        {
            "clip_result_id": ctx.clip_result_id,
            "status_distribution": status_distribution(baseline),
            "records": [
                {
                    "step_id": row.get("step_id"),
                    "index": row.get("index"),
                    "status": row.get("status"),
                    "confidence": row.get("confidence"),
                    "dependency_support": row.get("dependency_support", []),
                    "missing_requirements": row.get("missing_requirements", []),
                    "incompatibilities": row.get("incompatibilities", []),
                    "produced_effect_lifecycle": row.get("produced_effect_lifecycle", []),
                }
                for row in baseline
            ],
        },
    )


def write_readme(ctx: EvaluationContext) -> None:
    text = f"""# Evaluation 5: Symbolic Input Degradation

Evaluation 5 checks whether the reasoning layer degrades conservatively and traceably when already-symbolic evidence is deliberately made incomplete, low-confidence, contradictory, or semantically wrong.

This is not a real perception robustness benchmark. It does not test computer vision, object detection, action recognition, raw-video interpretation, or recovery from perception errors. Correctness against expert judgement is also outside this evaluation's scope.

## Selected Clip

- Clip/result ID: `{ctx.clip_result_id}`
- Reason: it contains accepted, uncertain, and rejected steps, dependencies, incompatibilities, removal actions, invalidated effects, and produced-effect lifecycle evidence.

## How To Run

```powershell
.venv\\Scripts\\python.exe scripts\\24_evaluate_symbolic_input_degradation.py --project-root . --clip-result-id {ctx.clip_result_id} --reasoning-dir results\\reasoning_layers\\{ctx.clip_result_id} --output-dir docs\\reasoning_layers\\Evaluation5 --strict
```

## Required Inputs

- `validation_records.jsonl`
- `step_validations.csv`
- `explanation_traces.json`
- `effect_history_diagnostics.csv`
- `step_records.jsonl`
- `predicates.jsonl`
- `inferred_constraints.csv`
- `rule_coverage_diagnostics.csv`
- `config/thesis_rules.yaml`

## Generated Outputs

- `evaluation5_report.md`
- `evaluation5_summary.csv`
- one CSV per perturbation scenario
- status-transition, conservative-degradation, trace-preservation, and dependency CSVs
- `evidence/evaluation5_results.json`
- `evidence/baseline_snapshot.json`
- exact perturbed inputs under `evidence/perturbation_inputs/`
- complete rerun outputs under `evidence/perturbation_outputs/`
- `missing_data_report.md` only when required data is missing or malformed.
"""
    (ctx.output_dir / "README.md").write_text(text, encoding="utf-8")


def write_report(
    ctx: EvaluationContext,
    baseline: list[dict[str, Any]],
    results: list[ScenarioResult],
) -> None:
    counts = Counter(row.status for row in results)
    lines = [
        "# Evaluation 5 Report: Symbolic Input Degradation",
        "",
        f"- Evaluated clip/result ID: `{ctx.clip_result_id}`",
        f"- Timestamp: `{ctx.timestamp}`",
        f"- Reasoning directory: `{ctx.reasoning_dir}`",
        f"- Output directory: `{ctx.output_dir}`",
        "",
        "## Scope",
        "",
        "This evaluation measures symbolic robustness under controlled degradation. It does not test real perception robustness and does not prove correctness against expert judgement.",
        "",
        "## Baseline Status Distribution",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status, count in status_distribution(baseline).items():
        lines.append(f"| {status} | {count} |")
    lines.extend(
        [
            "",
            "## Perturbation Outcomes",
            "",
            "| Perturbation | Status | Transition | Conservative | Perturbed distribution |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in results:
        distribution = row.details.get("perturbed_status_distribution", {})
        lines.append(
            f"| {row.perturbation_name} | {row.status} | {row.status_transition or 'n/a'} | "
            f"{row.conservative_transition} | `{distribution}` |"
        )
    lines.extend(["", "## Detailed Perturbations", ""])
    for row in results:
        details = row.details
        lines.extend(
            [
                f"### {row.check_id}: {row.perturbation_name}",
                "",
                f"- Result: **{row.status}**",
                f"- Target step: `{row.affected_step_id}` ({short_step_id(row.affected_step_id)})",
                f"- Baseline to perturbed status: `{row.status_transition or 'n/a'}`",
            ]
        )
        if row.scenario_key == "confidence_degradation":
            changed = details.get("changed_predicate_ids", [])
            predicate_names = sorted(
                {
                    predicate_id.split("::p::", 1)[1].split("::", 1)[0]
                    for predicate_id in changed
                    if "::p::" in predicate_id
                }
            )
            lines.extend(
                [
                    "- What was changed: all symbolic predicates attached to the selected accepted step had their confidence lowered.",
                    f"- New confidence: `{details.get('new_confidence')}`; this is below `tau_unc`, so the evidence is insufficient for acceptance.",
                    f"- Changed predicates: {len(changed)} records covering `{', '.join(predicate_names)}`.",
                    "- Why this target: the step was accepted and depended on an earlier produced effect, making it suitable for checking whether low-confidence evidence prevents clean acceptance.",
                    f"- Observed consequence: the target became `{row.perturbed_status}`; the perturbed clip distribution was `{details.get('perturbed_status_distribution', {})}`.",
                    f"- Diagnostic evidence: the degraded confidence was preserved in the explanation trace (`diagnostic_visible={details.get('diagnostic_visible')}`).",
                ]
            )
        elif row.scenario_key == "missing_support_predicate":
            condition = format_condition(details.get("required_condition"))
            lines.extend(
                [
                    "- What was changed: the earlier `produces(...)` constraint that supplied Layer 4 dependency support was removed from the copied `inferred_constraints.csv`.",
                    "- Scope clarification: this scenario removes produced-effect evidence, the closest symbolic dependency input consumed by Layer 4; it does not edit the baseline artifacts.",
                    f"- Removed producer: `{details.get('removed_support_step_id')}` ({short_step_id(details.get('removed_support_step_id'))}).",
                    f"- Removed constraint: `{details.get('removed_support_constraint_id')}`.",
                    f"- Requirement that lost support: `{condition}`.",
                    f"- Observed consequence: the target became `{row.perturbed_status}`, gained {details.get('missing_requirement_count', 0)} missing requirement(s), and dependency support was removed (`{details.get('dependency_support_removed')}`).",
                    f"- Trace evidence: the missing requirement is visible in the perturbed explanation trace (`{details.get('trace_exposes_missing_requirement')}`).",
                ]
            )
        elif row.scenario_key == "incompatible_object_type":
            lines.extend(
                [
                    "- What was changed: the selected step's `usesObject(step, object)` predicate was rewritten to reference a different plausible component already present in the clip.",
                    f"- Object substitution: `{details.get('original_object')}` -> `{details.get('replacement_object')}`.",
                    f"- Changed predicate: `{details.get('predicate_id')}`.",
                    "- Why this is semantically incompatible: the remaining type, install-target, and domain predicates still describe the original object, so the substituted object no longer forms a coherent rule match.",
                    f"- Observed consequence: the target became `{row.perturbed_status}` with {details.get('warning_count', 0)} warning(s) and {details.get('missing_requirement_count', 0)} missing requirement(s).",
                    f"- Trace evidence: the replacement object is preserved in the explanation trace (`{details.get('corrupted_input_visible_in_trace')}`).",
                ]
            )
        elif row.scenario_key == "injected_error_action":
            lines.extend(
                [
                    "- What was changed: an additional high-confidence `hasAction(step, error)` predicate was injected while retaining the step's original object evidence.",
                    f"- Injected predicate: `{details.get('injected_predicate_id')}`.",
                    "- Rule response: the existing compatibility rule inferred `incompatibleAction(step, object, error)`, which Layer 4 treats as a hard violation.",
                    f"- Observed consequence: the target became `{row.perturbed_status}` and incompatibility evidence was visible (`{details.get('incompatibility_visible')}`).",
                    f"- Dependency consequence: later steps still supported by the rejected target: `{details.get('later_supported_step_ids', [])}`.",
                    f"- Rejected-support violations: `{len(details.get('rejected_support_violations', []))}`.",
                ]
            )
        elif row.scenario_key == "removed_produced_effect":
            affected = details.get("affected_steps", [])
            lines.extend(
                [
                    "- What was changed: one frequently reused `produces(installed, component, target)` constraint was removed before rerunning Layer 4.",
                    f"- Producer step: `{details.get('producer_step_id')}` ({short_step_id(details.get('producer_step_id'))}).",
                    f"- Removed effect constraint: `{details.get('removed_effect_constraint_id')}`.",
                    f"- Baseline dependent steps affected: `{details.get('baseline_dependent_count', 0)}`.",
                    "- Per-step consequences:",
                ]
            )
            for affected_row in affected:
                lines.append(
                    f"  - `{short_step_id(affected_row.get('step_id'))}`: "
                    f"`{affected_row.get('baseline_status')} -> {affected_row.get('perturbed_status')}`, "
                    f"support removed=`{affected_row.get('support_removed')}`, "
                    f"missing requirements=`{affected_row.get('missing_requirement_count')}`."
                )
            lines.extend(
                [
                    f"- Rejected-support violations after removal: `{len(details.get('rejected_support_violations', []))}`.",
                    f"- Trace evidence was preserved for every affected dependent: `{details.get('trace_preserved')}`.",
                ]
            )
        lines.extend(
            [
                f"- Exact perturbed input: `evidence/perturbation_inputs/{row.scenario_key}/perturbation.json`",
                f"- Complete rerun output: `evidence/perturbation_outputs/{row.scenario_key}/`",
                "",
            ]
        )
    lines.extend(
        [
            "",
            "## Conservative Transition Summary",
            "",
            f"- Conservative transitions: {sum(row.conservative_transition is True for row in results)}",
            f"- Accepted remained accepted after direct degradation: {sum(row.baseline_status == 'accepted' and row.perturbed_status == 'accepted' for row in results)}",
            "",
            "## Traceability Summary",
            "",
            f"- Scenarios with preserved traces: {sum(bool(row.details.get('trace_preserved')) for row in results)} of {sum(row.status != 'SKIPPED' for row in results)} executed scenarios.",
            f"- Rejected-support violations after perturbation: {sum(len(row.details.get('rejected_support_violations', [])) for row in results)}.",
            "- Exact perturbations and complete rerun artifacts are stored under `evidence/perturbation_inputs/` and `evidence/perturbation_outputs/`.",
            "",
            "## Limitations",
            "",
            "The perturbations operate on already-symbolic inputs and use one representative clip. They test conservative reasoning behavior and diagnostic traceability, not perception quality, dataset-wide robustness, or semantic correctness against expert annotations.",
            "",
            f"Status totals: PASS={counts['PASS']}, FAIL={counts['FAIL']}, WARNING={counts['WARNING']}, SKIPPED={counts['SKIPPED']}.",
            "",
        ]
    )
    (ctx.output_dir / "evaluation5_report.md").write_text("\n".join(lines), encoding="utf-8")


def evaluate(ctx: EvaluationContext) -> dict[str, Any]:
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "evidence").mkdir(parents=True, exist_ok=True)
    missing = detect_missing_data(ctx)
    if missing:
        write_missing_data_report(ctx, missing)
        results = [
            make_skipped(check_id, key, name, "Required baseline or symbolic input artifacts are missing.", f"{key}_results.csv")
            for check_id, key, name in SCENARIOS
        ]
        result = {
            "evaluation": "Evaluation 5: Symbolic input degradation",
            "timestamp": ctx.timestamp,
            "clip_result_id": ctx.clip_result_id,
            "missing": missing,
            "checks": [asdict(row) for row in results],
        }
        write_json(ctx.output_dir / "evidence" / "evaluation5_results.json", result)
        write_readme(ctx)
        return result
    try:
        baseline = load_jsonl(ctx.reasoning_dir / "validation_records.jsonl")
        load_csv(ctx.reasoning_dir / "step_validations.csv")
        load_json(ctx.reasoning_dir / "explanation_traces.json")
        load_csv(ctx.reasoning_dir / "effect_history_diagnostics.csv")
        tau_acc, tau_unc = load_thresholds(ctx.config_path)
    except Exception as exc:
        malformed = [{"path": str(ctx.reasoning_dir), "why_needed": f"malformed required input: {exc}"}]
        write_missing_data_report(ctx, malformed)
        result = {
            "evaluation": "Evaluation 5: Symbolic input degradation",
            "timestamp": ctx.timestamp,
            "clip_result_id": ctx.clip_result_id,
            "missing": malformed,
            "checks": [],
        }
        write_json(ctx.output_dir / "evidence" / "evaluation5_results.json", result)
        write_readme(ctx)
        return result

    runners: list[Callable[[], ScenarioResult]] = [
        lambda: scenario_confidence(ctx, baseline, tau_unc),
        lambda: scenario_missing_support(ctx, baseline),
        lambda: scenario_incompatible_object(ctx, baseline),
        lambda: scenario_error_action(ctx, baseline),
        lambda: scenario_removed_effect(ctx, baseline),
    ]
    results = [runner() for runner in runners]
    write_scenario_csvs(ctx, results)
    write_aggregate_csvs(ctx, baseline, results)
    result = {
        "evaluation": "Evaluation 5: Symbolic input degradation",
        "timestamp": ctx.timestamp,
        "clip_result_id": ctx.clip_result_id,
        "input_directories": {"reasoning_dir": str(ctx.reasoning_dir)},
        "output_dir": str(ctx.output_dir),
        "thresholds": {"tau_acc": tau_acc, "tau_unc": tau_unc},
        "baseline_status_distribution": status_distribution(baseline),
        "checks": [asdict(row) for row in results],
        "status_counts": dict(Counter(row.status for row in results)),
        "status_transition_matrix": dict(
            Counter(row.status_transition for row in results if row.status_transition)
        ),
        "accepted_remained_accepted_after_direct_degradation": any(
            row.baseline_status == "accepted" and row.perturbed_status == "accepted"
            for row in results
            if row.status != "SKIPPED"
        ),
        "rejected_support_violation_count": sum(
            len(row.details.get("rejected_support_violations", [])) for row in results
        ),
        "trace_preserved_count": sum(
            bool(row.details.get("trace_preserved")) for row in results
        ),
    }
    write_json(ctx.output_dir / "evidence" / "evaluation5_results.json", result)
    write_readme(ctx)
    write_report(ctx, baseline, results)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--clip-result-id", default=DEFAULT_CLIP_RESULT_ID)
    parser.add_argument("--reasoning-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/reasoning_layers/Evaluation5"))
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def build_context(args: argparse.Namespace) -> EvaluationContext:
    project_root = args.project_root.resolve()
    reasoning_dir = args.reasoning_dir or Path("results") / "reasoning_layers" / args.clip_result_id
    output_dir = args.output_dir
    return EvaluationContext(
        project_root=project_root,
        clip_result_id=args.clip_result_id,
        reasoning_dir=(project_root / reasoning_dir).resolve() if not reasoning_dir.is_absolute() else reasoning_dir,
        output_dir=(project_root / output_dir).resolve() if not output_dir.is_absolute() else output_dir,
        strict=args.strict,
    )


def main(argv: list[str] | None = None) -> int:
    ctx = build_context(parse_args(argv))
    result = evaluate(ctx)
    if not ctx.strict:
        return 0
    if result.get("missing"):
        return 1
    checks = result.get("checks", [])
    if not any(row.get("status") != "SKIPPED" for row in checks):
        return 1
    if any(row.get("status") == "FAIL" and row.get("severity") == "critical" for row in checks):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
