#!/usr/bin/env python3
"""
Crayotter Agent 行为追踪可视化
Manus-style agent behavior tracking visualization.

用法:
    python script\\visualize.py                            # 默认使用 logs/ 下最新日志
    python script\\visualize.py logs\\video_agent_xxx.log   # 指定日志文件
    python script\\visualize.py --port 8080                # 指定端口
"""

import re
import json
import sys
import os
import glob
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from urllib.parse import urlparse, parse_qs

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─── 日志解析 ───────────────────────────────────────────────────────────────────

LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"  # timestamp
    r" - (\w[\w._]*)"                                    # logger name
    r" - (DEBUG|INFO|WARNING|ERROR|CRITICAL)"            # level
    r" - (.*)$"                                           # message
)

# 用于处理“同一物理行拼接多条日志记录”的新结构。
_INLINE_LOG_SPLIT_RE = re.compile(
  r"(?<!\n)(?=(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (?:\w[\w._]*) - (?:DEBUG|INFO|WARNING|ERROR|CRITICAL) - )"
)

# 事件匹配模式
PATTERNS = {
    "task_start":       re.compile(r"📋 新任务开始: (.+)"),
    "task_end":         re.compile(r"⏱\s*任务完成，总耗时: ([\d.]+)s"),
    "cleanup_pre":      re.compile(r"🧹 任务前已清理 temp 文件: (\d+)"),
    "cleanup_post":     re.compile(r"🧹 任务后已清理中间文件: (\d+)"),
    "kept_files":       re.compile(r"📌 任务后保留文件: (.+)"),

    # Phase 1
    "phase1_start":     re.compile(r"(?:🎯\s*)?Phase\s*1\s*[—-]\s*Planner\s*开始规划"),
    "plan_summary":     re.compile(r"📋 素材准备计划 \((\d+) 步\): (.+)"),
    "plan_step":        re.compile(r"\[(\d+)\] (.+?) → (\w+)"),
    "target_duration":  re.compile(r"⏱ 目标时长: ([\d.]+)s"),
    "executor_step":    re.compile(r"🔧 Executor 步骤 \[(\d+)\]: (.+)"),
    "step_complete":    re.compile(r"✅ 步骤 \[(\d+)\] 完成"),
    "tool_whitelist":   re.compile(r"🧰 步骤 \[(\d+)\] 工具白名单: (.+)"),
    "prep_router":      re.compile(r"📌 Prep Router: 步骤 (\d+)/(\d+)"),
    "download_det":     re.compile(r"📥 下载步骤走确定性路径: top_k=(\d+)"),
    "video_filter":     re.compile(r"🧠 源视频过滤: 待分析=(\d+), 已分析=(\d+)"),
    "phase1_done":      re.compile(r"✅ Phase 1 完成: (.+)"),

    # Phase 2
    "phase15_start":    re.compile(r"(?:🔬\s*)?═+\s*Phase\s*2\s*开始[:：]\s*深度剪辑研究"),
    "blueprint_ctx":    re.compile(r"📝 分析上下文长度: (\d+) 字"),
    "blueprint_prompt": re.compile(r"📝 总提示长度: (\d+) 字"),
    "blueprint_done":   re.compile(r"🔬 剪辑蓝图生成完成 \((\d+) 字\)"),
    "blueprint_summary":re.compile(r"📝 蓝图摘要: (.+)"),

    # Phase 3 (ReAct Editor)
    "phase2_start":     re.compile(r"(?:🎬\s*)?═+\s*Phase\s*3\s*开始[:：]\s*ReAct Editor"),
    "react_tools":      re.compile(r"🧰 ReAct Editor 工具集: (.+)"),
    "phase2_done":      re.compile(r"(?:🎬\s*)?═+\s*Phase\s*3\s*完成"),
    "final_output":     re.compile(r"📝 最终输出"),

    # Tool events
    "llm_call":         re.compile(r"🔍 _get_llm\(\) model=(.+)"),
    "search_start":     re.compile(r"🔍 开始搜索Bilibili视频: query='(.+?)'"),
    "search_stats":     re.compile(r"📊 Bilibili搜索统计: (.+)"),
    "rank_start":       re.compile(r"🤖 MLLM筛选开始: (.+)"),
    "rank_done":        re.compile(r"✅ MLLM筛选完成: (.+)"),
    "download_start":   re.compile(r"📥 开始下载Bilibili视频: url='(.+?)', filename='(.+?)'"),
    "analysis_ts":      re.compile(r"🕒 已生成时间戳分析视频: (.+)"),
    "analysis_native":  re.compile(r"多模态视频内容分析\(原生\): (.+)"),
    "analysis_done":    re.compile(r"视频分析完成（(?:原生多模态|视觉|视觉\+音频)?）"),
    "batch_cut":        re.compile(r"✂️ 批量剪辑完成: 源=(.+?), 片段=(\d+), 总时长=([\d.]+)s"),
    "segment_merge":    re.compile(r"🔗 片段合并: (\d+) 个原始片段 → (\d+) 个合并片段"),
    "duration_check":   re.compile(r"📏 时长检测: (.+?) -> ([\d.]+)s"),
    "tts_success":      re.compile(r"TTS 生成成功: (.+?) \((\d+) chars\) -> (.+)"),
    "subtitle_fail":    re.compile(r"段 (\d+) 字幕创建失败: (.+)"),
    "subtitle_layout":  re.compile(r"字幕布局: 段=?(\d+), font=([^,]+), clip_h=(\d+), y=(\d+), video_h=(\d+)"),
    "phase_tool_call":  re.compile(r"🛠️\s*Phase\s*(\d+)\s*工具调用:\s*([a-zA-Z_][\w]*)\s*args=(.+)"),
    "phase_tool_result":re.compile(r"📦\s*Phase\s*(\d+)\s*工具结果:\s*([a-zA-Z_][\w]*)\s*->\s*(.+)"),
    "export":           re.compile(r"🎬 视频已导出"),
}


