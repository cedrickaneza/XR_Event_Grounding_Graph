# Layer 4 Remove Validation Check

Evaluated sample:

```text
run_id: raw_cad_dataset__all_test_clips
mode: od_only
archive: test_p1
clip: 03_assy_0_1
remove step index: 9
remove step action: Remove front wheel assy
```

## Active-State Semantics

Layer 4 now separates:

```text
historical produced effects
active produced effects
```

Historical effects are retained for traceability. Active effects are the only effects used to support future requirements.

For a remove effect:

```text
produces(step, removed, component, target)
```

Layer 4 invalidates the matching active installed condition:

```text
installed(component, target)
```

The invalidated effect remains in the trace as historical evidence.

## Remove Step Result

For the sample remove step:

```text
step: step::raw_cad_dataset__all_test_clips::od_only::test_p1::03_assy_0_1::event_9
status: accepted
required condition: installed(front_wheel_assy, front_chassis)
supporting step: event_8
supporting effect status: accepted
provisional support: false
invalidated condition: installed(front_wheel_assy, front_chassis)
invalidating effect: removed(front_wheel_assy, front_chassis)
```

The status is accepted because the required installed condition was actively supported by a prior accepted install step. The remove step then invalidated that active installed effect for future dependency checks.

## Evidence Files

- `validation_records.jsonl` contains `invalidated_effects` in the remove-step validation record and trace.
- `step_validations.csv` contains the same invalidation evidence in tabular form.
- `effect_history_diagnostics.csv` records both produced effects and invalidated effects.
