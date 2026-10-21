# Tool contract

## Contents

1. Entry points and exit codes
2. Recognition and precedence
3. Inspection report
4. Conversion settings
5. Conversion report
6. Supported SVG and semantic types
7. Interpretation examples

## 1. Entry points and exit codes

### Inspector

```text
inspect_svg_plan.py input
  [--config FILE]
  [--profile generic|codex-plan]
  [--map-class CLASS=TYPE]...
  [--set ELEMENT.KEY=VALUE]...
  [the same geometry settings as the converter]
  [--json]
  [--output-json FILE]
  [--strict]
  [--strict-wall-overlaps]
  [--ceiling-alignment-tolerance FLOAT]
  [--strict-ceiling-alignment]
```

- `0`: inspection ran; with `--strict`, no unrecognized or invalid elements exist.
- `2`: input/configuration failure, `--strict` found unrecognized/invalid elements, `--strict-wall-overlaps` found intersecting parallel final wall volumes, or `--strict-ceiling-alignment` found a ceiling/wall vertical mismatch.
- `ignore` elements never make strict inspection fail.

### Converter

```text
svg_to_godot3d.py input output_dir
  [--config FILE]
  [--profile generic|codex-plan]
  [--project-root DIR | --resource-dir res://PATH]
  [--scene-name NAME]
  [--scale FLOAT]
  [--wall-height FLOAT]
  [--wall-thickness FLOAT]
  [--wall-embed FLOAT]
  [--wall-joint-mode butt|overlap]
  [--wall-joint-gap FLOAT]
  [--floor-thickness FLOAT]
  [--floor-anchor center|top]
  [--floor-mesh surface|slab]
  [--floor-extent wall-outer|centerline]
  [--floor-edge-offset FLOAT]
  [--no-ceilings]
  [--ceiling-height FLOAT]
  [--ceiling-thickness FLOAT]
  [--ceiling-mesh surface|slab]
  [--ceiling-extent wall-outer|centerline]
  [--ceiling-edge-offset FLOAT]
  [--obstacle-height FLOAT]
  [--elevation FLOAT]
  [--opening-snap FLOAT]
  [--door-height FLOAT]
  [--door-bottom FLOAT]
  [--window-height FLOAT]
  [--window-bottom FLOAT]
  [--wall-merge-tolerance FLOAT]
  [--origin center|top-left|none]
  [--invert-z]
  [--collision-layer INT]
  [--include-preview]
  [--map-class CLASS=TYPE]...
  [--set ELEMENT.KEY=VALUE]...
  [--strict]
  [--strict-wall-overlaps]
  [--ceiling-alignment-tolerance FLOAT]
  [--strict-ceiling-alignment]
```

- `0`: conversion completed with no registered errors.
- `2`: invalid input/configuration or registered conversion errors.

## 2. Recognition and precedence

Semantic type precedence, highest first:

1. `--set ELEMENT.type=TYPE`
2. `element_overrides.ELEMENT.type` from JSON config
3. SVG `data-godot-type`
4. first matching CSS class in the active class map
5. unrecognized

Numeric value precedence, highest first:

1. `--set ELEMENT.KEY=VALUE`
2. SVG `data-*` attribute
3. conversion CLI option
4. config `defaults`
5. built-in default

Supported semantic types:

- `area`: floor, ceiling, plus boundary walls
- `floor`: floor only
- `floor-opening`: closed polygon subtracted from floor visual and collision geometry at the same elevation
- `ceiling`: ceiling only
- `ceiling-opening`: closed polygon subtracted only from ceiling visual and collision geometry
- `wall`: wall segments
- `opening`: full-height subtraction by default; optional vertical limits
- `door`: floor-level opening with a lintel plus `Marker3D`
- `window`: opening with a sill and lintel plus `Marker3D`
- `obstacle`: solid obstacle and collision
- `stairs`: `Marker3D` in the current tool version
- `marker`: `Marker3D`
- `ignore`: intentionally exclude

