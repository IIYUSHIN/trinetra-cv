"""IMP-0086/0087/0088 tests: CapabilitySnapshot, requirement evaluation and snapshot digests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from trinetra.domain.capability import CapabilityName, CapabilityRequirement, CapabilityState
from trinetra.domain.capability_digest import (
    ITEMS_SCHEMA,
    SNAPSHOT_SCHEMA,
    capability_items_digest,
    capability_snapshot_digest,
)
from trinetra.domain.capability_evaluation import (
    RequirementEvaluation,
    RequirementOutcome,
    evaluate_requirements,
)
from trinetra.domain.capability_snapshot import (
    AccessName,
    CapabilityAssessment,
    CapabilityReasonCode,
    CapabilitySnapshot,
)
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.digests import DigestSha256
from trinetra.domain.references import ReferencePurpose
from trinetra.domain.timing import UtcTimestamp

T0 = UtcTimestamp.parse("2026-10-31T10:00:00Z")
T1 = UtcTimestamp.parse("2026-10-31T10:00:05Z")
S = CapabilityState
CASE = CaseId.from_entropy(lambda n: b"\x01" * n)


def item(name="asset_identity", state=S.AVAILABLE, code="available_now", text="Works.", *,
         req=(), have=(), purposes=()):  # fmt: skip
    return CapabilityAssessment(
        capability=CapabilityName(name), capability_state=state, reason_code=CapabilityReasonCode(code),
        reason_text=text, required_access=tuple(AccessName(a) for a in req),
        available_access=tuple(AccessName(a) for a in have),
        required_reference_purposes=tuple(ReferencePurpose(p) for p in purposes),
    )


def snapshot(*items, at=T0, revision=1):
    return CapabilitySnapshot.of(
        captured_at=at, case_id=CASE, case_state_revision=CaseStateRevision(revision), items=list(items),
    )


HONEST = (
    item("asset_identity"),
    item("model_runtime", S.UNAVAILABLE, "not_implemented_in_current_build_stage", "Not built yet."),
    item("data_integrity", S.UNAVAILABLE, "not_implemented_in_current_build_stage", "Not built yet."),
)


# ------------------------------------------------------------------------------ assessment (0086)
def test_an_assessment_carries_the_source_fields_and_no_claim_semantics():
    a = item(req=("cpu",), have=("cpu",), purposes=("ground_truth",))
    expected = {"capability", "capability_state", "reason_code", "reason_text", "required_access",
                "available_access", "required_reference_purposes"}  # fmt: skip
    assert set(a.to_wire()) == expected
    assert not set(CapabilityAssessment.model_fields) & {"status", "state", "score", "verdict", "claim"}


@pytest.mark.parametrize("text", ["", " lead", "trail ", "a\x00b", "a\nb", "x" * 2049])
def test_reason_text_is_bounded_clean_text(text):
    with pytest.raises(ValidationError):
        item(text=text)


def test_reason_text_boundaries_are_accepted():
    assert item(text="x").reason_text == "x"
    assert len(item(text="é" * 2048).reason_text) == 2048


def test_access_and_purpose_lists_must_be_sorted_and_unique():
    assert item(req=("cpu", "gpu")).required_access == (AccessName("cpu"), AccessName("gpu"))
    for bad in (("gpu", "cpu"), ("cpu", "cpu")):
        with pytest.raises(ValidationError):
            item(req=bad)
        with pytest.raises(ValidationError):
            item(have=bad)
        with pytest.raises(ValidationError):
            item(purposes=bad)


def test_tokens_of_different_meaning_never_mix():
    with pytest.raises(ValidationError):
        CapabilityAssessment(capability=CapabilityName("a"), capability_state=S.AVAILABLE,
                             reason_code=CapabilityName("a"), reason_text="x", required_access=(),
                             available_access=(), required_reference_purposes=())  # fmt: skip
    with pytest.raises(ValidationError):
        CapabilityAssessment(capability=CapabilityName("a"), capability_state="available",
                             reason_code=CapabilityReasonCode("a"), reason_text="x", required_access=(),
                             available_access=(), required_reference_purposes=())  # fmt: skip
    # a plain list is not a tuple: mutable inputs are refused
    with pytest.raises(ValidationError):
        CapabilityAssessment(capability=CapabilityName("a"), capability_state=S.AVAILABLE,
                             reason_code=CapabilityReasonCode("a"), reason_text="x", required_access=[],
                             available_access=(), required_reference_purposes=())  # fmt: skip


# ------------------------------------------------------------------------------ snapshot (0086)
def test_items_are_put_in_capability_order_by_the_builder():
    snap = snapshot(*reversed(HONEST))
    assert [i.capability.text for i in snap.items] == ["asset_identity", "data_integrity", "model_runtime"]


def test_a_snapshot_built_directly_must_already_be_ordered_and_unique():
    ordered = tuple(sorted(HONEST, key=lambda i: i.capability.text))
    base = {"captured_at": T0, "case_id": CASE, "case_state_revision": CaseStateRevision(1)}
    assert CapabilitySnapshot(**base, items=ordered).items == ordered
    with pytest.raises(ValidationError, match="ascending"):
        CapabilitySnapshot(**base, items=tuple(reversed(ordered)))
    with pytest.raises(ValidationError, match="ascending"):
        CapabilitySnapshot(**base, items=(ordered[0], ordered[0]))
    with pytest.raises(ValidationError):
        CapabilitySnapshot(**base, items=list(ordered))


def test_the_same_facts_in_any_order_give_the_same_bytes():
    a = snapshot(*HONEST)
    b = snapshot(*reversed(HONEST))
    assert a.canonical_bytes() == b.canonical_bytes() and a == b


def test_snapshot_size_is_bounded():
    many = [item(f"cap_{i:03d}") for i in range(64)]
    assert len(snapshot(*many).items) == 64
    with pytest.raises(ValidationError):
        snapshot(*many, item("one_too_many"))


def test_a_snapshot_names_its_case_and_revision_and_has_no_generic_source_revision():
    snap = snapshot(*HONEST, revision=7)
    wire = snap.to_wire()
    assert wire["case_id"] == str(CASE) and wire["case_state_revision"] == 7
    assert "source_revision" not in wire


def test_get_finds_a_capability_or_none():
    snap = snapshot(*HONEST)
    assert snap.get(CapabilityName("model_runtime")).capability_state is S.UNAVAILABLE
    assert snap.get(CapabilityName("nope")) is None


def test_the_wire_form_round_trips():
    snap = snapshot(*HONEST)
    assert CapabilitySnapshot.model_validate_json(snap.canonical_bytes()) == snap


def test_an_empty_snapshot_is_allowed_and_means_nothing_is_claimed():
    assert snapshot().items == ()


# ------------------------------------------------------------------------------ evaluation (0087)
def need(name, minimum=S.AVAILABLE):
    return CapabilityRequirement(capability=CapabilityName(name), minimum=minimum)


def test_each_requirement_is_judged_against_the_observed_state():
    snap = snapshot(item("a", S.AVAILABLE), item("b", S.LIMITED), item("c", S.UNAVAILABLE))
    result = evaluate_requirements([need("a"), need("b"), need("c")], snap)
    assert [(o.capability.text, o.observed_capability_state, o.satisfied) for o in result.outcomes] == [
        ("a", S.AVAILABLE, True), ("b", S.LIMITED, False), ("c", S.UNAVAILABLE, False)]  # fmt: skip
    assert not result.all_satisfied and [o.capability.text for o in result.unmet] == ["b", "c"]


def test_limited_satisfies_only_a_requirement_that_accepts_limited():
    snap = snapshot(item("b", S.LIMITED))
    assert evaluate_requirements([need("b", S.LIMITED)], snap).all_satisfied
    assert not evaluate_requirements([need("b", S.AVAILABLE)], snap).all_satisfied


def test_a_missing_capability_is_unavailable_and_reported_as_unlisted():
    result = evaluate_requirements([need("ghost", S.LIMITED)], snapshot(item("a")))
    (outcome,) = result.outcomes
    assert outcome.observed_capability_state is S.UNAVAILABLE
    assert outcome.listed_in_snapshot is False and outcome.satisfied is False


def test_a_listed_capability_is_marked_listed():
    (outcome,) = evaluate_requirements([need("a")], snapshot(item("a"))).outcomes
    assert outcome.listed_in_snapshot is True


def test_results_keep_the_order_given_and_no_requirements_is_vacuously_satisfied():
    result = evaluate_requirements([need("z"), need("a")], snapshot(item("a"), item("z")))
    assert [o.capability.text for o in result.outcomes] == ["z", "a"]
    empty = evaluate_requirements([], snapshot())
    assert empty.all_satisfied and empty.unmet == () and empty.outcomes == ()


def test_an_outcome_cannot_be_forged():
    base = {"capability": CapabilityName("a"), "minimum": S.AVAILABLE, "observed_capability_state": S.LIMITED,
            "listed_in_snapshot": True, "satisfied": False}  # fmt: skip
    assert RequirementOutcome(**base).satisfied is False
    with pytest.raises(ValidationError, match="disagrees"):
        RequirementOutcome(**{**base, "satisfied": True})
    with pytest.raises(ValidationError, match="UNAVAILABLE"):
        RequirementOutcome(**{**base, "listed_in_snapshot": False})


def test_evaluation_is_immutable_and_bounded():
    result = evaluate_requirements([need("a")], snapshot(item("a")))
    with pytest.raises(ValidationError):
        result.outcomes = ()
    with pytest.raises(ValidationError):
        RequirementEvaluation(outcomes=list(result.outcomes))


# ------------------------------------------------------------------------------ digests (0088)
def test_the_digest_is_sha256_of_the_canonical_bytes_with_a_schema_tag():
    snap = snapshot(*HONEST)
    body = json.dumps({"schema": SNAPSHOT_SCHEMA, "snapshot": snap.to_wire()}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)  # fmt: skip
    assert capability_snapshot_digest(snap) == DigestSha256.of(body.encode())
    assert capability_snapshot_digest(snap).to_wire().startswith("sha256:")


def test_the_digest_is_stable_across_input_order_and_instances():
    forward, backward = snapshot(*HONEST), snapshot(*reversed(HONEST))
    assert capability_snapshot_digest(forward) == capability_snapshot_digest(backward)


@pytest.mark.parametrize("change", ["time", "revision", "state", "reason", "item_removed"])
def test_any_change_to_the_snapshot_changes_its_digest(change):
    base = snapshot(*HONEST)
    stage = "not_implemented_in_current_build_stage"
    variants = {
        "time": snapshot(*HONEST, at=T1),
        "revision": snapshot(*HONEST, revision=2),
        "state": snapshot(HONEST[0], item("model_runtime", S.LIMITED, stage, "Not built yet."), HONEST[2]),
        "reason": snapshot(HONEST[0], item("model_runtime", S.UNAVAILABLE, "other_reason", "Not built yet."),
                           HONEST[2]),
        "item_removed": snapshot(*HONEST[:2]),
    }
    other = variants[change]
    assert capability_snapshot_digest(base) != capability_snapshot_digest(other)


def test_the_items_digest_ignores_time_and_revision_but_not_facts():
    a, b = snapshot(*HONEST), snapshot(*HONEST, at=T1, revision=9)
    assert capability_items_digest(a) == capability_items_digest(b)
    assert capability_snapshot_digest(a) != capability_snapshot_digest(b)
    assert capability_items_digest(a) != capability_items_digest(snapshot(*HONEST[:2]))


def test_the_two_digests_and_the_schema_tags_are_distinct_domains():
    snap = snapshot(*HONEST)
    assert SNAPSHOT_SCHEMA != ITEMS_SCHEMA
    assert capability_snapshot_digest(snap) != capability_items_digest(snap)


def test_an_empty_snapshot_has_a_stable_known_answer():
    empty = snapshot()
    assert capability_items_digest(empty) == DigestSha256.of(
        b'{"items":[],"schema":"trinetra_capability_items_v1"}')


def test_access_and_purpose_lists_are_capped_at_sixty_four():
    ok = tuple(f"acc_{i:03d}" for i in range(64))
    assert len(item(req=ok, have=ok, purposes=ok).required_access) == 64
    too_many = (*ok, "acc_zzz")
    for kwargs in ({"req": too_many}, {"have": too_many}, {"purposes": too_many}):
        with pytest.raises(ValidationError):
            item(**kwargs)


def test_the_requirement_list_is_capped_at_two_hundred_fifty_six():
    snap = snapshot(item("a"))
    assert len(evaluate_requirements([need("a")] * 256, snap).outcomes) == 256
    with pytest.raises(ValidationError):
        evaluate_requirements([need("a")] * 257, snap)
