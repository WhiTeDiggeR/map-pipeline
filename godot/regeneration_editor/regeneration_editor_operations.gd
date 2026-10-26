@tool
extends RefCounted
class_name MapRegenerationEditorOperations

const REPORT_SCHEMA_ID := "caretaker.safe_regeneration_report"
const REPORT_SCHEMA_VERSION := "1.0.0"
const CLEAN_STATUSES := ["success", "noop", "validated"]
const MIN_SVG_VERSION := [1, 19, 0]
const MIN_STAIR_VERSION := [2, 9, 0]
const REPAIR_QUEUE_SCHEMA_ID := "caretaker.repair_queue"
const AGENT_FIX_CODES := [
	"missing_anchor",
	"outside_sector", "outside_space", "wrong_sector",
	"object_overlap", "authored_overlap", "authored_object_overlap", "collision_overlap",
	"wall_penetration", "floor_penetration", "ceiling_penetration",
	"door_blockage", "passage_blockage", "stair_conflict", "shaft_conflict",
	"support_missing", "wall_mount_out_of_bounds", "mount_invalid",
]
const ENVIRONMENT_KEYS := {
	"python_executable": "MAP_PIPELINE_PYTHON_EXECUTABLE",
	"svg_tool_root": "MAP_PIPELINE_SVG_TOOL_ROOT",
	"stair_tool_root": "MAP_PIPELINE_STAIR_TOOL_ROOT",
	"agent_launcher": "MAP_PIPELINE_AGENT_LAUNCHER",
}
const INKSCAPE_ENVIRONMENT_KEY := "MAP_PIPELINE_INKSCAPE"
const BUNDLED_GENERATORS := "res://addons/map_pipeline/generators"
const CLAUDE_LAUNCHER_SCRIPT := "res://addons/map_pipeline/pipeline/repair_package/claude_agent_launcher.py"
const STORE_INKSCAPE_PREFIX := "25415Inkscape.Inkscape_"
const STORE_INKSCAPE_RELATIVE := "VFS/ProgramFilesX64/Inkscape/bin/inkscape.exe"

var _store_inkscape_checked := false
var _store_inkscape_path := ""


func load_json_object(path: String) -> Dictionary:
	var text := FileAccess.get_file_as_string(path)
	if text.is_empty() and not FileAccess.file_exists(path):
		return {"ok": false, "errors": PackedStringArray(["file is missing: %s" % path])}
	var value: Variant = JSON.parse_string(text)
	if not value is Dictionary:
		return {"ok": false, "errors": PackedStringArray(["JSON root must be an object: %s" % path])}
	return {"ok": true, "value": value as Dictionary, "errors": PackedStringArray()}


func resolve_sector(metadata: Dictionary, scene_path: String, manifest_path: String) -> Dictionary:
	var loaded := load_json_object(manifest_path)
	if not bool(loaded.get("ok", false)):
		return loaded
	var manifest := loaded["value"] as Dictionary
	var sectors_value: Variant = manifest.get("sectors")
	if not sectors_value is Array:
		return _blocked("manifest sectors must be an array")
	var declared_ids: Array[String] = []
	for key: String in ["sector_id"]:
		var value := str(metadata.get(key, "")).strip_edges()
		if not value.is_empty() and value not in declared_ids:
			declared_ids.append(value)
	if declared_ids.size() > 1:
		return _blocked("conflicting sector metadata: %s" % ", ".join(declared_ids))
	var path_matches: Array[String] = []
	var normalized_scene := scene_path.replace("\\", "/")
	for sector_value: Variant in sectors_value:
		if not sector_value is Dictionary:
			continue
		var sector := sector_value as Dictionary
		var sector_id := str(sector.get("sector_id", ""))
		var output_root := str(sector.get("output_resource_dir", "")).trim_suffix("/")
		if not sector_id.is_empty() and not output_root.is_empty() and (normalized_scene == output_root or normalized_scene.begins_with(output_root + "/")):
			path_matches.append(sector_id)
	if path_matches.size() > 1:
		return _blocked("scene path matches multiple sector outputs")
	var resolved_id := declared_ids[0] if declared_ids.size() == 1 else (path_matches[0] if path_matches.size() == 1 else "")
	if resolved_id.is_empty():
		return _blocked("sector ID is missing; set sector_id metadata or open a scene under one manifest output")
	if declared_ids.size() == 1 and path_matches.size() == 1 and declared_ids[0] != path_matches[0]:
		return _blocked("sector metadata conflicts with the scene output path")
	var matches: Array[Dictionary] = []
	for sector_value: Variant in sectors_value:
		if sector_value is Dictionary and str((sector_value as Dictionary).get("sector_id", "")) == resolved_id:
			matches.append(sector_value as Dictionary)
	if matches.size() != 1:
		return _blocked("unknown or duplicate sector ID: %s" % resolved_id)
	var sector := matches[0]
	var source_svg := _project_path(manifest_path, str(manifest.get("project_root", ".")), str(sector.get("source_svg", "")))
	return {
		"ok": true,
		"errors": PackedStringArray(),
		"sector_id": resolved_id,
		"sector": sector,
		"source_svg": source_svg,
		"output_resource_dir": str(sector.get("output_resource_dir", "")),
		"sector_scene": str(sector.get("sector_scene", "")),
	}


