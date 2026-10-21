#!/usr/bin/env python3
"""Generate editable Godot 4 stair scenes from architectural dimensions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from stair_core import Blueprint, NodeRequest, Vec3, compile_joint_hubs, compile_model, compile_support_caps, finish_beam_joints, intersect_axes_in_plan, lowest_beam_underside_over_footprint, resolve_nearby_nodes, validate_compiled, validate_model


VERSION = "2.9.0"
REPORT_SCHEMA_VERSION = "1.1.0"
ANCHOR_CONTRACT_VERSION = "1.0.0"
EPS = 1.0e-6
SIDES = ("north", "east", "south", "west")


class GenerationError(Exception):
    def __init__(self, message: str, solutions: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.solutions = list(solutions)


@dataclass
class Box:
    category: str
    name: str
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    metadata: dict[str, object] = field(default_factory=dict)
    primitive: str = "box"
    mesh_faces: tuple[tuple[tuple[float, float, float], ...], ...] | None = None
    mesh_file: str | None = None


@dataclass
class Flight:
    name: str
    start: tuple[float, float]
    direction: tuple[float, float]
    width: float
    tread: float
    risers: int
    base_y: float
    riser_height: float
    local_bounds: tuple[float, float]

    @property
    def run(self) -> float:
        return self.tread * self.risers

    @property
    def end(self) -> tuple[float, float]:
        return (
            self.start[0] + self.direction[0] * self.run,
            self.start[1] + self.direction[1] * self.run,
        )

    @property
    def top_y(self) -> float:
        return self.base_y + self.risers * self.riser_height


@dataclass
class Landing:
    name: str
    center: tuple[float, float]
    size: tuple[float, float]
    top_y: float
    special_guard: tuple[tuple[float, float], tuple[float, float]] | None = None


@dataclass
class Layout:
    kind: str
    stair_width: float
    tread: float
    riser_height: float
    flights: list[Flight]
    landings: list[Landing]
    local_upper_side: str
    turn_direction: str | None
    assumptions: list[str]


def positive(value: str) -> float:
    result = float(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return result


def nonnegative(value: str) -> float:
    result = float(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate an editable Godot 4 stair scene.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("output_dir", help="Output directory inside or mapped to a Godot project")
    p.add_argument("--shaft-width", required=True, type=positive, help="Clear shaft width on Godot X, metres")
    p.add_argument("--shaft-length", required=True, type=positive, help="Clear shaft length on Godot Z, metres")
    p.add_argument("--floor-height", required=True, type=positive, help="Floor-to-floor height, metres")
    p.add_argument("--layout", choices=["auto", "straight", "l-turn", "u-turn", "perimeter"], default="auto")
    p.add_argument("--perimeter-flights", choices=["auto", "3", "4"], default="auto")
    p.add_argument("--turn-direction", choices=["auto", "left", "right"], default="auto")
    p.add_argument("--stair-width", type=positive, help="Flight width; auto-sized when omitted")
    p.add_argument("--target-riser", type=positive, default=0.17)
    p.add_argument("--max-riser", type=positive, default=0.19)
    p.add_argument("--target-tread", type=positive, default=0.28)
    p.add_argument("--min-tread", type=positive, default=0.25)
    p.add_argument("--min-stair-width", type=positive, default=0.90)
    p.add_argument("--landing-depth", type=positive, help="Landing depth; defaults to the selected stair width")
    p.add_argument("--flight-gap", type=nonnegative, default=0.10)
    p.add_argument("--step-thickness", type=positive, default=0.15)
    p.add_argument("--minimum-headroom", type=positive, default=2.0)
    p.add_argument("--wall-thickness", type=positive, default=0.18)
    p.add_argument("--shaft-wall-bottom", type=nonnegative, help="Bottom Y of optional gap-filling shaft walls")
    p.add_argument("--shaft-wall-top", type=positive, help="Top Y of optional gap-filling shaft walls")
    p.add_argument("--no-shaft", action="store_true", help="Disable shaft walls even when wall bounds are supplied")
    p.add_argument("--railings", choices=["auto", "both", "none"], default="auto")
    p.add_argument("--rail-height", type=positive, default=1.0)
    p.add_argument("--rail-post-spacing", type=positive, default=1.0)
    p.add_argument("--rail-post-size", type=positive, default=0.05)
    p.add_argument("--handrail-size", type=positive, default=0.05)
    p.add_argument("--midrail-size", type=positive, default=0.035)
    p.add_argument("--debug-geometry", action="store_true", help="Visualize ports, directions, connections, and errors")
    p.add_argument("--lower-entry-side", choices=SIDES, default="south")
    p.add_argument("--upper-exit-side", choices=["auto", *SIDES], default="auto")
    p.add_argument("--project-root", help="Godot project directory containing project.godot")
    p.add_argument("--resource-dir", help="Explicit res:// directory matching output_dir")
    p.add_argument("--scene-name", default="generated_stair")
    p.add_argument("--collision-layer", type=int, default=1)
    p.add_argument("--include-preview", action="store_true")
    p.add_argument("--origin", choices=["center", "south-west"], default="center")
    p.add_argument("--invert-z", action="store_true")
    p.add_argument("--version", action="version", version=VERSION)
    return p


def split_even(total: int, count: int) -> list[int]:
    if total < count * 2:
        raise GenerationError(
            f"{total} risers are insufficient for {count} usable flights.",
            ["Increase --floor-height", "Choose a layout with fewer flights"],
        )
    base, remainder = divmod(total, count)
    return [base + (1 if i < remainder else 0) for i in range(count)]


def riser_count(height: float, target: float, maximum: float) -> tuple[int, float]:
    if target > maximum:
        raise GenerationError("--target-riser cannot exceed --max-riser.", ["Increase --max-riser or reduce --target-riser"])
    count = max(2, round(height / target), math.ceil(height / maximum - EPS))
    return count, height / count


def choose_width(explicit: float | None, minimum: float, maximum: float) -> float:
    if maximum + EPS < minimum:
        raise GenerationError(
            f"Only {maximum:.3f} m is available for a flight, below the minimum {minimum:.3f} m.",
            ["Increase shaft dimensions", "Reduce --min-stair-width"],
        )
    if explicit is not None:
        if explicit + EPS < minimum:
            raise GenerationError(
                f"Requested stair width {explicit:.3f} m is below the minimum {minimum:.3f} m.",
                ["Increase --stair-width", "Reduce --min-stair-width"],
            )
        if explicit > maximum + EPS:
            raise GenerationError(
                f"Requested stair width {explicit:.3f} m exceeds the available {maximum:.3f} m.",
                ["Reduce --stair-width", "Increase shaft dimensions", "Choose another layout"],
            )
        return explicit
    return min(1.20, maximum)


def fit_tread(target: float, minimum: float, capacities: list[float], risers: list[int]) -> float:
    if target < minimum:
        raise GenerationError("--target-tread cannot be below --min-tread.", ["Increase --target-tread or reduce --min-tread"])
    possible = min(cap / count for cap, count in zip(capacities, risers))
    tread = min(target, possible)
    if tread + EPS < minimum:
        details = ", ".join(f"{cap:.2f} m/{count} steps" for cap, count in zip(capacities, risers))
        raise GenerationError(
            f"The available runs ({details}) allow only {possible:.3f} m treads, below the minimum {minimum:.3f} m.",
            ["Increase shaft dimensions", "Reduce --min-tread", "Reduce floor height", "Choose a layout with more flights"],
        )
    return tread


def side_vector(side: str) -> tuple[float, float]:
    return {"north": (0.0, 1.0), "east": (1.0, 0.0), "south": (0.0, -1.0), "west": (-1.0, 0.0)}[side]


def world_basis(lower_side: str) -> tuple[tuple[float, float], tuple[float, float]]:
    outward = side_vector(lower_side)
    forward = (-outward[0], -outward[1])
    right = (forward[1], -forward[0])
    return right, forward


def map_local_side(side: str, lower_side: str) -> str:
    right, forward = world_basis(lower_side)
    vx, vz = side_vector(side)
    wx = right[0] * vx + forward[0] * vz
    wz = right[1] * vx + forward[1] * vz
    candidates = {name: side_vector(name) for name in SIDES}
    return min(candidates, key=lambda name: (candidates[name][0] - wx) ** 2 + (candidates[name][1] - wz) ** 2)


def reflected_side(side: str) -> str:
    return {"north": "south", "south": "north", "east": "east", "west": "west"}[side]


def basis_entry_side(args: argparse.Namespace) -> str:
    # Pre-compensate the basis so --lower-entry-side remains the final,
    # user-visible side after an optional global Z reflection.
    return reflected_side(args.lower_entry_side) if args.invert_z else args.lower_entry_side


def mapped_side(side: str, args: argparse.Namespace) -> str:
    mapped = map_local_side(side, basis_entry_side(args))
    return reflected_side(mapped) if args.invert_z else mapped


def local_dimensions(args: argparse.Namespace) -> tuple[float, float]:
    if args.lower_entry_side in ("south", "north"):
        return args.shaft_width, args.shaft_length
    return args.shaft_length, args.shaft_width


def resolve_turn(args: argparse.Namespace, layout_kind: str, local_upper: str | None = None) -> str:
    if args.turn_direction != "auto":
        return args.turn_direction
    if args.upper_exit_side != "auto" and layout_kind in ("l-turn", "perimeter"):
        right_upper = "east" if layout_kind == "l-turn" else local_upper or "west"
        left_upper = "west" if layout_kind == "l-turn" else ("east" if right_upper == "west" else "west")
        if mapped_side(right_upper, args) == args.upper_exit_side:
            return "right"
        if mapped_side(left_upper, args) == args.upper_exit_side:
            return "left"
    return "right"


def check_upper_side(args: argparse.Namespace, local_side: str) -> None:
    actual = mapped_side(local_side, args)
    if args.upper_exit_side != "auto" and actual != args.upper_exit_side:
        raise GenerationError(
            f"This configuration exits on {actual}, not the requested {args.upper_exit_side} side.",
            [f"Use --upper-exit-side {actual}", "Use --upper-exit-side auto", "Choose another layout or turn direction"],
        )


def build_layout(kind: str, args: argparse.Namespace, perimeter_count: int | None = None) -> Layout:
    local_w, local_l = local_dimensions(args)
    total_risers, riser = riser_count(args.floor_height, args.target_riser, args.max_riser)
    assumptions: list[str] = []

    if kind == "straight":
        width = choose_width(args.stair_width, args.min_stair_width, local_w)
        counts = [total_risers]
        tread = fit_tread(args.target_tread, args.min_tread, [local_l], counts)
        run = tread * counts[0]
        start = (0.0, -run / 2.0)
        flights = [Flight("Flight_01", start, (0.0, 1.0), width, tread, counts[0], 0.0, riser, (local_w, local_l))]
        check_upper_side(args, "north")
        return Layout(kind, width, tread, riser, flights, [], "north", None, assumptions)

    if kind == "l-turn":
        width = choose_width(args.stair_width, args.min_stair_width, min(local_w, local_l) - 0.01)
        landing = args.landing_depth or width
        if landing + EPS < width:
            raise GenerationError("Landing depth cannot be smaller than the stair width.", ["Increase --landing-depth"])
        counts = split_even(total_risers, 2)
        capacities = [local_l - landing, local_w - landing]
        tread = fit_tread(args.target_tread, args.min_tread, capacities, counts)
        turn = resolve_turn(args, kind)
        sign = 1.0 if turn == "right" else -1.0
        run1, run2 = counts[0] * tread, counts[1] * tread
        # Place the corner opposite the requested turn direction so the
        # outgoing flight crosses the shaft and terminates at that side.
        corner_x = -sign * (local_w / 2.0 - landing / 2.0)
        corner_z = local_l / 2.0 - landing / 2.0
        first_x = corner_x
        first_start = (first_x, corner_z - landing / 2.0 - run1)
        second_start = (corner_x + sign * landing / 2.0, corner_z)
        flights = [
            Flight("Flight_01", first_start, (0.0, 1.0), width, tread, counts[0], 0.0, riser, (local_w, local_l)),
            Flight("Flight_02", second_start, (sign, 0.0), width, tread, counts[1], counts[0] * riser, riser, (local_w, local_l)),
        ]
        landings = [Landing("Landing_01", (corner_x, corner_z), (landing, landing), counts[0] * riser)]
        upper = "east" if turn == "right" else "west"
        check_upper_side(args, upper)
        return Layout(kind, width, tread, riser, flights, landings, upper, turn, assumptions)

    if kind == "u-turn":
        max_width = (local_w - args.flight_gap) / 2.0
        width = choose_width(args.stair_width, args.min_stair_width, max_width)
        landing = args.landing_depth or width
        if landing + EPS < width:
            raise GenerationError("Landing depth cannot be smaller than the stair width.", ["Increase --landing-depth"])
        counts = split_even(total_risers, 2)
        capacity = local_l - landing
        tread = fit_tread(args.target_tread, args.min_tread, [capacity, capacity], counts)
        run1, run2 = counts[0] * tread, counts[1] * tread
        xoff = args.flight_gap / 2.0 + width / 2.0
        landing_z = local_l / 2.0 - landing / 2.0
        start1 = (-xoff, landing_z - landing / 2.0 - run1)
        start2 = (xoff, landing_z - landing / 2.0)
        flights = [
            Flight("Flight_01", start1, (0.0, 1.0), width, tread, counts[0], 0.0, riser, (local_w, local_l)),
            Flight("Flight_02", start2, (0.0, -1.0), width, tread, counts[1], counts[0] * riser, riser, (local_w, local_l)),
        ]
        guard_a = (-args.flight_gap / 2.0, landing_z - landing / 2.0)
        guard_b = (args.flight_gap / 2.0, landing_z - landing / 2.0)
        landings = [
            Landing(
                "Landing_01",
                (0.0, landing_z),
                (2.0 * width + args.flight_gap, landing),
                counts[0] * riser,
                (guard_a, guard_b) if args.flight_gap > EPS else None,
            )
        ]
        check_upper_side(args, "south")
        return Layout(kind, width, tread, riser, flights, landings, "south", None, assumptions)

    if kind == "perimeter":
        count = perimeter_count or 3
        max_width = min(local_w, local_l) / 2.0 - 0.01
        width = choose_width(args.stair_width, args.min_stair_width, max_width)
        landing = args.landing_depth or width
        if abs(landing - width) > EPS:
            assumptions.append("Perimeter corner landings use stair width; --landing-depth is ignored for this layout.")
        landing = width
        counts = split_even(total_risers, count)
        capacities = [local_l - 2.0 * landing, local_w - 2.0 * landing, local_l - 2.0 * landing]
        if count == 4:
            capacities.append(local_w - 2.0 * landing)
        tread = fit_tread(args.target_tread, args.min_tread, capacities, counts)
        turn = resolve_turn(args, kind, "west")
        sign = 1.0 if turn == "right" else -1.0
        west_x = -sign * (local_w / 2.0 - width / 2.0)
        east_x = sign * (local_w / 2.0 - width / 2.0)
        south_z = -local_l / 2.0 + width / 2.0
        north_z = local_l / 2.0 - width / 2.0
        corner1 = (west_x, north_z)
        corner2 = (east_x, north_z)
        corner3 = (east_x, south_z)
        runs = [c * tread for c in counts]
        starts = [
            (west_x, north_z - width / 2.0 - runs[0]),
            (west_x + sign * width / 2.0, north_z),
            (east_x, north_z - width / 2.0),
        ]
        directions = [(0.0, 1.0), (sign, 0.0), (0.0, -1.0)]
        if count == 4:
            starts.append((east_x - sign * width / 2.0, south_z))
            directions.append((-sign, 0.0))
        flights: list[Flight] = []
        base_y = 0.0
        for index, (start, direction, risers) in enumerate(zip(starts, directions, counts), 1):
            flights.append(Flight(f"Flight_{index:02d}", start, direction, width, tread, risers, base_y, riser, (local_w, local_l)))
            base_y += risers * riser
        # Keep every intermediate transition connected even when the selected
        # tread depth does not consume the complete wall-to-wall capacity.
        # The destination landing extends back to meet the last tread.
        gap2 = max(0.0, capacities[1] - runs[1])
        landing2_center = (corner2[0] - sign * gap2 / 2.0, corner2[1])
        landings = [
            Landing("Landing_01", corner1, (width, width), flights[0].top_y),
            Landing("Landing_02", landing2_center, (width + gap2, width), flights[1].top_y),
        ]
        if count == 4:
            gap3 = max(0.0, capacities[2] - runs[2])
            landing3_center = (corner3[0], corner3[1] + gap3 / 2.0)
            landings.append(Landing("Landing_03", landing3_center, (width, width + gap3), flights[2].top_y))
        upper = "south" if count == 3 else ("west" if turn == "right" else "east")
        check_upper_side(args, upper)
        return Layout(kind, width, tread, riser, flights, landings, upper, turn, assumptions)

    raise AssertionError(kind)


def layout_candidates(args: argparse.Namespace) -> list[tuple[str, int | None]]:
    if args.layout != "auto":
        if args.layout != "perimeter":
            return [(args.layout, None)]
        if args.perimeter_flights == "auto":
            return [("perimeter", 3), ("perimeter", 4)]
        return [("perimeter", int(args.perimeter_flights))]
    result: list[tuple[str, int | None]] = [("straight", None), ("l-turn", None), ("u-turn", None)]
    if args.perimeter_flights == "auto":
        result.extend([("perimeter", 3), ("perimeter", 4)])
    else:
        result.append(("perimeter", int(args.perimeter_flights)))
    return result


def select_layout(args: argparse.Namespace) -> tuple[Layout, list[dict[str, object]]]:
    failures: list[dict[str, object]] = []
    for kind, count in layout_candidates(args):
        try:
            layout = build_layout(kind, args, count)
            if args.layout == "auto":
                label = kind if count is None else f"{kind}-{count}"
                layout.assumptions.append(f"Auto-selected {label}, the first fitting layout.")
            return layout, failures
        except GenerationError as exc:
            label = kind if count is None else f"{kind}-{count}"
            failures.append({"layout": label, "reason": str(exc), "solutions": exc.solutions})
    solutions: list[str] = []
    for item in failures:
        for solution in item["solutions"]:
            if solution not in solutions:
                solutions.append(solution)
    raise GenerationError("No requested stair layout fits the supplied dimensions.", solutions)


def sanitize_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not value:
        raise GenerationError("--scene-name must contain at least one letter or digit.")
    return value


def transform_point(x: float, z: float, args: argparse.Namespace) -> tuple[float, float]:
    right, forward = world_basis(basis_entry_side(args))
    wx = right[0] * x + forward[0] * z
    wz = right[1] * x + forward[1] * z
    if args.invert_z:
        wz = -wz
    if args.origin == "south-west":
        wx += args.shaft_width / 2.0
        wz += args.shaft_length / 2.0
    return wx, wz


def transform_direction(x: float, z: float, args: argparse.Namespace) -> tuple[float, float]:
    right, forward = world_basis(basis_entry_side(args))
    wx = right[0] * x + forward[0] * z
    wz = right[1] * x + forward[1] * z
    if args.invert_z:
        wz = -wz
    return wx, wz


def yaw_for_direction(dx: float, dz: float) -> float:
    return math.atan2(dx, dz)


def add_step_boxes(boxes: list[Box], layout: Layout, args: argparse.Namespace) -> None:
    for flight_index, flight in enumerate(layout.flights, 1):
        dx, dz = transform_direction(*flight.direction, args)
        yaw = yaw_for_direction(dx, dz)
        for i in range(flight.risers):
            local_x = flight.start[0] + flight.direction[0] * flight.tread * (i + 0.5)
            local_z = flight.start[1] + flight.direction[1] * flight.tread * (i + 0.5)
            x, z = transform_point(local_x, local_z, args)
            top = flight.base_y + (i + 1) * flight.riser_height
            # The prism below supplies the continuous sloped soffit, while the
            # tread box supplies its minimum waist thickness. Keeping that
            # minimum equal to the landing slab prevents thin wedges where a
            # flight enters a floor or landing.
            tread_thickness = args.step_thickness
            boxes.append(
                Box(
                    "Stairs",
                    f"{flight.name}_Tread_{i + 1:03d}",
                    (x, top - tread_thickness / 2.0, z),
                    (flight.width, tread_thickness, flight.tread),
                    (0.0, yaw, 0.0),
                    {"flight": flight_index, "step": i + 1, "part": "tread", "top_y": round(top, 6)},
                )
            )
            # A right-triangular fill under every tread makes all lower edges
            # form one continuous inclined soffit, like a monolithic concrete
            # stair. Unlike stacked full-height boxes, it preserves clearance
            # beneath the flight.
            prism_yaw = math.atan2(-dz, dx)
            boxes.append(
                Box(
                    "Stairs",
                    f"{flight.name}_Riser_{i + 1:03d}",
                    (x, top - tread_thickness - flight.riser_height / 2.0, z),
                    (flight.tread, flight.riser_height, flight.width),
                    (math.pi, prism_yaw, 0.0),
                    {"flight": flight_index, "step": i + 1, "part": "riser", "top_y": round(top, 6)},
                    "prism",
                )
            )
    for index, landing in enumerate(layout.landings, 1):
        x, z = transform_point(*landing.center, args)
        # Local landing dimensions rotate with the local frame.
        right, _ = world_basis(basis_entry_side(args))
        rotated = abs(right[0]) < 0.5
        sx, sz = landing.size
        if rotated:
            sx, sz = sz, sx
        boxes.append(
            Box(
                "Landings",
                landing.name,
                (x, landing.top_y - args.step_thickness / 2.0, z),
                (sx, args.step_thickness, sz),
                metadata={"landing": index, "top_y": round(landing.top_y, 6)},
            )
        )


def flight_rect(flight: Flight) -> tuple[float, float, float, float]:
    x1, z1 = flight.start
    x2, z2 = flight.end
    if abs(flight.direction[0]) > EPS:
        return min(x1, x2), max(x1, x2), z1 - flight.width / 2.0, z1 + flight.width / 2.0
    return x1 - flight.width / 2.0, x1 + flight.width / 2.0, min(z1, z2), max(z1, z2)


def landing_rect(landing: Landing) -> tuple[float, float, float, float]:
    return (
        landing.center[0] - landing.size[0] / 2.0,
        landing.center[0] + landing.size[0] / 2.0,
        landing.center[1] - landing.size[1] / 2.0,
        landing.center[1] + landing.size[1] / 2.0,
    )


def edge_boundary_side(flight: Flight, side_sign: float, tolerance: float = 0.08) -> str | None:
    dx, dz = flight.direction
    lateral = (dz * side_sign, -dx * side_sign)
    p1 = (flight.start[0] + lateral[0] * flight.width / 2.0, flight.start[1] + lateral[1] * flight.width / 2.0)
    p2 = (flight.end[0] + lateral[0] * flight.width / 2.0, flight.end[1] + lateral[1] * flight.width / 2.0)
    half_w, half_l = flight.local_bounds[0] / 2.0, flight.local_bounds[1] / 2.0
    if abs(p1[0] - p2[0]) < EPS:
        if abs(p1[0] + half_w) <= tolerance:
            return "west"
        if abs(p1[0] - half_w) <= tolerance:
            return "east"
    else:
        if abs(p1[1] + half_l) <= tolerance:
            return "south"
        if abs(p1[1] - half_l) <= tolerance:
            return "north"
    return None


def generated_wall_covers_guard(local_side: str | None, bottom_y: float, top_y: float, layout: Layout, args: argparse.Namespace) -> bool:
    if local_side is None or args.no_shaft:
        return False
    if args.shaft_wall_bottom is None or args.shaft_wall_top is None:
        return False
    return args.shaft_wall_bottom <= bottom_y + EPS and args.shaft_wall_top >= top_y - EPS


def landing_edge_boundary_side(
    rect: tuple[float, float, float, float], edge: str, layout: Layout, tolerance: float = 1.0e-6
) -> str | None:
    """Return a shaft side only when this landing edge actually touches it."""
    xmin, xmax, zmin, zmax = rect
    local_bounds = layout.flights[0].local_bounds
    if edge == "west" and abs(xmin + local_bounds[0] / 2.0) <= tolerance:
        return edge
    if edge == "east" and abs(xmax - local_bounds[0] / 2.0) <= tolerance:
        return edge
    if edge == "south" and abs(zmin + local_bounds[1] / 2.0) <= tolerance:
        return edge
    if edge == "north" and abs(zmax - local_bounds[1] / 2.0) <= tolerance:
        return edge
    return None


def subtract_intervals(base: tuple[float, float], covered: list[tuple[float, float]]) -> list[tuple[float, float]]:
    start, end = base
    clipped = sorted((max(start, a), min(end, b)) for a, b in covered if min(end, b) - max(start, a) > EPS)
    result: list[tuple[float, float]] = []
    cursor = start
    for a, b in clipped:
        if a > cursor + EPS:
            result.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end - EPS:
        result.append((cursor, end))
    return result


def connected_edge_intervals(
    rect: tuple[float, float, float, float],
    edge: str,
    neighbours: list[tuple[float, float, float, float]],
    tolerance: float = 1.0e-5,
) -> list[tuple[float, float]]:
    xmin, xmax, zmin, zmax = rect
    result: list[tuple[float, float]] = []
    for nxmin, nxmax, nzmin, nzmax in neighbours:
        if edge == "west" and abs(nxmax - xmin) <= tolerance:
            result.append((nzmin, nzmax))
        elif edge == "east" and abs(nxmin - xmax) <= tolerance:
            result.append((nzmin, nzmax))
        elif edge == "south" and abs(nzmax - zmin) <= tolerance:
            result.append((nxmin, nxmax))
        elif edge == "north" and abs(nzmin - zmax) <= tolerance:
            result.append((nxmin, nxmax))
    return result


def flat_edge_segment(
    rect: tuple[float, float, float, float], edge: str, interval: tuple[float, float], inset: float
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    xmin, xmax, zmin, zmax = rect
    a, b = interval
    full_start, full_end = (zmin, zmax) if edge in ("west", "east") else (xmin, xmax)
    # Inset only true outer corners. An endpoint produced by subtracting a
    # connected flight/landing must remain at that connection so the rails meet.
    if abs(a - full_start) <= EPS:
        a += inset
    if abs(b - full_end) <= EPS:
        b -= inset
    if b - a <= EPS:
        return None
    if edge == "west":
        return (xmin + inset, a), (xmin + inset, b)
    if edge == "east":
        return (xmax - inset, a), (xmax - inset, b)
    if edge == "south":
        return (a, zmin + inset), (b, zmin + inset)
    return (a, zmax - inset), (b, zmax - inset)


@dataclass
class RailSpec:
    """A requested rail axis; this is structure, not emitted scene geometry."""

    name: str
    start: Vec3
    end: Vec3
    size: float
    metadata: dict[str, object]
    start_terminal: Vec3 | None = None
    end_terminal: Vec3 | None = None


def _resolve_rail_topology(
    rails: list[RailSpec],
    args: argparse.Namespace,
    isolated_members: set[str] | None = None,
    explicit_node_tiers: dict[str, str] | None = None,
) -> tuple[dict[str, Vec3], tuple[object, ...], dict[str, object]]:
    isolated_members = isolated_members or set()
    explicit_node_tiers = explicit_node_tiers or {}
    requests: list[NodeRequest] = []
    for rail in rails:
        tier = "handrail" if "_Handrail" in rail.name else "midrail"
        priority = 0 if "flight" in rail.metadata else 1
        start_id, end_id = rail.name + ".start", rail.name + ".end"
        start_tier = start_id if start_id in isolated_members else explicit_node_tiers.get(start_id, tier)
        end_tier = end_id if end_id in isolated_members else explicit_node_tiers.get(end_id, tier)
        requests.append(NodeRequest(start_id, start_tier, rail.start, priority))
        requests.append(NodeRequest(end_id, end_tier, rail.end, priority))
    resolved_positions, resolved_nodes = resolve_nearby_nodes(
        requests,
        max(args.handrail_size, args.rail_post_size) / 2.0 + 1.0e-6,
    )
    node_for_member = {member: node for node in resolved_nodes for member in node.member_ids}
    return resolved_positions, resolved_nodes, node_for_member


def _add_perimeter_landing_transitions(
    rails: list[RailSpec],
    node_for_member: dict[str, object],
    explicit_node_tiers: dict[str, str],
) -> int:
    """Add explicit case-3 members between isolated slopes and landing rails."""
    candidates: list[tuple[float, RailSpec, str, Vec3, RailSpec, str, Vec3, Vec3]] = []
    for flight_rail in [rail for rail in rails if "flight" in rail.metadata]:
        flight_axis = flight_rail.end - flight_rail.start
        if abs(flight_axis.y) <= EPS:
            continue
        tier = "handrail" if "_Handrail" in flight_rail.name else "midrail"
        for flight_endpoint, flight_point in (("start", flight_rail.start), ("end", flight_rail.end)):
            flight_member = f"{flight_rail.name}.{flight_endpoint}"
            if len(node_for_member[flight_member].member_ids) > 1:
                continue
            flight_outward = (
                flight_rail.start - flight_rail.end
                if flight_endpoint == "start"
                else flight_rail.end - flight_rail.start
            )
            flight_outward = Vec3(flight_outward.x, 0.0, flight_outward.z).normalized()
            for landing_rail in [rail for rail in rails if "landing" in rail.metadata]:
                landing_tier = "handrail" if "_Handrail" in landing_rail.name else "midrail"
                if landing_tier != tier:
                    continue
                landing_axis = landing_rail.end - landing_rail.start
                if abs(landing_axis.y) > EPS:
                    continue
                for landing_endpoint, landing_point in (("start", landing_rail.start), ("end", landing_rail.end)):
                    landing_member = f"{landing_rail.name}.{landing_endpoint}"
                    if len(node_for_member[landing_member].member_ids) > 1:
                        continue
                    if abs(flight_point.y - landing_point.y) > 1.0e-6:
                        continue
                    intersection = intersect_axes_in_plan(
                        flight_rail.start,
                        flight_rail.end,
                        landing_rail.start,
                        landing_rail.end,
                    )
                    if intersection is None:
                        continue
                    flight_intersection, _landing_intersection = intersection
                    corner = Vec3(flight_intersection.x, flight_point.y, flight_intersection.z)
                    flight_offset = corner - flight_point
                    landing_offset = corner - landing_point
                    flight_distance = math.hypot(flight_offset.x, flight_offset.z)
                    landing_distance = math.hypot(landing_offset.x, landing_offset.z)
                    maximum_span = max(flight_rail.size, landing_rail.size) * 1.5
                    if flight_distance <= EPS or landing_distance <= EPS:
                        continue
                    if flight_distance > maximum_span + EPS or landing_distance > maximum_span + EPS:
                        continue
                    if Vec3(flight_offset.x, 0.0, flight_offset.z).normalized().dot(flight_outward) < 1.0 - 1.0e-6:
                        continue
                    landing_outward = (
                        landing_rail.start - landing_rail.end
                        if landing_endpoint == "start"
                        else landing_rail.end - landing_rail.start
                    )
                    landing_outward = Vec3(landing_outward.x, 0.0, landing_outward.z).normalized()
                    if Vec3(landing_offset.x, 0.0, landing_offset.z).normalized().dot(landing_outward) < 1.0 - 1.0e-6:
                        continue
                    candidates.append(
                        (
                            flight_distance + landing_distance,
                            flight_rail,
                            flight_endpoint,
                            flight_point,
                            landing_rail,
                            landing_endpoint,
                            landing_point,
                            corner,
                        )
                    )

    used_flight_ports: set[str] = set()
    used_landing_ports: set[str] = set()
    added = 0
    for _cost, flight_rail, flight_endpoint, flight_point, landing_rail, landing_endpoint, _landing_point, corner in sorted(
        candidates,
        key=lambda item: (item[0], item[1].name, item[2], item[4].name, item[5]),
    ):
        flight_member = f"{flight_rail.name}.{flight_endpoint}"
        landing_member = f"{landing_rail.name}.{landing_endpoint}"
        if flight_member in used_flight_ports or landing_member in used_landing_ports:
            continue
        if landing_endpoint == "start":
            landing_rail.start = corner
        else:
            landing_rail.end = corner
        transition_name = f"{flight_rail.name}_{flight_endpoint.title()}_Landing_Transition"
        rails.append(
            RailSpec(
                transition_name,
                flight_point,
                corner,
                flight_rail.size,
                {
                    "flight": flight_rail.metadata["flight"],
                    "landing": landing_rail.metadata["landing"],
                    "landing_transition": True,
                    "transition_kind": "slope_to_flat",
                    "pipeline": "ports",
                },
            )
        )
        slope_joint = f"explicit:{transition_name}:slope"
        landing_joint = f"explicit:{transition_name}:landing"
        explicit_node_tiers[flight_member] = slope_joint
        explicit_node_tiers[transition_name + ".start"] = slope_joint
        explicit_node_tiers[landing_member] = landing_joint
        explicit_node_tiers[transition_name + ".end"] = landing_joint
        used_flight_ports.add(flight_member)
        used_landing_ports.add(landing_member)
        added += 1
    return added


def _v3(local: tuple[float, float], y: float, args: argparse.Namespace) -> Vec3:
    x, z = transform_point(*local, args)
    return Vec3(x, y, z)


def generated_wall_touches_endpoint(point: Vec3, clearance: float, args: argparse.Namespace) -> bool:
    """Return true when a generated wall physically receives this rail end."""
    if args.no_shaft or args.shaft_wall_bottom is None or args.shaft_wall_top is None:
        return False
    if point.y < args.shaft_wall_bottom - clearance or point.y > args.shaft_wall_top + clearance:
        return False
    local_w, local_l = local_dimensions(args)
    corners = [
        transform_point(x, z, args)
        for x in (-local_w / 2.0, local_w / 2.0)
        for z in (-local_l / 2.0, local_l / 2.0)
    ]
    xmin, xmax = min(x for x, _z in corners), max(x for x, _z in corners)
    zmin, zmax = min(z for _x, z in corners), max(z for _x, z in corners)
    return min(abs(point.x - xmin), abs(point.x - xmax), abs(point.z - zmin), abs(point.z - zmax)) <= clearance + EPS


def build_railing_blueprint(layout: Layout, args: argparse.Namespace) -> tuple[object, dict[str, object]]:
    """Describe the complete railing graph using explicit ports and supports."""
    blueprint = Blueprint()
    if args.railings == "none":
        model = blueprint.finish()
        return model, validate_model(model).report()

    rails: list[RailSpec] = []
    post_factories: list[tuple[RailSpec, list[tuple[float, float, Vec3, dict[str, object]]]]] = []

    # Flights contribute abstract axes and tread-supported post candidates.
    for flight_index, flight in enumerate(layout.flights, 1):
        for side_sign, side_label in ((-1.0, "Left"), (1.0, "Right")):
            boundary_side = edge_boundary_side(flight, side_sign)
            if args.railings == "auto" and generated_wall_covers_guard(
                boundary_side, flight.base_y, flight.top_y + args.rail_height, layout, args
            ):
                continue
            dx, dz = flight.direction
            lateral = (dz * side_sign, -dx * side_sign)
            inset = max(args.rail_post_size, args.handrail_size) / 2.0
            edge_offset = flight.width / 2.0 - inset
            local_start = (flight.start[0] + lateral[0] * edge_offset, flight.start[1] + lateral[1] * edge_offset)
            local_end = (flight.end[0] + lateral[0] * edge_offset, flight.end[1] + lateral[1] * edge_offset)
            terminal_start = (local_start[0] + dx * flight.tread * 0.5, local_start[1] + dz * flight.tread * 0.5)
            terminal_end = (local_end[0] - dx * flight.tread * 0.5, local_end[1] - dz * flight.tread * 0.5)
            prefix = f"{flight.name}_{side_label}"
            meta = {"flight": flight_index, "side": side_label.lower(), "pipeline": "ports"}
            handrail = RailSpec(
                prefix + "_Handrail",
                _v3(local_start, flight.base_y + args.rail_height, args),
                _v3(local_end, flight.top_y + args.rail_height, args),
                args.handrail_size,
                meta,
                _v3(terminal_start, flight.base_y + args.rail_height + flight.riser_height * 0.5, args),
                _v3(terminal_end, flight.top_y + args.rail_height - flight.riser_height * 0.5, args),
            )
            rails.append(handrail)
            rails.append(
                RailSpec(
                    prefix + "_Midrail",
                    _v3(local_start, flight.base_y + args.rail_height * 0.5, args),
                    _v3(local_end, flight.top_y + args.rail_height * 0.5, args),
                    args.midrail_size,
                    meta,
                    _v3(terminal_start, flight.base_y + args.rail_height * 0.5 + flight.riser_height * 0.5, args),
                    _v3(terminal_end, flight.top_y + args.rail_height * 0.5 - flight.riser_height * 0.5, args),
                )
            )
            post_count = max(2, math.ceil(flight.run / args.rail_post_spacing) + 1)
            candidates: list[tuple[float, float, Vec3, dict[str, object]]] = []
            for post_index in range(post_count):
                target = flight.run * post_index / (post_count - 1)
                if post_index == 0:
                    distance = flight.tread * 0.5
                elif post_index == post_count - 1:
                    distance = flight.run - flight.tread * 0.5
                else:
                    step_index = min(flight.risers - 1, max(0, round(target / flight.tread - 0.5)))
                    distance = (step_index + 0.5) * flight.tread
                step_index = min(flight.risers - 1, max(0, int(distance / flight.tread)))
                base_y = flight.base_y + (step_index + 1) * flight.riser_height
                local = (local_start[0] + dx * distance, local_start[1] + dz * distance)
                candidates.append((distance / flight.run, base_y, _v3(local, 0.0, args), {**meta, "post": post_index + 1}))
            post_factories.append((handrail, candidates))

    # Landings contribute exposed contour segments. Their endpoints are graph
    # nodes, not coordinates inferred later from emitted boxes.
    walkable = [flight_rect(flight) for flight in layout.flights] + [landing_rect(landing) for landing in layout.landings]
    for landing_index, landing in enumerate(layout.landings, 1):
        rect = landing_rect(landing)
        neighbours = [candidate for candidate in walkable if candidate != rect]
        xmin, xmax, zmin, zmax = rect
        guard_inset = max(args.rail_post_size, args.handrail_size) / 2.0
        edges = {"west": (zmin, zmax), "east": (zmin, zmax), "south": (xmin, xmax), "north": (xmin, xmax)}
        for edge, interval in edges.items():
            for segment_index, exposed in enumerate(subtract_intervals(interval, connected_edge_intervals(rect, edge, neighbours)), 1):
                if args.railings == "auto" and generated_wall_covers_guard(
                    landing_edge_boundary_side(rect, edge, layout),
                    landing.top_y,
                    landing.top_y + args.rail_height,
                    layout,
                    args,
                ):
                    continue
                segment = flat_edge_segment(rect, edge, exposed, guard_inset)
                if segment is None:
                    continue
                start_local, end_local = segment
                prefix = f"{landing.name}_{edge.title()}_{segment_index:02d}"
                is_turn_bridge = False
                if landing.special_guard is not None:
                    special_start, special_end = landing.special_guard
                    special_mid = ((special_start[0] + special_end[0]) * 0.5, (special_start[1] + special_end[1]) * 0.5)
                    segment_mid = ((start_local[0] + end_local[0]) * 0.5, (start_local[1] + end_local[1]) * 0.5)
                    is_turn_bridge = (
                        abs(math.dist(start_local, end_local) - math.dist(special_start, special_end)) <= 1.0e-6
                        and math.dist(segment_mid, special_mid) <= guard_inset + 1.0e-6
                    )
                meta = {
                    "landing": landing_index,
                    "side": edge,
                    "segment": segment_index,
                    "pipeline": "ports",
                    **({"turn_bridge": True} if is_turn_bridge else {}),
                }
                handrail = RailSpec(
                    prefix + "_Handrail",
                    _v3(start_local, landing.top_y + args.rail_height, args),
                    _v3(end_local, landing.top_y + args.rail_height, args),
                    args.handrail_size,
                    meta,
                )
                rails.append(handrail)
                rails.append(
                    RailSpec(
                        prefix + "_Midrail",
                        _v3(start_local, landing.top_y + args.rail_height * 0.5, args),
                        _v3(end_local, landing.top_y + args.rail_height * 0.5, args),
                        args.midrail_size,
                        meta,
                    )
                )
                length = math.dist(start_local, end_local)
                count = max(2, math.ceil(length / args.rail_post_spacing) + 1)
                candidates = []
                for post_index in range(count):
                    t = post_index / (count - 1)
                    local = (
                        start_local[0] + (end_local[0] - start_local[0]) * t,
                        start_local[1] + (end_local[1] - start_local[1]) * t,
                    )
                    candidates.append((t, landing.top_y, _v3(local, 0.0, args), {**meta, "post": post_index + 1}))
                post_factories.append((handrail, candidates))

    # A narrow U-turn guard is a continuous five-member path: slope, short
    # horizontal tangent, landing bridge, opposite tangent, slope. Both
    # slope/tangent joints therefore use the same planar rule as an ordinary
    # slope-to-flat connection; the geometry core needs no spatial-corner case.
    for bridge in [rail for rail in rails if rail.metadata.get("turn_bridge")]:
        tier = "handrail" if "_Handrail" in bridge.name else "midrail"
        bridge_mid = (bridge.start + bridge.end) * 0.5
        endpoint_candidates: list[tuple[float, RailSpec, str, Vec3]] = []
        for flight_rail in rails:
            if "flight" not in flight_rail.metadata:
                continue
            flight_tier = "handrail" if "_Handrail" in flight_rail.name else "midrail"
            if flight_tier != tier:
                continue
            for endpoint_name, point in (("start", flight_rail.start), ("end", flight_rail.end)):
                if abs(point.y - bridge.start.y) > 1.0e-6:
                    continue
                endpoint_candidates.append((point.distance_to(bridge_mid), flight_rail, endpoint_name, point))
        selected = sorted(endpoint_candidates, key=lambda item: (item[0], item[1].name, item[2]))[:2]
        if len(selected) != 2:
            raise GenerationError("U-turn guard could not resolve its two flight endpoints")
        assignments: list[tuple[RailSpec, str, Vec3, Vec3]] = []
        remaining = list(selected)
        for bridge_point in (bridge.start, bridge.end):
            chosen = min(remaining, key=lambda item: item[3].distance_to(bridge_point))
            remaining.remove(chosen)
            _distance, flight_rail, endpoint_name, slope_point = chosen
            outward = (
                flight_rail.start - flight_rail.end
                if endpoint_name == "start"
                else flight_rail.end - flight_rail.start
            )
            tangent = Vec3(outward.x, 0.0, outward.z).normalized()
            transition_point = slope_point + tangent * (bridge.size * 1.5)
            assignments.append((flight_rail, endpoint_name, slope_point, transition_point))
        bridge.start = assignments[0][3]
        bridge.end = assignments[1][3]
        for transition_index, (flight_rail, endpoint_name, slope_point, transition_point) in enumerate(assignments, 1):
            rails.append(
                RailSpec(
                    f"{bridge.name}_Transition_{transition_index:02d}",
                    slope_point,
                    transition_point,
                    bridge.size,
                    {
                        **bridge.metadata,
                        "turn_transition": True,
                        "flight": flight_rail.metadata["flight"],
                        "flight_endpoint": endpoint_name,
                    },
                )
            )
        if tier == "handrail":
            for factory_index, (factory_rail, _candidates) in enumerate(post_factories):
                if factory_rail is not bridge:
                    continue
                post_factories[factory_index] = (
                    bridge,
                    [
                        (
                            float(index),
                            layout.landings[int(bridge.metadata["landing"]) - 1].top_y,
                            Vec3(point.x, 0.0, point.z),
                            {**bridge.metadata, "post": index + 1},
                        )
                        for index, point in enumerate((bridge.start, bridge.end))
                    ],
                )
                break

    explicit_node_tiers: dict[str, str] = {}
    resolved_positions, resolved_nodes, node_for_member = _resolve_rail_topology(rails, args)

    # Perimeter contours can leave a small intentional offset between a sloped
    # flight port and a perpendicular landing rail. Express that gap as the
    # same explicit slope-to-flat member used by showcase case 3. Port
    # tolerance remains unchanged; no existing beam axis is stretched.
    if layout.kind == "perimeter" and _add_perimeter_landing_transitions(
        rails, node_for_member, explicit_node_tiers
    ):
        resolved_positions, resolved_nodes, node_for_member = _resolve_rail_topology(
            rails, args, explicit_node_tiers=explicit_node_tiers
        )

    # A free inner corner between perpendicular flights is described as two
    # planar slope-to-flat transitions joined by one ordinary horizontal
    # corner. Perimeter landings keep that horizontal corner as the actual
    # handrail path: slope -> flat -> flat -> slope. L-turns additionally use
    # their established socketed corner newel. No spatial-slope primitive or
    # hand-positioned fitting exists in the geometry core.
    flight_rails = {
        (int(rail.metadata["flight"]), str(rail.metadata["side"]), "handrail" if "_Handrail" in rail.name else "midrail"): rail
        for rail in rails
        if "flight" in rail.metadata and "side" in rail.metadata
    }
    socketed_members: set[str] = set()
    corner_newels: list[tuple[str, Vec3, Vec3, tuple[str, ...], dict[str, object]]] = []
    for first_index in range(1, len(layout.flights)):
        second_index = first_index + 1
        candidates: list[tuple[float, str, str, Vec3, Vec3]] = []
        for first_side in ("left", "right"):
            first_rail = flight_rails.get((first_index, first_side, "handrail"))
            if first_rail is None:
                continue
            if len(node_for_member[first_rail.name + ".end"].member_ids) > 1:
                continue
            for second_side in ("left", "right"):
                second_rail = flight_rails.get((second_index, second_side, "handrail"))
                if second_rail is None:
                    continue
                if len(node_for_member[second_rail.name + ".start"].member_ids) > 1:
                    continue
                intersection = intersect_axes_in_plan(
                    first_rail.start,
                    first_rail.end,
                    second_rail.start,
                    second_rail.end,
                )
                if intersection is None:
                    continue
                first_point, second_point = intersection
                first_direction = first_rail.end - first_rail.start
                second_direction = second_rail.end - second_rail.start
                if (first_point - first_rail.end).dot(first_direction) < -1.0e-8:
                    continue
                if (second_point - second_rail.start).dot(second_direction) > 1.0e-8:
                    continue
                cost = first_point.distance_to(first_rail.end) + second_point.distance_to(second_rail.start)
                candidates.append((cost, first_side, second_side, first_point, second_point))
        if not candidates:
            continue
        _, first_side, second_side, _first_hand_point, _second_hand_point = min(candidates, key=lambda item: item[0])
        use_corner_newel = layout.kind != "perimeter"
        newel_port_ids: list[str] = []
        socket_positions: list[Vec3] = []
        for tier in ("handrail", "midrail"):
            first_rail = flight_rails[(first_index, first_side, tier)]
            second_rail = flight_rails[(second_index, second_side, tier)]
            points = intersect_axes_in_plan(first_rail.start, first_rail.end, second_rail.start, second_rail.end)
            if points is None:
                raise GenerationError("Perpendicular corner rails unexpectedly became parallel")
            first_point, second_point = points
            first_axis = first_rail.end - first_rail.start
            second_axis = second_rail.end - second_rail.start
            first_tangent = Vec3(first_axis.x, 0.0, first_axis.z).normalized()
            second_tangent = Vec3(second_axis.x, 0.0, second_axis.z).normalized()
            arm = first_rail.size * 1.5
            corner_y = max(first_point.y, second_point.y)
            corner = Vec3(first_point.x, corner_y, first_point.z)
            if first_point.y >= second_point.y:
                first_transition = first_point - first_tangent * arm
                if abs(second_axis.y) <= EPS:
                    raise GenerationError("Lower corner rail must be sloped")
                second_on_axis = second_rail.start + second_axis * (
                    (corner_y - second_rail.start.y) / second_axis.y
                )
                second_length = max(
                    arm,
                    math.hypot(second_on_axis.x - corner.x, second_on_axis.z - corner.z),
                )
                second_transition = corner + second_tangent * second_length
            else:
                if abs(first_axis.y) <= EPS:
                    raise GenerationError("Lower corner rail must be sloped")
                first_on_axis = first_rail.start + first_axis * (
                    (corner_y - first_rail.start.y) / first_axis.y
                )
                first_length = max(
                    arm,
                    math.hypot(first_on_axis.x - corner.x, first_on_axis.z - corner.z),
                )
                first_transition = corner - first_tangent * first_length
                second_transition = second_point + second_tangent * arm
            first_rail.end = first_transition
            second_rail.start = second_transition
            tier_label = "Handrail" if tier == "handrail" else "Midrail"
            metadata = {
                "landing": first_index,
                "corner_transition": True,
                "pipeline": "ports",
            }
            flat_a = RailSpec(
                f"Corner_{first_index:02d}_{tier_label}_Flat_A",
                first_transition,
                corner,
                first_rail.size,
                metadata,
            )
            flat_b = RailSpec(
                f"Corner_{first_index:02d}_{tier_label}_Flat_B",
                corner,
                second_transition,
                second_rail.size,
                metadata,
            )
            rails.extend((flat_a, flat_b))
            if use_corner_newel:
                newel_port_ids.extend((flat_a.name + ".end", flat_b.name + ".start"))
            socket_positions.extend((corner, corner))
        if not use_corner_newel:
            continue
        anchor = Vec3(
            socket_positions[0].x,
            layout.landings[first_index - 1].top_y,
            socket_positions[0].z,
        )
        top = Vec3(anchor.x, max(point.y for point in socket_positions) + args.rail_post_size * 0.5, anchor.z)
        newel_id = f"Corner_Newel_{first_index:02d}"
        socketed_members.update(newel_port_ids)
        corner_newels.append(
            (
                newel_id,
                anchor,
                top,
                tuple(newel_port_ids),
                {"landing": first_index, "corner_newel": True, "flat_adapters": True, "pipeline": "ports"},
            )
        )

    # Re-resolve after topology expansion so every new transition endpoint is
    # a named graph node before any 3D geometry is compiled.
    resolved_positions, resolved_nodes, node_for_member = _resolve_rail_topology(
        rails, args, socketed_members, explicit_node_tiers
    )
    for rail in rails:
        start_id, end_id = rail.name + ".start", rail.name + ".end"
        start_node, end_node = node_for_member[start_id], node_for_member[end_id]
        if rail.start_terminal is not None and len(start_node.member_ids) == 1:
            rail.start = rail.start_terminal
        else:
            rail.start = resolved_positions[start_id]
        if rail.end_terminal is not None and len(end_node.member_ids) == 1:
            rail.end = rail.end_terminal
        else:
            rail.end = resolved_positions[end_id]

    connected_nodes = [node for node in resolved_nodes if len(node.member_ids) > 1]
    joint_ids = {node.id: f"joint:{index:03d}:{node.id}" for index, node in enumerate(connected_nodes, 1)}
    for rail in rails:
        start_node = node_for_member[rail.name + ".start"]
        end_node = node_for_member[rail.name + ".end"]
        blueprint.add_beam(
            rail.name,
            rail.start,
            rail.end,
            rail.size,
            rail.size,
            None if rail.name + ".start" in socketed_members else joint_ids.get(start_node.id),
            None if rail.name + ".end" in socketed_members else joint_ids.get(end_node.id),
            rail.metadata,
        )

    for newel_id, base, top, port_ids, metadata in corner_newels:
        blueprint.add_socketed_post(
            newel_id,
            base,
            top,
            args.rail_post_size,
            args.rail_post_size,
            port_ids,
            metadata,
        )

    # Posts are deduplicated in the abstract model. No emitted Box is searched,
    # merged, shortened, or moved after compilation.
    used_posts: dict[tuple[float, float, float], str] = {
        (round(base.x, 5), round(base.y, 5), round(base.z, 5)): newel_id
        for newel_id, base, _top, _port_ids, _metadata in corner_newels
    }
    for rail, candidates in post_factories:
        axis = rail.end - rail.start
        horizontal_sq = axis.x * axis.x + axis.z * axis.z
        start_node = node_for_member[rail.name + ".start"]
        end_node = node_for_member[rail.name + ".end"]
        for candidate_index, (_planned_t, base_y, plan, metadata) in enumerate(candidates):
            if candidate_index == 0 and len(start_node.member_ids) > 1:
                continue
            if candidate_index == len(candidates) - 1 and len(end_node.member_ids) > 1:
                continue
            t = max(0.0, min(1.0, ((plan.x - rail.start.x) * axis.x + (plan.z - rail.start.z) * axis.z) / horizontal_sq))
            center = rail.start + axis * t
            base = Vec3(plan.x, base_y, plan.z)
            key = (round(base.x, 5), round(base.y, 5), round(base.z, 5))
            if key in used_posts:
                continue
            post_name = f"{rail.name.replace('_Handrail', '')}_Post_{len(used_posts) + 1:03d}"
            endpoint: str | None = None
            if center.distance_to(rail.start) <= 1.0e-5 and start_node.id not in joint_ids:
                endpoint = rail.name + ".start"
            elif center.distance_to(rail.end) <= 1.0e-5 and end_node.id not in joint_ids:
                endpoint = rail.name + ".end"
            wall_terminated = abs(axis.y) <= EPS and generated_wall_touches_endpoint(
                center, rail.size / 2.0, args
            )
            if endpoint and not wall_terminated:
                blueprint.add_terminal_post(
                    post_name,
                    endpoint,
                    base,
                    args.rail_post_size,
                    args.rail_post_size,
                    metadata,
                )
            else:
                blueprint.add_post_support(
                    post_name,
                    rail.name,
                    base,
                    lowest_beam_underside_over_footprint(
                        center,
                        rail.end - rail.start,
                        rail.size,
                        args.rail_post_size / 2.0,
                        args.rail_post_size / 2.0,
                    ),
                    args.rail_post_size,
                    args.rail_post_size,
                    metadata,
                )
            used_posts[key] = post_name

    model = blueprint.finish()
    validation = validate_model(model)
    if not validation.ok:
        details = "; ".join(issue.message for issue in validation.issues[:8])
        raise GenerationError(
            f"Railing graph validation failed: {details}",
            ["Inspect --debug-geometry output", "Check the reported connection and element identifiers"],
        )
    return model, validation.report()


def compile_railing_boxes(model: object, args: argparse.Namespace) -> tuple[list[Box], dict[str, object]]:
    beams, posts = compile_model(model)
    compiled_validation = validate_compiled(model, beams, posts)
    if not compiled_validation.ok:
        details = "; ".join(issue.message for issue in compiled_validation.issues[:8])
        raise GenerationError(
            f"Compiled railing validation failed: {details}",
            ["Inspect --debug-geometry output", "Change the abstract railing contour instead of applying coordinate offsets"],
        )
    finished_beams, finish_report = finish_beam_joints(model, beams)
    compiled_report = compiled_validation.report()
    compiled_report["joint_finish"] = {
        "surface_union_elements": list(finish_report.clipped_elements),
        "operation_count": len(finish_report.operations),
        "operations": list(finish_report.operations),
        "ports_changed": 0,
        "connections_changed": 0,
    }
    boxes: list[Box] = []
    for beam in beams:
        forward = beam.orientation.forward
        pitch = -math.atan2(forward.y, math.hypot(forward.x, forward.z))
        yaw = math.atan2(forward.x, forward.z)
        finished = finished_beams[beam.id]
        if finished.clipped_by:
            local_faces = tuple(
                tuple(beam.orientation.transpose_apply(point - beam.center).tuple() for point in face)
                for face in finished.faces
            )
            boxes.append(
                Box(
                    beam.category,
                    beam.id,
                    beam.center.tuple(),
                    beam.size.tuple(),
                    (pitch, yaw, 0.0),
                    metadata={**beam.metadata, "joint_finish": "surface_union", "finished_by": ",".join(finished.clipped_by)},
                    primitive="union_surface",
                    mesh_faces=local_faces,
                )
            )
        else:
            boxes.append(Box(beam.category, beam.id, beam.center.tuple(), beam.size.tuple(), (pitch, yaw, 0.0), beam.metadata))
    for post in posts:
        boxes.append(Box(post.category, post.id, post.center.tuple(), post.size.tuple(), metadata=post.metadata))
    for joint in compile_joint_hubs(model):
        forward = joint.orientation.forward
        pitch = -math.atan2(forward.y, math.hypot(forward.x, forward.z))
        yaw = math.atan2(forward.x, forward.z)
        boxes.append(
            Box(
                joint.category,
                joint.id,
                joint.center.tuple(),
                joint.size.tuple(),
                (pitch, yaw, 0.0),
                joint.metadata,
            )
        )
    for cap in compile_support_caps(model, beams, posts):
        boxes.append(
            Box(
                cap.category,
                cap.id,
                cap.center.tuple(),
                cap.size.tuple(),
                (0.0, 0.0, 0.0),
                cap.metadata,
            )
        )
    if args.debug_geometry:
        debug_index = 0
        for beam in beams:
            direction = beam.orientation.forward
            for label, point in (
                ("Start", beam.center - direction * (beam.size.z / 2.0)),
                ("End", beam.center + direction * (beam.size.z / 2.0)),
            ):
                debug_index += 1
                boxes.append(
                    Box(
                        "Debug",
                        f"Debug_Boundary_{debug_index:03d}_{sanitize_name(beam.id)}_{label}",
                        point.tuple(),
                        (0.045, 0.045, 0.045),
                        metadata={"debug": True, "element_id": beam.id, "boundary": label.lower()},
                        primitive="sphere",
                    )
                )
        for index, port in enumerate(model.ports.values(), 1):
            point = port.frame.position
            boxes.append(
                Box(
                    "Debug",
                    f"Debug_Port_{index:03d}_{sanitize_name(port.id)}",
                    point.tuple(),
                    (0.07, 0.07, 0.07),
                    metadata={"debug": True, "port_id": port.id, "owner_id": port.owner_id},
                    primitive="sphere",
                )
            )
            direction_end = point + port.frame.orientation.forward * 0.18
            delta = direction_end - point
            boxes.append(
                Box(
                    "Debug",
                    f"Debug_Direction_{index:03d}_{sanitize_name(port.id)}",
                    ((point.x + direction_end.x) / 2.0, (point.y + direction_end.y) / 2.0, (point.z + direction_end.z) / 2.0),
                    (0.015, 0.015, delta.length()),
                    (-math.atan2(delta.y, math.hypot(delta.x, delta.z)), math.atan2(delta.x, delta.z), 0.0),
                    {"debug": True, "port_id": port.id},
                )
            )
        for index, connection in enumerate(model.connections.values(), 1):
            available = [model.ports[port_id] for port_id in connection.port_ids if port_id in model.ports]
            if not available:
                continue
            anchor = available[0].frame.position
            boxes.append(
                Box(
                    "Debug",
                    f"Debug_Connection_{index:03d}_{sanitize_name(connection.id)}",
                    anchor.tuple(),
                    (0.09, 0.09, 0.09),
                    metadata={"debug": True, "connection_id": connection.id, "member_count": len(available)},
                    primitive="sphere",
                )
            )
    return boxes, compiled_report


def actual_side(side: str, args: argparse.Namespace) -> str:
    return mapped_side(side, args)


def add_shaft_walls(boxes: list[Box], layout: Layout, args: argparse.Namespace) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    supplied = args.shaft_wall_bottom is not None or args.shaft_wall_top is not None
    if args.no_shaft:
        if supplied:
            warnings.append("Shaft wall bounds were supplied but ignored because --no-shaft is active.")
        return False, warnings
    if not supplied:
        return False, warnings
    if args.shaft_wall_bottom is None or args.shaft_wall_top is None:
        raise GenerationError(
            "Both --shaft-wall-bottom and --shaft-wall-top are required to generate shaft walls.",
            ["Supply both bounds", "Omit both bounds to generate no shaft walls"],
        )
    if args.shaft_wall_top <= args.shaft_wall_bottom + EPS:
        raise GenerationError(
            "--shaft-wall-top must be greater than --shaft-wall-bottom.",
            ["Increase --shaft-wall-top", "Reduce --shaft-wall-bottom", "Omit both bounds to generate no walls"],
        )
    height = args.shaft_wall_top - args.shaft_wall_bottom
    y = args.shaft_wall_bottom + height / 2.0
    t = args.wall_thickness
    # Shaft dimensions describe the clear playable opening. Wall thickness is
    # placed outside that opening, so the wall can touch its boundary but can
    # never consume stair or railing volume.
    specs = {
        "north": ((0.0, args.shaft_length / 2.0 + t / 2.0), (args.shaft_width + 2.0 * t, height, t)),
        "south": ((0.0, -args.shaft_length / 2.0 - t / 2.0), (args.shaft_width + 2.0 * t, height, t)),
        "east": ((args.shaft_width / 2.0 + t / 2.0, 0.0), (t, height, args.shaft_length + 2.0 * t)),
        "west": ((-args.shaft_width / 2.0 - t / 2.0, 0.0), (t, height, args.shaft_length + 2.0 * t)),
    }
    for side in SIDES:
        (x, z), size = specs[side]
        if args.origin == "south-west":
            x += args.shaft_width / 2.0
            z += args.shaft_length / 2.0
        boxes.append(
            Box(
                "Shaft",
                f"ShaftWall_{side.title()}",
                (x, y, z),
                size,
                metadata={
                    "side": side,
                    "vertical_gap_only": True,
                    "bottom_y": args.shaft_wall_bottom,
                    "top_y": args.shaft_wall_top,
                },
            )
        )
    return True, warnings


def box_world_aabb(box: Box) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return a deterministic world AABB for the generator's Y-X-Z Euler boxes."""
    pitch, yaw, roll = box.rotation
    if abs(roll) > EPS:
        raise GenerationError(f"Unsupported non-zero roll on {box.name}: {roll:.9f} rad")
    hx, hy, hz = (value / 2.0 for value in box.size)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    extents = (
        abs(cy) * hx + abs(sy * sp) * hy + abs(sy * cp) * hz,
        abs(cp) * hy + abs(sp) * hz,
        abs(sy) * hx + abs(cy * sp) * hy + abs(cy * cp) * hz,
    )
    lower = tuple(box.center[index] - extents[index] for index in range(3))
    upper = tuple(box.center[index] + extents[index] for index in range(3))
    return lower, upper


