from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Iterable

from .models import (
    SimilarityFinding,
    SimilarityReport,
    SourceWork,
    StoryDNA,
    StoryPackage,
)


def build_similarity_report(
    package: StoryPackage,
    *,
    sources: list[SourceWork],
    reference_dna: StoryDNA,
    corpus_scope: str = "references",
) -> SimilarityReport:
    source_passages = _source_passages(sources)
    findings: list[SimilarityFinding] = []
    signal_scores: dict[str, float] = {}

    dialogue_candidates = [
        (
            f"{episode.episode_id}/{scene.scene_id}/dialogue/{index}",
            item.get("text", ""),
        )
        for episode in package.episodes
        for scene in episode.scenes
        for index, item in enumerate(scene.dialogue, start=1)
        if item.get("text", "").strip()
    ]
    dialogue_score, dialogue_findings = _compare_passages(
        dialogue_candidates,
        source_passages,
        signal="dialogue",
        scorer=_lexical_similarity,
        medium=0.72,
        high=0.88,
        recommendation="Rewrite the line from character intent; change syntax, imagery, and information order.",
    )
    signal_scores["dialogue"] = dialogue_score
    findings.extend(dialogue_findings)

    scene_candidates = [
        (
            f"{episode.episode_id}/{scene.scene_id}",
            " ".join(
                part
                for part in (scene.purpose, scene.action, scene.hook)
                if str(part).strip()
            ),
        )
        for episode in package.episodes
        for scene in episode.scenes
    ]
    semantic_score, semantic_findings = _compare_passages(
        scene_candidates,
        source_passages,
        signal="semantic",
        scorer=_semantic_similarity,
        medium=0.68,
        high=0.84,
        recommendation="Change the causal action, leverage, consequence, or viewpoint—not only wording.",
    )
    signal_scores["semantic"] = semantic_score
    findings.extend(semantic_findings)

    structure_score = _sequence_similarity(
        [scene.purpose for episode in package.episodes for scene in episode.scenes],
        [beat.event for beat in reference_dna.beats],
    )
    signal_scores["structure"] = structure_score
    if structure_score >= 0.62:
        findings.append(
            SimilarityFinding(
                finding_id="finding_structure_001",
                signal="structure",
                risk=_risk(structure_score, medium=0.62, high=0.82),
                score=structure_score,
                output_locator="episode_sequence",
                output_excerpt=" → ".join(
                    scene.purpose
                    for episode in package.episodes
                    for scene in episode.scenes
                )[:400],
                source_id="story_dna",
                source_locator="beat_sequence",
                source_excerpt=" → ".join(beat.event for beat in reference_dna.beats)[
                    :400
                ],
                explanation="The generated episode progression follows a similar semantic beat sequence.",
                recommendation="Reorder causal beats and replace at least one pressure/payoff mechanism.",
            )
        )

    relationship_score = _relationship_similarity(package, reference_dna)
    signal_scores["relationship"] = relationship_score
    if relationship_score >= 0.65:
        findings.append(
            SimilarityFinding(
                finding_id="finding_relationship_001",
                signal="relationship",
                risk=_risk(relationship_score, medium=0.65, high=0.9),
                score=relationship_score,
                output_locator="character_relationships",
                output_excerpt="; ".join(
                    item.relation for item in package.relationships
                )[:300],
                source_id="story_dna",
                source_locator="relationships",
                source_excerpt="; ".join(
                    item.relation for item in reference_dna.relationships
                )[:300],
                explanation="The role and relationship topology overlaps the reference analysis.",
                recommendation="Change role dependency, leverage direction, or the relationship's transformation.",
            )
        )

    setting_candidates = [
        (
            f"{episode.episode_id}/{scene.scene_id}/setting",
            f"{scene.location} {scene.heading}",
        )
        for episode in package.episodes
        for scene in episode.scenes
    ]
    setting_score, setting_findings = _compare_passages(
        setting_candidates,
        source_passages,
        signal="setting",
        scorer=_lexical_similarity,
        medium=0.72,
        high=0.9,
        recommendation="Replace the specific location, profession, institution, or status markers.",
    )
    signal_scores["setting"] = setting_score
    findings.extend(setting_findings)

    overall_score = _overall_score(signal_scores)
    if dialogue_score >= 0.9:
        overall_score = max(overall_score, 0.86)
    report = SimilarityReport(
        corpus_scope=corpus_scope,
        corpus_hash=hashlib.sha256(
            "|".join(sorted(item.sha256 for item in sources)).encode("ascii")
        ).hexdigest(),
        overall_risk=_risk(overall_score, medium=0.48, high=0.76),
        overall_score=overall_score,
        signal_scores=signal_scores,
        findings=sorted(findings, key=lambda item: item.score, reverse=True)[:30],
    )
    return report


def _source_passages(sources: list[SourceWork]) -> list[tuple[str, str, str]]:
    passages: list[tuple[str, str, str]] = []
    for source in sources:
        for chunk in source.chunks:
            for index, text in enumerate(_split_passages(chunk.text), start=1):
                if len(_normalize(text)) < 6:
                    continue
                passages.append(
                    (
                        source.source_id,
                        f"{chunk.chunk_id}/passage/{index}",
                        text,
                    )
                )
    return passages


def _split_passages(text: str, max_chars: int = 360) -> list[str]:
    parts = re.split(r"(?<=[。！？.!?])\s*|\n+", str(text or ""))
    output: list[str] = []
    buffer = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if buffer and len(buffer) + len(part) > max_chars:
            output.append(buffer)
            buffer = ""
        buffer = f"{buffer} {part}".strip()
    if buffer:
        output.append(buffer)
    return output


