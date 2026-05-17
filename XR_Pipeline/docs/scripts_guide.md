# XR Pipeline — Scripts Guide

A plain-English reference for every script in the pipeline: what it does, why it exists, what it produces, and exactly where those outputs are saved.

---

## What is "Legacy"?

> **Legacy** means a script (or folder) was written for an older version of the pipeline that no longer applies to how the project works today. It is kept around so that old data captured under that older approach can still be processed, but it plays no role in normal day-to-day use. You can safely ignore legacy items unless you are specifically working with old data.

---

## Where Does Raw Data Come From?

The pipeline reads raw Quest 3 captures directly from:

```
Quest_Capture/quest_capture/
```

This is the **only** folder you need to put new captures into. The `data/raw/` folder that exists inside `XR_Pipeline/` is **not used** by the active pipeline — it is a leftover from an earlier design and only exists because script 00 creates it as an empty placeholder.

All pipeline **outputs** are written to:

```
XR_Pipeline/data/processed/<session_id>/
```

---

## Stage 0 — Setup

### `00_bootstrap_repo.py`

**What it does**
Creates all the folders the pipeline needs to save its outputs, writes documentation files describing each data format, and creates a `.env.example` template where you fill in your database credentials.

**Why it matters**
Without the correct folder structure, every other script will fail when it tries to save its output. Run this once when setting up the project for the first time.

**Legacy note**
This script also creates `data/raw/session_001/` and related subfolders. Those folders are **not used** by the active pipeline — they are a legacy holdover. You do not need to put anything in them.

**Outputs**

| Output | Saved to |
|---|---|
| All processed output folders | `XR_Pipeline/data/processed/session_001/` |
| Schema documentation files | `XR_Pipeline/docs/schemas/` |
| Design notes | `XR_Pipeline/docs/decisions/design_notes.md` |
| Credentials template | `XR_Pipeline/.env.example` |

---

## Stage 1 — Ingest the Raw Footage

### `01_build_frame_manifest.py`

**What it does**
Scans the raw Quest 3 capture folder and builds a master spreadsheet with one row per recorded frame. Each row records the path to the colour image, the path to the depth image, the exact timestamp, where the headset was in the room (its "pose"), and technical camera settings (focal length, image size, etc.).

**Why it matters**
Every subsequent script uses this spreadsheet as its table of contents for the recording. Nothing else in the pipeline can run without it.

**Reads from**
`Quest_Capture/quest_capture/`

**Outputs**

| Output | Saved to |
|---|---|
| `frame_manifest.csv` | `data/processed/<session>/manifests/frame_manifest.csv` |

---

### `02_validate_manifest.py`

**What it does**
Acts as a quality inspector for the manifest. It checks that image files actually exist on disk, that timestamps only go forward in time, that camera positions do not jump impossibly far between frames, and that a sample of images can actually be decoded and read.

**Why it matters**
Catches data problems early, before you waste time running expensive AI detection on broken data. Any warning or error here should be investigated before continuing.

**Reads from**
`data/processed/<session>/manifests/frame_manifest.csv`

**Outputs**

| Output | Saved to |
|---|---|
| `manifest_validation.json` | `data/processed/<session>/manifests/manifest_validation.json` |

---

### `03_visualize_rgb_depth_pose.py`

**What it does**
Picks a handful of evenly-spaced sample frames and saves visual images so you can see whether the data looks correct with your own eyes:
- A side-by-side image of the colour frame and its depth map
- A 3D point cloud image showing the scene geometry for frames that have depth data
- A top-down map showing the path the headset took through the room

**Why it matters**
A visual sanity check. If the images look distorted or the trajectory looks impossible, there is likely a problem with the raw data or camera calibration settings.

**Reads from**
`data/processed/<session>/manifests/frame_manifest.csv` and the raw image/depth files referenced within it

**Outputs**

| Output | Saved to |
|---|---|
| Per-frame RGB + depth overlay PNGs | `data/processed/<session>/manifests/sample_visualizations/frame_XXXXXX_rgb_depth.png` |
| Per-frame 3D point cloud PNGs | `data/processed/<session>/graphs/debug_pointclouds/frame_XXXXXX_pointcloud.png` |
| Camera trajectory map | `data/processed/<session>/manifests/sample_visualizations/camera_trajectory.png` |

