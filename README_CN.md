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
</p>

Crayotter 是一个多模态、Agent 驱动的视频自动编辑系统，可以把一条文本需求转化为完整成片。

它将 **规划（planning）**、**深度剪辑研究（deep editing research）** 和 **工具执行（tool-based execution）** 组合为三阶段工作流，并通过完整日志与可视化轨迹来支持调试与迭代。

---

## 近期动态

- 2026.3.30：第一款 release 版本已发布，见[v0.1.0-demo](https://github.com/idwts/Crayotter/releases/tag/v0.1.0-demo)。

---

## 项目概览

本仓库主要由四个核心组件构成：

- **`script\agent.py`**：主入口。负责初始化运行环境、执行任务（交互式或单次请求）、清理工作目录，并写入日志与经验记忆。
- **`script\graph.py`**：编排层（LangGraph StateGraph）。定义三阶段工作流与状态路由。
- **`script\tools\`**：模块化工具集，覆盖搜索、下载、分析、剪辑、转场、配音、字幕与导出。
- **`script\visualize.py`**：日志解析 + 本地可视化服务，用于查看阶段进度和工具调用轨迹。

配套目录：

- **`temp\`**：执行过程中的中间文件与输出文件。
- **`user_temp\`**：用户提供的本地素材目录。
- **`logs\`**：运行日志（`video_agent_*.log`）。
- **`memory_experience\`**：任务后沉淀的经验文档。
- **`website\`**：静态官网与 GitHub Pages 资源。

---

## 工作流

Crayotter 使用三阶段架构：

1. **Phase 1 — 素材准备（Planner + Executor）**
   - 搜索候选素材
   - 排序与筛选高质量候选
   - 下载入选视频
   - 对每个源视频执行多模态分析

2. **Phase 2 — 剪辑研究（Editing Research）**
   - 读取全部分析结果
   - 生成结构化剪辑蓝图（叙事、节奏、转场、配音策略）
   - 本阶段不调用剪辑工具

   该阶段可通过 `script\agent.py` 中的 `ENABLE_PHASE2_RESEARCH = False` 关闭，以节省token。
   关闭后流程变为：Phase 1 → Phase 3。

3. **Phase 3 — ReAct 自动执行（ReAct Editing Execution）**
   - 执行裁剪、合并、转场、配音/字幕、最终导出
   - 记录完整工具调用轨迹，便于后续可视化复盘

---

## 快速开始

### 1）环境准备

建议 Python 3.10+。

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 2）安装依赖

```bash
pip install -r requirements.txt
```

### 3）配置 API

编辑 `script\agent.py` 顶部的 API 配置（主模型、视频模型、TTS 等）。

你也可以配置是否启用 Phase 2：

```python
ENABLE_PHASE2_RESEARCH = True  # True: 启用 Phase 2；False: 跳过 Phase 2 直达 Phase 3
```

> 安全提醒：不要把真实 API Key 提交到版本控制。

### 4）运行 Agent

交互模式：

```bash
python script\agent.py
```

单任务模式：

```bash
python script\agent.py "制作一个1分钟校园主题宣传片"
```

### 5）运行图形化工作台

启动本地后端服务：

```bash
python script\run_backend.py --host 127.0.0.1 --port 8765
```

然后在浏览器打开：

```text
http://127.0.0.1:8765/ui/
```

工作台当前支持：

- 创建 `demo` 和 `agent` 任务
- 本地配置管理
- 任务历史查看
- 结构化日志与事件查看
- 产物预览与打开

后端同时暴露这些本地接口：

- `GET /health`
- `GET /config`
- `PUT /config`
- `GET /jobs`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/events`
- `POST /jobs/{job_id}/cancel`

> 图形化工作台会把本地运行状态写到 `app_state/`。不要提交 `app_state/config.json`。

---

## 日志轨迹可视化

使用最新日志启动可视化：

```bash
python script\visualize.py
```

指定日志文件：

```bash
python script\visualize.py logs\video_agent_20260321_045836.log
```

指定端口：

```bash
python script\visualize.py --port 8080
```

`script\visualize.py` 还会在日志同目录导出静态 HTML 轨迹文件（例如 `*_trace.html`）。

---

## 仓库结构

```text
Crayotter\
├─ script\
│  ├─ agent.py
│  ├─ graph.py
│  ├─ visualize.py
│  └─ tools\
├─ logs\
├─ temp\
├─ user_temp\
├─ memory_experience\
├─ website\
├─ logo.png
└─ requirements.txt
```
