# Pipeline Guide — Grounded Assembly Reasoning

This guide explains the core pipelines in this repository for readers who need to understand, run, maintain, or extend the project.

The repository has two main pipelines:

- `XR_Pipeline`: converts a Quest 3 RGB-D capture into object tracks, events, an Event-Grounded Graph, and assembly reasoning outputs.
- `IndustReal_Pipeline`: converts IndustReal dataset labels and CAD/domain knowledge into procedure steps, graph exports, rule constraints, validations, and a procedural reasoning graph.

The two pipelines have the same research direction: they turn assembly activity into structured graph evidence that can be queried and reasoned about. They differ in their input evidence. `XR_Pipeline` is sensor-first. `IndustReal_Pipeline` is dataset-label/CAD-rule-first.

## Quick Mental Model

```text
XR_Pipeline:
Quest RGB-D capture
  -> frame manifest
  -> object detections
  -> 3D object tracks
  -> primitive events
  -> Event-Grounded Graph
  -> assembly facts, operations, subtasks, review

IndustReal_Pipeline:
IndustReal dataset labels and CAD/domain config
  -> raw clip manifests
  -> oracle evidence
  -> CAD state timeline
  -> procedure steps
  -> graph CSVs
  -> predicates
  -> inferred constraints
  -> validations
  -> procedural reasoning graph
```

## Core Terms

| Term | Easy meaning | Example |
| --- | --- | --- |
| Frame manifest | A table that lists every frame and its files, timestamp, camera settings, and pose. | `frame_manifest.csv` |
| Observation | One detected object in one frame. | A `blue_lego` detection in frame 3 |
| Track | The same object linked across many frames. | `trk_0001` follows the blue Lego |
| Primitive event | A low-level event inferred from tracks. | `APPEAR`, `DISAPPEAR`, `INTERACTION` |
| Event-Grounded Graph | A graph connecting objects, events, rooms, and evidence. | `egg_graph.json` |
| Operation event | A higher-level action inferred from lower-level events. | `HOLD(hand, blue_lego)` |
| State fact | A symbolic fact that is true or candidate over a frame range. | `touching_candidate(hand, blue_lego)` |
| Subtask | A domain-level step inferred from operations and facts. | `hold_part(blue_lego)` |
| Predicate | A symbolic statement used by the rule engine. | `hasAction(step, install)` |
| Constraint | A rule-inferred requirement or expected effect. | `requires installed(base, workspace)` |
| Validation | A decision about whether evidence supports the constraints. | `accepted`, `uncertain`, `rejected` |

## Repository Areas

| Path | Purpose |
| --- | --- |
| `README.md` | High-level overview of the project. |
| `Quest_Capture/session_003/quest_capture` | Input Quest capture for the main XR example. |
| `XR_Pipeline/` | Sensor-first Quest RGB-D pipeline. |
| `XR_Pipeline/configs/` | Configuration for XR data loading, detection, tracking, events, and assembly reasoning. |
| `XR_Pipeline/src/` | Reusable implementation modules for the XR pipeline. |
| `XR_Pipeline/scripts/` | CLI stages for running the XR pipeline end to end. |
| `XR_Pipeline/data/processed/session_003/` | Existing processed output for the main XR example. |
| `IndustReal_Pipeline/` | Dataset/CAD/rule-first industrial assembly pipeline. |
| `IndustReal_Pipeline/configs/` | Dataset, CAD, procedure, and phase configuration. |
| `IndustReal_Pipeline/config/` | Reasoning-layer ontology and rule configuration. |
| `IndustReal_Pipeline/src/` | Reusable implementation modules for the IndustReal pipeline. |
| `IndustReal_Pipeline/scripts/` | CLI stages for running batch processing, graph export, reasoning, and evaluations. |
| `IndustReal_Pipeline/results/` | Existing outputs, reports, graph CSVs, and reasoning artifacts. |

## XR_Pipeline

### Purpose

`XR_Pipeline` takes a Quest 3 capture and turns it into a structured record of what objects appeared, where they were, how they moved, and what assembly-relevant actions likely happened.

The main example is `session_003`, a Lego manipulation sequence.

Important paths:

| Item | Path |
| --- | --- |
| Input capture | `Quest_Capture/session_003/quest_capture` |
| Main config | `XR_Pipeline/configs/pipeline.yaml` |
| Domain config | `XR_Pipeline/configs/domain_lego.yaml` |
| Processed output | `XR_Pipeline/data/processed/session_003` |
| Human-readable review | `XR_Pipeline/data/processed/session_003/reviews/assembly/assembly_review.md` |

### XR Session 003 Result Summary

The existing `session_003` run produced:

| Artifact | Count |
| --- | --- |
| Frames | 61 |
| Object observations | 152 |
| Track rows | 110 |
| Primitive events | 11 |
| Operation events | 3 |
| State facts | 35 |
| Subtasks | 6 |

The strongest result is that the pipeline detects and tracks a hand, a red Lego, and a blue Lego, then infers hand-object holding/release behavior.

The cautious interpretation is important: current session 003 evidence supports holding, release, and co-held candidate behavior, but it does not strongly prove final Lego stacking/contact.

## XR Config Files

