---
name: svg-plan-to-godot
description: Inspect conventionally annotated HTML or SVG game plans, explain which SVG elements are recognized, explicitly ignored, unrecognized, or invalid, resolve semantic mappings, and convert annotated plans into Godot 4 3D blockout scenes with meshes, collisions, door, window, and floor openings, markers, and JSON reports. Use when you need to optionally preflight a plan, interpret inspect_svg_plan.py or svg_to_godot3d.py output, choose conversion settings, suppress intentionally excluded furniture or labels, diagnose plan-to-scene conversion, or generate and verify a Godot scene from an HTML/SVG floor plan.
---

# SVG plan to Godot

Use the bundled scripts as the canonical tool version:

- `scripts/inspect_svg_plan.py` — inspect only; never generate scene files.
- `scripts/svg_to_godot3d.py` — convert to a Godot 4 scene package.

Resolve `<skill-dir>` to the directory containing this `SKILL.md`. Run the scripts by absolute path so the current working directory does not affect imports. Use Python 3.10 or newer; no third-party packages are required.

Read [references/tool-contract.md](references/tool-contract.md) before choosing non-default parameters, interpreting JSON fields, or diagnosing a failed run.

## Workflow

1. Identify the input `.html` or `.svg`, the Godot project root, output directory, optional JSON configuration, and requested content exclusions.
2. Select `generic` unless the input or project contract explicitly uses the `codex-plan` classes. Do not infer custom class meanings from colors or labels.
3. Decide whether inspection is useful. It is optional, not a prerequisite for conversion. Run it when the input or semantic mappings are unfamiliar, the user requests a preflight, unresolved elements are likely, mappings have changed, or conversion output needs diagnosis. A trusted, already understood input may be converted directly.
4. When running inspection, request JSON and save it:

```powershell
python "<skill-dir>\scripts\inspect_svg_plan.py" "<input>" `
  --profile <profile> `
  --json --output-json "<preflight-report.json>"
```

5. If inspection was run, interpret all four categories:
   - `recognized`: the classifier assigned a supported semantic type. This proves classification, not spatial correctness.
   - `explicitly_ignored`: a rule deliberately mapped the element to `ignore`. Do not call it missing geometry.
   - `unrecognized`: no semantic rule exists. Treat it as blocking until the user or an existing project contract assigns a type or explicitly ignores it.
   - `invalid`: the semantic type or SVG geometry cannot be parsed. Always block conversion.
6. For each unresolved inspected element, report its `id`, SVG tag, classes, and exact reason. Suggest a concrete `--map-class CLASS=TYPE` or `--set ELEMENT.type=TYPE`. Never silently convert an unknown element or silently map it to `ignore`.
7. If inspection revealed unresolved semantics and they were resolved, rerun it with the accepted options and `--strict` before converting. Otherwise the agent may proceed directly to conversion at its discretion.
8. When inspection was used, keep its semantic options identical in conversion. Add conversion-only geometry and output parameters separately. When inspection was skipped, choose conversion options from the input contract, configuration, user request, and documented defaults.
   - In version 1.16 or newer, inspection and conversion share the same `spatial_handoff` contract. Keep every geometry-affecting option identical too when comparing or relying on that section.
9. Read `conversion_report.json`; do not rely only on console text. Require `errors` to be empty. Interpret warnings and assumptions explicitly.
   - In version 1.17 or newer, also inspect `spatial_handoff.wall_volume_overlaps`. It lists exact positive-volume intersections between parallel final wall segments after openings, normalization, and joint resolution. Explain every pair; use `--strict-wall-overlaps` when such overlaps must block output.
   - In version 1.18 or newer, inspect `ceiling_construction` and `ceiling_wall_alignment_issues`. `default_elevation` is a floor/base level; an explicit ceiling without `ceiling_elevation` uses `default_elevation + ceiling_height`. Use `--strict-ceiling-alignment` when ceilings are required to meet wall tops.
   - In version 1.19 or newer, inspect `anchor_frames` and `anchor_frame_issues` when downstream content depends on semantic wall, door, floor, ceiling, or marker frames. Treat a relevant issue as blocking downstream binding even when legacy geometry conversion succeeds.
10. Verify every filename in `files` exists and is non-empty. Verify the `.tscn` mesh `res://` path matches the selected Godot project/output location.
11. If a Godot executable is available, import or load the generated scene headlessly. Otherwise state that structural/package checks passed but engine loading remains unverified.

## Semantic-option invariant when inspection is used

Keep these options identical between the final strict inspection and conversion:

- `--profile`
- `--config`
- all `--map-class`
- all semantic `--set` overrides, especially `ELEMENT.type`

