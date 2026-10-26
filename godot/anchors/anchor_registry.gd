@tool
extends Node
class_name MapAnchorRegistry

signal registry_changed(generation_id: String)

const SCHEMA_ID := "caretaker.anchor_frames"
const SUPPORTED_MAJOR := 1
const ALLOWED_TYPES := ["point", "wall", "door", "floor", "ceiling", "shaft", "stair_entry", "stair_exit"]
const LINEAR_TYPES := ["wall", "door", "stair_entry", "stair_exit"]
const EPS := 1.0e-6

@export_file("*.json") var anchor_frames_path := ""
@export var load_on_ready := true

var _anchors: Dictionary = {}
var _objects: Dictionary = {}
var _map_id := ""
var _sector_id := ""
var _generation_id := ""
var _last_errors := PackedStringArray()


func _ready() -> void:
	if load_on_ready and not anchor_frames_path.is_empty():
		load_anchor_frames(anchor_frames_path)


func load_anchor_frames(path: String = anchor_frames_path) -> PackedStringArray:
	var errors := PackedStringArray()
	if path.is_empty() or not FileAccess.file_exists(path):
		errors.append("anchor_frames file is missing: %s" % path)
		_last_errors = errors
		return errors
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		errors.append("cannot open anchor_frames: %s" % path)
		_last_errors = errors
		return errors
	var parsed: Variant = JSON.parse_string(file.get_as_text())
	if not parsed is Dictionary:
		errors.append("anchor_frames root must be an object")
		_last_errors = errors
		return errors
	return load_anchor_document(parsed as Dictionary)


func load_anchor_document(document: Dictionary) -> PackedStringArray:
	var errors := _validate_document(document)
	if not errors.is_empty():
		_last_errors = errors
		return errors
	var staged: Dictionary = {}
	for frame_value: Variant in document["anchors"]:
		var frame := frame_value as Dictionary
		staged[str(frame["anchor_id"])] = frame.duplicate(true)
	_anchors = staged
	_map_id = str(document["map_id"])
	_sector_id = str(document["sector_id"])
	_generation_id = str(document["generation_id"])
	_last_errors = PackedStringArray()
	registry_changed.emit(_generation_id)
	return PackedStringArray()


func _validate_document(document: Dictionary) -> PackedStringArray:
	var errors := PackedStringArray()
	if str(document.get("schema_id", "")) != SCHEMA_ID:
		errors.append("unsupported anchor schema_id")
	var version := str(document.get("schema_version", ""))
	var major_text := version.split(".")[0] if not version.is_empty() else ""
	if not major_text.is_valid_int() or int(major_text) != SUPPORTED_MAJOR:
		errors.append("unsupported anchor schema major: %s" % version)
	for key: String in ["map_id", "sector_id", "generation_id"]:
		if str(document.get(key, "")).is_empty():
			errors.append("anchor document is missing %s" % key)
	var coordinate := document.get("coordinate_space", {}) as Dictionary
	if str(coordinate.get("units", "")) != "m" or str(coordinate.get("horizontal_plane", "")) != "XZ" or str(coordinate.get("up_axis", "")) != "+Y" or str(coordinate.get("space", "")) != "world":
		errors.append("anchor document coordinate_space must be world metres with XZ/+Y")
	var anchors_value: Variant = document.get("anchors", null)
	if not anchors_value is Array:
		errors.append("anchor document anchors must be an array")
		return errors
	var ids: Dictionary = {}
	for frame_value: Variant in anchors_value:
		if not frame_value is Dictionary:
			errors.append("anchor frame must be an object")
			continue
		var frame := frame_value as Dictionary
		var anchor_id := str(frame.get("anchor_id", ""))
		if anchor_id.is_empty():
			errors.append("anchor frame has no anchor_id")
		elif ids.has(anchor_id):
			errors.append("duplicate anchor_id: %s" % anchor_id)
		else:
			ids[anchor_id] = true
		_validate_frame(frame, anchor_id, errors)
	return errors


