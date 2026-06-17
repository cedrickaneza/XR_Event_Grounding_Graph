# Dataset Provenance

This document records the provenance and licensing of the data, captures, and
generated artifacts included in this repository. It does **not** grant any
rights beyond those held by the upstream sources.

## Summary

| Material | Examples | License / terms |
| --- | --- | --- |
| First-party XR/Quest captures | `Quest_Capture/session_003/` | Apache-2.0 (this repo) |
| First-party code & XR-derived outputs | `XR_Pipeline/` code and processed session outputs | Apache-2.0 (this repo) |
| IndustReal dataset material | `IndustReal_Pipeline/data/ar_labels/`, `ASD_results/`, `relevant_slice_test_p1.tar.gz`, and IndustReal RGB frames in `IndustReal_Pipeline/results/**/debug_visuals/` | Upstream IndustReal terms — **not** Apache-2.0 |
| STEMFIE part geometries | `IndustReal_Pipeline/data/part_geometries.zip` | STEMFIE project terms — **not** Apache-2.0 |
| Third-party models / dependencies | Grounding DINO, MM-Grounding-DINO, YOLO, etc. (not vendored) | Respective upstream licenses |

## First-party material

The Meta Quest 3 RGB-D + pose captures under `Quest_Capture/` were recorded by
the project authors. These captures, the original source code in this
repository, and the outputs the pipeline generates from first-party captures are
distributed under this repository's Apache License 2.0 (see [LICENSE](LICENSE)).

## IndustReal-derived material

This repository includes material derived from the IndustReal project
(Schoonbeek et al., 2024 — <https://github.com/TimSchoonbeek/IndustReal>):

- `IndustReal_Pipeline/data/ar_labels/` and `ar_labels.zip`
- `IndustReal_Pipeline/data/ASD_results/` and `ASD_results.zip`
- `IndustReal_Pipeline/data/relevant_slice_test_p1.tar.gz`
- `IndustReal_Pipeline/data/part_geometries.zip` (CAD / part geometries)
- IndustReal RGB frames embedded in debug visuals under
  `IndustReal_Pipeline/results/**/debug_visuals/`

IndustReal **source code** is released under Apache-2.0 by its authors. The
IndustReal **dataset** (images, labels, geometries, results) is published via
4TU.ResearchData under its own terms and is **not** relicensed here. The
CAD/part geometries originate from the **STEMFIE** project
(<https://stemfie.org>) and remain subject to STEMFIE's terms.

For authoritative dataset terms, consult the upstream IndustReal repository and
its 4TU.ResearchData entry, and the STEMFIE project for the geometries. To obtain
a clean copy of the IndustReal dataset, download it directly from those sources.

If you use IndustReal-derived material, please cite:

> Schoonbeek, T. J., Houben, T., Onvlee, H., van der Sommen, F., et al.
> "IndustReal: A Dataset for Procedure Step Recognition Handling Execution
> Errors in Egocentric Videos in an Industrial-Like Setting."
> IEEE/CVF Winter Conference on Applications of Computer Vision (WACV), 2024.

## Third-party models and dependencies

Detector backends (e.g. Grounding DINO, MM-Grounding-DINO, YOLO) and other
Python/Node dependencies are not vendored in this repository and retain their
original upstream licenses.
