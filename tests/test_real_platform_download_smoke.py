from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@unittest.skipUnless(
    _enabled(os.environ.get("CRAYOTTER_RUN_REAL_PLATFORM_SMOKE")),
    "set CRAYOTTER_RUN_REAL_PLATFORM_SMOKE=1 to run real platform download smoke tests",
)
class RealPlatformDownloadSmokeTests(unittest.TestCase):
    PLATFORMS = (
        ("douyin", "CRAYOTTER_SMOKE_DOUYIN_URL"),
        ("kuaishou", "CRAYOTTER_SMOKE_KUAISHOU_URL"),
        ("xiaohongshu", "CRAYOTTER_SMOKE_XIAOHONGSHU_URL"),
    )

    def test_real_platform_download_smoke(self) -> None:
        import script.tools.download_material_video as module

        download_tool = module.download_material_video
        with tempfile.TemporaryDirectory(prefix="crayotter_real_platform_smoke_") as tmp:
            tmp_path = Path(tmp)

            def safe_output(filename: str, default_stem: str = "material_video") -> Path:
                stem = Path(filename or default_stem).stem or default_stem
                return tmp_path / f"{stem}.mp4"

            with patch.object(module, "WORKSPACE", tmp_path), patch.object(
                module, "_safe_output_video_path", safe_output
            ):
                for source, url_env in self.PLATFORMS:
                    with self.subTest(source=source):
                        url = os.environ.get(url_env, "").strip()
                        if not url:
                            self.skipTest(f"set {url_env} to run {source} smoke")

                        raw = download_tool.invoke(
                            {
                                "url": url,
                                "source": source,
                                "filename": f"smoke_{source}",
                                "fallback_query": "校园宣传片",
                                "fallback_to_bilibili": True,
                            }
                        )
                        try:
                            result = json.loads(str(raw))
                        except json.JSONDecodeError as exc:
                            self.fail(f"{source} returned non-JSON smoke response: {str(raw)[:500]}; {exc}")

                        self.assertIsInstance(result, dict, f"{source} returned JSON but not an object: {result!r}")
                        status = result.get("status")
                        self.assertIn(status, {"success", "error"}, f"{source} returned unexpected status: {result!r}")

                        if status == "success":
                            output_path = Path(str(result.get("path") or ""))
                            self.assertTrue(
                                output_path.exists(),
                                f"{source} reported success but output path is missing: {output_path}",
                            )
                            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else result
                            for key in ("source", "original_source", "standardized"):
                                self.assertIn(key, metadata, f"{source} success metadata missing {key}: {result!r}")
                            print(
                                f"[real-platform-smoke] {source}: success path={output_path} "
                                f"source={metadata.get('source')} original_source={metadata.get('original_source')}"
                            )
                            continue

                        self.assertIn("error_type", result, f"{source} error response missing error_type: {result!r}")
                        self.assertIn("error", result, f"{source} error response missing error: {result!r}")
                        print(
                            f"[real-platform-smoke] {source}: error_type={result.get('error_type')} "
                            f"error={str(result.get('error') or '')[:500]}"
                        )


if __name__ == "__main__":
    unittest.main()
