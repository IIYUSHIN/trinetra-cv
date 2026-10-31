"""IMP-0053 tests: AssetRecord is immutable, identity is content, names are labels."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from trinetra.domain.asset_record import ActorId, AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.base import DomainConventionError
from trinetra.domain.digests import DigestSha256
from trinetra.domain.references import ReferenceId
from trinetra.domain.timing import UtcTimestamp


def fixed(raw):
    return lambda _n: raw


DIGEST = DigestSha256.of(b"weights")
ID = AssetId.from_entropy(fixed(bytes(range(16))))


def make(**overrides):
    base = {
        "record_id": ID, "asset_kind": AssetKind.MODEL, "blob_digest": DIGEST, "size_bytes": 7,
        "media_type": "application/octet-stream", "supplied_name": "model.pt",
        "created_at": UtcTimestamp.parse("2026-10-31T00:00:00Z"), "created_by": ActorId("core"),
    }
    return AssetRecord(**(base | overrides))


def test_a_valid_record_round_trips_through_its_canonical_json():
    record = make()
    assert AssetRecord.model_validate_json(record.canonical_bytes()) == record


def test_the_wire_body_has_the_flat_keys_the_database_binds():
    wire = make().to_wire()
    assert wire["record_id"] == str(ID)
    assert wire["asset_kind"] == "model"
    assert wire["blob_digest"] == DIGEST.to_wire()
    assert wire["size_bytes"] == 7
    assert wire["created_at"] == "2026-10-31T00:00:00Z"
    assert wire["created_by"] == "core"
    assert wire["schema_name"] == "trinetra.asset_record"
    assert wire["schema_version"] == 1
    assert wire["parent_asset_refs"] == []


def test_body_json_is_canonical_and_deterministic():
    a, b = make(), make()
    assert a.body_json() == b.body_json()
    text = a.body_json()
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert a.canonical_digest() == b.canonical_digest()
    assert make(supplied_name="other.pt").canonical_digest() != a.canonical_digest()


def test_no_storage_location_exists_on_the_record():
    names = set(AssetRecord.model_fields)
    assert not {n for n in names if "path" in n or "locator" in n or "uri" in n or "url" in n}


def test_the_record_is_immutable_and_strict():
    record = make()
    with pytest.raises(ValidationError):
        record.size_bytes = 1
    with pytest.raises(ValidationError):
        make(unknown_field=1)
    with pytest.raises(DomainConventionError):
        record.model_copy(update={"size_bytes": 1})
    assert record.evolve(supplied_name="x.pt").supplied_name == "x.pt"


@pytest.mark.parametrize("size", [-1, True, 1.5, "7", 2**53])
def test_size_must_be_a_bounded_exact_int(size):
    with pytest.raises(ValidationError):
        make(size_bytes=size)


@pytest.mark.parametrize("size", [0, 7, 2**53 - 1])
def test_size_boundaries_are_accepted(size):
    assert make(size_bytes=size).size_bytes == size


@pytest.mark.parametrize("media", ["", "text", "Text/Plain", "text/", "/plain", "text/plain; charset=utf-8",
                                   "a/b/c", "text /plain", "te\nxt/plain", "x" * 70 + "/y"])
def test_media_type_must_be_a_lowercase_type_subtype(media):
    with pytest.raises(ValidationError):
        make(media_type=media)


@pytest.mark.parametrize("media", ["application/octet-stream", "image/png", "application/vnd.api+json",
                                   "text/x-c++src"])
def test_ordinary_media_types_are_accepted(media):
    assert make(media_type=media).media_type == media


@pytest.mark.parametrize("name", ["a/b.pt", "..\\evil", "x/", "\\", "tab\there", "nul\x00", " lead", "trail ",
                                  "n" * 256, "é".join(["", ""]) + "é"])
def test_supplied_name_is_a_bounded_label_never_a_path(name):
    with pytest.raises(ValidationError):
        make(supplied_name=name)


@pytest.mark.parametrize("name", ["", "model.pt", "n" * 255, "моdel.pt", "データ.csv"])
def test_supplied_name_boundaries_are_accepted(name):
    assert make(supplied_name=name).supplied_name == name


def test_two_records_may_share_one_blob_with_different_ids_and_names():
    other = AssetId.from_entropy(fixed(bytes(range(1, 17))))
    a, b = make(), make(record_id=other, supplied_name="copy.pt")
    assert a.blob_digest == b.blob_digest
    assert a != b


def test_identity_does_not_come_from_the_name():
    assert make(supplied_name="a.pt").blob_digest == make(supplied_name="b.pt").blob_digest


def test_references_are_bounded_and_unique():
    parent = AssetId.from_entropy(fixed(bytes([9] * 16)))
    ref = ReferenceId.from_entropy(fixed(bytes([8] * 16)))
    assert make(parent_asset_refs=(parent,), source_metadata_refs=(ref,)).parent_asset_refs == (parent,)
    with pytest.raises(ValidationError, match="unique"):
        make(parent_asset_refs=(parent, parent))
    with pytest.raises(ValidationError, match="unique"):
        make(source_metadata_refs=(ref, ref))
    many = tuple(AssetId.from_entropy(fixed(bytes([i + 1] * 16))) for i in range(65))
    with pytest.raises(ValidationError):
        make(parent_asset_refs=many)
    with pytest.raises(ValidationError):
        make(parent_asset_refs=[parent])  # lists are rejected in strict mode


def test_schema_markers_cannot_be_changed():
    with pytest.raises(ValidationError):
        make(schema_version=2)
    with pytest.raises(ValidationError):
        make(schema_name="other")


def test_actor_ids_are_canonical_tokens_that_do_not_equal_other_tokens():
    assert ActorId("core") == ActorId("core")
    assert str(ActorId("core")) == "core"
    with pytest.raises(Exception, match="actor_id"):
        ActorId("Core")


def test_media_type_part_length_boundaries():
    assert make(media_type="a" * 63 + "/" + "b" * 63).media_type
    with pytest.raises(ValidationError):
        make(media_type="a" * 64 + "/b")
    with pytest.raises(ValidationError):
        make(media_type="a/" + "b" * 64)


def test_source_metadata_references_are_capped_at_64():
    refs = tuple(ReferenceId.from_entropy(fixed(bytes([i + 1] * 16))) for i in range(65))
    assert len(make(source_metadata_refs=refs[:64]).source_metadata_refs) == 64
    with pytest.raises(ValidationError):
        make(source_metadata_refs=refs)
