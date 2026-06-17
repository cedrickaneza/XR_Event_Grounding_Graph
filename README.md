# Grounded Assembly Reasoning

Turning multimodal assembly evidence — **Meta Quest 3 RGB-D + pose captures** and **IndustReal CAD/label data** — into traceable, queryable reasoning over assembly procedures. Sensor captures are grounded into an **Event-Grounding Graph (EGG)**; an **assembly reasoning layer** then infers industrial operation steps, workflow phases, and symbolic assembly state. Results are exported to Neo4j for graph queries.

```
Quest 3 capture → object detection → 3D tracks → events → EGG graph
                                                         → assembly reasoning → Neo4j
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
- [Domain Configuration](#domain-configuration)
- [EGG Graph Structure](#egg-graph-structure)
- [Assembly Reasoning Layer](#assembly-reasoning-layer)
- [Neo4j Import](#neo4j-import)
- [Example Queries](#example-queries)
- [IndustReal Pipeline](#industreal-pipeline)
- [Project Structure](#project-structure)
- [Running Tests](#running-tests)
- [License](#license)

---

## Overview

The pipeline has two layers:

### Base layer — perception and events

1. **Detect** — Grounding DINO, MM-Grounding-DINO, or YOLOv8 detects objects in each RGB frame using a configurable text prompt or vocabulary
2. **Backproject** — Bounding box centres are projected into 3D world space using depth maps and camera pose
3. **Track** — Observations are linked across frames into persistent object tracks using spatial + class similarity
4. **Detect events** — Tracks are analysed to produce MOVE, APPEAR, DISAPPEAR, CO_LOCATE, SEPARATE, and INTERACTION events
5. **Build graph** — Events and objects are assembled into an EGG graph (JSON)
6. **Export** — Graph is exported to Neo4j-ready CSVs and pushed to Neo4j Aura

### Assembly reasoning layer — industrial process understanding

7. **State facts** — Raw events and operations are lifted into time-scoped symbolic predicates (`holding(hand, lego)`, `released(lego)`, `co_held(a, b)`, …)
8. **Operation events** — Higher-level operations are inferred: PICK_UP, PUT_DOWN, HOLD, CONTACT, PLACE_ONTO_CANDIDATE, INSERT_CANDIDATE, ATTACH_CANDIDATE, …
9. **Workflow timeline** — Operation clusters are segmented into workflow phases (manipulation → hold → placement → idle, …)
10. **Subtask events** — Assembly subtasks (pick_up_part, insert_part, attach_part, …) are inferred from facts + operations + domain config
11. **Assembly graph** — A rich typed graph (objects, relation_facts, subtasks, subgoals, phases, constraints) integrates all layers
12. **Assembly review** — Symbolic reasoner answers queries: what step is active, what is achieved, what is blocked, likely next, full evidence traces

---

## Requirements

- Python 3.10+
- A Meta Quest 3 capture folder (see [Data Format](#data-format))
- A [Neo4j Aura Free](https://neo4j.com/cloud/platform/aura-graph-database/) instance (for graph import)

Install Python dependencies:

```bash
pip install -r XR_Pipeline/requirements.txt
```

Key packages: `transformers`, `torch`, `pandas`, `numpy`, `networkx`, `neo4j`, `typer`, `rich`

---

## Setup

```bash
git clone <repo-url>
cd grounded-assembly-reasoning

pip install -r XR_Pipeline/requirements.txt

