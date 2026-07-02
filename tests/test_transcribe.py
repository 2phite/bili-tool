import sys
import types
from pathlib import Path


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
