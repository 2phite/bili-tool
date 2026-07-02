import json

import pytest

from harvest.cli import apply_overrides, main, parse_args
from harvest.config import Settings


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


def test_ingest_accepts_lang_flag():
    args = parse_args(["ingest", "https://youtu.be/dQw4w9WgXcQ", "--lang", "es"])
    assert args.lang == "es"


def test_ingest_lang_defaults_to_none():
    args = parse_args(["ingest", "https://youtu.be/dQw4w9WgXcQ"])
    assert args.lang is None


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
    from harvest import cli
    from harvest.resolve import Canonical
    from harvest.schema import ProbeResult

    monkeypatch.setattr(
        cli, "resolve", lambda url: Canonical("bilibili.com", "BV1", 1, url)
    )

    def fake_probe(canonical, settings, **kwargs):
        return ProbeResult(
            platform="bilibili.com",
            id="BV1",
            title="T",
            uploader="U",
            uploader_id="1",
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


def test_decide_transcript_youtube_reuses_human_sub_no_quality_gate(monkeypatch):
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata, SubtitleOutcome
    from harvest.schema import Segment

    canonical = Canonical("youtube.com", "dQw4w9WgXcQ", 1, "https://youtu.be/dQw4w9WgXcQ")
    meta = SourceMetadata(platform="youtube.com", id="dQw4w9WgXcQ", title="T", uploader="C",
                          uploader_id="UCx", description="d", duration_s=100,
                          published_at="2009-10-25T06:57:33Z", parts=1, part_durations_s=[100])

    class _FakeYT:
        def fetch_subtitle(self, c, settings, m, *, pinned_lang=None):
            assert pinned_lang is None
            return SubtitleOutcome(
                accepted=True, source="human-sub",
                source_reason="human-sub (exact-key match: en)", language="en",
                segments=[Segment(start=0.0, end=1.0, text="hi")], quality_gate=None)

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeYT())
    args = parse_args(["ingest", "https://youtu.be/dQw4w9WgXcQ"])
    t = cli.decide_transcript(canonical, meta, _settings(), args)
    assert t.source == "human-sub"
    assert t.language == "en"
    assert t.quality_gate is None
    assert t.segments[0].text == "hi"


def test_decide_transcript_youtube_falls_back_to_whisper(monkeypatch):
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata

    canonical = Canonical("youtube.com", "x", 1, "https://youtu.be/x")
    meta = SourceMetadata(platform="youtube.com", id="x", title=None, uploader=None,
                          uploader_id=None, description=None, duration_s=10,
                          published_at=None, parts=1, part_durations_s=[10])

    class _FakeYT:
        def fetch_subtitle(self, c, settings, m, *, pinned_lang=None):
            return None

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeYT())
    captured = {}

    def fake_whisper(canonical, settings, args, *, reason, gate=None, lang=None):
        from harvest.schema import Transcript
        captured["lang"] = lang
        return Transcript(source="whisper", source_reason=reason, language=lang, segments=[])

    monkeypatch.setattr(cli, "_whisper", fake_whisper)
    args = parse_args(["ingest", "https://youtu.be/x"])
    t = cli.decide_transcript(canonical, meta, _settings(), args)
    assert t.source == "whisper"
    assert captured["lang"] is None


def test_decide_transcript_bilibili_accepted_outcome_keeps_gate(monkeypatch):
    # Agnostic path: provider returns an accepted outcome (gate passed) -> Transcript mirrors it.
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata, SubtitleOutcome
    from harvest.schema import QualityGate, Segment

    canonical = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    meta = SourceMetadata(platform="bilibili.com", id="BV1", title="T", uploader="U",
                          uploader_id="7", description="d", duration_s=100, published_at=None,
                          parts=1, part_durations_s=[100])
    gate = QualityGate(passed=True, punct_density=1.0, dup_ratio=0.0, nonzh_ratio=0.0, cps=5.0)

    class _FakeBili:
        def fetch_subtitle(self, c, settings, m, *, pinned_lang=None):
            return SubtitleOutcome(accepted=True, source="auto-sub",
                                   source_reason="auto-sub (quality-gate: passed)", language="zh",
                                   segments=[Segment(start=0.0, end=1.0, text="你好")], quality_gate=gate)

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeBili())
    t = cli.decide_transcript(canonical, meta, _settings(),
                              parse_args(["ingest", "https://www.bilibili.com/video/BV1"]))
    assert t.source == "auto-sub" and t.language == "zh" and t.quality_gate is gate


