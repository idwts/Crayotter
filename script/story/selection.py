from __future__ import annotations

import re

from .models import StoryDirection, StoryDNA, StoryJobConfig


def _direction_text(direction: StoryDirection) -> str:
    return " ".join(
        [
            direction.title,
            direction.logline,
            direction.protagonist_strategy,
            direction.opposition_mechanism,
            direction.recurring_reward,
            direction.cost,
            direction.differentiation,
            direction.market_fit,
        ]
    ).lower()


def _constraint_anchors(dna: StoryDNA, config: StoryJobConfig) -> list[str]:
    source = " ".join(
        [
            dna.title,
            dna.logline,
            dna.premise,
            config.genre,
            config.adaptation_requirements,
            *config.themes,
        ]
    )
    anchors: list[str] = [
        item.strip().lower() for item in config.themes if item.strip()
    ]
    anchors.extend(re.findall(r"(?<!\d)\d{2,4}(?!\d)|\d+\s*[x×]\s*\d+", source.lower()))
    for marker in (
        "北京",
        "女子",
        "女性",
        "接力",
        "第二名",
        "短跑",
        "冬奥",
        "普通人",
        "female",
        "woman",
        "relay",
    ):
        if marker in source.lower():
            anchors.append(marker)
    return list(dict.fromkeys(item for item in anchors if len(item) >= 2))


def _direct_direction(dna: StoryDNA, config: StoryJobConfig) -> StoryDirection:
    return StoryDirection(
        direction_id="direction_direct_brief",
        title=dna.title or "Direct brief",
        logline=dna.logline or dna.premise,
        protagonist_strategy="Follow the approved user brief and its concrete production constraints.",
        opposition_mechanism=dna.conflict_engine
        or "The conflict stated in the user brief.",
        recurring_reward=dna.emotional_promise
        or "Fulfil each requested narrative beat.",
        cost="Do not trade away hard user constraints for novelty.",
        differentiation="Direct execution of the supplied short-video brief.",
        market_fit=", ".join(config.target_markets),
        recommended=True,
    )


def select_relevant_direction(
    dna: StoryDNA,
    directions: list[StoryDirection],
    config: StoryJobConfig,
) -> tuple[StoryDirection, bool, float]:
    """Select a direction and report whether the LLM recommendation was overridden."""

    recommended = next((item for item in directions if item.recommended), directions[0])
    if config.content_type not in {"short_video", "advertisement"}:
        return recommended, False, 1.0
    anchors = _constraint_anchors(dna, config)
    if not anchors:
        return recommended, False, 1.0
    scored = [
        (
            sum(1 for anchor in anchors if anchor in _direction_text(item))
            / len(anchors),
            item,
        )
        for item in directions
    ]
    best_score, best = max(scored, key=lambda item: item[0])
    if best_score >= 0.45:
        return best, best.direction_id != recommended.direction_id, best_score
    return _direct_direction(dna, config), True, best_score
