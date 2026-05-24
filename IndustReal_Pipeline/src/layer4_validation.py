"""Layer 4 validation over Layer 3 constraints and accumulated effects."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "thesis_rules.yaml"
SAME_STEP_CONSTRAINT_SUPPORT = {
    "type": "same_step_constraint",
    "notes": "Constraint observed in the step.",
}


@dataclass(frozen=True)
class Layer4Inputs:
    step_records_path: Path
    predicates_path: Path
    constraints_path: Path
    output_path: Path
    config_path: Path | None = DEFAULT_CONFIG_PATH
    rule_coverage_path: Path | None = None
    tau_acc: float | None = None
    tau_unc: float | None = None


def run_layer4_validation(inputs: Layer4Inputs) -> dict[str, Any]:
    steps = _read_records(Path(inputs.step_records_path))
    predicates = _read_records(Path(inputs.predicates_path))
    constraints = _read_records(Path(inputs.constraints_path))
    rule_coverage_path = inputs.rule_coverage_path or Path(inputs.constraints_path).with_name("rule_coverage_diagnostics.csv")
    rule_coverage = _read_records(rule_coverage_path) if rule_coverage_path.exists() else []
    validation_config = _load_validation_config(inputs.config_path)
    tau_acc = float(inputs.tau_acc if inputs.tau_acc is not None else validation_config["tau_acc"])
    tau_unc = float(inputs.tau_unc if inputs.tau_unc is not None else validation_config["tau_unc"])

    predicates_by_step = _group_by_step(predicates)
    constraints_by_step = _group_by_step(constraints)
    diagnostics_by_step = _group_by_step(rule_coverage)
    ordered_steps = sorted(steps, key=_step_sort_key)

    validation_records: list[dict[str, Any]] = []
    explanation_traces: list[dict[str, Any]] = []
    historical_effects: list[dict[str, Any]] = []
    active_effects: dict[tuple[Any, ...], dict[str, Any]] = {}
    produced_effect_lifecycle: list[dict[str, Any]] = []
    effect_history_rows: list[dict[str, Any]] = []
    for step in ordered_steps:
        step_id = str(step.get("id") or step.get("step_id") or "")
        if not step_id:
            continue
        step_predicates = predicates_by_step.get(step_id, [])
        step_constraints = constraints_by_step.get(step_id, [])
        step_diagnostics = diagnostics_by_step.get(step_id, [])
        record = _validate_step(
            step,
            step_predicates,
            step_constraints,
            step_diagnostics,
            active_effects,
            tau_acc=tau_acc,
            tau_unc=tau_unc,
        )
        invalidated_effects = _apply_produced_effects(
            record,
            step_constraints,
            historical_effects,
            active_effects,
            produced_effect_lifecycle,
        )
        record["invalidated_effects"] = invalidated_effects
        record["trace"]["invalidated_effects"] = invalidated_effects
        validation_records.append(record)
        explanation_traces.append(record["trace"])
        effect_history_rows.extend(_effect_history_rows_for_step(record, step_constraints, invalidated_effects))

    lifecycle_by_step: dict[str, list[dict[str, Any]]] = {}
    for effect in produced_effect_lifecycle:
        step_id = str(effect.get("step_id") or "")
        if step_id:
            lifecycle_by_step.setdefault(step_id, []).append(effect)
    for record in validation_records:
        step_lifecycle = [dict(item) for item in lifecycle_by_step.get(str(record.get("step_id") or ""), [])]
        record["produced_effect_lifecycle"] = step_lifecycle
        record["trace"]["produced_effect_lifecycle"] = step_lifecycle

    output_path = Path(inputs.output_path)
    trace_path = output_path.with_name("explanation_traces.json")
    csv_path = output_path.with_name("step_validations.csv")
    effect_history_path = output_path.with_name("effect_history_diagnostics.csv")
    _write_jsonl(output_path, validation_records)
    _write_json(trace_path, explanation_traces)
    _write_validation_csv(csv_path, validation_records)
    _write_effect_history_csv(effect_history_path, effect_history_rows)
    return {
        "step_records": len(steps),
        "predicates": len(predicates),
        "constraints": len(constraints),
        "validation_records": len(validation_records),
        "output_path": str(output_path),
        "step_validations_csv": str(csv_path),
        "explanation_traces_path": str(trace_path),
        "effect_history_diagnostics_csv": str(effect_history_path),
        "validation_config_path": str(inputs.config_path) if inputs.config_path else None,
        "rule_coverage_diagnostics_path": str(rule_coverage_path) if rule_coverage_path.exists() else None,
        "warnings": sum(len(item.get("warnings", [])) for item in validation_records),
        "tau_acc": tau_acc,
        "tau_unc": tau_unc,
        "status_counts": _count_by(validation_records, "status"),
        "supported_requirements": sum(len(item.get("supported_requirements", [])) for item in validation_records),
        "missing_requirements": sum(len(item.get("missing_requirements", [])) for item in validation_records),
        "historical_effects": len(historical_effects),
        "active_effects": len(active_effects),
        "produced_effect_lifecycle": len(produced_effect_lifecycle),
        "invalidated_effects": sum(len(item.get("invalidated_effects", [])) for item in validation_records),
    }


def _validate_step(
    step: dict[str, Any],
    predicates: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    active_effects: dict[tuple[Any, ...], dict[str, Any]],
    *,
    tau_acc: float,
    tau_unc: float,
) -> dict[str, Any]:
    step_id = str(step.get("id") or step.get("step_id") or "")
    evidence_predicates = [_predicate_ref(item) for item in predicates]
    evidence_constraints = [_constraint_ref(item, support=_same_step_constraint_support()) for item in constraints]
    rule_coverage = _rule_coverage_summary(diagnostics, constraints)
    warnings = _warnings_from_rule_coverage(rule_coverage)
    requirements = [item for item in constraints if _is_requirement(item)]
    incompatibilities = [
        item
        for item in constraints
        if item.get("name") == "incompatibleAction" or item.get("status") == "incompatibility"
    ]
    supported_requirements = []
    missing_requirements = []
    dependency_support = []
    provisional_dependency_support = []
    for constraint in requirements:
        support = _support_for_required_condition(constraint, predicates, active_effects)
        if support is None:
            missing_requirements.append(_constraint_ref(constraint, support=None))
        else:
            requirement = _constraint_ref(constraint, support=support)
            supported_requirements.append(requirement)
            if support.get("type") == "previous_produced_effect":
                dependency_support.append(
                    {
                        "required_condition": _condition_ref(constraint),
                        "supporting_effect": support,
                    }
                )
                if support.get("provisional"):
                    provisional_dependency_support.append(support)

    confidence_values = [
        _parse_float(item.get("conf"))
        for item in [*predicates, *constraints]
        if _parse_float(item.get("conf")) is not None
    ]
    confidence = min(confidence_values) if confidence_values else None
    comparable_confidence = confidence if confidence is not None else -1.0
    partial_support = bool(supported_requirements) or (not requirements and bool(predicates or constraints))

    if incompatibilities:
        status = "rejected"
    elif warnings and not rule_coverage.get("has_rule_coverage"):
        status = "uncertain"
    elif missing_requirements:
        status = "uncertain" if partial_support and comparable_confidence >= tau_unc else "rejected"
    elif _is_remove_step(constraints) and provisional_dependency_support:
        status = "uncertain"
    elif not missing_requirements and comparable_confidence >= tau_acc:
        status = "accepted"
    elif partial_support and comparable_confidence >= tau_unc:
        status = "uncertain"
    else:
        status = "rejected"

    trace = {
        "step_id": step_id,
        "predicate_evidence": evidence_predicates,
        "constraint_evidence": evidence_constraints,
        "incompatibility_evidence": [
            _constraint_ref(item, support=_same_step_constraint_support())
            for item in incompatibilities
        ],
        "dependency_evidence": dependency_support,
        "missing_requirements": missing_requirements,
        "invalidated_effects": [],
        "produced_effect_lifecycle": [],
        "warnings": warnings,
        "diagnostics": {"rule_coverage": rule_coverage, "warnings": warnings},
        "status": status,
        "confidence": confidence,
    }
    return {
        "schema_version": "thesis_layer4_validation.v1",
        "record_type": "validation_record",
        "step_id": step_id,
        "source_event_id": step.get("source_event_id"),
        "index": step.get("index"),
        "status": status,
        "confidence": confidence,
        "conf": confidence,
        "supported_requirements": supported_requirements,
        "missing_requirements": missing_requirements,
        "dependency_support": dependency_support,
        "incompatibilities": [
            _constraint_ref(item, support=_same_step_constraint_support())
            for item in incompatibilities
        ],
        "evidence_predicates": evidence_predicates,
        "evidence_constraints": evidence_constraints,
        "warnings": warnings,
        "diagnostics": {"rule_coverage": rule_coverage, "warnings": warnings},
        "has_rule_coverage": rule_coverage.get("has_rule_coverage"),
        "matched_rule_count": rule_coverage.get("matched_rule_count"),
        "produced_constraint_count": rule_coverage.get("produced_constraint_count"),
        "has_expected_effect": rule_coverage.get("has_expected_effect"),
        "unsupported_action": bool(warnings),
        "unsupported_action_name": rule_coverage.get("action_name") if warnings else None,
        "trace_id": step_id,
        # Backward-compatible aliases for existing downstream readers.
        "supported_requires": supported_requirements,
        "missing_requires": missing_requirements,
        "produced_effects": [
            _constraint_ref(item, support=_same_step_constraint_support())
            for item in constraints
            if item.get("name") == "produces"
        ],
        "safety_requirements": [
            _constraint_ref(item, support=_same_step_constraint_support())
            for item in constraints
            if item.get("name") == "requiresSafety"
        ],
        "tool_requirements": [
            _constraint_ref(item, support=_same_step_constraint_support())
            for item in constraints
            if item.get("name") == "requiresTool"
        ],
        "trace": trace,
    }


def _apply_produced_effects(
    record: dict[str, Any],
    constraints: list[dict[str, Any]],
    historical_effects: list[dict[str, Any]],
    active_effects: dict[tuple[Any, ...], dict[str, Any]],
    produced_effect_lifecycle: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    produced = [item for item in constraints if item.get("name") == "produces"]
    invalidated_effects: list[dict[str, Any]] = []
    step_status = str(record.get("status") or "")
    for constraint in produced:
        effect_record = _effect_record(constraint, record)
        historical_effects.append(effect_record)
        produced_effect_lifecycle.append(effect_record)
        if step_status == "rejected":
            effect_record["effect_lifecycle_status"] = "inactive_rejected"
            continue
        effect_record["effect_lifecycle_status"] = "active"
        condition = _condition_ref(constraint)
        if condition.get("name") == "removed":
            invalidated = _invalidate_installed_effect(
                constraint,
                record,
                active_effects,
            )
            invalidated_effects.extend(invalidated)
        active_effects[_condition_key(constraint)] = effect_record
    return invalidated_effects


def _effect_record(constraint: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "previous_produced_effect",
        "constraint_id": constraint.get("constraint_id"),
        "step_id": constraint.get("step_id"),
        "producer_status": record.get("status"),
        "provisional": record.get("status") == "uncertain",
        "args": _constraint_args(constraint),
        "condition": _condition_ref(constraint),
        "conf": _parse_float(constraint.get("conf")),
        "effect_lifecycle_status": "active",
        "invalidated_by_constraint_id": None,
    }


def _invalidate_installed_effect(
    removing_constraint: dict[str, Any],
    record: dict[str, Any],
    active_effects: dict[tuple[Any, ...], dict[str, Any]],
) -> list[dict[str, Any]]:
    args = _constraint_args(removing_constraint)
    if len(args) < 4:
        return []
    installed_key = ("installed", args[2], args[3])
    active_effect = active_effects.pop(installed_key, None)
    if not active_effect:
        return []
    active_effect["effect_lifecycle_status"] = "invalidated"
    active_effect["invalidated_by_constraint_id"] = removing_constraint.get("constraint_id")
    return [
        {
            "condition": {"name": "installed", "args": [args[2], args[3]]},
            "produced_by_step_id": active_effect.get("step_id"),
            "produced_by_constraint_id": active_effect.get("constraint_id"),
            "producer_status": active_effect.get("producer_status"),
            "invalidated_by_step_id": record.get("step_id"),
            "invalidated_by_effect": _condition_ref(removing_constraint),
            "invalidated_by_constraint_id": removing_constraint.get("constraint_id"),
        }
    ]


def _effect_history_rows_for_step(
    record: dict[str, Any],
    constraints: list[dict[str, Any]],
    invalidated_effects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for constraint in constraints:
        if constraint.get("name") != "produces":
            continue
        condition = _condition_ref(constraint)
        rows.append(
            {
                "step_id": record.get("step_id"),
                "step_index": record.get("index"),
                "status": record.get("status"),
                "event": "produced_effect",
                "condition_name": condition.get("name"),
                "condition_args": json.dumps(condition.get("args", []), ensure_ascii=False),
                "constraint_id": constraint.get("constraint_id"),
                "related_step_id": "",
                "active_after_step": str(record.get("status") != "rejected").lower(),
                "notes": "",
            }
        )
    for item in invalidated_effects:
        rows.append(
            {
                "step_id": record.get("step_id"),
                "step_index": record.get("index"),
                "status": record.get("status"),
                "event": "invalidated_effect",
                "condition_name": item.get("condition", {}).get("name"),
                "condition_args": json.dumps(item.get("condition", {}).get("args", []), ensure_ascii=False),
                "constraint_id": item.get("invalidated_by_constraint_id"),
                "related_step_id": item.get("produced_by_step_id"),
                "active_after_step": "false",
                "notes": "Historical effect retained but removed from active support.",
            }
        )
    return rows


def _rule_coverage_summary(
    diagnostics: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostic = diagnostics[0] if diagnostics else {}
    produced_constraint_count = _parse_int(diagnostic.get("produced_constraint_count"))
    if produced_constraint_count is None:
        produced_constraint_count = len(constraints)
    matched_rule_count = _parse_int(diagnostic.get("matched_rule_count"))
    if matched_rule_count is None:
        matched_rule_count = len({str(item.get("rule_id")) for item in constraints if item.get("rule_id")})
    return {
        "step_id": diagnostic.get("step_id"),
        "step_index": _parse_int(diagnostic.get("step_index")),
        "action_name": diagnostic.get("action_name"),
        "object_args": diagnostic.get("object_args") if isinstance(diagnostic.get("object_args"), list) else _json_or_empty_list(diagnostic.get("object_args")),
        "predicate_count": _parse_int(diagnostic.get("predicate_count")) or 0,
        "matched_rule_count": matched_rule_count,
        "produced_constraint_count": produced_constraint_count,
        "has_expected_effect": _parse_bool(diagnostic.get("has_expected_effect")) if diagnostic else any(item.get("name") == "produces" for item in constraints),
        "has_requirement": _parse_bool(diagnostic.get("has_requirement")) if diagnostic else any(_is_requirement(item) for item in constraints),
        "has_incompatibility": _parse_bool(diagnostic.get("has_incompatibility")) if diagnostic else any(item.get("status") == "incompatibility" for item in constraints),
        "has_meaningful_evidence": _parse_bool(diagnostic.get("has_meaningful_evidence")) if diagnostic else None,
        "has_rule_coverage": _parse_bool(diagnostic.get("has_rule_coverage")) if diagnostic else bool(constraints),
        "warning_code": diagnostic.get("warning_code") or "",
        "warning_message": diagnostic.get("warning_message") or "",
        "evidence_predicates": diagnostic.get("evidence_predicates") if isinstance(diagnostic.get("evidence_predicates"), list) else _json_or_empty_list(diagnostic.get("evidence_predicates")),
        "suggested_fix": diagnostic.get("suggested_fix") or "",
    }


def _warnings_from_rule_coverage(rule_coverage: dict[str, Any]) -> list[dict[str, Any]]:
    warning_code = rule_coverage.get("warning_code")
    if not warning_code:
        return []
    return [
        {
            "warning_code": warning_code,
            "warning_message": rule_coverage.get("warning_message"),
            "action_name": rule_coverage.get("action_name"),
            "step_id": rule_coverage.get("step_id"),
            "step_index": rule_coverage.get("step_index"),
            "evidence_predicates": rule_coverage.get("evidence_predicates", []),
            "suggested_fix": rule_coverage.get("suggested_fix"),
        }
    ]


def _support_for_required_condition(
    constraint: dict[str, Any],
    predicates: list[dict[str, Any]],
    active_effects: dict[tuple[Any, ...], dict[str, Any]],
) -> dict[str, Any] | None:
    key = _condition_key(constraint)
    if _can_use_dependency_support(constraint) and key in active_effects:
        effect = active_effects[key]
        return {
            "type": "previous_produced_effect",
            "constraint_id": effect.get("constraint_id"),
            "step_id": effect.get("step_id"),
            "producer_status": effect.get("producer_status"),
            "provisional": bool(effect.get("provisional")),
            "args": list(effect.get("args", []) or []),
            "condition": _condition_ref(effect),
        }
    if _requires_previous_effect(constraint):
        return None
    for predicate in predicates:
        if _predicate_supports_condition(predicate, key):
            return {
                "type": "same_step_predicate",
                "predicate_id": predicate.get("id"),
                "step_id": predicate.get("step_id"),
                "args": _predicate_args(predicate),
                "condition": _predicate_condition_ref(predicate),
            }
    return None


def _predicate_supports_condition(predicate: dict[str, Any], condition_key: tuple[Any, ...]) -> bool:
    if not condition_key:
        return False
    predicate_args = _predicate_args(predicate)
    predicate_name = str(predicate.get("name") or "")
    condition_name = str(condition_key[0])
    condition_args = list(condition_key[1:])
    if predicate_name in {"hasRequiredCondition", "hasSafetyRequirement", "requiresInstalledBefore", "hasRequiredTool"}:
        return False
    if _norm(predicate_name) == _norm(condition_name):
        return predicate_args == condition_args or predicate_args[1:] == condition_args
    if _norm(condition_name) == "requirestool":
        return _predicate_supports_tool(predicate, condition_args)
    return False


def _predicate_supports_tool(predicate: dict[str, Any], condition_args: list[Any]) -> bool:
    if len(condition_args) != 1:
        return False
    required_tool = condition_args[0]
    args = _predicate_args(predicate)
    if len(args) < 2:
        return False
    name = str(predicate.get("name") or "")
    if name == "usesTool":
        return args[1] == required_tool
    if name in {"isA", "hasLabel"}:
        return args[1] == required_tool
    return False


def _is_requirement(constraint: dict[str, Any]) -> bool:
    return str(constraint.get("name") or "") in {"requires", "requiresSafety", "requiresTool"}


def _requires_previous_effect(constraint: dict[str, Any]) -> bool:
    return constraint.get("name") == "requires" and constraint.get("kind") == "inferred_precondition"


def _can_use_dependency_support(constraint: dict[str, Any]) -> bool:
    return str(constraint.get("name") or "") in {"requires", "requiresSafety"}


def _condition_key(constraint: dict[str, Any]) -> tuple[Any, ...]:
    args = _constraint_args(constraint)
    if not args:
        return ()
    if constraint.get("name") == "requiresTool" and len(args) > 1:
        return ("requiresTool", args[1])
    return tuple(args[1:])


def _condition_ref(item: dict[str, Any]) -> dict[str, Any]:
    if isinstance(item.get("condition"), dict):
        return dict(item["condition"])
    args = _constraint_args(item)
    if not args:
        return {"name": item.get("name"), "args": []}
    if item.get("name") == "requiresTool":
        return {"name": "requiresTool", "args": args[1:]}
    return {"name": args[1] if len(args) > 1 else item.get("name"), "args": args[2:]}


def _predicate_condition_ref(predicate: dict[str, Any]) -> dict[str, Any]:
    return {"name": predicate.get("name"), "args": _predicate_args(predicate)}


def _constraint_ref(constraint: dict[str, Any], *, support: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "constraint_id": constraint.get("constraint_id"),
        "name": constraint.get("name"),
        "kind": constraint.get("kind"),
        "args": _constraint_args(constraint),
        "conf": _parse_float(constraint.get("conf")),
        "rule_id": constraint.get("rule_id"),
        "support": support,
    }


def _same_step_constraint_support() -> dict[str, Any]:
    return dict(SAME_STEP_CONSTRAINT_SUPPORT)


def _predicate_ref(predicate: dict[str, Any]) -> dict[str, Any]:
    return {
        "predicate_id": predicate.get("id"),
        "name": predicate.get("name"),
        "predicate_key": predicate.get("predicate_key"),
        "category": predicate.get("category"),
        "args": _predicate_args(predicate),
        "conf": _parse_float(predicate.get("conf")),
        "source": predicate.get("source"),
        "notes": predicate.get("notes"),
    }


def _group_by_step(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        step_id = str(row.get("step_id") or "")
        if step_id:
            grouped.setdefault(step_id, []).append(row)
    return grouped


def _step_sort_key(step: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(step.get("clip_result_id") or ""),
        int(step.get("index") if step.get("index") is not None else 0),
        str(step.get("id") or ""),
    )


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    with open(path, newline="", encoding="utf-8") as f:
        return [_parse_csv_record(row) for row in csv.DictReader(f)]


def _load_validation_config(path: Path | None) -> dict[str, float]:
    if path is None:
        raise ValueError("Layer 4 validation config path is required")
    config = _load_config(Path(path))
    validation = config.get("validation")
    if not isinstance(validation, dict):
        raise ValueError(f"config missing validation thresholds: {path}")
    missing = [key for key in ("tau_acc", "tau_unc") if key not in validation]
    if missing:
        raise ValueError(f"config validation block missing: {', '.join(missing)}")
    return {
        "tau_acc": float(validation["tau_acc"]),
        "tau_unc": float(validation["tau_unc"]),
    }


def _load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return json.loads(text)
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"config must be a mapping: {path}")
    return loaded


def _parse_csv_record(row: dict[str, str]) -> dict[str, Any]:
    parsed: dict[str, Any] = dict(row)
    for key in ("args", "evidence_predicate_ids", "object_args", "evidence_predicates"):
        if parsed.get(key):
            parsed[key] = json.loads(parsed[key])
    for key in ("conf", "threshold"):
        if key in parsed:
            parsed[key] = _parse_float(parsed[key])
    return parsed


def _constraint_args(constraint: dict[str, Any]) -> list[Any]:
    args = constraint.get("args", [])
    if isinstance(args, str):
        return json.loads(args) if args else []
    return list(args or [])


def _predicate_args(predicate: dict[str, Any]) -> list[Any]:
    args = predicate.get("args", [])
    if isinstance(args, str):
        return json.loads(args) if args else []
    return list(args or [])


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_validation_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "step_id",
        "status",
        "confidence",
        "supported_requirements",
        "missing_requirements",
        "invalidated_effects",
        "dependency_support",
        "incompatibilities",
        "evidence_predicates",
        "evidence_constraints",
        "produced_effect_lifecycle",
        "warnings",
        "has_rule_coverage",
        "matched_rule_count",
        "produced_constraint_count",
        "has_expected_effect",
        "unsupported_action",
        "unsupported_action_name",
        "trace_id",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row.get(key), ensure_ascii=False, sort_keys=True)
                    if isinstance(row.get(key), (list, dict))
                    else row.get(key)
                    for key in fieldnames
                }
            )


def _parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    return str(value).strip().lower() in {"1", "true", "yes"}


def _json_or_empty_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    return json.loads(value)


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _norm(value: Any) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _is_remove_step(constraints: list[dict[str, Any]]) -> bool:
    for constraint in constraints:
        if constraint.get("name") != "produces":
            continue
        condition = _condition_ref(constraint)
        if condition.get("name") == "removed":
            return True
    return False


def _write_effect_history_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "step_id",
        "step_index",
        "status",
        "event",
        "condition_name",
        "condition_args",
        "constraint_id",
        "related_step_id",
        "active_after_step",
        "notes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
