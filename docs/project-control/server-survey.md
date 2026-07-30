# 服务器现状调研

> 调研目标：为 Crayotter 第一版上线（balanced-control-plane）评估当前服务器与代码现状，识别已就绪项与缺口。智能体阅读本表后应记住：线上目前是一个 **Public Trial 单实例** 部署，具备基础 HTTP + Nginx 反代 + 匿名会话隔离，但 **HTTPS、PostgreSQL、Docker、真实用户体系均未落地**。

## 1. 服务器基本信息

| 项目 | 值 |
|------|-----|
| 主机 | 阿里云 ECS |
| 公网 IP | `8.161.229.68` |
| 系统 | Ubuntu 5.15.0-186-generic |
| 架构 | x86_64 |
| 内存 | 7.1 GiB（可用约 6.3 GiB） |
| 磁盘 | 40 GiB（已用 5.0 GiB / 14%） |
| Swap | 无 |
| 当前时间 | 2026-07-31 04:15 CST |
| 运行时长 | 3 天 13 小时 |

## 2. 服务状态

| 服务 | 状态 | 说明 |
|------|------|------|
| Nginx | active | 监听 80，反代到后端 127.0.0.1:8765 |
| crayotter.service | active | Public trial 模式运行中，User=`crayotter` |
| PostgreSQL | inactive | 未安装/未启动 |
| Docker | inactive | 未安装/未启动 |

## 3. 项目部署路径

| 路径 | 用途 | 备注 |
|------|------|------|
| `/opt/crayotter` | 应用代码 | 非 git 仓库，是代码快照 |
| `/srv/crayotter` | 运行时数据 | jobs、uploads、logs、.env 等 |
| `/etc/systemd/system/crayotter.service` | 服务单元 | 见下方配置摘要 |
| `/etc/nginx/sites-enabled/crayotter` (推测) | Nginx 配置 | 实际文件待确认 |

## 4. Nginx 配置摘要

- 监听 `80`（HTTP），未配置 443/HTTPS。
- `client_max_body_size 64k`（**注意**：与第一版规划的单文件 500 MiB 不符，且 multipart 上传走此限制）。
- 已配置 IP 限流：`limit_req zone=crayotter_per_ip burst=60 nodelay`。
- 已配置安全响应头：`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`。
- 反向代理到 `127.0.0.1:8765`，长连接保持 3600s。

## 5. crayotter.service 配置摘要

```ini
ExecStart=/opt/crayotter/.venv/bin/python script/run_backend.py --host 127.0.0.1 --port 8765
Environment=CRAYOTTER_RUNTIME_ROOT=/srv/crayotter
Environment=CRAYOTTER_PUBLIC_MODE=1
Environment=CRAYOTTER_COOKIE_SECURE=0
Environment=CRAYOTTER_MAX_JSON_BODY_BYTES=65536
Environment=CRAYOTTER_PUBLIC_JOBS_PER_HOUR=3
Environment=CRAYOTTER_PUBLIC_ACTIVE_JOBS_PER_SESSION=1
```

- 当前为 Public Trial 模式，匿名 owner_id 会话。
- `ProtectSystem=strict`、`ProtectHome=true`，只读写 `/srv/crayotter`。

## 6. 线上代码版本

- `/opt/crayotter` **不是 git 仓库**，无法直接读取 commit hash。
- 文件修改时间集中在 **2026-07-27 18:47–22:45**，推测为一次手动上传部署。
- 与本地 `feature` 分支的关系需要人工 diff 确认。

## 7. 环境变量（已脱敏）

| 变量 | 线上值/说明 |
|------|-------------|
| `CRAYOTTER_BASE_URL` | DashScope 兼容模式 |
| `CRAYOTTER_MODEL_NAME` | `qwen-plus` |
| `CRAYOTTER_VIDEO_MODEL_NAME` | `qwen-vl-max-latest` |
| `CRAYOTTER_TTS_MODEL_NAME` | `qwen-tts-latest` |
| `CRAYOTTER_ENABLE_PHASE2_RESEARCH` | true |
| `CRAYOTTER_ENABLE_PLAN_REVIEW` | true |
| `CRAYOTTER_*_POOL_SIZE` | 已配置 search/download/video_analysis/llm/ffmpeg/tts/export |
| API KEY / TOKEN | 已脱敏，不在本文档记录 |

## 8. 与 balanced-control-plane 第一版目标的差距

| 模块 | 现状 | 差距 |
|------|------|------|
| M1 发布回滚 | 无版本化 release 目录，无回滚脚本 | 大 |
| M2 HTTPS | Nginx 仅 HTTP，client_max_body_size 64k | 大 |
| M3 PostgreSQL | 未安装 | 大 |
| M4 账号体系 | 无注册/登录/Session/恢复码 | 大 |
| M5 租户隔离 | 仅有匿名 owner_id，无 tenant 模型 | 中 |
| M6 前端账号 | 无登录/注册 UI | 大 |
| M7 Docker Worker | Docker 未安装，进程直接在服务内运行 | 大 |
| M8 配额审计 | 仅有简单 PublicTrialGuard（每小时 3 任务、每会话 1 活动任务） | 中 |

## 9. 关键风险

1. **Nginx 上传限制 64k**：与 500 MiB 素材上传目标冲突，需要改为 `client_max_body_size 500m` 或更大。
2. **无 HTTPS**：账号密码无法安全传输。
3. **无 PostgreSQL**：用户数据、任务队列、审计均无法持久化与隔离。
4. **线上代码非 git 管理**：发布/回滚/追踪困难。
5. **Docker 未安装**：M7 容器化任务执行无法开始。

## 10. 建议下一步（优先级）

1. 在服务器安装 PostgreSQL 并仅监听本地。
2. 调整 Nginx：`client_max_body_size` 与 HTTPS（Let's Encrypt IP 证书或自签证书过渡）。
3. 在 `feature` 分支实现账号/租户/Session 数据层。
4. 为后端增加 FastAPI/Flask 风格路由，替代当前 BaseHTTPRequestHandler（若第一版规划如此）。
5. 准备 Docker rootless 运行环境与 worker 镜像。
