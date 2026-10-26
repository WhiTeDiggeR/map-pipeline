@tool
extends Node3D
class_name AnchoredObject3D

signal anchor_resolution_changed(valid: bool, errors: PackedStringArray)

@export var object_id := ""
@export var anchor_id := ""
@export_enum("point", "wall", "door", "floor", "ceiling", "shaft", "stair_entry", "stair_exit") var expected_anchor_type := "wall"
@export var placement: MapAnchorPlacement
@export var author_correction := Transform3D.IDENTITY
@export var anchor_registry: MapAnchorRegistry
@export var apply_on_ready := true

var _last_errors := PackedStringArray()


func _ready() -> void:
	if anchor_registry == null:
		_last_errors = PackedStringArray(["anchor registry is missing"])
		anchor_resolution_changed.emit(false, _last_errors)
		return
	var registration_errors := anchor_registry.register_object(self)
	if not registration_errors.is_empty():
		_last_errors = registration_errors
		anchor_resolution_changed.emit(false, _last_errors)
		return
	if apply_on_ready:
		apply_anchor_transform()


func _exit_tree() -> void:
	if anchor_registry != null:
		anchor_registry.unregister_object(self)


func apply_anchor_transform() -> PackedStringArray:
	if anchor_registry == null:
		_last_errors = PackedStringArray(["anchor registry is missing"])
		anchor_resolution_changed.emit(false, _last_errors)
		return _last_errors.duplicate()
	var resolved := anchor_registry.resolve_transform(anchor_id, expected_anchor_type, placement, author_correction)
	if not bool(resolved.get("ok", false)):
		_last_errors = resolved.get("errors", PackedStringArray(["anchor resolution failed"])) as PackedStringArray
		anchor_resolution_changed.emit(false, _last_errors)
		return _last_errors.duplicate()
	global_transform = resolved["transform"] as Transform3D
	_last_errors = PackedStringArray()
	anchor_resolution_changed.emit(true, _last_errors)
	return PackedStringArray()


func get_anchor_errors() -> PackedStringArray:
	return _last_errors.duplicate()


func apply_editor_binding_state(state: Dictionary) -> void:
	if anchor_registry != null:
		anchor_registry.unregister_object(self)
	object_id = str(state.get("object_id", object_id))
	anchor_id = str(state.get("anchor_id", ""))
	expected_anchor_type = str(state.get("expected_anchor_type", expected_anchor_type))
	placement = state.get("placement") as MapAnchorPlacement
	author_correction = state.get("author_correction", Transform3D.IDENTITY) as Transform3D
	anchor_registry = state.get("anchor_registry") as MapAnchorRegistry
	apply_on_ready = bool(state.get("apply_on_ready", apply_on_ready))
	if anchor_registry != null:
		_last_errors = anchor_registry.register_object(self)
	else:
		_last_errors = PackedStringArray()
	if bool(state.get("resolve", false)) and _last_errors.is_empty():
		apply_anchor_transform()
	else:
		global_transform = state.get("world_transform", global_transform) as Transform3D
