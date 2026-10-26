"""The resolver's side mapping must hold on the real stair generator (all layouts and sides).

Needs STAIR_TOOL_ROOT (generate_godot_stairs >= 2.9.0); skipped otherwise. For every combination of
entry and exit side the plan is resolved, the generator is run with the derived arguments, the
generated entry/exit frames are moved to world space with the derived transform, and they must lie
on the marked sides of the shaft and point the right way.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import regenerate_sector as backend
import vertical_resolver as resolver

STAIR_TOOL_ROOT = os.environ.get("STAIR_TOOL_ROOT")
RECT = (0.0, 0.0, 10.0, 10.0)
POSITION_TOLERANCE_M = 0.05
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
SIDES = ("north", "east", "south", "west")


def door_line(side: str, width: float = 1.5, shift: float = 0.0) -> tuple:
    x0, z0, x1, z1 = RECT
    centre_x, centre_z = (x0 + x1) / 2, (z0 + z1) / 2
    if side in ("north", "south"):
        centre_x += shift
    else:
        centre_z += shift
    if side == "north":
        return (centre_x - width / 2, z0, centre_x + width / 2, z0)
    if side == "south":
        return (centre_x - width / 2, z1, centre_x + width / 2, z1)
    if side == "west":
        return (x0, centre_z - width / 2, x0, centre_z + width / 2)
    return (x1, centre_z - width / 2, x1, centre_z + width / 2)


def plan(kind: str, role: str, door: tuple) -> str:
    x0, z0, x1, z1 = RECT
    ax, ay, bx, by = door
    return (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<rect id="shaft" data-godot-type="{kind}" data-vertical-id="VT-X" data-vertical-role="shaft" x="{x0}" y="{z0}" width="{x1 - x0}" height="{z1 - z0}"/>'
        f'<line id="door" data-godot-type="door" data-vertical-id="VT-X" data-vertical-role="{role}" x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}"/>'
        "</svg>"
    )


@unittest.skipUnless(STAIR_TOOL_ROOT, "STAIR_TOOL_ROOT is not configured")
class StairOrientationTests(unittest.TestCase):
    # Generator defaults for landing depth and flight gap are used on purpose: `--landing-depth 2` makes the
    # L-turn railing validation of generate_godot_stairs 2.9.0 fail for every size (1.5 mm beam overlap).
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.script = backend.find_script(Path(STAIR_TOOL_ROOT).resolve(), "generate_godot_stairs.py")
        cls.sectors = {
            "U": {"sector_id": "U", "source_svg": "u.svg", "metric_settings": {"elevation_m": 0.0}, "shared_args": ["--wall-height", "1.5", "--ceiling-thickness", "0.2"]},
            "L": {"sector_id": "L", "source_svg": "l.svg", "metric_settings": {"elevation_m": -3.0}, "shared_args": ["--wall-height", "1.5", "--ceiling-thickness", "0.2"]},
        }
        cls.entry = {
            "vertical_id": "VT-X", "source": "svg", "levels": {"upper": {"sector_id": "U"}, "lower": {"sector_id": "L"}},
            "args": ["--target-riser", "0.15", "--max-riser", "0.15", "--target-tread", "0.25", "--min-tread", "0.25",
                     "--minimum-headroom", "2.4", "--railings", "auto",
                     "--wall-thickness", "0.09", "--scene-name", "x"],
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def generate(self, entry_side: str, exit_side: str) -> tuple[list, dict, dict]:
        # A u-turn has two flights side by side: the doors sit on their own flights (width 1.5 + default gap 0.1 -> +-0.8 m from the centre).
        shift = resolver.ENTRY_HALF[entry_side] * 0.8 if entry_side == exit_side else 0.0
        (self.root / "u.svg").write_text(plan("floor-opening", "exit", door_line(exit_side, shift=-shift)), encoding="utf-8")
        (self.root / "l.svg").write_text(plan("ceiling-opening", "entry", door_line(entry_side, shift=shift)), encoding="utf-8")
        resolved = resolver.resolve_stair(self.entry, self.sectors, self.root)
        output = self.root / f"out-{entry_side}-{exit_side}"
        process = subprocess.run(
            [sys.executable, str(self.script), str(output), *resolved["args"], "--resource-dir", f"res://stairs/{entry_side}_{exit_side}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(process.returncode, 0, f"{entry_side}->{exit_side}: {process.stdout}{process.stderr}")
        report = json.loads((output / "generation_report.json").read_text(encoding="utf-8"))
        frames = [backend.transform_frame(frame, resolved["local_to_world"]) for frame in report["anchor_frames"]]
        return frames, resolved, report

    def check(self, entry_side: str, exit_side: str) -> None:
        frames, resolved, _ = self.generate(entry_side, exit_side)
        entry = next(frame for frame in frames if frame["type"] == "stair_entry")
        exit_ = next(frame for frame in frames if frame["type"] == "stair_exit")
        label = f"{entry_side}->{exit_side} ({resolved['summary']['layout']})"
        # Unit step from a side into the shaft in world axes (x, z); +z is south.
        inward = {"north": (0, 1), "south": (0, -1), "west": (1, 0), "east": (-1, 0)}
        entry_dot = entry["forward"][0] * inward[entry_side][0] + entry["forward"][2] * inward[entry_side][1]
        exit_dot = exit_["forward"][0] * inward[exit_side][0] + exit_["forward"][2] * inward[exit_side][1]
        self.assertGreater(entry_dot, 0.9, f"{label}: entry does not lead into the shaft from the {entry_side} side: {entry['forward']}")
        self.assertLess(exit_dot, -0.9, f"{label}: exit does not lead out through the {exit_side} side: {exit_['forward']}")
        if exit_side != entry_side:
            # The exit lies further towards its side than the entry does.
            outward = (-inward[exit_side][0], -inward[exit_side][1])
            shift = (exit_["origin"][0] - entry["origin"][0]) * outward[0] + (exit_["origin"][2] - entry["origin"][2]) * outward[1]
            self.assertGreater(shift, 0.5, f"{label}: exit is not on the {exit_side} side of the entry ({entry['origin']} -> {exit_['origin']})")
        if exit_side == entry_side:
            # A U-turn returns to the same edge: entry and exit share the passage-axis coordinate.
            axis = 2 if entry_side in ("north", "south") else 0
            self.assertAlmostEqual(entry["origin"][axis], exit_["origin"][axis], delta=0.02, msg=f"{label}: entry and exit are on different edges")
            # The generator puts the entry flight on the half the resolver (and the door check) expects.
            lateral = 0 if entry_side in ("north", "south") else 2
            self.assertAlmostEqual(entry["origin"][lateral], 5.0 + resolver.ENTRY_HALF[entry_side] * 0.8, delta=0.02, msg=f"{label}: entry flight is on the other half")
            self.assertAlmostEqual(exit_["origin"][lateral], 5.0 - resolver.ENTRY_HALF[entry_side] * 0.8, delta=0.02, msg=f"{label}: exit flight is on the other half")
        self.assertAlmostEqual(entry["origin"][1], -3.0, places=3)
        self.assertAlmostEqual(exit_["origin"][1], 0.0, places=3)

    def test_u_turn_on_every_side(self) -> None:
        for side in SIDES:
            with self.subTest(layout="u-turn", side=side):
                self.check(side, side)

    def test_straight_between_opposite_sides(self) -> None:
        for side in SIDES:
            with self.subTest(layout="straight", entry=side):
                self.check(side, OPPOSITE[side])

    def test_l_turn_to_both_adjacent_sides(self) -> None:
        for entry_side, exit_side in itertools.product(SIDES, SIDES):
            if exit_side in (entry_side, OPPOSITE[entry_side]):
                continue
            with self.subTest(layout="l-turn", entry=entry_side, exit=exit_side):
                self.check(entry_side, exit_side)


if __name__ == "__main__":
    unittest.main()
