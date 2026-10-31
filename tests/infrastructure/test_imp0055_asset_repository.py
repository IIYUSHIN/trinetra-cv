"""IMP-0055 tests: the repository stores and re-verifies AssetRecords; it trusts nothing it reads."""

from __future__ import annotations

import json

import pytest

from persistence_support import sha
from trinetra.domain.asset_record import ActorId, AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp
from trinetra.infrastructure.asset_immutability import ddl_window
from trinetra.infrastructure.asset_repository import (
    AssetExistsError,
    AssetIntegrityError,
    AssetRepository,
)
from trinetra.infrastructure.cas_layout import locator_for
from trinetra.timeutil import FixedClock, parse_utc

NOW = "2026-10-31T00:00:00Z"


def make_id(n):
    return AssetId.from_entropy(lambda _n: bytes([n]) * 16)


def make_record(n=1, data=b"weights", name="model.pt", kind=AssetKind.MODEL):
    return AssetRecord(
        record_id=make_id(n), asset_kind=kind, blob_digest=DigestSha256.of(data), size_bytes=len(data),
        media_type="application/octet-stream", supplied_name=name, created_at=UtcTimestamp.parse(NOW),
        created_by=ActorId("core"),
    )


@pytest.fixture
def repo():
    return AssetRepository(FixedClock(parse_utc("2026-11-01T00:00:00Z")))


def test_add_then_get_returns_an_equal_record(store, repo):
    record = make_record()
    with store.unit_of_work() as uow:
        repo.add(uow, record)
        uow.commit()
    with store.unit_of_work() as uow:
        assert repo.get(uow, record.record_id) == record
        uow.rollback()


def test_add_registers_the_blob_locator_without_a_local_path(store, repo, raw):
    record = make_record()
    with store.unit_of_work() as uow:
        repo.add(uow, record)
        uow.commit()
    rows = raw.rows("SELECT blob_digest, size_bytes, backend_type, locator, verification_status, "
                    "last_verified_at FROM blob_locators")
    assert rows == [(record.blob_digest.to_wire(), 7, "FILESYSTEM_CAS", locator_for(record.blob_digest),
                     "VERIFIED", "2026-11-01T00:00:00Z")]
    assert not rows[0][3].startswith("/")


def test_one_blob_may_back_two_records_and_its_locator_is_kept_once(store, repo, raw):
    with store.unit_of_work() as uow:
        repo.add(uow, make_record(1, name="a.pt"))
        repo.add(uow, make_record(2, name="b.pt"))
        uow.commit()
    assert raw.rows("SELECT count(*) FROM blob_locators") == [(1,)]
    assert raw.rows("SELECT count(*) FROM asset_records") == [(2,)]


def test_a_duplicate_id_is_refused_and_the_original_survives(store, repo):
    record = make_record()
    with store.unit_of_work() as uow:
        repo.add(uow, record)
        uow.commit()
    with store.unit_of_work() as uow:
        with pytest.raises(AssetExistsError):
            repo.add(uow, make_record(1, name="other.pt"))
        uow.rollback()
    with store.unit_of_work() as uow:
        assert repo.get(uow, record.record_id).supplied_name == "model.pt"
        uow.rollback()


def test_get_of_an_unknown_id_is_none_and_exists_agrees(store, repo):
    with store.unit_of_work() as uow:
        assert repo.get(uow, make_id(9)) is None
        assert repo.exists(uow, make_id(9)) is False
        repo.add(uow, make_record(9))
        assert repo.exists(uow, make_id(9)) is True
        assert repo.count(uow) == 1
        uow.rollback()


def test_a_rolled_back_add_leaves_nothing(store, repo, raw):
    with store.unit_of_work() as uow:
        repo.add(uow, make_record())
        uow.rollback()
    assert raw.rows("SELECT count(*) FROM asset_records") == [(0,)]
    assert raw.rows("SELECT count(*) FROM blob_locators") == [(0,)]


def test_the_database_itself_refuses_a_size_that_disagrees_with_the_blob(store, repo):
    with store.unit_of_work() as uow:
        repo.add(uow, make_record(1, data=b"weights"))
        liar = make_record(2).evolve(size_bytes=3)
        with pytest.raises(Exception, match="blob identity"):
            repo.add(uow, liar)
        uow.rollback()


def edit(store, set_clause, params):
    with store.unit_of_work() as uow:
        with ddl_window(uow.connection):
            uow.connection.exec_driver_sql("DROP TRIGGER asset_records_no_update")
        with ddl_window(uow.connection):
            uow.connection.exec_driver_sql("DROP TRIGGER asset_records_size_matches_blob")
        uow.connection.exec_driver_sql(f"UPDATE asset_records SET {set_clause}", params)  # noqa: S608
        uow.commit()


def stored(store, repo, record):
    with store.unit_of_work() as uow:
        repo.add(uow, record)
        uow.commit()


def read(store, repo, record):
    with store.unit_of_work() as uow:
        try:
            return repo.get(uow, record.record_id)
        finally:
            uow.rollback()


def test_a_tampered_body_digest_is_detected(store, repo):
    record = make_record()
    stored(store, repo, record)
    edit(store, "body_digest = ?", (sha(b"forged"),))
    with pytest.raises(AssetIntegrityError, match="digest"):
        read(store, repo, record)


def test_a_body_edited_consistently_but_not_canonically_is_detected(store, repo):
    record = make_record()
    stored(store, repo, record)
    pretty = json.dumps(json.loads(record.body_json()), indent=2)
    edit(store, "body_json = ?, body_digest = ?", (pretty, sha(pretty.encode())))
    with pytest.raises(AssetIntegrityError, match="canonical"):
        read(store, repo, record)


def test_a_body_that_is_not_a_valid_record_is_detected(store, repo):
    record = make_record()
    stored(store, repo, record)
    bad = json.loads(record.body_json())
    bad["unexpected"] = 1
    text = json.dumps(bad, sort_keys=True, separators=(",", ":"))
    edit(store, "body_json = ?, body_digest = ?", (text, sha(text.encode())))
    with pytest.raises(AssetIntegrityError, match="valid AssetRecord"):
        read(store, repo, record)


def test_a_column_edited_behind_the_body_is_detected(store, repo):
    record = make_record()
    stored(store, repo, record)
    with store.unit_of_work() as uow:
        for trigger in ("asset_records_no_update", "asset_records_size_matches_blob"):
            with ddl_window(uow.connection):
                uow.connection.exec_driver_sql(f"DROP TRIGGER {trigger}")
        with ddl_window(uow.connection):
            uow.connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
        uow.connection.exec_driver_sql("UPDATE asset_records SET supplied_name = 'swapped.pt'")
        uow.commit()
    with pytest.raises(AssetIntegrityError, match="column"):
        read(store, repo, record)


def test_count_tracks_commits(store, repo):
    with store.unit_of_work() as uow:
        assert repo.count(uow) == 0
        uow.rollback()
    stored(store, repo, make_record(1))
    stored(store, repo, make_record(2))
    with store.unit_of_work() as uow:
        assert repo.count(uow) == 2
        uow.rollback()


def test_default_clock_is_the_system_clock(store):
    repo = AssetRepository()
    with store.unit_of_work() as uow:
        repo.add(uow, make_record())
        uow.commit()
    with store.unit_of_work() as uow:
        stamp = uow.connection.exec_driver_sql("SELECT last_verified_at FROM blob_locators").fetchone()[0]
        uow.rollback()
    assert stamp.startswith("20")
    assert stamp.endswith("Z")
