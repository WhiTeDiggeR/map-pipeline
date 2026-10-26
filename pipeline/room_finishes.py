#!/usr/bin/env python3
"""Room finishes: materials by room finish class, applied after generation.

The finish class of a room is part of the plan: build_canonical_from_approved.py writes it from room_finishes.json to the
canonical SVG (data-finish-class on the floor and ceiling of the room, data-wall-rooms on every wall and opening). This
stage runs after the converter, like vertical_closures.py, and points every floor, ceiling and wall mesh of the generated
architecture scene at the material of its room's class. The materials live outside the generated package (the registry names them),
so regeneration never overwrites them; a mesh without a class keeps the package material.

A wall mesh has one material: a wall between rooms of different classes takes the class with the smaller priority.

Usage (re-apply to the live package of one sector, e.g. after changing room_finishes.json materials):
    python <pipeline>/room_finishes.py --manifest <project manifest> --sector U-SECURITY [--check]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SURFACES = {"Floors/Visual": "floor", "Ceilings/Visual": "ceiling", "Walls/Visual": "wall"}
SVG_SURFACE = {"floor": "floor", "ceiling": "ceiling", "wall": "wall"}
FINISH_ID_PREFIX = "finish_"


class FinishError(ValueError):
    pass


def load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_registry(registry: dict[str, Any], project_root: Path) -> list[str]:
    errors: list[str] = []
    classes = registry.get("classes", {})
    priorities = [value.get("priority") for value in classes.values()]
    if len(set(priorities)) != len(priorities) or not all(isinstance(value, int) for value in priorities):
        errors.append("finish class priorities must be distinct integers")
    for name, value in classes.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            errors.append(f"{name}: finish class name must be a lowercase identifier")
        if not value.get("label"):
            errors.append(f"{name}: finish class needs a label")
        materials = value.get("materials", {})
        for surface in SURFACES.values():
            path = str(materials.get(surface, ""))
            if not path.startswith("res://") or path.startswith("res://gen/"):
                errors.append(f"{name}.{surface}: material must be a res:// path outside gen/")
            elif not (project_root / path.removeprefix("res://")).is_file():
                errors.append(f"{name}.{surface}: material is missing: {path}")
    for sector, entry in registry.get("sectors", {}).items():
        for room, value in [("default", entry.get("default", "")), *entry.get("rooms", {}).items()]:
            if value and value not in classes:
                errors.append(f"{sector}/{room}: unknown finish class {value!r}")
    return errors


def plan_finishes(svg_path: Path, registry: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Element id -> (surface, finish class) for every floor, ceiling and wall of a canonical SVG whose room has a class."""
    return plan_finishes_from_text(svg_path.read_text(encoding="utf-8"), registry, svg_path.name)


def plan_finishes_from_text(svg: str, registry: dict[str, Any], name: str = "plan") -> dict[str, tuple[str, str]]:
    classes = registry.get("classes", {})
    elements = [el for el in ET.fromstring(svg).iter() if el.get("data-godot-type") in SVG_SURFACE and el.get("id")]
    room_class: dict[str, str] = {}
    for el in elements:
        if el.get("data-godot-type") == "floor" and el.get("data-space-id") and el.get("data-finish-class"):
            room_class[el.get("data-space-id", "")] = el.get("data-finish-class", "")
    plan: dict[str, tuple[str, str]] = {}
    for el in elements:
        surface = SVG_SURFACE[el.get("data-godot-type", "")]
        if surface == "wall":
            found = [room_class[space] for space in el.get("data-wall-rooms", "").split("|") if space in room_class]
            value = min(found, key=lambda name: classes.get(name, {}).get("priority", 1 << 30)) if found else ""
        else:
            value = el.get("data-finish-class", "")
        if not value:
            continue
        if value not in classes:
            raise FinishError(f"{name}: {el.get('id')}: unknown finish class {value!r}")
        plan[el.get("id", "")] = (surface, value)
    return plan


def _sections(text: str) -> list[str]:
    """Split a text scene into its header and [section] blocks, keeping every byte."""
    parts = re.split(r"(?m)^(?=\[)", text)
    return [part for part in parts if part]


def apply(scene: Path, svg_path: Path, registry: dict[str, Any], write: bool = True) -> tuple[list[dict[str, str]], bool]:
    """Point the floor, ceiling and wall meshes of a generated scene at their finish materials. Idempotent: a mesh without
    a class gets the package material back. Returns the assignments (for the regeneration report) and whether the scene
    changed; with write=False the scene file is left as is."""
    text = scene.read_text(encoding="utf-8")
    updated, assignments = render(text, scene.name, plan_finishes(svg_path, registry), registry)
    if write and updated != text:
        scene.write_text(updated, encoding="utf-8", newline="\n")
    return assignments, updated != text


