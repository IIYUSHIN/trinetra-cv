"""IMP-0093 tests: the capability catalog, the build-stage declarations and the PreflightPlanner."""

from __future__ import annotations

import pytest

from projection_support import CASE_ID, T0, T1, FixedClock, make_case
from trinetra.application.preflight.catalog import (
    CAPABILITY_CATALOG,
    BuildStageCapabilitySource,
    build_stage_declarations,
)
from trinetra.application.preflight.planner import PreflightPlanError, PreflightPlanner
from trinetra.domain.capability import CapabilityName, CapabilityState
from trinetra.domain.capability_snapshot import AccessName, CapabilityAssessment, CapabilityReasonCode
from trinetra.domain.references import ReferencePurpose

AVAILABLE = CapabilityState.AVAILABLE
UNAVAILABLE = CapabilityState.UNAVAILABLE
LIMITED = CapabilityState.LIMITED


def item(name="cap_a", *, state=AVAILABLE, code="ready", text="It is ready.", needs=(), has=(), purposes=()):
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


def plan(*items, approved=(), case=None, moment=T1):
    return PreflightPlanner(Declared(*items), FixedClock(moment)).plan(case or make_case(), approved)


# ------------------------------------------------------------------ catalog
def test_the_catalog_names_the_eight_capabilities_in_order():
    assert [c.text for c in CAPABILITY_CATALOG] == [
        "asset_identity", "dataset_semantic_adapter", "model_runtime", "data_integrity",
        "model_integrity", "drift_assessment", "tiir_verification", "assurance_reasoning",
    ]


def test_this_build_stage_declares_only_asset_identity_available():
    declared = {d.capability.text: d for d in build_stage_declarations()}
    assert list(declared) == [c.text for c in CAPABILITY_CATALOG]
    assert declared["asset_identity"].capability_state is AVAILABLE
    assert declared["asset_identity"].reason_code.text == "available_in_current_build_stage"
    for name in list(declared)[1:]:
        assert declared[name].capability_state is UNAVAILABLE, name
        assert declared[name].reason_code.text == "not_implemented_in_current_build_stage", name
        assert "this build stage" in declared[name].reason_text, name


def test_declarations_name_no_access_and_no_reference_in_this_stage():
    for d in build_stage_declarations():
        assert (d.required_access, d.available_access, d.required_reference_purposes) == ((), (), ())


def test_the_source_returns_the_build_stage_declarations():
    assert BuildStageCapabilitySource().declared() == build_stage_declarations()


# ------------------------------------------------------------------ planner
def test_a_snapshot_is_for_this_case_revision_and_moment():
    snapshot = plan(item(), moment=T0)
    assert snapshot.case_id == CASE_ID
    assert snapshot.case_state_revision.value == 1
    assert snapshot.captured_at == T0


def test_the_snapshot_follows_the_case_revision():
    case = make_case().touch(T1)
    assert plan(item(), case=case).case_state_revision.value == 2


def test_items_come_out_in_capability_order_whatever_the_source_order():
    snapshot = plan(item("zeta"), item("alpha"), item("mid"))
    assert [i.capability.text for i in snapshot.items] == ["alpha", "mid", "zeta"]


def test_a_capability_declared_twice_is_refused():
    with pytest.raises(PreflightPlanError) as error:
        plan(item("dup"), item("dup", code="other"))
    assert error.value.code == "PREFLIGHT_PLAN_INVALID"


def test_an_empty_declaration_gives_an_empty_snapshot():
    assert plan().items == ()


def test_a_ready_capability_is_kept_as_declared():
    ready = item(needs=["gpu"], has=["gpu"], purposes=["labels"])
    assert plan(ready, approved=[ReferencePurpose("labels")]).items == (ready,)


def test_unavailable_stays_unavailable_with_its_own_reason_even_when_all_is_present():
    declared = item(state=UNAVAILABLE, code="not_built", text="Not built.")
    assert plan(declared).items == (declared,)


def test_unavailable_with_missing_access_keeps_its_original_reason():
    declared = item(state=UNAVAILABLE, code="not_built", text="Not built.", needs=["gpu"])
    assert plan(declared).items == (declared,)