def parse_timestamp(ts_str: str) -> datetime:
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")


def parse_log(filepath: str) -> dict:
    """解析日志文件，提取结构化行为轨迹"""
    events = []
    task_info = {}
    phases = []
    current_phase = None
    current_step = None
    plan_steps = []

    with open(filepath, "r", encoding="utf-8") as f:
      raw_text = f.read()

    # 新日志里可能出现“上一条 message 后直接拼接下一条完整日志”的情况。
    normalized_text = _INLINE_LOG_SPLIT_RE.sub("\n", raw_text)
    lines = normalized_text.splitlines()

    all_timestamps = []

    for line in lines:
        line = line.rstrip("\n")
        m = LOG_RE.match(line)
        if not m:
            continue
        ts_str, logger, level, message = m.groups()
        ts = parse_timestamp(ts_str)
        all_timestamps.append(ts)

        # 只关注 agent / graph / tools 系列 logger（如 tools._shared）
        if not (logger == "agent" or logger == "graph" or logger.startswith("tools")):
            continue
        if level == "DEBUG":
            continue

        event = {
            "timestamp": ts_str,
            "ts_ms": int(ts.timestamp() * 1000),
            "logger": logger,
            "level": level,
            "message": message,
        }

        # 匹配事件类型
        for etype, pattern in PATTERNS.items():
            em = pattern.search(message)
            if em:
                event["type"] = etype
                event["groups"] = em.groups()
                break
        else:
            event["type"] = "info"
            event["groups"] = []

        # 丰富事件信息
        _enrich_event(event, task_info, phases, plan_steps)
        events.append(event)

    # 后处理：计算阶段耗时
    _compute_durations(events, phases)

    return {
        "file": os.path.basename(filepath),
        "total_lines": len(lines),
        "task": task_info,
        "phases": phases,
        "plan_steps": plan_steps,
        "events": events,
        "start_time": all_timestamps[0].isoformat() if all_timestamps else None,
        "end_time": all_timestamps[-1].isoformat() if all_timestamps else None,
    }


def _enrich_event(event, task_info, phases, plan_steps):
    """根据事件类型丰富数据"""
    etype = event["type"]
    groups = event["groups"]

    if etype == "task_start":
        task_info["description"] = groups[0]
    elif etype == "task_end":
        task_info["total_seconds"] = float(groups[0])
    elif etype == "kept_files":
        task_info["output_file"] = groups[0]
    elif etype == "plan_summary":
        task_info["plan_count"] = int(groups[0])
        task_info["plan_desc"] = groups[1]
    elif etype == "plan_step":
        plan_steps.append({
            "index": int(groups[0]),
            "description": groups[1],
            "tool": groups[2],
        })
    elif etype == "target_duration":
        task_info["target_duration"] = float(groups[0])
    elif etype in ("phase1_start", "phase15_start", "phase2_start"):
        phase_map = {
            "phase1_start": {"id": "phase1", "name": "Phase 1: 素材准备", "icon": "🎯"},
            "phase15_start": {"id": "phase15", "name": "Phase 2: 深度剪辑研究", "icon": "🔬"},
            "phase2_start": {"id": "phase2", "name": "Phase 3: ReAct Editor", "icon": "🎬"},
        }
        p = phase_map[etype].copy()
        p["start_ts"] = event["timestamp"]
        p["events"] = []
        phases.append(p)
    elif phases:
        phases[-1]["events"].append(event)


