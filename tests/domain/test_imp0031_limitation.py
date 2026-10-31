"""IMP-0031 tests: Limitation value object."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.entity import EntityRef, EntityType
from trinetra.domain.limitations import MAX_SCOPE_REFS, Limitation, LimitationCode, LimitationId


def ref(n=1):
    return EntityRef(entity_type=EntityType("dataset"), entity_id=f"asset_{n}", display_label=f"Dataset {n}")


def make(**over):
    base = {"limitation_id": LimitationId.generate(), "code": LimitationCode("reference_missing"),
            "summary": "No approved clean reference was available.", "scope_refs": (ref(),),
            "decision_relevant": True, "detail_available": False}
    return Limitation(**{**base, **over})


def test_wire_form_has_exactly_the_contract_fields_and_roundtrips():
    lim = make()
    wire = lim.to_wire()
    assert set(wire) == {
        "limitation_id", "code", "summary", "scope_refs", "decision_relevant", "detail_available",
    }
    assert wire["code"] == "reference_missing" and wire["decision_relevant"] is True
    assert wire["scope_refs"][0]["entity_id"] == "asset_1"
    assert Limitation(**{**wire, "scope_refs": tuple(wire["scope_refs"])}) == lim


def test_hashable_immutable_and_lists_are_rejected_as_scopes():
    lim = make(scope_refs=(ref(1), ref(2)))
    assert isinstance(lim.scope_refs, tuple) and hash(lim) == hash(lim) and len({lim, lim}) == 1
    with pytest.raises(ValidationError):
        lim.decision_relevant = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        make(scope_refs=[ref(1)])


def test_at_least_one_and_at_most_max_unique_scope_refs():
    with pytest.raises(ValidationError):
        make(scope_refs=())
    assert len(make(scope_refs=tuple(ref(i) for i in range(MAX_SCOPE_REFS))).scope_refs) == MAX_SCOPE_REFS
    with pytest.raises(ValidationError):
        make(scope_refs=tuple(ref(i) for i in range(MAX_SCOPE_REFS + 1)))
    with pytest.raises(ValidationError, match="duplicates"):
        make(scope_refs=(ref(1), ref(1)))


@pytest.mark.parametrize("flag", ["decision_relevant", "detail_available"])
@pytest.mark.parametrize("bad", [1, 0, "true", None, "yes"])
def test_flags_are_strict_booleans(flag, bad):
    with pytest.raises(ValidationError):
        make(**{flag: bad})


@pytest.mark.parametrize("summary", ["", " leading", "trailing ", "tab\tinside", "x" * 1025, None, 3])
def test_summary_rules(summary):
    with pytest.raises(ValidationError):
        make(summary=summary)


def test_no_severity_score_verdict_or_status_can_ride_along():
    for extra in ({"severity": "high"}, {"score": 1}, {"verdict": "PASS"}, {"status": "open"}):
        with pytest.raises(ValidationError):
            make(**extra)


def test_code_is_a_token_and_id_is_a_limitation_id():
    with pytest.raises(ValidationError):
        make(code="Reference Missing")
    other = make().limitation_id
    assert str(other).startswith("lim_")
    with pytest.raises(ValidationError):
        make(limitation_id=str(other).replace("lim_", "job_"))


def test_scope_ref_bound_is_the_documented_literal():
    assert MAX_SCOPE_REFS == 64
