from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "21_evaluate_constraint_inference_coverage.py"
SPEC = importlib.util.spec_from_file_location("evaluation2", SCRIPT_PATH)
evaluation2 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = evaluation2
SPEC.loader.exec_module(evaluation2)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def make_context(tmp_path: Path, *, all_available: bool = False, clip_ids: list[str] | None = None) -> evaluation2.EvaluationContext:
    return evaluation2.EvaluationContext(
        project_root=tmp_path,
        results_root=tmp_path / "results" / "reasoning_layers",
        output_dir=tmp_path / "docs" / "reasoning_layers" / "Evaluation2",
        clip_result_ids=clip_ids or ["clip_a"],
        all_available=all_available,
    )


def write_reasoning_artifacts(
    root: Path,
    clip_id: str = "clip_a",
    *,
    include_remove_constraints: bool = True,
    invalid_confidence: bool = False,
    incompatibility: bool = False,
) -> Path:
    clip_dir = root / clip_id
    steps = [
        {
            "id": "step_0",
            "index": 0,
            "run_id": "run",
            "mode": "od_only",
            "archive_name": "test_p1",
            "clip": "03_assy_0_1",
            "action": {"name": "install"},
            "objects": [{"type": "base"}],
        },
        {
            "id": "step_1",
            "index": 1,
            "run_id": "run",
            "mode": "od_only",
            "archive_name": "test_p1",
            "clip": "03_assy_0_1",
            "action": {"name": "remove"},
            "objects": [{"type": "base"}],
        },
    ]
    write_jsonl(clip_dir / "step_records.jsonl", steps)
    predicates = [
        {"id": "p0", "step_id": "step_0", "name": "hasAction", "args": ["step_0", "install"], "conf": 1.0},
        {"id": "p1", "step_id": "step_0", "name": "usesObject", "args": ["step_0", "base"], "conf": 1.0},
        {"id": "p2", "step_id": "step_1", "name": "hasAction", "args": ["step_1", "remove"], "conf": 1.0},
        {"id": "p3", "step_id": "step_1", "name": "usesObject", "args": ["step_1", "base"], "conf": 1.0},
    ]
    write_jsonl(clip_dir / "predicates.jsonl", predicates)
    constraints = [
        {
            "constraint_id": "c0",
            "step_id": "step_0",
            "name": "produces",
            "kind": "expected_effect",
            "args": json.dumps(["step_0", "installed", "base", "workspace"]),
            "conf": "1.0",
            "rule_id": "effect_install",
        },
        {
            "constraint_id": "c1",
            "step_id": "step_1",
            "name": "requires",
            "kind": "inferred_precondition",
            "args": json.dumps(["step_1", "installed", "base", "workspace"]),
            "conf": "1.2" if invalid_confidence else "0.9",
            "rule_id": "remove_requires_installed",
        },
    ]
    if include_remove_constraints:
        constraints.append(
            {
                "constraint_id": "c2",
                "step_id": "step_1",
                "name": "produces",
                "kind": "expected_effect",
                "args": json.dumps(["step_1", "removed", "base", "workspace"]),
                "conf": "0.9",
                "rule_id": "remove_produces_removed",
            }
        )
    if incompatibility:
        constraints.append(
            {
                "constraint_id": "c3",
                "step_id": "step_0",
                "name": "incompatibleAction",
                "kind": "compatibility",
                "args": json.dumps(["step_0", "base", "error"]),
                "conf": "0.8",
                "rule_id": "incompatibility_rule",
            }
        )
    write_csv(
        clip_dir / "inferred_constraints.csv",
        constraints,
        ["constraint_id", "step_id", "name", "kind", "args", "conf", "rule_id"],
    )
    diagnostics = [
        {
            "step_id": "step_0",
            "step_index": "0",
            "action_name": "install",
            "object_args": json.dumps(["base"]),
            "predicate_count": "2",
            "matched_rule_count": "1",
            "produced_constraint_count": "1",
            "has_rule_coverage": "true",
            "warning_code": "",
        },
        {
            "step_id": "step_1",
            "step_index": "1",
            "action_name": "remove",
            "object_args": json.dumps(["base"]),
            "predicate_count": "2",
            "matched_rule_count": "2" if include_remove_constraints else "0",
            "produced_constraint_count": "2" if include_remove_constraints else "1",
            "has_rule_coverage": "true",
            "warning_code": "",
        },
    ]
    write_csv(
        clip_dir / "rule_coverage_diagnostics.csv",
        diagnostics,
        [
            "step_id",
            "step_index",
            "action_name",
            "object_args",
            "predicate_count",
            "matched_rule_count",
            "produced_constraint_count",
            "has_rule_coverage",
            "warning_code",
        ],
    )
    return clip_dir


