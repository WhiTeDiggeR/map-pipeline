# map-pipeline

Tools for turning annotated SVG floor plans into Godot 4 scenes, independent of any one game project.

| Path | What it is |
|---|---|
| `generators/svg-plan-to-godot` | svg_to_godot3d 1.19.0 — inspects and converts annotated SVG/HTML plans into Godot 4 blockout scenes (walls, floors, ceilings, openings, collisions, anchor frames) |
| `generators/generate-godot-stairs` | generate_godot_stairs 2.9.0 — straight, L-turn, U-turn and perimeter stair scenes from shaft size and floor height |

Each generator folder is a self-contained skill: `SKILL.md`, `references/` (tool contract, report schemas), `scripts/` and, for stairs, `tests/`. Python 3.10+, no third-party packages.

Planned next (caretaker issue WhiTeDiggeR/caretaker#244): Godot editor plugins (regeneration editor, anchor editor), anchor runtime, and the generic regeneration pipeline (safe sector regeneration, composition validation, binding resolution, stair resolver, shaft closures). Projects consume this repository as a git submodule.
