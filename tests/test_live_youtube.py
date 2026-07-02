import pytest

from harvest.config import Settings
from harvest.providers.base import SourceMetadata
from harvest.providers.youtube import YouTubeProvider

# Big Buck Bunny — stable, public, license-clean; the drift canary.
_LIVE_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


@pytest.mark.live
def test_live_youtube_metadata_and_subtitle():
    p = YouTubeProvider()
    canonical = p.resolve(_LIVE_URL)
    assert canonical.id == "aqz-KE-bpKQ"

    meta = p.fetch_metadata(canonical, Settings())
    assert isinstance(meta, SourceMetadata)
    assert meta.platform == "youtube.com"
    assert meta.uploader_id and meta.uploader_id.startswith("UC")
    assert meta.duration_s and meta.duration_s > 0
    assert meta.parts == 1

    # Probe showed language None + empty subtitles -> Whisper path (None). Tolerate either.
    got = p.fetch_subtitle(canonical, Settings(), meta)
    assert got is None or (got.accepted and got.source == "human-sub" and isinstance(got.segments, list))