def test_missing_access_lowers_available_to_unavailable_and_names_it():
    got = plan(item(needs=["gpu", "tpu"], has=["tpu"])).items[0]
    assert got.capability_state is UNAVAILABLE
    assert got.reason_code.text == "required_access_missing"
    assert got.reason_text == "Required access is not available: gpu."


def test_missing_access_lowers_limited_too():
    got = plan(item(state=LIMITED, needs=["gpu"])).items[0]
    assert got.capability_state is UNAVAILABLE
    assert got.reason_code.text == "required_access_missing"


def test_access_lowering_keeps_everything_else_about_the_item():
    declared = item(needs=["gpu"], purposes=["labels"])
    got = plan(declared).items[0]
    assert got.capability == declared.capability
    assert got.required_access == declared.required_access
    assert got.available_access == declared.available_access
    assert got.required_reference_purposes == declared.required_reference_purposes


def test_unapproved_reference_purpose_lowers_to_unavailable_and_names_it():
    got = plan(item(purposes=["labels", "masks"]), approved=[ReferencePurpose("masks")]).items[0]
    assert got.capability_state is UNAVAILABLE
    assert got.reason_code.text == "required_reference_not_approved"
    assert got.reason_text == "No approved reference exists for: labels."


def test_unapproved_reference_lowers_limited_too():
    got = plan(item(state=LIMITED, purposes=["labels"])).items[0]
    assert got.capability_state is UNAVAILABLE
    assert got.reason_code.text == "required_reference_not_approved"


def test_access_is_checked_before_references():
    got = plan(item(needs=["gpu"], purposes=["labels"])).items[0]
    assert got.reason_code.text == "required_access_missing"


def test_every_required_purpose_must_be_approved():
    one_missing = plan(item(purposes=["a", "b"]), approved=[ReferencePurpose("a")]).items[0]
    assert one_missing.capability_state is UNAVAILABLE
    all_there = plan(item(purposes=["a", "b"]), approved=[ReferencePurpose("a"), ReferencePurpose("b")])
    assert all_there.items[0].capability_state is AVAILABLE


def test_extra_approved_purposes_change_nothing():
    assert plan(item(), approved=[ReferencePurpose("unrelated")]).items == (item(),)


def test_the_planner_never_raises_a_capability():
    declared = [item("a", state=UNAVAILABLE, code="no"), item("b", state=LIMITED, code="lim"),
                item("c", needs=["x"])]
    rank = {UNAVAILABLE: 0, LIMITED: 1, AVAILABLE: 2}
    for before, after in zip(sorted(declared, key=lambda d: d.capability.text),
                             plan(*declared, approved=[ReferencePurpose("zzz")]).items, strict=True):
        assert rank[after.capability_state] <= rank[before.capability_state]


def test_long_missing_lists_are_shortened_to_stay_within_the_text_bound():
    needs = [f"access_{n:02d}" for n in range(64)]
    got = plan(item(needs=needs)).items[0]
    assert got.reason_text.startswith("Required access is not available: access_00, access_01")
    assert got.reason_text.endswith("and 56 more.")
    assert "access_08" not in got.reason_text


def test_exactly_eight_missing_names_are_listed_in_full():
    needs = [f"access_{n}" for n in range(8)]
    got = plan(item(needs=needs)).items[0]
    assert "more" not in got.reason_text
    assert got.reason_text.endswith("access_7.")


def test_the_build_stage_plan_matches_the_declarations():
    snapshot = PreflightPlanner(BuildStageCapabilitySource(), FixedClock()).plan(make_case(), ())
    assert {i.capability.text: i for i in snapshot.items} == {
        d.capability.text: d for d in build_stage_declarations()}


def test_nine_missing_names_show_eight_and_count_the_ninth():
    needs = [f"access_{n}" for n in range(9)]
    got = plan(item(needs=needs)).items[0]
    assert got.reason_text.endswith("access_7 and 1 more.")
    assert "access_8" not in got.reason_text
