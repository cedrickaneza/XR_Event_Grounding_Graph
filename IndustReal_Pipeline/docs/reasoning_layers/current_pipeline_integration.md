# Current Reasoning Layer Integration Notes

This document describes how the current reasoning-layer implementation connects to the existing IndustReal pipeline. It is meant as a practical implementation note that can later be folded into a full pipeline README.

## Current Data Flow

The existing pipeline first builds an assembly graph and exports Neo4j-style CSV files under:

```text
results/neo4j/<run_id>/
```

The current reasoning-layer bridge starts from those exported CSV files. It does not replace the existing graph generation, Neo4j export, or Neo4j import path.

The flow is:

```text
existing graph CSVs
  -> Layer 3 reasoning adapter
  -> step_records.jsonl + predicates.jsonl
  -> Layer 3 rule inference
  -> inferred_constraints.csv
  -> Layer 4 validation
  -> validation_records.jsonl + step_validations.csv + explanation_traces.json + effect_history_diagnostics.csv
  -> procedural_reasoning_graph
  -> procedural_reasoning_graph.json + procedural_reasoning_graph_nodes.csv + procedural_reasoning_graph_edges.csv
  -> Neo4j procedural graph import
```

Current scripts:

```text
scripts/14_build_layer3_reasoning_adapter.py
scripts/15_run_layer3_inference.py
scripts/16_run_layer4_validation.py
scripts/17_build_procedural_reasoning_graph.py
scripts/18_import_procedural_reasoning_graph_neo4j.py
```

Current implementation modules:

```text
src/layer3_reasoning_adapter.py
src/layer3_inference.py
src/layer4_validation.py
src/procedural_reasoning_graph.py
src/procedural_neo4j_import.py
```

Adapter runtime defaults are configured in:

```text
config/reasoning_adapter.yaml
```

That file defines the default run id, input CSV directory, output root, predicate/rule config path, domain config path, and the expected Neo4j CSV filenames consumed by the adapter. `scripts/14_build_layer3_reasoning_adapter.py` accepts `--adapter-config` to load a different adapter config.

## Adapter Role

The adapter turns existing graph CSV records into the JSONL inputs used by the reasoning layers.

It reads:

```text
nodes_events.csv
edges_event_component.csv
edges_event_next.csv
nodes_components.csv
```

It writes:

```text
step_records.jsonl
predicates.jsonl
```

`step_records.jsonl` contains one normalized step record per source assembly event.

`predicates.jsonl` contains symbolic facts derived from each step, such as the step action, time window, object use, and component metadata.

The upstream graph stores event instants. The adapter fills `time_window.start_s` and `start_frame` from the event row, and currently infers `end_s` and `end_frame` from the next distinct event timestamp in the same clip when one exists. The final timestamp group remains open-ended with null end values. This is a downstream fallback until upper-layer step segmentation provides explicit step windows.

## Predicate Configuration

Predicate names are configured in:

```text
config/thesis_rules.yaml
```

under:

```text
adapter.predicates
```

The current categories are:

```text
event
object_interaction
entity_metadata
```

Each configured predicate has a stable adapter key, an output name, an argument description, and an enabled flag.

Example:

```json
"has_action": {
  "name": "hasAction",
  "description": "Associates a step with its normalized action label.",
  "args": ["step_id", "action_name"],
  "enabled": true
}
```

The stable adapter key is used by Python code to decide which extraction path to run. The configured `name` is what appears in `predicates.jsonl` and what Layer 3 rules match against.

Predicate aliases are configured under `predicate_aliases`. Layer 3 normalizes predicate names before rule matching, so equivalent names such as `stepHasAction`, `actsOn`, and `typeOf` can be mapped to canonical names such as `hasAction`, `usesObject`, and `isA`.

Disabling a current predicate is also a config change:

```json
"enabled": false
```

## Upstream Boundary

The config controls the vocabulary for predicates that the adapter already knows how to generate.

The adapter still needs Python logic for each kind of predicate because each predicate depends on specific upstream data. For example:

```text
has_action
  reads event_type/action_desc from nodes_events.csv

uses_object
  reads event-component edges from edges_event_component.csv

is_a
  reads component metadata from nodes_components.csv
```

This is expected. A config file can say what a predicate is called, but it cannot invent source evidence that does not exist upstream.

So the extension rule is:

```text
Rename, recategorize, or disable an existing predicate:
  update config/thesis_rules.yaml

Add a new predicate using data the adapter does not currently read or derive:
  add a small generator path in src/layer3_reasoning_adapter.py
  add the predicate definition to config/thesis_rules.yaml
  add or update Layer 3 rules that consume the new predicate
```

