"""Wall finishes by side (caretaker #297): the converter writes each wall as surfaces by the room in front of each face,
room_finishes.py gives every side its room's class, a niche is cut with a thin back part and a lining surface."""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CONVERTER = _load("svg_to_godot3d_297", ROOT / "generators/svg-plan-to-godot/scripts/svg_to_godot3d.py")
FINISHES = _load("room_finishes_297", ROOT / "pipeline/room_finishes.py")

# two rooms side by side (A west, B east) sharing the wall x=4; A also has a niche in its north wall; a neighbour's room
# outline lies south of B's south wall
SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 10 8">
  <rect id="a--floor" data-godot-type="floor" data-space-id="A" data-finish-class="utility" x="0" y="0" width="4" height="4" />
  <rect id="b--floor" data-godot-type="floor" data-space-id="B" data-finish-class="medical" x="4" y="0" width="4" height="4" />
  <rect id="n--side" data-godot-type="wall-side-room" data-finish-class="domestic" x="4" y="4" width="4" height="2" />
  <line id="a--north" data-godot-type="wall" data-wall-rooms="A" x1="0" y1="0" x2="4" y2="0" />
  <line id="b--north" data-godot-type="wall" data-wall-rooms="B" x1="4" y1="0" x2="8" y2="0" />
  <line id="a--south" data-godot-type="wall" data-wall-rooms="A" x1="0" y1="4" x2="4" y2="4" />
  <line id="b--south" data-godot-type="wall" data-wall-rooms="B" x1="4" y1="4" x2="8" y2="4" />
  <line id="a--west" data-godot-type="wall" data-wall-rooms="A" x1="0" y1="0" x2="0" y2="4" />
  <line id="a--east" data-godot-type="wall" data-wall-rooms="A|B" x1="4" y1="0" x2="4" y2="4" />
  <line id="b--east" data-godot-type="wall" data-wall-rooms="B" x1="8" y1="0" x2="8" y2="4" />
  <line id="a--niche" data-godot-type="niche" data-niche-room="a--floor" data-niche-depth="0.2" data-opening-bottom="0.5" data-opening-height="0.6" x1="1.7" y1="0" x2="2.3" y2="0" />
