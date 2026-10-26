extends SceneTree

const OPERATIONS_SCRIPT := preload("res://addons/map_pipeline/godot/regeneration_editor/regeneration_editor_operations.gd")


func _init() -> void:
	var operations: RefCounted = OPERATIONS_SCRIPT.new()
	if not OS.get_environment("MAP_PIPELINE_SVG_TOOL_ROOT").is_empty():
		var toolchain: Dictionary = operations.resolve_toolchain({})
		_assert(bool(toolchain.get("ok", false)), "configured toolchain was not resolved: %s" % "; ".join(toolchain.get("errors", PackedStringArray()) as PackedStringArray))
	var manifest := "res://addons/map_pipeline/godot/regeneration_editor/fixtures/editor_manifest.json"
	var fixture_scene := load("res://addons/map_pipeline/godot/regeneration_editor/fixtures/fixture_scene.tscn") as PackedScene
	var fixture_root := fixture_scene.instantiate()
	var metadata_context: Dictionary = operations.resolve_sector({"sector_id": fixture_root.get_meta("sector_id")}, "res://outside.tscn", manifest)
	fixture_root.free()
	_assert(bool(metadata_context.get("ok", false)), "metadata sector resolution failed")
	_assert(metadata_context.get("source_svg") == "res://addons/map_pipeline/godot/regeneration_editor/fixtures/source.svg", "source path resolution failed")
	var path_context: Dictionary = operations.resolve_sector({}, "res://addons/map_pipeline/godot/regeneration_editor/fixtures/live/generated.tscn", manifest)
	_assert(bool(path_context.get("ok", false)), "manifest path sector resolution failed")
	var conflict: Dictionary = operations.resolve_sector({"sector_id": "OTHER"}, "res://addons/map_pipeline/godot/regeneration_editor/fixtures/live/generated.tscn", manifest)
	_assert(not bool(conflict.get("ok", true)), "conflicting metadata was accepted")
	var invocation: Dictionary = operations.build_cli_invocation(metadata_context, {
		"python": "python-fixture", "manifest": manifest, "report": "user://fixture report.json",
		"svg_tool_root": "C:/SVG Tool", "stair_tool_root": "",
	}, true)
	_assert(bool(invocation.get("ok", false)), "CLI invocation was not built")
	var arguments := invocation.get("arguments", PackedStringArray()) as PackedStringArray
	_assert("--validate-only" in arguments and "--sector" in arguments and "FIXTURE-EDITOR" in arguments, "CLI arguments are incomplete")
	var report: Dictionary = operations.read_report("res://addons/map_pipeline/godot/regeneration_editor/fixtures/clean_report.json")
	_assert(bool(report.get("ok", false)) and report.get("last_stage") == "atomic_promotion", "report summary failed")
	_assert(not bool(report.get("offer_agent_fix", true)), "clean result offered Agent Fix")
	_assert(report.get("display_status") == "Clean" and int(report.get("problem_count", -1)) == 0, "clean status summary failed")
	_assert(operations.should_reload(0, report), "clean exit did not request reload")
	_assert(not operations.should_reload(2, report), "failed exit requested reload")
	var allowed_blocked: Dictionary = operations.read_report("res://addons/map_pipeline/godot/regeneration_editor/fixtures/allowed_blocked_report.json")
	_assert(bool(allowed_blocked.get("offer_agent_fix", false)), "authored missing-anchor blocker did not offer Agent Fix")
	_assert(allowed_blocked.get("display_status") == "Blocked" and int(allowed_blocked.get("problem_count", 0)) == 1, "blocked status summary failed")
	var agent_invocation: Dictionary = operations.build_agent_fix_invocation(metadata_context, {
		"python": "python-fixture", "manifest": manifest, "agent_launcher": "codex-fixture",
		"svg_tool_root": "C:/SVG Tool", "stair_tool_root": "C:/Stair Tool",
	}, "res://addons/map_pipeline/godot/regeneration_editor/fixtures/allowed_blocked_report.json")
	_assert(bool(agent_invocation.get("ok", false)), "Agent Fix runner invocation was not built")
	var agent_arguments := agent_invocation.get("arguments", PackedStringArray()) as PackedStringArray
	_assert("--agent-launcher" in agent_arguments and "codex-fixture" in agent_arguments, "Agent Fix runner arguments are incomplete")
	var denied_blocked: Dictionary = operations.read_report("res://addons/map_pipeline/godot/regeneration_editor/fixtures/denied_blocked_report.json")
	_assert(not bool(denied_blocked.get("offer_agent_fix", true)), "generated blocker offered Agent Fix")
	var invalid_source: Dictionary = operations.read_report("res://addons/map_pipeline/godot/regeneration_editor/fixtures/invalid_source_report.json")
	_assert(invalid_source.get("display_status") == "Failed", "invalid SVG was not classified as Failed")
	_assert(not bool(invalid_source.get("offer_agent_fix", true)), "invalid SVG offered Agent Fix")
	_assert(operations.has_unsaved_authored_changes("res://fixture.tscn", 6, 5), "unsaved version was not blocked")
	_assert(not operations.has_unsaved_authored_changes("res://fixture.tscn", 5, 5), "saved version was blocked")
	var no_inkscape: Dictionary = operations.resolve_inkscape("C:/definitely/missing/inkscape.exe")
	_assert(not bool(no_inkscape.get("ok", true)) and no_inkscape.get("source") == "настройки", "missing configured Inkscape was accepted")
	var inkscape_here: Dictionary = operations.resolve_inkscape(OS.get_executable_path())
	_assert(bool(inkscape_here.get("ok", false)) and inkscape_here.get("path") == OS.get_executable_path(), "existing configured Inkscape path was rejected")
	var claude_setting: String = operations.claude_launcher_setting("python-fixture")
	var claude_parts: Variant = JSON.parse_string(claude_setting)
	_assert(claude_parts is Array and (claude_parts as Array).size() == 2 and str((claude_parts as Array)[0]) == "python-fixture", "Claude launcher setting is not a JSON array")
	_assert(str((claude_parts as Array)[1]).ends_with("claude_agent_launcher.py"), "Claude launcher script is wrong")
	_assert(operations.agent_name(claude_setting) == "Claude" and operations.agent_name("") == "не выбран" and operations.agent_name("[\"codex\"]") == "Codex", "agent name detection failed")
	print("MAP_REGENERATION_EDITOR_CHECK_OK")
	quit(0)


func _assert(condition: bool, message: String) -> void:
	if condition:
		return
	push_error(message)
	quit(1)
