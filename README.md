# map-pipeline

Tools for turning annotated SVG floor plans into Godot 4 scenes, independent of any one game project.
A project uses it as a git submodule at `addons/map_pipeline` (Godot finds the editor plugins there; `generators/`, `pipeline/`
and `docs/` carry `.gdignore`, so Godot does not import them).

| Path | What it is |
|---|---|
| `generators/svg-plan-to-godot` | svg_to_godot3d 1.19.0 — inspects and converts annotated SVG/HTML plans into Godot 4 blockout scenes (walls, floors, ceilings, openings, collisions, anchor frames) |
| `generators/generate-godot-stairs` | generate_godot_stairs 2.9.0 — straight, L-turn, U-turn and perimeter stair scenes from shaft size and floor height |
| `pipeline/` | regeneration of one map sector from a project manifest: `regenerate_sector.py` (backend), `safe_regenerate.py` (staging, validation, atomic promotion), `manifest_contract.py`, `vertical_resolver.py` (stairs from `data-vertical-*` markup), `vertical_closures.py` (shaft lining and exit thresholds), `room_finishes.py` (materials by room class), `resolve_bindings.py`, `composition_validator/`, `repair_package/` |
| `docs/vertical-markup.md` | markup contract for stairs and shafts in the plans |

## Project manifest

`safe_regenerate.py --manifest <project manifest> --sector <ID> --report <file>` regenerates one sector. Everything project-specific
is in the manifest: sectors (source SVG, metric settings, output `res://` directory, stair generators), `map_id`, optional
`finish_registry` (project-relative path of the room finish registry) and optional `production_rules`
(`sector_count`, `source_contains`, `output_prefix`). Schema identifiers keep their original `caretaker.*` names for
compatibility with existing packages.

## Tests

```
SVG_TOOL_ROOT=generators/svg-plan-to-godot STAIR_TOOL_ROOT=generators/generate-godot-stairs python -m unittest discover -s pipeline/tests
python -m unittest discover -s pipeline/composition_validator/tests
python -m unittest discover -s pipeline/repair_package/tests
python -m unittest discover -s generators/generate-godot-stairs/tests
```

Godot editor plugins and the anchor runtime follow (caretaker issue WhiTeDiggeR/caretaker#244).
