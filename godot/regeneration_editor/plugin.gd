@tool
extends EditorPlugin

const OPERATIONS_SCRIPT := preload("res://addons/map_pipeline/godot/regeneration_editor/regeneration_editor_operations.gd")
const ANCHOR_OPERATIONS_SCRIPT := preload("res://addons/map_pipeline/godot/anchor_editor/anchor_editor_operations.gd")
const DOOR_BINDING_SCRIPT := preload("res://addons/map_pipeline/godot/anchors/door_binding_builder.gd")
const SETTINGS_PREFIX := "map_pipeline/"
## Project setting with the project's sector generation manifest (res:// path).
const MANIFEST_SETTING := "map_pipeline/sector_manifest"
## A narrower depth range reduces z-fighting where surfaces overlap. Only factory values are replaced.
const VIEWPORT_Z_NEAR := 0.1
const VIEWPORT_Z_FAR := 300.0
const FACTORY_Z_NEAR := 0.05
const FACTORY_Z_FAR := 4000.0
const PERSISTENT_SETTINGS := ["python_executable", "svg_tool_root", "stair_tool_root", "agent_launcher", "inkscape_executable"]
const FILE_SETTINGS := ["python_executable", "agent_launcher", "inkscape_executable"]
const COLOR_OK := Color(0.42, 0.78, 0.45)
const COLOR_WARNING := Color(0.93, 0.72, 0.30)
const COLOR_ERROR := Color(0.93, 0.42, 0.40)
const COLOR_MUTED := Color(0.65, 0.68, 0.72)
const STAGE_NAMES := {
	"idle": "ожидание",
	"regenerate": "пересоздание",
	"validate": "проверка",
	"agent_fix": "исправление агентом",
	"source_validation": "проверка исходного SVG",
	"staging_generation": "генерация во временную папку",
	"stair_generation": "генерация лестниц",
	"combined_validation": "проверка сборки сектора",
	"composition_validation": "проверка расстановки объектов",
	"atomic_promotion": "замена рабочих файлов",
	"generated_validation": "проверка сгенерированных файлов",
	"geometry_validation": "проверка геометрии",
	"toolchain_validation": "проверка инструментов",
	"binding_resolution": "применение привязок объектов",
	"diagnostic_persistence": "сохранение диагностики",
	"agent_revalidation": "повторная проверка после агента",
	"report_unavailable": "отчёт недоступен",
	"none": "не начато",
}

var _operations: MapRegenerationEditorOperations
var _anchor_operations: MapAnchorEditorOperations
var _panel: ScrollContainer
var _content: VBoxContainer
var _sector_label: Label
var _source_label: Label
var _hint_label: Label
var _toolchain_label: Label
var _inkscape_label: Label
var _agent_label: Label
var _stage_label: Label
var _exit_label: Label
var _status: Label
var _details: TextEdit
var _copy_button: Button
var _problems_label: Label
var _manifest: LineEdit
var _python: LineEdit
var _svg_root: LineEdit
var _stair_root: LineEdit
var _agent_launcher: LineEdit
var _inkscape: LineEdit
var _anchor_id: LineEdit
var _agent_fix_button: Button
var _claude_button: Button
var _binding_toggle: Button
var _binding_container: VBoxContainer
var _settings_toggle: Button
var _settings_container: VBoxContainer
var _report_path := ""
var _source_svg := ""
var _saved_versions: Dictionary = {}
var _thread: Thread
var _thread_result: Dictionary = {}
var _mutex := Mutex.new()
var _running := false
var _buttons: Array[Button] = []
var _resolved_toolchain: Dictionary = {}
var _active_context: Dictionary = {}
var _active_action := ""
var _reload_in_progress := false


func _enter_tree() -> void:
	_operations = OPERATIONS_SCRIPT.new()
	_anchor_operations = ANCHOR_OPERATIONS_SCRIPT.new()
	_ensure_editor_settings()
	_apply_viewport_depth_range()
	_build_panel()
	add_control_to_dock(DOCK_SLOT_RIGHT_UL, _panel)
	scene_changed.connect(_on_scene_changed)
	scene_saved.connect(_on_scene_saved)
	get_editor_interface().get_selection().selection_changed.connect(_refresh_context)
	_on_scene_changed(get_editor_interface().get_edited_scene_root())
	set_process(true)


