#!/usr/bin/env python3
"""Build the procedural_reasoning_graph from Layer 4 validation records."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.procedural_reasoning_graph import (  # noqa: E402
    ProceduralReasoningGraphInputs,
    build_procedural_reasoning_graph,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--step-records", type=Path, default=None)
    parser.add_argument("--predicates", type=Path, default=None)
    parser.add_argument("--constraints", type=Path, default=None)
    parser.add_argument("--graph-name", type=str, default="procedural_reasoning_graph")
    parser.add_argument("--exclude-rejected", action="store_true")
    parser.add_argument("--shortLabels", action="store_true", help="Use compact Step display labels such as S0 [A], S1 [U], S2 [R].")
    args = parser.parse_args()

    result = build_procedural_reasoning_graph(
        ProceduralReasoningGraphInputs(
            validations_path=args.validations,
            output_dir=args.output_dir,
            step_records_path=args.step_records,
            predicates_path=args.predicates,
            constraints_path=args.constraints,
            exclude_rejected=args.exclude_rejected,
            graph_name=args.graph_name,
            short_labels=args.shortLabels,
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
