"""Direct bilibili player-API subtitle fetch (cookie-authenticated, no WBI signing).

yt-dlp (even latest, 2026.06.09) does **not** surface bilibili's AI subtitle tracks. But the
plain `x/player/v2` endpoint returns them with the same login cookies yt-dlp already reads from
the browser — no WBI signing needed (the SPEC §3 surface we deliberately avoid). This is the
fallback that lights up the subtitle-reuse path (SPEC §5 step 2, D4) on real content when
`_pick_track` (yt-dlp's list) comes back empty.

Tracks carry `ai_type`: 0 = original-language transcription, 1 = a translation to another locale.
We only want the original zh transcription; translations are ignored.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import yt_dlp
from pydantic import BaseModel, ValidationError

from .config import REFERER, Settings
from .resolve import Canonical
from .schema import Segment
from .subtitles import _ZH_KEYS, parse_bcc, ydl_opts

_API_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
_API_PLAYER = "https://api.bilibili.com/x/player/v2?aid={aid}&cid={cid}&bvid={bvid}"
_UA = "Mozilla/5.0"

# bilibili.com is a China-domestic platform; `pubdate` is presented in China Standard Time.
# Centralized here so a future scope expansion (bilibili.tv, YouTube) has one place to map
# per-platform source timezones instead of hunting through call sites.
SOURCE_TZ = timezone(timedelta(hours=8))  # CST / UTC+8


def published_at_iso(pubdate: int | None) -> str | None:
    """Convert a `pubdate` Unix-seconds epoch to an ISO 8601 string in `SOURCE_TZ`.

    `None`/`0` (bilibili's "unknown" sentinel) both map to `None`.
    """
    if not pubdate:
        return None
    return datetime.fromtimestamp(pubdate, tz=SOURCE_TZ).isoformat()


class ViewError(Exception):
    """Raised when web-interface/view responds with a non-zero `code`."""


class ViewPage(BaseModel):
    """One entry of a (possibly multi-part) video, from `data.pages[]` (or synthesized)."""

    part: int
    cid: int | None = None
    title: str | None = None
    duration: int | None = None


class ViewData(BaseModel):
    """Parsed `web-interface/view` response: the single source of truth for video metadata."""

    aid: int | None = None
    cid: int | None = None
    title: str | None = None
    desc: str | None = None
    duration: int | None = None
    pubdate: int | None = None  # Unix seconds, publish time (SPEC: published_at source)
    owner_mid: int | None = None
    owner_name: str | None = None
    pages: list[ViewPage] = []


def cid_for_part(view_data: ViewData, part: int) -> int | None:
    """Map a 1-based part to its cid from `ViewData.pages` (page-number match first, then
    positional index), falling back to the top-level cid for a single-part video.
    """
    pages = view_data.pages
    for pg in pages:
        if pg.part == part:
            return pg.cid
    if 1 <= part <= len(pages):
        return pages[part - 1].cid
    if part == 1:
        return view_data.cid
    return None


def _zh_rank(lan: str) -> int:
    return _ZH_KEYS.index(lan) if lan in _ZH_KEYS else len(_ZH_KEYS)


def select_zh_subtitle(subtitles: list[dict]) -> dict | None:
    """Pick the original-language zh track: original transcription (ai_type 0) before any
    translation, then our zh-key preference order. Returns None if no zh track is present."""
    zh = [s for s in subtitles if s.get("lan") in _ZH_KEYS]
    if not zh:
        return None
    zh.sort(key=lambda s: (s.get("ai_type", 0), _zh_rank(s.get("lan", ""))))
    return zh[0]


def _opener(settings: Settings):
    """urllib opener carrying the same browser/SESSDATA cookies yt-dlp uses, + the Referer
    bilibili's CDN requires."""
    with yt_dlp.YoutubeDL(ydl_opts(settings)) as ydl:
        jar = ydl.cookiejar
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("Referer", REFERER), ("User-Agent", _UA)]
    return op


def _get_json(opener, url: str) -> dict:
    return json.loads(opener.open(url, timeout=60).read().decode("utf-8"))


def fetch_view(canonical: Canonical, settings: Settings, *, opener=None) -> ViewData:
    """Fetch + parse `web-interface/view` for this video: one GET, the single source of truth
    for title/owner/desc/duration/pages.

    Raises `ViewError` when the response's `code != 0`. `opener` is injectable for tests;
    production builds one carrying the live cookies.
    """
    op = opener or _opener(settings)
    view = _get_json(op, _API_VIEW.format(bvid=canonical.id))
    if view.get("code") != 0:
        raise ViewError(f"web-interface/view error: code={view.get('code')!r}, "
                         f"message={view.get('message')!r}")

    data = view.get("data") or {}
    owner = data.get("owner") or {}
    desc = data.get("desc") or None

    try:
        raw_pages = data.get("pages") or []
        if raw_pages:
            pages = [
                ViewPage(
                    part=pg.get("page"),
                    cid=pg.get("cid"),
                    title=pg.get("part"),
                    duration=pg.get("duration"),
                )
                for pg in raw_pages
            ]
        else:
            pages = [
                ViewPage(part=1, cid=data.get("cid"), title=None, duration=data.get("duration"))
            ]

        return ViewData(
            aid=data.get("aid"),
            cid=data.get("cid"),
            title=data.get("title"),
            desc=desc,
            duration=data.get("duration"),
            pubdate=data.get("pubdate"),
            owner_mid=owner.get("mid"),
            owner_name=owner.get("name"),
            pages=pages,
        )
    except ValidationError as exc:
        raise ViewError(
            f"web-interface/view returned an unparseable shape: {exc}"
        ) from exc


def part_segments(
    canonical: Canonical, settings: Settings, *, opener=None, view: ViewData | None = None
) -> tuple[str, list[Segment]] | None:
    """Fetch + parse the original-zh subtitle for this part via the player API.

    Returns (lang, segments) or None when no usable zh track exists. `opener` is injectable for
    tests; production builds one carrying the live cookies. `view` lets a caller that already
    fetched `ViewData` (Task 4: one fetch per part) share it instead of triggering a second GET.
    """
    op = opener or _opener(settings)

    if view is None:
        try:
            view = fetch_view(canonical, settings, opener=op)
        except ViewError:
            return None
    aid = view.aid
    cid = cid_for_part(view, canonical.part)
    if not (aid and cid):
        return None

    player = _get_json(op, _API_PLAYER.format(aid=aid, cid=cid, bvid=canonical.id))
    subs = (((player.get("data") or {}).get("subtitle") or {}).get("subtitles")) or []
    pick = select_zh_subtitle(subs)
    if not pick:
        return None

    url = pick.get("subtitle_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    if not url:
        return None
    raw = op.open(url, timeout=60).read().decode("utf-8")
    segments = parse_bcc(raw)
    if not segments:
        return None
    return (pick.get("lan") or "ai-zh"), segments
