"""IMP-0060 tests: an AuditEvent is an immutable, bounded statement of fact -- and never Evidence."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trinetra.domain import audit as audit_module
from trinetra.domain.asset_record import ActorId
from trinetra.domain.audit import (
    MAX_CONTEXTS,
    MAX_DETAILS,
    MAX_SUBJECTS,
    AuditDetail,
    AuditEvent,
    AuditEventId,
    AuditEventType,
    AuditOutcome,
    DetailKey,
    JournalId,
    ObjectRef,
    ObjectType,
)
from trinetra.domain.base import DomainConventionError
from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp


def fixed(byte):
    return lambda n: bytes([byte]) * n


def subject(n=1, kind="asset_record"):
    return ObjectRef(
        object_type=ObjectType(kind), object_id=f"asset_{n:032x}", record_digest=DigestSha256.of(bytes([n]))
    )


def make(**overrides):
    base = {
        "record_id": AuditEventId.from_entropy(fixed(1)),
        "created_at": UtcTimestamp.parse("2026-10-31T08:00:00Z"),
        "created_by": ActorId("core"),
        "journal_id": JournalId("local_main"),
        "journal_sequence": 1,
        "event_type": AuditEventType("asset_ingested"),
        "outcome": AuditOutcome.SUCCESS,
    }
    return AuditEvent(**(base | overrides))


def detail(key, value):
    return AuditDetail(key=DetailKey(key), value=value)


# ---------------------------------------------------------------------------------- shape
def test_a_minimal_event_round_trips_through_its_canonical_json():
    event = make()
    assert AuditEvent.model_validate_json(event.canonical_bytes()) == event
    assert json.loads(event.body_json()) == event.to_wire()


def test_the_wire_body_has_exactly_the_keys_the_database_binds():
    wire = make().to_wire()
    assert set(wire) == {
        "schema_name", "schema_version", "record_id", "created_at", "created_by", "journal_id",
        "journal_sequence", "event_type", "outcome", "subject_refs", "context_ids", "reason_code", "details",
    }  # fmt: skip
    assert wire["schema_name"] == "trinetra.audit_event" and wire["schema_version"] == 1
    assert wire["outcome"] == "SUCCESS" and wire["reason_code"] is None
    assert wire["record_id"].startswith("audit_")


def test_a_full_event_keeps_subjects_contexts_and_details_in_order():
    event = make(
        subject_refs=(subject(2), subject(1)),
        context_ids=("case_b", "case_a"),
        details=(detail("bytes_stored", 12), detail("media_type", "text/plain")),
    )
    again = AuditEvent.model_validate_json(event.canonical_bytes())
    assert again == event
    assert [s.object_id for s in again.subject_refs] == [subject(2).object_id, subject(1).object_id]
    assert again.context_ids == ("case_b", "case_a")


def test_events_are_frozen_and_closed():
    event = make()
    with pytest.raises(ValidationError):
        event.journal_sequence = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        AuditEvent.model_validate({**event.to_wire(), "verdict": "PASS"})


def test_the_wrong_schema_marker_is_refused():
    with pytest.raises(ValidationError):
        AuditEvent.model_validate({**make().to_wire(), "schema_version": 2})
    with pytest.raises(ValidationError):
        AuditEvent.model_validate({**make().to_wire(), "schema_name": "trinetra.asset_record"})


# ----------------------------------------------------------------------- outcome and reason
def test_failure_requires_a_reason_and_success_forbids_one():
    failed = make(outcome=AuditOutcome.FAILURE, reason_code="CAS_COMMIT_FAILED")
    assert failed.reason_code == "CAS_COMMIT_FAILED"
    with pytest.raises(ValidationError, match="reason_code"):
        make(outcome=AuditOutcome.FAILURE)
    with pytest.raises(ValidationError, match="reason_code"):
        make(reason_code="CAS_COMMIT_FAILED")


@pytest.mark.parametrize("bad", ["cas_commit", "Cas_Commit", "", "A" * 65, "BAD-CODE", " X"])
def test_a_malformed_reason_code_is_refused(bad):
    with pytest.raises(ValidationError):
        make(outcome=AuditOutcome.FAILURE, reason_code=bad)


def test_outcomes_are_a_closed_exact_wire_enum():
    assert [m.value for m in AuditOutcome] == ["SUCCESS", "FAILURE"]
    with pytest.raises(ValidationError):
        AuditEvent.model_validate({**make().to_wire(), "outcome": "success"})
    with pytest.raises(ValidationError):
        AuditEvent.model_validate({**make().to_wire(), "outcome": "PARTIAL"})


# ------------------------------------------------------------------------- sequence boundaries
@pytest.mark.parametrize("bad", [0, -1, 2**53, True, 1.0, "1"])
def test_the_sequence_must_be_a_positive_interoperable_integer(bad):
    with pytest.raises(ValidationError):
        make(journal_sequence=bad)


def test_the_sequence_boundaries_are_inclusive():
    assert make(journal_sequence=1).journal_sequence == 1
    assert make(journal_sequence=2**53 - 1).journal_sequence == 2**53 - 1


# ------------------------------------------------------------------------------- subjects
BAD_REFERENCES = ("", " x", "a b", "tab\tid", "x" * 129, "\u00e9\u0301", "\u00e9", "a\u200bb", "a\x00b")


@pytest.mark.parametrize("bad", BAD_REFERENCES)
def test_subject_and_context_ids_must_be_visible_ascii(bad):
    digest = DigestSha256.of(b"x")
    with pytest.raises(ValidationError):
        ObjectRef(object_type=ObjectType("asset_record"), object_id=bad, record_digest=digest)
    with pytest.raises(ValidationError):
        make(context_ids=(bad,))


def test_subject_digests_pin_the_record():
    assert subject().record_digest == DigestSha256.of(b"\x01")
    assert subject(128).object_id == "asset_" + f"{128:032x}"


def test_an_event_names_each_subject_once():
    with pytest.raises(ValidationError, match="once"):
        make(subject_refs=(subject(1), subject(1)))
    assert len(make(subject_refs=(subject(1), subject(1, kind="other_record"))).subject_refs) == 2


def test_subject_and_context_counts_are_bounded_inclusively():
    many = tuple(subject(n) for n in range(1, MAX_SUBJECTS + 1))
    assert len(make(subject_refs=many).subject_refs) == MAX_SUBJECTS
    with pytest.raises(ValidationError):
        make(subject_refs=(*many, subject(MAX_SUBJECTS + 1)))
    contexts = tuple(f"ctx{n}" for n in range(MAX_CONTEXTS))
    assert len(make(context_ids=contexts).context_ids) == MAX_CONTEXTS
    with pytest.raises(ValidationError):
        make(context_ids=(*contexts, "one_more"))


def test_contexts_are_unique_and_clean():
    with pytest.raises(ValidationError, match="unique"):
        make(context_ids=("a", "a"))
    with pytest.raises(ValidationError):
        make(context_ids=("has space",))


# --------------------------------------------------------------------------------- details
def test_details_must_be_sorted_unique_and_bounded():
    ok = (detail("alpha", 1), detail("beta", "two"))
    assert make(details=ok).details == ok
    with pytest.raises(ValidationError, match="sorted"):
        make(details=(detail("beta", 1), detail("alpha", 2)))
    with pytest.raises(ValidationError, match="sorted"):
        make(details=(detail("alpha", 1), detail("alpha", 2)))
    full = tuple(detail(f"k{n:02d}", n) for n in range(MAX_DETAILS))
    assert len(make(details=full).details) == MAX_DETAILS
    with pytest.raises(ValidationError):
        make(details=(*full, detail("zz", 1)))


@pytest.mark.parametrize("bad", [True, False, 1.5, None, b"x", ["a"], 2**53, -(2**53), "x" * 257])
def test_detail_values_are_flat_text_or_bounded_integers(bad):
    with pytest.raises(ValidationError):
        detail("alpha", bad)


def test_detail_value_boundaries_are_inclusive():
    assert detail("alpha", 2**53 - 1).value == 2**53 - 1
    assert detail("alpha", -(2**53 - 1)).value == -(2**53 - 1)
    assert detail("alpha", "x" * 256).value == "x" * 256
    assert detail("alpha", "").value == ""


def test_detail_keys_are_canonical_tokens():
    with pytest.raises(Exception, match="audit_detail_key"):
        DetailKey("Not-A-Token")


# ------------------------------------------------------------------------ type distinctness
def test_audit_tokens_of_different_meaning_never_compare_equal():
    assert AuditEventType("asset_record") != ObjectType("asset_record")
    assert JournalId("local_main") != AuditEventType("local_main")


def test_audit_ids_are_prefixed_and_not_interchangeable():
    ident = AuditEventId.from_entropy(fixed(9))
    assert str(ident).startswith("audit_") and AuditEventId.parse(str(ident)) == ident
    with pytest.raises(ValidationError):
        make(record_id="asset_" + str(ident).split("_", 1)[1])


# ------------------------------------------------------------------------ never Evidence
def test_the_audit_model_has_no_verdict_score_or_state_vocabulary():
    names = set(AuditEvent.model_fields) | set(AuditDetail.model_fields) | set(ObjectRef.model_fields)
    assert not names & {"status", "state", "verdict", "claim", "coverage", "score", "confidence", "evidence"}
    with pytest.raises(DomainConventionError):
        type("Bad", (AuditEvent,), {"__annotations__": {"status": str}})


def test_the_audit_module_does_not_import_evidence_or_reasoning_modules():
    tree = ast.parse(Path(audit_module.__file__).read_text("utf-8"))
    imported = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    banned = ("evidence", "claims", "claim_state", "coverage", "verdicts", "policy", "decisions")
    assert not [m for m in imported if any(m.endswith(f".{b}") for b in banned)]


def test_the_proposed_bounds_are_pinned():
    assert (MAX_SUBJECTS, MAX_CONTEXTS, MAX_DETAILS) == (64, 64, 16)
    assert make(context_ids=("x" * 128,)).context_ids == ("x" * 128,)