def validate_shaft_clearance(boxes: list[Box]) -> dict[str, object]:
    """Reject positive-volume overlap between shaft walls and generated parts."""
    walls = [box for box in boxes if box.category == "Shaft"]
    occupants = [box for box in boxes if box.category not in ("Shaft", "Debug")]
    issues: list[dict[str, object]] = []
    maximum_overlap = 0.0
    for wall in walls:
        wall_min, wall_max = box_world_aabb(wall)
        for occupant in occupants:
            item_min, item_max = box_world_aabb(occupant)
            overlap = tuple(min(wall_max[axis], item_max[axis]) - max(wall_min[axis], item_min[axis]) for axis in range(3))
            if all(value > EPS for value in overlap):
                penetration = min(overlap)
                maximum_overlap = max(maximum_overlap, penetration)
                issues.append(
                    {
                        "code": "shaft_intersection",
                        "wall_id": wall.name,
                        "element_id": occupant.name,
                        "overlap_xyz": [round(value, 9) for value in overlap],
                        "minimum_penetration": round(penetration, 9),
                    }
                )
    return {
        "ok": not issues,
        "wall_count": len(walls),
        "checked_element_count": len(occupants),
        "maximum_penetration": maximum_overlap,
        "issues": issues,
    }


def gd_float(value: float) -> str:
    if abs(value) < 5e-10:
        value = 0.0
    return f"{value:.6f}".rstrip("0").rstrip(".") + (".0" if abs(value - round(value)) < EPS else "")


