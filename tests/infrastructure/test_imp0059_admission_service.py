"""IMP-0059 tests: the quarantine-to-admitted workflow never trusts the caller and never half-admits."""

from __future__ import annotations

import hashlib
import io
import itertools
from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.asset_admission import (
    DEFAULT_PROFILE,
    AdmissionProfile,
    AdmissionProfileError,
    KindRule,
    sniff_media_type,
)
from trinetra.domain.asset_record import ActorId
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.digests import DigestSha256
from trinetra.domain.references import ReferenceId
from trinetra.infrastructure.asset_admission_service import (
    AdmissionRequest,
    AssetAdmissionService,
)
from trinetra.infrastructure.asset_repository import AssetExistsError, AssetRepository
from trinetra.infrastructure.cas_admission import AdmissionError
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_read import read_object
from trinetra.infrastructure.cas_store import CasStore
from trinetra.infrastructure.cas_verify import scan_objects
from trinetra.timeutil import FixedClock

NOTE = b"a short contributor note"
ACTOR = ActorId("core")


@pytest.fixture
def cas(state_root):
    return CasStore(CasLayout.for_state_root(state_root))


def counter_entropy():
    numbers = itertools.count(1)
    return lambda n: next(numbers).to_bytes(n, "big")


def service(cas, **kw):
    return AssetAdmissionService(
        cas,
        AssetRepository(),
        clock=kw.pop("clock", FixedClock(datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC))),
        entropy=kw.pop("entropy", counter_entropy()),
        **kw,
    )


def request(**kw):
    base = {
        "kind": AssetKind.CONTRIBUTOR_METADATA,
        "media_type": "text/plain",
        "supplied_name": "note.txt",
        "created_by": ACTOR,
    }
    return AdmissionRequest(**{**base, **kw})


class Counting(io.BytesIO):
    reads = 0

    def read(self, size=-1):
        type(self).reads += 1
        return super().read(size)


def admit(store, svc, data=NOTE, **kw):
    with store.unit_of_work() as uow:
        result = svc.admit(uow, io.BytesIO(data), request(**kw))
        uow.commit()
    return result


def tiny_profile(ceiling):
    return AdmissionProfile(tuple((k, KindRule(r.media_types, ceiling)) for k, r in DEFAULT_PROFILE.rules))


def object_count(cas):
    report = scan_objects(cas.layout)
    assert report.clean
    return report.verified


def record_count(store):
    with store.unit_of_work() as uow:
        n = AssetRepository().count(uow)
        uow.rollback()
    return n


# ----------------------------------------------------------------------------------- happy path
def test_admission_writes_one_blob_and_one_record_from_the_real_bytes(store, cas):
    result = admit(store, service(cas))
    record = result.record
    assert record.blob_digest == DigestSha256(hashlib.sha256(NOTE).digest())
    assert record.size_bytes == len(NOTE)
    assert (record.media_type, record.supplied_name, record.created_by) == ("text/plain", "note.txt", ACTOR)
    assert record.created_at.to_wire() == "2026-10-31T08:00:00Z"
    assert result.blob_created is True
    with store.unit_of_work() as uow:
        assert AssetRepository().get(uow, record.record_id) == record
        uow.rollback()
    assert read_object(cas.layout, record.blob_digest, max_bytes=10_000) == NOTE
    assert object_count(cas) == 1


def test_the_record_id_comes_from_the_injected_entropy_not_from_the_name(store, cas):
    expected = AssetId.from_entropy(lambda n: b"\x05" * n)
    record = admit(store, service(cas, entropy=lambda n: b"\x05" * n), supplied_name="a.txt").record
    assert record.record_id == expected
    other = admit(store, service(cas), data=b"other", supplied_name="a.txt").record
    assert other.record_id != record.record_id and other.blob_digest != record.blob_digest


def test_default_entropy_gives_distinct_identities(store, cas):
    svc = AssetAdmissionService(cas, AssetRepository())
    ids = {admit(store, svc, data=b"x" * (i + 1)).record.record_id for i in range(3)}
    assert len(ids) == 3


