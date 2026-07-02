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
