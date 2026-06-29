from bili_tool.parts import PartResult, count_parts, part_url, run_parts, select_parts
from bili_tool.resolve import Canonical


def test_part_url_appends_query_to_bare_canonical():
    assert (
        part_url("https://www.bilibili.com/video/BV1XY411o7Cv", 3)
        == "https://www.bilibili.com/video/BV1XY411o7Cv?p=3"
    )


def test_part_url_replaces_existing_part():
    assert (
        part_url("https://www.bilibili.com/video/BV1XY411o7Cv?p=2", 5)
        == "https://www.bilibili.com/video/BV1XY411o7Cv?p=5"
    )


def test_count_parts_reads_playlist_entries():
    info = {"_type": "playlist", "entries": [{}, {}, {}]}
    assert count_parts(info) == 3


def test_count_parts_single_video_is_one():
    assert count_parts({"title": "x", "duration": 10}) == 1


def test_select_parts_all():
    args = type("A", (), {"all_parts": True, "part": None})()
    c = Canonical("bilibili.com", "BV1", 1, "u")
    assert select_parts(args, c, total=4) == [1, 2, 3, 4]


def test_select_parts_explicit_single():
    args = type("A", (), {"all_parts": False, "part": 3})()
    c = Canonical("bilibili.com", "BV1", 3, "u")
    assert select_parts(args, c, total=4) == [3]


def test_select_parts_default_uses_canonical_part():
    args = type("A", (), {"all_parts": False, "part": None})()
    c = Canonical("bilibili.com", "BV1", 2, "u")
    assert select_parts(args, c, total=4) == [2]


def test_run_parts_isolates_failures():
    c = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    seen = []

    def processor(canonical, settings, args):
        seen.append(canonical.part)
        if canonical.part == 2:
            raise RuntimeError("boom on p2")

    results = run_parts(c, [1, 2, 3], settings=None, args=None, processor=processor)

    assert seen == [1, 2, 3]  # p2 failure did not abort the loop
    assert [r.part for r in results] == [1, 2, 3]
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error and "boom on p2" in results[1].error
    assert isinstance(results[0], PartResult)


def test_run_parts_builds_part_specific_urls():
    c = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    urls = []

    def processor(canonical, settings, args):
        urls.append((canonical.part, canonical.url))

    run_parts(c, [1, 3], settings=None, args=None, processor=processor)

    assert urls == [
        (1, "https://www.bilibili.com/video/BV1?p=1"),
        (3, "https://www.bilibili.com/video/BV1?p=3"),
    ]
