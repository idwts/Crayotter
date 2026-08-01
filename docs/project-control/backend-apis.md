# 后端接口表

> 说明：本表汇总当前后端对外暴露的 HTTP 接口与 `RuntimeManager` 内部方法，便于后续鉴权、复用和改造。智能体阅读后应记住：**当前后端是 `http.server.BaseHTTPRequestHandler` 手写路由；认证接口 `/api/auth/*` 已上线（2026-07-31），使用 `crayotter_auth_session` Cookie（SHA-256 digest session，30 天滚动过期），匿名隔离仍用 `crayotter_session` Cookie 的 `owner_id`；后续需要把业务接口迁移到带租户校验的框架并补齐鉴权。**

## 1. HTTP 接口总览

### 1.1 静态与系统

| 方法 | 路径 | 说明 | 当前鉴权 |
|------|------|------|----------|
| GET | `/ui/` 及 `/ui/*` | 前端静态资源 | 无 |
| GET | `/` | 重定向到 `/ui/` | 无 |
| GET | `/health` | 健康检查 | 无 |
| GET | `/config` | 公开配置（模型、功能开关等） | 无 |

### 1.1a 认证（Auth，2026-07-31 上线）

| 方法 | 路径 | 说明 | 当前鉴权 |
|------|------|------|----------|
| POST | `/api/auth/register` | 注册，返回 `{user, tenant, recovery_codes}` | 无 |
| POST | `/api/auth/login` | 登录，设置 `crayotter_auth_session` Cookie | 无 |
| POST | `/api/auth/logout` | 注销当前 session 并清除 Cookie | auth Cookie |
| GET | `/api/auth/me` | 当前登录用户信息，未登录 401 | auth Cookie |
| POST | `/api/auth/password` | 登录态改密，成功后吊销全部 session | auth Cookie |
| POST | `/api/auth/reset` | 一次性恢复码重置密码，成功后吊销全部 session 并清除 Cookie | 无 |

### 1.2 素材（Uploads）

| 方法 | 路径 | 说明 | 当前鉴权 |
|------|------|------|----------|
| GET | `/uploads` | 列出当前 owner 的上传视频 | owner_id Cookie |
| POST | `/uploads` | multipart 上传视频 | owner_id Cookie + PublicTrialGuard 容量限制 |
| DELETE | `/uploads?path=` | 删除指定上传及关联分析文件 | owner_id Cookie + 路径校验 |

### 1.3 任务（Jobs）

| 方法 | 路径 | 说明 | 当前鉴权 |
|------|------|------|----------|
| GET | `/jobs` | 列出当前 owner 的所有任务 | owner_id Cookie |
| POST | `/jobs` | 创建新任务 | owner_id Cookie + PublicTrialGuard 频率限制 |
| GET | `/jobs/{job_id}` | 任务详情 | owner_id Cookie |
| DELETE | `/jobs/{job_id}` | 删除任务 | owner_id Cookie |
| POST | `/jobs/{job_id}/cancel` | 取消任务 | owner_id Cookie |
| POST | `/jobs/{job_id}/resume` | 恢复中断任务 | owner_id Cookie |
| POST | `/jobs/{job_id}/pause` | 暂停任务 | owner_id Cookie |
| POST | `/jobs/{job_id}/approve` | 批准继续（pause 后） | owner_id Cookie + pause_token |

### 1.4 任务事件与消息

| 方法 | 路径 | 说明 | 当前鉴权 |
|------|------|------|----------|
| GET | `/jobs/{job_id}/events?after=` | 分页事件 | owner_id Cookie |
| GET | `/jobs/{job_id}/events/stream?after=` | SSE 实时事件流 | owner_id Cookie |
| GET | `/jobs/{job_id}/events.log` | 下载事件日志文本 | owner_id Cookie |
| GET | `/jobs/{job_id}/messages` | 任务消息列表 | owner_id Cookie |
| POST | `/jobs/{job_id}/messages` | 发送用户消息 | owner_id Cookie |

### 1.5 计划（Plans）

