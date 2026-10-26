@tool
extends Node
class_name MapSectorAnchorController

signal readiness_changed(ready: bool, errors: PackedStringArray)

const BINDINGS_SCHEMA_ID := "caretaker.object_bindings"
const SUPPORTED_SCHEMA_VERSION := "1.0.0"
const ALLOWED_ANCHOR_TYPES := ["point", "wall", "door", "floor", "ceiling", "shaft", "stair_entry", "stair_exit"]

@export_file("*.json") var anchor_frames_path := ""
@export_file("*.json") var object_bindings_path := ""
@export_file("*.tscn") var authored_scene_path := ""
@export_node_path("Node") var anchor_registry_path := NodePath("../AnchorRegistry")
@export_node_path("Node3D") var authored_content_path := NodePath("../AuthoredContent")
@export var apply_on_ready := false

var _last_errors := PackedStringArray()
var _ready_state := false


func _ready() -> void:
	if apply_on_ready:
		call_deferred("_apply_on_ready")


func _apply_on_ready() -> void:
	var errors := apply_bindings()
	for error: String in errors:
		push_error(error)


func apply_bindings() -> PackedStringArray:
	var errors := PackedStringArray()
	var anchor_document := _load_json_document(anchor_frames_path, "anchor_frames", errors)
	var bindings_document := _load_json_document(object_bindings_path, "object_bindings", errors)
	if not errors.is_empty():
		return _finish(false, errors)
	return apply_documents(anchor_document, bindings_document)


