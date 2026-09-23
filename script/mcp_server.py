"""MCP server exposing the Crayotter editing tools over the Model Context Protocol.

Run with:  python -m script.mcp_server
Requires the optional `mcp` package (pip install mcp). Every tool in
script.tools.ALL_TOOLS is registered under its existing name, so MCP clients
(Claude Desktop, Cursor, etc.) see the same editing surface as the agent graph.
"""

from __future__ import annotations

import functools
import inspect
import sys


def _build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise SystemExit(
            "mcp package not installed; install it with `pip install mcp` "
            "to run the Crayotter MCP server"
        ) from exc

    from script.tools import ALL_TOOLS

    server = FastMCP("crayotter")
    for langchain_tool in ALL_TOOLS:
        func = getattr(langchain_tool, "func", None) or getattr(langchain_tool, "coroutine", None)
        if func is None:
            continue
        # FastMCP infers the schema from the signature/docstring of the wrapped
        # function; functools.wraps carries the LangChain tool's metadata over.
        server.tool(name=langchain_tool.name, description=langchain_tool.description or "")(
            functools.wraps(func)(func)
        )
    return server


def main() -> None:
    server = _build_server()
    server.run()


if __name__ == "__main__":
    sys.exit(main())
