"""IMP-0030 tests: AssetRef."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.asset_refs import AssetRef
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.case import CaseId
from trinetra.domain.digests import ContentIdentity


def make(**over):
    base = {"asset_id": AssetId.generate(), "asset_kind": AssetKind.MODEL,
            "content": ContentIdentity.of(b"weights")}
    return AssetRef(**{**base, **over})


def test_wire_form_roundtrip_and_canonical_digest():
    ref = make()
    wire = ref.to_wire()
    assert set(wire) == {"asset_id", "asset_kind", "content"} and wire["asset_kind"] == "model"
    assert wire["content"] == {"digest": ContentIdentity.of(b"weights").digest.to_wire(), "size_bytes": 7}
    assert AssetRef(**wire) == ref and AssetRef(**wire).canonical_digest() == ref.canonical_digest()


def test_resolves_to_requires_same_id_and_same_content():
    ref = make()
    assert ref.resolves_to(ref.asset_id, ref.content)
    assert not ref.resolves_to(AssetId.generate(), ref.content)
    assert not ref.resolves_to(ref.asset_id, ContentIdentity.of(b"other"))
    other_size = ContentIdentity(digest=ref.content.digest, size_bytes=ref.content.size_bytes + 1)
    assert not ref.resolves_to(ref.asset_id, other_size)


def test_ids_of_other_kinds_cannot_stand_in_for_an_asset_id():
    with pytest.raises(ValidationError):
        make(asset_id=CaseId.generate())
    with pytest.raises(ValidationError):
        make(asset_id=str(CaseId.generate()))


@pytest.mark.parametrize("kind", ["MODEL", "Model", "weights", None, 1])
def test_kind_must_be_an_exact_member(kind):
    with pytest.raises(ValidationError):
        make(asset_kind=kind)


def test_content_is_required_and_strict():
    for bad in (None, {"digest": "sha256:" + "0" * 64}, {"digest": "x", "size_bytes": 1}, b"weights"):
        with pytest.raises(ValidationError):
            make(content=bad)
    with pytest.raises(ValidationError):
        AssetRef(asset_id=AssetId.generate(), asset_kind=AssetKind.MODEL)


def test_equality_is_by_value_and_immutable():
    a = make()
    assert a == AssetRef(**a.to_wire()) and hash(a) == hash(AssetRef(**a.to_wire()))
    assert a != a.evolve(asset_kind=AssetKind.DATASET)
    with pytest.raises(ValidationError):
        a.asset_kind = AssetKind.DATASET  # type: ignore[misc]
