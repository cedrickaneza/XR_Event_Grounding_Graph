"""Layer 3 rule-based procedural inference for thesis reasoning outputs."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES_PATH = ROOT / "config" / "thesis_rules.yaml"

CONSTRAINT_FIELDS = [
    "constraint_id",
    "step_id",
    "name",
    "kind",
    "args",
    "conf",
    "rule_id",
    "rule_type",
    "threshold",
    "aggregation",
    "evidence_predicate_ids",
    "status",
]

RULE_COVERAGE_FIELDS = [
    "step_id",
    "step_index",
    "action_name",
    "object_args",
    "predicate_count",
    "matched_rule_count",
    "produced_constraint_count",
    "has_expected_effect",
    "has_requirement",
    "has_incompatibility",
    "has_meaningful_evidence",
    "has_rule_coverage",
    "warning_code",
    "warning_message",
    "evidence_predicates",
    "suggested_fix",
]


@dataclass(frozen=True)
class Layer3Inputs:
    step_records_path: Path
    predicates_path: Path
    rules_path: Path
    output_path: Path


def run_layer3_inference(inputs: Layer3Inputs) -> dict[str, Any]:
    steps = _read_records(Path(inputs.step_records_path))
    predicates = _read_records(Path(inputs.predicates_path))
    config = _load_rule_config(Path(inputs.rules_path))
    defaults = config.get("defaults", {})
    aliases = _predicate_aliases(config)
    predicates = [_normalize_predicate_record(predicate, aliases) for predicate in predicates]
    rules = [_normalize_rule(rule, aliases) for rule in list(config.get("rules", []))]

    predicates_by_step: dict[str, list[dict[str, Any]]] = {}
    for predicate in predicates:
        step_id = str(predicate.get("step_id") or "")
        if step_id:
            predicates_by_step.setdefault(step_id, []).append(predicate)

    constraints: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    inference_rules = [rule for rule in rules if str(rule.get("type")) != "compatibility"]
    compatibility_rules = [rule for rule in rules if str(rule.get("type")) == "compatibility"]
    for step in steps:
        step_id = str(step.get("id") or step.get("step_id") or "")
        if not step_id:
            continue
        step_predicates = predicates_by_step.get(step_id, [])
        step_constraints: list[dict[str, Any]] = []
        for rule in inference_rules:
            step_constraints.extend(
                _apply_inference_rule(
                    step_id,
                    step_predicates,
                    rule,
                    default_threshold=float(defaults.get("threshold", 0.0)),
                    default_aggregation=str(defaults.get("aggregation", "min")),
                )
            )
        for rule in compatibility_rules:
            step_constraints.extend(
                _apply_compatibility_rule(
                    step_id,
                    step_predicates,
                    rule,
                    default_aggregation=str(defaults.get("aggregation", "min")),
                )
            )
        constraints.extend(step_constraints)
        diagnostics.append(_rule_coverage_diagnostic(step, step_predicates, step_constraints))

    _write_constraints_csv(Path(inputs.output_path), constraints)
    diagnostics_path = Path(inputs.output_path).with_name("rule_coverage_diagnostics.csv")
    _write_rule_coverage_csv(diagnostics_path, diagnostics)
    return {
        "step_records": len(steps),
        "predicates": len(predicates),
        "rules": len(rules),
        "constraints": len(constraints),
        "output_path": str(inputs.output_path),
        "rule_coverage_diagnostics_path": str(diagnostics_path),
        "rule_coverage_warnings": sum(1 for row in diagnostics if row.get("warning_code")),
        "constraints_by_kind": _count_by(constraints, "kind"),
        "constraints_by_rule_type": _count_by(constraints, "rule_type"),
        "constraints_by_rule": _count_by(constraints, "rule_id"),
    }


def _apply_inference_rule(
    step_id: str,
    predicates: list[dict[str, Any]],
    rule: dict[str, Any],
    *,
    default_threshold: float,
    default_aggregation: str,
) -> list[dict[str, Any]]:
    antecedents = list(rule.get("antecedents", []))
    constraint_templates = _rule_constraint_templates(rule)
    threshold = float(rule.get("threshold", default_threshold))
    aggregation = str(rule.get("aggregation", default_aggregation))
    matches = _find_matches(antecedents, predicates)
    constraints: list[dict[str, Any]] = []
    for match_idx, match in enumerate(matches):
        evidence = list(match["evidence"])
        conf = _aggregate_confidence(evidence, aggregation)
        if conf is None or conf < threshold:
            continue
        bindings = dict(match["bindings"])
        constraints.extend(
            _instantiate_constraints(
                step_id,
                rule,
                constraint_templates,
                bindings,
                evidence,
                conf=conf,
                threshold=threshold,
                aggregation=aggregation,
                match_idx=match_idx,
                status="inferred",
            )
        )
    return constraints


def _apply_compatibility_rule(
    step_id: str,
    predicates: list[dict[str, Any]],
    rule: dict[str, Any],
    *,
    default_aggregation: str,
) -> list[dict[str, Any]]:
    antecedents = list(rule.get("antecedents", []))
    constraint_templates = _rule_constraint_templates(rule)
    aggregation = str(rule.get("aggregation", default_aggregation))
    matches = _find_matches(antecedents, predicates)
    constraints: list[dict[str, Any]] = []
    for match_idx, match in enumerate(matches):
        evidence = list(match["evidence"])
        conf = _aggregate_confidence(evidence, aggregation)
        bindings = dict(match["bindings"])
        constraints.extend(
            _instantiate_constraints(
                step_id,
                rule,
                constraint_templates,
                bindings,
                evidence,
                conf=conf,
                threshold=_parse_float(rule.get("threshold")),
                aggregation=aggregation,
                match_idx=match_idx,
                status="incompatibility",
            )
        )
    return constraints


def _rule_constraint_templates(rule: dict[str, Any]) -> list[dict[str, Any]]:
    templates = list(rule.get("constraints", []))
    if not templates:
        raise ValueError(f"rule must define one or more constraints: {rule.get('id')}")
    return templates


def _instantiate_constraints(
    step_id: str,
    rule: dict[str, Any],
    constraint_templates: list[dict[str, Any]],
    bindings: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    conf: float | None,
    threshold: float | None,
    aggregation: str,
    match_idx: int,
    status: str,
) -> list[dict[str, Any]]:
    constraints = []
    for template_idx, template in enumerate(constraint_templates):
        name = str(template["name"])
        kind = str(template.get("kind") or rule.get("type") or "constraint")
        args = [_instantiate_arg(arg, bindings) for arg in template.get("args", [])]
        constraints.append(
            {
                "constraint_id": _constraint_id(step_id, str(rule["id"]), match_idx, template_idx, name, args),
                "step_id": step_id,
                "name": name,
                "kind": kind,
                "args": json.dumps(args, ensure_ascii=False),
                "conf": "" if conf is None else f"{conf:.6g}",
                "rule_id": str(rule["id"]),
                "rule_type": str(rule.get("type") or "inference"),
                "threshold": "" if threshold is None else f"{threshold:.6g}",
                "aggregation": aggregation,
                "evidence_predicate_ids": json.dumps([str(item.get("id")) for item in evidence], ensure_ascii=False),
                "status": status,
            }
        )
    return constraints


def _rule_coverage_diagnostic(
    step: dict[str, Any],
    predicates: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> dict[str, Any]:
    step_id = str(step.get("id") or step.get("step_id") or "")
    action_predicates = [item for item in predicates if item.get("name") == "hasAction"]
    object_predicates = [item for item in predicates if item.get("name") in {"usesObject", "usesTool"}]
    action_name = _first_action_name(action_predicates)
    object_args = _object_args(object_predicates)
    has_meaningful_evidence = bool(action_predicates and object_predicates)
    matched_rules = sorted({str(item.get("rule_id")) for item in constraints if item.get("rule_id")})
    has_expected_effect = any(item.get("name") == "produces" for item in constraints)
    has_requirement = any(str(item.get("name") or "") in {"requires", "requiresTool", "requiresSafety"} for item in constraints)
    has_incompatibility = any(
        item.get("name") == "incompatibleAction" or item.get("status") == "incompatibility"
        for item in constraints
    )
    warning_code = ""
    warning_message = ""
    suggested_fix = ""
    if has_meaningful_evidence and not constraints:
        warning_code = "no_applicable_rule"
        warning_message = "Step has predicate evidence but no Layer 3 rule produced constraints."
        suggested_fix = "Add an explicit rule for this action or treat it as unsupported in the domain model."
    return {
        "step_id": step_id,
        "step_index": step.get("index"),
        "action_name": action_name,
        "object_args": json.dumps(object_args, ensure_ascii=False),
        "predicate_count": len(predicates),
        "matched_rule_count": len(matched_rules),
        "produced_constraint_count": len(constraints),
        "has_expected_effect": str(has_expected_effect).lower(),
        "has_requirement": str(has_requirement).lower(),
        "has_incompatibility": str(has_incompatibility).lower(),
        "has_meaningful_evidence": str(has_meaningful_evidence).lower(),
        "has_rule_coverage": str(bool(constraints)).lower(),
        "warning_code": warning_code,
        "warning_message": warning_message,
        "evidence_predicates": json.dumps(_compact_evidence_predicates(action_predicates + object_predicates), ensure_ascii=False),
        "suggested_fix": suggested_fix,
    }


def _first_action_name(predicates: list[dict[str, Any]]) -> str:
    for predicate in predicates:
        args = _predicate_args(predicate)
        if len(args) >= 2:
            return str(args[1])
    return ""


def _object_args(predicates: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for predicate in predicates:
        args = _predicate_args(predicate)
        if len(args) >= 2:
            output.append(str(args[1]))
    return output


def _compact_evidence_predicates(predicates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "args": _predicate_args(item),
        }
        for item in predicates
    ]


def _find_matches(antecedents: list[dict[str, Any]], predicates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = [{"bindings": {}, "evidence": []}]
    for antecedent in antecedents:
        next_matches = []
        for partial in matches:
            for predicate in predicates:
                updated = _match_antecedent(antecedent, predicate, dict(partial["bindings"]))
                if updated is None:
                    continue
                next_matches.append(
                    {
                        "bindings": updated,
                        "evidence": list(partial["evidence"]) + [predicate],
                    }
                )
        matches = next_matches
        if not matches:
            break
    return _dedupe_matches(matches)


def _match_antecedent(
    antecedent: dict[str, Any],
    predicate: dict[str, Any],
    bindings: dict[str, Any],
) -> dict[str, Any] | None:
    if str(predicate.get("name")) != str(antecedent.get("name")):
        return None
    pattern_args = list(antecedent.get("args", []))
    predicate_args = _predicate_args(predicate)
    if len(pattern_args) != len(predicate_args):
        return None
    for pattern_arg, predicate_arg in zip(pattern_args, predicate_args):
        if isinstance(pattern_arg, str) and pattern_arg.startswith("?"):
            existing = bindings.get(pattern_arg)
            if pattern_arg in bindings and existing != predicate_arg:
                return None
            bindings[pattern_arg] = predicate_arg
        elif pattern_arg != predicate_arg:
            return None
    return bindings


def _predicate_args(predicate: dict[str, Any]) -> list[Any]:
    args = predicate.get("args", [])
    if isinstance(args, str):
        if not args:
            return []
        return json.loads(args)
    return list(args or [])


def _aggregate_confidence(evidence: list[dict[str, Any]], aggregation: str) -> float | None:
    confidences = [_parse_float(item.get("conf")) for item in evidence]
    confidences = [value for value in confidences if value is not None]
    if not confidences:
        return None
    if aggregation != "min":
        raise ValueError(f"unsupported Layer 3 aggregation: {aggregation}")
    return min(confidences)


def _instantiate_arg(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("?"):
        return bindings.get(value)
    return value


def _dedupe_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    output = []
    for match in matches:
        evidence_ids = tuple(str(item.get("id")) for item in match["evidence"])
        bindings = tuple(sorted((key, json.dumps(value, sort_keys=True)) for key, value in match["bindings"].items()))
        key = (evidence_ids, bindings)
        if key in seen:
            continue
        seen.add(key)
        output.append(match)
    return output


def _constraint_id(step_id: str, rule_id: str, match_idx: int, template_idx: int, name: str, args: list[Any]) -> str:
    safe_rule = _safe_id(rule_id)
    safe_name = _safe_id(name)
    safe_args = _safe_id("_".join(str(arg) for arg in args if arg is not None))[:80]
    return f"{step_id}::c::{safe_rule}::{match_idx}_{template_idx}::{safe_name}::{safe_args}"


def _safe_id(value: str) -> str:
    chars = []
    for char in str(value).lower():
        chars.append(char if char.isalnum() else "_")
    return "_".join("".join(chars).split("_")) or "unknown"


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    with open(path, newline="", encoding="utf-8") as f:
        rows = []
        for row in csv.DictReader(f):
            rows.append(_parse_csv_record(row))
        return rows


def _parse_csv_record(row: dict[str, str]) -> dict[str, Any]:
    parsed: dict[str, Any] = dict(row)
    for key in ("args", "source", "objects", "source_descriptions", "available_evidence_files", "missing_inputs", "provenance", "sequence", "time_window", "action"):
        if key in parsed and parsed[key]:
            try:
                parsed[key] = json.loads(parsed[key])
            except json.JSONDecodeError:
                pass
    for key in ("conf", "confidence"):
        if key in parsed:
            parsed[key] = _parse_float(parsed[key])
    return parsed


def _load_rule_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return json.loads(text)
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"rule config must be a mapping: {path}")
    _validate_rule_config(loaded, path)
    return loaded


def _predicate_aliases(config: dict[str, Any]) -> dict[str, str]:
    aliases = config.get("predicate_aliases", {})
    if not isinstance(aliases, dict):
        return {}
    return {str(key): str(value) for key, value in aliases.items()}


def _canonical_predicate_name(name: Any, aliases: dict[str, str]) -> str:
    current = str(name)
    seen: set[str] = set()
    while current in aliases and current not in seen:
        seen.add(current)
        current = aliases[current]
    return current


def _normalize_predicate_record(predicate: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    name = _canonical_predicate_name(predicate.get("name"), aliases)
    if name == predicate.get("name"):
        return predicate
    return {**predicate, "name": name}


def _normalize_rule(rule: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    normalized = dict(rule)
    normalized["antecedents"] = [
        {**antecedent, "name": _canonical_predicate_name(antecedent.get("name"), aliases)}
        for antecedent in list(rule.get("antecedents", []))
    ]
    normalized["constraints"] = [
        {**constraint, "name": _canonical_predicate_name(constraint.get("name"), aliases)}
        for constraint in list(rule.get("constraints", []))
    ]
    return normalized


def _validate_rule_config(config: dict[str, Any], path: Path) -> None:
    vocabulary = config.get("predicate_vocabulary", {})
    if not isinstance(vocabulary, dict):
        raise ValueError(f"rule config predicate_vocabulary must be a mapping: {path}")
    aliases = _predicate_aliases(config)
    for rule in list(config.get("rules", [])):
        rule_id = str(rule.get("id") or "<unknown>")
        for idx, antecedent in enumerate(list(rule.get("antecedents", []))):
            _validate_predicate_pattern(
                antecedent,
                vocabulary,
                aliases,
                f"rules.{rule_id}.antecedents.{idx}",
            )
        for idx, constraint in enumerate(list(rule.get("constraints", []))):
            _validate_predicate_pattern(
                constraint,
                vocabulary,
                aliases,
                f"rules.{rule_id}.constraints.{idx}",
            )


def _validate_predicate_pattern(
    pattern: dict[str, Any],
    vocabulary: dict[str, Any],
    aliases: dict[str, str],
    location: str,
) -> None:
    name = _canonical_predicate_name(pattern.get("name"), aliases)
    if name not in vocabulary:
        raise ValueError(f"unknown predicate '{pattern.get('name')}' at {location}")
    vocab_entry = vocabulary.get(name)
    expected_arity = int(vocab_entry.get("arity")) if isinstance(vocab_entry, dict) else None
    actual_arity = len(list(pattern.get("args", []) or []))
    if expected_arity is not None and actual_arity != expected_arity:
        raise ValueError(f"predicate '{name}' at {location} has arity {actual_arity}, expected {expected_arity}")


def _write_constraints_csv(path: Path, constraints: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CONSTRAINT_FIELDS)
        writer.writeheader()
        writer.writerows(constraints)


def _write_rule_coverage_csv(path: Path, diagnostics: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RULE_COVERAGE_FIELDS)
        writer.writeheader()
        writer.writerows(diagnostics)


def _parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
