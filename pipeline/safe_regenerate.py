#!/usr/bin/env python3
"""Safely regenerate and atomically promote one map sector package."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence


VERSION = "1.2.0"
SCHEMA_ID = "caretaker.safe_regeneration_report"
SCHEMA_VERSION = "1.0.0"
MANAGED_NAMES = ("Generated", "anchor_frames.json", "generation_manifest.json", "regeneration_report.json")
EQUIVALENCE_NAMES = ("Generated", "anchor_frames.json", "generation_manifest.json")
# Godot writes these next to imported resources; they are not generator output and must neither
# defeat the noop comparison nor be lost (their UIDs are referenced) when the package is replaced.
GODOT_SIDECAR_SUFFIXES = (".import", ".uid")
RESOURCE_RE = re.compile(r'path="(res://[^"]+)"')


class OrchestrationError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely regenerate one map sector")
    parser.add_argument("--sector", required=True)
    parser.add_argument("--manifest", required=True, help="sector generation manifest of the project")
    parser.add_argument("--svg-tool-root")
    parser.add_argument("--stair-tool-root")
    parser.add_argument("--backend", default=str(Path(__file__).with_name("regenerate_sector.py")))
    parser.add_argument("--composition-validator", default=str(Path(__file__).parent / "composition_validator" / "validate_composition.py"))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--report", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def load_object(path: Path, stage: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationError(stage, f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OrchestrationError(stage, f"JSON root must be an object: {path}")
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


def path_hash(path: Path, names: Sequence[str] | None = None, ignore_suffixes: Sequence[str] = ()) -> str:
    hasher = hashlib.sha256()
    roots = [path / name for name in names] if names is not None else [path]
    files: list[tuple[str, Path]] = []
    for root in roots:
        if root.is_file():
            if root.suffix not in ignore_suffixes:
                files.append((root.relative_to(path).as_posix(), root))
        elif root.is_dir():
            files.extend((item.relative_to(path).as_posix(), item) for item in root.rglob("*") if item.is_file() and item.suffix not in ignore_suffixes)
    for relative, item in sorted(files):
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(item.read_bytes())
        hasher.update(b"\0")
    return "sha256:" + hasher.hexdigest()


def resolve_under(root: Path, relative: str, stage: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise OrchestrationError(stage, f"Path escapes project root: {relative}") from exc
    return candidate


def load_context(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    manifest_path = Path(args.manifest).resolve()
    manifest = load_object(manifest_path, "source_validation")
    sectors = manifest.get("sectors")
    if not isinstance(sectors, list):
        raise OrchestrationError("source_validation", "Manifest sectors must be an array")
    matches = [item for item in sectors if isinstance(item, dict) and item.get("sector_id") == args.sector]
    if len(matches) != 1:
        raise OrchestrationError("source_validation", f"Unknown or duplicate sector ID: {args.sector}")
    sector = matches[0]
    if sector.get("status") != "ready" or sector.get("blockers"):
        raise OrchestrationError("source_validation", f"Sector {args.sector} is not ready")
    project_root = (manifest_path.parent / str(manifest.get("project_root", "."))).resolve()
    output = sector.get("output_resource_dir")
    if not isinstance(output, str) or not output.startswith("res://") or ".." in output:
        raise OrchestrationError("source_validation", "output_resource_dir must be a safe res:// path")
    live = resolve_under(project_root, output.removeprefix("res://"), "source_validation")
    if live == project_root:
        raise OrchestrationError("source_validation", "Live package cannot be the project root")
    return manifest, sector, project_root, live


def mark(report: dict[str, Any], stage: str, status: str, detail: str = "") -> None:
    report["stages"].append({"stage": stage, "status": status, "detail": detail})


def run_process(command: list[str], cwd: Path, stage: str) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise OrchestrationError(stage, f"Cannot start command: {exc}") from exc
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "no diagnostic output"
        if stage == "staging_generation":
            if "SVG inspection" in detail:
                stage = "source_validation"
            elif "Stair generator" in detail:
                stage = "stair_generation"
        raise OrchestrationError(stage, f"Command failed with exit code {process.returncode}: {detail}")
    return process


def validate_generated(package: Path, sector_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    generation = load_object(package / "generation_manifest.json", "generated_validation")
    anchors = load_object(package / "anchor_frames.json", "generated_validation")
    report = load_object(package / "regeneration_report.json", "generated_validation")
    if generation.get("schema_id") != "caretaker.sector_generation_result" or generation.get("schema_version") != "1.0.0":
        raise OrchestrationError("generated_validation", "Unsupported generation manifest schema")
    if anchors.get("schema_id") != "caretaker.anchor_frames" or anchors.get("schema_version") != "1.0.0":
        raise OrchestrationError("generated_validation", "Unsupported anchor frame schema")
    if generation.get("sector_id") != sector_id or anchors.get("sector_id") != sector_id or report.get("status") != "ok":
        raise OrchestrationError("generated_validation", "Generated package identity or status is invalid")
    anchor_items = anchors.get("anchors")
    if not isinstance(anchor_items, list):
        raise OrchestrationError("generated_validation", "Anchor list is missing")
    ids = [item.get("anchor_id") for item in anchor_items if isinstance(item, dict)]
    if len(ids) != len(anchor_items) or any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise OrchestrationError("generated_validation", "Anchor IDs must be present and unique")
    return generation, anchors


def build_candidate(live: Path, staging: Path, candidate: Path) -> None:
    if live.exists():
        shutil.copytree(live, candidate, symlinks=True)
    else:
        candidate.mkdir(parents=True)
    for name in MANAGED_NAMES:
        target = candidate / name
        sidecars = _godot_sidecars(target) if target.is_dir() else {}
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        source = staging / name
        if source.is_dir():
            shutil.copytree(source, target)
            _restore_godot_sidecars(target, sidecars)
        elif source.is_file():
            shutil.copy2(source, target)


def _godot_sidecars(root: Path) -> dict[str, bytes]:
    return {
        item.relative_to(root).as_posix(): item.read_bytes()
        for item in root.rglob("*") if item.is_file() and item.suffix in GODOT_SIDECAR_SUFFIXES
    }


def _restore_godot_sidecars(root: Path, sidecars: dict[str, bytes]) -> None:
    """Keep Godot's .import/.uid files (and their UIDs) for resources that still exist."""
    for relative, content in sidecars.items():
        sidecar = root / relative
        if sidecar.exists() or not sidecar.with_suffix("").is_file():
            continue
        sidecar.write_bytes(content)


