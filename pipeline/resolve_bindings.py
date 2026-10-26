#!/usr/bin/env python3
"""Resolve T01 object bindings into canonical world-space validator bounds."""

from __future__ import annotations

import copy
import math
from typing import Any, Sequence


EPS = 1.0e-6
LINEAR_TYPES = {"wall", "door", "stair_entry", "stair_exit"}
SURFACE_TYPES = {"floor", "ceiling"}


class BindingResolutionError(ValueError):
    pass


def vector(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3 or not all(
        isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in value
    ):
        raise BindingResolutionError(f"{label} must contain three finite numbers")
    return tuple(float(item) for item in value)  # type: ignore[return-value]


def finite(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise BindingResolutionError(f"{label} must be finite")
    return float(value)


def interval(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise BindingResolutionError(f"{label} must be [min, max]")
    result = finite(value[0], label), finite(value[1], label)
    if result[0] > result[1]:
        raise BindingResolutionError(f"{label} min exceeds max")
    return result


def add(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return tuple(a[index] + b[index] for index in range(3))  # type: ignore[return-value]


def scale(a: Sequence[float], amount: float) -> tuple[float, float, float]:
    return tuple(item * amount for item in a)  # type: ignore[return-value]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(a[index] * b[index] for index in range(3))


def cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def matrix_multiply(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(sum(left[row][item] * right[item][column] for item in range(3)) for column in range(3)) for row in range(3))


def euler_yxz(value: Sequence[float]) -> tuple[tuple[float, ...], ...]:
    yaw, pitch, roll = (math.radians(item) for item in value)
    cy, sy, cx, sx, cz, sz = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch), math.cos(roll), math.sin(roll)
    rotate_y = ((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy))
    rotate_x = ((1.0, 0.0, 0.0), (0.0, cx, -sx), (0.0, sx, cx))
    rotate_z = ((cz, -sz, 0.0), (sz, cz, 0.0), (0.0, 0.0, 1.0))
    return matrix_multiply(matrix_multiply(rotate_y, rotate_x), rotate_z)


def basis_matrix(columns: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(columns[column][row] for column in range(3)) for row in range(3))


def transform_vector(matrix: Sequence[Sequence[float]], value: Sequence[float]) -> tuple[float, float, float]:
    return tuple(sum(matrix[row][column] * value[column] for column in range(3)) for row in range(3))  # type: ignore[return-value]


def axis_value(policy: Any, bounds: tuple[float, float], label: str) -> float:
    if not isinstance(policy, dict) or not isinstance(policy.get("policy"), str):
        raise BindingResolutionError(f"{label} policy is missing")
    kind = policy["policy"]
    if kind == "normalized":
        fraction = finite(policy.get("value"), f"{label}.value")
        if fraction < 0.0 or fraction > 1.0:
            raise BindingResolutionError(f"{label}.value must be in [0, 1]")
        result = bounds[0] + (bounds[1] - bounds[0]) * fraction
    elif kind in {"from_start_m", "from_end_m"}:
        distance = finite(policy.get("distance_m"), f"{label}.distance_m")
        if distance < 0.0:
            raise BindingResolutionError(f"{label}.distance_m must be nonnegative")
        result = bounds[0] + distance if kind == "from_start_m" else bounds[1] - distance
    elif kind == "centered":
        offset = finite(policy.get("offset_m", 0.0), f"{label}.offset_m")
        result = (bounds[0] + bounds[1]) * 0.5 + offset
    else:
        raise BindingResolutionError(f"{label} has unknown policy {kind}")
    if result < bounds[0] - EPS or result > bounds[1] + EPS:
        raise BindingResolutionError(f"{label} is outside anchor bounds")
    return result


def rotation(placement: dict[str, Any], limits: dict[str, Any]) -> tuple[float, float, float]:
    raw = placement.get("rotation")
    if not isinstance(raw, dict) or raw.get("representation") != "euler_deg" or raw.get("order") != "YXZ":
        raise BindingResolutionError("rotation must be euler_deg/YXZ")
    values = vector(raw.get("yaw_pitch_roll"), "rotation.yaw_pitch_roll")
    raw_limits = limits.get("rotation_deg")
    if not isinstance(raw_limits, dict):
        raise BindingResolutionError("rotation limits are missing")
    for name, value in zip(("yaw", "pitch", "roll"), values):
        minimum, maximum = interval(raw_limits.get(name), f"rotation limit {name}")
        if value < minimum - EPS or value > maximum + EPS:
            raise BindingResolutionError(f"rotation {name} is outside limits")
    return values


def scalar_limit(value: float, limits: dict[str, Any], name: str) -> None:
    minimum, maximum = interval(limits.get(name), f"placement_limits.{name}")
    if value < minimum - EPS or value > maximum + EPS:
        raise BindingResolutionError(f"{name} is outside limits")


def point_in_polygon(point: tuple[float, float], polygon: Sequence[Sequence[float]]) -> bool:
    x, z = point
    inside = False
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        ax, az, bx, bz = float(first[0]), float(first[1]), float(second[0]), float(second[1])
        cross_value = (x - ax) * (bz - az) - (z - az) * (bx - ax)
        if abs(cross_value) <= EPS and min(ax, bx) - EPS <= x <= max(ax, bx) + EPS and min(az, bz) - EPS <= z <= max(az, bz) + EPS:
            return True
        if (az > z) != (bz > z) and x < ax + (z - az) * (bx - ax) / (bz - az):
            inside = not inside
    return inside


def segments_cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    def orient(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    values = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return values[0] * values[1] < -EPS and values[2] * values[3] < -EPS


def convex_hull(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) < 3:
        raise BindingResolutionError("projected footprint is degenerate")
    def turn(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower: list[tuple[float, float]] = []
    for item in unique:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], item) <= EPS:
            lower.pop()
        lower.append(item)
    upper: list[tuple[float, float]] = []
    for item in reversed(unique):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], item) <= EPS:
            upper.pop()
        upper.append(item)
    return lower[:-1] + upper[:-1]


def polygon_contains_hull(polygon: Sequence[Sequence[float]], hull: Sequence[tuple[float, float]]) -> bool:
    if not all(point_in_polygon(point, polygon) for point in hull):
        return False
    polygon_points = [(float(point[0]), float(point[1])) for point in polygon]
    for index, first in enumerate(hull):
        second = hull[(index + 1) % len(hull)]
        for edge_index, edge_first in enumerate(polygon_points):
            if segments_cross(first, second, edge_first, polygon_points[(edge_index + 1) % len(polygon_points)]):
                return False
    return True


def holes_clear(holes: Any, hull: Sequence[tuple[float, float]]) -> bool:
    if holes is None:
        holes = []
    if not isinstance(holes, list):
        raise BindingResolutionError("holes_xz must be an array")
    for raw in holes:
        if not isinstance(raw, list) or len(raw) < 3:
            raise BindingResolutionError("hole must be a polygon")
        hole = [(finite(point[0], "hole x"), finite(point[1], "hole z")) for point in raw]
        if any(point_in_polygon(point, hole) for point in hull) or any(point_in_polygon(point, hull) for point in hole):
            return False
        for index, first in enumerate(hull):
            for other, edge_first in enumerate(hole):
                if segments_cross(first, hull[(index + 1) % len(hull)], edge_first, hole[(other + 1) % len(hole)]):
                    return False
    return True


def resolve_binding(binding: dict[str, Any], frame: dict[str, Any]) -> dict[str, Any]:
    expected_type = binding["anchor_ref"].get("expected_type")
    if frame.get("type") != expected_type or frame.get("status") != "active":
        raise BindingResolutionError("anchor type/status does not match binding")
    placement = binding.get("placement")
    if not isinstance(placement, dict):
        raise BindingResolutionError("placement is missing")
    limits = frame.get("placement_limits")
    bounds = frame.get("bounds")
    if not isinstance(limits, dict) or not isinstance(bounds, dict):
        raise BindingResolutionError("anchor bounds or placement_limits are missing")
    origin = vector(frame.get("origin"), "anchor.origin")
    forward, normal, up = vector(frame.get("forward"), "anchor.forward"), vector(frame.get("normal"), "anchor.normal"), vector(frame.get("up"), "anchor.up")
    normal_offset = finite(placement.get("normal_offset_m"), "normal_offset_m")
    height = finite(placement.get("height_m"), "height_m")
    scalar_limit(normal_offset, limits, "normal_offset_m")
    scalar_limit(height, limits, "height_m")
    angles = rotation(placement, limits)
    anchor_type = str(frame["type"])
    if anchor_type in LINEAR_TYPES:
        if placement.get("mode") != "linear" or not isinstance(placement.get("linear"), dict):
            raise BindingResolutionError("linear placement is required")
        along_range = interval(bounds.get("along_range_m", [0.0, bounds.get("length_m")]), "along_range_m")
        along = axis_value(placement["linear"].get("along"), along_range, "linear.along")
        position = add(add(add(origin, scale(forward, along)), scale(normal, normal_offset)), scale(up, height))
        columns = (forward, up, normal)
    elif anchor_type in SURFACE_TYPES:
        surface = placement.get("surface")
        if placement.get("mode") != "surface" or not isinstance(surface, dict):
            raise BindingResolutionError("surface placement is required")
        along_u = axis_value(surface.get("u"), interval(bounds.get("u_range_m"), "u_range_m"), "surface.u")
        along_v = axis_value(surface.get("v"), interval(bounds.get("v_range_m"), "v_range_m"), "surface.v")
        axis_v = cross(forward, up)
        position = add(add(add(add(origin, scale(forward, along_u)), scale(axis_v, along_v)), scale(normal, normal_offset)), scale(up, height))
        columns = (forward, up, axis_v)
    else:
        raise BindingResolutionError(f"anchor type {anchor_type} is not supported by the pilot resolver")
    matrix = matrix_multiply(basis_matrix(columns), euler_yxz(angles))
    footprint = vector(binding.get("footprint_m"), "footprint_m")
    if min(footprint) <= 0:
        raise BindingResolutionError("footprint_m dimensions must be positive")
    center = vector(binding.get("footprint_center_m", [0, 0, 0]), "footprint_center_m")
    corners: list[tuple[float, float, float]] = []
    for x in (-0.5, 0.5):
        for y in (-0.5, 0.5):
            for z in (-0.5, 0.5):
                local = add(center, (footprint[0] * x, footprint[1] * y, footprint[2] * z))
                corners.append(add(position, transform_vector(matrix, local)))
    if anchor_type in LINEAR_TYPES:
        along_range = interval(bounds.get("along_range_m", [0.0, bounds.get("length_m")]), "along_range_m")
        height_range = (0.0, float(bounds.get("height_m", bounds.get("clear_height_m", bounds.get("height", -1.0)))))
        if height_range[1] < 0:
            raise BindingResolutionError("linear anchor height is missing")
        if any(dot(add(corner, scale(origin, -1)), forward) < along_range[0] - EPS or dot(add(corner, scale(origin, -1)), forward) > along_range[1] + EPS for corner in corners):
            raise BindingResolutionError("footprint exceeds linear anchor length")
        if any(dot(add(corner, scale(origin, -1)), up) < height_range[0] - EPS or dot(add(corner, scale(origin, -1)), up) > height_range[1] + EPS for corner in corners):
            raise BindingResolutionError("footprint exceeds linear anchor height")
    else:
        polygon = bounds.get("polygon_xz")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise BindingResolutionError("surface polygon_xz is missing")
        hull = convex_hull([(corner[0], corner[2]) for corner in corners])
        if not polygon_contains_hull(polygon, hull) or not holes_clear(bounds.get("holes_xz"), hull):
            raise BindingResolutionError("footprint exceeds permitted surface polygon")
    minimum = [min(corner[axis] for corner in corners) for axis in range(3)]
    maximum = [max(corner[axis] for corner in corners) for axis in range(3)]
    return {
        "origin": [round(item, 6) for item in position],
        "basis_x": [round(matrix[row][0], 6) for row in range(3)],
        "basis_y": [round(matrix[row][1], 6) for row in range(3)],
        "basis_z": [round(matrix[row][2], 6) for row in range(3)],
        "bounds": {"min": [round(item, 6) for item in minimum], "max": [round(item, 6) for item in maximum]},
        "mount_range": {
            "along_m": [round(min(dot(add(corner, scale(origin, -1)), forward) for corner in corners), 6), round(max(dot(add(corner, scale(origin, -1)), forward) for corner in corners), 6)],
            "height_m": [round(min(dot(add(corner, scale(origin, -1)), up) for corner in corners), 6), round(max(dot(add(corner, scale(origin, -1)), up) for corner in corners), 6)],
        },
    }


def resolve_document(composition: dict[str, Any], bindings: dict[str, Any], anchors: dict[str, Any]) -> dict[str, Any]:
    if bindings.get("schema_id") != "caretaker.object_bindings" or bindings.get("schema_version") != "1.0.0":
        raise BindingResolutionError("unsupported object_bindings schema")
    for key in ("map_id", "sector_id"):
        if bindings.get(key) != anchors.get(key):
            raise BindingResolutionError(f"bindings {key} does not match anchors")
    result = copy.deepcopy(composition)
    result.update({key: anchors[key] for key in ("map_id", "sector_id", "generation_id", "anchors")})
    objects = result.get("objects")
    if not isinstance(objects, list):
        raise BindingResolutionError("composition objects must be an array")
    objects_by_id: dict[str, dict[str, Any]] = {}
    for item in objects:
        if not isinstance(item, dict) or not isinstance(item.get("object_id"), str) or item["object_id"] in objects_by_id:
            raise BindingResolutionError("composition object IDs must be present and unique")
        objects_by_id[item["object_id"]] = item
    frames: dict[str, dict[str, Any]] = {}
    for frame in anchors.get("anchors", []):
        if not isinstance(frame, dict) or not isinstance(frame.get("anchor_id"), str) or frame["anchor_id"] in frames:
            raise BindingResolutionError("anchor IDs must be present and unique")
        frames[frame["anchor_id"]] = frame
    raw_bindings = bindings.get("bindings")
    if not isinstance(raw_bindings, list):
        raise BindingResolutionError("bindings must be an array")
    binding_ids: set[str] = set()
    for binding in raw_bindings:
        if not isinstance(binding, dict) or not isinstance(binding.get("binding_id"), str) or binding["binding_id"] in binding_ids:
            raise BindingResolutionError("binding IDs must be present and unique")
        binding_ids.add(binding["binding_id"])
    bound_objects: set[str] = set()
    for binding in raw_bindings:
        object_ref, anchor_ref = binding.get("object_ref"), binding.get("anchor_ref")
        if not isinstance(object_ref, dict) or not isinstance(anchor_ref, dict):
            raise BindingResolutionError("binding object_ref/anchor_ref is missing")
        object_id, anchor_id = object_ref.get("object_id"), anchor_ref.get("anchor_id")
        if object_id not in objects_by_id or object_id in bound_objects:
            raise BindingResolutionError(f"binding object {object_id} is missing or duplicated")
        bound_objects.add(str(object_id))
        item = objects_by_id[str(object_id)]
        item.update({"binding_id": binding["binding_id"], "anchor_id": anchor_id, "expected_anchor_type": anchor_ref.get("expected_type")})
        frame = frames.get(str(anchor_id))
        if frame is None:
            # Preserve the old bounds and exact absent ID so the validator emits
            # a repair item. Never select a geometrically nearby replacement.
            continue
        try:
            resolved = resolve_binding(binding, frame)
        except BindingResolutionError as error:
            raise BindingResolutionError(f"{binding['binding_id']}: {error}") from error
        item["bounds"] = resolved["bounds"]
        item["resolved_transform"] = {key: value for key, value in resolved.items() if key.startswith("basis_") or key == "origin"}
        if item.get("placement_mode") == "wall":
            item["mount_range"] = resolved["mount_range"]
    infrastructure = result.get("infrastructure")
    if not isinstance(infrastructure, list):
        raise BindingResolutionError("composition infrastructure must be an array")
    resolved_infrastructure: list[dict[str, Any]] = []
    for item in infrastructure:
        if not isinstance(item, dict):
            raise BindingResolutionError("infrastructure entry must be an object")
        source_anchor_id = item.get("source_anchor_id")
        if source_anchor_id is None:
            resolved_infrastructure.append(item)
            continue
        frame = frames.get(str(source_anchor_id))
        if frame is None:
            # A missing bound anchor must reach composition validation so it can
            # produce the T01 repair item. Promotion will fail before stale
            # infrastructure data can become live.
            resolved_infrastructure.append(item)
            continue
        if item.get("derivation") == "wall_prism_segments":
            opening_ids = item.get("opening_anchor_ids", [])
            if not isinstance(opening_ids, list) or not all(isinstance(value, str) for value in opening_ids):
                raise BindingResolutionError("opening_anchor_ids must be a string array")
            openings = [frames[value] for value in opening_ids if value in frames]
            if len(openings) != len(opening_ids):
                resolved_infrastructure.append(item)
                continue
            for index, segment_bounds in enumerate(derive_wall_segment_bounds(frame, openings), 1):
                part = copy.deepcopy(item)
                part["infrastructure_id"] = f"{item['infrastructure_id']}::part-{index:03d}"
                part["bounds"] = segment_bounds
                resolved_infrastructure.append(part)
        else:
            item["bounds"] = derive_infrastructure_bounds(item, frame)
            resolved_infrastructure.append(item)
    result["infrastructure"] = resolved_infrastructure
    return result


def _wall_prism(frame: dict[str, Any], along: tuple[float, float]) -> dict[str, list[float]]:
    origin = vector(frame.get("origin"), "wall origin")
    forward, normal, up = vector(frame.get("forward"), "wall forward"), vector(frame.get("normal"), "wall normal"), vector(frame.get("up"), "wall up")
    bounds = frame.get("bounds")
    if not isinstance(bounds, dict):
        raise BindingResolutionError("wall bounds are missing")
    thickness, height = finite(bounds.get("thickness_m"), "wall thickness"), finite(bounds.get("height_m"), "wall height")
    center = add(origin, scale(normal, -thickness * 0.5))
    points = [add(add(add(center, scale(forward, distance)), scale(normal, thickness * side)), scale(up, elevation)) for distance in along for side in (-0.5, 0.5) for elevation in (0.0, height)]
    return {"min": [round(min(point[axis] for point in points), 6) for axis in range(3)], "max": [round(max(point[axis] for point in points), 6) for axis in range(3)]}


def derive_wall_segment_bounds(frame: dict[str, Any], openings: Sequence[dict[str, Any]]) -> list[dict[str, list[float]]]:
    bounds = frame.get("bounds")
    if not isinstance(bounds, dict):
        raise BindingResolutionError("wall bounds are missing")
    wall_range = interval(bounds.get("along_range_m"), "wall along_range_m")
    wall_origin, wall_forward = vector(frame.get("origin"), "wall origin"), vector(frame.get("forward"), "wall forward")
    cuts: list[tuple[float, float]] = []
    for opening in openings:
        opening_origin = vector(opening.get("origin"), "opening origin")
        opening_forward = vector(opening.get("forward"), "opening forward")
        opening_bounds = opening.get("bounds")
        if not isinstance(opening_bounds, dict):
            raise BindingResolutionError("opening bounds are missing")
        opening_range = interval(opening_bounds.get("along_range_m"), "opening along_range_m")
        endpoints = [add(opening_origin, scale(opening_forward, distance)) for distance in opening_range]
        projected = sorted(dot(add(point, scale(wall_origin, -1)), wall_forward) for point in endpoints)
        cut = max(wall_range[0], projected[0]), min(wall_range[1], projected[1])
        if cut[1] - cut[0] <= EPS:
            raise BindingResolutionError("declared door does not cut its wall")
        cuts.append(cut)
    cuts.sort()
    merged: list[list[float]] = []
    for start, end in cuts:
        if merged and start <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    segments: list[tuple[float, float]] = []
    cursor = wall_range[0]
    for start, end in merged:
        if start - cursor > EPS:
            segments.append((cursor, start))
        cursor = max(cursor, end)
    if wall_range[1] - cursor > EPS:
        segments.append((cursor, wall_range[1]))
    return [_wall_prism(frame, segment) for segment in segments]


def derive_infrastructure_bounds(item: dict[str, Any], frame: dict[str, Any]) -> dict[str, list[float]]:
    derivation = item.get("derivation")
    origin = vector(frame.get("origin"), "infrastructure anchor origin")
    forward, normal, up = vector(frame.get("forward"), "infrastructure forward"), vector(frame.get("normal"), "infrastructure normal"), vector(frame.get("up"), "infrastructure up")
    bounds = frame.get("bounds")
    if not isinstance(bounds, dict):
        raise BindingResolutionError("infrastructure anchor bounds are missing")
    points: list[tuple[float, float, float]] = []
    if derivation == "wall_prism":
        thickness = finite(bounds.get("thickness_m"), "wall thickness")
        along = interval(bounds.get("along_range_m"), "wall along_range_m")
        height = finite(bounds.get("height_m"), "wall height")
        center = add(origin, scale(normal, -thickness * 0.5))
        for distance in along:
            for side in (-0.5, 0.5):
                for elevation in (0.0, height):
                    points.append(add(add(add(center, scale(forward, distance)), scale(normal, thickness * side)), scale(up, elevation)))
    elif derivation in {"floor_prism", "ceiling_prism"}:
        polygon = bounds.get("polygon_xz")
        thickness = finite(bounds.get("thickness_m"), "surface thickness")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise BindingResolutionError("surface polygon is missing")
        vertical = (-thickness, 0.0) if derivation == "floor_prism" else (0.0, thickness)
        points = [(finite(point[0], "surface x"), origin[1] + offset, finite(point[1], "surface z")) for point in polygon for offset in vertical]
    elif derivation == "door_clearance":
        along = interval(bounds.get("along_range_m"), "door along_range_m")
        height = finite(bounds.get("height_m"), "door height")
        depth = finite(item.get("clearance_depth_m"), "clearance_depth_m")
        if depth <= 0:
            raise BindingResolutionError("clearance_depth_m must be positive")
        for distance in along:
            for side in (-0.5, 0.5):
                for elevation in (0.0, height):
                    points.append(add(add(add(origin, scale(forward, distance)), scale(normal, depth * side)), scale(up, elevation)))
    else:
        raise BindingResolutionError(f"unknown infrastructure derivation {derivation}")
    return {
        "min": [round(min(point[axis] for point in points), 6) for axis in range(3)],
        "max": [round(max(point[axis] for point in points), 6) for axis in range(3)],
    }
