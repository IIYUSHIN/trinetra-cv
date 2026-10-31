"""IMP-0057 tests: record, locator and stored bytes must agree; disagreement is never repaired."""

from __future__ import annotations

import io
import os

import pytest

from trinetra.domain.asset_record import ActorId, AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.timing import UtcTimestamp
from trinetra.infrastructure.asset_identity import ContentIdentityError, assert_content_identity
from trinetra.infrastructure.asset_immutability import ddl_window
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_store import CasStore


@pytest.fixture
def cas(state_root):
    return CasStore(CasLayout.for_state_root(state_root), max_bytes=10_000)


def admit(store, cas, data=b"model weights", n=1):
    result = cas.admit(io.BytesIO(data))
    record = AssetRecord(
        record_id=AssetId.from_entropy(lambda _n: bytes([n]) * 16), asset_kind=AssetKind.MODEL,
        blob_digest=result.digest, size_bytes=result.size, media_type="application/octet-stream",
        supplied_name="m.pt", created_at=UtcTimestamp.parse("2026-10-31T00:00:00Z"),
        created_by=ActorId("core"),
    )
    with store.unit_of_work() as uow:
        AssetRepository().add(uow, record)
        uow.commit()
    return record


def check(store, cas, record):
    with store.unit_of_work() as uow:
        try:
            assert_content_identity(uow, cas.layout, record)
        finally:
            uow.rollback()


def test_an_honestly_admitted_asset_satisfies_the_invariant(store, cas):
    check(store, cas, admit(store, cas))


def test_the_empty_asset_satisfies_the_invariant(store, cas):
    check(store, cas, admit(store, cas, b""))


def test_corrupted_bytes_violate_the_invariant_and_are_not_repaired(store, cas):
    record = admit(store, cas)
    path = cas.layout.object_path(record.blob_digest)
    os.chmod(path, 0o644)
    path.write_bytes(b"MODEL WEIGHTS")
    os.chmod(path, 0o444)
    with pytest.raises(ContentIdentityError, match="DIGEST_MISMATCH"):
        check(store, cas, record)
    assert path.read_bytes() == b"MODEL WEIGHTS"


def test_a_missing_object_violates_the_invariant(store, cas):
    record = admit(store, cas)
    cas.layout.object_path(record.blob_digest).unlink()
    with pytest.raises(ContentIdentityError, match="MISSING"):
        check(store, cas, record)


def test_a_missing_locator_row_violates_the_invariant(store, cas):
    record = admit(store, cas)
    other = record.evolve(blob_digest=cas.admit(io.BytesIO(b"other")).digest, size_bytes=5)
    with pytest.raises(ContentIdentityError, match="no BlobLocator"):
        check(store, cas, other)


def test_a_record_claiming_a_different_size_violates_the_invariant(store, cas):
    record = admit(store, cas)
    with pytest.raises(ContentIdentityError, match="BlobLocator size"):
        check(store, cas, record.evolve(size_bytes=record.size_bytes + 1))


def test_a_locator_pointing_elsewhere_violates_the_invariant(store, cas):
    record = admit(store, cas)
    with store.unit_of_work() as uow:
        wrong = "cas/sha256/00/00/" + "0" * 64
        uow.connection.exec_driver_sql("UPDATE blob_locators SET locator = ?", (wrong,))
        uow.commit()
    with pytest.raises(ContentIdentityError, match="derives"):
        check(store, cas, record)


def test_a_malformed_stored_object_violates_the_invariant(store, cas):
    record = admit(store, cas)
    os.chmod(cas.layout.object_path(record.blob_digest), 0o644)
    with pytest.raises(ContentIdentityError, match="MALFORMED"):
        check(store, cas, record)


def test_the_error_names_the_machine_readable_status(store, cas):
    record = admit(store, cas)
    cas.layout.object_path(record.blob_digest).unlink()
    with pytest.raises(ContentIdentityError) as info:
        check(store, cas, record)
    assert info.value.details == {"status": "MISSING"}


def test_two_records_on_one_blob_both_fail_when_the_blob_is_corrupt(store, cas):
    a = admit(store, cas, n=1)
    b = admit(store, cas, n=2)
    path = cas.layout.object_path(a.blob_digest)
    os.chmod(path, 0o644)
    path.write_bytes(b"x")
    os.chmod(path, 0o444)
    for record in (a, b):
        with pytest.raises(ContentIdentityError):
            check(store, cas, record)


def test_a_locator_for_another_backend_violates_the_invariant(store, cas):
    record = admit(store, cas)
    with store.unit_of_work() as uow:
        with ddl_window(uow.connection):
            uow.connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
        uow.connection.exec_driver_sql("UPDATE blob_locators SET backend_type = 'S3'")
        uow.commit()
    with pytest.raises(ContentIdentityError, match="derives"):
        check(store, cas, record)