</svg>
"""
CLASSES = {name: {"priority": index, "materials": {surface: f"res://m/{name}_{surface}.tres" for surface in ("wall", "floor", "ceiling")}} for index, name in enumerate(["medical", "domestic", "utility"], 1)}
REGISTRY = {"classes": CLASSES, "niche_material": "res://m/niche.tres"}


def _walls(scene: str) -> list[dict[str, object]]:
    found = []
    for block in re.split(r"(?m)^(?=\[)", scene):
        node = re.match(r'\[node name="([^"]+)" type="MeshInstance3D" parent="Walls/Visual"\]', block)
        if not node:
            continue
        keys = re.findall(r'"([^"]*)"', re.search(r"wall_surfaces = PackedStringArray\((.*)\)", block).group(1))
        values = re.search(r"finish_classes = PackedStringArray\((.*)\)", block)
        found.append({"name": node.group(1), "source": re.search(r'source_id = "([^"]*)"', block).group(1), "keys": keys,
                      "classes": re.findall(r'"([^"]*)"', values.group(1)) if values else [], "block": block})
    return found


class WallSideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        source = Path(cls.directory.name) / "plan.svg"
        source.write_text(SVG, encoding="utf-8")
        output = Path(cls.directory.name) / "out"
        code = CONVERTER.main([str(source), str(output), "--scene-name", "plan", "--resource-dir", "res://out", "--scale", "1", "--wall-height", "3", "--wall-thickness", "0.3", "--no-ceilings"])
        assert code == 0, (output / "conversion_report.json").read_text(encoding="utf-8")
        cls.raw = (output / "plan.tscn").read_text(encoding="utf-8")
        cls.output = output
        plan = FINISHES.plan_finishes_from_text(SVG, REGISTRY)
        cls.scene, cls.assignments = FINISHES.render(cls.raw, "plan.tscn", plan, REGISTRY)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.directory.cleanup()

    def wall(self, source: str) -> dict[str, object]:
        return next(item for item in _walls(self.scene) if item["source"] == source)

    def test_shared_wall_has_one_surface_per_side_and_ends(self) -> None:
        wall = self.wall("a--east")
        self.assertEqual(sorted(wall["keys"]), sorted(["side:+:a--floor", "side:-:b--floor", "ends"]) if "side:+:a--floor" in wall["keys"]
                         else sorted(["side:-:a--floor", "side:+:b--floor", "ends"]))

    def test_each_side_takes_its_room_class_and_ends_the_wall_class(self) -> None:
        wall = self.wall("a--east")
        by_key = dict(zip(wall["keys"], wall["classes"]))
        sides = {key.split(":", 2)[2]: value for key, value in by_key.items() if key.startswith("side:")}
        self.assertEqual(sides, {"a--floor": "utility", "b--floor": "medical"})
        self.assertEqual(by_key["ends"], "medical")  # the wall's own class: the smaller priority of its rooms

    def test_each_surface_points_at_its_material(self) -> None:
        wall = self.wall("a--east")
        for index, value in enumerate(wall["classes"]):
            self.assertIn(f'surface_material_override/{index} = ExtResource("finish_{value}_wall")', wall["block"])
        self.assertNotIn("\nmaterial_override", wall["block"])
        self.assertIn('[ext_resource type="Material" path="res://m/utility_wall.tres" id="finish_utility_wall"]', self.scene)

    def test_neighbour_room_outline_finishes_its_side(self) -> None:
        # the south wall east of the crossing: B's room on one side, the neighbour's room outline on the other
        wall = next(item for item in _walls(self.scene) if "b--south" in str(item["source"]).split("|") and any("b--floor" in key for key in item["keys"]))
        by_key = dict(zip(wall["keys"], wall["classes"]))
        sides = {key.split(":", 2)[2]: value for key, value in by_key.items() if key.startswith("side:")}
        self.assertEqual(sides, {"b--floor": "medical", "n--side": "domestic"})

    def test_side_without_a_room_keeps_the_wall_class(self) -> None:
        wall = self.wall("a--west")
        by_key = dict(zip(wall["keys"], wall["classes"]))
        self.assertEqual(sorted(value for key, value in by_key.items() if key.startswith("side:")), ["utility", "utility"])
        self.assertIn("side:+:", wall["keys"] + ["side:+:"])

    def test_niche_cuts_the_wall_and_leaves_a_back_part_lined_dark(self) -> None:
        pieces = [item for item in _walls(self.scene) if item["source"] == "a--north" or item["source"].startswith("a--north|")]
        lined = [item for item in pieces if "niche" in item["keys"]]
        self.assertEqual(len(lined), 5)  # left and right reveals, sill, lintel, back part
        for item in lined:
            index = item["keys"].index("niche")
            self.assertIn(f'surface_material_override/{index} = ExtResource("finish_niche")', item["block"])
        back = [item for item in lined if not any(key.startswith("side:") and key.endswith("a--floor") for key in item["keys"])]
        self.assertEqual(len(back), 1, "only the back part has no face towards room A")
        report = (self.output / "conversion_report.json").read_text(encoding="utf-8")
        self.assertIn('"wall_niches": 1', report)

    def test_rendering_is_idempotent_and_keeps_old_single_material_walls(self) -> None:
        plan = FINISHES.plan_finishes_from_text(SVG, REGISTRY)
        again, _ = FINISHES.render(self.scene, "plan.tscn", plan, REGISTRY)
        self.assertEqual(again, self.scene)
        old = ('[ext_resource type="Material" path="res://p/wall_material.tres" id="3_wall_material"]\n\n'
               '[node name="Wall_001" type="MeshInstance3D" parent="Walls/Visual"]\nmesh = ExtResource("m")\n'
               'material_override = ExtResource("3_wall_material")\nmetadata/source_id = "a--east"\n')
        rendered, _ = FINISHES.render(old, "old.tscn", plan, REGISTRY)
        self.assertIn('material_override = ExtResource("finish_medical_wall")', rendered)


class StripTests(unittest.TestCase):
    def test_face_hidden_in_a_crossing_wall_joins_its_neighbour(self) -> None:
        wall = CONVERTER.Segment((-0.15, 0.0), (8.15, 0.0), "w", 3.0, 0.3, 0.0)
        rooms = [("a", [(0, 0), (4, 0), (4, 4), (0, 4)], 0.0, 1), ("b", [(4, 0), (8, 0), (8, 4), (4, 4)], 0.0, 1),
                 ("c", [(-4, 0), (0, 0), (0, 4), (-4, 4)], 0.0, 1)]
        strips = [strip for strip in CONVERTER.wall_side_strips(wall, rooms) if strip[0] == 1]
        self.assertEqual([room for _side, _start, _end, room in strips], ["a", "b"])
        self.assertAlmostEqual(strips[0][1], -4.15)
        self.assertAlmostEqual(strips[-1][2], 4.15)

    def test_room_of_the_plan_wins_over_a_neighbour_outline(self) -> None:
        wall = CONVERTER.Segment((0.0, 0.0), (4.0, 0.0), "w", 3.0, 0.3, 0.0)
        square = [(0, 0), (4, 0), (4, 4), (0, 4)]
        strips = CONVERTER.wall_side_strips(wall, [("own", square, 0.0, 1), ("outline", square, 0.0, 0)])
        self.assertIn((1, -2.0, 2.0, "own"), strips)

    def test_upper_floor_rooms_do_not_finish_a_lower_wall(self) -> None:
        wall = CONVERTER.Segment((0.0, 0.0), (4.0, 0.0), "w", 3.0, 0.3, 0.0)
        square = [(0, 0), (4, 0), (4, 4), (0, 4)]
        strips = CONVERTER.wall_side_strips(wall, [("upper", square, 3.0, 1)])
        self.assertEqual({room for *_rest, room in strips}, {""})


if __name__ == "__main__":
    unittest.main()
