"""IMP-0064 tests: an action and its audit record exist together or not at all."""

from __future__ import annotations

import io
import itertools
from datetime import UTC, datetime

import pytest

from trinetra.domain.asset_admission import AdmissionProfileError
from trinetra.domain.asset_record import ActorId
from trinetra.domain.assets import AssetKind
from trinetra.domain.audit import AuditEventType, AuditOutcome, ObjectRef, ObjectType
from trinetra.domain.digests import DigestSha256
from trinetra.errors import TrinetraError
from trinetra.infrastructure.asset_admission_service import AdmissionRequest, AssetAdmissionService
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.audit_coupling import (
    INTERNAL_ERROR,
    AuditFailureNotRecordedError,
    reason_for,
    run_audited,
)
from trinetra.infrastructure.audit_repository import AuditDraft, AuditRepository
from trinetra.infrastructure.audit_verify import verify_chain
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_store import CasStore
from trinetra.timeutil import FixedClock

ACTOR = ActorId("core")
NOTE = b"a contributor note"
NUMBERS = itertools.count(1)


def entropy(n):
    return next(NUMBERS).to_bytes(n, "big")


@pytest.fixture
def audit():
    return AuditRepository(clock=FixedClock(datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC)), entropy=entropy)


@pytest.fixture
def admission(state_root):
    cas = CasStore(CasLayout.for_state_root(state_root))
    return AssetAdmissionService(cas, AssetRepository(), entropy=entropy)


def template(**kw):
    base = {
        "event_type": AuditEventType("asset_ingested"),
        "outcome": AuditOutcome.SUCCESS,
        "created_by": ACTOR,
    }
    return AuditDraft(**(base | kw))


def request(**kw):
    base = {
        "kind": AssetKind.CONTRIBUTOR_METADATA,
        "media_type": "text/plain",
        "supplied_name": "note.txt",
        "created_by": ACTOR,
    }
    return AdmissionRequest(**(base | kw))


def ingest(admission, data=NOTE, **kw):
    def work(uow):
        admitted = admission.admit(uow, io.BytesIO(data), request(**kw))
        record = admitted.record
        subject = ObjectRef(
            object_type=ObjectType("asset_record"),
            object_id=str(record.record_id),
            record_digest=DigestSha256.parse(record.canonical_digest()),
        )
        return admitted, template(subject_refs=(subject,))

    return work


def counts(raw):
    return (
        raw.rows("SELECT count(*) FROM asset_records")[0][0],
        raw.rows("SELECT count(*) FROM audit_events")[0][0],
    )


def failure_template():
    return template(event_type=AuditEventType("asset_rejected"))


def events(raw):
    return raw.rows("SELECT journal_sequence, event_type, outcome FROM audit_events ORDER BY 1")


# ------------------------------------------------------------------------------------- success
def test_success_commits_the_work_and_its_audit_event_together(store, raw, audit, admission):
    result = run_audited(store, audit, ingest(admission), on_failure=failure_template())
    assert result.event.outcome is AuditOutcome.SUCCESS and result.event.journal_sequence == 1
    assert result.value.record.supplied_name == "note.txt"
    assert counts(raw) == (1, 1)
    subject = result.event.subject_refs[0]
    assert subject.object_id == str(result.value.record.record_id)
    with store.unit_of_work() as uow:
        assert verify_chain(uow, result.event.journal_id).intact
        uow.rollback()


def test_the_success_outcome_is_forced_whatever_the_template_says(store, audit, admission):
    odd = lambda uow: (  # noqa: E731 - tiny inline work function
        ingest(admission)(uow)[0],
        template(outcome=AuditOutcome.FAILURE, reason_code="SOME_CODE"),
    )
    result = run_audited(store, audit, odd, on_failure=None)
    assert result.event.outcome is AuditOutcome.SUCCESS and result.event.reason_code is None


# ------------------------------------------------------------------------------------- failure
def test_a_failed_action_leaves_no_work_and_a_failure_event_with_the_error_code(store, raw, audit, admission):
    bad = ingest(admission, data=b"PK\x03\x04" + b"\x00" * 30)  # zip bytes claimed as text
    with pytest.raises(AdmissionProfileError) as caught:
        run_audited(store, audit, bad, on_failure=failure_template())
    assert counts(raw) == (0, 1)
    assert events(raw) == [(1, "asset_rejected", "FAILURE")]
    reason = raw.rows("SELECT json_extract(body_json, '$.reason_code') FROM audit_events")[0][0]
    assert reason == caught.value.code == "ASSET_PROFILE_REJECTED"


