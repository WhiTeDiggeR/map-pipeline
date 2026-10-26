extends SceneTree

const REGISTRY_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/anchor_registry.gd")
const PLACEMENT_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/anchor_placement.gd")
const ANCHORED_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/anchored_object_3d.gd")
const OPERATIONS_SCRIPT := preload("res://addons/map_pipeline/godot/anchor_editor/anchor_editor_operations.gd")
const FRAMES := "res://addons/map_pipeline/godot/anchor_editor/fixtures/editor_anchor_frames.json"


func _initialize() -> void:
	var errors := PackedStringArray()
	var registry: Variant = REGISTRY_SCRIPT.new()
	registry.load_on_ready = false
	root.add_child(registry)
	await process_frame
	_append(errors, registry.load_anchor_frames(FRAMES))
	var object: Variant = ANCHORED_SCRIPT.new()
	object.expected_anchor_type = "wall"
	object.global_transform = Transform3D(Basis.IDENTITY, Vector3(5.0, 1.0, 0.05))
	root.add_child(object)
	await process_frame
	var undo_redo := UndoRedo.new()
	var operations: Variant = OPERATIONS_SCRIPT.new()
	var placement: Variant = PLACEMENT_SCRIPT.new()
	placement.policy = "centered"
	placement.height_m = 1.0
	var initial_transform: Transform3D = object.global_transform
	_append(errors, operations.bind(undo_redo, object, registry, "AF-EDITOR-WALL-A", placement))
	var persistent_id: String = object.object_id
	if not persistent_id.begins_with("OBJ-AUTO-") or object.anchor_id != "AF-EDITOR-WALL-A":
		errors.append("Bind did not create a stable object ID and binding")
	if not object.global_transform.is_equal_approx(initial_transform):
		errors.append("Bind changed the authored world transform")
	undo_redo.undo()
	if not object.object_id.is_empty() or not object.anchor_id.is_empty() or not object.global_transform.is_equal_approx(initial_transform):
		errors.append("Bind undo did not restore the original state")
	undo_redo.redo()
	if object.object_id != persistent_id or object.anchor_id != "AF-EDITOR-WALL-A":
		errors.append("Bind redo changed the persistent ID")
	var before_rebind: Transform3D = object.global_transform
	_append(errors, operations.rebind(undo_redo, object, registry, "AF-EDITOR-WALL-B", placement))
	if object.anchor_id != "AF-EDITOR-WALL-B" or not object.global_transform.is_equal_approx(before_rebind):
		errors.append("Rebind did not preserve world transform")
	undo_redo.undo()
	if object.anchor_id != "AF-EDITOR-WALL-A" or not object.global_transform.is_equal_approx(before_rebind):
		errors.append("Rebind undo failed")
	undo_redo.redo()
	var before_unbind: Transform3D = object.global_transform
	_append(errors, operations.unbind(undo_redo, object))
	if not object.anchor_id.is_empty() or object.anchor_registry != null or not object.global_transform.is_equal_approx(before_unbind):
		errors.append("Unbind did not preserve world transform")
	undo_redo.undo()
	if object.anchor_id != "AF-EDITOR-WALL-B" or not object.global_transform.is_equal_approx(before_unbind):
		errors.append("Unbind undo failed")
	undo_redo.redo()
	undo_redo.undo()
	object.global_position += Vector3(0.2, 0.1, 0.3)
	var corrected_transform: Transform3D = object.global_transform
	_append(errors, operations.record_local_correction(undo_redo, object))
	object.apply_anchor_transform()
	if not object.global_transform.is_equal_approx(corrected_transform):
		errors.append("record local correction did not preserve manual adjustment")
	var single: Array = registry.find_compatible_candidates("wall", Vector3(5.0, 1.0, 0.05), 0.1)
	var ambiguous: Array = registry.find_compatible_candidates("wall", Vector3(5.0, 1.0, 0.25), 0.3)
	if single.size() != 1 or str(single[0]["anchor_id"]) != "AF-EDITOR-WALL-A":
		errors.append("single-candidate selection is incorrect")
	if ambiguous.size() != 2:
		errors.append("ambiguous candidate case did not return an explicit list")
	if operations.single_candidate_id(single) != "AF-EDITOR-WALL-A" or not operations.single_candidate_id(ambiguous).is_empty():
		errors.append("automatic choice was not restricted to exactly one candidate")
	var wrong_kind: PackedStringArray = operations.rebind(undo_redo, object, registry, "AF-EDITOR-DOOR-A", placement)
	if wrong_kind.is_empty() or not str(wrong_kind[0]).contains("expected wall"):
		errors.append("incompatible anchor kind was not blocked")
	var duplicate: Variant = ANCHORED_SCRIPT.new()
	duplicate.object_id = persistent_id
	duplicate.expected_anchor_type = "wall"
	duplicate.anchor_id = "AF-EDITOR-WALL-A"
	duplicate.placement = placement
	duplicate.anchor_registry = registry
	root.add_child(duplicate)
	await process_frame
	if duplicate.get_anchor_errors().is_empty() or not str(duplicate.get_anchor_errors()[0]).contains("duplicate object_id"):
		errors.append("duplicate persistent object ID was not blocked")
	await _test_surface_transaction(errors)
	if errors.is_empty():
		print("MAP_EDITOR_BINDING_OK bind=rebind=unbind undo=redo candidates=single_ambiguous duplicate=blocked wrong_kind=blocked")
	else:
		for error: String in errors:
			push_error(error)
	undo_redo.clear_history()
	undo_redo.free()
	duplicate.queue_free()
	object.queue_free()
	registry.queue_free()
	await process_frame
	quit(0 if errors.is_empty() else 1)


