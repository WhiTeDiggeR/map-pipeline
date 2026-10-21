from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_godot_stairs as stairs  # noqa: E402
from stair_core import Blueprint, Vec3, lowest_beam_underside_over_footprint, validate_model  # noqa: E402


SECTION = 0.12


def add_beam(
    blueprint: Blueprint,
    name: str,
    start: Vec3,
    end: Vec3,
    start_joint: str | None = None,
    end_joint: str | None = None,
    case: str = "",
) -> None:
    blueprint.add_beam(
        name,
        start,
        end,
        SECTION,
        SECTION,
        start_joint,
        end_joint,
        {"showcase_case": case},
    )


def build_showcase() -> tuple[object, list[dict[str, object]]]:
    blueprint = Blueprint()
    cases: list[dict[str, object]] = []

    add_beam(blueprint, "Straight_A", Vec3(-5.5, 1.0, -3.0), Vec3(-4.0, 1.0, -3.0), end_joint="joint:straight", case="straight")
    add_beam(blueprint, "Straight_B", Vec3(-4.0, 1.0, -3.0), Vec3(-2.5, 1.0, -3.0), start_joint="joint:straight", case="straight")
    cases.append({"id": "straight", "label": "1  Straight", "position": (-4.0, 0.35, -3.0), "target": (-4.0, 1.0, -3.0)})

    add_beam(blueprint, "Corner_A", Vec3(-1.5, 1.0, -3.0), Vec3(0.0, 1.0, -3.0), end_joint="joint:corner", case="corner")
    add_beam(blueprint, "Corner_B", Vec3(0.0, 1.0, -3.0), Vec3(0.0, 1.0, -1.5), start_joint="joint:corner", case="corner")
    cases.append({"id": "corner", "label": "2  Horizontal corner", "position": (0.0, 0.35, -3.0), "target": (0.0, 1.0, -3.0)})

    add_beam(blueprint, "SlopeFlat_A", Vec3(2.5, 0.55, -3.0), Vec3(4.0, 1.4, -3.0), end_joint="joint:slope_flat", case="slope_flat")
    add_beam(blueprint, "SlopeFlat_B", Vec3(4.0, 1.4, -3.0), Vec3(5.5, 1.4, -3.0), start_joint="joint:slope_flat", case="slope_flat")
    cases.append({"id": "slope_flat", "label": "3  Slope to flat", "position": (4.0, 0.35, -3.0), "target": (4.0, 1.4, -3.0)})

    slope_corner = Vec3(-4.0, 1.4, 1.0)
    arm = SECTION * 1.5
    first_transition = Vec3(slope_corner.x - arm, slope_corner.y, slope_corner.z)
    second_transition = Vec3(slope_corner.x, slope_corner.y, slope_corner.z + arm)
    add_beam(
        blueprint,
        "SlopeCorner_A",
        Vec3(-5.4, 0.55, 1.0),
        first_transition,
        end_joint="joint:slope_corner_first",
        case="slope_corner",
    )
    add_beam(
        blueprint,
        "SlopeCorner_Flat_A",
        first_transition,
        slope_corner,
        start_joint="joint:slope_corner_first",
        end_joint="joint:slope_corner_turn",
        case="slope_corner",
    )
    add_beam(
        blueprint,
        "SlopeCorner_Flat_B",
        slope_corner,
        second_transition,
        start_joint="joint:slope_corner_turn",
        end_joint="joint:slope_corner_second",
        case="slope_corner",
    )
    add_beam(
        blueprint,
        "SlopeCorner_B",
        second_transition,
        Vec3(-4.0, 2.25, 2.4),
        start_joint="joint:slope_corner_second",
        case="slope_corner",
    )
    cases.append({"id": "slope_corner", "label": "4  Sloped corner", "position": (-4.0, 0.35, 1.0), "target": (-4.0, 1.4, 1.0)})

    add_beam(blueprint, "T_Left", Vec3(-1.5, 1.1, 1.0), Vec3(0.0, 1.1, 1.0), end_joint="joint:t", case="t_joint")
    add_beam(blueprint, "T_Right", Vec3(0.0, 1.1, 1.0), Vec3(1.5, 1.1, 1.0), start_joint="joint:t", case="t_joint")
    add_beam(blueprint, "T_Stem", Vec3(0.0, 1.1, 1.0), Vec3(0.0, 1.1, 2.5), start_joint="joint:t", case="t_joint")
    cases.append({"id": "t_joint", "label": "5  Three-way joint", "position": (0.0, 0.35, 1.0), "target": (0.0, 1.1, 1.0)})

    terminal_start = Vec3(2.5, 1.2, 1.0)
    terminal_end = Vec3(4.0, 1.2, 1.0)
    add_beam(blueprint, "Terminal_Rail", terminal_start, terminal_end, case="terminal_post")
    blueprint.add_terminal_post(
        "Terminal_Post",
        "Terminal_Rail.end",
        Vec3(4.0, 0.1, 1.0),
        SECTION,
        SECTION,
        {"showcase_case": "terminal_post"},
    )
    cases.append({"id": "terminal_post", "label": "6  Rail to terminal post", "position": (4.0, 0.35, 1.0), "target": (4.0, 1.2, 1.0)})

    sloped_start = Vec3(-1.5, 0.7, 4.5)
    sloped_end = Vec3(1.5, 2.1, 4.5)
    sloped_center = (sloped_start + sloped_end) * 0.5
    add_beam(blueprint, "Sloped_Support_Rail", sloped_start, sloped_end, case="sloped_support")
    sloped_support = lowest_beam_underside_over_footprint(
        sloped_center,
        sloped_end - sloped_start,
        SECTION,
        SECTION / 2.0,
        SECTION / 2.0,
    )
    blueprint.add_post_support(
        "Sloped_Support_Post",
        "Sloped_Support_Rail",
        Vec3(0.0, 0.1, 4.5),
        sloped_support,
        SECTION,
        SECTION,
        {"showcase_case": "sloped_support"},
    )
    cases.append({"id": "sloped_support", "label": "7  Post under slope", "position": (0.0, 0.35, 4.5), "target": (0.0, 1.35, 4.5)})

    return blueprint.finish(), cases