| Config file | What it controls | Why it is important | Easy sentence |
| --- | --- | --- | --- |
| `XR_Pipeline/configs/pipeline.yaml` | Main session settings: `session_id`, raw data path, camera intrinsics, depth format, detector backend, object vocabulary, detection groups, and domain config path. | This is the first file to update for a new Quest capture. | "This tells the pipeline where the recording is and what objects to look for." |
| `XR_Pipeline/configs/domain_lego.yaml` | Lego-specific domain knowledge: object roles, enabled operations, workflow phases, subtask templates, subgoal templates, and relation rules. | It tells the reasoning layer how to interpret detections as assembly activity. | "This says what counts as holding, releasing, or co-holding Lego parts." |
| `XR_Pipeline/configs/domain_industrial_example.yaml` | Example industrial domain with tools, fixtures, workpieces, insertion, alignment, and attachment concepts. | It is a template for adapting the XR pipeline to a new industrial assembly domain without rewriting code. | "This is a starting point for a non-Lego workstation." |
| `XR_Pipeline/configs/thresholds.yaml` | Numeric thresholds for tracking, event detection, object detection, confidence filtering, and operation inference. | Small threshold changes can strongly affect tracks, events, and operations. | "This is where you tune how sensitive the pipeline is." |
| `XR_Pipeline/configs/neo4j.yaml` | Default Neo4j connection settings. | Neo4j import uses these defaults and environment overrides. | "This tells the graph importer where Neo4j is." |

### Important XR Config Fields

In `pipeline.yaml`:

| Field | Meaning | When to change it |
| --- | --- | --- |
| `session_id` | Name of the processed-output session. | Change for every new capture. |
| `raw_data_root` | Path to the Quest capture folder. | Change for every new capture. |
| `input_format` | Loader format, currently `quest3_capture` or `custom_csv`. | Change if the source is not a Quest capture. |
| `depth_source` | Depth file type, such as `npy`, `f32`, `alpha_channel`, or `none`. | Change if the new capture stores depth differently. |
| `depth_aligned` | Whether depth pixels line up directly with RGB pixels. | Change if the new device/export gives aligned depth. |
| `stereo_eye` | Which half of a stereo buffer to use. | Change if projections look mirrored or wrong. |
| `camera` | Width, height, intrinsics, depth size, vertical flip. | Change for every new camera or capture format. |
| `observations_source` | Detector backend: Grounding DINO, MM-Grounding-DINO, YOLO, depth blobs, or CSV. | Change when using a different detector. |
| `object_vocabulary` | Canonical classes, prompts, aliases, and object roles. | Change when the objects in the scene change. |
| `detection_groups` | Separate detector passes for groups such as hands and workpieces. | Change to improve recall or add tools/fixtures. |
| `domain_config` | Path to the assembly domain config. | Change when switching from Lego to another task. |

In `thresholds.yaml`:

| Section | Meaning | Easy sentence |
| --- | --- | --- |
| `tracking` | Controls how observations become tracks. | "How far can an object jump and still be considered the same object?" |
| `events` | Controls event detection from tracks. | "How much movement or proximity is enough to count as an event?" |
| `detection` | Shared detection filtering. | "Which detector boxes are too small, too weak, or duplicates?" |
| `grounding_dino` and `mm_grounding_dino` | Detector-specific confidence thresholds. | "How confident must the text-grounded detector be?" |
| `confidence` | Minimum confidence for observations, tracks, and events. | "What low-confidence evidence should be discarded?" |
| `operation_events` | Rules for HOLD, PICK_UP, CONTACT, transfer, placement, alignment, and attachment candidates. | "What low-level evidence is enough to become an assembly operation?" |

## XR Scripts

Run XR scripts from inside `XR_Pipeline`:

```bash
cd XR_Pipeline
python scripts/<script_name>.py --session session_003
```