func _validate_frame(frame: Dictionary, anchor_id: String, errors: PackedStringArray) -> void:
	var anchor_type := str(frame.get("type", ""))
	if anchor_type not in ALLOWED_TYPES:
		errors.append("%s has unsupported anchor type: %s" % [anchor_id, anchor_type])
	if str(frame.get("status", "")) != "active":
		errors.append("%s is not active" % anchor_id)
	var vectors: Dictionary = {}
	for key: String in ["origin", "forward", "normal", "up"]:
		var result: Variant = _read_vector3(frame.get(key), "%s.%s" % [anchor_id, key], errors)
		if result != null:
			vectors[key] = result
	if vectors.size() != 4:
		return
	var forward := vectors["forward"] as Vector3
	var normal := vectors["normal"] as Vector3
	var up := vectors["up"] as Vector3
	if absf(forward.length() - 1.0) > EPS or absf(normal.length() - 1.0) > EPS or absf(up.length() - 1.0) > EPS:
		errors.append("%s basis vectors must be normalized" % anchor_id)
	if absf(forward.dot(up)) > EPS or absf(normal.dot(forward)) > EPS:
		errors.append("%s basis vectors are not orthogonal" % anchor_id)
	if not up.is_equal_approx(Vector3.UP):
		errors.append("%s up must be world +Y" % anchor_id)
	if anchor_type in ["floor", "ceiling"]:
		var expected_normal := up if anchor_type == "floor" else -up
		if not normal.is_equal_approx(expected_normal):
			errors.append("%s surface normal has the wrong direction" % anchor_id)
	if anchor_type in LINEAR_TYPES and not forward.cross(up).is_equal_approx(normal):
		errors.append("%s linear basis must satisfy forward cross up = normal" % anchor_id)
	if not frame.get("bounds") is Dictionary or not frame.get("placement_limits") is Dictionary:
		errors.append("%s must declare bounds and placement_limits" % anchor_id)


func _read_vector3(value: Variant, label: String, errors: PackedStringArray) -> Variant:
	if not value is Array or (value as Array).size() != 3:
		errors.append("%s must contain three numbers" % label)
		return null
	var values := value as Array
	for item: Variant in values:
		if not item is float and not item is int:
			errors.append("%s must contain only numbers" % label)
			return null
	var result := Vector3(float(values[0]), float(values[1]), float(values[2]))
	if not result.is_finite():
		errors.append("%s must be finite" % label)
		return null
	return result


func resolve_transform(anchor_id: String, expected_type: String, placement: MapAnchorPlacement, author_correction := Transform3D.IDENTITY) -> Dictionary:
	if not _anchors.has(anchor_id):
		return {"ok": false, "errors": PackedStringArray(["missing anchor_id: %s" % anchor_id])}
	var frame := _anchors[anchor_id] as Dictionary
	var anchor_type := str(frame["type"])
	if anchor_type != expected_type:
		return {"ok": false, "errors": PackedStringArray(["anchor %s has type %s, expected %s" % [anchor_id, anchor_type, expected_type]])}
	if placement == null:
		return {"ok": false, "errors": PackedStringArray(["placement resource is missing"])}
	if not is_finite(placement.normal_offset_m) or not is_finite(placement.height_m) or not placement.yaw_pitch_roll_deg.is_finite() or not author_correction.is_finite():
		return {"ok": false, "errors": PackedStringArray(["placement and author correction must be finite"])}
	if anchor_type in ["floor", "ceiling"]:
		return _resolve_surface(frame, placement, author_correction)
	if anchor_type not in LINEAR_TYPES:
		return {"ok": false, "errors": PackedStringArray(["anchor type %s has no linear placement in contract v1" % anchor_type])}
	if placement.mode != "linear":
		return {"ok": false, "errors": PackedStringArray(["linear anchor requires linear placement"])}
	var bounds := frame["bounds"] as Dictionary
	var raw_range: Variant = bounds.get("along_range_m")
	var along_range := [0.0, float(bounds.get("length_m", -1.0))]
	if raw_range != null:
		if not _valid_range(raw_range):
			return {"ok": false, "errors": PackedStringArray(["anchor %s has invalid along_range_m" % anchor_id])}
		along_range = [float(raw_range[0]), float(raw_range[1])]
	elif along_range[1] < 0.0:
		return {"ok": false, "errors": PackedStringArray(["anchor %s has no explicit linear range" % anchor_id])}
	var along := MapAnchorAxisPlacement.resolve_policy(
		placement.policy, placement.normalized_value, placement.distance_m,
		placement.centered_offset_m, along_range[0], along_range[1]
	)
	if not bool(along.get("ok", false)):
		return {"ok": false, "errors": PackedStringArray([str(along.get("error", "invalid placement"))])}
	var distance := float(along["distance_m"])
	var errors := PackedStringArray()
	var limits := frame["placement_limits"] as Dictionary
	_validate_scalar_limit(placement.normal_offset_m, limits.get("normal_offset_m"), "normal_offset_m", errors)
	_validate_scalar_limit(placement.height_m, limits.get("height_m"), "height_m", errors)
	_validate_rotation_limits(placement.yaw_pitch_roll_deg, limits.get("rotation_deg"), errors)
	if not errors.is_empty():
		return {"ok": false, "errors": errors}
	var origin := _vector_from_frame(frame, "origin")
	var forward := _vector_from_frame(frame, "forward")
	var normal := _vector_from_frame(frame, "normal")
	var up := _vector_from_frame(frame, "up")
	var anchor_basis := Basis(forward, up, normal)
	var position := origin + forward * distance + normal * placement.normal_offset_m + up * placement.height_m
	var resolved := Transform3D(anchor_basis * placement.rotation_basis(), position) * author_correction
	_validate_linear_footprint(frame, bounds, placement, resolved, origin, forward, up, errors)
	if not errors.is_empty():
		return {"ok": false, "errors": errors}
	return {"ok": true, "transform": resolved, "anchor_id": anchor_id, "generation_id": _generation_id}


