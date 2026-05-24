# Evaluation 4 Neo4j Screenshot Views

## A. Step Status And Temporal Order

Purpose: Shows validated Step nodes in sequence with NEXT edges only.

```cypher
MATCH path = (s1:Step)-[:NEXT]->(s2:Step)
WHERE s1.graph_name = "procedural_reasoning_graph::raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1" AND s2.graph_name = s1.graph_name
RETURN path;
```

Thesis claim supported: Supports order preservation and Step status visibility.

Suggested screenshot filename: `eval04_A_step_status_temporal_order.png`

Suggested caption: Procedural Step nodes ordered by NEXT edges, with validation status shown on each Step.

## B. Dependency View

Purpose: Shows temporal order and inferred procedural dependency support between steps.

```cypher
MATCH path = (s1:Step)-[:NEXT|DEPENDS_ON]->(s2:Step)
WHERE s1.graph_name = "procedural_reasoning_graph::raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1" AND s2.graph_name = s1.graph_name
RETURN path;
```

Thesis claim supported: Supports dependency grounding and rejected-step isolation.

Suggested screenshot filename: `eval04_B_step_dependencies.png`

Suggested caption: Step sequence with DEPENDS_ON links exposing dependency support between validated steps.

## C. Constraint Evidence View

Purpose: Shows Step nodes connected to requirement, produced-effect, and incompatibility constraints.

```cypher
MATCH path = (s:Step)-[:REQUIRES|PRODUCES|HAS_CONSTRAINT]->(c:Constraint)
WHERE s.graph_name = "procedural_reasoning_graph::raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1"
  AND c.graph_name = s.graph_name
  AND (c.name IN ["requires", "requiresTool", "requiresSafety", "produces", "incompatibleAction"])
RETURN path;
```

Thesis claim supported: Supports requirement visibility, produced-effect visibility, and incompatibility traceability.

Suggested screenshot filename: `eval04_C_constraint_evidence.png`

Suggested caption: Graph view of Step-to-Constraint evidence, including requirements, produced effects, and incompatibilities.

## D. Effect Lifecycle And Invalidation

Purpose: Shows invalidated produced effects and the later removal effects that invalidated them.

```cypher
MATCH path =
  (s1:Step)-[:PRODUCES]->(c1:Constraint)
  -[:INVALIDATED_BY]->(c2:Constraint)<-[:PRODUCES]-(s2:Step)
WHERE s1.graph_name = "procedural_reasoning_graph::raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1" AND s2.graph_name = s1.graph_name
RETURN path;
```

Thesis claim supported: Supports effect invalidation visibility and produced-effect lifecycle traceability.

Suggested screenshot filename: `eval04_D_effect_lifecycle_invalidation.png`

Suggested caption: Produced-effect constraints linked by INVALIDATED_BY relations to later removal effects.

## E. Full Representative Trace

Purpose: Shows one compact local explanation neighborhood around a selected Step.

```cypher
MATCH (s:Step)
WHERE s.graph_name = "procedural_reasoning_graph::raw_cad_dataset__all_test_clips__od_plus_psr_error_hints__test_p1__08_assy_0_1" AND s.index = 17
OPTIONAL MATCH p1 = (s)-[:HAS_PREDICATE|HAS_CONSTRAINT|REQUIRES|PRODUCES]->()
OPTIONAL MATCH p2 = (s)-[:DEPENDS_ON]->(:Step)
OPTIONAL MATCH p3 = (s)-[:HAS_CONSTRAINT]->(:Constraint)-[:DERIVED_FROM|SUPPORTED_BY|HAS_ENTITY]->()
RETURN p1, p2, p3;
```

Thesis claim supported: Supports the claim that a reader can start from a Step and follow predicates, constraints, rules, dependencies, and entities.

Suggested screenshot filename: `eval04_E_full_representative_trace_step17.png`

Suggested caption: Compact explanation neighborhood for a representative Step, showing evidence and provenance links.