`generic` recognizes literal/common classes such as `area`, `room`, `floor`, `floor-opening`, `floor-hole`, `wall`, `partition`, `opening`, `door-gap`, `door`, `window`, `window-gap`, `obstacle`, `stairs`, `steps`, `marker`, and `ignore`. `floor-hole` maps to `floor-opening`; `door-gap` maps to `door`.

`codex-plan` additionally maps `old`, `corridor`, and `support` to `area`; `equipment` to `obstacle`; `room-gap` to full-height `opening`; `door-gap` to `door`; `partition` to `wall`; `steps` to `stairs`; and visual `door` swing graphics to `ignore`.

## 3. Inspection report

Top-level fields:

- `inspector_version`: bundled tool version.
- `source`: resolved input path.
- `profile`: active class profile.
- `view_box`: four SVG viewBox numbers.
- `summary`: category counts.
- `recognized`: successfully parsed semantic elements.
- `explicitly_ignored`: elements mapped to `ignore`.
- `unrecognized`: elements with no semantic mapping.
- `invalid`: parsing/type error strings.
- `warnings`: non-fatal limitations, including detected wall-volume overlaps when strict overlap mode is disabled.
- `settings`: effective settings used for the spatial calculation.
- `spatial_handoff`: the same exact-data-only spatial contract used by conversion.

`summary` fields:

- `recognized`: number of elements that will participate in conversion.
- `explicitly_ignored`: intentional exclusions.
- `unrecognized`: unresolved elements; block conversion.
- `invalid`: malformed/unsupported elements; block conversion.

Recognized item fields:

- `id`: stable SVG ID, `data-godot-id`, or generated order-dependent ID.
- `tag`: SVG element name.
- `type`: assigned semantic type.
- `classes`: source CSS classes.
- `path_count`: parsed subpath count; not a generated-object count.

Ignored/unrecognized item fields:

- `id`, `tag`, `classes`
- `reason`: `mapped to ignore` or `no recognized semantic type`

An inspection success means the source was classified under the supplied rules. In version 1.17 or newer it also reports exact parallel final-wall volume overlaps, but it does not validate mission topology, scale, playability, or Godot import.

## 4. Conversion settings

Built-in defaults:

| Setting | Default | Meaning |
|---|---:|---|
| `scale` | `0.05` | metres per SVG unit |
| `wall_height` | `3.0` | wall height, metres |
| `wall_thickness` | `0.18` | wall thickness, metres |
| `floor_thickness` | `0.12` | floor slab thickness, metres |
| `floor_anchor` | `top` | place elevation at the walking/top surface; `center` centers the slab on elevation |
| `floor_mesh` | `slab` | emit a closed slab below elevation; `surface` emits only the flat top |
| `floor_extent` | `wall-outer` | extend `area` floors from wall centerlines to outer wall faces |
| `floor_edge_offset` | inferred | explicit outward offset; when omitted, use half each area's wall thickness |
| `generate_ceilings` | `true` | generate a ceiling for every `area`; `--no-ceilings` disables automatic ceilings |
| `ceiling_height` | wall height | default underside height for explicit `ceiling` elements |
| `ceiling_thickness` | `0.12` | ceiling slab thickness extending upward from its underside |
| `ceiling_mesh` | `slab` | closed slab or underside-only `surface` |
| `ceiling_extent` | `wall-outer` | extend area ceilings to outer wall faces |
| `ceiling_edge_offset` | inferred | explicit outward offset; when omitted, use half each area's wall thickness |
| `wall_embed` | `0.0` | optionally extend base wall visual and collision below elevation without changing wall top |
| `wall_joint_mode` | `butt` | trim branch walls at joints instead of overlapping wall boxes |
| `wall_joint_gap` | `0.0005` | tiny render clearance at butt joints, metres |
| `obstacle_height` | `1.2` | default obstacle height, metres |
| `default_elevation` | `0.0` | base Y elevation, metres |
| `opening_snap` | `0.10` | opening-to-wall snap tolerance, metres |
| `door_height` | `2.1` | default door opening height, metres |
| `door_bottom` | `0.0` | door bottom above wall elevation, metres |
| `window_height` | `1.2` | default window opening height, metres |
| `window_bottom` | `1.0` | window sill height above wall elevation, metres |
| `wall_merge_tolerance` | `0.02` | collinear merge tolerance, metres |
| `strict_wall_overlaps` | `false` | make detected parallel final-wall volume intersections fatal |
| `ceiling_alignment_tolerance` | `0.01` | allowed ceiling-underside to nearest related wall-top difference, metres |
| `strict_ceiling_alignment` | `false` | make detected ceiling/wall vertical mismatches fatal |
| `origin` | `center` | SVG-to-Godot origin mode |
| `invert_z` | `false` | reverse mapped Z direction |
| `collision_layer` | `1` | layer and mask for generated static bodies |
| `include_preview` | `false` | add preview camera/light/environment |

