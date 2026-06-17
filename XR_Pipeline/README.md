# Grounded Assembly Reasoning — XR Sensor Pipeline

A sensor-log-first pipeline that turns **Meta Quest 3 RGB-D + pose captures** into a queryable **Event-Grounded Graph (EGG)** — tracking what objects appeared, moved, and were interacted with across a recording session. Results are exported to Neo4j for graph queries.

```
Quest 3 capture → object detection → 3D tracks → events → EGG graph → Neo4j
```

---

## Table of Contents

- [Overview](#overview)
- [Requirements](#requirements)
- [Setup](#setup)
- [Running a Session](#running-a-session)
- [Pipeline Stages](#pipeline-stages)
- [Configuration](#configuration)
- [Object Detection Backends](#object-detection-backends)
- [EGG Graph Structure](#egg-graph-structure)
- [Neo4j Import](#neo4j-import)
- [Example Queries](#example-queries)
- [Project Structure](#project-structure)
- [Running Tests](#running-tests)

---

## Overview

The pipeline processes raw Quest 3 captures frame by frame:

1. **Detect** — Grounding DINO (open-vocabulary) detects objects in each RGB frame using a text prompt you define (e.g. `"laptop. mouse. hands."`)
2. **Backproject** — Bounding box centres are projected into 3D world space using depth maps and camera pose
3. **Track** — Observations are linked across frames into persistent object tracks using spatial + class similarity
4. **Detect events** — Tracks are analysed to produce MOVE, APPEAR, DISAPPEAR, CO_LOCATE, SEPARATE, and INTERACTION events
5. **Build graph** — Events and objects are assembled into an EGG graph (JSON)
6. **Export** — Graph is exported to Neo4j-ready CSVs and pushed automatically to your Neo4j Aura instance

---

## Requirements

- Python 3.10+
- A Meta Quest 3 capture folder (see [Data Format](#data-format))
- A [Neo4j Aura Free](https://neo4j.com/cloud/platform/aura-graph-database/) instance (for graph import)

Install Python dependencies:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

For NVIDIA GPU acceleration, install the CUDA-enabled PyTorch build instead of the CPU build. Use the official selector at [pytorch.org/get-started/locally](https://pytorch.org/get-started/locally/) to choose the right command for your OS and CUDA version. For example:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

Then verify what PyTorch can see:

```bash
python -c "import torch; print(torch.__version__); print('CUDA available:', torch.cuda.is_available())"
```

Key packages: `transformers`, `torch`, `torchvision`, `pandas`, `numpy`, `networkx`, `neo4j`, `typer`, `rich`

---

## Setup

```bash
git clone <repo-url>
cd XR_Pipeline

# Install PyTorch first. Use the CPU build below, or replace it with the CUDA
# command from https://pytorch.org/get-started/locally/ for NVIDIA GPU support.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Copy and fill in your local credentials
cp .env.example .env
# Edit .env with your NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
# Optional: add HF_TOKEN for Hugging Face model downloads

# Bootstrap output directories
python scripts/00_bootstrap_repo.py
```

### .env

```
NEO4J_URI=neo4j+s://<instance-id>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password-here

# Optional, but recommended for Hugging Face rate limits
HF_TOKEN=hf_your-token-here
```

Find these in the [Neo4j Aura console](https://console.neo4j.io) → your instance → **Inspect**.

For Hugging Face, create a read token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). This avoids warnings such as `You are sending unauthenticated requests to the HF Hub` and gives higher rate limits for model downloads.

> `.env` is gitignored. Never commit real credentials. Keep model names and non-secret settings in `configs/pipeline.yaml`; keep tokens/passwords in environment variables or `.env`.

---

## Running a Session

### New capture

1. Place your Quest 3 capture folder at any path, e.g.:
   ```
   ../Quest_Capture/session_002/quest_capture/
   ```

2. Update `configs/pipeline.yaml`:
   ```yaml
   raw_data_root: "../Quest_Capture/session_002/quest_capture"
   detection_prompt: "laptop. mouse. hands. cup."   # objects in your scene
   ```

3. Run all pipeline scripts with your session ID:
   ```bash
   python scripts/01_build_frame_manifest.py      --session session_002
   python scripts/02_validate_manifest.py         --session session_002
   python scripts/05_build_object_observations.py --session session_002
   python scripts/06_link_object_tracks.py        --session session_002
   python scripts/07_build_event_windows.py       --session session_002
   python scripts/08_generate_event_summaries.py  --session session_002
   python scripts/09_build_egg_graph.py           --session session_002
   python scripts/10_prune_egg_graph.py           --session session_002
   python scripts/11_export_neo4j_csv.py          --session session_002
   python scripts/14_import_neo4j.py              --session session_002
   ```

4. Query in Neo4j Browser or run demo queries:
   ```bash
   python scripts/12_demo_queries.py --session session_002
   ```

Each session's output is fully isolated under `data/processed/<session_id>/`.

---

## Pipeline Stages

| # | Script | Key Output | Description |
|---|--------|-----------|-------------|
| 00 | `00_bootstrap_repo.py` | directories | Create output folder structure |
| 01 | `01_build_frame_manifest.py` | `frame_manifest.csv` | Scan capture, build canonical frame list with pose + timestamps |
| 02 | `02_validate_manifest.py` | `manifest_validation.json` | Verify files exist, depth decodes correctly, poses are valid |
| 03 | `03_visualize_rgb_depth_pose.py` | `sample_visualizations/` | RGB/depth overlays and pose trajectory plots |
| 04 | `04_ingest_spatialobjects.py` | _(optional)_ | Ingest `spatialobjects.csv` from Quest if present |
| 05 | `05_build_object_observations.py` | `object_observations.csv` | Detect objects per frame, backproject to 3D, save detection overlays |
| 06 | `06_link_object_tracks.py` | `object_tracks.csv` | Link per-frame observations into persistent tracks |
| 07 | `07_build_event_windows.py` | `event_windows.csv` | Detect MOVE, APPEAR, DISAPPEAR, CO_LOCATE, SEPARATE, INTERACTION |
| 08 | `08_generate_event_summaries.py` | `events.csv` | Generate natural language summaries with spatial context |
| 09 | `09_build_egg_graph.py` | `egg_graph.json` | Assemble full EGG graph (rooms, objects, events, edges) |
| 10 | `10_prune_egg_graph.py` | `pruned_subgraph.json` | Query-driven subgraph retrieval |
| 11 | `11_export_neo4j_csv.py` | `neo4j/*.csv` | Export 6 CSVs ready for Neo4j import |
| 12 | `12_demo_queries.py` | `demo_query_results.json` | Run demo natural language queries against the EGG |
| 13 | `13_visualize_3d_debug.py` | `debug_pointclouds/` | 3D point cloud screenshots for debugging |
| 14 | `14_import_neo4j.py` | _(Neo4j Aura)_ | Push CSVs directly to Neo4j via Bolt driver — no manual steps |

---

## Data Format

Quest 3 captures must be a flat folder containing per-frame file triples:

```
quest_capture/
  frame_000001_<ticks>_640x480.rgba     # RGBA32 raw bytes
  frame_000001_<ticks>_depth.npy        # float32 depth in metres
  frame_000001_<ticks>_meta.json        # pose + dimensions
  frame_000002_<ticks>_640x480.rgba
  frame_000002_<ticks>_depth.npy
  frame_000002_<ticks>_meta.json
  ...
```

`meta.json` structure:
```json
{
  "frame_index": 1,
  "ticks": 639088157281460250,
  "pose": {
    "position": [x, y, z],
    "rotation_xyzw": [rx, ry, rz, rw]
  },
  "rgb": { "width": 640, "height": 480 },
  "depth": { "count": 204800 }
}
```

Frames without a matching `_depth.npy` are processed for detection but skipped during 3D backprojection.

---

## Configuration

### `configs/pipeline.yaml`

| Key | Default | Description |
|-----|---------|-------------|
| `session_id` | `session_001` | Session label (used for output paths) |
| `raw_data_root` | `../Quest_Capture/quest_capture` | Path to raw capture folder |
| `observations_source` | `grounding_dino` | Detection backend: `grounding_dino`, `yolo`, `depth_blobs` |
| `grounding_dino_model` | `IDEA-Research/grounding-dino-base` | HuggingFace model ID (auto-downloaded ~700 MB) |
| `detection_prompt` | `"monitor. laptop. mouse. hands."` | Dot-separated list of objects to detect |
| `default_room_id` | `workstation_A` | Room label for all objects in this session |

### `configs/thresholds.yaml`

| Key | Default | Effect |
|-----|---------|--------|
| `tracking.max_spatial_jump_m` | `0.8` | Max 3D distance to link two observations as the same track |
| `tracking.class_must_match` | `true` | Only link observations of the same semantic class |
| `events.min_move_distance_m` | `0.05` | Minimum displacement to register as a MOVE event |
| `events.near_threshold_m` | `0.5` | Distance threshold for CO_LOCATE / INTERACTION events |
| `grounding_dino.box_threshold` | `0.30` | Objectness score cutoff — lower = more detections, more noise |
| `grounding_dino.text_threshold` | `0.25` | Label match score cutoff — lower = looser semantic matching |

---

## Object Detection Backends

### Grounding DINO (default, recommended)

Open-vocabulary zero-shot detection. You define what to look for via `detection_prompt`.

```yaml
observations_source: grounding_dino
detection_prompt: "laptop. mouse. hands. coffee cup. notebook."
```

The model is downloaded automatically from HuggingFace on first run (~700 MB). No GPU required but runs faster with one. Set `HF_TOKEN` in your environment or local `.env` file for higher rate limits and fewer download interruptions.

**Tuning tips:**
- Raise `box_threshold` (e.g. `0.40`) to reduce false positives
- Lower `text_threshold` (e.g. `0.20`) to pick up objects the model is less confident naming
- Add specific labels for objects in your scene — the prompt is your vocabulary

Detection overlays (bounding boxes + confidence scores per frame) are saved to:
```
data/processed/<session>/graphs/debug_boxes/frame_XXXXXX_detections.png
```

### YOLO

```yaml
observations_source: yolo
yolo_model: yolov8n.pt
```

Requires: `pip install ultralytics`. Limited to COCO 80-class vocabulary.

### Depth Blobs (fallback)

```yaml
observations_source: depth_blobs
```

No model required. Segments depth discontinuities as anonymous blobs. Useful for quick pipeline validation.

---

## EGG Graph Structure

The EGG (Event-Grounded Graph) is stored as `egg_graph.json`:

```
rooms[]
  └─ objects[]                     # each object track (persistent identity across frames)
       └─ time_variant_history[]   # per-frame 3D position + confidence
events[]                           # MOVE, APPEAR, DISAPPEAR, CO_LOCATE, SEPARATE, INTERACTION
event_edges[]                      # Event → Object role assignments
temporal_edges[]                   # Event → Event BEFORE relationships
```

### Event Types

| Type | Trigger |
|------|---------|
| `APPEAR` | Object first observed in scene |
| `DISAPPEAR` | Object absent for N consecutive frames |
| `MOVE` | Object track displaces > `min_move_distance_m` |
| `CO_LOCATE` | Two objects come within `near_threshold_m` of each other |
| `SEPARATE` | Two previously near objects move apart |
| `INTERACTION` | `hands` track comes within `near_threshold_m` of another object |

---

## Neo4j Import

Script `14_import_neo4j.py` pushes data directly to Neo4j Aura via the Bolt driver — no manual CSV upload needed.

```bash
python scripts/14_import_neo4j.py --session session_001
```

It will:
1. Connect using credentials from `.env`
2. Clear all existing data for that session's room
3. Import rooms, objects, events, and all relationships
4. Print a summary of nodes and edges created

### Graph schema in Neo4j

```
(:Room)-[:CONTAINS]->(:Object)
(:Event)-[:INVOLVES {role}]->(:Object)
(:Event)-[:BEFORE]->(:Event)
```

Node labels: `Room`, `Object`, `Event`

---

## Example Queries

Run these in the [Neo4j Browser](https://browser.neo4j.io) after importing.

```cypher
-- All event types and counts
MATCH (e:Event) RETURN e.event_type, count(*) ORDER BY count(*) DESC

-- Full interaction timeline (what did the hands touch and when?)
MATCH (e:Event {event_type:"INTERACTION"})-[:INVOLVES {role:"target_object"}]->(o)
RETURN round(toFloat(e.start_ts_ns)/1e9, 2) AS sec, o.semantic_class, e.summary
ORDER BY e.start_ts_ns

-- Sequence of events in the first 30 seconds
MATCH (e:Event)
WHERE e.start_ts_ns <= 30000000000
WITH e ORDER BY e.start_ts_ns
MATCH (e)-[:INVOLVES]->(o:Object)
RETURN e.event_type, e.summary, o.semantic_class,
       round(toFloat(e.start_ts_ns)/1e9, 2) AS time_sec
ORDER BY e.start_ts_ns

-- Objects co-located and what happened next
MATCH (e1:Event {event_type:"CO_LOCATE"})-[:INVOLVES]->(a:Object)
MATCH (e1)-[:INVOLVES]->(b:Object)
WHERE a.track_id < b.track_id
OPTIONAL MATCH (e1)-[:BEFORE]->(e2:Event)
RETURN a.semantic_class, b.semantic_class, e1.summary, e2.event_type, e2.summary

-- Subgraph: hands and everything it touched (switch to Graph view in Browser)
MATCH (e:Event {event_type:"INTERACTION"})-[:INVOLVES]->(n)
RETURN e, n
```

---

## Project Structure

```
XR_Pipeline/
├── configs/
│   ├── pipeline.yaml          # session config, detection backend, prompts
│   └── thresholds.yaml        # tracking, event, and detection thresholds
├── scripts/
│   ├── 00_bootstrap_repo.py
│   ├── 01_build_frame_manifest.py
│   ├── 02_validate_manifest.py
│   ├── 03_visualize_rgb_depth_pose.py
│   ├── 04_ingest_spatialobjects.py
│   ├── 05_build_object_observations.py
│   ├── 06_link_object_tracks.py
│   ├── 07_build_event_windows.py
│   ├── 08_generate_event_summaries.py
│   ├── 09_build_egg_graph.py
│   ├── 10_prune_egg_graph.py
│   ├── 11_export_neo4j_csv.py
│   ├── 12_demo_queries.py
│   ├── 13_visualize_3d_debug.py
│   └── 14_import_neo4j.py     # automated Neo4j push via Bolt
├── src/
│   ├── config.py              # PipelinePaths, config loading
│   ├── io_utils.py            # Quest 3 file scanning and loading
│   ├── depth_utils.py         # depth decoding and blob detection
│   ├── pose_utils.py          # quaternion → rotation matrix, world projection
│   ├── geometry.py            # spatial_relation(), distance helpers
│   ├── objects.py             # Grounding DINO + YOLO detection wrappers
│   ├── tracking.py            # observation → track linking
│   ├── events.py              # event detection state machines
│   ├── egg.py                 # EGG graph assembly and serialisation
│   ├── pruning.py             # query-driven subgraph retrieval
│   ├── neo4j_export.py        # CSV export logic
│   └── viz.py                 # detection overlays, depth visualisation
├── data/
│   ├── raw/                   # symlink or copy of Quest captures (gitignored)
│   └── processed/
│       └── <session_id>/
│           ├── manifests/     # frame_manifest.csv, validation report
│           ├── objects/       # observations, tracks
│           ├── events/        # event windows, summaries
│           ├── graphs/        # egg_graph.json, debug overlays
│           └── neo4j/         # 6 CSV files for Neo4j
├── tests/
├── .env                       # credentials (gitignored)
├── .env.example               # template — copy to .env and fill in
└── requirements.txt
```

---

## Running Tests

```bash
cd XR_Pipeline
python -m pytest tests/ -v
```