def label_nodes(cases: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for index, case in enumerate(cases, 1):
        x, y, z = case["position"]
        lines.extend(
            [
                f'[node name="CaseLabel_{index:02d}" type="Label3D" parent="."]',
                f'position = Vector3({x}, {y}, {z})',
                f'text = {json.dumps(case["label"])}',
                'font_size = 32',
                'outline_size = 8',
                'modulate = Color(0.92, 0.95, 1, 1)',
                'billboard = 1',
                'no_depth_test = true',
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    output_dir = Path(__file__).resolve().parents[2] / "stair-test-examples" / "_port_joint_showcase"
    args = stairs.parser().parse_args(
        [
            str(output_dir),
            "--shaft-width", "12",
            "--shaft-length", "9",
            "--floor-height", "4",
            "--layout", "straight",
            "--railings", "both",
            "--resource-dir", "res://_port_joint_showcase",
            "--scene-name", "port_joint_showcase",
            "--include-preview",
            "--rail-post-size", str(SECTION),
            "--handrail-size", str(SECTION),
        ]
    )
    model, cases = build_showcase()
    graph_validation = validate_model(model)
    if not graph_validation.ok:
        raise RuntimeError(graph_validation.report())
    boxes, compiled_validation = stairs.compile_railing_boxes(model, args)
    layout = stairs.Layout("port-joint-showcase", SECTION, .28, .17, [], [], "north", None, [])
    output_dir.mkdir(parents=True, exist_ok=True)
    stairs.write_materials(output_dir)
    material = output_dir / "railing_material.tres"
    material.write_text(
        '[gd_resource type="StandardMaterial3D" format=3]\n\n'
        '[resource]\n'
        'albedo_color = Color(0.08, 0.42, 0.72, 1)\n'
        'metallic = 0.65\n'
        'roughness = 0.28\n',
        encoding="utf-8",
    )
    scene = stairs.scene_text(boxes, "res://_port_joint_showcase", "port_joint_showcase", args, layout)
    capture_script = output_dir / "capture_showcase.gd"
    capture_cases = ",\n".join(
        '\t\t{"id": %s, "target": Vector3(%s, %s, %s)}'
        % (json.dumps(case["id"]), *case["target"])
        for case in cases
    )
    capture_script.write_text(
        'extends Node\n\n'
        'func _set_visible_case(case_id: String) -> void:\n'
        '\tfor child in get_node("Railings/Visual").get_children():\n'
        '\t\tchild.visible = child.get_meta("showcase_case", "") == case_id\n'
        '\tfor child in get_children():\n'
        '\t\tif child is Label3D:\n'
        '\t\t\tchild.visible = false\n\n'
        'func _capture(path: String) -> bool:\n'
        '\tawait get_tree().process_frame\n'
        '\tawait RenderingServer.frame_post_draw\n'
        '\tvar error := get_viewport().get_texture().get_image().save_png(path)\n'
        '\tif error != OK:\n'
        '\t\tpush_error("Screenshot save failed: %s" % error_string(error))\n'
        '\t\treturn false\n'
        '\treturn true\n\n'
        'func _ready() -> void:\n'
        '\tvar camera := get_node("Preview/Camera3D") as Camera3D\n'
        '\tcamera.fov = 34.0\n'
        '\tvar cases := [\n'
        + capture_cases + '\n\t]\n'
        '\tfor case in cases:\n'
        '\t\t_set_visible_case(case.id)\n'
        '\t\tvar target: Vector3 = case.target\n'
        '\t\tvar offset := Vector3(2.7, 1.8, 3.1)\n'
        '\t\tif case.id == "slope_corner":\n'
        '\t\t\toffset = Vector3(1.25, 0.8, 1.35)\n'
        '\t\telif case.id == "sloped_support":\n'
        '\t\t\toffset = Vector3(1.2, 0.75, 1.35)\n'
        '\t\tcamera.position = target + offset\n'
        '\t\tcamera.look_at(target, Vector3.UP)\n'
        '\t\tif not await _capture("res://_port_joint_showcase/joint_%s.png" % case.id):\n'
        '\t\t\tget_tree().quit(2)\n'
        '\t\t\treturn\n'
        '\tget_tree().quit(0)\n',
        encoding="utf-8",
    )
    scene = re.sub(
        r"\[gd_scene load_steps=(\d+)",
        lambda match: f"[gd_scene load_steps={int(match.group(1)) + 1}",
        scene,
        count=1,
    )
    scene = scene.replace(
        '[ext_resource type="Material" path="res://_port_joint_showcase/stair_material.tres" id="1_stairs"]',
        '[ext_resource type="Material" path="res://_port_joint_showcase/stair_material.tres" id="1_stairs"]\n'
        '[ext_resource type="Script" path="res://_port_joint_showcase/capture_showcase.gd" id="4_capture"]',
        1,
    )
    scene = scene.replace(
        '[node name="port_joint_showcase" type="Node3D"]',
        '[node name="port_joint_showcase" type="Node3D"]\nscript = ExtResource("4_capture")',
        1,
    )
    scene = scene.replace(
        'position = Vector3(16.2, 5.2, 16.2)\nrotation = Vector3(-0.45, 0.78, 0.0)',
        'position = Vector3(10.0, 7.5, 12.0)\nrotation = Vector3(-0.43, 0.69, 0.0)',
    )
    scene += "\n" + label_nodes(cases)
    scene_path = output_dir / "port_joint_showcase.tscn"
    scene_path.write_text(scene, encoding="utf-8")
    report = {
        "status": "ok",
        "generator_version": stairs.VERSION,
        "scene": str(scene_path.resolve()),
        "cases": cases,
        "graph_validation": graph_validation.report(),
        "compiled_validation": compiled_validation,
        "element_count": len(model.beams) + len(model.posts),
        "connection_count": len(model.connections),
        "captures": [
            str((output_dir / f'joint_{case["id"]}.png').resolve())
            for case in cases
        ],
    }
    stairs.write_report(output_dir / "joint_showcase_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