# Copy and fill in your Neo4j Aura credentials
cp XR_Pipeline/.env.example XR_Pipeline/.env
# Edit .env with your NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# Bootstrap output directories
cd XR_Pipeline
python scripts/00_bootstrap_repo.py
```

### .env

```
NEO4J_URI=neo4j+s://<instance-id>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password-here
```

Find these in the [Neo4j Aura console](https://console.neo4j.io) → your instance → **Inspect**.

> `.env` is gitignored. Never commit real credentials.

---

## Running a Session

### New capture

1. Place your Quest 3 capture folder at any path, e.g.:
   ```
   Quest_Capture/session_003/quest_capture/
   ```

2. Update `XR_Pipeline/configs/pipeline.yaml`:
   ```yaml
   raw_data_root: "../Quest_Capture/session_003/quest_capture"
   domain_config: configs/domain_lego.yaml   # or your own domain
   ```

3. Run all pipeline scripts from inside `XR_Pipeline/`:

   **Base pipeline:**
   ```bash
   cd XR_Pipeline

   python scripts/01_build_frame_manifest.py      --session session_003
   python scripts/02_validate_manifest.py         --session session_003
   python scripts/05_build_object_observations.py --session session_003
   python scripts/06_link_object_tracks.py        --session session_003
   python scripts/07_build_event_windows.py       --session session_003
   python scripts/08_generate_event_summaries.py  --session session_003
   python scripts/09_build_egg_graph.py           --session session_003
   python scripts/10_prune_egg_graph.py           --session session_003
   python scripts/11_export_neo4j_csv.py          --session session_003
   python scripts/14_import_neo4j.py              --session session_003
   ```

   **Assembly reasoning layer** (run after the base pipeline):
   ```bash
   python scripts/09b_build_scene_state_package.py  --session session_003
   python scripts/09c_build_state_facts.py          --session session_003
   python scripts/10b_build_operation_events.py     --session session_003
   python scripts/10c_build_workflow_timeline.py    --session session_003
   python scripts/10d_build_subtask_events.py       --session session_003
   python scripts/09d_build_assembly_state_package.py --session session_003
   python scripts/10e_build_assembly_graph.py       --session session_003
   python scripts/11b_build_assembly_review.py      --session session_003
   ```

4. Query in Neo4j Browser or run demo queries:
   ```bash
   python scripts/12_demo_queries.py --session session_003
   ```

Each session's output is fully isolated under `XR_Pipeline/data/processed/<session_id>/`.

---

## Pipeline Stages

### Base pipeline

| # | Script | Key Output | Description |
|---|--------|-----------|-------------|
| 00 | `00_bootstrap_repo.py` | directories | Create output folder structure |
| 01 | `01_build_frame_manifest.py` | `frame_manifest.csv` | Scan capture, build canonical frame list with pose + timestamps |
| 02 | `02_validate_manifest.py` | `manifest_validation.json` | Verify files exist, depth decodes correctly, poses are valid |
| 03 | `03_visualize_rgb_depth_pose.py` | `sample_visualizations/` | RGB/depth overlays and pose trajectory plots |
| 04 | `04_ingest_spatialobjects.py` | _(optional)_ | Ingest `spatialobjects.csv` from Quest if present |
| 05 | `05_build_object_observations.py` | `object_observations.csv` | Detect objects per frame (multi-pass groups), backproject to 3D, save detection overlays |
| 06 | `06_link_object_tracks.py` | `object_tracks.csv` | Link per-frame observations into persistent tracks |
| 07 | `07_build_event_windows.py` | `event_windows.csv` | Detect MOVE, APPEAR, DISAPPEAR, CO_LOCATE, SEPARATE, INTERACTION |
| 08 | `08_generate_event_summaries.py` | `events.csv` | Generate natural language summaries with spatial context |
| 09 | `09_build_egg_graph.py` | `egg_graph.json` | Assemble full EGG graph (rooms, objects, events, edges) |
| 10 | `10_prune_egg_graph.py` | `pruned_subgraph.json` | Query-driven subgraph retrieval |
| 11 | `11_export_neo4j_csv.py` | `neo4j/*.csv` | Export CSVs ready for Neo4j import |
| 11 | `11_build_operation_review.py` | `operation_review.json` | Human-readable summary of detected operations |
| 12 | `12_demo_queries.py` | `demo_query_results.json` | Run demo natural language queries against the EGG |
| 13 | `13_visualize_3d_debug.py` | `debug_pointclouds/` | 3D point cloud screenshots for debugging |
| 14 | `14_import_neo4j.py` | _(Neo4j Aura)_ | Push CSVs directly to Neo4j via Bolt driver |

### Assembly reasoning layer

| # | Script | Key Output | Description |
|---|--------|-----------|-------------|
| 09b | `09b_build_scene_state_package.py` | `scene_state_package.json` | Normalised reasoning-layer input (entity-centric, relation-centric, provenance-preserving) |
| 09c | `09c_build_state_facts.py` | `state_facts.csv` | Formal time-scoped predicates (`holding`, `released`, `co_held`, `in_contact`, …) |
| 09d | `09d_build_assembly_state_package.py` | `assembly_state_package.json` | Consolidated assembly reasoning inputs: active facts, achieved subgoals, blocked subtasks, constraint satisfaction |
| 10b | `10b_build_operation_events.py` | `operation_events.csv` | Higher-level operations: PICK_UP, PUT_DOWN, HOLD, APPROACH, CONTACT, TRANSFER, USE_TOOL, PLACE_ONTO_CANDIDATE, INSERT_CANDIDATE, ALIGN_CANDIDATE, ATTACH_CANDIDATE |
| 10c | `10c_build_workflow_timeline.py` | `workflow_timeline.json` | Session-level phase segmentation (manipulation → hold → placement → idle, …) |
| 10d | `10d_build_subtask_events.py` | `subtask_events.csv` | Step-level inference: pick_up_part, insert_part, attach_part, … |
| 10e | `10e_build_assembly_graph.py` | `assembly_graph.json` | Typed assembly graph (objects, facts, subtasks, subgoals, phases, constraints) |
| 11b | `11b_build_assembly_review.py` | `assembly_review.json` | Symbolic reasoning report: what step, achieved subgoals, blocked steps, likely next, evidence traces |

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
| `session_id` | `session_003` | Session label (used for output paths) |
| `raw_data_root` | `../Quest_Capture/quest_capture` | Path to raw capture folder |
| `observations_source` | `grounding_dino` | Detection backend: `grounding_dino`, `mm_grounding_dino`, `yolo`, `depth_blobs` |
| `grounding_dino_model` | `IDEA-Research/grounding-dino-base` | HuggingFace model ID (auto-downloaded ~700 MB) |
| `mm_grounding_dino_model` | `openmmlab-community/mm_grounding_dino_base_all` | MM-Grounding-DINO model (~830 MB) |
| `detection_prompt` | `"a red lego brick. a blue lego brick."` | Fallback dot-separated object list (overridden by `object_vocabulary`) |
| `detection_groups` | see below | Multi-pass groups — separate detector pass per role (hands, workpieces, tools, fixtures) |
| `object_vocabulary` | _(in yaml)_ | Per-class prompts, aliases, and role assignments |
| `domain_config` | `configs/domain_lego.yaml` | Path to domain YAML (see [Domain Configuration](#domain-configuration)) |
| `default_room_id` | `workstation_A` | Room label for all objects in this session |
| `offline_mode` | `false` | Force HuggingFace `local_files_only` (after model is cached) |
| `stereo_eye` | `left` | For Quest 3 sparse stereo buffers: `left` or `right` |

### `configs/thresholds.yaml`

| Key | Default | Effect |
|-----|---------|--------|
| `tracking.max_spatial_jump_m` | `0.8` | Max 3D distance to link two observations as the same track |
| `tracking.class_must_match` | `true` | Only link observations of the same semantic class |
| `events.min_move_distance_m` | `0.05` | Minimum displacement to register as a MOVE event |
| `events.near_threshold_m` | `0.5` | Distance threshold for CO_LOCATE / INTERACTION events |
| `grounding_dino.box_threshold` | `0.30` | Objectness score cutoff |
| `grounding_dino.text_threshold` | `0.25` | Label match score cutoff |

---

## Object Detection Backends

### Grounding DINO (default)

Open-vocabulary zero-shot detection. Objects are defined in `object_vocabulary` (per-class prompts + role) or via `detection_prompt`.

```yaml
observations_source: grounding_dino
```

~700 MB, auto-downloaded from HuggingFace on first run.

### MM-Grounding-DINO

Trained on Objects365 + COCO + RefCOCO — better recall on small objects and hands.

```yaml
observations_source: mm_grounding_dino
```

~830 MB, auto-downloaded on first run.

### YOLOv8

Fast inference with a pretrained or fine-tuned YOLO model. Useful when a domain-specific model is available.

```yaml
observations_source: yolo
```

### Depth Blobs (fallback, no model required)

```yaml
observations_source: depth_blobs
```

Segments depth discontinuities as anonymous blobs. No model required — useful for quick pipeline validation.

### Multi-pass detection groups

To avoid token competition between object roles, define `detection_groups` in `pipeline.yaml`. Each enabled group runs as a separate detector pass with its own prompt and optionally its own thresholds:

```yaml
detection_groups:
  hands:
    enabled: true
    classes: ["hand"]
    box_threshold: 0.20
    text_threshold: 0.20
  workpieces:
    enabled: true
    classes: ["red_lego", "blue_lego"]
    box_threshold: 0.25
    text_threshold: 0.25
  tools:
    enabled: false
    classes: []
```

Isolated prompts significantly improve per-role recall (e.g. hand detection goes from ~26% to ~85% recall when run separately).

Detection overlays are saved to:
```
XR_Pipeline/data/processed/<session>/graphs/debug_boxes/frame_XXXXXX_detections.png
```

---

## Domain Configuration

Domain configs (`configs/domain_*.yaml`) separate domain-specific knowledge from generic pipeline logic. They define the object vocabulary, role assignments, enabled operations, and role pairings for a specific process — without any code changes.

Two example domains are provided: `domain_lego.yaml` and `domain_industrial_example.yaml`.

```yaml
# configs/domain_lego.yaml (excerpt)
domain_name: "lego_assembly"
domain_version: "1.0"

object_classes:
  - canonical: "red_lego"
    role: workpiece
  - canonical: "hand"
    role: hand

workflow_phases:
  - label: "hold"
    description: "Object held by hand"

enabled_operations:
  - HOLD
  - PICK_UP
  - PUT_DOWN
  - CONTACT
  - INSERT_CANDIDATE
  - ATTACH_CANDIDATE

role_pairings:
  - agent_role: hand
    patient_role: workpiece
    valid_operations: [HOLD, PICK_UP, PUT_DOWN]
  - agent_role: workpiece
    patient_role: workpiece
    valid_operations: [CONTACT, INSERT_CANDIDATE, ATTACH_CANDIDATE]
```

Set `domain_config` in `pipeline.yaml` to activate:

```yaml
domain_config: configs/domain_lego.yaml
```

---

## EGG Graph Structure

The base EGG (`egg_graph.json`) is stored as:

```
rooms[]
  └─ objects[]                     # each object track (persistent identity across frames)
       └─ time_variant_history[]   # per-frame 3D position + confidence
events[]                           # MOVE, APPEAR, DISAPPEAR, CO_LOCATE, SEPARATE, INTERACTION
event_edges[]                      # Event → Object role assignments
temporal_edges[]                   # Event → Event BEFORE relationships
```

### Base event types

| Type | Trigger |
|------|---------|
| `APPEAR` | Object first observed in scene |
| `DISAPPEAR` | Object absent for N consecutive frames |
| `MOVE` | Object track displaces > `min_move_distance_m` |
| `CO_LOCATE` | Two objects come within `near_threshold_m` of each other |
| `SEPARATE` | Two previously near objects move apart |
| `INTERACTION` | `hands` track comes within `near_threshold_m` of another object |

### Operation-level events (assembly layer)

| Type | Trigger |
|------|---------|
| `PICK_UP` | INTERACTION onset + workpiece MOVE shortly after |
| `PUT_DOWN` | Workpiece MOVE end + INTERACTION offset shortly after |
| `HOLD` | Sustained INTERACTION without significant workpiece displacement |
| `APPROACH` | Workpiece track converging toward a fixture over time |
| `CONTACT` | Two objects within contact threshold (CO_LOCATE or closer) |
| `TRANSFER` | Workpiece MOVE with no hand present |
| `USE_TOOL` | Tool-role object proximate to a workpiece for sustained frames |
| `PLACE_ONTO_CANDIDATE` | Workpiece MOVE endpoint within placement proximity of a fixture |
| `INSERT_CANDIDATE` | Workpiece MOVE endpoint within contact range of a fixture |
| `ALIGN_CANDIDATE` | Workpiece reaches alignment tolerance of a fixture without a full MOVE |
| `ATTACH_CANDIDATE` | Sustained contact between workpiece and fixture after a move |

Operations ending in `_CANDIDATE` carry partial evidence and are promoted when additional evidence is available.

---

## Assembly Reasoning Layer

The assembly reasoning stack sits on top of the EGG and turns sensor observations into symbolic assembly state — with no LLM calls and no model downloads.

### State facts (`state_facts.csv`)

Facts are time-scoped symbolic predicates derived from events and operations:

| Category | Examples |
|----------|---------|
| Presence | `present(obj)`, `appeared(obj)`, `disappeared(obj)` |
| Motion | `started_moving(obj)`, `stopped_moving(obj)` |
| Proximity | `near(a,b)`, `touching_candidate(a,b)` |
| Support state | `resting(obj)`, `carried(obj)`, `surface_contact(obj)` |
| Operation-derived | `holding(agent,obj)`, `released(obj)`, `in_contact(a,b)`, `inserted_into_candidate(a,b)`, `placed_on_candidate(a,b)`, `aligned_with_candidate(a,b)`, `attached_to_candidate(a,b)`, `used_tool_on(tool,obj)` |
| Inter-object | `co_held(a,b)`, `co_held_started(a,b)`, `co_held_ended(a,b)` |

Each fact has a status lifecycle: `candidate → active → achieved / invalidated`.

### Assembly graph (`assembly_graph.json`)

The assembly graph layers on top of the EGG with typed nodes and edges:

**Node types:** `object`, `relation_fact`, `subtask`, `subgoal`, `phase`, `constraint`

**Edge types:**

| Edge | Meaning |
|------|---------|
| `involves` | subtask → object |
| `supports` | fact → subtask (evidence) |
| `achieves` | subtask → subgoal |
| `requires` | subtask → constraint |
| `depends_on` | subtask_B → subtask_A (ordering) |
| `evidence_for` | operation → subtask |
| `next_candidate` | subtask_A → subtask_B (likely sequence) |
| `belongs_to_phase` | subtask → phase |

### Assembly reasoner (symbolic, no LLM)

The reasoner (`assembly_reasoner.py`) answers structured queries over the assembly state package:

| Query | Returns |
|-------|---------|
| `what_step_now` | Most recent active or candidate subtask |
| `what_is_achieved` | All achieved subgoals + their evidence chains |
| `what_is_blocked` | Blocked subtasks + unmet dependencies |
| `what_changed` | Facts/subtasks with `start_frame` in the last N frames |
| `likely_next` | Pending subtasks whose prerequisites are all met |
| `why_current_step` | Evidence chain for the active subtask |
| `state_transitions` | Released / support_changed / co_held timeline |
| `what_objects_related` | Inter-object pairwise relations (co_held, in_contact, …) |
| `full_report` | All of the above in one dict |

---

## Neo4j Import

Script `14_import_neo4j.py` pushes data directly to Neo4j Aura via the Bolt driver — no manual CSV upload needed.

```bash
cd XR_Pipeline
python scripts/14_import_neo4j.py --session session_003
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

## IndustReal Pipeline

`IndustReal_Pipeline/` is a standalone sub-pipeline for the [IndustReal](https://github.com/TimSchoonbeek/IndustReal) benchmark dataset — a HoloLens 2 recording of a bolt assembly task.

It ports the IndustReal PSR (Procedure Step Recognition) algorithms (B1, B2, B3) and wraps their output in an EGG-style graph for uniform querying.

### Running the IndustReal demo

```bash
cd IndustReal_Pipeline
pip install -r requirements.txt
python scripts/01_run_demo.py
```

### Architecture

| Module | Role |
|--------|------|
| `src/data_loader.py` | Load IndustReal clips and procedure info |
| `src/psr.py` | PSR algorithms B1, B2, B3 (pure Python, no C extensions) |
| `src/egg_builder.py` | Convert PSR step predictions into EGG-style `StateEvent` + `AssemblyGraph` |

The `AssemblyGraph` produced here uses the same event taxonomy as the XR Pipeline (INSTALL, REMOVE, ERROR, CORRECT) and tracks final component states across the recording.

---

## Project Structure

```
grounded-assembly-reasoning/
├── Quest_Capture/                 # sample Quest 3 capture (session_003, tracked)
│   └── session_003/
│       └── quest_capture/
├── XR_Pipeline/
│   ├── configs/
│   │   ├── pipeline.yaml          # session config, detection backend, vocabulary, domain
│   │   ├── thresholds.yaml        # tracking, event, and detection thresholds
│   │   ├── domain_lego.yaml       # domain config: Lego brick assembly
│   │   └── domain_industrial_example.yaml
│   ├── scripts/
│   │   ├── 00_bootstrap_repo.py
│   │   ├── 01_build_frame_manifest.py
│   │   ├── 02_validate_manifest.py
│   │   ├── 03_visualize_rgb_depth_pose.py
│   │   ├── 04_ingest_spatialobjects.py
│   │   ├── 05_build_object_observations.py
│   │   ├── 06_link_object_tracks.py
│   │   ├── 07_build_event_windows.py
│   │   ├── 08_generate_event_summaries.py
│   │   ├── 09_build_egg_graph.py
│   │   ├── 09b_build_scene_state_package.py
│   │   ├── 09c_build_state_facts.py
│   │   ├── 09d_build_assembly_state_package.py
│   │   ├── 10_prune_egg_graph.py
│   │   ├── 10b_build_operation_events.py
│   │   ├── 10c_build_workflow_timeline.py
│   │   ├── 10d_build_subtask_events.py
│   │   ├── 10e_build_assembly_graph.py
│   │   ├── 11_build_operation_review.py
│   │   ├── 11_export_neo4j_csv.py
│   │   ├── 11b_build_assembly_review.py
│   │   ├── 12_demo_queries.py
│   │   ├── 13_visualize_3d_debug.py
│   │   └── 14_import_neo4j.py
│   ├── src/
│   │   ├── config.py              # PipelinePaths, config loading
│   │   ├── io_utils.py            # Quest 3 file scanning and loading
│   │   ├── depth_utils.py         # depth decoding and blob detection
│   │   ├── pose_utils.py          # quaternion → rotation matrix, world projection
│   │   ├── geometry.py            # spatial_relation(), distance helpers
│   │   ├── vocabulary.py          # object vocabulary management
│   │   ├── domain_config.py       # domain adaptation layer (YAML-driven)
│   │   ├── detectors/             # detector backends (grounding_dino, mm_grounding_dino, yolo, depth_blobs)
│   │   ├── detection_groups.py    # multi-pass detection group runner
│   │   ├── detection_postprocess.py # NMS, confidence filtering
│   │   ├── objects.py             # detection wrappers
│   │   ├── tracking.py            # observation → track linking
│   │   ├── events.py              # event detection state machines
│   │   ├── egg.py                 # EGG graph assembly and serialisation
│   │   ├── operation_events.py    # higher-level operation inference
│   │   ├── workflow_timeline.py   # session-level phase segmentation
│   │   ├── subtask_events.py      # subtask/step inference
│   │   ├── state_facts.py         # formal time-scoped symbolic predicates
│   │   ├── scene_state_package.py # normalised reasoning-layer input contract
│   │   ├── assembly_state_package.py # consolidated assembly reasoning inputs
│   │   ├── assembly_graph.py      # typed assembly graph construction
│   │   ├── assembly_reasoner.py   # symbolic assembly reasoner (no LLM)
│   │   ├── workflow_queries.py    # workflow query helpers
│   │   ├── pruning.py             # query-driven subgraph retrieval
│   │   ├── neo4j_export.py        # CSV export logic
│   │   ├── run_metadata.py        # run metadata tracking
│   │   └── viz.py                 # detection overlays, depth visualisation
│   ├── data/
│   │   └── processed/
│   │       └── <session_id>/
│   │           ├── manifests/
│   │           ├── objects/
│   │           ├── events/
│   │           ├── graphs/
│   │           └── neo4j/
│   ├── tests/
│   ├── .env                       # credentials (gitignored)
│   ├── .env.example               # template
│   └── requirements.txt
├── IndustReal_Pipeline/
│   ├── scripts/
│   │   └── 01_run_demo.py
│   ├── src/
│   │   ├── data_loader.py         # IndustReal clip + procedure loader
│   │   ├── psr.py                 # PSR algorithms B1, B2, B3
│   │   └── egg_builder.py         # StateEvent + AssemblyGraph builder
│   ├── data/                      # IndustReal labels/results/geometries (tracked; raw clips gitignored) — see DATASETS.md
│   ├── results/
│   └── requirements.txt
└── README.md
```

---

## Running Tests

```bash
cd XR_Pipeline
python -m pytest tests/ -v
```

---

## License

This repository's first-party code and original content are licensed under the
**Apache License 2.0** — see [LICENSE](LICENSE).

- Attribution and upstream notices: [NOTICE](NOTICE)
- Data and artifact provenance: [DATASETS.md](DATASETS.md)
- To cite this work: [CITATION.cff](CITATION.cff)

This project adapts code from the Apache-2.0–licensed
[IndustReal](https://github.com/TimSchoonbeek/IndustReal) project (Schoonbeek et
al., WACV 2024). **IndustReal dataset material redistributed here — including RGB
frames, labels, and the STEMFIE-derived part geometries — is _not_ relicensed
under Apache-2.0** and remains subject to its upstream terms. See
[DATASETS.md](DATASETS.md) for details.
