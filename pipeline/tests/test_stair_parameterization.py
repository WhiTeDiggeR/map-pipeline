"""Producer semantics must survive the SVG/stair adapter boundary."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copy
import unittest

import regenerate_sector as backend


class StairParameterizationTests(unittest.TestCase):
    def test_physical_wall_face_and_limits_are_preserved(self):
        frame = {
            "anchor_id": "shaft-east", "type": "wall", "role": "shaft_face",
            "origin": [3, 3.6, 3.5], "forward": [0, 0, -1],
            "normal": [1, 0, 0], "up": [0, 1, 0],
            "bounds": {"length_m": 7, "height_m": 2.2, "thickness_m": 0.09},
            "placement_limits": {"normal_offset_m": [0, 0.09], "height_m": [0, 2.2]},
        }
        before = copy.deepcopy(frame)
        sector = {"anchor_parameterization": {"defaults_by_type": {}}}
        result = backend.parameterize_frames([frame], sector, {}, producer="stairs")[0]
        self.assertEqual(result["origin"], before["origin"])
        self.assertEqual(result["placement_limits"], before["placement_limits"])
        self.assertEqual(result["bounds"]["along_range_m"], [0, 7])
        self.assertEqual(frame, before)

    def test_passage_forward_is_not_reinterpreted_as_width_axis(self):
        frame = {
            "anchor_id": "entry", "type": "stair_entry", "role": "lower_entry",
            "origin": [-53.6, -6, 6], "forward": [0, 0, 1],
            "normal": [-1, 0, 0], "up": [0, 1, 0],
            "bounds": {"clear_width_m": 1.5},
            "placement_limits": {"normal_offset_m": [-0.75, 0.75], "height_m": [0, 0]},
        }
        result = backend.parameterize_frames([frame], {"anchor_parameterization": {"defaults_by_type": {}}}, {}, producer="stairs")[0]
        self.assertEqual(result["bounds"], frame["bounds"])
        self.assertEqual(result["forward"], [0, 0, 1])

    def test_svg_opening_cannot_cut_generator_owned_landing(self):
        frame = {
            "anchor_id": "landing", "type": "floor", "role": "landing",
            "origin": [0, -3, 0], "forward": [0, 0, -1],
            "normal": [0, 1, 0], "up": [0, 1, 0],
            "bounds": {"polygon_xz": [[0, 0], [4, 0], [4, 2], [0, 2]], "elevation_m": -3},
            "placement_limits": {"normal_offset_m": [0, 0], "height_m": [0, 0]},
        }
        opening = {"surface": "floor", "applied": True, "polygon_xz_m": [[0, 0], [4, 0], [4, 2], [0, 2]]}
        result = backend.parameterize_frames([frame], {"anchor_parameterization": {"defaults_by_type": {}}}, {"surface_openings": [opening]}, producer="stairs")[0]
        self.assertEqual(result["bounds"]["holes_xz"], [])
        self.assertEqual(result["bounds"]["u_range_m"], [-2, 0])
        self.assertEqual(result["bounds"]["v_range_m"], [0, 4])
