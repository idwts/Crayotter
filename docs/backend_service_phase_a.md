# Crayotter Phase A Backend Service

## 1. 这次新增了什么

本次不是直接做完整桌面 GUI，而是先把第一阶段的服务化基础设施落下来，让现有 Python agent 可以被桌面端或其他前端调用。

新增内容包括：

- `app/backend/models.py`
  - 配置、任务、事件的数据模型
- `app/backend/config_store.py`
  - 本地配置存储，默认写入 `app_state/config.json`
- `app/backend/event_bus.py`
  - 任务级事件总线
- `app/backend/runtime_manager.py`
  - 单任务运行时管理
  - 支持 `demo` 模式和 `agent` 模式
- `app/backend/server.py`
  - 基于标准库 `http.server` 的本地 HTTP 服务
  - 支持任务创建、查询、事件流、配置读写
- `script/run_backend.py`
  - 简单启动入口
- `script/agent.py`
  - 新增运行时配置注入
  - 新增事件回调能力
  - 继续兼容原命令行执行

## 2. 为什么这版先用标准库服务

原方案里推荐了 FastAPI，但当前仓库环境里没有 `fastapi` / `uvicorn`，如果为了第一阶段可跑性强行引入，会把“服务化”这件事本身拖住。

所以这版先做成：

- 纯标准库可运行
- 接口清晰
- 事件机制先稳定
- 后续可以平滑切换到 FastAPI

也就是说，这一版重点是把“运行时能力”搭起来，而不是把 Web 框架选型一次性定死。

## 3. 当前能力边界

已经支持：

- 本地 HTTP 服务启动
- 配置读取与更新
- 任务创建与状态查询
- 任务事件列表
- SSE 事件流
- `demo` 模式验收整条链路
- `agent` 模式接入现有 `script/agent.py`

当前限制：

- 只支持单任务串行运行
- `agent` 任务暂时还不支持真正安全的强制取消
- 现有视频工具仍然基于全局 `temp/` 工作目录
- 还没有桌面前端

## 4. 接口一览

### 基础接口

- `GET /`
- `GET /health`
- `GET /config`
- `PUT /config`

### 任务接口

- `GET /jobs`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/events`
- `GET /jobs/{job_id}/events/stream`
- `POST /jobs/{job_id}/cancel`

## 5. 如何启动

在仓库根目录执行：

```powershell
python script/run_backend.py --host 127.0.0.1 --port 8765
```

或者：

```powershell
python -m app.backend.server --host 127.0.0.1 --port 8765
```

启动后访问：

```text
http://127.0.0.1:8765/health
```

## 6. 如何测试

建议按下面顺序测试。

### 6.1 测试服务是否启动

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

预期结果：

```json
{
  "ok": true
}
```

### 6.2 测试配置读写

先读：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/config
```

再写：

```powershell
$body = @{
  profiles = @{
    default = @{
      api_key = "your-real-key"
      base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
      model_name = "qwen-plus"
      video_model_name = "qwen-vl-max-latest"
      tts_model_name = "qwen-tts-latest"
    }
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Put `
  -Uri http://127.0.0.1:8765/config `
  -ContentType "application/json" `
  -Body $body
```

预期结果：

- 返回最新配置
- 本地生成 `app_state/config.json`

### 6.3 测试 Demo 任务

```powershell
$job = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8765/jobs `
  -ContentType "application/json" `
  -Body '{"task":"demo acceptance run","mode":"demo"}'

$job
```

然后查看任务详情：

```powershell
Invoke-RestMethod ("http://127.0.0.1:8765/jobs/" + $job.job_id)
```

查看事件列表：

```powershell
Invoke-RestMethod ("http://127.0.0.1:8765/jobs/" + $job.job_id + "/events")
```

预期结果：

- 任务最终状态为 `completed`
- 事件数量大于 0
- `app_state/jobs/<job_id>/summary.json` 存在
- `app_state/jobs/<job_id>/events.jsonl` 存在
- `app_state/jobs/<job_id>/output/demo_final_summary.txt` 存在

### 6.4 测试 SSE 事件流

PowerShell 对 SSE 体验一般，推荐用浏览器或支持流式的客户端工具。

直接访问：

```text
http://127.0.0.1:8765/jobs/<job_id>/events/stream
```

预期结果：

- 持续收到 `data: {...}` 形式事件
- 任务结束后收到 `event: end`

### 6.5 测试真实 Agent 任务

先在配置里写入真实 API Key，然后创建：

```powershell
$body = @{
  task = "Create a 1-minute campus-themed promo video"
  mode = "agent"
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8765/jobs `
  -ContentType "application/json" `
  -Body $body
```

预期结果：

- 任务进入 `running`
- 事件列表里能看到：
  - `task_started`
  - `phase_started`
  - `plan_created`
  - `step_started`
  - `step_completed`
  - `tool_called`
  - `tool_result`
  - `task_completed` 或 `job_failed`

注意：

- 真实任务会真正调用现有视频工具链
- 会使用当前仓库里的 `temp/`、`user_temp/`、`logs/`
- 如果模型、网络或依赖不可用，任务可能失败，但后端和事件机制本身仍然应该正常工作

## 7. 验收标准

你可以按下面清单验收这次新增内容。

### A. 服务层是否建立成功

- 能启动本地服务
- `/health` 正常
- `/config` 可读写

### B. 任务层是否建立成功

- 能创建任务
- 能查询任务状态
- 能查看任务事件
- 能看到任务持久化目录

### C. 事件流是否建立成功

- Demo 任务能持续产出事件
- 事件包含阶段、步骤、工具、结果这类结构化信息
- 任务结束时有完成事件

### D. Agent 是否被服务接管

- `script/agent.py` 仍可命令行运行
- 服务层能通过 `agent` 模式调用它
- 运行时配置不再要求用户改源码

## 8. 后续建议

如果这部分你验收通过，下一阶段最自然的动作就是：

1. 在现有服务层之上做一个桌面工作台 UI
2. 先用 `demo` 模式联调前端交互
3. 再接 `agent` 模式跑真实任务
4. 最后开始处理 Job 级工作目录和安装打包