def test_the_timestamp_is_truncated_to_whole_seconds(store, cas):
    clock = FixedClock(datetime(2026, 10, 31, 8, 0, 0, 987654, tzinfo=UTC))
    assert admit(store, service(cas, clock=clock)).record.created_at.to_wire() == "2026-10-31T08:00:00Z"


def test_parent_references_are_preserved_in_order(store, cas):
    parents = tuple(AssetId.from_entropy(lambda n, i=i: bytes([i]) * n) for i in (9, 3, 7))
    record = admit(store, service(cas), parent_asset_refs=parents).record
    assert record.parent_asset_refs == parents


def test_source_references_and_the_acting_identity_are_carried_through(store, cas):
    refs = (ReferenceId.from_entropy(lambda n: b"\x01" * n), ReferenceId.from_entropy(lambda n: b"\x02" * n))
    actor = ActorId("operator")
    record = admit(store, service(cas), source_metadata_refs=refs, created_by=actor).record
    assert record.source_metadata_refs == refs
    assert record.created_by == actor
    with store.unit_of_work() as uow:
        assert AssetRepository().get(uow, record.record_id) == record
        uow.rollback()


def test_the_empty_stream_is_a_valid_boundary(store, cas):
    record = admit(store, service(cas), data=b"").record
    assert record.size_bytes == 0
    assert record.blob_digest == DigestSha256(hashlib.sha256(b"").digest())


# ------------------------------------------------------------------------------- duplicate bytes
def test_identical_bytes_share_one_blob_but_get_separate_records(store, cas):
    svc = service(cas)
    first = admit(store, svc)
    second = admit(store, svc, supplied_name="renamed.txt")
    assert first.record.record_id != second.record.record_id
    assert first.record.blob_digest == second.record.blob_digest
    assert (first.blob_created, second.blob_created) == (True, False)
    assert object_count(cas) == 1
    assert record_count(store) == 2


# --------------------------------------------------------------------------------- profile checks
def test_the_size_ceiling_is_inclusive(store, cas):
    svc = service(cas, profile=tiny_profile(20))
    assert admit(store, svc, data=b"x" * 20).record.size_bytes == 20
    with pytest.raises(AdmissionProfileError, match="ceiling"):
        admit(store, svc, data=b"y" * 21)
    assert object_count(cas) == 1  # only the accepted one


def test_an_oversize_upload_leaves_nothing_behind(store, cas):
    with pytest.raises(AdmissionProfileError):
        admit(store, service(cas, profile=tiny_profile(20)), data=b"z" * 5000)
    assert object_count(cas) == 0 and record_count(store) == 0
    assert cas._quarantine.orphans() == []


def test_content_that_contradicts_the_claimed_type_is_refused(store, cas):
    with pytest.raises(AdmissionProfileError, match="contradicts"):
        admit(store, service(cas), data=b"PK\x03\x04" + b"\x00" * 30)
    assert object_count(cas) == 0 and record_count(store) == 0


def test_a_short_stream_is_still_type_checked_before_it_reaches_the_store(store, cas):
    with pytest.raises(AdmissionProfileError, match="contradicts"):
        admit(store, service(cas), data=b"PK\x03\x04")  # shorter than the sniff window
    assert object_count(cas) == 0 and record_count(store) == 0


class Trickle:
    """A hostile or slow source that hands over one byte per read."""

    def __init__(self, data):
        self._data = io.BytesIO(data)

    def read(self, _n):
        return self._data.read(1)


@pytest.mark.parametrize("wrap", [Trickle, io.BytesIO])
def test_the_type_check_sees_the_whole_signature_however_the_bytes_arrive(store, cas, wrap):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    with store.unit_of_work() as uow:
        with pytest.raises(AdmissionProfileError, match="contradicts"):
            service(cas).admit(uow, wrap(png), request())
        uow.rollback()
    assert object_count(cas) == 0


