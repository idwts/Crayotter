"""Local stdio MCP adapter for the authoritative Crayotter tool registry."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from pathlib import Path
import sys


def create_server(registry):
    from mcp.server.lowlevel import Server
    from mcp.types import CallToolResult, TextContent, Tool

    server = Server("crayotter")

    @server.list_tools()
    async def list_tools():
        return [Tool(**definition.to_mcp()) for definition in registry.definitions()]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict | None):
        # Serial deterministic execution: external requests cannot introduce a
        # separate worker pool or race the workspace. Job DAGs retain the scheduler.
        with redirect_stdout(sys.stderr):
            result = registry.call(name, arguments)
        return CallToolResult(
            content=[TextContent(type="text", text=result.model_dump_json())],
            structuredContent=result.model_dump(mode="json"), isError=result.is_error,
        )

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Crayotter tools over local MCP stdio")
    parser.add_argument("--list-tools", action="store_true", help="Print tool schemas as JSON and exit")
    parser.add_argument("--workspace", type=Path, help="Persistent scratch workspace for this MCP server")
    args = parser.parse_args(argv)
    from app.runtime_paths import configure_runtime_environment, load_runtime_env_file
    load_runtime_env_file()
    configure_runtime_environment()
    import os
    from app.runtime_paths import get_runtime_root
    from uuid import uuid4
    if args.workspace:
        os.environ["CRAYOTTER_TASK_WORKSPACE"] = str(args.workspace.expanduser().resolve())
    else:
        os.environ.setdefault("CRAYOTTER_TASK_WORKSPACE", str(get_runtime_root() / "app_state" / "mcp" / uuid4().hex))
    # Support both python -m script.mcp_server and python script/run_mcp.py.
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    with redirect_stdout(sys.stderr):
        from tools import TOOL_REGISTRY, configure
        from script.runtime import RuntimeSettings
        defaults = RuntimeSettings()
        api_key = os.environ.get("CRAYOTTER_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
        base_url = os.environ.get("CRAYOTTER_BASE_URL") or defaults.base_url
        configure(
            api_key=api_key, base_url=base_url,
            model_name=os.environ.get("CRAYOTTER_MODEL_NAME") or defaults.model_name,
            video_api_key=os.environ.get("CRAYOTTER_VIDEO_API_KEY") or api_key,
            video_base_url=os.environ.get("CRAYOTTER_VIDEO_BASE_URL") or base_url,
            video_model_name=os.environ.get("CRAYOTTER_VIDEO_MODEL_NAME") or defaults.video_model_name,
            tts_api_key=os.environ.get("CRAYOTTER_TTS_API_KEY") or api_key,
            tts_base_url=os.environ.get("CRAYOTTER_TTS_BASE_URL") or defaults.base_url,
            tts_model_name=os.environ.get("CRAYOTTER_TTS_MODEL_NAME") or defaults.tts_model_name,
        )
    if args.list_tools:
        import json
        print(json.dumps([d.to_mcp() for d in TOOL_REGISTRY.definitions()], ensure_ascii=False, indent=2))
        return
    try:
        server = create_server(TOOL_REGISTRY)
    except ImportError as exc:
        raise SystemExit("MCP support requires: pip install -r requirements-mcp.txt") from exc

    async def run():
        from mcp.server.stdio import stdio_server
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
