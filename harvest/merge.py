"""Timeline alignment -> bundle.json + bundle.md (SPEC §5 step 5, §6, D1/D2/D3/D8).

bundle.md is the product (D1): a provenance header (D2) + slide/wall-clock chunks (D3), each
"what was on screen + the speech while it was up". bundle.json is the precise backing record.
"""

from __future__ import annotations

import shutil
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .providers.base import Canonical, SourceMetadata
from .schema import Bundle, Frame, Meta, Segment, Transcript


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


@dataclass
class Chunk:
    start: float
    frames: list[Frame]
    segments: list[Segment]


def chunk(
    segments: list[Segment],
    frames: list[Frame],
    *,
    window_s: float,
    duration_s: float | None,
) -> list[Chunk]:
    """D3: boundaries = deduped frame timestamps when present, else a fixed wall-clock window.
    Segments are assigned whole by their start timestamp; never split across a boundary."""
    if frames:
        boundaries = sorted({0.0, *(f.ts for f in frames)})
    else:
        end = duration_s or (max((s.end for s in segments), default=0.0))
        boundaries = [i * window_s for i in range(int(end // window_s) + 1)] or [0.0]

    chunks = [Chunk(start=b, frames=[], segments=[]) for b in boundaries]
    for f in frames:
        chunks[bisect_right(boundaries, f.ts) - 1].frames.append(f)
    for seg in segments:
        chunks[bisect_right(boundaries, seg.start) - 1].segments.append(seg)
    return [c for c in chunks if c.segments or c.frames]


def build_bundle(
    canonical: Canonical,
    meta: SourceMetadata,
    transcript: Transcript,
    frames: list[Frame],
    settings: Settings,
    *,
    vision_model: str | None = None,
) -> Bundle:
    return Bundle(
        platform=canonical.platform, id=canonical.id, part=canonical.part, url=canonical.url,
        title=meta.title, uploader=meta.uploader, uploader_id=meta.uploader_id,
        description=meta.description, duration_s=meta.duration_s, published_at=meta.published_at,
        fetched_at=iso_now(), transcript=transcript, frames=frames,
        meta=Meta(
            cookies_used=bool(settings.sessdata or settings.cookies_browser),
            referer_used=(canonical.platform == "bilibili.com"),
            vision_model=vision_model, tool_version=settings.tool_version,
        ),
    )


def render_markdown(bundle: Bundle, settings: Settings) -> str:
    t = bundle.transcript
    dur = _mmss(bundle.duration_s) if bundle.duration_s else "?"
    lines = [
        "---",
        f"platform: {bundle.platform}",
        f"id: {bundle.id}",
        f"part: {bundle.part}",
        f"url: {bundle.url}",
        f"title: {bundle.title or ''}",
        f"uploader: {bundle.uploader or ''}",
        f"uploader_id: {bundle.uploader_id or ''}",
        f"duration: {dur}",
        f"published_at: {bundle.published_at or ''}",
        f"fetched_at: {bundle.fetched_at}",
        f"transcript_source: {t.source} ({t.source_reason})",
        f"vision_model: {bundle.meta.vision_model or 'none'}",
        f"tool_version: {bundle.meta.tool_version}",
        "---",
        "",
        f"# {bundle.title or bundle.id}",
        "",
    ]

    if bundle.description:
        lines.append("## Description")
        lines.append("")
        lines.append(bundle.description)
        lines.append("")

    if not t.segments and not bundle.frames:
        lines.append("_(no transcript yet — Whisper pending)_")
        return "\n".join(lines) + "\n"

    for ch in chunk(
        t.segments, bundle.frames, window_s=settings.chunk_window_s, duration_s=bundle.duration_s
    ):
        lines.append(f"## [{_mmss(ch.start)}]")
        for fr in ch.frames:
            if fr.ocr:
                lines.append(f"**slide (OCR):** {fr.ocr}")
            if fr.caption:
                lines.append(f"**slide (figure):** {fr.caption}")
        if ch.frames:
            lines.append("")
        text = "".join(s.text for s in ch.segments).strip()
        if text:
            lines.append(text)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_bundle(
    bundle: Bundle,
    settings: Settings,
    *,
    frame_sources: dict[str, Path] | None = None,
    frame_images: bool = True,
) -> Path:
    """Write the self-contained delivery dir out/<id>-p<part>/ (D8).

    frame_images=False (--no-frame-images, D8): omit PNGs from out/ and null each frame's `path`
    in bundle.json — the record keeps phash/ts/caption/ocr and the markdown keeps the caption
    text, so only the QA images are dropped.
    """
    out = settings.out_dir / f"{bundle.id}-p{bundle.part}"
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = out / "frames"
    # Rebuild frames/ from scratch so the delivered dir matches bundle.json exactly (D8) and
    # stale PNGs from prior runs (e.g. a different dedup threshold, or a prior images-on run)
    # never linger.
    if frames_dir.exists():
        shutil.rmtree(frames_dir)

    if frame_images:
        frames_dir.mkdir(parents=True, exist_ok=True)
        if frame_sources:
            for fr in bundle.frames:
                if fr.path and fr.path in frame_sources:
                    shutil.copy2(frame_sources[fr.path], out / fr.path)
    else:
        for fr in bundle.frames:
            fr.path = None

    (out / "bundle.json").write_text(
        bundle.model_dump_json(indent=2), encoding="utf-8"
    )
    (out / "bundle.md").write_text(render_markdown(bundle, settings), encoding="utf-8")
    return out