For example, a future `isAfter(step_a, step_b)` predicate could be derived from `edges_event_next.csv`, but the adapter would need explicit code that converts those event-next edges into that predicate shape.

## Layer 3 Rule Inference

Layer 3 inference reads:

```text
step_records.jsonl
predicates.jsonl
config/thesis_rules.yaml
```

It writes:

```text
inferred_constraints.csv
rule_coverage_diagnostics.csv
```

Layer 4 validation reads:

```text
step_records.jsonl
predicates.jsonl
inferred_constraints.csv
rule_coverage_diagnostics.csv
```

It writes:

```text
validation_records.jsonl
step_validations.csv
explanation_traces.json
effect_history_diagnostics.csv
```

Rules are also stored in `config/thesis_rules.yaml`, under:

```text
rules
```

Each rule matches predicate names and argument patterns. Rule outputs are defined under the `constraints` field. When the antecedents match and confidence passes the threshold, the rule emits one or more constraints.

The current rule categories follow the methodology draft:

```text
inferred_precondition
expected_effect
safety_constraint
required_tool
implicit_assembly_condition
compatibility
```

These categories use the rule evaluation structure from `Methodology_Design.tex`, Listing `lst:alg_rule_evaluation`. Non-compatibility rules are evaluated first: find bindings, collect supporting predicates, aggregate confidence, compare with the rule threshold, and instantiate configured constraints. Compatibility rules are evaluated in a separate pass and emit incompatibility constraints with provenance; these are interpreted as hard validity conditions during later validation.

Current examples use domain individual ids and generic class predicates:

```text
hasAction(step1, install) + usesObject(step1, base) + isA(base, Component)
  -> produces(step1, installed, base, workspace)

hasAction(step2, install) + usesObject(step2, rear_chassis) + isA(rear_chassis, Chassis)
  -> requires(step2, installed, base, workspace)
  -> produces(step2, installed, rear_chassis, base)

hasAction(step3, install) + usesObject(step3, front_rear_chassis_pin) + isA(front_rear_chassis_pin, ChassisPin)
  -> requires(step3, installed, rear_chassis, base)
  -> requires(step3, aligned, front_rear_chassis_pin, rear_chassis)
  -> produces(step3, installed, front_rear_chassis_pin, rear_chassis)

usesObject(step7, front_bracket_screw) + isA(front_bracket_screw, Screw)
  -> requiresTool(step, screwdriver)

hasAction(step, error) + usesObject(step, object)
  -> incompatibleAction(step, object, error)
```

Because rules match by predicate name after alias normalization, changing a predicate output name in `adapter.predicates` should either use a canonical vocabulary name or add an explicit alias to `predicate_aliases`.

## Layer 4 Validation

Layer 3 only infers requirements and expected effects. It does not decide whether a requirement is satisfied.

Layer 3 also writes `rule_coverage_diagnostics.csv`, one row per step. This diagnostic records the action name, object/tool arguments, predicate count, matched rule count, produced constraint count, and coverage booleans such as `has_expected_effect`, `has_requirement`, `has_incompatibility`, and `has_rule_coverage`. If a step has meaningful predicate evidence, such as `hasAction(...)` plus `usesObject(...)` or `usesTool(...)`, but no Layer 3 rule produces constraints, the diagnostic row uses:

```text
warning_code: no_applicable_rule
warning_message: Step has predicate evidence but no Layer 3 rule produced constraints.
```

This is intentionally diagnostic rather than a fabricated semantic rule. For example, if a future action has predicate evidence but no matching Layer 3 rule, the step is marked as unsupported by the current rule coverage instead of being assigned invented dependencies or effects.

The current rule set does define remove-action semantics. A `remove` action over a configured component can produce a precondition requiring the component to be installed and an expected `removed(component, target)` effect. Layer 4 then uses that removed effect to invalidate the matching active installed effect.

Layer 4 walks the ordered steps and maintains two views of previous `produces(...)` effects:

```text
historical produced effects  all produced effects retained for traceability
active produced effects      non-rejected effects still available to support later requirements
```

For each step, it checks requirement constraints such as `requires(...)`, `requiresSafety(...)`, and `requiresTool(...)` against:

```text
same-step predicates
active previous produced effects
```