func apply_documents(anchor_document: Dictionary, bindings_document: Dictionary) -> PackedStringArray:
	var errors := _validate_binding_document(anchor_document, bindings_document)
	var registry := get_node_or_null(anchor_registry_path) as MapAnchorRegistry
	var authored_root := get_node_or_null(authored_content_path) as Node3D
	if registry == null:
		errors.append("AnchorRegistry node is missing")
	if authored_root == null:
		errors.append("AuthoredContent node is missing")
	if not errors.is_empty():
		return _finish(false, errors)

	errors.append_array(registry.load_anchor_document(anchor_document))
	if not errors.is_empty():
		return _finish(false, errors)

	var objects: Dictionary = {}
	_index_anchored_objects(authored_root, objects, errors)
	var binding_ids: Dictionary = {}
	var bound_object_ids: Dictionary = {}
	var plans: Array[Dictionary] = []
	for binding_value: Variant in bindings_document["bindings"]:
		if not binding_value is Dictionary:
			errors.append("binding must be an object")
			continue
		var binding := binding_value as Dictionary
		var binding_id := str(binding.get("binding_id", ""))
		if binding_id.is_empty():
			errors.append("binding has no binding_id")
		elif binding_ids.has(binding_id):
			errors.append("duplicate binding_id: %s" % binding_id)
		else:
			binding_ids[binding_id] = true
		var object_ref_value: Variant = binding.get("object_ref")
		if not object_ref_value is Dictionary:
			errors.append("%s object_ref must be an object" % binding_id)
			continue
		var object_ref := object_ref_value as Dictionary
		var object_id := str(object_ref.get("object_id", ""))
		if object_id.is_empty():
			errors.append("%s has no object_ref.object_id" % binding_id)
			continue
		if bound_object_ids.has(object_id):
			errors.append("two bindings target object_id: %s" % object_id)
			continue
		bound_object_ids[object_id] = true
		if authored_scene_path.is_empty() or str(object_ref.get("scene", "")) != authored_scene_path:
			errors.append("%s object_ref.scene does not match the sector authored scene" % binding_id)
			continue
		if not objects.has(object_id):
			errors.append("binding object was not found: %s" % object_id)
			continue
		var object := objects[object_id] as AnchoredObject3D
		var anchor_ref_value: Variant = binding.get("anchor_ref")
		if not anchor_ref_value is Dictionary:
			errors.append("%s anchor_ref must be an object" % binding_id)
			continue
		var anchor_ref := anchor_ref_value as Dictionary
		var anchor_id := str(anchor_ref.get("anchor_id", ""))
		var expected_type := str(anchor_ref.get("expected_type", ""))
		if anchor_id.is_empty():
			errors.append("%s has no anchor_ref.anchor_id" % binding_id)
			continue
		if expected_type not in ALLOWED_ANCHOR_TYPES:
			errors.append("%s has invalid expected anchor type: %s" % [binding_id, expected_type])
			continue
		if str(binding.get("on_missing_anchor", "")) != "block":
			errors.append("%s must use on_missing_anchor=block" % binding_id)
			continue
		var frame := registry.get_anchor_frame(anchor_id)
		if frame.is_empty():
			errors.append("%s: missing anchor_id: %s" % [object_id, anchor_id])
			continue
		if str(frame.get("type", "")) != expected_type:
			errors.append("%s: anchor %s has type %s, expected %s" % [object_id, anchor_id, str(frame.get("type", "")), expected_type])
			continue
		var placement_result := _placement_from_binding(binding)
		if not bool(placement_result.get("ok", false)):
			errors.append("%s: %s" % [object_id, str(placement_result.get("error", "invalid placement"))])
			continue
		var placement := placement_result["placement"] as MapAnchorPlacement
		var correction_result := _author_correction_from_binding(binding, object.author_correction)
		if not bool(correction_result.get("ok", false)):
			errors.append("%s: %s" % [object_id, str(correction_result.get("error", "invalid author_correction"))])
			continue
		var correction := correction_result["transform"] as Transform3D
		var resolved := registry.resolve_transform(anchor_id, expected_type, placement, correction)
		if not bool(resolved.get("ok", false)):
			var resolution_errors := resolved.get("errors", PackedStringArray(["anchor resolution failed"])) as PackedStringArray
			for resolution_error: String in resolution_errors:
				errors.append("%s: %s" % [object_id, resolution_error])
			continue
		plans.append({
			"object": object,
			"object_id": object_id,
			"anchor_id": anchor_id,
			"expected_type": expected_type,
			"placement": placement,
			"author_correction": correction,
		})

	if not errors.is_empty():
		return _finish(false, errors)

	# Commit only after every binding has resolved. This prevents a failed binding
	# from leaving the open scene in a partially updated state.
	for object_id: String in objects:
		if not bound_object_ids.has(object_id):
			var unbound := objects[object_id] as AnchoredObject3D
			if unbound.anchor_registry == registry:
				unbound.apply_editor_binding_state({
					"object_id": object_id,
					"anchor_id": "",
					"placement": null,
					"author_correction": unbound.author_correction,
					"anchor_registry": null,
					"resolve": false,
					"world_transform": unbound.global_transform,
				})
	for plan: Dictionary in plans:
		var anchored := plan["object"] as AnchoredObject3D
		anchored.apply_editor_binding_state({
			"object_id": plan["object_id"],
			"anchor_id": plan["anchor_id"],
			"expected_anchor_type": plan["expected_type"],
			"placement": plan["placement"],
			"author_correction": plan["author_correction"],
			"anchor_registry": registry,
			"apply_on_ready": false,
			"resolve": true,
		})
		errors.append_array(anchored.get_anchor_errors())
	if errors.is_empty():
		errors.append_array(registry.refresh_registered_objects())
	return _finish(errors.is_empty(), errors)


func refresh_after_generation() -> PackedStringArray:
	return apply_bindings()


func is_ready_for_use() -> bool:
	return _ready_state


func get_last_errors() -> PackedStringArray:
	return _last_errors.duplicate()