---

## Stage 2 — Detect Objects

### `04_ingest_spatialobjects.py` ⚠️ Legacy

**What it does**
Looks for an old-format file called `spatialobjects.csv` inside `data/raw/<session>/spatialobjects/`. If found, it translates that file into the modern `object_observations.csv` format so the rest of the pipeline can use it. If no such file is found, it does nothing and exits.

**Legacy note**
This script exists solely for backward compatibility with captures made under an older version of the pipeline that produced a `spatialobjects.csv` file. **Current captures do not produce this file.** If you are working with a recent Quest 3 capture, this script will find nothing and skip itself — that is expected and correct. Use script 05 instead.

**Reads from**
`data/raw/<session>/spatialobjects/spatialobjects*.csv` (legacy location, not used in current workflow)

**Outputs**

| Output | Saved to |
|---|---|
| `object_observations.csv` (only if legacy file found) | `data/processed/<session>/objects/object_observations.csv` |

---

### `05_build_object_observations.py`

**What it does**
The main object detection step. For every frame in the recording, an AI model looks at the colour image, finds objects, and uses the depth data to calculate where each object actually is in 3D space (real-world X, Y, Z coordinates in metres).

Three detection backends are available, configured via `configs/pipeline.yaml`:

| Backend | How it works | When to use |
|---|---|---|
| `grounding_dino` *(default)* | You give it a text prompt (e.g. `"object."`) and it finds anything matching — very flexible, no fixed class list | Most captures |
| `yolo` | Detects from a fixed list of 80 common classes (laptop, chair, person, etc.) | When you want faster, fixed-category detection |
| `depth_blobs` | Finds distinct lumps in the depth image — no AI model, no labels | Fastest option; use when you have no GPU or need a quick check |

**Reads from**
`data/processed/<session>/manifests/frame_manifest.csv` and the raw image/depth files it references

**Outputs**

| Output | Saved to |
|---|---|
| `object_observations.csv` (one row per detected object per frame) | `data/processed/<session>/objects/object_observations.csv` |
| Detection overlay debug images | `data/processed/<session>/graphs/debug_boxes/frame_XXXXXX_detections.png` |

---

## Stage 3 — Track Objects Over Time

### `06_link_object_tracks.py`

**What it does**
Takes all the individual per-frame detections and connects them into continuous tracks. It decides that the "laptop" spotted in frame 100 and the "laptop" spotted in frame 110 are the same physical object, as long as it has not moved too far and has not been absent for too long between frames.

**Why it matters**
Without tracking, you just have a pile of unconnected sightings. Tracking gives each physical object a persistent identity across the whole recording, which is what makes it possible to say things like "the cup was seen for 30 seconds and then disappeared."

**Reads from**
`data/processed/<session>/objects/object_observations.csv`

**Outputs**

| Output | Saved to |
|---|---|
| `object_tracks.csv` (each detection row gains a `track_id`) | `data/processed/<session>/objects/object_tracks.csv` |
| `track_summary.csv` (one row per unique object: first/last seen, average position) | `data/processed/<session>/objects/track_summary.csv` |
| `track_debug.json` (debug info) | `data/processed/<session>/objects/track_debug.json` |

---

## Stage 4 — Detect Events

### `07_build_event_windows.py`

**What it does**
Looks at how tracked objects moved over time and identifies moments when something interesting happened: an object moved significantly, something appeared, something disappeared, or two things came close together. Each such moment is called an **event window**.

**Why it matters**
This is the jump from "a list of objects" to "a story of what happened." Events are the building blocks of understanding activity in the scene.

**Reads from**
`data/processed/<session>/objects/object_tracks.csv`

**Outputs**

| Output | Saved to |
|---|---|
| `event_windows.csv` (each event: type, start/end time, objects involved) | `data/processed/<session>/events/event_windows.csv` |

---

### `08_generate_event_summaries.py`

