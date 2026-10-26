#!/usr/bin/env python3
"""Build a minimal deterministic agent repair package without invoking an agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence


VERSION = "1.0.0"
SCHEMA_ID = "caretaker.agent_repair_package"
SCHEMA_VERSION = "1.0.0"


class PackageError(ValueError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a minimal map repair package")
    parser.add_argument("--composition-report", required=True, type=Path)
    parser.add_argument("--repair-queue", required=True, type=Path)
    parser.add_argument("--composition-input", required=True, type=Path)
    parser.add_argument("--bindings", required=True, type=Path)
    parser.add_argument("--anchor-frames", required=True, type=Path)
    parser.add_argument("--authored-scene", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--object-id", action="append", default=[])
    parser.add_argument("--allow-file", action="append", default=[])
    parser.add_argument("--validate-command-json", required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"{label} root must be an object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def require_schema(document: dict[str, Any], schema_id: str, label: str) -> None:
    if document.get("schema_id") != schema_id or document.get("schema_version") != "1.0.0":
        raise PackageError(f"Unsupported {label} schema")


def require_same_context(documents: Sequence[dict[str, Any]]) -> dict[str, str]:
    keys = ("map_id", "sector_id")
    result: dict[str, str] = {}
    for key in keys:
        values = {document.get(key) for document in documents}
        if len(values) != 1 or not all(isinstance(value, str) and value for value in values):
            raise PackageError(f"Input documents disagree on {key}")
        result[key] = str(next(iter(values)))
    generation_documents = [document for document in documents if document.get("schema_id") != "caretaker.object_bindings"]
    generations = {document.get("generation_id") for document in generation_documents}
    if len(generations) != 1 or not all(isinstance(value, str) and value for value in generations):
        raise PackageError("Input documents disagree on generation_id")
    result["generation_id"] = str(next(iter(generations)))
    return result


def index_unique(items: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PackageError(f"{label} must be an object array")
    result: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for item in items:
        identity = item.get(key)
        if not isinstance(identity, str) or not identity:
            raise PackageError(f"{label} entry has no {key}")
        if identity in result:
            duplicates.add(identity)
        result[identity] = item
    if duplicates:
        raise PackageError(f"Duplicate {key} in {label}: {', '.join(sorted(duplicates))}")
    return result


def selected_repair_items(queue: dict[str, Any], requested: Sequence[str]) -> tuple[list[str], list[dict[str, Any]]]:
    items = queue.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PackageError("Repair queue items must be an object array")
    open_blocking = [item for item in items if item.get("status") == "open" and item.get("severity") == "blocking"]
    repair_ids = [item.get("repair_id") for item in open_blocking]
    if any(not isinstance(value, str) or not value for value in repair_ids) or len(repair_ids) != len(set(repair_ids)):
        raise PackageError("Open repair IDs must be present and unique")
    by_object: dict[str, list[dict[str, Any]]] = {}
    for item in open_blocking:
        object_id = item.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise PackageError("Open blocking repair item has no object_id")
        by_object.setdefault(object_id, []).append(item)
    requested_ids = sorted(set(requested)) if requested else sorted(by_object)
    if not requested_ids:
        raise PackageError("Repair queue has no open blocking object IDs")
    unknown = sorted(set(requested_ids) - set(by_object))
    if unknown:
        raise PackageError("Requested object IDs are not open blocking repairs: " + ", ".join(unknown))
    selected = [item for object_id in requested_ids for item in by_object[object_id]]
    return requested_ids, sorted(selected, key=lambda item: str(item["repair_id"]))


def resolve_allow_path(value: str, project_root: Path) -> tuple[str, Path]:
    if value.startswith("res://"):
        normalized = value
        path = (project_root / value.removeprefix("res://")).resolve()
    else:
        path = Path(value).resolve()
        try:
            normalized = "res://" + path.relative_to(project_root).as_posix()
        except ValueError as exc:
            raise PackageError(f"Allowlisted path is outside project root: {value}") from exc
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise PackageError(f"Allowlisted path escapes project root: {value}") from exc
    if not path.is_file():
        raise PackageError(f"Allowlisted file is missing: {value}")
    return normalized, path


def parse_validation_command(value: str) -> list[str]:
    try:
        command = json.loads(value)
    except json.JSONDecodeError as exc:
        raise PackageError(f"validate command must be a JSON string array: {exc}") from exc
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise PackageError("validate command must be a non-empty JSON string array")
    return command


def render_prompt(template: str, package: dict[str, Any]) -> str:
    object_ids = "\n".join(f"- `{value}`" for value in package["object_ids"])
    allowed_actions = "\n".join(f"- `{value}`" for value in package["allowed_actions"])
    writable = "\n".join(f"- `{item['path']}`" for item in package["file_policy"]["writable"])
    validate = json.dumps(package["validation_command"], ensure_ascii=False)
    replacements = {
        "{{SECTOR_ID}}": package["sector_id"],
        "{{OBJECT_IDS}}": object_ids,
        "{{ALLOWED_ACTIONS}}": allowed_actions,
        "{{WRITABLE_FILES}}": writable,
        "{{VALIDATE_COMMAND}}": validate,
    }
    for marker, content in replacements.items():
        template = template.replace(marker, content)
    if "{{" in template or "}}" in template:
        raise PackageError("Agent prompt template contains unresolved markers")
    return template


def execute(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    inputs = {
        "composition_report": args.composition_report.resolve(),
        "repair_queue": args.repair_queue.resolve(),
        "composition_input": args.composition_input.resolve(),
        "bindings": args.bindings.resolve(),
        "anchor_frames": args.anchor_frames.resolve(),
        "authored_scene": args.authored_scene.resolve(),
    }
    documents = {name: load_object(path, name) for name, path in inputs.items() if name != "authored_scene"}
    if not inputs["authored_scene"].is_file():
        raise PackageError(f"Authored sector scene is missing: {inputs['authored_scene']}")
    require_schema(documents["composition_report"], "caretaker.composition_validation_report", "composition report")
    require_schema(documents["repair_queue"], "caretaker.repair_queue", "repair queue")
    require_schema(documents["composition_input"], "caretaker.composition_validation_input", "composition input")
    require_schema(documents["bindings"], "caretaker.object_bindings", "bindings")
    require_schema(documents["anchor_frames"], "caretaker.anchor_frames", "anchor frames")
    context = require_same_context(list(documents.values()))
    object_ids, repair_items = selected_repair_items(documents["repair_queue"], args.object_id)
    objects = index_unique(documents["composition_input"].get("objects"), "object_id", "composition objects")
    binding_items = index_unique(documents["bindings"].get("bindings"), "binding_id", "bindings")
    bindings_by_object: dict[str, list[dict[str, Any]]] = {}
    for binding in binding_items.values():
        object_ref = binding.get("object_ref")
        if not isinstance(object_ref, dict) or not isinstance(object_ref.get("object_id"), str):
            raise PackageError("Binding has no object_ref.object_id")
        bindings_by_object.setdefault(object_ref["object_id"], []).append(binding)
    for object_id in object_ids:
        if object_id not in objects:
            raise PackageError(f"Unknown object_id in composition input: {object_id}")
        matches = bindings_by_object.get(object_id, [])
        if len(matches) != 1:
            raise PackageError(f"Object {object_id} must have exactly one binding, found {len(matches)}")
    report_issues = documents["composition_report"].get("issues")
    if not isinstance(report_issues, list) or not all(isinstance(item, dict) for item in report_issues):
        raise PackageError("Composition report issues must be an object array")
    selected_issues = sorted(
        [item for item in report_issues if item.get("object_id") in object_ids],
        key=lambda item: str(item.get("issue_id", "")),
    )
    issue_ids = {item.get("issue_id") for item in selected_issues}
    if any(not isinstance(value, str) or not value for value in issue_ids):
        raise PackageError("Selected diagnostics have invalid issue IDs")
    anchor_index = index_unique(documents["anchor_frames"].get("anchors"), "anchor_id", "anchor frames")
    relevant_anchor_ids: set[str] = set()
    for item in repair_items:
        previous = item.get("previous_anchor_ref")
        if isinstance(previous, dict) and isinstance(previous.get("anchor_id"), str):
            relevant_anchor_ids.add(previous["anchor_id"])
        candidates = item.get("candidate_anchor_ids", [])
        if not isinstance(candidates, list) or not all(isinstance(value, str) for value in candidates):
            raise PackageError("candidate_anchor_ids must be a string array")
        relevant_anchor_ids.update(candidates)
    selected_anchors = [anchor_index[value] for value in sorted(relevant_anchor_ids) if value in anchor_index]
    for item in repair_items:
        actions = item.get("allowed_actions")
        if not isinstance(actions, list) or not all(isinstance(action, str) and action for action in actions):
            raise PackageError("Selected repair has invalid allowed_actions")
    allowed_actions = sorted({action for item in repair_items for action in item["allowed_actions"]})
    if not allowed_actions:
        raise PackageError("Selected repairs have no allowed actions")
    validation_command = parse_validation_command(args.validate_command_json)
    authored_scene_path, authored_scene_source = resolve_allow_path(str(inputs["authored_scene"]), project_root)
    writable_sources: dict[str, Path] = {authored_scene_path: authored_scene_source}
    bindings_path, bindings_source = resolve_allow_path(str(inputs["bindings"]), project_root)
    writable_sources[bindings_path] = bindings_source
    for value in args.allow_file:
        path, source = resolve_allow_path(value, project_root)
        writable_sources[path] = source
    neighboring_scene = [path for path in writable_sources if path.endswith(".tscn") and path != authored_scene_path]
    if neighboring_scene:
        raise PackageError("Only the authored sector scene may be writable; neighboring scenes are forbidden")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise PackageError(f"Output directory must be absent or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    context_dir = output / "context"
    context_dir.mkdir()
    selected_bindings = sorted([bindings_by_object[value][0] for value in object_ids], key=lambda item: str(item["binding_id"]))
    selected_summary = {
        "blocking_count": sum(item.get("severity") == "blocking" for item in selected_issues),
        "warning_count": sum(item.get("severity") == "warning" for item in selected_issues),
        "static_count": sum(isinstance(item.get("evidence"), dict) and item["evidence"].get("proof_kind") == "static_geometry" for item in selected_issues),
        "runtime_physics_count": sum(isinstance(item.get("evidence"), dict) and item["evidence"].get("proof_kind") == "runtime_physics" for item in selected_issues),
    }
    filtered_documents = {
        "composition_report.json": {**documents["composition_report"], "status": "blocked", "summary": selected_summary, "issues": selected_issues},
        "repair_queue.json": {**documents["repair_queue"], "blocking_count": len(repair_items), "items": repair_items},
        "composition_objects.json": {**context, "schema_id": "caretaker.repair_composition_objects", "schema_version": "1.0.0", "objects": [objects[value] for value in object_ids]},
        "bindings.json": {**documents["bindings"], "bindings": selected_bindings},
        "anchor_frames.json": {**documents["anchor_frames"], "anchors": selected_anchors},
    }
    for name, value in filtered_documents.items():
        write_json(context_dir / name, value)
    scene_copy = context_dir / "authored_sector_scene" / authored_scene_source.name
    scene_copy.parent.mkdir()
    shutil.copy2(authored_scene_source, scene_copy)
    additional_context: list[str] = []
    additional_dir = context_dir / "allowlisted"
    for path, source in sorted(writable_sources.items()):
        if path in {authored_scene_path, bindings_path}:
            continue
        destination = additional_dir / path.removeprefix("res://")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        additional_context.append(destination.relative_to(output).as_posix())
    writable = [{"path": path, "sha256": digest_file(source)} for path, source in sorted(writable_sources.items())]
    package = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "contract_version": "1.0.0",
        "producer": {"name": "map_pipeline_repair_package", "version": VERSION},
        **context,
        "object_ids": object_ids,
        "repair_ids": [item["repair_id"] for item in repair_items],
        "diagnostic_ids": sorted(issue_ids),
        "allowed_actions": allowed_actions,
        "file_policy": {
            "writable": writable,
            "read_only": [
                {"path": str(inputs["anchor_frames"]), "owner": "generated"},
                {"path": "Generated/**", "owner": "generated"},
                {"path": str(inputs["composition_report"]), "owner": "validator"},
                {"path": str(inputs["repair_queue"]), "owner": "validator"},
            ],
            "forbidden": ["**/Generated/**", "**/*.svg", "converter settings", "neighboring sector scenes"],
        },
        "validation_command": validation_command,
        "agent_invoked": False,
        "context_files": sorted([f"context/{name}" for name in filtered_documents] + [f"context/authored_sector_scene/{authored_scene_source.name}", *additional_context]),
        "input_hashes": {name: digest_file(path) for name, path in sorted(inputs.items())},
    }
    package["package_id"] = digest_bytes(canonical_bytes(package))
    write_json(output / "repair_package.json", package)
    template_path = Path(__file__).with_name("templates") / "agent_prompt.md"
    prompt = render_prompt(template_path.read_text(encoding="utf-8"), package)
    (output / "agent_prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return execute(args)
    except (OSError, PackageError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
