# Crayotter 开发与进阶配置

普通用户请先阅读 [快速上手](../README_CN.md)。本页保留源码安装、模型预算、调度、日志和配置说明。

### 前置准备

确保系统的 `PATH` 环境变量中已包含 `ffmpeg` 二进制可执行文件，使其能够在终端中被直接调用。可前往 <https://ffmpeg.org/download.html> 下载对应平台的安装包。安装完成后，可在终端执行 `ffmpeg -version`，若能正常输出版本信息，则说明配置成功。运行时会按操作系统和 CPU 架构自动选择二进制：Windows 使用 `script/dep/windows`；macOS 的 Apple Silicon / Intel 分别使用 `script/dep/macos/arm64` / `script/dep/macos/x86_64` 中的 FFmpeg 和 FFprobe。缺少本机 macOS 文件时可运行 `python script/setup_ffmpeg.py` 下载并校验；显式设置的 `FFMPEG_BIN` / `FFPROBE_BIN` 优先。

### 1) 环境准备

建议 Python 3.10+。

```bash
python -m venv .venv
.venv\Scripts\activate
```

工具已统一注册，并提供可选的标准 MCP stdio 服务；视频剪辑、配音合成、字幕和导出均使用原生 FFmpeg。使用方式见 [工具协议与 FFmpeg 迁移说明](tool_protocol_and_ffmpeg.md)。

### 2) 安装依赖

```bash
pip install -r requirements.txt
```

如需启用抖音/小红书的浏览器渲染与用户授权重试，请改用：

```bash
pip install -r requirements-browser.txt
```

该功能复用本机 Chrome/Edge，不处理或绕过验证码；浏览器登录态只在当前任务的私有工作目录中临时使用。

### 3) API 配置与运行参数设置

首先把模板配置文件复制一份，生成专属的自定义配置文件 `.env`，直接在终端执行这条命令即可：

```bash
copy .env.example .env
```

打开刚生成的 `.env` 文件，填写/修改以下常用配置，每一项都标注了用途，新手直接按说明填就行：

```env
# 【必填】你的阿里云通义千问API密钥
CRAYOTTER_API_KEY=your-key

# API接口地址（默认已配置好，无需修改）
CRAYOTTER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 文本对话模型（默认qwen3.8-flash，可按需更换）
CRAYOTTER_MODEL_NAME=qwen3.8-flash

# 视频/多模态理解模型（默认qwen3.8-omni-flash）
CRAYOTTER_VIDEO_MODEL_NAME=qwen3.8-omni-flash

# 语音合成模型（默认qwen3-tts-flash）
CRAYOTTER_TTS_MODEL_NAME=qwen3-tts-flash

# 是否开启第二阶段调研（true=开启，false=关闭）
CRAYOTTER_ENABLE_PHASE2_RESEARCH=true

# 是否开启剪辑计划审阅（true=剪辑前等待确认，false=自动执行）
CRAYOTTER_ENABLE_PLAN_REVIEW=true

# 是否直接执行第三阶段（true=直接跳过前序步骤，false=按正常流程执行）
CRAYOTTER_DIRECT_PHASE3_EXECUTION=false

# 是否优先使用本地素材（true=优先本地，false=优先在线获取）
CRAYOTTER_PREFER_LOCAL_MATERIALS=false

# 资源池并发上限
CRAYOTTER_SEARCH_POOL_SIZE=4
CRAYOTTER_DOWNLOAD_POOL_SIZE=2
CRAYOTTER_VIDEO_ANALYSIS_POOL_SIZE=2
CRAYOTTER_LLM_POOL_SIZE=2
CRAYOTTER_FFMPEG_POOL_SIZE=2
CRAYOTTER_TTS_POOL_SIZE=2
CRAYOTTER_EXPORT_POOL_SIZE=1
CRAYOTTER_STANDARDIZE_TARGET_FPS=30
CRAYOTTER_AUDIO_LOUDNORM_TARGET=0

# 智能代理超时等待时间（单位：秒，默认1200秒，超时会自动结束当前任务）
CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS=1200
```

说明：

