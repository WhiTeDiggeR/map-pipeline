# Tool contract

## Contents

1. Invocation and exit codes
2. Layouts and selection
3. Architectural defaults
4. Railings and shaft walls
5. Coordinates and output
6. Report and validation

## 1. Invocation and exit codes

```text
generate_godot_stairs.py output_dir
  --shaft-width METRES --shaft-length METRES --floor-height METRES
  [--layout auto|straight|l-turn|u-turn|perimeter]
  [--perimeter-flights auto|3|4]
  [--turn-direction auto|left|right]
  [--stair-width METRES]
  [--target-riser METRES] [--max-riser METRES]
  [--target-tread METRES] [--min-tread METRES]
  [--min-stair-width METRES]
  [--landing-depth METRES] [--flight-gap METRES]
  [--step-thickness METRES] [--minimum-headroom METRES]
  [--railings auto|both|none]
  [--rail-height METRES] [--rail-post-spacing METRES]
  [--rail-post-size METRES] [--handrail-size METRES] [--midrail-size METRES]
  [--debug-geometry]
  [--wall-thickness METRES]
  [--shaft-wall-bottom Y --shaft-wall-top Y]
  [--no-shaft]
  [--lower-entry-side north|east|south|west]
  [--upper-exit-side auto|north|east|south|west]
  [--project-root DIR | --resource-dir res://PATH]
  [--scene-name NAME] [--collision-layer MASK]
  [--include-preview]
  [--origin center|south-west] [--invert-z]
```

- `0`: the scene, materials, and success report were generated and verified as non-empty.
- `2`: parameters, geometry, path mapping, or output creation failed. A failure report is written when the output directory is writable.

`--project-root` must contain `project.godot`, and `output_dir` must be inside it. Use `--resource-dir` when the output location is staged elsewhere but its eventual Godot `res://` path is already known.

## 2. Layouts and selection

- `straight`: one flight; lower and upper exits are opposite.
- `l-turn`: two flights and a corner landing; upper exit is adjacent. `--turn-direction` chooses left or right.
- `u-turn`: two parallel, opposing flights and a half-turn landing; both exits are on the same shaft side.
- `perimeter`: three or four flights with square corner landings along the shaft boundary. Use `--perimeter-flights` to force a count.
- `auto`: test the layouts in this order: straight, L-turn, U-turn, perimeter with three flights, perimeter with four flights. Select the first valid result.

`--lower-entry-side` rotates the complete layout. `--upper-exit-side` constrains selection; `auto` accepts the natural exit of the selected form. `--invert-z` mirrors the layout while keeping the requested lower-entry side fixed; handed layouts therefore swap their world-space turn direction.

The generator selects an integer riser count so that actual riser height is close to the target and never exceeds the maximum. Risers are distributed as evenly as possible across flights. A shared tread depth is reduced from the target only as far as the configured minimum. The tool fails instead of violating a minimum.

## 3. Architectural defaults

| Setting | Default | Meaning |
|---|---:|---|
| `target_riser` | `0.17` | preferred riser height |
| `max_riser` | `0.19` | largest permitted riser |
| `target_tread` | `0.28` | preferred tread depth |
| `min_tread` | `0.25` | smallest permitted tread |
| `min_stair_width` | `0.90` | smallest permitted flight width |
| `flight_gap` | `0.10` | gap between parallel U-turn flights |
| `step_thickness` | `0.15` | landing slab and step support thickness |
| `minimum_headroom` | `2.0` | declared playability limit |
| `wall_thickness` | `0.18` | optional shaft-wall thickness |

When `--stair-width` is absent, the widest practical value up to `1.20 m` is selected without violating the minimum. Landing depth defaults to stair width and may not be smaller. Perimeter corner landings always use stair width.

## 4. Railings and shaft walls

Railing modes:

