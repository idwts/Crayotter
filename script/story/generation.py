from __future__ import annotations

import json
from typing import Any, Callable

from script.model_runtime import sanitize_error
from script.parsing import parse_json_object, strip_code_fences
from . import gateway as model_gateway

from .models import (
    EpisodePlan,
    LocalizedVariant,
    ScreenplayEpisode,
    ScreenplayScene,
    SourceWork,
    StoryBeat,
    StoryCharacter,
    StoryDirection,
    StoryDNA,
    StoryJobConfig,
    StoryPackage,
    StoryRelationship,
)

EventSink = Callable[[str, dict[str, Any]], None]


def _emit(sink: EventSink | None, event_type: str, payload: dict[str, Any]) -> None:
    if callable(sink):
        sink(event_type, payload)


class StoryGenerator:
    def __init__(self, *, event_sink: EventSink | None = None) -> None:
        self.event_sink = event_sink

    def extract_dna(
        self, sources: list[SourceWork], config: StoryJobConfig
    ) -> StoryDNA:
        _emit(self.event_sink, "story_stage_started", {"stage": "dna_extraction"})
        payload = {
            "request": config.model_dump(),
            "sources": _source_prompt_payload(sources),
        }
        prompt = """
You are Crayotter's story analyst. Extract a structured story DNA from the provided sources.
Return one JSON object only. Do not reproduce long source passages. Every non-obvious character,
relationship, and beat should include evidence entries with source_id, start, end, and a short
excerpt. Separate reusable dramatic mechanisms from protected expression.
Keep the response compact: at most 6 characters, 8 relationships, 12 beats, and the requested
episode count. Use at most one short evidence entry per character, relationship, or beat.

Required top-level keys:
title, logline, premise, genre, target_audience, themes, world_rules,
conflict_engine, emotional_promise, characters, relationships, beats, episodes,
reversals, hooks, motifs, reusable_mechanisms.

characters require: character_id, name, role, archetype, goal, motivation, flaw,
leverage, boundary, arc, evidence.
relationships require: relationship_id, source_character_id, target_character_id,
relation, tension, changes, evidence.
beats require: beat_id, sequence, label, event, conflict, emotion, intensity (0-100),
reveal, payoff, reusable_mechanism, evidence.
episodes require: episode_id, number, title, incoming_state, objective, obstacles,
escalation, turn, payoff, outgoing_pressure, beat_ids.
""".strip()
        try:
            raw = model_gateway.chat(
                "story_dna_extraction",
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            dna = StoryDNA.model_validate(
                _normalize_dna(_parse_story_json(raw), sources, config)
            )
        except Exception as exc:
            _emit(
                self.event_sink,
                "story_generation_fallback",
                {"stage": "dna_extraction", "error": sanitize_error(exc)[:300]},
            )
            dna = _fallback_dna(sources, config)
        _emit(
            self.event_sink,
            "story_dna_created",
            {
                "characters": len(dna.characters),
                "relationships": len(dna.relationships),
                "beats": len(dna.beats),
                "episodes": len(dna.episodes),
            },
        )
        return dna

    def generate_directions(
        self, dna: StoryDNA, config: StoryJobConfig
    ) -> list[StoryDirection]:
        _emit(self.event_sink, "story_stage_started", {"stage": "direction_generation"})
        prompt = """
You are a short-drama development lead. Propose exactly three genuinely different new-story
directions derived from the reusable mechanisms in the supplied story DNA. Do not copy names,
dialogue, unique settings, or the same event sequence. The directions must differ through
protagonist strategy, source of opposition, recurring reward, or cost—not superficial renaming.
Return JSON: {"directions":[...]}. Each item requires direction_id, title, logline,
protagonist_strategy, opposition_mechanism, recurring_reward, cost, differentiation,
market_fit, recommended. Exactly one direction must be recommended.
""".strip()
        try:
            raw = model_gateway.chat(
                "story_direction_generation",
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"dna": dna.model_dump(), "request": config.model_dump()},
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.75,
                max_tokens=1800,
            )
            data = _parse_story_json(raw)
            directions = [
                StoryDirection.model_validate(_normalize_direction(item, index))
                for index, item in enumerate(data.get("directions", []), start=1)
            ][:3]
        except Exception as exc:
            _emit(
                self.event_sink,
                "story_generation_fallback",
                {"stage": "direction_generation", "error": sanitize_error(exc)[:300]},
            )
            directions = []
        if len(directions) != 3:
            directions = _fallback_directions(dna, config)
        if not any(item.recommended for item in directions):
            directions[0].recommended = True
        recommended_seen = False
        for item in directions:
            if item.recommended and not recommended_seen:
                recommended_seen = True
            elif item.recommended:
                item.recommended = False
        _emit(
            self.event_sink,
            "story_directions_created",
            {
                "count": len(directions),
                "recommended": next(
                    (item.direction_id for item in directions if item.recommended), ""
                ),
            },
        )
        return directions

    def generate_package(
        self,
        dna: StoryDNA,
        direction: StoryDirection,
        config: StoryJobConfig,
    ) -> StoryPackage:
        _emit(
            self.event_sink, "story_stage_started", {"stage": "screenplay_generation"}
        )
        episode_count = min(3, config.episode_count)
        prompt = f"""
You are a professional short-drama development lead. Create the compact foundation for a new
{episode_count}-episode story using the selected direction and story DNA only as abstract craft
constraints. Avoid copied names, unique settings, event order, and dialogue. Do not write scene
action or dialogue in this response; episodes will be written separately.

Return JSON with title, logline, synopsis, world, character_bible, relationships,
episode_outline, episodes (an empty list), and video_prompt_package (an empty list).
""".strip()
        try:
            raw = model_gateway.chat(
                "story_package_foundation",
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "request": config.model_dump(),
                                "story_dna": dna.model_dump(),
                                "selected_direction": direction.model_dump(),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.55,
                max_tokens=2200,
            )
            package = StoryPackage.model_validate(
                _normalize_package(_parse_story_json(raw), dna, direction, config)
            )
        except Exception as exc:
            _emit(
                self.event_sink,
                "story_generation_fallback",
                {
                    "stage": "story_package_foundation",
                    "error": sanitize_error(exc)[:300],
                },
            )
            package = _fallback_package(dna, direction, config)
        package.episodes = []
        for episode_number in range(1, episode_count + 1):
            package.episodes.append(
                self._generate_episode(
                    dna,
                    direction,
                    config,
                    episode_number=episode_number,
                    prior_episodes=package.episodes,
                )
            )
        package.video_prompt_package = [
            {"scene_id": scene.scene_id, "prompt": scene.video_prompt}
            for episode in package.episodes
            for scene in episode.scenes
        ]
        _emit(
            self.event_sink,
            "story_package_created",
            {
                "title": package.title,
                "episodes": len(package.episodes),
                "scenes": sum(len(item.scenes) for item in package.episodes),
            },
        )
        return package

    def _generate_episode(
        self,
        dna: StoryDNA,
        direction: StoryDirection,
        config: StoryJobConfig,
        *,
        episode_number: int,
        prior_episodes: list[ScreenplayEpisode],
    ) -> ScreenplayEpisode:
        plan = next(
            (item for item in dna.episodes if item.number == episode_number),
            dna.episodes[min(episode_number - 1, len(dna.episodes) - 1)]
            if dna.episodes
            else None,
        )
        prompt = f"""
Write episode {episode_number} of a vertical short drama, targeting
{config.episode_duration_seconds} seconds. Return JSON as {{"episode": {{...}}}}.
The episode requires episode_id, number, title, synopsis, target_duration_seconds and 3-6
scenes. Every scene requires scene_id, number, heading, location, time_of_day, purpose, action,
dialogue (list of {{"character":"...","text":"...","direction":"..."}}), emotional_start,
emotional_end, hook, and video_prompt. Each scene must change story state. Deliver an episode
payoff before the final hook. Preserve stable character IDs/names and avoid reference expression.
Write compact, performable dialogue rather than prose.
""".strip()
        try:
            raw = model_gateway.chat(
                f"story_episode_{episode_number:03d}",
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "request": config.model_dump(),
                                "selected_direction": direction.model_dump(),
                                "characters": [
                                    item.model_dump() for item in dna.characters
                                ],
                                "episode_plan": plan.model_dump() if plan else {},
                                "prior_episode_summaries": [
                                    {
                                        "episode_id": item.episode_id,
                                        "synopsis": item.synopsis,
                                    }
                                    for item in prior_episodes
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.65,
                max_tokens=3200,
            )
            payload = _parse_story_json(raw)
            episode_payload = payload.get("episode", payload)
            episode = ScreenplayEpisode.model_validate(
                _normalize_episode(episode_payload, episode_number, config)
            )
        except Exception as exc:
            _emit(
                self.event_sink,
                "story_generation_fallback",
                {
                    "stage": "screenplay_episode",
                    "episode": episode_number,
                    "error": sanitize_error(exc)[:300],
                },
            )
            episode = _fallback_package(dna, direction, config).episodes[
                min(episode_number - 1, min(config.episode_count, 3) - 1)
            ]
        _emit(
            self.event_sink,
            "story_episode_generated",
            {
                "episode": episode.number,
                "scenes": len(episode.scenes),
            },
        )
        return episode

    def localize(
        self,
        package: StoryPackage,
        *,
        language: str,
        market: str,
        config: StoryJobConfig,
    ) -> LocalizedVariant:
        _emit(
            self.event_sink,
            "story_stage_started",
            {"stage": "localization", "language": language, "market": market},
        )
        prompt = """
You are a screenplay localization lead. Adapt the supplied package for the target language and
market. This is cultural adaptation, not literal translation: adjust names, locations,
professions, class signals, family/marriage assumptions, conflict behavior, dialogue register,
and platform constraints while preserving character_id, episode_id and scene_id. Record every
intentional change in cultural_changes.

Return a compact JSON object with language, market, title, logline, synopsis, cultural_changes,
character_names (character_id -> localized name), and episodes as an empty list. Episodes are
localized separately.
""".strip()
        package_core = package.model_dump(exclude={"episodes", "video_prompt_package"})
        try:
            raw = model_gateway.chat(
                f"story_localization_foundation_{language}",
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "target_language": language,
                                "target_market": market,
                                "platform_constraints": config.platform_constraints,
                                "package": package_core,
                                "episode_summaries": [
                                    {
                                        "episode_id": item.episode_id,
                                        "title": item.title,
                                        "synopsis": item.synopsis,
                                    }
                                    for item in package.episodes
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=1200,
            )
            payload = _parse_story_json(raw)
            payload["language"] = language
            payload["market"] = market
            payload["episodes"] = []
            if isinstance(payload.get("character_names"), list):
                payload["character_names"] = {
                    str(item.get("character_id") or item.get("id") or index): str(
                        item.get("name") or ""
                    )
                    for index, item in enumerate(payload["character_names"], start=1)
                    if isinstance(item, dict)
                }
            variant = LocalizedVariant.model_validate(payload)
        except Exception as exc:
            _emit(
                self.event_sink,
                "story_generation_fallback",
                {
                    "stage": "localization",
                    "language": language,
                    "error": sanitize_error(exc)[:300],
                },
            )
            variant = LocalizedVariant(
                language=language,
                market=market,
                title=package.title,
                logline=package.logline,
                synopsis=package.synopsis,
                cultural_changes=[
                    "Automatic localization was unavailable; this variant preserves source text for human review."
                ],
                character_names={
                    item.character_id: item.name for item in package.character_bible
                },
                episodes=[],
            )
        variant.episodes = [
            self._localize_episode(
                episode,
                language=language,
                market=market,
                character_names=variant.character_names,
                config=config,
            )
            for episode in package.episodes
        ]
        _emit(
            self.event_sink,
            "story_localized",
            {
                "language": language,
                "market": market,
                "changes": len(variant.cultural_changes),
            },
        )
        return variant

    def _localize_episode(
        self,
        episode: ScreenplayEpisode,
        *,
        language: str,
        market: str,
        character_names: dict[str, str],
        config: StoryJobConfig,
    ) -> ScreenplayEpisode:
        prompt = """
Localize one short-drama episode for the target language and market. Preserve episode_id,
scene_id, scene order, dramatic action, payoff, and hook. Adapt names, locations, institutions,
social register, and dialogue naturally; do not merely transliterate. Return JSON as
{"episode":{...}} using the same episode and scene schema. Keep action and dialogue compact.
""".strip()
        try:
            raw = model_gateway.chat(
                f"story_localization_{language}_{episode.episode_id}",
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "target_language": language,
                                "target_market": market,
                                "platform_constraints": config.platform_constraints,
                                "character_names": character_names,
                                "episode": episode.model_dump(),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.25,
                max_tokens=2200,
            )
            payload = _parse_story_json(raw)
            candidate = payload.get("episode", payload)
            localized = ScreenplayEpisode.model_validate(
                _normalize_localized_episode(candidate, episode, config)
            )
        except Exception as exc:
            _emit(
                self.event_sink,
                "story_generation_fallback",
                {
                    "stage": "localization_episode",
                    "language": language,
                    "episode": episode.number,
                    "error": sanitize_error(exc)[:300],
                },
            )
            localized = episode.model_copy(deep=True)
        return localized


def _source_prompt_payload(
    sources: list[SourceWork], max_chars: int = 50000
) -> list[dict[str, Any]]:
    remaining = max_chars
    payload: list[dict[str, Any]] = []
    for source in sources:
        chunks: list[dict[str, Any]] = []
        for chunk in source.chunks:
            if remaining <= 0:
                break
            text = chunk.text[:remaining]
            chunks.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "start": chunk.start,
                    "end": chunk.start + len(text),
                    "text": text,
                }
            )
            remaining -= len(text)
        payload.append(
            {
                "source_id": source.source_id,
                "title": source.title,
                "language": source.language,
                "chunks": chunks,
            }
        )
        if remaining <= 0:
            break
    return payload