If any of them changes after an inspection whose result is being relied upon, rerun that inspection. Do not present an older preflight report as applying to different semantic options. This rule does not require creating a preflight report when the agent chooses direct conversion.

## Decision rules

- Allow `explicitly_ignored` when the mapping is user-provided, configuration-provided, or part of the selected profile.
- Do not treat decorative text as harmless merely because it is `<text>`; explicitly map its class to `ignore` when the user or project contract excludes labels.
- Block on unsupported path commands, malformed polygons, missing four-number `viewBox`, unknown semantic types, and any `invalid` item.
- Treat a stair warning as material: stairs remain `Marker3D` unless the converter version explicitly supports stair geometry.
- Require `floor-opening` and `ceiling-opening` to be explicit closed rects, polygons, or closed paths (or explicit independent opening flags on an `area`). Decorative stair lines never imply either cut. Confirm floor and ceiling requested/applied counts separately.
- Distinguish `opening`, `door`, and `window`: an unqualified `opening` is full-height; a `door` keeps the wall above it; a `window` keeps wall below and above it. Report the effective bottom and height when they matter.
- Do not interpret `generated_walls > wall_segments_after_merge` as duplication by itself. Intersection splitting can increase the final count. Use normalization fields from the report.
- In version 1.17 or newer, use `counts.wall_volume_overlaps` and `spatial_handoff.wall_volume_overlaps` to diagnose offset duplicate walls, especially walls with different thicknesses that normalization intentionally cannot merge. Perpendicular butt/cross joints are excluded. Default behavior is a warning; `--strict-wall-overlaps` independently returns code 2.
- For explicit `ceiling`, never treat `data-default-elevation` as an absolute ceiling position. Use `data-ceiling-elevation` or `ELEMENT.ceiling_elevation` for the underside. In version 1.18 or newer, explain the formula warning and inspect `counts.ceiling_wall_alignment_issues`; do not accept a clean syntax-only exit as proof of vertical alignment.
- When the map contract requires ceilings flush with wall tops, pass identical `--ceiling-alignment-tolerance` and `--strict-ceiling-alignment` options to inspection and conversion. A reported issue means the ceiling underside differs from the nearest spatially related wall-top elevation beyond tolerance. No issue is emitted when no exact spatial wall relationship can be established.
- For floor or ceiling flicker or suspected overlap, inspect the corresponding polygon/cell counts and removed-overlap assumption. Coplanar slabs are normalized independently; different elevation or thickness intentionally remains separate.
- In converter version 1.14 or newer, treat `Floors`, `Ceilings`, `Walls`, and `Obstacles` as logical components. Explicit internal `wall` elements split area slabs into room components; decomposition cells are repackaged into one named visual/collision pair per component. Walls and obstacles remain per generated part. Their `Collisions` subtrees mirror visual names.
- When users report floor/ceiling strips, distinguish editor selection triangulation from rendered shading. Version 1.14 OBJ must contain `s off`, explicit flat `vn` records, and no uncancelled internal slab side faces; verify an unselected/runtime render before calling thin editor wireframe lines a geometry defect.
- Aggregate `floor_geometry.obj`, `wall_geometry.obj`, and `obstacle_geometry.obj` files are compatibility exports and are not referenced by version 1.7 scenes. Verify the scene references the numbered per-part OBJ files and compare `generated_*_visual_nodes` with the corresponding generated geometry counts.
- Generated `.tres` materials are user-owned after first creation. A repeat conversion must preserve them; use the report's created/preserved material fields when diagnosing material resets.
- Version 1.17 removes only numbered per-part OBJ files listed in the previous `conversion_report.json` before regeneration. Use `counts.removed_stale_generated_parts` to distinguish safely removed stale outputs from current scene references; arbitrary user files are not cleanup targets.
- In version 1.19 or newer, require semantic anchor IDs to derive from explicit source `id`/`data-godot-id`. Never substitute generated OBJ/segment indices. Door inside/outside thresholds require explicit `inside_side`; hinge requires explicit `hinge_side`. Do not infer either side from authored endpoint order or nearby geometry.
- Do not claim that a successful conversion proves the map is playable or architecturally correct.
- Preserve the input file. Apply semantics through config or CLI unless the user explicitly asks to annotate the source.

## Report to the user

Show:

- the exact conversion command and, when inspection was run, its exact command;
- profile, config, mappings, scale, dimensions, elevation, origin, collision layer, and exclusions;
- preflight category counts and every remaining blocking item when inspection was run;
- conversion errors, warnings, assumptions, and generated counts;
- wall- and floor-normalization before/after counts;
- clickable paths to `.tscn`, `.obj`, `.mtl`, and JSON reports;
- whether Godot itself loaded the scene.

When inspection is used, stop after it if user input is required to classify an element. Do not guess a topology-changing or content-changing semantic assignment.