func _resolve_surface(frame: Dictionary, placement: MapAnchorPlacement, correction: Transform3D) -> Dictionary:
	var errors := PackedStringArray()
	if placement.mode != "surface" or placement.surface_u == null or placement.surface_v == null:
		return {"ok": false, "errors": PackedStringArray(["surface placement requires explicit U and V policies"])}
	var bounds := frame["bounds"] as Dictionary
	var ranges: Dictionary = {}
	for key: String in ["u_range_m", "v_range_m"]:
		var raw: Variant = bounds.get(key)
		if not _valid_range(raw):
			errors.append("surface bounds.%s must be an explicit finite range" % key)
		else:
			ranges[key] = raw
	if not errors.is_empty():
		return {"ok": false, "errors": errors}
	var u := placement.surface_u.resolve_range(float(ranges["u_range_m"][0]), float(ranges["u_range_m"][1]))
	var v := placement.surface_v.resolve_range(float(ranges["v_range_m"][0]), float(ranges["v_range_m"][1]))
	for axis: Dictionary in [u, v]:
		if not axis["ok"]:
			errors.append(str(axis["error"]))
	var limits := frame["placement_limits"] as Dictionary
	_validate_scalar_limit(placement.normal_offset_m, limits.get("normal_offset_m"), "normal_offset_m", errors)
	_validate_scalar_limit(placement.height_m, limits.get("height_m"), "height_m", errors)
	_validate_rotation_limits(placement.yaw_pitch_roll_deg, limits.get("rotation_deg"), errors)
	if not errors.is_empty():
		return {"ok": false, "errors": errors}
	var origin := _vector_from_frame(frame, "origin")
	var forward := _vector_from_frame(frame, "forward")
	var up := _vector_from_frame(frame, "up")
	var normal := _vector_from_frame(frame, "normal")
	# Physical normal is vertical here, not the object's local Z axis.
	var axis_v := forward.cross(up)
	var position := origin + forward * float(u["distance_m"]) + axis_v * float(v["distance_m"]) + normal * placement.normal_offset_m + up * placement.height_m
	var resolved := Transform3D(Basis(forward, up, axis_v) * placement.rotation_basis(), position) * correction
	if not resolved.is_finite() or not is_finite(resolved.basis.determinant()) or absf(resolved.basis.determinant()) <= EPS:
		return {"ok": false, "errors": PackedStringArray(["surface transform must be finite and nonsingular"])}
	_validate_surface_footprint(bounds, placement, resolved, origin, forward, axis_v, errors)
	if not errors.is_empty():
		return {"ok": false, "errors": errors}
	return {"ok": true, "transform": resolved, "anchor_id": str(frame["anchor_id"]), "generation_id": _generation_id}


func _valid_range(raw: Variant) -> bool:
	if not raw is Array or (raw as Array).size() != 2:
		return false
	for item: Variant in raw:
		if (not item is int and not item is float) or not is_finite(float(item)):
			return false
	return float(raw[0]) <= float(raw[1])