def _parse_story_json(text: str) -> dict[str, Any]:
    try:
        return parse_json_object(text)
    except json.JSONDecodeError:
        from json_repair import repair_json

        content = strip_code_fences(text)
        start = content.find("{")
        if start >= 0:
            content = content[start:]
        repaired = repair_json(content, return_objects=True)
        if not isinstance(repaired, dict):
            raise ValueError("Story model output did not contain a JSON object.")
        return repaired


def _normalize_dna(
    payload: dict[str, Any], sources: list[SourceWork], config: StoryJobConfig
) -> dict[str, Any]:
    data = dict(payload)
    data.setdefault("title", sources[0].title)
    data.setdefault("genre", config.genre)
    source_id = sources[0].source_id

    data["characters"] = _object_list(data.get("characters"), scalar_key="name")
    for index, item in enumerate(data.get("characters") or [], start=1):
        item.setdefault("character_id", f"char_{index:03d}")
        item.setdefault("name", f"Character {index}")
        _normalize_character_fields(item)
        item["evidence"] = _normalize_evidence(item.get("evidence"), source_id)
    character_ids = [item["character_id"] for item in data.get("characters") or []]

    data["relationships"] = _object_list(
        data.get("relationships"), scalar_key="relation"
    )
    for index, item in enumerate(data.get("relationships") or [], start=1):
        item.setdefault("relationship_id", f"rel_{index:03d}")
        item.setdefault(
            "source_character_id",
            item.get("source")
            or item.get("from")
            or (character_ids[0] if character_ids else "char_001"),
        )
        item.setdefault(
            "target_character_id",
            item.get("target")
            or item.get("to")
            or (
                character_ids[min(1, len(character_ids) - 1)]
                if character_ids
                else "char_002"
            ),
        )
        item.setdefault("relation", item.get("type") or "conflict")
        item["tension"] = _compact_text(item.get("tension"))
        item["changes"] = _text_list(item.get("changes"))
        item["evidence"] = _normalize_evidence(item.get("evidence"), source_id)

    data["beats"] = _object_list(data.get("beats"), scalar_key="event")
    for index, item in enumerate(data.get("beats") or [], start=1):
        item.setdefault("beat_id", f"beat_{index:03d}")
        item.setdefault("sequence", index)
        item.setdefault("label", f"Beat {index}")
        item.setdefault("event", item.get("label", f"Beat {index}"))
        for field in (
            "label",
            "event",
            "conflict",
            "emotion",
            "reveal",
            "payoff",
            "reusable_mechanism",
        ):
            item[field] = _compact_text(item.get(field))
        try:
            item["intensity"] = max(0, min(100, int(item.get("intensity", 50))))
        except (TypeError, ValueError):
            item["intensity"] = 50
        item["evidence"] = _normalize_evidence(item.get("evidence"), source_id)
    beat_ids = [item["beat_id"] for item in data.get("beats") or []]

    data["episodes"] = _object_list(data.get("episodes"), scalar_key="objective")
    for index, item in enumerate(data.get("episodes") or [], start=1):
        item.setdefault("episode_id", f"ep_{index:03d}")
        item.setdefault("number", index)
        item.setdefault("beat_ids", beat_ids[max(0, index - 1) : index])
        item["obstacles"] = _text_list(item.get("obstacles"))
        for field in (
            "title",
            "incoming_state",
            "objective",
            "escalation",
            "turn",
            "payoff",
            "outgoing_pressure",
        ):
            item[field] = _compact_text(item.get(field))
    return data