def test_decide_transcript_rejected_outcome_falls_back_to_whisper_with_gate(monkeypatch):
    # accepted=False must carry source_reason + failed gate into the Whisper transcript.
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata, SubtitleOutcome
    from harvest.schema import QualityGate

    canonical = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    meta = SourceMetadata(platform="bilibili.com", id="BV1", title="T", uploader="U",
                          uploader_id="7", description="d", duration_s=100, published_at=None,
                          parts=1, part_durations_s=[100])
    failed = QualityGate(passed=False, punct_density=0.0, dup_ratio=0.9, nonzh_ratio=0.0, cps=1.0)

    class _FakeBili:
        def fetch_subtitle(self, c, settings, m, *, pinned_lang=None):
            return SubtitleOutcome(accepted=False, source=None,
                                   source_reason="subtitle rejected (dup_ratio 0.90)", language=None,
                                   segments=[], quality_gate=failed)

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeBili())
    captured = {}

    def fake_whisper(canonical, settings, args, *, reason, gate=None, lang=None):
        from harvest.schema import Transcript
        captured.update(reason=reason, gate=gate, lang=lang)
        return Transcript(source="whisper", source_reason=reason, language=lang,
                          quality_gate=gate, segments=[])

    monkeypatch.setattr(cli, "_whisper", fake_whisper)
    t = cli.decide_transcript(canonical, meta, _settings(),
                              parse_args(["ingest", "https://www.bilibili.com/video/BV1"]))
    assert t.source == "whisper" and captured["gate"] is failed
    assert "rejected" in captured["reason"] and captured["lang"] == "zh"


def test_ingest_enumerates_parts_from_view_pages(monkeypatch):
    """--all-parts on a .com URL must derive its part count from the provider, not yt-dlp."""
    from harvest import cli
    from harvest.resolve import Canonical

    monkeypatch.setattr(
        cli, "resolve", lambda url: Canonical("bilibili.com", "BV1", 1, url)
    )

    class _FakeP:
        def enumerate_parts(self, canonical, settings):
            return 3

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeP())

    seen_parts = []

    def fake_process_part(canonical, settings, args, view=None):
        seen_parts.append(canonical.part)

    monkeypatch.setattr(cli, "process_part", fake_process_part)

    rc = main(["ingest", "https://b/video/BV1", "--all-parts", "--no-vision"])
    assert rc == 0
    assert seen_parts == [1, 2, 3]


def test_process_part_fetches_metadata_once_and_shares_it(monkeypatch):
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata
    from harvest.schema import Bundle, Meta, Transcript

    canonical = Canonical("youtube.com", "x", 1, "https://youtu.be/x")
    meta = SourceMetadata(platform="youtube.com", id="x", title="t", uploader=None,
                          uploader_id=None, description=None, duration_s=1,
                          published_at=None, parts=1, part_durations_s=[1])
    calls = {"meta": 0}

    class _FakeP:
        def fetch_metadata(self, c, settings):
            calls["meta"] += 1
            return meta

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeP())
    seen = {}

    def fake_decide(canonical, m, settings, args):
        seen["decide_meta"] = m
        return Transcript(source="whisper", source_reason="x", language=None, segments=[])

    monkeypatch.setattr(cli, "decide_transcript", fake_decide)

    def fake_build(canonical, m, transcript, frames, settings, *, vision_model=None):
        seen["build_meta"] = m
        return Bundle(platform="youtube.com", id="x", part=1, url=canonical.url, title="t",
                      fetched_at="2026-07-02T00:00:00Z", transcript=transcript, frames=[],
                      meta=Meta(cookies_used=False, referer_used=False, tool_version="0"))

    monkeypatch.setattr(cli, "build_bundle", fake_build)
    monkeypatch.setattr(cli, "write_bundle", lambda *a, **k: "out/path")

    args = parse_args(["ingest", "https://youtu.be/x", "--no-vision"])
    cli.process_part(canonical, _settings(), args)
    assert calls["meta"] == 1
    assert seen["decide_meta"] is meta and seen["build_meta"] is meta
