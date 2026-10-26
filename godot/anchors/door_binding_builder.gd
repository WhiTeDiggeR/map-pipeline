@tool
extends RefCounted
class_name MapDoorBindingBuilder

## Binds authored door objects to door anchors by an exact identifier match:
## `metadata/placement_id` on the object == `data-handoff-id` on the SVG door.
## Nothing is matched by position, name similarity or distance.

const SCHEMA_ID := "caretaker.object_bindings"
const SCHEMA_VERSION := "1.0.0"
const DOOR_TYPE := "door"
## Door frame prefabs declare the distance between their post centres at scale 1 in this root metadata.
const SPACING_META := "door_frame_post_spacing_m"
const PLACEHOLDER_FOOTPRINT := [0.1, 0.1, 0.1]
## The marker box stands on the pivot (floor) so it stays inside the door height limits.
const PLACEHOLDER_FOOTPRINT_CENTER := [0.0, 0.05, 0.0]


## Returns {"ok": bool, "bindings": Array, "errors": PackedStringArray, "skipped": Array}.
## `objects` are AnchoredObject3D instances already positioned by the author; their
## orientation is preserved, the position is seated on the door centre and the frame width
## follows the door opening (height/depth scale are kept).
static func build_bindings(
	objects: Array,
	anchor_document: Dictionary,
	source_svg_path: String,
	authored_scene_path: String,
	sector_id: String
) -> Dictionary:
	var errors := PackedStringArray()
	var skipped: Array = []
	var handoff_to_element := _door_handoff_ids(source_svg_path, errors)
	var registry := MapAnchorRegistry.new()
	errors.append_array(registry.load_anchor_document(anchor_document))
	if not errors.is_empty():
		return {"ok": false, "bindings": [], "errors": errors, "skipped": skipped}
	var bindings: Array = []
	var used_anchors := {}
	for value: Variant in objects:
		var object := value as AnchoredObject3D
		if object == null or object.expected_anchor_type != DOOR_TYPE:
			continue
		var placement_id := str(object.get_meta("placement_id", ""))
		if object.object_id.is_empty():
			errors.append("%s has no object_id" % object.name)
			continue
		if placement_id.is_empty() or not handoff_to_element.has(placement_id):
			skipped.append("%s: no SVG door with data-handoff-id %s" % [object.name, placement_id])
			continue
		var anchor_id := "svg:%s:door:center" % str(handoff_to_element[placement_id])
		if registry.get_anchor_frame(anchor_id).is_empty():
			errors.append("%s: door anchor is missing: %s" % [object.name, anchor_id])
			continue
		if used_anchors.has(anchor_id):
			errors.append("%s: anchor %s is already bound to %s" % [object.name, anchor_id, used_anchors[anchor_id]])
			continue
		used_anchors[anchor_id] = object.name
		var base := registry.resolve_transform(anchor_id, DOOR_TYPE, door_placement(), Transform3D.IDENTITY)
		if not bool(base.get("ok", false)):
			errors.append("%s: %s" % [object.name, ", ".join(base.get("errors", PackedStringArray(["anchor resolution failed"])) as PackedStringArray)])
			continue
		var base_transform := base["transform"] as Transform3D
		var current := object.global_transform if object.is_inside_tree() else object.transform
		# Keep orientation and height/depth scale; the width follows the door opening.
		var spacing := frame_post_spacing(object)
		if spacing <= 0.0:
			errors.append("%s: its prefab declares no %s" % [object.name, SPACING_META])
			continue
		var door_width := float(((registry.get_anchor_frame(anchor_id).get("bounds", {})) as Dictionary).get("width_m", 0.0))
		var scale := current.basis.get_scale()
		if door_width > 0.0:
			scale.x = door_width / spacing
		var local_basis := Basis(current.basis.orthonormalized().get_rotation_quaternion()).scaled_local(scale)
		var correction := Transform3D(base_transform.basis.inverse() * local_basis, Vector3.ZERO)
		bindings.append(_binding_entry(sector_id, authored_scene_path, object, anchor_id, correction))
	return {"ok": errors.is_empty(), "bindings": bindings, "errors": errors, "skipped": skipped}