def _compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(_compact_text(item) for item in value if item is not None)
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_compact_text(item)}" for key, item in value.items())
    return str(value)


def _object_list(value: Any, *, scalar_key: str) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        raw_items = value.values()
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    return [
        dict(item) if isinstance(item, dict) else {scalar_key: _compact_text(item)}
        for item in raw_items
    ]


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_compact_text(item) for item in value if _compact_text(item)]
    return [_compact_text(value)] if _compact_text(value) else []


def _normalize_evidence(value: Any, source_id: str) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else ([value] if value else [])
    evidence: list[dict[str, Any]] = []
    for raw in raw_items[:1]:
        if isinstance(raw, dict):
            item = dict(raw)
            item.setdefault("source_id", source_id)
            item.setdefault("start", 0)
            item.setdefault("end", item["start"])
            item.setdefault("excerpt", "")
        else:
            item = {
                "source_id": source_id,
                "start": 0,
                "end": 0,
                "excerpt": _compact_text(raw)[:240],
            }
        evidence.append(item)
    return evidence


def _normalize_character_fields(item: dict[str, Any]) -> None:
    for field in (
        "name",
        "role",
        "archetype",
        "goal",
        "motivation",
        "flaw",
        "leverage",
        "boundary",
        "arc",
    ):
        item[field] = _compact_text(item.get(field))


