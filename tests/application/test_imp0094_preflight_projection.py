"""IMP-0094 tests: PreflightProjection - the honest, evidence-bound view of what this build can do."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from projection_support import T1, FixedClock, build_stage_snapshot, dataset_record, make_case, model_record
from trinetra.application.preflight import catalog
from trinetra.application.preflight.planner import PreflightPlanner
from trinetra.application.projections.case_context import build_case_context
from trinetra.application.projections.preflight import (
    GROUP_OF,
    PreflightProjection,
    ReferenceReadiness,
    build_preflight,
)
from trinetra.application.projections.shared import ProjectionInconsistentError
from trinetra.domain.capability import CapabilityName, CapabilityState
from trinetra.domain.capability_snapshot import AccessName, CapabilityAssessment, CapabilityReasonCode
from trinetra.domain.references import ReferencePurpose

AVAILABLE = CapabilityState.AVAILABLE
UNAVAILABLE = CapabilityState.UNAVAILABLE
LIMITED = CapabilityState.LIMITED


def preflight(snapshot=None, approved=(), case=None, at=T1):
    case = case or make_case()
    return build_preflight(case, dataset_record(), model_record(), snapshot or build_stage_snapshot(case),
                           approved_purposes=approved, generated_at=at)


def item(name, *, state=AVAILABLE, code="ready", text="It is ready.", needs=(), has=(), purposes=()):
    return CapabilityAssessment(
        capability=CapabilityName(name), capability_state=state, reason_code=CapabilityReasonCode(code),
        reason_text=text, required_access=tuple(AccessName(a) for a in sorted(needs)),
        available_access=tuple(AccessName(a) for a in sorted(has)),
        required_reference_purposes=tuple(ReferencePurpose(p) for p in sorted(purposes)),
    )


class Declared:
    def __init__(self, *items):
        self.items = items

    def declared(self):
        return self.items


def planned(*items, approved=()):
    case = make_case()
    return PreflightPlanner(Declared(*items), FixedClock()).plan(case, approved)


def groups_of(projection):
    return {g.group.text: [c.capability.text for c in g.capabilities] for g in projection.capability_groups}


# ------------------------------------------------------------------ the honest build-stage answer
def test_the_build_stage_view_lists_all_eight_capabilities_in_four_groups():
    assert groups_of(preflight()) == {
        "identity": ["asset_identity"],
        "inputs": ["dataset_semantic_adapter", "model_runtime"],
        "integrity": ["data_integrity", "drift_assessment", "model_integrity", "tiir_verification"],
        "reasoning": ["assurance_reasoning"],
    }
    assert list(groups_of(preflight())) == ["identity", "inputs", "integrity", "reasoning"]


def test_only_asset_identity_is_available_and_the_rest_say_why():
    shown = {c.capability.text: c for g in preflight().capability_groups for c in g.capabilities}
    assert shown["asset_identity"].capability_state is AVAILABLE
    for name, entry in shown.items():
        if name != "asset_identity":
            assert entry.capability_state is UNAVAILABLE, name
            assert entry.reason_code.text == "not_implemented_in_current_build_stage", name


def test_seven_obligations_are_owed_one_for_each_unbuilt_capability():
    obligations = preflight().unavailable_obligations
    assert [o.capability.text for o in obligations] == [
        "assurance_reasoning", "data_integrity", "dataset_semantic_adapter", "drift_assessment",
        "model_integrity", "model_runtime", "tiir_verification",
    ]
    assert len(obligations) == 7
    assert all(o.capability_state is UNAVAILABLE for o in obligations)
    assert all(o.reason_code.text == "not_implemented_in_current_build_stage" for o in obligations)
    assert "asset_identity" not in [o.capability.text for o in obligations]


def test_planned_tests_are_empty_because_no_test_exists():
    projection = preflight()
    assert projection.planned_tests == ()
    for group in projection.capability_groups:
        for entry in group.capabilities:
            assert entry.planned_test_refs == ()
    assert preflight().to_wire()["planned_tests"] == []


def test_no_resource_or_reference_requirement_is_invented():
    projection = preflight()
    assert projection.resource_requirements == ()
    assert projection.reference_requirements == ()


def test_start_assessment_is_disabled_and_says_the_runtime_is_not_implemented():
    [action] = preflight().available_actions
    assert (action.action.text, action.allowed, action.reason_code.text) == (
        "start_assessment", False, "assessment_runtime_not_implemented")


def test_the_view_carries_the_no_conclusion_limitation():
    [limitation] = preflight().critical_limitations
    assert limitation.summary == (
        "Assessment capabilities are not yet implemented. No assurance conclusion has been produced.")
    assert limitation.decision_relevant is True


def test_the_nested_case_context_is_the_standalone_one():
    case, snapshot = make_case(), build_stage_snapshot()
    expected = build_case_context(case, dataset_record(), model_record(), snapshot, T1)
    assert preflight(snapshot, case=case).case_context == expected


def test_top_level_actions_and_limitations_match_the_context():
    projection = preflight()
    assert projection.available_actions == projection.case_context.available_actions
    assert projection.critical_limitations == projection.case_context.critical_limitations


def test_meta_carries_the_schema_version_and_time():
    assert preflight().to_wire()["meta"] == {"schema_version": "v1", "projection_generated_at": T1.to_wire()}


def test_the_view_round_trips_and_is_deterministic():
    projection = preflight()
    assert PreflightProjection.model_validate_json(projection.canonical_bytes()) == projection
    assert preflight().canonical_bytes() == projection.canonical_bytes()


def test_the_view_contains_no_score_verdict_or_readiness_claim():
    text = preflight().canonical_bytes().decode().lower()
    for word in ("score", "verdict", "ready", "pass", "trusted", "safe"):
        assert f'"{word}' not in text
    assert "readiness" not in text


# ------------------------------------------------------------------ grouping
def test_the_group_table_covers_exactly_the_catalog():
    assert set(GROUP_OF) == set(catalog.CAPABILITY_CATALOG)


def test_an_unknown_capability_is_shown_under_other_and_never_dropped():
    projection = preflight(planned(item("custom_thing"), item("asset_identity")))
    assert groups_of(projection) == {"identity": ["asset_identity"], "other": ["custom_thing"]}


def test_groups_are_in_the_fixed_order_even_when_the_snapshot_is_sorted_otherwise():
    projection = preflight(planned(item("zzz"), item("assurance_reasoning"), item("asset_identity"),
                                   item("model_runtime")))
    assert list(groups_of(projection)) == ["identity", "inputs", "reasoning", "other"]


def test_an_empty_snapshot_gives_no_groups_and_no_obligations():
    projection = preflight(planned())
    assert projection.capability_groups == ()
    assert projection.unavailable_obligations == ()


# ------------------------------------------------------------------ requirements
def test_resources_are_collected_per_access_with_who_needs_it_and_whether_it_is_there():
    snapshot = planned(
        item("a_cap", needs=["gpu"], has=[]),
        item("b_cap", state=UNAVAILABLE, code="x", needs=["gpu"], has=["gpu"]),
        item("c_cap", needs=["disk"], has=["disk"]),
        item("d_cap", needs=["disk"], has=["disk"]),
    )
    got = {r.access.text: (tuple(c.text for c in r.needed_by), r.available)
           for r in preflight(snapshot).resource_requirements}
    # a_cap lacks gpu and was lowered by the planner, but the requirement view reports what the snapshot holds
    assert got == {"disk": (("c_cap", "d_cap"), True), "gpu": (("a_cap", "b_cap"), False)}
    assert [r.access.text for r in preflight(snapshot).resource_requirements] == ["disk", "gpu"]


def test_an_access_is_available_only_if_every_needer_has_it():
    snapshot = planned(item("a_cap", state=UNAVAILABLE, code="x", needs=["gpu"], has=["gpu"]),
                       item("b_cap", state=UNAVAILABLE, code="x", needs=["gpu"]))
    [requirement] = preflight(snapshot).resource_requirements
    assert requirement.available is False


def test_references_are_collected_per_purpose_with_approval():
    snapshot = planned(item("a_cap", purposes=["masks"]), item("b_cap", purposes=["labels"]),
                       item("c_cap", purposes=["labels"]), approved=[ReferencePurpose("masks")])
    projection = preflight(snapshot, approved=[ReferencePurpose("masks")])
    got = {r.purpose.text: (tuple(c.text for c in r.needed_by), r.approved)
           for r in projection.reference_requirements}
    assert got == {"labels": (("b_cap", "c_cap"), False), "masks": (("a_cap",), True)}
    assert [r.purpose.text for r in projection.reference_requirements] == ["labels", "masks"]


def test_reference_readiness_per_capability():
    snapshot = planned(
        item("a_cap"), item("b_cap", purposes=["labels"]), item("c_cap", purposes=["labels", "masks"]),
        approved=[ReferencePurpose("labels")])
    projection = preflight(snapshot, approved=[ReferencePurpose("labels")])
    shown = {c.capability.text: c.reference_state
             for g in projection.capability_groups for c in g.capabilities}
    assert shown == {
        "a_cap": ReferenceReadiness.NOT_REQUIRED,
        "b_cap": ReferenceReadiness.SATISFIED,
        "c_cap": ReferenceReadiness.MISSING,
    }


def test_reference_readiness_wire_forms_are_lowercase():
    assert {m.name: m.value for m in ReferenceReadiness} == {
        "NOT_REQUIRED": "not_required", "SATISFIED": "satisfied", "MISSING": "missing"}


def test_an_approved_purpose_nobody_needs_is_not_listed():
    projection = preflight(approved=[ReferencePurpose("unused")])
    assert projection.reference_requirements == ()


def test_access_lists_are_carried_into_the_capability_entries():
    snapshot = planned(item("a_cap", needs=["gpu"], has=["gpu", "tpu"]))
    [entry] = preflight(snapshot).capability_groups[0].capabilities
    assert [a.text for a in entry.required_access] == ["gpu"]
    assert [a.text for a in entry.available_access] == ["gpu", "tpu"]


# ------------------------------------------------------------------ obligations cannot hide anything
def test_a_limited_capability_is_an_obligation_with_its_own_state():
    snapshot = planned(item("a_cap", state=LIMITED, code="partial", text="Partly there."))
    [obligation] = preflight(snapshot).unavailable_obligations
    assert (obligation.capability.text, obligation.capability_state, obligation.reason_code.text,
            obligation.reason_text) == ("a_cap", LIMITED, "partial", "Partly there.")


def test_an_all_available_snapshot_owes_nothing_and_allows_start():
    snapshot = planned(*[item(c.text) for c in catalog.CAPABILITY_CATALOG])
    projection = preflight(snapshot)
    assert projection.unavailable_obligations == ()
    assert projection.critical_limitations == ()
    assert projection.available_actions[0].allowed is True


def _doctored(projection, mutate):
    data = json.loads(projection.canonical_bytes())
    mutate(data)
    return json.dumps(data)


def test_dropping_an_obligation_is_rejected():
    projection = preflight()
    with pytest.raises(ValidationError):
        PreflightProjection.model_validate_json(
            _doctored(projection, lambda d: d["unavailable_obligations"].pop()))


def test_listing_an_available_capability_as_an_obligation_is_rejected():
    def add(d):
        d["unavailable_obligations"].append(
            {"capability": "asset_identity", "capability_state": "UNAVAILABLE",
             "reason_code": "available_in_current_build_stage", "reason_text": "x"})

    with pytest.raises(ValidationError):
        PreflightProjection.model_validate_json(_doctored(preflight(), add))


def test_an_obligation_that_changes_the_reason_is_rejected():
    def change(d):
        d["unavailable_obligations"][0]["reason_code"] = "something_softer"

    with pytest.raises(ValidationError):
        PreflightProjection.model_validate_json(_doctored(preflight(), change))


def test_an_obligation_that_changes_the_state_is_rejected():
    snapshot = planned(item("a_cap", state=LIMITED, code="partial"))

    def change(d):
        d["unavailable_obligations"][0]["capability_state"] = "UNAVAILABLE"

    with pytest.raises(ValidationError):
        PreflightProjection.model_validate_json(_doctored(preflight(snapshot), change))


def test_a_duplicate_obligation_is_rejected():
    def dup(d):
        d["unavailable_obligations"].append(d["unavailable_obligations"][0])

    with pytest.raises(ValidationError):
        PreflightProjection.model_validate_json(_doctored(preflight(), dup))


def test_a_capability_listed_twice_is_rejected():
    def dup(d):
        d["capability_groups"][0]["capabilities"].append(d["capability_groups"][0]["capabilities"][0])

    with pytest.raises(ValidationError):
        PreflightProjection.model_validate_json(_doctored(preflight(), dup))


def test_a_group_must_not_be_empty():
    def empty(d):
        d["capability_groups"][0]["capabilities"] = []

    with pytest.raises(ValidationError):
        PreflightProjection.model_validate_json(_doctored(preflight(), empty))


# ------------------------------------------------------------------ consistency with the case
def test_a_snapshot_for_another_revision_is_refused():
    stale = build_stage_snapshot()
    with pytest.raises(ProjectionInconsistentError):
        preflight(stale, case=make_case().touch(T1))


def test_swapped_asset_records_are_refused():
    with pytest.raises(ProjectionInconsistentError):
        build_preflight(make_case(), model_record(), dataset_record(), build_stage_snapshot(),
                        approved_purposes=(), generated_at=T1)