- `auto`: generate guards on all open flight and landing edges. Omit a guard only when a generated shaft wall covers the complete edge from its lowest walking surface through the required guard height. A shaft boundary alone is not proof that a wall exists.
- `both`: generate both flight-side railings and guards on every exposed landing edge.
- `none`: create no railing geometry or collisions.

Default railing geometry is a top handrail, one midrail, and vertical posts. The generator creates a graph of rail axes, explicit oriented ports, support planes, and named connections. A deterministic topology pass resolves nearby planned endpoints with the ordinary half-section tolerance before any mesh exists. A remaining gap between a sloped flight port and a perpendicular landing rail becomes an explicit short horizontal member with a standard slope-to-flat joint at the flight and a flat-to-flat joint at the landing; port tolerance is never enlarged to hide it. The geometry core then extends each connected beam only to the far boundary of the other connector volume. The same algorithm handles straight, corner, sloped-to-horizontal, and terminal post joints. A separate deterministic surface-union pass removes only face fragments inside directly connected volumes; it does not search the emitted scene or change ports, transforms, axes, or extents. A spatial turn is expanded into two planar slope-to-flat adapters at one shared elevation. On a perimeter landing they form the continuous path `slope -> flat -> flat -> slope`, using two ordinary slope-to-flat joints and one ordinary horizontal corner. An L-turn keeps its full-section socketed corner newel. Sloped rails never connect directly to each other. A narrow U-turn guard is a five-member path: slope, tangent, bridge, tangent, slope. A support port is placed at the lowest handrail-underside height over all four corners of an intermediate post footprint, preventing any post corner from penetrating a sloped rail; a full-section block then forms separate post-to-block and block-to-rail contacts. At a terminal, the sloped rail joins a short horizontal adapter through the ordinary slope-to-flat rule, and the post supports that adapter from below. Equal `rail-post-size` and `handrail-size` are required. Interior flight posts retain tread-centre anchors, while endpoint posts at already-connected nodes are suppressed. No legacy positioning mode exists.

`--debug-geometry` adds a separate top-level `Debug` branch with non-colliding `Ports`, `Directions`, `Boundaries`, and `Connections` children. The complete branch can be hidden or deleted without touching generated stairs or railings.

Shaft walls are opt-in:

- omit both wall-bound flags: generate no walls;
- provide both flags: generate only the vertical band from bottom to top;
- provide only one: fail;
- use `--no-shaft`: generate no walls regardless of supplied bounds.

All four shaft sides are generated only inside the explicit vertical interval from `--shaft-wall-bottom` to `--shaft-wall-top`. This closes the inter-floor gap while leaving the lower entry open below the interval and the upper exit open above it. Shaft width and length describe the clear playable opening; wall thickness is placed outward from that boundary. Walls therefore touch the shaft boundary without consuming stair or railing volume. A measured wall-clearance validation rejects any positive-volume intersection. The generator does not create lower- or upper-floor walls, door openings, floor slabs, or ceilings.

A wall protects a landing edge only when that edge itself reaches the matching shaft boundary. A wall separated from the landing by a horizontal gap never suppresses its guard, even when the wall spans the full guard height.

## 5. Coordinates and output

- Godot X: shaft width.
- Godot Y: vertical height.
- Godot Z: shaft length.
- `center`: shaft centre at `(0, 0, 0)` with the lower floor at `Y=0`.
- `south-west`: shift the footprint so its minimum X and Z lie at zero.
- `invert-z`: reflect the result across the X axis.

Scene structure:

```text
GeneratedStair
|- Stairs
|  |- Visual (one MeshInstance3D per step)
|  `- Collision (one StaticBody3D, matching CollisionShape3D children)
|- Landings
|  |- Visual
|  `- Collision
|- Railings
|  |- Visual
|  `- Collision
`- Shaft
   |- Visual
   `- Collision
