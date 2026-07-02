import json

import pytest

from harvest.config import Settings
from harvest.player_api import (
    ViewData,
    ViewError,
    ViewPage,
    cid_for_part,
    fetch_view,
    published_at_iso,
    select_zh_subtitle,
)
from harvest.resolve import Canonical


def test_cid_for_part_matches_page_number():
    view = ViewData(aid=1, cid=100, pages=[
        ViewPage(part=1, cid=100), ViewPage(part=2, cid=200), ViewPage(part=3, cid=300)])
    assert cid_for_part(view, 2) == 200


def test_cid_for_part_falls_back_to_index_when_no_page_field():
    # If entries lack a meaningful `part` field, positional index is the backstop.
    view = ViewData(aid=1, pages=[ViewPage(part=0, cid=100), ViewPage(part=0, cid=200)])
    assert cid_for_part(view, 2) == 200


def test_cid_for_part_single_page_uses_top_level_cid():
    view = ViewData(aid=1, cid=555, pages=[])
    assert cid_for_part(view, 1) == 555


def test_cid_for_part_out_of_range_is_none():
    view = ViewData(aid=1, pages=[ViewPage(part=1, cid=100)])
    assert cid_for_part(view, 9) is None


def test_select_zh_prefers_original_transcription_over_translation():
    subs = [
        {"lan": "ai-en", "ai_type": 1, "subtitle_url": "//x/en"},
        {"lan": "ai-zh", "ai_type": 0, "subtitle_url": "//x/zh"},
        {"lan": "ai-ja", "ai_type": 1, "subtitle_url": "//x/ja"},
    ]
    pick = select_zh_subtitle(subs)
    assert pick["lan"] == "ai-zh"


def test_select_zh_prefers_human_zh_keys_in_order():
    subs = [
        {"lan": "zh-CN", "ai_type": 0},
        {"lan": "zh-Hans", "ai_type": 0},
    ]
    assert select_zh_subtitle(subs)["lan"] == "zh-Hans"


def test_select_zh_none_when_only_foreign_tracks():
    subs = [{"lan": "ai-en", "ai_type": 1}, {"lan": "ai-ja", "ai_type": 1}]
    assert select_zh_subtitle(subs) is None


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Minimal stand-in for urllib's opener: maps URL -> JSON-serializable payload."""

    def __init__(self, responses: dict):
        self._responses = responses
        self.requested_urls: list[str] = []

    def open(self, url: str, timeout: int = 60):
        self.requested_urls.append(url)
        body = self._responses[url]
        payload = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
        return _FakeResponse(payload)


def _canonical(part: int = 1) -> Canonical:
    return Canonical("bilibili.com", "BV1", part, f"https://b/video/BV1?p={part}")


def _view_url(canonical: Canonical) -> str:
    from harvest.player_api import _API_VIEW

    return _API_VIEW.format(bvid=canonical.id)


def test_fetch_view_parses_full_fixture():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "My Video",
            "desc": "A description.",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"page": 1, "cid": 100, "part": "Part One", "duration": 300},
                {"page": 2, "cid": 200, "part": "Part Two", "duration": 300},
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)

    assert isinstance(view, ViewData)
    assert view.aid == 42
    assert view.cid == 100
    assert view.title == "My Video"
    assert view.desc == "A description."
    assert view.duration == 600
    assert view.owner_mid == 7
    assert view.owner_name == "Uploader"
    assert len(view.pages) == 2
    assert view.pages[0].part == 1
    assert view.pages[0].cid == 100
    assert view.pages[0].title == "Part One"
    assert view.pages[0].duration == 300
    assert view.pages[1].part == 2
    assert view.pages[1].cid == 200
    # exactly one GET for the view endpoint
    assert opener.requested_urls == [_view_url(canonical)]


def test_fetch_view_synthesizes_single_page_when_pages_empty():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "",
            "duration": 120,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)

    assert len(view.pages) == 1
    page = view.pages[0]
    assert page.part == 1
    assert page.cid == 555
    assert page.title is None
    assert page.duration == 120


def test_published_at_iso_converts_epoch_to_cst_offset():
    # 2024-06-28T08:00:00Z -> 2024-06-28T16:00:00+08:00 in bilibili's native CST.
    assert published_at_iso(1719561600) == "2024-06-28T16:00:00+08:00"


def test_published_at_iso_none_for_none():
    assert published_at_iso(None) is None


def test_published_at_iso_none_for_zero():
    assert published_at_iso(0) is None


def test_fetch_view_parses_pubdate():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "d",
            "duration": 120,
            "pubdate": 1719561600,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.pubdate == 1719561600


