"""Structured contracts for controlled and short-form editing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ShortFormClip(BaseModel):
    source_path: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    label: str = ""


class ShortFormNarration(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class ShortFormEditPlan(BaseModel):
    clips: list[ShortFormClip] = Field(min_length=3, max_length=4)
    narration: list[ShortFormNarration] = Field(min_length=1, max_length=3)
    voice: str = "Ethan"
    output_name: str = "short_form_promo"


class ShortFormExecutionError(RuntimeError):
    pass


class ControlledClip(BaseModel):
    scene_id: str = ""
    source_path: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    label: str = ""


class ControlledNarration(BaseModel):
    scene_id: str = ""
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class ControlledEditPlan(BaseModel):
    clips: list[ControlledClip] = Field(min_length=1, max_length=24)
    narration: list[ControlledNarration] = Field(default_factory=list, max_length=24)
    voice: str = "Ethan"
    output_name: str = "output_final"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"


class ControlledNarrationPlan(BaseModel):
    narration: list[ControlledNarration] = Field(default_factory=list, max_length=24)
    voice: str = "Ethan"
