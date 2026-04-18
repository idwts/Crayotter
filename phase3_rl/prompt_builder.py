from __future__ import annotations

import json
from pathlib import Path

from script import graph as graph_module


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def _workspace_snapshot(root: Path, relative_dir: str, max_files: int = 30) -> str:
    base = root / relative_dir
    if not base.exists():
        return f"({relative_dir} 不存在)"
    files = [item for item in base.rglob("*") if item.is_file()]
    if not files:
        return f"({relative_dir} 为空)"
    files = sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)[:max_files]
    rows: list[str] = []
    for item in files:
        rel = item.relative_to(base)
        size_mb = item.stat().st_size / (1024 * 1024)
        rows.append(f"- {rel} ({size_mb:.1f}MB)")
    return "\n".join(rows)


def _analysis_context(root: Path, max_files: int = 12, max_chars_per_file: int = 4000) -> str:
    candidates = sorted(
        (list((root / "temp").glob("*_analysis.json")) + list((root / "user_temp").glob("*_analysis.json"))),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return "(无分析数据)"

    blocks: list[str] = []
    for item in candidates[:max_files]:
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
        except Exception:
            continue
        source_video = str(payload.get("source_video", "")) or item.stem.replace("_analysis", "")
        analysis_text = str(payload.get("analysis_text", "")).strip()
        segments = payload.get("segments", [])
        segment_preview: list[str] = []
        if isinstance(segments, list):
            for seg in segments[:8]:
                if not isinstance(seg, dict):
                    continue
                try:
                    start = float(seg.get("start", 0.0))
                    end = float(seg.get("end", 0.0))
                except Exception:
                    continue
                semantic = str(seg.get("semantic_text", "") or seg.get("description", "")).strip()
                preview = f"t={start:.1f}s-{end:.1f}s"
                if semantic:
                    preview += f": {semantic[:120]}"
                segment_preview.append(preview)
        block = [f"### 源视频: {source_video}", f"- 分析文件: {item.name}"]
        if segment_preview:
            block.append("- 片段摘要:")
            block.extend([f"  {row}" for row in segment_preview])
        if analysis_text:
            block.append(f"- 分析正文:\n{analysis_text[:max_chars_per_file]}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks) if blocks else "(无分析数据)"


def build_phase3_messages(
    *,
    user_request: str,
    target_duration_seconds: float,
    editing_blueprint: str,
    runtime_root: str | Path,
    tool_names: list[str],
) -> list[dict[str, str]]:
    root = Path(runtime_root).resolve()
    system_prompt = graph_module.REACT_EDITOR_PROMPT.format(
        workspace=root / "temp",
        user_workspace=root / "user_temp",
        memory_experience=root / "memory_experience",
    )

    memory_text = _read_text(root / "memory_experience" / "latest_skills.md", "(无历史经验)")
    user_parts = [
        f"## 用户需求\n{user_request}",
        f"\n## 目标时长\n{target_duration_seconds:.1f} 秒",
    ]
    if editing_blueprint.strip():
        user_parts.append(f"\n## 剪辑蓝图\n{editing_blueprint.strip()}")
    user_parts.extend(
        [
            f"\n## 已有素材分析数据\n{_analysis_context(root)}",
            f"\n## 当前工作目录文件\n{_workspace_snapshot(root, 'temp')}",
            f"\n## 用户素材目录文件\n{_workspace_snapshot(root, 'user_temp')}",
            f"\n## 历史案例经验（仅供参考）\n{memory_text[:8000]}",
            f"\n## 本轮允许调用的工具\n{', '.join(tool_names)}",
            "\n## 开始执行\n"
            "请先给出简短创作思路，然后再基于现有分析数据进行工具调用。"
            "如果你认为当前素材已经足够，就直接执行剪辑；不要联网搜索。",
        ]
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
