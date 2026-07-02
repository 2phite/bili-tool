import json

from harvest.config import Settings
from harvest.merge import (
    DANMAKU_MD_CAP,
    build_bundle,
    chunk,
    chunk_boundaries,
    render_markdown,
    write_bundle,
)
from harvest.providers.base import Canonical, SourceMetadata
from harvest.schema import Bundle, Danmaku, DanmakuLine, DanmakuWindow, Frame, Meta, Segment, Transcript


def _seg(start, end, text="x"):
    return Segment(start=start, end=end, text=text)


def _canonical():
    return Canonical(
        platform="bilibili.com", id="BV1", part=1, url="https://b/video/BV1"
    )


def _transcript():
    return Transcript(source="whisper", source_reason="test", segments=[])


def _settings():
    s = Settings()
    s.tool_version = "t"
    return s


def _bundle_with_frame(rel_path):
    return Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[Frame(ts=2.0, path=rel_path, phash="abc", ocr="SLIDE TEXT", caption="a chart")],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )


def test_no_frame_images_omits_png_nulls_path_keeps_caption(tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG fake")
    bundle = _bundle_with_frame("frames/00002000.png")
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(
        bundle, settings,
        frame_sources={"frames/00002000.png": src},
        frame_images=False,
    )

    assert not (out / "frames").exists()  # D8: no PNGs shipped
    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["path"] is None  # path nulled
    assert data["frames"][0]["phash"] == "abc"  # but record retained
    md = (out / "bundle.md").read_text(encoding="utf-8")
    assert "SLIDE TEXT" in md and "a chart" in md  # caption text still in the product


def test_default_ships_png_and_keeps_path(tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG fake")
    bundle = _bundle_with_frame("frames/00002000.png")
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={"frames/00002000.png": src})

    assert (out / "frames" / "00002000.png").exists()
    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["path"] == "frames/00002000.png"


def test_wallclock_chunking_buckets_by_window():
    segs = [_seg(0, 5), _seg(10, 15), _seg(80, 85), _seg(160, 165)]
    chunks = chunk(segs, [], window_s=75.0, duration_s=200.0)
    # boundaries at 0,75,150 -> segments grouped [0,10],[80],[160]
    assert [c.start for c in chunks] == [0.0, 75.0, 150.0]
    assert [len(c.segments) for c in chunks] == [2, 1, 1]


def test_segment_assigned_whole_by_start_never_split():
    # A segment straddling a boundary belongs entirely to the chunk of its start.
    segs = [_seg(70, 80)]  # starts in [0,75)
    chunks = chunk(segs, [], window_s=75.0, duration_s=150.0)
    assert len(chunks) == 1
    assert chunks[0].start == 0.0
    assert chunks[0].segments[0].start == 70


def test_frame_boundaries_used_when_frames_present():
    frames = [Frame(ts=12.0, phash="a"), Frame(ts=30.0, phash="b")]
    segs = [_seg(0, 5), _seg(20, 25), _seg(40, 45)]
    chunks = chunk(segs, frames, window_s=75.0, duration_s=60.0)
    # boundaries {0,12,30}: seg@0 -> chunk0; seg@20 -> chunk12; seg@40 -> chunk30
    assert [c.start for c in chunks] == [0.0, 12.0, 30.0]
    assert [s.segments and s.segments[0].start for s in chunks] == [0, 20, 40]
    # frame goes into the chunk it opens
    assert chunks[1].frames[0].ts == 12.0


def test_empty_chunks_dropped():
    frames = [Frame(ts=100.0, phash="a")]  # boundary with nothing before it but seg later
    segs = [_seg(105, 110)]
    chunks = chunk(segs, frames, window_s=75.0, duration_s=120.0)
    assert all(c.segments or c.frames for c in chunks)


def test_chunk_boundaries_extraction_leaves_chunk_output_identical_fixed_window():
    # Regression guard: chunk() must produce byte-identical output before/after the
    # chunk_boundaries() extraction, for the fixed wall-clock fallback branch (no frames).
    segs = [_seg(0, 5), _seg(10, 15), _seg(80, 85), _seg(160, 165)]
    boundaries = chunk_boundaries(segs, [], window_s=75.0, duration_s=200.0)
    assert boundaries == [0.0, 75.0, 150.0]
    chunks = chunk(segs, [], window_s=75.0, duration_s=200.0)
    assert [c.start for c in chunks] == boundaries[: len(chunks)]
    assert [c.start for c in chunks] == [0.0, 75.0, 150.0]
    assert [len(c.segments) for c in chunks] == [2, 1, 1]


def test_chunk_boundaries_extraction_leaves_chunk_output_identical_frames_present():
    # Same regression guard for the frames-present branch.
    frames = [Frame(ts=12.0, phash="a"), Frame(ts=30.0, phash="b")]
    segs = [_seg(0, 5), _seg(20, 25), _seg(40, 45)]
    boundaries = chunk_boundaries(segs, frames, window_s=75.0, duration_s=60.0)
    assert boundaries == [0.0, 12.0, 30.0]
    chunks = chunk(segs, frames, window_s=75.0, duration_s=60.0)
    assert [c.start for c in chunks] == [0.0, 12.0, 30.0]
    assert chunks[1].frames[0].ts == 12.0


def test_build_bundle_consumes_source_metadata():
    meta = SourceMetadata(
        platform="bilibili.com", id="BV1", title="View Title", uploader="View Owner",
        uploader_id="999", description="View description.", duration_s=123,
        published_at="2024-06-28T16:00:00+08:00", parts=1, part_durations_s=[123],
        thumbnail_url="http://x/thumb.jpg", view_count=1000, like_count=200,
        coin_count=30, favorite_count=40, share_count=10, reply_count=20, danmaku_count=50,
    )
    bundle = build_bundle(_canonical(), meta, _transcript(), [], _settings())
    assert bundle.title == "View Title"
    assert bundle.uploader == "View Owner"
    assert bundle.duration_s == 123
    assert bundle.uploader_id == "999"
    assert bundle.description == "View description."
    assert bundle.published_at == "2024-06-28T16:00:00+08:00"
    assert bundle.thumbnail_url == "http://x/thumb.jpg"
    assert bundle.stats.view_count == 1000
    assert bundle.stats.like_count == 200
    assert bundle.stats.coin_count == 30
    assert bundle.stats.favorite_count == 40
    assert bundle.stats.share_count == 10
    assert bundle.stats.reply_count == 20
    assert bundle.stats.danmaku_count == 50


def test_build_bundle_with_no_stats_or_thumbnail_leaves_them_none():
    meta = SourceMetadata(
        platform="youtube.com", id="x", title="yt title", uploader="yt uploader",
        uploader_id=None, description="yt description", duration_s=456,
        published_at=None, parts=1, part_durations_s=[456],
    )
    bundle = build_bundle(_canonical(), meta, _transcript(), [], _settings())
    assert bundle.thumbnail_url is None
    # stats is still populated (view_count/like_count fields exist even if all None) --
    # mirrors probe's Stats(...) construction so Bundle and ProbeResult behave consistently.
    assert bundle.stats is not None
    assert bundle.stats.view_count is None
    assert bundle.stats.danmaku_count is None


def test_build_bundle_with_missing_metadata_fields_is_none():
    meta = SourceMetadata(
        platform="youtube.com", id="x", title="yt title", uploader="yt uploader",
        uploader_id=None, description="yt description", duration_s=456,
        published_at=None, parts=1, part_durations_s=[456],
    )
    bundle = build_bundle(_canonical(), meta, _transcript(), [], _settings())
    assert bundle.title == "yt title"
    assert bundle.uploader == "yt uploader"
    assert bundle.duration_s == 456
    assert bundle.uploader_id is None
    assert bundle.description == "yt description"
    assert bundle.published_at is None


def test_render_markdown_emits_thumbnail_url_in_header():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        thumbnail_url="http://x/thumb.jpg",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert "thumbnail_url: http://x/thumb.jpg" in md.splitlines()


def test_render_markdown_thumbnail_url_empty_when_none():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert "thumbnail_url: " in md.splitlines()


def test_render_markdown_emits_uploader_id_and_description_section():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        uploader_id="42",
        description="Line one.\n\nLine two with a URL: https://example.com",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert "uploader_id: 42" in md
    lines = md.splitlines()
    uploader_idx = next(i for i, l in enumerate(lines) if l.startswith("uploader:"))
    mid_idx = next(i for i, l in enumerate(lines) if l.startswith("uploader_id:"))
    assert mid_idx == uploader_idx + 1
    assert "## Description" in md
    assert "Line one." in md
    assert "Line two with a URL: https://example.com" in md
    # Description section appears after the H1 and before the transcript body.
    h1_idx = next(i for i, l in enumerate(lines) if l.startswith("# "))
    desc_idx = next(i for i, l in enumerate(lines) if l == "## Description")
    transcript_idx = next(i for i, l in enumerate(lines) if l.startswith("## ["))
    assert h1_idx < desc_idx < transcript_idx


def test_render_markdown_emits_published_at_after_duration():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        published_at="2024-06-28T16:00:00+08:00",
        duration_s=60,
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()
    assert "published_at: 2024-06-28T16:00:00+08:00" in lines
    duration_idx = next(i for i, l in enumerate(lines) if l.startswith("duration:"))
    published_idx = next(i for i, l in enumerate(lines) if l.startswith("published_at:"))
    assert published_idx == duration_idx + 1


def test_render_markdown_published_at_empty_when_none():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert "published_at: " in md.splitlines()


def test_render_markdown_omits_description_section_when_none():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert "## Description" not in md
    assert "uploader_id: " in md  # still emitted, empty


def test_render_markdown_description_with_literal_dashes_and_hash_line_is_safe():
    """A description containing a line that is exactly `---` (a YAML/HR delimiter) and a line
    starting with `#` (looks like a heading) must not corrupt the frontmatter block or be
    misread as a new section. The frontmatter stays exactly the intended scalar lines, and the
    description text appears verbatim, in full, inside the ## Description body."""
    description = "Intro line.\n---\n# Not a real heading\nTrailing line."
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        uploader_id="42",
        description=description,
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()

    # Frontmatter: a parser reads from the first `---` to the *next* `---` it meets. That
    # block must be exactly the intended scalar lines -- the adversarial `---` inside the
    # description (which necessarily comes later, after the H1 and `## Description` heading)
    # must not be mistaken for the close fence, and nothing from the description must leak
    # into it.
    assert lines[0] == "---"
    close_idx = lines.index("---", 1)
    frontmatter = lines[1:close_idx]
    assert frontmatter == [
        "platform: bilibili.com",
        "id: BV1",
        "part: 1",
        "url: https://b/video/BV1",
        "title: My Title",
        "uploader: My Uploader",
        "uploader_id: 42",
        "thumbnail_url: ",
        "duration: ?",
        "published_at: ",
        "fetched_at: 2026-06-29T00:00:00Z",
        "transcript_source: whisper (test)",
        "vision_model: none",
        "tool_version: t",
    ]

    # The description text appears verbatim (each of its lines, in order) inside the
    # ## Description section, after the H1.
    h1_idx = next(i for i, l in enumerate(lines) if l.startswith("# "))
    desc_idx = next(i for i, l in enumerate(lines) if l == "## Description")
    assert h1_idx < desc_idx
    assert "Intro line." in md
    assert "Not a real heading" in md
    assert "Trailing line." in md
    body_after_desc = "\n".join(lines[desc_idx:])
    assert description in body_after_desc or all(
        part in body_after_desc for part in description.split("\n")
    )


def test_bundle_json_roundtrips_new_fields(tmp_path):
    from harvest.schema import Stats

    bundle = _bundle_with_frame(None)
    bundle.uploader_id = "123"
    bundle.description = "desc text"
    bundle.thumbnail_url = "http://x/thumb.jpg"
    bundle.stats = Stats(view_count=1000, danmaku_count=50)
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={}, frame_images=False)

    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["uploader_id"] == "123"
    assert data["description"] == "desc text"
    assert data["thumbnail_url"] == "http://x/thumb.jpg"
    assert data["stats"]["view_count"] == 1000
    assert data["stats"]["danmaku_count"] == 50


