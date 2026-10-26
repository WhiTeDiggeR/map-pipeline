from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("regenerate_sector", ROOT / "regenerate_sector.py")
assert SPEC and SPEC.loader
BACKEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKEND)


SVG_FRAME = {
    "anchor_id": "svg:wall-main:center",
    "type": "floor",
    "role": "surface",
    "status": "active",
    "source_ref": {"artifact_id": "geometry-handoff", "source_id": "wall-main"},
    "origin": [1.0, 0.0, 2.0],
    "forward": [1.0, 0.0, 0.0],
    "normal": [0.0, 1.0, 0.0],
    "up": [0.0, 1.0, 0.0],
    "geometry_hash": "sha256:fixture-svg",
    "bounds": {"polygon_xz_m": [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0]], "elevation_m": 0.0},
    "placement_limits": {"normal_offset_m": [0.0, 0.0], "height_m": [0.0, 0.0]},
}


INSPECTOR = r'''import argparse, json
import sys
if "--version" in sys.argv:
 print("svg-plan-to-godot 1.19.0"); raise SystemExit(0)
p=argparse.ArgumentParser(add_help=False)
p.add_argument("source"); p.add_argument("--output-json"); p.add_argument("--json", action="store_true")
a,_=p.parse_known_args()
frame=%s
handoff={"anchor_frames":[frame],"anchor_frame_issues":[]}
if "mismatch" in a.source: handoff["fixture_side"]="inspector"
report={"unrecognized":[],"invalid":[],"spatial_handoff":handoff}
open(a.output_json,"w",encoding="utf-8").write(json.dumps(report))
''' % repr(SVG_FRAME)


CONVERTER = r'''import json, os, sys
source=sys.argv[1]; output=sys.argv[2]
os.makedirs(output,exist_ok=True)
scene_name=sys.argv[sys.argv.index("--scene-name")+1]
frame=%s
handoff={"anchor_frames":[frame],"anchor_frame_issues":[]}
report={"generator_version":"1.19.0","errors":[],"spatial_handoff":handoff,"files":[scene_name+".tscn"]}
open(os.path.join(output,scene_name+".tscn"),"w",encoding="utf-8").write("[gd_scene format=3]\n")
open(os.path.join(output,"conversion_report.json"),"w",encoding="utf-8").write(json.dumps(report))
''' % repr(SVG_FRAME)


STAIRS = r'''import json, os, sys
if "--version" in sys.argv:
 print("generate-godot-stairs 2.9.0"); raise SystemExit(0)
output=sys.argv[1]; os.makedirs(output,exist_ok=True)
frame={"anchor_id":"stairs:fixture:lower_entry","type":"stair_entry","role":"lower_entry","status":"active","source_ref":{"artifact_id":"generation-report","source_id":"fixture:lower_entry"},"origin":[0.0,0.0,0.0],"forward":[0.0,0.0,1.0],"normal":[-1.0,0.0,0.0],"up":[0.0,1.0,0.0],"geometry_hash":"sha256:fixture-stairs","bounds":{"clear_bounds_xz":[-1.0,-2.0,1.0,2.0],"bottom_y":0.0,"top_y":3.0},"placement_limits":{"normal_offset_m":[-1.0,1.0],"height_m":[0.0,3.0]}}
report={"schema_id":"caretaker.godot_stairs.generation_report","schema_version":"1.1.0","generator_version":"2.9.0","status":"ok","errors":[],"geometry_validation":{"ok":True,"compiled":{"ok":True},"shaft":{"ok":True}},"anchor_frames":[frame],"files":["stairs.tscn"]}
open(os.path.join(output,"stairs.tscn"),"w",encoding="utf-8").write("[gd_scene format=3]\n")
open(os.path.join(output,"generation_report.json"),"w",encoding="utf-8").write(json.dumps(report))
'''


class SectorRegenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.svg_root = self.root / "svg_tool" / "scripts"
        self.stair_root = self.root / "stair_tool" / "scripts"
        self.svg_root.mkdir(parents=True)
        self.stair_root.mkdir(parents=True)
        (self.svg_root / "inspect_svg_plan.py").write_text(INSPECTOR, encoding="utf-8")
        (self.svg_root / "svg_to_godot3d.py").write_text(CONVERTER, encoding="utf-8")
        (self.stair_root / "generate_godot_stairs.py").write_text(STAIRS, encoding="utf-8")
        (self.root / "ordinary.svg").write_text("<svg/>", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def manifest(self, *, source: str = "ordinary.svg", vertical: bool = False, transform: dict | None = None) -> Path:
        identity = transform or {
            "origin": [10.0, 4.0, 20.0],
            "basis_x": [0.0, 0.0, -1.0], "basis_y": [0.0, 1.0, 0.0], "basis_z": [1.0, 0.0, 0.0],
        }
        generators = []
        if vertical:
            generators.append({"generator_id": "main", "args": ["--fixture"], "local_to_world": identity})
        value = {
            "schema_id": "caretaker.sector_generation_manifest", "schema_version": "1.0.0",
            "map_id": "fixture-map", "project_root": ".", "sector_count": 1,
            "sectors": [{
                "sector_id": "FIXTURE", "status": "ready", "blockers": [], "source_svg": source,
                "profile": "generic", "metric_settings": {"scale_m_per_svg_unit": 1.0, "origin": "none", "elevation_m": 0.0},
                "shared_args": [], "semantic_mappings": ["wall=wall"], "material_mappings": {},
                "scene_name": "fixture", "output_resource_dir": "res://generated/fixture",
                "local_to_world": identity, "vertical_generators": generators,
            }],
        }
        path = self.root / "manifest.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def run_backend(self, staging: Path, manifest: Path, sector: str = "FIXTURE") -> int:
        return BACKEND.main([
            "--sector", sector, "--staging", str(staging), "--manifest", str(manifest),
            "--svg-tool-root", str(self.svg_root.parent), "--stair-tool-root", str(self.stair_root.parent),
            "--python", sys.executable,
        ])

    def test_unknown_sector_is_code_2_without_staging(self) -> None:
        staging = self.root / "unknown"
        self.assertEqual(self.run_backend(staging, self.manifest(), "NOPE"), 2)
        self.assertFalse(staging.exists())

    def test_incompatible_production_tool_is_code_2_without_staging(self) -> None:
        staging = self.root / "production"
        (self.svg_root / "inspect_svg_plan.py").write_text('print("svg-plan-to-godot 1.18.9")', encoding="utf-8")
        self.assertEqual(self.run_backend(staging, self.manifest(), "FIXTURE"), 2)
        self.assertFalse(staging.exists())

    def test_nonempty_staging_is_rejected_without_overwrite(self) -> None:
        staging = self.root / "occupied"
        staging.mkdir()
        marker = staging / "authored.txt"
        marker.write_text("preserve", encoding="utf-8")
        self.assertEqual(self.run_backend(staging, self.manifest()), 2)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_ready_sector_is_deterministic_and_transforms_frames(self) -> None:
        manifest = self.manifest()
        first, second = self.root / "first", self.root / "second"
        self.assertEqual(self.run_backend(first, manifest), 0)
        self.assertEqual(self.run_backend(second, manifest), 0)
        anchors = json.loads((first / "anchor_frames.json").read_text(encoding="utf-8"))
        frame = anchors["anchors"][0]
        self.assertEqual(frame["type"], "floor")
        self.assertNotIn("kind", frame)
        self.assertEqual(frame["origin"], [12.0, 4.0, 19.0])
        self.assertEqual(frame["bounds"]["polygon_xz"], [[10.0, 20.0], [10.0, 18.0], [11.0, 18.0]])
        self.assertEqual((first / "anchor_frames.json").read_bytes(), (second / "anchor_frames.json").read_bytes())
        self.assertEqual((first / "generation_manifest.json").read_bytes(), (second / "generation_manifest.json").read_bytes())
        self.assertEqual((first / "regeneration_report.json").read_bytes(), (second / "regeneration_report.json").read_bytes())
        for relative in ("preflight_report.json", "Generated/Architecture/conversion_report.json", "Generated/Architecture/fixture.tscn"):
            content = (first / relative).read_text(encoding="utf-8")
            self.assertNotIn(str(self.root), content)
            self.assertEqual(content, (second / relative).read_text(encoding="utf-8"))

    def test_vertical_sector_merges_stair_frames_and_bounds(self) -> None:
        staging = self.root / "vertical"
        self.assertEqual(self.run_backend(staging, self.manifest(vertical=True)), 0)
        anchors = json.loads((staging / "anchor_frames.json").read_text(encoding="utf-8"))["anchors"]
        stair = next(frame for frame in anchors if frame["type"] == "stair_entry")
        self.assertNotIn("kind", stair)
        self.assertEqual(stair["origin"], [10.0, 4.0, 20.0])
        self.assertEqual(stair["bounds"]["clear_bounds_xz"], [8.0, 19.0, 12.0, 21.0])
        self.assertEqual(stair["bounds"]["bottom_y"], 4.0)
        self.assertEqual(stair["bounds"]["top_y"], 7.0)

    def test_legacy_kind_cannot_escape_as_contract_type(self) -> None:
        legacy = dict(SVG_FRAME)
        legacy["kind"] = legacy.pop("type")
        transform = {
            "origin": [0.0, 0.0, 0.0],
            "basis_x": [1.0, 0.0, 0.0],
            "basis_y": [0.0, 1.0, 0.0],
            "basis_z": [0.0, 0.0, 1.0],
        }
        with self.assertRaisesRegex(BACKEND.RegenerationError, "legacy kind=.*not accepted"):
            BACKEND.transform_frame(legacy, transform)

    def test_incomplete_transform_and_inspector_mismatch_are_blocking(self) -> None:
        bad_transform = {"origin": [0, 0, 0], "basis_x": [1, 0, 0], "basis_y": [0, 1, 0]}
        self.assertEqual(self.run_backend(self.root / "bad", self.manifest(transform=bad_transform)), 2)
        (self.root / "mismatch.svg").write_text("<svg/>", encoding="utf-8")
        self.assertEqual(self.run_backend(self.root / "mismatch", self.manifest(source="mismatch.svg")), 2)

    def test_parameterization_derives_surface_ranges_and_exact_holes(self) -> None:
        frame = BACKEND.transform_frame(SVG_FRAME, {
            "origin": [0.0, 0.0, 0.0], "basis_x": [1.0, 0.0, 0.0],
            "basis_y": [0.0, 1.0, 0.0], "basis_z": [0.0, 0.0, 1.0],
        })
        limits = {"normal_offset_m": [0, 0], "height_m": [0, 0], "rotation_deg": {"yaw": [-180, 180], "pitch": [0, 0], "roll": [0, 0]}}
        sector = {"anchor_parameterization": {"defaults_by_type": {"floor": {"placement_limits": limits}}}}
        opening = {"surface": "floor", "applied": True, "polygon_xz_m": [[1.2, 0.1], [1.4, 0.1], [1.4, 0.2], [1.2, 0.2]]}
        result = BACKEND.parameterize_frames([frame], sector, {"surface_openings": [opening]})[0]
        self.assertEqual(result["bounds"]["u_range_m"], [-1.0, 1.0])
        self.assertEqual(result["bounds"]["v_range_m"], [-2.0, -1.0])
        self.assertEqual(result["bounds"]["holes_xz"], [opening["polygon_xz_m"]])

    def test_parameterization_blocks_boundary_clipped_opening(self) -> None:
        frame = BACKEND.transform_frame(SVG_FRAME, {
            "origin": [0.0, 0.0, 0.0], "basis_x": [1.0, 0.0, 0.0],
            "basis_y": [0.0, 1.0, 0.0], "basis_z": [0.0, 0.0, 1.0],
        })
        limits = {"normal_offset_m": [0, 0], "height_m": [0, 0], "rotation_deg": {"yaw": [-180, 180], "pitch": [0, 0], "roll": [0, 0]}}
        sector = {"anchor_parameterization": {"defaults_by_type": {"floor": {"placement_limits": limits}}}}
        opening = {"surface": "floor", "applied": True, "polygon_xz_m": [[0.5, 0.3], [1.5, 0.3], [1.5, 0.7], [0.5, 0.7]]}
        with self.assertRaisesRegex(BACKEND.RegenerationError, "crosses the boundary"):
            BACKEND.parameterize_frames([frame], sector, {"surface_openings": [opening]})

    def test_parameterization_allows_opening_touching_adjacent_surface_edge(self) -> None:
        frame = BACKEND.transform_frame(SVG_FRAME, {
            "origin": [0.0, 0.0, 0.0], "basis_x": [1.0, 0.0, 0.0],
            "basis_y": [0.0, 1.0, 0.0], "basis_z": [0.0, 0.0, 1.0],
        })
        limits = {"normal_offset_m": [0, 0], "height_m": [0, 0], "rotation_deg": {"yaw": [-180, 180], "pitch": [0, 0], "roll": [0, 0]}}
        sector = {"anchor_parameterization": {"defaults_by_type": {"floor": {"placement_limits": limits}}}}
        opening = {"surface": "floor", "applied": True, "polygon_xz_m": [[0.0, 1.0], [2.0, 1.0], [2.0, 2.0], [0.0, 2.0]]}
        result = BACKEND.parameterize_frames([frame], sector, {"surface_openings": [opening]})[0]
        self.assertEqual(result["bounds"]["holes_xz"], [])

    def test_parameterization_ignores_opening_on_different_elevation(self) -> None:
        frame = BACKEND.transform_frame(SVG_FRAME, {
            "origin": [0.0, 0.0, 0.0], "basis_x": [1.0, 0.0, 0.0],
            "basis_y": [0.0, 1.0, 0.0], "basis_z": [0.0, 0.0, 1.0],
        })
        limits = {"normal_offset_m": [0, 0], "height_m": [0, 0], "rotation_deg": {"yaw": [-180, 180], "pitch": [0, 0], "roll": [0, 0]}}
        sector = {"anchor_parameterization": {"defaults_by_type": {"floor": {"placement_limits": limits}}}}
        opening = {"surface": "floor", "elevation_m": 3.0, "applied": True, "polygon_xz_m": [[0.5, 0.3], [1.5, 0.3], [1.5, 0.7], [0.5, 0.7]]}
        result = BACKEND.parameterize_frames([frame], sector, {"surface_openings": [opening]})[0]
        self.assertEqual(result["bounds"]["holes_xz"], [])


if __name__ == "__main__":
    unittest.main()
