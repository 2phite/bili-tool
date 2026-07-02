from pathlib import Path

from harvest.config import Settings


def test_load_reads_harvest_env_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("HARVEST_COOKIES_BROWSER", "chrome")
    monkeypatch.setenv("HARVEST_COOKIES_PROFILE", "Default")
    monkeypatch.setenv("HARVEST_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("HARVEST_OUT_DIR", str(tmp_path / "o"))
    monkeypatch.setenv("BILI_COOKIES_BROWSER", "firefox")  # old key must be ignored
    s = Settings.load()
    assert s.cookies_browser == "chrome"
    assert s.cookies_profile == "Default"
    assert s.cache_dir == Path(tmp_path / "c")
    assert s.out_dir == Path(tmp_path / "o")


def test_youtube_cookies_defaults_off(monkeypatch):
    monkeypatch.delenv("HARVEST_YT_COOKIES", raising=False)
    assert Settings.load().youtube_cookies is False


def test_youtube_cookies_opt_in_via_env(monkeypatch):
    for val in ("1", "true", "YES", "on"):
        monkeypatch.setenv("HARVEST_YT_COOKIES", val)
        assert Settings.load().youtube_cookies is True, val
    monkeypatch.setenv("HARVEST_YT_COOKIES", "0")
    assert Settings.load().youtube_cookies is False