func _load_json_document(path: String, label: String, errors: PackedStringArray) -> Dictionary:
	if path.is_empty() or not FileAccess.file_exists(path):
		errors.append("%s file is missing: %s" % [label, path])
		return {}
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		errors.append("cannot open %s: %s" % [label, path])
		return {}
	var parsed: Variant = JSON.parse_string(file.get_as_text())
	if not parsed is Dictionary:
		errors.append("%s root must be an object" % label)
		return {}
	return parsed as Dictionary


func _validate_binding_document(anchor_document: Dictionary, document: Dictionary) -> PackedStringArray:
	var errors := PackedStringArray()
	if str(document.get("schema_id", "")) != BINDINGS_SCHEMA_ID or str(document.get("schema_version", "")) != SUPPORTED_SCHEMA_VERSION:
		errors.append("unsupported object_bindings schema")
	if not document.get("bindings") is Array:
		errors.append("object_bindings.bindings must be an array")
	if str(document.get("map_id", "")) != str(anchor_document.get("map_id", "")):
		errors.append("object_bindings map_id does not match anchor_frames")
	if str(document.get("sector_id", "")) != str(anchor_document.get("sector_id", "")):
		errors.append("object_bindings sector_id does not match anchor_frames")
	var sector_id := str(get_parent().get_meta("sector_id", "")) if get_parent() != null else ""
	if not sector_id.is_empty() and sector_id != str(document.get("sector_id", "")):
		errors.append("open sector metadata does not match object_bindings")
	return errors


func _index_anchored_objects(node: Node, objects: Dictionary, errors: PackedStringArray) -> void:
	for child: Node in node.get_children():
		if child is AnchoredObject3D:
			var anchored := child as AnchoredObject3D
			if anchored.object_id.is_empty():
				errors.append("AnchoredObject3D has no object_id: %s" % anchored.get_path())
			elif objects.has(anchored.object_id):
				errors.append("duplicate object_id: %s" % anchored.object_id)
			else:
				objects[anchored.object_id] = anchored
		_index_anchored_objects(child, objects, errors)


func _placement_from_binding(binding: Dictionary) -> Dictionary:
	var raw_value: Variant = binding.get("placement")
	if not raw_value is Dictionary:
		return {"ok": false, "error": "placement must be an object"}
	var raw := raw_value as Dictionary
	var placement := MapAnchorPlacement.new()
	placement.mode = str(raw.get("mode", ""))
	if placement.mode == "linear":
		var linear_value: Variant = raw.get("linear")
		if not linear_value is Dictionary or not (linear_value as Dictionary).get("along") is Dictionary:
			return {"ok": false, "error": "linear placement requires linear.along objects"}
		var along := (linear_value as Dictionary)["along"] as Dictionary
		var axis_error := _configure_axis(placement, along)
		if not axis_error.is_empty():
			return {"ok": false, "error": axis_error}
	elif placement.mode == "surface":
		var surface_value: Variant = raw.get("surface")
		if not surface_value is Dictionary:
			return {"ok": false, "error": "surface placement requires a surface object"}
		var surface := surface_value as Dictionary
		if not surface.get("u") is Dictionary or not surface.get("v") is Dictionary:
			return {"ok": false, "error": "surface placement requires surface.u and surface.v objects"}
		placement.surface_u = MapAnchorAxisPlacement.new()
		placement.surface_v = MapAnchorAxisPlacement.new()
		var u_error := _configure_axis(placement.surface_u, surface["u"] as Dictionary)
		var v_error := _configure_axis(placement.surface_v, surface["v"] as Dictionary)
		if not u_error.is_empty() or not v_error.is_empty():
			return {"ok": false, "error": u_error if not u_error.is_empty() else v_error}
	else:
		return {"ok": false, "error": "placement.mode must be linear or surface"}
	if not _is_finite_number(raw.get("normal_offset_m")) or not _is_finite_number(raw.get("height_m")):
		return {"ok": false, "error": "normal_offset_m and height_m must be finite numbers"}
	placement.normal_offset_m = float(raw["normal_offset_m"])
	placement.height_m = float(raw["height_m"])
	var rotation_value: Variant = raw.get("rotation")
	if not rotation_value is Dictionary:
		return {"ok": false, "error": "rotation must be an object"}
	var rotation := rotation_value as Dictionary
	if str(rotation.get("representation", "")) != "euler_deg" or str(rotation.get("order", "")) != "YXZ":
		return {"ok": false, "error": "rotation must use euler_deg/YXZ"}
	var angles: Variant = _vector3_from_array(rotation.get("yaw_pitch_roll"))
	var footprint: Variant = _vector3_from_array(binding.get("footprint_m"))
	var footprint_center: Variant = _vector3_from_array(binding.get("footprint_center_m", [0.0, 0.0, 0.0]))
	if angles == null or footprint == null or footprint_center == null:
		return {"ok": false, "error": "rotation and footprint values must be finite 3-vectors"}
	placement.yaw_pitch_roll_deg = angles as Vector3
	placement.footprint_m = footprint as Vector3
	placement.footprint_center_m = footprint_center as Vector3
	return {"ok": true, "placement": placement}


