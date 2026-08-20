from enum import StrEnum

from pydantic import BaseModel, Field


class MediaKind(StrEnum):
    MOVIE = "MOVIE"
    EPISODE = "EPISODE"
    UNKNOWN = "UNKNOWN"


class MediaIdentity(BaseModel):
    kind: MediaKind
    series_title: str | None = None
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=0)
    episode_end: int | None = Field(default=None, ge=0)
    year: int | None = Field(default=None, ge=1800, le=2200)
    normalized_title: str


class MediaMatch(BaseModel):
    accepted: bool
    automatic: bool
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
