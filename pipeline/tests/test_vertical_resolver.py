"""Stair generator geometry is derived from SVG markup, not hand-copied into the manifest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copy
import json
import tempfile
import unittest
from pathlib import Path

import vertical_resolver as resolver

RECT = (-55.5, 6.0, -49.5, 13.0)


def svg(shaft: tuple, kind: str, door_role: str, door_line: tuple, vertical_id: str = "VT-X", shafts: int = 1) -> str:
    x0, z0, x1, z1 = shaft
    rect = (
        f'<rect id="shaft{{n}}" data-godot-type="{kind}" data-vertical-id="{vertical_id}" data-vertical-role="shaft" '
        f'x="{x0}" y="{z0}" width="{x1 - x0}" height="{z1 - z0}"/>'
    )
    ax, ay, bx, by = door_line
    door = (
        f'<line id="door" data-godot-type="door" data-vertical-id="{vertical_id}" data-vertical-role="{door_role}" '
        f'x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}"/>'
    )
    body = "".join(rect.format(n=index) for index in range(shafts))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-60 0 20 20">{body}{door}</svg>'


class ResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sectors = {
            "U": {"sector_id": "U", "source_svg": "u.svg", "metric_settings": {"elevation_m": 0.0}, "shared_args": ["--wall-height", "3.4", "--ceiling-thickness", "0.2"]},
            "L": {"sector_id": "L", "source_svg": "l.svg", "metric_settings": {"elevation_m": -6.0}, "shared_args": ["--wall-height", "3.4", "--ceiling-thickness", "0.2"]},
        }
        self.entry = {
            "vertical_id": "VT-X", "source": "svg", "generator_id": "x",
            "levels": {"upper": {"sector_id": "U"}, "lower": {"sector_id": "L"}}, "args": ["--scene-name", "x", "--target-riser", "0.15", "--max-riser", "0.15", "--target-tread", "0.25", "--flight-gap", "0.7"],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, upper_door: tuple, lower_door: tuple, lower_rect: tuple = RECT, **extra) -> None:
        (self.root / "u.svg").write_text(svg(RECT, "floor-opening", "exit", upper_door, **extra), encoding="utf-8")
        (self.root / "l.svg").write_text(svg(lower_rect, "ceiling-opening", "entry", lower_door, **extra), encoding="utf-8")

    def resolve(self) -> dict:
        return resolver.resolve_stair(self.entry, self.sectors, self.root)

    @staticmethod
    def pairs(arguments: list) -> dict:
        values, index = {}, 0
        while index < len(arguments):
            flag = arguments[index]
            if index + 1 < len(arguments) and not arguments[index + 1].startswith("--"):
                values[flag] = arguments[index + 1]
                index += 2
            else:
                values[flag] = True  # a flag without a value, e.g. --no-shaft
                index += 1
        return values

    def test_u_turn_on_the_north_side_matches_the_hand_written_route_a_values(self) -> None:
        self.write((-52.15, 6, -50.65, 6), (-54.35, 6, -52.85, 6))
        result = self.resolve()
        values = self.pairs(result["args"])
        self.assertEqual(values["--shaft-width"], "5.98")
        self.assertEqual(values["--shaft-length"], "7")
        self.assertEqual((values["--floor-height"], values["--layout"], values["--stair-width"]), ("6", "u-turn", "1.5"))
        self.assertEqual((values["--lower-entry-side"], values["--upper-exit-side"]), ("north", "north"))
        self.assertIn("--no-shaft", result["args"])
        self.assertEqual(values["--scene-name"], "x")
        self.assertEqual(result["local_to_world"]["origin"], [-52.5, -6.0, 9.51])
        self.assertEqual(result["local_to_world"]["basis_x"], [-1, 0, 0])

    def test_east_and_west_swap_in_the_generator_frame_and_the_clearance_follows_the_passage(self) -> None:
        # the along-axis length is the shaft width (6 m): a smaller tread keeps the u-turn landing deep enough
        self.entry["args"] = ["--scene-name", "x", "--target-riser", "0.15", "--max-riser", "0.15", "--target-tread", "0.2", "--flight-gap", "0.7"]
        # flights sit at 9.5 +- 1.1; for an entry on the world east side the entry flight is on the north half
        self.write((-49.5, 9.85, -49.5, 11.35), (-49.5, 7.65, -49.5, 9.15))
        values = self.pairs(self.resolve()["args"])
        self.assertEqual((values["--lower-entry-side"], values["--upper-exit-side"]), ("west", "west"))
        self.assertEqual((values["--shaft-width"], values["--shaft-length"]), ("6", "6.98"))
        self.assertEqual(self.resolve()["local_to_world"]["origin"], [-52.51, -6.0, 9.5])

    def test_layout_follows_the_entry_and_exit_sides(self) -> None:
        self.write((-49.5, 7.0, -49.5, 8.5), (-54.35, 6, -52.85, 6))
        self.assertEqual(self.pairs(self.resolve()["args"])["--layout"], "l-turn")
        self.write((-54.35, 13, -52.85, 13), (-54.35, 6, -52.85, 6))
        self.assertEqual(self.pairs(self.resolve()["args"])["--layout"], "straight")

    def test_footprints_that_differ_between_levels_are_rejected(self) -> None:
        self.write((-52.15, 6, -50.65, 6), (-54.35, 6, -52.85, 6), lower_rect=(-55.5, 6.0, -49.5, 12.0))
        with self.assertRaisesRegex(resolver.VerticalError, "footprints differ"):
            self.resolve()

    def test_missing_duplicate_or_misplaced_markup_is_rejected(self) -> None:
        self.write((-52.15, 6, -50.65, 6), (-54.35, 6, -52.85, 6), shafts=2)
        with self.assertRaisesRegex(resolver.VerticalError, "exactly one shaft opening"):
            self.resolve()
        self.write((-52.15, 6, -50.65, 6), (-54.35, 6, -52.85, 6), vertical_id="OTHER")
        with self.assertRaisesRegex(resolver.VerticalError, "exactly one shaft opening"):
            self.resolve()
        self.write((-52.15, 9.0, -50.65, 9.0), (-54.35, 6, -52.85, 6))
        with self.assertRaisesRegex(resolver.VerticalError, "does not lie on a side"):
            self.resolve()

    def test_entry_and_exit_doors_must_be_equally_wide(self) -> None:
        self.write((-52.15, 6, -50.65, 6), (-54.35, 6, -52.35, 6))
        with self.assertRaisesRegex(resolver.VerticalError, "must match"):
            self.resolve()

    def test_geometric_arguments_may_not_be_set_in_the_manifest(self) -> None:
        self.write((-52.15, 6, -50.65, 6), (-54.35, 6, -52.85, 6))
        self.entry["args"] = ["--shaft-width", "6"]
        with self.assertRaisesRegex(resolver.VerticalError, "derived from the plan"):
            self.resolve()

    def test_sector_without_svg_verticals_is_returned_unchanged(self) -> None:
        sector = {"sector_id": "U", "vertical_generators": [{"generator_id": "x", "args": ["--a", "1"], "local_to_world": {}}]}
        self.assertIs(resolver.resolve_sector_verticals(sector, {"sectors": []}, self.root), sector)

    def test_resolution_does_not_modify_the_manifest_sector(self) -> None:
        self.write((-52.15, 6, -50.65, 6), (-54.35, 6, -52.85, 6))
        sector = {"sector_id": "U", "vertical_generators": [copy.deepcopy(self.entry)]}
        before = copy.deepcopy(sector)
        resolved = resolver.resolve_sector_verticals(sector, {"sectors": list(self.sectors.values())}, self.root)
        self.assertEqual(sector, before)
        self.assertIn("--shaft-width", resolved["vertical_generators"][0]["args"])


if __name__ == "__main__":
    unittest.main()
