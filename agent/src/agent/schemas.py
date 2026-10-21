"""Trusted runtime context and strict model output contracts."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class UserContext:
    user_id: str
    tenant_id: str
    note_id: str = ''

    def __post_init__(self):
        if not self.user_id.strip() or not self.tenant_id.strip():
            raise ValueError('user_id 和 tenant_id 不能为空')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Material(StrictModel):
    title: str
    course: str
    text: str


class Chunk(StrictModel):
    text: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)


class SearchSource(StrictModel):
    title: str
    url: str
    site_name: str = ''


class SearchResult(StrictModel):
    summary: str
    sources: list[SearchSource]


class Finding(StrictModel):
    point: str = Field(min_length=1)
    source_title: str
    source_url: str
    confidence: Literal['high', 'mid', 'low']


class Answer(StrictModel):
    answer: str = Field(min_length=1)
    followups: list[str]


class Point(StrictModel):
    text: str = Field(min_length=1)
    ts: str | None = None


class Definition(Point):
    term: str = Field(min_length=1)


class Notes(StrictModel):
    theme: str = Field(min_length=1)
    points: list[Point] = Field(max_length=8)
    definitions: list[Definition] = Field(max_length=6)
    emphasized: list[str] = Field(max_length=4)


QuestionType = Literal['single', 'multiple', 'indefinite', 'judge', 'blank', 'answer']
QUESTION_TYPES = ('single', 'multiple', 'indefinite', 'judge', 'blank', 'answer')


class Question(StrictModel):
    type: QuestionType
    difficulty: Literal['简单', '中等', '困难']
    stem: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    answer: str = Field(min_length=1)
    analysis: str = Field(min_length=1)
    cite: str = Field(min_length=1)
    knowledge: str = Field(min_length=2, max_length=6)
