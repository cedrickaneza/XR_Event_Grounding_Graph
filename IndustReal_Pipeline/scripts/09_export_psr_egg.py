#!/usr/bin/env python3
"""Convert CAD-grounded states into PSR outputs and EGG-style assembly graphs."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cad_catalog import load_cad_artifacts, load_procedure_info
from src.cad_reasoner import (
    direct_transition_steps_from_state_sequence,
    filter_steps_to_scored_slice,
    load_state_sequence,
    state_sequence_to_asd_frames,
)
from src.egg_builder import build_assembly_graph
from src.pilot_assets import load_latest_slice
from src.psr import run_psr
from src.raw_cad_config import RawCadPaths, load_raw_cad_config
from src.raw_loader import load_step_labels_csv


PSR_KWARGS = dict(
    implementation="expected",
    procedure="assy",
    cum_conf_threshold=8.0,
    cum_decay=0.75,
)


def _save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default))


def _graph_to_json(graph) -> dict:
    return {
        "clip": graph.clip,
        "n_frames": graph.n_frames,
        "events": [
            {
                "event_id": ev.event_id,
                "frame": ev.frame,
                "time_s": ev.time_s,
                "event_type": ev.event_type,
                "component": ev.component,
                "action_desc": ev.action_desc,
                "conf": ev.conf,
            }
            for ev in graph.events
        ],
        "component_states": graph.component_states,
    }


def _json_default(obj):
    try:
        import numpy as np

        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
    except ImportError:
        pass
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _normalize_gt_steps(rows: list[dict]) -> list[dict]:
    return [
        {
            "frame": int(row["frame_idx"]),
            "id": int(row["id"]),
            "description": row["description"],
            "conf": 1.0,
        }
        for row in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--slice-id", type=str, default=None)
    args = parser.parse_args()

    cfg = load_raw_cad_config(args.config)
    paths = RawCadPaths(cfg)
    latest = load_latest_slice(paths)
    slice_id = args.slice_id or latest["slice_id"]
    proc_info = load_procedure_info(ROOT / "configs" / "procedure_info.json")
    _, state_catalog = load_cad_artifacts(paths.slice_cad_dir(slice_id))
    slice_dir = paths.slice_workdir(slice_id)
    manifests_dir = paths.slice_manifests_dir(slice_id)

    outputs = []
    for clip_dir in sorted(paths.slice_results_dir(slice_id).iterdir()):
        if not clip_dir.is_dir() or clip_dir.name in {"cad", "manifests", "debug_visuals"}:
            continue
        clip = clip_dir.name
        state_path = clip_dir / "state_sequence.csv"
        if not state_path.exists():
            continue
        state_rows = load_state_sequence(state_path)
        asd_frames = state_sequence_to_asd_frames(state_rows, state_catalog=state_catalog)
        psr_pred = direct_transition_steps_from_state_sequence(state_rows, proc_info=proc_info)
        psr_pred_b3 = run_psr(asd_frames, proc_info, **PSR_KWARGS)
        psr_gt_full = load_step_labels_csv(slice_dir / clip / "PSR_labels_with_errors.csv")
        if not psr_gt_full:
            psr_gt_full = load_step_labels_csv(slice_dir / clip / "PSR_labels.csv")
        psr_gt_full = _normalize_gt_steps(psr_gt_full)
        psr_gt, score_start = filter_steps_to_scored_slice(psr_gt_full, state_rows=state_rows)
        n_frames = (max(int(row["frame_idx"]) for row in state_rows) + 1) if state_rows else 0
        graph = build_assembly_graph(clip, n_frames, psr_gt, proc_info)

        manifest_src = manifests_dir / f"{clip}_raw_manifest.csv"
        manifest_dst = clip_dir / "raw_manifest.csv"
        if manifest_src.exists() and not manifest_dst.exists():
            shutil.copy2(manifest_src, manifest_dst)

        with open(clip_dir / "psr_pred.json", "w") as f:
            json.dump(psr_pred, f, indent=2, default=_json_default)
        with open(clip_dir / "psr_pred_b3_diagnostic.json", "w") as f:
            json.dump(psr_pred_b3, f, indent=2, default=_json_default)
        with open(clip_dir / "psr_gt.json", "w") as f:
            json.dump(psr_gt, f, indent=2, default=_json_default)
        with open(clip_dir / "slice_gt_steps.json", "w") as f:
            json.dump(psr_gt, f, indent=2, default=_json_default)
        _save_json(clip_dir / "assembly_graph.json", _graph_to_json(graph))
        outputs.append(
            {
                "clip": clip,
                "psr_pred_steps": len(psr_pred),
                "psr_pred_b3_steps": len(psr_pred_b3),
                "psr_gt_steps": len(psr_gt),
                "score_start_frame": score_start,
                "assembly_graph_path": str(clip_dir / "assembly_graph.json"),
            }
        )

    print(json.dumps({"slice_id": slice_id, "outputs": outputs}, indent=2))


if __name__ == "__main__":
    main()
