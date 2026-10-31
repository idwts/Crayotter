# 工具协议与原生 FFmpeg

## 当前项目

Crayotter 将自然语言视频需求转成成片：Phase 1 搜索、下载和分析素材，Phase 2 生成剪辑研究与蓝图，Phase 3 规划并执行剪辑、配音、字幕与导出。当前图还包含计划审阅、计划校验和用户指导检查点。阶段内 DAG 继续由 `ResourceScheduler` 执行，并保留断点、资源池、写冲突及产物验收机制。

## 统一工具入口

`script/tools/__init__.py` 的 `TOOL_REGISTRY` 是公开工具的统一注册入口。它由现有类型化工具函数生成描述及 JSON Schema，当前公开 27 个工具；未注册的旧工具不会自动暴露。

内部结构化调用：

```python
# 与现有 graph 一样，需要将仓库根目录和 script 加入 Python 导入路径。
from tools import TOOL_REGISTRY

result = TOOL_REGISTRY.call("cut_video", {
    "input_path": "/absolute/task/workspace/source.mp4",
    "start_time": 1.0,
    "end_time": 3.0,
    "output_name": "intro",
})
print(result.model_dump())
```

统一结果 `ToolResult`：

```json
{
  "status": "success",
  "data": {"status": "success", "path": "/actual/path/intro.mp4", "duration": 2.0},
  "text": "原工具返回的文本或 JSON 字符串",
  "error": null
}
```

失败时 `status` 为 `error`，`error` 包含 `code` 和 `message`。错误码为 `unknown_tool`、`invalid_arguments`、`execution_error`。参数使用函数生成的 Pydantic schema 严格校验，拒绝多余参数。纯文本成功结果保存在 `text`，JSON 结果同时解析到 `data`。

兼容层 `ALL_TOOLS = TOOL_REGISTRY.langchain_tools()` 保留原 LangChain 工具对象、名称、参数、返回值与异常行为。现有图和 RL 调用无需改写其结果解析逻辑；新调用方应使用 `TOOL_REGISTRY.call()`。这次没有将原工具函数的返回值逐一改为新结构，以免破坏旧调用方和已有检查点。

## MCP 服务

安装项目依赖后安装可选 MCP SDK：

```bash
pip install -r requirements.txt
pip install -r requirements-mcp.txt
python script/run_mcp.py
```

服务使用标准 MCP stdio，支持 `tools/list` 与 `tools/call`；每个工具都公布 `inputSchema` 和统一 `outputSchema`，调用结果同时返回 `content`、`structuredContent` 和 `isError`。它与内部入口使用同一个注册表。SDK 接口参考 [官方 Python SDK v1 文档](https://py.sdk.modelcontextprotocol.io/v1/)。

客户端配置示例，替换为本机 Python 和仓库的绝对路径：

```json
{
  "mcpServers": {
    "crayotter": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/Crayotter/script/run_mcp.py"]
    }
  }
}
```

服务从 runtime-root `.env` 读取模型配置。默认每次启动使用独立的 `app_state/mcp/<session_id>` 工作目录，同时复用用户上传目录。需要跨会话复用临时产物时可设置 `CRAYOTTER_TASK_WORKSPACE`，或传入 `--workspace /absolute/path`；不要让多个服务写入同一目录。返回路径始终是实际产物路径。

`python script/run_mcp.py --list-tools` 可检查全部工具的 JSON Schema，无需启动 MCP 客户端。stdout 仅用于协议或该命令的 JSON 输出，启动信息和日志走 stderr。MCP 工具请求在单个服务进程中串行执行；现有 Agent 阶段内并发继续由共享调度器管理。

MCP 暴露单个工具操作，并不代替完整任务图；直接调用 `export_video` 只生成导出文件，最终成片仍由原任务图在质量验收后登记为 `final_video`。

## FFmpeg 迁移

以下旧 MoviePy 路径已改为原生 FFmpeg：

- `cut_video`、`batch_cut_video`：精确定位与裁剪。
- `merge_videos`：统一画幅和帧率，拼接视频及音频；无声片段补静音，短音轨补齐至视频长度。
- `export_video`：画幅适配与 H.264/AAC 导出。
- `add_narration`、`add_narration_segments`、`compose_prepared_narration`：通过 `atrim`、`adelay` 和 `amix` 混音，视频流直接复制，保留视频完整时长。
- `add_subtitles` 和分段配音字幕：Pillow 只负责文字测量、换行与静态 RGBA 字幕图片；FFmpeg 完成按时段叠加，没有 Python 逐帧视频合成。
- 后端音轨检测、Windows FFprobe 兼容脚本和 RL 合成测试素材也不再依赖 MoviePy。

原有转场、压背景音、响度归一化路径本来就使用 FFmpeg，继续沿用。新媒体辅助实现位于 `script/tools/_native_ffmpeg.py`，迁移后的操作先写同目录临时文件，成功后替换目标，支持输入输出同路径并避免失败时破坏已有文件。`CRAYOTTER_NATIVE_FFMPEG_PIPELINE` 不再控制回退，始终使用原生实现。

需要 `ffmpeg` 和 `ffprobe`，可通过 PATH 或 `FFMPEG_BIN` / `FFPROBE_BIN` 指定。建议安装完整 FFmpeg 发行包；Windows 旧打包中的 `ffprobe.cmd` 现在通过 FFmpeg 输入头信息提供兼容探测，不再加载 MoviePy。兼容探测的时长精度和元数据完整性低于真正 FFprobe，精确媒体校验应使用真正的 FFprobe。

MoviePy 及仅用于它的直接依赖已从主依赖和 RL 依赖文件移除。没有对真实业务视频进行前后耗时基准测试。

## 验证

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s phase3_rl/tests -p 'test_native_ffmpeg.py' -v
```

`tests/test_tool_protocol.py` 检查 schema、参数、结果、错误和兼容性；`tests/test_mcp_server.py` 通过实际 stdio 客户端检查发现与调用；`tests/test_ffmpeg_pipeline.py` 用合成音视频检查时长、画幅、无声/短音轨合并、旁白偏移、视频流保留、字幕显示区间和失败后的文件保护。媒体集成测试需要 FFmpeg/FFprobe，MCP 测试需要可选 SDK，缺少时会明确跳过。
