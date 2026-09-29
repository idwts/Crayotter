# Unified Tool Contract, MCP Server, and ffprobe Shim

> Introduced in `10c1eea` / hardened in `5b5016d` (branch `maint-2026-09-24`).
> 中文摘要见文末。

## 1. Unified tool result contract

Every editing tool in `script/tools/` (the 27 tools in `script/tools/__init__.py:ALL_TOOLS`)
returns exactly one of two shapes:

- **Success** — a JSON string produced by `tool_success(**fields)`
  (`script/tools/_shared.py`):

  ```json
  {"status": "success", "...": "tool-specific fields"}
  ```

- **Failure** — plain text produced by `tool_error(action, exc)`:
  `"{action}出错: {exc}"`. The substring `出错` is a **load-bearing marker**:
  `script/graph.py` and `script/phase3_rl/` detect tool failure by it. Never
  reword it.

Consumers (the agent graph, the RL harness, the MCP server) can therefore rely
on: `json.loads(result)["status"] == "success"` on the happy path, and the
`出错` marker otherwise.

### Intentional exceptions (do not "fix" these)

| Tool | Shape | Why it is load-bearing |
|---|---|---|
| `analyze_video` | prose report | `graph.py:2211` substring-matches it; `phase3_rl` parses paths out of it with regexes; locked by tests |
| `validate_*` tools | `pass`/`fail` semantics in their status field | validators are consumed for their verdict wording |
| `search_youtobe` | bare JSON list of candidates | `script/tools/source_adapters/youtube.py` requires exactly this |

When adding a new tool, return `tool_success(...)` / `tool_error(...)` unless it
is one of these documented exceptions.

## 2. MCP server

`script/mcp_server.py` exposes all 27 tools over the Model Context Protocol
(stdio), so MCP clients (Claude Desktop, Cursor, …) see the same editing
surface as the agent graph.

- **Run**: `python -m script.mcp_server` from the repo root, or
  `python script/mcp_server.py` from anywhere (the server inserts both
  `script/` and the repo root into `sys.path`).
- **Requirements**: the optional `mcp` package. Both mcp 1.x
  (`mcp.server.fastmcp.FastMCP`) and mcp 2.x (`mcp.server.mcpserver.MCPServer`,
  renamed but same surface) are supported.
- Tools are registered by their original LangChain names with schemas inferred
  from the annotated signatures. Registration uses the raw function objects
  directly — wrapping them with `functools.wraps(func)(func)` would make
  `func.__wrapped__ is func` and send `typing.get_type_hints` into an infinite
  unwrap loop (this actually hung the server before; do not reintroduce).
- **stdio hygiene**: nothing may be printed to stdout except protocol frames.
  The workspace/banner logs in `script/tools/_shared.py` go to **stderr** for
  this reason.
- Tools that take file paths resolve them inside the workspace
  (`_resolve_workspace_input_path`); outputs land in the workspace as well.

Smoke/E2E evidence: `tests/test_mcp_server.py` (real stdio handshake, tool
listing, a real cv2-verified cut) and `functional_20260926_e2e_mcp.py`
(full cut ×3 → merge → inspect → export 720p experiment with independent cv2
verification; report in `functional_20260926_e2e_mcp.json`).

## 3. ffprobe shim capability boundary

`script/ffprobe_shim.py` backs the `ffprobe` binary when the real one is
absent. It measures with cv2 (+moviepy for audio detection) and can *honestly*
serve only:

- bare duration (`-show_entries format=duration ... -of default=...`) → a
  single float line (the historical contract);
- `-of json` probes whose requested fields are a subset of:
  - `format`: `duration`
  - `stream`: `index`, `codec_type`, `width`, `height`, `avg_frame_rate`,
    `r_frame_rate`
- audio-presence probes (`-select_streams a ... stream=index`).

Anything beyond that (e.g. `codec_name`, `pix_fmt`, `size`, `bit_rate`) exits
**rc=1 with a stderr message instead of fabricating values** — consumers such
as `media_consistency` must see a loud failure, never silently invented
metadata. The dispatch order in `main()` is: audio probe → JSON probe → bare
duration.

---

## 中文摘要

- **统一返回契约**：`script/tools/` 全部 27 个工具成功时返回
  `tool_success(...)` 生成的 JSON（含 `"status": "success"`），失败时返回
  `tool_error` 的 `"...出错: ..."` 文本——`出错` 是 graph/RL 识别的承重标记，不可改写。
  例外三个（勿"修复"）：`analyze_video` 散文、`validate_*` 的 pass/fail、
  `search_youtobe` 裸列表，均有下游消费者锁定。
- **MCP server**：`python -m script.mcp_server`（仓库根目录）或直接
  `python script/mcp_server.py`；兼容 mcp 1.x/2.x；27 个工具同名暴露；
  绝不能用 `functools.wraps(func)(func)` 注册（`__wrapped__` 自指会让
  `get_type_hints` 死循环）；stdout 只走协议帧，banner 一律 stderr。
- **ffprobe shim 能力边界**：只能诚实提供 `format=duration` 与流字段
  `index/codec_type/width/height/avg_frame_rate/r_frame_rate`；请求超集时
  rc=1 响亮失败，绝不编造元数据。