def resource_path_exists(resource: str, project_root: Path, live: Path, candidate: Path) -> bool:
    relative = resource.removeprefix("res://")
    project_path = (project_root / relative).resolve()
    try:
        within_live = project_path.relative_to(live)
    except ValueError:
        return project_path.is_file() and project_path.stat().st_size > 0
    candidate_path = candidate / within_live
    return candidate_path.is_file() and candidate_path.stat().st_size > 0


def validate_resources(candidate: Path, project_root: Path, live: Path) -> None:
    scenes = list(candidate.rglob("*.tscn")) + list(candidate.rglob("*.tres"))
    if not scenes:
        raise OrchestrationError("combined_validation", "Candidate contains no Godot scenes or resources")
    for scene in scenes:
        try:
            text = scene.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise OrchestrationError("combined_validation", f"Cannot read Godot resource {scene}: {exc}") from exc
        missing = sorted({path for path in RESOURCE_RE.findall(text) if not resource_path_exists(path, project_root, live, candidate)})
        if missing:
            raise OrchestrationError("combined_validation", f"{scene.name} has missing resources: {', '.join(missing)}")


def resolve_composition(
    sector: dict[str, Any], project_root: Path, live: Path, candidate: Path,
    anchors: dict[str, Any], output: Path,
) -> Path:
    config = sector.get("safe_regeneration")
    if not isinstance(config, dict) or not isinstance(config.get("composition_input"), str):
        raise OrchestrationError("binding_resolution", "Sector must declare safe_regeneration.composition_input")
    source = resolve_under(project_root, config["composition_input"], "binding_resolution")
    try:
        source = candidate / source.relative_to(live)
    except ValueError:
        pass
    document = load_object(source, "binding_resolution")
    objects = document.get("objects")
    if not isinstance(objects, list):
        raise OrchestrationError("binding_resolution", "Composition objects must be an array")
    document["map_id"] = anchors.get("map_id")
    document["sector_id"] = anchors.get("sector_id")
    document["generation_id"] = anchors.get("generation_id")
    document["anchors"] = anchors.get("anchors")
    bindings_input = config.get("bindings_input")
    if bindings_input is not None:
        if not isinstance(bindings_input, str):
            raise OrchestrationError("binding_resolution", "safe_regeneration.bindings_input must be a path")
        bindings_path = resolve_under(project_root, bindings_input, "binding_resolution")
        try:
            bindings_path = candidate / bindings_path.relative_to(live)
        except ValueError:
            pass
        bindings = load_object(bindings_path, "binding_resolution")
        module_path = Path(__file__).with_name("resolve_bindings.py")
        spec = importlib.util.spec_from_file_location("map_pipeline_resolve_bindings", module_path)
        if spec is None or spec.loader is None:
            raise OrchestrationError("binding_resolution", "Cannot load binding resolver")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            document = module.resolve_document(document, bindings, anchors)
        except ValueError as exc:
            raise OrchestrationError("binding_resolution", str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, document)
    return output