| Script | What it does | Why it is important | Easy sentence |
| --- | --- | --- | --- |
| `00_bootstrap_repo.py` | Creates expected output folders. | Helps set up a fresh checkout. | "This prepares the folder structure." |
| `01_build_frame_manifest.py` | Scans the raw capture and writes `manifests/frame_manifest.csv`. | Every later stage depends on this canonical frame table. | "This makes the master list of frames." |
| `02_validate_manifest.py` | Checks that manifest rows point to valid files and usable metadata. | Catches missing files and bad capture assumptions early. | "This checks whether the recording can be processed." |
| `03_visualize_rgb_depth_pose.py` | Creates sample RGB/depth/pose visualizations. | Lets a human inspect whether the camera/depth setup looks correct. | "This creates pictures to sanity-check the capture." |
| `04_ingest_spatialobjects.py` | Imports optional `spatialobjects.csv` data when available. | Supports older or alternative data sources. | "This is for captures that already provide object records." |
| `05_build_object_observations.py` | Runs detection, filters boxes, backprojects detections to 3D, and writes `objects/object_observations.csv`. | This is where pixels become labeled 3D evidence. | "This finds objects in each frame." |
| `06_link_object_tracks.py` | Links observations into persistent object tracks. | Reasoning needs object identity over time, not isolated boxes. | "This decides which detections are the same physical object." |
| `07_build_event_windows.py` | Detects primitive event windows from track behavior. | It converts tracks into temporal activity segments. | "This finds when something happened." |
| `08_generate_event_summaries.py` | Writes event records, object roles, and text summaries. | It makes event data readable and graph-ready. | "This turns event windows into named events." |
| `09_build_egg_graph.py` | Builds `graphs/egg_graph.json`. | This is the base graph of rooms, objects, events, and relationships. | "This builds the Event-Grounded Graph." |
| `09b_build_scene_state_package.py` | Normalizes scene state for reasoning. | It separates reasoning from detector-specific details. | "This packages tracks/events into a clean reasoning input." |
| `09c_build_state_facts.py` | Produces symbolic facts such as `present`, `released`, and `touching_candidate`. | Facts are the bridge from numeric tracks/events to symbolic reasoning. | "This turns evidence into predicates over time." |
| `09d_build_assembly_state_package.py` | Consolidates operations, facts, subtasks, and active reasoning state. | The review and assembly graph need a unified state snapshot. | "This creates the current assembly-reasoning snapshot." |
| `10_prune_egg_graph.py` | Builds query-focused subgraphs. | Useful for smaller query examples or retrieval. | "This extracts a smaller graph around a question." |
| `10b_build_operation_events.py` | Infers operations such as HOLD, PICK_UP, PUT_DOWN, CONTACT, and TRANSFER. | This is where low-level events become assembly actions. | "This turns interactions into operations." |
| `10c_build_workflow_timeline.py` | Groups operations into workflow phases. | Helps describe the activity as phases instead of isolated events. | "This says what phase the work is in." |
| `10d_build_subtask_events.py` | Infers domain subtasks from operations and facts. | This is the first explicit step-level assembly output. | "This says which task step was achieved or is a candidate." |
| `10e_build_assembly_graph.py` | Builds the typed assembly graph. | It connects objects, facts, operations, subtasks, subgoals, phases, and constraints. | "This builds the assembly-level graph." |
| `11_build_operation_review.py` | Creates per-operation review files and images. | Helps humans inspect whether detected operations are believable. | "This makes operation evidence easy to review." |
| `11_export_neo4j_csv.py` | Exports graph data to Neo4j CSV files. | Required before importing into Neo4j. | "This converts graph JSON into database import files." |
| `11b_build_assembly_review.py` | Writes the final assembly review. | Best human-readable explanation of a session. | "This summarizes what the system thinks happened." |
| `12_demo_queries.py` | Runs example queries over the graph outputs. | Demonstrates what the graph can answer. | "This shows example questions and answers." |
| `13_visualize_3d_debug.py` | Creates 3D debug visualizations. | Useful when depth, pose, or tracking seems wrong. | "This helps debug the 3D geometry." |
| `14_import_neo4j.py` | Imports exported CSVs into Neo4j. | Publishes the graph for Cypher queries. | "This loads the graph into Neo4j." |
| `check_env.py` | Checks environment and configuration readiness. | Useful before running detector or Neo4j steps. | "This checks whether the runtime is ready." |
| `sweep_grounding_dino.py` | Runs detector prompt/threshold sweeps. | Helps tune object prompts and thresholds for new captures. | "This helps find better detector settings." |

## XR Source Modules

| Module | What it contains | Why it is important | Easy sentence |
| --- | --- | --- | --- |
| `src/config.py` | Config loading and path resolution. | Centralizes where every input/output file should live. | "This tells scripts where to read and write." |
| `src/io_utils.py` | Quest capture scanning, frame loading, depth loading, timestamp handling. | This is the bridge between raw files and pipeline tables. | "This knows how to read the capture." |
| `src/depth_utils.py` | Depth decoding and filtering helpers. | Clean depth is needed for 3D positions. | "This makes depth usable." |
| `src/pose_utils.py` | Pose and transform helpers. | Camera pose is needed to place detections in world coordinates. | "This handles camera position and orientation." |
| `src/geometry.py` | Backprojection and spatial math. | Converts 2D detections plus depth into 3D positions. | "This turns pixels into 3D points." |
| `src/vocabulary.py` | Canonical classes, detector prompts, aliases, and roles. | Keeps labels stable across detector wording variations. | "This maps detector words to project object names." |
| `src/detection_groups.py` | Multi-pass detector group logic. | Improves detection when hands, tools, and workpieces need different prompts. | "This lets different object groups be detected separately." |
| `src/detection_postprocess.py` | Confidence filtering, NMS, duplicate removal, label cleanup. | Prevents noisy detections from becoming tracks. | "This cleans detector outputs." |
| `src/detectors/base.py` | Detector interface contract. | Makes detectors interchangeable. | "This defines what every detector must return." |
| `src/detectors/grounding_dino.py` | Grounding DINO backend. | Main open-vocabulary detector used by session 003. | "This detects objects from text prompts." |
| `src/detectors/mm_grounding_dino.py` | MM-Grounding-DINO backend. | Alternative open-vocabulary detector. | "This is another text-prompt detector option." |
| `src/detectors/yolo.py` | YOLO backend. | Useful when using a trained closed-vocabulary detector. | "This supports YOLO models." |
| `src/detectors/depth_blobs.py` | Depth-only blob detector. | Fast fallback when semantic detection is unavailable. | "This finds object-like blobs from depth." |
| `src/objects.py` | Object observation data structures and helpers. | Common object representation across stages. | "This defines object records." |
| `src/tracking.py` | Track linking logic. | Maintains object identity over time. | "This links detections into tracks." |
| `src/events.py` | Primitive event detection. | Creates APPEAR, DISAPPEAR, MOVE, and interaction events. | "This detects basic events." |
| `src/egg.py` | Event-Grounded Graph construction utilities. | Builds the base graph representation. | "This creates the EGG graph." |
| `src/scene_state_package.py` | Normalized scene package construction. | Gives reasoning a stable input format. | "This packages scene evidence for reasoning." |
| `src/domain_config.py` | Domain config parsing and validation. | Lets reasoning adapt to different assembly domains. | "This reads the task rules." |
| `src/operation_events.py` | Operation inference. | Converts events/tracks into HOLD, PICK_UP, CONTACT, and related operations. | "This infers assembly actions." |
| `src/state_facts.py` | Symbolic fact generation. | Builds predicates used by subtasks and reviews. | "This writes facts like held or released." |
| `src/workflow_timeline.py` | Workflow phase segmentation. | Helps explain activity as phases. | "This groups actions into phases." |
| `src/subtask_events.py` | Subtask inference. | Lifts operations/facts into domain tasks. | "This infers task steps." |
| `src/assembly_state_package.py` | Consolidated assembly state. | Gives the review one package to inspect. | "This summarizes the current assembly state." |
| `src/assembly_graph.py` | Assembly graph builder. | Connects facts, subtasks, subgoals, and phases. | "This builds the assembly reasoning graph." |
| `src/assembly_reasoner.py` | Query/review reasoning. | Produces readable answers from the assembly graph. | "This explains what the system thinks happened." |
| `src/workflow_queries.py` | Query helper functions. | Supports demo/review queries. | "This answers graph questions." |
| `src/pruning.py` | Subgraph pruning utilities. | Helps build smaller query-focused graphs. | "This extracts useful graph slices." |
| `src/neo4j_export.py` | Neo4j CSV export. | Converts graph objects into database import files. | "This prepares graph data for Neo4j." |
| `src/viz.py` | Visualization helpers. | Used for visual sanity checks and debug output. | "This makes debug images." |
| `src/run_metadata.py` | Per-stage metadata writing. | Improves reproducibility by recording what each stage did. | "This records pipeline run details." |