If a requirement is supported by a previous effect, the validation record links it to the earlier producing constraint. Domain requirement predicates such as `hasRequiredCondition(...)`, `hasSafetyRequirement(...)`, and `hasRequiredTool(...)` state that a condition is required; they are not treated as evidence that the condition was satisfied.

When an accepted or uncertain step produces `removed(component, target)`, Layer 4 invalidates the matching active `installed(component, target)` effect. The installed effect remains in the historical record, but it is removed from active support so later requirements cannot use it. Rejected steps do not contribute active effects, so their produced effects are marked inactive and cannot support later requirements.

Layer 4 exposes this lifecycle explicitly through `produced_effect_lifecycle` records. Each produced effect receives:

```text
effect_lifecycle_status: active | invalidated | inactive_rejected
invalidated_by_constraint_id
```

`active` means the produced effect is still available after the complete validation pass. `invalidated` means a later produced effect removed it from the active support set. `inactive_rejected` means the producing step was rejected, so the effect is preserved historically but never entered the active support set.

If no support is found, the requirement is recorded as missing. A step is `accepted` only when no requirements are missing and its confidence meets `validation.tau_acc` from `config/thesis_rules.yaml`. A step with partial support and confidence above `validation.tau_unc` is marked `uncertain`. Compatibility constraints still act as hard violations and mark a step `rejected`.

Layer 4 propagates rule coverage warnings and effect lifecycle provenance into `validation_records.jsonl`, `step_validations.csv`, and `explanation_traces.json`. A step with meaningful evidence but no applicable Layer 3 rule is marked `uncertain` rather than silently accepted, unless a separate hard incompatibility rejects it. Validation records expose this through `warnings`, `diagnostics.rule_coverage`, `has_rule_coverage`, `matched_rule_count`, `produced_constraint_count`, `has_expected_effect`, `unsupported_action`, `unsupported_action_name`, `invalidated_effects`, and `produced_effect_lifecycle`.

## Procedural Reasoning Graph

The `procedural_reasoning_graph` is the reasoning-enriched procedural representation produced after Layer 3 inference and Layer 4 validation. It is separate from the upstream assembly/Neo4j graph: the upstream graph represents exported source events and component relations, while `procedural_reasoning_graph` represents validated steps, predicate evidence, inferred constraints, rule provenance, dependency support, missing requirements, and explanation traces.

The primary graph-builder input is:

```text
validation_records.jsonl
```

Optional inputs such as `step_records.jsonl`, `predicates.jsonl`, and `inferred_constraints.csv` are accepted by the script. The current builder uses `step_records.jsonl` for Step metadata enrichment and relies on `validation_records.jsonl` for validation status, predicate evidence, constraint evidence, produced effects, produced-effect lifecycle, dependency support, missing requirements, incompatibilities, and trace information. When `--step-records` is provided, Step nodes are enriched with source metadata from the adapter step records, including `clip_result_id`, `run_id`, `mode`, `archive_name`, and `clip`.

The graph JSON has this shape:

```json
{
  "schema_version": "1.0",
  "graph_name": "procedural_reasoning_graph",
  "nodes": [],
  "edges": []
}
```

The graph builder also writes:

```text
procedural_reasoning_graph_nodes.csv
procedural_reasoning_graph_edges.csv
```

Node types:

```text
Step        one node per validation record
Predicate   predicate evidence from evidence_predicates / trace.predicate_evidence
Constraint  inferred/validated constraints from evidence and requirement fields
Rule        rule_id provenance from constraints
Entity      object/tool/workspace/material arguments extracted from predicates and constraints
Source      predicate source file/field provenance
```

All nodes include display-oriented properties for Neo4j Aura captions:

```text
display_name   short readable caption, such as Step 2 or requires installed
display_label  slightly richer caption, such as Step 2 [uncertain]
short_id       compact source identifier when available
```

These fields are presentation helpers only. They do not change node ids, relationships, validation status, confidence, provenance, or reasoning semantics.

Step nodes also expose source-clip metadata when the graph is built with `--step-records`. In the IndustReal sample graph, each Step node includes:

```text
clip_result_id: raw_cad_dataset__all_test_clips::od_only::test_p1::03_assy_0_1
run_id: raw_cad_dataset__all_test_clips
mode: od_only
archive_name: test_p1
clip: 03_assy_0_1
```

Step nodes also expose validation diagnostics when present:

```text
warning_count
warnings
has_rule_coverage
matched_rule_count
produced_constraint_count
has_expected_effect
unsupported_action
unsupported_action_name
invalidates_effect_count
invalidated_effects
```

For the sample remove step, these properties make the graph visibly show which installed effect was invalidated by the remove action.

