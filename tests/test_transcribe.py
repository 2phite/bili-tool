import sys
import types
from pathlib import Path

import pytest

from harvest.config import Settings
from harvest.providers.base import Canonical
from harvest.subtitles import ydl_opts


def _install_fake_whisper(monkeypatch, recorder):
    class _Seg:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class _FakeModel:
        def __init__(self, *a, **k): ...
        def transcribe(self, audio, **kwargs):
            recorder["language"] = kwargs.get("language")
            recorder["condition_on_previous_text"] = kwargs.get("condition_on_previous_text")
            return [_Seg(0.0, 1.0, " hi ")], None

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)


def test_transcribe_defaults_language_to_none(monkeypatch):
    from harvest import transcribe as T
    monkeypatch.setattr(T, "_register_cuda_dlls", lambda: None)
    rec = {}
    _install_fake_whisper(monkeypatch, rec)
    segs = T.transcribe(Path("x.m4a"))
    assert rec["language"] is None
    assert segs[0].text == "hi"


def test_transcribe_threads_explicit_lang(monkeypatch):
    from harvest import transcribe as T
    monkeypatch.setattr(T, "_register_cuda_dlls", lambda: None)
    rec = {}
    _install_fake_whisper(monkeypatch, rec)
    T.transcribe(Path("x.m4a"), lang="zh")
    assert rec["language"] == "zh"


# --- issue #3: robust audio-file recovery + downloader scoping ---------------------------------


def test_ydl_opts_external_downloader_false_omits_aria2c():
    # issue #3: YouTube (external_downloader=False) must use yt-dlp's native downloader, not
    # aria2c, whose parallel connections YouTube throttles to a crawl and which bypasses the
    # n-signature handling. aria2c stays the default for bilibili's throttled CDN.
    s = Settings(aria2c_path="C:/aria2c.exe")
    opts = ydl_opts(s, external_downloader=False)
    assert "external_downloader" not in opts
    assert opts["http_chunk_size"] > 0


def test_ydl_opts_default_uses_aria2c_when_available():
    s = Settings(aria2c_path="C:/aria2c.exe")
    opts = ydl_opts(s)
    assert opts["external_downloader"] == {"default": "C:/aria2c.exe"}


class _FakeYDL:
    """Records the opts it was constructed with and runs a side effect on extract_info."""

    captured: dict = {}

    def __init__(self, opts):
        _FakeYDL.captured = opts
        self._info = opts.pop("_test_info", {})
        self._writes = opts.pop("_test_writes", None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download):
        if self._writes is not None:
            self._writes()
        return self._info


def _fake_ydl_factory(monkeypatch, info, writes=None):
    def make(opts):
        opts["_test_info"] = info
        opts["_test_writes"] = writes
        return _FakeYDL(opts)

    from harvest import transcribe as T
    fake_mod = types.SimpleNamespace(YoutubeDL=make)
    monkeypatch.setattr(T, "yt_dlp", fake_mod)


def _canon(platform="youtube.com"):
    return Canonical(platform, "F8X9_Dp3ZUk", 1, "https://www.youtube.com/watch?v=F8X9_Dp3ZUk")


def test_download_audio_returns_filepath_from_requested_downloads(tmp_path, monkeypatch):
    from harvest import transcribe as T
    s = Settings(cache_dir=tmp_path)
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    key = "youtube.com_F8X9_Dp3ZUk_1"
    target = audio / f"{key}.webm"

    def writes():
        target.write_bytes(b"x")

    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, writes)
    assert T.download_audio(_canon(), s) == target


def test_download_audio_recovers_via_glob_when_filepath_missing(tmp_path, monkeypatch):
    # The reported failure mode: requested_downloads[0] lacks a filepath key entirely, but the
    # file IS on disk. Recover it via the glob instead of raising a bare StopIteration.
    from harvest import transcribe as T
    s = Settings(cache_dir=tmp_path)
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    key = "youtube.com_F8X9_Dp3ZUk_1"
    target = audio / f"{key}.webm"

    def writes():
        target.write_bytes(b"x")

    info = {"requested_downloads": [{"asr": 44100, "format_id": "251"}]}  # no filepath
    _fake_ydl_factory(monkeypatch, info, writes)
    assert T.download_audio(_canon(), s) == target


def test_download_audio_raises_descriptive_error_when_no_file(tmp_path, monkeypatch):
    # issue #3: download "succeeded" (no exception) but wrote no file -> fail loud, not a bare
    # StopIteration. The message must name the expected pattern and the yt-dlp info keys.
    from harvest import transcribe as T
    s = Settings(cache_dir=tmp_path)
    info = {"requested_downloads": [{"asr": 44100}], "id": "F8X9_Dp3ZUk"}
    _fake_ydl_factory(monkeypatch, info, writes=None)  # writes nothing
    with pytest.raises(RuntimeError) as ei:
        T.download_audio(_canon(), s)
    msg = str(ei.value)
    assert "youtube.com_F8X9_Dp3ZUk_1" in msg
    assert "requested_downloads" in msg


def test_download_audio_youtube_uses_native_downloader(tmp_path, monkeypatch):
    # issue #3 root-cause mitigation: the YouTube audio path must NOT hand the download to aria2c.
    from harvest import transcribe as T
    s = Settings(cache_dir=tmp_path, aria2c_path="C:/aria2c.exe")
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    target = audio / "youtube.com_F8X9_Dp3ZUk_1.webm"
    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, lambda: target.write_bytes(b"x"))
    T.download_audio(_canon(), s)
    assert "external_downloader" not in _FakeYDL.captured


def test_download_audio_bilibili_keeps_aria2c(tmp_path, monkeypatch):
    from harvest import transcribe as T
    s = Settings(cache_dir=tmp_path, aria2c_path="C:/aria2c.exe")
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    target = audio / "bilibili.com_BV1_1.m4a"
    canon = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, lambda: target.write_bytes(b"x"))
    T.download_audio(canon, s)
    assert _FakeYDL.captured["external_downloader"] == {"default": "C:/aria2c.exe"}
