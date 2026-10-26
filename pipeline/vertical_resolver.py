"""Derive vertical (stair) generator geometry from SVG markup.

The SVG plans are the source of truth for the shaft footprint, the entry and exit doors and the
level heights. A manifest vertical generator with ``"source": "svg"`` keeps only non-geometric
choices; this module computes the generator arguments and the ``local_to_world`` transform.
Markup contract: docs/vertical-markup.md of map-pipeline
"""

from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BOUNDARY_TOLERANCE_M = 0.25
FOOTPRINT_TOLERANCE_M = 0.001
WIDTH_TOLERANCE_M = 0.01
DEFAULT_CLEARANCE_M = 0.01
OPENING_TYPES = {"floor-opening", "ceiling-opening"}
SIDES = {"north", "east", "south", "west"}
# The project maps generator-local axes to world axes by a 180 degree turn about Y:
# local north (+z) is world north (-z), local east (+x) is world west (-x).
LOCAL_SIDE = {"north": "north", "south": "south", "east": "west", "west": "east"}
# Unit step from a boundary side into the shaft, as (dx, dz) in world axes (+z is south).
INWARD = {"north": (0.0, 1.0), "south": (0.0, -1.0), "west": (1.0, 0.0), "east": (-1.0, 0.0)}
ROTATION = {"basis_x": [-1, 0, 0], "basis_y": [0, 1, 0], "basis_z": [0, 0, -1]}
GEOMETRIC_ARGS = {
    "--shaft-width", "--shaft-length", "--floor-height", "--layout", "--stair-width",
    "--lower-entry-side", "--upper-exit-side", "--shaft-wall-bottom", "--shaft-wall-top",
    "--turn-direction", "--invert-z", "--origin", "--no-shaft", "--landing-depth",
}


class VerticalError(ValueError):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number(element: ET.Element, name: str, label: str) -> float:
    try:
        value = float(element.attrib[name])
    except (KeyError, ValueError) as exc:
        raise VerticalError(f"{label}: attribute {name} is missing or not a number") from exc
    if not math.isfinite(value):
        raise VerticalError(f"{label}: attribute {name} is not finite")
    return value


def _format(value: float) -> str:
    return f"{round(value, 6):g}"


def read_markup(svg_path: Path, vertical_id: str) -> dict[str, list[dict[str, Any]]]:
    """Return the shaft rectangles and entry/exit doors marked for one vertical in an SVG."""
    root = ET.parse(svg_path).getroot()
    shafts: list[dict[str, Any]] = []
    doors: list[dict[str, Any]] = []
    for element in root.iter():
        if element.attrib.get("data-vertical-id") != vertical_id:
            continue
        element_id = element.attrib.get("id", "?")
        label = f"{svg_path.name}#{element_id}"
        role = element.attrib.get("data-vertical-role")
        kind = element.attrib.get("data-godot-type")
        tag = _local_name(element.tag)
        if role == "shaft":
            if tag != "rect" or kind not in OPENING_TYPES:
                raise VerticalError(f"{label}: a shaft must be a rect typed floor-opening or ceiling-opening")
            x, z = _number(element, "x", label), _number(element, "y", label)
            width, depth = _number(element, "width", label), _number(element, "height", label)
            if width <= 0 or depth <= 0:
                raise VerticalError(f"{label}: shaft size must be positive")
            shafts.append({"id": element_id, "kind": kind, "rect": (x, z, x + width, z + depth)})
        elif role in {"entry", "exit"}:
            if tag != "line" or kind != "door":
                raise VerticalError(f"{label}: an {role} must be a line typed door")
            doors.append({
                "id": element_id, "role": role,
                "line": (_number(element, "x1", label), _number(element, "y1", label), _number(element, "x2", label), _number(element, "y2", label)),
            })
        else:
            raise VerticalError(f"{label}: data-vertical-role must be shaft, entry or exit")
    return {"shafts": shafts, "doors": doors}