func build_cli_invocation(context: Dictionary, settings: Dictionary, validate_only: bool) -> Dictionary:
	if not bool(context.get("ok", false)):
		return context
	var python := str(settings.get("python", "")).strip_edges()
	var manifest := str(settings.get("manifest", "")).strip_edges()
	var report := str(settings.get("report", "")).strip_edges()
	if python.is_empty() or manifest.is_empty() or report.is_empty():
		return _blocked("python, manifest and report paths are required")
	var backend := str(settings.get("backend", "res://addons/map_pipeline/pipeline/regenerate_sector.py"))
	var validator := str(settings.get("composition_validator", "res://addons/map_pipeline/pipeline/composition_validator/validate_composition.py"))
	var arguments := PackedStringArray([
		_globalize(str(settings.get("orchestrator", "res://addons/map_pipeline/pipeline/safe_regenerate.py"))),
		"--sector", str(context["sector_id"]),
		"--manifest", _globalize(manifest),
		"--backend", _globalize(backend),
		"--composition-validator", _globalize(validator),
		"--python", python,
		"--report", _globalize(report),
	])
	var svg_root := str(settings.get("svg_tool_root", "")).strip_edges()
	var stair_root := str(settings.get("stair_tool_root", "")).strip_edges()
	if not svg_root.is_empty():
		arguments.append_array(PackedStringArray(["--svg-tool-root", _globalize(svg_root)]))
	if not stair_root.is_empty():
		arguments.append_array(PackedStringArray(["--stair-tool-root", _globalize(stair_root)]))
	if validate_only:
		arguments.append("--validate-only")
	return {"ok": true, "errors": PackedStringArray(), "executable": python, "arguments": arguments, "report": _globalize(report)}


func build_agent_fix_invocation(context: Dictionary, settings: Dictionary, safe_report: String) -> Dictionary:
	if not bool(context.get("ok", false)):
		return context
	var python := str(settings.get("python", "")).strip_edges()
	var manifest := str(settings.get("manifest", "")).strip_edges()
	var launcher := str(settings.get("agent_launcher", "")).strip_edges()
	var runner := str(settings.get("agent_runner", "res://addons/map_pipeline/pipeline/repair_package/run_agent_fix.py"))
	if python.is_empty() or manifest.is_empty() or safe_report.is_empty() or launcher.is_empty():
		return _blocked("python, manifest, safe report and agent launcher are required")
	if not FileAccess.file_exists(runner):
		return _blocked("Agent Fix runner is not installed yet: %s" % runner)
	var arguments := PackedStringArray([
		_globalize(runner),
		"--sector", str(context["sector_id"]),
		"--manifest", _globalize(manifest),
		"--safe-report", _globalize(safe_report),
		"--python", python,
		"--agent-launcher", launcher,
	])
	for setting_key: String in ["svg_tool_root", "stair_tool_root"]:
		var value := str(settings.get(setting_key, "")).strip_edges()
		if not value.is_empty():
			arguments.append_array(PackedStringArray(["--%s" % setting_key.replace("_", "-"), _globalize(value)]))
	return {"ok": true, "errors": PackedStringArray(), "executable": python, "arguments": arguments}