func _exit_tree() -> void:
	if _thread != null and _thread.is_started():
		_thread.wait_to_finish()
	if scene_changed.is_connected(_on_scene_changed):
		scene_changed.disconnect(_on_scene_changed)
	if scene_saved.is_connected(_on_scene_saved):
		scene_saved.disconnect(_on_scene_saved)
	var selection := get_editor_interface().get_selection()
	if selection.selection_changed.is_connected(_refresh_context):
		selection.selection_changed.disconnect(_refresh_context)
	remove_control_from_docks(_panel)
	_panel.queue_free()


func _process(_delta: float) -> void:
	if not _running or _thread == null or _thread.is_alive():
		return
	_thread.wait_to_finish()
	_mutex.lock()
	var result := _thread_result.duplicate(true)
	_mutex.unlock()
	_running = false
	_finish_cli(int(result.get("exit_code", -1)), result.get("output", PackedStringArray()) as PackedStringArray)


# --- Panel layout -----------------------------------------------------------------------


func _build_panel() -> void:
	_panel = ScrollContainer.new()
	_panel.name = "Генерация сектора"
	_panel.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_content = VBoxContainer.new()
	_content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_content.add_theme_constant_override("separation", 6)
	_panel.add_child(_content)

	var sector := _section("Сектор")
	_sector_label = _add_label("Сектор: не определён", sector)
	_source_label = _add_label("План: —", sector)
	_hint_label = _add_label("", sector, COLOR_MUTED)

	var actions := _section("Работа с планом")
	_add_button("1. Открыть план в Inkscape", _open_source, actions, "Открывает исходный SVG сектора в Inkscape. После правки сохраните файл.")
	_add_button("2. Проверить сектор", func() -> void: _start_cli(true), actions, "Проверяет план и сборку, ничего не меняя.")
	_add_button("3. Пересоздать сектор", func() -> void: _start_cli(false), actions, "Строит стены, полы и лестницы заново по плану и перезагружает сцену. Сначала сохраните сцену.")

	var result := _section("Результат")
	_status = _add_label("Статус: ещё не запускалось", result)
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_problems_label = _add_label("Проблем: —", result)
	_stage_label = _add_label("Этап: ожидание", result)
	_exit_label = _add_label("Код выхода: —", result)
	_details = TextEdit.new()
	_details.editable = false
	_details.context_menu_enabled = true
	_details.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_details.custom_minimum_size = Vector2(0, 120)
	_details.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_details.tooltip_text = "Текст можно выделять и копировать (Ctrl+C)."
	_details.visible = false
	result.add_child(_details)
	_copy_button = _add_button("Копировать ошибку", _copy_result, result, "Копирует статус и подробности в буфер обмена.")
	_copy_button.visible = false
	_add_button("Показать отчёт", _open_report, result, "Открывает полный отчёт последнего запуска.")
	_agent_fix_button = _add_button("Исправить агентом", _start_agent_fix, result, "Агент правит только авторские объекты и привязки, затем сектор проверяется заново.")
	_agent_fix_button.visible = false

	_binding_toggle = _add_button("Привязка объектов ▸", _toggle_binding, _content)
	_binding_toggle.toggle_mode = true
	_binding_container = VBoxContainer.new()
	_binding_container.visible = false
	_content.add_child(_binding_container)
	_add_label("Привязанные объекты (например, рамы дверей) двигаются вместе с якорем при пересоздании.", _binding_container, COLOR_MUTED).autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_add_button("Привязать двери по ID", _bind_doors_by_id, _binding_container, "Привязывает объекты-двери, у которых placement_id совпадает с data-handoff-id двери в SVG. По расстоянию и имени не ищет.")
	_add_label("Или выберите один объект и укажите ID якоря вручную:", _binding_container, COLOR_MUTED).autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_anchor_id = _add_field("ID якоря", "", _binding_container, "Например svg:u-route-a-door-hall-store:door:center")
	_add_button("Привязать выбранный", func() -> void: _binding_action("bind"), _binding_container)
	_add_button("Перепривязать выбранный", func() -> void: _binding_action("rebind"), _binding_container)
	_add_button("Отвязать выбранный", func() -> void: _binding_action("unbind"), _binding_container)

	var tools := _section("Инструменты")
	_toolchain_label = _add_label("Генераторы: проверка…", tools)
	_inkscape_label = _add_label("Inkscape: проверка…", tools)
	_agent_label = _add_label("Агент исправления: —", tools)
	_claude_button = _add_button("Выбрать агентом Claude", _use_claude_agent, tools, "Записывает лаунчер Claude Code. Claude CLI должен быть авторизован (команда claude, затем /login).")

	_settings_toggle = _add_button("Настройки ▸", _toggle_settings, _content)
	_settings_toggle.toggle_mode = true
	_settings_container = VBoxContainer.new()
	_settings_container.visible = false
	_content.add_child(_settings_container)
	_manifest = _add_field("Манифест секторов", str(ProjectSettings.get_setting(MANIFEST_SETTING, "")), _settings_container, "Список секторов и их исходных планов.")
	_python = _add_persistent_field("Python", "python_executable", _settings_container, "Интерпретатор Python 3.10+. Пусто — используется python из PATH.")
	_svg_root = _add_persistent_field("Папка svg-plan-to-godot", "svg_tool_root", _settings_container, "Папка установленного скилла svg-plan-to-godot (нужна версия 1.19.0 или новее).")
	_stair_root = _add_persistent_field("Папка generate-godot-stairs", "stair_tool_root", _settings_container, "Папка установленного скилла generate-godot-stairs (нужна версия 2.9.0 или новее).")
	_inkscape = _add_persistent_field("Inkscape (inkscape.exe)", "inkscape_executable", _settings_container, "Пусто — ищется в стандартных местах и в Microsoft Store.")
	_agent_launcher = _add_persistent_field("Запуск агента-исправителя", "agent_launcher", _settings_container, "JSON-массив: исполняемый файл и аргументы лаунчера.")
	var bottom_space := Control.new()
	bottom_space.custom_minimum_size = Vector2(0, 48)
	_content.add_child(bottom_space)
	_refresh_toolchain()