## Running XR Session 003

```bash
cd XR_Pipeline

python scripts/01_build_frame_manifest.py --session session_003
python scripts/02_validate_manifest.py --session session_003
python scripts/05_build_object_observations.py --session session_003
python scripts/06_link_object_tracks.py --session session_003
python scripts/07_build_event_windows.py --session session_003
python scripts/08_generate_event_summaries.py --session session_003
python scripts/09_build_egg_graph.py --session session_003

python scripts/09b_build_scene_state_package.py --session session_003
python scripts/10b_build_operation_events.py --session session_003
python scripts/09c_build_state_facts.py --session session_003
python scripts/10c_build_workflow_timeline.py --session session_003
python scripts/10d_build_subtask_events.py --session session_003
python scripts/09d_build_assembly_state_package.py --session session_003
python scripts/10e_build_assembly_graph.py --session session_003
python scripts/11b_build_assembly_review.py --session session_003
python scripts/11_export_neo4j_csv.py --session session_003
```

Import to Neo4j only after setting credentials:

```bash
python scripts/14_import_neo4j.py --session session_003
```

## Integrating A New XR Capture

A new XR capture should include enough information to build a frame manifest and enough visual/depth data to place detections in 3D.

### Required XR Capture Contents

At minimum, each frame should provide:

| Requirement | Why it is needed |
| --- | --- |
| RGB image or raw RGB/RGBA buffer | Object detection runs on RGB. |
| Frame index | Keeps files and records ordered. |
| Timestamp | Lets events and durations be computed. |
| Camera pose | Needed to place detections in world coordinates. |
| Camera intrinsics | Needed to backproject pixels into 3D. |
| Depth map or other distance source | Needed for 3D object position. |
| Consistent file naming or metadata | The loader must reliably match RGB, depth, and metadata for the same frame. |

For the current Quest loader, the capture is expected to look like this:

```text
quest_capture/
  frame_000001_<ticks>_1280x960.rgba
  frame_000001_<ticks>_meta.json
  frame_000001_<ticks>_depth.npy
  frame_000002_<ticks>_1280x960.rgba
  frame_000002_<ticks>_meta.json
  frame_000002_<ticks>_depth.npy
```

The metadata should contain pose and dimensions. If the format is different, update or extend `src/io_utils.py`.

### XR Integration Steps

1. Put the new capture in a stable folder.
2. Update `XR_Pipeline/configs/pipeline.yaml`:
   - `session_id`
   - `raw_data_root`
   - `depth_source`
   - `stereo_eye`
   - `camera`
   - `object_vocabulary`
   - `detection_groups`
   - `domain_config`
3. If the task domain is new, copy `domain_industrial_example.yaml` and edit object classes, roles, operations, subtasks, and subgoals.
4. Run `01_build_frame_manifest.py`.
5. Run `02_validate_manifest.py`.
6. Run `03_visualize_rgb_depth_pose.py` and inspect the images before trusting detections.
7. Tune prompts and thresholds using `sweep_grounding_dino.py` if detections are weak.
8. Run the rest of the pipeline.
9. Inspect debug boxes, track summaries, operation reviews, and the final assembly review.

### XR Integration Checklist

Before a new XR run is considered reliable, verify:

