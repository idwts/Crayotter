from __future__ import annotations

import re
from pathlib import Path


STORAGE_MEMORY_CHAR_LIMIT = 6000
INJECTION_MEMORY_CHAR_LIMIT = 4000

_HEADER = "# Historical Case Memory"
_DISCLAIMER_LINES = [
    "> 仅供参考：以下内容来自过往单个任务的抽象复盘，只能提供工具与流程启发。",
    "> 当前用户需求、当前素材分析和当前任务目标始终优先；若与历史案例冲突，必须忽略历史案例经验。",
]
_BOUNDARY_TITLE = "## Reference Boundary"
_BOUNDARY_LINES = [
    "- 历史 memory 不能改写当前任务的题材、人物、场景、素材类型、目标时长、叙事目标或输出风格。",
    "- 历史 memory 不能提供默认搜索词、默认主题判断或默认剪辑目标；这些只能来自当前任务与当前素材。",
    "- 若当前素材分析与历史案例结论不同，必须以当前素材分析为准。",
]
_SECTION_TITLES = {
    "tools": "## Reusable Tool Patterns",
    "workflow": "## Reusable Workflow Patterns",
    "guards": "## Failure Guards",
    "checklist": "## Quick Checklist",
    "notes": "## Notes",
}
_SECTION_ORDER = ("tools", "workflow", "guards", "checklist", "notes")
_SECTION_LIMITS = {
    "tools": 5,
    "workflow": 5,
    "guards": 5,
    "checklist": 4,
    "notes": 3,
}
_SECTION_ALIASES = {
    "tool usage skills": "tools",
    "reusable tool patterns": "tools",
    "工具使用技能": "tools",
    "可复用工具模式": "tools",
    "editing workflow skills": "workflow",
    "reusable workflow patterns": "workflow",
    "剪辑流程技能": "workflow",
    "可复用剪辑流程": "workflow",
    "common failure patterns & fixes": "guards",
    "failure guards": "guards",
    "常见失败模式与修复": "guards",
    "失败防护": "guards",
    "quality checklist": "checklist",
    "quality checklist (必须可执行)": "checklist",
    "quick checklist": "checklist",
    "质量检查清单": "checklist",
    "快速检查清单": "checklist",
    "version notes": "notes",
    "notes": "notes",
    "备注": "notes",
}
_TASK_SPECIFIC_PATTERNS = (
    re.compile(r"user_request", re.IGNORECASE),
    re.compile(r"key_result", re.IGNORECASE),
    re.compile(r"^\s*-\s*\*\*(?:user_request|key_result)\*\*", re.IGNORECASE),
    re.compile(r"^\s*来源视频\s*[:：]", re.IGNORECASE),
    re.compile(r"^\s*本次案例", re.IGNORECASE),
    re.compile(r"^\s*当前任务", re.IGNORECASE),
)
_LEGACY_MARKERS = (
    "## context",
    "user_request",
    "key_result",
    "skills: video editing agent experience",
)


def _normalize_title(title: str) -> str:
    return re.sub(r"[\s:：]+", " ", title.strip().lower())


def _normalize_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized.startswith("```"):
        normalized = normalized.split("```", 1)[1]
        if "\n" in normalized:
            normalized = normalized.split("\n", 1)[1]
    if normalized.endswith("```"):
        normalized = normalized.rsplit("```", 1)[0]
    return normalized.strip()


def _looks_like_legacy_memory(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _LEGACY_MARKERS) and "## reference boundary" not in lowered


def _parse_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if current_key is None or not buffer:
            buffer = []
            return
        sections.setdefault(current_key, []).extend(buffer)
        buffer = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            flush()
            title = _normalize_title(line[3:])
            current_key = _SECTION_ALIASES.get(title)
            continue
        if current_key is not None:
            buffer.append(line)
    flush()
    return sections