func _section(title: String) -> VBoxContainer:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 3)
	var header := Label.new()
	header.text = title
	header.add_theme_font_size_override("font_size", 15)
	header.add_theme_color_override("font_color", Color(0.80, 0.85, 0.95))
	box.add_child(header)
	box.add_child(HSeparator.new())
	_content.add_child(box)
	return box


func _add_label(text: String, parent: Container = null, color: Color = Color.WHITE) -> Label:
	var label := Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	if color != Color.WHITE:
		label.add_theme_color_override("font_color", color)
	(parent if parent != null else _content).add_child(label)
	return label


func _add_field(label_text: String, initial: String, parent: Container, tooltip: String = "") -> LineEdit:
	var label := Label.new()
	label.text = label_text
	label.tooltip_text = tooltip
	parent.add_child(label)
	var edit := LineEdit.new()
	edit.text = initial
	edit.tooltip_text = tooltip
	edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	parent.add_child(edit)
	if not tooltip.is_empty():
		_add_label(tooltip, parent, COLOR_MUTED)
	return edit


func _add_persistent_field(label_text: String, key: String, parent: Container, tooltip: String = "") -> LineEdit:
	var edit := _add_field(label_text, str(get_editor_interface().get_editor_settings().get_setting(SETTINGS_PREFIX + key)), parent, tooltip)
	edit.text_submitted.connect(func(_value: String) -> void: _save_setting(key, edit.text))
	edit.focus_exited.connect(func() -> void: _save_setting(key, edit.text))
	return edit


func _add_button(text: String, callback: Callable, parent: Container = null, tooltip: String = "") -> Button:
	var button := Button.new()
	button.text = text
	button.tooltip_text = tooltip
	button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	button.pressed.connect(callback)
	(parent if parent != null else _content).add_child(button)
	_buttons.append(button)
	return button


func _toggle_settings() -> void:
	_settings_container.visible = _settings_toggle.button_pressed
	_settings_toggle.text = "Настройки ▾" if _settings_toggle.button_pressed else "Настройки ▸"


func _toggle_binding() -> void:
	_binding_container.visible = _binding_toggle.button_pressed
	_binding_toggle.text = "Привязка объектов ▾" if _binding_toggle.button_pressed else "Привязка объектов ▸"


# --- Status display ---------------------------------------------------------------------


func _set_status(kind: String, text: String, details: PackedStringArray = PackedStringArray()) -> void:
	var icon := "…"
	var color := COLOR_MUTED
	match kind:
		"clean":
			icon = "✔"
			color = COLOR_OK
		"running":
			icon = "⏳"
			color = COLOR_WARNING
		"blocked":
			icon = "✖"
			color = COLOR_WARNING
		"failed":
			icon = "✖"
			color = COLOR_ERROR
	_status.text = "Статус: %s %s" % [icon, text]
	_status.add_theme_color_override("font_color", color)
	_details.visible = not details.is_empty()
	_copy_button.visible = not details.is_empty()
	_details.text = "\n".join(PackedStringArray(Array(details).map(func(line: String) -> String: return "• %s" % line)))


