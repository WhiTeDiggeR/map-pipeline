---
name: generate-godot-stairs
description: Generate realistic, playable Godot 4 stair scenes from shaft width, shaft length, and floor height using a deterministic Python CLI. Use when you need to create straight, L-turn, U-turn, or multi-flight perimeter stairs; auto-select a fitting stair layout; add configurable open-side railings and optional gap-filling shaft walls; diagnose dimensions that cannot produce a valid staircase; or verify generated stair scenes, collisions, materials, and reports in Godot.
---

# Generate Godot stairs

Use `scripts/generate_godot_stairs.py` as the canonical generator. Resolve `<skill-dir>` to the directory containing this `SKILL.md` and run the script by absolute path. Require Python 3.10 or newer; no third-party packages are required.

Read [references/tool-contract.md](references/tool-contract.md) before selecting non-default architectural limits, forcing a layout, generating shaft walls, or diagnosing a failed run.
Read [references/architecture.md](references/architecture.md) before modifying the generator or adding a layout.
Use [references/generation-report.schema.json](references/generation-report.schema.json) when validating or consuming machine-readable reports.

## Workflow

1. Obtain the Godot project root, output directory, shaft width, shaft length, and floor-to-floor height. Treat all dimensions as metres.
2. Prefer `--layout auto` unless the user requests a particular form. Auto selection tries `straight`, `l-turn`, `u-turn`, then three- and four-flight `perimeter` layouts.
3. Keep the architectural defaults unless the project has explicit requirements. Do not reduce minimum tread depth, stair width, or headroom silently.
4. Choose railing mode: `auto` for ordinary guards on every unprotected flight and landing edge, `both` for both flight sides plus every exposed landing edge, or `none` to omit them. Treat an edge as wall-protected only when generated wall geometry spans the complete required guard height.
5. Do not generate shaft walls by default. Generate all four gap-filling shaft sides only when both `--shaft-wall-bottom` and `--shaft-wall-top` are explicitly known. Entry and exit remain open below/above that vertical interval. Use `--no-shaft` to force omission even when bounds are present.
6. Run the generator with either `--project-root` or the exact matching `--resource-dir`:

```powershell
python "<skill-dir>\scripts\generate_godot_stairs.py" "<output-dir>" `
  --project-root "<godot-project>" `
  --shaft-width 3.6 --shaft-length 5.8 --floor-height 3.2 `
  --layout auto --railings auto --scene-name main_stair
```

7. Require exit code `0`, `generation_report.json.status == "ok"`, an empty `errors` list, and every reported file to exist and be non-empty.
   Also require `geometry_validation.ok == true`, `geometry_validation.compiled.ok == true`, and `geometry_validation.shaft.ok == true`.
8. Explain `selected_layout`, effective riser and tread dimensions, warnings, assumptions, and any layouts rejected before auto-selection.
   For generator `2.9.0` or newer, also inspect `anchor_frames`: entry, exit, flight passage, landing, clear shaft, and any generated shaft-face frames must agree with `selected_layout` and `anchor_coordinate_space`.
9. If Godot 4.7 is available, load the generated `.tscn` headlessly. Otherwise report that deterministic package checks passed but engine loading remains unverified.
10. When diagnosing topology, add `--debug-geometry`. Debug visuals live under the separate root `Debug`, have no collisions, and identify logical ports, their directions, physical beam boundaries, and connection IDs by node name and metadata.

Before changing geometric code, run the independent core and integration tests:

```powershell
python -m unittest discover -s "<skill-dir>\tests" -v
```

Change a layout or railing contour in the declarative blueprint first. Do not add post-generation Box offsets, rotations, endpoint searches, or bounding-box correction passes to the port pipeline.
The mesh-finishing stage may remove only surface fragments that lie inside an explicitly connected neighbour. It must not change a port, connection, compiled transform, or outer union silhouette.

## Failure rules

- Exit code `2` means generation failed. Read `errors`, `solutions`, and `layout_failures`; do not claim that a partial scene is usable.
- Do not invent a fitting layout after the generator rejects the dimensions. Present its concrete remedies and ask which dimension or constraint may change.
- If only one shaft-wall bound is supplied, stop. Never infer the missing vertical boundary.
- Existing `.tres` materials are user-owned and intentionally preserved on regeneration.
- Require each generated step to consist of one thin horizontal tread and one thin vertical riser closing the interval to the previous tread. A missing riser indicates a pre-1.2 scene; a step volume extending down to the flight base indicates an obsolete pre-1.1 scene. Regenerate either format.
- Require posts to stay inset inside the stair or landing footprint. A post top must stop at the lowest point of the handrail underside over the complete post footprint; it must neither penetrate the rail nor leave a measurable support gap. Post and handrail cross-sections must be equal.
- Require every coordinated joint to exist in the abstract graph before scene geometry is emitted. Near planned endpoints are resolved into one deterministic node using the ordinary half-section tolerance. A remaining gap between a sloped flight port and a perpendicular landing rail must be represented by an explicit short horizontal slope-to-flat member; never enlarge port tolerance to bridge it. Duplicate emitted posts must never be repaired after generation.
- Require connected handrails and midrails to enter the other connector volume maximally without crossing its far boundary. This is computed once by the geometry core for every joint type. Ordinary joints must not receive decorative caps. A spatial turn is expressed as two planar slope-to-flat adapters at one shared elevation. On a perimeter landing those adapters form `slope -> flat -> flat -> slope` through an ordinary horizontal corner; an L-turn retains its full-section socketed corner newel. A direct beam-to-beam spatial slope joint is invalid. Narrow U-turn guards use two planar adapters and a horizontal bridge. A terminal rail first joins a short horizontal adapter by the same slope-to-flat rule; the end post supports that adapter from below. Sloped intermediate supports compile as post-to-block and block-to-rail connections.
- Require connected overlapping beams to pass through the deterministic surface-union stage. It removes only internal face fragments and emits flat-shaded OBJ meshes under `joint_meshes/`; the original overlapping boxes remain the exact static-collision decomposition. CSG nodes and coordinate repair passes are forbidden.
- Require interior flight posts to be anchored inside tread surfaces. Only the first and last flight posts may use the source and destination floor or landing elevations; a post selected from a riser boundary or floating above a tread is invalid.
- A valid generated scene proves structural consistency and Godot loadability, not compliance with local building regulations or fitness for a particular character controller.

## Report to the user

Provide the exact command, selected layout, flight and riser counts, actual stair width/riser/tread, railing mode, whether shaft walls were generated, graph and compiled validation summaries, warnings, and clickable paths to the `.tscn`, materials, and JSON report.