Produced-effect Constraint nodes expose lifecycle fields when Layer 4 provides them:

```text
effect_lifecycle_status: active | invalidated | inactive_rejected
invalidated_by_constraint_id
```

For example, an earlier `produces(installed, wheel, hub)` Constraint can be marked `effect_lifecycle_status="invalidated"` and `invalidated_by_constraint_id` can point to the later `produces(removed, wheel, hub)` Constraint.

The graph also materializes this relationship with an `INVALIDATED_BY` edge between Constraint nodes:

```text
(:Constraint {name: "produces", effect_lifecycle_status: "invalidated"})
  -[:INVALIDATED_BY]->
(:Constraint {name: "produces"})
```

The edge direction reads as "this produced effect was invalidated by that produced effect." The invalidating step and invalidating effect details are intentionally not duplicated on the invalidated node; they can be reached by following `INVALIDATED_BY` to the invalidating Constraint and then following the incoming `PRODUCES` edge back to its Step.

Edge types:

```text
NEXT            Step -> Step ordered by validation index
HAS_PREDICATE   Step -> Predicate
HAS_CONSTRAINT  Step -> Constraint
USES            Step -> Entity from usesObject / usesTool predicates
PRODUCES        Step -> Constraint for produced_effects
REQUIRES        Step -> Constraint for requires / requiresTool / requiresSafety
DEPENDS_ON      later Step -> earlier Step when a requirement is supported by a previous produced effect
SUPPORTED_BY    Constraint -> Predicate or Constraint support evidence
INVALIDATED_BY  invalidated produced-effect Constraint -> invalidating produced-effect Constraint
DERIVED_FROM    Constraint -> Rule and Predicate -> Source
HAS_ENTITY      Predicate or Constraint -> Entity
```

Neo4j import uses only the semantic node type as the Neo4j label:

```text
Step
Predicate
Constraint
Rule
Entity
Source
```

The importer does not add a generic `ProceduralReasoningGraph` or `ProceduralReasoningGraphNode` label. Graph-level identity is kept as node and relationship properties, especially `graph_name="procedural_reasoning_graph"` and `schema_version`, so all imported nodes can still be queried by graph name without cluttering the Aura visualization labels.

Accepted, uncertain, and rejected steps are included by default. `--exclude-rejected` omits rejected steps. Rejected steps are not allowed to support later `DEPENDS_ON` edges. Uncertain steps may support later dependencies, but those dependency edges are marked `provisional=true`.

## Current Output Contract

Current predicate records include:

```text
schema_version
record_type
id
step_id
name
predicate_key
category
args
conf
source
notes
```

`name` is the configured predicate name used by Layer 3 matching.

`predicate_key` is the stable adapter key used to trace the predicate back to the adapter extraction path.

`category` comes from the config grouping under `adapter.predicates`.

`source` records which CSV file and fields produced the predicate.

The reasoning-record contract is stable: the adapter writes `step_records.jsonl` and `predicates.jsonl`, Layer 3 writes `inferred_constraints.csv` plus `rule_coverage_diagnostics.csv`, and Layer 4 writes `validation_records.jsonl` plus human/debug views in `step_validations.csv`, `explanation_traces.json`, and `effect_history_diagnostics.csv`. Validation records include requirement support, missing requirements, dependency support, `invalidated_effects`, and `produced_effect_lifecycle`. The procedural graph export writes JSON plus node/edge CSV files, and node properties include presentation helpers such as `display_name`, `display_label`, and `short_id`.

Configured domain components use the domain individual `name` in predicate arguments, such as `base`, while generic classes stay class-like, such as `Base` or `Chassis`. Labels remain separate through `hasLabel(base, "base")`.

## Domain Configuration

Component-specific assembly knowledge is stored separately in:

```text
config/domain_config.yaml
```

This file maps source component ids to generic assembly roles and relations:

```text
component id/name
generic type
parent component
expected installation target
required tool
required assembly conditions
safety requirements
```

For example, the config maps both `front_chassis` and `rear_chassis` to `Chassis`, and maps chassis pins to `ChassisPin` with their parent chassis as the installation target.

The domain config now also carries lightweight ontology-style metadata:

```text
type_hierarchy
type_defaults
condition_vocabulary
predicate_aliases
```

`type_hierarchy` makes generic classes explicit. The adapter emits the configured class and its parents, for example `isA(front_bracket_screw, Screw)`, `isA(front_bracket_screw, Fastener)`, and `isA(front_bracket_screw, Component)`.