- Frame count is expected.
- RGB frames are not mirrored, cropped incorrectly, or upside down.
- Depth values are in meters or correctly scaled.
- 3D positions are plausible.
- Object vocabulary maps raw detector labels to stable canonical names.
- Each object has the correct role: `hand`, `workpiece`, `tool`, `fixture`, or another domain role.
- Tracks persist across frames instead of flickering.
- Operation events make sense when compared to the video.
- The assembly review does not overclaim weak evidence.

## IndustReal_Pipeline

### Purpose

`IndustReal_Pipeline` demonstrates procedure understanding on a larger industrial assembly dataset. It uses dataset labels as an oracle, combines them with CAD/domain rules, and builds graph and reasoning artifacts.

The main completed run is:

```text
raw_cad_dataset__all_test_clips
```

### Main Outputs

| Output | Path |
| --- | --- |
| Dataset reports | `IndustReal_Pipeline/results/raw_cad_dataset_reports/raw_cad_dataset__all_test_clips` |
| Neo4j CSVs | `IndustReal_Pipeline/results/neo4j/raw_cad_dataset__all_test_clips` |
| Reasoning outputs | `IndustReal_Pipeline/results/reasoning_layers/raw_cad_dataset__all_test_clips` |
| Procedural reasoning graph | `IndustReal_Pipeline/results/procedural_reasoning_graph/raw_cad_dataset__all_test_clips` |

Current full-run summary:

| Metric | Value |
| --- | --- |
| Clips | 19 |
| Frames | 65,838 |
| Clip/mode jobs | 38 |
| Failed jobs | 0 |

Overall mode comparison:

| Mode | Mean step recall | Mean step precision | Mean error-window recall |
| --- | --- | --- | --- |
| `od_only` | 0.794 | 0.373 | 0.000 |
| `od_plus_psr_error_hints` | 0.981 | 0.437 | 0.579 |

## IndustReal Config Files

| Config file | What it controls | Why it is important | Easy sentence |
| --- | --- | --- | --- |
| `IndustReal_Pipeline/configs/raw_cad_dataset.json` | Full dataset batch: archive locations, run modes, detector/oracle settings, reasoner settings, evaluation settings, CAD components, prompts, and state bit indices. | Main config for reproducing the full IndustReal result. | "This tells the batch runner what dataset to process and how each CAD part maps to labels/states." |
| `IndustReal_Pipeline/configs/raw_cad_pilot.json` | Smaller pilot setup with slice rules, storage limits, selected clips, and the same CAD/detector concepts. | Useful for testing changes before the full dataset run. | "This is the small test version of the IndustReal pipeline." |
| `IndustReal_Pipeline/configs/procedure_info.json` | Maps procedure action IDs to descriptions, install/remove flags, and state indices. | It tells the evaluator/reasoner what each dataset action means. | "This translates numeric procedure labels into named steps." |
| `IndustReal_Pipeline/configs/assembly_phase_rules.json` | Maps components and event types to assembly phases; defines the final CAD state. | Used to group steps into readable phases such as chassis assembly or wheel assembly. | "This turns individual steps into assembly phases." |
| `IndustReal_Pipeline/config/domain_config.yaml` | Domain ontology: component types, install targets, parent components, required tools, required conditions, and safety requirements. | Layer 3 depends on this to infer meaningful constraints. | "This says what each part is and what must be true to install it." |
| `IndustReal_Pipeline/config/thesis_rules.yaml` | Predicate vocabulary, rule types, thresholds, validation thresholds, and Layer 3 rules. | This is the rulebook that turns predicates into constraints. | "This is the symbolic reasoning rule file." |
| `IndustReal_Pipeline/config/reasoning_adapter.yaml` | Default run ID, graph CSV input directory, reasoning output directory, and required CSV filenames. | Lets the adapter know where to read graph CSVs and where to write reasoning files. | "This connects the graph export to the reasoning pipeline." |

### Important IndustReal Config Concepts

In `raw_cad_dataset.json`:

| Field | Meaning |
| --- | --- |
| `paths` | Defines where data, reports, working files, extracted clips, and temporary caches live. |
| `archives` | Defines required source archives, CAD geometry archives, and optional download URLs. |
| `batch` | Defines run name, scope, modes, resume behavior, and whether missing archives may be downloaded. |
| `detector` | Defines the oracle or detector backend and smoothing thresholds. |
| `reasoner` | Defines how CAD state timelines become procedure steps. |
| `cad.components` | Lists the CAD parts, display names, state bit indices, detector prompts, aliases, and asset families. |
| `cad.context_components` | Lists non-CAD context objects such as hands. |

In `domain_config.yaml`:

| Field | Meaning |
| --- | --- |
| `type_hierarchy` | Defines classes such as `Component`, `Fastener`, `ChassisPin`, and `WheelAssembly`. |
| `type_defaults` | Adds default requirements for classes, such as screws requiring a screwdriver. |
| `condition_vocabulary` | Defines conditions such as `installed`, `aligned`, `secured`, and `removed`. |
| `components` | Maps component IDs to names, generic types, parent components, and installation targets. |

In `thesis_rules.yaml`:

| Section | Meaning |
| --- | --- |
| `adapter.predicates` | Which predicates should be extracted from graph CSVs and domain config. |
| `predicate_vocabulary` | Human-readable definitions of predicates and inferred constraints. |
| `defaults` | Default confidence threshold and aggregation method for rules. |
| `validation` | Thresholds used by Layer 4 validation. |
| `rule_types` | Explanations of rule categories. |
| `rules` | Actual Layer 3 symbolic inference rules. |

