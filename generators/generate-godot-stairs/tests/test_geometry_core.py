from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from stair_core import (  # noqa: E402
    BeamIntent,
    Blueprint,
    Connection,
    Frame,
    Mat3,
    Model,
    Port,
    NodeRequest,
    Vec3,
    angle_degrees,
    compile_model,
    compile_joint_hubs,
    compile_support_caps,
    connect,
    face_exit_distance,
    finish_beam_joints,
    lowest_beam_underside_over_footprint,
    resolve_nearby_nodes,
    validate_compiled,
    validate_model,
)


def frame(position: Vec3, forward: Vec3) -> Frame:
    return Frame.from_forward_up(position, forward)


class GeometryCoreTests(unittest.TestCase):
    def test_connect_aligns_position_and_opposes_forward(self) -> None:
        fixed = Port("fixed", "wall", frame(Vec3(4, 2, -1), Vec3(1, 0, 0)), Vec3(1, 1, 1), "socket")
        moving = Port("moving", "beam", frame(Vec3(0, 0, 2), Vec3(0, 0, 1)), Vec3(1, 1, 1), "plug")
        transform = connect(fixed, moving)
        placed = transform.frame(moving.frame)
        self.assertLess(placed.position.distance_to(fixed.frame.position), 1.0e-9)
        self.assertLess(abs(180.0 - angle_degrees(placed.orientation.forward, fixed.frame.orientation.forward)), 1.0e-9)

    def test_beam_compilation_is_deterministic(self) -> None:
        model = Model()
        start = Port("beam.start", "beam", frame(Vec3(0, 0, 0), Vec3(-1, 0, 0)), Vec3(.1, .1, .1), "end")
        end = Port("beam.end", "beam", frame(Vec3(2, 0, 0), Vec3(1, 0, 0)), Vec3(.1, .1, .1), "end")
        model.add_port(start)
        model.add_port(end)
        model.add_beam(BeamIntent("beam", start.id, end.id, .05, .05))
        first = compile_model(model)
        second = compile_model(model)
        self.assertEqual(first, second)
        self.assertAlmostEqual(first[0][0].size.z, 2.0)

    def test_mutual_connection_extends_both_beams_to_socket_boundary(self) -> None:
        model = Model()
        joint = Vec3(1, 0, 0)
        ports = (
            Port("a.start", "a", frame(Vec3(0, 0, 0), Vec3(-1, 0, 0)), Vec3(.05, .05, .05), "end"),
            Port("a.end", "a", frame(joint, Vec3(1, 0, 0)), Vec3(.05, .05, .05), "end"),
            Port("b.start", "b", frame(joint, Vec3(0, 0, -1)), Vec3(.05, .05, .05), "end"),
            Port("b.end", "b", frame(Vec3(1, 0, 1), Vec3(0, 0, 1)), Vec3(.05, .05, .05), "end"),
        )
        for port in ports:
            model.add_port(port)
        model.add_beam(BeamIntent("a", "a.start", "a.end", .05, .05))
        model.add_beam(BeamIntent("b", "b.start", "b.end", .05, .05))
        model.add_connection(Connection("corner", ("a.end", "b.start")))
        beams, _ = compile_model(model)
        by_id = {beam.id: beam for beam in beams}
        self.assertAlmostEqual(by_id["a"].end_extension, .025)
        self.assertAlmostEqual(by_id["b"].start_extension, .025)

    def test_sloped_face_insertion_stops_before_a_corner_crosses_flat_receiver(self) -> None:
        joint = Vec3(0, 1, 0)
        slope = frame(joint, Vec3(1, .5, 0))
        flat = frame(joint, Vec3(-1, 0, 0))
        distance = face_exit_distance(flat, Vec3(.06, .06, .06), slope, .06, .06, slope.orientation.forward)
        self.assertGreater(distance, 0.0)
        self.assertLess(distance, .06 / abs(flat.orientation.transpose_apply(slope.orientation.forward).z))
        for width_sign in (-1.0, 1.0):
            for height_sign in (-1.0, 1.0):
                corner = (
                    slope.position
                    + slope.orientation.right * (.06 * width_sign)
                    + slope.orientation.up * (.06 * height_sign)
                    + slope.orientation.forward * distance
                )
                local = flat.orientation.transpose_apply(corner - flat.position)
                if abs(local.x) <= .06 + 1.0e-9 and abs(local.y) <= .06 + 1.0e-9:
                    self.assertLessEqual(abs(local.z), .06 + 1.0e-9)

    def test_spatial_slope_corner_uses_two_planar_transitions(self) -> None:
        blueprint = Blueprint()
        anchor = Vec3(0, 1, 0)
        first_transition = Vec3(-.18, 1, 0)
        second_transition = Vec3(0, 1, .18)
        blueprint.add_beam("a", Vec3(-1, .5, 0), first_transition, .12, .12, None, "first")
        blueprint.add_beam("flat_a", first_transition, anchor, .12, .12, "first", "turn")
        blueprint.add_beam("flat_b", anchor, second_transition, .12, .12, "turn", "second")
        blueprint.add_beam("b", second_transition, Vec3(0, 1.5, 1), .12, .12, "second", None)
        model = blueprint.finish()
        joints = compile_joint_hubs(model)
        beams, posts = compile_model(model)
        self.assertEqual(joints, [])
        self.assertTrue(validate_model(model).ok)
        self.assertEqual(posts, [])
        by_id = {beam.id: beam for beam in beams}
        self.assertGreater(by_id["a"].end_extension, 0.0)
        self.assertGreater(by_id["flat_a"].start_extension, 0.0)
        self.assertGreater(by_id["flat_a"].end_extension, 0.0)
        self.assertGreater(by_id["flat_b"].start_extension, 0.0)
        self.assertGreater(by_id["flat_b"].end_extension, 0.0)
        self.assertGreater(by_id["b"].start_extension, 0.0)
        self.assertTrue(validate_compiled(model, beams, posts).ok)

    def test_direct_spatial_slope_corner_is_rejected(self) -> None:
        blueprint = Blueprint()
        blueprint.add_beam("a", Vec3(-1, -.5, 0), Vec3(0, 0, 0), .12, .12, None, "joint")
        blueprint.add_beam("b", Vec3(0, 0, 0), Vec3(0, .5, 1), .12, .12, "joint", None)
        result = validate_model(blueprint.finish())
        self.assertFalse(result.ok)
        self.assertIn("ambiguous_spatial_slope_joint", {issue.code for issue in result.issues})

    def test_slope_to_flat_joint_does_not_emit_redundant_hub(self) -> None:
        blueprint = Blueprint()
        blueprint.add_beam("slope", Vec3(-1, -.5, 0), Vec3(0, 0, 0), .12, .12, None, "joint")
        blueprint.add_beam("flat", Vec3(0, 0, 0), Vec3(1, 0, 0), .12, .12, "joint", None)
        self.assertEqual(compile_joint_hubs(blueprint.finish()), [])

    def test_joint_finishing_unions_slope_to_flat_without_changing_ports(self) -> None:
        blueprint = Blueprint()
        blueprint.add_beam("slope", Vec3(-1, -.5, 0), Vec3(0, 0, 0), .12, .12, None, "joint")
        blueprint.add_beam("flat", Vec3(0, 0, 0), Vec3(1, 0, 0), .12, .12, "joint", None)
        model = blueprint.finish()
        ports_before = dict(model.ports)
        connections_before = dict(model.connections)
        beams, _posts = compile_model(model)

        first, first_report = finish_beam_joints(model, beams)
        second, second_report = finish_beam_joints(model, beams)

        self.assertEqual(first, second)
        self.assertEqual(first_report, second_report)
        self.assertEqual(model.ports, ports_before)
        self.assertEqual(model.connections, connections_before)
        self.assertIn("slope", first_report.clipped_elements)
        operation = next(item for item in first_report.operations if item["operation"] == "surface_union")
        self.assertEqual(operation["through_id"], "flat")
        self.assertEqual(operation["member_ids"], ["flat", "slope"])
        self.assertGreaterEqual(len(first["slope"].vertices), 6)
        self.assertTrue(first["flat"].faces)

    def test_joint_finishing_uses_one_through_member_at_flat_corner(self) -> None:
        blueprint = Blueprint()
        blueprint.add_beam("long", Vec3(-1, 0, 0), Vec3(0, 0, 0), .12, .12, None, "corner")
        blueprint.add_beam("short", Vec3(0, 0, 0), Vec3(0, 0, .5), .12, .12, "corner", None)
        model = blueprint.finish()
        beams, _posts = compile_model(model)
        finished, report = finish_beam_joints(model, beams)

        operation = next(item for item in report.operations if item["operation"] == "surface_union")
        self.assertEqual(operation["through_id"], "long")
        self.assertEqual(operation["member_ids"], ["long", "short"])
        self.assertTrue(finished["long"].faces)
        self.assertTrue(finished["short"].faces)

    def test_sloped_support_compiles_full_section_two_port_block(self) -> None:
        blueprint = Blueprint()
        start, end = Vec3(-1, 1, 0), Vec3(1, 2, 0)
        blueprint.add_beam("rail", start, end, .12, .12, None, None)
        support = lowest_beam_underside_over_footprint(Vec3(0, 1.5, 0), end - start, .12, .06, .06)
        blueprint.add_post_support("post", "rail", Vec3(0, 0, 0), support, .12, .12)
        model = blueprint.finish()
        beams, posts = compile_model(model)
        caps = compile_support_caps(model, beams, posts)
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0].size, Vec3(.12, .12, .12))
        self.assertAlmostEqual(caps[0].center.y, support.y + .06)
        self.assertEqual(caps[0].high_direction, Vec3(1, 0, 0))
        self.assertEqual(caps[0].metadata["connection_ports"], ("post.top", "rail.support.post"))

    def test_chain_and_closed_loop_validate(self) -> None:
        model = Model()
        points = (Vec3(0, 0, 0), Vec3(1, 0, 0), Vec3(1, 0, 1), Vec3(0, 0, 1))
        for index, point in enumerate(points):
            next_point = points[(index + 1) % len(points)]
            direction = (next_point - point).normalized()
            previous = points[(index - 1) % len(points)]
            incoming = (point - previous).normalized()
            model.add_port(Port(f"b{index}.start", f"b{index}", frame(point, incoming * -1), Vec3(.05, .05, .05), "end"))
            model.add_port(Port(f"b{index}.end", f"b{index}", frame(next_point, direction), Vec3(.05, .05, .05), "end"))
            model.add_beam(BeamIntent(f"b{index}", f"b{index}.start", f"b{index}.end", .05, .05))
        for index in range(len(points)):
            model.add_connection(Connection(f"j{index}", (f"b{(index - 1) % 4}.end", f"b{index}.start")))
        result = validate_model(model)
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(result.connection_count, 4)

    def test_validation_reports_distance_angle_and_ids(self) -> None:
        model = Model()
        model.add_port(Port("a", "beam-a", frame(Vec3(0, 0, 0), Vec3(1, 0, 0)), Vec3(.1, .1, .1), "end"))
        model.add_port(Port("b", "beam-b", frame(Vec3(.02, 0, 0), Vec3(0, 0, 1)), Vec3(.1, .1, .1), "end"))
        model.add_connection(Connection("bad-joint", ("a", "b")))
        result = validate_model(model)
        self.assertFalse(result.ok)
        self.assertEqual({issue.code for issue in result.issues}, {"port_position_mismatch", "port_orientation_mismatch"})
        self.assertTrue(all(issue.connection_id == "bad-joint" for issue in result.issues))
        self.assertAlmostEqual(result.maximum_position_error, .02)
        self.assertAlmostEqual(result.maximum_angle_error, 90.0)

    def test_blueprint_builds_explicit_joint_and_terminal_support(self) -> None:
        blueprint = Blueprint()
        blueprint.add_beam(
            "rail",
            Vec3(0, 1, 0),
            Vec3(1, 1, 0),
            .05,
            .05,
            None,
            "joint:end",
        )
        blueprint.add_beam(
            "landing",
            Vec3(1, 1, 0),
            Vec3(1, 1, 1),
            .05,
            .05,
            "joint:end",
            None,
        )
        blueprint.add_terminal_post(
            "post",
            "rail.start",
            Vec3(0, 0, 0),
            .05,
            .05,
        )
        model = blueprint.finish()
        self.assertIn("joint:end", model.connections)
        self.assertIn("terminal:post", model.connections)
        self.assertIn("support:post", model.connections)
        self.assertIn("post.terminal_adapter", model.beams)
        result = validate_model(model)
        self.assertTrue(result.ok, result.issues)
        beams, posts = compile_model(model)
        self.assertGreater(next(beam for beam in beams if beam.id == "rail").start_extension, 0.0)
        self.assertAlmostEqual(posts[0].size.y, .975)
        self.assertTrue(validate_compiled(model, beams, posts).ok)

    def test_sloped_terminal_posts_use_planar_adapters_at_both_ends(self) -> None:
        blueprint = Blueprint()
        start = Vec3(0, 1, 0)
        end = Vec3(2, 2, 0)
        blueprint.add_beam("rail", start, end, .12, .12, None, None)
        blueprint.add_terminal_post("start_post", "rail.start", Vec3(0, 0, 0), .12, .12)
        blueprint.add_terminal_post("end_post", "rail.end", Vec3(2, 0, 0), .12, .12)
        model = blueprint.finish()
        beams, posts = compile_model(model)

        self.assertTrue(validate_model(model).ok)
        self.assertTrue(validate_compiled(model, beams, posts).ok)
        self.assertEqual(compile_support_caps(model, beams, posts), [])
        by_id = {post.id: post for post in posts}
        self.assertAlmostEqual(by_id["start_post"].center.y + by_id["start_post"].size.y * .5, start.y - .06)
        self.assertAlmostEqual(by_id["end_post"].center.y + by_id["end_post"].size.y * .5, end.y - .06)
        for adapter_id in ("start_post.terminal_adapter", "end_post.terminal_adapter"):
            adapter = model.beams[adapter_id]
            direction = (
                model.ports[adapter.end_port].frame.position
                - model.ports[adapter.start_port].frame.position
            ).normalized()
            self.assertAlmostEqual(direction.y, 0.0)
        self.assertGreater(beams[0].start_extension, 0.0)
        self.assertGreater(beams[0].end_extension, 0.0)

    def test_zero_length_direction_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "zero-length"):
            Mat3.from_forward_up(Vec3(0, 0, 0))

    def test_sloped_beam_support_uses_lowest_footprint_corner(self) -> None:
        support = lowest_beam_underside_over_footprint(
            Vec3(0, 1, 0),
            Vec3(0, 1, 2),
            .05,
            .025,
            .025,
        )
        direction = Vec3(0, 1, 2).normalized()
        horizontal = math.hypot(direction.x, direction.z)
        centre_underside = 1.0 - .05 / (2.0 * horizontal)
        self.assertLess(support.y, centre_underside)
        for z in (-.025, .025):
            axis_y = 1.0 + direction.y * z / direction.z
            underside = axis_y - .05 / (2.0 * horizontal)
            self.assertLessEqual(support.y, underside + 1.0e-9)

    def test_topology_resolution_is_transitive_and_deterministic(self) -> None:
        requests = [
            NodeRequest("landing", "upper", Vec3(.024, 1, 0), 1),
            NodeRequest("flight", "upper", Vec3(0, 1, 0), 0),
            NodeRequest("corner", "upper", Vec3(.048, 1, 0), 1),
        ]
        first = resolve_nearby_nodes(requests, .025)
        second = resolve_nearby_nodes(list(reversed(requests)), .025)
        self.assertEqual(first, second)
        mapping, nodes = first
        self.assertEqual(len(nodes), 1)
        self.assertEqual(mapping["landing"], Vec3(0, 1, 0))

    def test_compiled_validation_rejects_unplanned_crossing(self) -> None:
        blueprint = Blueprint()
        blueprint.add_beam("a", Vec3(-1, 0, 0), Vec3(1, 0, 0), .1, .1, None, None)
        blueprint.add_beam("b", Vec3(0, 0, -1), Vec3(0, 0, 1), .1, .1, None, None)
        model = blueprint.finish()
        beams, posts = compile_model(model)
        result = validate_compiled(model, beams, posts)
        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].code, "unplanned_intersection")


if __name__ == "__main__":
    unittest.main()