func _copy_result() -> void:
	DisplayServer.clipboard_set("%s\n%s" % [_status.text, _details.text])


func _stage_text(stage: String) -> String:
	return str(STAGE_NAMES.get(stage, stage))


# --- Settings and toolchain -------------------------------------------------------------


func _ensure_editor_settings() -> void:
	var settings := get_editor_interface().get_editor_settings()
	for key: String in PERSISTENT_SETTINGS:
		var full_key := SETTINGS_PREFIX + key
		if not settings.has_setting(full_key):
			settings.set_setting(full_key, "")
		settings.add_property_info({"name": full_key, "type": TYPE_STRING, "hint": PROPERTY_HINT_GLOBAL_FILE if key in FILE_SETTINGS else PROPERTY_HINT_GLOBAL_DIR})


func _apply_viewport_depth_range() -> void:
	var settings := get_editor_interface().get_editor_settings()
	if settings.has_setting("editors/3d/default_z_near") and is_equal_approx(float(settings.get_setting("editors/3d/default_z_near")), FACTORY_Z_NEAR):
		settings.set_setting("editors/3d/default_z_near", VIEWPORT_Z_NEAR)
	if settings.has_setting("editors/3d/default_z_far") and is_equal_approx(float(settings.get_setting("editors/3d/default_z_far")), FACTORY_Z_FAR):
		settings.set_setting("editors/3d/default_z_far", VIEWPORT_Z_FAR)


func _save_setting(key: String, value: String) -> void:
	get_editor_interface().get_editor_settings().set_setting(SETTINGS_PREFIX + key, value.strip_edges())
	_refresh_toolchain()


func _use_claude_agent() -> void:
	var launcher := _operations.claude_launcher_setting(_python.text)
	_agent_launcher.text = launcher
	_save_setting("agent_launcher", launcher)
	_set_status("clean", "Агент исправления: Claude", PackedStringArray(["Если при запуске будет «Not logged in», один раз выполните в терминале: claude, затем /login."]))


func _refresh_toolchain() -> void:
	if _operations == null or _toolchain_label == null:
		return
	_resolved_toolchain = _operations.resolve_toolchain({
		"python_executable": _python.text, "svg_tool_root": _svg_root.text,
		"stair_tool_root": _stair_root.text, "agent_launcher": _agent_launcher.text,
	})
	if bool(_resolved_toolchain.get("ok", false)):
		var versions := _resolved_toolchain.get("versions", {}) as Dictionary
		_toolchain_label.text = "Генераторы: ✔ готовы · SVG %s · лестницы %s" % [versions.get("svg_to_godot3d", "?"), versions.get("generate_godot_stairs", "?")]
		_toolchain_label.add_theme_color_override("font_color", COLOR_OK)
	else:
		_toolchain_label.text = "Генераторы: ✖ не готовы — %s. Укажите пути в «Настройки»." % "; ".join(_resolved_toolchain.get("errors", PackedStringArray()) as PackedStringArray)
		_toolchain_label.add_theme_color_override("font_color", COLOR_ERROR)
	var inkscape := _operations.resolve_inkscape(_inkscape.text)
	if bool(inkscape.get("ok", false)):
		_inkscape_label.text = "Inkscape: ✔ %s (%s)" % [str(inkscape["path"]).get_file(), inkscape["source"]]
		_inkscape_label.add_theme_color_override("font_color", COLOR_OK)
	else:
		_inkscape_label.text = "Inkscape: ✖ %s" % "; ".join(inkscape.get("errors", PackedStringArray()) as PackedStringArray)
		_inkscape_label.add_theme_color_override("font_color", COLOR_ERROR)
	var agent := _operations.agent_name(str(_resolved_toolchain.get("agent_launcher", _agent_launcher.text)))
	_agent_label.text = "Агент исправления: %s" % agent
	_agent_label.add_theme_color_override("font_color", COLOR_OK if agent != "не выбран" else COLOR_WARNING)
	if _agent_fix_button != null:
		_agent_fix_button.text = "Исправить агентом (%s)" % agent
	if _claude_button != null:
		_claude_button.visible = agent != "Claude"


# --- Scene context ----------------------------------------------------------------------


func _on_scene_changed(root: Node) -> void:
	if root != null and not root.scene_file_path.is_empty() and not _saved_versions.has(root.scene_file_path):
		_saved_versions[root.scene_file_path] = _history_version(root)
	_refresh_context()


