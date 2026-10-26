# Map pipeline regeneration editor

The dock is a thin Godot 4.7 editor client for `pipeline/safe_regenerate.py` of map-pipeline. It does not implement geometry generation in GDScript.

The Russian-language dock is ordered as the work happens:

1. **Сектор** — sector id and source plan file; if the scene is not a sector scene a hint says which scene to open.
2. **Работа с планом** — `1. Открыть план в Inkscape`, `2. Проверить сектор` (`--validate-only`), `3. Пересоздать сектор`.
3. **Результат** — coloured status (`✔` clean, `✖` blocked/failed), problem count, human-readable stage, exit code, a selectable read-only list of problems with `Копировать ошибку` (copies the status and details to the clipboard), `Показать отчёт` and, when offered, `Исправить агентом`.
4. **Привязка объектов** (collapsed) — `Привязать двери по ID` plus manual Bind/Rebind/Unbind with an explicit anchor ID.
5. **Инструменты** — generator readiness (`Ready` only with `svg_to_godot3d >= 1.19.0` and `generate_godot_stairs >= 2.9.0`), the resolved Inkscape and the selected Agent Fix agent (Claude, Codex or custom) with the `Выбрать агентом Claude` button, which writes the Claude Code launcher and hides itself once Claude is selected.
6. **Настройки** (collapsed) — every field has a one-line explanation.

- `Пересоздать сектор` blocks while the active scene UndoRedo version differs from its last saved version.
- Sector identity comes from root metadata `sector_id`, or one unambiguous manifest `output_resource_dir` match. Conflicts block.
- `Открыть план в Inkscape` starts Inkscape on the source SVG. The executable is resolved from the `inkscape_executable` setting, then `MAP_PIPELINE_INKSCAPE`, then the standard install locations and the Microsoft Store package (found through `Get-AppxPackage`). The dock shows where it was found; if nothing is found it asks for the path. No browser fallback is used.
- A successful regeneration waits for `EditorFileSystem` import, replaces the loaded architecture/stairs resources, rebuilds `Generated`, reloads `AnchorRegistry`, applies authored bindings, and reloads the same saved sector scene. Failed CLI transactions never trigger a scan or scene reload.
- `Исправить агентом` is hidden for clean/warning-only reports and for source semantics, toolchain/version, stair, resource, or generated-geometry failures. It is visible only when composition validation produced a current repair queue whose every open blocker belongs to authored content/bindings and has an allowlisted code.
- `Привязать двери по ID` uses `MapDoorBindingBuilder` (`godot/anchors/door_binding_builder.gd`), writes the sector's bindings file and applies it immediately. Manual Bind/Rebind delegate to the T07 `MapAnchorEditorOperations`, preserving Undo/Redo and the no-guessing rules.

Tool paths are persistent `EditorSettings`, configured once in the dock's Settings
section:

- `map_pipeline/python_executable`
- `map_pipeline/svg_tool_root`
- `map_pipeline/stair_tool_root`
- `map_pipeline/agent_launcher`
- `map_pipeline/inkscape_executable`

Each value resolves in this order: EditorSettings, then its `MAP_PIPELINE_*` environment
variable. There is no default skill directory: the tool roots must point at the
skills the user's agent actually has installed, and the panel stays blocked with a
request for the missing path until they are set. The panel shows detected
versions and `Ready` only for `svg_to_godot3d >= 1.19.0` and
`generate_godot_stairs >= 2.9.0`; missing or incompatible tools block before the
CLI can create staging. Reports are written under
`user://map_pipeline_reports/` and can be opened with `Show Report`.

Headless fixture check:

```powershell
godot --headless --path . --script res://addons/map_pipeline/godot/regeneration_editor/regeneration_editor_check.gd
```

For manual editor workflow checks, open `fixtures/fixture_scene.tscn`. Its root carries `sector_id`; the fixture manifest, source SVG, composition input, and clean machine report are colocated. The fixture manifest is for editor behavior tests, not production generation.

When enabled, the plugin narrows the 3D editor depth range to near 0.1 / far 300 (the player camera uses the same values) to reduce z-fighting. It only replaces the factory defaults `editors/3d/default_z_near = 0.05` and `default_z_far = 4000`; values you changed are kept. These are global editor settings, so they also apply to other projects opened with the same Godot.

The project names its manifest in the project setting `map_pipeline/sector_manifest`. Without settings, the generators bundled with map-pipeline (`generators/`) are used.
