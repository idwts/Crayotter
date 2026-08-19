# 前端报文表

> 说明：本表汇总 `app/frontend_src/src/main.jsx` 及辅助模块中所有前端发起的 HTTP/SSE 请求，便于后续鉴权、Mock、调试和接口改造。智能体阅读后应记住：**前端通过全局 `request(url, options)` 调用后端，自动携带 Cookie；登录态由 `crayotter_auth_session` Cookie 维护（`authUser` 状态 + 401 统一跳登录页），匿名任务隔离仍依赖 `crayotter_session`；认证页面组件在 `AuthPages.jsx`（Login/Register/ResetPassword）。**

## 0. 认证报文（2026-07-31 上线）

### 0.1 注册

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/register` |
| 请求体 | `{ username, password, security_question?, security_answer?, agree_terms }`；密保可选但问题与答案必须成对（2026-08-17 起）；`agree_terms` 必传 true，未同意用户协议 400（2026-08-19 起） |
| 响应 201 | `{ user, tenant, recovery_codes }`；2026-08-02 起同时 `Set-Cookie: crayotter_auth_session`（注册即登录） |
| 调用位置 | AuthPages.jsx `RegisterPage.submit` |

### 0.2 登录

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/login` |
| 请求体 | `{ username, password, remember_me, agree_terms }`；`agree_terms` 必传 true，未同意 400（2026-08-19 起） |
| 响应 200 | `{ user, expires_at }`，`Set-Cookie: crayotter_auth_session`；`remember_me=true` 时追加 `Set-Cookie: crayotter_remember`（30 天，HttpOnly+SameSite=Lax） |
| 调用位置 | AuthPages.jsx `LoginPage.submit` |

### 0.3 忘记密码重置（恢复码 / 密保问题双通道）

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/reset` |
| 请求体 | 恢复码通道：`{ username, recovery_code, new_password }`；密保通道：`{ username, security_answer, new_password }`（2026-08-17 起，两者共用本端点与失败锁定） |
| 响应 200 | `{ ok: true }`，后端吊销该用户全部 session 并清除 Cookie |
| 错误 | 400：恢复码无效/已使用、密保答案不正确、新密码少于 8 位 |
| 调用位置 | AuthPages.jsx `ResetPasswordPage.submit`（页面含「恢复码找回 / 密保问题找回」切换） |

### 0.3a 查询密保问题

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/api/auth/security-question?username=` |
| 响应 200 | `{ question }`；用户不存在或未设置时 `question` 为 `null`；无需登录 |
| 调用位置 | AuthPages.jsx `ResetPasswordPage.fetchQuestion`（密保通道下用户名失焦/切换 tab 时自动查询） |

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

### 0.7 读取我的 API 配置

| 项目 | 内容 |
|------|------|
| 方法 | `GET` |
| URL | `/api/auth/model-config` |
| 响应 200 | `{ model_config: { use_own_key, has_api_key, api_key_preview, base_url, model_name, ...(video/tts 同构), updated_at } }`；密钥只回掩码预览；未登录 401 |
| 调用位置 | main.jsx 登录后 effect（恢复“我的 API”表单） |

### 0.8 保存我的 API 配置

| 项目 | 内容 |
|------|------|
| 方法 | `PUT` |
| URL | `/api/auth/model-config` |
| 请求体 | `{ use_own_key?, api_key?, base_url?, model_name?, video_*?, tts_*? }`；密钥字段留空不下发=保持不变 |
| 响应 200 | `{ model_config }`（掩码视图）；400：use_own_key 无 key / base_url 非 http(s) / 字段非法 |
| 调用位置 | main.jsx `saveUserModelConfig`（设置弹窗公开模式保存按钮） |

### 0.9 清除我的 API 配置

| 项目 | 内容 |
|------|------|
| 方法 | `DELETE` |
| URL | `/api/auth/model-config` |
| 响应 200 | `{ ok: true }` |
| 调用位置 | main.jsx `clearUserModelConfig`（“清除我的配置”按钮） |