def _evidence_value(
    kind: str, value: str, map_id: str, sector_id: str, generation_id: str,
) -> dict[str, Any]:
    return {
        "schema_id": "caretaker.safe_regeneration_evidence_value",
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "map_id": map_id,
        "sector_id": sector_id,
        "generation_id": generation_id,
        "value": value,
    }


def _require_evidence_identity(
    document: dict[str, Any], name: str, map_id: str, sector_id: str, generation_id: str,
) -> None:
    expected = {"map_id": map_id, "sector_id": sector_id, "generation_id": generation_id}
    mismatches = [key for key, value in expected.items() if document.get(key) != value]
    if mismatches:
        raise OrchestrationError(
            "diagnostic_persistence",
            f"Evidence identity mismatch in {name}: {', '.join(mismatches)}",
        )


def preserve_validation_evidence(
    output: Path, candidate_package: Path, report_path: Path, report: dict[str, Any],
) -> None:
    """Keep this attempt's diagnostics outside the disposable transaction and live.

    A fresh directory prevents stale repair items from an earlier attempt from
    masquerading as current evidence. The report is the sole current pointer;
    previous attempts are retained for audit, never deleted implicitly.
    """
    anchors = load_object(candidate_package / "anchor_frames.json", "diagnostic_persistence")
    generation = load_object(candidate_package / "generation_manifest.json", "diagnostic_persistence")
    regeneration = load_object(candidate_package / "regeneration_report.json", "diagnostic_persistence")
    map_id = str(anchors.get("map_id", ""))
    sector_id = str(anchors.get("sector_id", ""))
    generation_id = str(anchors.get("generation_id", ""))
    if not map_id or not sector_id or not generation_id:
        raise OrchestrationError("diagnostic_persistence", "Candidate anchor identity is incomplete")
    _require_evidence_identity(generation, "generation_manifest.json", map_id, sector_id, generation_id)
    if "map_id" not in regeneration:
        regeneration["map_id"] = map_id
    _require_evidence_identity(regeneration, "regeneration_report.json", map_id, sector_id, generation_id)

    documents: dict[str, dict[str, Any]] = {
        "candidate_anchor_frames.json": anchors,
        "candidate_generation_manifest.json": generation,
        "candidate_regeneration_report.json": regeneration,
        "source_sha256": _evidence_value(
            "source_sha256", str(report.get("input_hashes", {}).get("source", "")),
            map_id, sector_id, generation_id,
        ),
        "sector_config_sha256": _evidence_value(
            "sector_config_sha256", str(report.get("input_hashes", {}).get("sector_config", "")),
            map_id, sector_id, generation_id,
        ),
        "generation_id": _evidence_value(
            "generation_id", generation_id, map_id, sector_id, generation_id,
        ),
    }
    for name in ("resolved_composition.json", "validation_report.json", "repair_queue.json"):
        source = output / name
        if source.is_file():
            document = load_object(source, "diagnostic_persistence")
            _require_evidence_identity(document, name, map_id, sector_id, generation_id)
            documents[name] = document

    evidence: Path | None = None
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix=f"{report_path.stem}.evidence-", dir=report_path.parent))
        artifacts: dict[str, dict[str, str]] = {}
        for name, document in documents.items():
            destination = evidence / name
            atomic_write_json(destination, document)
            artifacts[name] = {
                "path": str(destination), "sha256": digest_bytes(destination.read_bytes()),
            }
        report["map_id"] = map_id
        report["generation_id"] = generation_id
        report["evidence_directory"] = str(evidence)
        report["validation_artifacts"] = artifacts
    except OSError as exc:
        if evidence is not None:
            shutil.rmtree(evidence, ignore_errors=True)
        raise OrchestrationError("diagnostic_persistence", f"Cannot retain validation evidence: {exc}") from exc


