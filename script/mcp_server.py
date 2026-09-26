"""MCP server exposing the Crayotter editing tools over the Model Context Protocol.

Run with:  python -m script.mcp_server
Requires the optional `mcp` package (pip install mcp). Every tool in
script.tools.ALL_TOOLS is registered under its existing name, so MCP clients
(Claude Desktop, Cursor, etc.) see the same editing surface as the agent graph.
"""

from __future__ import annotations

import sys


def _build_server():
    try:
        # mcp 1.x
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        try:
            # mcp 2.x renamed FastMCP to MCPServer (same tool()/run() surface)
            from mcp.server.mcpserver import MCPServer as FastMCP
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise SystemExit(
                "mcp package not installed; install it with `pip install mcp` "
                "to run the Crayotter MCP server"
            ) from exc

    from pathlib import Path

    # The tools package imports sibling top-level modules (model_runtime, ...)
    # that live next to it in script/, mirroring how tests and graph.py run.
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    from script.tools import ALL_TOOLS

    server = FastMCP("crayotter")
    for langchain_tool in ALL_TOOLS:
        func = getattr(langchain_tool, "func", None) or getattr(langchain_tool, "coroutine", None)
        if func is None:
            continue
        # Register the raw callable: name/description come from the LangChain
        # tool, and the schema is inferred from the original annotated
        # signature. Never functools.wraps(func)(func) here — that would set
        # func.__wrapped__ = func and send typing.get_type_hints into an
        # infinite unwrap loop.
        server.tool(name=langchain_tool.name, description=langchain_tool.description or "")(func)
    return server


def main() -> None:
    server = _build_server()
    server.run()


if __name__ == "__main__":
    sys.exit(main())
