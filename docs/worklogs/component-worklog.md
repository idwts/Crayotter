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

### 组件名：PostgreSQL 部署
- **负责人**：待定
- **状态**：未开始
- **已做**：
  - [x] 调研：服务器未安装 PostgreSQL
- **待做**：
  - [ ] 安装 PostgreSQL
  - [ ] 创建业务库与低权限账号
  - [ ] 配置仅本地监听
  - [ ] 执行初始迁移脚本
- **阻塞/风险**：
  - 无

### 组件名：表结构设计
- **负责人**：待定
- **状态**：未开始
- **关联文档**：[balanced-control-plane.md](../../balanced-control-plane.md)
- **已做**：
  - [x] 第一版规划已确定 8 张表
- **待做**：
  - [ ] 设计并创建 users/tenants/sessions/recovery_codes/jobs/uploads/artifacts/audit_logs
  - [ ] 配置 RLS
  - [ ] 编写迁移脚本
- **阻塞/风险**：
  - 无

### 组件名：账号认证（注册/登录/注销/改密/恢复码）
- **负责人**：待定
- **状态**：未开始
- **关联文档**：[backend-apis.md](./backend-apis.md)、[frontend-messages.md](./frontend-messages.md)
- **已做**：
  - [x] 梳理现有 owner_id Cookie 机制
- **待做**：
  - [ ] 后端新增 `/api/auth/*` 接口
  - [ ] 密码 SHA-256 + 随机盐哈希
  - [ ] 服务端 Session 与 Cookie 设置
  - [ ] 前端新增登录/注册页面
  - [ ] 注册恢复码生成与展示
- **阻塞/风险**：
  - 需要 PostgreSQL 就绪后才能联调。

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
- **状态**：未开始
- **关联文档**：[frontend-style.md](./frontend-style.md)
- **已做**：
  - [x] 梳理现有组件与样式系统
- **待做**：
  - [ ] 新增 LoginPage / RegisterPage / AccountPage
  - [ ] 401 统一处理
  - [ ] 新增导航入口（账号、退出）
- **阻塞/风险**：
  - 需与后端 `/api/auth/*` 同步开发。

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