def _compute_durations(events, phases):
    """计算每个阶段的耗时"""
    for i, phase in enumerate(phases):
        start_ts = parse_timestamp(phase["start_ts"])
        if i + 1 < len(phases):
            end_ts = parse_timestamp(phases[i + 1]["start_ts"])
        elif events:
            end_ts = parse_timestamp(events[-1]["timestamp"])
        else:
            end_ts = start_ts
        phase["duration_s"] = round((end_ts - start_ts).total_seconds(), 1)
        # 统计子事件数量
        phase["tool_calls"] = sum(
            1 for e in phase["events"]
            if e["type"] in (
                "search_start", "rank_start", "download_start",
                "analysis_native", "batch_cut", "tts_success",
            "duration_check", "segment_merge",
            "phase_tool_call", "phase_tool_result", "subtitle_layout",
            )
        )
        phase["warnings"] = sum(1 for e in phase["events"] if e["level"] == "WARNING")
        phase["errors"] = sum(1 for e in phase["events"] if e["level"] == "ERROR")
        # 去掉嵌套events 以减少JSON体积 (前端用全局events)
        del phase["events"]


# ─── HTML 模板 ──────────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crayotter Agent Trace</title>
<style>
:root {
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #21262d;
  --bg-card: #1c2128;
  --border: #30363d;
  --text-primary: #e6edf3;
  --text-secondary: #8b949e;
  --text-muted: #6e7681;
  --accent-blue: #58a6ff;
  --accent-green: #3fb950;
  --accent-yellow: #d29922;
  --accent-red: #f85149;
  --accent-purple: #bc8cff;
  --accent-cyan: #39d2c0;
  --phase1-color: #58a6ff;
  --phase15-color: #bc8cff;
  --phase2-color: #3fb950;
  --glow-blue: rgba(88, 166, 255, 0.15);
  --glow-green: rgba(63, 185, 80, 0.15);
  --glow-purple: rgba(188, 140, 255, 0.15);
  --radius: 12px;
  --radius-sm: 8px;
  --shadow: 0 4px 24px rgba(0,0,0,0.3);
}
body.theme-light {
  --bg-primary: #f6f8fc;
  --bg-secondary: #ffffff;
  --bg-tertiary: #eef3fb;
  --bg-card: #ffffff;
  --border: #d1dced;
  --text-primary: #1e2b3f;
  --text-secondary: #566784;
  --text-muted: #6e7f9c;
  --accent-blue: #2f6bda;
  --accent-green: #208d5b;
  --accent-yellow: #9f6f05;
  --accent-red: #d74444;
  --accent-purple: #7b56d8;
  --accent-cyan: #1f9ea5;
  --phase1-color: #2f6bda;
  --phase15-color: #7b56d8;
  --phase2-color: #208d5b;
  --glow-blue: rgba(47, 107, 218, 0.14);
  --glow-green: rgba(32, 141, 91, 0.14);
  --glow-purple: rgba(123, 86, 216, 0.14);
  --shadow: 0 8px 24px rgba(35, 58, 95, 0.12);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.6;
  overflow: hidden;
  height: 100vh;
  transition: background 0.2s ease, color 0.2s ease;
}

/* ── Layout ── */
.app-container {
  display: grid;
  grid-template-columns: 320px 1fr;
  grid-template-rows: 64px 1fr;
  height: 100vh;
  gap: 0;
}

