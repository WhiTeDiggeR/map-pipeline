extends SceneTree

const REGISTRY := preload("res://addons/map_pipeline/godot/anchors/anchor_registry.gd")
const PLACEMENT := preload("res://addons/map_pipeline/godot/anchors/anchor_placement.gd")
const AXIS := preload("res://addons/map_pipeline/godot/anchors/anchor_axis_placement.gd")

var _errors := PackedStringArray()
var _checks := 0


func _initialize() -> void:
	for kind: String in ["floor", "ceiling"]:
		var frame := _frame(kind)
		var placement := _placement()
		var baseline := _resolve(frame, placement)
		_expect(baseline.get("ok", false), "%s baseline: %s" % [kind, baseline])
		if baseline.get("ok", false):
			_expect((baseline["transform"] as Transform3D).origin.is_equal_approx(Vector3(5, 0, 4)), "%s UV position" % kind)
			_expect((baseline["transform"] as Transform3D).basis.is_equal_approx(Basis.IDENTITY), "%s nonsingular object basis" % kind)
		placement.normal_offset_m = 0.25
		frame["placement_limits"]["normal_offset_m"] = [0, 0.5]
		var offset := _resolve(frame, placement)
		_expect(offset.get("ok", false), "%s normal offset" % kind)
		if offset.get("ok", false):
			_expect(is_equal_approx((offset["transform"] as Transform3D).origin.y, 0.25 if kind == "floor" else -0.25), "%s physical normal direction" % kind)
	var policies := ["normalized", "from_start_m", "from_end_m", "centered"]
	var expected := [2.5, 2.0, 8.0, 6.0]
	for index: int in policies.size():
		var placement := _placement()
		placement.surface_u.policy = policies[index]
		placement.surface_u.normalized_value = 0.25
		placement.surface_u.distance_m = 2.0
		placement.surface_u.centered_offset_m = 1.0
		var result := _resolve(_frame(), placement)
		_expect(result.get("ok", false), "%s policy" % policies[index])
		if result.get("ok", false):
			_expect(is_equal_approx((result["transform"] as Transform3D).origin.x, expected[index]), "%s coordinate" % policies[index])
	_test_bounds()
	_test_transform()
	for error: String in _errors:
		push_error(error)
	print("MAP_SURFACE_CHECK checks=%d failures=%d" % [_checks, _errors.size()])
	quit(0 if _errors.is_empty() else 1)


func _frame(kind: String = "floor") -> Dictionary:
	return {
		"anchor_id": "AF-SURFACE", "type": kind, "status": "active",
		"origin": [0, 0, 0], "forward": [1, 0, 0], "up": [0, 1, 0],
		"normal": [0, 1 if kind == "floor" else -1, 0],
		"bounds": {"polygon_xz": [[0, 0], [10, 0], [10, 8], [0, 8]], "u_range_m": [0, 10], "v_range_m": [0, 8]},
		"placement_limits": {"normal_offset_m": [0, 0], "height_m": [0, 0], "rotation_deg": {"yaw": [-180, 180], "pitch": [-90, 90], "roll": [-180, 180]}},
	}


func _placement() -> MapAnchorPlacement:
	var placement := PLACEMENT.new()
	placement.mode = "surface"
	placement.surface_u = AXIS.new()
	placement.surface_v = AXIS.new()
	placement.footprint_m = Vector3(1, 1, 1)
	placement.footprint_center_m = Vector3(0, 0.5, 0)
	return placement


func _resolve(frame: Dictionary, placement: MapAnchorPlacement, correction: Transform3D = Transform3D.IDENTITY) -> Dictionary:
	var registry := REGISTRY.new()
	var document := {
		"schema_id": "caretaker.anchor_frames", "schema_version": "1.0.0",
		"map_id": "fixture", "sector_id": "fixture", "generation_id": "fixture",
		"coordinate_space": {"space": "world", "units": "m", "up_axis": "+Y", "horizontal_plane": "XZ"}, "anchors": [frame],
	}
	var errors := registry.load_anchor_document(document)
	var result := {"ok": false, "errors": errors}
	if errors.is_empty():
		result = registry.resolve_transform("AF-SURFACE", str(frame["type"]), placement, correction)
	registry.free()
	return result


func _expect(condition: bool, label: String) -> void:
	_checks += 1
	if not condition:
		_errors.append(label)


