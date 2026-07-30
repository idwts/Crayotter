# 项目控制表

> 智能体速记：本目录是 Crayotter 第一版上线（`balanced-control-plane.md`）的控制平面文档集合。任何智能体接手本项目时，应先通读本目录下的 `server-survey.md`、`backend-apis.md`、`frontend-messages.md`、`frontend-style.md`，再查看 `../worklogs/component-worklog.md` 了解当前进度。

## 控制表清单

| 文件 | 用途 | 阅读优先级 |
|------|------|------------|
| [server-survey.md](./server-survey.md) | 线上服务器 `8.161.229.68` 的现状调研 | ⭐⭐⭐ 必读 |
| [directory-tree.md](./directory-tree.md) | 项目源码与配置文件目录树及简介 | ⭐⭐⭐ 必读 |
| [backend-apis.md](./backend-apis.md) | 后端 HTTP 接口与 RuntimeManager 方法 | ⭐⭐⭐ 必读 |
| [frontend-messages.md](./frontend-messages.md) | 前端所有 API 报文（方法/URL/请求体/响应体） | ⭐⭐⭐ 必读 |
| [frontend-style.md](./frontend-style.md) | 前端设计系统、组件清单与复用约定 | ⭐⭐ 改造前端前读 |
| [../worklogs/component-worklog.md](../worklogs/component-worklog.md) | 组件级别工作日志与进度 | ⭐⭐⭐ 必读 |

## 维护约定

1. **新增接口**必须同步更新 `backend-apis.md` 与 `frontend-messages.md`。
2. **新增组件/页面**必须同步更新 `frontend-style.md` 与 `directory-tree.md`。
3. **服务器配置变更**必须同步更新 `server-survey.md`。
4. **任务进度变更**必须同步更新 `component-worklog.md`。
5. 每个 Markdown 文件开头必须保留一段智能体说明，说明阅读目标。

## 当前阶段

第一版上线尚未开始代码实现，处于调研与文档准备阶段。详见 [component-worklog.md](../worklogs/component-worklog.md)。