def validate_composition(
    args: argparse.Namespace, project_root: Path, input_path: Path, output: Path,
    candidate_package: Path, report_path: Path, report: dict[str, Any],
) -> None:
    validator = Path(args.composition_validator).resolve()
    if not validator.is_file():
        raise OrchestrationError("composition_validation", f"Composition validator is missing: {validator}")
    command = [args.python, str(validator), "--input", str(input_path), "--output", str(output)]
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "resolved_composition.json", load_object(input_path, "binding_resolution"))
    try:
        run_process(command, project_root, "composition_validation")
    finally:
        # The validator intentionally exits 2 for blocked compositions, but its
        # repair queue is still the useful result and must survive cleanup.
        preserve_validation_evidence(output, candidate_package, report_path, report)
    result = load_object(output / "validation_report.json", "composition_validation")
    if result.get("status") != "clean":
        raise OrchestrationError("composition_validation", "Composition validator did not report clean")


def promote(candidate: Path, live: Path, backup: Path) -> None:
    moved_live = False
    try:
        if live.exists():
            os.replace(live, backup)
            moved_live = True
        os.replace(candidate, live)
    except OSError as exc:
        if moved_live and backup.exists() and not live.exists():
            os.replace(backup, live)
        raise OrchestrationError("atomic_promotion", f"Atomic promotion failed and was rolled back: {exc}") from exc
    if backup.exists():
        shutil.rmtree(backup)


