from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("resolve_bindings", ROOT / "resolve_bindings.py")
assert SPEC and SPEC.loader
RESOLVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESOLVER)


def limits(normal: tuple[float, float] = (0, 1), height: tuple[float, float] = (0, 3)) -> dict:
    return {
        "normal_offset_m": list(normal), "height_m": list(height),
        "rotation_deg": {"yaw": [-180, 180], "pitch": [-90, 90], "roll": [-180, 180]},
    }


def wall(anchor_id: str = "AF-WALL", length: float = 10) -> dict:
    return {
        "anchor_id": anchor_id, "type": "wall", "status": "active",
        "origin": [0, 0, 0.15], "forward": [1, 0, 0], "normal": [0, 0, 1], "up": [0, 1, 0],
        "bounds": {"length_m": length, "height_m": 3, "along_range_m": [0, length]}, "placement_limits": limits(),
    }


def floor() -> dict:
    return {
        "anchor_id": "AF-FLOOR", "type": "floor", "status": "active",
        "origin": [0, 0, 0], "forward": [1, 0, 0], "normal": [0, 1, 0], "up": [0, 1, 0],
        "bounds": {"polygon_xz": [[0, 0], [10, 0], [10, 8], [0, 8]], "holes_xz": [], "u_range_m": [0, 10], "v_range_m": [0, 8]},
        "placement_limits": limits((0, 0), (0, 0)),
    }


def placement(mode: str) -> dict:
    value = {
        "mode": mode, "normal_offset_m": 0, "height_m": 0,
        "rotation": {"representation": "euler_deg", "order": "YXZ", "yaw_pitch_roll": [0, 0, 0]},
    }
    if mode == "linear":
        value["linear"] = {"along": {"policy": "centered", "offset_m": 0}}
    else:
        value["surface"] = {"u": {"policy": "centered", "offset_m": 0}, "v": {"policy": "centered", "offset_m": 0}}
    return value


def binding(object_id: str, anchor_id: str, anchor_type: str, mode: str, footprint: list[float]) -> dict:
    return {
        "binding_id": "BIND-" + object_id, "object_ref": {"object_id": object_id},
        "anchor_ref": {"anchor_id": anchor_id, "expected_type": anchor_type},
        "placement": placement(mode), "footprint_m": footprint,
        "constraints": {"inside_anchor_bounds": True, "collision_free": True}, "on_missing_anchor": "block",
    }


