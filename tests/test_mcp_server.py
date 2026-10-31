from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


@unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK not installed")
class McpServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_discovery_and_calls_from_another_directory(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as runtime:
            parameters = StdioServerParameters(
                command=sys.executable, args=[str(root / "script/run_mcp.py")],
                cwd=runtime, env={**os.environ, "CRAYOTTER_RUNTIME_ROOT": runtime},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    tools = {tool.name: tool for tool in listing.tools}
                    self.assertIn("cut_video", tools)
                    self.assertIn("export_video", tools)
                    self.assertIn("status", tools["cut_video"].outputSchema["properties"])
                    presets = await session.call_tool("list_transition_presets", {})
                    self.assertFalse(presets.isError)
                    self.assertEqual(presets.structuredContent["status"], "success")
                    invalid = await session.call_tool("cut_video", {})
                    self.assertTrue(invalid.isError)
                    self.assertEqual(invalid.structuredContent["error"]["code"], "invalid_arguments")
                    missing = await session.call_tool("inspect_video_duration", {"video_path": "missing.mp4"})
                    self.assertTrue(missing.isError)
                    self.assertEqual(missing.structuredContent["status"], "error")
                    unknown = await session.call_tool("not_registered", {})
                    self.assertTrue(unknown.isError)


if __name__ == "__main__":
    unittest.main()
