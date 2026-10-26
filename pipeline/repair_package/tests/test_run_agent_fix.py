from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SPEC = importlib.util.spec_from_file_location("run_agent_fix", ROOT / "run_agent_fix.py")
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


AGENT_ADAPTER = r'''import argparse, json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument("--mode",required=True); p.add_argument("--observed",required=True,type=Path)
p.add_argument("--project-root",required=True,type=Path); p.add_argument("--repair-package",required=True,type=Path); p.add_argument("--prompt",required=True,type=Path)
a=p.parse_args(); package=json.loads(a.repair_package.read_text(encoding="utf-8"))
a.observed.write_text(json.dumps({"objects":package["object_ids"],"package":str(a.repair_package)}),encoding="utf-8")
bindings=next(item["path"] for item in package["file_policy"]["writable"] if item["path"].endswith("bindings.json"))
path=a.project_root/bindings.removeprefix("res://"); value=json.loads(path.read_text(encoding="utf-8")); value["agent_fixture_fix"]=True
path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
if a.mode=="forbidden": (a.project_root/"source.svg").write_text("<svg id='forbidden'/>",encoding="utf-8",newline="\n")
'''


FAKE_SAFE = r'''import argparse, json
from pathlib import Path
p=argparse.ArgumentParser(add_help=False)
p.add_argument("--report",required=True,type=Path); p.add_argument("--manifest",required=True,type=Path); p.add_argument("--sector",required=True)
a,extra=p.parse_known_args(); manifest=json.loads(a.manifest.read_text(encoding="utf-8")); root=(a.manifest.parent/manifest.get("project_root",".")).resolve()
(root/"safe_invocation.json").write_text(json.dumps({"extra":extra,"sector":a.sector}),encoding="utf-8")
failed=(root/"force_revalidation_failure").exists()
report={"schema_id":"caretaker.safe_regeneration_report","schema_version":"1.0.0","sector_id":a.sector,"status":"failed" if failed else "success","ready":not failed,"errors":[] if not failed else [{"stage":"composition_validation","message":"still blocked"}],"stages":[]}
a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
raise SystemExit(2 if failed else 0)
'''


class AgentFixRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.source = self.root / "source.svg"
        self.source.write_text("<svg id='source'/>", encoding="utf-8", newline="\n")
        self.authored = self.root / "authored.tscn"
        self.authored.write_bytes((FIXTURES / "authored_sector.tscn").read_bytes())
        self.bindings = self.root / "bindings.json"
        self.bindings.write_bytes((FIXTURES / "bindings.json").read_bytes())
        self.composition = self.root / "composition.json"
        composition = json.loads((FIXTURES / "composition_input.json").read_text(encoding="utf-8"))
        composition["objects"] = composition["objects"][:1]
        self.write_json(self.composition, composition)
        self.manifest = self.root / "manifest.json"
        self.sector = {
            "sector_id": "FIXTURE-SECTOR", "status": "ready", "blockers": [],
            "source_svg": "source.svg", "output_resource_dir": "res://gen/fixture",
            "authored_scene": "res://authored.tscn",
            "safe_regeneration": {"composition_input": "composition.json", "bindings_input": "bindings.json"},
        }
        self.write_json(self.manifest, {
            "schema_id": "caretaker.sector_generation_manifest", "schema_version": "1.1.0",
            "map_id": "fixture-map", "project_root": ".", "sectors": [self.sector],
        })
        self.adapter = self.root / "agent_adapter.py"
        self.adapter.write_text(AGENT_ADAPTER, encoding="utf-8", newline="\n")
        self.fake_safe = self.root / "fake_safe.py"
        self.fake_safe.write_text(FAKE_SAFE, encoding="utf-8", newline="\n")
        self.observed = Path(self.temp.name) / "observed.json"
        self.report = Path(self.temp.name) / "safe-report.json"
        self.make_blocked_attempt()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    def make_blocked_attempt(self) -> None:
        evidence = Path(self.temp.name) / "evidence"
        evidence.mkdir(exist_ok=True)
        anchors = json.loads((FIXTURES / "anchor_frames.json").read_text(encoding="utf-8"))
        validation = json.loads((FIXTURES / "composition_report.json").read_text(encoding="utf-8"))
        validation["issues"] = validation["issues"][:1]
        queue = json.loads((FIXTURES / "repair_queue.json").read_text(encoding="utf-8"))
        queue["items"] = queue["items"][:1]
        queue["blocking_count"] = 1
        queue["items"][0]["evidence"] = {"responsible_owner": "authored_bindings"}
        documents = {
            "resolved_composition.json": json.loads(self.composition.read_text(encoding="utf-8")),
            "validation_report.json": validation,
            "repair_queue.json": queue,
            "candidate_anchor_frames.json": anchors,
            "candidate_generation_manifest.json": {
                "schema_id": "caretaker.sector_generation_result", "schema_version": "1.0.0",
                "map_id": "fixture-map", "sector_id": "FIXTURE-SECTOR", "generation_id": "sha256:fixture-generation",
            },
            "candidate_regeneration_report.json": {
                "schema_id": "caretaker.sector_regeneration_report", "schema_version": "1.0.0", "status": "ok",
                "map_id": "fixture-map", "sector_id": "FIXTURE-SECTOR", "generation_id": "sha256:fixture-generation",
            },
        }
        for name, kind_value in (
            ("source_sha256", RUNNER.digest_bytes(self.source.read_bytes())),
            ("sector_config_sha256", RUNNER.digest_bytes(RUNNER.canonical_bytes(self.sector))),
            ("generation_id", "sha256:fixture-generation"),
        ):
            documents[name] = {
                "schema_id": "caretaker.safe_regeneration_evidence_value", "schema_version": "1.0.0",
                "kind": name, "map_id": "fixture-map", "sector_id": "FIXTURE-SECTOR",
                "generation_id": "sha256:fixture-generation", "value": kind_value,
            }
        artifacts = {}
        for name, document in documents.items():
            path = evidence / name
            self.write_json(path, document)
            artifacts[name] = {"path": str(path), "sha256": RUNNER.digest_bytes(path.read_bytes())}
        self.write_json(self.report, {
            "schema_id": "caretaker.safe_regeneration_report", "schema_version": "1.0.0",
            "map_id": "fixture-map", "sector_id": "FIXTURE-SECTOR", "generation_id": "sha256:fixture-generation",
            "status": "failed", "ready": False, "agent_invoked": False,
            "input_hashes": {
                "source": RUNNER.digest_bytes(self.source.read_bytes()),
                "sector_config": RUNNER.digest_bytes(RUNNER.canonical_bytes(self.sector)),
            },
            "errors": [{"stage": "composition_validation", "message": "blocked"}],
            "stages": [{"stage": "composition_validation", "status": "failed"}],
            "evidence_directory": str(evidence), "validation_artifacts": artifacts,
        })

    def args(self, mode: str = "success") -> list[str]:
        launcher = json.dumps([sys.executable, str(self.adapter), "--mode", mode, "--observed", str(self.observed)])
        return [
            "--sector", "FIXTURE-SECTOR", "--manifest", str(self.manifest),
            "--safe-report", str(self.report), "--agent-launcher", launcher,
            "--python", sys.executable, "--safe-regenerate", str(self.fake_safe),
        ]

    def test_success_uses_one_object_and_runs_full_regeneration(self) -> None:
        self.assertEqual(RUNNER.main(self.args()), 0)
        observed = json.loads(self.observed.read_text(encoding="utf-8"))
        self.assertEqual(observed["objects"], ["OBJ-A"])
        self.assertTrue(Path(observed["package"]).is_file())
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertTrue(report["ready"])
        self.assertTrue(report["agent_invoked"])
        self.assertEqual(report["agent_exit_code"], 0)
        self.assertEqual(report["agent_changed_files"], ["bindings.json"])
        self.assertTrue(report["agent_revalidation"]["clean"])
        invocation = json.loads((self.root / "safe_invocation.json").read_text(encoding="utf-8"))
        self.assertNotIn("--validate-only", invocation["extra"])
        self.assertNotIn("--dry-run", invocation["extra"])

    def test_clean_report_does_not_launch_agent(self) -> None:
        report = json.loads(self.report.read_text(encoding="utf-8"))
        report.update({"status": "success", "ready": True, "errors": [], "stages": []})
        self.write_json(self.report, report)
        self.assertEqual(RUNNER.main(self.args()), 2)
        self.assertFalse(self.observed.exists())

    def test_stale_source_hash_does_not_launch_agent(self) -> None:
        self.source.write_text("<svg id='changed'/>", encoding="utf-8", newline="\n")
        self.assertEqual(RUNNER.main(self.args()), 2)
        self.assertFalse(self.observed.exists())

    def test_forbidden_change_is_rejected_without_regeneration(self) -> None:
        self.assertEqual(RUNNER.main(self.args("forbidden")), 2)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertIn("source.svg", report["agent_forbidden_changes"])
        self.assertFalse((self.root / "safe_invocation.json").exists())
        self.assertFalse(report["ready"])

    def test_nonclean_revalidation_keeps_report_not_ready(self) -> None:
        (self.root / "force_revalidation_failure").write_text("fixture", encoding="utf-8")
        self.assertEqual(RUNNER.main(self.args()), 2)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertFalse(report["ready"])
        self.assertFalse(report["agent_revalidation"]["clean"])
        self.assertEqual(report["agent_revalidation"]["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