Coordinate mapping: SVG X becomes Godot X, SVG Y becomes Godot Z, and vertical height uses Godot Y. `--invert-z` reverses Z.

With the default `floor_mesh=slab` and `floor_anchor=top`, floor visual geometry is a closed slab whose top is exactly at elevation `E` and whose thickness extends only downward. Correct outward winding keeps the top visible without raising a rim above wall bases. `floor_mesh=surface` explicitly requests only upward-facing top triangles and matching top collision. Base wall segments may optionally extend down by `wall_embed`, which defaults to zero.

With the default `floor_extent=wall-outer`, each `area` floor polygon is offset outward by half that area's effective wall thickness so its outer boundary matches the outer wall face. `floor_edge_offset` supplies an explicit global distance; `ELEMENT.floor_edge_offset` or `data-floor-edge-offset` overrides one floor or floor opening. A floor opening whose original polygon exactly matches an `area` polygon inherits that area's offset, preserving whole-room stair shafts without a residual floor rim. Explicit `floor` elements remain unchanged unless they have their own offset. `centerline` restores the legacy area boundary.

Automatic area ceilings use the area's effective wall height as their underside and extend thickness upward. Explicit `ceiling` uses `ceiling_elevation` when supplied, otherwise `default_elevation + ceiling_height`. `default_elevation`, `data-default-elevation`, and `data-elevation` are base/floor levels, never absolute ceiling positions. Use `data-ceiling-elevation` or a `ceiling_elevation` override for an absolute underside. An explicit per-element base elevation without an explicit ceiling elevation produces a formula warning. Ceiling extent and edge-offset rules mirror floors. Ceiling collision triangles face downward into the room.

`floor-opening` and `ceiling-opening` are independent. A ceiling opening without explicit `ceiling_elevation` targets every spatially overlapping ceiling elevation. To cut both slabs, supply both semantic elements. An `area` may keep its room/wall role and additionally set `floor_opening=true` and/or `ceiling_opening=true` via `data-*`, config, or `--set`; the two flags remain independent.

With the default `wall_joint_mode=butt`, the converter selects a continuous wall direction at T/cross junctions, trims perpendicular branch endpoints to the continuous wall surface, and extends one wall across an L corner before trimming the other. `wall_joint_gap` prevents coincident coplanar faces. `overlap` restores the legacy intersecting-box behavior. Automatic butt resolution applies only to walls with matching elevation and height.

`--project-root` must contain the output directory so the converter can derive the `res://` OBJ path. Use `--resource-dir` only when the caller already knows the exact corresponding `res://` directory.

Per-element override keys currently consumed by geometry include `type`, `wall_height`, `wall_thickness`, `floor_thickness`, `obstacle_height`, `default_elevation`, `opening_bottom`, `opening_height`, `door_bottom`, `door_height`, `window_bottom`, and `window_height`. Equivalent SVG attributes use `data-` and hyphens, for example `data-window-bottom`.

Vertical opening rules:

- `opening` without `opening_height` cuts the complete wall height for backward compatibility.
- `door` defaults to bottom `0.0` and height `2.1`, leaving a lintel when the wall is taller.
- `window` defaults to bottom `1.0` and height `1.2`, leaving a sill and lintel when the wall is tall enough.
- Element-specific door/window keys take precedence over generic `opening_bottom` and `opening_height`.
- Bottom is relative to the wall element's elevation. Non-positive heights and negative bottoms are conversion errors.

