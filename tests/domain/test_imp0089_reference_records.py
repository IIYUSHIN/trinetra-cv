"""IMP-0089/0090 tests: ReferenceAssuranceRecord, state events, history rules and derived standing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.assets import AssetId
from trinetra.domain.reference_records import (
    ReasonCode,
    ReferenceAssuranceRecord,
    ReferenceHistory,
    ReferenceInvariantError,
    ReferenceStanding,
    ReferenceState,
    ReferenceStateEvent,
)
from trinetra.domain.references import ReferenceEventId, ReferenceId, ReferencePurpose
from trinetra.domain.timing import UtcTimestamp

T0 = UtcTimestamp.parse("2026-10-31T10:00:00Z")
T1 = UtcTimestamp.parse("2026-10-31T10:00:05Z")
T2 = UtcTimestamp.parse("2026-10-31T10:01:00Z")
T3 = UtcTimestamp.parse("2026-10-31T11:00:00Z")


def rid(n=1):
    return ReferenceId.from_entropy(lambda s: bytes([n]) * s)


def eid(n=1):
    return ReferenceEventId.from_entropy(lambda s: bytes([n]) * s)


def record(n=1, *, start=T0, until=None, **over):
    values = {"reference_id": rid(n), "asset_id": AssetId.from_entropy(lambda s: b"\x09" * s),
              "purpose": ReferencePurpose("ground_truth"), "authority": "Harbour QA board",
              "valid_from": start, "valid_until": until, "created_at": T0, **over}  # fmt: skip
    return ReferenceAssuranceRecord(**values)


def active(n=1, at=T0):
    return ReferenceHistory.begin(eid(1), rid(n), at)


# ------------------------------------------------------------------------------ the record (0089)
def test_a_record_names_asset_purpose_authority_and_window():
    r = record(until=T3)
    assert set(r.to_wire()) == {"reference_id", "asset_id", "purpose", "authority", "valid_from",
                                "valid_until", "created_at"}  # fmt: skip
    assert r.valid_until == T3


def test_the_window_must_have_positive_length():
    assert record(until=None).valid_until is None
    assert record(start=T0, until=T1).valid_until == T1
    for until in (T0, UtcTimestamp.parse("2026-10-30T10:00:00Z")):
        with pytest.raises(ValidationError, match="valid_until"):
            record(start=T0, until=until)


@pytest.mark.parametrize("over", [{"authority": ""}, {"authority": " x"}, {"authority": "x" * 129},
                                  {"purpose": "Ground Truth"}, {"purpose": ""},
                                  {"reference_id": "ref_x"}, {"asset_id": "asset_x"}])
def test_bad_fields_are_refused(over):
    with pytest.raises(ValidationError):
        record(**over)


def test_a_record_has_no_approval_or_state_field_so_upload_cannot_approve():
    fields = set(ReferenceAssuranceRecord.model_fields)
    assert not fields & {"status", "state", "approved", "approved_at", "approved_by"}


def test_the_record_round_trips_and_is_immutable():
    r = record(until=T3)
    assert ReferenceAssuranceRecord.model_validate_json(r.canonical_bytes()) == r
    with pytest.raises(ValidationError):
        r.purpose = ReferencePurpose("other")


# ------------------------------------------------------------------------------ events (0090)
def test_the_first_event_is_active_at_sequence_one():
    h = active()
    assert [(e.sequence, e.reference_state) for e in h.events] == [(1, ReferenceState.ACTIVE)]
    assert h.recorded_state is ReferenceState.ACTIVE


def event(**over):
    base = {"event_id": eid(), "reference_id": rid(), "sequence": 1, "reference_state": ReferenceState.ACTIVE,
            "occurred_at": T0}  # fmt: skip
    return ReferenceStateEvent(**{**base, **over})


@pytest.mark.parametrize("over", [
    {"sequence": 2},  # ACTIVE only at 1
    {"reference_state": ReferenceState.REVOKED},  # sequence 1 must be ACTIVE
    {"sequence": 2, "reference_state": ReferenceState.SUPERSEDED},  # no successor
    {"sequence": 2, "reference_state": ReferenceState.REVOKED, "superseded_by_reference_id": rid(2)},
    {"sequence": 2, "reference_state": ReferenceState.SUPERSEDED, "superseded_by_reference_id": rid(1)},
    {"reason_text": ""}, {"reason_text": "x" * 2049}, {"reason_text": " x"}, {"reason_text": "a\x00b"},
    {"sequence": 0}, {"sequence": 3}, {"sequence": True},
])
def test_incoherent_events_cannot_be_built(over):
    with pytest.raises(ValidationError):
        event(**over)


def test_reason_text_bounds_are_exact():
    assert event(reason_text="x" * 2048).reason_text == "x" * 2048
    assert event(reason_text="x").reason_text == "x"


def test_revoking_appends_sequence_two():
    h = active().revoke(eid(2), T1, reason_code=ReasonCode("withdrawn"), reason_text="board decision")
    assert [e.sequence for e in h.events] == [1, 2]
    last = h.events[-1]
    assert last.reference_state is ReferenceState.REVOKED and last.reason_code == ReasonCode("withdrawn")
    assert last.reason_text == "board decision" and last.occurred_at == T1
    assert h.recorded_state is ReferenceState.REVOKED


def test_superseding_names_the_successor():
    h = active().supersede(eid(2), T1, rid(2))
    assert h.events[-1].superseded_by_reference_id == rid(2)
    assert h.recorded_state is ReferenceState.SUPERSEDED


def test_a_reference_cannot_supersede_itself():
    with pytest.raises(ValidationError):
        active().supersede(eid(2), T1, rid(1))


def test_nothing_follows_a_terminal_state():
    revoked = active().revoke(eid(2), T1)
    for attempt in (lambda: revoked.revoke(eid(3), T2), lambda: revoked.supersede(eid(3), T2, rid(2))):
        with pytest.raises(ReferenceInvariantError) as exc:
            attempt()
        assert exc.value.details["recorded_state"] == "REVOKED"


def test_an_event_cannot_predate_the_one_before_it():
    with pytest.raises(ValidationError, match="predate"):
        active(at=T2).revoke(eid(2), T1)
    assert active(at=T1).revoke(eid(2), T1).recorded_state is ReferenceState.REVOKED  # equal is fine


def test_a_history_validates_what_it_is_given():
    first = active().events[0]
    second = active().revoke(eid(2), T1).events[1]
    assert ReferenceHistory(reference_id=rid(), events=(first, second)).events == (first, second)
    for bad in ((second,), (first, first), (second, first)):
        with pytest.raises(ValidationError):
            ReferenceHistory(reference_id=rid(), events=bad)
    with pytest.raises(ValidationError):
        ReferenceHistory(reference_id=rid(2), events=(first,))  # another reference's event
    with pytest.raises(ValidationError):
        ReferenceHistory(reference_id=rid(), events=())
    with pytest.raises(ValidationError):
        ReferenceHistory(reference_id=rid(), events=[first])


def test_a_history_longer_than_active_plus_one_terminal_is_impossible():
    first = active().events[0]
    second = active().revoke(eid(2), T1).events[1]
    with pytest.raises(ValidationError):  # even building a third event is refused
        event(event_id=eid(3), sequence=3, reference_state=ReferenceState.REVOKED, occurred_at=T2)
    assert second.sequence == 2 and first.sequence == 1


# ------------------------------------------------------------------------------ standing
def test_an_active_reference_without_end_date_stays_active():
    r, h = record(), active()
    assert h.standing(r, T3) is ReferenceStanding.ACTIVE


def test_expiry_is_derived_after_the_window_ends_and_never_stored():
    r, h = record(until=T2), active()
    assert h.standing(r, T2) is ReferenceStanding.ACTIVE  # inclusive at the end
    assert h.standing(r, T3) is ReferenceStanding.EXPIRED
    assert h.recorded_state is ReferenceState.ACTIVE
    assert "EXPIRED" not in {s.value for s in ReferenceState}


def test_a_stronger_terminal_state_beats_expiry():
    r = record(until=T1)
    assert active().revoke(eid(2), T1).standing(r, T3) is ReferenceStanding.REVOKED
    assert active().supersede(eid(2), T1, rid(2)).standing(r, T3) is ReferenceStanding.SUPERSEDED


def test_standing_needs_the_matching_record():
    with pytest.raises(ReferenceInvariantError):
        active(1).standing(record(2), T1)


def test_usability_needs_an_active_standing_inside_the_window():
    r = record(start=T1, until=T3)
    h = active()
    assert not h.is_usable(r, T0)  # not started yet
    assert h.is_usable(r, T1)  # starts inclusive
    assert h.is_usable(r, T3)
    assert not h.is_usable(r, UtcTimestamp.parse("2026-10-31T11:00:01Z"))  # expired
    assert not active().revoke(eid(2), T1).is_usable(r, T2)


def test_standing_and_state_are_separate_dimensions():
    assert ReferenceStanding.ACTIVE != ReferenceState.ACTIVE
    assert ReferenceStanding("REVOKED").value == ReferenceState.REVOKED.value
    assert [s.value for s in ReferenceState] == ["ACTIVE", "REVOKED", "SUPERSEDED"]
    assert [s.value for s in ReferenceStanding] == ["ACTIVE", "REVOKED", "SUPERSEDED", "EXPIRED"]
