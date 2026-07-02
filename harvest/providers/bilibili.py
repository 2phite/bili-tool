"""BilibiliProvider: existing bilibili acquisition behind the Provider interface. Adapter.

Owns the FULL bilibili subtitle trust decision (probe tier-1 duration + tier-2 #6357, then the
quality gate), so the CLI never branches on platform (SPEC §4.1, §6). A move of the proven
cli.decide_transcript bilibili flow into the seam — not a rewrite."""

from __future__ import annotations

from urllib.parse import urlparse

from ..config import Settings
from ..parts import part_url
from ..player_api import ViewData, ViewError, fetch_view, published_at_iso
from ..quality import describe_failure, evaluate
from ..resolve import resolve as _resolve
from ..subtitles import extract_info, fetch_subtitle_segments, ydl_opts
from ..subtitles import probe as subtitle_probe
from .base import Canonical, SourceMetadata, SubtitleOutcome, register


class BilibiliProvider:
    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host.endswith("bilibili.com") or host.endswith("bilibili.tv") or host.endswith("b23.tv")

    def resolve(self, url: str) -> Canonical:
        return _resolve(url)

    def auth_opts(self, settings: Settings) -> dict:
        return ydl_opts(settings)

    def _view(self, canonical, settings, *, opener=None) -> ViewData | None:
        try:
            return fetch_view(canonical, settings, opener=opener)
        except ViewError:
            return None

    def fetch_metadata(self, canonical, settings, *, opener=None) -> SourceMetadata:
        view = fetch_view(canonical, settings, opener=opener)
        return SourceMetadata(
            platform=canonical.platform,
            id=canonical.id,
            title=view.title,
            uploader=view.owner_name,
            uploader_id=str(view.owner_mid) if view.owner_mid is not None else None,
            description=view.desc,
            duration_s=view.duration,
            published_at=published_at_iso(view.pubdate),
            parts=max(len(view.pages), 1),
            part_durations_s=[pg.duration for pg in view.pages],
        )

    def enumerate_parts(self, canonical, settings, *, opener=None) -> int:
        view = self._view(canonical, settings, opener=opener)
        return max(len(view.pages), 1) if view else 1

    def _part1_segments(self, canonical, settings):
        """#6357 tier-2 input: part 1's subtitle, fetched only for part>1. Best-effort — a
        failure here just skips tier-2, never aborts (relocated from cli._part1_segments)."""
        if canonical.part <= 1:
            return None
        try:
            p1_url = part_url(canonical.url, 1)
            p1 = Canonical(canonical.platform, canonical.id, 1, p1_url)
            p1_info = extract_info(p1_url, settings)
            return fetch_subtitle_segments(p1_info, p1, settings)
        except Exception:  # noqa: BLE001 - tier-2 is an optional guard, never fatal
            return None

    def fetch_subtitle(self, canonical, settings, meta, *, pinned_lang=None, opener=None):
        """Full bilibili trust decision → SubtitleOutcome. `pinned_lang` is unused (bilibili is
        zh-only; --lang only affects the Whisper fallback language in the CLI). A rejected outcome
        still carries source_reason (+ the failed quality_gate) so the bundle records why."""
        info = extract_info(canonical.url, settings)
        view = self._view(canonical, settings, opener=opener)
        sub = subtitle_probe(
            info, canonical, settings,
            part1_segments=self._part1_segments(canonical, settings),
            view=view,
        )
        if not sub.found:
            return SubtitleOutcome(
                accepted=False, source=None,
                source_reason=f"no usable subtitle ({sub.reason})",
                language=None, segments=[],
            )
        gate = evaluate(sub.segments, float(info.get("duration") or 0), settings.quality)
        if gate.passed:
            return SubtitleOutcome(
                accepted=True, source=sub.source,
                source_reason=f"{sub.source} (quality-gate: passed)",
                language="zh", segments=sub.segments, quality_gate=gate,
            )
        return SubtitleOutcome(
            accepted=False, source=None,
            source_reason=f"subtitle rejected ({describe_failure(gate, settings.quality)})",
            language=None, segments=[], quality_gate=gate,
        )


register(BilibiliProvider())
