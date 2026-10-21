#!/usr/bin/env python3
"""Preflight inspection for HTML/SVG plans accepted by svg_to_godot3d."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from svg_to_godot3d import ConversionError, VERSION, inspect_source


def build_parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(description="Inspect HTML/SVG recognition without generating a Godot scene")
    parser.add_argument("input",help="Input .html or .svg file")
    parser.add_argument("--config",help="The same JSON configuration used by the converter")
    parser.add_argument("--profile",choices=["generic","codex-plan"],default="generic")
    parser.add_argument("--map-class",action="append",metavar="CLASS=TYPE",help="Override a CSS class mapping; repeatable")
    parser.add_argument("--set",dest="set_value",action="append",metavar="ELEMENT.KEY=VALUE",help="Override one element; repeatable")
    parser.add_argument("--scale",type=float,help="Meters per SVG unit")
    parser.add_argument("--wall-height",type=float); parser.add_argument("--wall-thickness",type=float)
    parser.add_argument("--wall-embed",type=float); parser.add_argument("--wall-joint-mode",choices=["butt","overlap"]); parser.add_argument("--wall-joint-gap",type=float)
    parser.add_argument("--floor-thickness",type=float); parser.add_argument("--floor-anchor",choices=["center","top"]); parser.add_argument("--floor-mesh",choices=["surface","slab"])
    parser.add_argument("--floor-extent",choices=["wall-outer","centerline"]); parser.add_argument("--floor-edge-offset",type=float)
    parser.add_argument("--no-ceilings",action="store_true"); parser.add_argument("--ceiling-height",type=float); parser.add_argument("--ceiling-thickness",type=float)
    parser.add_argument("--ceiling-mesh",choices=["surface","slab"]); parser.add_argument("--ceiling-extent",choices=["wall-outer","centerline"]); parser.add_argument("--ceiling-edge-offset",type=float)
    parser.add_argument("--obstacle-height",type=float); parser.add_argument("--elevation",type=float); parser.add_argument("--opening-snap",type=float)
    parser.add_argument("--door-height",type=float); parser.add_argument("--door-bottom",type=float); parser.add_argument("--window-height",type=float); parser.add_argument("--window-bottom",type=float)
    parser.add_argument("--wall-merge-tolerance",type=float); parser.add_argument("--origin",choices=["center","top-left","none"]); parser.add_argument("--invert-z",action="store_true")
    parser.add_argument("--strict-wall-overlaps",action="store_true",help="Return exit code 2 when parallel final wall volumes overlap")
    parser.add_argument("--ceiling-alignment-tolerance",type=float,help="Allowed ceiling-underside to wall-top difference in meters; default 0.01")
    parser.add_argument("--strict-ceiling-alignment",action="store_true",help="Return exit code 2 when a ceiling underside does not align with nearby wall tops")
    parser.add_argument("--collision-layer",type=int); parser.add_argument("--include-preview",action="store_true")
    parser.add_argument("--json",action="store_true",help="Print the full report as JSON")
    parser.add_argument("--output-json",metavar="FILE",help="Also save the full report as UTF-8 JSON")
    parser.add_argument("--strict",action="store_true",help="Return exit code 2 when unrecognized or invalid elements exist")
    parser.add_argument("--version",action="version",version=VERSION)
    return parser


def class_label(item: dict[str,Any]) -> str:
    return item.get("classes") or "-"


def print_human(report: dict[str,Any]) -> None:
    summary=report["summary"]
    print(f'Файл: {report["source"]}')
    print(f'Профиль: {report["profile"]}')
    print(f'viewBox: {report["view_box"]}')
    print(
        "Итого: "
        f'распознано {summary["recognized"]}, '
        f'явно пропущено {summary["explicitly_ignored"]}, '
        f'не распознано {summary["unrecognized"]}, '
        f'ошибок геометрии {summary["invalid"]}'
    )
    print("\nРАСПОЗНАННЫЕ ЭЛЕМЕНТЫ")
    if not report["recognized"]: print("  нет")
    for item in report["recognized"]:
        print(f'  [OK] {item["id"]}: <{item["tag"]}> => {item["type"]}; class={class_label(item)}')
    print("\nЯВНО ПРОПУЩЕННЫЕ ЭЛЕМЕНТЫ")
    if not report["explicitly_ignored"]: print("  нет")
    for item in report["explicitly_ignored"]:
        print(f'  [SKIP] {item["id"]}: <{item["tag"]}>; class={class_label(item)}')
    print("\nНЕРАСПОЗНАННЫЕ ЭЛЕМЕНТЫ")
    if not report["unrecognized"]: print("  нет")
    for item in report["unrecognized"]:
        print(f'  [?] {item["id"]}: <{item["tag"]}>; class={class_label(item)}; {item["reason"]}')
    print("\nОШИБКИ ГЕОМЕТРИИ ИЛИ ТИПА")
    if not report["invalid"]: print("  нет")
    for message in report["invalid"]: print(f"  [ERROR] {message}")
    print("\nПРЕДУПРЕЖДЕНИЯ")
    if not report["warnings"]: print("  нет")
    for message in report["warnings"]: print(f"  [WARN] {message}")
    print("\nТОЧНЫЕ ПРОСТРАНСТВЕННЫЕ ПАРАМЕТРЫ")
    print(json.dumps(report["spatial_handoff"],ensure_ascii=False,indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args=build_parser().parse_args(argv)
    try:
        report=inspect_source(args.input,args.profile,args.config,args.map_class,args.set_value,args)
        rendered=json.dumps(report,ensure_ascii=False,indent=2)
        if args.output_json:
            output=Path(args.output_json); output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(rendered+"\n",encoding="utf-8")
        if args.json: print(rendered)
        else: print_human(report)
        incomplete=bool(report["unrecognized"] or report["invalid"])
        wall_overlap_failure=bool(args.strict_wall_overlaps and report["spatial_handoff"].get("wall_volume_overlaps"))
        ceiling_alignment_failure=bool(args.strict_ceiling_alignment and report["spatial_handoff"].get("ceiling_wall_alignment_issues"))
        return 2 if wall_overlap_failure or ceiling_alignment_failure or (args.strict and incomplete) else 0
    except (ConversionError,OSError,ValueError,json.JSONDecodeError) as exc:
        print(f"error: {exc}",file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
