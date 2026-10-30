# Crayotter

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">中文</a>
</p>

<p align="center">
  <img src="./logo.png" alt="Crayotter Logo" width="180" />
</p>

<p align="center">
  <a href="https://idwts.github.io/Crayotter" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/🚀-在线演示-4CAF50?style=for-the-badge&logo=googlechrome&logoColor=white" alt="在线演示">
  </a>
  <a href="https://github.com/idwts/Crayotter/stargazers" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/github/stars/idwts/Crayotter?style=for-the-badge&logo=github&label=Star%20Crayotter&color=ffb347" alt="在 GitHub Star Crayotter">
  </a>
  <a href="https://idwts.github.io/Crayotter/paper/" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/Blog-Paper%20Page-6f7cff?style=for-the-badge" alt="论文博客">
  </a>
  <a href="https://arxiv.org/abs/2606.07636" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/arXiv-2606.07636-b31b1b?style=for-the-badge&logo=arxiv&logoColor=white" alt="arXiv 论文">
  </a>
  <a href="https://arxiv.org/abs/2608.02694" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/arXiv-2608.02694-b31b1b?style=for-the-badge&logo=arxiv&logoColor=white" alt="arXiv 论文">
  </a>
  <a href="./LICENSE" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-blue?style=for-the-badge" alt="许可证">
  </a>
</p>

<p align="center">
  如果这个项目能切实帮助到您，欢迎在 GitHub 上给 Crayotter 点一个 Star。
</p>

Crayotter 是一个多模态、Agent 驱动的视频自动编辑系统，可以把一条文本需求转化为完整成片。Crayotter 工作流由 **规划（planning）**、**深度剪辑研究（deep editing research）** 和 **工具执行（tool-based execution）** 三阶段组成，同时支持基于完整日志与可视化轨迹的调试与迭代功能。

---

## 📽️ 演示视频

<p align="center">
  <a href="https://idwts.github.io/Crayotter/#demo">
    <img src="./demo/showcase-poster.jpg" alt="Crayotter 演示视频预览" width="100%" />
  </a>
</p>

<p align="center">
  <a href="https://idwts.github.io/Crayotter/#demo">在线观看操作演示</a>
</p>

一次完整的端到端运行：从一句文本需求出发，Crayotter 自动准备素材、研究剪辑蓝图，最终产出成片。在线播放器中也可以继续选择三个代表性 case 成片。

---

## 目录