- `CRAYOTTER_DIRECT_PHASE3_EXECUTION=true`：跳过 Phase 2 素材搜索/下载，直接走“现有素材分析 + Phase 3 执行”链路。
- `CRAYOTTER_ENABLE_PLAN_REVIEW=true`：剪辑前生成可视化 EditingPlan，等待用户确认或自然语言修改后再执行。
- `CRAYOTTER_PREFER_LOCAL_MATERIALS=true`：先分析本地素材，若当前素材已足够则直接进入后续剪辑，不足时才联网补充。
- 资源池参数分别控制搜索、下载、视频分析、LLM、FFmpeg、TTS 和最终导出的并发上限。
- 素材下载统一走 `download_material_video`；B 站仍是默认关键词搜索源，用户提供的第三方平台 URL 会进入同一下载与清洗管线。
- `CRAYOTTER_STANDARDIZE_TARGET_FPS` 控制下载素材清洗后的目标帧率。`CRAYOTTER_AUDIO_LOUDNORM_TARGET=0` 表示关闭响度归一化；设置为 `-16` 等负 LUFS 值时启用两遍 EBU R128 loudnorm。
- `CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS`：控制任务“长时间无新进展”判定阈值。
- 环境隐式逻辑：图形化工作台中的 API 设置、Phase 2、直达 Phase 3、本地素材优先和超时设置，都会同步写回同一份 `.env`。
- 成品画幅控制：候选素材排序现在会把目标横竖屏当成评分因子：默认优先横屏；如果用户明确要求竖屏，则优先竖屏。Phase 3 合并/导出也改成“放缩后居中裁切”，不再简单拉伸。
- 隐式删除逻辑：对于 `user_temp` 里的用户视频，Crayotter 现在会把对应的 `*_analysis.json` 直接写回 `user_temp`，后续运行自动复用；如果你在 Web 工作台删除这个上传视频，也会一起删除同名分析文件。
- 历史经验压缩：`memory_experience\latest_skills.md` 会被自动压缩成“历史案例参考”，长度受控，*不会随着任务累积而无限变长*，也不会重新定义后续任务目标。

> 安全提醒：不要把真实 API Key 提交到版本控制。

### 模型输出预算

默认文本模型为 `qwen3.8-flash`，视频分析为 `qwen3.8-omni-flash`，配音为 `qwen3-tts-flash`。只需要视觉分析时，将 `CRAYOTTER_VIDEO_MODEL_NAME` 设为 `qwen3.8-flash`。

每次模型调用的输出上限统一在 `app/model_defaults.py` 定义，包含思考 token，不是整个阶段的累计限额，也不是上下文窗口大小：

| 调用类型 | 默认输出上限 | `.env` 覆盖项 |
| --- | ---: | --- |
| 素材准备、筛选和素材评估 | 32768 | `CRAYOTTER_PHASE1_MAX_OUTPUT_TOKENS` |
| 素材研究、专题研究和蓝图整合 | 65536 | `CRAYOTTER_PHASE2_MAX_OUTPUT_TOKENS` |
| 编辑规划、配音规划和 ReAct | 32768 | `CRAYOTTER_PHASE3_MAX_OUTPUT_TOKENS` |
| 视频分析 | 32768 | `CRAYOTTER_VIDEO_ANALYSIS_MAX_OUTPUT_TOKENS` |
| 分类、摘要等辅助调用 | 8192 | `CRAYOTTER_AUXILIARY_MAX_OUTPUT_TOKENS` |

保留模型默认思考能力；提高上限不会强制模型生成同等数量的 token。两个 Qwen3.8 模型当前最大输出为131072，上下文为1M，详见[文本/视觉模型说明](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)和[Omni模型说明](https://help.aliyun.com/en/model-studio/qwen3-8-omni-flash)。修改 `.env` 后重启运行进程。显式配置的已有模型不会被自动迁移。

### 4) 运行 Agent

交互模式：

```bash
python script\agent.py
```

或单任务模式：

```bash
# 在此处填入你需要制作的成品需求，此处为示例
python script\agent.py "制作一个1分钟校园主题宣传片"
```

### 5) 运行图形化工作台

图形化工作台提供了一个直观的 Web 界面，用于管理任务、配置环境以及监控 Agent 的实时状态。

桌面模式会自动启动后端并打开独立窗口：

```bash
python script\run_desktop.py
```

也可以只启动本地后端服务：

```bash
# 在本地8765端口打开WEB工作台
python script\run_backend.py --host 127.0.0.1 --port 8765
```

然后在浏览器打开：

```text
http://127.0.0.1:8765/ui/
```

> 图形化工作台以运行根目录 `.env` 作为唯一配置真源。不要提交真实 `.env`。

---

## 日志轨迹可视化

1. 直接使用最新日志启动可视化：

```bash
python script\visualize.py
```

2. 或对指定日志文件启动可视化：

```bash
python script\visualize.py logs\<video_agent_YYYYMMDD_HHMMSS>.log
```

请将 `<video_agent_YYYYMMDD_HHMMSS>.log` 文件名替换为您在 `logs` 目录下实际生成的日志名称。

3. 网络配置：可视化界面默认运行在 8080 端口。如果该端口被占用或有特定需求，可使用 `--port` 参数自定义：

```bash
# 将 Web 端口设置为 9000
python script/visualize.py --port 9000
```

4. 隐式导出静态 HTML：`script\visualize.py` 还会在日志同目录导出静态 HTML 轨迹文件（例如 `*_trace.html`）。

---

