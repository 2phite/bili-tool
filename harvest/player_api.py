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

import gzip
import json
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
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
    """Parsed `web-interface/view` response: the single source of truth for video metadata.

    `pic` + the `*_count` fields (Task 1) come from the SAME response's `data.pic`/`data.stat.*`
    -- no second network call.
    """

    aid: int | None = None
    cid: int | None = None
    title: str | None = None
    desc: str | None = None
    duration: int | None = None
    pubdate: int | None = None  # Unix seconds, publish time (SPEC: published_at source)
    owner_mid: int | None = None
    owner_name: str | None = None
    pic: str | None = None
    view_count: int | None = None
    danmaku_count: int | None = None
    like_count: int | None = None
    coin_count: int | None = None
    favorite_count: int | None = None
    share_count: int | None = None
    reply_count: int | None = None
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
    stat = data.get("stat") or {}

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
            pic=data.get("pic") or None,
            view_count=stat.get("view"),
            danmaku_count=stat.get("danmaku"),
            like_count=stat.get("like"),
            coin_count=stat.get("coin"),
            favorite_count=stat.get("favorite"),
            share_count=stat.get("share"),
            reply_count=stat.get("reply"),
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
    # language key (provenance set to "auto-sub" by subtitles._acquire)
    return (pick.get("lan") or "ai-zh"), segments


# --- danmaku acquisition (Task 2): the server-sampled XML endpoint, MVP scope. The protobuf
# seg.so census endpoint is deferred (see task brief) -- `sampled` lets Task 3's schema tell the
# difference once that lands. ---

_API_DANMAKU_XML = "https://comment.bilibili.com/{cid}.xml"


@dataclass(frozen=True)
class RawDanmaku:
    """One `<d>` element, stripped to the fields the acquisition contract keeps: `content_ts`
    (seconds into the video the comment is pinned to) + its text. `mode`/`post_unix`/`post_iso`
    and the rest of the `p=` attribute are deliberately dropped (descoped, see task brief)."""

    content_ts: float
    text: str


@dataclass(frozen=True)
class DanmakuFetch:
    """Result of a danmaku acquisition attempt: `source_total` is bilibili's platform-reported
    count (`ViewData.danmaku_count`, may be `None` if unavailable), `fetched_total` is how many
    records this fetch actually got (`len(records)`), and `sampled` is `True` when the XML
    endpoint's server-side cap means `fetched_total` undercounts `source_total`."""

    source_total: int | None
    fetched_total: int
    sampled: bool
    records: list[RawDanmaku]


def _decode(body: bytes) -> str:
    """comment.bilibili.com/{cid}.xml is served deflate/gzip-compressed; urllib won't unwrap it.
    Lifted verbatim from the proven `scratch/dump_danmaku.py` probe."""
    for attempt in (
        lambda b: b.decode("utf-8"),                # already plain
        lambda b: gzip.decompress(b).decode("utf-8"),
        lambda b: zlib.decompress(b, -zlib.MAX_WBITS).decode("utf-8"),  # raw deflate
        lambda b: zlib.decompress(b).decode("utf-8"),                   # zlib-wrapped
    ):
        try:
            return attempt(body)
        except Exception:  # noqa: BLE001 - try the next codec
            continue
    raise ValueError("could not decode danmaku response (unknown encoding)")


def _parse_danmaku_xml(raw: str) -> list[RawDanmaku]:
    """Parse `<d p="content_ts,mode,fontsize,color,post_unix,pool,userhash,dmid">text</d>`
    elements, keeping only `content_ts` (field 0) + text. Skips malformed `<d>` (fewer than 5
    comma fields -- the minimum needed to trust field 0). Sorted ascending by `content_ts`
    (chronological order is load-bearing for Task 3's windowing)."""
    root = ET.fromstring(raw)
    out: list[RawDanmaku] = []
    for d in root.findall("d"):
        p = (d.get("p") or "").split(",")
        if len(p) < 5:
            continue
        try:
            content_ts = float(p[0])
        except ValueError:
            continue
        out.append(RawDanmaku(content_ts=content_ts, text=d.text or ""))
    out.sort(key=lambda r: r.content_ts)
    return out


def fetch_danmaku(
    canonical: Canonical, settings: Settings, *, opener=None, view: ViewData | None = None
) -> DanmakuFetch:
    """Fetch + parse the raw danmaku stream for this part via the server-sampled XML endpoint.

    Always returns a `DanmakuFetch`, never raises or returns `None`: when no cid resolves for
    the part (or the view itself is unavailable/`ViewError`s), returns an empty result --
    `records=[]`, `fetched_total=0`, `sampled=False`, `source_total` carried over from `view`
    when one was available. This mirrors `part_segments`'s "absence degrades gracefully" stance,
    adapted to a non-Optional return type since `DanmakuFetch` already has a natural empty state.

    `opener` is injectable for tests; production builds one carrying the live cookies. `view`
    lets a caller that already fetched `ViewData` (Task 4: one fetch per part) share it instead
    of triggering a second `web-interface/view` GET.
    """
    op = opener or _opener(settings)

    if view is None:
        try:
            view = fetch_view(canonical, settings, opener=op)
        except ViewError:
            view = None

    source_total = view.danmaku_count if view is not None else None
    cid = cid_for_part(view, canonical.part) if view is not None else None
    if not cid:
        return DanmakuFetch(source_total=source_total, fetched_total=0, sampled=False, records=[])

    raw = _decode(op.open(_API_DANMAKU_XML.format(cid=cid), timeout=60).read())
    records = _parse_danmaku_xml(raw)
    fetched_total = len(records)
    sampled = source_total is not None and fetched_total < source_total
    return DanmakuFetch(
        source_total=source_total, fetched_total=fetched_total, sampled=sampled, records=records
    )
