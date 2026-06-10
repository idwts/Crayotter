from __future__ import annotations

import re


_LEADING_REQUEST_RE = re.compile(
    r"^(?:请(?:你)?|麻烦(?:你)?|可以)?\s*"
    r"(?:(?:帮|替|为)我)?\s*"
    r"(?:制作|生成|剪辑|创建|做)\s*"
    r"(?:一个|一段|一条|一期|个|段|条)?\s*",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"(?:时长|长度)?\s*(?:约|大约|控制在)?\s*"
    r"\d+(?:\.\d+)?\s*(?:小时|分钟|分|秒钟|秒)\s*(?:左右|以内|以上)?",
    re.IGNORECASE,
)
_FEATURE_RE = re.compile(
    r"(?:需要|要求|并且|同时|以及|和|并|要|带|含|有|添加|配上|包含)?\s*"
    r"(?:中文字幕|中英字幕|字幕|旁白|配音|背景音乐|音乐|转场)\s*",
    re.IGNORECASE,
)
_CLAUSE_PREFIX_RE = re.compile(
    r"^(?:短片|视频|成片)?\s*(?:中|里)?\s*"
    r"(?:需要|要求|要|并且|同时|突出)?\s*"
    r"(?:体现出?|展现出?|展示|突出|反映|介绍)\s*",
    re.IGNORECASE,
)
_PUNCTUATION_RE = re.compile(r"[，,。.!！?？;；:：、\n\r]+")
_SPACE_RE = re.compile(r"\s+")


def summarize_task_title(task: str, max_length: int = 24) -> str:
    """Create a short, deterministic UI title without changing the task prompt."""
    text = _SPACE_RE.sub(" ", str(task or "")).strip()
    if not text:
        return "未命名任务"

    clauses: list[str] = []
    for raw_clause in _PUNCTUATION_RE.split(text):
        clause = _LEADING_REQUEST_RE.sub("", raw_clause.strip())
        clause = _DURATION_RE.sub("", clause)
        clause = _FEATURE_RE.sub("", clause)
        clause = _CLAUSE_PREFIX_RE.sub("", clause)
        clause = clause.strip(" -_·，。")
        clause = re.sub(r"^(?:的|一个|一段|一条)\s*", "", clause)
        clause = _SPACE_RE.sub(" ", clause).strip()
        if not clause or clause in clauses:
            continue
        clauses.append(clause)

    if not clauses:
        clauses = [_SPACE_RE.sub(" ", text).strip()]

    title_parts: list[str] = []
    for clause in clauses:
        candidate = clause
        for existing in title_parts:
            shared_prefix = _longest_common_prefix(existing, candidate)
            if len(shared_prefix) >= 2:
                candidate = candidate[len(shared_prefix):].lstrip("的")
                break
        if candidate and candidate not in title_parts:
            title_parts.append(candidate)
        if len(" · ".join(title_parts)) >= max_length:
            break

    title = " · ".join(title_parts) or clauses[0]
    if len(title) <= max_length:
        return title
    return title[: max(1, max_length - 1)].rstrip(" ·") + "…"


def _longest_common_prefix(left: str, right: str) -> str:
    length = min(len(left), len(right))
    index = 0
    while index < length and left[index] == right[index]:
        index += 1
    return left[:index]