**What it does**
Enriches the raw events with two additions:
1. A human-readable text description of what happened (e.g. *"laptop moved 0.3 m in workstation_A"*)
2. Role assignments — which object was the primary mover, which was nearby, etc.

Also computes the 3D position where each event occurred (the average position of the objects involved).

**Why it matters**
Turns machine-readable event codes into something a person or an AI assistant can understand and reason about.

**Reads from**
`data/processed/<session>/events/event_windows.csv` and `data/processed/<session>/objects/object_tracks.csv`

**Outputs**

| Output | Saved to |
|---|---|
| `events.csv` (events with text summaries and 3D positions) | `data/processed/<session>/events/events.csv` |
| `event_object_roles.csv` (which object played what role in each event) | `data/processed/<session>/events/event_object_roles.csv` |

---

## Stage 5 — Build the Knowledge Graph

### `09_build_egg_graph.py`

**What it does**
Assembles all pipeline outputs into a single structured **Event-Grounding Graph (EGG)**. The graph has:
- **Nodes**: rooms, objects, events
- **Edges**: connections between them — *this room contains this object*, *this event involves this object*, *this event happened before that event*

**Why it matters**
A graph structure makes it possible to answer questions by following connections rather than just scanning rows in a spreadsheet. It is the primary queryable output of the pipeline.

**Reads from**
`object_tracks.csv`, `events.csv`, `event_object_roles.csv`

**Outputs**

| Output | Saved to |
|---|---|
| `egg_graph.json` | `data/processed/<session>/graphs/egg_graph.json` |

---

### `09b_build_scene_state_package.py`

**What it does**
Produces a clean, standardised summary document called the **Scene State Package (SSP)** that an AI reasoning layer can read without needing to know anything about the camera, depth sensor, or detection model used. It contains:
- **Entities** — the persistent tracked objects
- **Relations** — accepted facts about how objects relate to each other
- **Hypotheses** — uncertain candidate events or low-confidence relations
- **Observations** — the raw detector hits kept for traceability
- **Provenance** — a record of where each piece of information came from

Think of it as a clean briefing document for an AI agent, completely decoupled from the hardware details of how the data was captured.

**Reads from**
`object_tracks.csv`, `object_observations.csv`, `event_windows.csv`, `events.csv` (optional enrichment), `event_object_roles.csv`

**Outputs**

| Output | Saved to |
|---|---|
| `scene_state_package.json` | `data/processed/<session>/graphs/scene_state_package.json` |

---

## Stage 6 — Query the Graph

### `10_prune_egg_graph.py`

**What it does**
Lets you ask a natural language question (e.g. *"What moved?"*) and returns a text answer plus a pruned mini-graph containing only the objects and events relevant to that question.

**Why it matters**
The full EGG graph can be large. This focuses it down to only what is relevant to the question at hand, which makes further reasoning faster and cleaner.

**Reads from**
`data/processed/<session>/graphs/egg_graph.json`

**Outputs**

| Output | Saved to |
|---|---|
| `pruned_subgraph.json` | `data/processed/<session>/queries/pruned_subgraph.json` |
| `query_answer.json` (the question and its text answer) | `data/processed/<session>/queries/query_answer.json` |

---

### `12_demo_queries.py`

**What it does**
Runs a batch of pre-written demo questions against the EGG graph to show the pipeline working end-to-end. Example questions include *"What moved?"*, *"Where was the laptop last seen?"*, and *"Which events happened in workstation_A?"*

**Why it matters**
A quick way to verify that the whole pipeline produced a useful and sensible result after a new capture has been processed.

**Reads from**
`data/processed/<session>/graphs/egg_graph.json`

**Outputs**

| Output | Saved to |
|---|---|
| `demo_query_results.json` (all Q&A pairs) | `data/processed/<session>/queries/demo_query_results.json` |

---

## Stage 7 — Export to Database

### `11_export_neo4j_csv.py`

**What it does**
Converts the EGG graph and, when available, the current assembly graph into CSV files formatted for Neo4j (a graph database), and writes a Cypher script containing the database import commands.

