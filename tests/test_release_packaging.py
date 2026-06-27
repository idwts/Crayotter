from __future__ import annotations

import unittest
import importlib.util
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class ReleasePackagingTests(unittest.TestCase):
    def test_pyinstaller_spec_collects_dynamic_bilibili_dependencies(self) -> None:
        spec_text = (ROOT / "packaging" / "crayotter.spec").read_text(encoding="utf-8")

        self.assertIn('collect_all("bilibili_api")', spec_text)
        self.assertIn("*bilibili_hiddenimports", spec_text)
        self.assertIn("*bilibili_datas", spec_text)

    def test_icon_source_is_centered_on_square_canvas(self) -> None:
        module_path = ROOT / "packaging" / "prepare_windows_assets.py"
        spec = importlib.util.spec_from_file_location("prepare_windows_assets", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        wide_logo = Image.new("RGBA", (1791, 610), (255, 0, 0, 255))
        icon = module.build_square_icon_image(wide_logo, size=256, padding_ratio=0.08)

        self.assertEqual(icon.size, (256, 256))
        bbox = icon.getbbox()
        self.assertIsNotNone(bbox)
        if bbox is None:
            return
        content_width = bbox[2] - bbox[0]
        content_height = bbox[3] - bbox[1]
        self.assertGreater(content_width, content_height)
        self.assertGreater(bbox[1], 0)
        self.assertGreater(256 - bbox[3], 0)


if __name__ == "__main__":
    unittest.main()
