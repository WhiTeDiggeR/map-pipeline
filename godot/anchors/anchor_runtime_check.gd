extends SceneTree

const REGISTRY_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/anchor_registry.gd")
const PLACEMENT_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/anchor_placement.gd")
const ANCHORED_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/anchored_object_3d.gd")
const BASELINE := "res://addons/map_pipeline/godot/anchors/fixtures/anchor_frames_baseline.json"
const MOVED := "res://addons/map_pipeline/godot/anchors/fixtures/anchor_frames_moved_rotated_resized.json"
const MISSING := "res://addons/map_pipeline/godot/anchors/fixtures/anchor_frames_missing.json"
const DUPLICATE := "res://addons/map_pipeline/godot/anchors/fixtures/anchor_frames_duplicate.json"
const ANCHOR_ID := "AF-FIXTURE-WALL-MAIN"
const EPS := 0.00001


func _initialize() -> void:
	var errors := PackedStringArray()
	var registry: Variant = REGISTRY_SCRIPT.new()
	registry.load_on_ready = false
	root.add_child(registry)
	await process_frame
	_append(errors, registry.load_anchor_frames(BASELINE))
	_test_policies(registry, errors)
	_test_limits_and_rotation(registry, errors)
	await _test_follow_and_fail_closed(registry, errors)
	if errors.is_empty():
		print("MAP_ANCHOR_RUNTIME_OK policies=4 limits=blocked rotation=YXZ follow=move_rotate_resize missing=blocked duplicates=blocked")
	else:
		for error: String in errors:
			push_error(error)
	registry.queue_free()
	await process_frame
	quit(0 if errors.is_empty() else 1)


func _test_policies(registry: Variant, errors: PackedStringArray) -> void:
	var cases := [
		{"policy": "normalized", "normalized": 0.25, "expected": 2.5},
		{"policy": "from_start_m", "distance": 2.0, "expected": 2.0},
		{"policy": "from_end_m", "distance": 2.0, "expected": 8.0},
		{"policy": "centered", "offset": 1.0, "expected": 6.0},
	]
	for item: Dictionary in cases:
		var placement: Variant = PLACEMENT_SCRIPT.new()
		placement.policy = item["policy"]
		placement.normalized_value = float(item.get("normalized", 0.5))
		placement.distance_m = float(item.get("distance", 0.0))
		placement.centered_offset_m = float(item.get("offset", 0.0))
		placement.height_m = 1.0
		placement.normal_offset_m = 0.2
		var first: Dictionary = registry.resolve_transform(ANCHOR_ID, "wall", placement)
		var second: Dictionary = registry.resolve_transform(ANCHOR_ID, "wall", placement)
		if not bool(first.get("ok", false)):
			errors.append("policy %s failed: %s" % [item["policy"], first.get("errors", [])])
			continue
		var expected := Vector3(float(item["expected"]), 1.0, 0.2)
		var transform := first["transform"] as Transform3D
		if not transform.origin.is_equal_approx(expected):
			errors.append("policy %s resolved %s, expected %s" % [item["policy"], transform.origin, expected])
		if not transform.is_equal_approx(second["transform"] as Transform3D):
			errors.append("policy %s is not deterministic" % item["policy"])


func _test_limits_and_rotation(registry: Variant, errors: PackedStringArray) -> void:
	var rotated: Variant = PLACEMENT_SCRIPT.new()
	rotated.policy = "centered"
	rotated.height_m = 1.0
	rotated.yaw_pitch_roll_deg = Vector3(30.0, 10.0, -5.0)
	var first: Dictionary = registry.resolve_transform(ANCHOR_ID, "wall", rotated)
	var second: Dictionary = registry.resolve_transform(ANCHOR_ID, "wall", rotated)
	if not bool(first.get("ok", false)) or not bool(second.get("ok", false)):
		errors.append("valid YXZ rotation failed to resolve")
	elif not (first["transform"] as Transform3D).is_equal_approx(second["transform"] as Transform3D):
		errors.append("YXZ rotation is not deterministic")
	for item: Dictionary in [
		{"field": "normal_offset_m", "value": 0.6},
		{"field": "height_m", "value": 3.1},
	]:
		var placement: Variant = PLACEMENT_SCRIPT.new()
		placement.policy = "centered"
		placement.set(item["field"], item["value"])
		var result: Dictionary = registry.resolve_transform(ANCHOR_ID, "wall", placement)
		if bool(result.get("ok", false)):
			errors.append("%s outside placement limits was accepted" % item["field"])
	var invalid_rotation: Variant = PLACEMENT_SCRIPT.new()
	invalid_rotation.policy = "centered"
	invalid_rotation.yaw_pitch_roll_deg = Vector3(181.0, 0.0, 0.0)
	if bool(registry.resolve_transform(ANCHOR_ID, "wall", invalid_rotation).get("ok", false)):
		errors.append("rotation outside placement limits was accepted")


