#!/usr/bin/env python3
"""01_run_demo.py — IndustReal PSR proof-of-concept end-to-end demo.

Processes two real recordings from the IndustReal test set:
  03_assy_0_1  —  clean assembly (participant succeeds, ASD model works)
  03_assy_1_3  —  error case     (participant makes error, ASD model fails entirely)

For each recording:
  1. Load real ASD pred/GT CSVs.
  2. Derive PSR ground-truth labels from GT state transitions.
  3. Run PSR B3 (AccumulatedConfidencePSR with procedure='assy').
  4. Evaluate: POS, F1, avg_delay_s.
  5. Build EGG-style AssemblyGraph for GT and predictions.
  6. Print rich summary.

Usage:
    cd /workspaces/grounded-assembly-reasoning/IndustReal_Pipeline
    python scripts/01_run_demo.py
"""
from __future__ import annotations

import csv
import json
import sys
import tarfile
from pathlib import Path

# Allow running from repo root or from IndustReal_Pipeline/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data_loader import load_recording
from src.psr import run_psr, evaluate
from src.egg_builder import build_assembly_graph, diff_graphs

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR      = ROOT / "data" / "ASD_results" / "ASD_IndustRealplusSynthetic_test"
PROC_INFO     = ROOT / "configs" / "procedure_info.json"
RESULTS_DIR   = ROOT / "results"
SLICE_TARBALL = ROOT / "data" / "relevant_slice_test_p1.tar.gz"

RECORDINGS = [
    ("03_assy_0_1", "Clean assembly — participant succeeds"),
    ("03_assy_1_3", "Error case    — participant makes error, ASD model fails"),
]

