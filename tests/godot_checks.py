#!/usr/bin/env python3
"""Run the Godot checks of map-pipeline in a throwaway project that mounts this checkout at res://addons/map_pipeline.

Needs Godot 4.7: GODOT_BIN or `godot` on PATH. Exit 1 if a check fails or does not print its success marker.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGINS = ["res://addons/map_pipeline/godot/regeneration_editor/plugin.cfg", "res://addons/map_pipeline/godot/anchor_editor/plugin.cfg"]
CHECKS = [
    ("res://addons/map_pipeline/godot/anchors/anchor_runtime_check.gd", r"MAP_ANCHOR_RUNTIME_OK"),
    ("res://addons/map_pipeline/godot/anchors/anchor_surface_check.gd", r"MAP_SURFACE_CHECK checks=[0-9]+ failures=0"),
    ("res://addons/map_pipeline/godot/anchors/sector_anchor_controller_check.tscn", r"MAP_SECTOR_ANCHOR_CONTROLLER_OK"),
    ("res://addons/map_pipeline/godot/anchor_editor/editor_operations_check.gd", r"MAP_EDITOR_BINDING_OK"),
    ("res://addons/map_pipeline/godot/regeneration_editor/regeneration_editor_check.gd", r"MAP_REGENERATION_EDITOR_CHECK_OK"),
]


def main() -> int:
    godot = os.environ.get("GODOT_BIN") or shutil.which("godot")
    if not godot:
        print("Godot is not available: set GODOT_BIN")
        return 2
    failed = []
    with tempfile.TemporaryDirectory(prefix="map-pipeline-godot-") as temporary:
        project = Path(temporary)
        shutil.copytree(REPO, project / "addons/map_pipeline", ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        (project / "main.tscn").write_text('[gd_scene format=3]\n\n[node name="Root" type="Node"]\n', encoding="utf-8")
        enabled = ", ".join(f'"{plugin}"' for plugin in PLUGINS)
        (project / "project.godot").write_text(
            'config_version=5\n\n[application]\nrun/main_scene="res://main.tscn"\n\n'
            f"[editor_plugins]\nenabled=PackedStringArray({enabled})\n", encoding="utf-8")
        subprocess.run([godot, "--headless", "--editor", "--path", str(project), "--quit"], capture_output=True, text=True, timeout=600)
        for resource, marker in CHECKS:
            argv = [godot, "--headless", "--path", str(project)]
            argv += [resource] if resource.endswith(".tscn") else ["--script", resource]
            process = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            output = process.stdout + process.stderr
            ok = process.returncode == 0 and re.search(marker, output) is not None
            print(("PASS  " if ok else "FAIL  ") + resource)
            if not ok:
                print("\n".join(output.splitlines()[-25:]))
                failed.append(resource)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