def _single(items: list[dict[str, Any]], what: str, svg_name: str) -> dict[str, Any]:
    if len(items) != 1:
        raise VerticalError(f"{svg_name}: expected exactly one {what}, found {len(items)}")
    return items[0]


def door_side(door: dict[str, Any], rect: tuple[float, float, float, float]) -> tuple[str, float]:
    """Return the shaft side a door line lies on and the door width."""
    x0, z0, x1, z1 = rect
    ax, ay, bx, by = door["line"]
    if abs(ay - by) < 1e-6 and min(ax, bx) >= x0 - BOUNDARY_TOLERANCE_M and max(ax, bx) <= x1 + BOUNDARY_TOLERANCE_M:
        if abs(ay - z0) <= BOUNDARY_TOLERANCE_M:
            return "north", abs(bx - ax)
        if abs(ay - z1) <= BOUNDARY_TOLERANCE_M:
            return "south", abs(bx - ax)
    if abs(ax - bx) < 1e-6 and min(ay, by) >= z0 - BOUNDARY_TOLERANCE_M and max(ay, by) <= z1 + BOUNDARY_TOLERANCE_M:
        if abs(ax - x0) <= BOUNDARY_TOLERANCE_M:
            return "west", abs(by - ay)
        if abs(ax - x1) <= BOUNDARY_TOLERANCE_M:
            return "east", abs(by - ay)
    raise VerticalError(f"door {door['id']} does not lie on a side of the shaft {tuple(round(v, 3) for v in rect)}")


def _sector(sectors: dict[str, dict[str, Any]], sector_id: str, vertical_id: str) -> dict[str, Any]:
    if sector_id not in sectors:
        raise VerticalError(f"{vertical_id}: unknown sector {sector_id}")
    return sectors[sector_id]


def _shared_value(sector: dict[str, Any], flag: str) -> float:
    arguments = sector.get("shared_args", [])
    if flag not in arguments or arguments.index(flag) + 1 >= len(arguments):
        raise VerticalError(f"sector {sector['sector_id']} has no {flag} in shared_args")
    return float(arguments[arguments.index(flag) + 1])



# Lateral half of the shaft used by the lower entry flight of a u-turn stair, per entry side, as the generator lays it out
# (calibrated against generated packages; check_stair_fit.py verifies it on the result).
ENTRY_HALF = {"north": -1.0, "south": 1.0, "east": -1.0, "west": 1.0}
DEFAULT_FLIGHT_GAP_M = 0.10  # generate_godot_stairs default of --flight-gap
DOOR_CENTER_TOLERANCE_M = 0.15
MIN_LANDING_DEPTH_FACTOR = 1.0


def _arg(arguments: list[str], flag: str, default: float) -> float:
    if flag in arguments and arguments.index(flag) + 1 < len(arguments):
        return float(arguments[arguments.index(flag) + 1])
    return default


