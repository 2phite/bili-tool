from harvest.resolve import Canonical
from harvest.schema import Segment
from harvest.subtitles import SubtitleResult, is_part1_duplicate, probe


def _segs(texts):
    return [Segment(start=i * 4.0, end=(i + 1) * 4.0, text=t) for i, t in enumerate(texts)]


def test_is_part1_duplicate_identical_text():
    a = _segs(["第一句。", "第二句。", "第三句。"])
    b = _segs(["第一句。", "第二句。", "第三句。"])
    assert is_part1_duplicate(a, b) is True


def test_is_part1_duplicate_distinct_text():
    a = _segs(["这是第二部分的内容。", "完全不同的讲解。"])
    b = _segs(["这是第一部分的开场。", "另一段话。"])
    assert is_part1_duplicate(a, b) is False


def test_is_part1_duplicate_near_identical_counts_as_dup():
    # #6357 returns part 1's text verbatim; allow trivial drift (one cue differs).
    a = _segs(["大家好，欢迎来到本课程。", "今天我们讲第一章。", "谢谢观看。"])
    b = _segs(["大家好，欢迎来到本课程。", "今天我们讲第一章。", "谢谢观看！"])
    assert is_part1_duplicate(a, b) is True


def test_is_part1_duplicate_empty_part1_is_not_dup():
    a = _segs(["有内容。"])
    assert is_part1_duplicate(a, []) is False


def test_probe_rejects_part_gt1_when_identical_to_part1():
    # tier-1 (duration sanity) passes; tier-2 (#6357) must reject.
    segs = _segs(["第一句。", "第二句。", "第三句。"])
    info = {
        "duration": 12.0,
        "subtitles": {"zh-Hans": [{"ext": "json", "url": "http://x/sub.json"}]},
    }
    canonical = Canonical("bilibili.com", "BV1", 3, "https://b/video/BV1?p=3")
    result = probe(
        info, canonical, _Settings(), part1_segments=segs, _fetch=lambda *_: segs
    )
    assert result.found is False
    assert "6357" in result.reason


def test_probe_accepts_part_gt1_when_text_differs_from_part1():
    segs_p3 = _segs(["这是第三部分。", "独特的内容。"])
    segs_p1 = _segs(["这是第一部分。", "开场白。"])
    info = {
        "duration": 8.0,
        "subtitles": {"zh-Hans": [{"ext": "json", "url": "http://x/sub.json"}]},
    }
    canonical = Canonical("bilibili.com", "BV1", 3, "https://b/video/BV1?p=3")
    result = probe(
        info, canonical, _Settings(), part1_segments=segs_p1, _fetch=lambda *_: segs_p3
    )
    assert isinstance(result, SubtitleResult)
    assert result.found is True


class _Settings:
    """Minimal stand-in; probe must not touch the network when _fetch is injected."""
