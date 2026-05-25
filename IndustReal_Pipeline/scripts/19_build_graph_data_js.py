#!/usr/bin/env python3
"""Build platform/data/graph-data.js for the IndustReal UI.

Reads from:
  results/<clip_id>_results.json          — n_frames, duration_s, metrics, gt_steps
  results/neo4j/<run_id>/nodes_phases.csv  — phase metadata
  results/neo4j/<run_id>/nodes_goals.csv   — goal metadata
  results/neo4j/<run_id>/nodes_components.csv — global component list
  results/neo4j/<run_id>/nodes_events.csv  — event_type, component name per event
  results/neo4j/<run_id>/edges_event_component.csv — event → component_id mapping
  results/neo4j/<run_id>/edges_phase_step.csv      — phase → event mapping

Writes:
  platform/data/graph-data.js             — window.INDUSTREAL_DATA = {...}
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NEO4J_RUN_ID = "raw_cad_dataset__all_test_clips"
DEFAULT_MODE = "od_only"
DEFAULT_ARCHIVE = "test_p1"
DEFAULT_OUTPUT = ROOT.parent / "platform" / "data" / "graph-data.js"


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _strip_neo4j_type(header: str) -> str:
    return re.sub(r":(int|float|boolean|ID\([^)]+\)|LABEL)$", "", header)


def _clean_row(row: dict) -> dict:
    return {_strip_neo4j_type(k): v for k, v in row.items()}


def _parse_int(v: str | None, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _parse_float(v: str | None, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_json_list(v: str | None) -> list:
    if not v:
        return []
    try:
        result = json.loads(v)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _clip_result_id(run_id: str, mode: str, archive: str, clip: str) -> str:
    return f"{run_id}::{mode}::{archive}::{clip}"


def _event_kg_id(run_id: str, mode: str, archive: str, clip: str, local_id: int) -> str:
    return f"{_clip_result_id(run_id, mode, archive, clip)}::event_{local_id}"


def _phase_key_from_phase_id(phase_id: str) -> str:
    part = phase_id.split("::")[-1]
    return part.removeprefix("phase_")


def build_graph_data(
    neo4j_dir: Path,
    results_dir: Path,
    run_id: str = NEO4J_RUN_ID,
    mode: str = DEFAULT_MODE,
    archive: str = DEFAULT_ARCHIVE,
) -> dict:
    # ── Load Neo4j CSVs ───────────────────────────────────────────────────────
    components_rows = [_clean_row(r) for r in _read_csv(neo4j_dir / "nodes_components.csv")]
    events_rows = [_clean_row(r) for r in _read_csv(neo4j_dir / "nodes_events.csv")]
    phases_rows = [_clean_row(r) for r in _read_csv(neo4j_dir / "nodes_phases.csv")]
    goals_rows = [_clean_row(r) for r in _read_csv(neo4j_dir / "nodes_goals.csv")]
    edge_event_comp = [_clean_row(r) for r in _read_csv(neo4j_dir / "edges_event_component.csv")]
    edge_phase_step = [_clean_row(r) for r in _read_csv(neo4j_dir / "edges_phase_step.csv")]

    # Index by event_id
    kg_event_by_id: dict[str, dict] = {}
    for r in events_rows:
        eid = r.get("event_id") or r.get(":ID(AssemblyEvent)", "")
        if eid:
            kg_event_by_id[eid] = r

    # event_id → component_id
    comp_by_event: dict[str, str] = {}
    for r in edge_event_comp:
        eid = r.get(":START_ID(AssemblyEvent)", "")
        cid = r.get(":END_ID(Component)", "")
        if eid and cid:
            comp_by_event[eid] = cid

    # event_id → phase_key
    phase_by_event: dict[str, str] = {}
    for r in edge_phase_step:
        phase_id = r.get(":START_ID(AssemblyPhase)", "")
        eid = r.get(":END_ID(AssemblyEvent)", "")
        if phase_id and eid:
            phase_by_event[eid] = _phase_key_from_phase_id(phase_id)

    # ── Global components list ────────────────────────────────────────────────
    components = []
    for r in sorted(components_rows, key=lambda x: x.get("name", "")):
        cid = r.get("component_id") or r.get(":ID(Component)", "")
        components.append({
            "id": cid,
            "name": r.get("name", ""),
            "normalized": r.get("normalized_name", ""),
        })

    # ── Per-clip data ─────────────────────────────────────────────────────────
    clips: dict[str, dict] = {}
    result_paths = sorted(results_dir.glob("*_results.json"))

    for rp in result_paths:
        clip_id = rp.stem.replace("_results", "")
        data = json.loads(rp.read_text())
        cr_id = _clip_result_id(run_id, mode, archive, clip_id)

        # Phases for this clip
        clip_phases = [r for r in phases_rows if r.get("clip_result_id", "") == cr_id]
        phases = sorted(
            [
                {
                    "id": r.get("phase_key", ""),
                    "name": r.get("phase_name", "") or r.get("name", ""),
                    "order": _parse_int(r.get("phase_order")),
                    "first_frame": _parse_int(r.get("first_frame")),
                    "last_frame": _parse_int(r.get("last_frame")),
                    "step_count": _parse_int(r.get("step_count")),
                    "status": r.get("status", ""),
                }
                for r in clip_phases
            ],
            key=lambda x: x["order"],
        )

        # Goal for this clip
        clip_goal_rows = [r for r in goals_rows if r.get("clip_result_id", "") == cr_id]
        goal: dict = {}
        if clip_goal_rows:
            gr = clip_goal_rows[0]
            target_components_raw = gr.get("target_components", "")
            target_components = _parse_json_list(target_components_raw)
            goal = {
                "id": f"goal::{clip_id}",
                "name": gr.get("goal_name", "") or gr.get("name", ""),
                "target_state_index": _parse_int(gr.get("target_state_index")),
                "target_state_name": gr.get("target_state_name", ""),
                "target_state_asset": gr.get("target_state_asset", ""),
                "target_components": target_components,
            }

        # Events from gt_steps, enriched with KG metadata
        gt_steps = data.get("gt_steps", [])
        events = []
        for local_id, step in enumerate(gt_steps):
            kg_id = _event_kg_id(run_id, mode, archive, clip_id, local_id)
            kg_ev = kg_event_by_id.get(kg_id, {})
            component = kg_ev.get("component", "")
            component_id = comp_by_event.get(kg_id, "")
            event_type = kg_ev.get("event_type", "INSTALL")
            phase_key = phase_by_event.get(kg_id, "")
            events.append({
                "id": f"event::{clip_id}::{local_id}",
                "local_id": local_id,
                "step_id": int(step.get("id", 0)),
                "frame": int(step.get("frame", 0)),
                "time_s": float(step.get("time_s", 0.0)),
                "event_type": event_type,
                "component": component,
                "component_id": component_id,
                "action_desc": str(step.get("description", "")),
                "conf": float(step.get("conf", 1.0)),
                "phase_key": phase_key,
            })

        components_in_clip = sorted({ev["component"] for ev in events if ev["component"]})

        metrics_raw = data.get("metrics", {})
        metrics = {
            "pos": float(metrics_raw.get("pos", 0.0)),
            "f1": float(metrics_raw.get("f1", 0.0)),
            "avg_delay_s": (
                float(metrics_raw["avg_delay_s"])
                if metrics_raw.get("avg_delay_s") is not None
                else None
            ),
            "system_TPs": int(metrics_raw.get("system_TPs", 0)),
            "system_FPs": int(metrics_raw.get("system_FPs", 0)),
            "system_FNs": int(metrics_raw.get("system_FNs", 0)),
        }

        clips[clip_id] = {
            "id": clip_id,
            "duration_s": float(data.get("duration_s", 0.0)),
            "n_frames": int(data.get("n_frames", 0)),
            "metrics": metrics,
            "goal": goal,
            "phases": phases,
            "events": events,
            "components_in_clip": components_in_clip,
        }

    default_clip = next(iter(clips)) if clips else ""

    return {
        "default_clip": default_clip,
        "components": components,
        "clips": clips,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--neo4j-dir", type=Path,
                        default=ROOT / "results" / "neo4j" / NEO4J_RUN_ID)
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-id", type=str, default=NEO4J_RUN_ID)
    parser.add_argument("--mode", type=str, default=DEFAULT_MODE)
    parser.add_argument("--archive", type=str, default=DEFAULT_ARCHIVE)
    args = parser.parse_args()

    data = build_graph_data(
        neo4j_dir=args.neo4j_dir,
        results_dir=args.results_dir,
        run_id=args.run_id,
        mode=args.mode,
        archive=args.archive,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    js_payload = json.dumps(data, indent=2, ensure_ascii=False)
    args.output.write_text(f"window.INDUSTREAL_DATA = {js_payload};\n", encoding="utf-8")

    clip_count = len(data["clips"])
    event_count = sum(len(c["events"]) for c in data["clips"].values())
    print(json.dumps({
        "output": str(args.output),
        "clips": clip_count,
        "total_events": event_count,
        "default_clip": data["default_clip"],
    }, indent=2))


if __name__ == "__main__":
    main()
