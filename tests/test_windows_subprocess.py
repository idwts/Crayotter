from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tools import _shared


class WindowsSubprocessTests(unittest.TestCase):
    def test_hidden_subprocess_kwargs_hide_child_console_on_windows(self) -> None:
        kwargs = _shared._hidden_subprocess_kwargs()

        if os.name != "nt":
            self.assertEqual(kwargs, {})
            return

        self.assertTrue(
            kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW,
            "Windows child processes must be created without a visible console.",
        )
        startupinfo = kwargs["startupinfo"]
        self.assertTrue(startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startupinfo.wShowWindow, subprocess.SW_HIDE)