/* ── Header ── */
.header {
  grid-column: 1 / -1;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 24px;
  gap: 16px;
  z-index: 10;
}
.header-logo {
  font-size: 20px;
  font-weight: 700;
  background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: -0.5px;
}
.header-divider {
  width: 1px;
  height: 28px;
  background: var(--border);
}
.header-task {
  flex: 1;
  font-size: 14px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-stats {
  display: flex;
  gap: 16px;
  font-size: 13px;
}
.header-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}
.theme-toggle {
  border: 1px solid var(--border);
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border-radius: 999px;
  font-size: 12px;
  padding: 5px 10px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.theme-toggle:hover {
  border-color: var(--accent-blue);
}
.stat-item {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-secondary);
}
.stat-value {
  color: var(--text-primary);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

/* ── Sidebar ── */
.sidebar {
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding: 16px 0;
}
.sidebar::-webkit-scrollbar { width: 4px; }
.sidebar::-webkit-scrollbar-track { background: transparent; }
.sidebar::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

.phase-block {
  margin-bottom: 4px;
}
.phase-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  cursor: pointer;
  transition: background 0.15s;
  user-select: none;
}
.phase-header:hover { background: var(--bg-tertiary); }
.phase-header.active { background: var(--bg-tertiary); }
.phase-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.phase-dot.phase1 { background: var(--phase1-color); box-shadow: 0 0 8px var(--glow-blue); }
.phase-dot.phase15 { background: var(--phase15-color); box-shadow: 0 0 8px var(--glow-purple); }
.phase-dot.phase2 { background: var(--phase2-color); box-shadow: 0 0 8px var(--glow-green); }
.phase-label {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
}
.phase-duration {
  font-size: 11px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.phase-chevron {
  color: var(--text-muted);
  transition: transform 0.2s;
  font-size: 12px;
}
.phase-header.expanded .phase-chevron { transform: rotate(90deg); }

.phase-steps {
  display: none;
  padding: 2px 0 8px 0;
}
.phase-header.expanded + .phase-steps { display: block; }

.step-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 20px 6px 44px;
  cursor: pointer;
  transition: background 0.15s;
  font-size: 12px;
  color: var(--text-secondary);
  position: relative;
}
.step-item::before {
  content: '';
  position: absolute;
  left: 29px;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--border);
}
.step-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
.step-item.active { background: var(--bg-tertiary); color: var(--text-primary); }
.step-icon {
  font-size: 14px;
  flex-shrink: 0;
  line-height: 1.4;
}
.step-text {
  flex: 1;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.step-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: 600;
  flex-shrink: 0;
}
.badge-tool { background: rgba(88,166,255,0.15); color: var(--accent-blue); }
.badge-warn { background: rgba(210,153,34,0.15); color: var(--accent-yellow); }
.badge-error { background: rgba(248,81,73,0.15); color: var(--accent-red); }

