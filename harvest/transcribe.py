"""faster-whisper transcription (SPEC §5 step 3, §7).

large-v3 on CUDA, language configurable (None => auto-detect), vad_filter=True, word_timestamps=True. Keeps the default
hallucination guards; --robust disables condition_on_previous_text for lectures that degrade into
repetition loops. Audio is downloaded once and cached (D6: audio depends only on the video).
"""

from __future__ import annotations

from pathlib import Path

import yt_dlp

from .cache import fs_key
from .config import REFERER, Settings
from .resolve import Canonical
from .schema import Segment
from .subtitles import ydl_opts

WHISPER_MODEL = "large-v3"


def download_audio(canonical: Canonical, settings: Settings) -> Path:
    """Download + cache bestaudio for the part. faster-whisper decodes the container directly."""
    key = fs_key(canonical.platform, canonical.id, canonical.part)
    audio_dir = settings.cache_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    existing = [p for p in audio_dir.glob(f"{key}.*") if p.suffix != ".part"]
    if existing:
        return existing[0]

    is_bilibili = canonical.platform == "bilibili.com"
    referer = REFERER if is_bilibili else None
    # YouTube media download hits the same cookie trap as extraction (issue #1): a logged-in
    # browser session breaks yt-dlp's format selection. Cookie-free unless opted in.
    browser_cookies = is_bilibili or settings.youtube_cookies
    # aria2c is a throttled-bilibili-CDN optimization (issue #3): on YouTube its parallel
    # connections get throttled to a crawl and it bypasses yt-dlp's n-signature handling, so
    # downloads stall or "succeed" without writing a file. Native downloader off bilibili.
    opts = ydl_opts(
        settings,
        skip_download=False,
        referer=referer,
        browser_cookies=browser_cookies,
        external_downloader=is_bilibili,
    )
    opts.update({"format": "bestaudio/best", "outtmpl": str(audio_dir / f"{key}.%(ext)s")})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(canonical.url, download=True)

    return _resolve_audio_path(info, audio_dir, key)


def _resolve_audio_path(info: dict, audio_dir: Path, key: str) -> Path:
    """Recover the downloaded audio file from yt-dlp's info dict, else the on-disk glob.

    yt-dlp's `requested_downloads[0]["filepath"]` is the happy path but is inconsistently present
    (issue #3), so we also try the top-level `filepath` and the entry's `_filename`/`filename`
    before globbing the cache. If nothing resolves to a real file the download silently produced
    none — fail loud with the expected pattern and yt-dlp's info keys instead of a bare
    StopIteration from an exhausted `next()`."""
    rd = (info.get("requested_downloads") or [{}])[0]
    keys = ("filepath", "_filename", "filename")
    for cand in (info.get("filepath"), *(rd.get(k) for k in keys)):
        if cand and Path(cand).exists():
            return Path(cand)
    matches = [p for p in audio_dir.glob(f"{key}.*") if p.suffix != ".part"]
    if matches:
        return matches[0]
    listing = sorted(p.name for p in audio_dir.iterdir()) if audio_dir.exists() else []
    raise RuntimeError(
        f"audio download completed but no file was found for {key!r}. "
        f"Expected {audio_dir / (key + '.*')} (excluding .part), "
        f"but audio dir contains: {listing}. "
        f"yt-dlp info keys: {sorted(info.keys())}; "
        f"requested_downloads[0] keys: {sorted(rd.keys())}."
    )


def _register_cuda_dlls() -> None:
    """ctranslate2 needs the CUDA 12 runtime DLLs (cudart, cublas, cudnn). On Windows we ship them
    via the nvidia-*-cu12 pip wheels. ctranslate2 loads them with plain LoadLibrary, which searches
    PATH (not dirs added via add_dll_directory) — so we must prepend the bin dirs to PATH too."""
    import os

    try:
        import nvidia
    except ImportError:
        return  # rely on a system CUDA install already on PATH
    bins = [str(b) for root in nvidia.__path__ for b in Path(root).glob("*/bin")]
    for b in bins:
        os.add_dll_directory(b)
    if bins:
        os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")


def transcribe(
    audio_path: Path, *, robust: bool = False, model: str = WHISPER_MODEL, lang: str | None = None
) -> list[Segment]:
    _register_cuda_dlls()
    from faster_whisper import WhisperModel

    wm = WhisperModel(model, device="cuda", compute_type="float16")
    segments, _info = wm.transcribe(
        str(audio_path),
        language=lang,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=not robust,
    )
    return [
        Segment(start=round(s.start, 3), end=round(s.end, 3), text=s.text.strip())
        for s in segments
    ]