func _surface_polygon(raw: Variant, label: String, errors: PackedStringArray) -> PackedVector2Array:
	var result := PackedVector2Array()
	if raw is Array:
		for point: Variant in raw:
			if not point is Array or (point as Array).size() != 2:
				errors.append("%s must contain XZ pairs" % label)
				return result
			for coordinate: Variant in point:
				if (not coordinate is int and not coordinate is float) or not is_finite(float(coordinate)):
					errors.append("%s coordinates must be finite numbers" % label)
					return result
			result.append(Vector2(float(point[0]), float(point[1])))
	if result.size() > 1 and result[0].is_equal_approx(result[-1]):
		result.resize(result.size() - 1)
	if result.size() < 3:
		errors.append("%s must have at least three vertices" % label)
		return result
	for index: int in result.size():
		var next := (index + 1) % result.size()
		if result[index].is_equal_approx(result[next]):
			errors.append("%s has a zero-length edge" % label)
			return result
		for other: int in range(index + 1, result.size()):
			var other_next := (other + 1) % result.size()
			if other == next or other_next == index:
				continue
			if _segments_intersect(result[index], result[next], result[other], result[other_next]):
				errors.append("%s has intersecting nonadjacent edges" % label)
				return result
	if Geometry2D.triangulate_polygon(result).is_empty():
		errors.append("%s must be a valid simple polygon" % label)
	return result


func _segments_intersect(a: Vector2, b: Vector2, c: Vector2, d: Vector2) -> bool:
	var ab := b - a
	var cd := d - c
	var denominator := ab.cross(cd)
	if absf(denominator) <= EPS:
		if absf((c - a).cross(ab)) > EPS:
			return false
		return minf(maxf(a.x, b.x), maxf(c.x, d.x)) >= maxf(minf(a.x, b.x), minf(c.x, d.x)) - EPS and minf(maxf(a.y, b.y), maxf(c.y, d.y)) >= maxf(minf(a.y, b.y), minf(c.y, d.y)) - EPS
	var t := (c - a).cross(cd) / denominator
	var u := (c - a).cross(ab) / denominator
	return t >= -EPS and t <= 1.0 + EPS and u >= -EPS and u <= 1.0 + EPS


func _polygon_area(polygon: PackedVector2Array) -> float:
	var twice_area := 0.0
	for index: int in polygon.size():
		twice_area += polygon[index].cross(polygon[(index + 1) % polygon.size()])
	return absf(twice_area) * 0.5


func _validate_surface_footprint(bounds: Dictionary, placement: MapAnchorPlacement, resolved: Transform3D, origin: Vector3, axis_u: Vector3, axis_v: Vector3, errors: PackedStringArray) -> void:
	var size := placement.footprint_m
	if not size.is_finite() or minf(size.x, minf(size.y, size.z)) <= 0.0 or not placement.footprint_center_m.is_finite():
		errors.append("surface placement requires explicit positive footprint dimensions and finite center")
		return
	var polygon := _surface_polygon(bounds.get("polygon_xz"), "polygon_xz", errors)
	var holes: Array[PackedVector2Array] = []
	var raw_holes: Variant = bounds.get("holes_xz", [])
	if not raw_holes is Array:
		errors.append("holes_xz must be an array")
	else:
		for hole: Variant in raw_holes:
			holes.append(_surface_polygon(hole, "hole", errors))
	if not errors.is_empty():
		return
	var corners := PackedVector2Array()
	for x: float in [-0.5, 0.5]:
		for y: float in [-0.5, 0.5]:
			for z: float in [-0.5, 0.5]:
				var world := resolved * (placement.footprint_center_m + size * Vector3(x, y, z))
				if not world.is_finite():
					errors.append("transformed footprint must be finite")
					return
				corners.append(Vector2(world.x, world.z))
				_validate_scalar_limit((world - origin).dot(axis_u), bounds["u_range_m"], "footprint U", errors)
				_validate_scalar_limit((world - origin).dot(axis_v), bounds["v_range_m"], "footprint V", errors)
	var hull := Geometry2D.convex_hull(corners)
	if hull.size() > 1 and hull[0].is_equal_approx(hull[-1]):
		hull.resize(hull.size() - 1)
	if _polygon_area(hull) <= EPS or not _polygon_contains_polygon(polygon, hull):
		errors.append("surface footprint extends outside polygon")
	for hole: PackedVector2Array in holes:
		if _polygons_overlap(hull, hole):
			errors.append("surface footprint overlaps an opening")