def _bundle_with_danmaku(danmaku):
    return Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        danmaku=danmaku,
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )


def test_render_markdown_omits_danmaku_section_when_none():
    bundle = _bundle_with_danmaku(None)
    md = render_markdown(bundle, _settings())
    assert "## Danmaku" not in md


def test_render_markdown_omits_danmaku_section_when_no_windows():
    bundle = _bundle_with_danmaku(
        Danmaku(source_total=0, fetched_total=0, sampled=False, model=None, windows=[])
    )
    md = render_markdown(bundle, _settings())
    assert "## Danmaku" not in md


def test_render_markdown_emits_danmaku_section_with_provenance_and_counts():
    dm = Danmaku(
        source_total=100,
        fetched_total=80,
        sampled=True,
        model="qwen-test",
        windows=[
            DanmakuWindow(
                start=0.0, end=75.0, total=5,
                lines=[
                    DanmakuLine(text="草", count=3),
                    DanmakuLine(text="singleton comment", count=1),
                ],
            ),
        ],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, _settings())

    assert "## Danmaku" in md
    # Provenance line conveys: lower authority, fetched/source totals, sampled, model.
    assert "lower authority than transcript" in md
    assert "fetched 80" in md
    assert "of 100" in md
    assert "sampled" in md
    assert "qwen-test" in md
    # Per-window header uses mm:ss + total (raw, pre-clustering) count.
    assert "### [00:00] (5 danmaku)" in md
    # count > 1 -> ×count shown; count == 1 -> omitted.
    assert "「草」 ×3" in md
    assert "「singleton comment」" in md
    assert "「singleton comment」 ×1" not in md


