@tool
extends RefCounted
class_name MapAnchorEditorOperations


func single_candidate_id(candidates: Array) -> String:
	if candidates.size() != 1 or not candidates[0] is Dictionary:
		return ""
	return str((candidates[0] as Dictionary).get("anchor_id", ""))


func bind(
	undo_redo: Variant,
	object: AnchoredObject3D,
	registry: MapAnchorRegistry,
	anchor_id: String,
	placement: MapAnchorPlacement
) -> PackedStringArray:
	return _bind_or_rebind("Bind map anchor", undo_redo, object, registry, anchor_id, placement)


func rebind(
	undo_redo: Variant,
	object: AnchoredObject3D,
	registry: MapAnchorRegistry,
	anchor_id: String,
	placement: MapAnchorPlacement
) -> PackedStringArray:
	return _bind_or_rebind("Rebind map anchor", undo_redo, object, registry, anchor_id, placement)


func unbind(undo_redo: Variant, object: AnchoredObject3D) -> PackedStringArray:
	var errors := _validate_common(undo_redo, object)
	if not errors.is_empty():
		return errors
	var old_state := _capture_state(object)
	var new_state := old_state.duplicate()
	new_state["anchor_id"] = ""
	new_state["anchor_registry"] = null
	new_state["apply_on_ready"] = false
	new_state["resolve"] = false
	new_state["world_transform"] = object.global_transform
	_commit_state_action(undo_redo, "Unbind map anchor", object, new_state, old_state)
	return PackedStringArray()


func record_local_correction(undo_redo: Variant, object: AnchoredObject3D) -> PackedStringArray:
	var errors := _validate_common(undo_redo, object)
	if object == null:
		return errors
	if object.anchor_registry == null or object.anchor_id.is_empty() or object.placement == null:
		errors.append("object has no complete anchor binding")
	if not errors.is_empty():
		return errors
	var base := object.anchor_registry.resolve_transform(
		object.anchor_id,
		object.expected_anchor_type,
		object.placement,
		Transform3D.IDENTITY
	)
	if not bool(base.get("ok", false)):
		return base.get("errors", PackedStringArray(["anchor resolution failed"])) as PackedStringArray
	var old_state := _capture_state(object)
	var new_state := old_state.duplicate()
	new_state["author_correction"] = (base["transform"] as Transform3D).affine_inverse() * object.global_transform
	var corrected := object.anchor_registry.resolve_transform(object.anchor_id, object.expected_anchor_type, object.placement, new_state["author_correction"])
	if not bool(corrected.get("ok", false)):
		return corrected.get("errors", PackedStringArray(["corrected anchor resolution failed"])) as PackedStringArray
	new_state["resolve"] = true
	_commit_state_action(undo_redo, "Record map local correction", object, new_state, old_state)
	return PackedStringArray()


func _bind_or_rebind(
	action_name: String,
	undo_redo: Variant,
	object: AnchoredObject3D,
	registry: MapAnchorRegistry,
	anchor_id: String,
	placement: MapAnchorPlacement
) -> PackedStringArray:
	var errors := _validate_common(undo_redo, object)
	if object == null:
		return errors
	if registry == null:
		errors.append("anchor registry is missing")
	if placement == null:
		errors.append("placement resource is missing")
	var frame := registry.get_anchor_frame(anchor_id) if registry != null else {}
	if frame.is_empty():
		errors.append("missing anchor_id: %s" % anchor_id)
	elif str(frame.get("type", "")) != object.expected_anchor_type:
		errors.append("anchor %s has type %s, expected %s" % [anchor_id, frame.get("type", ""), object.expected_anchor_type])
	if not errors.is_empty():
		return errors
	var object_id := object.object_id
	if object_id.is_empty():
		object_id = _new_object_id(registry, object)
		if object_id.is_empty():
			return PackedStringArray(["could not allocate a unique persistent object_id"])
	elif not registry.can_register_object_id(object_id, object):
		return PackedStringArray(["duplicate object_id: %s" % object_id])
	var placement_copy := placement.duplicate(true) as MapAnchorPlacement
	var base := registry.resolve_transform(anchor_id, object.expected_anchor_type, placement_copy, Transform3D.IDENTITY)
	if not bool(base.get("ok", false)):
		return base.get("errors", PackedStringArray(["anchor resolution failed"])) as PackedStringArray
	var world_transform := object.global_transform
	var correction := (base["transform"] as Transform3D).affine_inverse() * world_transform
	var corrected := registry.resolve_transform(anchor_id, object.expected_anchor_type, placement_copy, correction)
	if not bool(corrected.get("ok", false)):
		return corrected.get("errors", PackedStringArray(["corrected anchor resolution failed"])) as PackedStringArray
	var old_state := _capture_state(object)
	var new_state := {
		"object_id": object_id,
		"anchor_id": anchor_id,
		"expected_anchor_type": object.expected_anchor_type,
		"placement": placement_copy,
		"author_correction": correction,
		"anchor_registry": registry,
		"apply_on_ready": true,
		"resolve": true,
		"world_transform": world_transform,
	}
	_commit_state_action(undo_redo, action_name, object, new_state, old_state)
	return PackedStringArray()


func _validate_common(undo_redo: Variant, object: AnchoredObject3D) -> PackedStringArray:
	var errors := PackedStringArray()
	if undo_redo == null or not undo_redo.has_method("create_action"):
		errors.append("Undo/Redo service is missing")
	if object == null:
		errors.append("selected node must be AnchoredObject3D")
	return errors


func _capture_state(object: AnchoredObject3D) -> Dictionary:
	return {
		"object_id": object.object_id,
		"anchor_id": object.anchor_id,
		"expected_anchor_type": object.expected_anchor_type,
		"placement": object.placement,
		"author_correction": object.author_correction,
		"anchor_registry": object.anchor_registry,
		"apply_on_ready": object.apply_on_ready,
		"resolve": false,
		"world_transform": object.global_transform,
	}


func _commit_state_action(undo_redo: Variant, action_name: String, object: AnchoredObject3D, new_state: Dictionary, old_state: Dictionary) -> void:
	undo_redo.create_action(action_name)
	undo_redo.add_do_method(Callable(object, "apply_editor_binding_state").bind(new_state))
	undo_redo.add_undo_method(Callable(object, "apply_editor_binding_state").bind(old_state))
	undo_redo.commit_action()


func _new_object_id(registry: MapAnchorRegistry, object: AnchoredObject3D) -> String:
	var crypto := Crypto.new()
	for _attempt: int in range(32):
		var candidate := "OBJ-AUTO-%s" % crypto.generate_random_bytes(16).hex_encode().to_upper()
		if registry.can_register_object_id(candidate, object):
			return candidate
	return ""