| 方法 | 路径 | 说明 | 当前鉴权 |
|------|------|------|----------|
| GET | `/jobs/{job_id}/plans/current` | 当前计划版本 | owner_id Cookie |
| GET | `/jobs/{job_id}/plans/{version}` | 指定版本计划 | owner_id Cookie |
| GET | `/jobs/{job_id}/plans/diff?from=&to=` | 两版本计划差异 | owner_id Cookie |
| POST | `/jobs/{job_id}/plans/{version}/feedback` | 提交反馈 | owner_id Cookie |
| POST | `/jobs/{job_id}/plans/{version}/approve` | 批准计划 | owner_id Cookie |
| POST | `/jobs/{job_id}/plans/{version}/reject` | 拒绝计划 | owner_id Cookie |

### 1.6 文件与产物

| 方法 | 路径 | 说明 | 当前鉴权 |
|------|------|------|----------|
| GET | `/files?path=` | 按路径下载文件 | Public mode 下仅允许访问当前 owner 的 artifact |
| GET | `/jobs/{job_id}/artifacts` | 任务产物列表 | owner_id Cookie |

### 1.7 配置管理

| 方法 | 路径 | 说明 | 当前鉴权 |
|------|------|------|----------|
| PUT | `/config` | 更新服务端配置 | **Public mode 下禁止** |

## 2. RuntimeManager 方法表（Agent 相关/核心）

| 方法 | 位置 | 说明 | 后续复用建议 |
|------|------|------|--------------|
| `list_jobs(owner_id)` | runtime_manager.py:79 | 列出某 owner 的任务 | 保留，增加 tenant_id 过滤 |
| `get_job(job_id, owner_id)` | runtime_manager.py:90 | 获取 ManagedJob 对象 | 保留，增加 tenant 校验 |
| `get_job_detail(job_id, owner_id)` | runtime_manager.py:97 | 任务详情字典 | 保留 |
| `list_job_artifacts(job_id, owner_id)` | runtime_manager.py:109 | 任务产物列表 | 保留，用于 artifact 下载鉴权 |
| `get_current_plan(job_id, owner_id)` | runtime_manager.py:115 | 当前编辑计划 | 保留 |
| `get_plan(job_id, version, owner_id)` | runtime_manager.py:130 | 指定版本计划 | 保留 |
| `get_plan_diff(...)` | runtime_manager.py:139 | 计划差异 | 保留 |
| `apply_plan_feedback(...)` | runtime_manager.py:145 | 应用用户反馈 | 保留 |
| `approve_plan/reject_plan` | runtime_manager.py:219/242 | 计划审批 | 保留 |
| `create_job(request, owner_id, overrides, upload_root)` | runtime_manager.py:252 | 创建任务并启动 worker | **核心，需要 tenant 化改造** |
| `cancel_job/resume_job/delete_job` | runtime_manager.py:362/388/435 | 任务生命周期 | 保留 |
| `list_events/events_log_text/list_messages/add_message` | runtime_manager.py:447/453/456/462 | 事件消息读写 | 保留 |
| `pause_job/approve_job` | runtime_manager.py:538/551 | 暂停/继续控制 | 保留 |
| `wait_for_events(...)` | runtime_manager.py:563 | SSE 事件等待 | 保留 |
| `_run_job/_run_agent_job/_watch_agent_process` | runtime_manager.py:569/700/884 | Agent 执行核心 | **M7 Docker 化重点改造对象** |
| `_load_existing_jobs` | runtime_manager.py:1263 | 启动时恢复历史任务 | 保留 |

## 3. 后续鉴权改造清单

- [x] 增加 `/api/auth/*` 注册、登录、登出、密码、恢复码接口（2026-07-31 上线，含 `/api/auth/reset`）。
- [ ] 所有接口增加 `tenant_id` / `user_id` 校验。
- [ ] `/uploads`、`/jobs`、`/files` 等接口拒绝跨租户访问（当前仅 owner_id）。
- [ ] `/jobs/{job_id}` 及其子资源统一做 404/403 区分。
- [ ] 上传接口增加租户存储配额校验。
- [ ] `/config` 接口在 public mode 外开放给管理员。
- [ ] 认证接口（register/login/reset）增加频率限制与失败锁定。