def checks_by_name(result: dict) -> dict[str, dict]:
    return {row["check_name"]: row for row in result["checks"]}


def test_constraint_counts_are_computed_correctly(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_reasoning_artifacts(ctx.results_root)
    result = evaluation2.evaluate(ctx)
    row = result["clips"][0]["coverage_row"]
    assert row["predicate_count"] == 4
    assert row["requires_count"] == 1
    assert row["produces_count"] == 2
    assert row["requires_tool_count"] == 0
    assert row["requires_safety_count"] == 0


def test_confidence_validation_catches_values_outside_range(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_reasoning_artifacts(ctx.results_root, invalid_confidence=True)
    result = evaluation2.evaluate(ctx)
    assert checks_by_name(result)["Confidence values valid"]["status"] == "FAIL"
    rows = read_csv(ctx.output_dir / "confidence_validation_results.csv")
    assert any(row["status"] == "FAIL" for row in rows)


def test_rule_coverage_diagnostics_are_summarized_correctly(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_reasoning_artifacts(ctx.results_root)
    result = evaluation2.evaluate(ctx)
    rows = read_csv(ctx.output_dir / "rule_coverage_summary.csv")
    remove = next(row for row in rows if row["action_name"] == "remove")
    assert remove["covered_step_count"] == "1"
    assert remove["matched_rule_count_sum"] == "2"
    assert result["clips"][0]["counts"]["rule_coverage"]["matched_rule_count_distribution"] == {"1": 1, "2": 1}


def test_remove_step_with_requires_installed_and_produces_removed_is_detected(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_reasoning_artifacts(ctx.results_root)
    result = evaluation2.evaluate(ctx)
    remove = result["clips"][0]["remove_rows"][0]
    assert remove["has_requires_installed"] is True
    assert remove["has_produces_removed"] is True
    assert checks_by_name(result)["Remove-action semantics covered"]["status"] == "PASS"


def test_remove_step_without_expected_remove_constraints_is_warning(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_reasoning_artifacts(ctx.results_root, include_remove_constraints=False)
    result = evaluation2.evaluate(ctx)
    assert checks_by_name(result)["Remove-action semantics covered"]["status"] == "WARNING"
    remove = result["clips"][0]["remove_rows"][0]
    assert "missing remove produces" in remove["notes"]


def test_missing_clip_result_folder_creates_missing_data_report(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, clip_ids=["missing_clip"])
    result = evaluation2.evaluate(ctx)
    assert result["missing"]
    assert (ctx.output_dir / "missing_data_report.md").exists()


def test_zero_incompatibility_constraints_does_not_fail_evaluation(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    write_reasoning_artifacts(ctx.results_root, incompatibility=False)
    result = evaluation2.evaluate(ctx)
    assert result["clips"][0]["coverage_row"]["incompatibility_count"] == 0
    assert checks_by_name(result)["Natural incompatibility coverage reported"]["status"] == "PASS"


def test_all_available_skips_folders_without_required_files_and_reports_them(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, all_available=True, clip_ids=[])
    write_reasoning_artifacts(ctx.results_root, clip_id="complete_clip")
    incomplete = ctx.results_root / "incomplete_clip"
    incomplete.mkdir(parents=True)
    (incomplete / "step_records.jsonl").write_text("", encoding="utf-8")
    result = evaluation2.evaluate(ctx)
    assert result["evaluated_clip_count"] == 1
    assert result["skipped"]
    assert (ctx.output_dir / "missing_data_report.md").exists()
    assert checks_by_name(result)["All-available discovery"]["status"] == "WARNING"
