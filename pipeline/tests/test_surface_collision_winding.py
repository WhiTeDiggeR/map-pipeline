
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from pathlib import Path

from regenerate_sector import normalize_surface_collision_winding


class SurfaceCollisionWindingTests(unittest.TestCase):
    def test_floor_and_ceiling_fronts_are_opposite_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            scene = Path(directory) / "scene.tscn"
            scene.write_text('[sub_resource type="ConcavePolygonShape3D" id="ConcaveFloor_1"]\ndata = PackedVector3Array(0, 0, 0, 0, 0, 1, 1, 0, 0)\n[sub_resource type="ConcavePolygonShape3D" id="ConcaveCeiling_1"]\ndata = PackedVector3Array(0, 3, 0, 1, 3, 0, 0, 3, 1)\n', encoding="utf-8")
            normalize_surface_collision_winding(scene)
            text = scene.read_text(encoding="utf-8")
            self.assertIn('PackedVector3Array(0, 0, 0, 1, 0, 0, 0, 0, 1)', text)
            self.assertIn('PackedVector3Array(0, 3, 0, 0, 3, 1, 1, 3, 0)', text)
            normalize_surface_collision_winding(scene)
            self.assertEqual(scene.read_text(encoding="utf-8"), text)
