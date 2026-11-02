"""Close the gaps that the plan converter and the stair generator leave around vertical openings.

Every floor opening of a sector (a stair or lift shaft marked with data-vertical-id) cuts through the slab between two levels:
the lower room's walls stop at its wall height, the upper floor starts one slab higher, and nothing covers the shaft sides in
between. This module derives, from the canonical SVG plans only, boxes that the regenerator adds to the sector architecture:

* lining   - four thin plates around every floor opening, from the top of the lower walls up to the upper floor. Their inner faces
             lie exactly on the edges of the opening rectangle, which the plans draw on the inner faces of the shaft walls, so the
             walls below, the lining and the walls above make one flat shaft side (caretaker #272). The slabs of both levels are cut
             SLAB_RECESS_M wider than the rectangle (data-floor-edge-offset / data-ceiling-edge-offset on the openings), so their
             cut edges stand behind the plates instead of in their plane (coplanar faces flicker) and no slab sticks into the shaft;
* threshold - for a stair hosted by the sector, the strip of upper floor between the last tread and the edge of the opening on
              the exit side (the generator ends the flight one tread short of the shaft wall, the floor opening is cut to it).

The boxes carry the package wall / floor material, so shafts look like the rooms around them, and get box collisions.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

# The plans cut both slabs of a shaft this much wider than the opening rectangle (data-floor-edge-offset on the floor opening,
# data-ceiling-edge-offset on the ceiling opening); the lining plates fill that recess.
SLAB_RECESS_M = 0.008
LINING_THICKNESS_M = 0.006  # thinner than the recess: the plates never touch the slab edges behind them
# The threshold bottom stays this much above the floor underside, so the two bottom faces are not coplanar (coplanar faces flicker).
Z_FIGHT_OFFSET_M = 0.02  # well above the 5 mm coplanar tolerance of surface_conflict_check.gd
FLOOR_THICKNESS_M = 0.2
MIN_SIZE_M = 0.005
EDGE_GAP_M = 0.001  # between the threshold end and the recessed slab edge: touching faces would only be an edge, not a seam


@dataclass(frozen=True)
class Box:
    name: str
    kind: str          # "lining" | "threshold"
    center: tuple[float, float, float]
    size: tuple[float, float, float]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def openings(svg_path: Path, kind: str) -> list[dict[str, Any]]:
    """Return the vertical openings of one kind ("floor-opening" / "ceiling-opening") of a canonical SVG, in metres."""
    found = []
    for element in ET.parse(svg_path).getroot().iter():
        if _local(element.tag) != "rect" or element.get("data-godot-type") != kind or not element.get("data-vertical-id"):
            continue
        x, z = float(element.get("x", 0)), float(element.get("y", 0))
        found.append({"id": element.get("id", ""), "vertical_id": element.get("data-vertical-id"),
                      "rect": (x, z, x + float(element.get("width", 0)), z + float(element.get("height", 0)))})
    return found


def _arg(arguments: Sequence[str], flag: str) -> float:
    return float(arguments[list(arguments).index(flag) + 1])


def _same_rect(a: Sequence[float], b: Sequence[float], tolerance: float = 0.05) -> bool:
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


def lower_sector(manifest: dict[str, Any], project_root: Path, opening: dict[str, Any], elevation: float) -> dict[str, Any] | None:
    """The sector directly below whose ceiling opening matches this floor opening."""
    best = None
    for sector in manifest.get("sectors", []):
        lower_elevation = float(sector["metric_settings"]["elevation_m"])
        if lower_elevation >= elevation - 1e-6:
            continue
        for candidate in openings(project_root / sector["source_svg"], "ceiling-opening"):
            if candidate["vertical_id"] == opening["vertical_id"] and _same_rect(candidate["rect"], opening["rect"]):
                if best is None or lower_elevation > float(best["metric_settings"]["elevation_m"]):
                    best = sector
    return best


def lining_boxes(rect: Sequence[float], bottom: float, top: float, prefix: str, low_sides: frozenset[str] = frozenset(),
                 low_top: float | None = None) -> list[Box]:
    """Four plates around the opening, inner faces on its edges, from the top of the lower walls (bottom) to the upper floor (top),
    so they meet the walls of both levels edge to edge. Sides in low_sides (the exit side of a stair) stop at low_top, under the exit
    threshold, which closes that side above it."""
    x0, z0, x1, z1 = rect
    t = LINING_THICKNESS_M
    xa, xb = x0 - t, x1 + t          # north / south plates run over the corners
    sides = {
        "N": (((xa + xb) / 2, z0 - t / 2), (xb - xa, t)),
        "S": (((xa + xb) / 2, z1 + t / 2), (xb - xa, t)),
        "W": ((x0 - t / 2, (z0 + z1) / 2), (t, z1 - z0)),
        "E": ((x1 + t / 2, (z0 + z1) / 2), (t, z1 - z0)),
    }
    boxes = []
    for side, ((cx, cz), (sx, sz)) in sides.items():
        side_top = low_top if side in low_sides and low_top is not None else top
        height = side_top - bottom
        if height <= MIN_SIZE_M:
            continue
        boxes.append(Box(f"{prefix}_{side}", "lining", (cx, (bottom + side_top) / 2, cz), (sx, height, sz)))
    return boxes


def threshold_bottom(elevation: float) -> float:
    """Underside of the exit threshold: a little above the floor underside, so the two bottom faces are not coplanar."""
    return elevation - FLOOR_THICKNESS_M + Z_FIGHT_OFFSET_M


def threshold_box(rect: Sequence[float], exit_side: str, exit_point: Sequence[float], elevation: float, name: str) -> Box | None:
    """Upper-floor strip between the end of the exit flight and the recessed slab edge on the exit side, across the opening between
    the inner faces of the side plates. Its top is flush with the floor."""
    x0, z0, x1, z1 = rect
    reach = SLAB_RECESS_M - EDGE_GAP_M  # past the edge, over the exit plate, up to the slab cut
    ex, ez = exit_point[0], exit_point[2]
    if exit_side == "north":
        lo, hi, across = z0 - reach, min(max(ez, z0), z1), "x"
    elif exit_side == "south":
        lo, hi, across = max(min(ez, z1), z0), z1 + reach, "x"
    elif exit_side == "west":
        lo, hi, across = x0 - reach, min(max(ex, x0), x1), "z"
    else:
        lo, hi, across = max(min(ex, x1), x0), x1 + reach, "z"
    depth = hi - lo
    if depth <= reach + MIN_SIZE_M:
        return None
    bottom = threshold_bottom(elevation)
    y, thickness = (bottom + elevation) / 2, elevation - bottom
    if across == "x":
        return Box(name, "threshold", ((x0 + x1) / 2, y, (lo + hi) / 2), (x1 - x0, thickness, depth))
    return Box(name, "threshold", ((lo + hi) / 2, y, (z0 + z1) / 2), (depth, thickness, z1 - z0))


def _world(local_to_world: dict[str, Any], point: Sequence[float]) -> tuple[float, float, float]:
    o, bx, by, bz = (local_to_world[key] for key in ("origin", "basis_x", "basis_y", "basis_z"))
    return tuple(o[i] + bx[i] * point[0] + by[i] * point[1] + bz[i] * point[2] for i in range(3))  # type: ignore[return-value]


def plan_closures(sector: dict[str, Any], manifest: dict[str, Any], project_root: Path,
                  stair_reports: Sequence[dict[str, Any]] = ()) -> list[Box]:
    elevation = float(sector["metric_settings"]["elevation_m"])
    floor_openings = openings(project_root / sector["source_svg"], "floor-opening")
    side_letter = {"north": "N", "south": "S", "west": "W", "east": "E"}
    by_vertical = {opening["vertical_id"]: opening for opening in floor_openings}
    thresholds: dict[str, tuple[str, Box]] = {}
    for generator, report in zip(sector.get("vertical_generators", []), stair_reports):
        opening = by_vertical.get(generator.get("vertical_id"))
        resolved = generator.get("resolved", {})
        if opening is None or not resolved:
            continue
        exits = [frame for frame in report.get("anchor_frames", []) if frame.get("role") == "upper_exit"]
        if not exits:
            continue
        point = _world(generator["local_to_world"], exits[0]["origin"])
        box = threshold_box(opening["rect"], resolved["exit_side"], point, elevation, f"Threshold_{generator['vertical_id']}")
        if box is not None:
            thresholds[generator["vertical_id"]] = (side_letter[resolved["exit_side"]], box)
    boxes: list[Box] = []
    for index, opening in enumerate(floor_openings, start=1):
        below = lower_sector(manifest, project_root, opening, elevation)
        if below is None:
            raise ValueError(f"{sector['sector_id']}: floor opening {opening['id']} ({opening['vertical_id']}) has no matching ceiling opening below")
        bottom = float(below["metric_settings"]["elevation_m"]) + _arg(below["shared_args"], "--wall-height")
        # up to the upper floor; on the exit side of a stair only up to the underside of the threshold, which closes it above
        exit_side, _ = thresholds.get(opening["vertical_id"], (None, None))
        boxes.extend(lining_boxes(opening["rect"], bottom, elevation, f"Lining_{index:02d}_{opening['vertical_id']}",
                                  frozenset({exit_side}) if exit_side else frozenset(), threshold_bottom(elevation)))
    boxes.extend(box for _, box in thresholds.values())
    return boxes


def _vector(values: Sequence[float]) -> str:
    return "Vector3(%s)" % ", ".join(format(round(v, 6), "g") for v in values)


def inject(scene: Path, boxes: Sequence[Box]) -> None:
    """Add the boxes to a generated architecture scene as a Closures layer (Visual meshes + Collisions)."""
    if not boxes:
        return
    text = scene.read_text(encoding="utf-8")
    materials = dict(re.findall(r'\[ext_resource type="Material" path="[^"]*/(wall|floor)_material\.tres" id="([^"]+)"\]', text))
    if "wall" not in materials or "floor" not in materials:
        raise ValueError(f"{scene.name}: wall/floor material resources are missing")
    resources, nodes = [], ['[node name="Closures" type="Node3D" parent="."]', "", '[node name="Visual" type="Node3D" parent="Closures"]', "",
                            '[node name="Collisions" type="Node3D" parent="Closures"]', ""]
    for index, box in enumerate(boxes, start=1):
        size = _vector(box.size)
        resources += [f'[sub_resource type="BoxMesh" id="ClosureMesh_{index}"]', f"size = {size}", "",
                      f'[sub_resource type="BoxShape3D" id="ClosureShape_{index}"]', f"size = {size}", ""]
        material = materials["wall" if box.kind == "lining" else "floor"]
        nodes += [f'[node name="{box.name}" type="MeshInstance3D" parent="Closures/Visual"]', f"position = {_vector(box.center)}",
                  f'mesh = SubResource("ClosureMesh_{index}")', f'material_override = ExtResource("{material}")',
                  f'metadata/closure_kind = "{box.kind}"', "",
                  f'[node name="{box.name}" type="StaticBody3D" parent="Closures/Collisions"]', f"position = {_vector(box.center)}",
                  "collision_layer = 1", "collision_mask = 1", f'metadata/closure_kind = "{box.kind}"', "",
                  f'[node name="CollisionShape3D" type="CollisionShape3D" parent="Closures/Collisions/{box.name}"]',
                  f'shape = SubResource("ClosureShape_{index}")', ""]
    first_node = text.index("\n[node ") + 1
    text = text[:first_node] + "\n".join(resources) + "\n" + text[first_node:].rstrip("\n") + "\n\n" + "\n".join(nodes)
    text = re.sub(r"load_steps=(\d+)", lambda m: f"load_steps={int(m.group(1)) + 2 * len(boxes)}", text, count=1)
    scene.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")


def describe(boxes: Sequence[Box]) -> list[dict[str, Any]]:
    return [{"name": b.name, "kind": b.kind, "center": [round(v, 4) for v in b.center], "size": [round(v, 4) for v in b.size]} for b in boxes]


def is_finite(boxes: Sequence[Box]) -> bool:
    return all(math.isfinite(v) for b in boxes for v in (*b.center, *b.size))
