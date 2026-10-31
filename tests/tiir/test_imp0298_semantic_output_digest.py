"""IMP-0298: the semantic DetectionSet digest is SHA-256 over JCS, never CBOR.

The two known-answer vectors are project vectors, not published standard vectors. Their bytes were
re-serialised independently with Node's JSON.stringify (ECMAScript Number::toString plus sorted keys)
and re-hashed with Node's crypto; both agreed with the Python JCS helper byte for byte, including the
exponent forms ``1e-7`` and ``5e-324``.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

import cbor2
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trinetra.domain.detection import MAX_DETECTIONS, Detection, DetectionSet
from trinetra.domain.digests import DigestSha256
from trinetra.domain.geometry import CanonicalBoxXYXY
from trinetra.jcs import canonicalize, loads_strict
from trinetra.tiir import semantic_output
from trinetra.tiir.deterministic_cbor import encode_cbor
from trinetra.tiir.errors import TiirEncodingError, TiirSchemaError
from trinetra.tiir.semantic_output import (
    SEMANTIC_OUTPUT_SCHEMA,
    semantic_output_bytes,
    semantic_output_digest,
    semantic_output_value,
)

IMAGE = DigestSha256(bytes.fromhex("ab" * 32))


def det(class_index: int, score: float, box: tuple[float, float, float, float]) -> Detection:
    x1, y1, x2, y2 = box
    return Detection(class_index=class_index, score=score, box=CanonicalBoxXYXY(x1=x1, y1=y1, x2=x2, y2=y2))


def dset(detections: list[Detection], *, width: int = 640, height: int = 480,
         image: DigestSha256 = IMAGE) -> DetectionSet:
    return DetectionSet.from_unordered(
        image_member_digest=image, image_width=width, image_height=height, detections=detections
    )


MIXED = [
    det(2, 0.1234567890123, (0.5, 0.25, 639.75, 479.125)),
    det(0, 0.9, (10.0, 20.0, 110.0, 220.5)),
    det(0, 0.9, (10.0, 20.0, 110.0, 220.5)),
    det(4294967295, 1.0, (0.0, 0.0, 640.0, 480.0)),
    det(7, 1e-7, (1e-7, 0.0, 2.0, 3.0)),
    det(8, 5e-324, (1.0, 1.0, 2.0, 2.0)),
]

EMPTY_BYTES = (
    b'{"detections":[],"image_height":480,"image_member_digest":"sha256:'
    + b"ab" * 32
    + b'","image_width":640,"schema":"trinetra_detection_set_v1"}'
)
EMPTY_DIGEST = "sha256:5488133c139b41e2f6f79bdb8eaeb356274dc7cf4e3cde887a47054cca461e52"
MIXED_BYTES = (
    b'{"detections":['
    b'{"box":{"x1":10,"x2":110,"y1":20,"y2":220.5},"class_index":0,"score":0.9},'
    b'{"box":{"x1":10,"x2":110,"y1":20,"y2":220.5},"class_index":0,"score":0.9},'
    b'{"box":{"x1":0.5,"x2":639.75,"y1":0.25,"y2":479.125},"class_index":2,"score":0.1234567890123},'
    b'{"box":{"x1":1e-7,"x2":2,"y1":0,"y2":3},"class_index":7,"score":1e-7},'
    b'{"box":{"x1":1,"x2":2,"y1":1,"y2":2},"class_index":8,"score":5e-324},'
    b'{"box":{"x1":0,"x2":640,"y1":0,"y2":480},"class_index":4294967295,"score":1}'
    b'],"image_height":480,"image_member_digest":"sha256:' + b"ab" * 32
    + b'","image_width":640,"schema":"trinetra_detection_set_v1"}'
)
MIXED_DIGEST = "sha256:a655296c0579d1f4b7fcfc1f89e6321f8f79e1889d6ece4f0280dd0b66500f7a"


def test_known_answer_empty_set() -> None:
    value = dset([])
    assert semantic_output_bytes(value) == EMPTY_BYTES
    assert semantic_output_digest(value).to_wire() == EMPTY_DIGEST


def test_known_answer_mixed_set_with_duplicates_and_extreme_floats() -> None:
    value = dset(MIXED)
    assert semantic_output_bytes(value) == MIXED_BYTES
    assert semantic_output_digest(value).to_wire() == MIXED_DIGEST


def test_digest_is_sha256_of_the_bytes_and_a_digest_value() -> None:
    value = dset(MIXED)
    digest = semantic_output_digest(value)
    assert isinstance(digest, DigestSha256)
    assert digest.raw == hashlib.sha256(semantic_output_bytes(value)).digest()


def test_bytes_are_json_not_cbor_and_are_already_canonical() -> None:
    value = dset(MIXED)
    data = semantic_output_bytes(value)
    assert data[:1] == b"{"
    assert canonicalize(loads_strict(data)) == data
    assert data != cbor2.dumps(semantic_output_value(value), canonical=True)
    with pytest.raises(TiirEncodingError):  # the receipt codec carries neither floats nor text-keyed maps
        encode_cbor(semantic_output_value(value))
    assert SEMANTIC_OUTPUT_SCHEMA == "trinetra_detection_set_v1"


def test_preimage_has_exactly_the_canonical_fields_and_no_record_metadata() -> None:
    value = semantic_output_value(dset(MIXED))
    assert set(value) == {"schema", "image_member_digest", "image_width", "image_height", "detections"}
    for entry in value["detections"]:
        assert set(entry) == {"class_index", "score", "box"}
        assert set(entry["box"]) == {"x1", "y1", "x2", "y2"}


def test_duplicates_are_preserved_and_counted() -> None:
    one = dset([MIXED[1]])
    two = dset([MIXED[1], MIXED[1]])
    assert semantic_output_digest(one) != semantic_output_digest(two)


def test_negative_zero_and_positive_zero_are_one_identity() -> None:
    negative = dset([det(1, 0.5, (-0.0, -0.0, 4.0, 4.0))])
    positive = dset([det(1, 0.5, (0.0, 0.0, 4.0, 4.0))])
    assert semantic_output_bytes(negative) == semantic_output_bytes(positive)


def _variants() -> list[tuple[str, DetectionSet]]:
    base = [det(3, 0.5, (10.0, 20.0, 30.0, 40.0))]
    ulp = math.nextafter(0.5, 1.0)
    return [
        ("image digest", dset(base, image=DigestSha256(bytes.fromhex("ac" * 32)))),
        ("width", dset(base, width=641)),
        ("height", dset(base, height=481)),
        ("class", dset([det(4, 0.5, (10.0, 20.0, 30.0, 40.0))])),
        ("score by one ulp", dset([det(3, ulp, (10.0, 20.0, 30.0, 40.0))])),
        ("x1", dset([det(3, 0.5, (11.0, 20.0, 30.0, 40.0))])),
        ("y1", dset([det(3, 0.5, (10.0, 21.0, 30.0, 40.0))])),
        ("x2", dset([det(3, 0.5, (10.0, 20.0, 31.0, 40.0))])),
        ("y2", dset([det(3, 0.5, (10.0, 20.0, 30.0, 41.0))])),
        ("extra detection", dset([*base, det(3, 0.4, (1.0, 1.0, 2.0, 2.0))])),
        ("no detections", dset([])),
    ]


def test_every_meaningful_field_changes_the_digest() -> None:
    baseline = semantic_output_digest(dset([det(3, 0.5, (10.0, 20.0, 30.0, 40.0))]))
    seen = {baseline}
    for name, variant in _variants():
        digest = semantic_output_digest(variant)
        assert digest != baseline, name
        seen.add(digest)
    assert len(seen) == len(_variants()) + 1


def test_a_different_schema_tag_would_change_the_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    before = semantic_output_digest(dset(MIXED))
    monkeypatch.setattr(semantic_output, "SEMANTIC_OUTPUT_SCHEMA", "trinetra_detection_set_v2")
    assert semantic_output_digest(dset(MIXED)) != before


@pytest.mark.parametrize("bad", [None, {}, [], "x", b"x", 1])
def test_only_an_exact_detection_set_is_accepted(bad: Any) -> None:
    with pytest.raises(TiirSchemaError):
        semantic_output_digest(bad)


def test_largest_permitted_set_is_canonicalisable() -> None:
    many = [det(i % 7, 0.5, (0.0, 0.0, 1.0 + (i % 5), 1.0)) for i in range(MAX_DETECTIONS)]
    value = dset(many)
    data = semantic_output_bytes(value)
    assert len(loads_strict(data)["detections"]) == MAX_DETECTIONS
    assert semantic_output_digest(value).raw == hashlib.sha256(data).digest()


_finite_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_coord = st.floats(min_value=0.0, max_value=300.0, allow_nan=False, allow_infinity=False)


def _above(low: float) -> st.SearchStrategy[float]:
    return st.floats(min_value=low, max_value=320.0, allow_nan=False, allow_infinity=False).filter(
        lambda v: v > low
    )


@st.composite
def _detections(draw: st.DrawFn) -> Detection:
    x1 = draw(_coord)
    y1 = draw(_coord)
    x2 = draw(_above(x1))
    y2 = draw(_above(y1))
    return det(draw(st.integers(0, 2**32 - 1)), draw(_finite_unit), (x1, y1, x2, y2))


@settings(max_examples=150, deadline=None)
@given(items=st.lists(_detections(), max_size=12), seed=st.randoms(use_true_random=False))
def test_arrival_order_never_matters_and_floats_round_trip(items: list[Detection], seed: Any) -> None:
    shuffled = list(items)
    seed.shuffle(shuffled)
    first = dset(items, width=320, height=320)
    second = dset(shuffled, width=320, height=320)
    assert semantic_output_bytes(first) == semantic_output_bytes(second)
    decoded = loads_strict(semantic_output_bytes(first))["detections"]
    for original, entry in zip(first.detections, decoded, strict=True):
        assert float(entry["score"]) == original.score
        assert (float(entry["box"]["x1"]), float(entry["box"]["y1"]),
                float(entry["box"]["x2"]), float(entry["box"]["y2"])) == (
            original.box.x1, original.box.y1, original.box.x2, original.box.y2)