func _on_scene_saved(path: String) -> void:
	var root := get_editor_interface().get_edited_scene_root()
	if root != null and root.scene_file_path == path:
		_saved_versions[path] = _history_version(root)
	_refresh_context()


func _history_version(root: Node) -> int:
	var manager := get_undo_redo()
	var history_id := manager.get_object_history_id(root)
	var history := manager.get_history_undo_redo(history_id)
	return history.get_version() if history != null else -1


func _metadata(root: Node) -> Dictionary:
	var result := {}
	if root == null:
		return result
	for key: String in ["sector_id"]:
		if root.has_meta(key):
			result[key] = root.get_meta(key)
	return result


func _context() -> Dictionary:
	var root := get_editor_interface().get_edited_scene_root()
	return _operations.resolve_sector(_metadata(root), root.scene_file_path if root != null else "", _manifest.text)


func _refresh_context() -> void:
	if _operations == null or _manifest == null:
		return
	var context := _context()
	if bool(context.get("ok", false)):
		_sector_label.text = "Сектор: %s" % context["sector_id"]
		_source_svg = str(context["source_svg"])
		_source_label.text = "План: %s" % _source_svg.get_file()
		_source_label.tooltip_text = _source_svg
		_hint_label.text = ""
	else:
		_sector_label.text = "Сектор: не определён"
		_source_label.text = "План: —"
		_source_label.tooltip_text = ""
		_source_svg = ""
		_hint_label.text = "Откройте сцену сектора (корень с метаданными sector_id)."


# --- Regeneration -----------------------------------------------------------------------


func _start_cli(validate_only: bool) -> void:
	if _running or _reload_in_progress:
		return
	_refresh_toolchain()
	if not bool(_resolved_toolchain.get("ok", false)):
		_show_errors(_resolved_toolchain.get("errors", PackedStringArray()) as PackedStringArray)
		return
	var root := get_editor_interface().get_edited_scene_root()
	if root == null:
		_show_errors(PackedStringArray(["нет открытой сцены"]))
		return
	if not validate_only:
		var saved_version := int(_saved_versions.get(root.scene_file_path, -1))
		if _operations.has_unsaved_authored_changes(root.scene_file_path, _history_version(root), saved_version):
			_show_errors(PackedStringArray(["Пересоздание заблокировано: сначала сохраните изменения сцены."]))
			return
	var context := _context()
	if not bool(context.get("ok", false)):
		_show_errors(context.get("errors", PackedStringArray()) as PackedStringArray)
		return
	_report_path = "user://map_pipeline_reports/%s-last.json" % str(context["sector_id"]).to_lower().replace("/", "-")
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(_report_path).get_base_dir())
	if FileAccess.file_exists(_report_path):
		DirAccess.remove_absolute(ProjectSettings.globalize_path(_report_path))
	var invocation := _operations.build_cli_invocation(context, {
		"python": str(_resolved_toolchain["python_executable"]), "manifest": _manifest.text, "report": _report_path,
		"svg_tool_root": str(_resolved_toolchain["svg_tool_root"]), "stair_tool_root": str(_resolved_toolchain["stair_tool_root"]),
	}, validate_only)
	if not bool(invocation.get("ok", false)):
		_show_errors(invocation.get("errors", PackedStringArray()) as PackedStringArray)
		return
	_active_context = context.duplicate(true)
	_active_action = "validate" if validate_only else "regenerate"
	_launch_invocation(invocation)


func _start_agent_fix() -> void:
	if _running or _reload_in_progress:
		return
	var report := _operations.read_report(_report_path)
	if not bool(report.get("ok", false)) or not bool(report.get("offer_agent_fix", false)):
		_show_errors(PackedStringArray(["Исправление агентом недоступно для этого отчёта."]))
		return
	_refresh_toolchain()
	if not bool(_resolved_toolchain.get("ok", false)):
		_show_errors(_resolved_toolchain.get("errors", PackedStringArray()) as PackedStringArray)
		return
	var context := _context()
	if not bool(context.get("ok", false)):
		_show_errors(context.get("errors", PackedStringArray()) as PackedStringArray)
		return
	var root := get_editor_interface().get_edited_scene_root()
	var saved_version := int(_saved_versions.get(root.scene_file_path, -1)) if root != null else -1
	if root == null or _operations.has_unsaved_authored_changes(root.scene_file_path, _history_version(root), saved_version):
		_show_errors(PackedStringArray(["Исправление заблокировано: сначала сохраните изменения сцены."]))
		return
	var invocation := _operations.build_agent_fix_invocation(context, {
		"python": str(_resolved_toolchain["python_executable"]),
		"manifest": _manifest.text,
		"agent_launcher": str(_resolved_toolchain["agent_launcher"]),
		"svg_tool_root": str(_resolved_toolchain["svg_tool_root"]),
		"stair_tool_root": str(_resolved_toolchain["stair_tool_root"]),
	}, _report_path)
	if not bool(invocation.get("ok", false)):
		_show_errors(invocation.get("errors", PackedStringArray()) as PackedStringArray)
		return
	_active_context = context.duplicate(true)
	_active_action = "agent_fix"
	_launch_invocation(invocation)


