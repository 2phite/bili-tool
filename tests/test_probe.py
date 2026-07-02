import pytest

from harvest.config import Settings
from harvest.player_api import ViewError
from harvest.probe import probe
from harvest.resolve import Canonical
from harvest.schema import ProbeResult
from tests.test_player_api import _FakeOpener, _view_url


def _canonical(platform: str = "bilibili.com", part: int = 1) -> Canonical:
    return Canonical(platform, "BV1", part, f"https://b/video/BV1?p={part}")


def test_probe_maps_full_fixture_to_probe_result():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "My Video",
            "desc": "A description.",
            "duration": 600,
            "pubdate": 1719561600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"page": 1, "cid": 100, "part": "Part One", "duration": 300},
                {"page": 2, "cid": 200, "part": "Part Two", "duration": 300},
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})

    result = probe(canonical, Settings(), opener=opener)

    assert isinstance(result, ProbeResult)
    assert result.platform == "bilibili.com"
    assert result.id == "BV1"
    assert result.title == "My Video"
    assert result.uploader == "Uploader"
    assert result.uploader_mid == 7
    assert result.description == "A description."
    assert result.duration_s == 600
    assert result.published_at == "2024-06-28T16:00:00+08:00"
    assert result.parts == 2
    assert result.part_durations_s == [300, 300]


def test_probe_published_at_none_when_pubdate_missing():
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

    result = probe(canonical, Settings(), opener=opener)

    assert result.published_at is None


def test_probe_single_part_synthesizes_one_page():
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

    result = probe(canonical, Settings(), opener=opener)

    assert result.parts == 1
    assert result.part_durations_s == [120]


def test_probe_tv_platform_raises_before_any_fetch():
    canonical = _canonical(platform="bilibili.tv")
    opener = _FakeOpener({})

    with pytest.raises(ValueError):
        probe(canonical, Settings(), opener=opener)

    assert opener.requested_urls == []


def test_probe_propagates_view_error():
    canonical = _canonical()
    payload = {"code": -400, "message": "request error"}
    opener = _FakeOpener({_view_url(canonical): payload})

    with pytest.raises(ViewError):
        probe(canonical, Settings(), opener=opener)
