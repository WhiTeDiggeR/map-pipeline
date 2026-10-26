@tool
extends EditorPlugin

const OPERATIONS_SCRIPT := preload("res://addons/map_pipeline/godot/anchor_editor/anchor_editor_operations.gd")

var _panel: VBoxContainer
var _status: Label
var _candidate_picker: OptionButton
var _policy_picker: OptionButton
var _tolerance: SpinBox
var _normalized: SpinBox
var _distance: SpinBox
var _center_offset: SpinBox
var _normal_offset: SpinBox
var _height: SpinBox
var _yaw: SpinBox
var _pitch: SpinBox
var _roll: SpinBox
var _bind_button: Button
var _rebind_button: Button
var _unbind_button: Button
var _record_button: Button
var _selected_object: AnchoredObject3D
var _registry: MapAnchorRegistry
var _registry_ambiguous := false
var _candidate_explicit := false
var _operations: MapAnchorEditorOperations


func _enter_tree() -> void:
	_operations = OPERATIONS_SCRIPT.new()
	_build_panel()
	add_control_to_dock(DOCK_SLOT_RIGHT_UL, _panel)
	get_editor_interface().get_selection().selection_changed.connect(_refresh_selection)
	_refresh_selection()


func _exit_tree() -> void:
	var selection := get_editor_interface().get_selection()
	if selection.selection_changed.is_connected(_refresh_selection):
		selection.selection_changed.disconnect(_refresh_selection)
	remove_control_from_docks(_panel)
	_panel.queue_free()


func _build_panel() -> void:
	_panel = VBoxContainer.new()
	_panel.name = "Map Anchors"
	var title := Label.new()
	title.text = "Map Anchor Binding"
	_panel.add_child(title)
	_status = Label.new()
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_panel.add_child(_status)
	_tolerance = _add_spin("Candidate tolerance (m)", 0.01, 100.0, 0.05, 2.0)
	_tolerance.value_changed.connect(func(_value: float) -> void: _refresh_candidates())
	_candidate_picker = OptionButton.new()
	_candidate_picker.item_selected.connect(func(_index: int) -> void:
		_candidate_explicit = true
		_update_buttons()
	)
	_panel.add_child(_candidate_picker)
	_policy_picker = OptionButton.new()
	for policy: String in ["normalized", "from_start_m", "from_end_m", "centered"]:
		_policy_picker.add_item(policy)
	_panel.add_child(_policy_picker)
	_normalized = _add_spin("Normalized value", 0.0, 1.0, 0.01, 0.5)
	_distance = _add_spin("Distance (m)", 0.0, 1000.0, 0.05, 0.0)
	_center_offset = _add_spin("Center offset (m)", -1000.0, 1000.0, 0.05, 0.0)
	_normal_offset = _add_spin("Normal offset (m)", -100.0, 100.0, 0.01, 0.0)
	_height = _add_spin("Height (m)", -100.0, 100.0, 0.01, 0.0)
	_yaw = _add_spin("Yaw (deg)", -360.0, 360.0, 1.0, 0.0)
	_pitch = _add_spin("Pitch (deg)", -360.0, 360.0, 1.0, 0.0)
	_roll = _add_spin("Roll (deg)", -360.0, 360.0, 1.0, 0.0)
	_bind_button = _add_button("Bind selected", _bind_selected)
	_rebind_button = _add_button("Rebind preserving world transform", _rebind_selected)
	_unbind_button = _add_button("Unbind preserving world transform", _unbind_selected)
	_record_button = _add_button("Record current local correction", _record_correction)


func _add_spin(label_text: String, minimum: float, maximum: float, step: float, initial: float) -> SpinBox:
	var row := HBoxContainer.new()
	var label := Label.new()
	label.text = label_text
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(label)
	var spin := SpinBox.new()
	spin.min_value = minimum
	spin.max_value = maximum
	spin.step = step
	spin.value = initial
	row.add_child(spin)
	_panel.add_child(row)
	return spin


func _add_button(label_text: String, callback: Callable) -> Button:
	var button := Button.new()
	button.text = label_text
	button.pressed.connect(callback)
	_panel.add_child(button)
	return button