func _launch_invocation(invocation: Dictionary) -> void:
	_running = true
	_set_buttons_enabled(false)
	_agent_fix_button.visible = false
	_stage_label.text = "Этап: %s" % _stage_text(_active_action)
	_exit_label.text = "Код выхода: выполняется"
	_set_status("running", "выполняется…")
	_problems_label.text = "Проблем: —"
	_thread_result = {}
	_thread = Thread.new()
	_thread.start(_run_cli.bind(str(invocation["executable"]), invocation["arguments"] as PackedStringArray))


func _run_cli(executable: String, arguments: PackedStringArray) -> void:
	var output := []
	var exit_code := OS.execute(executable, arguments, output, true)
	_mutex.lock()
	_thread_result = {"exit_code": exit_code, "output": PackedStringArray(output)}
	_mutex.unlock()


func _finish_cli(exit_code: int, output: PackedStringArray) -> void:
	_exit_label.text = "Код выхода: %d" % exit_code
	var report := _operations.read_report(_report_path)
	if bool(report.get("ok", false)):
		_stage_label.text = "Этап: %s" % _stage_text(str(report["last_stage"]))
		_problems_label.text = "Проблем: %d" % int(report["problem_count"])
		var display := str(report["display_status"])
		if display == "Clean":
			_set_status("clean", "чисто — ошибок нет")
		elif display == "Blocked":
			_set_status("blocked", "заблокировано проверкой расстановки", PackedStringArray(["Подробности — в отчёте («Показать отчёт»)."]))
		else:
			_set_status("failed", "ошибка на этапе «%s»" % _stage_text(str(report["last_stage"])), PackedStringArray(["Подробности — в отчёте («Показать отчёт»)."]))
		_agent_fix_button.visible = bool(report.get("offer_agent_fix", false))
	else:
		_stage_label.text = "Этап: %s" % _stage_text("report_unavailable")
		_show_errors(report.get("errors", PackedStringArray()) as PackedStringArray, output)
	if _active_action != "validate" and _operations.should_reload(exit_code, report):
		_reload_in_progress = true
		_reload_promoted_sector()
	else:
		_set_buttons_enabled(true)


func _reload_promoted_sector() -> void:
	var filesystem := get_editor_interface().get_resource_filesystem()
	filesystem.scan()
	var frames := 0
	while filesystem.is_scanning() and frames < 600:
		await get_tree().process_frame
		frames += 1
	if filesystem.is_scanning():
		_finish_reload(PackedStringArray(["импорт ресурсов не завершился вовремя"]))
		return
	await get_tree().process_frame
	var root := get_editor_interface().get_edited_scene_root()
	if root == null or root.scene_file_path != str(_active_context.get("sector_scene", "")):
		_finish_reload(PackedStringArray(["во время пересоздания открыта другая сцена"]))
		return
	var errors := _replace_loaded_generated_resources(root, _active_context)
	if errors.is_empty() and root.has_method("rebuild_contract_generated"):
		errors.append_array(root.call("rebuild_contract_generated") as PackedStringArray)
	if not errors.is_empty():
		_finish_reload(errors)
		return
	var scene_path := root.scene_file_path
	get_editor_interface().reload_scene_from_path(scene_path)
	for _index: int in range(120):
		await get_tree().process_frame
		var reloaded := get_editor_interface().get_edited_scene_root()
		if reloaded != null and reloaded != root and reloaded.scene_file_path == scene_path:
			var controller := reloaded.get_node_or_null("AnchorController")
			if controller == null or not controller.has_method("apply_bindings"):
				errors.append("в перезагруженном секторе нет AnchorController")
			else:
				# Doors may have moved or been resized: re-seat their frames before applying bindings.
				var doors := _run_door_binding(reloaded, _active_context)
				if not bool(doors["ok"]):
					errors.append_array(doors["errors"] as PackedStringArray)
			_finish_reload(errors)
			return
	_finish_reload(PackedStringArray(["перезагрузка сцены сектора не завершилась вовремя"]))