func _append(target: PackedStringArray, source: PackedStringArray) -> void:
	for item: String in source:
		target.append(item)


func _test_surface_transaction(errors: PackedStringArray) -> void:
	var registry := REGISTRY_SCRIPT.new()
	registry.load_on_ready = false
	root.add_child(registry)
	var document := JSON.parse_string(FileAccess.get_file_as_string(FRAMES)) as Dictionary
	document["anchors"] = [{
		"anchor_id": "AF-EDITOR-FLOOR", "type": "floor", "status": "active",
		"origin": [0, 0, 0], "forward": [1, 0, 0], "normal": [0, 1, 0], "up": [0, 1, 0],
		"bounds": {"u_range_m": [0, 10], "v_range_m": [0, 10], "polygon_xz": [[0, 0], [10, 0], [10, 10], [0, 10]]},
		"placement_limits": {"normal_offset_m": [0, 0], "height_m": [0, 0], "rotation_deg": {"yaw": [0, 0], "pitch": [0, 0], "roll": [0, 0]}},
	}]
	_append(errors, registry.load_anchor_document(document))
	var object := ANCHORED_SCRIPT.new()
	object.apply_on_ready = false
	object.expected_anchor_type = "floor"
	root.add_child(object)
	await process_frame
	object.global_position = Vector3(20, 0, 20)
	var placement := PLACEMENT_SCRIPT.new()
	placement.mode = "surface"
	placement.surface_u = MapAnchorAxisPlacement.new()
	placement.surface_v = MapAnchorAxisPlacement.new()
	placement.footprint_m = Vector3.ONE
	var operations := OPERATIONS_SCRIPT.new()
	var undo_redo := UndoRedo.new()
	var version := undo_redo.get_version()
	var rejected := operations.bind(undo_redo, object, registry, "AF-EDITOR-FLOOR", placement)
	if rejected.is_empty() or undo_redo.get_version() != version or not object.object_id.is_empty() or not object.anchor_id.is_empty():
		errors.append("out-of-bounds surface bind changed object or Undo/Redo state")
	object.global_position = Vector3(5, 0, 5)
	_append(errors, operations.bind(undo_redo, object, registry, "AF-EDITOR-FLOOR", placement))
	version = undo_redo.get_version()
	var saved_correction := object.author_correction
	object.global_position = Vector3(20, 0, 20)
	rejected = operations.record_local_correction(undo_redo, object)
	if rejected.is_empty() or undo_redo.get_version() != version or not saved_correction.is_equal_approx(object.author_correction):
		errors.append("out-of-bounds correction was committed")
	if not object.global_position.is_equal_approx(Vector3(20, 0, 20)):
		errors.append("rejected correction discarded the user's unsaved manual move")
	var stable_id := object.object_id
	rejected = operations.rebind(undo_redo, object, registry, "AF-EDITOR-FLOOR", placement)
	if rejected.is_empty() or undo_redo.get_version() != version or object.object_id != stable_id:
		errors.append("out-of-bounds surface rebind was committed")
	undo_redo.clear_history()
	undo_redo.free()
	object.queue_free()
	registry.queue_free()
	await process_frame
