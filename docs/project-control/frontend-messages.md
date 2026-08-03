# 前端报文表

> 说明：本表汇总 `app/frontend_src/src/main.jsx` 及辅助模块中所有前端发起的 HTTP/SSE 请求，便于后续鉴权、Mock、调试和接口改造。智能体阅读后应记住：**前端通过全局 `request(url, options)` 调用后端，自动携带 Cookie；登录态由 `crayotter_auth_session` Cookie 维护（`authUser` 状态 + 401 统一跳登录页），匿名任务隔离仍依赖 `crayotter_session`；认证页面组件在 `AuthPages.jsx`（Login/Register/ResetPassword）。**

## 0. 认证报文（2026-07-31 上线）

### 0.1 注册

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/register` |
| 请求体 | `{ username, password }` |
| 响应 201 | `{ user, tenant, recovery_codes }`；2026-08-02 起同时 `Set-Cookie: crayotter_auth_session`（注册即登录） |
| 调用位置 | AuthPages.jsx `RegisterPage.submit` |

### 0.2 登录

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/login` |
| 请求体 | `{ username, password, remember_me }` |
| 响应 200 | `{ user, expires_at }`，`Set-Cookie: crayotter_auth_session`；`remember_me=true` 时追加 `Set-Cookie: crayotter_remember`（30 天，HttpOnly+SameSite=Lax） |
| 调用位置 | AuthPages.jsx `LoginPage.submit` |

### 0.3 恢复码重置密码

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/reset` |
| 请求体 | `{ username, recovery_code, new_password }` |
| 响应 200 | `{ ok: true }`，后端吊销该用户全部 session 并清除 Cookie |
| 错误 | 400：恢复码无效/已使用/新密码少于 8 位 |
| 调用位置 | AuthPages.jsx `ResetPasswordPage.submit` |

### 0.4 查询当前登录用户

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/api/auth/me` |
| 响应 200 | `{ user }`；未登录 401；携带有效 remember Cookie 时自动续期返回 `{ user, renewed: true }` 并重置两个 Cookie |
| 调用位置 | main.jsx `checkSession`（应用启动时） |

### 0.5 注销

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/logout` |
| 响应 200 | `{ ok: true }`，清除 auth + remember Cookie 并吊销对应 remember token |
| 调用位置 | main.jsx 侧边栏退出按钮处理函数 |

### 0.6 读取服务端偏好

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/api/auth/preferences` |
| 响应 200 | `{ preferences: {...} }`；未登录 401 |
| 调用位置 | main.jsx 偏好同步 effect（`authUser` 变化后拉取并应用语言/侧栏/视图/任务草稿等） |

### 0.8 读取我的 API 配置

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/api/auth/model-config` |
| 响应 200 | `{ model_config: { use_own_key, has_api_key, api_key_preview, base_url, model_name, ...(video/tts 同构), updated_at } }`；密钥只回掩码预览；未登录 401 |
| 调用位置 | main.jsx 登录后 effect（恢复“我的 API”表单） |

### 0.9 保存我的 API 配置

| 项目 | 内容 |
|------|------|
| 方法 | `PUT` |
| URL | `/api/auth/model-config` |
| 请求体 | `{ use_own_key?, api_key?, base_url?, model_name?, video_*?, tts_*? }`；密钥字段留空不下发=保持不变 |
| 响应 200 | `{ model_config }`（掩码视图）；400：use_own_key 无 key / base_url 非 http(s) / 字段非法 |
| 调用位置 | main.jsx `saveUserModelConfig`（设置弹窗公开模式保存按钮） |

### 0.10 清除我的 API 配置

| 项目 | 内容 |
|------|------|
| 方法 | `DELETE` |
| URL | `/api/auth/model-config` |
| 响应 200 | `{ ok: true }` |
| 调用位置 | main.jsx `clearUserModelConfig`（“清除我的配置”按钮） |

### 0.7 合并更新服务端偏好

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/preferences` |
| 请求体 | `{ preferences: { key: value, ... } }`（合并语义；键禁 `__` 前缀且 ≤64 字符，整体 ≤16KB） |
| 响应 200 | `{ preferences: {...} }`（合并后全量） |
| 调用位置 | main.jsx 偏好同步 effect（受控状态变更后 1s 防抖回写，`prefsApplyingRef` 防回环） |

## 1. 通用请求封装

| 函数 | 位置 | 说明 |
|------|------|------|
| `request(url, options)` | main.jsx:36 | 基于 `fetch` 的通用请求封装，默认 `credentials: "same-origin"`，自动解析 JSON。 |
| `fileUrl(path)` | main.jsx:55 | 生成 `/files?path=` 预览 URL。 |
| `downloadFileUrl(path)` | main.jsx:56 | 生成带 `download=1` 的下载 URL。 |

## 2. 素材相关报文

### 2.1 获取上传列表

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/uploads` |
| 请求头 | Cookie（后端取 owner_id） |
| 响应 | `{ items: UploadItem[] }` |
| 调用位置 | main.jsx:384 |

### 2.2 上传文件

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/uploads` |
| 请求体 | `multipart/form-data`，字段名 `files` 或 `file` |
| 响应 | `{ items: UploadItem[] }` |
| 调用位置 | main.jsx:703 |