func resolve_toolchain(configured: Dictionary) -> Dictionary:
	var result := {}
	var sources := {}
	for key: String in ENVIRONMENT_KEYS:
		var value := str(configured.get(key, "")).strip_edges()
		var source := "EditorSettings"
		if value.is_empty():
			value = OS.get_environment(str(ENVIRONMENT_KEYS[key])).strip_edges()
			source = "environment"
		if value.is_empty():
			value = _installed_default(key)
			source = "installed"
		result[key] = value
		sources[key] = source if not value.is_empty() else "missing"
	var errors := PackedStringArray()
	if str(result["python_executable"]).is_empty():
		errors.append("Python is not configured")
	if str(result["svg_tool_root"]).is_empty():
		errors.append("svg-plan-to-godot is not configured: set SVG tool root in Settings or MAP_PIPELINE_SVG_TOOL_ROOT (requires svg_to_godot3d >= 1.19.0)")
	if str(result["stair_tool_root"]).is_empty():
		errors.append("generate-godot-stairs is not configured: set Stair tool root in Settings or MAP_PIPELINE_STAIR_TOOL_ROOT (requires generate_godot_stairs >= 2.9.0)")
	var versions := {}
	if errors.is_empty():
		var python := str(result["python_executable"])
		var svg_script := _find_tool_script(str(result["svg_tool_root"]), "inspect_svg_plan.py")
		var stair_script := _find_tool_script(str(result["stair_tool_root"]), "generate_godot_stairs.py")
		if svg_script.is_empty():
			errors.append("inspect_svg_plan.py is missing under configured SVG tool root")
		else:
			_probe_version(python, svg_script, MIN_SVG_VERSION, "svg_to_godot3d", versions, errors)
		if stair_script.is_empty():
			errors.append("generate_godot_stairs.py is missing under configured stair tool root")
		else:
			_probe_version(python, stair_script, MIN_STAIR_VERSION, "generate_godot_stairs", versions, errors)
	result["sources"] = sources
	result["versions"] = versions
	result["errors"] = errors
	result["ok"] = errors.is_empty()
	return result

## Resolves the Inkscape executable: EditorSettings, then MAP_PIPELINE_INKSCAPE, then the
## standard Windows install locations. `source` tells the user where the path came from.
func resolve_inkscape(configured: String) -> Dictionary:
	var value := configured.strip_edges()
	if not value.is_empty():
		return _inkscape_result(value, "настройки", true)
	value = OS.get_environment(INKSCAPE_ENVIRONMENT_KEY).strip_edges()
	if not value.is_empty():
		return _inkscape_result(value, "переменная %s" % INKSCAPE_ENVIRONMENT_KEY, true)
	var program_files := OS.get_environment("ProgramFiles").strip_edges()
	var local_app_data := OS.get_environment("LOCALAPPDATA").strip_edges()
	var candidates: Array[String] = []
	if not program_files.is_empty():
		candidates.append(program_files.path_join("Inkscape/bin/inkscape.exe"))
	if not local_app_data.is_empty():
		candidates.append(local_app_data.path_join("Programs/Inkscape/bin/inkscape.exe"))
	for candidate: String in candidates:
		if FileAccess.file_exists(candidate):
			return _inkscape_result(candidate, "стандартная установка", false)
	var store := _store_inkscape(program_files)
	if not store.is_empty():
		return _inkscape_result(store, "установка из Microsoft Store", false)
	return {
		"ok": false, "path": "", "source": "missing",
		"errors": PackedStringArray(["Inkscape не найден: укажите путь к inkscape.exe в настройках или в %s" % INKSCAPE_ENVIRONMENT_KEY]),
	}


func _inkscape_result(path: String, source: String, must_exist: bool) -> Dictionary:
	if must_exist and not FileAccess.file_exists(path):
		return {"ok": false, "path": path, "source": source, "errors": PackedStringArray(["Inkscape не найден по пути (%s): %s" % [source, path]])}
	return {"ok": true, "path": path, "source": source, "errors": PackedStringArray()}


func _store_inkscape(program_files: String) -> String:
	if _store_inkscape_checked:
		return _store_inkscape_path
	_store_inkscape_checked = true
	# The package folder name contains the version, so ask Windows for the install location.
	var output := []
	if OS.get_name() == "Windows" and OS.execute("powershell", PackedStringArray([
		"-NoProfile", "-NonInteractive", "-Command",
		"(Get-AppxPackage -Name 25415Inkscape.Inkscape | Select-Object -First 1).InstallLocation",
	]), output, true) == 0 and not output.is_empty():
		var location := str(output[0]).strip_edges()
		var candidate := location.path_join(STORE_INKSCAPE_RELATIVE)
		if not location.is_empty() and FileAccess.file_exists(candidate):
			_store_inkscape_path = candidate
			return _store_inkscape_path
	var apps := program_files.path_join("WindowsApps") if not program_files.is_empty() else ""
	var directory := DirAccess.open(apps) if not apps.is_empty() else null
	if directory != null:
		var versions: Array[String] = []
		for entry: String in directory.get_directories():
			if entry.begins_with(STORE_INKSCAPE_PREFIX):
				versions.append(entry)
		versions.sort()
		versions.reverse()
		for entry: String in versions:
			var fallback := apps.path_join(entry).path_join(STORE_INKSCAPE_RELATIVE)
			if FileAccess.file_exists(fallback):
				_store_inkscape_path = fallback
				break
	return _store_inkscape_path


