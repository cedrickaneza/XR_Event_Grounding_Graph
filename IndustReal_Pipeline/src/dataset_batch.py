"""Whole-dataset oracle batch processing helpers for IndustReal."""
from __future__ import annotations

import csv
import json
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd

from .cad_catalog import (
    build_cad_part_catalog,
    build_state_catalog,
    load_procedure_info,
    save_cad_artifacts,
)
from .cad_reasoner import (
    direct_transition_steps_from_state_sequence,
    load_state_sequence,
    oracle_state_sequence_from_labels,
    save_state_sequence,
    state_sequence_to_asd_frames,
)
from .egg_builder import build_assembly_graph
from .eval_raw_cad import evaluate_state_predictions, step_metrics
from .pilot_assets import build_slice_windows
from .psr import evaluate, run_psr
from .raw_cad_config import ROOT, RawCadPaths, configure_runtime_environment, resolve_path
from .raw_loader import ROOT_METADATA_FILES, STREAM_NAMES, frame_name_to_idx, load_od_labels, load_step_labels_csv
from .raw_manifest import build_raw_manifest, save_manifest, save_manifest_report, validate_raw_manifest
from .track2d import load_jsonl, save_jsonl, smooth_frame_evidence
from .detector_rgb import run_detector_for_clip


PSR_KWARGS = dict(
    implementation="expected",
    procedure="assy",
    cum_conf_threshold=8.0,
    cum_decay=0.75,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_run_id(cfg: dict[str, Any]) -> str:
    return f"{cfg['batch']['name']}__{cfg['batch']['scope']}"


def oracle_mode_settings(run_mode: str) -> dict[str, Any]:
    if run_mode == "od_only":
        return {"include_error_step_hints": False}
    if run_mode == "od_plus_psr_error_hints":
        return {"include_error_step_hints": True}
    raise ValueError(f"unknown oracle run mode: {run_mode}")


def _clip_member_map(names: list[str], clip_name: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in names:
        if name.endswith("/"):
            continue
        parts = PurePosixPath(name).parts
        if clip_name not in parts:
            continue
        idx = parts.index(clip_name)
        rel = PurePosixPath(*parts[idx + 1 :]).as_posix()
        if rel:
            mapping[rel] = name
    return mapping


def _rgb_frame_indices_from_member_map(member_map: dict[str, str]) -> list[int]:
    indices = []
    for rel in member_map:
        parts = PurePosixPath(rel).parts
        if len(parts) == 2 and parts[0] == "rgb" and parts[1].endswith(".jpg"):
            indices.append(frame_name_to_idx(parts[1]))
    return sorted(indices)


def _stream_count_from_member_map(member_map: dict[str, str], stream: str) -> int:
    return sum(
        1
        for rel in member_map
        if rel.startswith(f"{stream}/") and rel.endswith(".jpg")
    )


def _metadata_present_from_member_map(member_map: dict[str, str]) -> dict[str, bool]:
    return {
        name: name in member_map
        for name in ROOT_METADATA_FILES
    }


def _normalize_gt_steps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "frame": int(row["frame_idx"]),
            "id": int(row["id"]),
            "description": str(row["description"]),
            "conf": 1.0,
        }
        for row in rows
    ]


def _json_default(obj: Any) -> Any:
    try:
        import numpy as np

        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
    except ImportError:
        pass
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default))


def _graph_to_json(graph: Any) -> dict[str, Any]:
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


def maybe_download_archive(archive_cfg: dict[str, Any], *, allow_download: bool) -> Path:
    local_path = resolve_path(str(archive_cfg["local_path"]), base=ROOT)
    if local_path.exists():
        return local_path
    if not allow_download:
        raise FileNotFoundError(
            f"missing archive {archive_cfg['name']} at {local_path}; rerun with --download-missing to fetch it"
        )
    url = str(archive_cfg["url"])
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, open(local_path, "wb") as out:
        shutil.copyfileobj(response, out)
    return local_path


