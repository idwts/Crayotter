from __future__ import annotations

from contextlib import ExitStack
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import runtime_paths
from script import setup_ffmpeg


class MacApplicationPathTests(unittest.TestCase):
    def test_frozen_mac_data_stays_outside_the_signed_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with (patch.dict(os.environ, {}, clear=True),
                  patch.object(runtime_paths, "is_frozen", return_value=True),
                  patch.object(runtime_paths.sys, "platform", "darwin"),
                  patch.object(Path, "home", return_value=home)):
                self.assertEqual(runtime_paths.get_runtime_root(), home / "Library/Application Support/Crayotter")

    def test_explicit_runtime_directory_still_overrides_mac_default(self):
        with tempfile.TemporaryDirectory() as directory:
            with (patch.dict(os.environ, {"CRAYOTTER_RUNTIME_ROOT": directory}, clear=True),
                  patch.object(runtime_paths, "is_frozen", return_value=True),
                  patch.object(runtime_paths.sys, "platform", "darwin")):
                self.assertEqual(runtime_paths.get_runtime_root(), Path(directory).resolve())


class RuntimeBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / "runtime"
        self.bundle = self.root / "bundle"
        self.runtime.mkdir()
        self.bundle.mkdir()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(runtime_paths, "get_runtime_root", return_value=self.runtime))
        self.stack.enter_context(patch.object(runtime_paths, "get_bundle_root", return_value=self.bundle))
        self.stack.enter_context(patch.dict(os.environ, {"PATH": ""}, clear=True))

    def binary(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        return path

    def platform(self, system, machine):
        self.stack.enter_context(patch.object(runtime_paths.platform, "system", return_value=system))
        self.stack.enter_context(patch.object(runtime_paths.platform, "machine", return_value=machine))

    def configure(self):
        with (patch.object(runtime_paths, "ensure_runtime_dirs"),
              patch.object(runtime_paths, "load_runtime_env_file"),
              patch.object(runtime_paths, "_seed_runtime_tree")):
            runtime_paths.configure_runtime_environment()

    def test_apple_silicon_selects_only_matching_macos_architecture(self):
        self.platform("Darwin", "arm64")
        expected = self.binary(self.bundle / "script/dep/macos/arm64/ffmpeg")
        self.binary(self.bundle / "script/dep/macos/x86_64/ffmpeg")
        self.binary(self.bundle / "script/dep/windows/ffmpeg.exe")
        self.assertEqual(runtime_paths.resolve_binary("ffmpeg"), expected)
        roots = runtime_paths._binary_search_roots()
        self.assertNotIn(self.bundle / "script/dep/windows", roots)
        self.assertNotIn(self.bundle / "script/dep/macos/x86_64", roots)

    def test_intel_mac_does_not_fall_back_to_arm_executable(self):
        self.platform("Darwin", "x86_64")
        self.binary(self.bundle / "script/dep/macos/arm64/ffmpeg")
        self.assertIsNone(runtime_paths.resolve_binary("ffmpeg"))
        expected = self.binary(self.bundle / "script/dep/macos/x86_64/ffmpeg")
        self.assertEqual(runtime_paths.resolve_binary("ffmpeg"), expected)

    def test_windows_keeps_legacy_directory_and_ffprobe_shim(self):
        self.platform("Windows", "AMD64")
        ffmpeg = self.binary(self.bundle / "script/dep/windows/ffmpeg.exe")
        ffprobe = self.binary(self.bundle / "script/dep/windows/ffprobe.cmd")
        self.configure()
        self.assertEqual(os.environ["FFMPEG_BIN"], str(ffmpeg))
        self.assertEqual(os.environ["FFPROBE_BIN"], str(ffprobe))
        self.assertNotIn("macos", os.environ["PATH"])

    def test_linux_uses_path_when_no_matching_bundle_exists(self):
        self.platform("Linux", "aarch64")
        with patch.object(runtime_paths.shutil, "which", return_value="/usr/bin/ffmpeg"):
            self.assertEqual(runtime_paths.resolve_binary("ffmpeg"), Path("/usr/bin/ffmpeg"))
        self.assertEqual(runtime_paths.binary_platform(), ("linux", "arm64"))

    def test_custom_overrides_take_precedence_for_variables_and_path(self):
        self.platform("Darwin", "arm64")
        self.binary(self.bundle / "script/dep/macos/arm64/ffmpeg")
        custom = self.binary(self.root / "custom/ffmpeg")
        probe = self.binary(self.root / "custom/ffprobe")
        os.environ.update(FFMPEG_BIN=str(custom), FFPROBE_BIN=str(probe))
        self.configure()
        self.assertEqual(os.environ["FFMPEG_BIN"], str(custom))
        self.assertEqual(os.environ["FFPROBE_BIN"], str(probe))
        self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(custom.parent))

    def test_prepend_reorders_existing_entries_without_duplicates(self):
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        os.environ["PATH"] = os.pathsep.join([str(second), str(first)])
        runtime_paths._prepend_path([first, first])
        runtime_paths._prepend_path([first])
        self.assertEqual(os.environ["PATH"].split(os.pathsep), [str(first), str(second)])

    def test_directory_is_not_resolved_as_executable(self):
        self.platform("Darwin", "arm64")
        (self.bundle / "script/dep/macos/arm64/ffmpeg").mkdir(parents=True)
        self.assertIsNone(runtime_paths.resolve_binary("ffmpeg"))


class BinaryDownloadTests(unittest.TestCase):
    def archive(self, content):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../ffmpeg", content)
        stream.seek(0)
        return stream

    def test_bad_checksum_never_publishes_a_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "macos/arm64/ffmpeg"
            with patch.object(setup_ffmpeg, "urlopen", return_value=self.archive(b"untrusted")):
                with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                    setup_ffmpeg.install_binary("ffmpeg", target, "https://example.test/archive.zip", "0" * 64)
            self.assertFalse(target.exists())

    def test_verified_member_is_written_only_to_requested_path(self):
        content = b"verified executable"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "macos/arm64/ffmpeg"
            with patch.object(setup_ffmpeg, "urlopen", return_value=self.archive(content)):
                setup_ffmpeg.install_binary("ffmpeg", target, "https://example.test/archive.zip", hashlib.sha256(content).hexdigest())
            self.assertEqual(target.read_bytes(), content)
            self.assertFalse((Path(directory) / "ffmpeg").exists())


if __name__ == "__main__":
    unittest.main()