func _test_follow_and_fail_closed(registry: Variant, errors: PackedStringArray) -> void:
	var placement: Variant = PLACEMENT_SCRIPT.new()
	placement.policy = "centered"
	placement.height_m = 1.0
	placement.normal_offset_m = 0.2
	var anchored: Variant = ANCHORED_SCRIPT.new()
	anchored.object_id = "OBJ-FIXTURE-PRIMARY"
	anchored.anchor_id = ANCHOR_ID
	anchored.expected_anchor_type = "wall"
	anchored.placement = placement
	anchored.anchor_registry = registry
	anchored.author_correction = Transform3D(Basis.IDENTITY, Vector3(0.1, 0.2, 0.3))
	root.add_child(anchored)
	await process_frame
	var baseline_expected := Vector3(5.1, 1.2, 0.5)
	if not anchored.global_position.is_equal_approx(baseline_expected):
		errors.append("anchored object baseline transform is incorrect: %s" % anchored.global_position)
	_append(errors, registry.load_anchor_frames(MOVED))
	_append(errors, registry.refresh_registered_objects())
	var moved_expected := Vector3(4.5, 2.2, 17.1)
	if not anchored.global_position.is_equal_approx(moved_expected):
		errors.append("anchored object did not follow move/rotation/resize: %s" % anchored.global_position)
	var before_missing: Transform3D = anchored.global_transform
	_append(errors, registry.load_anchor_frames(MISSING))
	var missing_errors: PackedStringArray = registry.refresh_registered_objects()
	if missing_errors.is_empty() or not str(missing_errors[0]).contains("missing anchor_id"):
		errors.append("missing anchor did not produce a blocking diagnostic")
	if not anchored.global_transform.is_equal_approx(before_missing) or anchored.anchor_id != ANCHOR_ID:
		errors.append("missing anchor moved or silently rebound the object")
	_append(errors, registry.load_anchor_frames(BASELINE))
	anchored.expected_anchor_type = "door"
	var wrong_kind_transform: Transform3D = anchored.global_transform
	var wrong_kind: PackedStringArray = anchored.apply_anchor_transform()
	if wrong_kind.is_empty() or not str(wrong_kind[0]).contains("expected door"):
		errors.append("wrong-kind anchor did not produce a diagnostic")
	if not anchored.global_transform.is_equal_approx(wrong_kind_transform):
		errors.append("wrong-kind anchor changed the object transform")
	anchored.expected_anchor_type = "wall"
	var generation_before_duplicate: String = registry.get_generation_id()
	var duplicate_anchor_errors: PackedStringArray = registry.load_anchor_frames(DUPLICATE)
	if duplicate_anchor_errors.is_empty() or registry.get_generation_id() != generation_before_duplicate:
		errors.append("duplicate anchor IDs did not block registry replacement")
	var duplicate_object: Variant = ANCHORED_SCRIPT.new()
	duplicate_object.object_id = anchored.object_id
	duplicate_object.anchor_id = ANCHOR_ID
	duplicate_object.expected_anchor_type = "wall"
	duplicate_object.placement = placement
	duplicate_object.anchor_registry = registry
	root.add_child(duplicate_object)
	await process_frame
	var duplicate_object_errors: PackedStringArray = duplicate_object.get_anchor_errors()
	if duplicate_object_errors.is_empty() or not str(duplicate_object_errors[0]).contains("duplicate object_id"):
		errors.append("duplicate object IDs did not block registration")
	duplicate_object.queue_free()
	anchored.queue_free()


func _append(target: PackedStringArray, source: PackedStringArray) -> void:
	for item: String in source:
		target.append(item)
