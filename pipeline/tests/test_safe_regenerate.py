from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("safe_regenerate", ROOT / "safe_regenerate.py")
assert SPEC and SPEC.loader
SAFE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SAFE)
VALIDATOR = ROOT / "composition_validator" / "validate_composition.py"


FAKE_BACKEND = r'''import argparse, hashlib, json, os, sys
from pathlib import Path
p=argparse.ArgumentParser(add_help=False)
p.add_argument("--sector"); p.add_argument("--staging"); p.add_argument("--manifest"); p.add_argument("--preflight-only",action="store_true")
a,_=p.parse_known_args()
manifest=json.loads(Path(a.manifest).read_text(encoding="utf-8"))
sector=next(item for item in manifest["sectors"] if item["sector_id"]==a.sector)
mode=sector.get("fixture_mode","ok")
failures={"inspector":"SVG inspection failed", "converter":"SVG conversion failed", "stairs":"Stair generator main failed"}
if a.preflight_only and mode=="toolchain":
    print("ERROR: svg-plan-to-godot 1.18.9 is older than required 1.19.0",file=sys.stderr); raise SystemExit(2)
if a.preflight_only: raise SystemExit(0)
if mode in failures:
    print("ERROR: "+failures[mode],file=sys.stderr); raise SystemExit(2)
root=Path(a.staging); generated=root/"Generated"/"Architecture"; generated.mkdir(parents=True)
scene='[gd_scene format=3]\n\n[node name="Fixture" type="Node3D"]\n'
if mode=="combined": scene='[gd_scene load_steps=2 format=3]\n\n[ext_resource path="res://missing_resource.tres" type="Resource" id="1"]\n[node name="Fixture" type="Node3D"]\n'
(generated/"fixture.tscn").write_text(scene,encoding="utf-8")
generation_id="sha256:"+hashlib.sha256(json.dumps(sector,sort_keys=True).encode()).hexdigest()
anchor={"anchor_id":"AF-WALL-A","type":"wall","status":"active","source_ref":{"artifact_id":"fixture","source_id":"wall-a"},"origin":[0,1,0],"forward":[1,0,0],"normal":[0,0,1],"up":[0,1,0],"bounds":{"length_m":10,"height_m":3},"placement_limits":{"normal_offset_m":[-1,1],"height_m":[0,3]}}
anchors={"schema_id":"caretaker.anchor_frames","schema_version":"1.0.0","map_id":manifest["map_id"],"sector_id":a.sector,"generation_id":generation_id,"anchors":[anchor]}
generation={"schema_id":"caretaker.sector_generation_result","schema_version":"1.0.0","map_id":manifest["map_id"],"sector_id":a.sector,"generation_id":generation_id}
report={"schema_id":"caretaker.sector_regeneration_report","schema_version":"1.0.0","sector_id":a.sector,"generation_id":generation_id,"status":"ok","staging_path":str(root)}
if mode=="identity": report["generation_id"]="sha256:mismatched"
for name,value in (("anchor_frames.json",anchors),("generation_manifest.json",generation),("regeneration_report.json",report)):
    (root/name).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True)+"\n",encoding="utf-8")
'''


class SafeRegenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "Проект с пробелами"
        self.root.mkdir()
        (self.root / "project.godot").write_text(
            '[application]\nconfig/name="Safe Regeneration Fixture"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n',
            encoding="utf-8",
        )
        self.source = self.root / "план.svg"
        self.source.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1">'
            '<rect width="1" height="1" fill="#000"/></svg>\n',
            encoding="utf-8",
            newline="\n",
        )
        self.backend = self.root / "fake_backend.py"
        self.backend.write_text(FAKE_BACKEND, encoding="utf-8")
        self.live = self.root / "sectors" / "fixture"
        self.composition = self.root / "composition.json"
        self.write_composition()
        self.manifest = self.root / "manifest.json"
        self.write_manifest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_composition(self, *, missing_anchor: bool = False) -> None:
        value = {
            "schema_id": "caretaker.composition_validation_input", "schema_version": "1.0.0",
            "map_id": "fixture-map", "sector_id": "FIXTURE", "generation_id": "old",
            "sector_bounds": {"min": [0, -1, 0], "max": [10, 4, 10]},
            "spaces": [{"space_id": "SPACE-A", "sector_id": "FIXTURE", "bounds": {"min": [0, 0, 0], "max": [10, 3, 10]}}],
            "anchors": [], "infrastructure": [{
                "infrastructure_id": "INFRA-FLOOR", "owner": "generated", "kind": "floor",
                "bounds": {"min": [0, -0.2, 0], "max": [10, 0, 10]},
            }],
            "objects": [{
                "object_id": "OBJ-A", "binding_id": "BIND-A", "owner": "authored", "sector_id": "FIXTURE", "space_id": "SPACE-A",
                "anchor_id": "AF-MISSING" if missing_anchor else "AF-WALL-A", "expected_anchor_type": "wall", "placement_mode": "floor",
                "support_tolerance_m": 0.02, "bounds": {"min": [1, 0, 1], "max": [2, 1, 2]},
            }],
        }
        self.composition.write_text(json.dumps(value), encoding="utf-8")

    def write_manifest(self, mode: str = "ok") -> None:
        value = {
            "schema_id": "caretaker.sector_generation_manifest", "schema_version": "1.0.0", "map_id": "fixture-map", "project_root": ".",
            "sectors": [{
                "sector_id": "FIXTURE", "status": "ready", "blockers": [], "source_svg": self.source.name,
                "output_resource_dir": "res://sectors/fixture", "fixture_mode": mode,
                "safe_regeneration": {"composition_input": self.composition.name},
            }],
        }
        self.manifest.write_text(json.dumps(value), encoding="utf-8")

    def args(self, *extra: str) -> list[str]:
        return [
            "--sector", "FIXTURE", "--manifest", str(self.manifest), "--backend", str(self.backend),
            "--composition-validator", str(VALIDATOR), "--python", sys.executable,
            "--report", str(self.root / "report.json"), *extra,
        ]

    def run_safe(self, *extra: str) -> tuple[int, dict]:
        code = SAFE.main(self.args(*extra))
        report = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        return code, report

    def seed_live(self) -> None:
        self.assertEqual(self.run_safe()[0], 0)
        (self.live / "Materials").mkdir()
        (self.live / "Materials" / "authored.tres").write_text("preserve", encoding="utf-8")
        (self.live / "notes.txt").write_text("user file", encoding="utf-8")

    def test_success_promotes_and_preserves_unmanaged_files(self) -> None:
        self.live.mkdir(parents=True)
        (self.live / "Materials").mkdir()
        (self.live / "Materials" / "authored.tres").write_text("preserve", encoding="utf-8")
        (self.live / "notes.txt").write_text("user file", encoding="utf-8")
        code, report = self.run_safe()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "success")
        self.assertTrue(report["ready"])
        self.assertFalse(report["agent_invoked"])
        self.assertTrue((self.live / "Generated" / "Architecture" / "fixture.tscn").is_file())
        self.assertEqual((self.live / "Materials" / "authored.tres").read_text(encoding="utf-8"), "preserve")
        self.assertEqual((self.live / "notes.txt").read_text(encoding="utf-8"), "user file")

    def test_incompatible_toolchain_fails_before_staging_is_created(self) -> None:
        self.write_manifest("toolchain")
        code, report = self.run_safe()
        self.assertEqual(code, 2)
        self.assertEqual(report["errors"][-1]["stage"], "toolchain_validation")
        self.assertEqual(list(self.live.parent.glob(".*.regeneration-*")), [])

    def test_authored_composition_inside_live_is_validated_from_candidate(self) -> None:
        authored = self.live / "AuthoredContent"
        authored.mkdir(parents=True)
        target = authored / "composition.json"
        target.write_bytes(self.composition.read_bytes())
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["sectors"][0]["safe_regeneration"]["composition_input"] = "sectors/fixture/AuthoredContent/composition.json"
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        before = target.read_bytes()
        code, report = self.run_safe()
        self.assertEqual((code, report["status"]), (0, "success"))
        self.assertEqual(target.read_bytes(), before)

    def test_report_inside_live_is_blocked_without_mutation(self) -> None:
        self.live.mkdir(parents=True)
        marker = self.live / "authored.txt"
        marker.write_text("preserve", encoding="utf-8")
        code = SAFE.main([
            "--sector", "FIXTURE", "--manifest", str(self.manifest), "--backend", str(self.backend),
            "--composition-validator", str(VALIDATOR), "--python", sys.executable,
            "--report", str(self.live / "report.json"),
        ])
        self.assertEqual(code, 2)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_backend_failures_leave_live_unchanged_and_report_exact_stage(self) -> None:
        self.seed_live()
        before = SAFE.path_hash(self.live)
        for mode, stage in (("inspector", "source_validation"), ("converter", "staging_generation"), ("stairs", "stair_generation")):
            with self.subTest(mode=mode):
                self.write_manifest(mode)
                code, report = self.run_safe()
                self.assertEqual(code, 2)
                self.assertEqual(report["errors"][0]["stage"], stage)
                self.assertFalse(report["ready"])
                self.assertEqual(SAFE.path_hash(self.live), before)

    def test_combined_and_composition_failures_leave_live_unchanged(self) -> None:
        self.seed_live()
        before = SAFE.path_hash(self.live)
        self.write_manifest("combined")
        code, report = self.run_safe()
        self.assertEqual((code, report["errors"][0]["stage"]), (2, "combined_validation"))
        self.assertEqual(SAFE.path_hash(self.live), before)
        self.write_manifest()
        self.write_composition(missing_anchor=True)
        code, report = self.run_safe()
        self.assertEqual((code, report["errors"][0]["stage"]), (2, "composition_validation"))
        self.assertEqual(SAFE.path_hash(self.live), before)

    def test_noop_does_not_replace_live_directory(self) -> None:
        self.assertEqual(self.run_safe()[0], 0)
        marker = self.live.stat().st_mtime_ns
        code, report = self.run_safe()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "noop")
        self.assertEqual(self.live.stat().st_mtime_ns, marker)

    def test_godot_sidecars_do_not_defeat_noop_and_survive_promotion(self) -> None:
        self.assertEqual(self.run_safe()[0], 0)
        scene = self.live / "Generated" / "Architecture" / "fixture.tscn"
        import_file = scene.with_name("fixture.tscn.import")
        uid_file = scene.with_name("fixture.tscn.uid")
        import_file.write_text('uid="uid://stable"\n', encoding="utf-8")
        uid_file.write_text("uid://stable_scene\n", encoding="utf-8")
        code, report = self.run_safe()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "noop")
        # A changed source regenerates the package but keeps Godot's stable UID files.
        self.write_source_change()
        code, report = self.run_safe()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "success")
        self.assertEqual(import_file.read_text(encoding="utf-8"), 'uid="uid://stable"\n')
        self.assertEqual(uid_file.read_text(encoding="utf-8"), "uid://stable_scene\n")

    def test_sidecar_of_a_removed_source_is_dropped(self) -> None:
        self.assertEqual(self.run_safe()[0], 0)
        orphan = self.live / "Generated" / "Architecture" / "gone.obj.import"
        orphan.write_text('uid="uid://orphan"\n', encoding="utf-8")
        self.write_source_change()
        self.assertEqual(self.run_safe()[1]["status"], "success")
        self.assertFalse(orphan.exists())

    def write_source_change(self) -> None:
        """Change the sector configuration so the generated output (generation id) differs."""
        value = json.loads(self.manifest.read_text(encoding="utf-8"))
        value["sectors"][0]["fixture_revision"] = value["sectors"][0].get("fixture_revision", 1) + 1
        self.manifest.write_text(json.dumps(value), encoding="utf-8")

    def test_removed_anchor_keeps_repair_queue_after_staging_cleanup(self) -> None:
        self.seed_live()
        self.write_composition(missing_anchor=True)
        before = SAFE.path_hash(self.live)
        authored_before = self.composition.read_bytes()
        code, report = self.run_safe()
        self.assertEqual(code, 2)
        self.assertFalse(report["ready"])
        self.assertEqual(report["live_hash_before"], before)
        self.assertEqual(report["live_hash_after"], before)
        self.assertEqual(self.composition.read_bytes(), authored_before)
        artifact = report["validation_artifacts"]["repair_queue.json"]
        path = Path(artifact["path"])
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_relative_to(self.live))
        self.assertEqual(artifact["sha256"], SAFE.digest_bytes(path.read_bytes()))
        queue = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(queue["schema_id"], "caretaker.repair_queue")
        missing = [item for item in queue["items"] if item["code"] == "missing_anchor"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["previous_anchor_ref"]["anchor_id"], "AF-MISSING")
        self.assertEqual(missing[0]["candidate_anchor_ids"], [])
        expected = {
            "resolved_composition.json", "validation_report.json", "repair_queue.json",
            "candidate_anchor_frames.json", "candidate_generation_manifest.json",
            "candidate_regeneration_report.json", "source_sha256",
            "sector_config_sha256", "generation_id",
        }
        self.assertEqual(set(report["validation_artifacts"]), expected)
        evidence_directories = set()
        for name, details in report["validation_artifacts"].items():
            evidence_path = Path(details["path"])
            self.assertTrue(evidence_path.is_file(), name)
            self.assertEqual(details["sha256"], SAFE.digest_bytes(evidence_path.read_bytes()), name)
            evidence_directories.add(evidence_path.parent)
            document = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(document["map_id"], report["map_id"], name)
            self.assertEqual(document["sector_id"], report["sector_id"], name)
            self.assertEqual(document["generation_id"], report["generation_id"], name)
        self.assertEqual(evidence_directories, {Path(report["evidence_directory"])})
        source_hash = json.loads(Path(report["validation_artifacts"]["source_sha256"]["path"]).read_text(encoding="utf-8"))
        config_hash = json.loads(Path(report["validation_artifacts"]["sector_config_sha256"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(source_hash["value"], report["input_hashes"]["source"])
        self.assertEqual(config_hash["value"], report["input_hashes"]["sector_config"])
        self.assertEqual(list(self.live.parent.glob(".*.regeneration-*")), [])

    def test_current_report_never_reuses_stale_repair_queue(self) -> None:
        self.seed_live()
        self.write_composition(missing_anchor=True)
        _, blocked = self.run_safe()
        previous = Path(blocked["validation_artifacts"]["repair_queue.json"]["path"])
        previous_evidence = Path(blocked["evidence_directory"])
        previous_bytes = previous.read_bytes()
        self.write_composition()
        code, clean = self.run_safe()
        self.assertEqual(code, 0)
        current = Path(clean["validation_artifacts"]["repair_queue.json"]["path"])
        self.assertNotEqual(previous, current)
        self.assertNotEqual(previous_evidence, Path(clean["evidence_directory"]))
        self.assertEqual(json.loads(current.read_text(encoding="utf-8"))["blocking_count"], 0)
        self.assertEqual(previous.read_bytes(), previous_bytes)
        self.write_manifest("converter")
        code, failed = self.run_safe()
        self.assertEqual(code, 2)
        self.assertEqual(failed["validation_artifacts"], {})

    def test_inconsistent_candidate_identity_blocks_and_preserves_live(self) -> None:
        self.seed_live()
        before = SAFE.path_hash(self.live)
        self.write_manifest("identity")
        code, report = self.run_safe()
        self.assertEqual(code, 2)
        self.assertEqual(report["errors"][0]["stage"], "diagnostic_persistence")
        self.assertIn("generation_id", report["errors"][0]["message"])
        self.assertEqual(SAFE.path_hash(self.live), before)
        self.assertEqual(report["validation_artifacts"], {})
        self.assertEqual(list(self.live.parent.glob(".*.regeneration-*")), [])

    def test_validate_only_failure_retains_diagnostics_without_live_mutation(self) -> None:
        self.seed_live()
        self.write_composition(missing_anchor=True)
        before = SAFE.path_hash(self.live)
        code, report = self.run_safe("--validate-only")
        self.assertEqual(code, 2)
        self.assertEqual(SAFE.path_hash(self.live), before)
        self.assertTrue(Path(report["validation_artifacts"]["repair_queue.json"]["path"]).is_file())

    def test_dry_run_and_validate_only_do_not_mutate_live(self) -> None:
        code, report = self.run_safe("--dry-run")
        self.assertEqual((code, report["status"], self.live.exists()), (0, "dry_run", False))
        self.assertFalse(report["ready"])
        self.assertTrue(report["candidate_validated"])
        self.assertEqual(self.run_safe()[0], 0)
        before = SAFE.path_hash(self.live)
        code, report = self.run_safe("--validate-only")
        self.assertEqual((code, report["status"]), (0, "validated"))
        self.assertEqual(SAFE.path_hash(self.live), before)

    def test_cli_supports_windows_unicode_and_spaces(self) -> None:
        process = subprocess.run([sys.executable, str(ROOT / "safe_regenerate.py"), *self.args("--dry-run")], text=True, capture_output=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads((self.root / "report.json").read_text(encoding="utf-8"))["status"], "dry_run")

    def test_evidence_write_failure_blocks_promotion(self) -> None:
        self.seed_live()
        self.write_manifest("changed")
        before = SAFE.path_hash(self.live)
        with mock.patch.object(SAFE.tempfile, "mkdtemp", side_effect=OSError("read-only evidence directory")):
            code, report = self.run_safe()
        self.assertEqual(code, 2)
        self.assertEqual(report["errors"][0]["stage"], "diagnostic_persistence")
        self.assertEqual(SAFE.path_hash(self.live), before)
        self.assertFalse(report["ready"])

    def test_atomic_promotion_restores_live_when_second_replace_fails(self) -> None:
        live, candidate, backup = self.root / "live", self.root / "candidate", self.root / "backup"
        live.mkdir(); candidate.mkdir()
        (live / "old.txt").write_text("old", encoding="utf-8")
        real_replace = os.replace
        calls = 0

        def fail_second(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture failure")
            real_replace(source, destination)

        with mock.patch.object(SAFE.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(SAFE.OrchestrationError, "rolled back"):
                SAFE.promote(candidate, live, backup)
        self.assertEqual((live / "old.txt").read_text(encoding="utf-8"), "old")
        self.assertFalse(backup.exists())

    @unittest.skipUnless(os.environ.get("GODOT_BIN"), "GODOT_BIN is not configured")
    def test_promoted_scene_loads_in_godot_headless(self) -> None:
        self.assertEqual(self.run_safe()[0], 0)
        process = subprocess.run([
            os.environ["GODOT_BIN"], "--headless", "--disable-crash-handler",
            "--log-file", str(self.root / "godot-safe-regeneration.log"),
            "--editor", "--path", str(self.root), "--quit",
        ], text=True, capture_output=True, check=False)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