def test_fetch_view_missing_pubdate_is_none():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "d",
            "duration": 120,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.pubdate is None


def test_fetch_view_empty_desc_becomes_none():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "",
            "duration": 120,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [{"page": 1, "cid": 555, "part": "Solo", "duration": 120}],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.desc is None


def test_fetch_view_raises_view_error_on_malformed_pages_entry():
    """An upstream `pages[]` entry missing `page` makes `ViewPage(part=...)` get `None`,
    which pydantic rejects (`part: int`). That parse failure must surface as `ViewError`
    (chained), never a bare `pydantic.ValidationError` escaping into callers that only
    catch `ViewError` (process_part, _run_probe)."""
    from pydantic import ValidationError

    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "Malformed",
            "desc": "d",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"cid": 100, "part": "Part One", "duration": 300},  # "page" key missing
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})

    with pytest.raises(ViewError) as excinfo:
        fetch_view(canonical, Settings(), opener=opener)

    assert not isinstance(excinfo.value, ValidationError)
    assert excinfo.value.__cause__ is not None


def test_part_segments_returns_none_on_malformed_view_pages_entry():
    """The same malformed response must degrade `part_segments` to None, not raise."""
    from harvest.player_api import part_segments

    canonical = _canonical(part=1)
    payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "Malformed",
            "desc": "d",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"cid": 100, "part": "Part One", "duration": 300},  # "page" key missing
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    result = part_segments(canonical, Settings(), opener=opener)
    assert result is None


def test_fetch_view_raises_view_error_on_nonzero_code():
    canonical = _canonical()
    payload = {"code": -400, "message": "request error"}
    opener = _FakeOpener({_view_url(canonical): payload})
    with pytest.raises(ViewError):
        fetch_view(canonical, Settings(), opener=opener)


def test_cid_for_part_via_view_data_page_number_match():
    view = ViewData(
        aid=1,
        cid=100,
        title=None,
        desc=None,
        duration=10,
        owner_mid=1,
        owner_name="x",
        pages=[
            ViewPage(part=1, cid=100, title=None, duration=5),
            ViewPage(part=2, cid=200, title=None, duration=5),
        ],
    )
    assert cid_for_part(view, 2) == 200


def test_part_segments_returns_none_when_view_missing_aid_and_cid():
    """Malformed view response (missing aid, and a page with cid absent) must degrade to
    None, matching the pre-refactor behavior, not raise a pydantic ValidationError."""
    from harvest.player_api import part_segments

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            # aid intentionally absent
            "title": "Malformed",
            "desc": "d",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"page": 1, "cid": None, "part": "Part One", "duration": 300},
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): view_payload})
    result = part_segments(canonical, Settings(), opener=opener)
    assert result is None


def test_part_segments_fetches_view_exactly_once():
    """End-to-end: part_segments must resolve cid via a single fetch_view call, not a raw GET."""
    from harvest.player_api import _API_PLAYER, part_segments

    canonical = _canonical(part=2)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "My Video",
            "desc": "d",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"page": 1, "cid": 100, "part": "Part One", "duration": 300},
                {"page": 2, "cid": 200, "part": "Part Two", "duration": 300},
            ],
        },
    }
    player_url = _API_PLAYER.format(aid=42, cid=200, bvid="BV1")
    player_payload = {"code": 0, "data": {"subtitle": {"subtitles": []}}}
    opener = _FakeOpener(
        {
            _view_url(canonical): view_payload,
            player_url: player_payload,
        }
    )
    result = part_segments(canonical, Settings(), opener=opener)
    assert result is None  # no zh subtitle present
    # exactly one GET to the view endpoint
    view_hits = [u for u in opener.requested_urls if u == _view_url(canonical)]
    assert len(view_hits) == 1


def test_part_segments_accepts_prefetched_view_and_skips_fetch_view():
    """Task 4: when `view` is supplied, part_segments must NOT hit the view endpoint at all."""
    from harvest.player_api import _API_PLAYER, ViewData, ViewPage, part_segments

    canonical = _canonical(part=2)
    view = ViewData(
        aid=42,
        cid=100,
        pages=[
            ViewPage(part=1, cid=100, duration=300),
            ViewPage(part=2, cid=200, duration=300),
        ],
    )
    player_url = _API_PLAYER.format(aid=42, cid=200, bvid="BV1")
    player_payload = {"code": 0, "data": {"subtitle": {"subtitles": []}}}
    opener = _FakeOpener({player_url: player_payload})

    result = part_segments(canonical, Settings(), opener=opener, view=view)

    assert result is None  # no zh subtitle present
    assert _view_url(canonical) not in opener.requested_urls
    assert opener.requested_urls == [player_url]

