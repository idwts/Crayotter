# 组件级别 Worklog

> 说明：本日志按组件记录 Crayotter 第一版上线过程中的状态、决策、阻塞与待办。智能体阅读后应记住：**当前第一版尚未开始编码，已有 Public Trial 骨架；工作日志采用 "组件 → 状态 → 已做 → 待做 → 风险" 结构，便于持续更新。**

## 记录格式模板

```markdown
### 组件名：[名称]
- **负责人**：待定 / [姓名]
- **状态**：未开始 / 设计中 / 开发中 / 待验证 / 已完成
- **关联文档**：[链接]
- **已做**：
  - [x] 事项 1
- **待做**：
  - [ ] 事项 2
- **关键决策**：
  - 决策内容
- **阻塞/风险**：
  - 风险描述
```

---

## 当前组件状态

### 组件名：Nginx HTTPS 配置
- **负责人**：待定
- **状态**：未开始
- **关联文档**：[server-survey.md](./server-survey.md)
- **已做**：
  - [x] 调研：当前 Nginx 仅监听 80，client_max_body_size 64k
- **待做**：
  - [ ] 申请 Let's Encrypt 短期 IP 证书
  - [ ] 配置 443 与 80→443 跳转
  - [ ] 将 client_max_body_size 改为 500m
  - [ ] 配置证书自动续期
- **关键决策**：
  - 短期使用 IP 证书，暂不使用域名证书。
- **阻塞/风险**：
  - Let's Encrypt 对 IP 证书支持有限，可能需要 ZeroSSL 或自签过渡。

### 组件名：账号认证（注册/登录/注销/改密/恢复码）
- **负责人**：待定
- **状态**：已完成（服务器已验证）
- **关联文档**：[backend-apis.md](../project-control/backend-apis.md)、[frontend-messages.md](../project-control/frontend-messages.md)
- **已做**：
  - [x] 梳理现有 owner_id Cookie 机制
  - [x] 后端新增 `/api/auth/*` 接口（register/login/logout/me/password/reset）
  - [x] 密码 SHA-256 + 随机盐哈希
  - [x] 服务端 Session 与 Cookie 设置（`crayotter_auth_session`，30 天滚动过期）
  - [x] 恢复码注册下发、一次性使用、重置后吊销全部 session
  - [x] 15 项 API 测试在服务器通过（tests/test_auth_api.py）
  - [x] remember-me 持久登录（selector:validator 轮换 + 盗用检测，`crayotter_remember` Cookie，migration 003）
  - [x] 服务端偏好同步 `/api/auth/preferences`（浏览器历史动作记忆：语言/侧栏/视图/任务草稿）
  - [x] 注册成功自动建立会话（前端注册后直入工作台）
  - [x] 24 项 API 测试 + 13 步 E2E 截图验证（本地与服务器双端通过）
- **待做**：
  - [ ] 认证接口频率限制与失败锁定
- **关键决策**：
  - auth Cookie 与匿名 `crayotter_session` 分离，互不干扰。
  - 审计中发现 reset 缺少密码强度校验，已补齐（与 change_password 一致 ≥8 位）。
  - `_write_json` 重构为 `set_auth_token/clear_auth/set_remember_token/clear_remember` 参数，修复 Set-Cookie 在 send_response 前发出的 HTTP 响应损坏 bug。
  - remember token 采用 OWASP selector:validator 模型：DB 仅存 validator digest，每次使用轮换；乐观锁 UPDATE + 10s `last_used_at` 宽限区分并发竞争与盗用（Jaspan 模式），确认盗用吊销该用户全部 token 并审计。
  - 密码永不在浏览器持久化；"记住用户名"仅存 localStorage 用户名。
- **阻塞/风险**：
  - 无。

### 组件名：PostgreSQL 部署
- **负责人**：待定
- **状态**：已完成
- **已做**：
  - [x] 调研：服务器未安装 PostgreSQL
  - [x] 安装 PostgreSQL 14（服务器 8.161.229.68）
  - [x] 创建 `crayotter` 业务库与账号
  - [x] 执行迁移 001/002（表结构 + role 字段）
  - [x] systemd `crayotter.service` 注入 `CRAYOTTER_DATABASE_URL`
- **阻塞/风险**：
  - 无

