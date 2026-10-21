#!/usr/bin/env python3
"""Convert a conventionally annotated HTML/SVG floor plan into a Godot 4 blockout."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence

VERSION = "1.19.0"
SUPPORTED_TYPES = {"area", "floor", "floor-opening", "ceiling", "ceiling-opening", "wall", "opening", "door", "window", "obstacle", "stairs", "marker", "ignore"}
GENERIC_CLASS_MAP = {
    "area": "area", "room": "area", "floor": "floor", "floor-opening": "floor-opening", "floor-hole": "floor-opening",
    "ceiling": "ceiling", "ceiling-opening": "ceiling-opening", "ceiling-hole": "ceiling-opening", "wall": "wall",
    "partition": "wall", "opening": "opening", "door-gap": "door",
    "door": "door", "window": "window", "window-gap": "window", "obstacle": "obstacle", "stairs": "stairs",
    "steps": "stairs", "marker": "marker", "ignore": "ignore",
}
CODEX_PLAN_CLASS_MAP = {
    **GENERIC_CLASS_MAP, "old": "area", "corridor": "area", "support": "area",
    "equipment": "obstacle", "room-gap": "opening", "door": "ignore",
}


class ConversionError(RuntimeError):
    pass


class IframeSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.srcdocs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "iframe":
            value = dict(attrs).get("srcdoc")
            if value:
                self.srcdocs.append(value)


@dataclass
class Report:
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ignored: list[dict[str, str]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str, amount: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + amount

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


@dataclass
class Settings:
    scale: float = 0.05
    wall_height: float = 3.0
    wall_thickness: float = 0.18
    floor_thickness: float = 0.12
    floor_anchor: str = "top"
    floor_mesh: str = "slab"
    floor_extent: str = "wall-outer"
    floor_edge_offset: float | None = None
    generate_ceilings: bool = True
    ceiling_height: float = 3.0
    ceiling_thickness: float = 0.12
    ceiling_mesh: str = "slab"
    ceiling_extent: str = "wall-outer"
    ceiling_edge_offset: float | None = None
    wall_embed: float = 0.0
    wall_joint_mode: str = "butt"
    wall_joint_gap: float = 0.0005
    obstacle_height: float = 1.2
    default_elevation: float = 0.0
    opening_snap: float = 0.10
    door_height: float = 2.1
    door_bottom: float = 0.0
    window_height: float = 1.2
    window_bottom: float = 1.0
    wall_merge_tolerance: float = 0.02
    strict_wall_overlaps: bool = False
    ceiling_alignment_tolerance: float = 0.01
    strict_ceiling_alignment: bool = False
    origin: str = "center"
    invert_z: bool = False
    collision_layer: int = 1
    include_preview: bool = False
    strict: bool = False


@dataclass
class RawItem:
    element_id: str
    kind: str
    tag: str
    paths: list[list[tuple[float, float]]]
    closed: bool
    attrs: dict[str, str]
    text: str = ""


@dataclass
class Segment:
    a: tuple[float, float]
    b: tuple[float, float]
    source_id: str
    height: float
    thickness: float
    elevation: float


@dataclass
class Opening:
    a: tuple[float, float]
    b: tuple[float, float]
    source_id: str
    kind: str
    bottom: float
    height: float | None


@dataclass
class Box:
    name: str
    source_id: str
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    angle_y: float = 0.0
    material: str = "Wall"


@dataclass
class Marker:
    name: str
    source_id: str
    kind: str
    position: tuple[float, float, float]
    metadata: dict[str, Any] = field(default_factory=dict)


Affine = tuple[float, float, float, float, float, float]
IDENTITY: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def multiply(left: Affine, right: Affine) -> Affine:
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f)


def apply_matrix(matrix: Affine, point: tuple[float, float]) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    x, y = point
    return (a*x+c*y+e, b*x+d*y+f)


def parse_numbers(value: str) -> list[float]:
    return [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", value)]


def parse_transform(value: str | None) -> Affine:
    result = IDENTITY
    if not value:
        return result
    for name, raw in re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", value):
        values = parse_numbers(raw)
        op = IDENTITY
        if name == "matrix" and len(values) == 6:
            op = tuple(values)  # type: ignore[assignment]
        elif name == "translate" and values:
            op = (1, 0, 0, 1, values[0], values[1] if len(values) > 1 else 0)
        elif name == "scale" and values:
            op = (values[0], 0, 0, values[1] if len(values) > 1 else values[0], 0, 0)
        elif name == "rotate" and values:
            radians = math.radians(values[0]); cos_v = math.cos(radians); sin_v = math.sin(radians)
            rotation = (cos_v, sin_v, -sin_v, cos_v, 0, 0)
            if len(values) >= 3:
                cx, cy = values[1], values[2]
                op = multiply((1, 0, 0, 1, cx, cy), multiply(rotation, (1, 0, 0, 1, -cx, -cy)))
            else:
                op = rotation
        else:
            raise ConversionError(f"Unsupported or malformed SVG transform: {name}({raw})")
        result = multiply(result, op)
    return result


def extract_svg(source: Path) -> str:
    text = source.read_text(encoding="utf-8-sig")
    if source.suffix.lower() == ".svg" or text.lstrip().startswith("<svg"):
        match = re.search(r"<svg\b[\s\S]*?</svg>", text, re.IGNORECASE)
        if not match:
            raise ConversionError("No complete <svg> element found")
        return match.group(0)
    parser = IframeSourceParser(); parser.feed(text)
    for candidate in parser.srcdocs or [text]:
        match = re.search(r"<svg\b[\s\S]*?</svg>", html.unescape(candidate), re.IGNORECASE)
        if match:
            return match.group(0)
    raise ConversionError("No SVG found in the HTML or its iframe srcdoc")


def path_subpaths(data: str) -> tuple[list[list[tuple[float, float]]], bool]:
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", data)
    paths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    cursor = (0.0, 0.0); start = cursor; command = ""; i = 0; any_closed = False
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]; i += 1
        if command in "Zz":
            if current and current[-1] != start:
                current.append(start)
            any_closed = True; command = ""; continue
        if command in "MmLl":
            if i + 1 >= len(tokens): raise ConversionError("Incomplete SVG path coordinate pair")
            x, y = float(tokens[i]), float(tokens[i+1]); i += 2
            if command.islower(): x += cursor[0]; y += cursor[1]
            cursor = (x, y)
            if command in "Mm":
                if current: paths.append(current)
                current = [cursor]; start = cursor; command = "l" if command == "m" else "L"
            else: current.append(cursor)
        elif command in "Hh":
            if i >= len(tokens): raise ConversionError("Incomplete SVG H command")
            x = float(tokens[i]); i += 1
            if command == "h": x += cursor[0]
            cursor = (x, cursor[1]); current.append(cursor)
        elif command in "Vv":
            if i >= len(tokens): raise ConversionError("Incomplete SVG V command")
            y = float(tokens[i]); i += 1
            if command == "v": y += cursor[1]
            cursor = (cursor[0], y); current.append(cursor)
        else:
            raise ConversionError(f"SVG path command {command or tokens[i]!r} is not supported; use M/L/H/V/Z")
    if current: paths.append(current)
    return paths, any_closed


def element_paths(tag: str, attrs: dict[str, str]) -> tuple[list[list[tuple[float, float]]], bool]:
    n = lambda key, default="0": float(attrs.get(key, default))
    if tag == "rect":
        x, y, w, h = n("x"), n("y"), n("width"), n("height")
        return [[(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]], True
    if tag == "line":
        return [[(n("x1"),n("y1")),(n("x2"),n("y2"))]], False
    if tag in {"polyline", "polygon"}:
        nums = parse_numbers(attrs.get("points", ""))
        if len(nums) < 4 or len(nums) % 2: raise ConversionError(f"Malformed {tag} points")
        points = list(zip(nums[::2], nums[1::2])); closed = tag == "polygon"
        if closed and points[0] != points[-1]: points.append(points[0])
        return [points], closed
    if tag == "path": return path_subpaths(attrs.get("d", ""))
    if tag in {"circle", "ellipse"}: return [[(n("cx"), n("cy"))]], False
    if tag == "text": return [[(n("x"), n("y"))]], False
    raise ConversionError(f"SVG element <{tag}> is not supported")


def parse_assignment(raw: str) -> tuple[str, str]:
    if "=" not in raw: raise ConversionError(f"Expected NAME=VALUE, got: {raw}")
    return tuple(raw.split("=", 1))  # type: ignore[return-value]


def coerce(value: str) -> Any:
    if value.lower() in {"true", "false"}: return value.lower() == "true"
    try: return float(value) if any(c in value for c in ".eE") else int(value)
    except ValueError: return value


def classify(attrs: dict[str, str], class_map: dict[str, str], overrides: dict[str, dict[str, Any]], element_id: str) -> str | None:
    explicit = attrs.get("data-godot-type")
    if element_id in overrides and "type" in overrides[element_id]: explicit = str(overrides[element_id]["type"])
    if explicit: return explicit.lower()
    for class_name in attrs.get("class", "").split():
        if class_name in class_map: return class_map[class_name]
    return None


def load_items(root: ET.Element, class_map: dict[str, str], overrides: dict[str, dict[str, Any]], report: Report) -> list[RawItem]:
    items: list[RawItem] = []; serial = 0; stable_ids: set[str] = set()
    shape_tags = {"rect", "line", "polyline", "polygon", "path", "circle", "ellipse", "text"}
    def walk(node: ET.Element, parent_matrix: Affine) -> None:
        nonlocal serial
        attrs = {local_name(k): v for k, v in node.attrib.items()}; tag = local_name(node.tag)
        try: matrix = multiply(parent_matrix, parse_transform(attrs.get("transform")))
        except ConversionError as exc: report.errors.append(str(exc)); matrix = parent_matrix
        if tag in shape_tags:
            serial += 1
            stable_element_id = attrs.get("data-godot-id") or attrs.get("id")
            element_id = stable_element_id or f"{tag}_{serial}"
            if stable_element_id is not None and element_id in stable_ids:
                report.errors.append(f"{element_id}: duplicate stable SVG id; semantic anchor IDs would be ambiguous")
            else:
                if stable_element_id is not None:
                    stable_ids.add(element_id)
                kind = classify(attrs, class_map, overrides, element_id)
                descriptor={"id":element_id,"tag":tag,"classes":attrs.get("class","")}
                if kind is None: report.ignored.append({**descriptor,"reason": "no recognized semantic type"})
                elif kind not in SUPPORTED_TYPES: report.errors.append(f"{element_id}: unsupported semantic type {kind!r}")
                elif kind == "ignore": report.ignored.append({**descriptor,"reason": "mapped to ignore"})
                else:
                    try:
                        paths, closed = element_paths(tag, attrs)
                        if kind in {"floor-opening","ceiling-opening"} and (not closed or not paths or len(paths[0]) < 3):
                            raise ConversionError(f"{kind} must be a closed rect, polygon, or closed path")
                        items.append(RawItem(element_id, kind, tag, [[apply_matrix(matrix,p) for p in path] for path in paths], closed, attrs, " ".join(node.itertext()).strip()))
                        report.bump(f"input_{kind.replace('-', '_')}")
                    except (ConversionError, ValueError) as exc: report.errors.append(f"{element_id}: {exc}")
        for child in node: walk(child, matrix)
    walk(root, IDENTITY)
    return items


def value_for(item: RawItem, key: str, defaults: Settings, overrides: dict[str, dict[str, Any]]) -> float:
    attr_key = "data-" + key.replace("_", "-")
    if item.element_id in overrides and key in overrides[item.element_id]: return float(overrides[item.element_id][key])
    if key == "default_elevation" and "data-elevation" in item.attrs: return float(item.attrs["data-elevation"])
    if attr_key in item.attrs: return float(item.attrs[attr_key])
    return float(getattr(defaults, key))


def optional_value_for(item: RawItem, keys: Sequence[str], overrides: dict[str, dict[str, Any]]) -> float | None:
    item_overrides = overrides.get(item.element_id, {})
    for key in keys:
        if key in item_overrides:
            return float(item_overrides[key])
    for key in keys:
        attr_key = "data-" + key.replace("_", "-")
        if attr_key in item.attrs:
            return float(item.attrs[attr_key])
    return None


def flag_for(item: RawItem, key: str, overrides: dict[str, dict[str, Any]]) -> bool:
    value=overrides.get(item.element_id,{}).get(key,item.attrs.get("data-"+key.replace("_","-"),False))
    if isinstance(value,bool): return value
    return str(value).strip().lower() in {"1","true","yes","on"}


def text_value_for(item: RawItem, key: str, overrides: dict[str, dict[str, Any]]) -> str | None:
    """Read optional non-geometric handoff metadata without guessing a value."""
    value=overrides.get(item.element_id,{}).get(key,item.attrs.get("data-"+key.replace("_","-")))
    if value is None:
        return None
    text=str(value).strip()
    return text or None


def opening_dimensions(item: RawItem, settings: Settings, overrides: dict[str, dict[str, Any]]) -> tuple[float, float | None]:
    if item.kind == "door":
        bottom = optional_value_for(item, ("door_bottom", "opening_bottom"), overrides)
        height = optional_value_for(item, ("door_height", "opening_height"), overrides)
        bottom = settings.door_bottom if bottom is None else bottom
        height = settings.door_height if height is None else height
    elif item.kind == "window":
        bottom = optional_value_for(item, ("window_bottom", "opening_bottom"), overrides)
        height = optional_value_for(item, ("window_height", "opening_height"), overrides)
        bottom = settings.window_bottom if bottom is None else bottom
        height = settings.window_height if height is None else height
    else:
        bottom = optional_value_for(item, ("opening_bottom",), overrides)
        height = optional_value_for(item, ("opening_height",), overrides)
        bottom = 0.0 if bottom is None else bottom
    if bottom < 0:
        raise ConversionError("opening bottom must be zero or greater")
    if height is not None and height <= 0:
        raise ConversionError("opening height must be greater than zero")
    return bottom, height


def polygon_of(item: RawItem) -> list[tuple[float, float]]:
    if not item.paths or len(item.paths[0]) < 3: raise ConversionError(f"{item.element_id}: a polygonal element is required")
    points = item.paths[0][:]
    if points[0] == points[-1]: points.pop()
    return points


def segments_of(item: RawItem, settings: Settings, overrides: dict[str, dict[str, Any]]) -> list[Segment]:
    result: list[Segment] = []
    for path in item.paths:
        for a, b in zip(path, path[1:]):
            if math.dist(a, b) > 1e-9:
                result.append(Segment(a,b,item.element_id,value_for(item,"wall_height",settings,overrides),value_for(item,"wall_thickness",settings,overrides),value_for(item,"default_elevation",settings,overrides)))
    return result


def point_segment_distance(point: tuple[float,float], a: tuple[float,float], b: tuple[float,float]) -> float:
    dx, dy = b[0]-a[0], b[1]-a[1]
    if dx*dx+dy*dy == 0: return math.dist(point,a)
    t = max(0.0,min(1.0,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/(dx*dx+dy*dy)))
    return math.dist(point,(a[0]+t*dx,a[1]+t*dy))


def carve_opening(wall: Segment, opening: Opening, snap: float) -> list[Segment]:
    a,b = wall.a,wall.b; c,d = opening.a,opening.b; vx,vy=b[0]-a[0],b[1]-a[1]; length=math.hypot(vx,vy)
    if length <= 1e-9: return []
    ux,uy=vx/length,vy/length; cross = abs((d[0]-c[0])*uy-(d[1]-c[1])*ux)
    if cross > snap or point_segment_distance(c,a,b)>snap or point_segment_distance(d,a,b)>snap: return [wall]
    tc=(c[0]-a[0])*ux+(c[1]-a[1])*uy; td=(d[0]-a[0])*ux+(d[1]-a[1])*uy
    lo=max(0.0,min(tc,td)); hi=min(length,max(tc,td))
    if hi-lo <= 1e-9: return [wall]
    wall_bottom=wall.elevation; wall_top=wall.elevation+wall.height
    cut_bottom=opening.bottom; cut_top=math.inf if opening.height is None else opening.bottom+opening.height
    clipped_bottom=max(wall_bottom,cut_bottom); clipped_top=min(wall_top,cut_top)
    if clipped_top-clipped_bottom <= 1e-9: return [wall]
    out=[]
    if lo>1e-6: out.append(Segment(a,(a[0]+ux*lo,a[1]+uy*lo),wall.source_id,wall.height,wall.thickness,wall.elevation))
    if hi<length-1e-6: out.append(Segment((a[0]+ux*hi,a[1]+uy*hi),b,wall.source_id,wall.height,wall.thickness,wall.elevation))
    inner_a=(a[0]+ux*lo,a[1]+uy*lo); inner_b=(a[0]+ux*hi,a[1]+uy*hi)
    if clipped_bottom-wall_bottom > 1e-6:
        out.append(Segment(inner_a,inner_b,wall.source_id,clipped_bottom-wall_bottom,wall.thickness,wall_bottom))
    if wall_top-clipped_top > 1e-6:
        out.append(Segment(inner_a,inner_b,wall.source_id,wall_top-clipped_top,wall.thickness,clipped_top))
    return out


def merge_collinear_segments(segments: Sequence[Segment], tolerance: float) -> list[Segment]:
    """Snap compatible collinear segments to one line and union their intervals."""
    groups: list[dict[str, Any]] = []
    property_tolerance = 1e-7
    for segment in segments:
        dx, dz = segment.b[0]-segment.a[0], segment.b[1]-segment.a[1]
        length = math.hypot(dx, dz)
        if length <= 1e-9:
            continue
        ux, uz = dx/length, dz/length
        if ux < -1e-9 or (abs(ux) <= 1e-9 and uz < 0):
            ux, uz = -ux, -uz
        selected = None
        for group in groups:
            gux, guz = group["direction"]
            compatible_properties = (
                abs(segment.height-group["height"]) <= property_tolerance
                and abs(segment.thickness-group["thickness"]) <= property_tolerance
                and abs(segment.elevation-group["elevation"]) <= property_tolerance
            )
            if not compatible_properties or abs(ux*guz-uz*gux) > 1e-6:
                continue
            normal = (-guz, gux)
            offset_a = segment.a[0]*normal[0]+segment.a[1]*normal[1]
            offset_b = segment.b[0]*normal[0]+segment.b[1]*normal[1]
            if max(abs(offset_a-group["offset"]), abs(offset_b-group["offset"])) <= tolerance:
                selected = group
                break
        if selected is None:
            normal = (-uz, ux)
            selected = {
                "direction": (ux, uz),
                "offset": segment.a[0]*normal[0]+segment.a[1]*normal[1],
                "height": segment.height,
                "thickness": segment.thickness,
                "elevation": segment.elevation,
                "segments": [],
            }
            groups.append(selected)
        selected["segments"].append(segment)

    merged: list[Segment] = []
    for group in groups:
        ux, uz = group["direction"]; nx, nz = -uz, ux; offset = group["offset"]
        intervals = []
        for segment in group["segments"]:
            ta = segment.a[0]*ux+segment.a[1]*uz
            tb = segment.b[0]*ux+segment.b[1]*uz
            intervals.append((min(ta,tb),max(ta,tb),{segment.source_id}))
        intervals.sort(key=lambda item: item[0])
        start, end, source_ids = intervals[0]
        for next_start, next_end, next_ids in intervals[1:]:
            if next_start <= end+tolerance:
                end = max(end,next_end); source_ids.update(next_ids)
            else:
                a=(ux*start+nx*offset,uz*start+nz*offset); b=(ux*end+nx*offset,uz*end+nz*offset)
                merged.append(Segment(a,b,"|".join(sorted(source_ids)),group["height"],group["thickness"],group["elevation"]))
                start,end,source_ids=next_start,next_end,set(next_ids)
        a=(ux*start+nx*offset,uz*start+nz*offset); b=(ux*end+nx*offset,uz*end+nz*offset)
        merged.append(Segment(a,b,"|".join(sorted(source_ids)),group["height"],group["thickness"],group["elevation"]))
    return merged


def split_at_intersections(segments: Sequence[Segment], tolerance: float) -> list[Segment]:
    """Split non-collinear walls where their center lines cross."""
    cuts: list[list[float]] = [[0.0,1.0] for _ in segments]
    for index, first in enumerate(segments):
        rx, rz = first.b[0]-first.a[0], first.b[1]-first.a[1]
        first_length = math.hypot(rx,rz)
        for other_index in range(index+1,len(segments)):
            second=segments[other_index]; sx,sz=second.b[0]-second.a[0],second.b[1]-second.a[1]
            second_length=math.hypot(sx,sz); denominator=rx*sz-rz*sx
            if first_length<=1e-9 or second_length<=1e-9 or abs(denominator)<=1e-10:
                continue
            qx,qz=second.a[0]-first.a[0],second.a[1]-first.a[1]
            t=(qx*sz-qz*sx)/denominator; u=(qx*rz-qz*rx)/denominator
            first_eps=tolerance/max(first_length,1e-9); second_eps=tolerance/max(second_length,1e-9)
            if -first_eps<=t<=1+first_eps and -second_eps<=u<=1+second_eps:
                if first_eps<t<1-first_eps: cuts[index].append(max(0.0,min(1.0,t)))
                if second_eps<u<1-second_eps: cuts[other_index].append(max(0.0,min(1.0,u)))
    result=[]
    for segment,parameters in zip(segments,cuts):
        unique=[]
        for value in sorted(parameters):
            if not unique or abs(value-unique[-1])>1e-9: unique.append(value)
        dx,dz=segment.b[0]-segment.a[0],segment.b[1]-segment.a[1]
        for start,end in zip(unique,unique[1:]):
            a=(segment.a[0]+dx*start,segment.a[1]+dz*start); b=(segment.a[0]+dx*end,segment.a[1]+dz*end)
            if math.dist(a,b)>1e-9:
                result.append(Segment(a,b,segment.source_id,segment.height,segment.thickness,segment.elevation))
    return result


def normalize_wall_segments(segments: Sequence[Segment], tolerance: float) -> tuple[list[Segment], dict[str,int]]:
    merged=merge_collinear_segments(segments,tolerance)
    normalized=split_at_intersections(merged,tolerance)
    return normalized,{"before":len(segments),"after_merge":len(merged),"after_intersections":len(normalized)}


def detect_parallel_wall_overlaps(segments: Sequence[Segment]) -> list[dict[str,Any]]:
    """Report positive-volume intersections between parallel final wall prisms."""
    overlaps=[]
    for first_index,first in enumerate(segments):
        first_dx,first_dz=first.b[0]-first.a[0],first.b[1]-first.a[1]
        first_length=math.hypot(first_dx,first_dz)
        if first_length<=1e-9:
            continue
        ux,uz=first_dx/first_length,first_dz/first_length
        nx,nz=-uz,ux
        first_start=first.a[0]*ux+first.a[1]*uz
        first_end=first.b[0]*ux+first.b[1]*uz
        first_min,first_max=min(first_start,first_end),max(first_start,first_end)
        for second_index in range(first_index+1,len(segments)):
            second=segments[second_index]
            second_dx,second_dz=second.b[0]-second.a[0],second.b[1]-second.a[1]
            second_length=math.hypot(second_dx,second_dz)
            if second_length<=1e-9:
                continue
            second_ux,second_uz=second_dx/second_length,second_dz/second_length
            if abs(ux*second_uz-uz*second_ux)>1e-6:
                continue
            second_start=second.a[0]*ux+second.a[1]*uz
            second_end=second.b[0]*ux+second.b[1]*uz
            overlap_length=min(first_max,max(second_start,second_end))-max(first_min,min(second_start,second_end))
            if overlap_length<=1e-7:
                continue
            first_offset=(first.a[0]+first.b[0])*0.5*nx+(first.a[1]+first.b[1])*0.5*nz
            second_offset=(second.a[0]+second.b[0])*0.5*nx+(second.a[1]+second.b[1])*0.5*nz
            axis_offset=abs(second_offset-first_offset)
            overlap_depth=(first.thickness+second.thickness)/2-axis_offset
            if overlap_depth<=1e-7:
                continue
            vertical_bottom=max(first.elevation,second.elevation)
            vertical_top=min(first.elevation+first.height,second.elevation+second.height)
            vertical_overlap=vertical_top-vertical_bottom
            if vertical_overlap<=1e-7:
                continue
            source_ids=sorted({value for value in (*first.source_id.split("|"),*second.source_id.split("|")) if value})
            overlaps.append({
                "segment_indices":[first_index+1,second_index+1],
                "source_ids":source_ids,
                "axis_offset_m":report_number(axis_offset),
                "overlap_length_m":report_number(overlap_length),
                "overlap_depth_m":report_number(overlap_depth),
                "vertical_overlap_m":report_number(vertical_overlap),
                "estimated_overlap_volume_m3":report_number(overlap_length*overlap_depth*vertical_overlap),
                "thicknesses_m":[report_number(first.thickness),report_number(second.thickness)],
                "base_elevations_m":[report_number(first.elevation),report_number(second.elevation)],
                "heights_m":[report_number(first.height),report_number(second.height)],
                "different_thickness":abs(first.thickness-second.thickness)>1e-7,
            })
    return overlaps


def resolve_wall_joints(segments: Sequence[Segment], tolerance: float, gap: float) -> tuple[list[Segment], int]:
    """Turn overlapping center-line junctions into stable through-and-butt joints."""
    adjusted=[Segment(item.a,item.b,item.source_id,item.height,item.thickness,item.elevation) for item in segments]
    endpoints=[(index,end,point) for index,item in enumerate(adjusted) for end,point in (("a",item.a),("b",item.b))]
    clusters: list[list[tuple[int,str,tuple[float,float]]]]=[]
    for endpoint in endpoints:
        for cluster in clusters:
            if math.dist(endpoint[2],cluster[0][2]) <= tolerance:
                cluster.append(endpoint); break
        else:
            clusters.append([endpoint])
    resolved=0
    for cluster in clusters:
        if len(cluster)<2:
            continue
        node=(sum(item[2][0] for item in cluster)/len(cluster),sum(item[2][1] for item in cluster)/len(cluster))
        refs=[]
        for index,end,_ in cluster:
            segment=adjusted[index]
            other=segment.b if end=="a" else segment.a
            length=math.dist(node,other)
            if length<=1e-8:
                continue
            refs.append({"index":index,"end":end,"length":length,"direction":((other[0]-node[0])/length,(other[1]-node[1])/length)})
        if len(refs)<2:
            continue
        opposite_pairs=[]
        for first_pos,first in enumerate(refs):
            for second in refs[first_pos+1:]:
                first_segment=adjusted[first["index"]]; second_segment=adjusted[second["index"]]
                same_vertical=(abs(first_segment.elevation-second_segment.elevation)<=1e-7 and abs(first_segment.height-second_segment.height)<=1e-7)
                cross=abs(first["direction"][0]*second["direction"][1]-first["direction"][1]*second["direction"][0])
                dot=first["direction"][0]*second["direction"][0]+first["direction"][1]*second["direction"][1]
                if same_vertical and cross<=1e-6 and dot<-0.999999:
                    opposite_pairs.append((first["length"]+second["length"],first,second))
        through_refs=[]
        if opposite_pairs:
            _,first,second=max(opposite_pairs,key=lambda item:item[0])
            through_refs=[first,second]
        else:
            through_refs=[max(refs,key=lambda item:(item["length"],-item["index"]))]
        through_indices={item["index"] for item in through_refs}
        reference=through_refs[0]
        through_segment=adjusted[reference["index"]]
        butt_refs=[]
        for item in refs:
            if item["index"] in through_indices:
                continue
            segment=adjusted[item["index"]]
            same_vertical=(abs(segment.elevation-through_segment.elevation)<=1e-7 and abs(segment.height-through_segment.height)<=1e-7)
            cross=abs(item["direction"][0]*reference["direction"][1]-item["direction"][1]*reference["direction"][0])
            if same_vertical and cross>1e-6:
                butt_refs.append(item)
        if not butt_refs:
            continue
        if len(through_refs)==1:
            extension=max(adjusted[item["index"]].thickness for item in butt_refs)/2
            direction=reference["direction"]
            point=(node[0]-direction[0]*extension,node[1]-direction[1]*extension)
            segment=adjusted[reference["index"]]
            if reference["end"]=="a": segment.a=point
            else: segment.b=point
        for item in butt_refs:
            segment=adjusted[item["index"]]
            trim=through_segment.thickness/2+gap
            if trim>=item["length"]-1e-7:
                continue
            direction=item["direction"]
            point=(node[0]+direction[0]*trim,node[1]+direction[1]*trim)
            if item["end"]=="a": segment.a=point
            else: segment.b=point
            resolved+=1
    return adjusted,resolved


def polygon_area(points: Sequence[tuple[float,float]]) -> float:
    return abs(sum(
        points[index][0]*points[(index+1)%len(points)][1]
        - points[(index+1)%len(points)][0]*points[index][1]
        for index in range(len(points))
    ))/2


def signed_polygon_area(points: Sequence[tuple[float,float]]) -> float:
    return sum(
        points[index][0]*points[(index+1)%len(points)][1]
        - points[(index+1)%len(points)][0]*points[index][1]
        for index in range(len(points))
    )/2


def polygon_centroid(points: Sequence[tuple[float,float]]) -> tuple[float,float]:
    """Return the area centroid of a non-degenerate simple polygon."""
    signed_area=signed_polygon_area(points)
    if abs(signed_area)<=1e-12:
        return (
            sum(point[0] for point in points)/len(points),
            sum(point[1] for point in points)/len(points),
        )
    factor=1/(6*signed_area)
    x=sum(
        (points[index][0]+points[(index+1)%len(points)][0])
        *(points[index][0]*points[(index+1)%len(points)][1]-points[(index+1)%len(points)][0]*points[index][1])
        for index in range(len(points))
    )*factor
    z=sum(
        (points[index][1]+points[(index+1)%len(points)][1])
        *(points[index][0]*points[(index+1)%len(points)][1]-points[(index+1)%len(points)][0]*points[index][1])
        for index in range(len(points))
    )*factor
    return x,z


def report_number(value: float) -> float:
    """Keep report coordinates stable and free of binary floating-point noise."""
    rounded=round(float(value),9)
    return 0.0 if abs(rounded)<1e-12 else rounded


def report_vector3(values: Sequence[float]) -> list[float]:
    return [report_number(value) for value in values]


def canonical_segment(a: tuple[float,float], b: tuple[float,float]) -> tuple[tuple[float,float],tuple[float,float]]:
    """Orient a source segment independently of authored endpoint order."""
    return (a,b) if (report_number(a[0]),report_number(a[1])) <= (report_number(b[0]),report_number(b[1])) else (b,a)


def anchor_geometry_hash(record: dict[str,Any]) -> str:
    payload=json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return "sha256:"+hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_anchor_frame(
    anchor_id: str,
    anchor_type: str,
    source_id: str,
    role: str,
    origin: Sequence[float],
    forward: Sequence[float],
    normal: Sequence[float],
    up: Sequence[float],
    bounds: dict[str,Any],
    placement_limits: dict[str,Any],
    metadata: dict[str,Any] | None = None,
) -> dict[str,Any]:
    record: dict[str,Any]={
        "anchor_id":anchor_id,
        "type":anchor_type,
        "role":role,
        "status":"active",
        "source_ref":{"artifact_id":"geometry-handoff","source_kind":"svg","source_id":source_id},
        "origin":report_vector3(origin),
        "forward":report_vector3(forward),
        "normal":report_vector3(normal),
        "up":report_vector3(up),
        "bounds":bounds,
        "placement_limits":placement_limits,
    }
    if metadata:
        record["metadata"]=metadata
    record["geometry_hash"]=anchor_geometry_hash(record)
    return record


def semantic_anchor_frames(
    items: Sequence[RawItem],
    floors: Sequence[tuple[str,list[tuple[float,float]],float,float]],
    ceilings: Sequence[tuple[str,list[tuple[float,float]],float,float]],
    walls: Sequence[Segment],
    settings: Settings,
    overrides: dict[str,dict[str,Any]],
    world,
) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    """Build T01 semantic frames from stable source elements, before wall splitting."""
    frames: list[dict[str,Any]]=[]; issues: list[dict[str,Any]]=[]
    stable_items={item.element_id:item for item in items if item.attrs.get("data-godot-id") or item.attrs.get("id")}
    for item in items:
        if item.kind not in {"area","floor","ceiling","wall","door","marker"}:
            continue
        if item.element_id not in stable_items:
            issues.append({
                "source_id":item.element_id,
                "code":"stable_svg_id_missing",
                "severity":"blocking",
                "message":"Semantic anchor frame omitted because the source element has no explicit id or data-godot-id",
            })

    walls_by_source: dict[str,list[Segment]]={}
    for wall in walls:
        if wall.source_id in stable_items:
            walls_by_source.setdefault(wall.source_id,[]).append(wall)
    for source_id in sorted(walls_by_source):
        source_walls=walls_by_source[source_id]
        source_item=stable_items[source_id]
        surface_side_id=text_value_for(source_item,"surface_side_id",overrides)
        normal_side=text_value_for(source_item,"normal_side",overrides)
        normal_side=normal_side.lower() if normal_side is not None else None
        if surface_side_id is None or normal_side not in {"normal","opposite"}:
            issues.append({
                "source_id":source_id,
                "code":"wall_side_ambiguous",
                "severity":"blocking",
                "message":"Wall anchor frame omitted; set data-surface-side-id and data-normal-side=normal|opposite relative to canonical endpoint order",
            })
            continue
        for index,wall in enumerate(source_walls,1):
            start,end=canonical_segment(world(wall.a),world(wall.b))
            dx,dz=end[0]-start[0],end[1]-start[1]; length=math.hypot(dx,dz)
            if length<=1e-9:
                issues.append({"source_id":source_id,"code":"wall_segment_degenerate","severity":"blocking","message":"Wall anchor frame omitted for a zero-length source segment"})
                continue
            canonical_forward=(dx/length,0.0,dz/length); canonical_normal=(-canonical_forward[2],0.0,canonical_forward[0])
            if normal_side=="normal":
                frame_origin=start; forward=canonical_forward; normal=canonical_normal
            else:
                frame_origin=end; forward=tuple(-value for value in canonical_forward); normal=tuple(-value for value in canonical_normal)
            suffix="wall" if len(source_walls)==1 else f"wall:edge-{index}"
            frames.append(make_anchor_frame(
                f"svg:{source_id}:{suffix}","wall",source_id,"surface",
                (frame_origin[0],wall.elevation,frame_origin[1]),forward,normal,(0.0,1.0,0.0),
                {
                    "kind":"linear_surface","length_m":report_number(length),"height_m":report_number(wall.height),
                    "thickness_m":report_number(wall.thickness),"orientation_policy":"canonical_endpoint_order",
                    "surface_side_id":surface_side_id,"normal_side":normal_side,"source_segment_index":index,
                },
                {"along_normalized":[0.0,1.0],"normal_offset_m":[0.0,0.0],"height_m":[0.0,report_number(wall.height)]},
            ))

    for anchor_type,surfaces in (("floor",floors),("ceiling",ceilings)):
        for source_id,polygon,elevation,thickness in surfaces:
            if source_id not in stable_items or not polygon:
                continue
            origin=min(polygon,key=lambda point:(report_number(point[0]),report_number(point[1])))
            normal=(0.0,1.0,0.0) if anchor_type=="floor" else (0.0,-1.0,0.0)
            frames.append(make_anchor_frame(
                f"svg:{source_id}:{anchor_type}",anchor_type,source_id,"surface",
                (origin[0],elevation,origin[1]),(1.0,0.0,0.0),normal,(0.0,1.0,0.0),
                {
                    "kind":"planar_surface","polygon_xz_m":[[report_number(x),report_number(z)] for x,z in polygon],
                    "elevation_m":report_number(elevation),"thickness_m":report_number(thickness),"orientation_policy":"world_positive_x",
                },
                {"normal_offset_m":[0.0,0.0],"height_m":[0.0,0.0]},
            ))

    for item in items:
        if item.kind!="door" or item.element_id not in stable_items:
            continue
        segments=[Segment(world(segment.a),world(segment.b),segment.source_id,segment.height,segment.thickness,segment.elevation) for segment in segments_of(item,settings,overrides)]
        if len(segments)!=1:
            issues.append({"source_id":item.element_id,"code":"door_geometry_ambiguous","severity":"blocking","message":"Door anchor frames require exactly one non-zero source segment"})
            continue
        segment=segments[0]; start,end=canonical_segment(segment.a,segment.b); dx,dz=end[0]-start[0],end[1]-start[1]; width=math.hypot(dx,dz)
        if width<=1e-9:
            issues.append({"source_id":item.element_id,"code":"door_geometry_degenerate","severity":"blocking","message":"Door anchor frames require a non-zero source segment"})
            continue
        canonical_forward=(dx/width,0.0,dz/width); canonical_normal=(-canonical_forward[2],0.0,canonical_forward[0]); bottom,height=opening_dimensions(item,settings,overrides)
        threshold_y=value_for(item,"default_elevation",settings,overrides)+bottom; center=((start[0]+end[0])/2,threshold_y,(start[1]+end[1])/2)
        inside_side=text_value_for(item,"inside_side",overrides)
        inside_side=inside_side.lower() if inside_side is not None else None
        valid_inside=inside_side in {"normal","opposite"}
        if inside_side is not None and not valid_inside:
            issues.append({"source_id":item.element_id,"code":"door_inside_side_invalid","severity":"blocking","message":"inside_side must be normal or opposite relative to canonical endpoint order"})
        if inside_side=="normal":
            semantic_forward=canonical_forward; semantic_normal=canonical_normal
        else:
            semantic_forward=tuple(-value for value in canonical_forward); semantic_normal=tuple(-value for value in canonical_normal)
        common_bounds={
            "kind":"portal","width_m":report_number(width),"height_m":report_number(height or 0.0),"bottom_m":report_number(bottom),
            "wall_thickness_m":report_number(segment.thickness),"orientation_policy":"canonical_endpoint_order","inside_side":inside_side,
        }
        limits={"along_normalized":[0.0,1.0],"normal_offset_m":[report_number(-segment.thickness/2),report_number(segment.thickness/2)],"height_m":[0.0,report_number(height or 0.0)]}
        if valid_inside:
            frames.append(make_anchor_frame(
                f"svg:{item.element_id}:door:center","door",item.element_id,"center",center,semantic_forward,semantic_normal,(0.0,1.0,0.0),
                common_bounds,limits,{"side_status":"explicit"},
            ))
            half=segment.thickness/2
            inside=(center[0]+semantic_normal[0]*half,center[1],center[2]+semantic_normal[2]*half)
            outside=(center[0]-semantic_normal[0]*half,center[1],center[2]-semantic_normal[2]*half)
            for role,origin in (("threshold_inside",inside),("threshold_outside",outside)):
                frames.append(make_anchor_frame(
                    f"svg:{item.element_id}:door:{role}","door",item.element_id,role,origin,semantic_forward,semantic_normal,(0.0,1.0,0.0),
                    common_bounds,limits,{"side_status":"explicit"},
                ))
        else:
            issues.append({"source_id":item.element_id,"code":"door_side_ambiguous","severity":"blocking","message":"Door anchor frames omitted; set data-inside-side=normal|opposite or the equivalent override"})
        hinge_side=text_value_for(item,"hinge_side",overrides)
        hinge_side=hinge_side.lower() if hinge_side is not None else None
        if hinge_side is not None and hinge_side not in {"start","end"}:
            issues.append({"source_id":item.element_id,"code":"door_hinge_side_invalid","severity":"blocking","message":"hinge_side must be start or end relative to canonical endpoint order"})
        elif hinge_side in {"start","end"} and valid_inside:
            hinge=start if hinge_side=="start" else end
            frames.append(make_anchor_frame(
                f"svg:{item.element_id}:door:hinge","door",item.element_id,"hinge",(hinge[0],threshold_y,hinge[1]),semantic_forward,semantic_normal,(0.0,1.0,0.0),
                common_bounds,limits,{"hinge_side":hinge_side,"side_status":"explicit"},
            ))

    for item in items:
        if item.kind!="marker" or item.element_id not in stable_items:
            continue
        declared_id=text_value_for(item,"anchor_id",overrides); declared_role=text_value_for(item,"anchor_role",overrides)
        if declared_id is None and declared_role is None:
            continue
        points=[world(point) for path in item.paths for point in path]
        if not points:
            issues.append({"source_id":item.element_id,"code":"marker_position_missing","severity":"blocking","message":"Explicit marker anchor has no exact position"})
            continue
        x=sum(point[0] for point in points)/len(points); z=sum(point[1] for point in points)/len(points); elevation=value_for(item,"default_elevation",settings,overrides)
        metadata={key:value for key,value in (("declared_anchor_id",declared_id),("declared_anchor_role",declared_role),("space_id",text_value_for(item,"space_id",overrides))) if value is not None}
        frames.append(make_anchor_frame(
            f"svg:{item.element_id}:point","point",item.element_id,declared_role or "point",(x,elevation,z),(1.0,0.0,0.0),(0.0,0.0,1.0),(0.0,1.0,0.0),
            {"kind":"point","radius_m":0.0},{"normal_offset_m":[0.0,0.0],"height_m":[0.0,0.0]},metadata,
        ))
    return sorted(frames,key=lambda frame:frame["anchor_id"]),sorted(issues,key=lambda issue:(issue["source_id"],issue["code"]))


def opening_handoff_record(
    surface: str,
    opening_id: str,
    source_id: str,
    space_id: str | None,
    polygon: Sequence[tuple[float,float]],
    elevation: float,
    surfaces: Sequence[tuple[str,list[tuple[float,float]],float,float]],
    markers: Sequence[Marker],
) -> dict[str,Any]:
    xs=[point[0] for point in polygon]; zs=[point[1] for point in polygon]
    minimum=(min(xs),min(zs)); maximum=(max(xs),max(zs))
    bounds_center=((minimum[0]+maximum[0])/2,(minimum[1]+maximum[1])/2)
    centroid=polygon_centroid(polygon)
    contained=[marker for marker in markers if point_in_polygon((marker.position[0],marker.position[2]),polygon)]
    applied=any(abs(surface_elevation-elevation)<=1e-7 and polygons_overlap(polygon,surface_polygon,1e-8) for _,surface_polygon,surface_elevation,_ in surfaces)
    record={
        "opening_id":opening_id,
        "source_id":source_id,
        "surface":surface,
        "elevation_m":report_number(elevation),
        "applied":applied,
        "polygon_xz_m":[[report_number(point[0]),report_number(point[1])] for point in polygon],
        "area_m2":report_number(polygon_area(polygon)),
        "bounds_m":{
            "min_x":report_number(minimum[0]),"max_x":report_number(maximum[0]),"min_z":report_number(minimum[1]),"max_z":report_number(maximum[1]),
            "width_x":report_number(maximum[0]-minimum[0]),"length_z":report_number(maximum[1]-minimum[1]),
        },
        "local_center_m":[report_number(bounds_center[0]),report_number(elevation),report_number(bounds_center[1])],
        "centroid_m":[report_number(centroid[0]),report_number(elevation),report_number(centroid[1])],
        "contained_markers":[{"source_id":marker.source_id,"type":marker.kind} for marker in contained],
        "is_stair_shaft_candidate":any(marker.kind=="stairs" for marker in contained),
    }
    if space_id is not None: record["space_id"]=space_id
    return record


def offset_polygon_outward(points: Sequence[tuple[float,float]], distance: float) -> list[tuple[float,float]]:
    """Offset a simple polygon outward with mitered corners."""
    if distance<=1e-12:
        return list(points)
    if len(points)<3 or abs(signed_polygon_area(points))<=1e-12:
        raise ConversionError("Cannot offset a degenerate floor polygon")
    ccw=signed_polygon_area(points)>0
    result=[]
    for index,current in enumerate(points):
        previous=points[index-1]; following=points[(index+1)%len(points)]
        first=(current[0]-previous[0],current[1]-previous[1]); second=(following[0]-current[0],following[1]-current[1])
        first_length=math.hypot(*first); second_length=math.hypot(*second)
        if first_length<=1e-12 or second_length<=1e-12:
            raise ConversionError("Cannot offset a polygon with duplicate adjacent vertices")
        first=(first[0]/first_length,first[1]/first_length); second=(second[0]/second_length,second[1]/second_length)
        if ccw:
            first_normal=(first[1],-first[0]); second_normal=(second[1],-second[0])
        else:
            first_normal=(-first[1],first[0]); second_normal=(-second[1],second[0])
        first_point=(current[0]+first_normal[0]*distance,current[1]+first_normal[1]*distance)
        second_point=(current[0]+second_normal[0]*distance,current[1]+second_normal[1]*distance)
        denominator=first[0]*second[1]-first[1]*second[0]
        if abs(denominator)<=1e-10:
            average=(first_normal[0]+second_normal[0],first_normal[1]+second_normal[1])
            average_length=math.hypot(*average)
            if average_length<=1e-12:
                raise ConversionError("Cannot offset a 180-degree floor corner")
            result.append((current[0]+average[0]/average_length*distance,current[1]+average[1]/average_length*distance))
            continue
        delta=(second_point[0]-first_point[0],second_point[1]-first_point[1])
        parameter=(delta[0]*second[1]-delta[1]*second[0])/denominator
        intersection=(first_point[0]+first[0]*parameter,first_point[1]+first[1]*parameter)
        if math.dist(current,intersection)>distance*20:
            raise ConversionError("Floor edge offset produces an excessive miter; use centerline extent or a smaller offset")
        result.append(intersection)
    return result


def polygons_equivalent(first: Sequence[tuple[float,float]], second: Sequence[tuple[float,float]], tolerance: float = 1e-7) -> bool:
    if len(first)!=len(second):
        return False
    for candidate in (list(second),list(reversed(second))):
        for shift in range(len(candidate)):
            if all(math.dist(first[index],candidate[(index+shift)%len(candidate)])<=tolerance for index in range(len(first))):
                return True
    return False


def point_in_polygon(point: tuple[float,float], polygon: Sequence[tuple[float,float]]) -> bool:
    x,z=point; inside=False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if ((a[1]>z)!=(b[1]>z)) and x < (b[0]-a[0])*(z-a[1])/(b[1]-a[1])+a[0]: inside=not inside
    return inside


def polygons_overlap(first: Sequence[tuple[float,float]], second: Sequence[tuple[float,float]], tolerance: float) -> bool:
    if any(point_in_polygon(point,second) for point in first) or any(point_in_polygon(point,first) for point in second): return True
    for a,b in zip(first,first[1:]+first[:1]):
        rx,rz=b[0]-a[0],b[1]-a[1]
        for c,d in zip(second,second[1:]+second[:1]):
            sx,sz=d[0]-c[0],d[1]-c[1]; denominator=rx*sz-rz*sx
            if abs(denominator)<=tolerance: continue
            qx,qz=c[0]-a[0],c[1]-a[1]
            t=(qx*sz-qz*sx)/denominator; u=(qx*rz-qz*rx)/denominator
            if -tolerance<=t<=1+tolerance and -tolerance<=u<=1+tolerance: return True
    return False


def point_segment_distance(point: tuple[float,float], first: tuple[float,float], second: tuple[float,float]) -> float:
    dx,dz=second[0]-first[0],second[1]-first[1]
    length_squared=dx*dx+dz*dz
    if length_squared<=1e-18:
        return math.dist(point,first)
    parameter=max(0.0,min(1.0,((point[0]-first[0])*dx+(point[1]-first[1])*dz)/length_squared))
    projection=(first[0]+dx*parameter,first[1]+dz*parameter)
    return math.dist(point,projection)


def segments_touch(first_a: tuple[float,float], first_b: tuple[float,float], second_a: tuple[float,float], second_b: tuple[float,float], tolerance: float) -> bool:
    if min(
        point_segment_distance(first_a,second_a,second_b),point_segment_distance(first_b,second_a,second_b),
        point_segment_distance(second_a,first_a,first_b),point_segment_distance(second_b,first_a,first_b),
    )<=tolerance:
        return True
    rx,rz=first_b[0]-first_a[0],first_b[1]-first_a[1]
    sx,sz=second_b[0]-second_a[0],second_b[1]-second_a[1]
    denominator=rx*sz-rz*sx
    if abs(denominator)<=1e-12:
        return False
    qx,qz=second_a[0]-first_a[0],second_a[1]-first_a[1]
    first_parameter=(qx*sz-qz*sx)/denominator
    second_parameter=(qx*rz-qz*rx)/denominator
    return -tolerance<=first_parameter<=1+tolerance and -tolerance<=second_parameter<=1+tolerance


def wall_touches_polygon(wall: Segment, polygon: Sequence[tuple[float,float]], tolerance: float) -> bool:
    midpoint=((wall.a[0]+wall.b[0])/2,(wall.a[1]+wall.b[1])/2)
    for point in (wall.a,wall.b,midpoint):
        if point_in_polygon(point,polygon):
            return True
        if any(point_segment_distance(point,first,second)<=tolerance for first,second in zip(polygon,polygon[1:]+polygon[:1])):
            return True
    return any(segments_touch(wall.a,wall.b,first,second,tolerance) for first,second in zip(polygon,polygon[1:]+polygon[:1]))


def detect_ceiling_wall_alignment(
    ceilings: Sequence[tuple[str,list[tuple[float,float]],float,float]],
    walls: Sequence[Segment],
    tolerance: float,
) -> list[dict[str,Any]]:
    """Compare each ceiling underside with the nearest top of walls it covers or touches."""
    issues=[]
    for source_id,polygon,underside,_ in ceilings:
        candidates=[wall for wall in walls if wall_touches_polygon(wall,polygon,max(tolerance,1e-7))]
        if not candidates:
            continue
        top_groups: dict[float,set[str]]={}
        for wall in candidates:
            top=report_number(wall.elevation+wall.height)
            top_groups.setdefault(top,set()).update(value for value in wall.source_id.split("|") if value)
        minimum_gap=min(abs(underside-top) for top in top_groups)
        nearest=sorted(top for top in top_groups if abs(abs(underside-top)-minimum_gap)<=1e-9)
        if minimum_gap<=tolerance:
            continue
        nearest_sources=sorted({source for top in nearest for source in top_groups[top]})
        record={
            "ceiling_source_id":source_id,
            "ceiling_underside_elevation_m":report_number(underside),
            "nearest_wall_top_elevations_m":[report_number(top) for top in nearest],
            "vertical_offsets_m":[report_number(underside-top) for top in nearest],
            "absolute_gap_m":report_number(minimum_gap),
            "tolerance_m":report_number(tolerance),
            "wall_source_ids":nearest_sources,
        }
        issues.append(record)
    return issues


def explicit_ceiling_elevation_warnings(items: Sequence[RawItem], settings: Settings, overrides: dict[str,dict[str,Any]]) -> list[str]:
    warnings=[]
    for item in items:
        if item.kind!="ceiling" or optional_value_for(item,("ceiling_elevation",),overrides) is not None:
            continue
        item_overrides=overrides.get(item.element_id,{})
        base_sources=[]
        if "default_elevation" in item_overrides: base_sources.append("default_elevation override")
        if "data-default-elevation" in item.attrs: base_sources.append("data-default-elevation")
        if "data-elevation" in item.attrs: base_sources.append("data-elevation")
        if not base_sources:
            continue
        base=value_for(item,"default_elevation",settings,overrides)
        height=value_for(item,"ceiling_height",settings,overrides)
        warnings.append(
            f'{item.element_id}: explicit ceiling defines {", ".join(base_sources)} but no ceiling_elevation; '
            f'default_elevation is a base level, so the underside is computed as {base:.6g} + {height:.6g} = {base+height:.6g} m. '
            'Use data-ceiling-elevation or a ceiling_elevation override for an absolute underside elevation'
        )
    return warnings


def normalize_floor_polygons(
    floors: Sequence[tuple[str,list[tuple[float,float]],float,float]],
    floor_openings: Sequence[tuple[str,list[tuple[float,float]],float]],
    splitters: Sequence[Segment] = (),
    tolerance: float = 1e-8,
) -> tuple[list[tuple[str,list[tuple[float,float]],float,float]],dict[str,float|int]]:
    """Union coplanar floors and subtract closed openings into non-overlapping cells."""
    groups: list[dict[str,Any]]=[]
    for floor in floors:
        selected=None
        for group in groups:
            if abs(floor[2]-group["elevation"])<=tolerance and abs(floor[3]-group["thickness"])<=tolerance:
                selected=group; break
        if selected is None:
            selected={"elevation":floor[2],"thickness":floor[3],"floors":[]}; groups.append(selected)
        selected["floors"].append(floor)

    normalized: list[tuple[str,list[tuple[float,float]],float,float]]=[]; applied_openings=set()
    source_area=sum(polygon_area(floor[1]) for floor in floors)
    for group in groups:
        group_floors=group["floors"]
        group_openings=[opening for opening in floor_openings if abs(opening[2]-group["elevation"])<=tolerance]
        group_splitters=[splitter for splitter in splitters if splitter.elevation-1e-7<=group["elevation"]<=splitter.elevation+splitter.height+1e-7]
        for opening_id,opening_polygon,_ in group_openings:
            if any(polygons_overlap(opening_polygon,floor[1],tolerance) for floor in group_floors): applied_openings.add(opening_id)
        if len(group_floors)==1 and not group_openings and not group_splitters:
            normalized.append(group_floors[0]); continue
        edges=[]; events=set()
        all_polygons=[(source_id,polygon,"floor") for source_id,polygon,_,_ in group_floors]+[(source_id,polygon,"opening") for source_id,polygon,_ in group_openings]
        for source_id,polygon,role in all_polygons:
            events.update(point[0] for point in polygon)
            for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                if math.dist(a,b)>tolerance: edges.append((a,b,source_id,role))
        for splitter_index,splitter in enumerate(group_splitters):
            if math.dist(splitter.a,splitter.b)>tolerance:
                edges.append((splitter.a,splitter.b,f"__splitter_{splitter_index}","splitter")); events.update((splitter.a[0],splitter.b[0]))
        for index,(a,b,_,_) in enumerate(edges):
            rx,rz=b[0]-a[0],b[1]-a[1]
            for c,d,_,_ in edges[index+1:]:
                sx,sz=d[0]-c[0],d[1]-c[1]; denominator=rx*sz-rz*sx
                if abs(denominator)<=tolerance: continue
                qx,qz=c[0]-a[0],c[1]-a[1]
                t=(qx*sz-qz*sx)/denominator; u=(qx*rz-qz*rx)/denominator
                if -tolerance<=t<=1+tolerance and -tolerance<=u<=1+tolerance:
                    events.add(a[0]+rx*max(0.0,min(1.0,t)))
        event_x=sorted(events); cells=[]
        def edge_z(edge: tuple[tuple[float,float],tuple[float,float],str,str], x: float) -> float:
            a,b,_,_=edge
            return a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])
        def intervals_for(polygons: Sequence[tuple[str,list[tuple[float,float]],str]], middle: float) -> list[list[Any]]:
            intervals=[]
            for source_index,(source_id,_,role) in enumerate(polygons):
                crossings=[]
                for edge in edges:
                    a,b,edge_source,edge_role=edge
                    if edge_source!=source_id or edge_role!=role or abs(b[0]-a[0])<=tolerance: continue
                    if min(a[0],b[0])<middle<max(a[0],b[0]): crossings.append((edge_z(edge,middle),edge))
                crossings.sort(key=lambda item:item[0])
                for low,high in zip(crossings[::2],crossings[1::2]): intervals.append([low[0],high[0],low[1],high[1],{source_id},source_index])
            if polygons and polygons[0][2]=="floor":
                events=sorted({value for interval in intervals for value in interval[:2]}); owned=[]
                for low,high in zip(events,events[1:]):
                    if high-low<=tolerance: continue
                    middle_z=(low+high)/2; active=[interval for interval in intervals if interval[0]<middle_z<interval[1]]
                    if not active: continue
                    owner=min(active,key=lambda interval:interval[5]); boundary_edges=[(abs(interval[0]-low),interval[2]) for interval in intervals]+[(abs(interval[1]-low),interval[3]) for interval in intervals]
                    low_edge=min(boundary_edges,key=lambda item:item[0])[1]; boundary_edges=[(abs(interval[0]-high),interval[2]) for interval in intervals]+[(abs(interval[1]-high),interval[3]) for interval in intervals]
                    high_edge=min(boundary_edges,key=lambda item:item[0])[1]
                    if owned and owned[-1][4]==owner[4] and abs(owned[-1][1]-low)<=tolerance:
                        owned[-1][1]=high; owned[-1][3]=high_edge
                    else:
                        owned.append([low,high,low_edge,high_edge,set(owner[4])])
                return owned
            intervals.sort(key=lambda item:item[0]); unions=[]
            for interval in intervals:
                if not unions or interval[0]>unions[-1][1]+tolerance: unions.append(interval[:5])
                else:
                    current=unions[-1]; current[4].update(interval[4])
                    if interval[1]>current[1]: current[1],current[3]=interval[1],interval[3]
            return unions
        for left,right in zip(event_x,event_x[1:]):
            if right-left<=tolerance: continue
            middle=(left+right)/2
            floor_unions=intervals_for([(source_id,polygon,"floor") for source_id,polygon,_,_ in group_floors],middle)
            opening_unions=intervals_for([(source_id,polygon,"opening") for source_id,polygon,_ in group_openings],middle)
            remaining=[]
            for low,high,low_edge,high_edge,source_ids in floor_unions:
                parts=[[low,high,low_edge,high_edge,set(source_ids)]]
                for cut_low,cut_high,cut_low_edge,cut_high_edge,_ in opening_unions:
                    next_parts=[]
                    for part_low,part_high,part_low_edge,part_high_edge,part_sources in parts:
                        if cut_high<=part_low+tolerance or cut_low>=part_high-tolerance: next_parts.append([part_low,part_high,part_low_edge,part_high_edge,part_sources]); continue
                        if cut_low>part_low+tolerance: next_parts.append([part_low,min(cut_low,part_high),part_low_edge,cut_low_edge,set(part_sources)])
                        if cut_high<part_high-tolerance: next_parts.append([max(cut_high,part_low),part_high,cut_high_edge,part_high_edge,set(part_sources)])
                    parts=next_parts
                remaining.extend(parts)
            splitter_crossings=[]
            for edge in edges:
                a,b,_,role=edge
                if role!="splitter" or abs(b[0]-a[0])<=tolerance: continue
                if min(a[0],b[0])<middle<max(a[0],b[0]): splitter_crossings.append((edge_z(edge,middle),edge))
            if splitter_crossings:
                split_remaining=[]
                for low,high,low_edge,high_edge,source_ids in remaining:
                    parts=[[low,high,low_edge,high_edge,set(source_ids)]]
                    for cut,splitter_edge in sorted(splitter_crossings,key=lambda item:item[0]):
                        next_parts=[]
                        for part_low,part_high,part_low_edge,part_high_edge,part_sources in parts:
                            if part_low+tolerance<cut<part_high-tolerance:
                                next_parts.append([part_low,cut,part_low_edge,splitter_edge,set(part_sources)])
                                next_parts.append([cut,part_high,splitter_edge,part_high_edge,set(part_sources)])
                            else: next_parts.append([part_low,part_high,part_low_edge,part_high_edge,part_sources])
                        parts=next_parts
                    split_remaining.extend(parts)
                remaining=split_remaining
            for _,_,low_edge,high_edge,source_ids in remaining:
                cells.append({
                    "x0":left,"x1":right,
                    "low0":edge_z(low_edge,left),"low1":edge_z(low_edge,right),
                    "high0":edge_z(high_edge,left),"high1":edge_z(high_edge,right),
                    "source_ids":set(source_ids),
                })
        merged_cells=[]
        for cell in cells:
            joined=False
            for previous in reversed(merged_cells):
                if abs(previous["x1"]-cell["x0"])>tolerance: continue
                seam_matches=abs(previous["low1"]-cell["low0"])<=tolerance and abs(previous["high1"]-cell["high0"])<=tolerance
                low_collinear=abs((previous["x1"]-previous["x0"])*(cell["low1"]-cell["low0"])-(previous["low1"]-previous["low0"])*(cell["x1"]-cell["x0"]))<=tolerance
                high_collinear=abs((previous["x1"]-previous["x0"])*(cell["high1"]-cell["high0"])-(previous["high1"]-previous["high0"])*(cell["x1"]-cell["x0"]))<=tolerance
                seam_midpoint=(cell["x0"],(cell["low0"]+cell["high0"])/2)
                seam_blocked=any(point_segment_distance(seam_midpoint,splitter.a,splitter.b)<=max(tolerance,splitter.thickness/2) for splitter in group_splitters)
                if seam_matches and low_collinear and high_collinear and previous["source_ids"]==cell["source_ids"] and not seam_blocked:
                    previous["x1"]=cell["x1"]; previous["low1"]=cell["low1"]; previous["high1"]=cell["high1"]
                    joined=True; break
            if not joined: merged_cells.append(cell)
        for cell in merged_cells:
            polygon=[(cell["x0"],cell["low0"]),(cell["x1"],cell["low1"]),(cell["x1"],cell["high1"]),(cell["x0"],cell["high0"])]
            clean=[]
            for point in polygon:
                if not clean or math.dist(point,clean[-1])>tolerance: clean.append(point)
            if len(clean)>2 and math.dist(clean[0],clean[-1])<=tolerance: clean.pop()
            if len(clean)>2 and polygon_area(clean)>tolerance:
                normalized.append(("|".join(sorted(cell["source_ids"])),clean,group["elevation"],group["thickness"]))
    normalized_area=sum(polygon_area(floor[1]) for floor in normalized)
    # Re-run without cutters to distinguish duplicate cleanup from intentional holes.
    if floor_openings:
        base_normalized,base_stats=normalize_floor_polygons(floors,[],splitters,tolerance)
        base_area=sum(polygon_area(floor[1]) for floor in base_normalized)
    else: base_area=normalized_area
    return normalized,{
        "before":len(floors),"after":len(normalized),
        "source_area":source_area,"normalized_area":normalized_area,
        "removed_overlap_area":max(0.0,source_area-base_area),
        "floor_openings_requested":len(floor_openings),"floor_openings_applied":len(applied_openings),
        "removed_opening_area":max(0.0,base_area-normalized_area),
    }


def group_normalized_surfaces(
    cells: Sequence[tuple[str,list[tuple[float,float]],float,float]],
    source_order: Sequence[str],
    splitters: Sequence[Segment] = (),
    tolerance: float = 1e-7,
) -> list[tuple[str,list[list[tuple[float,float]]],float,float]]:
    """Collect cells into room components, with wall centerlines as boundaries."""
    priority={source_id:index for index,source_id in enumerate(dict.fromkeys(source_order))}
    groups: dict[tuple[str,float,float],list[list[tuple[float,float]]]]={}
    for source_ids,polygon,elevation,thickness in cells:
        candidates=source_ids.split("|")
        owner=min(candidates,key=lambda source_id:(priority.get(source_id,len(priority)),source_id))
        groups.setdefault((owner,elevation,thickness),[]).append(polygon)
    def shared_boundary(first:list[tuple[float,float]],second:list[tuple[float,float]]) -> tuple[tuple[float,float],tuple[float,float]]|None:
        for a,b in zip(first,first[1:]+first[:1]):
            dx,dz=b[0]-a[0],b[1]-a[1]; length=math.hypot(dx,dz)
            if length<=tolerance: continue
            ux,uz=dx/length,dz/length
            for c,d in zip(second,second[1:]+second[:1]):
                if abs((c[0]-a[0])*uz-(c[1]-a[1])*ux)>tolerance or abs((d[0]-a[0])*uz-(d[1]-a[1])*ux)>tolerance: continue
                tc=(c[0]-a[0])*ux+(c[1]-a[1])*uz; td=(d[0]-a[0])*ux+(d[1]-a[1])*uz
                low=max(0.0,min(tc,td)); high=min(length,max(tc,td))
                if high-low>tolerance:
                    return ((a[0]+ux*low,a[1]+uz*low),(a[0]+ux*high,a[1]+uz*high))
        return None
    result=[]
    for (owner,elevation,thickness),polygons in groups.items():
        parents=list(range(len(polygons)))
        def find(index:int)->int:
            while parents[index]!=index:
                parents[index]=parents[parents[index]]; index=parents[index]
            return index
        def union(first:int,second:int)->None:
            first=find(first); second=find(second)
            if first!=second: parents[second]=first
        relevant=[splitter for splitter in splitters if splitter.elevation-tolerance<=elevation<=splitter.elevation+splitter.height+tolerance]
        for first_index,first in enumerate(polygons):
            for second_index in range(first_index+1,len(polygons)):
                boundary=shared_boundary(first,polygons[second_index])
                if boundary is None: continue
                midpoint=((boundary[0][0]+boundary[1][0])/2,(boundary[0][1]+boundary[1][1])/2)
                blocked=any(point_segment_distance(midpoint,splitter.a,splitter.b)<=max(tolerance,splitter.thickness/2) for splitter in relevant)
                if not blocked: union(first_index,second_index)
        components: dict[int,list[list[tuple[float,float]]]]={}
        for index,polygon in enumerate(polygons): components.setdefault(find(index),[]).append(polygon)
        result.extend((owner,component,elevation,thickness) for component in components.values())
    return result


def ear_clip(points: Sequence[tuple[float,float]]) -> list[tuple[int,int,int]]:
    if len(points) < 3: return []
    area=sum(points[i][0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points)))
    indices=list(range(len(points))) if area>0 else list(reversed(range(len(points)))); triangles=[]
    def cross(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def inside(p,a,b,c): return cross(a,b,p)>=-1e-9 and cross(b,c,p)>=-1e-9 and cross(c,a,p)>=-1e-9
    guard=0
    while len(indices)>3 and guard<len(points)*len(points):
        guard+=1; clipped=False
        for pos in range(len(indices)):
            i0,i1,i2=indices[pos-1],indices[pos],indices[(pos+1)%len(indices)]
            if cross(points[i0],points[i1],points[i2])<=1e-9: continue
            if any(inside(points[j],points[i0],points[i1],points[i2]) for j in indices if j not in {i0,i1,i2}): continue
            triangles.append((i0,i1,i2)); del indices[pos]; clipped=True; break
        if not clipped: raise ConversionError("Polygon cannot be triangulated; check self-intersections and duplicate vertices")
    if len(indices)==3: triangles.append(tuple(indices))  # type: ignore[arg-type]
    return triangles


def safe_name(value: str, fallback: str = "Element") -> str:
    cleaned=re.sub(r"[^A-Za-z0-9_]+","_",value).strip("_") or fallback
    return "N_"+cleaned if cleaned[0].isdigit() else cleaned


class ObjWriter:
    def __init__(self) -> None:
        self.lines=["# Generated by svg_to_godot3d", "mtllib geometry.mtl", "s off"]; self.vertex_count=0; self.normal_count=0
    def add_mesh(self,name:str,material:str,vertices:list[tuple[float,float,float]],faces:list[tuple[int,int,int]]) -> None:
        self.lines += [f"o {safe_name(name)}",f"usemtl {material}"]
        self.lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x,y,z in vertices]
        base=self.vertex_count; normal_base=self.normal_count
        normals=[]
        for a,b,c in faces:
            first=vertices[a]; second=vertices[b]; third=vertices[c]
            ab=(second[0]-first[0],second[1]-first[1],second[2]-first[2]); ac=(third[0]-first[0],third[1]-first[1],third[2]-first[2])
            normal=(ab[1]*ac[2]-ab[2]*ac[1],ab[2]*ac[0]-ab[0]*ac[2],ab[0]*ac[1]-ab[1]*ac[0]); length=math.sqrt(sum(value*value for value in normal))
            normals.append((normal[0]/length,normal[1]/length,normal[2]/length) if length>1e-12 else (0.0,1.0,0.0))
        self.lines += [f"vn {x:.6f} {y:.6f} {z:.6f}" for x,y,z in normals]
        self.lines += [f"f {base+a+1}//{normal_base+index+1} {base+b+1}//{normal_base+index+1} {base+c+1}//{normal_base+index+1}" for index,(a,b,c) in enumerate(faces)]
        self.vertex_count+=len(vertices); self.normal_count+=len(normals)
    def add_box(self,box:Box) -> None:
        sx,sy,sz=box.size; cx,cy,cz=box.center; co=math.cos(box.angle_y); si=math.sin(box.angle_y); vertices=[]
        for x,y,z in [(-sx/2,-sy/2,-sz/2),(sx/2,-sy/2,-sz/2),(sx/2,sy/2,-sz/2),(-sx/2,sy/2,-sz/2),(-sx/2,-sy/2,sz/2),(sx/2,-sy/2,sz/2),(sx/2,sy/2,sz/2),(-sx/2,sy/2,sz/2)]:
            vertices.append((cx+x*co+z*si,cy+y,cz-x*si+z*co))
        faces=[(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(3,7,6),(3,6,2),(0,4,7),(0,7,3),(1,2,6),(1,6,5)]
        self.add_mesh(box.name,box.material,vertices,faces)
    def add_prism(self,name:str,material:str,polygon:list[tuple[float,float]],bottom:float,top:float,collision_face:str="top") -> list[tuple[float,float,float]]:
        triangles=ear_clip(polygon); n=len(polygon); vertices=[(x,bottom,z) for x,z in polygon]+[(x,top,z) for x,z in polygon]; faces=[]
        upward_triangles=[(a,c,b) for a,b,c in triangles]
        for a,b,c in triangles: faces.append((a,b,c))
        for a,b,c in upward_triangles: faces.append((a+n,b+n,c+n))
        for i in range(n):
            j=(i+1)%n; faces += [(i,j+n,j),(i,i+n,j+n)]
        self.add_mesh(name,material,vertices,faces)
        collision_triangles=upward_triangles if collision_face=="top" else triangles
        collision_y=top if collision_face=="top" else bottom
        return [(polygon[i][0],collision_y,polygon[i][1]) for tri in collision_triangles for i in tri]
    def add_surface(self,name:str,material:str,polygon:list[tuple[float,float]],elevation:float,face:str="top") -> list[tuple[float,float,float]]:
        triangles=ear_clip(polygon); vertices=[(x,elevation,z) for x,z in polygon]
        upward_triangles=[(a,c,b) for a,b,c in triangles]
        oriented=upward_triangles if face=="top" else triangles
        self.add_mesh(name,material,vertices,oriented)
        return [(polygon[i][0],elevation,polygon[i][1]) for tri in oriented for i in tri]
    def add_cell_slab(self,name:str,material:str,polygons:Sequence[list[tuple[float,float]]],bottom:float,top:float,collision_face:str="top") -> list[tuple[float,float,float]]:
        vertices=[]; faces=[]; collision=[]; raw_edges=[]
        for raw_polygon in polygons:
            polygon=list(raw_polygon) if signed_polygon_area(raw_polygon)>0 else list(reversed(raw_polygon))
            triangles=ear_clip(polygon); upward=[(a,c,b) for a,b,c in triangles]; base=len(vertices); count=len(polygon)
            vertices.extend((x,bottom,z) for x,z in polygon); vertices.extend((x,top,z) for x,z in polygon)
            faces.extend((base+a,base+b,base+c) for a,b,c in triangles)
            faces.extend((base+a+count,base+b+count,base+c+count) for a,b,c in upward)
            selected=upward if collision_face=="top" else triangles; collision_y=top if collision_face=="top" else bottom
            collision.extend((polygon[i][0],collision_y,polygon[i][1]) for tri in selected for i in tri)
            for index in range(count):
                following=(index+1)%count; first=polygon[index]; second=polygon[following]
                raw_edges.append((first,second))
        endpoints=[point for edge in raw_edges for point in edge]; boundary_edges: dict[tuple[tuple[float,float],tuple[float,float]],list[tuple[tuple[float,float],tuple[float,float]]]]={}
        for first,second in raw_edges:
            dx,dz=second[0]-first[0],second[1]-first[1]; length_squared=dx*dx+dz*dz; points=[first,second]
            for point in endpoints:
                if point_segment_distance(point,first,second)<=1e-8:
                    parameter=((point[0]-first[0])*dx+(point[1]-first[1])*dz)/length_squared
                    if 1e-8<parameter<1-1e-8: points.append(point)
            points=sorted(points,key=lambda point:((point[0]-first[0])*dx+(point[1]-first[1])*dz)/length_squared)
            for start,end in zip(points,points[1:]):
                key=tuple(sorted(((round(start[0],8),round(start[1],8)),(round(end[0],8),round(end[1],8)))))
                boundary_edges.setdefault(key,[]).append((start,end))
        for occurrences in boundary_edges.values():
            if len(occurrences)!=1: continue
            first,second=occurrences[0]; base=len(vertices)
            vertices.extend(((first[0],bottom,first[1]),(second[0],bottom,second[1]),(second[0],top,second[1]),(first[0],top,first[1])))
            faces.extend(((base,base+2,base+1),(base,base+3,base+2)))
        self.add_mesh(name,material,vertices,faces)
        return collision
    def add_cell_surface(self,name:str,material:str,polygons:Sequence[list[tuple[float,float]]],elevation:float,face:str="top") -> list[tuple[float,float,float]]:
        vertices=[]; faces=[]; collision=[]
        for polygon in polygons:
            triangles=ear_clip(polygon); oriented=[(a,c,b) for a,b,c in triangles] if face=="top" else triangles; base=len(vertices)
            vertices.extend((x,elevation,z) for x,z in polygon); faces.extend((base+a,base+b,base+c) for a,b,c in oriented)
            collision.extend((polygon[i][0],elevation,polygon[i][1]) for tri in oriented for i in tri)
        self.add_mesh(name,material,vertices,faces)
        return collision


def godot_vector(values: Sequence[float]) -> str:
    return "Vector3("+", ".join(f"{v:.6g}" for v in values)+")"


def godot_string(value: str) -> str:
    return json.dumps(value,ensure_ascii=False)


def ensure_material_resource(path: Path, color: tuple[float,float,float,float]) -> bool:
    """Create an editable material once; preserve user changes on later conversions."""
    if path.exists(): return False
    path.write_text(
        '[gd_resource type="StandardMaterial3D" format=3]\n\n'
        '[resource]\n'
        f'albedo_color = Color({color[0]:.6g}, {color[1]:.6g}, {color[2]:.6g}, {color[3]:.6g})\n'
        'roughness = 0.8\n',
        encoding="utf-8",
    )
    return True


GENERATED_PART_PATTERN=re.compile(r"^(?:floor|ceiling|wall|obstacle)_\d{3}_[^\\/]+\.obj$",re.IGNORECASE)


def remove_previous_generated_parts(output_dir: Path) -> tuple[list[str],str|None]:
    """Delete only numbered part OBJs explicitly listed by the previous report."""
    report_path=output_dir/"conversion_report.json"
    if not report_path.is_file():
        return [],None
    try:
        previous=json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        return [],f"Skipped stale generated-part cleanup because the previous conversion report could not be read: {exc}"
    previous_files=previous.get("files",[])
    if not isinstance(previous_files,list):
        return [],"Skipped stale generated-part cleanup because the previous conversion report has no valid files list"
    removed=[]
    for filename in previous_files:
        if not isinstance(filename,str) or Path(filename).name!=filename or not GENERATED_PART_PATTERN.fullmatch(filename):
            continue
        target=output_dir/filename
        if target.is_file():
            target.unlink()
            removed.append(filename)
    return sorted(removed),None


def find_project_root(output_dir: Path, explicit: str | None) -> Path | None:
    if explicit: return Path(explicit).resolve()
    for candidate in [output_dir.resolve(),*output_dir.resolve().parents]:
        if (candidate/"project.godot").exists(): return candidate
    return None


def make_resource_path(output_dir: Path, filename: str, project_root: Path | None, explicit_dir: str | None) -> str:
    if explicit_dir: return explicit_dir.rstrip("/")+"/"+filename
    if project_root:
        try: return "res://"+(output_dir.resolve()/filename).relative_to(project_root).as_posix()
        except ValueError: raise ConversionError("Output directory is outside --project-root; supply --resource-dir explicitly")
    raise ConversionError("Cannot infer res:// path. Put output inside a Godot project, use --project-root, or use --resource-dir")


def build_semantic_options(profile: str, config_path: str | None, map_classes: Sequence[str] | None, set_values: Sequence[str] | None) -> tuple[dict[str,Any],dict[str,str],dict[str,dict[str,Any]]]:
    config: dict[str,Any]={}
    if config_path: config=json.loads(Path(config_path).read_text(encoding="utf-8"))
    class_map=dict(CODEX_PLAN_CLASS_MAP if profile=="codex-plan" else GENERIC_CLASS_MAP)
    class_map.update(config.get("class_map",{}))
    for raw in map_classes or []:
        key,value=parse_assignment(raw); class_map[key]=value
    overrides={key:dict(value) for key,value in config.get("element_overrides",{}).items()}
    for raw in set_values or []:
        left,value=parse_assignment(raw)
        if "." not in left: raise ConversionError(f"Expected ELEMENT.KEY=VALUE, got: {raw}")
        element,key=left.split(".",1); overrides.setdefault(element,{})[key]=coerce(value)
    return config,class_map,overrides


def build_settings(options: argparse.Namespace | None, defaults: dict[str,Any], strict: bool = False) -> Settings:
    value=lambda name,default: getattr(options,name,None) if options is not None and getattr(options,name,None) is not None else defaults.get(name,default)
    wall_height=float(value("wall_height",3.0))
    settings=Settings(
        scale=float(value("scale",0.05)),wall_height=wall_height,
        wall_thickness=float(value("wall_thickness",0.18)),floor_thickness=float(value("floor_thickness",0.12)),
        floor_anchor=value("floor_anchor","top"),floor_mesh=value("floor_mesh","slab"),floor_extent=value("floor_extent","wall-outer"),
        floor_edge_offset=float(value("floor_edge_offset",0.0)) if value("floor_edge_offset",None) is not None else None,
        generate_ceilings=not bool(getattr(options,"no_ceilings",False)) and bool(defaults.get("generate_ceilings",True)),
        ceiling_height=float(value("ceiling_height",wall_height)),ceiling_thickness=float(value("ceiling_thickness",0.12)),
        ceiling_mesh=value("ceiling_mesh","slab"),ceiling_extent=value("ceiling_extent","wall-outer"),
        ceiling_edge_offset=float(value("ceiling_edge_offset",0.0)) if value("ceiling_edge_offset",None) is not None else None,
        wall_embed=float(value("wall_embed",0.0)),wall_joint_mode=value("wall_joint_mode","butt"),wall_joint_gap=float(value("wall_joint_gap",0.0005)),
        obstacle_height=float(value("obstacle_height",1.2)),default_elevation=float(getattr(options,"elevation",None) if options is not None and getattr(options,"elevation",None) is not None else defaults.get("default_elevation",0.0)),
        opening_snap=float(value("opening_snap",0.10)),door_height=float(value("door_height",2.1)),door_bottom=float(value("door_bottom",0.0)),
        window_height=float(value("window_height",1.2)),window_bottom=float(value("window_bottom",1.0)),wall_merge_tolerance=float(value("wall_merge_tolerance",0.02)),
        strict_wall_overlaps=bool(getattr(options,"strict_wall_overlaps",False) or defaults.get("strict_wall_overlaps",False)),
        ceiling_alignment_tolerance=float(value("ceiling_alignment_tolerance",0.01)),
        strict_ceiling_alignment=bool(getattr(options,"strict_ceiling_alignment",False) or defaults.get("strict_ceiling_alignment",False)),
        origin=value("origin","center"),invert_z=bool(getattr(options,"invert_z",False) or defaults.get("invert_z",False)),
        collision_layer=int(value("collision_layer",1)),include_preview=bool(getattr(options,"include_preview",False) or defaults.get("include_preview",False)),strict=strict,
    )
    if settings.ceiling_alignment_tolerance<0:
        raise ConversionError("--ceiling-alignment-tolerance must be zero or greater")
    return settings


def world_transform(viewbox: Sequence[float], settings: Settings):
    min_x,min_y,width,height=viewbox
    if settings.origin=="center": offset=(min_x+width/2,min_y+height/2)
    elif settings.origin=="top-left": offset=(min_x,min_y)
    elif settings.origin=="none": offset=(0.0,0.0)
    else: raise ConversionError("--origin must be center, top-left, or none")
    def world(point:tuple[float,float])->tuple[float,float]:
        x=(point[0]-offset[0])*settings.scale; z=(point[1]-offset[1])*settings.scale
        return (x,-z if settings.invert_z else z)
    return world


def build_spatial_handoff(items: Sequence[RawItem], settings: Settings, overrides: dict[str,dict[str,Any]], viewbox: Sequence[float]) -> dict[str,Any]:
    """Build the exact shared spatial contract without generating scene files."""
    world=world_transform(viewbox,settings)
    floors=[]; ceilings=[]; walls=[]; wall_openings=[]; markers=[]
    pending_floor=[]; pending_ceiling=[]; area_floor_boundaries=[]; area_ceiling_boundaries=[]
    opening_metadata={}
    for item in items:
        elevation=value_for(item,"default_elevation",settings,overrides)
        if item.kind in {"area","floor"}:
            original=[world(point) for point in polygon_of(item)]; floor_polygon=original
            explicit_floor_offset=optional_value_for(item,("floor_edge_offset",),overrides)
            if item.kind=="area":
                thickness=value_for(item,"wall_thickness",settings,overrides)
                floor_offset=explicit_floor_offset if explicit_floor_offset is not None else (settings.floor_edge_offset if settings.floor_edge_offset is not None else thickness/2)
                if settings.floor_extent=="wall-outer" and floor_offset>0: floor_polygon=offset_polygon_outward(original,floor_offset)
                else: floor_offset=0.0
                area_floor_boundaries.append((original,floor_offset)); walls.extend(segments_of(item,settings,overrides))
                if settings.generate_ceilings:
                    explicit_ceiling_offset=optional_value_for(item,("ceiling_edge_offset",),overrides)
                    ceiling_offset=explicit_ceiling_offset if explicit_ceiling_offset is not None else (settings.ceiling_edge_offset if settings.ceiling_edge_offset is not None else thickness/2)
                    ceiling_polygon=offset_polygon_outward(original,ceiling_offset) if settings.ceiling_extent=="wall-outer" and ceiling_offset>0 else original
                    if settings.ceiling_extent!="wall-outer": ceiling_offset=0.0
                    underside=elevation+value_for(item,"wall_height",settings,overrides)
                    ceilings.append((item.element_id,ceiling_polygon,underside,value_for(item,"ceiling_thickness",settings,overrides)))
                    area_ceiling_boundaries.append((original,ceiling_offset,underside))
            elif explicit_floor_offset is not None and explicit_floor_offset>0:
                floor_polygon=offset_polygon_outward(original,explicit_floor_offset)
            floors.append((item.element_id,floor_polygon,elevation,value_for(item,"floor_thickness",settings,overrides)))
            if flag_for(item,"floor_opening",overrides):
                opening_id=item.element_id+"_floor_opening"; pending_floor.append((opening_id,original,elevation,explicit_floor_offset))
                opening_metadata[("floor",opening_id)]=(item.element_id,text_value_for(item,"space_id",overrides))
            if flag_for(item,"ceiling_opening",overrides):
                opening_id=item.element_id+"_ceiling_opening"; pending_ceiling.append((opening_id,original,None,optional_value_for(item,("ceiling_edge_offset",),overrides)))
                opening_metadata[("ceiling",opening_id)]=(item.element_id,text_value_for(item,"space_id",overrides))
        elif item.kind=="floor-opening":
            polygon=[world(point) for point in polygon_of(item)]; pending_floor.append((item.element_id,polygon,elevation,optional_value_for(item,("floor_edge_offset",),overrides)))
            opening_metadata[("floor",item.element_id)]=(item.element_id,text_value_for(item,"space_id",overrides))
        elif item.kind=="ceiling":
            polygon=[world(point) for point in polygon_of(item)]; offset=optional_value_for(item,("ceiling_edge_offset",),overrides)
            if offset: polygon=offset_polygon_outward(polygon,offset)
            explicit=optional_value_for(item,("ceiling_elevation",),overrides); underside=explicit if explicit is not None else elevation+value_for(item,"ceiling_height",settings,overrides)
            ceilings.append((item.element_id,polygon,underside,value_for(item,"ceiling_thickness",settings,overrides)))
        elif item.kind=="ceiling-opening":
            polygon=[world(point) for point in polygon_of(item)]; pending_ceiling.append((item.element_id,polygon,optional_value_for(item,("ceiling_elevation",),overrides),optional_value_for(item,("ceiling_edge_offset",),overrides)))
            opening_metadata[("ceiling",item.element_id)]=(item.element_id,text_value_for(item,"space_id",overrides))
        elif item.kind=="wall": walls.extend(segments_of(item,settings,overrides))
        elif item.kind in {"opening","door","window"}:
            bottom,height=opening_dimensions(item,settings,overrides)
            wall_openings.extend(Opening(world(segment.a),world(segment.b),item.element_id,item.kind,elevation+bottom,height) for segment in segments_of(item,settings,overrides))
            if item.kind=="opening":
                continue
            points=[world(point) for path in item.paths for point in path]
            if not points: continue
            x=sum(point[0] for point in points)/len(points); z=sum(point[1] for point in points)/len(points); metadata={}
            y=elevation+bottom+(height or 0.0)/2
            for key in ("space_id","anchor_id","anchor_role"):
                value=text_value_for(item,key,overrides)
                if value is not None: metadata[key]=value
            markers.append(Marker(item.kind.title()+"_"+safe_name(item.element_id),item.element_id,item.kind,(x,y,z),metadata))
        elif item.kind in {"stairs","marker"}:
            points=[world(point) for path in item.paths for point in path]
            if not points: continue
            x=sum(point[0] for point in points)/len(points); z=sum(point[1] for point in points)/len(points); metadata={}
            for key in ("space_id","anchor_id","anchor_role"):
                value=text_value_for(item,key,overrides)
                if value is not None: metadata[key]=value
            markers.append(Marker(item.kind.title()+"_"+safe_name(item.element_id),item.element_id,item.kind,(x,elevation,z),metadata))
    floor_openings=[]
    for opening_id,polygon,elevation,explicit_offset in pending_floor:
        offset=explicit_offset
        if offset is None: offset=next((candidate for boundary,candidate in area_floor_boundaries if polygons_equivalent(polygon,boundary)),0.0)
        floor_openings.append((opening_id,offset_polygon_outward(polygon,offset) if offset>0 else polygon,elevation))
    ceiling_openings=[]
    for opening_id,polygon,target,explicit_offset in pending_ceiling:
        elevations=[target] if target is not None else sorted({ceiling[2] for ceiling in ceilings if polygons_overlap(polygon,ceiling[1],1e-8)})
        for elevation in elevations:
            offset=explicit_offset
            if offset is None: offset=next((candidate for boundary,candidate,underside in area_ceiling_boundaries if abs(underside-elevation)<=1e-7 and polygons_equivalent(polygon,boundary)),0.0)
            ceiling_openings.append((opening_id,offset_polygon_outward(polygon,offset) if offset>0 else polygon,elevation))
    records=[]
    for surface,openings,surfaces in (("floor",floor_openings,floors),("ceiling",ceiling_openings,ceilings)):
        for opening_id,polygon,elevation in openings:
            source_id,space_id=opening_metadata.get((surface,opening_id),(opening_id,None))
            records.append(opening_handoff_record(surface,opening_id,source_id,space_id,polygon,elevation,surfaces,markers))
    world_walls=[Segment(world(wall.a),world(wall.b),wall.source_id,wall.height,wall.thickness,wall.elevation) for wall in walls]
    split_walls=world_walls
    for opening in wall_openings:
        split_walls=[piece for wall in split_walls for piece in carve_opening(wall,opening,settings.opening_snap)]
    final_walls,_=normalize_wall_segments(split_walls,settings.wall_merge_tolerance)
    if settings.wall_joint_mode=="butt":
        final_walls,_=resolve_wall_joints(final_walls,settings.wall_merge_tolerance,settings.wall_joint_gap)
    wall_volume_overlaps=detect_parallel_wall_overlaps(final_walls)
    ceiling_alignment_issues=detect_ceiling_wall_alignment(ceilings,world_walls,settings.ceiling_alignment_tolerance)
    wall_construction=[{"source_id":source_id,"thickness_m":report_number(thickness),"height_m":report_number(height),"base_elevation_m":report_number(elevation)} for source_id,thickness,height,elevation in sorted({(wall.source_id,wall.thickness,wall.height,wall.elevation) for wall in walls})]
    ceiling_construction=[{
        "source_id":source_id,
        "underside_elevation_m":report_number(underside),
        "top_elevation_m":report_number(underside+thickness),
        "thickness_m":report_number(thickness),
    } for source_id,_,underside,thickness in ceilings]
    levels=sorted({report_number(elevation+thickness/2 if settings.floor_anchor=="center" else elevation) for _,_,elevation,thickness in floors})
    anchor_frames,anchor_frame_issues=semantic_anchor_frames(items,floors,ceilings,walls,settings,overrides,world)
    points=[]; anchors=[]
    for marker in markers:
        record={"source_id":marker.source_id,"type":marker.kind,"position_m":[report_number(value) for value in marker.position]}
        for key in ("space_id","anchor_id","anchor_role"):
            if marker.metadata.get(key) is not None: record[key]=marker.metadata[key]
        points.append(record)
        if "anchor_id" in record or "anchor_role" in record: anchors.append(record.copy())
    handoff={
        "coordinate_space":{"name":"generated_scene_local","units":"meters","horizontal_axes":["x","z"],"vertical_axis":"y","origin_mode":settings.origin,"invert_z":settings.invert_z},
        "surface_openings":records,"wall_construction":wall_construction,"ceiling_construction":ceiling_construction,"floor_levels_m":levels,
        "reference_points":points,"anchors":anchors,"anchor_frames":anchor_frames,
        "space_ids":sorted({str(value) for value in [*[record.get("space_id") for record in records],*[point.get("space_id") for point in points]] if value is not None}),
    }
    heights=[{"from_elevation_m":lower,"to_elevation_m":upper,"height_m":report_number(upper-lower)} for lower,upper in zip(levels,levels[1:])]
    if heights: handoff["floor_to_floor_heights"]=heights
    if wall_volume_overlaps: handoff["wall_volume_overlaps"]=wall_volume_overlaps
    if ceiling_alignment_issues: handoff["ceiling_wall_alignment_issues"]=ceiling_alignment_issues
    if anchor_frame_issues: handoff["anchor_frame_issues"]=anchor_frame_issues
    return handoff


def wall_overlap_message(count: int) -> str:
    return f"Detected {count} positive-volume overlap(s) between parallel final wall segments; see spatial_handoff.wall_volume_overlaps"


def ceiling_alignment_message(count: int) -> str:
    return f"Detected {count} ceiling underside/wall-top alignment issue(s); see spatial_handoff.ceiling_wall_alignment_issues"


def inspect_source(source_path: str | Path, profile: str = "generic", config_path: str | None = None, map_classes: Sequence[str] | None = None, set_values: Sequence[str] | None = None, settings_options: argparse.Namespace | None = None) -> dict[str,Any]:
    source=Path(source_path).resolve(); config,class_map,overrides=build_semantic_options(profile,config_path,map_classes,set_values)
    settings=build_settings(settings_options,config.get("defaults",{}),False)
    svg=extract_svg(source)
    try: root=ET.fromstring(svg)
    except ET.ParseError as exc: raise ConversionError(f"SVG is not well-formed XML: {exc}")
    viewbox=parse_numbers(root.attrib.get("viewBox",root.attrib.get("viewbox","")))
    if len(viewbox)!=4: raise ConversionError("SVG root must have a four-number viewBox")
    report=Report(); items=load_items(root,class_map,overrides,report)
    recognized=[{
        "id":item.element_id,"tag":item.tag,"type":item.kind,
        "classes":item.attrs.get("class",""),"path_count":len(item.paths),
    } for item in items]
    explicitly_ignored=[item for item in report.ignored if item["reason"]=="mapped to ignore"]
    unrecognized=[item for item in report.ignored if item["reason"]!="mapped to ignore"]
    spatial_handoff=build_spatial_handoff(items,settings,overrides,viewbox)
    overlap_count=len(spatial_handoff.get("wall_volume_overlaps",[]))
    if overlap_count:
        message=wall_overlap_message(overlap_count)
        if settings.strict_wall_overlaps: report.errors.append(message)
        else: report.warn(message)
    alignment_count=len(spatial_handoff.get("ceiling_wall_alignment_issues",[]))
    if alignment_count:
        message=ceiling_alignment_message(alignment_count)
        if settings.strict_ceiling_alignment: report.errors.append(message)
        else: report.warn(message)
    for message in explicit_ceiling_elevation_warnings(items,settings,overrides):
        report.warn(message)
    return {
        "inspector_version":VERSION,"source":str(source),"profile":profile,"view_box":viewbox,
        "settings":settings.__dict__,"spatial_handoff":spatial_handoff,
        "summary":{
            "recognized":len(recognized),"explicitly_ignored":len(explicitly_ignored),
            "unrecognized":len(unrecognized),"invalid":len(report.errors),
        },
        "recognized":recognized,"explicitly_ignored":explicitly_ignored,
        "unrecognized":unrecognized,"invalid":report.errors,"warnings":report.warnings,
    }


def generate(args: argparse.Namespace) -> dict[str, Any]:
    source=Path(args.input).resolve(); output=Path(args.output_dir).resolve(); report=Report()
    config,class_map,overrides=build_semantic_options(args.profile,args.config,args.map_class,args.set_value)
    defaults=config.get("defaults",{})
    settings=build_settings(args,defaults,bool(args.strict))
    svg=extract_svg(source)
    try: root=ET.fromstring(svg)
    except ET.ParseError as exc: raise ConversionError(f"SVG is not well-formed XML: {exc}")
    viewbox=parse_numbers(root.attrib.get("viewBox",root.attrib.get("viewbox","")))
    if len(viewbox)!=4: raise ConversionError("SVG root must have a four-number viewBox")
    _,_,width,height=viewbox
    world=world_transform(viewbox,settings)
    if settings.floor_extent not in {"wall-outer","centerline"}:
        raise ConversionError("--floor-extent must be wall-outer or centerline")
    if settings.floor_edge_offset is not None and settings.floor_edge_offset<0:
        raise ConversionError("--floor-edge-offset must be zero or greater")
    if settings.ceiling_extent not in {"wall-outer","centerline"}:
        raise ConversionError("--ceiling-extent must be wall-outer or centerline")
    if settings.ceiling_edge_offset is not None and settings.ceiling_edge_offset<0:
        raise ConversionError("--ceiling-edge-offset must be zero or greater")
    if settings.ceiling_thickness<=0 or settings.ceiling_height<=0:
        raise ConversionError("ceiling height and thickness must be greater than zero")
    if settings.ceiling_mesh not in {"surface","slab"}:
        raise ConversionError("--ceiling-mesh must be surface or slab")
    items=load_items(root,class_map,overrides,report)
    floors: list[tuple[str,list[tuple[float,float]],float,float]]=[]; floor_openings: list[tuple[str,list[tuple[float,float]],float]]=[]; pending_floor_openings: list[tuple[str,list[tuple[float,float]],float,float|None]]=[]; area_floor_boundaries: list[tuple[list[tuple[float,float]],float]]=[]
    ceilings: list[tuple[str,list[tuple[float,float]],float,float]]=[]; ceiling_openings: list[tuple[str,list[tuple[float,float]],float]]=[]; pending_ceiling_openings: list[tuple[str,list[tuple[float,float]],float|None,float|None]]=[]; area_ceiling_boundaries: list[tuple[list[tuple[float,float]],float,float]]=[]
    walls: list[Segment]=[]; room_walls: list[Segment]=[]; openings: list[Opening]=[]; boxes: list[Box]=[]; markers: list[Marker]=[]
    opening_source_metadata: dict[tuple[str,str],dict[str,str|None]]={}
    expanded_area_floors=0; expanded_area_ceilings=0
    for item in items:
        try:
            elevation=value_for(item,"default_elevation",settings,overrides)
            if item.kind in {"area","floor"}:
                original_polygon=[world(p) for p in polygon_of(item)]
                floor_polygon=original_polygon
                explicit_offset=optional_value_for(item,("floor_edge_offset",),overrides)
                if explicit_offset is not None and explicit_offset<0:
                    raise ConversionError("floor edge offset must be zero or greater")
                if item.kind=="area":
                    wall_thickness=value_for(item,"wall_thickness",settings,overrides)
                    edge_offset=explicit_offset if explicit_offset is not None else (settings.floor_edge_offset if settings.floor_edge_offset is not None else wall_thickness/2)
                    if settings.floor_extent=="wall-outer" and edge_offset>0:
                        floor_polygon=offset_polygon_outward(original_polygon,edge_offset); expanded_area_floors+=1
                    else:
                        edge_offset=0.0
                    area_floor_boundaries.append((original_polygon,edge_offset))
                    walls.extend(segments_of(item,settings,overrides))
                    if settings.generate_ceilings:
                        ceiling_offset_override=optional_value_for(item,("ceiling_edge_offset",),overrides)
                        if ceiling_offset_override is not None and ceiling_offset_override<0: raise ConversionError("ceiling edge offset must be zero or greater")
                        ceiling_offset=ceiling_offset_override if ceiling_offset_override is not None else (settings.ceiling_edge_offset if settings.ceiling_edge_offset is not None else wall_thickness/2)
                        ceiling_polygon=original_polygon
                        if settings.ceiling_extent=="wall-outer" and ceiling_offset>0:
                            ceiling_polygon=offset_polygon_outward(original_polygon,ceiling_offset); expanded_area_ceilings+=1
                        else:
                            ceiling_offset=0.0
                        ceiling_underside=elevation+value_for(item,"wall_height",settings,overrides)
                        ceilings.append((item.element_id,ceiling_polygon,ceiling_underside,value_for(item,"ceiling_thickness",settings,overrides)))
                        area_ceiling_boundaries.append((original_polygon,ceiling_offset,ceiling_underside))
                elif explicit_offset is not None and explicit_offset>0:
                    floor_polygon=offset_polygon_outward(original_polygon,explicit_offset)
                floors.append((item.element_id,floor_polygon,elevation,value_for(item,"floor_thickness",settings,overrides)))
                if flag_for(item,"floor_opening",overrides):
                    opening_id=item.element_id+"_floor_opening"
                    pending_floor_openings.append((opening_id,original_polygon,elevation,explicit_offset))
                    opening_source_metadata[("floor",opening_id)]={"source_id":item.element_id,"space_id":text_value_for(item,"space_id",overrides)}
                if flag_for(item,"ceiling_opening",overrides):
                    opening_id=item.element_id+"_ceiling_opening"
                    pending_ceiling_openings.append((opening_id,original_polygon,None,optional_value_for(item,("ceiling_edge_offset",),overrides)))
                    opening_source_metadata[("ceiling",opening_id)]={"source_id":item.element_id,"space_id":text_value_for(item,"space_id",overrides)}
            elif item.kind=="floor-opening":
                if not item.closed: raise ConversionError("floor-opening must be a closed rect, polygon, or closed path")
                opening_polygon=[world(p) for p in polygon_of(item)]
                opening_offset=optional_value_for(item,("floor_edge_offset",),overrides)
                if opening_offset is not None and opening_offset<0: raise ConversionError("floor opening edge offset must be zero or greater")
                pending_floor_openings.append((item.element_id,opening_polygon,elevation,opening_offset))
                opening_source_metadata[("floor",item.element_id)]={"source_id":item.element_id,"space_id":text_value_for(item,"space_id",overrides)}
            elif item.kind=="ceiling":
                original_polygon=[world(p) for p in polygon_of(item)]
                ceiling_offset=optional_value_for(item,("ceiling_edge_offset",),overrides)
                if ceiling_offset is not None and ceiling_offset<0: raise ConversionError("ceiling edge offset must be zero or greater")
                ceiling_polygon=offset_polygon_outward(original_polygon,ceiling_offset) if ceiling_offset else original_polygon
                explicit_ceiling_elevation=optional_value_for(item,("ceiling_elevation",),overrides)
                underside=explicit_ceiling_elevation if explicit_ceiling_elevation is not None else elevation+value_for(item,"ceiling_height",settings,overrides)
                ceilings.append((item.element_id,ceiling_polygon,underside,value_for(item,"ceiling_thickness",settings,overrides)))
            elif item.kind=="ceiling-opening":
                if not item.closed: raise ConversionError("ceiling-opening must be a closed rect, polygon, or closed path")
                opening_polygon=[world(p) for p in polygon_of(item)]
                opening_offset=optional_value_for(item,("ceiling_edge_offset",),overrides)
                if opening_offset is not None and opening_offset<0: raise ConversionError("ceiling opening edge offset must be zero or greater")
                target_elevation=optional_value_for(item,("ceiling_elevation",),overrides)
                pending_ceiling_openings.append((item.element_id,opening_polygon,target_elevation,opening_offset))
                opening_source_metadata[("ceiling",item.element_id)]={"source_id":item.element_id,"space_id":text_value_for(item,"space_id",overrides)}
            elif item.kind=="wall":
                item_walls=segments_of(item,settings,overrides); walls.extend(item_walls); room_walls.extend(item_walls)
            elif item.kind in {"opening","door","window"}:
                bottom,opening_height=opening_dimensions(item,settings,overrides)
                segs=segments_of(item,settings,overrides)
                openings.extend(Opening(world(s.a),world(s.b),item.element_id,item.kind,elevation+bottom,opening_height) for s in segs)
                if item.kind in {"door","window"} and item.paths and item.paths[0]:
                    points=[world(p) for path in item.paths for p in path]; x=sum(p[0] for p in points)/len(points); z=sum(p[1] for p in points)/len(points)
                    marker_y=elevation+bottom+(opening_height or 0.0)/2
                    metadata={"opening_bottom":bottom,"opening_height":opening_height}
                    for key in ("space_id","anchor_id","anchor_role"):
                        value=text_value_for(item,key,overrides)
                        if value is not None: metadata[key]=value
                    markers.append(Marker(item.kind.title()+"_"+safe_name(item.element_id),item.element_id,item.kind,(x,marker_y,z),metadata))
            elif item.kind=="obstacle":
                polygon=[world(p) for p in polygon_of(item)]; xs=[p[0] for p in polygon]; zs=[p[1] for p in polygon]; h=value_for(item,"obstacle_height",settings,overrides)
                boxes.append(Box("Obstacle_"+safe_name(item.element_id),item.element_id,((min(xs)+max(xs))/2,elevation+h/2,(min(zs)+max(zs))/2),(max(xs)-min(xs),h,max(zs)-min(zs)),material="Obstacle"))
                if item.tag != "rect": report.warn(f"{item.element_id}: obstacle uses its axis-aligned bounding box")
            elif item.kind in {"stairs","marker"}:
                points=[world(p) for path in item.paths for p in path]
                if not points: raise ConversionError("marker has no position")
                x=sum(p[0] for p in points)/len(points); z=sum(p[1] for p in points)/len(points); meta={"text":item.text} if item.text else {}
                for key in ("space_id","anchor_id","anchor_role"):
                    value=text_value_for(item,key,overrides)
                    if value is not None: meta[key]=value
                markers.append(Marker(item.kind.title()+"_"+safe_name(item.element_id),item.element_id,item.kind,(x,elevation,z),meta))
                if item.kind=="stairs" and "data-step-count" not in item.attrs: report.warn(f"{item.element_id}: stairs emitted as a marker; step geometry requires explicit stair metadata")
        except (ConversionError,ValueError) as exc: report.errors.append(f"{item.element_id}: {exc}")
    for opening_id,opening_polygon,elevation,explicit_offset in pending_floor_openings:
        opening_edge_offset=explicit_offset
        if opening_edge_offset is None:
            opening_edge_offset=next((area_offset for area_polygon,area_offset in area_floor_boundaries if polygons_equivalent(opening_polygon,area_polygon)),0.0)
        if opening_edge_offset>0:
            opening_polygon=offset_polygon_outward(opening_polygon,opening_edge_offset)
        floor_openings.append((opening_id,opening_polygon,elevation))
    for opening_id,opening_polygon,target_elevation,explicit_offset in pending_ceiling_openings:
        matching_ceilings=[]
        if target_elevation is not None:
            matching_ceilings=[target_elevation]
        else:
            matching_ceilings=sorted({ceiling[2] for ceiling in ceilings if polygons_overlap(opening_polygon,ceiling[1],1e-8)})
        for underside in matching_ceilings:
            ceiling_opening_polygon=opening_polygon
            opening_edge_offset=explicit_offset
            if opening_edge_offset is None:
                opening_edge_offset=next((area_offset for area_polygon,area_offset,area_underside in area_ceiling_boundaries if abs(area_underside-underside)<=1e-7 and polygons_equivalent(opening_polygon,area_polygon)),0.0)
            if opening_edge_offset>0:
                ceiling_opening_polygon=offset_polygon_outward(opening_polygon,opening_edge_offset)
            ceiling_openings.append((opening_id,ceiling_opening_polygon,underside))
    spatial_handoff=build_spatial_handoff(items,settings,overrides,viewbox)
    ceiling_alignment_issues=spatial_handoff.get("ceiling_wall_alignment_issues",[])
    report.counts["ceiling_wall_alignment_issues"]=len(ceiling_alignment_issues)
    if ceiling_alignment_issues:
        message=ceiling_alignment_message(len(ceiling_alignment_issues))
        if settings.strict_ceiling_alignment: report.errors.append(message)
        else: report.warn(message)
    for message in explicit_ceiling_elevation_warnings(items,settings,overrides):
        report.warn(message)
    report.counts["area_floors_expanded_to_wall_outer"]=expanded_area_floors
    report.counts["area_ceilings_expanded_to_wall_outer"]=expanded_area_ceilings
    if expanded_area_floors:
        inferred="half each area's wall thickness" if settings.floor_edge_offset is None else f"the explicit {settings.floor_edge_offset:.6g} m offset"
        report.assumptions.append(f"Expanded {expanded_area_floors} area floor perimeter(s) to wall outer faces using {inferred}")
    if expanded_area_ceilings:
        inferred="half each area's wall thickness" if settings.ceiling_edge_offset is None else f"the explicit {settings.ceiling_edge_offset:.6g} m offset"
        report.assumptions.append(f"Expanded {expanded_area_ceilings} area ceiling perimeter(s) to wall outer faces using {inferred}")
    world_walls=[Segment(world(w.a),world(w.b),w.source_id,w.height,w.thickness,w.elevation) for w in walls]
    world_room_walls=[]
    for wall in room_walls:
        a,b=world(wall.a),world(wall.b); dx,dz=b[0]-a[0],b[1]-a[1]; length=math.hypot(dx,dz); extension=wall.thickness/2
        if length>1e-9:
            ux,uz=dx/length,dz/length; a=(a[0]-ux*extension,a[1]-uz*extension); b=(b[0]+ux*extension,b[1]+uz*extension)
        world_room_walls.append(Segment(a,b,wall.source_id,wall.height,wall.thickness,wall.elevation))
    split_walls=world_walls
    for opening in openings: split_walls=[piece for wall in split_walls for piece in carve_opening(wall,opening,settings.opening_snap)]
    normalized_walls,normalization=normalize_wall_segments(split_walls,settings.wall_merge_tolerance)
    if settings.wall_joint_mode not in {"butt","overlap"}:
        raise ConversionError("--wall-joint-mode must be butt or overlap")
    if settings.wall_joint_gap < 0:
        raise ConversionError("--wall-joint-gap must be zero or greater")
    resolved_wall_joints=0
    if settings.wall_joint_mode=="butt":
        normalized_walls,resolved_wall_joints=resolve_wall_joints(normalized_walls,settings.wall_merge_tolerance,settings.wall_joint_gap)
    wall_volume_overlaps=detect_parallel_wall_overlaps(normalized_walls)
    if wall_volume_overlaps:
        spatial_handoff["wall_volume_overlaps"]=wall_volume_overlaps
        message=wall_overlap_message(len(wall_volume_overlaps))
        if settings.strict_wall_overlaps: report.errors.append(message)
        else: report.warn(message)
    else:
        spatial_handoff.pop("wall_volume_overlaps",None)
    if settings.wall_embed < 0:
        raise ConversionError("--wall-embed must be zero or greater")
    if normalization["after_merge"] < normalization["before"]:
        report.assumptions.append(f'Wall normalization merged {normalization["before"]-normalization["after_merge"]} duplicate or overlapping segment(s) with tolerance {settings.wall_merge_tolerance} m')
    report.counts["wall_segments_before_normalization"]=normalization["before"]
    report.counts["wall_segments_after_merge"]=normalization["after_merge"]
    report.counts["wall_segments_after_normalization"]=normalization["after_intersections"]
    report.counts["wall_joints_resolved"]=resolved_wall_joints
    report.counts["wall_volume_overlaps"]=len(wall_volume_overlaps)
    if resolved_wall_joints:
        report.assumptions.append(f"Wall joint normalization converted {resolved_wall_joints} overlapping branch endpoint(s) to butt joints with a {settings.wall_joint_gap:.6g} m render gap")
    floor_elevations={floor[2] for floor in floors}
    for index,wall in enumerate(normalized_walls,1):
        dx,dz=wall.b[0]-wall.a[0],wall.b[1]-wall.a[1]; length=math.hypot(dx,dz)
        embed=settings.wall_embed if any(abs(wall.elevation-level)<=1e-7 for level in floor_elevations) else 0.0
        boxes.append(Box(f"Wall_{index}_{safe_name(wall.source_id)}",wall.source_id,((wall.a[0]+wall.b[0])/2,wall.elevation+(wall.height-embed)/2,(wall.a[1]+wall.b[1])/2),(length,wall.height+embed,wall.thickness),-math.atan2(dz,dx),"Wall"))
    normalized_floors,floor_normalization=normalize_floor_polygons(floors,floor_openings,world_room_walls)
    if settings.floor_anchor not in {"center","top"}:
        raise ConversionError("--floor-anchor must be center or top")
    if settings.floor_mesh not in {"surface","slab"}:
        raise ConversionError("--floor-mesh must be surface or slab")
    report.counts["floor_polygons_before_normalization"]=int(floor_normalization["before"])
    report.counts["floor_cells_after_normalization"]=int(floor_normalization["after"])
    report.counts["floor_openings_requested"]=int(floor_normalization["floor_openings_requested"])
    report.counts["floor_openings_applied"]=int(floor_normalization["floor_openings_applied"])
    removed_floor_overlap=float(floor_normalization["removed_overlap_area"])
    if removed_floor_overlap>1e-7:
        report.assumptions.append(f"Floor normalization removed {removed_floor_overlap:.6g} m2 of coplanar overlap")
    removed_opening_area=float(floor_normalization["removed_opening_area"])
    if removed_opening_area>1e-7:
        report.assumptions.append(f"Floor openings removed {removed_opening_area:.6g} m2 from visible and collision geometry")
    if floor_openings and floor_normalization["floor_openings_applied"]<len(floor_openings):
        report.warn(f'{len(floor_openings)-int(floor_normalization["floor_openings_applied"])} floor-opening element(s) did not overlap a floor at the same elevation')
    normalized_ceilings,ceiling_normalization=normalize_floor_polygons(ceilings,ceiling_openings,world_room_walls)
    report.counts["ceiling_polygons_before_normalization"]=int(ceiling_normalization["before"])
    report.counts["ceiling_cells_after_normalization"]=int(ceiling_normalization["after"])
    report.counts["ceiling_openings_requested"]=int(ceiling_normalization["floor_openings_requested"])
    report.counts["ceiling_openings_applied"]=int(ceiling_normalization["floor_openings_applied"])
    removed_ceiling_overlap=float(ceiling_normalization["removed_overlap_area"])
    if removed_ceiling_overlap>1e-7:
        report.assumptions.append(f"Ceiling normalization removed {removed_ceiling_overlap:.6g} m2 of coplanar overlap")
    removed_ceiling_opening_area=float(ceiling_normalization["removed_opening_area"])
    if removed_ceiling_opening_area>1e-7:
        report.assumptions.append(f"Ceiling openings removed {removed_ceiling_opening_area:.6g} m2 from visible and collision geometry")
    requested_ceiling_opening_ids={item[0] for item in pending_ceiling_openings}; applied_ceiling_opening_ids={item[0] for item in ceiling_openings}
    unmatched_ceiling_openings=len(requested_ceiling_opening_ids-applied_ceiling_opening_ids)
    unapplied_ceiling_openings=max(0,len(ceiling_openings)-int(ceiling_normalization["floor_openings_applied"]))
    if unmatched_ceiling_openings+unapplied_ceiling_openings:
        report.warn(f"{unmatched_ceiling_openings+unapplied_ceiling_openings} ceiling-opening element(s) did not overlap a ceiling at the selected elevation")
    floor_rooms=group_normalized_surfaces(normalized_floors,[item[0] for item in floors],world_room_walls)
    ceiling_rooms=group_normalized_surfaces(normalized_ceilings,[item[0] for item in ceilings],world_room_walls)
    output.mkdir(parents=True,exist_ok=True); scene_name=safe_name(args.scene_name or source.stem,"GeneratedScene")
    project_root=find_project_root(output,args.project_root)
    make_resource_path(output,"geometry.mtl",project_root,args.resource_dir)
    removed_stale_parts,cleanup_warning=remove_previous_generated_parts(output)
    report.counts["removed_stale_generated_parts"]=len(removed_stale_parts)
    if cleanup_warning: report.warn(cleanup_warning)
    if removed_stale_parts: report.assumptions.append(f"Removed {len(removed_stale_parts)} numbered generated OBJ part(s) listed by the previous conversion report before regeneration")
    writers={"Floors":ObjWriter(),"Ceilings":ObjWriter(),"Walls":ObjWriter(),"Obstacles":ObjWriter()}
    surface_parts=[]; box_parts=[]
    for index,(source_id,polygons,elevation,thickness) in enumerate(floor_rooms,1):
        node_name=f"Floor_{index:03d}_{safe_name(source_id)}"
        floor_bottom=elevation-thickness/2 if settings.floor_anchor=="center" else elevation-thickness
        floor_top=elevation+thickness/2 if settings.floor_anchor=="center" else elevation
        if settings.floor_mesh=="surface":
            collision=writers["Floors"].add_cell_surface(node_name,"Floor",polygons,floor_top,"top")
            writer=ObjWriter(); writer.add_cell_surface(node_name,"Floor",polygons,floor_top,"top")
        else:
            collision=writers["Floors"].add_cell_slab(node_name,"Floor",polygons,floor_bottom,floor_top,"top")
            writer=ObjWriter(); writer.add_cell_slab(node_name,"Floor",polygons,floor_bottom,floor_top,"top")
        surface_parts.append(("Floors",node_name,source_id,writer,collision))
    for index,(source_id,polygons,underside,thickness) in enumerate(ceiling_rooms,1):
        node_name=f"Ceiling_{index:03d}_{safe_name(source_id)}"; ceiling_top=underside+thickness
        if settings.ceiling_mesh=="surface":
            collision=writers["Ceilings"].add_cell_surface(node_name,"Ceiling",polygons,underside,"bottom")
            writer=ObjWriter(); writer.add_cell_surface(node_name,"Ceiling",polygons,underside,"bottom")
        else:
            collision=writers["Ceilings"].add_cell_slab(node_name,"Ceiling",polygons,underside,ceiling_top,"bottom")
            writer=ObjWriter(); writer.add_cell_slab(node_name,"Ceiling",polygons,underside,ceiling_top,"bottom")
        surface_parts.append(("Ceilings",node_name,source_id,writer,collision))
    wall_index=0; obstacle_index=0
    for box in boxes:
        group="Walls" if box.material=="Wall" else "Obstacles"
        writers[group].add_box(box)
        writer=ObjWriter(); writer.add_box(box)
        if group=="Walls": wall_index+=1; part_index=wall_index
        else: obstacle_index+=1; part_index=obstacle_index
        node_name=f'{"Wall" if group=="Walls" else "Obstacle"}_{part_index:03d}_{safe_name(box.source_id)}'
        box_parts.append((group,node_name,box.source_id,writer,box))
    scene_path=output/(scene_name+".tscn"); report_path=output/"conversion_report.json"; mtl_path=output/"geometry.mtl"
    mesh_specs=[
        ("Floors",output/"floor_geometry.obj",writers["Floors"],output/"floor_material.tres","2_floor_mesh","1_floor_material"),
        ("Ceilings",output/"ceiling_geometry.obj",writers["Ceilings"],output/"ceiling_material.tres","8_ceiling_mesh","7_ceiling_material"),
        ("Walls",output/"wall_geometry.obj",writers["Walls"],output/"wall_material.tres","4_wall_mesh","3_wall_material"),
        ("Obstacles",output/"obstacle_geometry.obj",writers["Obstacles"],output/"obstacle_material.tres","6_obstacle_mesh","5_obstacle_material"),
    ]
    material_colors={"Floors":(0.32,0.38,0.46,1.0),"Ceilings":(0.62,0.65,0.70,1.0),"Walls":(0.72,0.74,0.78,1.0),"Obstacles":(0.50,0.38,0.24,1.0)}
    created_materials=[]; preserved_materials=[]
    for group,_,_,material_path,_,_ in mesh_specs:
        (created_materials if ensure_material_resource(material_path,material_colors[group]) else preserved_materials).append(material_path.name)
    report.counts["material_files_created"]=len(created_materials); report.counts["material_files_preserved"]=len(preserved_materials)
    mtl_path.write_text("# Import fallback; scene nodes use editable .tres overrides\nnewmtl Floor\nKd 0.32 0.38 0.46\nNs 8\nnewmtl Ceiling\nKd 0.62 0.65 0.70\nNs 8\nnewmtl Wall\nKd 0.72 0.74 0.78\nNs 12\nnewmtl Obstacle\nKd 0.50 0.38 0.24\nNs 8\n",encoding="utf-8")
    ext_resources=[]; visual_nodes=[]
    files=[scene_path.name,mtl_path.name,*[spec[3].name for spec in mesh_specs],report_path.name]
    material_resources={}
    for group,obj_path,writer,material_path,mesh_id,material_id in mesh_specs:
        material_resource=make_resource_path(output,material_path.name,project_root,args.resource_dir)
        material_resources[group]=material_resource
        ext_resources.append(f'[ext_resource type="Material" path="{material_resource}" id="{material_id}"]')
        if writer.vertex_count:
            obj_path.write_text("\n".join(writer.lines)+"\n",encoding="utf-8"); files.insert(-1,obj_path.name)
    visual_nodes.extend(f'[node name="Visual" type="Node3D" parent="{group}"]' for group in ("Floors","Ceilings","Walls","Obstacles"))
    group_indices={"Floors":0,"Ceilings":0}
    for group,node_name,source_id,writer,_ in surface_parts:
        group_indices[group]+=1; index=group_indices[group]; prefix="floor" if group=="Floors" else "ceiling"; material_id="1_floor_material" if group=="Floors" else "7_ceiling_material"
        filename=f"{prefix}_{index:03d}_{safe_name(source_id)}.obj"; path=output/filename
        path.write_text("\n".join(writer.lines)+"\n",encoding="utf-8"); files.insert(-1,filename)
        resource=make_resource_path(output,filename,project_root,args.resource_dir); mesh_id=f"{prefix}_part_{index}_mesh"
        ext_resources.append(f'[ext_resource type="Mesh" path="{resource}" id="{mesh_id}"]')
        visual_nodes.append(f'[node name="{node_name}" type="MeshInstance3D" parent="{group}/Visual"]\nmesh = ExtResource("{mesh_id}")\nmaterial_override = ExtResource("{material_id}")\nmetadata/source_id = {godot_string(source_id)}')
    for index,(group,node_name,source_id,writer,_) in enumerate(box_parts,1):
        prefix="wall" if group=="Walls" else "obstacle"; filename=f"{prefix}_{node_name.split('_',2)[1]}_{safe_name(source_id)}.obj"; path=output/filename
        path.write_text("\n".join(writer.lines)+"\n",encoding="utf-8"); files.insert(-1,filename)
        resource=make_resource_path(output,filename,project_root,args.resource_dir); mesh_id=f"box_part_{index}_mesh"; material_id="3_wall_material" if group=="Walls" else "5_obstacle_material"
        ext_resources.append(f'[ext_resource type="Mesh" path="{resource}" id="{mesh_id}"]')
        visual_nodes.append(f'[node name="{node_name}" type="MeshInstance3D" parent="{group}/Visual"]\nmesh = ExtResource("{mesh_id}")\nmaterial_override = ExtResource("{material_id}")\nmetadata/source_id = {godot_string(source_id)}')
    subresources=[]; nodes=[]
    collision_indices={"Floors":0,"Ceilings":0}
    for group,node_name,source_id,_,collision in surface_parts:
        collision_indices[group]+=1; index=collision_indices[group]; kind="Floor" if group=="Floors" else "Ceiling"
        sid=f"Concave{kind}_{index}"; values=", ".join(f"{x:.6g}, {y:.6g}, {z:.6g}" for x,y,z in collision)
        subresources.append(f'[sub_resource type="ConcavePolygonShape3D" id="{sid}"]\ndata = PackedVector3Array({values})')
        nodes.append(f'[node name="{node_name}" type="StaticBody3D" parent="{group}/Collisions"]\ncollision_layer = {settings.collision_layer}\ncollision_mask = {settings.collision_layer}\nmetadata/source_id = {godot_string(source_id)}\n[node name="CollisionShape3D" type="CollisionShape3D" parent="{group}/Collisions/{node_name}"]\nshape = SubResource("{sid}")')
    for index,(_,node_name,source_id,_,box) in enumerate(box_parts,1):
        sid=f"Box_{index}"; subresources.append(f'[sub_resource type="BoxShape3D" id="{sid}"]\nsize = {godot_vector(box.size)}'); parent=("Walls" if box.material=="Wall" else "Obstacles")+"/Collisions"
        node_name=safe_name(node_name)
        nodes.append(f'[node name="{node_name}" type="StaticBody3D" parent="{parent}"]\nposition = {godot_vector(box.center)}\nrotation = Vector3(0, {box.angle_y:.8g}, 0)\ncollision_layer = {settings.collision_layer}\ncollision_mask = {settings.collision_layer}\nmetadata/source_id = {godot_string(source_id)}\n[node name="CollisionShape3D" type="CollisionShape3D" parent="{parent}/{node_name}"]\nshape = SubResource("{sid}")')
    if settings.include_preview: subresources.append('[sub_resource type="Environment" id="PreviewEnvironment"]\nbackground_mode = 1\nbackground_color = Color(0.06, 0.07, 0.09, 1)\nambient_light_source = 3\nambient_light_color = Color(0.55, 0.6, 0.7, 1)\nambient_light_energy = 0.7')
    marker_nodes=[]
    for marker in markers:
        lines=[f'[node name="{safe_name(marker.name)}" type="Marker3D" parent="Markers"]',f'position = {godot_vector(marker.position)}',f'metadata/type = {godot_string(marker.kind)}',f'metadata/source_id = {godot_string(marker.source_id)}']
        for key,value in marker.metadata.items(): lines.append(f'metadata/{safe_name(key)} = {godot_string(str(value))}')
        marker_nodes.append("\n".join(lines))
    tscn=[f'[gd_scene load_steps={1+len(ext_resources)+len(subresources)} format=3]',*ext_resources,*subresources,f'[node name="{scene_name}" type="Node3D"]',f'metadata/source_file = {godot_string(str(source))}',f'metadata/generator = "svg_to_godot3d {VERSION}"','[node name="Floors" type="Node3D" parent="."]','[node name="Collisions" type="Node3D" parent="Floors"]','[node name="Ceilings" type="Node3D" parent="."]','[node name="Collisions" type="Node3D" parent="Ceilings"]','[node name="Walls" type="Node3D" parent="."]','[node name="Collisions" type="Node3D" parent="Walls"]','[node name="Obstacles" type="Node3D" parent="."]','[node name="Collisions" type="Node3D" parent="Obstacles"]','[node name="Markers" type="Node3D" parent="."]',*visual_nodes,*nodes,*marker_nodes]
    if settings.include_preview:
        tscn += ['[node name="PreviewEnvironment" type="WorldEnvironment" parent="."]','environment = SubResource("PreviewEnvironment")','[node name="PreviewSun" type="DirectionalLight3D" parent="."]','rotation_degrees = Vector3(-55, -35, 0)','shadow_enabled = true','[node name="PreviewCamera" type="Camera3D" parent="."]',f'position = Vector3(0, {max(12.0,width*settings.scale*0.9):.6g}, {max(10.0,height*settings.scale*0.65):.6g})','rotation_degrees = Vector3(-55, 0, 0)','current = true']
    scene_path.write_text("\n\n".join(tscn)+"\n",encoding="utf-8")
    report.bump("generated_floors",len(floor_rooms)); report.bump("generated_ceilings",len(ceiling_rooms)); report.bump("generated_walls",len(normalized_walls)); report.bump("generated_obstacles",sum(1 for b in boxes if b.material=="Obstacle")); report.bump("generated_markers",len(markers))
    report.counts["generated_floor_visual_nodes"]=len(floor_rooms)
    report.counts["generated_ceiling_visual_nodes"]=len(ceiling_rooms)
    report.counts["generated_wall_visual_nodes"]=sum(1 for group,*_ in box_parts if group=="Walls")
    report.counts["generated_obstacle_visual_nodes"]=sum(1 for group,*_ in box_parts if group=="Obstacles")
    result={"generator_version":VERSION,"source":str(source),"scene":str(scene_path),"settings":settings.__dict__,"view_box":viewbox,"profile":args.profile,"spatial_handoff":spatial_handoff,"counts":report.counts,"assumptions":report.assumptions,"warnings":report.warnings,"errors":report.errors,"ignored":report.ignored,"materials":material_resources,"created_materials":created_materials,"preserved_materials":preserved_materials,"files":files}
    report_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if report.errors and (settings.strict or settings.strict_wall_overlaps or settings.strict_ceiling_alignment): raise ConversionError(f"Strict conversion failed with {len(report.errors)} error(s); see {report_path}")
    return result


def build_parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="Convert annotated HTML/SVG plans into Godot 4 3D blockout scenes")
    p.add_argument("input",help="Input .html or .svg file"); p.add_argument("output_dir",help="Output directory")
    p.add_argument("--config",help="JSON configuration file"); p.add_argument("--profile",choices=["generic","codex-plan"],default="generic")
    p.add_argument("--project-root",help="Godot project directory containing project.godot"); p.add_argument("--resource-dir",help="Explicit res:// directory matching output_dir")
    p.add_argument("--scene-name"); p.add_argument("--scale",type=float,help="Meters per SVG unit")
    p.add_argument("--wall-height",type=float); p.add_argument("--wall-thickness",type=float); p.add_argument("--wall-embed",type=float,help="Optionally extend base walls below elevation; default 0"); p.add_argument("--wall-joint-mode",choices=["butt","overlap"],help="Use non-overlapping butt joints (default) or legacy overlapping boxes"); p.add_argument("--wall-joint-gap",type=float,help="Tiny clearance at butt joints in meters; default 0.0005"); p.add_argument("--floor-thickness",type=float); p.add_argument("--floor-anchor",choices=["center","top"],help="Place floor elevation at top surface (default) or slab center"); p.add_argument("--floor-mesh",choices=["surface","slab"],help="Generate a closed slab extending below elevation (default) or only a flat top surface"); p.add_argument("--floor-extent",choices=["wall-outer","centerline"],help="Extend area floors to outer wall faces (default) or stop at wall centerlines"); p.add_argument("--floor-edge-offset",type=float,help="Explicit outward area-floor offset in meters; default is half wall thickness")
    p.add_argument("--no-ceilings",action="store_true",help="Do not generate automatic ceilings for area elements"); p.add_argument("--ceiling-height",type=float,help="Default ceiling underside above elevation"); p.add_argument("--ceiling-thickness",type=float); p.add_argument("--ceiling-mesh",choices=["surface","slab"]); p.add_argument("--ceiling-extent",choices=["wall-outer","centerline"]); p.add_argument("--ceiling-edge-offset",type=float)
    p.add_argument("--obstacle-height",type=float)
    p.add_argument("--elevation",type=float); p.add_argument("--opening-snap",type=float,help="Opening-to-wall snap tolerance in meters")
    p.add_argument("--door-height",type=float,help="Default door opening height in meters"); p.add_argument("--door-bottom",type=float,help="Default door opening bottom above wall elevation in meters")
    p.add_argument("--window-height",type=float,help="Default window opening height in meters"); p.add_argument("--window-bottom",type=float,help="Default window sill height above wall elevation in meters")
    p.add_argument("--wall-merge-tolerance",type=float,help="Tolerance for snapping and merging collinear walls in meters")
    p.add_argument("--strict-wall-overlaps",action="store_true",help="Return non-zero when parallel final wall volumes overlap")
    p.add_argument("--ceiling-alignment-tolerance",type=float,help="Allowed ceiling-underside to wall-top difference in meters; default 0.01")
    p.add_argument("--strict-ceiling-alignment",action="store_true",help="Return non-zero when a ceiling underside does not align with nearby wall tops")
    p.add_argument("--origin",choices=["center","top-left","none"]); p.add_argument("--invert-z",action="store_true")
    p.add_argument("--collision-layer",type=int); p.add_argument("--include-preview",action="store_true")
    p.add_argument("--map-class",action="append",metavar="CLASS=TYPE"); p.add_argument("--set",dest="set_value",action="append",metavar="ELEMENT.KEY=VALUE")
    p.add_argument("--strict",action="store_true",help="Return non-zero when any element cannot be converted"); p.add_argument("--version",action="version",version=VERSION)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result=generate(build_parser().parse_args(argv))
        print(json.dumps({"scene":result["scene"],"counts":result["counts"],"warnings":len(result["warnings"]),"errors":len(result["errors"])},ensure_ascii=False,indent=2))
        return 0 if not result["errors"] else 2
    except (ConversionError,OSError,ValueError,json.JSONDecodeError) as exc:
        print(f"error: {exc}",file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