## 5. Conversion report

Top-level fields:

- `generator_version`: bundled converter version.
- `source`, `scene`, `profile`, `view_box`
- `settings`: effective global settings
- `spatial_handoff`: exact generated-scene-local opening geometry, construction values, levels, markers, explicit anchors, and space IDs for downstream generators
- `counts`: input and generated counts
- `assumptions`: automatic operations that require interpretation
- `warnings`: non-fatal limitations
- `errors`: fatal or skipped conversion problems
- `ignored`: elements excluded or unresolved during conversion
- `files`: expected output filenames

Required success conditions:

```text
process exit code == 0
errors == []
every files[] entry exists and is non-empty
.tscn mesh resource path resolves under the selected Godot project
```

Important count fields:

- `input_area`, `input_floor`, `input_floor_opening`, `input_wall`, `input_opening`, `input_door`, `input_window`, `input_obstacle`, `input_stairs`, `input_marker`: classified source counts; absent means zero.
- `wall_segments_before_normalization`: wall pieces after opening subtraction.
- `wall_segments_after_merge`: unique collinear intervals after duplicate/overlap union.
- `wall_segments_after_normalization`: pieces after intersection splitting.
- `wall_joints_resolved`: branch endpoints changed from overlapping boxes to butt joints.
- `wall_volume_overlaps`: detected positive-volume intersections between parallel final wall segments.
- `ceiling_wall_alignment_issues`: ceilings whose undersides differ from the nearest spatially related wall tops beyond tolerance.
- `removed_stale_generated_parts`: numbered part OBJ files listed by the prior report and removed before regeneration.
- `floor_polygons_before_normalization`: source coplanar floor polygons before union.
- `area_floors_expanded_to_wall_outer`: `area` floor polygons offset to outer wall faces.
- `floor_cells_after_normalization`: non-overlapping floor cells after union.
- `floor_openings_requested`, `floor_openings_applied`: closed floor cuts supplied and cuts overlapping a floor at the same elevation.
- `ceiling_polygons_before_normalization`, `ceiling_cells_after_normalization`: ceiling equivalents.
- `ceiling_openings_requested`, `ceiling_openings_applied`: independent ceiling-cut counts.
- `generated_floors`, `generated_ceilings`, `generated_walls`, `generated_obstacles`, `generated_markers`: output-object counts.
- `generated_floor_visual_nodes`, `generated_wall_visual_nodes`, `generated_obstacle_visual_nodes`: separate `MeshInstance3D` counts in the category `Visual` subtrees; in version 1.7 they should match the corresponding generated geometry counts.

Interpret wall normalization as follows:

- `after_merge < before`: duplicates or overlapping collinear intervals were removed/combined.
- `after_normalization > after_merge`: long walls were split at T/cross junctions; this alone is not duplication.
- Walls with different height, thickness, or elevation are intentionally not merged.
- `spatial_handoff.wall_volume_overlaps[]` identifies remaining parallel prism intersections after openings, normalization, and butt-joint resolution. Each record includes source IDs, final segment indices, axis offset, overlap length/depth/height and estimated volume. Perpendicular joints are excluded. Without `--strict-wall-overlaps` these are warnings; with it they are errors and exit code 2.

Interpret floor and ceiling normalization as follows:

- Coplanar polygons with equal construction elevation, thickness, and floor anchor are unioned before visible geometry and collision triangles are emitted.
- `Floor normalization removed N m2 of coplanar overlap` reports the repeated source area removed from the output.
- Floors with different top elevation or thickness are intentionally not unioned.
- Floor openings are subtracted after coplanar union from the same cells used for OBJ and `ConcavePolygonShape3D`, so visible and collision holes match.
- `Floor openings removed N m2...` records the actual cut area. A requested opening that does not overlap a same-elevation floor produces a warning.
- `generated_floors` and `generated_ceilings` count packaged room objects, not decomposition cells; use the corresponding `*_cells_after_normalization` fields for cell counts.
- Explicit `wall` centerlines split a source region into room components for both floors and ceilings. Splitter ends extend by half wall thickness so nearly closed partitions connect reliably. Area boundary walls do not create redundant components.
- Decomposition cells in one room component are packaged into one OBJ, one `MeshInstance3D`, and one `StaticBody3D`; cell counts can exceed generated object counts.
- Generated OBJ uses `s off` and explicit flat face normals. Atomic boundary-edge cancellation removes internal slab side faces even when one long edge meets several shorter decomposition edges. Editor triangulation lines may remain visible only as selection wireframe, not as lighting/material bands.

