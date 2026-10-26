# Map Pipeline Anchors

The enabled Godot editor dock operates only on a single selected `AnchoredObject3D` and a validated `MapAnchorRegistry` in the edited scene.

- `Bind selected` assigns a persistent `OBJ-AUTO-...` ID when needed and records an authored correction so the object does not jump.
- `Rebind` changes the stable anchor ID while preserving the current world transform.
- `Unbind` clears the anchor reference and registry while preserving the current world transform.
- `Record current local correction` bakes a manual editor move into `author_correction`.
- Every mutation is one editor Undo/Redo action.

Candidate discovery is event-driven. It auto-selects only one compatible linear anchor inside the tolerance. Zero candidates block; multiple candidates are listed and require an explicit choice. Multiple registries also block unless the selected object already references one. Node paths and generated node names never become persistent IDs.

Run the fixture suite with Godot 4.7:

```powershell
godot --headless --path . --script res://addons/map_pipeline/godot/anchor_editor/editor_operations_check.gd
godot --headless --editor --path . --quit
```

This task does not add a full-regeneration button and does not edit production sector or set-dressing scenes.
