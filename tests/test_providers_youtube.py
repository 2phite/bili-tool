import io
import json
from pathlib import Path

from harvest.config import Settings
from harvest.providers.base import Canonical, SourceMetadata
from harvest.providers.youtube import YouTubeProvider

FIX = Path(__file__).parent / "fixtures" / "youtube"


def _info(name):
    return json.load(io.open(FIX / f"{name}.info.json", encoding="utf-8"))


def _canonical(vid="dQw4w9WgXcQ"):
    return Canonical("youtube.com", vid, 1, f"https://youtu.be/{vid}")


def test_matches_youtube_hosts():
    p = YouTubeProvider()
    assert p.matches("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert p.matches("https://youtu.be/dQw4w9WgXcQ")
    assert not p.matches("https://www.bilibili.com/video/BV1")


def test_resolve_extracts_11_char_id_part_always_1():
    p = YouTubeProvider()
    c = p.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5s")
    assert c.platform == "youtube.com" and c.id == "dQw4w9WgXcQ" and c.part == 1
    assert p.resolve("https://youtu.be/dQw4w9WgXcQ").id == "dQw4w9WgXcQ"


def test_metadata_from_info_maps_fields_and_utc_published_at():
    p = YouTubeProvider()
    meta = p._metadata_from_info(_info("dQw4w9WgXcQ"))
    assert isinstance(meta, SourceMetadata)
    assert meta.platform == "youtube.com"
    assert meta.id == "dQw4w9WgXcQ"
    assert meta.uploader == "Rick Astley"
    assert meta.uploader_id == "UCuAXFkgsw1L7xaCfnd5JJOw"   # channel_id, NOT @handle
    assert meta.published_at == "2009-10-25T06:57:33Z"       # timestamp 1256453853 -> UTC ...Z
    assert meta.parts == 1
    assert meta.part_durations_s == [meta.duration_s]


def test_published_at_falls_back_to_upload_date_midnight_utc():
    p = YouTubeProvider()
    assert p._published_at({"id": "x", "upload_date": "20240628"}) == "2024-06-28T00:00:00Z"


def test_published_at_none_when_no_date_fields():
    assert YouTubeProvider()._published_at({"id": "x"}) is None


def _meta_for(info):
    return YouTubeProvider()._metadata_from_info(info)


def test_fetch_subtitle_reuses_exact_language_human_track():
    p = YouTubeProvider()
    info = _info("dQw4w9WgXcQ")  # language "en", subtitles has "en" with a vtt entry
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n"
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: vtt)
    assert got is not None and got.accepted is True
    assert got.source == "human-sub" and got.language == "en"
    assert got.segments[0].text == "hi" and got.quality_gate is None


def test_fetch_subtitle_none_when_language_unknown_goes_to_whisper():
    # kJQP7kiw5Fk: language None though an es track exists -> unknown -> Whisper (None).
    p = YouTubeProvider()
    info = _info("kJQP7kiw5Fk")
    got = p.fetch_subtitle(_canonical("kJQP7kiw5Fk"), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "")
    assert got is None


def test_fetch_subtitle_pinned_lang_overrides_and_reuses_track():
    # --lang es pins Despacito's es track despite info["language"] being None.
    p = YouTubeProvider()
    info = _info("kJQP7kiw5Fk")
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhola\n"
    got = p.fetch_subtitle(_canonical("kJQP7kiw5Fk"), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: vtt, pinned_lang="es")
    assert got.accepted is True and got.source == "human-sub"
    assert got.language == "es" and got.segments[0].text == "hola"


def test_fetch_subtitle_none_when_known_lang_has_no_exact_track():
    # 9bZkp7q19f0: language "ko" but subtitles empty -> no exact track -> Whisper (None).
    p = YouTubeProvider()
    info = _info("9bZkp7q19f0")
    got = p.fetch_subtitle(_canonical("9bZkp7q19f0"), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "")
    assert got is None


def test_fetch_subtitle_never_consults_automatic_captions():
    # Rick Astley has automatic_captions["en"]; strip subtitles and confirm no fallback.
    p = YouTubeProvider()
    info = dict(_info("dQw4w9WgXcQ"))
    info["subtitles"] = {}
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "SHOULD NOT BE CALLED")
    assert got is None