Output package:

- `<scene-name>.tscn`: Godot scene
- `floor_geometry.obj`, `ceiling_geometry.obj`, `wall_geometry.obj`, `obstacle_geometry.obj`: aggregate compatibility exports; scenes do not reference them
- `floor_###_<source-id>.obj`, `ceiling_###_<source-id>.obj`, `wall_###_<source-id>.obj`, `obstacle_###_<source-id>.obj`: referenced visible meshes
- `floor_material.tres`, `ceiling_material.tres`, `wall_material.tres`, `obstacle_material.tres`: editable Godot materials, created only when absent and preserved on regeneration
- `geometry.mtl`: fallback OBJ import materials
- `conversion_report.json`: authoritative report

Scene composition:

```text
Root
|- Floors
|  |- Visual
|  |  |- Floor_001_<source-id> (MeshInstance3D)
|  |  `- Floor_002_<source-id> (MeshInstance3D)
|  `- Collisions (matching named StaticBody3D nodes)
|- Ceilings
|  |- Visual (one named MeshInstance3D per detected room component)
|  `- Collisions (matching named StaticBody3D nodes)
|- Walls
|  |- Visual (one named MeshInstance3D per wall segment)
|  `- Collisions (matching named StaticBody3D nodes)
|- Obstacles
|  |- Visual (one named MeshInstance3D per obstacle)
|  `- Collisions (matching named StaticBody3D nodes)
`- Markers
```

All per-part visual nodes share the category `.tres` material by default. Assigning a different `material_override` to one node customizes only that part. Matching visual and collision node names plus `metadata/source_id` provide traceability to the source SVG.

The report includes `materials`, `created_materials`, and `preserved_materials`. Existing material files are intentionally not overwritten.

In version 1.16 or newer, inspection and conversion call the same spatial-contract implementation. With identical semantic and geometry-affecting options, both `spatial_handoff` objects must be equal. Interpret it as follows:

