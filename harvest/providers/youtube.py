"""YouTubeProvider (SPEC §6): native yt-dlp metadata + exact-key human-caption reuse.

Caption rule: target_lang = pinned --lang, else info["language"] if truthy, else None.
None -> Whisper. Known L with an exact subtitles[L] human track -> reuse (human-sub, language L).
Otherwise -> Whisper. automatic_captions is NEVER consulted. No quality gate."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ..config import Settings
from ..subtitles import parse_vtt, ydl_opts
from .base import Canonical, SourceMetadata, SubtitleOutcome, register

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        cand = parsed.path.lstrip("/").split("/")[0]
        return cand if _ID.match(cand) else None
    v = parse_qs(parsed.query).get("v", [""])[0]
    if _ID.match(v):
        return v
    segs = [s for s in parsed.path.split("/") if s]
    for i, s in enumerate(segs):
        if s in ("shorts", "embed", "v") and i + 1 < len(segs) and _ID.match(segs[i + 1]):
            return segs[i + 1]
    return None


class YouTubeProvider:
    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host.endswith("youtube.com") or host.endswith("youtu.be")

    def resolve(self, url: str) -> Canonical:
        vid = _video_id(url)
        if not vid:
            raise ValueError(f"unrecognized YouTube video id in URL: {url}")
        return Canonical("youtube.com", vid, 1, f"https://www.youtube.com/watch?v={vid}")

    def _ydl_opts(self, settings: Settings, **kw) -> dict:
        # referer=None: the default Referer is a bilibili URL and must never reach YouTube.
        # browser_cookies opt-in (issue #1): a logged-in Firefox session breaks yt-dlp's default
        # format selection, so YouTube is cookie-free unless HARVEST_YT_COOKIES is set.
        return ydl_opts(settings, referer=None, browser_cookies=settings.youtube_cookies, **kw)

    def auth_opts(self, settings: Settings) -> dict:
        return self._ydl_opts(settings)

    def _published_at(self, info: dict) -> str | None:
        ts = info.get("timestamp")
        if ts:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ud = info.get("upload_date")
        if ud and len(str(ud)) == 8:
            ud = str(ud)
            return f"{ud[0:4]}-{ud[4:6]}-{ud[6:8]}T00:00:00Z"
        return None

    def _metadata_from_info(self, info: dict) -> SourceMetadata:
        dur = info.get("duration")
        dur_i = int(dur) if dur else None
        return SourceMetadata(
            platform="youtube.com",
            id=info.get("id"),
            title=info.get("title"),
            uploader=info.get("channel") or info.get("uploader"),
            uploader_id=info.get("channel_id"),   # UC..., NOT the mutable @handle
            description=info.get("description"),
            duration_s=dur_i,
            published_at=self._published_at(info),
            parts=1,
            part_durations_s=[dur_i],
            thumbnail_url=info.get("thumbnail"),
            view_count=info.get("view_count"),
            like_count=info.get("like_count"),
        )

    def _extract_info(self, canonical: Canonical, settings: Settings) -> dict:
        with yt_dlp.YoutubeDL(self._ydl_opts(settings)) as ydl:
            info = ydl.extract_info(canonical.url, download=False)
        self._guard_degraded(info, canonical, settings)
        return info

    @staticmethod
    def _guard_degraded(info: dict, canonical: Canonical, settings: Settings) -> None:
        """Fail loud on a degraded/blocked YouTube response (issue #5) instead of letting a corrupt
        bundle get written. Every real video reports a duration; a stripped response (bot check /
        no working JS runtime, seen as a placeholder title like "recommended") omits it."""
        if info.get("duration"):
            return
        runtime = settings.js_runtime[0] if settings.js_runtime else "none"
        raise RuntimeError(
            f"degraded YouTube extraction for {canonical.id!r}: no duration in the response "
            f"(title={info.get('title')!r}). yt-dlp likely got a bot-check/stripped page because "
            f"its YouTube JS challenge could not be solved (js_runtime={runtime}). "
            f"Install deno (https://deno.land) so yt-dlp can use the real web player client."
        )

    def fetch_metadata(self, canonical, settings, *, info=None) -> SourceMetadata:
        info = info if info is not None else self._extract_info(canonical, settings)
        return self._metadata_from_info(info)

    def enumerate_parts(self, canonical, settings) -> int:
        return 1

    def _target_lang(self, info: dict, pinned: str | None) -> str | None:
        if pinned:
            return pinned
        return info.get("language") or None

    def _fetch_url(self, url: str, settings: Settings) -> str:
        with yt_dlp.YoutubeDL(self._ydl_opts(settings)) as ydl:
            return ydl.urlopen(url).read().decode("utf-8", "replace")

    def fetch_subtitle(
        self, canonical, settings, meta, *, pinned_lang=None, info=None, fetch_url=None,
    ) -> SubtitleOutcome | None:
        if info is None:
            info = self._extract_info(canonical, settings)
        target = self._target_lang(info, pinned_lang)
        if target is None:
            return None                                     # unknown language -> Whisper (no gate)
        tracks = (info.get("subtitles") or {}).get(target)  # exact key only; never automatic_captions
        if not tracks:
            return None                                     # no exact human track -> Whisper
        vtt = next((t for t in tracks if t.get("ext") == "vtt"), None) or tracks[0]
        fetch = fetch_url or self._fetch_url
        raw = fetch(vtt["url"], settings)
        segments = parse_vtt(raw)
        if not segments:
            return None
        return SubtitleOutcome(
            accepted=True, source="human-sub",
            source_reason=f"human-sub (exact-key match: {target})",
            language=target, segments=segments, quality_gate=None,
        )


register(YouTubeProvider())
