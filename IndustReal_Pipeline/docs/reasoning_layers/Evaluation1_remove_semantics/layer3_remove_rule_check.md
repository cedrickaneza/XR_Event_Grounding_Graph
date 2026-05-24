# Layer 3 Remove Rule Check

Evaluated sample:

```text
run_id: raw_cad_dataset__all_test_clips
mode: od_only
archive: test_p1
clip: 03_assy_0_1
remove step index: 9
remove step action: Remove front wheel assy
```

## Rules Added

The remove action is covered by two config-driven Layer 3 rules:

```text
precondition_remove_requires_component_installed
hasAction(step, remove)
usesObject(step, component)
isA(component, Component)
hasInstallTarget(component, target)
  -> requires(step, installed, component, target)
```

```text
effect_remove_component_from_target
hasAction(step, remove)
usesObject(step, component)
isA(component, Component)
hasInstallTarget(component, target)
  -> produces(step, removed, component, target)
```

## Result

Layer 3 inference produced two constraints for the remove step:

```text
requires(step::...::event_9, installed, front_wheel_assy, front_chassis)
produces(step::...::event_9, removed, front_wheel_assy, front_chassis)
```

The corresponding `rule_coverage_diagnostics.csv` row reports:

```text
action_name: remove
matched_rule_count: 2
produced_constraint_count: 2
has_expected_effect: true
has_requirement: true
has_rule_coverage: true
warning_code: <empty>
```

## Scope Note

This is intentionally only the first safe step. Layer 4 active-effect invalidation has not been implemented here, so this evidence should not be interpreted as full temporal state retraction after removal.
