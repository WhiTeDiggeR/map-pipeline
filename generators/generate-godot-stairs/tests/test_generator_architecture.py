from __future__ import annotations

from pathlib import Path
from contextlib import redirect_stderr
import io
import json
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_godot_stairs as generator  # noqa: E402
from stair_core import compile_model, validate_compiled, validate_model  # noqa: E402


def l_turn_args(*extra: str):
    return generator.parser().parse_args(
        [
            "unused",
            "--shaft-width", "4.0",
            "--shaft-length", "4.0",
            "--floor-height", "3.2",
            "--layout", "l-turn",
            "--turn-direction", "right",
            "--railings", "both",
            "--resource-dir", "res://unused",
            *extra,
        ]
    )


class GeneratorArchitectureTests(unittest.TestCase):
    def test_semantic_anchor_frames_cover_layouts_and_are_deterministic(self) -> None:
        cases = (
            ("straight", "2.0", "6.0", "auto", "auto"),
            ("l-turn", "4.0", "4.0", "auto", "right"),
            ("u-turn", "2.7", "3.8", "auto", "auto"),
            ("perimeter", "4.4", "4.3", "3", "left"),
            ("perimeter", "4.2", "4.2", "4", "right"),
        )
        for kind, width, length, perimeter, turn in cases:
            with self.subTest(kind=kind, perimeter=perimeter):
                args = generator.parser().parse_args(
                    [
                        "unused", "--shaft-width", width, "--shaft-length", length,
                        "--floor-height", "3.2", "--layout", kind,
                        "--perimeter-flights", perimeter, "--turn-direction", turn,
                        "--scene-name", "stable_stair", "--resource-dir", "res://unused",
                    ]
                )
                layout = generator.build_layout(kind, args, None if perimeter == "auto" else int(perimeter))
                first = generator.semantic_anchor_frames(layout, args, False)
                second = generator.semantic_anchor_frames(layout, args, False)
                self.assertEqual(first, second)
                self.assertEqual(len({frame["anchor_id"] for frame in first}), len(first))
                roles = {frame["role"] for frame in first}
                self.assertTrue({"lower_entry", "upper_exit", "flight_passage", "clear_volume"}.issubset(roles))
                self.assertEqual(sum(frame["role"] == "landing" for frame in first), len(layout.landings))
                for frame in first:
                    forward, normal, up = frame["forward"], frame["normal"], frame["up"]
                    self.assertAlmostEqual(sum(value * value for value in forward), 1.0)
                    self.assertAlmostEqual(sum(value * value for value in normal), 1.0)
                    self.assertAlmostEqual(sum(value * value for value in up), 1.0)
                    self.assertAlmostEqual(sum(a * b for a, b in zip(forward, up)), 0.0)
                    if frame["type"] != "floor":
                        cross = [-forward[2], 0.0, forward[0]]
                        self.assertEqual(normal, cross)
                    else:
                        self.assertEqual(normal, up)

    def test_anchor_ids_survive_rotation_and_invert_z_while_frames_change(self) -> None:
        def frames(*extra: str) -> list[dict[str, object]]:
            args = l_turn_args("--scene-name", "stable_stair", *extra)
            layout = generator.build_layout("l-turn", args)
            return generator.semantic_anchor_frames(layout, args, False)

        baseline = frames()
        rotated = frames("--lower-entry-side", "east")
        mirrored = frames("--invert-z")
        self.assertEqual([frame["anchor_id"] for frame in baseline], [frame["anchor_id"] for frame in rotated])
        self.assertEqual([frame["anchor_id"] for frame in baseline], [frame["anchor_id"] for frame in mirrored])
        self.assertNotEqual(baseline, rotated)
        self.assertNotEqual(baseline, mirrored)
        for collection in (baseline, rotated, mirrored):
            shaft = next(frame for frame in collection if frame["role"] == "clear_volume")
            self.assertEqual(shaft["bounds"]["clear_bounds_xz"], [-2.0, -2.0, 2.0, 2.0])

    def test_shaft_face_anchors_require_generated_shaft_walls(self) -> None:
        open_args = l_turn_args("--scene-name", "shaft_test")
        open_layout = generator.build_layout("l-turn", open_args)
        open_frames = generator.semantic_anchor_frames(open_layout, open_args, False)
        self.assertFalse(any(frame["role"] == "shaft_face" for frame in open_frames))

        wall_args = l_turn_args(
            "--scene-name", "shaft_test", "--shaft-wall-bottom", "0.4", "--shaft-wall-top", "2.8"
        )
        wall_layout = generator.build_layout("l-turn", wall_args)
        wall_frames = generator.semantic_anchor_frames(wall_layout, wall_args, True)
        faces = [frame for frame in wall_frames if frame["role"] == "shaft_face"]
        self.assertEqual(len(faces), 4)
        self.assertEqual({frame["bounds"]["surface_side_id"] for frame in faces}, {
            "STAIRS-SHAFT-TEST/SHAFT/NORTH", "STAIRS-SHAFT-TEST/SHAFT/EAST",
            "STAIRS-SHAFT-TEST/SHAFT/SOUTH", "STAIRS-SHAFT-TEST/SHAFT/WEST",
        })

    def test_generation_report_publishes_versioned_anchor_frames_and_preserves_old_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code = generator.main([
                directory, "--shaft-width", "4.0", "--shaft-length", "4.0",
                "--floor-height", "3.2", "--layout", "l-turn", "--turn-direction", "right",
                "--scene-name", "report_stair", "--resource-dir", "res://generated/report_stair",
            ])
            self.assertEqual(code, 0)
            report = json.loads((Path(directory) / "generation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["generator_version"], "2.9.0")
            self.assertEqual(report["schema_version"], "1.1.0")
            self.assertTrue(report["anchor_frames"])
            self.assertTrue({
                "status", "scene", "resource_dir", "settings", "selected_layout", "counts",
                "geometry_validation", "materials", "assumptions", "warnings", "errors",
                "solutions", "layout_failures_before_selection", "files",
            }.issubset(report))

    def test_incomplete_shaft_bounds_remain_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()):
            code = generator.main([
                directory, "--shaft-width", "4.0", "--shaft-length", "4.0",
                "--floor-height", "3.2", "--layout", "l-turn",
                "--shaft-wall-bottom", "0.4", "--resource-dir", "res://generated/invalid",
            ])
            self.assertEqual(code, 2)
            report = json.loads((Path(directory) / "generation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "error")
            self.assertTrue(report["errors"])

    def test_finished_joint_meshes_keep_box_collision_and_external_visuals(self) -> None:
        args = l_turn_args()
        layout = generator.build_layout("l-turn", args)
        model, _ = generator.build_railing_blueprint(layout, args)
        boxes, report = generator.compile_railing_boxes(model, args)
        finished = [box for box in boxes if box.mesh_faces is not None]
        self.assertTrue(finished)
        self.assertEqual(report["joint_finish"]["ports_changed"], 0)
        self.assertEqual(report["joint_finish"]["connections_changed"], 0)
        with tempfile.TemporaryDirectory() as directory:
            files = generator.write_custom_meshes(Path(directory), boxes)
            self.assertEqual(len(files), len(finished))
            text = generator.scene_text(boxes, "res://unused", "finished", args, layout)
        self.assertIn('[ext_resource type="Mesh" path="res://unused/joint_meshes/', text)
        self.assertIn('[sub_resource type="BoxShape3D"', text)
        self.assertNotIn('CSG', text)

    def test_removed_legacy_joint_flag_is_rejected(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            l_turn_args("--railing-joint-rule", "legacy")

    def test_all_supported_layouts_build_valid_port_graphs(self) -> None:
        cases = (
            ("straight", "2.0", "6.0", "3.0", "auto", "auto"),
            ("l-turn", "4.0", "4.0", "3.2", "auto", "right"),
            ("u-turn", "2.7", "3.8", "3.0", "auto", "auto"),
            ("perimeter", "4.2", "4.2", "3.0", "3", "left"),
            ("perimeter", "4.0", "4.0", "3.0", "4", "right"),
        )
        for kind, width, length, height, perimeter, turn in cases:
            with self.subTest(kind=kind, perimeter=perimeter):
                args = generator.parser().parse_args(
                    [
                        "unused", "--shaft-width", width, "--shaft-length", length,
                        "--floor-height", height, "--layout", kind,
                        "--perimeter-flights", perimeter, "--turn-direction", turn,
                        "--railings", "both", "--resource-dir", "res://unused",
                    ]
                )
                layout = generator.build_layout(kind, args, None if perimeter == "auto" else int(perimeter))
                model, report = generator.build_railing_blueprint(layout, args)
                compiled = compile_model(model)
                self.assertTrue(report["ok"])
                self.assertTrue(validate_compiled(model, *compiled).ok)

    def test_l_turn_graph_has_expected_landing_and_planar_transition_joints(self) -> None:
        args = l_turn_args()
        layout = generator.build_layout("l-turn", args)
        model, report = generator.build_railing_blueprint(layout, args)
        joints = [connection for connection in model.connections.values() if connection.id.startswith("joint:")]
        self.assertEqual(len(joints), 10)
        self.assertTrue(report["ok"])
        self.assertTrue(validate_model(model).ok)

    def test_l_turn_inner_corner_uses_flat_adapters_and_one_four_socket_newel(self) -> None:
        args = l_turn_args()
        layout = generator.build_layout("l-turn", args)
        model, _ = generator.build_railing_blueprint(layout, args)
        newels = [post for post in model.posts.values() if post.metadata.get("corner_newel")]
        self.assertEqual(len(newels), 1)
        adapters = [beam for beam in model.beams.values() if beam.metadata.get("corner_transition")]
        self.assertEqual(len(adapters), 4)
        socket_connections = [
            connection
            for connection in model.connections.values()
            if connection.id.startswith("socket:" + newels[0].id + ":")
        ]
        self.assertEqual(len(socket_connections), 4)
        owners = {
            model.ports[port_id].owner_id
            for connection in socket_connections
            for port_id in connection.port_ids
            if model.ports[port_id].owner_id in model.beams
        }
        self.assertEqual(len(owners), 4)
        newel_base = newels[0].base
        regular_posts = [post for post in model.posts.values() if not post.metadata.get("corner_newel")]
        nearest_regular = min(
            ((post.base.x - newel_base.x) ** 2 + (post.base.z - newel_base.z) ** 2) ** 0.5
            for post in regular_posts
        )
        self.assertGreaterEqual(nearest_regular, args.target_tread - 1.0e-8)
        self.assertTrue(validate_model(model).ok)
        self.assertTrue(validate_compiled(model, *compile_model(model)).ok)

    def test_l_turn_compilation_is_deterministic_and_valid(self) -> None:
        args = l_turn_args()
        layout = generator.build_layout("l-turn", args)
        model, _ = generator.build_railing_blueprint(layout, args)
        first = compile_model(model)
        second = compile_model(model)
        self.assertEqual(first, second)
        self.assertTrue(validate_compiled(model, *first).ok)

    def test_perimeter_inner_turns_use_two_slope_to_flat_joints_and_one_flat_corner(self) -> None:
        for flight_count, turn in ((3, "left"), (4, "right")):
            with self.subTest(flight_count=flight_count):
                args = generator.parser().parse_args(
                    [
                        "unused", "--shaft-width", "4.4", "--shaft-length", "4.3",
                        "--floor-height", "3.2", "--layout", "perimeter",
                        "--perimeter-flights", str(flight_count), "--turn-direction", turn,
                        "--railings", "both", "--resource-dir", "res://unused",
                    ]
                )
                layout = generator.build_layout("perimeter", args, flight_count)
                model, report = generator.build_railing_blueprint(layout, args)
                adapters = [beam for beam in model.beams.values() if beam.metadata.get("corner_transition")]
                self.assertEqual(len(adapters), 4)
                self.assertFalse(any(post.metadata.get("corner_newel") for post in model.posts.values()))
                connected_port_sets = {
                    frozenset(connection.port_ids)
                    for connection in model.connections.values()
                }
                for tier in ("Handrail", "Midrail"):
                    flat_a = model.beams[f"Corner_01_{tier}_Flat_A"]
                    flat_b = model.beams[f"Corner_01_{tier}_Flat_B"]
                    self.assertIn(frozenset((flat_a.end_port, flat_b.start_port)), connected_port_sets)
                    for adapter_port in (flat_a.start_port, flat_b.end_port):
                        matching = [ports for ports in connected_port_sets if adapter_port in ports]
                        self.assertEqual(len(matching), 1)
                        other_port = next(port for port in matching[0] if port != adapter_port)
                        self.assertIn("flight", model.beams[model.ports[other_port].owner_id].metadata)
                self.assertTrue(report["ok"])
                self.assertTrue(validate_model(model).ok)
                self.assertTrue(validate_compiled(model, *compile_model(model)).ok)

    def test_perimeter_landing_gap_uses_explicit_case_three_transition_without_moving_slope_port(self) -> None:
        args = generator.parser().parse_args(
            [
                "unused", "--shaft-width", "4.4", "--shaft-length", "4.3",
                "--floor-height", "3.2", "--layout", "perimeter",
                "--perimeter-flights", "3", "--turn-direction", "left",
                "--railings", "both", "--lower-entry-side", "north",
                "--origin", "center", "--resource-dir", "res://unused",
            ]
        )
        layout = generator.build_layout("perimeter", args, 3)
        model, report = generator.build_railing_blueprint(layout, args)
        connected_port_sets = {
            frozenset(connection.port_ids)
            for connection in model.connections.values()
        }
        flight = layout.flights[2]
        dx, dz = flight.direction
        lateral = (-dz, dx)
        inset = max(args.rail_post_size, args.handrail_size) / 2.0
        edge_offset = flight.width / 2.0 - inset
        local_start = (
            flight.start[0] + lateral[0] * edge_offset,
            flight.start[1] + lateral[1] * edge_offset,
        )
        for tier in ("Handrail", "Midrail"):
            slope_port = f"Flight_03_Left_{tier}.start"
            landing_port = f"Landing_02_South_01_{tier}.start"
            transition_name = f"Flight_03_Left_{tier}_Start_Landing_Transition"
            transition = model.beams[transition_name]
            self.assertIn(
                frozenset((slope_port, transition.start_port)),
                connected_port_sets,
            )
            self.assertIn(
                frozenset((transition.end_port, landing_port)),
                connected_port_sets,
            )
            self.assertNotIn(frozenset((slope_port, landing_port)), connected_port_sets)
            expected_y = flight.base_y + args.rail_height * (1.0 if tier == "Handrail" else 0.5)
            expected_port = generator._v3(local_start, expected_y, args)
            self.assertLessEqual(model.ports[slope_port].frame.position.distance_to(expected_port), 1.0e-9)
            self.assertTrue(transition.metadata.get("landing_transition"))
            self.assertEqual(transition.metadata.get("transition_kind"), "slope_to_flat")
        self.assertTrue(report["ok"])
        self.assertTrue(validate_compiled(model, *compile_model(model)).ok)

    def test_u_turn_inner_guard_is_one_continuous_five_member_path_per_tier(self) -> None:
        args = generator.parser().parse_args(
            [
                "unused", "--shaft-width", "2.7", "--shaft-length", "3.8",
                "--floor-height", "3.0", "--layout", "u-turn",
                "--railings", "both", "--resource-dir", "res://unused",
            ]
        )
        layout = generator.build_layout("u-turn", args)
        model, _ = generator.build_railing_blueprint(layout, args)
        bridges = [beam for beam in model.beams.values() if beam.metadata.get("turn_bridge") and not beam.metadata.get("turn_transition")]
        transitions = [beam for beam in model.beams.values() if beam.metadata.get("turn_transition")]
        self.assertEqual(len(bridges), 2)
        self.assertEqual(len(transitions), 4)
        turn_connection_ports = {
            port_id
            for connection in model.connections.values()
            for port_id in connection.port_ids
            if "South_01" in port_id or port_id in {
                "Flight_01_Right_Handrail.end", "Flight_02_Right_Handrail.start",
                "Flight_01_Right_Midrail.end", "Flight_02_Right_Midrail.start",
            }
        }
        for transition in transitions:
            self.assertIn(transition.start_port, turn_connection_ports)
            self.assertIn(transition.end_port, turn_connection_ports)
        self.assertTrue(validate_model(model).ok)
        self.assertTrue(validate_compiled(model, *compile_model(model)).ok)

    def test_flight_waist_is_never_thinner_than_landing_slab(self) -> None:
        args = generator.parser().parse_args(
            [
                "unused", "--shaft-width", "4.0", "--shaft-length", "4.0",
                "--floor-height", "3.2", "--layout", "l-turn",
                "--step-thickness", "0.18", "--resource-dir", "res://unused",
            ]
        )
        layout = generator.build_layout("l-turn", args)
        boxes = []
        generator.add_step_boxes(boxes, layout, args)
        treads = [box for box in boxes if box.metadata.get("part") == "tread"]
        landings = [box for box in boxes if box.category == "Landings"]
        self.assertTrue(treads)
        self.assertTrue(landings)
        self.assertTrue(all(abs(box.size[1] - args.step_thickness) <= 1.0e-9 for box in treads))
        self.assertTrue(all(abs(box.size[1] - args.step_thickness) <= 1.0e-9 for box in landings))

    def test_every_post_top_is_vertically_above_its_explicit_base(self) -> None:
        args = l_turn_args()
        layout = generator.build_layout("l-turn", args)
        model, _ = generator.build_railing_blueprint(layout, args)
        for post in model.posts.values():
            top = model.ports[post.top_port].frame.position
            self.assertAlmostEqual(post.base.x, top.x)
            self.assertAlmostEqual(post.base.z, top.z)

    def test_posts_do_not_enter_supported_handrails(self) -> None:
        args = l_turn_args()
        layout = generator.build_layout("l-turn", args)
        model, _ = generator.build_railing_blueprint(layout, args)
        beams, posts = compile_model(model)
        compiled_beams = {beam.id: beam for beam in beams}
        for post in posts:
            connection = model.connections.get("support:" + post.id)
            if connection is None:
                continue
            support_port = next(model.ports[port_id] for port_id in connection.port_ids if model.ports[port_id].owner_id != post.id)
            beam = compiled_beams[support_port.owner_id]
            top = post.center.y + post.size.y / 2.0
            direction = beam.orientation.forward
            horizontal = (direction.x * direction.x + direction.z * direction.z) ** 0.5
            for sx in (-post.size.x / 2.0, post.size.x / 2.0):
                for sz in (-post.size.z / 2.0, post.size.z / 2.0):
                    corner_x = post.center.x + sx
                    corner_z = post.center.z + sz
                    along = (
                        (corner_x - beam.logical_start.x) * direction.x
                        + (corner_z - beam.logical_start.z) * direction.z
                    ) / (horizontal * horizontal)
                    axis_y = beam.logical_start.y + direction.y * along
                    underside = axis_y - beam.size.y / (2.0 * horizontal)
                    self.assertLessEqual(top, underside + 1.0e-8, (post.id, sx, sz, top, underside))

    def test_debug_geometry_has_separate_root_without_collision(self) -> None:
        args = l_turn_args("--debug-geometry")
        layout = generator.build_layout("l-turn", args)
        model, _ = generator.build_railing_blueprint(layout, args)
        boxes, _ = generator.compile_railing_boxes(model, args)
        text = generator.scene_text(boxes, "res://unused", "debug_test", args, layout)
        self.assertIn('[node name="Debug" type="Node3D" parent="."]', text)
        self.assertIn('parent="Debug/Ports"', text)
        self.assertNotIn('parent="Debug/Collision"', text)
        self.assertTrue(all(box.category == "Debug" for box in boxes if box.metadata.get("debug")))

    def test_shaft_walls_are_opt_in_and_fill_all_sides_only_between_bounds(self) -> None:
        args = l_turn_args()
        layout = generator.build_layout("l-turn", args)
        boxes = []
        generated, _ = generator.add_shaft_walls(boxes, layout, args)
        self.assertFalse(generated)
        self.assertEqual(boxes, [])

        wall_args = l_turn_args("--shaft-wall-bottom", "0.4", "--shaft-wall-top", "2.8")
        wall_layout = generator.build_layout("l-turn", wall_args)
        wall_boxes = []
        generator.add_step_boxes(wall_boxes, wall_layout, wall_args)
        model, _ = generator.build_railing_blueprint(wall_layout, wall_args)
        railing_boxes, _ = generator.compile_railing_boxes(model, wall_args)
        wall_boxes.extend(railing_boxes)
        generated, _ = generator.add_shaft_walls(wall_boxes, wall_layout, wall_args)
        self.assertTrue(generated)
        walls = [box for box in wall_boxes if box.category == "Shaft"]
        self.assertEqual(len(walls), 4)
        self.assertEqual({box.metadata["side"] for box in walls}, set(generator.SIDES))
        self.assertTrue(generator.validate_shaft_clearance(wall_boxes)["ok"])
        for wall in walls:
            self.assertAlmostEqual(wall.center[1] - wall.size[1] / 2.0, wall_args.shaft_wall_bottom)
            self.assertAlmostEqual(wall.center[1] + wall.size[1] / 2.0, wall_args.shaft_wall_top)
            self.assertTrue(wall.metadata["vertical_gap_only"])
            side = wall.metadata["side"]
            if side == "north":
                self.assertAlmostEqual(wall.center[2] - wall.size[2] / 2.0, wall_args.shaft_length / 2.0)
            elif side == "south":
                self.assertAlmostEqual(wall.center[2] + wall.size[2] / 2.0, -wall_args.shaft_length / 2.0)
            elif side == "east":
                self.assertAlmostEqual(wall.center[0] - wall.size[0] / 2.0, wall_args.shaft_width / 2.0)
            elif side == "west":
                self.assertAlmostEqual(wall.center[0] + wall.size[0] / 2.0, -wall_args.shaft_width / 2.0)

    def test_landing_guards_remain_when_gap_wall_does_not_cover_guard_height(self) -> None:
        open_args = l_turn_args()
        open_layout = generator.build_layout("l-turn", open_args)
        open_model, _ = generator.build_railing_blueprint(open_layout, open_args)
        expected = {
            beam.id
            for beam in open_model.beams.values()
            if "landing" in beam.metadata
        }

        partial_args = l_turn_args("--shaft-wall-bottom", "2.6", "--shaft-wall-top", "3.1")
        partial_layout = generator.build_layout("l-turn", partial_args)
        partial_model, _ = generator.build_railing_blueprint(partial_layout, partial_args)
        actual = {
            beam.id
            for beam in partial_model.beams.values()
            if "landing" in beam.metadata
        }
        self.assertTrue(expected)
        self.assertEqual(actual, expected)

    def test_inset_landing_edges_are_not_treated_as_wall_protected(self) -> None:
        args = generator.parser().parse_args(
            [
                "unused", "--shaft-width", "3.0", "--shaft-length", "4.6",
                "--floor-height", "3.2", "--layout", "u-turn",
                "--railings", "auto", "--shaft-wall-bottom", "0.45",
                "--shaft-wall-top", "2.75", "--wall-thickness", "0.20",
                "--lower-entry-side", "south", "--resource-dir", "res://unused",
            ]
        )
        layout = generator.build_layout("u-turn", args)
        model, _ = generator.build_railing_blueprint(layout, args)
        landing_sides = {
            beam.metadata["side"]
            for beam in model.beams.values()
            if "landing" in beam.metadata and not beam.metadata.get("terminal_adapter")
        }
        self.assertIn("west", landing_sides)
        self.assertIn("east", landing_sides)
        self.assertNotIn("north", landing_sides)
        self.assertTrue(validate_model(model).ok)
        self.assertTrue(validate_compiled(model, *compile_model(model)).ok)
        boxes = []
        generator.add_step_boxes(boxes, layout, args)
        railing_boxes, _ = generator.compile_railing_boxes(model, args)
        boxes.extend(railing_boxes)
        generated, _ = generator.add_shaft_walls(boxes, layout, args)
        self.assertTrue(generated)
        self.assertTrue(generator.validate_shaft_clearance(boxes)["ok"])

    def test_rotated_mirrored_shaft_walls_remain_clear(self) -> None:
        args = generator.parser().parse_args(
            [
                "unused", "--shaft-width", "6", "--shaft-length", "6", "--floor-height", "3.2",
                "--layout", "l-turn", "--turn-direction", "right", "--railings", "auto",
                "--lower-entry-side", "east", "--invert-z", "--origin", "south-west",
                "--shaft-wall-bottom", ".35", "--shaft-wall-top", "2.85", "--resource-dir", "res://unused",
            ]
        )
        layout = generator.build_layout("l-turn", args)
        boxes = []
        generator.add_step_boxes(boxes, layout, args)
        model, _ = generator.build_railing_blueprint(layout, args)
        railings, _ = generator.compile_railing_boxes(model, args)
        boxes.extend(railings)
        generator.add_shaft_walls(boxes, layout, args)
        self.assertTrue(generator.validate_shaft_clearance(boxes)["ok"])


if __name__ == "__main__":
    unittest.main()