/* ── Main Content ── */
.main {
  overflow-y: auto;
  padding: 24px;
  background: var(--bg-primary);
}
.main::-webkit-scrollbar { width: 6px; }
.main::-webkit-scrollbar-track { background: transparent; }
.main::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* ── Summary Cards ── */
.summary-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.summary-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.summary-label {
  font-size: 12px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.summary-value {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.summary-value.blue { color: var(--accent-blue); }
.summary-value.green { color: var(--accent-green); }
.summary-value.purple { color: var(--accent-purple); }
.summary-value.yellow { color: var(--accent-yellow); }

/* ── Timeline ── */
.timeline-section {
  margin-bottom: 32px;
}
.timeline-phase-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.timeline-phase-icon {
  font-size: 20px;
}
.timeline-phase-name {
  font-size: 16px;
  font-weight: 600;
}
.timeline-phase-time {
  font-size: 12px;
  color: var(--text-muted);
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

/* ── Event Cards ── */
.event-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.event-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 16px;
  display: grid;
  grid-template-columns: 90px 24px 1fr auto;
  align-items: center;
  gap: 10px;
  transition: border-color 0.15s, background 0.15s;
  cursor: default;
}
.event-card:hover {
  border-color: var(--accent-blue);
  background: rgba(88,166,255,0.04);
}
.event-card.warning {
  border-left: 3px solid var(--accent-yellow);
}
.event-card.error {
  border-left: 3px solid var(--accent-red);
}
.event-time {
  font-size: 11px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
}
.event-icon {
  font-size: 16px;
  text-align: center;
}
.event-msg {
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
  word-break: break-all;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.event-card.expanded .event-msg {
  white-space: normal;
}
.event-tag {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  flex-shrink: 0;
}
.tag-phase { background: rgba(88,166,255,0.12); color: var(--accent-blue); }
.tag-step { background: rgba(63,185,80,0.12); color: var(--accent-green); }
.tag-tool { background: rgba(188,140,255,0.12); color: var(--accent-purple); }
.tag-llm { background: rgba(57,210,192,0.12); color: var(--accent-cyan); }
.tag-warn { background: rgba(210,153,34,0.12); color: var(--accent-yellow); }
.tag-ok { background: rgba(63,185,80,0.12); color: var(--accent-green); }
.tag-info { background: rgba(139,148,158,0.12); color: var(--text-secondary); }

/* ── Phase Progress Bar ── */
.progress-bar-container {
  margin-bottom: 24px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
}
.progress-bar-label {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 8px;
  display: flex;
  justify-content: space-between;
}
.progress-bar {
  height: 8px;
  border-radius: 4px;
  background: var(--bg-tertiary);
  overflow: hidden;
  display: flex;
}
.progress-segment {
  height: 100%;
  transition: width 0.5s ease;
}
.progress-segment.phase1 { background: var(--phase1-color); }
.progress-segment.phase15 { background: var(--phase15-color); }
.progress-segment.phase2 { background: var(--phase2-color); }
.progress-legend {
  display: flex;
  gap: 16px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-secondary);
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 5px;
}
.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

/* ── Filter Bar ── */
.filter-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.filter-btn {
  padding: 4px 12px;
  border-radius: 16px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.filter-btn:hover { border-color: var(--accent-blue); color: var(--text-primary); }
.filter-btn.active { background: var(--accent-blue); color: #fff; border-color: var(--accent-blue); }

/* ── Animations ── */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
.event-card { animation: fadeIn 0.25s ease forwards; }

/* ── Responsive ── */
@media (max-width: 900px) {
  .app-container { grid-template-columns: 1fr; }
  .sidebar { display: none; }
}
</style>
</head>
<body>
<div class="app-container">
  <!-- Header -->
  <div class="header">
    <div class="header-logo">Crayotter Trace</div>
    <div class="header-divider"></div>
    <div class="header-task" id="headerTask">Loading...</div>
    <div class="header-controls">
      <button id="themeToggle" class="theme-toggle" type="button">☀️ 浅色</button>
      <div class="header-stats" id="headerStats"></div>
    </div>
  </div>

  <!-- Sidebar -->
  <div class="sidebar" id="sidebar"></div>

  <!-- Main -->
  <div class="main" id="main">
    <div id="summaryRow" class="summary-row"></div>
    <div id="progressBar"></div>
    <div id="filterBar" class="filter-bar"></div>
    <div id="timeline"></div>
  </div>
</div>

<script>
const DATA = __DATA_PLACEHOLDER__;

// ─── Helpers ─────────────────────────────────────────────────────────────
function fmtTime(ts) {
  return ts ? ts.split(' ')[1] || ts.split(',')[0] : '';
}
function fmtShortTime(ts) {
  const t = fmtTime(ts);
  return t ? t.substring(0, 8) : '';
}
function fmtDuration(s) {
  if (s < 60) return s.toFixed(1) + 's';
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m + 'm ' + sec + 's';
}
function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// 事件分类
function getEventCategory(e) {
  const t = e.type;
  if (e.level === 'WARNING') return 'warn';
  if (e.level === 'ERROR') return 'error';
  if (['phase1_start','phase15_start','phase2_start','phase1_done','phase2_done'].includes(t)) return 'phase';
  if (['executor_step','step_complete','plan_summary','plan_step','prep_router','download_det','video_filter'].includes(t)) return 'step';
  if (['search_start','search_stats','rank_start','rank_done','download_start','batch_cut',
       'analysis_ts','analysis_native','analysis_done','segment_merge','duration_check',
       'tts_success','subtitle_fail','subtitle_layout','phase_tool_call','phase_tool_result','export'].includes(t)) return 'tool';
  if (t === 'llm_call') return 'llm';
  return 'info';
}

function getEventIcon(e) {
  const icons = {
    task_start: '📋', task_end: '⏱️', cleanup_pre: '🧹', cleanup_post: '🧹', kept_files: '📌',
    phase1_start: '🎯', phase15_start: '🔬', phase2_start: '🎬',
    plan_summary: '📋', plan_step: '📝', target_duration: '⏱️',
    executor_step: '🔧', step_complete: '✅', tool_whitelist: '🧰',
    prep_router: '📌', download_det: '📥', video_filter: '🧠',
    phase1_done: '✅', phase2_done: '🎬', final_output: '📝',
    blueprint_ctx: '📝', blueprint_prompt: '📝', blueprint_done: '🔬', blueprint_summary: '📝',
    react_tools: '🧰',
    llm_call: '🤖', search_start: '🔍', search_stats: '📊',
    rank_start: '🤖', rank_done: '✅',
    download_start: '📥', analysis_ts: '🕒', analysis_native: '🎥',
    analysis_done: '✅', batch_cut: '✂️', segment_merge: '🔗',
    duration_check: '📏', tts_success: '🎙️', subtitle_fail: '⚠️', subtitle_layout: '🧱',
    phase_tool_call: '🛠️', phase_tool_result: '📦',
    export: '🎬', info: '💬',
  };
  return icons[e.type] || (e.level === 'WARNING' ? '⚠️' : '💬');
}

function getTagClass(category) {
  return {phase:'tag-phase',step:'tag-step',tool:'tag-tool',llm:'tag-llm',
          warn:'tag-warn',error:'tag-warn',info:'tag-info',ok:'tag-ok'}[category] || 'tag-info';
}

function getTagLabel(category) {
  return {phase:'PHASE',step:'STEP',tool:'TOOL',llm:'LLM',warn:'WARN',error:'ERROR',info:'INFO'}[category] || 'INFO';
}

// ─── Sidebar ─────────────────────────────────────────────────────────────
function buildSidebar() {
  const el = document.getElementById('sidebar');
  let html = '';

  // 任务概览
  html += '<div style="padding: 8px 20px 16px; border-bottom: 1px solid var(--border); margin-bottom: 8px;">';
  html += '<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;">TASK OVERVIEW</div>';
  html += '<div style="font-size:12px;color:var(--text-secondary);line-height:1.5;">';
  html += escapeHtml(DATA.task.description || 'N/A');
  html += '</div></div>';

  DATA.phases.forEach((phase, idx) => {
    const phaseClass = phase.id;
    html += '<div class="phase-block">';
    html += `<div class="phase-header expanded" data-phase="${phase.id}" onclick="togglePhase(this)">`;
    html += `<span class="phase-dot ${phaseClass}"></span>`;
    html += `<span class="phase-label">${escapeHtml(phase.name)}</span>`;
    html += `<span class="phase-duration">${fmtDuration(phase.duration_s)}</span>`;
    html += '<span class="phase-chevron">▸</span>';
    html += '</div>';
    html += '<div class="phase-steps">';

    // 获取该阶段的事件
    const phaseEvents = getPhaseEvents(phase.id);
    const keyEvents = phaseEvents.filter(e =>
      ['executor_step','step_complete','blueprint_done','search_start','download_start',
       'rank_start','rank_done','batch_cut','tts_success','duration_check',
       'analysis_done','phase1_done','phase2_done','subtitle_fail','subtitle_layout','segment_merge',
       'phase_tool_call','phase_tool_result'].includes(e.type)
    );

    keyEvents.forEach((e, i) => {
      const icon = getEventIcon(e);
      let text = e.message.substring(0, 60);
      let badge = '';
      if (e.level === 'WARNING') badge = '<span class="step-badge badge-warn">WARN</span>';
      html += `<div class="step-item" onclick="scrollToEvent('${e.ts_ms}')" title="${escapeHtml(e.message)}">`;
      html += `<span class="step-icon">${icon}</span>`;
      html += `<span class="step-text">${escapeHtml(text)}</span>`;
      html += badge;
      html += '</div>';
    });

    html += '</div></div>';
  });

  el.innerHTML = html;
}

function togglePhase(header) {
  header.classList.toggle('expanded');
}

function getPhaseEvents(phaseId) {
  const phaseIdx = DATA.phases.findIndex(p => p.id === phaseId);
  if (phaseIdx < 0) return [];

  const startTs = DATA.phases[phaseIdx].start_ts;
  const endTs = phaseIdx + 1 < DATA.phases.length ?
    DATA.phases[phaseIdx + 1].start_ts : null;

  return DATA.events.filter(e => {
    if (endTs) return e.timestamp >= startTs && e.timestamp < endTs;
    return e.timestamp >= startTs;
  });
}

function scrollToEvent(tsMs) {
  const card = document.querySelector(`[data-ts="${tsMs}"]`);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.style.borderColor = 'var(--accent-blue)';
    card.style.boxShadow = '0 0 16px rgba(88,166,255,0.2)';
    setTimeout(() => {
      card.style.borderColor = '';
      card.style.boxShadow = '';
    }, 2000);
  }
}

// ─── Header ──────────────────────────────────────────────────────────────
function buildHeader() {
  const taskEl = document.getElementById('headerTask');
  taskEl.textContent = DATA.task.description || 'N/A';
  taskEl.title = DATA.task.description || '';

  const statsEl = document.getElementById('headerStats');
  const total = DATA.task.total_seconds ? fmtDuration(DATA.task.total_seconds) : 'N/A';
  const eventCount = DATA.events.length;
  const toolCalls = DATA.events.filter(e =>
    ['search_start','download_start','rank_start','batch_cut','tts_success','analysis_native','segment_merge','duration_check','phase_tool_call','phase_tool_result','subtitle_layout'].includes(e.type)
  ).length;

  statsEl.innerHTML = `
    <div class="stat-item">⏱️ <span class="stat-value">${total}</span></div>
    <div class="stat-item">📊 <span class="stat-value">${eventCount}</span> events</div>
    <div class="stat-item">🔧 <span class="stat-value">${toolCalls}</span> tool calls</div>
  `;
}

// ─── Summary Cards ───────────────────────────────────────────────────────
function buildSummary() {
  const el = document.getElementById('summaryRow');
  const total = DATA.task.total_seconds || 0;
  const target = DATA.task.target_duration || 0;
  const phases = DATA.phases.length;
  const warns = DATA.events.filter(e => e.level === 'WARNING').length;

  el.innerHTML = `
    <div class="summary-card">
      <span class="summary-label">总耗时</span>
      <span class="summary-value blue">${fmtDuration(total)}</span>
    </div>
    <div class="summary-card">
      <span class="summary-label">目标时长</span>
      <span class="summary-value green">${target ? target + 's' : 'N/A'}</span>
    </div>
    <div class="summary-card">
      <span class="summary-label">流水线阶段</span>
      <span class="summary-value purple">${phases}</span>
    </div>
    <div class="summary-card">
      <span class="summary-label">警告</span>
      <span class="summary-value yellow">${warns}</span>
    </div>
  `;
}

// ─── Progress Bar ────────────────────────────────────────────────────────
function buildProgress() {
  const el = document.getElementById('progressBar');
  const total = DATA.task.total_seconds || 1;
  const segments = DATA.phases.map(p => {
    const pct = (p.duration_s / total * 100).toFixed(1);
    return { id: p.id, name: p.name, pct, duration: p.duration_s, icon: p.icon };
  });

  let barHtml = segments.map(s =>
    `<div class="progress-segment ${s.id}" style="width:${s.pct}%" title="${s.name}: ${fmtDuration(s.duration)}"></div>`
  ).join('');

  let legendHtml = segments.map(s =>
    `<div class="legend-item"><span class="legend-dot" style="background:var(--${s.id}-color)"></span>${s.icon} ${s.name} (${fmtDuration(s.duration)})</div>`
  ).join('');

  el.innerHTML = `
    <div class="progress-bar-container">
      <div class="progress-bar-label">
        <span>Pipeline 时间分布</span>
        <span>${fmtDuration(total)}</span>
      </div>
      <div class="progress-bar">${barHtml}</div>
      <div class="progress-legend">${legendHtml}</div>
    </div>
  `;
}

// ─── Filter Bar ──────────────────────────────────────────────────────────
let activeFilter = 'all';

function buildFilterBar() {
  const el = document.getElementById('filterBar');
  const filters = [
    { id: 'all', label: '全部' },
    { id: 'phase', label: '🎯 阶段' },
    { id: 'step', label: '🔧 步骤' },
    { id: 'tool', label: '🔨 工具' },
    { id: 'llm', label: '🤖 LLM' },
    { id: 'warn', label: '⚠️ 警告' },
  ];
  el.innerHTML = filters.map(f =>
    `<button class="filter-btn ${f.id === activeFilter ? 'active' : ''}" onclick="setFilter('${f.id}')">${f.label}</button>`
  ).join('');
}

function setFilter(f) {
  activeFilter = f;
  buildFilterBar();
  buildTimeline();
}

// ─── Timeline ────────────────────────────────────────────────────────────
function buildTimeline() {
  const el = document.getElementById('timeline');
  let html = '';

  DATA.phases.forEach((phase) => {
    const events = getPhaseEvents(phase.id);
    const filtered = activeFilter === 'all' ? events :
      events.filter(e => getEventCategory(e) === activeFilter);

    if (filtered.length === 0) return;

    html += '<div class="timeline-section">';
    html += `<div class="timeline-phase-title">`;
    html += `<span class="timeline-phase-icon">${phase.icon}</span>`;
    html += `<span class="timeline-phase-name">${escapeHtml(phase.name)}</span>`;
    html += `<span class="timeline-phase-time">${fmtDuration(phase.duration_s)} · ${phase.tool_calls} tool calls${phase.warnings ? ' · ' + phase.warnings + ' warnings' : ''}</span>`;
    html += '</div>';
    html += '<div class="event-list">';

    filtered.forEach((e, i) => {
      const cat = getEventCategory(e);
      const icon = getEventIcon(e);
      const tag = getTagLabel(cat);
      const tagCls = getTagClass(cat);
      const warnCls = e.level === 'WARNING' ? ' warning' : (e.level === 'ERROR' ? ' error' : '');

      html += `<div class="event-card${warnCls}" data-ts="${e.ts_ms}" onclick="this.classList.toggle('expanded')" style="animation-delay:${Math.min(i*20,500)}ms">`;
      html += `<span class="event-time">${fmtShortTime(e.timestamp)}</span>`;
      html += `<span class="event-icon">${icon}</span>`;
      html += `<span class="event-msg">${escapeHtml(e.message)}</span>`;
      html += `<span class="event-tag ${tagCls}">${tag}</span>`;
      html += '</div>';
    });

    html += '</div></div>';
  });

  el.innerHTML = html;
}

// ─── Init ────────────────────────────────────────────────────────────────
function init() {
  const themeStorageKey = 'crayotter-trace-theme';
  const themeToggle = document.getElementById('themeToggle');
  const applyTheme = (theme) => {
    const isLight = theme === 'light';
    document.body.classList.toggle('theme-light', isLight);
    if (themeToggle) themeToggle.textContent = isLight ? '🌙 切换深色' : '☀️ 切换浅色';
  };
  applyTheme(localStorage.getItem(themeStorageKey) || 'dark');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const next = document.body.classList.contains('theme-light') ? 'dark' : 'light';
      localStorage.setItem(themeStorageKey, next);
      applyTheme(next);
    });
  }

  buildHeader();
  buildSidebar();
  buildSummary();
  buildProgress();
  buildFilterBar();
  buildTimeline();
}