- [演示视频](#️-演示视频)
- [近期动态](#近期动态)
- [核心特性](#核心特性)
- [项目概览](#项目概览)
- [工作流](#工作流)
- [快速开始](#快速开始)
  - [安装与打开](#安装与打开)
  - [第一次使用](#第一次使用)
- [常见问题](#常见问题)
- [从源码运行](#从源码运行)
- [开发与研究](#开发与研究)
- [引用](#引用)
- [仓库结构](#仓库结构)

---

## 近期动态

- **2026.9.16** — 我们的[论文](https://arxiv.org/abs/2606.07636)已被 AACL-IJCNLP 2026 Demo Track 接收！
- **2026.8.5** — 全新工作已公开！迈向长时序视频编辑智能体：[Crayotter: Learning Long-Horizon Video Editing Agents via Group-Relative Preference Backpropagation](https://arxiv.org/abs/2608.02694)。
- **2026.6.27** — Crayotter 1.0.0 新增多素材源导入、统一下载清洗，并更新 Windows 发布包。
- **2026.6.15** — 异步调度升级后，视频生成整体流程提速约 1.6 倍。
- **2026.5.31** — 我们的论文已上线：[Crayotter: Traceable Multi-Agent Workflows for Long-Form Video Editing](https://arxiv.org/abs/2606.07636)。
- **2026.5.23** — 100 星达成！
- **2026.5.11** — 论文页面已上线，见 [Crayotter Paper Page](https://idwts.github.io/Crayotter/paper/)。
- **2026.4.10** — 优化后的 release 版本已更新。
- **2026.3.30** — 第一款 release 版本已发布，见 [v0.1.0-demo](https://github.com/idwts/Crayotter/releases/tag/v0.1.0-demo)。

---

## 核心特性

- **一句话出一条片** — 一句自然语言需求驱动从素材采集到导出的完整流水线。
- **三阶段、可追溯工作流** — 显式规划、纯推理的剪辑研究、受控工具执行，全程留痕可复盘。
- **多模态素材理解** — 每个源视频由多模态模型分析，支撑叙事与节奏决策。
- **资源感知 DAG 调度** — 搜索、下载、分析、LLM、FFmpeg、TTS、导出均按资源池限流，支持重试、冲突键、断点续跑。
- **多源素材导入** — B 站、抖音、小红书/Rednote、快手、YouTube 或 generic URL（经 `yt-dlp`）。
- **内置配音、字幕与音频混合** — 分段 TTS、响度归一化、背景音闪避、字幕烧录。
- **本地工作台 + 桌面模式** — Web 界面负责任务管理、`.env` 双向同步、结构化日志、产物预览与中断任务续跑。
- **可视化轨迹分析** — 本地服务 + 静态 HTML 导出，用于查看阶段进度与工具调用轨迹。

---

## 项目概览

更完整的当前实现说明见
[业务流程、系统架构与模块化设计](./docs/architecture_cn.md)，其中包含端到端流程图、
组件架构图、技术栈、关键数据对象、扩展规则与分阶段模块化路线。

<p align="center">
  <img src="./crayottor_framework.jpg" alt="Crayotter 框架总览">
</p>

本仓库主要由四个核心组件构成：

- **`script/agent.py`** — 主入口。负责初始化运行环境、执行任务（根据启动方式不同分交互式或单次请求）、清理工作目录，并写入日志与经验记忆。
- **`script/graph.py`** — 编排层（Orchestration Layer，implemented by LangGraph StateGraph）。定义三阶段工作流与状态路由。
- **`script/tools/`** — 模块化工具集，覆盖 1.素材的搜索、下载和分析 和 2.基于素材集的剪辑、转场、配音 以及 3.最终成品的字幕与导出。
- **`script/visualize.py`** — 基于日志解析的本地可视化服务，用于查看阶段进度和工具调用轨迹。

配套目录：

- **`temp/`** — 存储执行过程中的中间文件（如搜索并下载的素材文件）与输出最终剪辑成品文件。
- **`user_temp/`** — 存储用户提供的本地素材。
- **`logs/`** — 存储运行日志（`video_agent_*.log`）。
- **`memory_experience/`** — 存储单次任务后沉淀的历史案例参考文档，仅供方法参考，不能覆盖当前任务目标。
- **`website/`** — 静态官网与 GitHub Pages 资源。

---

## 工作流

Crayotter 工作流分三阶段：

```text
START -> planner -> phase1_scheduler -> material_gap_evaluator
material_gap_evaluator -> planner (supplement) | editing_research | react_editor
editing_research -> react_editor -> END
```

1. **Phase 1 — 素材准备（Planner + Executor）**
   - Planner 输出显式依赖 DAG，调度器验证依赖、资源池、重试与写冲突。
   - 搜索、逐视频下载、逐视频分析任务在资源允许时并发执行。
   - Material Gap Evaluator 判定继续推进或增量补充（最多两轮补充，复用已成功的任务）。
   - 通过平台无关的素材源层搜索候选素材。
   - 导入用户提供的 B 站、抖音、小红书/Rednote、快手、YouTube 或 generic URL（实际下载能力取决于 `yt-dlp` 和平台可访问性）。
   - 对候选素材进行排序并筛选高质量素材（目标横竖屏作为评分因子：默认横屏，用户明确要求时优先竖屏）。
   - 下载入选视频，并统一清洗为剪辑兼容的 MP4/H.264/AAC 素材。
   - 对每个源视频执行多模态分析。

2. **Phase 2 — 剪辑研究（Editing Research）**
   - 对入选素材读取并分析结果。
   - 并发生成叙事、画面、节奏、配音策略。
   - 整合为一份结构化剪辑蓝图（JSON + 兼容 Markdown）。
   - 本阶段不调用剪辑工具，纯推理。

   温馨提示：该阶段可通过运行根目录 `.env` 中的 `CRAYOTTER_ENABLE_PHASE2_RESEARCH=false` 关闭，以节省 token。关闭后流程变为：Phase 1 → Phase 3。但剪辑效果可能会有偏差。

3. **Phase 3 — ReAct 自动执行（ReAct Editing Execution）**
   - 优先走受控剪辑 DAG，并发裁剪素材与分段 TTS。
   - 时间线合并、混音、字幕、质量评估、导出保持串行。
   - 结构化规划或校验失败时回退到 ReAct 编辑器。
   - 记录完整的工具调用轨迹，用于后续的可视化复盘。

---

## 快速开始

### 安装与打开

| 平台 | 获取方式 | 打开方式 |
| --- | --- | --- |
| Windows 10/11 x64 | [Releases](https://github.com/idwts/Crayotter/releases) 中的 `Crayotter-Windows-x64.zip` | 完整解压后运行 `Crayotter.exe` |
| macOS Apple Silicon | 云端测试构建见 [Actions](https://github.com/idwts/Crayotter/actions/workflows/desktop-build.yml)；正式包以 Releases 为准 | 解压后双击 `Crayotter.app`，也可移到“应用程序” |

桌面包自带 Python、FFmpeg 和下载器，无需安装 Conda。Windows 独立窗口需要 Edge WebView2，缺少时会尝试浏览器模式。当前 Mac 包声明最低 macOS 15.0，仅支持已验证的 arm64 构建；测试包尚未做 Apple 公证。

### 第一次使用

1. **配置模型**：打开“设置”，填写自己的 API Key、服务地址和模型。模型调用会产生对应服务商的费用，项目不提供内置额度。
2. **选择素材**：上传本地视频，或在需求中写清题材，让它在线搜集；也可以提供视频链接。
3. **写清需求**：说明目标时长、风格、横竖屏，以及是否需要旁白和字幕，然后启动任务。
4. **查看结果**：在任务详情中预览、保存最终视频，并核对旁白、字幕和素材内容。

当前默认模型为 `qwen3.8-flash`（规划与剪辑）、`qwen3.8-omni-flash`（音视频分析）、`qwen3-tts-flash`（配音）。只需要视觉分析时，可将视频模型设为 `qwen3.8-flash`。已有自定义模型配置会保留。

网络素材、视频分析和模型思考都需要时间，实际处理时长取决于素材数量、长度和服务响应。可以先用约30秒的任务试跑。

## 常见问题

**成片保存在哪里？**
在工作台任务详情中查看最终产物。Mac 默认运行目录是 `~/Library/Application Support/Crayotter`；Windows 优先使用程序所在的可写目录，否则使用用户应用数据目录。

**为什么搜索或下载失败？**
先检查平台能否正常访问。需要登录的平台，在本机浏览器完成授权后重试；也可以上传自己的素材继续剪辑。

**模型报错或配音失败怎么办？**
检查密钥、服务地址、模型可用性和余额。文本、视频、配音可以在设置中分别配置。

**中断后还能继续吗？**
工作台支持恢复被标记为“已中断”的任务。已失败任务与已中断任务不同，不能保证原地恢复。

## 从源码运行

需要 Python 3.10+，并安装 FFmpeg/FFprobe。项目会按平台选择已配备的二进制；Mac 缺少工具时可运行 `python script/setup_ffmpeg.py`。

```bash
python -m pip install -r requirements.txt
python script/run_backend.py --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765/ui/` 后在设置中填写 API 配置。需要独立窗口时，另装 `requirements-desktop.txt` 并运行 `python script/run_desktop.py`。

## 开发与研究

- [源码安装、环境变量与日志](docs/development_cn.md)
- [三阶段架构](docs/architecture_cn.md)
- [统一工具接口、MCP 与 FFmpeg](docs/tool_protocol_and_ffmpeg.md)
- [Windows 构建](packaging/build_windows.ps1) / [macOS 构建与签名说明](packaging/README_MACOS_CN.md)
- [论文与引用](docs/citation.md)；[实验性 RL 训练](phase3_rl/README_CN.md)与日常剪辑运行分开。

---

## 引用

如果 Crayotter 对您的研究或工作有帮助，欢迎引用我们的论文：

```bibtex
@misc{yan2026crayottertraceablemultiagentworkflows,
      title={Crayotter: Traceable Multi-Agent Workflows for Long-Form Video Editing}, 
      author={Lecheng Yan and Yichong Zhang and Xiantao Xu and Jianze Lin and Ben Pan and Xiaoyu Zheng and Jiawei Qian and Anqi Wu and Jiahui Geng and Ruizhe Li and Fengyu Cai and Jingcheng Niu and Raymond Li and Wenxi Li and Chenyang Lyu},
      year={2026},
      eprint={2606.07636},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://arxiv.org/abs/2606.07636}, 
}

@misc{yan2026crayotterlearninglonghorizonvideo,
      title={Crayotter: Learning Long-Horizon Video Editing Agents via Group-Relative Preference Backpropagation}, 
      author={Lecheng Yan and Jianze Lin and Yichong Zhang and Ben Pan and Wenxi Li and Chenyang Lyu and Liting Zhou and Cathal Gurrin},
      year={2026},
      eprint={2608.02694},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2608.02694}, 
}
```

---

## 仓库结构

```text
Crayotter/
├─ script/
│  ├─ agent.py
│  ├─ graph.py
│  ├─ visualize.py
│  └─ tools/
├─ app/                # 后端服务、前端、运行时路径
├─ packaging/          # Windows / macOS 发布构建脚本
├─ phase3_rl/          # 实验性 RL 冒烟流水线（非主运行时）
├─ demo/               # 演示素材
├─ logs/
├─ temp/
├─ user_temp/
├─ memory_experience/
├─ website/            # 静态官网 + GitHub Pages 资源
├─ logo.png
├─ crayottor_framework.jpg
└─ requirements.txt
```

项目采用 [PolyForm Noncommercial 1.0.0](LICENSE)，商业使用请先核对许可条款。
