"""Room finishes of joined wall meshes (caretaker #273)."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("room_finishes_273", ROOT / "room_finishes.py")
assert SPEC and SPEC.loader
FINISHES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINISHES)

CLASSES = {"utility": {"priority": 1}, "medical": {"priority": 2}}
PLAN = {"hall--south": ("wall", "medical"), "tech--north": ("wall", "utility"), "hall--floor": ("floor", "medical")}


class JoinedWallFinishTests(unittest.TestCase):
    def test_single_source_keeps_its_class(self) -> None:
        self.assertEqual(FINISHES._target("hall--south", PLAN, CLASSES), ("wall", "medical"))

    def test_joined_mesh_takes_the_class_with_the_smaller_priority(self) -> None:
        self.assertEqual(FINISHES._target("hall--south|tech--north", PLAN, CLASSES), ("wall", "utility"))

    def test_joined_mesh_with_an_unclassed_part_takes_the_classed_one(self) -> None:
        self.assertEqual(FINISHES._target("hall--east--outer|hall--south", PLAN, CLASSES), ("wall", "medical"))

    def test_mesh_without_classed_parts_keeps_the_package_material(self) -> None:
        self.assertIsNone(FINISHES._target("a|b", PLAN, CLASSES))

    def test_joined_mesh_of_different_surfaces_is_refused(self) -> None:
        with self.assertRaises(FINISHES.FinishError):
            FINISHES._target("hall--south|hall--floor", PLAN, CLASSES)


if __name__ == "__main__":
    unittest.main()