class ResolveBindingTests(unittest.TestCase):
    def test_wall_and_surface_match_runtime_fixture_coordinates(self) -> None:
        mounted = binding("OBJ-W", "AF-WALL", "wall", "linear", [0.5, 0.4, 0.1])
        mounted["placement"].update({"normal_offset_m": 0.2, "height_m": 1.0})
        self.assertEqual(RESOLVER.resolve_binding(mounted, wall())["origin"], [5.0, 1.0, 0.35])
        grounded = binding("OBJ-F", "AF-FLOOR", "floor", "surface", [1, 1, 1])
        grounded["footprint_center_m"] = [0, 0.5, 0]
        resolved = RESOLVER.resolve_binding(grounded, floor())
        self.assertEqual(resolved["origin"], [5.0, 0.0, 4.0])
        self.assertEqual(resolved["basis_x"], [1.0, 0.0, 0.0])
        self.assertEqual(resolved["bounds"], {"min": [4.5, 0.0, 3.5], "max": [5.5, 1.0, 4.5]})

    def test_move_rotate_resize_changes_frame_not_ids(self) -> None:
        frame = wall(length=12)
        frame.update({"origin": [10, 0, 20], "forward": [0, 0, 1], "normal": [-1, 0, 0]})
        item = binding("OBJ-W", "AF-WALL", "wall", "linear", [0.5, 0.4, 0.1])
        item["placement"].update({"normal_offset_m": 0.2, "height_m": 1.0})
        item["placement"]["linear"]["along"] = {"policy": "normalized", "value": 0.25}
        resolved = RESOLVER.resolve_binding(item, frame)
        self.assertEqual(resolved["origin"], [9.8, 1.0, 23.0])
        self.assertEqual(item["binding_id"], "BIND-OBJ-W")
        self.assertEqual(frame["anchor_id"], "AF-WALL")

    def test_door_center_uses_symmetric_explicit_range(self) -> None:
        frame = wall("AF-DOOR", 1.2)
        frame["type"] = "door"
        frame["bounds"] = {"width_m": 1.2, "height_m": 2.4, "along_range_m": [-0.6, 0.6]}
        item = binding("OBJ-B", "AF-DOOR", "door", "linear", [0.2, 0.2, 0.2])
        item["placement"].update({"normal_offset_m": 0.5, "height_m": 1.0})
        self.assertEqual(RESOLVER.resolve_binding(item, frame)["origin"], [0.0, 1.0, 0.65])

    def test_footprint_and_rotation_cannot_escape(self) -> None:
        item = binding("OBJ-F", "AF-FLOOR", "floor", "surface", [1, 1, 1])
        item["placement"]["surface"]["u"] = {"policy": "from_start_m", "distance_m": 0.4}
        with self.assertRaisesRegex(ValueError, "footprint"):
            RESOLVER.resolve_binding(item, floor())
        item["placement"]["surface"]["u"] = {"policy": "from_start_m", "distance_m": 0.6}
        item["placement"]["rotation"]["yaw_pitch_roll"] = [45, 0, 0]
        with self.assertRaisesRegex(ValueError, "footprint"):
            RESOLVER.resolve_binding(item, floor())

    def test_hole_contained_inside_footprint_blocks(self) -> None:
        frame = floor()
        frame["bounds"]["holes_xz"] = [[[4.8, 3.8], [5.2, 3.8], [5.2, 4.2], [4.8, 4.2]]]
        with self.assertRaisesRegex(ValueError, "footprint"):
            RESOLVER.resolve_binding(binding("OBJ-F", "AF-FLOOR", "floor", "surface", [1, 1, 1]), frame)

    def test_removed_anchor_preserves_id_and_old_bounds(self) -> None:
        composition = {"infrastructure": [], "objects": [{"object_id": "OBJ-W", "bounds": {"min": [1, 1, 1], "max": [2, 2, 2]}}]}
        bindings = {"schema_id": "caretaker.object_bindings", "schema_version": "1.0.0", "map_id": "map", "sector_id": "sector", "bindings": [binding("OBJ-W", "AF-REMOVED", "wall", "linear", [1, 1, 1])]}
        anchors = {"map_id": "map", "sector_id": "sector", "generation_id": "new", "anchors": []}
        resolved = RESOLVER.resolve_document(composition, bindings, anchors)
        item = resolved["objects"][0]
        self.assertEqual(item["anchor_id"], "AF-REMOVED")
        self.assertEqual(item["bounds"], composition["objects"][0]["bounds"])

    def test_duplicates_and_unknown_policy_are_blocking(self) -> None:
        anchors = {"map_id": "map", "sector_id": "sector", "generation_id": "new", "anchors": [wall()]}
        duplicate = binding("OBJ-W", "AF-WALL", "wall", "linear", [1, 1, 1])
        bindings = {"schema_id": "caretaker.object_bindings", "schema_version": "1.0.0", "map_id": "map", "sector_id": "sector", "bindings": [duplicate, duplicate]}
        with self.assertRaisesRegex(ValueError, "binding IDs"):
            RESOLVER.resolve_document({"objects": [{"object_id": "OBJ-W", "bounds": {}}]}, bindings, anchors)
        duplicate["placement"]["linear"]["along"]["policy"] = "nearest"
        with self.assertRaisesRegex(ValueError, "unknown policy"):
            RESOLVER.resolve_binding(duplicate, wall())

    def test_nonfinite_and_missing_limits_are_blocking(self) -> None:
        item = binding("OBJ-W", "AF-WALL", "wall", "linear", [1, 1, 1])
        item["placement"]["height_m"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            RESOLVER.resolve_binding(item, wall())
        frame = wall()
        frame["placement_limits"].pop("rotation_deg")
        item["placement"]["height_m"] = 1
        with self.assertRaisesRegex(ValueError, "rotation limits"):
            RESOLVER.resolve_binding(item, frame)

    def test_document_error_names_the_failing_binding(self) -> None:
        item = binding("OBJ-W", "AF-WALL", "wall", "linear", [1, 1, 1])
        item["placement"]["linear"]["along"] = {"policy": "unknown"}
        bindings = {
            "schema_id": "caretaker.object_bindings", "schema_version": "1.0.0",
            "map_id": "map", "sector_id": "sector", "bindings": [item],
        }
        anchors = {"map_id": "map", "sector_id": "sector", "generation_id": "new", "anchors": [wall()]}
        composition = {"objects": [{"object_id": "OBJ-W", "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}}]}
        with self.assertRaisesRegex(ValueError, "BIND-OBJ-W: .*unknown policy"):
            RESOLVER.resolve_document(composition, bindings, anchors)


if __name__ == "__main__":
    unittest.main()