# PSR B3 hyper-parameters (IndustReal paper defaults).
PSR_KWARGS = dict(
    implementation      = "expected",
    procedure           = "assy",
    cum_conf_threshold  = 8.0,
    cum_decay           = 0.75,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIVIDER = "=" * 70

def _sep(title: str = ""):
    if title:
        print(f"\n{DIVIDER}\n  {title}\n{DIVIDER}")
    else:
        print(DIVIDER)


def _fmt_steps(steps: list, label: str):
    print(f"\n  {label} ({len(steps)} steps):")
    if not steps:
        print("    (none)")
    for s in steps:
        t_s = s["frame"] / 10
        print(f"    [{t_s:6.1f}s] id={s['id']:2d}  {s['description']}")


def _fmt_metrics(metrics: dict):
    print(f"\n  Metrics:")
    print(f"    POS        : {metrics['pos']:.4f}   (1.0 = perfect order)")
    print(f"    F1         : {metrics['f1']:.4f}")
    delay = metrics['avg_delay_s']
    delay_str = f"{delay:.2f} s" if delay is not None else "N/A"
    print(f"    Avg delay  : {delay_str}")
    print(f"    TP / FP / FN : {metrics['system_TPs']} / {metrics['system_FPs']} / {metrics['system_FNs']}")


def _json_default(obj):
    """Coerce numpy scalars so json.dumps doesn't choke."""
    import numpy as np
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _steps_to_dicts(steps: list) -> list[dict]:
    return [
        {"frame": int(s["frame"]), "time_s": round(int(s["frame"]) / 10, 2),
         "id": int(s["id"]), "description": s["description"], "conf": float(s.get("conf", 1.0))}
        for s in steps
    ]


def _save_results(clip: str, n_frames: int, has_pred: bool,
                  metrics: dict, psr_gt: list, psr_pred: list,
                  gt_graph, pred_graph) -> None:
    data = {
        "clip":       clip,
        "n_frames":   n_frames,
        "duration_s": round(n_frames / 10, 1),
        "has_pred":   has_pred,
        "metrics":    metrics,
        "gt_steps":   _steps_to_dicts(psr_gt),
        "pred_steps": _steps_to_dicts(psr_pred),
        "component_states": {
            "gt":   {k: v for k, v in gt_graph.component_states.items()},
            "pred": {k: v for k, v in pred_graph.component_states.items()},
        },
    }
    out = RESULTS_DIR / f"{clip}_results.json"
    out.write_text(json.dumps(data, indent=2, default=_json_default))
    print(f"\n  Saved → {out.relative_to(ROOT)}")


def _save_summary(all_results: list[dict]) -> None:
    out = RESULTS_DIR / "summary.csv"
    fieldnames = ["clip", "duration_s", "has_pred",
                  "pos", "f1", "avg_delay_s", "TPs", "FPs", "FNs"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in all_results:
            m = r["metrics"]
            w.writerow({
                "clip":        r["clip"],
                "duration_s":  r["duration_s"],
                "has_pred":    r["has_pred"],
                "pos":         m["pos"],
                "f1":          m["f1"],
                "avg_delay_s": m["avg_delay_s"],
                "TPs":         m["system_TPs"],
                "FPs":         m["system_FPs"],
                "FNs":         m["system_FNs"],
            })
    print(f"\n  Summary → {out.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# PSR label loader (reads authoritative per-step timestamps from tarball)
# ---------------------------------------------------------------------------

def _load_psr_steps_from_tarball(clip_name: str) -> list[dict] | None:
    """Extract PSR_labels_with_errors.csv (or PSR_labels.csv) from the slice
    tarball and return rows as dicts with keys frame_idx, id, description.
    Returns None when the tarball is absent or the clip is not inside it.
    """
    if not SLICE_TARBALL.exists():
        return None
    candidates = [
        f"relevant_slice/{clip_name}/PSR_labels_with_errors.csv",
        f"relevant_slice/{clip_name}/PSR_labels.csv",
    ]
    try:
        with tarfile.open(SLICE_TARBALL, "r:gz") as tf:
            for member_name in candidates:
                try:
                    member = tf.extractfile(member_name)
                except KeyError:
                    continue
                if member is None:
                    continue
                rows = []
                for row in csv.reader(member.read().decode().splitlines()):
                    if len(row) < 3:
                        continue
                    rows.append({
                        "frame_idx": int(Path(row[0]).stem),
                        "id": int(row[1]),
                        "description": row[2],
                    })
                return rows or None
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    proc_info = json.loads(PROC_INFO.read_text())
    RESULTS_DIR.mkdir(exist_ok=True)

    _sep("IndustReal PSR — Proof of Concept")
    print(f"  Procedure info  : {len(proc_info)} action steps")
    print(f"  PSR algorithm   : B3 (AccumulatedConfidence + procedure='assy')")
    print(f"  Recordings      : {len(RECORDINGS)}")
    print(f"  Output dir      : {RESULTS_DIR.relative_to(ROOT)}")

    all_results = []

    for clip_name, description in RECORDINGS:
        pred_csv = DATA_DIR / f"{clip_name}_results_pred.csv"
        gt_csv   = DATA_DIR / f"{clip_name}_results_gt.csv"

        _sep(f"{clip_name}  —  {description}")

        # ── Load ─────────────────────────────────────────────────────────────
        psr_step_rows = _load_psr_steps_from_tarball(clip_name)
        rec = load_recording(pred_csv, gt_csv, proc_info, psr_step_rows=psr_step_rows)

        print(f"\n  Clip     : {rec['clip']}")
        print(f"  Frames   : {rec['n_frames']}  ({rec['n_frames'] / 10:.1f} s at 10 fps)")
        print(f"  Has pred : {rec['has_pred']}")
        print(f"  GT source: {rec['psr_gt_source']}")

        if rec["psr_gt_source"] == "asd_state_transitions":
            print("  WARNING: PSR labels not found; GT step timestamps derived from ASD")
            print("           state transitions — simultaneous steps may be an artifact.")

        if not rec["has_pred"]:
            print("\n  *** ASD model produced ZERO predictions for this clip. ***")
            print("  This is expected: the participant hit an error-state (class 23)")
            print("  which the model was not trained to handle in a sequential context.")

        # ── GT steps ─────────────────────────────────────────────────────────
        gt_label = (
            "GT steps (from PSR_labels_with_errors.csv)"
            if rec["psr_gt_source"] == "psr_labels"
            else "GT steps (derived from ASD state transitions — may have simultaneous timestamps)"
        )
        _fmt_steps(rec["psr_gt"], gt_label)

        # ── PSR prediction ────────────────────────────────────────────────────
        psr_pred = run_psr(rec["asd_pred"], proc_info, **PSR_KWARGS)
        _fmt_steps(psr_pred, "Predicted steps (PSR B3)")

        # ── Evaluation ───────────────────────────────────────────────────────
        metrics = evaluate(rec["psr_gt"], psr_pred, proc_info)
        _fmt_metrics(metrics)

        # ── EGG graphs ────────────────────────────────────────────────────────
        gt_graph   = build_assembly_graph(clip_name, rec["n_frames"], rec["psr_gt"], proc_info)
        pred_graph = build_assembly_graph(clip_name, rec["n_frames"], psr_pred,     proc_info)

        print(f"\n{diff_graphs(gt_graph, pred_graph)}")

        # ── Save per-recording JSON ───────────────────────────────────────────
        _save_results(clip_name, rec["n_frames"], rec["has_pred"],
                      metrics, rec["psr_gt"], psr_pred, gt_graph, pred_graph)
        all_results.append({
            "clip": clip_name,
            "duration_s": round(rec["n_frames"] / 10, 1),
            "has_pred": rec["has_pred"],
            "metrics": metrics,
        })

    # ── Save cross-recording summary ─────────────────────────────────────────
    _save_summary(all_results)
    _sep("Done")


if __name__ == "__main__":
    main()
