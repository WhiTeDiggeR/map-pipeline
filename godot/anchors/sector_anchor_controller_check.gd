extends Node

const CONTROLLER_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/sector_anchor_controller.gd")
const REGISTRY_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/anchor_registry.gd")
const ANCHORED_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/anchored_object_3d.gd")


func _ready() -> void:
	var errors := PackedStringArray()
	var sector := Node3D.new()
	sector.name = "Sector"
	sector.set_meta("sector_id", "FIXTURE")
	add_child(sector)
	var authored := Node3D.new()
	authored.name = "AuthoredContent"
	sector.add_child(authored)
	var registry := REGISTRY_SCRIPT.new() as MapAnchorRegistry
	registry.name = "AnchorRegistry"
	registry.load_on_ready = false
	sector.add_child(registry)
	var controller := CONTROLLER_SCRIPT.new() as MapSectorAnchorController
	controller.name = "AnchorController"
	controller.apply_on_ready = false
	controller.authored_scene_path = "res://fixture_authored.tscn"
	sector.add_child(controller)

	var wall := _anchored("OBJ-WALL", Vector3(9.0, 9.0, 9.0))
	wall.author_correction = Transform3D(Basis.IDENTITY, Vector3(0.0, 0.25, 0.0))
	authored.add_child(wall)
	var door := _anchored("OBJ-DOOR", Vector3(-9.0, -9.0, -9.0))
	authored.add_child(door)
	var free_object := _anchored("OBJ-FREE", Vector3(7.0, 0.0, 7.0))
	authored.add_child(free_object)

	var baseline_free := free_object.global_transform
	errors.append_array(controller.apply_documents(_anchors(0.0, true), _bindings()))
	_expect(errors, wall.global_position.is_equal_approx(Vector3(2.0, 1.25, 0.0)), "wall object follows anchor and preserves author correction")
	_expect(errors, door.global_position.is_equal_approx(Vector3(4.0, 0.0, 3.0)), "door object follows door center")
	_expect(errors, free_object.global_transform.is_equal_approx(baseline_free), "unbound authored object remains unchanged")

	var moved_errors := controller.apply_documents(_anchors(5.0, true), _bindings())
	errors.append_array(moved_errors)
	_expect(errors, wall.global_position.is_equal_approx(Vector3(7.0, 1.25, 0.0)), "wall move updates bound object")
	_expect(errors, door.global_position.is_equal_approx(Vector3(9.0, 0.0, 3.0)), "door move updates bound frame")
	_expect(errors, wall.author_correction.origin.is_equal_approx(Vector3(0.0, 0.25, 0.0)), "author correction survives regeneration")

	var before_missing := wall.global_transform
	var missing_errors := controller.apply_documents(_anchors(12.0, false), _bindings())
	_expect(errors, not missing_errors.is_empty(), "removed anchor creates a blocking error")
	_expect(errors, _contains(missing_errors, "missing anchor_id"), "removed anchor reports the stable missing ID")
	_expect(errors, wall.global_transform.is_equal_approx(before_missing), "failed transaction does not move the object")
	_expect(errors, not controller.is_ready_for_use(), "failed transaction blocks readiness")

	var duplicate_bindings := _bindings()
	(duplicate_bindings["bindings"] as Array).append((duplicate_bindings["bindings"] as Array)[0].duplicate(true))
	var duplicate_errors := controller.apply_documents(_anchors(5.0, true), duplicate_bindings)
	_expect(errors, _contains(duplicate_errors, "duplicate binding_id") or _contains(duplicate_errors, "two bindings target object_id"), "duplicate binding is blocking")

	var malformed_bindings := _bindings()
	((malformed_bindings["bindings"] as Array)[0] as Dictionary)["object_ref"] = []
	var malformed_errors := controller.apply_documents(_anchors(5.0, true), malformed_bindings)
	_expect(errors, _contains(malformed_errors, "object_ref must be an object"), "malformed binding blocks without a runtime error")

	if errors.is_empty():
		print("MAP_SECTOR_ANCHOR_CONTROLLER_OK")
		get_tree().quit(0)
	else:
		for error: String in errors:
			push_error(error)
		get_tree().quit(1)


