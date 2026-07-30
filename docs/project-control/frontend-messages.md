# 前端报文表

> 说明：本表汇总 `app/frontend_src/src/main.jsx` 及辅助模块中所有前端发起的 HTTP/SSE 请求，便于后续鉴权、Mock、调试和接口改造。智能体阅读后应记住：**前端通过全局 `request(url, options)` 调用后端，依赖后端 Cookie 自动携带 `owner_id`；当前无显式登录态管理，所有状态存在 `localStorage`，第一版上线时需要接入 `/api/auth/*` 并改造为 session-based。**

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

- [ ] 登录后所有请求携带 Session Cookie；401 统一跳转登录页。
- [ ] `/config` 前端已读取，后续可能需要区分公开/管理员配置。
- [ ] 上传大文件需要前端分片或调整 Nginx `client_max_body_size`。
- [ ] SSE 连接在登录态过期时应自动重连或跳转。
- [ ] 新增 `/api/auth/register`、`/api/auth/login`、`/api/auth/logout`、`/api/auth/me`、`/api/auth/password` 等接口对接。
