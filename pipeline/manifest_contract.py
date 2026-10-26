"""Schema and portability checks for sector generation manifests.

Production rules a project wants enforced are declared in the manifest itself, under ``production_rules``:
``{"sector_count": 32, "source_contains": "/plans/generation/", "output_prefix": "res://gen/"}`` (all optional)."""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_ID = "caretaker.sector_generation_manifest"
SUPPORTED_SCHEMA_VERSIONS = {"1.0.0", "1.1.0"}
PRODUCTION_SCHEMA_VERSION = "1.1.0"
REQUIRED_SECTOR_FIELDS = {
    "sector_id", "status", "blockers", "source_svg", "scene_name", "output_resource_dir",
    "metric_settings", "local_to_world", "vertical_generators",
}
PRODUCTION_SECTOR_FIELDS = {"anchor_parameterization", "safe_regeneration", "sector_scene", "authored_scene"}


def _portable_relative(path: Any) -> bool:
    if not isinstance(path, str) or not path or "\\" in path or ":" in path:
        return False
    value = PurePosixPath(path)
    return not value.is_absolute() and ".." not in value.parts


def _transform_is_complete(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("origin", "basis_x", "basis_y", "basis_z"):
        vector = value.get(key)
        if not isinstance(vector, list) or len(vector) != 3:
            return False
        if not all(isinstance(item, (int, float)) and math.isfinite(item) for item in vector):
            return False
    return True


def validate_manifest_document(document: Any, project_root: Path, *, production: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["manifest root must be an object"]
    if document.get("schema_id") != SCHEMA_ID:
        errors.append(f"manifest schema_id must be {SCHEMA_ID}")
    schema_version = document.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(f"unsupported manifest schema_version: {schema_version}")
    if production and schema_version != PRODUCTION_SCHEMA_VERSION:
        errors.append(f"production manifest schema_version must be {PRODUCTION_SCHEMA_VERSION}")
    sectors = document.get("sectors")
    if not isinstance(sectors, list):
        return errors + ["manifest sectors must be an array"]
    if document.get("sector_count") != len(sectors):
        errors.append("sector_count does not match sectors length")
    rules = document.get("production_rules", {}) if production else {}
    if not isinstance(rules, dict):
        return errors + ["production_rules must be an object"]
    if "sector_count" in rules and len(sectors) != rules["sector_count"]:
        errors.append(f"production manifest must contain exactly {rules['sector_count']} sectors")

    seen: set[str] = set()
    for index, raw in enumerate(sectors):
        label = f"sectors[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{label} must be an object")
            continue
        sector_id = raw.get("sector_id")
        if not isinstance(sector_id, str) or not sector_id:
            errors.append(f"{label}.sector_id is required")
            continue
        label = sector_id
        if sector_id in seen:
            errors.append(f"duplicate sector_id: {sector_id}")
        seen.add(sector_id)
        required = REQUIRED_SECTOR_FIELDS | PRODUCTION_SECTOR_FIELDS if production else REQUIRED_SECTOR_FIELDS
        missing = sorted(required - set(raw))
        if missing:
            errors.append(f"{label} missing fields: {', '.join(missing)}")
        if production and (raw.get("status") != "ready" or raw.get("blockers") != []):
            errors.append(f"{label} is not production-ready")
        source = raw.get("source_svg")
        if not _portable_relative(source):
            errors.append(f"{label}.source_svg must be a portable relative path")
        elif rules.get("source_contains") and rules["source_contains"] not in f"/{source}":
            errors.append(f"{label}.source_svg is not a canonical generation plan")
        elif not (project_root / source).is_file():
            errors.append(f"{label}.source_svg is missing: {source}")
        output = raw.get("output_resource_dir")
        if not isinstance(output, str) or not output.startswith("res://") or ".." in output:
            errors.append(f"{label}.output_resource_dir must be a safe res:// path")
        elif rules.get("output_prefix") and not output.startswith(rules["output_prefix"]):
            errors.append(f"{label}.output_resource_dir must be under {rules['output_prefix']}")
        if not _transform_is_complete(raw.get("local_to_world")):
            errors.append(f"{label}.local_to_world is incomplete")
        metric = raw.get("metric_settings")
        if not isinstance(metric, dict) or any(metric.get(key) is None for key in ("scale_m_per_svg_unit", "origin", "elevation_m")):
            errors.append(f"{label}.metric_settings is incomplete")
        if production:
            for key in ("sector_scene", "authored_scene"):
                resource = raw.get(key)
                if not isinstance(resource, str) or not resource.startswith("res://"):
                    errors.append(f"{label}.{key} must be a res:// path")
                elif not (project_root / resource.removeprefix("res://")).is_file():
                    errors.append(f"{label}.{key} is missing: {resource}")
            safe = raw.get("safe_regeneration")
            if not isinstance(safe, dict):
                errors.append(f"{label}.safe_regeneration must be an object")
            else:
                for key in ("composition_input", "bindings_input"):
                    value = safe.get(key)
                    if not _portable_relative(value) or not (project_root / value).is_file():
                        errors.append(f"{label}.safe_regeneration.{key} is missing or non-portable")
    return errors