def _normalize_direction(item: Any, index: int) -> dict[str, Any]:
    data = dict(item) if isinstance(item, dict) else {}
    data.setdefault("direction_id", f"direction_{index:02d}")
    data.setdefault("title", f"Direction {index}")
    for key in (
        "logline",
        "protagonist_strategy",
        "opposition_mechanism",
        "recurring_reward",
        "cost",
        "differentiation",
    ):
        data.setdefault(key, "")
    data["recommended"] = bool(data.get("recommended", index == 1))
    return data


def _normalize_package(
    payload: dict[str, Any],
    dna: StoryDNA,
    direction: StoryDirection,
    config: StoryJobConfig,
) -> dict[str, Any]:
    data = dict(payload)
    data.setdefault("title", direction.title)
    data.setdefault("logline", direction.logline)
    for field in ("title", "logline", "synopsis", "world"):
        data[field] = _compact_text(data.get(field))

    raw_characters = data.get("character_bible")
    if isinstance(raw_characters, dict):
        characters = []
        for role, raw in raw_characters.items():
            item = dict(raw) if isinstance(raw, dict) else {"name": str(raw)}
            item.setdefault("role", str(role))
            characters.append(item)
        data["character_bible"] = characters
    elif isinstance(raw_characters, list):
        data["character_bible"] = [
            dict(item) if isinstance(item, dict) else {"name": str(item)}
            for item in raw_characters
        ]
    if not isinstance(data.get("character_bible"), list):
        data["character_bible"] = [item.model_dump() for item in dna.characters]
    for index, item in enumerate(data.get("character_bible") or [], start=1):
        item.setdefault("character_id", f"char_{index:03d}")
        item.setdefault("name", f"Character {index}")
        _normalize_character_fields(item)
        item["evidence"] = []

    relationships = _object_list(data.get("relationships"), scalar_key="relation")
    data["relationships"] = relationships or [
        item.model_dump() for item in dna.relationships
    ]
    character_ids = [item["character_id"] for item in data.get("character_bible") or []]
    character_lookup = {
        str(item.get("name") or "").strip().lower(): item["character_id"]
        for item in data.get("character_bible") or []
        if str(item.get("name") or "").strip()
    }
    for index, item in enumerate(data.get("relationships") or [], start=1):
        item.setdefault("relationship_id", f"rel_{index:03d}")
        if "source_character_id" not in item:
            item["source_character_id"] = item.get("source") or item.get("from") or ""
        if "target_character_id" not in item:
            item["target_character_id"] = item.get("target") or item.get("to") or ""
        if "relation" not in item:
            item["relation"] = item.get("type") or ""
        item.setdefault(
            "source_character_id", character_ids[0] if character_ids else "char_001"
        )
        item.setdefault(
            "target_character_id",
            character_ids[min(1, len(character_ids) - 1)]
            if character_ids
            else "char_002",
        )
        if not item["source_character_id"]:
            item["source_character_id"] = (
                character_ids[0] if character_ids else "char_001"
            )
        if not item["target_character_id"]:
            item["target_character_id"] = (
                character_ids[min(1, len(character_ids) - 1)]
                if character_ids
                else "char_002"
            )
        if not item["relation"]:
            item["relation"] = "conflict"
        item["source_character_id"] = character_lookup.get(
            str(item["source_character_id"]).strip().lower(),
            item["source_character_id"],
        )
        item["target_character_id"] = character_lookup.get(
            str(item["target_character_id"]).strip().lower(),
            item["target_character_id"],
        )
        item["tension"] = _compact_text(item.get("tension"))
        item["changes"] = _text_list(item.get("changes"))
        item["evidence"] = []

    outline = _object_list(data.get("episode_outline"), scalar_key="objective")
    data["episode_outline"] = outline or [item.model_dump() for item in dna.episodes]
    for index, item in enumerate(data.get("episode_outline") or [], start=1):
        item.setdefault("episode_id", f"ep_{index:03d}")
        item.setdefault("number", index)
    data["episodes"] = _object_list(data.get("episodes"), scalar_key="synopsis")
    for episode_index, episode in enumerate(data["episodes"], start=1):
        episode.setdefault("episode_id", f"ep_{episode_index:03d}")
        episode.setdefault("number", episode_index)
        episode.setdefault("title", f"Episode {episode_index}")
        episode.setdefault("target_duration_seconds", config.episode_duration_seconds)
        episode["scenes"] = _object_list(episode.get("scenes"), scalar_key="action")
        for scene_index, scene in enumerate(episode["scenes"], start=1):
            scene.setdefault(
                "scene_id", f"ep_{episode_index:03d}_scene_{scene_index:03d}"
            )
            scene.setdefault("number", scene_index)
            scene.setdefault("heading", f"SCENE {scene_index}")
    data.setdefault("video_prompt_package", [])
    return data


