from harvest.frames import dedup_phashes, hamming


def test_hamming_identical_is_zero():
    assert hamming("ffff0000ffff0000", "ffff0000ffff0000") == 0


def test_hamming_counts_differing_bits():
    # 0x0 vs 0xf differ in 4 bits; rest identical.
    assert hamming("0000000000000000", "000000000000000f") == 4


def test_dedup_collapses_near_duplicate_run():
    # Three near-identical frames (same slide) then a clearly different one.
    items = [
        (0.0, "0000000000000000"),
        (4.0, "0000000000000001"),  # 1 bit off -> same slide
        (8.0, "0000000000000003"),  # 2 bits off the first kept -> still same slide
        (12.0, "ffffffffffffffff"),  # totally different -> new slide
    ]
    kept = dedup_phashes(items, threshold=5)
    assert [ts for ts, _ in kept] == [0.0, 12.0]


def test_dedup_keeps_distinct_slides():
    items = [
        (0.0, "0000000000000000"),
        (4.0, "ffffffffffffffff"),
        (8.0, "0f0f0f0f0f0f0f0f"),
    ]
    kept = dedup_phashes(items, threshold=5)
    assert len(kept) == 3


def test_dedup_compares_against_last_kept_not_last_seen():
    # Gradual drift: each step is within threshold of its predecessor, but cumulatively far.
    # Comparing against the last KEPT frame must eventually trigger a new keep.
    items = [
        (0.0, "0000000000000000"),
        (4.0, "0000000000000003"),   # 2 from kept[0] -> drop
        (8.0, "000000000000000f"),   # 4 from kept[0] -> drop
        (12.0, "00000000000000ff"),  # 8 from kept[0] -> keep
    ]
    kept = dedup_phashes(items, threshold=5)
    assert [ts for ts, _ in kept] == [0.0, 12.0]


def test_dedup_empty():
    assert dedup_phashes([], threshold=5) == []
