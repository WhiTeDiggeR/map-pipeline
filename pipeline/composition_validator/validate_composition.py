from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_ID = "caretaker.composition_validation_input"
SCHEMA_VERSION = "1.0.0"
REPORT_SCHEMA_ID = "caretaker.composition_validation_report"
REPAIR_SCHEMA_ID = "caretaker.repair_queue"
PHYSICS_SCHEMA_ID = "caretaker.composition_physics_proof"
EPS = 1.0e-6
ALLOWED_ANCHOR_TYPES = {"point", "wall", "door", "floor", "ceiling", "shaft", "stair_entry", "stair_exit"}


class ValidationInputError(ValueError):
    pass


@dataclass(frozen=True)
class Bounds:
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    @classmethod
    def read(cls, value: Any, label: str) -> "Bounds":
        if not isinstance(value, dict):
            raise ValidationInputError(f"{label} must be an object")
        minimum = read_vector(value.get("min"), f"{label}.min")
        maximum = read_vector(value.get("max"), f"{label}.max")
        if any(low > high for low, high in zip(minimum, maximum)):
            raise ValidationInputError(f"{label} min must not exceed max")
        return cls(minimum, maximum)

    def contains(self, other: "Bounds") -> bool:
        return all(
            self.minimum[index] - EPS <= other.minimum[index]
            and other.maximum[index] <= self.maximum[index] + EPS
            for index in range(3)
        )

    def penetration(self, other: "Bounds") -> tuple[float, float, float] | None:
        values = tuple(
            min(self.maximum[index], other.maximum[index])
            - max(self.minimum[index], other.minimum[index])
            for index in range(3)
        )
        return values if all(value > EPS for value in values) else None

    def xz_overlap(self, other: "Bounds") -> bool:
        return (
            min(self.maximum[0], other.maximum[0]) - max(self.minimum[0], other.minimum[0]) > EPS
            and min(self.maximum[2], other.maximum[2]) - max(self.minimum[2], other.minimum[2]) > EPS
        )


def read_vector(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValidationInputError(f"{label} must contain three finite numbers")
    result: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)):
            raise ValidationInputError(f"{label} must contain three finite numbers")
        result.append(float(item))
    return tuple(result)  # type: ignore[return-value]


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def issue_id(code: str, object_id: str, anchor_id: str | None, measurement: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_bytes([code, object_id, anchor_id, measurement])).hexdigest()[:12].upper()
    return f"CV-{code.upper().replace('_', '-')}-{digest}"


def diagnostic(
    *,
    code: str,
    object_id: str,
    anchor_id: str | None,
    subject_owner: str,
    responsible_owner: str,
    measurement: dict[str, Any],
    allowed_actions: list[str],
    message: str,
    proof_kind: str = "static_geometry",
    severity: str = "blocking",
    binding_id: str | None = None,
    expected_anchor_type: str | None = None,
) -> dict[str, Any]:
    return {
        "issue_id": issue_id(code, object_id, anchor_id, measurement),
        "severity": severity,
        "status": "open",
        "code": code,
        "object_id": object_id,
        "binding_id": binding_id,
        "anchor_id": anchor_id,
        "expected_anchor_type": expected_anchor_type,
        "subject_owner": subject_owner,
        "responsible_owner": responsible_owner,
        "measurement": measurement,
        "evidence": {"proof_kind": proof_kind, "message": message},
        "allowed_actions": allowed_actions,
    }


def state_measurement(actual: str, limit: str) -> dict[str, Any]:
    return {"actual": actual, "limit": limit, "units": "state", "relation": "must_equal"}


def penetration_measurement(values: tuple[float, float, float]) -> dict[str, Any]:
    return {"actual": [round(value, 6) for value in values], "limit": [0.0, 0.0, 0.0], "units": "m", "relation": "penetration_lte"}