def _normalize_episode(
    payload: Any, episode_number: int, config: StoryJobConfig
) -> dict[str, Any]:
    data = dict(payload) if isinstance(payload, dict) else {}
    data.setdefault("episode_id", f"ep_{episode_number:03d}")
    data.setdefault("number", episode_number)
    data.setdefault("title", f"Episode {episode_number}")
    data.setdefault("synopsis", "")
    data.setdefault("target_duration_seconds", config.episode_duration_seconds)
    data["scenes"] = _object_list(data.get("scenes"), scalar_key="action")
    for scene_index, scene in enumerate(data["scenes"], start=1):
        scene.setdefault("scene_id", f"ep_{episode_number:03d}_scene_{scene_index:03d}")
        scene.setdefault("number", scene_index)
        scene.setdefault("heading", f"SCENE {scene_index}")
        raw_dialogue = scene.get("dialogue")
        if isinstance(raw_dialogue, dict):
            scene["dialogue"] = [
                {"character": str(character), "text": str(text), "direction": ""}
                for character, text in raw_dialogue.items()
            ]
        elif isinstance(raw_dialogue, str):
            scene["dialogue"] = [
                {"character": "CHARACTER", "text": raw_dialogue, "direction": ""}
            ]
        elif isinstance(raw_dialogue, list):
            normalized_dialogue = []
            for raw_line in raw_dialogue:
                if isinstance(raw_line, dict):
                    normalized_dialogue.append(
                        {
                            "character": _compact_text(
                                raw_line.get("character") or raw_line.get("speaker")
                            )
                            or "CHARACTER",
                            "text": _compact_text(
                                raw_line.get("text") or raw_line.get("line")
                            ),
                            "direction": _compact_text(
                                raw_line.get("direction")
                                or raw_line.get("parenthetical")
                            ),
                        }
                    )
                else:
                    normalized_dialogue.append(
                        {
                            "character": "CHARACTER",
                            "text": _compact_text(raw_line),
                            "direction": "",
                        }
                    )
            scene["dialogue"] = normalized_dialogue
        else:
            scene.setdefault("dialogue", [])
        for field in (
            "heading",
            "location",
            "time_of_day",
            "purpose",
            "action",
            "emotional_start",
            "emotional_end",
            "hook",
            "video_prompt",
        ):
            scene[field] = _compact_text(scene.get(field))
    return data


