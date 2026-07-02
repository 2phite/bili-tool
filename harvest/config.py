"""Settings + secret loading (SPEC §11). Reads .env, applies defaults, auto-detects ffmpeg.

Calibratable thresholds (D3/D5/D6) live here as named, overridable defaults seeded with the
SPEC's documented *guesses* — tuned on real lectures in build steps 3-4, not law.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import __version__

TOOL_VERSION = __version__
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERER = "https://www.bilibili.com"


def find_ffmpeg() -> str | None:
    """Locate ffmpeg: PATH first, then the winget install location (no shell refresh needed)."""
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    env = os.environ.get("FFMPEG_PATH")
    if env and Path(env).exists():
        return env
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        for exe in pkgs.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
            return str(exe)
    return None


def find_aria2c() -> str | None:
    """Locate aria2c (preferred downloader for throttled bilibili CDNs): PATH then winget."""
    on_path = shutil.which("aria2c")
    if on_path:
        return on_path
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        for exe in pkgs.glob("aria2.aria2*/**/aria2c.exe"):
            return str(exe)
    return None


@dataclass
class QualityThresholds:
    """D5 starting guesses — any single metric tripping falls back to Whisper. Calibrate step 3."""

    punct_density_min: float = 0.04  # punctuation marks per char
    dup_ratio_max: float = 0.30  # fraction of duplicated/near-repeated segments
    nonzh_ratio_max: float = 0.20  # fraction of non-CJK "garbage" chars
    cps_min: float = 1.0  # chars per second, lower bound
    cps_max: float = 8.0  # chars per second, upper bound


@dataclass
class Settings:
    # vision / LM Studio
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_api_key: str = ""
    lmstudio_vision_model: str = ""
    lmstudio_danmaku_model: str = ""
    lmstudio_danmaku_max_tokens: int = 8192

    # auth (D9)
    sessdata: str | None = None
    cookies_browser: str = "firefox"
    cookies_profile: str = ""

    # paths
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "cache")
    out_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "out")
    ffmpeg_path: str | None = None
    aria2c_path: str | None = None

    # frames (D3/D6) — defaults are guesses, tuned step 4
    sample_interval_s: float = 6.0  # periodic frame sampling cadence (slide-deck recordings)
    scene_threshold: float = 27.0  # PySceneDetect ContentDetector default (secondary signal)
    # hamming collapse; calibrated step 4 (within-slide jitter <10, slide cut >=16)
    phash_dedup_threshold: int = 10
    chunk_window_s: float = 75.0  # D3 wall-clock chunk fallback (~60-90s)

    # quality gate (D5)
    quality: QualityThresholds = field(default_factory=QualityThresholds)

    tool_version: str = TOOL_VERSION

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        s = cls(
            lmstudio_base_url=os.environ.get("LMSTUDIO_BASE_URL", cls.lmstudio_base_url),
            lmstudio_api_key=os.environ.get("LMSTUDIO_API_KEY", ""),
            lmstudio_vision_model=os.environ.get("LMSTUDIO_VISION_MODEL", ""),
            lmstudio_danmaku_model=os.environ.get("HARVEST_DANMAKU_MODEL", ""),
            lmstudio_danmaku_max_tokens=int(
                os.environ.get("HARVEST_DANMAKU_MAX_TOKENS", cls.lmstudio_danmaku_max_tokens)
            ),
            sessdata=os.environ.get("SESSDATA") or None,
            cookies_browser=os.environ.get("HARVEST_COOKIES_BROWSER", cls.cookies_browser),
            cookies_profile=os.environ.get("HARVEST_COOKIES_PROFILE", ""),
        )
        if os.environ.get("HARVEST_CACHE_DIR"):
            s.cache_dir = Path(os.environ["HARVEST_CACHE_DIR"])
        if os.environ.get("HARVEST_OUT_DIR"):
            s.out_dir = Path(os.environ["HARVEST_OUT_DIR"])
        s.ffmpeg_path = find_ffmpeg()
        s.aria2c_path = find_aria2c()
        return s