func _validate_linear_footprint(_frame: Dictionary, bounds: Dictionary, placement: MapAnchorPlacement, resolved: Transform3D, origin: Vector3, forward: Vector3, up: Vector3, errors: PackedStringArray) -> void:
	var size := placement.footprint_m
	# Pre-footprint linear resources remain valid for backward compatibility.
	# Binding documents are stricter: SectorAnchorController requires an explicit
	# positive footprint before calling the registry.
	if size == Vector3.ZERO and placement.footprint_center_m == Vector3.ZERO:
		return
	if not size.is_finite() or minf(size.x, minf(size.y, size.z)) <= 0.0 or not placement.footprint_center_m.is_finite():
		errors.append("linear placement requires explicit positive footprint dimensions and finite center")
		return
	var raw_range: Variant = bounds.get("along_range_m", [0.0, bounds.get("length_m")])
	if not _valid_range(raw_range):
		errors.append("linear anchor requires an explicit along range")
		return
	var height_extent := float(bounds.get("height_m", bounds.get("clear_height_m", bounds.get("height", -1.0))))
	if not is_finite(height_extent) or height_extent < 0.0:
		errors.append("linear anchor height is missing")
		return
	for x: float in [-0.5, 0.5]:
		for y: float in [-0.5, 0.5]:
			for z: float in [-0.5, 0.5]:
				var world := resolved * (placement.footprint_center_m + size * Vector3(x, y, z))
				if not world.is_finite():
					errors.append("transformed footprint must be finite")
					return
				_validate_scalar_limit((world - origin).dot(forward), raw_range, "footprint along", errors)
				_validate_scalar_limit((world - origin).dot(up), [0.0, height_extent], "footprint height", errors)


func _point_on_segment(point: Vector2, first: Vector2, second: Vector2) -> bool:
	if absf((point - first).cross(second - first)) > EPS:
		return false
	return point.x >= minf(first.x, second.x) - EPS and point.x <= maxf(first.x, second.x) + EPS and point.y >= minf(first.y, second.y) - EPS and point.y <= maxf(first.y, second.y) + EPS


func _point_in_polygon_inclusive(point: Vector2, polygon: PackedVector2Array) -> bool:
	var inside := false
	for index: int in polygon.size():
		var first := polygon[index]
		var second := polygon[(index + 1) % polygon.size()]
		if _point_on_segment(point, first, second):
			return true
		if (first.y > point.y) != (second.y > point.y):
			var x_at_y := (second.x - first.x) * (point.y - first.y) / (second.y - first.y) + first.x
			if point.x < x_at_y:
				inside = not inside
	return inside


func _segments_cross_strict(a: Vector2, b: Vector2, c: Vector2, d: Vector2) -> bool:
	var ab_c := (b - a).cross(c - a)
	var ab_d := (b - a).cross(d - a)
	var cd_a := (d - c).cross(a - c)
	var cd_b := (d - c).cross(b - c)
	return ab_c * ab_d < -EPS and cd_a * cd_b < -EPS


func _polygon_contains_polygon(container: PackedVector2Array, subject: PackedVector2Array) -> bool:
	for point: Vector2 in subject:
		if not _point_in_polygon_inclusive(point, container):
			return false
	for subject_index: int in subject.size():
		for container_index: int in container.size():
			if _segments_cross_strict(subject[subject_index], subject[(subject_index + 1) % subject.size()], container[container_index], container[(container_index + 1) % container.size()]):
				return false
	return true


func _polygons_overlap(first: PackedVector2Array, second: PackedVector2Array) -> bool:
	for point: Vector2 in first:
		if _point_in_polygon_inclusive(point, second):
			return true
	for point: Vector2 in second:
		if _point_in_polygon_inclusive(point, first):
			return true
	for first_index: int in first.size():
		for second_index: int in second.size():
			if _segments_intersect(first[first_index], first[(first_index + 1) % first.size()], second[second_index], second[(second_index + 1) % second.size()]):
				return true
	return false


func _validate_scalar_limit(value: float, raw_limit: Variant, label: String, errors: PackedStringArray) -> void:
	if not _valid_range(raw_limit):
		errors.append("placement_limits.%s is missing" % label)
		return
	var limit := raw_limit as Array
	if value < float(limit[0]) - EPS or value > float(limit[1]) + EPS:
		errors.append("%s is outside placement limits" % label)