def test_the_original_exception_object_is_what_the_caller_sees(store, audit):
    boom = AdmissionProfileError("original")

    def work(_uow):
        raise boom

    with pytest.raises(AdmissionProfileError) as caught:
        run_audited(store, audit, work, on_failure=failure_template())
    assert caught.value is boom


def test_a_non_trinetra_exception_is_recorded_as_an_internal_error_without_its_message(store, raw, audit):
    def work(_uow):
        raise ValueError("secret detail that must not be journalled")

    with pytest.raises(ValueError, match="secret"):
        run_audited(store, audit, work, on_failure=failure_template())
    body = raw.rows("SELECT body_json FROM audit_events")[0][0]
    assert INTERNAL_ERROR in body and "secret" not in body


def test_reason_for_uses_the_stable_code_only():
    assert reason_for(AdmissionProfileError("text")) == "ASSET_PROFILE_REJECTED"
    assert reason_for(RuntimeError("text")) == INTERNAL_ERROR == "INTERNAL_ERROR"
    assert isinstance(AdmissionProfileError("x"), TrinetraError)


def test_opting_out_of_failure_auditing_is_explicit_and_leaves_nothing(store, raw, audit):
    def work(_uow):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        run_audited(store, audit, work, on_failure=None)
    assert counts(raw) == (0, 0)


def test_a_failing_success_audit_rolls_the_work_back_and_records_the_failure(store, raw, audit, admission):
    def bad_template_work(uow):
        admitted = ingest(admission)(uow)[0]
        return admitted, template(context_ids=("has space",))  # invalid -> the audit append fails

    with pytest.raises(Exception):  # noqa: B017 - any validation error is acceptable here
        run_audited(store, audit, bad_template_work, on_failure=failure_template())
    assert counts(raw) == (0, 1)
    assert events(raw) == [(1, "asset_rejected", "FAILURE")]


def test_a_rolled_back_success_burns_no_sequence(store, raw, audit, admission):
    def work(uow):
        ingest(admission)(uow)
        raise AdmissionProfileError("late failure")

    with pytest.raises(AdmissionProfileError):
        run_audited(store, audit, work, on_failure=None)
    run_audited(store, audit, ingest(admission, data=b"second"), on_failure=None)
    assert events(raw) == [(1, "asset_ingested", "SUCCESS")]
    assert counts(raw) == (1, 1)


def test_work_that_commits_itself_is_refused_and_audited_as_a_failure(store, raw, audit, admission):
    def work(uow):
        value = ingest(admission)(uow)
        uow.commit()
        return value

    with pytest.raises(TrinetraError):
        run_audited(store, audit, work, on_failure=failure_template())
    assert [e[2] for e in events(raw)] == ["FAILURE"]


# --------------------------------------------------------------------- unaudited failure is loud
def test_a_failure_that_cannot_be_audited_is_never_swallowed(store, raw, audit):
    class Broken(AuditRepository):
        def append(self, uow, draft):
            if draft.outcome is AuditOutcome.FAILURE:
                raise RuntimeError("journal unavailable")
            return super().append(uow, draft)

    def work(_uow):
        raise AdmissionProfileError("original")

    with pytest.raises(AuditFailureNotRecordedError) as caught:
        run_audited(store, Broken(), work, on_failure=failure_template())
    assert caught.value.details["original_error"] == "AdmissionProfileError"
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert counts(raw) == (0, 0)


def test_interrupts_are_not_audited_and_leave_nothing(store, raw, audit, admission):
    def work(uow):
        ingest(admission)(uow)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_audited(store, audit, work, on_failure=failure_template())
    assert counts(raw) == (0, 0)


# ------------------------------------------------------------------------------- whole journal
def test_a_mixed_history_stays_a_valid_gapless_chain(store, raw, audit, admission):
    run_audited(store, audit, ingest(admission, data=b"one"), on_failure=failure_template())
    with pytest.raises(AdmissionProfileError):
        run_audited(
            store, audit, ingest(admission, data=b"PK\x03\x04" + b"\x00" * 30), on_failure=failure_template()
        )
    run_audited(store, audit, ingest(admission, data=b"two"), on_failure=failure_template())
    assert events(raw) == [
        (1, "asset_ingested", "SUCCESS"),
        (2, "asset_rejected", "FAILURE"),
        (3, "asset_ingested", "SUCCESS"),
    ]
    with store.unit_of_work() as uow:
        report = verify_chain(uow, audit.head(uow).journal_id)
        uow.rollback()
    assert report.intact and report.events_checked == 3