## JSON-array launcher setting that makes Claude Code the Agent Fix agent. `claude` itself is
## resolved from PATH by the launcher, so no executable location is guessed here.
func claude_launcher_setting(python: String) -> String:
	var interpreter := python.strip_edges() if not python.strip_edges().is_empty() else "python"
	return JSON.stringify([interpreter, _globalize(CLAUDE_LAUNCHER_SCRIPT)])


func agent_name(launcher: String) -> String:
	var lowered := launcher.to_lower()
	if lowered.is_empty():
		return "не выбран"
	if lowered.contains("claude"):
		return "Claude"
	if lowered.contains("codex"):
		return "Codex"
	return "свой запуск"



func _installed_default(key: String) -> String:
	if key == "python_executable":
		return "python"
	# The generators bundled with map-pipeline (next to this plugin) are the default; EditorSettings or the
	# MAP_PIPELINE_* environment variables override them.
	var bundled := {"svg_tool_root": "svg-plan-to-godot", "stair_tool_root": "generate-godot-stairs"}
	if bundled.has(key):
		var root := ProjectSettings.globalize_path(BUNDLED_GENERATORS.path_join(str(bundled[key])))
		return root if DirAccess.dir_exists_absolute(root) else ""
	return ""


func _find_tool_script(root: String, script_name: String) -> String:
	for candidate: String in [root.path_join("scripts").path_join(script_name), root.path_join(script_name)]:
		if FileAccess.file_exists(candidate):
			return candidate
	return ""


func _probe_version(python: String, script: String, minimum: Array, label: String, versions: Dictionary, errors: PackedStringArray) -> void:
	var output := []
	var exit_code := OS.execute(python, PackedStringArray([script, "--version"]), output, true)
	if exit_code != 0:
		errors.append("%s version probe failed" % label)
		return
	var regex := RegEx.new()
	regex.compile("(\\d+)\\.(\\d+)\\.(\\d+)")
	var match := regex.search("\n".join(output))
	if match == null:
		errors.append("%s did not report a semantic version" % label)
		return
	var parsed := [int(match.get_string(1)), int(match.get_string(2)), int(match.get_string(3))]
	versions[label] = match.get_string(0)
	if _version_less(parsed, minimum):
		errors.append("%s %s is older than required %s.%s.%s" % [label, match.get_string(0), minimum[0], minimum[1], minimum[2]])


func _version_less(left: Array, right: Array) -> bool:
	for index: int in range(3):
		if int(left[index]) != int(right[index]):
			return int(left[index]) < int(right[index])
	return false


func read_report(path: String) -> Dictionary:
	var loaded := load_json_object(path)
	if not bool(loaded.get("ok", false)):
		return loaded
	var report := loaded["value"] as Dictionary
	if report.get("schema_id") != REPORT_SCHEMA_ID or report.get("schema_version") != REPORT_SCHEMA_VERSION:
		return _blocked("unsupported safe regeneration report schema")
	var stages_value: Variant = report.get("stages", [])
	var last_stage := "none"
	if stages_value is Array and not stages_value.is_empty() and stages_value[-1] is Dictionary:
		last_stage = str((stages_value[-1] as Dictionary).get("stage", "none"))
	var failed_stage := _failed_stage(report)
	var repair := _repair_summary(report)
	var ready := bool(report.get("ready", false))
	var status := str(report.get("status", "unknown"))
	var display_status := "Clean" if ready and status in CLEAN_STATUSES else ("Blocked" if failed_stage == "composition_validation" else "Failed")
	var report_errors: Array = []
	if report.get("errors", []) is Array:
		report_errors = report.get("errors", []) as Array
	var problem_count := int(repair.get("blocking_count", report_errors.size()))
	return {
		"ok": true,
		"errors": PackedStringArray(),
		"report": report,
		"status": status,
		"display_status": display_status,
		"last_stage": last_stage,
		"failed_stage": failed_stage,
		"ready": ready,
		"problem_count": problem_count,
		"repair_queue_path": str(repair.get("path", "")),
		"offer_agent_fix": failed_stage == "composition_validation" and bool(repair.get("agent_fix_allowed", false)),
	}


