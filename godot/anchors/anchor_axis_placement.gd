extends Resource
class_name MapAnchorAxisPlacement

@export_enum("normalized", "from_start_m", "from_end_m", "centered") var policy := "centered"
@export var normalized_value := 0.5
@export var distance_m := 0.0
@export var centered_offset_m := 0.0


func resolve_range(minimum_m: float, maximum_m: float) -> Dictionary:
	return resolve_policy(policy, normalized_value, distance_m, centered_offset_m, minimum_m, maximum_m)


static func resolve_policy(kind: String, fraction: float, distance: float, offset: float, minimum: float, maximum: float) -> Dictionary:
	if not is_finite(minimum) or not is_finite(maximum) or maximum < minimum:
		return {"ok": false, "error": "invalid axis range"}
	var value: float
	match kind:
		"normalized":
			if not is_finite(fraction) or fraction < 0.0 or fraction > 1.0:
				return {"ok": false, "error": "normalized value must be in [0, 1]"}
			value = lerpf(minimum, maximum, fraction)
		"from_start_m", "from_end_m":
			if not is_finite(distance) or distance < 0.0:
				return {"ok": false, "error": "distance must be finite and nonnegative"}
			value = minimum + distance if kind == "from_start_m" else maximum - distance
		"centered":
			if not is_finite(offset):
				return {"ok": false, "error": "centered offset must be finite"}
			value = (minimum + maximum) * 0.5 + offset
		_:
			return {"ok": false, "error": "unknown placement policy: %s" % kind}
	if value < minimum - 1.0e-6 or value > maximum + 1.0e-6:
		return {"ok": false, "error": "placement is outside axis range"}
	return {"ok": true, "distance_m": value}