### 组件名：表结构设计
- **负责人**：待定
- **状态**：已完成
- **关联文档**：[balanced-control-plane.md](../../balanced-control-plane.md)
- **已做**：
  - [x] 第一版规划已确定 8 张表
  - [x] 设计并创建 users/tenants/sessions/recovery_codes/jobs/uploads/artifacts/audit_logs
  - [x] 配置 RLS（current_app_tenant_id + tenant_isolation policies）
  - [x] 编写迁移脚本 001（初始）/002（users.role）
- **阻塞/风险**：
  - 无

### 组件名：RuntimeManager 租户化
- **负责人**：待定
- **状态**：未开始
- **关联文档**：[backend-apis.md](./backend-apis.md)
- **已做**：
  - [x] 梳理 RuntimeManager 方法清单
- **待做**：
  - [ ] 所有方法增加 tenant_id 参数
  - [ ] 路径解析改为 `/srv/crayotter/tenants/{tenant_uuid}/`
  - [ ] 历史匿名数据归档
- **阻塞/风险**：
  - 改动面大，需要完整回归测试。

### 组件名：前端账号流程
- **负责人**：待定
- **状态**：已完成（服务器已验证）
- **关联文档**：[frontend-style.md](../project-control/frontend-style.md)
- **已做**：
  - [x] 梳理现有组件与样式系统
  - [x] 新增 LoginPage / RegisterPage / ResetPasswordPage（AuthPages.jsx）
  - [x] 401 统一处理（setUnauthorizedHandler → 跳登录页）
  - [x] 新增导航入口（侧边栏用户名展示、退出按钮）
  - [x] 登录页"忘记密码？"入口 → 恢复码重置密码页面
  - [x] 注册成功页展示一次性恢复码
- **待做**：
  - [ ] 新增 AccountPage（账号信息/改密入口）
- **阻塞/风险**：
  - 无

### 组件名：Docker Worker（用户任务受限容器）
- **负责人**：待定
- **状态**：未开始
- **关联文档**：[server-survey.md](./server-survey.md)
- **已做**：
  - [x] 调研：服务器未安装 Docker
- **待做**：
  - [ ] 安装 Docker 并启用 rootless
  - [ ] 构建 worker 镜像
  - [ ] 实现 Runner 与任务租约
  - [ ] 配置容器资源限制（3 vCPU、4 GiB、PID 512 等）
- **阻塞/风险**：
  - 服务器资源有限（7.1 GiB 内存），Docker rootless 可能有额外开销。

### 组件名：配额与磁盘管理
- **负责人**：待定
- **状态**：部分就绪
- **已做**：
  - [x] PublicTrialGuard 已实现每小时 3 任务、每会话 1 活动任务限制
  - [x] 上传容量限制 500 MB/文件、1.5 GB/会话
- **待做**：
  - [ ] 按真实用户配置配额
  - [ ] 磁盘水位监控（75%/85%）
  - [ ] 审计日志
- **阻塞/风险**：
  - 需要用户表与租户模型。

### 组件名：核心 Agent 架构
- **负责人**：待定
- **状态**：已就绪（持续迭代）
- **已做**：
  - [x] Phase 1/2/3 流程跑通
  - [x] ResourceScheduler + ArtifactRegistry
  - [x] ReAct fallback
- **待做**：
  - [ ] 容器化执行
  - [ ] 任务崩溃恢复与孤儿清理
  - [ ] TTS 限流重试与 fallback token 截断（bugfix 已修复）
- **阻塞/风险**：
  - TTS 限流、模型上下文限制。

## 变更记录