def gd_vec(values: tuple[float, float, float]) -> str:
    return "Vector3(" + ", ".join(gd_float(v) for v in values) + ")"


def gd_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return gd_float(value)
    return json.dumps(str(value), ensure_ascii=False)


class Resources:
    def __init__(self) -> None:
        self.meshes: dict[tuple[object, ...], str] = {}
        self.shapes: dict[tuple[object, ...], str] = {}
        self.blocks: list[str] = []

    @staticmethod
    def key(box: Box) -> tuple[object, ...]:
        if box.mesh_faces is not None:
            return (
                box.primitive,
                *(
                    round(value, 6)
                    for face in box.mesh_faces
                    for point in face
                    for value in point
                ),
            )
        return (box.primitive, *(round(v, 6) for v in box.size))

    def mesh(self, box: Box) -> str:
        if box.mesh_faces is not None:
            raise ValueError(f"Custom mesh {box.name} must be emitted as an external OBJ resource")
        key = self.key(box)
        if key not in self.meshes:
            resource_id = f"PrimitiveMesh_{len(self.meshes) + 1:04d}"
            self.meshes[key] = resource_id
            if box.primitive == "cylinder":
                radius = box.size[0] / 2.0
                block = (
                    f'[sub_resource type="CylinderMesh" id="{resource_id}"]\n'
                    f'top_radius = {gd_float(radius)}\n'
                    f'bottom_radius = {gd_float(radius)}\n'
                    f'height = {gd_float(box.size[1])}\n'
                    'radial_segments = 16\n'
                )
            elif box.primitive == "sphere":
                radius = box.size[0] / 2.0
                block = (
                    f'[sub_resource type="SphereMesh" id="{resource_id}"]\n'
                    f'radius = {gd_float(radius)}\n'
                    f'height = {gd_float(box.size[1])}\n'
                    'radial_segments = 16\n'
                    'rings = 8\n'
                )
            elif box.primitive == "prism":
                block = (
                    f'[sub_resource type="PrismMesh" id="{resource_id}"]\n'
                    f'size = {gd_vec(box.size)}\n'
                    'left_to_right = false\n'
                )
            else:
                block = f'[sub_resource type="BoxMesh" id="{resource_id}"]\nsize = {gd_vec(box.size)}\n'
            self.blocks.append(block)
        return self.meshes[key]

    def shape(self, box: Box) -> str:
        key = self.key(box)
        if key not in self.shapes:
            resource_id = f"PrimitiveShape3D_{len(self.shapes) + 1:04d}"
            self.shapes[key] = resource_id
            if box.mesh_faces is not None:
                # The exact collision union is represented by the original
                # overlapping convex boxes.  Their internal overlap is harmless
                # for a static body and avoids a concave collision approximation.
                block = f'[sub_resource type="BoxShape3D" id="{resource_id}"]\nsize = {gd_vec(box.size)}\n'
            elif box.primitive == "cylinder":
                radius = box.size[0] / 2.0
                block = (
                    f'[sub_resource type="CylinderShape3D" id="{resource_id}"]\n'
                    f'radius = {gd_float(radius)}\n'
                    f'height = {gd_float(box.size[1])}\n'
                )
            elif box.primitive == "sphere":
                block = (
                    f'[sub_resource type="SphereShape3D" id="{resource_id}"]\n'
                    f'radius = {gd_float(box.size[0] / 2.0)}\n'
                )
            elif box.primitive == "prism":
                hx, hy, hz = (value / 2.0 for value in box.size)
                points = (
                    (-hx, hy, hz), (hx, -hy, hz), (-hx, -hy, hz),
                    (-hx, hy, -hz), (hx, -hy, -hz), (-hx, -hy, -hz),
                )
                packed = ", ".join(gd_float(value) for point in points for value in point)
                block = (
                    f'[sub_resource type="ConvexPolygonShape3D" id="{resource_id}"]\n'
                    f'points = PackedVector3Array({packed})\n'
                )
            else:
                block = f'[sub_resource type="BoxShape3D" id="{resource_id}"]\nsize = {gd_vec(box.size)}\n'
            self.blocks.append(block)
        return self.shapes[key]


