#!/usr/bin/env python3
"""Import procedural_reasoning_graph outputs into Neo4j Aura or a local Neo4j DB."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from src.procedural_neo4j_import import (  # noqa: E402
    clear_graph_cypher,
    constraint_cyphers,
    edge_import_cypher,
    grouped_by_type,
    legacy_constraint_drop_cyphers,
    load_procedural_graph,
    node_import_cypher,
    normalize_graph,
)


def _resolve_env_file(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    for candidate in (Path.cwd() / path, ROOT / path, REPO_ROOT / path):
        if candidate.exists():
            return candidate
    return Path.cwd() / path


def _load_env(path: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise SystemExit("python-dotenv is required. Install IndustReal_Pipeline/requirements.txt.") from exc
    load_dotenv(path)


def _batches(rows: list[dict], size: int) -> list[list[dict]]:
    return [rows[idx : idx + size] for idx in range(0, len(rows), size)]


def _write_batches(session, cypher: str, rows: list[dict], batch_size: int) -> None:
    for batch in _batches(rows, batch_size):
        session.execute_write(lambda tx, batch_rows: tx.run(cypher, rows=batch_rows), batch)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--graph",
        type=Path,
        required=True,
        help="Graph JSON path or output directory containing procedural_reasoning_graph.json.",
    )
    parser.add_argument("--env-file", type=str, default=".env")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--no-replace-graph", action="store_true")
    parser.add_argument(
        "--drop-legacy-prg-id-constraints",
        action="store_true",
        help="Drop older prg_id-only uniqueness constraints before creating graph_name+prg_id constraints.",
    )
    args = parser.parse_args()

    graph = load_procedural_graph(args.graph)
    graph_name = str(graph.get("graph_name") or "procedural_reasoning_graph")
    normalized = normalize_graph(graph, graph_name=graph_name)
    node_groups = grouped_by_type(normalized["nodes"])
    edge_groups = grouped_by_type(normalized["edges"])

    env_path = _resolve_env_file(args.env_file)
    _load_env(env_path)

    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    if not uri or not password:
        raise SystemExit(f"NEO4J_URI and NEO4J_PASSWORD must be set in {env_path}")

    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise SystemExit("neo4j is required. Install IndustReal_Pipeline/requirements.txt.") from exc

    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    with driver.session() as session:
        if args.drop_legacy_prg_id_constraints:
            for cypher in legacy_constraint_drop_cyphers(list(node_groups)):
                session.execute_write(lambda tx, query: tx.run(query), cypher)
        for cypher in constraint_cyphers(list(node_groups)):
            session.execute_write(lambda tx, query: tx.run(query), cypher)
        if not args.no_replace_graph:
            session.execute_write(lambda tx: tx.run(clear_graph_cypher(), graph_name=graph_name))
        for node_type, rows in node_groups.items():
            _write_batches(session, node_import_cypher(node_type), rows, args.batch_size)
        for edge_type, rows in edge_groups.items():
            _write_batches(session, edge_import_cypher(edge_type), rows, args.batch_size)
    driver.close()

    print(f"Imported {graph_name} from {args.graph}")
    print(
        "Rows: "
        f"{len(normalized['nodes'])} nodes across {len(node_groups)} labels, "
        f"{len(normalized['edges'])} edges across {len(edge_groups)} relationship types"
    )


if __name__ == "__main__":
    main()