**Why it matters**
Neo4j lets you visually browse and query the graph in a web interface, and run complex graph queries that would be awkward to do in Python alone.

**Reads from**
`data/processed/<session>/graphs/egg_graph.json`

If present, it also reads:
`data/processed/<session>/graphs/assembly_graph.json`

**Outputs**

| Output | Saved to |
|---|---|
| `nodes_rooms.csv` | `data/processed/<session>/neo4j/` |
| `nodes_objects.csv` | `data/processed/<session>/neo4j/` |
| `nodes_events.csv` | `data/processed/<session>/neo4j/` |
| `edges_room_object.csv` | `data/processed/<session>/neo4j/` |
| `edges_event_object.csv` | `data/processed/<session>/neo4j/` |
| `edges_before.csv` | `data/processed/<session>/neo4j/` |
| `nodes_assembly.csv` | `data/processed/<session>/neo4j/` |
| `edges_assembly.csv` | `data/processed/<session>/neo4j/` |
| `import_egg.cypher` (database import commands) | `XR_Pipeline/neo4j/import_egg.cypher` |

---

### `14_import_neo4j.py`

**What it does**
Connects directly to your Neo4j Aura (cloud) database and pushes all the CSV files in. It imports the EGG layer and, when `nodes_assembly.csv` plus `edges_assembly.csv` are present, the current assembly graph layer as `AssemblyNode` nodes with typed relationships. It safely clears existing data for the session before importing, so re-running it is always safe and will not create duplicates.

Requires `NEO4J_URI` and `NEO4J_PASSWORD` to be set in your `.env` file.

**Reads from**
`data/processed/<session>/neo4j/*.csv` (produced by script 11)

**Outputs**
Data is written directly into your **Neo4j Aura database**. No local files are created.

---

## Stage 8 — Debug Visualisation

### `13_visualize_3d_debug.py`

**What it does**
Generates 3D point cloud images from the depth data — a 3D photograph of the scene from each sample frame — and a merged view combining multiple frames together. Optionally opens an interactive 3D window if you have a display connected.

**Why it matters**
Useful for diagnosing problems at the geometry level. If the 3D reconstruction looks broken or wildly wrong, there is likely an issue with the depth data or camera calibration.

**Reads from**
`data/processed/<session>/manifests/frame_manifest.csv` and the raw image/depth files it references

**Outputs**

| Output | Saved to |
|---|---|
| Per-frame 3D point cloud PNGs | `data/processed/<session>/graphs/debug_pointclouds/frame_XXXXXX_3d.png` |
| Per-frame RGB-depth overlay PNGs | `data/processed/<session>/graphs/debug_pointclouds/frame_XXXXXX_rgbd.png` |
| Merged multi-frame point cloud PNG | `data/processed/<session>/graphs/debug_pointclouds/merged_pointcloud.png` |

---

## Full Pipeline Flow

```
Quest_Capture/quest_capture/     ← put new captures here
        │
        ▼
  00  bootstrap_repo              create folders & docs
  01  build_frame_manifest        index all frames → frame_manifest.csv
  02  validate_manifest           quality check    → manifest_validation.json
  03  visualize_rgb_depth_pose    preview images   → PNGs
        │
  04  ingest_spatialobjects       ⚠️ LEGACY — skip for new captures
  05  build_object_observations   detect objects   → object_observations.csv
  06  link_object_tracks          track over time  → object_tracks.csv
        │
  07  build_event_windows         find events      → event_windows.csv
  08  generate_event_summaries    describe events  → events.csv + roles
        │
  09  build_egg_graph             assemble graph   → egg_graph.json
  09b build_scene_state_package   AI-ready summary → scene_state_package.json
        │
  10  prune_egg_graph             answer a query   → pruned_subgraph + answer
  12  demo_queries                run demo Q&A     → demo_query_results.json
  10e build_assembly_graph        task graph       → assembly_graph.json
        │
  11  export_neo4j_csv            prep for DB      → EGG + assembly Neo4j CSVs
  14  import_neo4j                push to database → Neo4j Aura
        │
  13  visualize_3d_debug          3D debug images  → point cloud PNGs
```
