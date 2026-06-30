"""The bundle interface contract (SPEC §6). Downstream Atlas depends on this shape — treat as a
stable API. bundle.md is the primary ingestion surface (D1); this JSON is the precise backing
record. Bump SCHEMA_VERSION on any breaking change to the field shape.

1.1 added `uploader_mid` and `description` (additive, nullable — backward compatible).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.1"

Platform = Literal["bilibili.com", "bilibili.tv"]
TranscriptSource = Literal["human-sub", "ai-zh", "whisper"]


class Segment(BaseModel):
    """A single timestamped transcript segment (whisper segment or subtitle cue)."""

    start: float
    end: float
    text: str


class QualityGate(BaseModel):
    """Quality-gate metrics + verdict for a subtitle source (SPEC §7, D5).

    Populated only when a subtitle track was evaluated; null when the source is whisper with no
    subtitle to gate (e.g. --force-whisper, or no sub returned).
    """

    passed: bool
    punct_density: float
    dup_ratio: float
    nonzh_ratio: float
    cps: float | None = None  # chars per second (length-vs-duration sanity, D5)


class Transcript(BaseModel):
    source: TranscriptSource  # provenance, load-bearing for downstream authority ranking (SPEC §6)
    source_reason: str  # human-readable decision, promoted into the bundle.md header (D2)
    language: str = "zh"
    model: str | None = None  # whisper model id; null when source is a subtitle
    robust: bool = False  # condition_on_previous_text disabled? (SPEC §7)
    quality_gate: QualityGate | None = None
    segments: list[Segment] = Field(default_factory=list)


class Frame(BaseModel):
    ts: float
    path: str | None = None  # relative to the bundle dir; null when --no-frame-images (D8)
    phash: str
    caption: str | None = None
    ocr: str | None = None


class Meta(BaseModel):
    cookies_used: bool  # "cookies supplied", NOT "server honored them" (D11)
    referer_used: bool
    vision_model: str | None = None
    tool_version: str


class Bundle(BaseModel):
    schema_version: str = SCHEMA_VERSION
    platform: Platform
    id: str
    part: int
    url: str
    title: str | None = None
    uploader: str | None = None
    uploader_mid: int | None = None
    description: str | None = None
    duration_s: int | None = None
    fetched_at: str  # ISO 8601 UTC, e.g. "2026-06-28T12:00:00Z"
    transcript: Transcript
    frames: list[Frame] = Field(default_factory=list)
    meta: Meta