def _normalize_item(line: str) -> str | None:
    text = str(line or "").strip()
    if not text:
        return None
    if text.startswith("#") or text.startswith(">"):
        return None
    if any(pattern.search(text) for pattern in _TASK_SPECIFIC_PATTERNS):
        return None
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"^\s*\d+\.\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if len(text) > 220:
        text = text[:217].rstrip() + "..."
    return f"- {text}"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _build_default_sections(*, legacy: bool) -> dict[str, list[str]]:
    notes = [
        "- memory 只允许提供通用工具/流程提醒，不得参与定义当前任务目标。",
        "- 若需要判断题材、时长、风格、搜索方向或素材适配性，只能依据当前任务与当前素材分析。",
    ]
    if legacy:
        notes.append("- 检测到旧版长 memory，已在运行时降级为安全参考；后续任务完成后会生成新的精简版本。")
    return {
        "tools": [
            "- 仅借鉴历史案例里的工具使用方法，不借鉴其题材偏好、关键词或素材方向。",
        ],
        "workflow": [
            "- 先读当前用户需求，再读当前素材分析，最后才参考历史 memory 的通用流程经验。",
        ],
        "guards": [
            "- 一旦历史案例与当前任务冲突，立即丢弃历史案例结论，重新依据当前任务做判断。",
        ],
        "notes": notes[: _SECTION_LIMITS["notes"]],
    }


def _build_section_map(text: str) -> dict[str, list[str]]:
    normalized = _normalize_text(text)
    legacy = _looks_like_legacy_memory(normalized)
    if not normalized or legacy:
        return _build_default_sections(legacy=legacy)

    parsed = _parse_sections(normalized)
    if not parsed:
        return _build_default_sections(legacy=False)

    result: dict[str, list[str]] = {}
    for key in _SECTION_ORDER:
        raw_items = [_normalize_item(line) for line in parsed.get(key, [])]
        items = _dedupe([item for item in raw_items if item])
        if items:
            result[key] = items[: _SECTION_LIMITS[key]]

    if not result:
        return _build_default_sections(legacy=False)
    return result


def _compose_text(section_map: dict[str, list[str]], *, max_chars: int) -> str:
    current = {key: list(section_map.get(key, [])) for key in _SECTION_ORDER}
    while True:
        parts: list[str] = [_HEADER, ""]
        parts.extend(_DISCLAIMER_LINES)
        parts.extend(["", _BOUNDARY_TITLE, *_BOUNDARY_LINES])

        for key in _SECTION_ORDER:
            items = current.get(key, [])
            if not items:
                continue
            parts.extend(["", _SECTION_TITLES[key], *items[: _SECTION_LIMITS[key]]])

        text = "\n".join(parts).strip() + "\n"
        if len(text) <= max_chars:
            return text

        trimmed = False
        for key in ("notes", "checklist", "workflow", "tools", "guards"):
            items = current.get(key, [])
            if items:
                current[key] = items[:-1]
                trimmed = True
                break
        if not trimmed:
            return text[:max_chars].rsplit("\n", 1)[0].rstrip() + "\n"


def sanitize_memory_reference(text: str, *, max_chars: int = STORAGE_MEMORY_CHAR_LIMIT) -> str:
    section_map = _build_section_map(text)
    return _compose_text(section_map, max_chars=max_chars)


def load_memory_reference(memory_dir: Path, *, max_chars: int = INJECTION_MEMORY_CHAR_LIMIT) -> str:
    candidates = [
        memory_dir / "latest_skills.md",
        memory_dir / "latest_skills.txt",
    ]

    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8")
        except Exception:
            continue
        return sanitize_memory_reference(raw, max_chars=max_chars).strip()

    try:
        files = sorted(
            memory_dir.glob("experience_*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        files = []

    for candidate in files:
        try:
            raw = candidate.read_text(encoding="utf-8")
        except Exception:
            continue
        return sanitize_memory_reference(raw, max_chars=max_chars).strip()

    return sanitize_memory_reference("", max_chars=max_chars).strip()
