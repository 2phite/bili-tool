import pytest

from harvest.config import Settings
from harvest.player_api import ViewError
from harvest.probe import probe
from harvest.resolve import Canonical
from harvest.schema import ProbeResult
from tests.test_player_api import _FakeOpener, _view_url


def _canonical(platform: str = "bilibili.com", part: int = 1) -> Canonical:
    host = "www.bilibili.com" if platform == "bilibili.com" else "www.bilibili.tv"
    return Canonical(platform, "BV1", part, f"https://{host}/video/BV1?p={part}")


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

    result = probe(canonical, Settings(), opener=opener)

    assert isinstance(result, ProbeResult)
    assert result.platform == "bilibili.com"
    assert result.id == "BV1"
    assert result.title == "My Video"
    assert result.uploader == "Uploader"
    assert result.uploader_id == "7"
    assert result.description == "A description."
    assert result.duration_s == 600
    assert result.published_at == "2024-06-28T16:00:00+08:00"
    assert result.parts == 2
    assert result.part_durations_s == [300, 300]
    assert result.thumbnail_url == "http://i0.hdslb.com/bfs/archive/thumb.jpg"
    assert result.fetched_at is not None and result.fetched_at.endswith("Z")
    assert result.stats.view_count == 1000
    assert result.stats.danmaku_count == 50
    assert result.stats.like_count == 200
    assert result.stats.coin_count == 30
    assert result.stats.favorite_count == 40
    assert result.stats.share_count == 10
    assert result.stats.reply_count == 20


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


def test_schema_version_is_1_0():
    from harvest.schema import SCHEMA_VERSION
    assert SCHEMA_VERSION == "1.0"


def test_probe_result_uses_uploader_id_string():
    from harvest.schema import ProbeResult
    r = ProbeResult(platform="youtube.com", id="x", uploader_id="UCabc", parts=1)
    assert r.uploader_id == "UCabc"
    assert not hasattr(r, "uploader_mid")


def test_probe_youtube_delegates_to_provider(monkeypatch):
    import sys

    import harvest  # noqa: F401  ensure harvest.probe submodule is registered in sys.modules

    from harvest.providers.base import Canonical, SourceMetadata

    # NOTE: `harvest/__init__.py` does `from .probe import probe`, which shadows the
    # `harvest.probe` *submodule* with the `probe` *function* as a package attribute. So
    # `from harvest import probe` gets the function, not the module. Go via sys.modules to
    # reach the real module object for monkeypatching `select_provider`.
    probe_mod = sys.modules["harvest.probe"]
    from harvest.schema import ProbeResult

    canonical = Canonical("youtube.com", "dQw4w9WgXcQ", 1, "https://youtu.be/dQw4w9WgXcQ")

    class _FakeYT:
        def fetch_metadata(self, c, settings):
            return SourceMetadata(
                platform="youtube.com", id="dQw4w9WgXcQ", title="T", uploader="C",
                uploader_id="UCx", description="d", duration_s=100,
                published_at="2009-10-25T06:57:33Z", parts=1, part_durations_s=[100],
                thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg",
                view_count=123, like_count=45)

    monkeypatch.setattr(probe_mod, "select_provider", lambda url: _FakeYT())
    result = probe_mod.probe(canonical, Settings())
    assert isinstance(result, ProbeResult)
    assert result.platform == "youtube.com"
    assert result.uploader_id == "UCx"
    assert result.published_at.endswith("Z")
    assert result.thumbnail_url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg"
    assert result.stats.view_count == 123
    assert result.stats.like_count == 45
    assert result.stats.coin_count is None
    assert result.fetched_at.endswith("Z")
