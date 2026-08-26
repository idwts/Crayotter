from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from script.orchestration.models import utc_now_iso

StoryLanguage = Literal["zh", "en", "es"]
StoryContentType = Literal[
    "short_drama", "short_video", "advertisement", "motion_comic"
]
StoryStatus = Literal["DRAFT", "GENERATED", "REVIEWED", "APPROVED", "SUPERSEDED"]


class StoryJobConfig(BaseModel):
    """User-controlled inputs for one story-development job."""

    content_type: StoryContentType = "short_drama"
    source_paths: list[str] = Field(default_factory=list)
    source_language: str = "auto"
    target_markets: list[str] = Field(default_factory=lambda: ["CN"])
    target_languages: list[StoryLanguage] = Field(default_factory=lambda: ["zh"])
    genre: str = ""
    themes: list[str] = Field(default_factory=list)
    episode_count: int = Field(default=3, ge=1, le=100)
    episode_duration_seconds: int = Field(default=90, ge=15, le=3600)
    adaptation_requirements: str = ""
    platform_constraints: str = ""
    reference_rights_confirmed: bool = False
    similarity_scope: Literal["references", "project_library"] = "references"
    generate_localizations: bool = True

    @field_validator("source_paths", "target_markets", "themes", mode="before")
    @classmethod
    def _coerce_text_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]  # type: ignore[arg-type]

    @field_validator("target_languages", mode="before")
    @classmethod
    def _coerce_languages(cls, value: object) -> list[str]:
        if value is None:
            return ["zh"]
        if isinstance(value, str):
            value = value.split(",")
        seen: list[str] = []
        aliases = {
            "zh-cn": "zh",
            "chinese": "zh",
            "中文": "zh",
            "english": "en",
            "英语": "en",
            "spanish": "es",
            "西语": "es",
            "西班牙语": "es",
        }
        for raw in value:  # type: ignore[union-attr]
            language = aliases.get(str(raw).strip().lower(), str(raw).strip().lower())
            if language in {"zh", "en", "es"} and language not in seen:
                seen.append(language)
        return seen or ["zh"]


class EvidenceRef(BaseModel):
    source_id: str
    start: int = Field(default=0, ge=0)
    end: int = Field(default=0, ge=0)
    excerpt: str = ""

    @model_validator(mode="after")
    def _ordered_span(self) -> "EvidenceRef":
        if self.end < self.start:
            self.start, self.end = self.end, self.start
        return self


class SourceChunk(BaseModel):
    chunk_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    # Source expression is used in-memory for analysis and similarity only;
    # immutable story versions persist locators/hashes, not a second full copy.
    text: str = Field(default="", exclude=True, repr=False)


class SourceWork(BaseModel):
    source_id: str
    title: str
    path: str = ""
    format: str = "text"
    language: str = "auto"
    sha256: str
    character_count: int = Field(ge=0)
    text: str = Field(default="", exclude=True, repr=False)
    chunks: list[SourceChunk] = Field(default_factory=list)


class StoryCharacter(BaseModel):
    character_id: str
    name: str
    role: str = ""
    archetype: str = ""
    goal: str = ""
    motivation: str = ""
    flaw: str = ""
    leverage: str = ""
    boundary: str = ""
    arc: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)


class StoryRelationship(BaseModel):
    relationship_id: str
    source_character_id: str
    target_character_id: str
    relation: str
    tension: str = ""
    changes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class StoryBeat(BaseModel):
    beat_id: str
    sequence: int = Field(ge=1)
    label: str
    event: str
    conflict: str = ""
    emotion: str = ""
    intensity: int = Field(default=50, ge=0, le=100)
    reveal: str = ""
    payoff: str = ""
    reusable_mechanism: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)


class EpisodePlan(BaseModel):
    episode_id: str
    number: int = Field(ge=1)
    title: str = ""
    incoming_state: str = ""
    objective: str = ""
    obstacles: list[str] = Field(default_factory=list)
    escalation: str = ""
    turn: str = ""
    payoff: str = ""
    outgoing_pressure: str = ""
    beat_ids: list[str] = Field(default_factory=list)