def resolve_source_archives(
    cfg: dict[str, Any],
    *,
    archive_filters: set[str] | None = None,
    allow_download: bool = False,
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for archive_cfg in cfg["archives"]["source_archives"]:
        archive_name = str(archive_cfg["name"])
        if archive_filters and archive_name not in archive_filters:
            continue
        local_path = maybe_download_archive(archive_cfg, allow_download=allow_download)
        resolved.append({**archive_cfg, "resolved_path": str(local_path)})
    return resolved


def enumerate_archive_clips(
    archive_cfg: dict[str, Any],
    *,
    clip_filters: set[str] | None = None,
) -> list[dict[str, Any]]:
    archive_path = Path(str(archive_cfg["resolved_path"]))
    with zipfile.ZipFile(archive_path) as zf:
        names = zf.namelist()
        clip_names = sorted(
            {
                part
                for name in names
                for part in PurePosixPath(name).parts
                if "_assy_" in part
            }
        )
        inventory: list[dict[str, Any]] = []
        for clip_name in clip_names:
            if clip_filters and clip_name not in clip_filters:
                continue
            member_map = _clip_member_map(names, clip_name)
            rgb_indices = _rgb_frame_indices_from_member_map(member_map)
            if not rgb_indices:
                continue
            inventory.append(
                {
                    "archive_name": str(archive_cfg["name"]),
                    "archive_path": str(archive_path),
                    "split": str(archive_cfg.get("split", "test")),
                    "clip": clip_name,
                    "frame_count": len(rgb_indices),
                    "frame_min": min(rgb_indices),
                    "frame_max": max(rgb_indices),
                    "metadata_present": _metadata_present_from_member_map(member_map),
                    "stream_counts": {
                        stream: _stream_count_from_member_map(member_map, stream)
                        for stream in STREAM_NAMES
                    },
                }
            )
    return inventory


def build_clip_inventory(
    cfg: dict[str, Any],
    *,
    archive_filters: set[str] | None = None,
    clip_filters: set[str] | None = None,
    allow_download: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    archives = resolve_source_archives(
        cfg,
        archive_filters=archive_filters,
        allow_download=allow_download,
    )
    inventory: list[dict[str, Any]] = []
    for archive_cfg in archives:
        inventory.extend(
            enumerate_archive_clips(archive_cfg, clip_filters=clip_filters)
        )
    inventory.sort(key=lambda item: (str(item["archive_name"]), str(item["clip"])))
    return archives, inventory


def save_clip_inventory_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out_path.write_text("")
        return
    flat_rows: list[dict[str, Any]] = []
    for row in rows:
        flat = {
            "archive_name": row["archive_name"],
            "archive_path": row["archive_path"],
            "split": row["split"],
            "clip": row["clip"],
            "frame_count": row["frame_count"],
            "frame_min": row["frame_min"],
            "frame_max": row["frame_max"],
        }
        for stream, count in sorted(row["stream_counts"].items()):
            flat[f"stream_{stream}_count"] = count
        for name, present in sorted(row["metadata_present"].items()):
            flat[f"meta_{name}"] = present
        flat_rows.append(flat)
    fieldnames = list(flat_rows[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_rows)


def extract_full_clip_from_zip(
    archive_path: Path,
    clip_name: str,
    *,
    out_dir: Path,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as zf:
        member_map = _clip_member_map(zf.namelist(), clip_name)
        if not member_map:
            raise FileNotFoundError(f"clip {clip_name} not found in {archive_path}")
        for metadata_name in ROOT_METADATA_FILES:
            member = member_map.get(metadata_name)
            if not member:
                continue
            target = out_dir / metadata_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))
        for stream in STREAM_NAMES:
            for rel, member in member_map.items():
                if not rel.startswith(f"{stream}/") or not rel.endswith(".jpg"):
                    continue
                target = out_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
    return {
        "clip": clip_name,
        "archive_path": str(archive_path),
        "clip_dir": str(out_dir),
    }


def _sample_rows(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if len(df) <= n:
        return df
    if n <= 1:
        return df.iloc[[0]]
    indices = sorted({int(round(i * (len(df) - 1) / (n - 1))) for i in range(n)})
    return df.iloc[indices]


def _translations(df: pd.DataFrame) -> list[tuple[float, float, float]]:
    pose_cols = [f"pose_{idx:02d}" for idx in range(16)]
    points: list[tuple[float, float, float]] = []
    for _, row in df.iterrows():
        flat = [float(row[col]) for col in pose_cols]
        points.append((flat[3], flat[7], flat[11]))
    return points


def generate_clip_debug_visuals(
    manifest_df: pd.DataFrame,
    *,
    clip_dir: Path,
    visual_dir: Path,
    n_samples: int,
) -> None:
    from .raw_viz import save_od_overlay, save_pose_trajectory, save_rgb_depth_preview, save_stereo_preview

    od_labels = load_od_labels(clip_dir / "OD_labels.json")
    visual_dir.mkdir(parents=True, exist_ok=True)
    for _, row in _sample_rows(manifest_df, n_samples).iterrows():
        frame_name = str(row["frame_name"])
        label = od_labels.get(frame_name)
        gaze_xy = (int(row["gaze_x"]), int(row["gaze_y"])) if row["gaze_x"] or row["gaze_y"] else None
        save_rgb_depth_preview(
            Path(str(row["rgb_path"])),
            Path(str(row["depth_path"])),
            visual_dir / f"{frame_name[:-4]}_rgb_depth.png",
            bbox_xyxy=(label or {}).get("bbox_xyxy"),
            bbox_label=(label or {}).get("state_name"),
            gaze_xy=gaze_xy,
            title=f"{clip_dir.name} {frame_name}",
        )
        save_stereo_preview(
            Path(str(row["stereo_left_path"])),
            Path(str(row["stereo_right_path"])),
            visual_dir / f"{frame_name[:-4]}_stereo.png",
            title=f"{clip_dir.name} {frame_name}",
        )
        save_od_overlay(
            Path(str(row["rgb_path"])),
            visual_dir / f"{frame_name[:-4]}_od_overlay.png",
            bbox_xyxy=(label or {}).get("bbox_xyxy"),
            state_name=(label or {}).get("state_name"),
            gaze_xy=gaze_xy,
        )
    save_pose_trajectory(
        _translations(manifest_df),
        visual_dir / "pose_trajectory.png",
        title=f"{clip_dir.name} camera trajectory",
    )


def load_full_gt_steps(clip_dir: Path) -> list[dict[str, Any]]:
    rows = load_step_labels_csv(clip_dir / "PSR_labels_with_errors.csv")
    if not rows:
        rows = load_step_labels_csv(clip_dir / "PSR_labels.csv")
    return _normalize_gt_steps(rows)


def build_full_clip_event_windows(
    gt_steps: list[dict[str, Any]],
    *,
    max_frame_idx: int,
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    if max_frame_idx < 0:
        return []
    step_rows = [
        {
            "frame_name": f"{int(step['frame']):06d}.jpg",
            "frame_idx": int(step["frame"]),
            "id": int(step["id"]),
            "description": str(step["description"]),
        }
        for step in gt_steps
    ]
    return build_slice_windows(
        step_rows,
        min_frame=0,
        max_frame=max_frame_idx,
        rules=rules,
    )


def detector_window_coverage_from_windows(
    evidence_rows: list[dict[str, Any]],
    *,
    event_windows: list[dict[str, Any]],
) -> float | None:
    non_context_windows = [
        window
        for window in event_windows
        if not any(str(reason).startswith("context_") for reason in window.get("reasons", []))
    ]
    if not non_context_windows:
        return None
    frames_with_evidence = {
        int(row["frame_idx"])
        for row in evidence_rows
        if row.get("detections") or row.get("source_state_name") or row.get("source_error_steps")
    }
    hits = 0
    for window in non_context_windows:
        if any(int(window["start"]) <= frame <= int(window["end"]) for frame in frames_with_evidence):
            hits += 1
    return hits / len(non_context_windows)


def error_window_recall_full(
    state_rows: list[dict[str, Any]],
    *,
    clip_dir: Path,
    pre: int = 120,
    post: int = 60,
) -> float | None:
    step_rows = load_step_labels_csv(clip_dir / "PSR_labels_with_errors.csv")
    error_steps = [row for row in step_rows if str(row["description"]).startswith("Incorrectly ")]
    if not error_steps:
        return None
    predicted_error_frames = {
        int(row["frame_idx"])
        for row in state_rows
        if str(row["predicted_state"]) == "error_state"
    }
    hits = 0
    for row in error_steps:
        start = max(0, int(row["frame_idx"]) - pre)
        end = int(row["frame_idx"]) + post
        if any(start <= frame <= end for frame in predicted_error_frames):
            hits += 1
    return hits / len(error_steps)


def compile_full_clip_metrics(
    *,
    run_mode: str,
    archive_name: str,
    split: str,
    clip: str,
    clip_dir: Path,
    n_frames: int,
    state_rows: list[dict[str, Any]],
    psr_gt: list[dict[str, Any]],
    psr_pred: list[dict[str, Any]],
    psr_pred_b3: list[dict[str, Any]],
    evidence_path: Path,
    event_windows: list[dict[str, Any]],
    proc_info: list[dict[str, Any]],
) -> dict[str, Any]:
    state_metrics = evaluate_state_predictions(state_rows, clip_dir=clip_dir)
    psr_metrics = evaluate(psr_gt, psr_pred, proc_info)
    diagnostic_metrics = evaluate(psr_gt, psr_pred_b3, proc_info)
    direct_step_metrics = step_metrics(psr_gt, psr_pred, proc_info)
    diagnostic_step_metrics = step_metrics(psr_gt, psr_pred_b3, proc_info)
    evidence_rows = load_jsonl(evidence_path)
    coverage = detector_window_coverage_from_windows(evidence_rows, event_windows=event_windows)
    error_recall = error_window_recall_full(state_rows, clip_dir=clip_dir)
    return {
        "run_mode": run_mode,
        "archive_name": archive_name,
        "split": split,
        "clip": clip,
        "n_frames": n_frames,
        **state_metrics,
        "gt_steps": len(psr_gt),
        "predicted_steps": len(psr_pred),
        "diagnostic_predicted_steps": len(psr_pred_b3),
        "legal_state_rate": 1.0 if state_rows else 0.0,
        "error_window_recall": error_recall,
        "event_window_evidence_ratio": coverage,
        **direct_step_metrics,
        "psr_pos": psr_metrics["pos"],
        "psr_f1": psr_metrics["f1"],
        "psr_avg_delay_s": psr_metrics["avg_delay_s"],
        "psr_tps": psr_metrics["system_TPs"],
        "psr_fps": psr_metrics["system_FPs"],
        "psr_fns": psr_metrics["system_FNs"],
        "b3_diagnostic_step_precision": diagnostic_step_metrics["step_precision"],
        "b3_diagnostic_step_recall": diagnostic_step_metrics["step_recall"],
        "b3_diagnostic_median_delay_frames": diagnostic_step_metrics["median_step_delay_frames"],
        "b3_diagnostic_psr_pos": diagnostic_metrics["pos"],
        "b3_diagnostic_psr_f1": diagnostic_metrics["f1"],
        "b3_diagnostic_psr_avg_delay_s": diagnostic_metrics["avg_delay_s"],
    }


def save_summary_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out_path.write_text("")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_mode_comparison_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_key.setdefault((str(row["archive_name"]), str(row["clip"])), {})
        by_key[(str(row["archive_name"]), str(row["clip"]))][str(row["run_mode"])] = row

    output: list[dict[str, Any]] = []
    for (archive_name, clip), modes in sorted(by_key.items()):
        od_only = modes.get("od_only")
        plus = modes.get("od_plus_psr_error_hints")
        if od_only is None or plus is None:
            continue
        output.append(
            {
                "scope": "clip",
                "archive_name": archive_name,
                "clip": clip,
                "od_only_step_recall": od_only["step_recall"],
                "od_plus_step_recall": plus["step_recall"],
                "delta_step_recall": plus["step_recall"] - od_only["step_recall"],
                "od_only_step_precision": od_only["step_precision"],
                "od_plus_step_precision": plus["step_precision"],
                "delta_step_precision": plus["step_precision"] - od_only["step_precision"],
                "od_only_error_window_recall": od_only["error_window_recall"],
                "od_plus_error_window_recall": plus["error_window_recall"],
                "delta_error_window_recall": (
                    (plus["error_window_recall"] or 0.0) - (od_only["error_window_recall"] or 0.0)
                ),
                "od_only_predicted_steps": od_only["predicted_steps"],
                "od_plus_predicted_steps": plus["predicted_steps"],
            }
        )

    overall_rows = [row for row in rows if row["run_mode"] in {"od_only", "od_plus_psr_error_hints"}]
    for mode in ("od_only", "od_plus_psr_error_hints"):
        mode_rows = [row for row in overall_rows if row["run_mode"] == mode]
        if not mode_rows:
            continue
        output.append(
            {
                "scope": "overall",
                "archive_name": "__all__",
                "clip": mode,
                "od_only_step_recall": None,
                "od_plus_step_recall": None,
                "delta_step_recall": None,
                "od_only_step_precision": None,
                "od_plus_step_precision": None,
                "delta_step_precision": None,
                "od_only_error_window_recall": None,
                "od_plus_error_window_recall": None,
                "delta_error_window_recall": None,
                "od_only_predicted_steps": None,
                "od_plus_predicted_steps": None,
                "mode": mode,
                "mean_step_recall": sum(float(row["step_recall"]) for row in mode_rows) / len(mode_rows),
                "mean_step_precision": sum(float(row["step_precision"]) for row in mode_rows) / len(mode_rows),
                "mean_error_window_recall": sum(float(row["error_window_recall"] or 0.0) for row in mode_rows)
                / len(mode_rows),
                "total_predicted_steps": sum(int(row["predicted_steps"]) for row in mode_rows),
                "total_gt_steps": sum(int(row["gt_steps"]) for row in mode_rows),
            }
        )
    return output


def load_existing_summary_rows(run_id: str, *, paths: RawCadPaths) -> list[dict[str, Any]]:
    summary_path = paths.dataset_summary_path(run_id)
    if not summary_path.exists():
        return []
    with open(summary_path, newline="") as f:
        return list(csv.DictReader(f))


def load_metrics_rows_from_working_results(run_id: str, *, paths: RawCadPaths) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    modes_root = paths.dataset_run_results_dir(run_id) / "modes"
    if not modes_root.exists():
        return rows
    for metrics_path in sorted(modes_root.glob("*/*/*/metrics.json")):
        rows.append(json.loads(metrics_path.read_text()))
    return rows


def init_run_manifest(
    *,
    run_id: str,
    cfg: dict[str, Any],
    archives: list[dict[str, Any]],
    clip_inventory: list[dict[str, Any]],
    selected_modes: list[str],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "batch_name": cfg["batch"]["name"],
        "scope": cfg["batch"]["scope"],
        "clip_scope": cfg["batch"]["clip_scope"],
        "archives": [
            {
                "name": archive["name"],
                "split": archive.get("split", "test"),
                "resolved_path": archive["resolved_path"],
            }
            for archive in archives
        ],
        "oracle_runs": list(selected_modes),
        "clip_count": len(clip_inventory),
        "status_by_item": {},
    }


def load_or_init_run_manifest(
    run_id: str,
    *,
    paths: RawCadPaths,
    cfg: dict[str, Any],
    archives: list[dict[str, Any]],
    clip_inventory: list[dict[str, Any]],
    selected_modes: list[str],
) -> dict[str, Any]:
    manifest_path = paths.dataset_run_manifest_path(run_id)
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    manifest = init_run_manifest(
        run_id=run_id,
        cfg=cfg,
        archives=archives,
        clip_inventory=clip_inventory,
        selected_modes=selected_modes,
    )
    save_run_manifest(manifest, manifest_path)
    return manifest


def save_run_manifest(manifest: dict[str, Any], manifest_path: Path) -> None:
    manifest["updated_at"] = utc_now_iso()
    _save_json(manifest_path, manifest)


def item_key(archive_name: str, clip: str, mode: str) -> str:
    return f"{archive_name}::{clip}::{mode}"


def is_item_complete(manifest: dict[str, Any], *, archive_name: str, clip: str, mode: str) -> bool:
    entry = manifest.get("status_by_item", {}).get(item_key(archive_name, clip, mode), {})
    return str(entry.get("status")) == "completed"


def update_item_status(
    manifest: dict[str, Any],
    *,
    archive_name: str,
    clip: str,
    mode: str,
    status: str,
    metrics_path: str | None = None,
    error: str | None = None,
) -> None:
    manifest.setdefault("status_by_item", {})
    manifest["status_by_item"][item_key(archive_name, clip, mode)] = {
        "archive_name": archive_name,
        "clip": clip,
        "mode": mode,
        "status": status,
        "metrics_path": metrics_path,
        "error": error,
        "updated_at": utc_now_iso(),
    }


def failure_log_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    failures = []
    for entry in manifest.get("status_by_item", {}).values():
        if str(entry.get("status")) == "failed":
            failures.append(entry)
    failures.sort(key=lambda item: (str(item["archive_name"]), str(item["clip"]), str(item["mode"])))
    return failures


def build_or_load_cad_catalogs(cfg: dict[str, Any], *, paths: RawCadPaths, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    shared_cad_dir = paths.dataset_run_shared_dir(run_id) / "cad"
    report_cad_dir = paths.dataset_run_reports_dir(run_id) / "cad"
    part_path = shared_cad_dir / "cad_part_catalog.json"
    state_path = shared_cad_dir / "cad_state_catalog.json"
    if part_path.exists() and state_path.exists():
        part_catalog = json.loads(part_path.read_text())
        state_catalog = json.loads(state_path.read_text())
        return part_catalog, state_catalog

    part_geometries_working = paths.resolve_archive_path("part_geometries_working")
    part_geometries_repo = paths.resolve_archive_path("part_geometries_repo")
    part_geometries_zip = None
    if part_geometries_working is not None and part_geometries_working.exists():
        part_geometries_zip = part_geometries_working
    elif part_geometries_repo is not None and part_geometries_repo.exists():
        part_geometries_zip = part_geometries_repo

    asd_results_zip = paths.resolve_archive_path("asd_results_zip")
    if asd_results_zip is None or not asd_results_zip.exists():
        raise FileNotFoundError(f"missing ASD results zip: {asd_results_zip}")

    proc_info = load_procedure_info(ROOT / "configs" / "procedure_info.json")
    part_catalog = build_cad_part_catalog(cfg, part_geometries_zip=part_geometries_zip)
    state_catalog = build_state_catalog(cfg, procedure_info=proc_info, asd_results_zip=asd_results_zip)
    save_cad_artifacts(shared_cad_dir, part_catalog=part_catalog, state_catalog=state_catalog)
    save_cad_artifacts(report_cad_dir, part_catalog=part_catalog, state_catalog=state_catalog)
    return part_catalog, state_catalog


def run_oracle_dataset_batch(
    cfg: dict[str, Any],
    *,
    paths: RawCadPaths,
    run_id: str | None = None,
    archive_filters: set[str] | None = None,
    clip_filters: set[str] | None = None,
    mode_filters: set[str] | None = None,
    download_missing: bool | None = None,
    resume: bool | None = None,
) -> dict[str, Any]:
    configure_runtime_environment(paths)
    paths.ensure_base_dirs()
    run_id = run_id or default_run_id(cfg)
    selected_modes = [
        mode
        for mode in cfg["batch"]["oracle_runs"]
        if not mode_filters or mode in mode_filters
    ]
    allow_download = bool(download_missing) if download_missing is not None else bool(cfg["batch"]["allow_download_missing"])
    resume_enabled = bool(resume) if resume is not None else bool(cfg["batch"]["resume"])

    archives, clip_inventory = build_clip_inventory(
        cfg,
        archive_filters=archive_filters,
        clip_filters=clip_filters,
        allow_download=allow_download,
    )
    reports_dir = paths.dataset_run_reports_dir(run_id)
    reports_dir.mkdir(parents=True, exist_ok=True)
    save_clip_inventory_csv(clip_inventory, paths.dataset_clip_inventory_path(run_id))

    manifest = load_or_init_run_manifest(
        run_id,
        paths=paths,
        cfg=cfg,
        archives=archives,
        clip_inventory=clip_inventory,
        selected_modes=selected_modes,
    )
    part_catalog, state_catalog = build_or_load_cad_catalogs(cfg, paths=paths, run_id=run_id)
    proc_info = load_procedure_info(ROOT / "configs" / "procedure_info.json")

    for clip_meta in clip_inventory:
        archive_name = str(clip_meta["archive_name"])
        clip = str(clip_meta["clip"])
        archive_path = Path(str(clip_meta["archive_path"]))
        extract_dir = paths.dataset_clip_extract_dir(run_id, archive_name, clip)
        shared_dir = paths.dataset_clip_shared_dir(run_id, archive_name, clip)
        all_completed = all(
            is_item_complete(manifest, archive_name=archive_name, clip=clip, mode=mode)
            for mode in selected_modes
        )
        if resume_enabled and all_completed:
            continue

        extract_full_clip_from_zip(archive_path, clip, out_dir=extract_dir)
        try:
            manifest_df = build_raw_manifest(
                extract_dir,
                source_archive=archive_name,
                split=str(clip_meta["split"]),
            )
            shared_dir.mkdir(parents=True, exist_ok=True)
            shared_manifest_path = shared_dir / "raw_manifest.csv"
            shared_report_path = shared_dir / "raw_manifest_report.json"
            save_manifest(manifest_df, shared_manifest_path)
            manifest_report = validate_raw_manifest(manifest_df, extract_dir)
            save_manifest_report(manifest_report, shared_report_path)

            if bool(cfg["batch"].get("keep_debug_visuals", True)):
                generate_clip_debug_visuals(
                    manifest_df,
                    clip_dir=extract_dir,
                    visual_dir=shared_dir / "debug_visuals",
                    n_samples=int(cfg["visualization"]["n_samples_per_clip"]),
                )

            gt_steps = load_full_gt_steps(extract_dir)
            max_frame_idx = int(manifest_df["frame_idx"].max()) if not manifest_df.empty else -1
            n_frames = max_frame_idx + 1 if max_frame_idx >= 0 else 0
            event_windows = build_full_clip_event_windows(
                gt_steps,
                max_frame_idx=max_frame_idx,
                rules=cfg["slice_rules"],
            )

            for mode in selected_modes:
                if resume_enabled and is_item_complete(manifest, archive_name=archive_name, clip=clip, mode=mode):
                    continue
                mode_dir = paths.dataset_clip_mode_dir(run_id, mode, archive_name, clip)
                mode_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shared_manifest_path, mode_dir / "raw_manifest.csv")
                settings = oracle_mode_settings(mode)
                raw_records = run_detector_for_clip(
                    manifest_df,
                    clip_dir=extract_dir,
                    part_catalog=part_catalog,
                    state_catalog=state_catalog,
                    cfg=cfg,
                    backend="oracle_od",
                    include_error_step_hints=bool(settings["include_error_step_hints"]),
                )
                smoothed = smooth_frame_evidence(
                    raw_records,
                    iou_threshold=float(cfg["detector"]["iou_smoothing_threshold"]),
                    track_decay=float(cfg["detector"]["track_decay"]),
                )
                frame_evidence_path = mode_dir / "frame_evidence.jsonl"
                smoothed_path = mode_dir / "smoothed_frame_evidence.jsonl"
                save_jsonl(raw_records, frame_evidence_path)
                save_jsonl(smoothed, smoothed_path)

                state_rows = oracle_state_sequence_from_labels(
                    manifest_df.to_dict("records"),
                    clip_dir=extract_dir,
                    state_catalog=state_catalog,
                    include_error_step_hints=bool(settings["include_error_step_hints"]),
                )
                state_path = mode_dir / "state_sequence.csv"
                save_state_sequence(state_rows, state_path)

                asd_frames = state_sequence_to_asd_frames(state_rows, state_catalog=state_catalog)
                psr_pred = direct_transition_steps_from_state_sequence(state_rows, proc_info=proc_info)
                psr_pred_b3 = run_psr(asd_frames, proc_info, **PSR_KWARGS)
                graph = build_assembly_graph(clip, n_frames, gt_steps, proc_info)
                metrics = compile_full_clip_metrics(
                    run_mode=mode,
                    archive_name=archive_name,
                    split=str(clip_meta["split"]),
                    clip=clip,
                    clip_dir=extract_dir,
                    n_frames=n_frames,
                    state_rows=state_rows,
                    psr_gt=gt_steps,
                    psr_pred=psr_pred,
                    psr_pred_b3=psr_pred_b3,
                    evidence_path=smoothed_path,
                    event_windows=event_windows,
                    proc_info=proc_info,
                )

                _save_json(mode_dir / "psr_pred.json", psr_pred)
                _save_json(mode_dir / "psr_pred_b3_diagnostic.json", psr_pred_b3)
                _save_json(mode_dir / "gt_steps.json", gt_steps)
                _save_json(mode_dir / "assembly_graph.json", _graph_to_json(graph))
                _save_json(mode_dir / "metrics.json", metrics)
                update_item_status(
                    manifest,
                    archive_name=archive_name,
                    clip=clip,
                    mode=mode,
                    status="completed",
                    metrics_path=str(mode_dir / "metrics.json"),
                )
                save_run_manifest(manifest, paths.dataset_run_manifest_path(run_id))
        except Exception as exc:
            for mode in selected_modes:
                if resume_enabled and is_item_complete(manifest, archive_name=archive_name, clip=clip, mode=mode):
                    continue
                update_item_status(
                    manifest,
                    archive_name=archive_name,
                    clip=clip,
                    mode=mode,
                    status="failed",
                    error=str(exc),
                )
            save_run_manifest(manifest, paths.dataset_run_manifest_path(run_id))
        finally:
            if extract_dir.exists():
                shutil.rmtree(extract_dir)

    summary_rows = load_metrics_rows_from_working_results(run_id, paths=paths)
    summary_rows.sort(key=lambda item: (str(item["run_mode"]), str(item["archive_name"]), str(item["clip"])))
    save_summary_csv(summary_rows, paths.dataset_summary_path(run_id))
    mode_comparison_rows = build_mode_comparison_rows(summary_rows)
    save_summary_csv(mode_comparison_rows, paths.dataset_mode_comparison_path(run_id))
    _save_json(paths.dataset_failure_log_path(run_id), failure_log_from_manifest(manifest))
    save_run_manifest(manifest, paths.dataset_run_manifest_path(run_id))

    return {
        "run_id": run_id,
        "reports_dir": str(paths.dataset_run_reports_dir(run_id)),
        "working_results_dir": str(paths.dataset_run_results_dir(run_id)),
        "clip_count": len(clip_inventory),
        "oracle_runs": selected_modes,
        "summary_rows": len(summary_rows),
    }
