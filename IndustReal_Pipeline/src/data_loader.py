"""data_loader.py — Load IndustReal ASD prediction/GT CSVs and derive PSR labels.

Each ASD CSV row:  clip, framenr, bb_class, bb_conf, bb_x, bb_y, bb_w, bb_h

GT CSVs use bb_conf=1.0 always.  Prediction CSVs may have 0 rows (model failure).

PSR ground-truth steps are derived here by walking consecutive GT state
annotations and calling convert_states_to_steps() — same logic as
convert_all_states_to_steps() in the original IndustReal psr_utils.py.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from .psr import CATEGORIES, state_string_to_list, convert_states_to_steps


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_asd_csv(path: Path) -> list[dict]:
    """Read an ASD CSV and return rows as dicts (empty file → [])."""
    if path.stat().st_size == 0:
        return []
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Background rows (bb_class=0) have no bounding box columns.
            if row.get("bb_conf") is None:
                continue
            rows.append({
                "clip":    row["clip"],
                "framenr": int(row["framenr"]),
                "bb_class": int(row["bb_class"]),
                "bb_conf":  float(row["bb_conf"]),
                "bb_x":     float(row["bb_x"]),
                "bb_y":     float(row["bb_y"]),
                "bb_w":     float(row["bb_w"]),
                "bb_h":     float(row["bb_h"]),
            })
    return rows


def _rows_to_frame_list(rows: list[dict], n_frames: int) -> list[list]:
    """Convert flat CSV rows into a per-frame list of [class, conf, [x,y,w,h]].

    Frames with no detection are represented as empty lists [].
    Multiple predictions per frame are kept in order of appearance.
    """
    frames: list[list] = [[] for _ in range(n_frames)]
    for r in rows:
        fn = r["framenr"]
        if fn < n_frames:
            frames[fn].append([r["bb_class"], r["bb_conf"],
                                [r["bb_x"], r["bb_y"], r["bb_w"], r["bb_h"]]])
    return frames


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_recording(
    pred_csv: Path,
    gt_csv: Path,
    proc_info: list,
    *,
    psr_step_rows: list[dict] | None = None,
) -> dict:
    """Load one recording and return everything needed to run PSR + evaluate.

    Returns a dict with keys:
      clip          — clip name string
      n_frames      — total frame count (from GT max framenr + 1)
      asd_pred      — per-frame prediction lists  (n_frames × variable)
      asd_gt        — per-frame GT lists           (n_frames × variable)
      psr_gt        — list of PSR step dicts
      has_pred      — bool: False when pred CSV is empty (model failure case)
      gt_rows       — raw GT rows (for diagnostics)
      psr_gt_source — "psr_labels" or "asd_state_transitions"

    psr_step_rows: optional rows with keys 'frame_idx', 'id', 'description'
        (as returned by raw_loader.load_step_labels_csv). When supplied,
        psr_gt is built from these directly, preserving the original per-step
        timestamps. When None, steps are derived from ASD state transitions —
        which collapses all components changed in one transition to a single
        frame number.
    """
    gt_rows   = _read_asd_csv(gt_csv)
    pred_rows = _read_asd_csv(pred_csv)

    if not gt_rows:
        raise ValueError(f"GT CSV is empty: {gt_csv}")

    n_frames  = max(r["framenr"] for r in gt_rows) + 1
    clip_name = gt_rows[0]["clip"]

    asd_pred = _rows_to_frame_list(pred_rows, n_frames)
    asd_gt   = _rows_to_frame_list(gt_rows,   n_frames)

    if psr_step_rows is not None:
        psr_gt = [
            {
                "frame": int(row["frame_idx"]),
                "id": int(row["id"]),
                "description": str(row["description"]),
                "conf": 1.0,
            }
            for row in psr_step_rows
        ]
        psr_gt_source = "psr_labels"
    else:
        # Derive PSR ground-truth steps from consecutive GT state transitions.
        # Walk frames in order; whenever the annotated state changes, infer steps.
        # Limitation: a multi-component transition emits all steps at the same frame.
        psr_gt = []
        prev_state: Optional[list] = None
        prev_state_str: Optional[str] = None

        for fn, frame_preds in enumerate(asd_gt):
            if not frame_preds:
                continue
            state_class = frame_preds[0][0]
            state_str   = CATEGORIES[int(state_class)]

            if state_str in ("background", "error_state"):
                continue
            if state_str == prev_state_str:
                continue

            curr_state = state_string_to_list(state_str)
            if prev_state is not None:
                actions, _ = convert_states_to_steps(
                    prev_state, curr_state, fn, proc_info, conf=1.0
                )
                psr_gt.extend(actions)

            prev_state     = curr_state
            prev_state_str = state_str

        psr_gt_source = "asd_state_transitions"

    return {
        "clip":           clip_name,
        "n_frames":       n_frames,
        "asd_pred":       asd_pred,
        "asd_gt":         asd_gt,
        "psr_gt":         psr_gt,
        "has_pred":       len(pred_rows) > 0,
        "gt_rows":        gt_rows,
        "psr_gt_source":  psr_gt_source,
    }
