from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[1]  # the map-pipeline checkout is the res:// root of these fixtures
FIXTURES = ROOT / "fixtures"
SPEC = importlib.util.spec_from_file_location("build_repair_package", ROOT / "build_repair_package.py")
assert SPEC and SPEC.loader
PACKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKER)


class RepairPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, output: Path, *extra: str, composition: Path | None = None, queue: Path | None = None) -> list[str]:
        return [
            "--composition-report", str(FIXTURES / "composition_report.json"),
            "--repair-queue", str(queue or FIXTURES / "repair_queue.json"),
            "--composition-input", str(composition or FIXTURES / "composition_input.json"),
            "--bindings", str(FIXTURES / "bindings.json"),
            "--anchor-frames", str(FIXTURES / "anchor_frames.json"),
            "--authored-scene", str(FIXTURES / "authored_sector.tscn"),
            "--project-root", str(PROJECT_ROOT), "--output", str(output),
            "--validate-command-json", '["python","pipeline/composition_validator/validate_composition.py","--input","resolved.json","--output","reports"]',
            *extra,
        ]

    def load(self, output: Path, relative: str = "repair_package.json") -> dict:
        return json.loads((output / relative).read_text(encoding="utf-8"))

    def test_single_object_package_is_minimal_and_restricted(self) -> None:
        output = self.root / "single"
        self.assertEqual(PACKER.main(self.args(output, "--object-id", "OBJ-A")), 0)
        package = self.load(output)
        self.assertEqual(package["schema_id"], "caretaker.agent_repair_package")
        self.assertEqual(package["object_ids"], ["OBJ-A"])
        self.assertFalse(package["agent_invoked"])
        objects = self.load(output, "context/composition_objects.json")["objects"]
        bindings = self.load(output, "context/bindings.json")["bindings"]
        anchors = self.load(output, "context/anchor_frames.json")["anchors"]
        self.assertEqual([item["object_id"] for item in objects], ["OBJ-A"])
        self.assertEqual([item["binding_id"] for item in bindings], ["BIND-A"])
        self.assertEqual([item["anchor_id"] for item in anchors], ["AF-WALL-A-CANDIDATE"])
        self.assertNotIn("neighbor_sector.tscn", "\n".join(package["context_files"]))
        prompt = (output / "agent_prompt.md").read_text(encoding="utf-8")
        for required in ("Result", "Changed artifacts", "Validation", "Risks", "Commit", "do not modify SVG", "Generated"):
            self.assertIn(required, prompt)

    def test_multiple_objects_and_explicit_extra_file(self) -> None:
        output = self.root / "multiple"
        self.assertEqual(PACKER.main(self.args(output, "--allow-file", "res://pipeline/repair_package/fixtures/object_script.gd")), 0)
        package = self.load(output)
        self.assertEqual(package["object_ids"], ["OBJ-A", "OBJ-B"])
        writable = [item["path"] for item in package["file_policy"]["writable"]]
        self.assertIn("res://pipeline/repair_package/fixtures/object_script.gd", writable)
        self.assertNotIn("res://pipeline/repair_package/fixtures/neighbor_sector.tscn", writable)
        self.assertTrue((output / "context/allowlisted/pipeline/repair_package/fixtures/object_script.gd").is_file())

    def test_unknown_and_duplicate_object_ids_block_package(self) -> None:
        self.assertEqual(PACKER.main(self.args(self.root / "unknown", "--object-id", "OBJ-NOPE")), 2)
        unknown_queue = json.loads((FIXTURES / "repair_queue.json").read_text(encoding="utf-8"))
        unknown_queue["items"][0]["object_id"] = "OBJ-GHOST"
        unknown_queue_path = self.root / "unknown_queue.json"
        unknown_queue_path.write_text(json.dumps(unknown_queue), encoding="utf-8")
        self.assertEqual(PACKER.main(self.args(self.root / "unknown-queue", queue=unknown_queue_path)), 2)
        duplicate = json.loads((FIXTURES / "composition_input.json").read_text(encoding="utf-8"))
        duplicate["objects"].append(dict(duplicate["objects"][0]))
        duplicate_path = self.root / "duplicate.json"
        duplicate_path.write_text(json.dumps(duplicate), encoding="utf-8")
        self.assertEqual(PACKER.main(self.args(self.root / "duplicate", composition=duplicate_path)), 2)

    def test_neighboring_scene_cannot_enter_writable_allowlist(self) -> None:
        output = self.root / "neighbor"
        self.assertEqual(PACKER.main(self.args(output, "--allow-file", "res://pipeline/repair_package/fixtures/neighbor_sector.tscn")), 2)
        self.assertFalse((output / "repair_package.json").exists())

    def test_repeated_package_is_byte_deterministic(self) -> None:
        first, second = self.root / "first", self.root / "second"
        self.assertEqual(PACKER.main(self.args(first)), 0)
        self.assertEqual(PACKER.main(self.args(second)), 0)
        first_files = {path.relative_to(first).as_posix(): path.read_bytes() for path in first.rglob("*") if path.is_file()}
        second_files = {path.relative_to(second).as_posix(): path.read_bytes() for path in second.rglob("*") if path.is_file()}
        self.assertEqual(first_files, second_files)


if __name__ == "__main__":
    unittest.main()
