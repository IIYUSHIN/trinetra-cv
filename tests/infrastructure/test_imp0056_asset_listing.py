"""IMP-0056 tests: keyset paging is stable, bounded, filterable and injection-proof."""

from __future__ import annotations

import base64

import pytest

from trinetra.domain.asset_record import ActorId, AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp
from trinetra.infrastructure.asset_repository import (
    AssetQueryError,
    AssetRepository,
    decode_cursor,
    encode_cursor,
)


def make(n, second, kind=AssetKind.DATASET, data=None):
    data = data or f"content-{n}".encode()
    return AssetRecord(
        record_id=AssetId.from_entropy(lambda _n: bytes([n]) * 16), asset_kind=kind,
        blob_digest=DigestSha256.of(data), size_bytes=len(data), media_type="application/octet-stream",
        supplied_name=f"f{n}", created_at=UtcTimestamp.parse(f"2026-10-31T00:00:{second:02d}Z"),
        created_by=ActorId("core"),
    )


@pytest.fixture
def repo():
    return AssetRepository()


@pytest.fixture
def seeded(store, repo):
    records = [make(1, 5), make(2, 1), make(3, 5), make(4, 3, AssetKind.MODEL), make(5, 9)]
    with store.unit_of_work() as uow:
        for r in records:
            repo.add(uow, r)
        uow.commit()
    return records


def page(store, repo, **kw):
    with store.unit_of_work() as uow:
        try:
            return repo.list_page(uow, **kw)
        finally:
            uow.rollback()


def ids(p):
    return [str(r.record_id) for r in p.items]


def expected_order(records):
    ordered = sorted(records, key=lambda r: (r.created_at.to_wire(), str(r.record_id)))
    return [str(r.record_id) for r in ordered]


def test_listing_is_ordered_by_time_then_id(store, repo, seeded):
    p = page(store, repo)
    assert ids(p) == expected_order(seeded)
    assert p.next_cursor is None


def test_paging_walks_every_record_once_in_the_same_order(store, repo, seeded):
    seen, cursor = [], None
    for _ in range(10):  # bounded: a cursor that never ends must fail, not hang
        p = page(store, repo, limit=2, after=cursor)
        assert len(p.items) <= 2
        seen += ids(p)
        cursor = p.next_cursor
        if cursor is None:
            break
    else:
        pytest.fail("paging did not terminate")
    assert seen == expected_order(seeded)


def test_an_exact_multiple_of_the_page_size_ends_without_a_dangling_cursor(store, repo, seeded):
    first = page(store, repo, limit=5)
    assert first.next_cursor is None
    assert len(first.items) == 5
    four = page(store, repo, limit=4)
    assert four.next_cursor is not None
    assert len(page(store, repo, limit=4, after=four.next_cursor).items) == 1


def test_records_with_equal_timestamps_are_never_skipped_or_repeated(store, repo, seeded):
    p1 = page(store, repo, limit=3)
    p2 = page(store, repo, limit=3, after=p1.next_cursor)
    assert set(ids(p1)).isdisjoint(ids(p2))
    assert len(ids(p1)) + len(ids(p2)) == 5


def test_filter_by_kind_and_blob(store, repo, seeded):
    models = page(store, repo, kind=AssetKind.MODEL).items
    assert [r.asset_kind for r in models] == [AssetKind.MODEL]
    one = page(store, repo, blob_digest=DigestSha256.of(b"content-2"))
    assert ids(one) == [str(seeded[1].record_id)]
    assert page(store, repo, kind=AssetKind.REFERENCE).items == ()
    none = page(store, repo, kind=AssetKind.MODEL, blob_digest=DigestSha256.of(b"content-2"))
    assert none.items == ()


def test_filters_and_cursor_compose(store, repo, seeded):
    p1 = page(store, repo, kind=AssetKind.DATASET, limit=2)
    p2 = page(store, repo, kind=AssetKind.DATASET, limit=2, after=p1.next_cursor)
    assert len(p1.items) + len(p2.items) == 4
    assert all(r.asset_kind is AssetKind.DATASET for r in (*p1.items, *p2.items))


def test_an_empty_store_lists_empty(store, repo):
    p = page(store, repo)
    assert (p.items, p.next_cursor) == ((), None)


@pytest.mark.parametrize("limit", [0, -1, 201, True])
def test_limit_must_be_within_bounds(store, repo, limit):
    with pytest.raises(AssetQueryError, match="limit"):
        page(store, repo, limit=limit)


def test_limit_boundaries_are_accepted(store, repo, seeded):
    assert len(page(store, repo, limit=1).items) == 1
    assert len(page(store, repo, limit=200).items) == 5


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.mark.parametrize("cursor", [
    "", "!!!", "x" * 300, b64("nonsense"), b64("2026-10-31T00:00:00Z|"), b64("bad|asset_" + "0" * 32),
    b64("2026-10-31T00:00:00Z|asset_" + "0" * 32 + "|extra"), b64("2026-10-31T00:00:00Z|case_" + "0" * 32),
    b64("2026-10-31T00:00:00Z' OR '1'='1|asset_" + "0" * 32), "é",
    base64.urlsafe_b64encode(b"2026-10-31T00:00:00Z|asset_" + b"0" * 32).decode(),  # padded: non-canonical
])
def test_malformed_cursors_are_refused(store, repo, seeded, cursor):
    with pytest.raises(AssetQueryError):
        page(store, repo, after=cursor)


def test_cursor_round_trip():
    cursor = encode_cursor("2026-10-31T00:00:05Z", "asset_" + "a" * 32)
    assert decode_cursor(cursor) == ("2026-10-31T00:00:05Z", "asset_" + "a" * 32)


def test_a_tampered_row_surfaces_in_listing_too(store, repo, seeded):
    with store.unit_of_work() as uow:
        uow.connection.exec_driver_sql("DROP TRIGGER asset_records_no_update")
        uow.connection.exec_driver_sql("UPDATE asset_records SET body_digest = ? WHERE record_id = ?",
                                       ("sha256:" + "0" * 64, str(seeded[0].record_id)))
        uow.commit()
    from trinetra.infrastructure.asset_repository import AssetIntegrityError

    with pytest.raises(AssetIntegrityError):
        page(store, repo)


def test_listing_uses_the_ordering_index(store, repo, seeded):
    with store.unit_of_work() as uow:
        plan = uow.connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN SELECT record_id FROM asset_records WHERE asset_kind = ? "
            "ORDER BY created_at, record_id LIMIT 10", ("dataset",)
        ).fetchall()
        uow.rollback()
    assert "ix_asset_records_kind" in " ".join(str(r) for r in plan)


def test_an_oversized_cursor_is_rejected_before_any_decoding(monkeypatch):
    import trinetra.infrastructure.asset_repository as module

    def forbidden(_s):
        raise AssertionError("decoded an oversized cursor")

    monkeypatch.setattr(module.base64, "urlsafe_b64decode", forbidden)
    with pytest.raises(AssetQueryError):
        decode_cursor("A" * 257)