func _replace_loaded_generated_resources(root: Node, context: Dictionary) -> PackedStringArray:
	var errors := PackedStringArray()
	var sector := context.get("sector", {}) as Dictionary
	var output := str(context.get("output_resource_dir", "")).trim_suffix("/")
	var architecture_path := "%s/Generated/Architecture/%s.tscn" % [output, str(sector.get("scene_name", ""))]
	var architecture := ResourceLoader.load(architecture_path, "PackedScene", ResourceLoader.CACHE_MODE_REPLACE) as PackedScene
	if architecture == null:
		errors.append("не удалось импортировать сгенерированную архитектуру: %s" % architecture_path)
	else:
		root.set("generated_architecture_scene", architecture)
	var vertical_value: Variant = sector.get("vertical_generators", [])
	if vertical_value is Array and not (vertical_value as Array).is_empty():
		var generator := (vertical_value as Array)[0] as Dictionary
		var generator_id := str(generator.get("generator_id", ""))
		var stair_path := "%s/Generated/Stairs/%s/%s.tscn" % [output, generator_id, generator_id]
		var stairs := ResourceLoader.load(stair_path, "PackedScene", ResourceLoader.CACHE_MODE_REPLACE) as PackedScene
		if stairs == null:
			errors.append("не удалось импортировать сгенерированные лестницы: %s" % stair_path)
		else:
			root.set("generated_stairs_scene", stairs)
	return errors


func _finish_reload(errors: PackedStringArray) -> void:
	_reload_in_progress = false
	if errors.is_empty():
		_set_status("clean", "чисто — сектор пересоздан и перезагружен")
		_problems_label.text = "Проблем: 0"
		var root := get_editor_interface().get_edited_scene_root()
		if root != null and not root.scene_file_path.is_empty():
			_saved_versions[root.scene_file_path] = _history_version(root)
	else:
		_set_status("failed", "не удалось применить результат", errors)
		_problems_label.text = "Проблем: %d" % errors.size()
		_agent_fix_button.visible = false
	_set_buttons_enabled(true)


func _set_buttons_enabled(enabled: bool) -> void:
	for button: Button in _buttons:
		button.disabled = not enabled


# --- Actions ----------------------------------------------------------------------------


func _open_source() -> void:
	_refresh_context()
	if _source_svg.is_empty() or not FileAccess.file_exists(_source_svg):
		_show_errors(PackedStringArray(["исходный SVG не определён или отсутствует — откройте сцену сектора"]))
		return
	var inkscape := _operations.resolve_inkscape(_inkscape.text)
	if not bool(inkscape.get("ok", false)):
		_show_errors(inkscape.get("errors", PackedStringArray()) as PackedStringArray)
		return
	var process_id := OS.create_process(str(inkscape["path"]), PackedStringArray([ProjectSettings.globalize_path(_source_svg)]))
	if process_id < 0:
		_show_errors(PackedStringArray(["не удалось запустить Inkscape: %s" % inkscape["path"]]))
		return
	_set_status("clean", "план открыт в Inkscape", PackedStringArray(["Править в Inkscape, сохранить файл, затем «Проверить» и «Пересоздать»."]))


func _open_report() -> void:
	if _report_path.is_empty() or not FileAccess.file_exists(_report_path):
		_show_errors(PackedStringArray(["отчёта пока нет — сначала выполните проверку или пересоздание"]))
		return
	OS.shell_open(ProjectSettings.globalize_path(_report_path))


func _bind_doors_by_id() -> void:
	var context := _context()
	var root := get_editor_interface().get_edited_scene_root()
	if not bool(context.get("ok", false)) or root == null:
		_show_errors(context.get("errors", PackedStringArray(["откройте сцену сектора"])) as PackedStringArray)
		return
	var outcome := _run_door_binding(root, context)
	if bool(outcome["ok"]):
		_set_status("clean", "привязано дверей: %d" % int(outcome["count"]), outcome["notes"] as PackedStringArray)
	else:
		_set_status("failed", "привязка дверей не выполнена", outcome["errors"] as PackedStringArray)


