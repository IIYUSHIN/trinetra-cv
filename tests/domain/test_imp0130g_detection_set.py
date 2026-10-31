"""IMP-0130G tests: a DetectionSet is inside its image, canonically ordered, bounded and lossless."""

from __future__ import annotations

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.detection import MAX_DETECTIONS, Detection, DetectionSet
from trinetra.domain.digests import DigestSha256
from trinetra.domain.geometry import CanonicalBoxXYXY

DIGEST = DigestSha256.of(b"image")
W, H = 640, 480


def d(c=0, s=0.5, x1=1.0, y1=2.0, x2=3.0, y2=4.0):  # noqa: PLR0917
    return Detection(class_index=c, score=s, box=CanonicalBoxXYXY(x1=x1, y1=y1, x2=x2, y2=y2))


def make(detections, width=W, height=H):
    return DetectionSet(image_member_digest=DIGEST, image_width=width, image_height=height,
                        detections=tuple(detections))


def unordered(detections, width=W, height=H):
    return DetectionSet.from_unordered(image_member_digest=DIGEST, image_width=width,
                                       image_height=height, detections=detections)


def test_an_empty_detection_set_is_valid():
    assert make([]).detections == ()


def test_the_documented_order_is_class_then_score_descending_then_box_ascending():
    expected = [
        d(0, 0.9, 1.0, 2.0, 3.0, 4.0), d(0, 0.9, 1.0, 2.0, 3.0, 5.0), d(0, 0.9, 1.0, 2.0, 4.0, 4.0),
        d(0, 0.9, 1.0, 3.0, 4.0, 5.0), d(0, 0.9, 2.0, 2.0, 3.0, 4.0),
        d(0, 0.5), d(1, 0.99), d(1, 0.1), d(2, 0.3),
    ]  # fmt: skip
    shuffled = list(expected)
    random.Random(7).shuffle(shuffled)  # noqa: S311 - deterministic test shuffle
    assert unordered(shuffled).detections == tuple(expected)
    assert make(expected).detections == tuple(expected)


def test_each_tie_break_key_is_used_in_turn():
    base = {"c": 1, "s": 0.5, "x1": 10.0, "y1": 10.0, "x2": 20.0, "y2": 20.0}
    for field, low, high in (("x1", 9.0, 11.0), ("y1", 9.0, 11.0), ("x2", 19.0, 21.0), ("y2", 19.0, 21.0)):
        a, b = d(**(base | {field: low})), d(**(base | {field: high}))
        assert unordered([b, a]).detections == (a, b), field
        with pytest.raises(ValidationError):
            make([b, a])


def test_higher_score_comes_first_within_a_class_and_lower_class_before_higher():
    hi, lo, other = d(0, 0.9), d(0, 0.1), d(1, 1.0)
    assert unordered([other, lo, hi]).detections == (hi, lo, other)
    with pytest.raises(ValidationError):
        make([lo, hi])
    with pytest.raises(ValidationError):
        make([other, hi])


def test_identical_duplicates_are_preserved_not_deduplicated():
    assert len(unordered([d(), d(), d()]).detections) == 3
    assert len(make([d(), d()]).detections) == 2


def test_a_box_outside_the_image_is_rejected_and_never_clipped():
    with pytest.raises(ValidationError):
        make([d(x2=641.0)])
    with pytest.raises(ValidationError):
        make([d(y2=480.5)])
    assert make([d(x2=640.0, y2=480.0)]).detections[0].box.x2 == 640.0


def test_image_dimensions_must_be_positive_strict_integers():
    for bad in (0, -1, 640.0, True, None):
        with pytest.raises(ValidationError):
            make([], width=bad)
        with pytest.raises(ValidationError):
            make([], height=bad)


def test_the_hard_detection_bound_is_enforced():
    many = [d(0, 0.5, float(i), 1.0, float(i) + 0.5, 2.0) for i in range(MAX_DETECTIONS)]
    assert len(unordered(many, width=20000).detections) == MAX_DETECTIONS
    with pytest.raises(ValidationError):
        unordered([*many, d()], width=20000)


def test_the_set_carries_no_extra_fields_and_is_immutable():
    s = make([d()])
    with pytest.raises(ValidationError):
        s.detections = ()
    with pytest.raises(ValidationError):
        DetectionSet(
            image_member_digest=DIGEST, image_width=W, image_height=H, detections=(), model_trust=0.9
        )
    assert isinstance(s.detections, tuple)


@pytest.mark.parametrize("bad", ["sha256:" + "0" * 63, "SHA256:" + "0" * 64, "0" * 64, b"\x00" * 32, None, 7])
def test_the_image_digest_must_be_a_real_sha256_value(bad):
    with pytest.raises(ValidationError):
        DetectionSet(image_member_digest=bad, image_width=W, image_height=H, detections=())


def test_equal_content_has_one_canonical_digest_regardless_of_input_order():
    items = [d(0, 0.9, 5.0, 2.0, 6.0, 4.0), d(1, 0.4), d(0, 0.2), d(2, 0.7)]
    a = unordered(items)
    b = unordered(list(reversed(items)))
    assert a == b
    assert a.canonical_digest() == b.canonical_digest()
    assert a.canonical_digest() != make([]).canonical_digest()


def test_changing_any_one_detection_value_changes_the_digest():
    base = unordered([d(0, 0.5, 1.0, 2.0, 3.0, 4.0)]).canonical_digest()
    variants = (d(1, 0.5), d(0, 0.6), d(0, 0.5, 1.5, 2.0, 3.0, 4.0), d(0, 0.5, 1.0, 2.5, 3.0, 4.0),
                d(0, 0.5, 1.0, 2.0, 3.5, 4.0), d(0, 0.5, 1.0, 2.0, 3.0, 4.5))  # fmt: skip
    for changed in variants:
        assert unordered([changed]).canonical_digest() != base


coords = st.floats(min_value=0.0, max_value=400.0, allow_nan=False)
detections = st.builds(
    lambda c, s, x, y, w, h: d(c, s, x, y, x + w + 0.5, y + h + 0.5),
    st.integers(0, 5), st.floats(0.0, 1.0, allow_nan=False), coords, coords,
    st.floats(0.0, 30.0, allow_nan=False), st.floats(0.0, 30.0, allow_nan=False),
)  # fmt: skip


@settings(max_examples=150)
@given(st.lists(detections, max_size=30), st.randoms())
def test_ordering_is_a_total_deterministic_permutation_invariant(items, rnd):
    shuffled = list(items)
    rnd.shuffle(shuffled)
    first, second = unordered(items), unordered(shuffled)
    assert first == second
    assert sorted(first.detections, key=repr) == sorted(items, key=repr)  # nothing added or lost
    assert make(first.detections) == first  # sorted output is accepted unchanged
    keys = [x.sort_key() for x in first.detections]
    assert keys == sorted(keys)


def test_the_frozen_abuse_bound_is_ten_thousand_detections():
    assert MAX_DETECTIONS == 10_000