### 0.10a 修改密码

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/api/auth/password` |
| 请求体 | `{ old_password, new_password }` |
| 响应 200 | `{ ok: true }`，后端吊销全部 session/remember token 并清除 Cookie，前端直接回登录页 |
| 错误 | 400：原密码错误/新密码少于 8 位；401：未登录 |
| 调用位置 | main.jsx `changePassword`（设置弹窗「账号安全」页签，2026-08-11 新增） |

> 认证限流（2026-08-11）：`/api/auth/login` 与 `/api/auth/reset` 同一 IP+账号 10 分钟内失败 5 次即锁定，返回 **429 `{ error, retry_after }`**；`/api/auth/register` 每 IP 每小时限 20 次，超限同 429。

### 0.10 合并更新服务端偏好

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
| URL | `/uploads?q=&has_analysis=&sort=&order=`（2026-08-04 起支持名称子串搜索、分析状态筛选、排序，300ms 防抖触发） |
| 请求头 | Cookie（后端取 owner_id） |
| 响应 | `{ items: UploadItem[] }` |
| 调用位置 | main.jsx `loadUploads(filters)`（素材库工具栏 `.materials-search-input` + 两个 `.materials-toolbar-select`） |

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
| 调用位置 | main.jsx `deleteUploads`（单行删除与批量删除共用，批量为循环调用） |

### 2.4 大文件分片上传（2026-08-06 新增）

| 步骤 | 方法 | URL | 请求体 | 响应 |
|------|------|-----|--------|------|
| 初始化 | `POST` | `/uploads/chunked/init` | `{ name, size }` JSON | 201 `{ upload_id, chunk_size, max_bytes }` |
| 上传分片 | `POST` | `/uploads/chunked/{upload_id}?index=N` | 二进制分片（原生 fetch，`Content-Type: application/octet-stream`，每片 = init 返回的 chunk_size，当前 1MB） | 200 `{ received_bytes, total_bytes }` |
| 完成合并 | `POST` | `/uploads/chunked/{upload_id}/complete` | `{}` | 201 `{ items: UploadItem[] }` |
| 中止清理 | `DELETE` | `/uploads/chunked/{upload_id}` | — | 200 `{ aborted: true }` |

- 调用位置：main.jsx `uploadLargeFiles`（素材库「上传大文件」按钮；小文件仍走 2.2 原接口）。
- 约束：单文件 ≤2GB；分片会话 24h 过期由后端清理；nginx 对 `/uploads/chunked` 单独 `client_max_body_size 2m`。
- 失败路径：任一分片非 2xx 时前端自动调中止接口清理会话。

### 2.5 通用与配置报文

| 方法 | URL | 说明 | 调用位置 |
|------|-----|------|----------|
| `GET` | `/health` | 启动时健康检查（`Promise.allSettled` 内，不阻塞渲染） | main.jsx `checkSession` 启动序列 |
| `PUT` | `/config` | 保存演示模式/工作流配置；Public 模式 403 | main.jsx `submitConfig`、`syncWorkflowConfig` |

> 说明：`request()` 封装（main.jsx）内含 503 限流自动退避 2.5s 重试一次；`fileUrl`/`downloadFileUrl` 紧随其后。行号随版本漂移，以函数名为准。4.1 节事件轮询实际不带 `?after=`（后端默认 after=0），仅 SSE 流携带 after。

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
| 请求体 | 前端实际发送 6 个字段：`{ task, mode, enable_phase2_research, enable_plan_review, direct_phase3_execution, prefer_local_materials }`；后端另支持可选字段 `target_duration_seconds, deadline_seconds, processing_mode, output_profile, enabled_material_platforms`（当前前端未发送，缺省走后端默认） |
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

### 3.6 恢复/重启任务

| 项目 | 内容 |
|------|------|
| 方法 | `POST` |
| URL | `/jobs/${jobId}/resume` |
| 请求体 | `{"strategy": "resume"}`（从断点继续，interrupted/failed 均可）或 `{"strategy": "restart"}`（重新开始，仅 failed，前端带确认弹窗） |
| 调用位置 | main.jsx `resumeJob(jobId, strategy)` / `restartJob(jobId)`；失败任务详情页显示「从断点继续」「重新开始」两个按钮（DashboardUI.jsx JobsView） |

### 3.7 任务完成通知

| 项目 | 内容 |
|------|------|
| 方法 | 状态开关（localStorage + 创作选项弹层） |
| 键值 | `notifyOnDone` / `notifyOnDoneHint` |
| 调用位置 | `DashboardUI.jsx Composer` 选项菜单、`main.jsx` 轮询检测任务状态变化 + `Notification` API |
| 说明 | 开启后任务从 queued/running 进入 completed/failed 时发浏览器系统通知；点击通知聚焦窗口并选中该任务 |

### 3.8 任务模板/一键复跑

| 项目 | 内容 |
|------|------|
| 行为 | 纯前端功能，无新接口：任务历史详情「用作模板」按钮读取 JobRecord 的 `task/mode/enable_phase2_research/enable_plan_review/direct_phase3_execution/prefer_local_materials`，套用进创作面板草稿与创作选项并跳转工作台，用户确认后仍走 3.2 `POST /jobs` 创建 |
| 键值 | `useAsTemplate` / `templateApplied` |
| 调用位置 | main.jsx `useJobAsTemplate`、DashboardUI.jsx JobsView 详情操作区（2026-08-17 新增） |

### 3.8a 使用引导与信息页（2026-08-19 新增）

| 项目 | 内容 |
|------|------|
| 行为 | 首次登录自动弹出使用引导（OnboardingDialog 五步轮播：工作台/素材库/任务历史/产物中心/我的 API），完成或跳过写 `localStorage["crayotter.onboardingDone.v1"]="1"`；侧栏底部「使用引导 · 《用户协议》 · 技术概览」三个入口可随时重开 |
| 信息页 | 用户协议/技术概览为占位页（内容开发中），登录前经登录/注册页《用户协议》链接打开（authView=agreement），登录后经侧栏 currentView=agreement/tech 打开；登录与注册必须勾选「我已阅读并同意《用户协议》」否则前端拦截+后端 400 |
| 调用位置 | main.jsx（onboardingOpen/authReturnView 状态）、OnboardingDialog.jsx、InfoPages.jsx、AuthPages.jsx 勾选框 |

### 3.9 标签页标题状态

| 项目 | 内容 |
|------|------|
| 行为 | 存在 queued/running 任务时 `document.title` 显示 `(N 运行中) Crayotter Workbench`（英文 `active`），全部结束后恢复原标题 |
| 键值 | `tabTitleActive` |
| 调用位置 | `main.jsx` jobs 轮询副作用（2026-08-06 新增） |

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
| 调用位置 | `src/logDownload.js` `jobEventsDownloadUrl(jobId)` 生成 URL + `downloadTextFile` 触发下载（注意：`downloadFileUrl` 生成的是 `/files?path=…&download=1`，与本接口无关） |

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
- [x] 新增 `/api/auth/register`、`/api/auth/login`、`/api/auth/logout`、`/api/auth/me`、`/api/auth/reset` 等接口对接（已实现；`/api/auth/password` 后端已就绪但前端尚无改密入口，见 frontend-style.md §6 后续建议）。
- [x] remember-me 登录（`remember_me` 字段 + `crayotter_remember` Cookie 自动续期）与 `/api/auth/preferences` 服务端偏好同步（2026-08-02 已实现，含 E2E 截图验证）。
- [x] “API 来源”二选一（平台配额/我的 API）+ `/api/auth/model-config` 三接口对接（2026-08-04 已实现，含 E2E 截图与真实 agent 冒烟）。
- [ ] `/config` 前端已读取，后续可能需要区分公开/管理员配置。
- [ ] 上传大文件需要前端分片或调整 Nginx `client_max_body_size`。
- [ ] SSE 连接在登录态过期时应自动重连或跳转。
- [ ] 认证接口前端增加提交频率限制提示（后端限流待实现）。