def _normalize_localized_episode(
    payload: Any,
    source: ScreenplayEpisode,
    config: StoryJobConfig,
) -> dict[str, Any]:
    data = _normalize_episode(payload, source.number, config)
    data["episode_id"] = source.episode_id
    scenes = data.get("scenes") or []
    if len(scenes) != len(source.scenes):
        data["scenes"] = [item.model_dump() for item in source.scenes]
        return data
    for index, scene in enumerate(scenes):
        scene["scene_id"] = source.scenes[index].scene_id
        scene["number"] = source.scenes[index].number
    return data


def _fallback_dna(sources: list[SourceWork], config: StoryJobConfig) -> StoryDNA:
    source = sources[0]
    excerpt = source.text[:180].replace("\n", " ")
    characters = [
        StoryCharacter(
            character_id="char_001",
            name="Protagonist",
            role="protagonist",
            goal="Achieve the user's stated objective",
            motivation="Derived from the supplied story brief",
        ),
        StoryCharacter(
            character_id="char_002",
            name="Opponent",
            role="opponent",
            goal="Prevent the protagonist's success",
        ),
    ]
    beats = [
        StoryBeat(
            beat_id=f"beat_{index:03d}",
            sequence=index,
            label=label,
            event=event,
            conflict=event,
            emotion=emotion,
            intensity=intensity,
            reusable_mechanism=mechanism,
        )
        for index, (label, event, emotion, intensity, mechanism) in enumerate(
            [
                (
                    "Disruption",
                    excerpt or "The protagonist's normal state is disrupted.",
                    "uncertainty",
                    35,
                    "status disruption",
                ),
                (
                    "Countermove",
                    "The opponent converts the disruption into public pressure.",
                    "frustration",
                    60,
                    "escalating counterplay",
                ),
                (
                    "Reversal",
                    "New information changes who holds leverage.",
                    "surprise",
                    80,
                    "information reversal",
                ),
            ],
            start=1,
        )
    ]
    episodes = [
        EpisodePlan(
            episode_id=f"ep_{index:03d}",
            number=index,
            title=f"Episode {index}",
            incoming_state="Pressure carried from the previous episode"
            if index > 1
            else "Stable but vulnerable",
            objective="Gain leverage over the central conflict",
            obstacles=["Opponent countermove", "Personal cost"],
            escalation="The attempted solution creates a larger visible risk.",
            turn="The protagonist discovers a new source of leverage.",
            payoff="A partial win changes the balance of power.",
            outgoing_pressure="The win exposes a more dangerous conflict.",
            beat_ids=[beats[min(index - 1, len(beats) - 1)].beat_id],
        )
        for index in range(1, min(config.episode_count, 3) + 1)
    ]
    return StoryDNA(
        title=source.title,
        logline=excerpt,
        premise=excerpt,
        genre=config.genre,
        themes=config.themes,
        conflict_engine="Every attempt to gain control gives the opponent a new way to raise the cost.",
        emotional_promise="Repeated reversals turn humiliation or pressure into earned agency.",
        characters=characters,
        relationships=[
            StoryRelationship(
                relationship_id="rel_001",
                source_character_id="char_001",
                target_character_id="char_002",
                relation="opposition",
                tension="Both need control of the same outcome.",
            )
        ],
        beats=beats,
        episodes=episodes,
        reversals=["The apparent loser holds hidden leverage."],
        hooks=["A partial victory reveals a larger threat."],
        reusable_mechanisms=[item.reusable_mechanism for item in beats],
    )