def test_render_markdown_danmaku_caps_lines_with_overflow_marker():
    lines = [DanmakuLine(text=f"line{i}", count=1) for i in range(DANMAKU_MD_CAP + 7)]
    dm = Danmaku(
        source_total=None, fetched_total=len(lines), sampled=False, model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, _settings())

    for i in range(DANMAKU_MD_CAP):
        assert f"「line{i}」" in md
    for i in range(DANMAKU_MD_CAP, DANMAKU_MD_CAP + 7):
        assert f"「line{i}」" not in md
    assert "﹢7 more — see bundle.json" in md


def test_render_markdown_danmaku_under_cap_has_no_overflow_marker():
    lines = [DanmakuLine(text=f"line{i}", count=1) for i in range(3)]
    dm = Danmaku(
        source_total=None, fetched_total=3, sampled=False, model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=3, lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, _settings())
    assert "more — see bundle.json" not in md


def test_bundle_json_roundtrip_carries_complete_uncapped_danmaku(tmp_path):
    lines = [DanmakuLine(text=f"line{i}", count=1) for i in range(DANMAKU_MD_CAP + 10)]
    dm = Danmaku(
        source_total=200, fetched_total=len(lines), sampled=True, model="m1",
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={}, frame_images=False)

    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert len(data["danmaku"]["windows"][0]["lines"]) == DANMAKU_MD_CAP + 10  # uncapped

    roundtripped = Bundle.model_validate_json((out / "bundle.json").read_text(encoding="utf-8"))
    assert len(roundtripped.danmaku.windows[0].lines) == DANMAKU_MD_CAP + 10
    assert roundtripped.danmaku.source_total == 200
    assert roundtripped.danmaku.sampled is True
    assert roundtripped.danmaku.model == "m1"

    md = (out / "bundle.md").read_text(encoding="utf-8")
    assert "﹢10 more — see bundle.json" in md  # bundle.md stays capped