`type_defaults` provides common requirements for all components of a generic type unless the component overrides the field. For example, `Screw` defines `required_tool: screwdriver`, and `ChassisPin` defines aligned and secured requirements shared by all chassis pins.

`condition_vocabulary` controls condition names and arities used by `required_conditions` and `safety_requirements`. The adapter validates those configured conditions at load time and raises a clear error for unknown names or wrong argument counts.

The adapter materializes this domain config into predicates such as:

```text
isA(component, Chassis)
isA(component, Component)
hasInstallTarget(component, target)
requiresInstalledBefore(component, target, support)
hasParentComponent(component, parent)
hasRequiredCondition(component, aligned, component, target)
hasSafetyRequirement(component, secured, base, workspace)
hasRequiredTool(component, screwdriver)
```

Layer 3 rules then match these generic predicates. The rule engine does not hardcode specific component names; object-specific knowledge comes from `domain_config.yaml`.

In principle, this domain config can be generated from CAD metadata. A CAD-derived generator could inspect assembly hierarchy, mating constraints, fastener relationships, component names, and contact/constraint graphs to propose generic types, parent components, installation targets, and required tools. The current file is manually authored from the exported IndustReal component list.

## Practical Commands

Build adapter outputs for a filtered clip:

```powershell
.venv\Scripts\python.exe scripts\14_build_layer3_reasoning_adapter.py `
  --clip-result-id raw_cad_dataset__all_test_clips::od_only::test_p1::03_assy_0_1 `
  --output-dir results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1
```

Run Layer 3 inference:

```powershell
.venv\Scripts\python.exe scripts\15_run_layer3_inference.py `
  --step-records results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\step_records.jsonl `
  --predicates results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\predicates.jsonl `
  --output results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\inferred_constraints.csv
```

This also writes:

```text
results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\rule_coverage_diagnostics.csv
```

Run Layer 4 validation:

```powershell
.venv\Scripts\python.exe scripts\16_run_layer4_validation.py `
  --step-records results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\step_records.jsonl `
  --predicates results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\predicates.jsonl `
  --constraints results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\inferred_constraints.csv `
  --rule-coverage results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\rule_coverage_diagnostics.csv `
  --output results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\validation_records.jsonl
```

Build the procedural reasoning graph:

```powershell
.venv\Scripts\python.exe scripts\17_build_procedural_reasoning_graph.py `
  --validations results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\validation_records.jsonl `
  --step-records results\reasoning_layers\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1\step_records.jsonl `
  --output-dir results\procedural_reasoning_graph\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1
```

Import the procedural reasoning graph into Neo4j:

```powershell
.venv\Scripts\python.exe scripts\18_import_procedural_reasoning_graph_neo4j.py `
  --graph results\procedural_reasoning_graph\raw_cad_dataset__all_test_clips__sample_test_p1_03_assy_0_1
```

Verify Neo4j labels after import:

```cypher
MATCH (n)
RETURN labels(n) AS labels, count(*) AS count
ORDER BY count DESC;
```

```cypher
MATCH (n)
WHERE n.graph_name = "procedural_reasoning_graph"
RETURN labels(n) AS labels, count(*) AS count
ORDER BY count DESC;
```

Expected label combinations are single semantic labels such as `["Step"]`, `["Constraint"]`, `["Predicate"]`, `["Rule"]`, `["Entity"]`, and `["Source"]`.

Verify display properties after import:

```cypher
MATCH (s:Step)
WHERE s.graph_name = "procedural_reasoning_graph"
RETURN s.display_name, s.display_label, s.status, s.confidence
ORDER BY s.index;
```

Use a different predicate/rule config:

```powershell
.venv\Scripts\python.exe scripts\14_build_layer3_reasoning_adapter.py --predicate-config path\to\custom_rules.yaml
```

Use a different domain config:

```powershell
.venv\Scripts\python.exe scripts\14_build_layer3_reasoning_adapter.py --domain-config path\to\domain_config.yaml
```

## Notes For Future README Integration

This implementation currently treats the reasoning adapter as a downstream bridge from the existing graph export to thesis-style reasoning records.

The most important design point is the separation between:

```text
upstream evidence
  what the existing pipeline exports

adapter predicates
  symbolic facts derived from that evidence

Layer 3 rules
  procedural constraints inferred from symbolic facts
```

That separation is useful because it keeps provenance clear. If a later layer derives a constraint, it can be traced back to the rule, the matched predicates, and the original CSV fields that produced those predicates.
