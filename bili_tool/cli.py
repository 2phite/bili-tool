"""CLI entry point (SPEC §9). Orchestrates resolve -> probe -> transcript -> frames -> merge.

Build state: spine (resolve, subtitle probe, bundle write) is live. Whisper/frames/vision call
their stage modules, which fail loud until their build step lands.
"""

from __future__ import annotations

import argparse
import hashlib
import sys

from .cache import fs_key, load_json, save_json
from .config import Settings
from .merge import build_bundle, write_bundle
from .parts import count_parts, part_url, run_parts, select_parts
from .quality import describe_failure, evaluate
from .resolve import Canonical, resolve
from .schema import Frame, Segment, Transcript
from .subtitles import extract_info, fetch_subtitle_segments, probe
from .transcribe import WHISPER_MODEL, download_audio, transcribe


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="bili-tool", description=__doc__)
    p.add_argument("url")
    p.add_argument("--part", type=int, default=None, help="1-based part index (default: from URL)")
    p.add_argument("--all-parts", action="store_true", help="loop every part (D12)")
    p.add_argument("--force-whisper", action="store_true", help="skip subtitle, always Whisper")
    p.add_argument("--robust", action="store_true", help="disable condition_on_previous_text")
    p.add_argument("--no-vision", action="store_true", help="skip frame captioning")
    p.add_argument(
        "--dedup-threshold", type=int, default=None,
        help="phash hamming distance to collapse near-duplicate frames (default: 10)",
    )
    p.add_argument(
        "--scene-threshold", type=float, default=None,
        help="DEPRECATED, ignored (D13 replaced scene-cut detection); use --dedup-threshold",
    )
    p.add_argument("--out", default=None, help="output root (default: ./out)")
    p.add_argument("--no-frame-images", action="store_true", help="omit PNGs from out/ (D8)")
    return p.parse_args(argv)


def apply_overrides(settings: Settings, args) -> list[str]:
    """Apply CLI levers onto Settings; return human-readable warnings for the caller to print."""
    warnings: list[str] = []
    if args.out:
        from pathlib import Path

        settings.out_dir = Path(args.out)
    if args.dedup_threshold is not None:
        settings.phash_dedup_threshold = args.dedup_threshold
    if getattr(args, "scene_threshold", None) is not None:
        warnings.append(
            "--scene-threshold is deprecated and ignored (D13 replaced scene-cut detection "
            "with periodic-sample + phash dedup); use --dedup-threshold instead."
        )
    return warnings


def decide_transcript(info: dict, canonical: Canonical, settings: Settings, args) -> Transcript:
    if args.force_whisper:
        return _whisper(canonical, settings, args, reason="forced via --force-whisper")

    sub = probe(info, canonical, settings, part1_segments=_part1_segments(canonical, settings))
    if not sub.found:
        return _whisper(
            canonical, settings, args, reason=f"no usable subtitle ({sub.reason})"
        )

    gate = evaluate(sub.segments, float(info.get("duration") or 0), settings.quality)
    if gate.passed:
        return Transcript(
            source=sub.source,  # type: ignore[arg-type]
            source_reason=f"{sub.source} (quality-gate: passed)",
            language="zh",
            quality_gate=gate,
            segments=sub.segments,
        )
    reason = f"subtitle rejected ({describe_failure(gate, settings.quality)})"
    return _whisper(canonical, settings, args, reason=reason, gate=gate)


def _part1_segments(canonical: Canonical, settings: Settings) -> list[Segment] | None:
    """D4 tier-2 input: part 1's subtitle for the #6357 identity check. Only fetched for part>1
    (an extra no-media probe). Best-effort — a failure here just skips tier-2, never aborts."""
    if canonical.part <= 1:
        return None
    try:
        p1_url = part_url(canonical.url, 1)
        p1 = Canonical(canonical.platform, canonical.id, 1, p1_url)
        p1_info = extract_info(p1_url, settings)
        return fetch_subtitle_segments(p1_info, p1, settings)
    except Exception:  # noqa: BLE001 - tier-2 is an optional guard, never fatal
        return None