def execute(args: argparse.Namespace) -> int:
    report_path = Path(args.report).resolve()
    report_allowed = True
    report: dict[str, Any] = {
        "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "orchestrator_version": VERSION,
        "sector_id": args.sector, "status": "failed", "ready": False, "mode": "validate_only" if args.validate_only else "dry_run" if args.dry_run else "regenerate",
        "stages": [], "input_hashes": {}, "output_hashes": {}, "live_hash_before": None, "live_hash_after": None,
        "agent_invoked": False, "errors": [], "validation_artifacts": {},
    }
    work_root: Path | None = None
    try:
        manifest, sector, project_root, live = load_context(args)
        try:
            report_path.relative_to(live)
        except ValueError:
            pass
        else:
            report_allowed = False
            raise OrchestrationError("source_validation", "Machine report must be outside the live package")
        source = resolve_under(project_root, str(sector.get("source_svg", "")), "source_validation")
        if not args.validate_only and not source.is_file():
            raise OrchestrationError("source_validation", f"Source SVG is missing: {source}")
        report["input_hashes"] = {
            "sector_config": digest_bytes(canonical_bytes(sector)),
            "source": digest_bytes(source.read_bytes()) if source.is_file() else "missing",
        }
        report["live_hash_before"] = path_hash(live) if live.exists() else None
        mark(report, "source_validation", "passed")
        if not args.validate_only:
            preflight_command = [
                args.python, str(Path(args.backend).resolve()), "--sector", args.sector,
                "--manifest", str(Path(args.manifest).resolve()), "--preflight-only", "--python", args.python,
            ]
            if args.svg_tool_root:
                preflight_command.extend(["--svg-tool-root", str(Path(args.svg_tool_root).resolve())])
            if args.stair_tool_root:
                preflight_command.extend(["--stair-tool-root", str(Path(args.stair_tool_root).resolve())])
            run_process(preflight_command, project_root, "toolchain_validation")
            mark(report, "toolchain_validation", "passed")
        work_root = live.parent / f".{live.name}.regeneration-{uuid.uuid4().hex}"
        staging, candidate, validation = work_root / "staging", work_root / "candidate", work_root / "validation"
        work_root.mkdir(parents=True)
        if args.validate_only:
            if not live.is_dir():
                raise OrchestrationError("generated_validation", f"Live package is missing: {live}")
            generation, anchors = validate_generated(live, args.sector)
            mark(report, "generated_validation", "passed")
            validate_resources(live, project_root, live)
            mark(report, "combined_validation", "passed")
            resolved = resolve_composition(sector, project_root, live, live, anchors, validation / "resolved_composition.json")
            mark(report, "binding_resolution", "passed")
            validate_composition(args, project_root, resolved, validation / "composition", live, report_path, report)
            mark(report, "composition_validation", "passed")
            report["output_hashes"] = {"managed": path_hash(live, EQUIVALENCE_NAMES, GODOT_SIDECAR_SUFFIXES), "generation_id": generation.get("generation_id")}
            report["status"], report["ready"] = "validated", True
        else:
            staging.mkdir()
            command = [args.python, str(Path(args.backend).resolve()), "--sector", args.sector, "--staging", str(staging), "--manifest", str(Path(args.manifest).resolve())]
            if args.svg_tool_root:
                command.extend(["--svg-tool-root", str(Path(args.svg_tool_root).resolve())])
            if args.stair_tool_root:
                command.extend(["--stair-tool-root", str(Path(args.stair_tool_root).resolve())])
            command.extend(["--python", args.python])
            run_process(command, project_root, "staging_generation")
            mark(report, "staging_generation", "passed")
            generation, anchors = validate_generated(staging, args.sector)
            mark(report, "generated_validation", "passed")
            stair_reports = list((staging / "Generated" / "Stairs").rglob("generation_report.json")) if (staging / "Generated" / "Stairs").exists() else []
            if any(load_object(path, "stair_generation").get("status") != "ok" for path in stair_reports):
                raise OrchestrationError("stair_generation", "A stair report is not ok")
            mark(report, "stair_generation", "passed", f"reports={len(stair_reports)}")
            build_candidate(live, staging, candidate)
            validate_resources(candidate, project_root, live)
            mark(report, "combined_validation", "passed")
            resolved = resolve_composition(sector, project_root, live, candidate, anchors, validation / "resolved_composition.json")
            mark(report, "binding_resolution", "passed")
            validate_composition(args, project_root, resolved, validation / "composition", candidate, report_path, report)
            mark(report, "composition_validation", "passed")
            # regeneration_report.json embeds ephemeral command/staging paths and is
            # deliberately excluded from semantic equality.
            generated_hash = path_hash(staging, EQUIVALENCE_NAMES, GODOT_SIDECAR_SUFFIXES)
            live_managed_hash = path_hash(live, EQUIVALENCE_NAMES, GODOT_SIDECAR_SUFFIXES) if live.exists() else None
            report["output_hashes"] = {"managed": generated_hash, "generation_id": generation.get("generation_id")}
            if live_managed_hash == generated_hash:
                report["status"], report["ready"] = "noop", True
                mark(report, "atomic_promotion", "skipped", "managed output is unchanged")
            elif args.dry_run:
                report["status"], report["ready"] = "dry_run", False
                report["candidate_validated"] = True
                mark(report, "atomic_promotion", "skipped", "--dry-run")
            else:
                backup = live.parent / f".{live.name}.backup-{uuid.uuid4().hex}"
                promote(candidate, live, backup)
                mark(report, "atomic_promotion", "passed")
                report["status"], report["ready"] = "success", True
        report["live_hash_after"] = path_hash(live) if live.exists() else None
        atomic_write_json(report_path, report)
        return 0
    except OrchestrationError as exc:
        report["errors"].append({"stage": exc.stage, "message": str(exc)})
        mark(report, exc.stage, "failed", str(exc))
        if "live" in locals():
            report["live_hash_after"] = path_hash(live) if live.exists() else None
        if report_allowed:
            try:
                atomic_write_json(report_path, report)
            except OSError:
                pass
        print(f"ERROR [{exc.stage}]: {exc}", file=sys.stderr)
        return 2
    finally:
        if work_root is not None and work_root.exists():
            shutil.rmtree(work_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    return execute(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