init();
</script>
</body>
</html>"""


# ─── HTTP Server ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    """简单的 HTTP 请求处理器"""

    html_content = ""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.html_content.encode("utf-8"))

    def log_message(self, fmt, *args):
        pass  # 静默日志


def find_latest_log(log_dir: str = "logs") -> str:
    """找到最新的 video_agent 日志文件"""
    candidates: list[str] = []

    if os.path.isabs(log_dir):
        candidates.append(log_dir)
    else:
        if log_dir == "logs":
            candidates.extend(
                [
                    os.path.join(PROJECT_ROOT, "runtime_logs"),
                    os.path.join(PROJECT_ROOT, "logs"),
                ]
            )
        else:
            candidates.append(os.path.join(PROJECT_ROOT, log_dir))

    for abs_log_dir in candidates:
        pattern = os.path.join(abs_log_dir, "video_agent_*.log")
        files = glob.glob(pattern)
        if files:
            return max(files, key=os.path.getmtime)

    print(f"❌ 未在这些目录中找到日志文件: {', '.join(candidates)}")
    sys.exit(1)


def main():
    port = 7860
    log_file = None

    # 解析命令行参数
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        else:
            candidate = args[i]
            log_file = (
                candidate
                if os.path.isabs(candidate)
                else os.path.join(PROJECT_ROOT, candidate)
            )
            i += 1

    if log_file is None:
        log_file = find_latest_log()

    if not os.path.exists(log_file):
        print(f"❌ 日志文件不存在: {log_file}")
        sys.exit(1)

    print(f"📂 解析日志: {log_file}")
    data = parse_log(log_file)
    print(f"✅ 提取 {len(data['events'])} 个事件, {len(data['phases'])} 个阶段")

    # 构建 HTML
    data_json = json.dumps(data, ensure_ascii=False, default=str)
    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", data_json)
    Handler.html_content = html

    # 同时保存一份静态 HTML
    out_html = os.path.splitext(log_file)[0] + "_trace.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"💾 静态 HTML 已保存: {out_html}")

    # 启动服务器
    server = HTTPServer(("0.0.0.0", port), Handler)
    url = f"http://localhost:{port}"
    print(f"🌐 服务已启动: {url}")
    print("   按 Ctrl+C 停止")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