- `coordinate_space` declares metre units, `X/Z` horizontal axes, `Y` vertical axis, origin mode, and Z inversion.
- `surface_openings[]` reports the effective post-offset floor/ceiling opening polygon, area, AABB dimensions, AABB `local_center_m`, polygon `centroid_m`, elevation, per-opening application status, contained markers, and an exact `is_stair_shaft_candidate` containment flag.
- `wall_construction[]` reports effective wall thickness, height, and base elevation per source element/property combination.
- `floor_levels_m` reports exact generated walking-surface elevations. `floor_to_floor_heights[]` exists only when two or more distinct floor levels exist; a ceiling height is not substituted for a floor-to-floor height.
- `reference_points[]` contains all generated marker positions. `anchors[]` contains only markers explicitly annotated by `data-anchor-id` or `data-anchor-role`, or equivalent overrides. Never promote an unannotated marker to an anchor.
- In version 1.19 or newer, `anchor_frames[]` contains T01 semantic frames for source walls, doors, floors, ceilings, and explicitly annotated `marker` elements. Every record includes `anchor_id`, `type`, `role`, `status`, `source_ref`, `origin`, `forward`, `normal`, `up`, `bounds`, `placement_limits`, and a deterministic `geometry_hash`.
- Anchor-frame identity is derived from an explicit SVG `id` or `data-godot-id`, never from an OBJ number or a post-carving/post-normalization wall segment. A single source wall uses `svg:SOURCE:wall`; an authored multi-edge wall uses source-edge suffixes before generated splitting. Duplicate stable SVG IDs are invalid.
- Linear endpoints are canonicalized in generated-scene coordinates, so reversing a source line does not flip its basis. Moving or resizing the same source ID changes `origin`, bounds, and `geometry_hash` without changing `anchor_id`.
- A wall frame requires explicit `data-surface-side-id` and `data-normal-side=normal|opposite` (or equivalent overrides). A door emits `center`, `threshold_inside`, and `threshold_outside` only with explicit `data-inside-side=normal|opposite`; it emits `hinge` only with explicit `data-hinge-side=start|end` and a valid door side. Missing or invalid side semantics are recorded in `anchor_frame_issues[]`; physical normals are never inferred from endpoint order, nearby rooms, labels, or swing graphics.
- `anchor_frame_issues[]` is an exact-only downstream blocking diagnostic. It does not make legacy geometry-only conversion fail by itself; consumers that require complete anchor semantics must block on relevant entries.
- `space_ids[]` contains only explicit `data-space-id` values or equivalent overrides. `source_id` remains traceability metadata and is not silently redefined as a space ID.
- `wall_volume_overlaps[]` appears only when parallel final wall prisms intersect with positive length, depth, and vertical height; it is shared exactly between inspector and converter for identical options.
- `ceiling_construction[]` reports exact underside, top, and thickness values for generated ceilings.
- `ceiling_wall_alignment_issues[]` appears only when a ceiling polygon covers or touches walls and its underside differs from the nearest candidate wall-top elevation beyond `ceiling_alignment_tolerance`. It includes source IDs, elevations, signed offsets, absolute gap, and tolerance. Use `--strict-ceiling-alignment` to make the issue fatal. If no spatially related wall exists, no relationship is invented.
- Exact-data-only rule: when a value cannot be computed without a semantic guess, its field is omitted. In particular, unsigned door segments and stairs drawings do not produce an entrance/exit orientation.

Supported handoff annotations are `data-space-id`, `data-anchor-id`, `data-anchor-role`, `data-surface-side-id`, `data-normal-side`, `data-inside-side`, and `data-hinge-side`; equivalent override keys use the same names with underscores instead of hyphens.

Warnings are not automatically fatal. Explain each warning. A stair-marker warning means no stair volume was generated. An obstacle bounding-box warning means geometry is approximate.

On regeneration, version 1.17 or newer removes only numbered `floor_###_*.obj`, `ceiling_###_*.obj`, `wall_###_*.obj`, and `obstacle_###_*.obj` files explicitly listed by the previous conversion report. Aggregate exports, materials, unlisted files, and arbitrary user files are preserved.

## 6. Supported SVG and semantic types

Supported shapes:

- `rect`, `line`, `polyline`, `polygon`
- `path` using only `M/m`, `L/l`, `H/h`, `V/v`, `Z/z`
- `circle`, `ellipse`, and `text` only as markers when explicitly typed
- groups with `matrix`, `translate`, `scale`, or `rotate`

Unsupported or invalid:

- arc/curve path commands such as `A`, `C`, `Q`, `S`, `T`
- skew transforms
- malformed/self-intersecting polygons that cannot be triangulated
- missing/non-four-number root `viewBox`
- visual CSS alone as physical semantics

## 7. Interpretation examples

### Decorative labels

If `label`, `tiny`, `minor`, and `context` text classes appear in `unrecognized`, do not silently discard them. When the user or project contract excludes plan labels from 3D output, rerun with:

```text
--map-class label=ignore
--map-class tiny=ignore
--map-class minor=ignore
--map-class context=ignore
```

### Excluding furniture

When the user asks for architecture without interior items and the plan profile maps furniture to `equipment`, use:

```text
--map-class equipment=ignore
```

Confirm in the conversion report that `generated_obstacles == 0` and that no obstacle object/body appears in OBJ/TSCN.

### Unknown rectangle

For an unrecognized `rect` named `storage_bank`, offer the semantically plausible options instead of guessing:

- `--set storage_bank.type=obstacle`
- `--set storage_bank.type=floor`
- `--set storage_bank.type=ignore`

Ask when the choice changes produced geometry or traversability.