func _validate_rotation_limits(value: Vector3, raw_limits: Variant, errors: PackedStringArray) -> void:
	if not raw_limits is Dictionary:
		errors.append("placement_limits.rotation_deg is missing")
		return
	var limits := raw_limits as Dictionary
	for item: Dictionary in [
		{"name": "yaw", "value": value.x},
		{"name": "pitch", "value": value.y},
		{"name": "roll", "value": value.z},
	]:
		var raw: Variant = limits.get(item["name"])
		if not _valid_range(raw):
			errors.append("rotation limit %s is missing" % item["name"])
		elif float(item["value"]) < float(raw[0]) - EPS or float(item["value"]) > float(raw[1]) + EPS:
			errors.append("rotation %s is outside placement limits" % item["name"])


func _vector_from_frame(frame: Dictionary, key: String) -> Vector3:
	var value := frame[key] as Array
	return Vector3(float(value[0]), float(value[1]), float(value[2]))


func register_object(object: Node) -> PackedStringArray:
	var errors := PackedStringArray()
	var object_id := str(object.get("object_id"))
	if object_id.is_empty():
		errors.append("anchored object has no object_id")
		return errors
	if _objects.has(object_id):
		var existing := (_objects[object_id] as WeakRef).get_ref() as Node
		if existing != null and existing != object:
			errors.append("duplicate object_id: %s" % object_id)
			return errors
	_objects[object_id] = weakref(object)
	return errors


func unregister_object(object: Node) -> void:
	var object_id := str(object.get("object_id"))
	if _objects.has(object_id) and (_objects[object_id] as WeakRef).get_ref() == object:
		_objects.erase(object_id)


func refresh_registered_objects() -> PackedStringArray:
	var errors := PackedStringArray()
	for object_id: String in _objects.keys():
		var object := (_objects[object_id] as WeakRef).get_ref() as Node
		if object == null:
			_objects.erase(object_id)
			continue
		var object_errors: PackedStringArray = object.call("apply_anchor_transform")
		for error: String in object_errors:
			errors.append("%s: %s" % [object_id, error])
	return errors


func get_anchor_frame(anchor_id: String) -> Dictionary:
	if not _anchors.has(anchor_id):
		return {}
	return (_anchors[anchor_id] as Dictionary).duplicate(true)


func find_compatible_candidates(expected_type: String, world_position: Vector3, tolerance_m: float) -> Array[Dictionary]:
	var candidates: Array[Dictionary] = []
	if expected_type not in LINEAR_TYPES or tolerance_m < 0.0:
		return candidates
	for anchor_id: String in _anchors.keys():
		var frame := _anchors[anchor_id] as Dictionary
		if str(frame.get("type", "")) != expected_type:
			continue
		var origin := _vector_from_frame(frame, "origin")
		var forward := _vector_from_frame(frame, "forward")
		var up := _vector_from_frame(frame, "up")
		var bounds := frame.get("bounds", {}) as Dictionary
		var along_range: Array = [0.0, float(bounds.get("length_m", bounds.get("width_m", bounds.get("clear_width_m", -1.0))))]
		if bounds.has("along_range_m"):
			var raw_range: Variant = bounds["along_range_m"]
			if not _valid_range(raw_range):
				continue
			along_range = [float(raw_range[0]), float(raw_range[1])]
		if along_range[1] < along_range[0]:
			continue
		var along_m := clampf((world_position - origin).dot(forward), along_range[0], along_range[1])
		var height_extent_m := maxf(float(bounds.get("height_m", 0.0)), 0.0)
		var height_m := clampf((world_position - origin).dot(up), 0.0, height_extent_m)
		var closest := origin + forward * along_m + up * height_m
		var distance_m := world_position.distance_to(closest)
		if distance_m <= tolerance_m + EPS:
			candidates.append({
				"anchor_id": anchor_id,
				"type": expected_type,
				"distance_m": distance_m,
				"along_m": along_m,
				"height_m": height_m,
				"closest_point": closest,
			})
	candidates.sort_custom(_candidate_less)
	return candidates


func _candidate_less(left: Dictionary, right: Dictionary) -> bool:
	var distance_delta := float(left["distance_m"]) - float(right["distance_m"])
	if absf(distance_delta) > EPS:
		return distance_delta < 0.0
	return str(left["anchor_id"]) < str(right["anchor_id"])


func can_register_object_id(object_id: String, object: Node) -> bool:
	if object_id.is_empty() or not _objects.has(object_id):
		return not object_id.is_empty()
	var existing := (_objects[object_id] as WeakRef).get_ref() as Node
	return existing == null or existing == object


func get_last_errors() -> PackedStringArray:
	return _last_errors.duplicate()


func get_generation_id() -> String:
	return _generation_id