| 日期 | 记录人 | 变更 |
|------|--------|------|
| 2026-07-31 | Claude | 初始化组件 worklog，记录第一版上线前各组件状态。 |
| 2026-07-31 | Claude | PostgreSQL 部署、表结构设计、账号认证、前端账号流程标记为已完成；新增 `/api/auth/reset` 恢复码重置密码（后端审计修复密码强度校验 + 前端忘记密码入口）；记录 `_write_json` Set-Cookie 顺序 bug 修复。 |
| 2026-08-02 | Claude | cookie 机制上线：remember-me 持久登录（OWASP selector:validator 轮换 + 乐观锁并发控制 + 10s 宽限盗用检测，migration 003）+ `/api/auth/preferences` 服务端历史动作记忆（语言/侧栏/视图/任务草稿等 1s 防抖同步）；前端记住我/记住用户名复选框；注册成功自动建立会话；修复 main.jsx checkSession TDZ 白屏 bug；同步服务器新版 runtime_manager/models（owner_id 隔离）回 git；本地+服务器双端 24 项功能测试与 13 步 E2E 截图验证全过。 |
| 2026-08-04 | Claude | 高价值优化三项：①任务产物保留 janitor（RuntimeManager 后台线程，终态/interrupted 超 CRAYOTTER_JOB_RETENTION_DAYS=7 天整目录清除，CRAYOTTER_JOB_JANITOR_INTERVAL_SECONDS=21600，单测 7 项双端全过）；②SIGTERM 优雅停机（server 信号 handler → begin_shutdown + manager.shutdown 落盘 interrupted，修复 agent 子进程同收信号导致 worker 抢先把任务标 failed 的停机竞态，重启不再出现 SIGKILL）；③前端自适应轮询（tab 隐藏暂停、活跃任务 6s、空闲 30s，smoke 实测 45s 仅 1 次空闲轮询）。生命周期 E2E 在新代码上回归全过。 |
| 2026-08-04 | Claude | 前端 `request()` 增加 503 退避 2.5s 自动重试一次（配合 nginx 限流提到 180r/m，resume/cancel 等动作被瞬时限流时无需用户手动再点）；已重新构建并部署 app/frontend。 |
| 2026-08-04 | Claude | issue 修复与任务生命周期 E2E：#33 字幕多行被裁切修复（y 扣除文本块实际高度，渲染实证 bottom_gap=61px）；质量门 true_peak 压线误判修复（loudnorm 目标 TP 留 0.5dB 余量，实证 -1.96≤-1.5；对抗审计否决 two-pass 假设）；**resume 阻断 bug 修复**（`owner_id` 因 `Field(exclude=True)` 不落 summary.json，重启后任务对所有者不可见）；新增生命周期 E2E（tests/test_e2e_job_lifecycle.py，9 截图全过：创建/历史查看/详情/取消/重启→可恢复/继续任务/再取消）；test_media_consistency 11 项回归全过；记录 nginx 限流（60r/m+burst60）致动作请求偶发 503（前端 toast 显式暴露，E2E 模拟用户重试通过）；合作者 bugfix 提交（8b4eb59、85b9df2）全套 135 tests 通过，#31 由 85b9df2 覆盖，#30 定性为架构级改造记录不改代码。 |
| 2026-08-04 | Claude | 主服务接入服务器：平台 dashscope key 运营者配置（/srv/crayotter/.env，公开视图只暴露 operator_api_configured）、补装 yt-dlp 修复下载失败；BYOK 持久化（migration 004 user_model_configs，Fernet 加密，`/api/auth/model-config` GET/PUT/DELETE，`POST /jobs` 按用户密钥覆盖且不占公开配额）；前端设置弹窗“API 来源”二选一 + 我的 API 表单；修复 Fernet key 路径解析跨进程不一致 bug；31 项功能测试双端全过、BYOK E2E 7 步截图全过、真实 agent 冒烟通过 Phase 1（LLM 规划+检索+下载+视频分析全链路）。 |
| 2026-08-04 | Claude | 失败任务恢复选择：后端 `resume_job(strategy=resume/restart)`（failed 也可恢复；restart 使 revision+1、清空 final_output/output_files）；前端失败任务详情显示「从断点继续/重新开始」两按钮（restart 带确认弹窗）；冲突单测 8 项（strategy 校验、状态门槛、并发任务冲突、owner 校验）+ 恢复 E2E（invalid BYOK key 造 failed，双路径截图）全过。 |
| 2026-08-04 | Claude | 素材库条件搜索：后端 `GET /uploads` 支持 `q`/`has_analysis`/`sort`/`order`；前端素材库工具栏搜索框+两个下拉（300ms 防抖）；修复 Public 模式 display_path `user_temp/` 前缀致 DELETE 400 的 bug（`_resolve_upload_path` 回退剥离前缀）；后端功能测试 7 步 + 素材库 E2E 5 步截图全过。 |
| 2026-08-04 | Claude | 小优化：`GET /jobs/{id}/plans/current` 无计划时由 404 KeyError 改为 200 `{"plan": null, ...}`，消除前端轮询常态 404 噪音。 |
| 2026-08-06 | Claude | 素材免责声明：素材库列表顶部常驻提示条（i18n `materialDisclaimer`，告诫勿上传/使用含个人隐私、敏感信息或未授权内容素材，上传即确认权利并自担责任）+ 创作选项弹层「上传并关联新素材」下方小字提示（`attachMaterialDisclaimer`）；纯前端展示层，无后端改动；E2E 3 步截图全过（中文提示条/弹层提示/英文提示条，英文用新 context 预置 localStorage + 双 cookie 注入规避语言偏好回写抖动）。 |
| 2026-08-06 | Claude | 大文件分片上传：后端 `init/chunk/complete/abort` 四接口（单文件硬上限 2GB、每片 1MB、24h 过期清理、PublicTrialGuard 容量校验）；前端素材库「上传大文件」按钮，小文件仍走原 `/uploads` 接口；nginx 新增 `/uploads/chunked` location（`client_max_body_size 2m`）；功能测试 + E2E 全过。 |
| 2026-08-06 | Claude | 磁盘水位 LRU：`RuntimeManager.evict_lru_jobs()` 在 janitor 循环中检测 JOBS_DIR 分区使用率，超 70% 时按最近使用时间从旧到新清除终态任务目录，interrupted 最后，running/queued 永不删除，目标水位 60%；环境变量 `CRAYOTTER_DISK_LRU_THRESHOLD_PERCENT`/`TARGET_PERCENT`；单测 6 项全过。 |
| 2026-08-06 | Claude | 素材库批量管理 + 在线预览：每行复选框 + 全选 +「删除选中(N)」批量删除（带确认弹窗）；点击「预览」打开 video 弹层直接播放；E2E 截图验证。 |
| 2026-08-06 | Claude | 任务完成通知：创作选项弹层新增「完成时通知我」开关（localStorage 持久化 + 请求 Notification 权限）；轮询发现任务从 queued/running 进入 completed/failed 时发送浏览器系统通知（点击聚焦并选中任务）；E2E 验证 localStorage 持久化。 |
| 2026-08-06 | Claude | 小改进：标签页标题实时反映活跃任务数（`document.title = "(N 运行中) Crayotter Workbench"`，任务结束自动恢复原标题）；与完成通知互补，不开通知权限的用户切走标签页也能看到状态；i18n `tabTitleActive`；E2E 实测 idle→运行中→恢复全链路。 |
| 2026-08-10 | Claude | design-review skill 全流程评审（11 张三端截图，`.design/workbench/DESIGN_REVIEW.md`）→ 修复三项 Must Fix：①移动端 hero 文字逐字竖排+统计卡重叠、②平板统计栏挤压——根因均为 styles.css 尾部顶层覆盖块（5113 行 `minmax(760px,1fr) minmax(250px,300px)`）凭源码顺序覆盖窄屏 media query，已在文件末尾补 ≤920px 单列/≤640px 统计卡单列守卫；③桌面 1280px 下 composer 创作选项弹层左缘被裁——轨道总宽 1036>1008 溢出后被 `justify-content:center` 对称外推 + `overflow:hidden` 裁切，第一列 min 760→600 修复。三端截图数值+目视验证，素材高级 E2E 6 步回归全过；级联顺序陷阱已记录于 frontend-style.md 第 7 节。 |
| 2026-08-11 | Claude | 整体性检查（前端设计/后端接口/架构稳定性/文档四线并行，双子 agent 审计 + 线上实测）。发现并修复：①SSE `/jobs/{id}/events/stream` 对不存在任务先发的 200 头后 KeyError 冒泡、404 被序列化进 body 的损坏响应（线上抓包实证）→ `_stream_events` 先校验任务再发头；②错误码跨方法不一致：GET 增加 RuntimeError→409、ValueError/TypeError→400，PUT 增加 RuntimeError→409（原 GET 参数错误落 500）；③`get_job_detail` 回传 job_dir/events_path/summary_path 服务器绝对路径（无前端消费方）→ 移除，收敛信息泄露面。文档同步：frontend-messages 补分片上传 4 报文 + PUT /config + GET /health 整块缺失；frontend-style §3 删除 7 个 styles.css 中不存在的"关键类"；directory-tree 补登 3 个测试文件；backend-apis 修正 uploads 限流归因（容量常量非 PublicTrialGuard）+ RuntimeManager 行号全表更新 + 错误码约定。新增 tests/test_api_hardening.py 6 步回归全过；disk LRU 6 项、auth 全套、分片上传回归全过。后端已部署重启。组件复用建议（`_require_auth` 7 处、`_require_job` ~15 处、`_get_cookie` 3 处等）按规则 2/3 不顺手重构，留作后续专门迭代。 |
