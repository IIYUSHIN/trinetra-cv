"""ReferenceAssuranceRecord, reference state events and derived standing (IMP-0089, IMP-0090).

LAW-GR-WI-021: a reference asset's identity and a Reference's authority are separate records. Uploading
a file in the reference slot creates an AssetRecord only; this record is created by a Core decision and
says which asset, for what purpose, under whose authority and for which validity window.

A reference's history is an append-only list of `ReferenceStateEvent`s: sequence 1 is ACTIVE, and at
most one terminal event (REVOKED or SUPERSEDED) may follow. `EXPIRED` is never stored: `standing` derives
it when the window has passed and no stronger terminal state applies.

PROPOSED (ERR-CL-022): after the first terminal event nothing may be appended (a second terminal event
would only contradict the first); `is_usable` additionally requires that the window has started.
"""

from __future__ import annotations

from typing import Annotated, Final, Self

from pydantic import Field, model_validator

from trinetra.domain.assets import AssetId
from trinetra.domain.base import DomainModel
from trinetra.domain.enums import WireEnum
from trinetra.domain.logical_identifier import LogicalIdentifier
from trinetra.domain.references import ReferenceEventId, ReferenceId, ReferencePurpose
from trinetra.domain.text import text_validator
from trinetra.domain.timing import UtcTimestamp
from trinetra.domain.tokens import CanonicalToken
from trinetra.errors import TrinetraError

MAX_REASON_TEXT: Final = 2048
MAX_HISTORY: Final = 2


class ReferenceInvariantError(TrinetraError):
    code = "REFERENCE_INVARIANT_VIOLATED"
    default_message = "the reference invariant was violated"


class ReferenceState(WireEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"


class ReferenceStanding(WireEnum):
    """What a reference is worth at a given moment: the stored state, or EXPIRED once derived."""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"


class ReasonCode(CanonicalToken):
    KIND = "reference_reason_code"
    __slots__ = ()


ReasonText = Annotated[str, text_validator("reason_text", 1, MAX_REASON_TEXT)]


class ReferenceAssuranceRecord(DomainModel):
    reference_id: ReferenceId
    asset_id: AssetId
    purpose: ReferencePurpose
    authority: LogicalIdentifier
    valid_from: UtcTimestamp
    valid_until: UtcTimestamp | None
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            msg = "valid_until must be after valid_from"
            raise ValueError(msg)
        return self


class ReferenceStateEvent(DomainModel):
    event_id: ReferenceEventId
    reference_id: ReferenceId
    sequence: int = Field(strict=True, ge=1, le=MAX_HISTORY)
    reference_state: ReferenceState
    occurred_at: UtcTimestamp
    superseded_by_reference_id: ReferenceId | None = None
    reason_code: ReasonCode | None = None
    reason_text: ReasonText | None = None

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if (self.sequence == 1) != (self.reference_state is ReferenceState.ACTIVE):
            msg = "sequence 1 is ACTIVE and only sequence 1 is ACTIVE"
            raise ValueError(msg)
        superseded = self.reference_state is ReferenceState.SUPERSEDED
        if superseded != (self.superseded_by_reference_id is not None):
            msg = "a successor is named exactly when the state is SUPERSEDED"
            raise ValueError(msg)
        if self.superseded_by_reference_id == self.reference_id:
            msg = "a reference cannot supersede itself"
            raise ValueError(msg)
        return self


class ReferenceHistory(DomainModel):
    """The full, validated event list of one reference."""

    reference_id: ReferenceId
    events: Annotated[tuple[ReferenceStateEvent, ...], Field(min_length=1, max_length=MAX_HISTORY)]

    @model_validator(mode="after")
    def _chain(self) -> Self:
        previous: ReferenceStateEvent | None = None
        for index, event in enumerate(self.events, start=1):
            if event.reference_id != self.reference_id or event.sequence != index:
                msg = "events must belong to the reference and run 1..n without gaps"
                raise ValueError(msg)
            if previous is not None and (
                previous.reference_state is not ReferenceState.ACTIVE
                or event.occurred_at < previous.occurred_at
            ):
                msg = "an event must follow an ACTIVE event and cannot predate it"
                raise ValueError(msg)
            previous = event
        return self

    @classmethod
    def begin(cls, event_id: ReferenceEventId, reference_id: ReferenceId, at: UtcTimestamp) -> Self:
        first = ReferenceStateEvent(event_id=event_id, reference_id=reference_id, sequence=1,
                                    reference_state=ReferenceState.ACTIVE, occurred_at=at)  # fmt: skip
        return cls(reference_id=reference_id, events=(first,))

    @property
    def recorded_state(self) -> ReferenceState:
        return self.events[-1].reference_state

    def revoke(
        self, event_id: ReferenceEventId, at: UtcTimestamp, *, reason_code: ReasonCode | None = None,
        reason_text: str | None = None,
    ) -> Self:
        return self._append(ReferenceState.REVOKED, event_id, at, None, (reason_code, reason_text))

    def supersede(
        self, event_id: ReferenceEventId, at: UtcTimestamp, successor: ReferenceId, *,
        reason_code: ReasonCode | None = None, reason_text: str | None = None,
    ) -> Self:
        return self._append(ReferenceState.SUPERSEDED, event_id, at, successor, (reason_code, reason_text))

    def _append(
        self, state: ReferenceState, event_id: ReferenceEventId, at: UtcTimestamp,
        successor: ReferenceId | None, reason: tuple[ReasonCode | None, str | None],
    ) -> Self:
        if self.recorded_state is not ReferenceState.ACTIVE:
            raise ReferenceInvariantError("a terminal reference cannot change again",
                                  details={"recorded_state": self.recorded_state.to_wire()})  # fmt: skip
        event = ReferenceStateEvent(
            event_id=event_id, reference_id=self.reference_id, sequence=len(self.events) + 1,
            reference_state=state, occurred_at=at, superseded_by_reference_id=successor,
            reason_code=reason[0], reason_text=reason[1],
        )
        return type(self)(reference_id=self.reference_id, events=(*self.events, event))

    def standing(self, record: ReferenceAssuranceRecord, now: UtcTimestamp) -> ReferenceStanding:
        if record.reference_id != self.reference_id:
            raise ReferenceInvariantError("the record belongs to another reference")
        recorded = self.recorded_state
        if recorded is not ReferenceState.ACTIVE:
            return ReferenceStanding(recorded.value)
        if record.valid_until is not None and now > record.valid_until:
            return ReferenceStanding.EXPIRED
        return ReferenceStanding.ACTIVE

    def is_usable(self, record: ReferenceAssuranceRecord, now: UtcTimestamp) -> bool:
        return self.standing(record, now) is ReferenceStanding.ACTIVE and now >= record.valid_from


__all__ = [
    "MAX_HISTORY", "MAX_REASON_TEXT", "ReasonCode", "ReasonText", "ReferenceAssuranceRecord",
    "ReferenceHistory", "ReferenceInvariantError", "ReferenceStanding", "ReferenceState",
    "ReferenceStateEvent",
]
