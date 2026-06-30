import json

from bili_tool.config import Settings
from bili_tool.merge import build_bundle, chunk, render_markdown, write_bundle
from bili_tool.player_api import ViewData
from bili_tool.resolve import Canonical
from bili_tool.schema import Bundle, Frame, Meta, Segment, Transcript


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


def test_build_bundle_with_view_present_wins_over_ytdlp():
    view = ViewData(
        aid=1,
        cid=2,
        title="View Title",
        desc="View description.",
        duration=123,
        owner_mid=999,
        owner_name="View Owner",
        pages=[],
    )
    info = {
        "title": "ytdlp title",
        "uploader": "ytdlp uploader",
        "duration": 456,
        "description": "ytdlp description",
    }
    bundle = build_bundle(
        _canonical(), info, _transcript(), [], _settings(), view=view
    )
    assert bundle.title == "View Title"
    assert bundle.uploader == "View Owner"
    assert bundle.duration_s == 123
    assert bundle.uploader_mid == 999
    assert bundle.description == "View description."


def test_build_bundle_with_view_none_falls_back_to_ytdlp():
    info = {
        "title": "ytdlp title",
        "uploader": "ytdlp uploader",
        "duration": 456,
        "description": "ytdlp description",
    }
    bundle = build_bundle(
        _canonical(), info, _transcript(), [], _settings(), view=None
    )
    assert bundle.title == "ytdlp title"
    assert bundle.uploader == "ytdlp uploader"
    assert bundle.duration_s == 456
    assert bundle.uploader_mid is None
    assert bundle.description == "ytdlp description"


def test_render_markdown_emits_uploader_mid_and_description_section():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        uploader_mid=42,
        description="Line one.\n\nLine two with a URL: https://example.com",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert "uploader_mid: 42" in md
    lines = md.splitlines()
    uploader_idx = next(i for i, l in enumerate(lines) if l.startswith("uploader:"))
    mid_idx = next(i for i, l in enumerate(lines) if l.startswith("uploader_mid:"))
    assert mid_idx == uploader_idx + 1
    assert "## Description" in md
    assert "Line one." in md
    assert "Line two with a URL: https://example.com" in md
    # Description section appears after the H1 and before the transcript body.
    h1_idx = next(i for i, l in enumerate(lines) if l.startswith("# "))
    desc_idx = next(i for i, l in enumerate(lines) if l == "## Description")
    transcript_idx = next(i for i, l in enumerate(lines) if l.startswith("## ["))
    assert h1_idx < desc_idx < transcript_idx


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
    assert "uploader_mid: " in md  # still emitted, empty


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
        uploader_mid=42,
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
        "uploader_mid: 42",
        "duration: ?",
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
    bundle = _bundle_with_frame(None)
    bundle.uploader_mid = 123
    bundle.description = "desc text"
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={}, frame_images=False)

    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["uploader_mid"] == 123
    assert data["description"] == "desc text"
