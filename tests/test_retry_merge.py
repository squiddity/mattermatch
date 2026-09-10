import pytest

from mattermatch.decoder import Detection, merge_variant_detections


TEXT = "MT:M5L90MP500K64J00000"


def _detection(box, text=TEXT):
    x1, y1, x2, y2 = box
    return Detection(text, [(x1, y1), (x2, y1), (x2, y2), (x1, y2)])


def test_reported_rotated_symbols_survive_different_retries():
    first = _detection((59, 59, 206, 208))
    second = _detection((189, 189, 336, 338))
    first_repeat = _detection((60, 58, 207, 209))
    second_repeat = _detection((188, 190, 335, 339))

    assert merge_variant_detections(
        [[first], [second], [first_repeat, second_repeat]]
    ) == [first, second]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "first_box, second_box",
    [
        ((0, 0, 100, 100), (99, 0, 199, 100)),  # Sliver overlap.
        ((0, 0, 100, 100), (100, 0, 200, 100)),  # Touching only.
        ((0, 0, 100, 100), (20, 0, 120, 100)),  # Substantial overlap, distant centers.
        ((0, 0, 100, 100), (0, 20, 100, 120)),
        ((0, 0, 100, 100), (40, 40, 60, 60)),  # Concentric, different sizes.
        ((0, 0, 100, 100), (-30, 20, 130, 80)),  # Similar area, different dimensions.
        ((0, 0, 2, 2), (1.5, 1.5, 3.5, 3.5)),  # Pixel tolerance is not enough.
        ((0, 0, 2, 2), (3, 0, 5, 2)),  # Nearby but disjoint tiny boxes.
    ],
)
def test_distinct_geometry_is_not_merged(first_box, second_box, reverse):
    first, second = _detection(first_box), _detection(second_box)
    if reverse:
        first, second = second, first
    assert merge_variant_detections([[first], [second]]) == [first, second]


@pytest.mark.parametrize(
    "box, retry_box",
    [
        ((59, 59, 206, 208), (59, 59, 206, 208)),
        ((59, 59, 206, 208), (62, 57, 204, 210)),
        ((1, 1, 11, 11), (2, 2, 12, 12)),
    ],
)
def test_jitter_merges_and_keeps_first_native_result(box, retry_box):
    first = _detection(box)
    retry = _detection(retry_box, text=f" {TEXT}\n")
    assert merge_variant_detections([[], [first], [retry]]) == [first]


@pytest.mark.parametrize(
    "position",
    [None, [], [(float("nan"), 0)], [(1, 1)], [(1, 1), (1, 10)]],
)
def test_missing_or_unusable_geometry_is_not_merged(position):
    first = Detection(TEXT, position)
    retry = Detection(TEXT, position)
    positioned = _detection((0, 0, 10, 10))
    assert merge_variant_detections([[first], [retry], [positioned]]) == [
        first, retry, positioned
    ]
    assert merge_variant_detections([[positioned], [first]]) == [positioned, first]


@pytest.mark.parametrize("prefix", [[], [[]]])
def test_same_variant_duplicates_remain(prefix):
    first = _detection((59, 59, 206, 208))
    duplicate = _detection((59, 59, 206, 208))
    retry = _detection((60, 60, 207, 209))
    assert merge_variant_detections(prefix + [[first, duplicate], [retry]]) == [
        first, duplicate
    ]


def test_different_text_is_not_merged_at_same_position():
    first = _detection((59, 59, 206, 208))
    second = _detection((59, 59, 206, 208), text="another payload")
    assert merge_variant_detections([[first], [second]]) == [first, second]