func _test_bounds() -> void:
	var frame := _frame()
	frame["bounds"].erase("u_range_m")
	_expect(not _resolve(frame, _placement())["ok"], "missing UV bounds must block")
	var placement := _placement()
	placement.surface_v = null
	_expect(not _resolve(_frame(), placement)["ok"], "missing V policy must block")
	placement = _placement()
	placement.footprint_m = Vector3.ZERO
	_expect(not _resolve(_frame(), placement)["ok"], "unknown footprint must block")
	placement = _placement()
	placement.surface_u.policy = "from_start_m"
	placement.surface_u.distance_m = 0.4
	_expect(not _resolve(_frame(), placement)["ok"], "pivot inside but footprint outside must block")
	placement.surface_u.distance_m = 0.5
	_expect(_resolve(_frame(), placement)["ok"], "exact footprint boundary is permitted")
	placement.yaw_pitch_roll_deg.x = 45.0
	_expect(not _resolve(_frame(), placement)["ok"], "rotated footprint overflow must block")
	frame = _frame()
	frame["bounds"]["polygon_xz"] = [[0, 0], [10, 0], [10, 2], [2, 2], [2, 8], [0, 8]]
	_expect(not _resolve(frame, _placement())["ok"], "concave polygon excluded region must block")
	frame = _frame()
	frame["bounds"]["holes_xz"] = [[[4.75, 3.75], [5.25, 3.75], [5.25, 4.25], [4.75, 4.25]]]
	_expect(not _resolve(frame, _placement())["ok"], "hole inside footprint must block even when corners are outside hole")
	frame = _frame()
	frame["placement_limits"]["height_m"] = [NAN, 0]
	_expect(not _resolve(frame, _placement())["ok"], "nonfinite limit must block")
	placement = _placement()
	placement.surface_u.policy = "normalized"
	placement.surface_u.normalized_value = 1.1
	_expect(not _resolve(_frame(), placement)["ok"], "invalid normalized fraction must block")
	placement.normal_offset_m = NAN
	_expect(not _resolve(_frame(), placement)["ok"], "nonfinite placement must block")
	frame = _frame()
	frame["normal"] = [0, -1, 0]
	_expect(not _resolve(frame, _placement())["ok"], "wrong floor normal must block")
	frame = _frame()
	frame["placement_limits"].erase("rotation_deg")
	_expect(not _resolve(frame, _placement())["ok"], "missing rotation limits must block")
	frame = _frame()
	frame["bounds"]["polygon_xz"] = [[0, 0], [10, 8], [10, 0], [0, 8]]
	_expect(not _resolve(frame, _placement())["ok"], "self-intersecting polygon must block")
	frame = _frame()
	frame["bounds"]["polygon_xz"] = [[0, 0], [10, 0], [10, 8], [0, 8], [0, 0]]
	_expect(_resolve(frame, _placement())["ok"], "explicitly closed polygon must resolve")


func _test_transform() -> void:
	var frame := _frame()
	# Translate, rotate the U/V frame 90 degrees, and resize U from 10 to 12 m.
	frame["origin"] = [20, 3, 30]
	frame["forward"] = [0, 0, 1]
	frame["bounds"]["u_range_m"] = [0, 12]
	frame["bounds"]["polygon_xz"] = [[20, 30], [20, 42], [12, 42], [12, 30]]
	var placement := _placement()
	var result := _resolve(frame, placement)
	_expect(result.get("ok", false), "moved rotated resized surface: %s" % result)
	if result.get("ok", false):
		_expect((result["transform"] as Transform3D).origin.is_equal_approx(Vector3(16, 3, 36)), "surface follows move rotate resize")
		_expect((result["transform"] as Transform3D).basis.x.is_equal_approx(Vector3.BACK), "surface orientation follows U")
	var correction := Transform3D(Basis.IDENTITY, Vector3(100, 0, 0))
	_expect(not _resolve(frame, placement, correction)["ok"], "authored correction cannot bypass containment")
	correction = Transform3D(Basis.from_scale(Vector3(1, 0, 1)), Vector3.ZERO)
	_expect(not _resolve(frame, placement, correction)["ok"], "singular author correction must block")
	var copy := placement.duplicate(true) as MapAnchorPlacement
	copy.surface_u.policy = "normalized"
	_expect(placement.surface_u.policy == "centered", "editor deep copy must preserve independent UV resources")