def _compare_passages(
    candidates: list[tuple[str, str]],
    sources: list[tuple[str, str, str]],
    *,
    signal: str,
    scorer,
    medium: float,
    high: float,
    recommendation: str,
) -> tuple[float, list[SimilarityFinding]]:
    best_overall = 0.0
    findings: list[SimilarityFinding] = []
    for locator, output_text in candidates:
        if len(_normalize(output_text)) < 6:
            continue
        best: tuple[float, str, str, str] = (0.0, "", "", "")
        for source_id, source_locator, source_text in sources:
            score = scorer(output_text, source_text)
            if score > best[0]:
                best = (score, source_id, source_locator, source_text)
        best_overall = max(best_overall, best[0])
        if best[0] < medium:
            continue
        findings.append(
            SimilarityFinding(
                finding_id=f"finding_{signal}_{len(findings) + 1:03d}",
                signal=signal,
                risk=_risk(best[0], medium=medium, high=high),
                score=best[0],
                output_locator=locator,
                output_excerpt=output_text[:500],
                source_id=best[1],
                source_locator=best[2],
                source_excerpt=best[3][:500],
                explanation=f"The strongest {signal} match exceeds the configured review threshold.",
                recommendation=recommendation,
            )
        )
    return round(best_overall, 4), findings


def _normalize(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(text or "").lower())


def _lexical_similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz

        ratio = max(fuzz.ratio(a, b), fuzz.partial_ratio(a, b)) / 100.0
    except ImportError:
        ratio = max(
            SequenceMatcher(None, a, b, autojunk=False).ratio(),
            _containment_ratio(a, b),
        )
    try:
        from datasketch import MinHash

        left_hash = MinHash(num_perm=64)
        right_hash = MinHash(num_perm=64)
        for token in _character_ngrams(a, 3):
            left_hash.update(token.encode("utf-8"))
        for token in _character_ngrams(b, 3):
            right_hash.update(token.encode("utf-8"))
        ratio = max(ratio, float(left_hash.jaccard(right_hash)))
    except (ImportError, ValueError):
        pass
    return round(min(1.0, ratio), 4)


def _containment_ratio(a: str, b: str) -> float:
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if short in long:
        return len(short) / max(1, min(len(long), len(short) * 1.25))
    return 0.0


def _character_ngrams(value: str, size: int) -> set[str]:
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


_embedding_model = None
_embedding_model_name = ""


def _semantic_similarity(left: str, right: str) -> float:
    model_name = os.environ.get("CRAYOTTER_STORY_EMBEDDING_MODEL", "").strip()
    if model_name:
        score = _embedding_similarity(left, right, model_name)
        if score is not None:
            return score
    return _weighted_ngram_similarity(left, right)


def _embedding_similarity(left: str, right: str, model_name: str) -> float | None:
    global _embedding_model, _embedding_model_name
    try:
        if _embedding_model is None or _embedding_model_name != model_name:
            from FlagEmbedding import FlagAutoModel

            _embedding_model = FlagAutoModel.from_finetuned(model_name, use_fp16=False)
            _embedding_model_name = model_name
        vectors = _embedding_model.encode([left, right])
        first, second = vectors[0], vectors[1]
        numerator = float(first @ second)
        denominator = math.sqrt(float(first @ first)) * math.sqrt(
            float(second @ second)
        )
        return (
            round(max(0.0, min(1.0, numerator / denominator)), 4)
            if denominator
            else 0.0
        )
    except Exception:
        return None


def _weighted_ngram_similarity(left: str, right: str) -> float:
    a = _semantic_tokens(left)
    b = _semantic_tokens(right)
    if not a or not b:
        return 0.0
    a_counts, b_counts = Counter(a), Counter(b)
    intersection = sum((a_counts & b_counts).values())
    union = sum((a_counts | b_counts).values())
    jaccard = intersection / union if union else 0.0
    lexical = _lexical_similarity(left, right)
    return round(min(1.0, 0.65 * jaccard + 0.35 * lexical), 4)


def _semantic_tokens(text: str) -> list[str]:
    raw = str(text or "").lower()
    words = re.findall(r"[a-z0-9]+", raw)
    han = "".join(re.findall(r"[\u4e00-\u9fff]", raw))
    words.extend(han[index : index + 2] for index in range(max(0, len(han) - 1)))
    return words


def _sequence_similarity(left: Iterable[str], right: Iterable[str]) -> float:
    a = [_normalize(item) for item in left if _normalize(item)]
    b = [_normalize(item) for item in right if _normalize(item)]
    if not a or not b:
        return 0.0
    matrix = [[_weighted_ngram_similarity(x, y) for y in b] for x in a]
    # Monotonic dynamic-programming alignment rewards the same causal order.
    dp = [[0.0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            dp[i][j] = max(
                dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1] + matrix[i - 1][j - 1]
            )
    return round(min(1.0, dp[-1][-1] / max(len(a), len(b))), 4)


def _relationship_similarity(package: StoryPackage, reference_dna: StoryDNA) -> float:
    generated = {
        _normalize(item.relation) for item in package.relationships if item.relation
    }
    reference = {
        _normalize(item.relation)
        for item in reference_dna.relationships
        if item.relation
    }
    if not generated or not reference:
        return 0.0
    return round(len(generated & reference) / len(generated | reference), 4)


def _overall_score(scores: dict[str, float]) -> float:
    weights = {
        "dialogue": 0.28,
        "semantic": 0.26,
        "structure": 0.22,
        "relationship": 0.14,
        "setting": 0.10,
    }
    weighted = sum(scores.get(name, 0.0) * weight for name, weight in weights.items())
    strongest = max(scores.values(), default=0.0)
    return round(min(1.0, 0.75 * weighted + 0.25 * strongest), 4)


def _risk(score: float, *, medium: float, high: float) -> str:
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"