func _failed_stage(report: Dictionary) -> String:
	var errors_value: Variant = report.get("errors", [])
	if errors_value is Array:
		for index: int in range((errors_value as Array).size() - 1, -1, -1):
			var item: Variant = (errors_value as Array)[index]
			if item is Dictionary and not str((item as Dictionary).get("stage", "")).is_empty():
				return str((item as Dictionary)["stage"])
	var stages_value: Variant = report.get("stages", [])
	if stages_value is Array:
		for index: int in range((stages_value as Array).size() - 1, -1, -1):
			var stage: Variant = (stages_value as Array)[index]
			if stage is Dictionary and str((stage as Dictionary).get("status", "")) == "failed":
				return str((stage as Dictionary).get("stage", ""))
	return ""


func _repair_summary(report: Dictionary) -> Dictionary:
	var artifacts_value: Variant = report.get("validation_artifacts", {})
	if not artifacts_value is Dictionary:
		return {"blocking_count": 0, "agent_fix_allowed": false, "path": ""}
	var artifact_value: Variant = (artifacts_value as Dictionary).get("repair_queue.json")
	if not artifact_value is Dictionary:
		return {"blocking_count": 0, "agent_fix_allowed": false, "path": ""}
	var path := str((artifact_value as Dictionary).get("path", ""))
	if path.is_empty():
		return {"blocking_count": 0, "agent_fix_allowed": false, "path": ""}
	var loaded := load_json_object(path)
	if not bool(loaded.get("ok", false)):
		return {"blocking_count": 0, "agent_fix_allowed": false, "path": path}
	var queue := loaded["value"] as Dictionary
	if queue.get("schema_id") != REPAIR_QUEUE_SCHEMA_ID or queue.get("schema_version") != REPORT_SCHEMA_VERSION:
		return {"blocking_count": 0, "agent_fix_allowed": false, "path": path}
	if str(queue.get("sector_id", "")) != str(report.get("sector_id", "")):
		return {"blocking_count": 0, "agent_fix_allowed": false, "path": path}
	var items_value: Variant = queue.get("items", [])
	if not items_value is Array:
		return {"blocking_count": 0, "agent_fix_allowed": false, "path": path}
	var open_items: Array[Dictionary] = []
	for value: Variant in items_value:
		if value is Dictionary and str((value as Dictionary).get("severity", "")) == "blocking" and str((value as Dictionary).get("status", "")) == "open":
			open_items.append(value as Dictionary)
	var allowed := not open_items.is_empty()
	for item: Dictionary in open_items:
		var evidence_value: Variant = item.get("evidence")
		if not evidence_value is Dictionary:
			allowed = false
			break
		var evidence := evidence_value as Dictionary
		var owner := str(evidence.get("responsible_owner", ""))
		var actions_value: Variant = item.get("allowed_actions", [])
		if str(item.get("code", "")) not in AGENT_FIX_CODES or owner not in ["authored_content", "authored_bindings"] or str(item.get("object_id", "")).is_empty() or not actions_value is Array or (actions_value as Array).is_empty():
			allowed = false
			break
	return {"blocking_count": open_items.size(), "agent_fix_allowed": allowed, "path": path}


func has_unsaved_authored_changes(scene_path: String, current_version: int, saved_version: int) -> bool:
	return scene_path.is_empty() or saved_version < 0 or current_version != saved_version


func should_reload(exit_code: int, report_result: Dictionary) -> bool:
	return exit_code == 0 and bool(report_result.get("ok", false)) and bool(report_result.get("ready", false)) and str(report_result.get("status", "")) in CLEAN_STATUSES


func _project_path(manifest_path: String, project_root: String, relative: String) -> String:
	var manifest_absolute := _globalize(manifest_path)
	var absolute := manifest_absolute.get_base_dir().path_join(project_root).path_join(relative).simplify_path()
	return ProjectSettings.localize_path(absolute)


func _globalize(path: String) -> String:
	return ProjectSettings.globalize_path(path) if path.begins_with("res://") or path.begins_with("user://") else path


func _blocked(message: String) -> Dictionary:
	return {"ok": false, "errors": PackedStringArray([message])}
