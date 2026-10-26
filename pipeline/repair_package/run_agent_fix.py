#!/usr/bin/env python3
"""Run one explicit, fail-closed map authored-object repair transaction."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Sequence


VERSION = "1.0.0"
CLEAN_STATUSES = {"success", "noop"}
AUTHORED_OWNERS = {"authored_content", "authored_bindings"}


class AgentFixError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a restricted map Agent Fix")
    parser.add_argument("--sector", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--safe-report", required=True, type=Path)
    parser.add_argument("--agent-launcher", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--svg-tool-root")
    parser.add_argument("--stair-tool-root")
    parser.add_argument("--allow-file", action="append", default=[])
    parser.add_argument("--safe-regenerate", type=Path, default=Path(__file__).parents[1] / "safe_regenerate.py")
    parser.add_argument("--backend", type=Path)
    parser.add_argument("--composition-validator", type=Path)
    return parser.parse_args(argv)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentFixError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AgentFixError(f"{label} root must be an object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def resolve_project_path(project_root: Path, value: str, label: str) -> Path:
    relative = value.removeprefix("res://") if value.startswith("res://") else value
    candidate = (project_root / relative).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise AgentFixError(f"{label} escapes project root: {value}") from exc
    return candidate


def failed_stage(report: dict[str, Any]) -> str:
    errors = report.get("errors", [])
    if isinstance(errors, list):
        for item in reversed(errors):
            if isinstance(item, dict) and isinstance(item.get("stage"), str):
                return item["stage"]
    stages = report.get("stages", [])
    if isinstance(stages, list):
        for item in reversed(stages):
            if isinstance(item, dict) and item.get("status") == "failed":
                return str(item.get("stage", ""))
    return ""


def load_context(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], Path]:
    manifest_path = args.manifest.resolve()
    manifest = load_object(manifest_path, "manifest")
    sectors = manifest.get("sectors")
    if not isinstance(sectors, list):
        raise AgentFixError("Manifest sectors must be an array")
    matches = [item for item in sectors if isinstance(item, dict) and item.get("sector_id") == args.sector]
    if len(matches) != 1:
        raise AgentFixError(f"Unknown or duplicate sector ID: {args.sector}")
    sector = matches[0]
    project_root = (manifest_path.parent / str(manifest.get("project_root", "."))).resolve()
    return manifest, sector, project_root


def verify_attempt(
    report: dict[str, Any], sector: dict[str, Any],
    project_root: Path, sector_id: str,
) -> dict[str, Path]:
    if report.get("ready") or failed_stage(report) != "composition_validation":
        raise AgentFixError("Agent Fix requires a failed composition_validation report")
    if report.get("sector_id") != sector_id:
        raise AgentFixError("Safe report sector does not match the requested sector")
    source = resolve_project_path(project_root, str(sector.get("source_svg", "")), "source SVG")
    hashes = report.get("input_hashes")
    expected_hashes = {
        "source": digest_bytes(source.read_bytes()) if source.is_file() else "missing",
        "sector_config": digest_bytes(canonical_bytes(sector)),
    }
    if not isinstance(hashes, dict) or any(hashes.get(key) != value for key, value in expected_hashes.items()):
        raise AgentFixError("Safe report is stale: source or sector config hash changed")
    artifacts = report.get("validation_artifacts")
    required = {
        "resolved_composition.json", "validation_report.json", "repair_queue.json",
        "candidate_anchor_frames.json", "candidate_generation_manifest.json",
        "candidate_regeneration_report.json", "source_sha256", "sector_config_sha256", "generation_id",
    }
    if not isinstance(artifacts, dict) or not required.issubset(artifacts):
        raise AgentFixError("Safe report does not contain a complete T24 candidate evidence set")
    paths: dict[str, Path] = {}
    parents: set[Path] = set()
    for name, details in artifacts.items():
        if not isinstance(details, dict) or not isinstance(details.get("path"), str):
            raise AgentFixError(f"Invalid artifact entry: {name}")
        path = Path(details["path"]).resolve()
        if not path.is_file() or details.get("sha256") != digest_bytes(path.read_bytes()):
            raise AgentFixError(f"Evidence artifact is missing or changed: {name}")
        paths[name] = path
        parents.add(path.parent)
    if len(parents) != 1:
        raise AgentFixError("Evidence artifacts do not belong to one attempt directory")
    queue = load_object(paths["repair_queue.json"], "repair queue")
    open_items = [
        item for item in queue.get("items", [])
        if isinstance(item, dict) and item.get("severity") == "blocking" and item.get("status") == "open"
    ] if isinstance(queue.get("items"), list) else []
    if not open_items:
        raise AgentFixError("Repair queue has no open blocking objects")
    for item in open_items:
        evidence = item.get("evidence")
        candidates = item.get("candidate_anchor_ids", [])
        if not isinstance(evidence, dict) or evidence.get("responsible_owner") not in AUTHORED_OWNERS:
            raise AgentFixError("Repair queue contains a non-authored blocking issue")
        if not isinstance(item.get("object_id"), str) or not item["object_id"]:
            raise AgentFixError("Repair queue contains a blocker without object_id")
        if not isinstance(item.get("allowed_actions"), list) or not item["allowed_actions"]:
            raise AgentFixError("Repair queue contains a blocker without allowed_actions")
        if item.get("reason") in {"object_id_ambiguous", "schema_incompatible"}:
            raise AgentFixError("Ambiguous repair cannot be delegated to Agent Fix")
        if not isinstance(candidates, list) or len(candidates) > 1:
            raise AgentFixError("Ambiguous candidate anchors require human approval")
    return paths


def load_builder() -> Any:
    path = Path(__file__).with_name("build_repair_package.py")
    spec = importlib.util.spec_from_file_location("map_pipeline_build_repair_package", path)
    if spec is None or spec.loader is None:
        raise AgentFixError("Cannot load repair package builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_launcher(value: str, project_root: Path, package: Path, prompt: Path) -> list[str]:
    stripped = value.strip()
    if not stripped:
        raise AgentFixError("Agent launcher is not configured")
    if stripped.startswith("["):
        try:
            base = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AgentFixError(f"Agent launcher JSON is invalid: {exc}") from exc
        if not isinstance(base, list) or not base or not all(isinstance(item, str) and item for item in base):
            raise AgentFixError("Agent launcher JSON must be a non-empty string array")
    else:
        base = [stripped]
    return [
        *base, "--project-root", str(project_root), "--repair-package", str(package),
        "--prompt", str(prompt),
    ]


def snapshot_project(project_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(project_root)
        if any(part in {".git", ".godot", "__pycache__"} for part in relative.parts) or path.suffix in {".import", ".pyc"}:
            continue
        result[relative.as_posix()] = digest_bytes(path.read_bytes())
    return result


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def normalize_allowed(project_root: Path, paths: Sequence[Path]) -> set[str]:
    result: set[str] = set()
    for path in paths:
        try:
            result.add(path.resolve().relative_to(project_root).as_posix())
        except ValueError as exc:
            raise AgentFixError(f"Writable path is outside project root: {path}") from exc
    return result


def record_agent(
    report_path: Path, report: dict[str, Any], *, command: Sequence[str], exit_code: int,
    changed: Sequence[str], forbidden: Sequence[str], package: Path,
    revalidation: dict[str, Any] | None = None,
) -> None:
    report["agent_invoked"] = True
    report["agent_command"] = list(command)
    report["agent_exit_code"] = exit_code
    report["agent_changed_files"] = list(changed)
    report["agent_forbidden_changes"] = list(forbidden)
    report["agent_repair_package"] = str(package)
    report["agent_revalidation"] = revalidation
    atomic_write_json(report_path, report)


def execute(args: argparse.Namespace) -> int:
    report_path = args.safe_report.resolve()
    original_report = load_object(report_path, "safe report")
    _manifest, sector, project_root = load_context(args)
    artifacts = verify_attempt(original_report, sector, project_root, args.sector)
    safe_config = sector.get("safe_regeneration")
    if not isinstance(safe_config, dict):
        raise AgentFixError("Sector has no safe_regeneration config")
    authored_scene = resolve_project_path(project_root, str(sector.get("authored_scene", "")), "authored scene")
    bindings = resolve_project_path(project_root, str(safe_config.get("bindings_input", "")), "bindings")
    composition = artifacts["resolved_composition.json"]
    configured_allow = safe_config.get("agent_allow_files", [])
    if not isinstance(configured_allow, list) or not all(isinstance(item, str) for item in configured_allow):
        raise AgentFixError("safe_regeneration.agent_allow_files must be a string array")
    allow_files = [*configured_allow, *args.allow_file]
    allow_paths = [resolve_project_path(project_root, value, "agent allow file") for value in allow_files]

    evidence_directory = Path(str(original_report.get("evidence_directory", ""))).resolve()
    if not evidence_directory.is_dir():
        raise AgentFixError("Safe report evidence_directory is missing")
    with nullcontext(tempfile.mkdtemp(prefix="agent-fix-", dir=evidence_directory)) as temporary_name:
        package_dir = Path(temporary_name) / "package"
        validate_command = [args.python, "-m", "json.tool", str(bindings)]
        builder_args = [
            "--composition-report", str(artifacts["validation_report.json"]),
            "--repair-queue", str(artifacts["repair_queue.json"]),
            "--composition-input", str(composition),
            "--bindings", str(bindings),
            "--anchor-frames", str(artifacts["candidate_anchor_frames.json"]),
            "--authored-scene", str(authored_scene),
            "--output", str(package_dir),
            "--project-root", str(project_root),
            "--validate-command-json", json.dumps(validate_command, ensure_ascii=False),
        ]
        for value in allow_files:
            builder_args.extend(["--allow-file", value])
        if load_builder().main(builder_args) != 0:
            raise AgentFixError("Repair package builder rejected the current attempt")
        package_path = package_dir / "repair_package.json"
        prompt_path = package_dir / "agent_prompt.md"
        package = load_object(package_path, "repair package")
        object_ids = package.get("object_ids")
        if not isinstance(object_ids, list) or not object_ids:
            raise AgentFixError("Repair package has no blocking objects")
        command = parse_launcher(args.agent_launcher, project_root, package_path, prompt_path)
        allowed = normalize_allowed(project_root, [authored_scene, bindings, *allow_paths])
        package_files = {path for path in package_dir.rglob("*") if path.is_file()}
        protected_paths = {report_path, *package_files, *artifacts.values()}
        protected_before = {str(path): digest_bytes(path.read_bytes()) for path in protected_paths}
        before = snapshot_project(project_root)
        try:
            process = subprocess.run(command, cwd=project_root, text=True, capture_output=True, check=False)
            launcher_exit = process.returncode
        except OSError as exc:
            launcher_exit = -1
            process = None
            launcher_error = str(exc)
        after = snapshot_project(project_root)
        changed = changed_paths(before, after)
        forbidden = sorted(set(changed) - allowed)
        protected_after = {
            str(path): digest_bytes(path.read_bytes()) if path.is_file() else "missing"
            for path in protected_paths
        }
        forbidden.extend(sorted(path for path, value in protected_after.items() if protected_before[path] != value))
        forbidden = sorted(set(forbidden))
        if launcher_exit != 0 or forbidden:
            detail = "launcher failed" if launcher_exit != 0 else "agent changed forbidden files"
            if process is None:
                detail += f": {launcher_error}"
            original_report["status"] = "failed"
            original_report["ready"] = False
            original_report.setdefault("errors", []).append({"stage": "agent_fix", "message": detail})
            record_agent(
                report_path, original_report, command=command, exit_code=launcher_exit,
                changed=sorted(set(changed) & allowed), forbidden=forbidden, package=package_path,
            )
            return 2

        safe_command = [
            args.python, str(args.safe_regenerate.resolve()), "--sector", args.sector,
            "--manifest", str(args.manifest.resolve()), "--python", args.python,
            "--report", str(report_path),
        ]
        if args.svg_tool_root:
            safe_command.extend(["--svg-tool-root", args.svg_tool_root])
        if args.stair_tool_root:
            safe_command.extend(["--stair-tool-root", args.stair_tool_root])
        if args.backend:
            safe_command.extend(["--backend", str(args.backend.resolve())])
        if args.composition_validator:
            safe_command.extend(["--composition-validator", str(args.composition_validator.resolve())])
        try:
            revalidation_process = subprocess.run(safe_command, cwd=project_root, text=True, capture_output=True, check=False)
            revalidation_exit = revalidation_process.returncode
            revalidation_error = ""
        except OSError as exc:
            revalidation_exit = -1
            revalidation_error = str(exc)
        try:
            final_report = load_object(report_path, "revalidation safe report")
        except AgentFixError:
            final_report = original_report
        clean = (
            revalidation_exit == 0
            and final_report.get("ready") is True
            and final_report.get("status") in CLEAN_STATUSES
        )
        revalidation = {
            "command": safe_command,
            "exit_code": revalidation_exit,
            "error": revalidation_error,
            "status": final_report.get("status", "missing"),
            "ready": bool(final_report.get("ready", False)),
            "clean": clean,
        }
        if not clean:
            final_report["status"] = "failed"
            final_report["ready"] = False
            final_report.setdefault("errors", []).append({
                "stage": "agent_revalidation", "message": "Full regeneration did not finish clean",
            })
        record_agent(
            report_path, final_report, command=command, exit_code=launcher_exit,
            changed=changed, forbidden=[], package=package_path, revalidation=revalidation,
        )
        return 0 if clean else 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return execute(args)
    except (AgentFixError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