def u_turn_fit(rect: tuple[float, float, float, float], entry_side: str, floor_height: float, stair_width: float,
               arguments: list[str], clearance: float) -> dict[str, Any]:
    """Landing depth that makes a u-turn stair span the whole shaft, and the lateral centres of its two flights.

    The generator lays flights of ``ceil(risers / 2) * tread`` plus one landing along the entry axis, so a shaft longer than that leaves
    a gap between the stair and the entry door. The landing absorbs the difference; the lateral centres are where the entry and exit
    doors must be.
    """
    x0, z0, x1, z1 = rect
    along_ns = entry_side in {"north", "south"}
    # the clearance is taken off the across-axis only (see resolve_stair), the along-axis keeps the full plan length
    along = z1 - z0 if along_ns else x1 - x0
    across_lo, across_hi = (x0, x1) if along_ns else (z0, z1)
    target_riser = _arg(arguments, "--target-riser", 0.17)  # generator defaults
    max_riser = _arg(arguments, "--max-riser", 0.19)
    tread = _arg(arguments, "--target-tread", 0.28)
    gap = _arg(arguments, "--flight-gap", DEFAULT_FLIGHT_GAP_M)
    risers = max(2, round(floor_height / target_riser), math.ceil(floor_height / max_riser - 1e-9))
    run = math.ceil(risers / 2) * tread
    landing = math.floor((along - run) * 1000.0) / 1000.0
    if landing < stair_width * MIN_LANDING_DEPTH_FACTOR:
        raise VerticalError(f"shaft is too short for a u-turn stair: along-axis length {along:.3f} m, flight run {run:.3f} m, "
                            f"landing would be {landing:.3f} m (< {stair_width:.3f} m)")
    across = across_hi - across_lo
    if 2 * stair_width + gap > across + 1e-6:
        raise VerticalError(f"shaft is too narrow for two flights: across-axis width {across:.3f} m, needs {2 * stair_width + gap:.3f} m")
    centre = (across_lo + across_hi) / 2
    offset = (stair_width + gap) / 2
    entry_c = centre + ENTRY_HALF[entry_side] * offset
    return {"landing_depth": landing, "flight_run": run, "entry_center": entry_c, "exit_center": 2 * centre - entry_c, "lateral_axis": "x" if along_ns else "z"}


