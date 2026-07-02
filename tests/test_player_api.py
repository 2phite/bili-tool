import gzip
import json
import zlib
from pathlib import Path

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

_DANMAKU_FIXTURE = (
    Path(__file__).parent / "fixtures" / "bilibili" / "sample_danmaku.xml"
).read_bytes()


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
            "pic": "http://i0.hdslb.com/bfs/archive/thumb.jpg",
            "stat": {
                "view": 1000, "danmaku": 50, "like": 200, "coin": 30,
                "favorite": 40, "share": 10, "reply": 20,
            },
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
    # thumbnail + engagement stats, from the SAME response (no second network call)
    assert view.pic == "http://i0.hdslb.com/bfs/archive/thumb.jpg"
    assert view.view_count == 1000
    assert view.danmaku_count == 50
    assert view.like_count == 200
    assert view.coin_count == 30
    assert view.favorite_count == 40
    assert view.share_count == 10
    assert view.reply_count == 20


def test_fetch_view_stat_and_pic_absent_are_none():
    """A response with no `stat`/`pic` keys must degrade to None, not raise."""
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
    assert view.pic is None
    assert view.view_count is None
    assert view.danmaku_count is None
    assert view.like_count is None
    assert view.coin_count is None
    assert view.favorite_count is None
    assert view.share_count is None
    assert view.reply_count is None


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


# --- danmaku acquisition (Task 2) ---


def _danmaku_url(cid: int) -> str:
    from harvest.player_api import _API_DANMAKU_XML

    return _API_DANMAKU_XML.format(cid=cid)


def test_decode_plain_utf8_bytes():
    from harvest.player_api import _decode

    assert _decode(_DANMAKU_FIXTURE) == _DANMAKU_FIXTURE.decode("utf-8")


def test_decode_gzip_bytes():
    from harvest.player_api import _decode

    body = gzip.compress(_DANMAKU_FIXTURE)
    assert _decode(body) == _DANMAKU_FIXTURE.decode("utf-8")


def test_decode_zlib_wrapped_bytes():
    from harvest.player_api import _decode

    body = zlib.compress(_DANMAKU_FIXTURE)
    assert _decode(body) == _DANMAKU_FIXTURE.decode("utf-8")


def test_decode_raw_deflate_bytes():
    from harvest.player_api import _decode

    co = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    body = co.compress(_DANMAKU_FIXTURE) + co.flush()
    assert _decode(body) == _DANMAKU_FIXTURE.decode("utf-8")


def test_decode_raises_on_unrecognized_bytes():
    from harvest.player_api import _decode

    with pytest.raises(ValueError):
        _decode(b"\xff\xfe\x00\x01not text or a known codec")


def test_parse_danmaku_xml_sorts_and_skips_malformed():
    from harvest.player_api import RawDanmaku, _parse_danmaku_xml

    records = _parse_danmaku_xml(_DANMAKU_FIXTURE.decode("utf-8"))

    # malformed <d p="1,2"> (too few comma fields) is skipped -> 5 of 6 survive
    assert len(records) == 5
    assert all(isinstance(r, RawDanmaku) for r in records)
    # sorted ascending by content_ts (fixture has 12.5 listed before 3.2)
    assert [r.content_ts for r in records] == [3.2, 3.2, 8.0, 8.0, 12.5]
    # only content_ts + text retained; duplicate/near-duplicate content preserved verbatim
    assert records[0].text == "弹幕测试"
    assert records[1].text == "弹幕测试"
    assert records[-1].text == "你好世界"


def test_fetch_danmaku_parses_records_and_source_total():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42, "cid": 100, "title": "T", "desc": "d", "duration": 600,
            "owner": {"mid": 7, "name": "U"},
            "stat": {"danmaku": 9999},
            "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 600}],
        },
    }
    opener = _FakeOpener({
        _view_url(canonical): view_payload,
        _danmaku_url(100): _DANMAKU_FIXTURE,
    })

    result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert result.source_total == 9999
    assert result.fetched_total == 5
    assert len(result.records) == 5
    assert [r.content_ts for r in result.records] == [3.2, 3.2, 8.0, 8.0, 12.5]
    # 5 fetched vs a much larger platform-reported total -> sampled
    assert result.sampled is True


def test_fetch_danmaku_not_sampled_when_fetched_meets_source_total():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42, "cid": 100, "title": "T", "desc": "d", "duration": 600,
            "owner": {"mid": 7, "name": "U"},
            "stat": {"danmaku": 5},
            "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 600}],
        },
    }
    opener = _FakeOpener({
        _view_url(canonical): view_payload,
        _danmaku_url(100): _DANMAKU_FIXTURE,
    })

    result = fetch_danmaku(canonical, Settings(), opener=opener)
    assert result.fetched_total == 5
    assert result.source_total == 5
    assert result.sampled is False


def test_fetch_danmaku_accepts_prefetched_view_and_skips_view_get():
    """Task 4: when `view` is supplied, fetch_danmaku must NOT hit the view endpoint at all."""
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view = ViewData(aid=42, cid=100, danmaku_count=5, pages=[ViewPage(part=1, cid=100)])
    opener = _FakeOpener({_danmaku_url(100): _DANMAKU_FIXTURE})

    result = fetch_danmaku(canonical, Settings(), opener=opener, view=view)

    assert _view_url(canonical) not in opener.requested_urls
    assert opener.requested_urls == [_danmaku_url(100)]
    assert result.source_total == 5
    assert result.fetched_total == 5


def test_fetch_danmaku_no_cid_returns_empty_result_not_raise():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=9)  # out of range -> no cid
    view = ViewData(aid=42, cid=100, danmaku_count=5, pages=[ViewPage(part=1, cid=100)])
    opener = _FakeOpener({})

    result = fetch_danmaku(canonical, Settings(), opener=opener, view=view)

    assert result.records == []
    assert result.fetched_total == 0
    assert result.sampled is False
    assert result.source_total == 5
    assert opener.requested_urls == []


def test_fetch_danmaku_view_error_returns_empty_result_not_raise():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    opener = _FakeOpener({_view_url(canonical): {"code": -400, "message": "nope"}})

    result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert result.records == []
    assert result.fetched_total == 0
    assert result.source_total is None
    assert result.sampled is False

