#!/usr/bin/env python3
"""11_export_neo4j_csv.py — Export graph layers as Neo4j-ready CSV files."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table

from src.config import PipelinePaths, load_pipeline_config
from src.egg import load_egg
from src.neo4j_export import export_assembly_neo4j_csvs, export_neo4j_csvs

app = typer.Typer()
console = Console()

NEO4J_CYPHER = """\
// XR_Pipeline — Neo4j import commands
// Run these in order against your Neo4j Aura instance

CREATE CONSTRAINT room_id IF NOT EXISTS
FOR (r:Room) REQUIRE r.room_id IS UNIQUE;

CREATE CONSTRAINT object_id IF NOT EXISTS
FOR (o:Object) REQUIRE o.track_id IS UNIQUE;

CREATE CONSTRAINT event_id IF NOT EXISTS
FOR (e:Event) REQUIRE e.event_id IS UNIQUE;

// Import nodes (replace $url with actual hosted CSV URLs or use local path)
LOAD CSV WITH HEADERS FROM $rooms_url AS row
MERGE (r:Room {room_id: row.`room_id:ID(Room)`})
SET r.name = row.name,
    r.x = toFloat(row.`x:float`),
    r.y = toFloat(row.`y:float`),
    r.z = toFloat(row.`z:float`);

LOAD CSV WITH HEADERS FROM $objects_url AS row
MERGE (o:Object {track_id: row.`track_id:ID(Object)`})
SET o.semantic_class = row.semantic_class,
    o.label = row.label,
    o.caption = row.caption;

LOAD CSV WITH HEADERS FROM $events_url AS row
MERGE (e:Event {event_id: row.`event_id:ID(Event)`})
SET e.event_type = row.event_type,
    e.summary = row.summary,
    e.start_ts_ns = toInteger(row.`start_ts_ns:long`),
    e.end_ts_ns = toInteger(row.`end_ts_ns:long`),
    e.pos_x = toFloat(row.`pos_x:float`),
    e.pos_y = toFloat(row.`pos_y:float`),
    e.pos_z = toFloat(row.`pos_z:float`);

// Import edges
LOAD CSV WITH HEADERS FROM $room_object_url AS row
MATCH (r:Room {room_id: row.`:START_ID(Room)`})
MATCH (o:Object {track_id: row.`:END_ID(Object)`})
MERGE (r)-[:CONTAINS]->(o);

LOAD CSV WITH HEADERS FROM $event_object_url AS row
MATCH (e:Event {event_id: row.`:START_ID(Event)`})
MATCH (o:Object {track_id: row.`:END_ID(Object)`})
MERGE (e)-[rel:INVOLVES]->(o)
SET rel.role = row.role,
    rel.role_description = row.role_description;

LOAD CSV WITH HEADERS FROM $before_url AS row
MATCH (e1:Event {event_id: row.`:START_ID(Event)`})
MATCH (e2:Event {event_id: row.`:END_ID(Event)`})
MERGE (e1)-[:BEFORE]->(e2);

// Optional assembly graph layer. The direct Python importer expands
// properties_json onto nodes/relationships; this Cypher keeps it available.
CREATE CONSTRAINT assembly_node_id IF NOT EXISTS
FOR (n:AssemblyNode) REQUIRE n.assembly_id IS UNIQUE;

LOAD CSV WITH HEADERS FROM $assembly_nodes_url AS row
MERGE (n:AssemblyNode {assembly_id: row.`assembly_id:ID(AssemblyNode)`})
SET n.node_id = row.node_id,
    n.session_id = row.session_id,
    n.node_type = row.node_type,
    n.properties_json = row.properties_json;

LOAD CSV WITH HEADERS FROM $assembly_edges_url AS row
MATCH (a:AssemblyNode {assembly_id: row.`:START_ID(AssemblyNode)`})
MATCH (b:AssemblyNode {assembly_id: row.`:END_ID(AssemblyNode)`})
MERGE (a)-[rel:ASSEMBLY_EDGE {edge_id: row.`edge_id:ID(AssemblyEdge)`}]->(b)
SET rel.session_id = row.session_id,
    rel.edge_type = row.edge_type,
    rel.properties_json = row.properties_json;
"""


@app.command()
def main(
    session: str = typer.Option("session_001"),
    config: str = typer.Option(None),
):
    """Export Neo4j-ready CSV files from EGG and assembly graph JSON."""
    cfg = load_pipeline_config(Path(config) if config else None)
    paths = PipelinePaths(session, cfg)
    paths.ensure_dirs()

    if not paths.egg_graph.exists():
        console.print("[red]egg_graph.json not found. Run 09 first.[/red]")
        raise typer.Exit(1)

    graph = load_egg(paths.egg_graph)
    counts = export_neo4j_csvs(graph, paths.neo4j_dir)

    if paths.assembly_graph.exists():
        with open(paths.assembly_graph, encoding="utf-8") as f:
            assembly_graph = json.load(f)
        counts.update(export_assembly_neo4j_csvs(assembly_graph, paths.neo4j_dir))
    else:
        console.print(
            "[yellow]assembly_graph.json not found — run 10e_build_assembly_graph.py "
            "before 11 to export the assembly layer.[/yellow]"
        )

    table = Table(title="Neo4j CSV Export")
    table.add_column("File"); table.add_column("Rows", justify="right")
    for name, cnt in counts.items():
        table.add_row(name.replace("_", " ") + ".csv", str(cnt))
    console.print(table)

    # Write Cypher import file
    cypher_out = Path(__file__).resolve().parent.parent / "neo4j" / "import_egg.cypher"
    cypher_out.parent.mkdir(exist_ok=True)
    cypher_out.write_text(NEO4J_CYPHER)
    console.print(f"[green]✓ Cypher import file → {cypher_out}[/green]")
    console.print(f"[green]✓ CSV files → {paths.neo4j_dir}[/green]")


if __name__ == "__main__":
    app()