def _fallback_directions(dna: StoryDNA, config: StoryJobConfig) -> list[StoryDirection]:
    genre = config.genre or dna.genre or "short drama"
    return [
        StoryDirection(
            direction_id="direction_01",
            title=f"{genre}: leverage reversal",
            logline="An underestimated outsider turns each public setback into evidence against a protected rival.",
            protagonist_strategy="Collect proof while appearing to retreat.",
            opposition_mechanism="The rival controls reputation and access.",
            recurring_reward="Hidden preparation converts humiliation into a visible reversal.",
            cost="Every reveal endangers an ally.",
            differentiation="Changes the engine to evidence and reputation warfare.",
            recommended=True,
        ),
        StoryDirection(
            direction_id="direction_02",
            title=f"{genre}: forced alliance",
            logline="Two enemies must cooperate while secretly racing to control the same fragile asset.",
            protagonist_strategy="Trade short-term cooperation for strategic information.",
            opposition_mechanism="The ally-opponent can withdraw access at any moment.",
            recurring_reward="Cooperation produces wins that deepen personal betrayal.",
            cost="Success makes separation more dangerous.",
            differentiation="Replaces direct revenge with unstable interdependence.",
        ),
        StoryDirection(
            direction_id="direction_03",
            title=f"{genre}: system trap",
            logline="A rule-following protagonist learns to weaponize the institution that was designed to silence them.",
            protagonist_strategy="Force the system to contradict its own public rules.",
            opposition_mechanism="Distributed institutional pressure instead of one villain.",
            recurring_reward="Each rule exploited opens a higher-level obstacle.",
            cost="Winning within the system risks becoming part of it.",
            differentiation="Moves conflict from personal rivalry to institutional strategy.",
        ),
    ]