def resolve_stair(entry: dict[str, Any], sectors: dict[str, dict[str, Any]], project_root: Path, strict: bool = True) -> dict[str, Any]:
    vertical_id = str(entry.get("vertical_id", ""))
    if not vertical_id:
        raise VerticalError("a vertical generator with source=svg requires vertical_id")
    levels = entry.get("levels", {})
    upper = _sector(sectors, str(levels.get("upper", {}).get("sector_id", "")), vertical_id)
    lower = _sector(sectors, str(levels.get("lower", {}).get("sector_id", "")), vertical_id)
    fixed = sorted(GEOMETRIC_ARGS.intersection(entry.get("args", [])))
    if fixed:
        raise VerticalError(f"{vertical_id}: {', '.join(fixed)} are derived from the plan and must not be set in args")
    upper_name, lower_name = Path(upper["source_svg"]).name, Path(lower["source_svg"]).name
    upper_markup = read_markup(project_root / upper["source_svg"], vertical_id)
    lower_markup = read_markup(project_root / lower["source_svg"], vertical_id)
    upper_rect = _single(upper_markup["shafts"], "shaft opening", upper_name)["rect"]
    lower_rect = _single(lower_markup["shafts"], "shaft opening", lower_name)["rect"]
    if any(abs(a - b) > FOOTPRINT_TOLERANCE_M for a, b in zip(upper_rect, lower_rect)):
        raise VerticalError(
            f"{vertical_id}: shaft footprints differ between levels: {upper_name} {tuple(round(v, 3) for v in upper_rect)} "
            f"vs {lower_name} {tuple(round(v, 3) for v in lower_rect)}"
        )
    exit_door = _single([d for d in upper_markup["doors"] if d["role"] == "exit"], "exit door", upper_name)
    entry_door = _single([d for d in lower_markup["doors"] if d["role"] == "entry"], "entry door", lower_name)
    exit_side, exit_width = door_side(exit_door, upper_rect)
    entry_side, entry_width = door_side(entry_door, upper_rect)
    if abs(exit_width - entry_width) > WIDTH_TOLERANCE_M:
        raise VerticalError(f"{vertical_id}: entry door is {entry_width:.3f} m wide but exit door is {exit_width:.3f} m; they must match")
    if exit_side == entry_side:
        layout = "u-turn"
    elif exit_side == {"north": "south", "south": "north", "east": "west", "west": "east"}[entry_side]:
        layout = "straight"
    else:
        layout = "l-turn"
    lower_y = float(lower["metric_settings"]["elevation_m"])
    upper_y = float(upper["metric_settings"]["elevation_m"])
    floor_height = upper_y - lower_y
    if floor_height <= 0:
        raise VerticalError(f"{vertical_id}: the upper level must be above the lower level")
    # The shaft walls start at the top of the lower walls (the underside of its ceiling) so they overlap the ceiling slab:
    # there is no gap at the joint when the ceilings are hidden or cut.
    bottom = _shared_value(lower, "--wall-height")
    if bottom >= floor_height:
        raise VerticalError(f"{vertical_id}: lower walls ({bottom}) reach the upper floor ({floor_height})")
    clearance = float(entry.get("clearance_m", DEFAULT_CLEARANCE_M))
    x0, z0, x1, z1 = upper_rect
    width, length = x1 - x0, z1 - z0
    if entry_side in {"north", "south"}:
        width -= 2 * clearance
    else:
        length -= 2 * clearance
    dx, dz = INWARD[entry_side]
    centre = ((x0 + x1) / 2 + dx * clearance, (z0 + z1) / 2 + dz * clearance)
    fit_args: list[str] = []
    door_targets: dict[str, Any] = {}
    if layout == "u-turn":
        fit = u_turn_fit(upper_rect, entry_side, floor_height, entry_width, entry.get("args", []), clearance)
        axis = 0 if fit["lateral_axis"] == "x" else 1
        for role, door, key in (("entry", entry_door, "entry_center"), ("exit", exit_door, "exit_center")):
            line = door["line"]
            centre_door = (line[axis] + line[axis + 2]) / 2
            door_targets[role] = {"door_id": door["id"], "sector_id": (lower if role == "entry" else upper)["sector_id"], "axis": fit["lateral_axis"],
                                  "actual_center": round(centre_door, 3), "expected_center": round(fit[key], 3), "line": list(line)}
            if strict and abs(centre_door - fit[key]) > DOOR_CENTER_TOLERANCE_M:
                raise VerticalError(
                    f"{vertical_id}: {role} door {door['id']} is centred at {centre_door:.3f} but the {role} flight of the stair is at "
                    f"{fit[key]:.3f} ({fit['lateral_axis']}); move the door or the stair will not meet it"
                )
        fit_args = ["--landing-depth", _format(fit["landing_depth"])]
    arguments = [
        "--shaft-width", _format(width), "--shaft-length", _format(length), "--floor-height", _format(floor_height),
        "--layout", layout, "--stair-width", _format(entry_width),
        "--lower-entry-side", LOCAL_SIDE[entry_side], "--upper-exit-side", LOCAL_SIDE[exit_side],
        # shaft walls come from vertical_closures (lining with the room wall material), not from the stair generator
        "--no-shaft",
        *fit_args,
        *entry.get("args", []),
    ]
    transform = {"origin": [round(centre[0], 6), lower_y, round(centre[1], 6)], **copy.deepcopy(ROTATION)}
    summary = {
        "vertical_id": vertical_id, "layout": layout, "entry_side": entry_side, "exit_side": exit_side,
        "shaft_m": [round(x1 - x0, 6), round(z1 - z0, 6)], "stair_width_m": round(entry_width, 6),
        "floor_height_m": round(floor_height, 6), "clearance_m": clearance,
        "door_targets": door_targets,
    }
    return {"args": arguments, "local_to_world": transform, "summary": summary}


def resolve_sector_verticals(sector: dict[str, Any], manifest: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Return the sector with every ``source: svg`` vertical generator resolved from the plans."""
    generators = sector.get("vertical_generators", [])
    if not any(isinstance(item, dict) and item.get("source") == "svg" for item in generators):
        return sector
    sectors = {item["sector_id"]: item for item in manifest.get("sectors", []) if isinstance(item, dict) and "sector_id" in item}
    resolved = copy.deepcopy(sector)
    for generator in resolved["vertical_generators"]:
        if generator.get("source") != "svg":
            continue
        result = resolve_stair(generator, sectors, project_root)
        generator["args"] = result["args"]
        generator["local_to_world"] = result["local_to_world"]
        generator["resolved"] = result["summary"]
    return resolved