class StoryDNA(BaseModel):
    title: str = ""
    logline: str = ""
    premise: str = ""
    genre: str = ""
    target_audience: str = ""
    themes: list[str] = Field(default_factory=list)
    world_rules: list[str] = Field(default_factory=list)
    conflict_engine: str = ""
    emotional_promise: str = ""
    characters: list[StoryCharacter] = Field(default_factory=list)
    relationships: list[StoryRelationship] = Field(default_factory=list)
    beats: list[StoryBeat] = Field(default_factory=list)
    episodes: list[EpisodePlan] = Field(default_factory=list)
    reversals: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    motifs: list[str] = Field(default_factory=list)
    reusable_mechanisms: list[str] = Field(default_factory=list)


class StoryDirection(BaseModel):
    direction_id: str
    title: str
    logline: str
    protagonist_strategy: str
    opposition_mechanism: str
    recurring_reward: str
    cost: str
    differentiation: str
    market_fit: str = ""
    recommended: bool = False


class ScreenplayScene(BaseModel):
    scene_id: str
    number: int = Field(ge=1)
    heading: str
    location: str = ""
    time_of_day: str = ""
    purpose: str = ""
    action: str = ""
    dialogue: list[dict[str, str]] = Field(default_factory=list)
    emotional_start: str = ""
    emotional_end: str = ""
    hook: str = ""
    video_prompt: str = ""


class ScreenplayEpisode(BaseModel):
    episode_id: str
    number: int = Field(ge=1)
    title: str
    synopsis: str = ""
    target_duration_seconds: int = Field(default=90, ge=1)
    scenes: list[ScreenplayScene] = Field(default_factory=list)


class StoryPackage(BaseModel):
    title: str
    logline: str = ""
    synopsis: str = ""
    world: str = ""
    character_bible: list[StoryCharacter] = Field(default_factory=list)
    relationships: list[StoryRelationship] = Field(default_factory=list)
    episode_outline: list[EpisodePlan] = Field(default_factory=list)
    episodes: list[ScreenplayEpisode] = Field(default_factory=list)
    video_prompt_package: list[dict[str, str]] = Field(default_factory=list)


class LocalizedVariant(BaseModel):
    language: StoryLanguage
    market: str
    title: str
    logline: str = ""
    synopsis: str = ""
    cultural_changes: list[str] = Field(default_factory=list)
    character_names: dict[str, str] = Field(default_factory=dict)
    episodes: list[ScreenplayEpisode] = Field(default_factory=list)


class SimilarityFinding(BaseModel):
    finding_id: str
    signal: Literal["dialogue", "semantic", "structure", "relationship", "setting"]
    risk: Literal["low", "medium", "high"]
    score: float = Field(ge=0.0, le=1.0)
    output_locator: str
    output_excerpt: str
    source_id: str
    source_locator: str
    source_excerpt: str
    explanation: str
    recommendation: str


class SimilarityReport(BaseModel):
    corpus_scope: str = "references"
    corpus_hash: str = ""
    overall_risk: Literal["low", "medium", "high"] = "low"
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0)
    signal_scores: dict[str, float] = Field(default_factory=dict)
    findings: list[SimilarityFinding] = Field(default_factory=list)
    disclaimer: str = "This report is a similarity-risk indicator, not a legal determination or clearance."


class StoryProvenance(BaseModel):
    source_hashes: dict[str, str] = Field(default_factory=dict)
    model_name: str = ""
    schema_version: str = "story-document-v1"
    generated_at: str = Field(default_factory=utc_now_iso)


class StoryDocument(BaseModel):
    schema_version: str = "story-document-v1"
    version: str = "v001"
    status: StoryStatus = "DRAFT"
    job_id: str = ""
    project_id: str = ""
    request: StoryJobConfig = Field(default_factory=StoryJobConfig)
    sources: list[SourceWork] = Field(default_factory=list)
    dna: StoryDNA = Field(default_factory=StoryDNA)
    directions: list[StoryDirection] = Field(default_factory=list)
    selected_direction_id: str = ""
    package: StoryPackage | None = None
    localizations: list[LocalizedVariant] = Field(default_factory=list)
    similarity_report: SimilarityReport | None = None
    provenance: StoryProvenance = Field(default_factory=StoryProvenance)
    previous_version: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
