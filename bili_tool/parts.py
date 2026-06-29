"""Multi-part orchestration (D12): enumerate parts and run the single-part pipeline per part
with per-part failure isolation.

The {platform, id, part} triple is the atomic identity unit (D12). A bilibili multi-part video
is one id with N parts; yt-dlp returns the whole set as a playlist when asked for the bare URL,
and a single part when asked for `?p=N`. So --all-parts = "count parts, then loop ?p=N".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode, urlparse, urlunparse

from .resolve import Canonical


def part_url(base_url: str, part: int) -> str:
    """Canonical per-part URL: set ?p=<part>, replacing any existing p= query."""
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(query=urlencode({"p": part})))


def count_parts(info: dict) -> int:
    """Number of parts in a yt-dlp info dict: playlist entry count, else 1."""
    entries = info.get("entries")
    if entries is None:
        return 1
    return len(list(entries))


def select_parts(args, canonical: Canonical, *, total: int) -> list[int]:
    """Which 1-based parts to run: all of them, an explicit --part, or the URL's part."""
    if getattr(args, "all_parts", False):
        return list(range(1, total + 1))
    if getattr(args, "part", None) is not None:
        return [args.part]
    return [canonical.part]


@dataclass
class PartResult:
    part: int
    ok: bool
    error: str | None = None


def run_parts(
    canonical: Canonical,
    parts: list[int],
    *,
    settings,
    args,
    processor: Callable[[Canonical, object, object], None],
) -> list[PartResult]:
    """Run `processor` for each selected part, isolating failures so one bad part (private,
    region-locked, transient CDN error) never aborts the rest of the batch."""
    results: list[PartResult] = []
    for p in parts:
        per_part = Canonical(
            canonical.platform, canonical.id, p, part_url(canonical.url, p)
        )
        try:
            processor(per_part, settings, args)
            results.append(PartResult(p, True))
        except Exception as exc:  # noqa: BLE001 - isolation is the whole point
            results.append(PartResult(p, False, f"{type(exc).__name__}: {exc}"))
    return results
