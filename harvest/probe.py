"""Public pre-flight metadata probe. Delegates to the URL-selected provider's fetch_metadata
-> SourceMetadata, then maps that normalized shape onto the stable ProbeResult schema. No
platform branches (SPEC §4.1)."""

from __future__ import annotations

from .config import Settings
from .providers.base import Canonical, select_provider
from .schema import ProbeResult


def probe(canonical: Canonical, settings: Settings, *, opener=None) -> ProbeResult:
    if canonical.platform == "bilibili.tv":
        raise ValueError("probe is bilibili.com-only; bilibili.tv unsupported (deferred)")

    provider = select_provider(canonical.url)
    if opener is not None:
        meta = provider.fetch_metadata(canonical, settings, opener=opener)
    else:
        meta = provider.fetch_metadata(canonical, settings)
    return ProbeResult(
        platform=meta.platform,
        id=meta.id,
        title=meta.title,
        uploader=meta.uploader,
        uploader_id=meta.uploader_id,
        description=meta.description,
        duration_s=meta.duration_s,
        published_at=meta.published_at,
        parts=meta.parts,
        part_durations_s=meta.part_durations_s,
    )