## IndustReal Scripts

Run IndustReal scripts from inside `IndustReal_Pipeline`:

```bash
cd IndustReal_Pipeline
python scripts/<script_name>.py
```

| Script | What it does | Why it is important | Easy sentence |
| --- | --- | --- | --- |
| `01_run_demo.py` | Runs a small demonstration pipeline. | Useful as a quick smoke test. | "This is the fastest basic demo." |
| `02_prepare_raw_pilot.py` | Prepares a smaller pilot dataset slice. | Lets developers test without processing the full dataset. | "This creates a small test dataset." |
| `03_build_raw_manifest.py` | Builds a raw frame manifest for a clip. | Gives later stages a frame-by-frame index. | "This lists the frames in a raw clip." |
| `04_validate_raw_manifest.py` | Validates the raw manifest. | Catches missing or inconsistent clip files. | "This checks whether a raw clip is usable." |
| `05_visualize_raw_recording.py` | Generates debug visualizations for raw recordings. | Helps humans inspect labels, frames, and depth. | "This makes visual checks for raw data." |
| `06_build_cad_catalog.py` | Builds CAD part/state catalogs. | Gives the pipeline a component and state vocabulary. | "This defines the parts and CAD states." |
| `07_run_detector.py` | Produces detection/evidence records. | In oracle mode, this converts labels into detector-like evidence. | "This creates object evidence for each frame." |
| `08_reason_states.py` | Converts evidence into CAD state timelines and procedure steps. | This is where label evidence becomes assembly progress. | "This reasons about which assembly state the clip is in." |
| `09_export_psr_egg.py` | Exports procedure/state reasoning outputs to graph-like files. | Supports the earlier pilot graph path. | "This exports pilot results as graph data." |
| `10_evaluate_raw_cad.py` | Evaluates raw/CAD predictions against ground truth. | Reports precision, recall, delay, and related metrics. | "This scores the pilot predictions." |
| `11_run_oracle_dataset_batch.py` | Runs the full dataset batch over configured archives and modes. | This is the main full IndustReal processing script. | "This processes all configured clips." |
| `12_export_neo4j_csv.py` | Exports full IndustReal results to Neo4j-ready CSVs. | Required for graph import and reasoning adapter input. | "This creates graph database import files." |
| `13_import_neo4j.py` | Imports the IndustReal graph CSVs into Neo4j. | Publishes the graph for Cypher queries. | "This loads the IndustReal graph into Neo4j." |
| `14_build_layer3_reasoning_adapter.py` | Converts graph CSVs into `step_records.jsonl` and `predicates.jsonl`. | Bridges graph data into the rule engine. | "This turns graph rows into reasoning records." |
| `15_run_layer3_inference.py` | Applies rules to predicates and writes `inferred_constraints.csv`. | Creates preconditions, expected effects, tool requirements, safety requirements, and incompatibilities. | "This infers what should be true." |
| `16_run_layer4_validation.py` | Validates inferred constraints and writes validation records/traces. | Decides whether each step is accepted, uncertain, or rejected. | "This checks whether the evidence supports the rules." |
| `17_build_procedural_reasoning_graph.py` | Builds the final procedural reasoning graph. | Makes steps, predicates, constraints, rules, entities, and evidence inspectable as a graph. | "This builds the explainable reasoning graph." |
| `18_import_procedural_reasoning_graph_neo4j.py` | Imports the procedural reasoning graph into Neo4j. | Publishes Layer 3/4 reasoning for graph exploration. | "This loads the reasoning graph into Neo4j." |
| `19_build_graph_data_js.py` | Builds static graph data for an optional viewer/demo. | Useful only if that viewer is being used. | "This creates static data for visualization." |
| `20_evaluate_pipeline_artifact_correctness.py` | Evaluation 1: checks that key artifacts exist and connect correctly. | Confirms the artifact chain is structurally valid. | "This checks that the pipeline produced the expected files." |
| `21_evaluate_constraint_inference_coverage.py` | Evaluation 2: checks Layer 3 constraint coverage. | Measures how well predicates are transformed into constraints. | "This checks whether rules are firing." |
| `22_evaluate_validation_behavior.py` | Evaluation 3: checks Layer 4 validation and effect lifecycle behavior. | Ensures accepted/rejected/effect behavior is consistent. | "This checks validation behavior." |
| `23_evaluate_graph_traceability.py` | Evaluation 4: checks graph traceability. | Ensures final graph nodes/edges expose reasoning evidence. | "This checks whether explanations are visible in the graph." |

## IndustReal Source Modules

