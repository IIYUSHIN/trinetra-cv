"""IMP-0097 tests: a commit interrupted at any point is repaired by retrying it, with nothing doubled."""

from __future__ import annotations

import pytest

from trinetra.application.errors import TransferRejectedError
from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState
from trinetra.domain.case_assets import CaseAssetRole

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class DiesBefore:
    """A Case store that fails before anything is written, once."""

    def __init__(self, inner):
        self.inner, self.armed = inner, True

    def get(self, case_id):
        return self.inner.get(case_id)

    def create(self, case, *, intake_id):
        if self.armed:
            self.armed = False
            raise OSError("disk went away")
        self.inner.create(case, intake_id=intake_id)


class DiesAfter:
    """A Case store whose write succeeds and then the process 'dies' before the caller can notice."""

    def __init__(self, inner):
        self.inner, self.armed = inner, True

    def get(self, case_id):
        return self.inner.get(case_id)

    def create(self, case, *, intake_id):
        self.inner.create(case, intake_id=intake_id)
        if self.armed:
            self.armed = False
            raise KeyboardInterrupt


def upload_minimal(rig, label=None):
    rig.new_intake(label)
    rig.upload(PackageSlot.DATASET, "coco", "application/zip")
    rig.upload(PackageSlot.MODEL, "onnx", data=b"\x08\x07" + b"m" * 30)
    rig.upload(PackageSlot.REFERENCE, media="application/zip")


def ingested(rig):
    return rig.event_types().count("asset_ingested")


def test_dying_before_the_case_is_written_leaves_admitted_assets_and_an_open_intake(rig):
    upload_minimal(rig)
    with pytest.raises(OSError, match="disk went away"):
        rig.service_over(DiesBefore(rig.cases)).commit(rig.intake)
    assert ingested(rig) == 3
    assert rig.registry.require(rig.intake).intake_state is IntakeState.OPEN
    assert rig.cases.get(rig.registry.require(rig.intake).reserved_case_id) is None
    assert "case_created" not in rig.event_types()


def test_retrying_after_that_stores_the_case_without_admitting_anything_twice(rig):
    upload_minimal(rig)
    flaky = DiesBefore(rig.cases)
    service = rig.service_over(flaky)
    with pytest.raises(OSError, match="disk went away"):
        service.commit(rig.intake)
    result = service.commit(rig.intake)
    assert result.case_created is True
    assert ingested(rig) == 3  # still exactly the three original ingestions
    assert rig.event_types().count("case_created") == 1
    assert rig.registry.require(rig.intake).intake_state is IntakeState.COMMITTED
    assert rig.cases.get(result.case.case_id) == result.case


def test_dying_right_after_the_case_is_written_is_repaired_by_a_retry(rig):
    upload_minimal(rig)
    flaky = DiesAfter(rig.cases)
    service = rig.service_over(flaky)
    with pytest.raises(KeyboardInterrupt):
        service.commit(rig.intake)
    session = rig.registry.require(rig.intake)
    assert session.intake_state is IntakeState.OPEN  # the intake never learned of it
    stored = rig.cases.get(session.reserved_case_id)
    assert stored is not None
    result = service.commit(rig.intake)
    assert result.case == stored
    assert result.case_created is False
    assert rig.event_types().count("case_created") == 1
    assert rig.registry.require(rig.intake).intake_state is IntakeState.COMMITTED


def test_every_retry_after_success_is_a_harmless_replay(rig):
    upload_minimal(rig)
    first = rig.service.commit(rig.intake)
    before = rig.event_types()
    for _ in range(3):
        again = rig.service.commit(rig.intake)
        assert again.case == first.case and again.case_created is False
    assert rig.event_types() == before


def test_a_rejected_upload_mid_commit_keeps_the_good_assets_and_a_replacement_completes_it(rig):
    rig.new_intake()
    data = rig.upload(PackageSlot.DATASET, "coco", "application/zip")
    bad_model = rig.upload(PackageSlot.MODEL, "onnx", data=PNG)  # an image is not a model
    with pytest.raises(TransferRejectedError):
        rig.service.commit(rig.intake)
    session = rig.registry.require(rig.intake)
    assert session.intake_state is IntakeState.OPEN
    states = {t.transfer_id: t.transfer_state for t in session.transfers}
    assert states[data.transfer_id] is TransferState.ADMITTED
    assert states[bad_model.transfer_id] is TransferState.FAILED
    assert ingested(rig) == 1
    assert rig.lookup.exists(bad_model.reserved_asset_id) is False

    good_model = rig.upload(PackageSlot.MODEL, "onnx", data=b"\x08\x07" + b"m" * 30)
    result = rig.service.commit(rig.intake)
    assert ingested(rig) == 2  # the dataset was not admitted again
    ids = {lk.role: lk.asset_id for lk in result.case.asset_links}
    assert ids[CaseAssetRole.DATASET] == data.reserved_asset_id
    assert ids[CaseAssetRole.MODEL] == good_model.reserved_asset_id
    assert bad_model.reserved_asset_id not in ids.values()


def test_a_not_ready_intake_commits_nothing_and_changes_nothing(rig):
    rig.new_intake()
    rig.upload(PackageSlot.DATASET, "coco", "application/zip")
    before = rig.event_types()
    from trinetra.application.errors import IntakeNotReadyError

    with pytest.raises(IntakeNotReadyError) as error:
        rig.service.commit(rig.intake)
    assert [u["slot"] for u in error.value.details["unmet"]] == ["model"]
    assert rig.event_types() == before == []
    assert rig.registry.require(rig.intake).intake_state is IntakeState.OPEN


def test_the_audit_journal_stays_intact_through_every_interruption(rig):
    upload_minimal(rig)
    service = rig.service_over(DiesBefore(rig.cases))
    with pytest.raises(OSError, match="disk went away"):
        service.commit(rig.intake)
    service.commit(rig.intake)
    _, report = rig.events()
    assert report.intact


def test_assets_admitted_before_an_interruption_have_one_record_each(rig, store):
    upload_minimal(rig)
    service = rig.service_over(DiesBefore(rig.cases))
    with pytest.raises(OSError, match="disk went away"):
        service.commit(rig.intake)
    service.commit(rig.intake)
    with store.unit_of_work() as uow:
        count = uow.connection.exec_driver_sql("SELECT COUNT(*) FROM asset_records").scalar_one()
        uow.rollback()
    assert count == 3
