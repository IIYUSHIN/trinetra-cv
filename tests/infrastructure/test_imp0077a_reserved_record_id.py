"""IMP-0077 (part): an admission may use an id reserved earlier; without one nothing changes."""

from __future__ import annotations

import io
import itertools
from datetime import UTC, datetime

import pytest

from trinetra.domain.asset_record import ActorId
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.infrastructure.asset_admission_service import AdmissionRequest, AssetAdmissionService
from trinetra.infrastructure.asset_repository import AssetExistsError, AssetRepository
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_store import CasStore
from trinetra.timeutil import FixedClock

ACTOR = ActorId("core")


def make(state_root):
    numbers = itertools.count(1)
    return AssetAdmissionService(
        CasStore(CasLayout.for_state_root(state_root)),
        AssetRepository(),
        clock=FixedClock(datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC)),
        entropy=lambda n: next(numbers).to_bytes(n, "big"),
    )


def request(**kw):
    base = {"kind": AssetKind.CONTRIBUTOR_METADATA, "media_type": "text/plain",
            "supplied_name": "note.txt", "created_by": ACTOR}  # fmt: skip
    return AdmissionRequest(**{**base, **kw})


def reserved(n=0xABCDEF):
    return AssetId.from_entropy(lambda size: n.to_bytes(size, "big"))


def test_no_reserved_id_means_the_service_generates_one_as_before(store, state_root):
    with store.unit_of_work() as uow:
        admitted = make(state_root).admit(uow, io.BytesIO(b"note"), request())
        uow.commit()
    assert admitted.record.record_id == AssetId.from_entropy(lambda n: (1).to_bytes(n, "big"))


def test_a_reserved_id_becomes_the_record_id_and_consumes_no_entropy(store, state_root):
    service = make(state_root)
    with store.unit_of_work() as uow:
        first = service.admit(uow, io.BytesIO(b"one"), request(record_id=reserved()))
        second = service.admit(uow, io.BytesIO(b"two"), request())
        uow.commit()
    assert first.record.record_id == reserved()
    assert second.record.record_id == AssetId.from_entropy(lambda n: (1).to_bytes(n, "big"))


def test_the_reserved_id_is_the_id_the_repository_stores(store, state_root):
    with store.unit_of_work() as uow:
        make(state_root).admit(uow, io.BytesIO(b"keep"), request(record_id=reserved()))
        uow.commit()
    with store.unit_of_work() as uow:
        assert AssetRepository().get(uow, reserved()) is not None
        uow.rollback()


def test_reusing_a_reserved_id_is_refused_and_leaves_the_first_record_intact(store, state_root):
    service = make(state_root)
    with store.unit_of_work() as uow:
        service.admit(uow, io.BytesIO(b"first"), request(record_id=reserved()))
        uow.commit()
    with pytest.raises(AssetExistsError), store.unit_of_work() as uow:
        service.admit(uow, io.BytesIO(b"second"), request(record_id=reserved()))
    with store.unit_of_work() as uow:
        assert AssetRepository().get(uow, reserved()).size_bytes == len(b"first")
        uow.rollback()


def test_the_request_field_defaults_to_none_and_is_optional():
    assert request().record_id is None
