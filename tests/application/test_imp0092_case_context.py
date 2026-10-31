"""IMP-0092 tests: CaseContextProjection, start-assessment availability and standing limitations."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from projection_support import (
    CASE_ID,
    DATASET_ID,
    MODEL_ID,
    OTHER_ID,
    T0,
    T1,
    FixedClock,
    build_stage_snapshot,
    dataset_record,
    make_case,
    model_record,
    record,
)
from trinetra.application.preflight import catalog
from trinetra.application.preflight.catalog import BuildStageCapabilitySource
from trinetra.application.preflight.planner import PreflightPlanner
from trinetra.application.projections.actions import available_actions, start_assessment_availability
from trinetra.application.projections.case_context import CaseContextProjection, build_case_context
from trinetra.application.projections.limitations import critical_limitations
from trinetra.application.projections.shared import ProjectionInconsistentError
from trinetra.domain.assets import AssetKind
from trinetra.domain.capability import CapabilityState
from trinetra.domain.capability_snapshot import CapabilitySnapshot
from trinetra.domain.case_lifecycle import CaseLifecycleState

RESERVED = ["principal_claim", "assurance_sufficiency", "policy_recommendation", "analyst_disposition",
            "freshness", "attention_category", "attention_reason", "coverage_summary"]


def context(case=None, snapshot=None, dataset=None, model=None, at=T1):
    case = case or make_case()
    return build_case_context(case, dataset or dataset_record(), model or model_record(),
                              snapshot or build_stage_snapshot(case), at)


class Declared:
    def __init__(self, items):
        self.items = items

    def declared(self):
        return self.items


def unbuilt(entry):
    return entry.evolve(capability_state=CapabilityState.UNAVAILABLE, reason_code=catalog.NOT_IMPLEMENTED)


def all_available_snapshot(case=None, override=None):
    """A snapshot in which every catalog capability is AVAILABLE (a future build), optionally altered."""
    case = case or make_case()
    items = [d.evolve(capability_state=CapabilityState.AVAILABLE, reason_code=catalog.AVAILABLE_IN_BUILD)
             for d in catalog.build_stage_declarations()]
    if override:
        items = [override(i) for i in items]
    return PreflightPlanner(Declared(items), FixedClock()).plan(case, ())


# ------------------------------------------------------------------ the projection
def test_the_context_names_the_case_its_label_state_and_revision():
    wire = context(make_case(label="Night patrol v3")).to_wire()
    assert wire["case_id"] == str(CASE_ID)
    assert wire["case_label"] == "Night patrol v3"
    assert wire["case_lifecycle_state"] == "preflight"
    assert wire["case_state_revision"] == 1
    assert wire["meta"] == {"schema_version": "v1", "projection_generated_at": T1.to_wire()}


def test_an_unlabelled_case_has_a_null_label():
    assert context().to_wire()["case_label"] is None


def test_the_revision_follows_the_case():
    case = make_case().touch(T1)
    assert context(case).to_wire()["case_state_revision"] == 2


def test_primary_assets_are_the_dataset_then_the_model_with_digest_and_label():
    dataset, model = context().primary_assets
    assert (dataset.entity_type.text, dataset.entity_id, dataset.display_label) == (
        "dataset", str(DATASET_ID), "coco.zip")
    assert (model.entity_type.text, model.entity_id, model.display_label) == (
        "model", str(MODEL_ID), "yolo.onnx")
    assert dataset.digest == dataset_record().blob_digest
    assert model.digest == model_record().blob_digest
    assert dataset.digest != model.digest


def test_an_unnamed_asset_is_labelled_by_its_kind():
    ctx = context(dataset=dataset_record(name=""), model=model_record(name=""))
    assert [a.display_label for a in ctx.primary_assets] == ["dataset", "model"]


def test_every_reserved_field_is_present_and_explicitly_null():
    wire = context().to_wire()
    for name in RESERVED:
        assert name in wire, name
        assert wire[name] is None, name


def test_a_reserved_field_cannot_be_filled():
    data = json.loads(context().canonical_bytes())
    for name in RESERVED:
        with pytest.raises(ValidationError):
            CaseContextProjection.model_validate_json(json.dumps({**data, name: "something"}))


def test_exactly_two_primary_assets_are_required():
    data = json.loads(context().canonical_bytes())
    for assets in (data["primary_assets"][:1], [*data["primary_assets"], data["primary_assets"][0]], []):
        with pytest.raises(ValidationError):
            CaseContextProjection.model_validate_json(json.dumps({**data, "primary_assets": assets}))


def test_the_context_round_trips_through_canonical_json():
    ctx = context()
    assert CaseContextProjection.model_validate_json(ctx.canonical_bytes()) == ctx


def test_the_same_facts_give_byte_identical_output():
    assert context().canonical_bytes() == context().canonical_bytes()


def test_the_wire_carries_no_score_or_verdict_field():
    text = context().canonical_bytes().decode()
    for word in ("score", "verdict", "confidence", "pass", "fail"):
        assert f'"{word}' not in text


# ------------------------------------------------------------------ consistency
def test_a_dataset_record_that_is_not_the_linked_one_is_refused():
    with pytest.raises(ProjectionInconsistentError):
        context(dataset=record(OTHER_ID, AssetKind.DATASET, "other.zip"))


def test_a_model_record_that_is_not_the_linked_one_is_refused():
    with pytest.raises(ProjectionInconsistentError):
        context(model=record(OTHER_ID, AssetKind.MODEL, "other.onnx"))


def test_a_record_of_the_wrong_kind_under_the_right_id_is_refused():
    with pytest.raises(ProjectionInconsistentError):
        context(dataset=record(DATASET_ID, AssetKind.MODEL, "oops.onnx"))
    with pytest.raises(ProjectionInconsistentError):
        context(model=record(MODEL_ID, AssetKind.DATASET, "oops.zip"))


def test_swapped_records_are_refused():
    with pytest.raises(ProjectionInconsistentError):
        build_case_context(make_case(), model_record(), dataset_record(), build_stage_snapshot(), T1)


def test_a_snapshot_for_another_case_is_refused():
    other = make_case().evolve(case_id=type(CASE_ID).from_entropy(lambda n: b"\x09" * n))
    with pytest.raises(ProjectionInconsistentError):
        context(snapshot=build_stage_snapshot(other))


def test_a_snapshot_from_another_revision_is_refused():
    stale = build_stage_snapshot(make_case())
    with pytest.raises(ProjectionInconsistentError):
        context(case=make_case().touch(T1), snapshot=stale)


def test_a_case_context_does_not_alter_its_inputs():
    case, snapshot = make_case(), build_stage_snapshot()
    before = (case.canonical_bytes(), snapshot.canonical_bytes())
    context(case, snapshot)
    assert (case.canonical_bytes(), snapshot.canonical_bytes()) == before


# ------------------------------------------------------------------ start-assessment availability
def test_in_this_build_stage_start_assessment_is_disabled_with_the_runtime_reason():
    [action] = context().available_actions
    assert action.action.text == "start_assessment"
    assert action.allowed is False
    assert action.reason_code.text == "assessment_runtime_not_implemented"
    assert "not implemented" in action.reason_text
    assert action.requires_fresh_revision is True
    assert (action.requires_confirmation, action.requires_reason) == (False, False)


def test_start_assessment_is_allowed_only_when_every_catalog_capability_is_available():
    case = make_case()
    action = start_assessment_availability(case, all_available_snapshot(case))
    assert action.allowed is True
    assert (action.reason_code, action.reason_text) == (None, None)
    assert action.requires_fresh_revision is True


def test_one_unavailable_capability_blocks_it_and_names_the_capability():
    case = make_case()
    snapshot = all_available_snapshot(
        case, lambda i: unbuilt(i) if i.capability == catalog.MODEL_RUNTIME else i)
    action = start_assessment_availability(case, snapshot)
    assert action.allowed is False
    assert action.reason_code.text == "required_capability_unavailable"
    assert action.reason_text == "Assessment cannot start: not available: model_runtime."


def test_a_limited_capability_blocks_it():
    case = make_case()
    snapshot = all_available_snapshot(
        case, lambda i: i.evolve(capability_state=CapabilityState.LIMITED)
        if i.capability == catalog.DRIFT_ASSESSMENT else i)
    action = start_assessment_availability(case, snapshot)
    assert action.allowed is False
    assert "drift_assessment" in action.reason_text


def test_a_capability_missing_from_the_snapshot_blocks_it():
    case = make_case()
    full = all_available_snapshot(case)
    partial = CapabilitySnapshot.of(
        captured_at=full.captured_at, case_id=full.case_id, case_state_revision=full.case_state_revision,
        items=[i for i in full.items if i.capability != catalog.TIIR_VERIFICATION])
    action = start_assessment_availability(case, partial)
    assert action.allowed is False
    assert "tiir_verification" in action.reason_text


def test_an_empty_snapshot_blocks_it():
    case = make_case()
    empty = PreflightPlanner(Declared([]), FixedClock()).plan(case, ())
    assert start_assessment_availability(case, empty).allowed is False


def test_the_runtime_reason_needs_assurance_reasoning_to_be_the_unbuilt_one():
    case = make_case()
    snapshot = all_available_snapshot(
        case, lambda i: unbuilt(i) if i.capability == catalog.DATA_INTEGRITY else i)
    # reasoning itself is available, so the block is an ordinary unavailable capability, not "no runtime"
    assert start_assessment_availability(case, snapshot).reason_code.text == "required_capability_unavailable"


AFTER_PREFLIGHT = [s for s in CaseLifecycleState if s is not CaseLifecycleState.PREFLIGHT]


@pytest.mark.parametrize("lifecycle", AFTER_PREFLIGHT)
def test_a_case_past_preflight_cannot_start_an_assessment(lifecycle):
    case = make_case(lifecycle_state=lifecycle)
    action = start_assessment_availability(case, all_available_snapshot(case))
    assert action.allowed is False
    assert action.reason_code.text == "case_not_in_preflight"


def test_actions_are_the_start_assessment_decision_alone():
    case, snapshot = make_case(), build_stage_snapshot()
    assert available_actions(case, snapshot) == (start_assessment_availability(case, snapshot),)


# ------------------------------------------------------------------ standing limitations
def test_while_reasoning_is_unbuilt_the_view_carries_the_no_conclusion_limitation():
    [limitation] = context().critical_limitations
    assert limitation.summary == (
        "Assessment capabilities are not yet implemented. No assurance conclusion has been produced.")
    assert limitation.code.text == "assessment_not_implemented"
    assert limitation.decision_relevant is True
    assert limitation.detail_available is False


def test_the_limitation_is_scoped_to_the_case_and_uses_its_label():
    [labelled] = context(make_case(label="Night patrol v3")).critical_limitations
    [unlabelled] = context().critical_limitations
    scope = labelled.scope_refs[0]
    assert (scope.entity_type.text, scope.entity_id) == ("case", str(CASE_ID))
    assert labelled.scope_refs[0].display_label == "Night patrol v3"
    assert unlabelled.scope_refs[0].display_label == "Assurance case"


def test_limitation_identity_is_stable_per_case_and_differs_between_cases():
    first = critical_limitations(make_case(), build_stage_snapshot())[0].limitation_id
    again = critical_limitations(make_case(), build_stage_snapshot())[0].limitation_id
    other_case = make_case().evolve(case_id=type(CASE_ID).from_entropy(lambda n: b"\x09" * n))
    other = critical_limitations(other_case, build_stage_snapshot(other_case))[0].limitation_id
    assert first == again
    assert first != other


def test_the_limitation_disappears_only_when_reasoning_is_available():
    case = make_case()
    assert critical_limitations(case, all_available_snapshot(case)) == ()


def test_a_limited_reasoning_capability_keeps_the_limitation():
    case = make_case()
    limited = all_available_snapshot(
        case, lambda i: i.evolve(capability_state=CapabilityState.LIMITED)
        if i.capability == catalog.ASSURANCE_REASONING else i)
    assert len(critical_limitations(case, limited)) == 1


def test_a_snapshot_with_no_reasoning_entry_keeps_the_limitation():
    case = make_case()
    empty = PreflightPlanner(Declared([]), FixedClock()).plan(case, ())
    assert len(critical_limitations(case, empty)) == 1


def test_dates_are_not_used_for_identity():
    assert T0 != T1
    assert context(at=T0).critical_limitations == context(at=T1).critical_limitations


def test_the_source_default_planner_is_the_build_stage_one():
    snapshot = PreflightPlanner(BuildStageCapabilitySource(), FixedClock()).plan(make_case(), ())
    assert snapshot == build_stage_snapshot()


def test_an_empty_snapshot_blocks_start_by_naming_every_catalog_capability():
    case = make_case()
    empty = PreflightPlanner(Declared([]), FixedClock()).plan(case, ())
    action = start_assessment_availability(case, empty)
    assert action.reason_code.text == "required_capability_unavailable"
    assert action.reason_text == (
        "Assessment cannot start: not available: asset_identity, dataset_semantic_adapter, model_runtime, "
        "data_integrity, model_integrity, drift_assessment, tiir_verification, assurance_reasoning.")


def test_asset_identity_alone_unavailable_blocks_start():
    case = make_case()
    snapshot = all_available_snapshot(
        case, lambda i: i.evolve(capability_state=CapabilityState.UNAVAILABLE)
        if i.capability == catalog.ASSET_IDENTITY else i)
    action = start_assessment_availability(case, snapshot)
    assert action.allowed is False
    assert action.reason_text == "Assessment cannot start: not available: asset_identity."


def test_assurance_reasoning_alone_unavailable_blocks_start_for_an_ordinary_reason():
    case = make_case()
    snapshot = all_available_snapshot(
        case, lambda i: i.evolve(capability_state=CapabilityState.UNAVAILABLE)
        if i.capability == catalog.ASSURANCE_REASONING else i)
    action = start_assessment_availability(case, snapshot)
    assert action.allowed is False
    assert action.reason_code.text == "required_capability_unavailable"
    assert action.reason_text == "Assessment cannot start: not available: assurance_reasoning."


@pytest.mark.parametrize("lifecycle", AFTER_PREFLIGHT)
def test_the_context_reports_the_case_lifecycle_it_was_given(lifecycle):
    case = make_case(lifecycle_state=lifecycle)
    assert context(case).to_wire()["case_lifecycle_state"] == lifecycle.to_wire()


def test_the_label_must_be_stated_even_when_it_is_null():
    data = json.loads(context().canonical_bytes())
    del data["case_label"]
    with pytest.raises(ValidationError):
        CaseContextProjection.model_validate_json(json.dumps(data))