### 2.3 删除上传

| 项目 | 内容 |
|------|------|
| 方法 | `DELETE` |
| URL | `/uploads?path=${encodeURIComponent(displayPath)}` |
| 响应 | `{ deleted: true, path, display_path, deleted_analysis_count }` |
| 调用位置 | main.jsx:736 |

## 3. 任务相关报文

### 3.1 获取任务列表

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/jobs` |
| 响应 | `{ items: JobSummary[] }` |
| 调用位置 | main.jsx:460 |

### 3.2 创建任务

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/jobs` |
| 请求体 | `{ task, mode, enable_phase2_research, enable_plan_review, direct_phase3_execution, prefer_local_materials, target_duration_seconds, deadline_seconds, processing_mode, output_profile, enabled_material_platforms }` |
| 响应 | 新建 JobRecord |
| 调用位置 | main.jsx:788 |

### 3.3 获取任务详情

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/jobs/${jobId}` |
| 响应 | JobRecord |
| 调用位置 | main.jsx:480/518/548 |

### 3.4 删除任务

| 项目 | 内容 |
|------|------|
| 方法 | `DELETE` |
| URL | `/jobs/${jobId}` |
| 响应 | `{ deleted: true }` |
| 调用位置 | main.jsx:756 |

### 3.5 取消任务

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/jobs/${jobId}/cancel` |
| 请求体 | `{}` |
| 调用位置 | main.jsx:800 |

### 3.6 恢复任务

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/jobs/${jobId}/resume` |
| 请求体 | `{}` |
| 调用位置 | main.jsx:807 |

## 4. 任务事件与消息报文

### 4.1 获取事件

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/jobs/${jobId}/events?after=${after}` |
| 响应 | `{ items: RuntimeEvent[] }` |
| 调用位置 | main.jsx:481/549/518 |

### 4.2 SSE 实时事件流

| 项目 | 内容 |
|------|------|
| 类型 | `EventSource` |
| URL | `/jobs/${jobId}/events/stream?after=${after}` |
| 事件 | `message`（数据为 RuntimeEvent JSON）、`end` |
| 调用位置 | main.jsx:506 |

### 4.3 获取消息

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/jobs/${jobId}/messages` |
| 响应 | `{ items: Message[] }` |
| 调用位置 | main.jsx:393/482/550 |

### 4.4 发送消息

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/jobs/${jobId}/messages` |
| 请求体 | `{ content: string }` |
| 响应 | Message |
| 调用位置 | main.jsx:817 |

### 4.5 下载事件日志

| 项目 | 内容 |
|------|------|
| 方法 | `GET`（下载） |
| URL | `/jobs/${jobId}/events.log` |
| 响应 | text/plain 附件 |
| 调用位置 | 通过 `downloadFileUrl` 间接使用 |

## 5. 计划相关报文

### 5.1 获取当前计划

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/jobs/${jobId}/plans/current` |
| 响应 | Plan |
| 调用位置 | main.jsx:405 |

### 5.2 提交计划反馈

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/jobs/${jobId}/plans/${version}/feedback` |
| 请求体 | `{ content: string }` |
| 响应 | Plan |
| 调用位置 | main.jsx:836 |

### 5.3 批准计划

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/jobs/${jobId}/plans/${version}/approve` |
| 请求体 | `{}` |
| 响应 | Plan |
| 调用位置 | main.jsx:847 |

### 5.4 拒绝计划

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/jobs/${jobId}/plans/${version}/reject` |
| 请求体 | `{}` |
| 响应 | Plan |
| 调用位置 | main.jsx:858 |

## 6. 文件预览/下载报文

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/files?path=${encodeURIComponent(path)}`（预览）或 `&download=1`（下载） |
| 响应 | 二进制文件流 |
| 调用位置 | `fileUrl()` / `downloadFileUrl()` |

## 7. 后续鉴权/改造要点

- [x] 登录后所有请求携带 Session Cookie；401 统一跳转登录页（已实现）。
- [x] 新增 `/api/auth/register`、`/api/auth/login`、`/api/auth/logout`、`/api/auth/me`、`/api/auth/password`、`/api/auth/reset` 等接口对接（已实现）。
- [x] remember-me 登录（`remember_me` 字段 + `crayotter_remember` Cookie 自动续期）与 `/api/auth/preferences` 服务端偏好同步（2026-08-02 已实现，含 E2E 截图验证）。
- [x] “API 来源”二选一（平台配额/我的 API）+ `/api/auth/model-config` 三接口对接（2026-08-04 已实现，含 E2E 截图与真实 agent 冒烟）。
- [ ] `/config` 前端已读取，后续可能需要区分公开/管理员配置。
- [ ] 上传大文件需要前端分片或调整 Nginx `client_max_body_size`。
- [ ] SSE 连接在登录态过期时应自动重连或跳转。
- [ ] 认证接口前端增加提交频率限制提示（后端限流待实现）。