| Module | What it contains | Why it is important | Easy sentence |
| --- | --- | --- | --- |
| `src/raw_cad_config.py` | Config loading, path resolution, runtime environment setup. | Every batch script depends on consistent paths. | "This tells IndustReal scripts where everything is." |
| `src/dataset_batch.py` | Full dataset orchestration. | This is the engine behind `11_run_oracle_dataset_batch.py`. | "This runs the full batch." |
| `src/raw_loader.py` | Raw archive/clip loading and label reading. | Converts source data into usable clip records. | "This reads the dataset." |
| `src/raw_manifest.py` | Raw frame manifest construction. | Standardizes frames and timestamps. | "This lists each frame in a clip." |
| `src/raw_viz.py` | Raw recording visualization helpers. | Helps debug clips visually. | "This makes raw-data debug images." |
| `src/cad_catalog.py` | CAD part and state catalog building. | Defines component and state vocabulary. | "This builds the part/state dictionary." |
| `src/cad_reasoner.py` | CAD state sequence and procedure-step reasoning. | Converts evidence into assembly progress. | "This turns labels into step predictions." |
| `src/detector_rgb.py` | Oracle and detector evidence generation. | Produces object-like evidence from labels or detector backends. | "This creates per-frame object evidence." |
| `src/egg_builder.py` | IndustReal assembly graph construction. | Builds event/component/goal/phase graph structures. | "This turns predicted steps into a graph." |
| `src/eval_raw_cad.py` | Raw/CAD evaluation metrics. | Computes precision, recall, delay, and related metrics. | "This scores predictions." |
| `src/neo4j_export.py` | Neo4j CSV export for IndustReal graph. | Provides graph files for import and reasoning adapter input. | "This writes graph CSVs." |
| `src/neo4j_import.py` | Neo4j import support for IndustReal graph. | Loads graph CSVs into Neo4j. | "This imports graph CSVs." |
| `src/layer3_reasoning_adapter.py` | Converts graph CSVs into steps and predicates. | This is the input bridge for symbolic rules. | "This prepares rule-engine input." |
| `src/layer3_inference.py` | Layer 3 rule application. | Infers constraints from predicates. | "This applies symbolic rules." |
| `src/layer4_validation.py` | Layer 4 validation and effect lifecycle tracking. | Determines whether constraints are supported, missing, or invalidated. | "This validates each step." |
| `src/procedural_reasoning_graph.py` | Procedural graph construction from reasoning outputs. | Makes reasoning inspectable and traceable. | "This builds the final reasoning graph." |
| `src/procedural_neo4j_import.py` | Neo4j import for procedural reasoning graph. | Publishes the reasoning graph. | "This loads the reasoning graph." |
| `src/assembly_hierarchy.py` | Assembly/component hierarchy helpers. | Supports parent/child or assembly-level relationships. | "This understands part hierarchy." |
| `src/data_loader.py` | General data loading utilities. | Supports reusable loading patterns. | "This contains shared loading helpers." |
| `src/hl2_pose.py` | HoloLens/pose helper utilities. | Supports pose handling for related data formats. | "This handles pose data for supported formats." |
| `src/pilot_assets.py` | Pilot asset preparation helpers. | Supports smaller pilot experiments. | "This prepares pilot data assets." |
| `src/psr.py` | Procedure step recognition helpers. | Supports PSR labels, windows, and diagnostics. | "This works with procedure-step labels." |
| `src/track2d.py` | Lightweight 2D tracking helpers. | Useful for image-space evidence smoothing/tracking. | "This tracks objects in 2D." |

## Running The Main IndustReal Flow

Full dataset batch:

```bash
cd IndustReal_Pipeline
python scripts/11_run_oracle_dataset_batch.py --config configs/raw_cad_dataset.json
```

Export graph CSVs:

```bash
python scripts/12_export_neo4j_csv.py --run-id raw_cad_dataset__all_test_clips
```

Build reasoning inputs:

```bash
python scripts/14_build_layer3_reasoning_adapter.py
```

Run Layer 3:

```bash
python scripts/15_run_layer3_inference.py \
  --step-records results/reasoning_layers/raw_cad_dataset__all_test_clips/step_records.jsonl \
  --predicates results/reasoning_layers/raw_cad_dataset__all_test_clips/predicates.jsonl \
  --output results/reasoning_layers/raw_cad_dataset__all_test_clips/inferred_constraints.csv
```

Run Layer 4:

```bash
python scripts/16_run_layer4_validation.py \
  --step-records results/reasoning_layers/raw_cad_dataset__all_test_clips/step_records.jsonl \
  --predicates results/reasoning_layers/raw_cad_dataset__all_test_clips/predicates.jsonl \
  --constraints results/reasoning_layers/raw_cad_dataset__all_test_clips/inferred_constraints.csv \
  --rule-coverage results/reasoning_layers/raw_cad_dataset__all_test_clips/rule_coverage_diagnostics.csv \
  --output results/reasoning_layers/raw_cad_dataset__all_test_clips/validation_records.jsonl
```

Build procedural graph:

```bash
python scripts/17_build_procedural_reasoning_graph.py \
  --validations results/reasoning_layers/raw_cad_dataset__all_test_clips/validation_records.jsonl \
  --step-records results/reasoning_layers/raw_cad_dataset__all_test_clips/step_records.jsonl \
  --predicates results/reasoning_layers/raw_cad_dataset__all_test_clips/predicates.jsonl \
  --constraints results/reasoning_layers/raw_cad_dataset__all_test_clips/inferred_constraints.csv \
  --output-dir results/procedural_reasoning_graph/raw_cad_dataset__all_test_clips
```

## Integrating A New IndustReal-Style Dataset

A new dataset can be integrated if it provides enough information to create frame-level evidence, component state timelines, and procedure-step ground truth or labels.

### Required Dataset Contents

At minimum, a new dataset should provide:

