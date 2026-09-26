"""Functional test: the Crayotter MCP server over real stdio transport.

Spawns `python -m script.mcp_server` as a subprocess and drives it with a
real MCP client: initialize handshake (fails if banner prints pollute
stdout), list tools (must cover ALL_TOOLS), then call inspect_video_duration
on a real cv2-written clip and require the unified JSON success payload.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    MCP_AVAILABLE = False


@unittest.skipUnless(MCP_AVAILABLE, "optional mcp package not installed")
class McpServerStdioTests(unittest.TestCase):
    def test_stdio_handshake_list_and_real_call(self):
        asyncio.run(self._run_client())

    async def _run_client(self):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        params = StdioServerParameters(
            command=sys.executable,
            args=["-X", "utf8", "-m", "script.mcp_server"],
            cwd=str(REPO_ROOT),
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                # Handshake itself proves stdout carries clean JSON-RPC frames.
                await session.initialize()

                from script.tools import ALL_TOOLS

                expected = {t.name for t in ALL_TOOLS}
                listed = await session.list_tools()
                names = {t.name for t in listed.tools}
                self.assertEqual(names, expected)

                # Real tool call through the wire: write a tiny clip with cv2,
                # then ask the MCP-exposed tool for its duration.
                import cv2
                import numpy as np

                # The tool only accepts files inside WORKSPACE.
                from script.tools._shared import WORKSPACE

                clip = WORKSPACE / "mcp_server_test_clip.mp4"
                try:
                    writer = cv2.VideoWriter(
                        str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 48)
                    )
                    frame = np.zeros((48, 64, 3), dtype="uint8")
                    for _ in range(30):
                        writer.write(frame)
                    writer.release()
                    result = await session.call_tool(
                        "inspect_video_duration", {"video_path": str(clip)}
                    )
                finally:
                    clip.unlink(missing_ok=True)
                self.assertFalse(result.is_error, result.content)
                payload = json.loads(result.content[0].text)
                self.assertEqual(payload["status"], "success")
                self.assertAlmostEqual(payload["duration_seconds"], 3.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
