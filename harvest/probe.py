"""Public pre-flight: cheap metadata probe ahead of the full pipeline.

Atlas calls `probe()` to estimate workload (title/duration/parts/per-part durations) before
committing to the full harvest run. Maps `fetch_view`'s `ViewData` onto the stable
`ProbeResult` schema (SPEC: downstream-facing contract, schema.py).

bilibili.tv is deferred (no player-API view endpoint wired yet) - guarded out before any HTTP.
"""

from __future__ import annotations

from .config import Settings
from .player_api import fetch_view, published_at_iso
from .resolve import Canonical
from .schema import ProbeResult


def probe(canonical: Canonical, settings: Settings, *, opener=None) -> ProbeResult:
    """Fetch + map view metadata into a `ProbeResult`. bilibili.com-only; raises `ValueError`
    for bilibili.tv before any HTTP. Propagates `ViewError` from `fetch_view` (fails loud)."""
    if canonical.platform != "bilibili.com":
        raise ValueError("probe is bilibili.com-only; bilibili.tv unsupported (deferred)")

    view = fetch_view(canonical, settings, opener=opener)

    return ProbeResult(
        platform=canonical.platform,
        id=canonical.id,
        title=view.title,
        uploader=view.owner_name,
        uploader_mid=view.owner_mid,
        description=view.desc,
        duration_s=view.duration,
        published_at=published_at_iso(view.pubdate),
        parts=max(len(view.pages), 1),
        part_durations_s=[p.duration for p in view.pages],
    )
