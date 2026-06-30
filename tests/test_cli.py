import json

import pytest

from bili_tool.cli import apply_overrides, main, parse_args
from bili_tool.config import Settings


def _settings():
    s = Settings()
    return s


def test_dedup_threshold_overrides_phash_setting():
    args = parse_args(["ingest", "https://b/video/BV1", "--dedup-threshold", "16"])
    s = _settings()
    warnings = apply_overrides(s, args)
    assert s.phash_dedup_threshold == 16
    assert warnings == []


def test_scene_threshold_is_deprecated_noop_with_warning():
    args = parse_args(["ingest", "https://b/video/BV1", "--scene-threshold", "27"])
    s = _settings()
    before = s.phash_dedup_threshold
    warnings = apply_overrides(s, args)
    # retired: it must NOT change any live lever, and it must warn.
    assert s.phash_dedup_threshold == before
    assert any("scene-threshold" in w for w in warnings)


def test_no_overrides_leaves_defaults_and_is_silent():
    args = parse_args(["ingest", "https://b/video/BV1"])
    s = _settings()
    warnings = apply_overrides(s, args)
    assert warnings == []
    assert s.phash_dedup_threshold == Settings().phash_dedup_threshold


def test_out_override_sets_out_dir():
    from pathlib import Path

    args = parse_args(["ingest", "https://b/video/BV1", "--out", "somewhere"])
    s = _settings()
    apply_overrides(s, args)
    assert s.out_dir == Path("somewhere")


def test_ingest_subcommand_parses_url_and_all_flags():
    args = parse_args(
        [
            "ingest",
            "https://b/video/BV1",
            "--part",
            "2",
            "--all-parts",
            "--force-whisper",
            "--robust",
            "--no-vision",
            "--dedup-threshold",
            "12",
            "--scene-threshold",
            "5",
            "--out",
            "outdir",
            "--no-frame-images",
        ]
    )
    assert args.command == "ingest"
    assert args.url == "https://b/video/BV1"
    assert args.part == 2
    assert args.all_parts is True
    assert args.force_whisper is True
    assert args.robust is True
    assert args.no_vision is True
    assert args.dedup_threshold == 12
    assert args.scene_threshold == 5
    assert args.out == "outdir"
    assert args.no_frame_images is True


def test_probe_subcommand_parses_url_only():
    args = parse_args(["probe", "https://b/video/BV1"])
    assert args.command == "probe"
    assert args.url == "https://b/video/BV1"


def test_bare_url_without_subcommand_is_a_system_exit():
    with pytest.raises(SystemExit):
        parse_args(["https://b/video/BV1"])


def test_no_args_at_all_is_a_system_exit():
    with pytest.raises(SystemExit):
        parse_args([])


def test_probe_path_prints_only_json_to_stdout(monkeypatch, capsys):
    from bili_tool import cli
    from bili_tool.resolve import Canonical
    from bili_tool.schema import ProbeResult

    monkeypatch.setattr(
        cli, "resolve", lambda url: Canonical("bilibili.com", "BV1", 1, url)
    )

    def fake_probe(canonical, settings, **kwargs):
        return ProbeResult(
            platform="bilibili.com",
            id="BV1",
            title="T",
            uploader="U",
            uploader_mid=1,
            description="d",
            duration_s=100,
            parts=1,
            part_durations_s=[100],
        )

    monkeypatch.setattr(cli, "probe", fake_probe)

    rc = main(["probe", "https://b/video/BV1"])
    assert rc == 0

    captured = capsys.readouterr()
    # stdout must be exactly the JSON (one line), nothing else
    assert json.loads(captured.out) == fake_probe(None, None).model_dump()
    assert captured.out.strip().count("\n") == 0


def test_probe_malformed_url_exits_1_with_error_on_stderr_and_clean_stdout(capsys):
    """resolve() raises ValueError for a malformed URL; `probe` must catch it like any other
    pre-flight failure: exit 1, `error: ...` on stderr, stdout stays empty (PROTOCOL.md)."""
    rc = main(["probe", "https://example.com/not-bilibili"])
    assert rc == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_ingest_malformed_url_exits_nonzero_with_error_on_stderr(capsys):
    """Same contract for `ingest`: a bad URL must not raise an uncaught traceback."""
    rc = main(["ingest", "https://example.com/not-bilibili"])
    assert rc != 0

    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")


def test_ingest_enumerates_parts_from_view_pages(monkeypatch):
    """--all-parts on a .com URL must derive its part count from `view.pages`, not yt-dlp."""
    from bili_tool import cli
    from bili_tool.player_api import ViewData, ViewPage
    from bili_tool.resolve import Canonical

    monkeypatch.setattr(
        cli, "resolve", lambda url: Canonical("bilibili.com", "BV1", 1, url)
    )

    view = ViewData(
        aid=1,
        cid=100,
        pages=[ViewPage(part=1, cid=100), ViewPage(part=2, cid=200), ViewPage(part=3, cid=300)],
    )
    monkeypatch.setattr(cli, "fetch_view", lambda canonical, settings: view)

    seen_parts = []

    def fake_process_part(canonical, settings, args, view=None):
        seen_parts.append(canonical.part)

    monkeypatch.setattr(cli, "process_part", fake_process_part)

    rc = main(["ingest", "https://b/video/BV1", "--all-parts", "--no-vision"])
    assert rc == 0
    assert seen_parts == [1, 2, 3]


def test_process_part_fetches_view_once_and_reuses_it_for_subtitle_path(monkeypatch):
    """One view fetch per part: process_part must fetch view once and pass it through to both
    build_bundle and the subtitle-cid path (decide_transcript), never re-fetching."""
    from bili_tool import cli
    from bili_tool.player_api import ViewData, ViewPage
    from bili_tool.resolve import Canonical
    from bili_tool.schema import Transcript

    canonical = Canonical("bilibili.com", "BV1", 1, "https://b/video/BV1")
    view = ViewData(aid=1, cid=100, pages=[ViewPage(part=1, cid=100)])

    fetch_calls = []

    def fake_fetch_view(c, settings):
        fetch_calls.append(c)
        return view

    monkeypatch.setattr(cli, "fetch_view", fake_fetch_view)
    monkeypatch.setattr(cli, "extract_info", lambda url, settings: {"title": "t", "duration": 1})

    decide_transcript_views = []

    def fake_decide_transcript(info, canonical, settings, args, view=None):
        decide_transcript_views.append(view)
        return Transcript(source="whisper", source_reason="x", language="zh", segments=[])

    monkeypatch.setattr(cli, "decide_transcript", fake_decide_transcript)

    build_bundle_views = []

    def fake_build_bundle(canonical, info, transcript, frames, settings, *, view=None, vision_model=None):
        build_bundle_views.append(view)
        from bili_tool.schema import Bundle, Meta

        return Bundle(
            platform="bilibili.com",
            id="BV1",
            part=1,
            url=canonical.url,
            title="t",
            uploader=None,
            duration_s=None,
            fetched_at="2026-06-30T00:00:00Z",
            transcript=transcript,
            frames=[],
            meta=Meta(cookies_used=False, referer_used=True, tool_version="0"),
        )

    monkeypatch.setattr(cli, "build_bundle", fake_build_bundle)
    monkeypatch.setattr(cli, "write_bundle", lambda *a, **k: "out/path")

    args = parse_args(["ingest", "https://b/video/BV1", "--no-vision"])
    cli.process_part(canonical, _settings(), args)

    assert len(fetch_calls) == 1  # fetched exactly once
    assert build_bundle_views == [view]
    assert decide_transcript_views == [view]
