# Crayotter

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">中文</a>
</p>

<p align="center">
  <img src="./logo.png" alt="Crayotter Logo" width="180" />
</p>

Crayotter 是一个多模态、Agent 驱动的视频自动编辑系统，可以把一条文本请求转化为完整的视频制作流程。

它将 **规划**、**深度剪辑研究** 和 **工具执行** 组织成三阶段工作流，并通过完整日志与可视化轨迹支持调试与迭代。

---

## 项目概览

本仓库主要由四个核心组件构成：

- **`script\agent.py`**：主入口。负责初始化运行环境、执行任务（交互式或单次请求）、清理工作目录，并写入日志与经验记忆。
- **`script\graph.py`**：编排层（LangGraph StateGraph）。定义三阶段工作流与状态路由。
- **`script\tools\`**：模块化工具集，覆盖搜索、下载、分析、裁剪、转场、旁白、字幕与导出。
- **`script\visualize.py`**：日志解析 + 本地可视化服务，用于查看阶段进度与工具调用轨迹。

配套目录：

- **`temp\`**：执行过程中的中间文件与输出文件。
- **`user_temp\`**：用户提供的本地素材目录。
- **`logs\`**：运行日志（`video_agent_*.log`）。
- **`memory_experience\`**：任务后沉淀的经验文档。
- **`website\`**：静态官网与 GitHub Pages 资源。

### 本分支新增内容

本分支在原项目基础上新增了本地后端服务和图形化工作台，让 Agent 不仅能通过命令行运行，也能通过浏览器里的桌面式界面操作。

- **`app\backend\`**：本地 HTTP 服务、任务管理、配置存储、事件流。
- **`app\frontend\`**：图形化工作台界面，支持任务创建、历史记录、日志查看、配置管理与产物预览。
- **`script\run_backend.py`**：本地 GUI / 后端服务启动入口。
- 原有 CLI 工作流仍然保留，可继续通过 `script\agent.py` 使用。

---

## 工作流

Crayotter 使用三阶段架构：

1. **Phase 1 - 素材准备（Planner + Executor）**
   - 搜索候选视频
   - 排序与筛选高质量候选
   - 下载选中视频
   - 对每个源视频执行多模态分析

2. **Phase 2 - 剪辑研究**
   - 读取全部分析结果
   - 生成结构化剪辑蓝图（叙事、节奏、转场、旁白策略）
   - 本阶段不直接调用剪辑工具

3. **Phase 3 - ReAct 编辑执行**
   - 执行裁剪、合并、转场设计、旁白 / 字幕与最终导出
   - 记录完整工具调用轨迹，便于后续可视化复盘

---

## 快速开始

### 1）环境准备

建议使用 Python 3.10+。

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 2）安装依赖

```bash
pip install -r requirements.txt
```

### 3）配置 API

可以直接编辑 `script\agent.py` 中的 API 配置（模型 API、视频 API、TTS API）。

> 安全提示：不要把真实 API Key 提交到版本控制。

### 4）运行 Agent

交互模式：

```bash
python script\agent.py
```

单任务模式：

```bash
python script\agent.py "制作一个 3 分钟校园主题宣传视频"
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
├─ app\
│  ├─ backend\
│  │  ├─ config_store.py
│  │  ├─ event_bus.py
│  │  ├─ models.py
│  │  ├─ runtime_manager.py
│  │  └─ server.py
│  └─ frontend\
│     ├─ app.js
│     ├─ index.html
│     └─ styles.css
├─ script\
│  ├─ agent.py
│  ├─ graph.py
│  ├─ run_backend.py
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
