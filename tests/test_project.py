"""Repository portability and configuration checks; no model or mouse loop is started."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from aim_ai.runtime import (
    Config, build_search_regions, build_tracking_region, load_config, resolve_model_path,
)
from training import prepare_dataset_config


class ConfigurationTests(unittest.TestCase):
    def test_partial_configuration_preserves_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            path.write_text(json.dumps({"confidence": 0.4}), encoding="utf-8")
            loaded = load_config(path)
            self.assertEqual(loaded.confidence, 0.4)
            self.assertEqual(loaded.search_tile_width, Config().search_tile_width)

    def test_invalid_configuration_fails_before_runtime(self):
        bad_values = (
            {"unknown_option": 1}, {"search_stride_x": 2000}, {"aimlab_vertical_fov": 180},
            {"search_tile_width": "1344"}, {"confidence": 0}, {"max_mouse_step": -1},
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            for values in bad_values:
                with self.subTest(values=values):
                    path.write_text(json.dumps(values), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_example_configuration_loads(self):
        path = Path(__file__).resolve().parents[1] / "configs" / "profile.example.json"
        self.assertEqual(load_config(path).expected_screen_width, 3840)


class PortabilityTests(unittest.TestCase):
    def test_weight_precedence_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / "Aim_Bot_Runs" / "5080_training" / "weights" / "aim.pt"
            legacy.parent.mkdir(parents=True)
            legacy.touch()
            self.assertEqual(resolve_model_path(None, root), legacy)
            canonical = root / "weights" / "aim.pt"
            canonical.parent.mkdir()
            canonical.touch()
            self.assertEqual(resolve_model_path(None, root), canonical)
            with self.assertRaises(FileNotFoundError):
                resolve_model_path(root / "missing.pt", root)

    def test_dataset_root_is_relative_to_yaml(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "configs" / "dataset.yaml"
            source.parent.mkdir()
            source.write_text("path: ../data\ntrain: train/images\nval: valid/images\n", encoding="utf-8")
            prepared = prepare_dataset_config(source)
            self.assertEqual(Path(prepared["path"]), (root / "data").resolve())
            self.assertEqual(prepared["train"], "train/images")

    def test_search_regions_cover_screen_rows_and_columns(self):
        config = Config()
        for width, height in ((3840, 2160), (1920, 1080), (800, 600)):
            with self.subTest(resolution=(width, height)):
                regions = build_search_regions(width, height, config)
                horizontal = np.zeros(width, dtype=bool)
                vertical = np.zeros(height, dtype=bool)
                for region in regions:
                    self.assertGreaterEqual(region.left, 0)
                    self.assertGreaterEqual(region.top, 0)
                    self.assertLessEqual(region.left + region.width, width)
                    self.assertLessEqual(region.top + region.height, height)
                    horizontal[region.left:region.left + region.width] = True
                    vertical[region.top:region.top + region.height] = True
                self.assertTrue(horizontal.all())
                self.assertTrue(vertical.all())

    def test_tracking_crop_stays_inside_screen(self):
        for focus in ((-100, -100), (4000, 2300), (1920, 1080)):
            region = build_tracking_region(np.array(focus), 3840, 2160, Config())
            self.assertGreaterEqual(region.left, 0)
            self.assertGreaterEqual(region.top, 0)
            self.assertLessEqual(region.left + region.width, 3840)
            self.assertLessEqual(region.top + region.height, 2160)


if __name__ == "__main__":
    unittest.main()