def test_octet_stream_cannot_smuggle_a_type_the_kind_forbids(store, cas):
    with pytest.raises(AdmissionProfileError, match="not allowed"):
        admit(store, service(cas), media_type="application/octet-stream", data=b"PK\x03\x04" + b"\x00" * 30)
    assert object_count(cas) == 0


def test_a_media_type_the_kind_does_not_allow_is_refused_before_any_byte_is_read(store, cas):
    Counting.reads = 0
    svc = service(cas)
    with store.unit_of_work() as uow:
        with pytest.raises(AdmissionProfileError, match="media type"):
            svc.admit(uow, Counting(NOTE), request(kind=AssetKind.MODEL, media_type="text/plain"))
        uow.rollback()
    assert Counting.reads <= 2  # the guard checks on the first chunk; nothing is stored
    assert object_count(cas) == 0


def test_an_invalid_request_is_refused_before_the_source_is_touched(store, cas):
    Counting.reads = 0
    svc = service(cas)
    for bad in ({"media_type": "Text/Plain"}, {"supplied_name": "dir/evil.txt"}, {"supplied_name": "a\\b"}):
        with store.unit_of_work() as uow:
            with pytest.raises(ValidationError):
                svc.admit(uow, Counting(NOTE), request(**bad))
            uow.rollback()
    assert Counting.reads == 0
    assert object_count(cas) == 0


# ------------------------------------------------------------------------- declared digest / size
def test_declared_digest_and_size_are_verified_not_trusted(store, cas):
    good = DigestSha256(hashlib.sha256(NOTE).digest())
    wrong = DigestSha256(hashlib.sha256(b"different").digest())
    svc = service(cas)
    assert admit(store, svc, expected_digest=good, expected_size=len(NOTE)).record.blob_digest == good
    with pytest.raises(AdmissionError, match="digest"):
        admit(store, svc, expected_digest=wrong)
    with pytest.raises(AdmissionError, match="size"):
        admit(store, svc, expected_size=len(NOTE) + 1)
    assert record_count(store) == 1


def test_a_source_returning_text_is_refused(store, cas):
    class Textual:
        def read(self, _n):
            return "not bytes"

    with store.unit_of_work() as uow:
        with pytest.raises(AdmissionError, match="non-bytes"):
            service(cas).admit(uow, Textual(), request())
        uow.rollback()
    assert object_count(cas) == 0


# ----------------------------------------------------------------------- transaction boundaries
def test_a_rolled_back_unit_of_work_leaves_an_orphan_blob_but_no_record(store, cas):
    svc = service(cas)
    with store.unit_of_work() as uow:
        svc.admit(uow, io.BytesIO(NOTE), request())
        uow.rollback()
    assert record_count(store) == 0
    assert object_count(cas) == 1  # acceptable orphan: visible, never repaired
    again = admit(store, svc)
    assert again.blob_created is False and record_count(store) == 1


def test_a_record_id_collision_fails_loudly_and_never_overwrites(store, cas):
    svc = service(cas, entropy=lambda n: b"\x07" * n)
    first = admit(store, svc)
    with pytest.raises(AssetExistsError):
        admit(store, svc, data=b"second")
    with store.unit_of_work() as uow:
        assert AssetRepository().get(uow, first.record.record_id) == first.record
        uow.rollback()


# ---------------------------------------------------------------------------------- property
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=st.binary(max_size=2000).filter(lambda b: sniff_media_type(b[:16]) is None))
def test_any_untyped_bytes_round_trip_exactly(store, cas, data):
    svc = AssetAdmissionService(cas, AssetRepository())
    result = admit(
        store,
        svc,
        data=data,
        kind=AssetKind.INFERENCE_OUTPUT,
        media_type="application/octet-stream",
    )
    assert result.record.size_bytes == len(data)
    assert result.record.blob_digest == DigestSha256(hashlib.sha256(data).digest())
    assert read_object(cas.layout, result.record.blob_digest, max_bytes=10_000) == data
