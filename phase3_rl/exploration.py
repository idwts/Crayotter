from __future__ import annotations

import copy
import hashlib
import os
import random
import threading
from pathlib import Path
from typing import Any


BRANCH_PROFILES: list[dict[str, Any]] = [
    {
        "id": "story_first",
        "name": "narrative-first",
        "instruction": "Prioritize story progression and shot ordering before pacing polish.",
        "preferred_stages": ["diagnosis", "timeline_ordering", "rough_cut", "export_validation"],
    },
    {
        "id": "music_pacing_first",
        "name": "pacing-first",
        "instruction": "Prioritize removing drag and establishing a clear shot rhythm.",
        "preferred_stages": ["diagnosis", "rough_cut", "pacing_transition", "export_validation"],
    },
    {
        "id": "coverage_first",
        "name": "coverage-first",
        "instruction": "Prioritize relevant source coverage while avoiding repeated shots.",
        "preferred_stages": ["diagnosis", "material_selection", "rough_cut", "export_validation"],
    },
    {
        "id": "visual_continuity_first",
        "name": "continuity-first",
        "instruction": "Prioritize subject, scene, and transition continuity across the timeline.",
        "preferred_stages": ["diagnosis", "timeline_ordering", "pacing_transition", "validation"],
    },
    {
        "id": "semantic_delivery_first",
        "name": "semantic-delivery-first",
        "instruction": "Prioritize clear delivery of requested information through picture and text.",
        "preferred_stages": ["diagnosis", "subtitle_narration", "timeline_ordering", "export_validation"],
    },
    {
        "id": "minimal_repair_first",
        "name": "minimal-revision-first",
        "instruction": "Preserve valid prior work and make the smallest sufficient revision.",
        "preferred_stages": ["diagnosis", "material_selection", "repair", "export_validation"],
    },
    {
        "id": "opening_hook_first",
        "name": "opening-hook-first",
        "instruction": "Prioritize a strong opening while keeping later narrative support coherent.",
        "preferred_stages": ["diagnosis", "rough_cut", "timeline_ordering", "export_validation"],
    },
    {
        "id": "constraint_first",
        "name": "constraint-first",
        "instruction": "Prioritize explicit user constraints and preservation requirements.",
        "preferred_stages": ["diagnosis", "validation", "timeline_ordering", "export_validation"],
    },
]

COUNTERFACTUAL_BRANCHES: list[dict[str, Any]] = [
    {
        "id": "opening_hook_suffix",
        "name": "opening-hook suffix",
        "instruction": "After the shared diagnosis, prioritize a high-information opening and remove early drag.",
        "preferred_stages": ["rough_cut", "timeline_ordering", "export_validation"],
    },
    {
        "id": "story_order_suffix",
        "name": "story-order suffix",
        "instruction": "After the shared diagnosis, prioritize causal story order and a clear beginning-development-ending structure.",
        "preferred_stages": ["rough_cut", "timeline_ordering", "export_validation"],
    },
    {
        "id": "minimal_preservation_suffix",
        "name": "minimal-preservation suffix",
        "instruction": "After the shared diagnosis, preserve valid prior content and make the smallest sufficient timeline change.",
        "preferred_stages": ["rough_cut", "timeline_ordering", "repair", "export_validation"],
    },
    {
        "id": "coverage_suffix",
        "name": "coverage suffix",
        "instruction": "After the shared diagnosis, maximize requested semantic coverage without repeated or off-topic shots.",
        "preferred_stages": ["rough_cut", "timeline_ordering", "export_validation"],
    },
]

_LOCAL_COUNTER_LOCK = threading.Lock()
_LOCAL_COUNTERS: dict[str, int] = {}


def profile_for_repeat(repeat_index: int) -> dict[str, Any]:
    return copy.deepcopy(BRANCH_PROFILES[repeat_index % len(BRANCH_PROFILES)])


def sample_rollout_profile() -> dict[str, Any]:
    return copy.deepcopy(random.SystemRandom().choice(BRANCH_PROFILES))


def next_counterfactual_profile(task_key: str) -> dict[str, Any]:
    """Assign every consecutive rollout in a task group a distinct suffix branch."""

    digest = hashlib.sha1(task_key.encode("utf-8", errors="replace")).hexdigest()
    counter_dir = os.environ.get("CRAYOTTER_RL_COUNTERFACTUAL_COUNTER_DIR", "").strip()
    counter_value: int
    if counter_dir:
        path = Path(counter_dir).expanduser().resolve() / f"{digest}.counter"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import fcntl

            with path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.seek(0)
                raw = handle.read().strip()
                counter_value = int(raw) if raw else 0
                handle.seek(0)
                handle.truncate()
                handle.write(str(counter_value + 1))
                handle.flush()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError, ValueError):
            with _LOCAL_COUNTER_LOCK:
                counter_value = _LOCAL_COUNTERS.get(digest, 0)
                _LOCAL_COUNTERS[digest] = counter_value + 1
    else:
        with _LOCAL_COUNTER_LOCK:
            counter_value = _LOCAL_COUNTERS.get(digest, 0)
            _LOCAL_COUNTERS[digest] = counter_value + 1

    branch = copy.deepcopy(COUNTERFACTUAL_BRANCHES[counter_value % len(COUNTERFACTUAL_BRANCHES)])
    branch.update(
        {
            "counterfactual": True,
            "branch_index": counter_value % len(COUNTERFACTUAL_BRANCHES),
            "branch_count": len(COUNTERFACTUAL_BRANCHES),
            "prefix_id": f"prefix_{digest[:16]}",
            "branch_point_event_index": 0,
            "branch_point_stage": "rough_cut",
            "shared_prefix": "the mandatory first tool call and its observation",
        }
    )
    return branch


def append_profile_to_messages(
    raw_prompt: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    messages = [dict(message) for message in raw_prompt]
    heading = "## Same-prefix counterfactual suffix" if profile.get("counterfactual") else "## Internal rollout exploration prior"
    prefix_contract = ""
    if profile.get("counterfactual"):
        prefix_contract = (
            f"prefix_id: {profile['prefix_id']}\n"
            "All rollouts in this group must execute the exact mandatory first tool call shown above. "
            "That action and its returned observation are the shared prefix. Apply this branch strategy only after that observation; "
            "do not alter or skip the shared prefix.\n"
        )
    block = (
        f"\n\n{heading}\n"
        f"{prefix_contract}"
        f"strategy_id: {profile['id']}\n"
        f"strategy: {profile['name']}\n"
        f"guidance: {profile['instruction']}\n"
        "This is an exploration prior, not a replacement for the user's requested style. "
        "Make concrete editing decisions that follow it while satisfying the same user goal."
    )
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user" and isinstance(messages[index].get("content"), str):
            messages[index]["content"] = str(messages[index]["content"]) + block
            break
    return messages