def validate_document(document: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if document.get("schema_id") != SCHEMA_ID or document.get("schema_version") != SCHEMA_VERSION:
        raise ValidationInputError("unsupported composition input schema")
    for key in ("map_id", "sector_id", "generation_id"):
        if not isinstance(document.get(key), str) or not document[key]:
            raise ValidationInputError(f"input is missing {key}")
    sector_bounds = Bounds.read(document.get("sector_bounds"), "sector_bounds")
    spaces = index_unique(document.get("spaces"), "space_id", "spaces")
    infrastructure = index_unique(document.get("infrastructure"), "infrastructure_id", "infrastructure")
    for infrastructure_id, item in infrastructure.items():
        if item.get("owner") != "generated":
            raise ValidationInputError(f"infrastructure {infrastructure_id} must have owner=generated")
    anchors, duplicate_anchor_ids = index_with_duplicates(document.get("anchors"), "anchor_id", "anchors")
    objects_list = require_object_array(document.get("objects"), "objects")
    object_counts: dict[str, int] = {}
    for item in objects_list:
        object_counts[str(item.get("object_id", ""))] = object_counts.get(str(item.get("object_id", "")), 0) + 1
    issues: list[dict[str, Any]] = []

    for object_id, count in sorted(object_counts.items()):
        if not object_id:
            raise ValidationInputError("object has no persistent object_id")
        if count > 1:
            issues.append(diagnostic(
                code="duplicate_object_id", object_id=object_id, anchor_id=None,
                subject_owner="authored", responsible_owner="authored_bindings",
                measurement={"actual": count, "limit": 1, "units": "count", "relation": "lte"},
                allowed_actions=["assign_new_object_id", "remove_duplicate_object"],
                message="Persistent object ID is not unique.",
            ))

    for anchor_id in sorted(duplicate_anchor_ids):
        issues.append(diagnostic(
            code="duplicate_anchor_id", object_id=f"ANCHOR::{anchor_id}", anchor_id=anchor_id,
            subject_owner="generated", responsible_owner="generated",
            measurement={"actual": duplicate_anchor_ids[anchor_id], "limit": 1, "units": "count", "relation": "lte"},
            allowed_actions=["regenerate_anchor_frames", "fix_generator_source_ids"],
            message="Generated anchor ID is not unique; no authored object is assigned responsibility.",
        ))

    for item in objects_list:
        issues.extend(validate_object(item, anchors, spaces, infrastructure, sector_bounds))
    issues.extend(validate_generated_infrastructure(infrastructure))
    issues.extend(read_declared_warnings(document.get("declared_warnings", [])))
    context = {
        "map_id": document["map_id"],
        "sector_id": document["sector_id"],
        "generation_id": document["generation_id"],
    }
    return sorted(issues, key=lambda item: item["issue_id"]), context


def require_object_array(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValidationInputError(f"{label} must be an array")
    if not all(isinstance(item, dict) for item in value):
        raise ValidationInputError(f"{label} entries must be objects")
    return value


def index_unique(value: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    items = require_object_array(value, label)
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identity = item.get(key)
        if not isinstance(identity, str) or not identity:
            raise ValidationInputError(f"{label} entry is missing {key}")
        if identity in result:
            raise ValidationInputError(f"duplicate {key} in {label}: {identity}")
        result[identity] = item
    return result


def index_with_duplicates(value: Any, key: str, label: str) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    items = require_object_array(value, label)
    result: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for item in items:
        identity = item.get(key)
        if not isinstance(identity, str) or not identity:
            raise ValidationInputError(f"{label} entry is missing {key}")
        counts[identity] = counts.get(identity, 0) + 1
        result.setdefault(identity, item)
    return result, {identity: count for identity, count in counts.items() if count > 1}


def validate_object(
    item: dict[str, Any],
    anchors: dict[str, dict[str, Any]],
    spaces: dict[str, dict[str, Any]],
    infrastructure: dict[str, dict[str, Any]],
    sector_bounds: Bounds,
) -> list[dict[str, Any]]:
    object_id = require_string(item, "object_id")
    binding_id = str(item.get("binding_id")) if item.get("binding_id") else None
    anchor_id = str(item.get("anchor_id")) if item.get("anchor_id") else None
    owner = str(item.get("owner", "authored"))
    if owner != "authored":
        raise ValidationInputError(f"object {object_id} must have owner=authored")
    bounds = Bounds.read(item.get("bounds"), f"object {object_id}.bounds")
    issues: list[dict[str, Any]] = []
    anchor = anchors.get(anchor_id) if anchor_id else None
    expected_type = str(item.get("expected_anchor_type", ""))
    if anchor_id and anchor is None:
        issues.append(diagnostic(
            code="missing_anchor", object_id=object_id, anchor_id=anchor_id,
            subject_owner="authored", responsible_owner="authored_bindings",
            measurement=state_measurement("missing", "active anchor exists"),
            allowed_actions=["restore_anchor", "rebind_explicit", "remove_binding"],
            message="Referenced stable anchor ID is absent.", binding_id=binding_id, expected_anchor_type=expected_type,
        ))
    elif anchor is not None and anchor.get("status") != "active":
        issues.append(diagnostic(
            code="anchor_retired", object_id=object_id, anchor_id=anchor_id,
            subject_owner="authored", responsible_owner="authored_bindings",
            measurement=state_measurement(str(anchor.get("status", "inactive")), "active"),
            allowed_actions=["restore_anchor", "rebind_explicit", "remove_binding"],
            message="Referenced anchor is not active and cannot be promoted.", binding_id=binding_id, expected_anchor_type=expected_type,
        ))
    elif anchor is not None:
        anchor_type = str(anchor.get("type", ""))
        if anchor_type not in ALLOWED_ANCHOR_TYPES:
            raise ValidationInputError(f"anchor {anchor_id} has invalid type")
        if anchor_type != expected_type:
            issues.append(diagnostic(
                code="wrong_anchor_kind", object_id=object_id, anchor_id=anchor_id,
                subject_owner="authored", responsible_owner="authored_bindings",
                measurement=state_measurement(anchor_type, expected_type),
                allowed_actions=["restore_anchor_type", "rebind_explicit", "remove_binding"],
                message="Anchor kind does not match the binding contract.", binding_id=binding_id, expected_anchor_type=expected_type,
            ))
        issues.extend(validate_wall_mount(item, anchor, bounds))

    if not sector_bounds.contains(bounds):
        issues.append(diagnostic(
            code="outside_sector", object_id=object_id, anchor_id=anchor_id,
            subject_owner="authored", responsible_owner="authored_content",
            measurement=state_measurement("outside", "inside sector bounds"),
            allowed_actions=["move_object", "change_sector_explicit"],
            message="Object bounds extend outside the declared sector.", binding_id=binding_id,
        ))
    space_id = item.get("space_id")
    if space_id:
        space = spaces.get(str(space_id))
        if space is None:
            raise ValidationInputError(f"object {object_id} references unknown space_id {space_id}")
        if str(space.get("sector_id", "")) != str(item.get("sector_id", "")):
            issues.append(diagnostic(
                code="wrong_sector", object_id=object_id, anchor_id=anchor_id,
                subject_owner="authored", responsible_owner="authored_content",
                measurement=state_measurement(str(item.get("sector_id", "")), str(space.get("sector_id", ""))),
                allowed_actions=["move_object", "change_space_explicit", "change_sector_explicit"],
                message="Object sector does not match its declared space.", binding_id=binding_id,
            ))
        space_bounds = Bounds.read(space.get("bounds"), f"space {space_id}.bounds")
        if not space_bounds.contains(bounds):
            issues.append(diagnostic(
                code="outside_space", object_id=object_id, anchor_id=anchor_id,
                subject_owner="authored", responsible_owner="authored_content",
                measurement=state_measurement("outside", "inside space bounds"),
                allowed_actions=["move_object", "change_space_explicit"],
                message="Object bounds extend outside the declared space.", binding_id=binding_id,
            ))

    support_found = False
    for infrastructure_id, infra in infrastructure.items():
        infra_bounds = Bounds.read(infra.get("bounds"), f"infrastructure {infrastructure_id}.bounds")
        kind = str(infra.get("kind", ""))
        penetration = bounds.penetration(infra_bounds)
        if penetration is not None:
            code = {
                "wall": "wall_penetration", "floor": "floor_penetration", "ceiling": "ceiling_penetration",
                "door_clearance": "door_blockage", "required_passage": "passage_blockage",
                "stair": "stair_conflict", "shaft": "shaft_conflict",
            }.get(kind)
            if code == "wall_penetration" and wall_integration_allows(item, infra, anchors, penetration):
                code = None
            if code:
                issues.append(diagnostic(
                    code=code, object_id=object_id, anchor_id=anchor_id,
                    subject_owner="authored", responsible_owner="authored_content",
                    measurement=penetration_measurement(penetration),
                    allowed_actions=["move_object", "resize_object", "rebind_explicit"],
                    message=f"Authored object intersects generated {kind} {infrastructure_id}.", binding_id=binding_id,
                ))
        if kind == "floor" and bounds.xz_overlap(infra_bounds):
            gap = bounds.minimum[1] - infra_bounds.maximum[1]
            if abs(gap) <= float(item.get("support_tolerance_m", 0.02)) + EPS:
                support_found = True
    if item.get("placement_mode") == "floor" and not support_found:
        issues.append(diagnostic(
            code="support_missing", object_id=object_id, anchor_id=anchor_id,
            subject_owner="authored", responsible_owner="authored_content",
            measurement={"actual": None, "limit": float(item.get("support_tolerance_m", 0.02)), "units": "m", "relation": "support_gap_lte"},
            allowed_actions=["move_object_to_support", "add_generated_support", "mark_non_floor_object"],
            message="No generated floor support exists below the floor-mounted object.", binding_id=binding_id,
        ))
    return issues


def wall_integration_allows(
    item: dict[str, Any], wall: dict[str, Any], anchors: dict[str, dict[str, Any]],
    penetration: tuple[float, float, float],
) -> bool:
    """Allow only an explicit, bounded mount or door-frame/wall integration."""
    integration = item.get("wall_integration")
    if not isinstance(integration, dict) or integration.get("mode") not in {"mounted", "door_frame"}:
        return False
    maximum = integration.get("max_depth_m")
    if not isinstance(maximum, (int, float)) or not math.isfinite(float(maximum)) or float(maximum) < 0.0:
        raise ValidationInputError(f"object {item['object_id']} has invalid wall_integration.max_depth_m")
    if integration["mode"] == "mounted" and wall.get("source_anchor_id") != item.get("anchor_id"):
        return False
    if integration["mode"] == "door_frame" and item.get("expected_anchor_type") != "door":
        return False
    wall_anchor = anchors.get(str(wall.get("source_anchor_id", "")))
    if wall_anchor is None:
        return False
    normal = read_vector(wall_anchor.get("normal"), f"wall anchor {wall.get('source_anchor_id')}.normal")
    horizontal_axis = 0 if abs(normal[0]) >= abs(normal[2]) else 2
    return penetration[horizontal_axis] <= float(maximum) + EPS


def validate_wall_mount(item: dict[str, Any], anchor: dict[str, Any], _object_bounds: Bounds) -> list[dict[str, Any]]:
    if item.get("placement_mode") != "wall" or anchor.get("type") != "wall":
        return []
    mount = item.get("mount_range")
    if not isinstance(mount, dict):
        raise ValidationInputError(f"wall-mounted object {item['object_id']} has no mount_range")
    anchor_bounds = anchor.get("bounds")
    if not isinstance(anchor_bounds, dict):
        raise ValidationInputError(f"wall anchor {anchor.get('anchor_id')} has no bounds")
    length_m = float(anchor_bounds.get("length_m", -1.0))
    height_m = float(anchor_bounds.get("height_m", -1.0))
    along = read_range(mount.get("along_m"), f"object {item['object_id']}.mount_range.along_m")
    height = read_range(mount.get("height_m"), f"object {item['object_id']}.mount_range.height_m")
    overflow = [max(0.0, -along[0]), max(0.0, along[1] - length_m), max(0.0, -height[0]), max(0.0, height[1] - height_m)]
    if max(overflow) <= EPS:
        return []
    return [diagnostic(
        code="wall_mount_out_of_bounds", object_id=str(item["object_id"]), anchor_id=str(anchor.get("anchor_id")),
        subject_owner="authored", responsible_owner="authored_bindings",
        measurement={"actual": [*along, *height], "limit": [0.0, length_m, 0.0, height_m], "units": "m", "relation": "range_inside"},
        allowed_actions=["adjust_placement", "resize_object", "rebind_explicit"],
        message="Wall-mounted footprint exceeds anchor length or height.", binding_id=str(item.get("binding_id")) if item.get("binding_id") else None,
        expected_anchor_type=str(item.get("expected_anchor_type", "wall")),
    )]


def read_range(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, (int, float)) for item in value):
        raise ValidationInputError(f"{label} must contain two numbers")
    result = float(value[0]), float(value[1])
    if result[0] > result[1]:
        raise ValidationInputError(f"{label} start must not exceed end")
    return result


def validate_generated_infrastructure(infrastructure: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    clearances = [(identity, item) for identity, item in infrastructure.items() if item.get("kind") in {"door_clearance", "required_passage"}]
    walls = [(identity, item) for identity, item in infrastructure.items() if item.get("kind") == "wall"]
    for wall_id, wall in walls:
        wall_bounds = Bounds.read(wall.get("bounds"), f"infrastructure {wall_id}.bounds")
        for clearance_id, clearance in clearances:
            penetration = wall_bounds.penetration(Bounds.read(clearance.get("bounds"), f"infrastructure {clearance_id}.bounds"))
            if penetration is None:
                continue
            code = "generated_door_blockage" if clearance.get("kind") == "door_clearance" else "generated_passage_blockage"
            issues.append(diagnostic(
                code=code, object_id=wall_id, anchor_id=None,
                subject_owner="generated", responsible_owner="generated",
                measurement=penetration_measurement(penetration),
                allowed_actions=["fix_generated_geometry", "regenerate_sector"],
                message=f"Generated wall intersects generated clearance {clearance_id}; no authored object is assigned responsibility.",
            ))
    return issues


def read_declared_warnings(value: Any) -> list[dict[str, Any]]:
    warnings = require_object_array(value, "declared_warnings")
    result: list[dict[str, Any]] = []
    for item in warnings:
        result.append(diagnostic(
            code=require_string(item, "code"), object_id=require_string(item, "object_id"),
            anchor_id=str(item["anchor_id"]) if item.get("anchor_id") else None,
            subject_owner=str(item.get("subject_owner", "authored")),
            responsible_owner=str(item.get("responsible_owner", "authored_content")),
            measurement=item.get("measurement", state_measurement("review", "review")),
            allowed_actions=list(item.get("allowed_actions", ["review"])),
            message=str(item.get("message", "Declared non-blocking warning.")), severity="warning",
        ))
    return result


def require_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValidationInputError(f"entry is missing {key}")
    return result


def load_physics_proof(path: Path, context: dict[str, Any]) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_id") != PHYSICS_SCHEMA_ID or document.get("schema_version") != SCHEMA_VERSION:
        raise ValidationInputError("unsupported physics proof schema")
    for key in ("map_id", "sector_id", "generation_id"):
        if document.get(key) != context[key]:
            raise ValidationInputError(f"physics proof {key} does not match input")
    issues: list[dict[str, Any]] = []
    for check in require_object_array(document.get("checks"), "physics checks"):
        if check.get("result") != "blocking":
            continue
        issues.append(diagnostic(
            code=require_string(check, "code"), object_id=require_string(check, "object_id"),
            anchor_id=str(check["anchor_id"]) if check.get("anchor_id") else None,
            subject_owner=str(check.get("subject_owner", "authored")),
            responsible_owner=str(check.get("responsible_owner", "authored_content")),
            measurement=check.get("measurement", state_measurement("failed", "clean")),
            allowed_actions=list(check.get("allowed_actions", ["move_object", "review_physics"])),
            message=str(check.get("message", "Runtime physics proof failed.")), proof_kind="runtime_physics",
            binding_id=str(check["binding_id"]) if check.get("binding_id") else None,
        ))
    return issues


def repair_reason(code: str) -> str:
    if code == "missing_anchor":
        return "anchor_missing"
    if code == "wrong_anchor_kind":
        return "anchor_type_changed"
    if code == "anchor_retired":
        return "anchor_retired"
    if code == "duplicate_object_id":
        return "object_id_ambiguous"
    if code == "duplicate_anchor_id":
        return "schema_incompatible"
    if code in {"outside_sector", "outside_space", "wrong_sector", "wall_mount_out_of_bounds"}:
        return "out_of_bounds"
    return "collision_detected"


def make_repair_queue(context: dict[str, Any], issues: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items_by_id: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if issue["severity"] != "blocking":
            continue
        reason = repair_reason(issue["code"])
        binding_identity = issue.get("binding_id") or f"UNBOUND::{issue['object_id']}"
        identity = [context["map_id"], context["sector_id"], binding_identity, issue.get("anchor_id"), reason, context["generation_id"]]
        repair_id = "RQ-" + hashlib.sha256(canonical_bytes(identity)).hexdigest()[:20].upper()
        evidence_item = {
            "code": issue["code"],
            "proof_kind": issue["evidence"]["proof_kind"],
            "message": issue["evidence"]["message"],
            "measurement": issue["measurement"],
        }
        if repair_id in items_by_id:
            item = items_by_id[repair_id]
            item["related_codes"] = sorted(set(item["related_codes"] + [issue["code"]]))
            item["allowed_actions"] = sorted(set(item["allowed_actions"] + issue["allowed_actions"]))
            item["evidence"]["diagnostics"].append(evidence_item)
            item["evidence"]["diagnostics"].sort(key=lambda value: (value["code"], canonical_bytes(value["measurement"])))
            continue
        items_by_id[repair_id] = {
            "repair_id": repair_id,
            "severity": "blocking",
            "status": "open",
            "reason": reason,
            "code": issue["code"],
            "related_codes": [issue["code"]],
            "binding_id": issue.get("binding_id"),
            "object_id": issue["object_id"],
            "previous_anchor_ref": {"anchor_id": issue.get("anchor_id"), "expected_type": issue.get("expected_anchor_type"), "geometry_hash": None},
            "evidence": {
                "source_generation_id": None,
                "target_generation_id": context["generation_id"],
                "proof_kind": issue["evidence"]["proof_kind"],
                "message": issue["evidence"]["message"],
                "measurement": issue["measurement"],
                "responsible_owner": issue["responsible_owner"],
                "diagnostics": [evidence_item],
            },
            "candidate_anchor_ids": [],
            "allowed_actions": issue["allowed_actions"],
            "resolution": None,
        }
    items = list(items_by_id.values())
    items.sort(key=lambda item: item["repair_id"])
    return {
        "schema_id": REPAIR_SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "contract_version": SCHEMA_VERSION,
        **context,
        "blocking_count": len(items),
        "items": items,
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Complex v3 composition validation",
        "",
        f"- Result: **{report['status']}**",
        f"- Blocking: {summary['blocking_count']}",
        f"- Warnings: {summary['warning_count']}",
        f"- Static geometry diagnostics: {summary['static_count']}",
        f"- Runtime physics diagnostics: {summary['runtime_physics_count']}",
        "",
    ]
    if not report["issues"]:
        lines.append("No open diagnostics.")
    else:
        lines.extend(["| Severity | Code | Object | Anchor | Owner | Proof |", "|---|---|---|---|---|---|"])
        for item in report["issues"]:
            lines.append(
                f"| {item['severity']} | `{item['code']}` | `{item['object_id']}` | "
                f"`{item.get('anchor_id') or '-'}` | `{item['responsible_owner']}` | `{item['evidence']['proof_kind']}` |"
            )
    lines.append("")
    return "\n".join(lines)


def write_outputs(output: Path, context: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    blocking_count = sum(item["severity"] == "blocking" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    report = {
        "schema_id": REPORT_SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        **context,
        "status": "blocked" if blocking_count else "clean",
        "summary": {
            "blocking_count": blocking_count,
            "warning_count": warning_count,
            "static_count": sum(item["evidence"]["proof_kind"] == "static_geometry" for item in issues),
            "runtime_physics_count": sum(item["evidence"]["proof_kind"] == "runtime_physics" for item in issues),
        },
        "issues": sorted(issues, key=lambda item: item["issue_id"]),
    }
    repair_queue = make_repair_queue(context, report["issues"])
    (output / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (output / "repair_queue.json").write_text(json.dumps(repair_queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (output / "validation_report.md").write_text(markdown_report(report), encoding="utf-8", newline="\n")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate map generated + authored composition")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--physics-proof", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        document = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValidationInputError("composition input root must be an object")
        issues, context = validate_document(document)
        if args.physics_proof:
            issues.extend(load_physics_proof(args.physics_proof, context))
        report = write_outputs(args.output, context, issues)
    except (OSError, json.JSONDecodeError, ValidationInputError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(
        f"{report['status'].upper()}: blocking={report['summary']['blocking_count']} "
        f"warnings={report['summary']['warning_count']} static={report['summary']['static_count']} "
        f"runtime_physics={report['summary']['runtime_physics_count']}"
    )
    return 0 if report["summary"]["blocking_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