| Requirement | Why it is needed |
| --- | --- |
| Clip/video or frame sequence | The pipeline needs frame order and duration. |
| Frame timestamps or frame rate | Procedure windows and delays need time. |
| Component vocabulary | The system must know which parts exist. |
| Component labels or detector evidence | The oracle/detector stage needs per-frame part evidence. |
| CAD state labels or assembly state representation | The CAD reasoner needs state progress over time. |
| Procedure step labels | Evaluation needs ground truth steps. |
| Action descriptions | Numeric labels must map to readable actions. |
| Install/remove/error flags | The reasoner must know the meaning of state transitions. |
| Phase or goal definition | Graphs need goal and phase context. |

### IndustReal Integration Steps

1. Create or adapt a dataset config based on `configs/raw_cad_dataset.json`.
2. Add source archives or local clip paths under `archives.source_archives`.
3. Define CAD components in `cad.components`:
   - `key`
   - `display_name`
   - `state_bit_index`
   - `detector_group`
   - `prompts`
   - `aliases`
   - `asset_family`
4. Update `procedure_info.json` for the new action IDs and step meanings.
5. Update `assembly_phase_rules.json` for new phases, final state, components, and correction events.
6. Update `config/domain_config.yaml` for the new component ontology, install targets, required tools, required conditions, and safety rules.
7. Update `config/thesis_rules.yaml` if the new dataset introduces new action types, predicates, or constraint logic.
8. Run a small pilot first.
9. Run the full batch only after pilot artifacts and metrics look reasonable.
10. Rebuild Neo4j CSVs, reasoning outputs, validations, and the procedural graph.

### When A Loader Change Is Needed

You may need to change `src/raw_loader.py` or related modules if the new dataset:

- Uses a different archive layout.
- Uses different label filenames.
- Stores labels in a different schema.
- Does not have the same OD/PSR/CAD label style.
- Stores timestamps differently.
- Uses videos instead of extracted frames.

Use `src/raw_manifest.py` as the standard output target: once the loader can produce a reliable manifest and label records, the rest of the pipeline is easier to reuse.

## How To Verify A New Dataset Integration

For XR:

- Check manifest row count.
- Inspect RGB/depth/pose visualizations.
- Inspect detection debug boxes.
- Inspect track summary for stable object identities.
- Compare operation reviews to the actual video.
- Read the final assembly review and look for overclaiming.

For IndustReal:

- Check `summary.csv` for failures and unexpected zero counts.
- Check `mode_comparison.csv` for recall/precision changes.
- Inspect `nodes_events.csv` to confirm procedure steps look correct.
- Inspect `step_records.jsonl` and `predicates.jsonl` to confirm the reasoning adapter is reading the graph correctly.
- Inspect `inferred_constraints.csv` to confirm rules are firing.
- Inspect `step_validations.csv` to confirm validation status makes sense.
- Inspect the procedural graph to confirm traceability from step to predicate to constraint to rule.

## Common Extension Points

| Goal | Files to modify first |
| --- | --- |
| Add new XR object classes | `XR_Pipeline/configs/pipeline.yaml`, `XR_Pipeline/configs/domain_*.yaml` |
| Tune XR detector | `pipeline.yaml`, `thresholds.yaml`, `sweep_grounding_dino.py` |
| Add XR operation type | `domain_*.yaml`, `thresholds.yaml`, `src/operation_events.py`, tests |
| Add XR subtask | `domain_*.yaml`, `src/subtask_events.py` if current templates are not enough |
| Add IndustReal component | `raw_cad_dataset.json`, `domain_config.yaml`, `assembly_phase_rules.json` |
| Add IndustReal action type | `procedure_info.json`, `thesis_rules.yaml`, Layer 3/4 tests |
| Add new reasoning predicate | `thesis_rules.yaml`, `layer3_reasoning_adapter.py`, tests |
| Add new validation behavior | `layer4_validation.py`, `thesis_rules.yaml`, tests |
| Support a new dataset layout | `raw_loader.py`, `raw_manifest.py`, dataset tests |

## Testing

Run XR tests:

```bash
cd XR_Pipeline
pytest
```

Run IndustReal tests:

```bash
cd IndustReal_Pipeline
pytest
```

Detector-heavy tests may require model dependencies. Neo4j import scripts require credentials and connectivity.

## What To Read First

For XR:

1. `XR_Pipeline/configs/pipeline.yaml`
2. `XR_Pipeline/configs/domain_lego.yaml`
3. `XR_Pipeline/scripts/01_build_frame_manifest.py`
4. `XR_Pipeline/scripts/05_build_object_observations.py`
5. `XR_Pipeline/scripts/10b_build_operation_events.py`
6. `XR_Pipeline/data/processed/session_003/reviews/assembly/assembly_review.md`

For IndustReal:

1. `IndustReal_Pipeline/configs/raw_cad_dataset.json`
2. `IndustReal_Pipeline/config/domain_config.yaml`
3. `IndustReal_Pipeline/config/thesis_rules.yaml`
4. `IndustReal_Pipeline/scripts/11_run_oracle_dataset_batch.py`
5. `IndustReal_Pipeline/scripts/14_build_layer3_reasoning_adapter.py`
6. `IndustReal_Pipeline/scripts/16_run_layer4_validation.py`
7. `IndustReal_Pipeline/results/raw_cad_dataset_reports/raw_cad_dataset__all_test_clips/mode_comparison.csv`