def _whisper(canonical, settings, args, *, reason, gate=None) -> Transcript:
    # D6 transcript cache: keyed by identity + the params that change the output.
    key = fs_key(
        canonical.platform, canonical.id, canonical.part,
        stage="transcript", force_whisper=args.force_whisper,
        robust=args.robust, model=WHISPER_MODEL,
    )
    cached = load_json(settings.cache_dir, "transcript", key)
    if cached is not None:
        segments = [Segment(**s) for s in cached]
        print(
            f"[{canonical.id} p{canonical.part}] whisper "
            f"({len(segments)} seg, cached): {reason}"
        )
    else:
        print(f"[{canonical.id} p{canonical.part}] whisper: {reason} -> downloading audio...")
        audio = download_audio(canonical, settings)
        print(f"[{canonical.id} p{canonical.part}] transcribing {audio.name} ({WHISPER_MODEL})...")
        segments = transcribe(audio, robust=args.robust)
        save_json(settings.cache_dir, "transcript", key, [s.model_dump() for s in segments])
    return Transcript(
        source="whisper",
        source_reason=reason,
        language="zh",
        model=WHISPER_MODEL,
        robust=args.robust,
        quality_gate=gate,
        segments=segments,
    )


def process_part(canonical: Canonical, settings: Settings, args) -> None:
    info = extract_info(canonical.url, settings)
    transcript = decide_transcript(info, canonical, settings, args)

    frames = []
    frame_sources = {}
    vision_model = None
    if not args.no_vision:
        from .frames import download_video, extract_frames

        print(f"[{canonical.id} p{canonical.part}] preparing video + frames...")
        video = download_video(canonical, settings)
        frames, frame_sources = extract_frames(canonical, video, settings)
        print(f"[{canonical.id} p{canonical.part}] {len(frames)} frames after dedup")
        if frames:
            frames = _caption(canonical, frames, frame_sources, settings)
            vision_model = settings.lmstudio_vision_model

    bundle = build_bundle(
        canonical, info, transcript, frames, settings, view=None, vision_model=vision_model
    )
    out = write_bundle(
        bundle, settings, frame_sources=frame_sources, frame_images=not args.no_frame_images
    )
    n = len(transcript.segments)
    print(
        f"[{canonical.id} p{canonical.part}] {transcript.source}: "
        f"{n} segments, {len(frames)} frames -> {out}"
    )


def _caption(canonical, frames, frame_sources, settings):
    """Step 5: D7 projector probe + per-frame captioning, all-or-nothing caption cache (D10)."""
    from .vision import PROMPT_VERSION, caption_frames, verify_projector

    frameset = hashlib.sha1("".join(f.phash for f in frames).encode()).hexdigest()[:10]
    key = fs_key(
        canonical.platform, canonical.id, canonical.part,
        stage="captions", model=settings.lmstudio_vision_model,
        prompt=PROMPT_VERSION, frameset=frameset,
    )
    cached = load_json(settings.cache_dir, "captions", key)
    if cached is not None:
        print(f"[{canonical.id} p{canonical.part}] captions: cached ({len(cached)})")
        return [Frame(**f) for f in cached]

    verify_projector(settings)  # D7: hard-stop if the mmproj isn't really reading images
    print(f"[{canonical.id} p{canonical.part}] captioning {len(frames)} frames via "
          f"{settings.lmstudio_vision_model}...")
    captioned = caption_frames(frames, frame_sources, settings)
    save_json(settings.cache_dir, "captions", key, [f.model_dump() for f in captioned])
    return captioned


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # CJK titles on Windows consoles
    except Exception:
        pass

    args = parse_args(argv)
    settings = Settings.load()
    for w in apply_overrides(settings, args):
        print(f"[warn] {w}")

    canonical = resolve(args.url)

    # Enumerate parts once (cheap, no media), then run the single-part pipeline per selected
    # part with failure isolation (D12). Bilibili returns the whole set as a playlist for the
    # bare URL, so we always re-extract each part via its ?p=N URL inside process_part.
    info = extract_info(canonical.url, settings)
    total = count_parts(info)
    parts = select_parts(args, canonical, total=total)
    if len(parts) > 1:
        print(f"[{canonical.id}] {total} parts; running {len(parts)} -> {parts}")

    results = run_parts(
        canonical, parts, settings=settings, args=args, processor=process_part
    )

    failed = [r for r in results if not r.ok]
    if len(results) > 1 or failed:
        for r in results:
            status = "ok" if r.ok else f"FAILED ({r.error})"
            print(f"[{canonical.id} p{r.part}] {status}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
