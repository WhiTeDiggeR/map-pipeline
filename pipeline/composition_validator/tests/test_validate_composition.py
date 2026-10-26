from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_composition", ROOT / "validate_composition.py")
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class CompositionValidatorTests(unittest.TestCase):
    def run_case(self, name: str, expected_code: str | None) -> tuple[int, dict, dict]:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report"
            exit_code = VALIDATOR.main([
                "--input", str(ROOT / "fixtures" / f"{name}.json"),
                "--output", str(output),
            ])
            report = json.loads((output / "validation_report.json").read_text(encoding="utf-8"))
            repair = json.loads((output / "repair_queue.json").read_text(encoding="utf-8"))
            self.assertTrue((output / "validation_report.md").is_file())
            codes = {item["code"] for item in report["issues"]}
            if expected_code is not None:
                self.assertIn(expected_code, codes)
            return exit_code, report, repair

    def test_clean_is_exit_zero_and_empty_repair_queue(self) -> None:
        exit_code, report, repair = self.run_case("clean", None)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "clean")
        self.assertEqual(repair["blocking_count"], 0)
        self.assertEqual(repair["items"], [])

    def test_required_blocking_fixtures(self) -> None:
        cases = {
            "missing_anchor": "missing_anchor",
            "duplicate_id": "duplicate_object_id",
            "duplicate_anchor": "duplicate_anchor_id",
            "wall_penetration": "wall_penetration",
            "door_blockage": "door_blockage",
            "floating_object": "support_missing",
            "stair_conflict": "stair_conflict",
            "wrong_kind": "wrong_anchor_kind",
            "wall_mount_out_of_bounds": "wall_mount_out_of_bounds",
            "outside_space_sector": "outside_sector",
        }
        for fixture, code in cases.items():
            with self.subTest(fixture=fixture):
                exit_code, report, repair = self.run_case(fixture, code)
                self.assertEqual(exit_code, 2)
                self.assertGreater(report["summary"]["blocking_count"], 0)
                self.assertGreater(repair["blocking_count"], 0)
                self.assertLessEqual(repair["blocking_count"], report["summary"]["blocking_count"])
                self.assertEqual(repair["schema_id"], "caretaker.repair_queue")
                self.assertEqual([item["repair_id"] for item in repair["items"]], sorted(item["repair_id"] for item in repair["items"]))
                for item in repair["items"]:
                    self.assertEqual(item["status"], "open")
                    self.assertTrue(item["object_id"])
                    self.assertTrue(item["allowed_actions"])
                    self.assertIsNone(item["resolution"])
                for item in report["issues"]:
                    self.assertIn("object_id", item)
                    self.assertIn("anchor_id", item)
                    self.assertIn("measurement", item)
                    self.assertTrue(item["allowed_actions"])

    def test_warning_does_not_mask_missing_anchor_blocker(self) -> None:
        exit_code, report, _repair = self.run_case("missing_anchor", "missing_anchor")
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["summary"]["warning_count"], 1)
        self.assertEqual(report["status"], "blocked")

    def test_floor_ceiling_passage_and_shaft_checks(self) -> None:
        exit_code, report, _repair = self.run_case("surface_passage_shaft", "passage_blockage")
        self.assertEqual(exit_code, 2)
        codes = {item["code"] for item in report["issues"]}
        self.assertTrue({"floor_penetration", "ceiling_penetration", "passage_blockage", "shaft_conflict"}.issubset(codes))

    def test_generated_error_is_not_assigned_to_authored_content(self) -> None:
        exit_code, report, _repair = self.run_case("generated_infrastructure_error", "generated_door_blockage")
        self.assertEqual(exit_code, 2)
        issue = next(item for item in report["issues"] if item["code"] == "generated_door_blockage")
        self.assertEqual(issue["object_id"], "INFRA-BAD-WALL")
        self.assertEqual(issue["subject_owner"], "generated")
        self.assertEqual(issue["responsible_owner"], "generated")

    def test_output_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first"
            second = Path(temporary) / "second"
            fixture = ROOT / "fixtures" / "door_blockage.json"
            self.assertEqual(VALIDATOR.main(["--input", str(fixture), "--output", str(first)]), 2)
            self.assertEqual(VALIDATOR.main(["--input", str(fixture), "--output", str(second)]), 2)
            for name in ("validation_report.json", "repair_queue.json", "validation_report.md"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

    def test_runtime_physics_proof_is_reported_separately(self) -> None:
        proof = {
            "schema_id": "caretaker.composition_physics_proof",
            "schema_version": "1.0.0",
            "map_id": "fixture-map",
            "sector_id": "PHYSICS",
            "generation_id": "sha256:physics",
            "checks": [{
                "object_id": "OBJ-PHYS-WALL",
                "anchor_id": None,
                "code": "wall_penetration",
                "result": "blocking",
                "measurement": {"actual": 1, "limit": 0, "units": "physics_shape_hits", "relation": "lte"},
                "allowed_actions": ["move_object"],
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof_path = root / "physics.json"
            proof_path.write_text(json.dumps(proof), encoding="utf-8")
            output = root / "output"
            exit_code = VALIDATOR.main([
                "--input", str(ROOT / "fixtures" / "physics_input.json"),
                "--physics-proof", str(proof_path),
                "--output", str(output),
            ])
            report = json.loads((output / "validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 2)
            self.assertEqual(report["summary"]["static_count"], 0)
            self.assertEqual(report["summary"]["runtime_physics_count"], 1)

    def test_explicit_wall_integration_is_bounded_and_anchor_specific(self) -> None:
        anchors = {
            "AF-WALL": {"anchor_id": "AF-WALL", "normal": [0, 0, 1]},
            "AF-OTHER": {"anchor_id": "AF-OTHER", "normal": [1, 0, 0]},
        }
        wall = {"source_anchor_id": "AF-WALL"}
        mounted = {
            "object_id": "OBJ-MOUNTED", "anchor_id": "AF-WALL",
            "wall_integration": {"mode": "mounted", "max_depth_m": 0.2},
        }
        self.assertTrue(VALIDATOR.wall_integration_allows(mounted, wall, anchors, (0.5, 0.5, 0.2)))
        self.assertFalse(VALIDATOR.wall_integration_allows(mounted, wall, anchors, (0.5, 0.5, 0.21)))
        self.assertFalse(VALIDATOR.wall_integration_allows(mounted, {"source_anchor_id": "AF-OTHER"}, anchors, (0.1, 0.1, 0.1)))

        door_frame = {
            "object_id": "OBJ-FRAME", "anchor_id": "AF-DOOR", "expected_anchor_type": "door",
            "wall_integration": {"mode": "door_frame", "max_depth_m": 0.3},
        }
        self.assertTrue(VALIDATOR.wall_integration_allows(door_frame, wall, anchors, (0.1, 0.1, 0.3)))
        door_frame["expected_anchor_type"] = "wall"
        self.assertFalse(VALIDATOR.wall_integration_allows(door_frame, wall, anchors, (0.1, 0.1, 0.1)))

    def test_invalid_wall_integration_depth_blocks_input(self) -> None:
        item = {
            "object_id": "OBJ-MOUNTED", "anchor_id": "AF-WALL",
            "wall_integration": {"mode": "mounted", "max_depth_m": -0.1},
        }
        with self.assertRaisesRegex(ValueError, "max_depth_m"):
            VALIDATOR.wall_integration_allows(
                item, {"source_anchor_id": "AF-WALL"},
                {"AF-WALL": {"normal": [0, 0, 1]}}, (0.1, 0.1, 0.1),
            )


if __name__ == "__main__":
    unittest.main()