func _refresh_selection() -> void:
	_selected_object = null
	var nodes := get_editor_interface().get_selection().get_selected_nodes()
	if nodes.size() == 1 and nodes[0] is AnchoredObject3D:
		_selected_object = nodes[0] as AnchoredObject3D
	var scene_root := get_editor_interface().get_edited_scene_root()
	var registries: Array[MapAnchorRegistry] = []
	_collect_registries(scene_root, registries)
	_registry_ambiguous = false
	if _selected_object != null and _selected_object.anchor_registry != null:
		_registry = _selected_object.anchor_registry
	elif registries.size() == 1:
		_registry = registries[0]
	else:
		_registry = null
		_registry_ambiguous = registries.size() > 1
	_refresh_candidates()


func _collect_registries(node: Node, result: Array[MapAnchorRegistry]) -> void:
	if node == null:
		return
	if node is MapAnchorRegistry:
		result.append(node as MapAnchorRegistry)
	for child: Node in node.get_children():
		_collect_registries(child, result)


func _refresh_candidates() -> void:
	_candidate_picker.clear()
	_candidate_explicit = false
	if _selected_object == null:
		_status.text = "Select exactly one AnchoredObject3D."
		_update_buttons()
		return
	if _registry == null:
		_status.text = "Multiple registries exist; set anchor_registry explicitly." if _registry_ambiguous else "No MapAnchorRegistry exists in the edited scene."
		_update_buttons()
		return
	var candidates := _registry.find_compatible_candidates(
		_selected_object.expected_anchor_type,
		_selected_object.global_position,
		_tolerance.value
	)
	for candidate: Dictionary in candidates:
		var index := _candidate_picker.item_count
		_candidate_picker.add_item("%s  (%.3f m)" % [candidate["anchor_id"], candidate["distance_m"]])
		_candidate_picker.set_item_metadata(index, candidate["anchor_id"])
	if not _operations.single_candidate_id(candidates).is_empty():
		_candidate_picker.select(0)
		_candidate_explicit = true
		_status.text = "One compatible anchor found; automatic choice is allowed."
	elif candidates.is_empty():
		_status.text = "No compatible anchors inside tolerance. Nothing will be guessed."
	else:
		_candidate_picker.select(-1)
		_status.text = "%d compatible anchors; choose one explicitly." % candidates.size()
	_update_buttons()


func _update_buttons() -> void:
	var has_object := _selected_object != null
	var can_bind := has_object and _registry != null and _candidate_explicit and _candidate_picker.selected >= 0
	_bind_button.disabled = not can_bind
	_rebind_button.disabled = not can_bind
	_unbind_button.disabled = not has_object or _selected_object.anchor_id.is_empty()
	_record_button.disabled = not has_object or _selected_object.anchor_id.is_empty() or _selected_object.anchor_registry == null


func _placement_from_panel() -> MapAnchorPlacement:
	var placement := MapAnchorPlacement.new()
	placement.policy = _policy_picker.get_item_text(_policy_picker.selected)
	placement.normalized_value = _normalized.value
	placement.distance_m = _distance.value
	placement.centered_offset_m = _center_offset.value
	placement.normal_offset_m = _normal_offset.value
	placement.height_m = _height.value
	placement.yaw_pitch_roll_deg = Vector3(_yaw.value, _pitch.value, _roll.value)
	return placement


func _selected_anchor_id() -> String:
	if _candidate_picker.selected < 0:
		return ""
	return str(_candidate_picker.get_item_metadata(_candidate_picker.selected))


func _bind_selected() -> void:
	_show_result(_operations.bind(get_undo_redo(), _selected_object, _registry, _selected_anchor_id(), _placement_from_panel()))


func _rebind_selected() -> void:
	_show_result(_operations.rebind(get_undo_redo(), _selected_object, _registry, _selected_anchor_id(), _placement_from_panel()))


func _unbind_selected() -> void:
	_show_result(_operations.unbind(get_undo_redo(), _selected_object))


func _record_correction() -> void:
	_show_result(_operations.record_local_correction(get_undo_redo(), _selected_object))


func _show_result(errors: PackedStringArray) -> void:
	_refresh_candidates()
	if errors.is_empty():
		_status.text = "Operation completed. Use editor Undo/Redo to revert or reapply it."
	else:
		_status.text = "Blocked: %s" % "; ".join(errors)
