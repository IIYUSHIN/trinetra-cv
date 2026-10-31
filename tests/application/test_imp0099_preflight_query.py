"""IMP-0099 tests: PreflightQueryService builds each view from the stored Case, never from a guess."""

from __future__ import annotations

import pytest

from intake_support import MemoryCaseStore
from projection_support import (
    CASE_ID,
    DATASET_ID,
    MODEL_ID,
    T0,
    T1,
    FixedClock,
    dataset_record,
    make_case,
    model_record,
)
from trinetra.application.errors import UnknownCaseError
from trinetra.application.preflight.catalog import BuildStageCapabilitySource
from trinetra.application.preflight.planner import PreflightPlanner
from trinetra.application.projections.preflight import build_preflight
from trinetra.application.projections.queries import PreflightQueryService
from trinetra.application.projections.shared import ProjectionInconsistentError
from trinetra.domain.capability import CapabilityName, CapabilityState
from trinetra.domain.capability_snapshot import CapabilityAssessment, CapabilityReasonCode
from trinetra.domain.case import CaseId
from trinetra.domain.references import ReferencePurpose

GT = ReferencePurpose("ground_truth")


class Assets:
    def __init__(self, *records):
        self.by_id = {r.record_id: r for r in records}
        self.reads = []

    def get(self, asset_id):
        self.reads.append(asset_id)
        return self.by_id.get(asset_id)


class Approvals:
    def __init__(self, purposes=()):
        self.purposes = frozenset(purposes)
        self.asked = []

    def approved_purposes(self, case, now):
        self.asked.append((case, now))
        return self.purposes


class Declared:
    def __init__(self, *items):
        self.items = items

    def declared(self):
        return self.items


def service(case=None, assets=None, approvals=None, source=None, now=T1):
    cases = MemoryCaseStore()
    case = case or make_case()
    cases.cases[case.case_id] = case
    assets = assets or Assets(dataset_record(), model_record())
    approvals = approvals or Approvals()
    clock = FixedClock(now)
    planner = PreflightPlanner(source or BuildStageCapabilitySource(), clock)
    return PreflightQueryService(cases, assets, approvals, planner, clock), cases, assets, approvals


def test_the_preflight_view_is_what_the_pure_builder_makes_from_the_same_facts():
    svc, *_ = service()
    case = make_case()
    snapshot = PreflightPlanner(BuildStageCapabilitySource(), FixedClock(T1)).plan(case, frozenset())
    expected = build_preflight(case, dataset_record(), model_record(), snapshot,
                               approved_purposes=frozenset(), generated_at=T1)
    assert svc.preflight(CASE_ID) == expected


def test_the_case_context_view_matches_the_one_inside_the_preflight_view():
    svc, *_ = service()
    assert svc.case_context(CASE_ID) == svc.preflight(CASE_ID).case_context


def test_the_view_is_stamped_with_the_clock_time_and_follows_the_case_revision():
    svc, cases, *_ = service(now=T0)
    assert svc.preflight(CASE_ID).meta.projection_generated_at == T0
    cases.cases[CASE_ID] = cases.cases[CASE_ID].touch(T1)
    assert svc.preflight(CASE_ID).case_context.case_state_revision.value == 2


def test_an_unknown_case_is_reported_as_unknown_for_both_views():
    svc, *_ = service()
    other = CaseId.from_entropy(lambda size: b"\x44" * size)
    for call in (svc.preflight, svc.case_context):
        with pytest.raises(UnknownCaseError) as error:
            call(other)
        assert error.value.details == {"case_id": str(other)}


def test_a_linked_asset_without_a_record_is_an_inconsistency_not_a_blank():
    svc, *_ = service(assets=Assets(model_record()))
    with pytest.raises(ProjectionInconsistentError) as error:
        svc.preflight(CASE_ID)
    assert error.value.details["role"] == "dataset" and error.value.details["asset_id"] == str(DATASET_ID)
    svc, *_ = service(assets=Assets(dataset_record()))
    with pytest.raises(ProjectionInconsistentError) as error:
        svc.case_context(CASE_ID)
    assert error.value.details["role"] == "model" and error.value.details["asset_id"] == str(MODEL_ID)


def test_approved_purposes_are_asked_for_this_case_at_the_clock_time_and_reach_the_planner():
    needs = CapabilityAssessment(
        capability=CapabilityName("label_agreement"), capability_state=CapabilityState.AVAILABLE,
        reason_code=CapabilityReasonCode("ready"), reason_text="Ready.", required_access=(),
        available_access=(), required_reference_purposes=(GT,))
    refused, cases, _, approvals = service(source=Declared(needs))
    assert refused.preflight(CASE_ID).unavailable_obligations[0].reason_code.text == (
        "required_reference_not_approved")
    assert approvals.asked == [(cases.cases[CASE_ID], T1)]
    granted, *_ = service(source=Declared(needs), approvals=Approvals([GT]))
    view = granted.preflight(CASE_ID)
    assert view.unavailable_obligations == ()
    assert [(r.purpose, r.approved) for r in view.reference_requirements] == [(GT, True)]
    assert view.capability_groups[0].capabilities[0].reference_state.value == "satisfied"


def test_the_view_reads_and_never_writes():
    svc, cases, *_ = service()
    svc.preflight(CASE_ID)
    svc.case_context(CASE_ID)
    assert cases.saves == [] and cases.created == []