## Binds the sector's door objects to door anchors by ID, writes the bindings file and applies it.
func _run_door_binding(root: Node, context: Dictionary) -> Dictionary:
	var controller := root.get_node_or_null("AnchorController")
	var authored := root.get_node_or_null("AuthoredContent")
	if controller == null or authored == null:
		return {"ok": false, "count": 0, "errors": PackedStringArray(["в сцене нет AnchorController или AuthoredContent"]), "notes": PackedStringArray()}
	var objects: Array = []
	_collect_anchored_objects(authored, objects)
	var anchor_document: Variant = JSON.parse_string(FileAccess.get_file_as_string(str(controller.get("anchor_frames_path"))))
	if not anchor_document is Dictionary:
		return {"ok": false, "count": 0, "errors": PackedStringArray(["не прочитан файл якорей: %s" % str(controller.get("anchor_frames_path"))]), "notes": PackedStringArray()}
	var result := DOOR_BINDING_SCRIPT.build_bindings(
		objects, anchor_document as Dictionary, ProjectSettings.globalize_path(str(context["source_svg"])),
		str(controller.get("authored_scene_path")), str(context["sector_id"])
	)
	if not bool(result["ok"]):
		return {"ok": false, "count": 0, "errors": result["errors"] as PackedStringArray, "notes": PackedStringArray()}
	var bindings_path := str(controller.get("object_bindings_path"))
	var existing: Variant = JSON.parse_string(FileAccess.get_file_as_string(bindings_path))
	var merged := DOOR_BINDING_SCRIPT.merge_document(
		existing as Dictionary if existing is Dictionary else {},
		str((anchor_document as Dictionary).get("map_id", "")), str(context["sector_id"]), result["bindings"] as Array
	)
	var file := FileAccess.open(bindings_path, FileAccess.WRITE)
	if file == null:
		return {"ok": false, "count": 0, "errors": PackedStringArray(["не удалось записать файл привязок: %s" % bindings_path]), "notes": PackedStringArray()}
	file.store_string(JSON.stringify(merged, "  ") + "
")
	file.close()
	var notes := PackedStringArray(Array(result["skipped"]).map(func(line: Variant) -> String: return "пропущено: %s" % str(line)))
	var errors := controller.call("apply_bindings") as PackedStringArray
	return {"ok": errors.is_empty(), "count": (result["bindings"] as Array).size(), "errors": errors, "notes": notes}


func _collect_anchored_objects(node: Node, objects: Array) -> void:
	for child: Node in node.get_children():
		if child is AnchoredObject3D:
			objects.append(child)
		_collect_anchored_objects(child, objects)


func _binding_action(action: String) -> void:
	var selected := get_editor_interface().get_selection().get_selected_nodes()
	if selected.size() != 1 or not selected[0] is AnchoredObject3D:
		_show_errors(PackedStringArray(["выберите ровно один объект AnchoredObject3D"]))
		return
	var object := selected[0] as AnchoredObject3D
	if action == "unbind":
		_show_errors(_anchor_operations.unbind(get_undo_redo(), object))
		return
	var registry := object.anchor_registry
	if registry == null:
		registry = _single_registry(get_editor_interface().get_edited_scene_root())
	var placement := object.placement.duplicate(true) as MapAnchorPlacement if object.placement != null else MapAnchorPlacement.new()
	var errors := _anchor_operations.bind(get_undo_redo(), object, registry, _anchor_id.text.strip_edges(), placement) if action == "bind" else _anchor_operations.rebind(get_undo_redo(), object, registry, _anchor_id.text.strip_edges(), placement)
	_show_errors(errors)


func _single_registry(root: Node) -> MapAnchorRegistry:
	var registries: Array[MapAnchorRegistry] = []
	_collect_registries(root, registries)
	return registries[0] if registries.size() == 1 else null


func _collect_registries(node: Node, result: Array[MapAnchorRegistry]) -> void:
	if node == null:
		return
	if node is MapAnchorRegistry:
		result.append(node as MapAnchorRegistry)
	for child: Node in node.get_children():
		_collect_registries(child, result)


func _show_errors(errors: PackedStringArray, output: PackedStringArray = PackedStringArray()) -> void:
	if errors.is_empty():
		_set_status("clean", "чисто — ошибок нет")
		_problems_label.text = "Проблем: 0"
		return
	var lines := errors.duplicate()
	for line: String in output:
		if not line.strip_edges().is_empty():
			lines.append(line.strip_edges())
	_set_status("blocked", "действие заблокировано", lines)
	_problems_label.text = "Проблем: %d" % errors.size()