```

The scene uses reusable `BoxMesh` and `BoxShape3D` subresources for ordinary parts. Connected railing members additionally reference flat-shaded OBJ surfaces under `joint_meshes/`; their collisions remain the original overlapping `BoxShape3D` volumes, which exactly decompose the same union. Each step has an independent horizontal tread slab of `step_thickness` plus a vertical riser panel spanning exactly from the preceding tread level to the current tread level. The riser closes visible and physical gaps, while neither component extends down to the flight base; repeated floor modules therefore retain usable clearance underneath. Visual and collision transforms match. Collision shapes are consolidated under one static body per category.

Files:

- `<scene-name>.tscn`
- `stair_material.tres`
- `railing_material.tres`
- `shaft_material.tres`
- `joint_meshes/*.obj` when connected railing surfaces require union finishing
- `generation_report.json`

Material resources are created only when absent and are preserved on subsequent runs.

## 6. Report and validation

Important report fields:

- `status`: `ok` or `error`.
- `settings`: effective CLI settings.
- `selected_layout`: type, turn, actual dimensions, entry/exit sides, shaft-wall status, and per-flight data.
- `counts`: flights, risers, visuals, collision shapes, and category counts.
  - `tread_nodes` and `riser_nodes` must both equal architectural `risers`.
  - `railing_posts` counts unique posts declared by the abstract graph.

- `geometry_validation`: graph-level position/orientation errors with connection and element IDs, distances, and angular errors.
- `geometry_validation.compiled`: positive dimensions and unplanned-intersection checks after deterministic compilation.
- `geometry_validation.shaft`: wall count, checked element count, maximum penetration, and identified wall/element intersections.
- `schema_id`, `schema_version`, and `contract_version`: the machine-readable report and anchor contract versions. Version `1.1.0` is additive over legacy reports; older fields retain their meaning.
- `anchor_coordinate_space`: exact local metres/XZ/+Y space together with `origin_mode` and `invert_z`.
- `anchor_frames`: stable T01 semantic frames derived from `Layout`, flights, landings, and explicit shaft-wall bounds before Godot nodes are emitted. IDs use the sanitized scene name plus semantic role and never use array order from emitted nodes, AABBs, coordinates, or geometry hashes.
  - `stair_entry/lower_entry`: centre of the lower transition line, facing from the lower floor into the first flight.
  - `stair_exit/upper_exit`: centre of the upper transition line, facing from the last flight onto the upper floor.
  - `point/flight_passage`: one semantic passage frame per declared flight.
  - `floor/landing`: the exact declared landing polygon and elevation.
  - `shaft/clear_volume`: the clear shaft bounds from input dimensions.
  - `wall/shaft_face`: one physical face for each explicitly generated shaft wall; no face anchor is published when shaft walls are omitted.
- Every frame includes `origin`, normalized `forward`, `normal`, `up`, deterministic `geometry_hash`, exact `bounds`, and `placement_limits`. For vertical and point frames, `forward × up = normal`; landing frames use `normal = up`.
- Rotation, `--lower-entry-side`, `--upper-exit-side`, `--origin`, and `--invert-z` change frame geometry but do not change IDs for the same `--scene-name`. Incomplete shaft bounds still fail before any success report; a missing face is never inferred.
- The normative JSON Schema is [generation-report.schema.json](generation-report.schema.json).
- Core tests cover rigid connection, translation, rotation, chains, closed loops, topology resolution, invalid crossings, boundary cases, all supported layouts, post verticality, and shaft-wall opt-in behavior.
- `materials.created` / `materials.preserved`: regeneration ownership evidence.
- `assumptions`: automatic decisions such as auto-selection.
- `warnings`: non-fatal limitations.
- `errors`: blocking failures.
- `solutions`: concrete parameter changes for a failed request.
- `layout_failures_before_selection`: rejected layouts examined before auto-selection.
- `files`: required output files.

Successful deterministic validation requires exit code `0`, `status == "ok"`, no errors, and all files present and non-empty. Godot headless loading is a separate validation layer. Neither check establishes local building-code compliance.
