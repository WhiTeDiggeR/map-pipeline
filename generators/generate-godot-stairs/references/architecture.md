# Deterministic generator architecture

## Data flow

```text
CLI dimensions
  -> Layout (flights, landings, exposed contours)
  -> Blueprint (elements, oriented ports, named connections, supports)
  -> topology resolution (planned near-points become one canonical node)
  -> graph validation
  -> geometry-core compilation
  -> compiled-geometry validation
  -> connected-volume surface union
  -> Godot meshes and collisions
```

Only the last stage knows about `Box`, `MeshInstance3D`, or `CollisionShape3D`.
No stage may search emitted boxes to discover a connection or repair it.
The surface-union stage consumes the explicit graph plus compiled boxes. It may
remove a face fragment only when that fragment lies inside a directly connected
neighbour. It never changes topology, ports, centres, axes, or extents.

## Core types

- `Frame`: explicit position plus orthonormal orientation.
- `Port`: named owner, frame, connector volume, and role.
- `Connection`: named collection of port IDs and an orientation rule.
- `BeamIntent` / `PostIntent`: required elements without Godot nodes.
- `Blueprint`: declarative builder for beams, supports, and joint membership.
- `connect()`: universal rigid transform aligning any moving port to a fixed port.
- `compile_model()`: converts intents to deterministic physical extents.

All vector, frame, rotation, connector-volume, and node-resolution math belongs
under `scripts/stair_core/`. High-level layout code may choose topology and
dimensions but must not reproduce that math.

Endpoint resolution retains the ordinary half-section threshold. When a flight
port and a perpendicular landing rail remain separated, the blueprint adds a
named horizontal transition with two explicit nodes: slope-to-flat at the
flight and flat-to-flat at the landing. Port tolerance is never enlarged to
hide the missing member.

## Joint rule

Every connected beam exposes its endpoint as a port. All ports in a named joint
share one canonical logical position. During compilation, each beam is extended
along its own axis until the first corner of its complete rectangular end face
reaches a receiver boundary. The largest valid extension is used. This maximizes
insertion without allowing a beam corner to cross a far face. Ordinary straight,
flat-corner, and slope-to-flat joints remain beam-only. A spatial turn is first
expanded into two planar slope-to-flat adapters at one shared elevation. On a
perimeter landing those adapters form the continuous path
`slope -> flat -> flat -> slope` through an ordinary horizontal corner. An
L-turn additionally uses its established full-section socketed corner newel;
sloped beams never attach directly to that newel. A narrow U-turn is
the same declarative idea expanded into slope, tangent, bridge, tangent, slope.
A direct spatial slope-to-slope beam connection is rejected as ambiguous instead
of producing an inferred fitting. A terminal handrail joins a short horizontal
adapter through the ordinary slope-to-flat rule, and its post supports that
adapter from below.

Posts expose a top support port. Its Y coordinate is the lowest point of the
handrail underside over all four corners of the post footprint. A deterministic
full-section connector block starts on that port and enters the supported rail.
This is compiled as post-to-block and block-to-rail, so the post itself neither
enters the rail nor leaves a support gap. The post base remains an explicit tread
or landing anchor.

## Surface union

Compiled connector volumes intentionally overlap. Before serialization, every
face polygon is deterministically subtracted by the convex volume of its graph
neighbours. Only internal fragments disappear, so the visible result is the
Boolean union of the approved connected boxes and the outer silhouette remains
unchanged. Flat-shaded OBJ meshes carry these exposed surfaces. Static collision
keeps the original overlapping convex boxes; together they describe the exact
same union without a concave approximation. This stage never adds CSG nodes and
never edits a transform or port.

## Adding a new form

1. Add or change `Layout` data: flights, landings, and exposed contour segments.
2. Describe required rail axes and semantic endpoint nodes in the blueprint.
3. Reuse `resolve_nearby_nodes`, `Blueprint`, and `compile_model` unchanged.
4. Add an integration case proving the new graph validates and compiles twice
   to identical output.
5. Do not add a mesh-name search, bounding-box inference, manual rotation, or
   post-generation coordinate correction.

## Validation and diagnostics

Graph validation reports connection IDs, owner IDs, position distance, and angle
error. Compiled validation rejects non-positive elements and intersections that
are not represented by a connection. Generation stops with exit code 2 and
concrete remedies when either layer fails.

Use `--debug-geometry` to emit a separate non-colliding `Debug` branch. Node
names and metadata identify logical ports, directions, physical beam endpoints,
and connections; deleting the branch cannot affect the generated scene.

Run all tests after changes:

```powershell
python -m unittest discover -s tests -v
```
