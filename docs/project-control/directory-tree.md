# 目录树与文件简介

> 说明：本表记录项目源码与配置文件的用途，便于智能体快速定位。排除运行时目录（logs/runtime_logs/app_state/temp/user_temp 等）、缓存（__pycache__/.venv）、大型二进制（图片/字体/视频/构建产物）以及 HGPO 历史归档。

| 路径 | 简介 |

|------|------|

| .env.example | 环境变量示例 |
| AGENTS.md | Crayotter Project Agent Notes |
| app/__init__.py | Crayotter application package. |
| app/backend/__init__.py | Backend service package for the Crayotter desktop workbench. |
| app/backend/auth.py | 账号认证服务：注册/登录/注销/改密/恢复码重置/Session 管理/审计日志（SHA-256+盐）。 |
| app/backend/config_store.py | from __future__ import annotations |
| app/backend/db.py | PostgreSQL 连接池（ThreadedConnectionPool）与租户上下文（RLS set_tenant_id）。 |
| app/backend/event_bus.py | from __future__ import annotations |
| app/backend/models.py | from __future__ import annotations |
| app/backend/runtime_manager.py | from __future__ import annotations |
| app/backend/server.py | 手写 HTTP 路由后端：静态资源、uploads/jobs/plans/messages 业务接口 + /api/auth/* 认证接口。 |
| app/backend/services/__init__.py | from .artifacts import ArtifactQueryService |
| app/backend/services/artifacts.py | Read-only artifact projection used by the backend API. |
| app/backend/services/jobs.py | Persistence boundary for backend job summaries and event history. |
| app/backend/services/plans.py | Versioned editing-plan storage boundary. |
| app/backend/services/workers.py | OS process control for backend-managed agent workers. |
| app/backend/task_titles.py | Create a short, deterministic UI title without changing the task prompt. |
| app/frontend/index.html | <!DOCTYPE html> |
| app/frontend_src/index.html | <!DOCTYPE html> |
| app/frontend_src/package-lock.json | JSON 配置文件 |
| app/frontend_src/package.json | JSON 配置文件 |
| app/frontend_src/postcss.config.js | export default { |
| app/frontend_src/src/components/AuthPages.jsx | 认证页面组件：LoginPage/RegisterPage/ResetPasswordPage + Field 复用表单组件。 |
| app/frontend_src/src/components/DashboardUI.jsx | import React, { useEffect, useRef, useState } from "react"; |
| app/frontend_src/src/components/FeedbackUI.jsx | import React, { useEffect, useRef } from "react"; |
| app/frontend_src/src/components/SettingsModal.jsx | import React, { useState } from "react"; |
| app/frontend_src/src/eventState.js | const PHASE_ORDER = { |
| app/frontend_src/src/i18n.js | export const MESSAGES = { |
| app/frontend_src/src/logDownload.js | export function downloadTextFile({ |
| app/frontend_src/src/logDownload.test.js | import assert from "node:assert/strict"; |
| app/frontend_src/src/main.jsx | import React, { useCallback, useEffect, useRef, useState } from "react"; |
| app/frontend_src/src/styles.css | @tailwind base; |
| app/frontend_src/src/workbenchFlow.js | const PLAN_REVIEW_STATUSES = new Set(["DRAFT", "VALIDATED", "WAITING_FOR_USER_REVIEW"]); |
| app/frontend_src/src/workbenchFlow.test.js | import assert from "node:assert/strict"; |
| app/frontend_src/tailwind.config.js | export default { |
| app/frontend_src/vite.config.js | import { defineConfig } from "vite"; |
| app/media_index.py | from __future__ import annotations |
| app/media_metadata.py | from __future__ import annotations |
| app/runtime_paths.py | from __future__ import annotations |
| app/steering.py | from __future__ import annotations |
| CLAUDE.md | Crayotter Claude Code Context |
| docs/8x4090_hgpo_worklog_2026-07-08.md | 8x4090 HGPO/RL Worklog - 2026-07-08 |
| docs/8x4090_hgpo_worklog_2026-07-15.md | Crayotter HGPO Worklog 2026-07-15 |
| docs/architecture_cn.md | Crayotter 业务流程、系统架构与模块化设计 |
| docs/material_source_plugins.md | Material Source Plugin Policy |
| docs/phase3-rl-4b-2026-06-17.md | Crayotter Phase 3 GRPO 4B 30-Step 训练调试记录 |
| docs/project-control/README.md | 项目控制表索引与维护约定 |
| docs/project-control/backend-apis.md | 后端接口表（HTTP 路由 + RuntimeManager 方法清单） |
| docs/project-control/directory-tree.md | 目录树 |
| docs/project-control/frontend-messages.md | 前端报文表（前端发起的全部 HTTP/SSE 请求） |
| docs/project-control/frontend-style.md | 前端风格表（设计 Token、组件清单、复用约定） |
| docs/project-control/server-survey.md | 服务器现状调研文档 |
| docs/project-control/test-accounts.md | 测试账号与管理员账号表（含明文密码，勿外传） |
| docs/worklogs/component-worklog.md | 组件级别 worklog（第一版上线各组件状态/决策/阻塞） |
| docs/reading_log.md | Crayotter-main 项目阅读笔记 |
| docs/real_platform_smoke_tests.md | Real Platform Download Smoke Tests |
| docs/rl_guide.md | Crayotter Phase 3 RL 训练指南 |
| docs/wsl_setup/deploy_to_server.ps1 | PowerShell 脚本 |
| docs/wsl_setup/finish_wsl_setup.ps1 | PowerShell 脚本 |
| docs/wsl_setup/install_wsl.ps1 | PowerShell 脚本 |
| docs/wsl_setup/run_rl_wsl.ps1 | PowerShell 脚本 |
| docs/wsl_setup/setup_wsl_cuda.sh | Shell 脚本 |
| export_train_test.py | Export train and test fixture datasets separately for verl. |
| LICENSE | Required Notice: Copyright 2026 Crayotter Contributors. |
| migrations/001_initial_schema.sql | 初始迁移：tenants/users/sessions/recovery_codes/jobs/uploads/artifacts/audit_logs 8 表 + RLS + updated_at 触发器。 |
| migrations/002_add_user_role.sql | 迁移 002：users 表新增 role 字段（user/admin）。 |
| packaging/build_windows.ps1 | PowerShell 脚本 |
| packaging/crayotter.spec | from pathlib import Path |
| packaging/prepare_windows_assets.py | from __future__ import annotations |
| packaging/README_WINDOWS_CN.txt | Crayotter Windows 使用说明 |
| packaging/windows_version_info.txt | VSVersionInfo( |
| phase3_rl/__init__.py | Phase 3 RL rollout scaffolding for Crayotter. |
| phase3_rl/build_fixtures_from_eval.py | Build Crayotter Phase 3 fixtures from the cloned ModelScope eval dataset. |
| phase3_rl/dryrun4_analysis_report.md | Crayotter Phase 3 RL dry-run4 训练分析报告 |
| phase3_rl/dryrun5_analysis_report.md | Crayotter Phase 3 RL dry-run5 奖励分析与训练总结 |
| phase3_rl/eval_expert_trace.py | Crayotter Phase3 RL eval 专家轨迹转换器。 |
| phase3_rl/export_verl_phase3_dataset.py | from __future__ import annotations |
| phase3_rl/fixture.py | from __future__ import annotations |
| phase3_rl/generate_verl_tool_config.py | from __future__ import annotations |
| phase3_rl/judge_client.py | 你是一位严格的视频剪辑质量评估专家。请根据以下信息，对 AI 剪辑智能体本次执行的轨迹和结果打分。 |
| phase3_rl/judge_subagent.py | Local subagent judge for Kimi (or any judge that needs agent-based proxy). |
| phase3_rl/local_env.py | from __future__ import annotations |
| phase3_rl/policies.py | Parse Qwen-style XML tool calls out of assistant content. |
| phase3_rl/probe_models.py | Quick probe for Qwen and Kimi model names via OpenAI-compatible endpoints. |
| phase3_rl/prompt_builder.py | from __future__ import annotations |
| phase3_rl/README.md | Crayotter Phase 3 RL Pipeline |
| phase3_rl/README_CN.md | Crayotter Phase 3 RL Pipeline |
| phase3_rl/rebuild_fixtures_with_real_materials.py | Rebuild Crayotter Phase 3 fixtures with real raw materials from ModelScope. |
| phase3_rl/reward.py | Reward task phases that are actually completed, only for phases the fixture allows. |
| phase3_rl/reward_visualize.py | Visualize Crayotter Phase 3 RL reward logs. |
| phase3_rl/run_local_rollout.py | from __future__ import annotations |
| phase3_rl/run_on_server.py | Run a command on the A800 server via paramiko (avoids local PATH injection). |
| phase3_rl/run_verl_phase3_grpo.sh | Shell 脚本 |
| phase3_rl/ssh_server.py | Paramiko SSH helper for the Crayotter A800 training server. |
| phase3_rl/stage_curriculum.py | State-conditioned action curriculum for long-horizon editing rollouts. |
| phase3_rl/staged_agent_loop.py | HGPO agent loop with a state-conditioned executable tool set. |
| phase3_rl/start_train_100step.sh | Shell 脚本 |
| phase3_rl/test_judge_apis.py | Test all configured judge APIs for availability and score parsing. |
| phase3_rl/test_stage_curriculum.py | from __future__ import annotations |
| phase3_rl/tool_catalog.py | from __future__ import annotations |
| phase3_rl/tool_runner.py | from __future__ import annotations |
| phase3_rl/tool_runtime.py | from __future__ import annotations |
| phase3_rl/train_visualize.py | Visualize Crayotter Phase 3 RL training metrics. |
| phase3_rl/trajectory_guard.py | Deterministic trajectory guard and rollout-quality metrics. |
| phase3_rl/upload_to_server.py | Upload changed Phase 3 RL files to the A800 server via paramiko SFTP. |
| phase3_rl/validate_fixtures.py | Validate Crayotter Phase 3 fixtures before training/evaluation. |
| phase3_rl/verl_agent_loop.py | Compute average perplexity from response token log probabilities. |
| phase3_rl/verl_agent_loop.yaml | YAML 配置文件 |
| phase3_rl/verl_tools.py | Wrap a Crayotter Phase 3 tool as a verl native tool. |
| README.md | Crayotter |
| README_CN.md | Crayotter |
| requirements-browser.txt | -r requirements.txt |
| requirements-desktop.txt | pyinstaller==6.20.0 |
| requirements.txt | a i o h a p p y e y e b a l l s = = 2 . 6 . 1  |
| script/agent.py | 任务开始前清理 temp，避免历史脏文件干扰。 |
| script/dep/windows/ffmpeg.exe | MZ                @                                       	!L!This program cannot be run in DOS mode. |
| script/dep/windows/ffprobe.cmd | @echo off |
| script/dep/windows/yt-dlp.exe | MZ                @                                       	!L!This program cannot be run in DOS mode. |
| script/editing_plan.py | from __future__ import annotations |
| script/ffprobe_shim.py | from __future__ import annotations |
| script/graph.py | 多模态视频自动编辑 Agent — Planner + Deep Research + ReAct 混合架构 |
| script/media_consistency/__init__.py | Deterministic media-normalization primitives used by controlled editing. |
| script/media_consistency/probe.py | from __future__ import annotations |
| script/media_consistency/profile.py | Resolve the versioned canonical output contract for one task. |
| script/media_consistency/quality.py | Return read-only analysis commands used to populate final quality metrics. |
| script/media_consistency/render.py | Build, but do not execute, a canonical FFmpeg render command. |
| script/media_consistency/validation.py | from __future__ import annotations |
| script/memory_reference.py | from __future__ import annotations |
| script/model_runtime.py | from __future__ import annotations |
| script/orchestration/__init__.py | from .artifacts import ArtifactRegistry |
| script/orchestration/artifacts.py | from __future__ import annotations |
| script/orchestration/budget.py | Persistable best-effort budget shared by all three graph phases. |
| script/orchestration/models.py | from __future__ import annotations |
| script/orchestration/scheduler.py | from __future__ import annotations |
| script/phases/__init__.py | Business phase policies used by the main LangGraph workflow. |
| script/phases/editing_execution/__init__.py | from .models import ( |
| script/phases/editing_execution/models.py | Structured contracts for controlled and short-form editing. |
| script/phases/editing_execution/policy.py | Bounded fallback policy for Phase 3 execution loops. |
| script/phases/editing_research/__init__.py | from .tasks import build_research_execution_plan, select_research_mode |
| script/phases/editing_research/tasks.py | Phase 2 mode selection and deterministic research DAG construction. |
| script/phases/material_preparation/__init__.py | from .gap_policy import deterministic_material_sufficient, normalize_gap_report |
| script/phases/material_preparation/gap_policy.py | Deterministic guardrails around the LLM material-gap evaluator. |
| script/phases/material_preparation/planning.py | Pure Phase 1 planning and normalization policies. |
| script/run_agent_worker.py | from __future__ import annotations |
| script/run_backend.py | from __future__ import annotations |
| script/run_desktop.py | from __future__ import annotations |
| script/runtime/__init__.py | Runtime configuration and dependency context for workflow execution. |
| script/runtime/context.py | Dependency context injected into the workflow compatibility layer. |
| script/runtime/settings.py | Validated runtime settings shared by entrypoints and workflow nodes. |
| script/tools/__init__.py | from __future__ import annotations |
| script/tools/_shared.py | 多模态视频自动编辑 Agent — 工具集 |
| script/tools/add_narration.py | 为视频添加 AI 生成的 TTS 旁白配音，原视频背景音量会自动降至 20%。 |
| script/tools/add_narration_segments.py | 解析可用字幕字体路径，优先使用用户传入字体。 |
| script/tools/add_subtitles.py | 为视频添加字幕（不含配音），字幕按时间段显示在画面底部。 |
| script/tools/add_transition.py | 返回专业转场预设列表、默认时长与分类建议。 |
| script/tools/analyze_video.py | 使用多模态 AI 深度分析视频内容，输出全片时间线分析和推荐剪辑方案。 |
| script/tools/audio_post_tools.py | 按旁白时间段压低背景音，提升人声可懂度。 |
| script/tools/batch_cut_video.py | 根据 analyze_video 生成的分析JSON，从源视频批量裁剪所有推荐时间段。 |
| script/tools/browser_auth.py | An authorization request without any credential material. |
| script/tools/build_timeline_plan.py | 根据片段列表生成专业时间线规划（参考 CapCut timelines/audio_timelines 思路）。 |
| script/tools/continuity_tools.py | 对相邻两段视频做切点连续性评分（0-100）。 |
| script/tools/cut_video.py | 从视频中裁剪指定时间段的片段。 |
| script/tools/download_bilibili_video.py | Use Bilibili's public play API when webpage extraction is blocked. |
| script/tools/download_material_video.py | 下载任意支持平台素材，并清洗为剪辑兼容的 MP4/H.264/AAC。 |
| script/tools/download_youtobe_video.py | 从 YouTube 下载视频到工作目录。 |
| script/tools/export_video.py | 导出最终成品视频，可指定输出分辨率并重新编码为高质量 H.264。 |
| script/tools/generate_video.py | 兼容导出工具别名（generate_video -> export_video）。 |
| script/tools/import_material_urls.py | 将用户提供的素材 URL/分享链接导入候选池，适用于抖音、小红书、快手、B站、YouTube 等平台。 |
| script/tools/inspect_video_duration.py | 检测视频时长、分辨率、帧率等基本信息。 |
| script/tools/material_source_policy.py | Return whether a declared material-source capability is policy-allowed. |
| script/tools/material_sources.py | from __future__ import annotations |
| script/tools/merge_videos.py | 将多个视频片段按顺序合并为一个视频。 |
| script/tools/narration_pipeline.py | Mix already generated narration audio artifacts onto a video. |
| script/tools/plan_narration_segments.py | 根据视频分析结果自动生成“音画同步”的分段旁白规划。 |
| script/tools/plan_transition_for_clips.py | 为片段序列生成逐边界转场方案（专业化转场规划）。 |
| script/tools/rank_video_candidates.py | 使用 AI 模型对候选视频进行评分排序，选出最适合剪辑的 Top K 个视频。 |
| script/tools/rebuild_local_dataset.py | 重构本地分析数据集，生成统一的可检索编辑数据文件。 |
| script/tools/recall_semantic_segments.py | 基于 analyze_video 产出的分析JSON，按语义召回最匹配的视频片段。 |
| script/tools/search_bilibili_video.py | 在 Bilibili 上搜索视频资源，返回视频标题、BV号、时长、播放量等信息。 |
| script/tools/search_material_sources.py | Bridge the private browser broker to the adapter's in-memory protocol. |
| script/tools/search_youtobe_video.py | 在 YouTube 上搜索视频资源，返回视频标题、URL、时长列表。 |
| script/tools/source_adapters/__init__.py | Policy-checked material source adapters. |
| script/tools/source_adapters/base.py | Supplies ephemeral Playwright storage state; implementations own cleanup. |
| script/tools/source_adapters/bilibili.py | from __future__ import annotations |
| script/tools/source_adapters/browser.py | Render a public page without making Playwright a runtime requirement. |
| script/tools/source_adapters/crawler.py | HTTP downloader carrying the detail page's ephemeral session context. |
| script/tools/source_adapters/models.py | from __future__ import annotations |
| script/tools/source_adapters/normalization.py | Emit the stable candidate fields consumed by the existing ranking path. |
| script/tools/source_adapters/registry.py | from __future__ import annotations |
| script/tools/source_adapters/youtube.py | Short, task-cacheable reachability probe for conditional YouTube use. |
| script/tools/timeline_tools.py | 根据片段列表构建可训练的结构化时间线。 |
| script/tools/validate_narration_timeline.py | 校验分段旁白时间线，提前发现音画不同步风险。 |
| script/visualize.py | Crayotter Agent 行为追踪可视化 |
| script/workflow/__init__.py | Stable workflow contracts shared by the Crayotter graph. |
| script/workflow/loops.py | Reusable bounded-loop policy for future workflow-level iteration. |
| script/workflow/skills.py | Composable tool-skill groups built on top of the authoritative tool catalog. |
| script/workflow/state.py | State contracts for the three-phase editing workflow. |
| script/workflow/tool_catalog.py | Tool groups exposed to each workflow phase. |
| script/workflow/topology.py | Declarative LangGraph topology for the Crayotter workflow. |
| tests/test_analyze_video_retry.py | import importlib |
| tests/test_auth_api.py | 认证 API 功能/冲突测试：register/login/me/password/logout/reset 15 项（需运行中的后端 + PostgreSQL）。 |
| tests/test_backend_logs.py | from __future__ import annotations |
| tests/test_browser_auth.py | from __future__ import annotations |
| tests/test_editing_plan.py | from __future__ import annotations |
| tests/test_material_source_policy.py | from __future__ import annotations |
| tests/test_material_sources.py | [Parsed_loudnorm_0 @ 000001] |
| tests/test_media_consistency.py | from __future__ import annotations |
| tests/test_modular_architecture.py | from __future__ import annotations |
| tests/test_orchestration.py | from __future__ import annotations |
| tests/test_phase1_material_sources.py | from __future__ import annotations |
| tests/test_phase3_execution_guards.py | from __future__ import annotations |
| tests/test_processing_budget.py | from __future__ import annotations |
| tests/test_real_platform_download_smoke.py | from __future__ import annotations |
| tests/test_release_packaging.py | from __future__ import annotations |
| tests/test_runtime_config_defaults.py | from __future__ import annotations |
| tests/test_source_adapters.py | from __future__ import annotations |
| tests/test_steering.py | from __future__ import annotations |
| tests/test_windows_subprocess.py | from __future__ import annotations |
| tests/test_workflow_modules.py | from __future__ import annotations |
| tools/build_hgpo_reference_archive.py | Create a provenance-preserving, secret-redacted local HGPO code archive. |
| tools/create_test_accounts.py | 生成 10 个测试账号 + 1 个管理员账号（随机强密码），输出 docs/project-control/test-accounts.md。 |
| website/index.html | <!DOCTYPE html> |
| website/paper/assets/paper.css | .paper-hero { |
| website/paper/index.html | <!DOCTYPE html> |
| website/README.txt | Crayotter Website |
| website/script.js | (() => { |
| website/styles.css | :root { |