def render(text: str, name: str, plan: dict[str, tuple[str, str]], registry: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    classes = registry.get("classes", {})
    sections = _sections(text)
    package: dict[str, str] = {}
    for block in sections:
        match = re.match(r'\[ext_resource type="Material" path="[^"]*/(wall|floor|ceiling)_material\.tres" id="([^"]+)"\]', block)
        if match and not match.group(2).startswith(FINISH_ID_PREFIX):
            package[match.group(1)] = match.group(2)
    used: dict[str, str] = {}
    assignments: list[dict[str, str]] = []
    result: list[str] = []
    for block in sections:
        header = block.split("\n", 1)[0]
        node = re.match(r'\[node name="([^"]+)" type="MeshInstance3D" parent="([^"]+)"\]', header)
        if not node or node.group(2) not in SURFACES:
            result.append(block)
            continue
        surface = SURFACES[node.group(2)]
        source = re.search(r'(?m)^metadata/source_id = "([^"]*)"', block)
        target = plan.get(source.group(1)) if source else None
        if target and target[0] != surface:
            raise FinishError(f"{name}: {node.group(1)} is a {surface} mesh but its source is a {target[0]}")
        if target:
            material_id = f"{FINISH_ID_PREFIX}{target[1]}_{surface}"
            used[material_id] = classes[target[1]]["materials"][surface]
            assignments.append({"node": f"{node.group(2)}/{node.group(1)}", "source_id": source.group(1), "finish_class": target[1]})  # type: ignore[union-attr]
        elif surface in package:
            material_id = package[surface]
        else:
            raise FinishError(f"{name}: package {surface} material is missing")
        block = re.sub(r'(?m)^metadata/finish_class = "[^"]*"\n', "", block)
        block, count = re.subn(r'(?m)^material_override = ExtResource\("[^"]+"\)$', f'material_override = ExtResource("{material_id}")', block)
        if count != 1:
            raise FinishError(f"{name}: {node.group(1)} has no single material_override")
        if target:
            block = re.sub(r'(?m)^(metadata/source_id = "[^"]*")$', rf'\1\nmetadata/finish_class = "{target[1]}"', block, count=1)
        result.append(block)
    # finish material resources: drop the old ones, declare the used ones after the package materials
    result = [block for block in result if not re.match(rf'\[ext_resource type="Material" path="[^"]*" id="{FINISH_ID_PREFIX}', block)]
    declared = [f'[ext_resource type="Material" path="{path}" id="{material_id}"]\n\n' for material_id, path in sorted(used.items())]
    first_ext = next((i for i, block in enumerate(result) if block.startswith("[ext_resource")), 1)
    result[first_ext:first_ext] = declared
    updated = "".join(result)
    resources = len(re.findall(r"(?m)^\[(?:ext|sub)_resource ", updated))
    updated = re.sub(r"load_steps=\d+", f"load_steps={resources + 1}", updated, count=1)
    return updated, assignments


def describe(assignments: list[dict[str, str]]) -> dict[str, Any]:
    by_class: dict[str, int] = {}
    for item in assignments:
        by_class[item["finish_class"]] = by_class.get(item["finish_class"], 0) + 1
    return {"meshes": len(assignments), "by_class": dict(sorted(by_class.items()))}


def _live_paths(sector_id: str, manifest_path: Path, project_root: Path) -> tuple[Path, Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sector = next((item for item in manifest["sectors"] if item["sector_id"] == sector_id), None)
    if sector is None:
        raise FinishError(f"unknown sector {sector_id}")
    package = project_root / sector["output_resource_dir"].removeprefix("res://")
    return package / "Generated" / "Architecture" / f"{sector['scene_name']}.tscn", project_root / sector["source_svg"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sector", action="append", required=True, help="sector ID (repeatable)")
    parser.add_argument("--manifest", required=True, help="sector generation manifest; its finish_registry names the registry")
    parser.add_argument("--check", action="store_true", help="only report whether the live scene matches the finishes")
    args = parser.parse_args(argv)
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    project_root = (manifest_path.parent / manifest.get("project_root", ".")).resolve()
    if not manifest.get("finish_registry"):
        print("the manifest names no finish_registry", file=sys.stderr)
        return 2
    registry = load_registry(project_root / manifest["finish_registry"])
    errors = validate_registry(registry, project_root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    stale = []
    for sector_id in args.sector:
        scene, svg = _live_paths(sector_id, manifest_path, project_root)
        assignments, changed = apply(scene, svg, registry, write=not args.check)
        if args.check and changed:
            stale.append(sector_id)
        print(f"{sector_id:22} {json.dumps(describe(assignments), ensure_ascii=False)}")
    if stale:
        print("finishes are not applied: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