def write_custom_meshes(output_dir: Path, boxes: list[Box]) -> list[str]:
    """Write deterministic OBJ meshes for convex bodies that are not boxes."""
    custom = [box for box in boxes if box.mesh_faces is not None]
    if not custom:
        return []
    mesh_dir = output_dir / "joint_meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for index, box in enumerate(custom, 1):
        filename = f"{index:04d}_{sanitize_name(box.name)}.obj"
        relative = f"joint_meshes/{filename}"
        path = mesh_dir / filename
        vertices: list[tuple[float, float, float]] = []
        face_indices: list[tuple[int, ...]] = []
        assert box.mesh_faces is not None
        for face in box.mesh_faces:
            indices: list[int] = []
            for point in face:
                try:
                    vertex_index = next(
                        item_index
                        for item_index, existing in enumerate(vertices)
                        if all(abs(a - b) <= 1.0e-8 for a, b in zip(point, existing))
                    )
                except StopIteration:
                    vertices.append(point)
                    vertex_index = len(vertices) - 1
                indices.append(vertex_index + 1)
            face_indices.append(tuple(indices))
        lines = [f"o {sanitize_name(box.name)}", "s off"]
        lines.extend(f"v {gd_float(x)} {gd_float(y)} {gd_float(z)}" for x, y, z in vertices)
        for face in face_indices:
            for triangle_index in range(1, len(face) - 1):
                lines.append(f"f {face[0]} {face[triangle_index]} {face[triangle_index + 1]}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        box.mesh_file = relative
        files.append(relative)
    return files


def derive_resource_dir(output_dir: Path, project_root: Path | None, explicit: str | None) -> str:
    if explicit:
        if not explicit.startswith("res://"):
            raise GenerationError("--resource-dir must start with res://.", ["Use a path such as res://generated/stairs"])
        return explicit.rstrip("/")
    if project_root is None:
        raise GenerationError(
            "Either --project-root or --resource-dir is required so the scene can reference materials.",
            ["Pass --project-root PATH", "Pass --resource-dir res://PATH"],
        )
    project_root = project_root.resolve()
    if not (project_root / "project.godot").is_file():
        raise GenerationError(f"No project.godot exists under {project_root}.", ["Choose the correct --project-root"])
    try:
        relative = output_dir.resolve().relative_to(project_root)
    except ValueError as exc:
        raise GenerationError(
            "output_dir is outside --project-root and cannot be mapped to res://.",
            ["Place output_dir inside the project", "Pass an explicit --resource-dir"],
        ) from exc
    suffix = relative.as_posix().strip("/")
    return "res://" + suffix if suffix else "res://"


MATERIALS = {
    "stairs": ("stair_material.tres", "Color(0.46, 0.48, 0.52, 1)", 0.72, 0.0),
    "railings": ("railing_material.tres", "Color(0.12, 0.14, 0.16, 1)", 0.35, 0.65),
    "shaft": ("shaft_material.tres", "Color(0.58, 0.58, 0.56, 1)", 0.85, 0.0),
}


def write_materials(output_dir: Path) -> tuple[list[str], list[str]]:
    created: list[str] = []
    preserved: list[str] = []
    for _, (filename, color, roughness, metallic) in MATERIALS.items():
        path = output_dir / filename
        if path.exists():
            preserved.append(filename)
            continue
        content = (
            '[gd_resource type="StandardMaterial3D" format=3]\n\n'
            '[resource]\n'
            f'albedo_color = {color}\n'
            f'roughness = {gd_float(roughness)}\n'
            f'metallic = {gd_float(metallic)}\n'
        )
        path.write_text(content, encoding="utf-8")
        created.append(filename)
    return created, preserved


def scene_text(boxes: list[Box], resource_dir: str, scene_name: str, args: argparse.Namespace, layout: Layout) -> str:
    resources = Resources()
    for box in boxes:
        if box.mesh_faces is None:
            resources.mesh(box)
        if not box.metadata.get("debug"):
            resources.shape(box)
    custom_mesh_ids = {
        box.name: f"4_joint_mesh_{index:04d}"
        for index, box in enumerate((item for item in boxes if item.mesh_faces is not None), 1)
    }
    for index, box in enumerate((item for item in boxes if item.mesh_faces is not None), 1):
        if box.mesh_file is None:
            box.mesh_file = f"joint_meshes/{index:04d}_{sanitize_name(box.name)}.obj"
    preview_env_id = None
    if args.include_preview:
        preview_env_id = "Environment_preview"
        resources.blocks.append(
            '[sub_resource type="Environment" id="Environment_preview"]\n'
            'background_mode = 1\n'
            'background_color = Color(0.075, 0.085, 0.11, 1)\n'
            'ambient_light_source = 2\n'
            'ambient_light_color = Color(0.65, 0.68, 0.75, 1)\n'
            'ambient_light_energy = 0.8\n'
        )
    load_steps = 4 + len(custom_mesh_ids) + len(resources.blocks)
    lines = [f"[gd_scene load_steps={load_steps} format=3]", ""]
    base = resource_dir.rstrip("/")
    if base == "res:/":
        base = "res://"
    separator = "" if base.endswith("//") else "/"
    lines.extend(
        [
            f'[ext_resource type="Material" path="{base}{separator}stair_material.tres" id="1_stairs"]',
            f'[ext_resource type="Material" path="{base}{separator}railing_material.tres" id="2_railings"]',
            f'[ext_resource type="Material" path="{base}{separator}shaft_material.tres" id="3_shaft"]',
            "",
        ]
    )
    for box in (item for item in boxes if item.mesh_faces is not None):
        assert box.mesh_file is not None
        lines.append(
            f'[ext_resource type="Mesh" path="{base}{separator}{box.mesh_file}" id="{custom_mesh_ids[box.name]}"]'
        )
    if custom_mesh_ids:
        lines.append("")
    for block in resources.blocks:
        lines.extend([block.rstrip(), ""])
    lines.extend(
        [
            f'[node name="{scene_name}" type="Node3D"]',
            f'metadata/generator = "generate-godot-stairs"',
            f'metadata/generator_version = "{VERSION}"',
            f'metadata/layout = "{layout.kind}"',
            "",
        ]
    )
    material_id = {"Stairs": "1_stairs", "Landings": "1_stairs", "Railings": "2_railings", "Shaft": "3_shaft"}
    for category in ("Stairs", "Landings", "Railings", "Shaft"):
        lines.extend(
            [
                f'[node name="{category}" type="Node3D" parent="."]',
                "",
                f'[node name="Visual" type="Node3D" parent="{category}"]',
                "",
                f'[node name="Collision" type="StaticBody3D" parent="{category}"]',
                f'collision_layer = {args.collision_layer}',
                f'collision_mask = {args.collision_layer}',
                "",
            ]
        )
        category_boxes = [box for box in boxes if box.category == category]
        for box in category_boxes:
            mesh_reference = (
                f'ExtResource("{custom_mesh_ids[box.name]}")'
                if box.mesh_faces is not None
                else f'SubResource("{resources.mesh(box)}")'
            )
            lines.extend(
                [
                    f'[node name="{box.name}" type="MeshInstance3D" parent="{category}/Visual"]',
                    f'position = {gd_vec(box.center)}',
                ]
            )
            if any(abs(v) > EPS for v in box.rotation):
                lines.append(f'rotation = {gd_vec(box.rotation)}')
            lines.extend([f'mesh = {mesh_reference}', f'material_override = ExtResource("{material_id[category]}")'])
            for key, value in box.metadata.items():
                lines.append(f"metadata/{key} = {gd_value(value)}")
            lines.append("")
        for box in category_boxes:
            if box.metadata.get("debug"):
                continue
            shape_id = resources.shape(box)
            lines.extend(
                [
                    f'[node name="{box.name}" type="CollisionShape3D" parent="{category}/Collision"]',
                    f'position = {gd_vec(box.center)}',
                ]
            )
            if any(abs(v) > EPS for v in box.rotation):
                lines.append(f'rotation = {gd_vec(box.rotation)}')
            lines.extend([f'shape = SubResource("{shape_id}")', ""])
    debug_boxes = [box for box in boxes if box.category == "Debug"]
    if debug_boxes:
        lines.extend(
            [
                '[node name="Debug" type="Node3D" parent="."]',
                'metadata/generated_debug_geometry = true',
                "",
            ]
        )
        debug_groups = {
            "Boundary": "Boundaries",
            "Port": "Ports",
            "Direction": "Directions",
            "Connection": "Connections",
        }
        for group in debug_groups.values():
            lines.extend([f'[node name="{group}" type="Node3D" parent="Debug"]', ""])
        for box in debug_boxes:
            token = next((candidate for candidate in debug_groups if f"Debug_{candidate}_" in box.name), "Boundary")
            group = debug_groups[token]
            mesh_id = resources.mesh(box)
            lines.extend(
                [
                    f'[node name="{box.name}" type="MeshInstance3D" parent="Debug/{group}"]',
                    f'position = {gd_vec(box.center)}',
                ]
            )
            if any(abs(v) > EPS for v in box.rotation):
                lines.append(f'rotation = {gd_vec(box.rotation)}')
            lines.extend([f'mesh = SubResource("{mesh_id}")', 'material_override = ExtResource("2_railings")'])
            for key, value in box.metadata.items():
                lines.append(f"metadata/{key} = {gd_value(value)}")
            lines.append("")
    if args.include_preview:
        span = max(args.shaft_width, args.shaft_length, args.floor_height)
        lines.extend(
            [
                '[node name="Preview" type="Node3D" parent="."]',
                "",
                '[node name="Camera3D" type="Camera3D" parent="Preview"]',
                f'position = Vector3({gd_float(span * 1.35)}, {gd_float(args.floor_height * 1.3)}, {gd_float(span * 1.35)})',
                'rotation = Vector3(-0.45, 0.78, 0.0)',
                'current = true',
                "",
                '[node name="DirectionalLight3D" type="DirectionalLight3D" parent="Preview"]',
                'rotation = Vector3(-0.8, -0.6, 0.0)',
                'shadow_enabled = true',
                "",
                '[node name="WorldEnvironment" type="WorldEnvironment" parent="Preview"]',
                f'environment = SubResource("{preview_env_id}")',
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def count_report(boxes: list[Box], layout: Layout) -> dict[str, int]:
    counts = {category.lower(): sum(1 for box in boxes if box.category == category) for category in ("Stairs", "Landings", "Railings", "Shaft", "Debug")}
    counts.update(
        {
            "flights": len(layout.flights),
            "risers": sum(flight.risers for flight in layout.flights),
            "tread_nodes": sum(1 for box in boxes if box.category == "Stairs" and box.metadata.get("part") == "tread"),
            "riser_nodes": sum(1 for box in boxes if box.category == "Stairs" and box.metadata.get("part") == "riser"),
            "railing_posts": sum(
                1 for box in boxes
                if box.category == "Railings" and "_Post_" in box.name and not box.metadata.get("debug")
            ),
            "railing_joints": sum(
                1 for box in boxes if box.category == "Railings" and box.name.startswith("Joint_Post_")
            ),
            "railing_joint_fittings": sum(
                1 for box in boxes if box.category == "Railings" and box.name.startswith("Joint_Handrail_Fitting_")
            ),
            "visual_nodes": len(boxes),
            "debug_nodes": sum(1 for box in boxes if box.metadata.get("debug")),
            "collision_shapes": sum(1 for box in boxes if not box.metadata.get("debug")),
            "static_bodies": 4,
        }
    )
    return counts


def settings_report(args: argparse.Namespace) -> dict[str, object]:
    excluded = {"project_root", "resource_dir"}
    return {key: value for key, value in vars(args).items() if key not in excluded}


def report_number(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if abs(rounded) < 1.0e-12 else rounded


def report_vector(values: tuple[float, float, float]) -> list[float]:
    return [report_number(value) for value in values]


def anchor_geometry_hash(record: dict[str, object]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def anchor_source_id(scene_name: str) -> str:
    return re.sub(r"[^A-Z0-9_-]+", "-", scene_name.upper()).replace("_", "-").strip("-")


def make_anchor_frame(
    anchor_id: str,
    anchor_type: str,
    role: str,
    source_id: str,
    origin: tuple[float, float, float],
    forward: tuple[float, float, float],
    normal: tuple[float, float, float],
    bounds: dict[str, object],
    placement_limits: dict[str, object],
) -> dict[str, object]:
    limits = dict(placement_limits)
    limits.setdefault(
        "rotation_deg",
        {"yaw": [-180.0, 180.0], "pitch": [0.0, 0.0], "roll": [0.0, 0.0]},
    )
    record: dict[str, object] = {
        "anchor_id": anchor_id,
        "type": anchor_type,
        "role": role,
        "status": "active",
        "source_ref": {"artifact_id": "generation-report", "source_id": source_id},
        "origin": report_vector(origin),
        "forward": report_vector(forward),
        "normal": report_vector(normal),
        "up": [0.0, 1.0, 0.0],
        "bounds": bounds,
        "placement_limits": limits,
    }
    record["geometry_hash"] = anchor_geometry_hash(record)
    return record


def horizontal_frame(direction: tuple[float, float]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    length = math.hypot(direction[0], direction[1])
    if length <= EPS:
        raise GenerationError("Anchor frame direction is zero-length.")
    forward = (direction[0] / length, 0.0, direction[1] / length)
    normal = (-forward[2], 0.0, forward[0])
    return forward, normal


def landing_polygon(landing: Landing, args: argparse.Namespace) -> list[list[float]]:
    half_x, half_z = landing.size[0] / 2.0, landing.size[1] / 2.0
    corners = [
        (landing.center[0] - half_x, landing.center[1] - half_z),
        (landing.center[0] + half_x, landing.center[1] - half_z),
        (landing.center[0] + half_x, landing.center[1] + half_z),
        (landing.center[0] - half_x, landing.center[1] + half_z),
    ]
    return [[report_number(x), report_number(z)] for x, z in (transform_point(*corner, args) for corner in corners)]


def semantic_anchor_frames(layout: Layout, args: argparse.Namespace, shaft_generated: bool) -> list[dict[str, object]]:
    """Publish stable T01 frames from layout semantics, never emitted-node AABBs."""
    source_id = anchor_source_id(args.scene_name)
    prefix = f"AF-STAIRS-{source_id}"
    transition_id = f"STAIRS-{source_id}"
    frames: list[dict[str, object]] = []

    first = layout.flights[0]
    first_x, first_z = transform_point(*first.start, args)
    entry_direction = transform_direction(*first.direction, args)
    entry_forward, entry_normal = horizontal_frame(entry_direction)
    frames.append(make_anchor_frame(
        f"{prefix}-ENTRY-LOWER", "stair_entry", "lower_entry", transition_id,
        (first_x, first.base_y, first_z), entry_forward, entry_normal,
        {"clear_width_m": report_number(first.width), "level_id": "LOWER", "transition_id": transition_id},
        {"normal_offset_m": [report_number(-first.width / 2.0), report_number(first.width / 2.0)], "height_m": [0.0, 0.0]},
    ))

    last = layout.flights[-1]
    last_x, last_z = transform_point(*last.end, args)
    exit_direction = transform_direction(*last.direction, args)
    exit_forward, exit_normal = horizontal_frame(exit_direction)
    frames.append(make_anchor_frame(
        f"{prefix}-EXIT-UPPER", "stair_exit", "upper_exit", transition_id,
        (last_x, last.top_y, last_z), exit_forward, exit_normal,
        {"clear_width_m": report_number(last.width), "level_id": "UPPER", "transition_id": transition_id},
        {"normal_offset_m": [report_number(-last.width / 2.0), report_number(last.width / 2.0)], "height_m": [0.0, 0.0]},
    ))

    for index, flight in enumerate(layout.flights, 1):
        midpoint = (
            (flight.start[0] + flight.end[0]) / 2.0,
            (flight.start[1] + flight.end[1]) / 2.0,
        )
        x, z = transform_point(*midpoint, args)
        direction = transform_direction(*flight.direction, args)
        forward, normal = horizontal_frame(direction)
        frames.append(make_anchor_frame(
            f"{prefix}-PASSAGE-FLIGHT-{index:02d}", "point", "flight_passage", transition_id,
            (x, (flight.base_y + flight.top_y) / 2.0, z), forward, normal,
            {"radius_m": 0.0, "flight_id": flight.name, "run_m": report_number(flight.run), "rise_m": report_number(flight.top_y - flight.base_y), "clear_width_m": report_number(flight.width)},
            {"normal_offset_m": [report_number(-flight.width / 2.0), report_number(flight.width / 2.0)], "height_m": [0.0, 0.0]},
        ))

    for index, landing in enumerate(layout.landings, 1):
        polygon = landing_polygon(landing, args)
        origin_x, origin_z = min(polygon, key=lambda point: (point[0], point[1]))
        outgoing = layout.flights[min(index, len(layout.flights) - 1)].direction
        forward, _ = horizontal_frame(transform_direction(*outgoing, args))
        frames.append(make_anchor_frame(
            f"{prefix}-LANDING-{index:02d}", "floor", "landing", transition_id,
            (origin_x, landing.top_y, origin_z), forward, (0.0, 1.0, 0.0),
            {"polygon_xz": polygon, "elevation_m": report_number(landing.top_y), "landing_id": landing.name},
            {"normal_offset_m": [0.0, 0.0], "height_m": [0.0, 0.0]},
        ))

    shaft_x = args.shaft_width / 2.0 if args.origin == "south-west" else 0.0
    shaft_z = args.shaft_length / 2.0 if args.origin == "south-west" else 0.0
    frames.append(make_anchor_frame(
        f"{prefix}-SHAFT-CLEAR", "shaft", "clear_volume", transition_id,
        (shaft_x, 0.0, shaft_z), entry_forward, entry_normal,
        {"clear_bounds_xz": [report_number(shaft_x - args.shaft_width / 2.0), report_number(shaft_z - args.shaft_length / 2.0), report_number(shaft_x + args.shaft_width / 2.0), report_number(shaft_z + args.shaft_length / 2.0)], "bottom_y": 0.0, "top_y": report_number(args.floor_height)},
        {"normal_offset_m": [0.0, 0.0], "height_m": [0.0, report_number(args.floor_height)]},
    ))

    if shaft_generated:
        ox = args.shaft_width / 2.0 if args.origin == "south-west" else 0.0
        oz = args.shaft_length / 2.0 if args.origin == "south-west" else 0.0
        wall_height = args.shaft_wall_top - args.shaft_wall_bottom
        wall_specs = {
            "NORTH": ((ox - args.shaft_width / 2.0, args.shaft_wall_bottom, oz + args.shaft_length / 2.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), args.shaft_width),
            "EAST": ((ox + args.shaft_width / 2.0, args.shaft_wall_bottom, oz + args.shaft_length / 2.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), args.shaft_length),
            "SOUTH": ((ox + args.shaft_width / 2.0, args.shaft_wall_bottom, oz - args.shaft_length / 2.0), (-1.0, 0.0, 0.0), (0.0, 0.0, -1.0), args.shaft_width),
            "WEST": ((ox - args.shaft_width / 2.0, args.shaft_wall_bottom, oz - args.shaft_length / 2.0), (0.0, 0.0, 1.0), (-1.0, 0.0, 0.0), args.shaft_length),
        }
        for side, (origin, forward, normal, length) in wall_specs.items():
            frames.append(make_anchor_frame(
                f"{prefix}-SHAFT-FACE-{side}", "wall", "shaft_face", transition_id,
                origin, forward, normal,
                {"kind": "linear_surface", "length_m": report_number(length), "height_m": report_number(wall_height), "thickness_m": report_number(args.wall_thickness), "surface_side_id": f"{transition_id}/SHAFT/{side}"},
                {"along_normalized": [0.0, 1.0], "normal_offset_m": [0.0, report_number(args.wall_thickness)], "height_m": [0.0, report_number(wall_height)]},
            ))

    frames.sort(key=lambda frame: str(frame["anchor_id"]))
    ids = [frame["anchor_id"] for frame in frames]
    if len(ids) != len(set(ids)):
        raise GenerationError("Duplicate semantic anchor IDs were generated.")
    return frames


def write_report(path: Path, report: dict[str, object]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def failure_report(args: argparse.Namespace, message: str, solutions: list[str], failures: list[dict[str, object]] | None = None) -> int:
    output_dir = Path(args.output_dir).expanduser()
    report = {
        "schema_id": "caretaker.godot_stairs.generation_report",
        "schema_version": REPORT_SCHEMA_VERSION,
        "contract_version": ANCHOR_CONTRACT_VERSION,
        "generator_version": VERSION,
        "status": "error",
        "settings": settings_report(args),
        "errors": [message],
        "warnings": [],
        "solutions": solutions,
        "layout_failures": failures or [],
        "files": [],
    }
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_report(output_dir / "generation_report.json", report)
    except OSError:
        pass
    print(f"ERROR: {message}", file=sys.stderr)
    for solution in solutions:
        print(f"  - {solution}", file=sys.stderr)
    return 2


def validate_args(args: argparse.Namespace) -> None:
    if args.collision_layer < 1:
        raise GenerationError("--collision-layer must be a positive bit mask.", ["Use --collision-layer 1 or another positive mask"])
    if args.minimum_headroom > args.floor_height:
        raise GenerationError("Minimum headroom exceeds the floor height.", ["Reduce --minimum-headroom", "Increase --floor-height"])
    if args.railings != "none" and abs(args.rail_post_size - args.handrail_size) > EPS:
        raise GenerationError(
            "The deterministic railing pipeline requires --rail-post-size and --handrail-size to be equal.",
            ["Set both values to the same cross-section", "Use the default 0.05 m for both"],
        )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        validate_args(args)
        args.scene_name = sanitize_name(args.scene_name)
        output_dir = Path(args.output_dir).expanduser()
        project_root = Path(args.project_root).expanduser() if args.project_root else None
        resource_dir = derive_resource_dir(output_dir, project_root, args.resource_dir)
        try:
            layout, layout_failures = select_layout(args)
        except GenerationError as exc:
            failures: list[dict[str, object]] = []
            for kind, count in layout_candidates(args):
                try:
                    build_layout(kind, args, count)
                except GenerationError as inner:
                    label = kind if count is None else f"{kind}-{count}"
                    failures.append({"layout": label, "reason": str(inner), "solutions": inner.solutions})
            return failure_report(args, str(exc), exc.solutions, failures)
        boxes: list[Box] = []
        add_step_boxes(boxes, layout, args)
        railing_model, geometry_validation = build_railing_blueprint(layout, args)
        railing_boxes, compiled_validation = compile_railing_boxes(railing_model, args)
        boxes.extend(railing_boxes)
        geometry_validation["compiled"] = compiled_validation
        shaft_generated, warnings = add_shaft_walls(boxes, layout, args)
        shaft_validation = validate_shaft_clearance(boxes)
        geometry_validation["shaft"] = shaft_validation
        if not shaft_validation["ok"]:
            first = shaft_validation["issues"][0]
            raise GenerationError(
                f"Shaft wall {first['wall_id']} intersects {first['element_id']} by {first['minimum_penetration']:.9f} m.",
                ["Increase the clear shaft dimensions", "Reduce stair width", "Disable shaft walls"],
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        created_materials, preserved_materials = write_materials(output_dir)
        custom_mesh_files = write_custom_meshes(output_dir, boxes)
        scene_filename = args.scene_name + ".tscn"
        scene_path = output_dir / scene_filename
        scene_path.write_text(scene_text(boxes, resource_dir, args.scene_name, args, layout), encoding="utf-8")
        files = [scene_filename, *[value[0] for value in MATERIALS.values()], *custom_mesh_files, "generation_report.json"]
        upper_side = actual_side(layout.local_upper_side, args)
        report = {
            "schema_id": "caretaker.godot_stairs.generation_report",
            "schema_version": REPORT_SCHEMA_VERSION,
            "contract_version": ANCHOR_CONTRACT_VERSION,
            "generator_version": VERSION,
            "status": "ok",
            "scene": str(scene_path.resolve()),
            "resource_dir": resource_dir,
            "settings": settings_report(args),
            "selected_layout": {
                "type": layout.kind,
                "turn_direction": layout.turn_direction,
                "stair_width": round(layout.stair_width, 6),
                "tread_depth": round(layout.tread, 6),
                "riser_height": round(layout.riser_height, 6),
                "lower_entry_side": actual_side("south", args),
                "upper_exit_side": upper_side,
                "shaft_walls_generated": shaft_generated,
                "flights": [
                    {
                        "name": flight.name,
                        "risers": flight.risers,
                        "run": round(flight.run, 6),
                        "base_y": round(flight.base_y, 6),
                        "top_y": round(flight.top_y, 6),
                    }
                    for flight in layout.flights
                ],
            },
            "anchor_coordinate_space": {
                "units": "m",
                "horizontal_plane": "XZ",
                "up_axis": "+Y",
                "space": "generated_scene_local",
                "origin_mode": args.origin,
                "invert_z": args.invert_z,
            },
            "anchor_frames": semantic_anchor_frames(layout, args, shaft_generated),
            "counts": count_report(boxes, layout),
            "geometry_validation": geometry_validation,
            "materials": {
                "created": created_materials,
                "preserved": preserved_materials,
            },
            "assumptions": layout.assumptions,
            "warnings": warnings,
            "errors": [],
            "solutions": [],
            "layout_failures_before_selection": layout_failures,
            "files": files,
        }
        write_report(output_dir / "generation_report.json", report)
        missing = [name for name in files if not (output_dir / name).is_file() or (output_dir / name).stat().st_size == 0]
        if missing:
            return failure_report(args, "Generated files are missing or empty: " + ", ".join(missing), ["Check output permissions and free disk space"])
        print(json.dumps({"status": "ok", "scene": str(scene_path), "layout": layout.kind, "report": str(output_dir / "generation_report.json")}, ensure_ascii=False))
        return 0
    except GenerationError as exc:
        return failure_report(args, str(exc), exc.solutions)
    except OSError as exc:
        return failure_report(args, f"File operation failed: {exc}", ["Check paths and write permissions"])


if __name__ == "__main__":
    raise SystemExit(main())
