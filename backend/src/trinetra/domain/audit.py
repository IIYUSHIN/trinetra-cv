"""AuditEvent: the append-only record that an authoritative action happened (IMP-0060).

An audit event states WHAT Core did, to WHICH records, WHEN, and with what OUTCOME. It is operational
history, never Evidence: it carries no verdict, claim, coverage or score, and nothing here may be
cited as support for a claim. Hash chaining, persistence and verification live in later packets; this
module is the pure, immutable data model and its wire form.

Subjects are (object_type, object_id, record_digest) triples so an event pins the exact record it
spoke about; the digest means a later reader can tell whether that record is still the one audited.
Details are a short, sorted, flat list of key/value facts, bounded so an event can never become a
dumping ground for payloads or secrets.

PROPOSED fields, bounds and grammars (ERR-CL-018, not frozen): the body keys (they are bound
column-for-column by migration 0001), the 64-entry subject/context limits, the 16-entry detail limit,
flat detail values, and FAILURE <=> reason_code present.
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, Strict, field_validator, model_validator

from trinetra.domain.asset_record import ActorId
from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.domain.enums import WireEnum
from trinetra.domain.ids import OpaqueId
from trinetra.domain.text import check_text
from trinetra.domain.timing import UtcTimestamp
from trinetra.domain.tokens import CanonicalToken
from trinetra.errors import validate_error_code

SCHEMA_NAME: Final = "trinetra.audit_event"
SCHEMA_VERSION: Final = 1
MAX_SUBJECTS: Final = 64
MAX_CONTEXTS: Final = 64
MAX_DETAILS: Final = 16
MAX_REFERENCE_TEXT: Final = 128
MAX_DETAIL_TEXT: Final = 256
MAX_DETAIL_INT: Final = 2**53 - 1


class AuditEventId(OpaqueId):
    PREFIX = "audit"
    __slots__ = ()


class AuditEventType(CanonicalToken):
    """What kind of action was recorded (open vocabulary: `asset_ingested`, ...)."""

    KIND = "audit_event_type"
    __slots__ = ()


class ObjectType(CanonicalToken):
    """The kind of record an event refers to (`asset_record`, ...)."""

    KIND = "audit_object_type"
    __slots__ = ()


class JournalId(CanonicalToken):
    """Names one independent audit chain (`local_main`)."""

    KIND = "audit_journal_id"
    __slots__ = ()


class DetailKey(CanonicalToken):
    KIND = "audit_detail_key"
    __slots__ = ()


class AuditOutcome(WireEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


_REFERENCE: Final = re.compile(rf"[\x21-\x7e]{{1,{MAX_REFERENCE_TEXT}}}")


def _reference_text(value: str) -> str:
    if not _REFERENCE.fullmatch(value):
        msg = f"reference text must be 1..{MAX_REFERENCE_TEXT} visible ASCII characters"
        raise ValueError(msg)
    return value


ReferenceText = Annotated[str, Strict(), AfterValidator(_reference_text)]
ReasonCode = Annotated[str, Strict(), AfterValidator(validate_error_code)]


class ObjectRef(DomainModel):
    object_type: ObjectType
    object_id: ReferenceText
    record_digest: DigestSha256


class AuditDetail(DomainModel):
    key: DetailKey
    value: Annotated[str, Strict(), Field(max_length=MAX_DETAIL_TEXT)] | Annotated[int, Strict()]

    @field_validator("value")
    @classmethod
    def _flat_and_bounded(cls, value: str | int) -> str | int:
        if isinstance(value, bool):
            msg = "detail values are text or integers, never booleans"
            raise ValueError(msg)  # noqa: TRY004 - pydantic converts ValueError into a validation error
        if isinstance(value, int):
            if abs(value) > MAX_DETAIL_INT:
                msg = "detail integers must stay within the interoperable JSON range"
                raise ValueError(msg)
            return value
        check_text(value, name="detail", minimum=0, maximum=MAX_DETAIL_TEXT)
        return value


class AuditEvent(DomainModel):
    schema_name: Literal["trinetra.audit_event"] = SCHEMA_NAME
    schema_version: Literal[1] = SCHEMA_VERSION
    record_id: AuditEventId
    created_at: UtcTimestamp
    created_by: ActorId
    journal_id: JournalId
    journal_sequence: Annotated[int, Strict(), Field(ge=1, le=MAX_DETAIL_INT)]
    event_type: AuditEventType
    outcome: AuditOutcome
    subject_refs: Annotated[tuple[ObjectRef, ...], Field(max_length=MAX_SUBJECTS)] = ()
    context_ids: Annotated[tuple[ReferenceText, ...], Field(max_length=MAX_CONTEXTS)] = ()
    reason_code: ReasonCode | None = None
    details: Annotated[tuple[AuditDetail, ...], Field(max_length=MAX_DETAILS)] = ()

    @field_validator("subject_refs")
    @classmethod
    def _unique_subjects(cls, value: tuple[ObjectRef, ...]) -> tuple[ObjectRef, ...]:
        if len({(r.object_type, r.object_id) for r in value}) != len(value):
            msg = "an event names each subject once"
            raise ValueError(msg)
        return value

    @field_validator("context_ids")
    @classmethod
    def _unique_contexts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            msg = "context ids must be unique"
            raise ValueError(msg)
        return value

    @field_validator("details")
    @classmethod
    def _sorted_unique_details(cls, value: tuple[AuditDetail, ...]) -> tuple[AuditDetail, ...]:
        keys = [d.key.text for d in value]
        if keys != sorted(set(keys)):
            msg = "details must be sorted by key with no duplicates"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _outcome_and_reason_agree(self) -> AuditEvent:
        if self.outcome is AuditOutcome.FAILURE and self.reason_code is None:
            msg = "a FAILURE event must carry a reason_code"
            raise ValueError(msg)
        if self.outcome is AuditOutcome.SUCCESS and self.reason_code is not None:
            msg = "a SUCCESS event carries no reason_code"
            raise ValueError(msg)
        return self

    def body_json(self) -> str:
        """The canonical (RFC 8785) JSON text that is persisted and digested."""
        return self.canonical_bytes().decode("utf-8")


__all__ = [
    "AuditDetail",
    "AuditEvent",
    "AuditEventId",
    "AuditEventType",
    "AuditOutcome",
    "DetailKey",
    "JournalId",
    "ObjectRef",
    "ObjectType",
]