## Merges generated entries into an existing bindings document. Entries for other objects
## are preserved; an entry for the same object_id is replaced.
static func merge_document(existing: Dictionary, map_id: String, sector_id: String, bindings: Array) -> Dictionary:
	var document := existing.duplicate(true)
	document["schema_id"] = SCHEMA_ID
	document["schema_version"] = SCHEMA_VERSION
	document["map_id"] = map_id
	document["sector_id"] = sector_id
	var replaced := {}
	for entry: Variant in bindings:
		replaced[str(((entry as Dictionary)["object_ref"] as Dictionary)["object_id"])] = true
	var kept: Array = []
	for entry: Variant in document.get("bindings", []) as Array:
		var ref := (entry as Dictionary).get("object_ref", {}) as Dictionary
		if not replaced.has(str(ref.get("object_id", ""))):
			kept.append(entry)
	kept.append_array(bindings)
	document["bindings"] = kept
	return document


## Post spacing at scale 1 declared by the prefab instanced as the object's `Content`; 0 if undeclared.
static func frame_post_spacing(object: Node) -> float:
	var content := object.get_node_or_null("Content")
	if content == null:
		return 0.0
	return float(content.get_meta(SPACING_META, 0.0))


static func door_placement() -> MapAnchorPlacement:
	var placement := MapAnchorPlacement.new()
	placement.mode = "linear"
	placement.policy = "centered"
	placement.centered_offset_m = 0.0
	placement.normal_offset_m = 0.0
	placement.height_m = 0.0
	placement.yaw_pitch_roll_deg = Vector3.ZERO
	placement.footprint_m = Vector3(PLACEHOLDER_FOOTPRINT[0], PLACEHOLDER_FOOTPRINT[1], PLACEHOLDER_FOOTPRINT[2])
	placement.footprint_center_m = Vector3(PLACEHOLDER_FOOTPRINT_CENTER[0], PLACEHOLDER_FOOTPRINT_CENTER[1], PLACEHOLDER_FOOTPRINT_CENTER[2])
	return placement


static func _binding_entry(sector_id: String, authored_scene_path: String, object: AnchoredObject3D, anchor_id: String, correction: Transform3D) -> Dictionary:
	var basis := correction.basis
	return {
		"binding_id": "BIND-%s-%s" % [sector_id, object.object_id],
		"object_ref": {
			"object_id": object.object_id,
			"scene": authored_scene_path,
			"node_path_hint": str(object.name),
		},
		"anchor_ref": {"anchor_id": anchor_id, "expected_type": DOOR_TYPE},
		"placement": {
			"mode": "linear",
			"linear": {"along": {"policy": "centered", "offset_m": 0.0}},
			"normal_offset_m": 0.0,
			"height_m": 0.0,
			"rotation": {"representation": "euler_deg", "order": "YXZ", "yaw_pitch_roll": [0.0, 0.0, 0.0]},
		},
		"footprint_m": PLACEHOLDER_FOOTPRINT,
		"footprint_center_m": PLACEHOLDER_FOOTPRINT_CENTER,
		"author_correction": {
			"origin": [0.0, 0.0, 0.0],
			"basis_x": _rounded(basis.x),
			"basis_y": _rounded(basis.y),
			"basis_z": _rounded(basis.z),
		},
		"constraints": {"inside_anchor_bounds": true, "collision_free": false},
		"on_missing_anchor": "block",
	}


static func _rounded(vector: Vector3) -> Array:
	return [snappedf(vector.x, 0.000001), snappedf(vector.y, 0.000001), snappedf(vector.z, 0.000001)]


## Maps data-handoff-id -> SVG element id for door elements of the sector's source SVG.
static func _door_handoff_ids(svg_path: String, errors: PackedStringArray) -> Dictionary:
	var result := {}
	var parser := XMLParser.new()
	if parser.open(svg_path) != OK:
		errors.append("cannot open source SVG: %s" % svg_path)
		return result
	while parser.read() == OK:
		if parser.get_node_type() != XMLParser.NODE_ELEMENT:
			continue
		if parser.has_attribute("data-godot-type") and parser.get_named_attribute_value("data-godot-type") == DOOR_TYPE \
				and parser.has_attribute("data-handoff-id") and parser.has_attribute("id"):
			var handoff_id := parser.get_named_attribute_value("data-handoff-id")
			if result.has(handoff_id):
				errors.append("duplicate data-handoff-id in source SVG: %s" % handoff_id)
			result[handoff_id] = parser.get_named_attribute_value("id")
	return result