def _fallback_package(
    dna: StoryDNA, direction: StoryDirection, config: StoryJobConfig
) -> StoryPackage:
    episodes: list[ScreenplayEpisode] = []
    for episode_index in range(1, min(config.episode_count, 3) + 1):
        scenes = [
            ScreenplayScene(
                scene_id=f"ep_{episode_index:03d}_scene_{scene_index:03d}",
                number=scene_index,
                heading=f"INT. STORY SPACE - {'DAY' if scene_index < 3 else 'NIGHT'}",
                location="Story space",
                time_of_day="DAY" if scene_index < 3 else "NIGHT",
                purpose=purpose,
                action=action,
                dialogue=[{"character": "PROTAGONIST", "text": line, "direction": ""}],
                emotional_start="uncertain",
                emotional_end="determined" if scene_index < 3 else "alarmed",
                hook=hook,
                video_prompt=f"Vertical short drama, {purpose}, controlled performance, clear blocking",
            )
            for scene_index, (purpose, action, line, hook) in enumerate(
                [
                    (
                        "Establish pressure",
                        "A visible setback removes the protagonist's safe option.",
                        "I need another way in.",
                        "The opponent notices the retreat.",
                    ),
                    (
                        "Execute countermove",
                        "The protagonist tests a plan and obtains partial leverage.",
                        "You only saw what I wanted you to see.",
                        "The evidence points to an ally.",
                    ),
                    (
                        "Deliver payoff and hook",
                        "A partial public win changes the power balance but reveals a greater cost.",
                        "This is not over.",
                        "A new threat enters with proof of its own.",
                    ),
                ],
                start=1,
            )
        ]
        episodes.append(
            ScreenplayEpisode(
                episode_id=f"ep_{episode_index:03d}",
                number=episode_index,
                title=f"Episode {episode_index}",
                synopsis=direction.logline,
                target_duration_seconds=config.episode_duration_seconds,
                scenes=scenes,
            )
        )
    return StoryPackage(
        title=direction.title,
        logline=direction.logline,
        synopsis=direction.differentiation,
        world="A production-conscious contemporary world derived from the selected target market.",
        character_bible=dna.characters,
        relationships=dna.relationships,
        episode_outline=dna.episodes[: len(episodes)],
        episodes=episodes,
        video_prompt_package=[
            {"scene_id": scene.scene_id, "prompt": scene.video_prompt}
            for episode in episodes
            for scene in episode.scenes
        ],
    )
