"""The bundle interface contract (SPEC §6). Downstream Atlas depends on this shape — treat as a
stable API. bundle.md is the primary ingestion surface (D1); this JSON is the precise backing
record. Bump SCHEMA_VERSION on any breaking change to the field shape.

1.0 is the fresh multi-source contract: `uploader_id` (string, all sources) replaces the
bilibili-only integer `uploader_mid`; `Platform` includes `youtube.com`; `TranscriptSource`
renames bilibili's `"ai-zh"` provenance label to the source-neutral `"auto-sub"`, with language
now tracked separately on `Transcript.language`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

Platform = Literal["bilibili.com", "bilibili.tv", "youtube.com"]
TranscriptSource = Literal["human-sub", "auto-sub", "whisper"]


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
    language: str | None = None
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


class Stats(BaseModel):
    """Engagement metrics — a POINT-IN-TIME SNAPSHOT as of the enclosing record's `fetched_at`,
    NOT stable identity. All fields volatile (generally grow, but can be reset/hidden) and
    per-platform partial: bilibili fills all; YouTube fills view_count/like_count only.
    Null-tolerate every field; never compare across bundles without accounting for each
    record's `fetched_at`."""
    view_count:     int | None = None
    like_count:     int | None = None
    coin_count:     int | None = None   # bilibili 硬币; YouTube null
    favorite_count: int | None = None   # bilibili 收藏; YouTube null
    share_count:    int | None = None   # bilibili 分享; YouTube null
    reply_count:    int | None = None   # top-level comments (bilibili stat.reply); YT null
    danmaku_count:  int | None = None   # bilibili danmaku total (--danmaku opt-in signal); YT null


class ProbeResult(BaseModel):
    """Cheap pre-flight metadata (no transcript/frames): lets Atlas estimate workload before
    committing to the full pipeline."""

    schema_version: str = SCHEMA_VERSION
    platform: Platform
    id: str
    title: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None
    description: str | None = None
    duration_s: int | None = None
    published_at: str | None = None  # ISO 8601, video's publish time (SPEC: bilibili pubdate)
    thumbnail_url: str | None = None  # intrinsic/descriptive, NOT part of `stats`
    fetched_at: str | None = None  # ISO 8601 UTC, e.g. "2026-06-28T12:00:00Z" -- when probe ran
    stats: Stats | None = None
    parts: int
    part_durations_s: list[int | None] = Field(default_factory=list)


class DanmakuLine(BaseModel):
    """A representative danmaku = one cluster head. `text` is VERBATIM (never paraphrased/translated/
    decoded). `count` = near-identical variants collapsed into this representative within the window
    (1 = singleton). Lines within a window are ordered CHRONOLOGICALLY by content time, never by
    count."""

    text: str
    count: int = 1


class DanmakuWindow(BaseModel):
    """Danmaku pinned to content-time window [start, end) seconds (aligned to bundle chunks).
    `total` = raw danmaku in the window BEFORE clustering — the density signal that survives even if
    `lines` is capped in bundle.md."""

    start: float
    end: float
    total: int
    lines: list[DanmakuLine] = Field(default_factory=list)


class Danmaku(BaseModel):
    """Crowd danmaku track — a faithful MIRROR of the audience stream, NOT interpreted content.
    LOWER AUTHORITY than `transcript`: crowd expression (jokes, memes, sarcasm, frequently 'wrong'
    claims); never treat as authoritative. bilibili-only; present only when `--danmaku` was requested
    on a supporting platform (else Bundle.danmaku is null). bundle.json is the COMPLETE record;
    bundle.md may cap per window with a '+N more' marker (read this JSON for the full set)."""

    source_total: int | None = None  # platform-reported total (stat.danmaku)
    fetched_total: int  # how many actually pulled (endpoint may sample)
    sampled: bool  # fetched_total < source_total -> a sample, not a census
    model: str | None = None  # the LLM that produced this representation (provenance)
    windows: list[DanmakuWindow] = Field(default_factory=list)


class Bundle(BaseModel):
    schema_version: str = SCHEMA_VERSION
    platform: Platform
    id: str
    part: int
    url: str
    title: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None
    description: str | None = None
    duration_s: int | None = None
    published_at: str | None = None  # ISO 8601, video's publish time (SPEC: bilibili pubdate)
    thumbnail_url: str | None = None  # intrinsic/descriptive, NOT part of `stats`
    fetched_at: str  # ISO 8601 UTC, e.g. "2026-06-28T12:00:00Z"
    stats: Stats | None = None
    transcript: Transcript
    frames: list[Frame] = Field(default_factory=list)
    danmaku: Danmaku | None = None
    meta: Meta