func _configure_axis(axis: Object, raw: Dictionary) -> String:
	var policy := str(raw.get("policy", ""))
	if policy not in MapAnchorPlacement.POLICIES:
		return "unknown placement policy: %s" % policy
	if policy == "normalized" and not _is_finite_number(raw.get("value")):
		return "normalized placement requires a finite value"
	if policy in ["from_start_m", "from_end_m"] and not _is_finite_number(raw.get("distance_m")):
		return "%s placement requires a finite distance_m" % policy
	if policy == "centered" and raw.has("offset_m") and not _is_finite_number(raw["offset_m"]):
		return "centered placement offset_m must be finite"
	axis.set("policy", policy)
	axis.set("normalized_value", float(raw.get("value", 0.5)))
	axis.set("distance_m", float(raw.get("distance_m", 0.0)))
	axis.set("centered_offset_m", float(raw.get("offset_m", 0.0)))
	return ""


func _author_correction_from_binding(binding: Dictionary, fallback: Transform3D) -> Dictionary:
	if not binding.has("author_correction"):
		return {"ok": true, "transform": fallback}
	var raw: Variant = binding["author_correction"]
	if not raw is Dictionary:
		return {"ok": false, "error": "author_correction must be an object"}
	var document := raw as Dictionary
	var origin: Variant = _vector3_from_array(document.get("origin"))
	var basis_x: Variant = _vector3_from_array(document.get("basis_x"))
	var basis_y: Variant = _vector3_from_array(document.get("basis_y"))
	var basis_z: Variant = _vector3_from_array(document.get("basis_z"))
	if origin == null or basis_x == null or basis_y == null or basis_z == null:
		return {"ok": false, "error": "author_correction requires finite origin and basis_x/basis_y/basis_z"}
	var transform := Transform3D(Basis(basis_x as Vector3, basis_y as Vector3, basis_z as Vector3), origin as Vector3)
	if not transform.is_finite() or absf(transform.basis.determinant()) <= 1.0e-6:
		return {"ok": false, "error": "author_correction must be finite and nonsingular"}
	return {"ok": true, "transform": transform}


func _vector3_from_array(value: Variant) -> Variant:
	if not value is Array or (value as Array).size() != 3:
		return null
	var raw := value as Array
	for item: Variant in raw:
		if (not item is int and not item is float) or not is_finite(float(item)):
			return null
	return Vector3(float(raw[0]), float(raw[1]), float(raw[2]))


func _is_finite_number(value: Variant) -> bool:
	return (value is int or value is float) and is_finite(float(value))


func _finish(ready: bool, errors: PackedStringArray) -> PackedStringArray:
	_ready_state = ready
	_last_errors = errors.duplicate()
	readiness_changed.emit(ready, _last_errors)
	return _last_errors.duplicate()