func _anchored(object_id: String, position: Vector3) -> AnchoredObject3D:
	var object := ANCHORED_SCRIPT.new() as AnchoredObject3D
	object.name = object_id
	object.object_id = object_id
	object.apply_on_ready = false
	object.position = position
	return object


func _anchors(x_offset: float, include_wall: bool) -> Dictionary:
	var anchors: Array[Dictionary] = []
	if include_wall:
		anchors.append(_frame("AF-WALL", "wall", Vector3(x_offset, 0.0, 0.0), 4.0))
	anchors.append(_frame("AF-DOOR", "door", Vector3(x_offset + 3.0, 0.0, 3.0), 2.0))
	return {
		"schema_id": "caretaker.anchor_frames",
		"schema_version": "1.0.0",
		"map_id": "fixture-map",
		"sector_id": "FIXTURE",
		"generation_id": "fixture:%s:%s" % [x_offset, include_wall],
		"coordinate_space": {"units": "m", "horizontal_plane": "XZ", "up_axis": "+Y", "space": "world"},
		"anchors": anchors,
	}


func _frame(anchor_id: String, anchor_type: String, origin: Vector3, length_m: float) -> Dictionary:
	return {
		"anchor_id": anchor_id,
		"type": anchor_type,
		"status": "active",
		"origin": [origin.x, origin.y, origin.z],
		"forward": [1.0, 0.0, 0.0],
		"normal": [0.0, 0.0, 1.0],
		"up": [0.0, 1.0, 0.0],
		"bounds": {"along_range_m": [0.0, length_m], "length_m": length_m, "height_m": 3.0},
		"placement_limits": {
			"normal_offset_m": [0.0, 1.0],
			"height_m": [0.0, 3.0],
			"rotation_deg": {"yaw": [-180.0, 180.0], "pitch": [0.0, 0.0], "roll": [0.0, 0.0]},
		},
	}


func _bindings() -> Dictionary:
	return {
		"schema_id": "caretaker.object_bindings",
		"schema_version": "1.0.0",
		"map_id": "fixture-map",
		"sector_id": "FIXTURE",
		"bindings": [
			_binding("BIND-WALL", "OBJ-WALL", "AF-WALL", "wall", "from_start_m", 2.0, 1.0),
			_binding("BIND-DOOR", "OBJ-DOOR", "AF-DOOR", "door", "centered", 0.0, 0.0),
		],
	}


func _binding(binding_id: String, object_id: String, anchor_id: String, anchor_type: String, policy: String, distance: float, height: float) -> Dictionary:
	var along := {"policy": policy}
	if policy == "centered":
		along["offset_m"] = distance
	else:
		along["distance_m"] = distance
	return {
		"binding_id": binding_id,
		"object_ref": {"object_id": object_id, "scene": "res://fixture_authored.tscn"},
		"anchor_ref": {"anchor_id": anchor_id, "expected_type": anchor_type},
		"placement": {
			"mode": "linear",
			"linear": {"along": along},
			"normal_offset_m": 0.0,
			"height_m": height,
			"rotation": {"representation": "euler_deg", "order": "YXZ", "yaw_pitch_roll": [0.0, 0.0, 0.0]},
		},
		"footprint_m": [0.2, 0.2, 0.2],
		"footprint_center_m": [0.0, 0.1, 0.0],
		"on_missing_anchor": "block",
	}


func _contains(errors: PackedStringArray, fragment: String) -> bool:
	for error: String in errors:
		if error.contains(fragment):
			return true
	return false


func _expect(errors: PackedStringArray, condition: bool, message: String) -> void:
	if not condition:
		errors.append(message)
