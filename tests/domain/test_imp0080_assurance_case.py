"""IMP-0080/0081/0082 tests: Case header, lifecycle and revision invariant, asset roles and links."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.application.intake.slots import SLOT_CONTRACTS
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.assurance_case import INITIAL_LIFECYCLE, AssuranceCase, CaseInvariantError
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.case_assets import (
    MAX_LINKS_PER_ROLE,
    ROLE_CARDINALITY,
    CaseAssetLink,
    CaseAssetLinkError,
    CaseAssetRole,
    link_assets,
    role_for_kind,
)
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.timing import UtcTimestamp

T0 = UtcTimestamp.parse("2026-10-31T10:00:00Z")
T1 = UtcTimestamp.parse("2026-10-31T10:00:05Z")
T2 = UtcTimestamp.parse("2026-10-31T10:01:00Z")
R = CaseAssetRole


def case_id(n=1):
    return CaseId.from_entropy(lambda size: bytes([n]) * size)


def asset(n):
    return AssetId.from_entropy(lambda size: bytes([n]) * size)


MINIMAL = [(R.DATASET, asset(1)), (R.MODEL, asset(2))]


# ------------------------------------------------------------------ the Case header
def test_a_new_case_is_in_preflight_at_revision_one():
    c = AssuranceCase.create(case_id(), T0)
    assert c.lifecycle_state is CaseLifecycleState.PREFLIGHT is INITIAL_LIFECYCLE
    assert c.case_state_revision == CaseStateRevision.first()
    assert c.created_at == c.updated_at == T0


def test_there_is_no_ready_lifecycle_state_and_no_generic_status():
    assert "READY" not in {m.value for m in CaseLifecycleState}
    assert "status" not in AssuranceCase.model_fields and "state" not in AssuranceCase.model_fields
    assert "source_revision" not in AssuranceCase.model_fields


def test_touch_moves_the_revision_by_exactly_one_and_keeps_the_lifecycle():
    c = AssuranceCase.create(case_id(), T0)
    d = c.touch(T1)
    assert d.case_state_revision.value == 2 and d.updated_at == T1 and d.created_at == T0
    assert d.lifecycle_state is c.lifecycle_state and d.case_id == c.case_id
    assert c.case_state_revision.value == 1  # the original is untouched
    assert d.touch(T1).case_state_revision.value == 3  # equal time is allowed


def test_time_cannot_move_backwards():
    c = AssuranceCase.create(case_id(), T1)
    with pytest.raises(CaseInvariantError):
        c.touch(T0)
    with pytest.raises(ValidationError, match="updated_at"):
        AssuranceCase(case_id=case_id(), lifecycle_state=INITIAL_LIFECYCLE,
                      case_state_revision=CaseStateRevision.first(), created_at=T1,
                      updated_at=T0)  # fmt: skip


def test_the_header_is_immutable_strict_and_closed():
    c = AssuranceCase.create(case_id(), T0)
    with pytest.raises(ValidationError):
        c.case_state_revision = CaseStateRevision(5)
    with pytest.raises(ValidationError):
        AssuranceCase.model_validate({**c.to_wire(), "extra": 1})
    with pytest.raises(ValidationError):
        AssuranceCase(case_id=case_id(), lifecycle_state="READY",
                      case_state_revision=CaseStateRevision.first(), created_at=T0,
                      updated_at=T0)  # fmt: skip
    with pytest.raises(ValidationError):
        AssuranceCase(case_id=case_id(), lifecycle_state=INITIAL_LIFECYCLE,
                      case_state_revision=0, created_at=T0, updated_at=T0)  # fmt: skip


def test_the_wire_form_is_deterministic_and_carries_case_id_and_revision():
    c = AssuranceCase.create(case_id(), T0)
    wire = c.to_wire()
    assert set(wire) == {"case_id", "lifecycle_state", "case_state_revision", "created_at", "updated_at"}
    assert wire["lifecycle_state"] == "PREFLIGHT" and wire["case_state_revision"] == 1
    assert c.canonical_bytes() == AssuranceCase.model_validate(wire).canonical_bytes()


def test_the_revision_counter_stops_at_its_limit_instead_of_wrapping():
    top = CaseStateRevision(2**53 - 1)
    c = AssuranceCase(case_id=case_id(), lifecycle_state=INITIAL_LIFECYCLE, case_state_revision=top,
                      created_at=T0, updated_at=T0)  # fmt: skip
    with pytest.raises(Exception, match="case_state_revision"):
        c.touch(T1)


# ------------------------------------------------------------------ roles and cardinality
def test_cardinality_matches_the_intake_slot_contracts():
    got = {r.value: ROLE_CARDINALITY[r] for r in R}
    want = {s.value.removesuffix("s"): (c.minimum, c.maximum) for s, c in SLOT_CONTRACTS.items()}
    assert got == want | {"inference_record": want["inference_record"]}


def test_every_package_kind_has_exactly_one_role_and_produced_kinds_have_none():
    mapped = {k: role_for_kind(k) for k in AssetKind if k.value in {r.value for r in R}}
    assert {k.value: r.value for k, r in mapped.items()} == {k.value: k.value for k in mapped}
    assert len(mapped) == 6
    for produced in (AssetKind.INFERENCE_OUTPUT, AssetKind.INFERENCE_RECEIPT):
        with pytest.raises(CaseAssetLinkError) as exc:
            role_for_kind(produced)
        assert exc.value.details["kind"] == produced.to_wire()


# ------------------------------------------------------------------ building the link set
def test_links_are_numbered_per_role_in_the_given_order():
    entries = [(R.DATASET, asset(1)), (R.MODEL, asset(2)), (R.REFERENCE, asset(3)),
               (R.REFERENCE, asset(4)), (R.CONTRIBUTOR_METADATA, asset(5)),
               (R.REFERENCE, asset(6))]  # fmt: skip
    links = link_assets(case_id(), entries, T0)
    assert [(lk.role, lk.asset_id, lk.position) for lk in links] == [
        (R.DATASET, asset(1), 1), (R.MODEL, asset(2), 1), (R.REFERENCE, asset(3), 1),
        (R.REFERENCE, asset(4), 2), (R.CONTRIBUTOR_METADATA, asset(5), 1), (R.REFERENCE, asset(6), 3),
    ]  # fmt: skip
    assert all(lk.case_id == case_id() and lk.linked_at == T0 for lk in links)


def test_the_minimal_case_is_one_dataset_and_one_model():
    assert [lk.role for lk in link_assets(case_id(), MINIMAL, T0)] == [R.DATASET, R.MODEL]


@pytest.mark.parametrize("entries", [
    [(R.MODEL, asset(2))],  # no dataset
    [(R.DATASET, asset(1))],  # no model
    [],
    [(R.DATASET, asset(1)), (R.DATASET, asset(3)), (R.MODEL, asset(2))],  # two datasets
    [(R.DATASET, asset(1)), (R.MODEL, asset(2)), (R.MODEL, asset(3))],  # two models
    [(R.DATASET, asset(1)), (R.MODEL, asset(2)), (R.CONTRIBUTOR_METADATA, asset(3)),
     (R.CONTRIBUTOR_METADATA, asset(4))],  # two metadata
    [(R.DATASET, asset(1)), (R.MODEL, asset(2)), (R.ASSESSMENT_PROFILE, asset(3)),
     (R.ASSESSMENT_PROFILE, asset(4))],  # two profiles
    [(R.DATASET, asset(1)), (R.MODEL, asset(1))],  # one asset, two roles
])  # fmt: skip
def test_an_invalid_link_set_cannot_be_built(entries):
    with pytest.raises(CaseAssetLinkError):
        link_assets(case_id(), entries, T0)


def test_the_missing_roles_are_named_in_the_error():
    with pytest.raises(CaseAssetLinkError) as exc:
        link_assets(case_id(), [], T0)
    assert sorted(exc.value.details["roles"]) == ["dataset", "model"]


def test_the_reference_cap_is_enforced_exactly():
    many = [(R.REFERENCE, AssetId.from_entropy(lambda s, i=i: i.to_bytes(4, "big") * (s // 4)))
            for i in range(1, MAX_LINKS_PER_ROLE + 1)]  # fmt: skip
    ok = link_assets(case_id(), [*MINIMAL, *many], T0)
    assert max(lk.position for lk in ok if lk.role is R.REFERENCE) == MAX_LINKS_PER_ROLE
    extra = (R.REFERENCE, AssetId.from_entropy(lambda s: b"\xff" * s))
    with pytest.raises(CaseAssetLinkError):
        link_assets(case_id(), [*MINIMAL, *many, extra], T0)


def test_a_link_validates_its_own_position():
    base = {"case_id": case_id(), "asset_id": asset(1), "role": R.DATASET, "linked_at": T0}
    assert CaseAssetLink(**base, position=1).position == 1
    for bad in (0, -1, 2, True, "1", 1.0):
        with pytest.raises(ValidationError):
            CaseAssetLink(**base, position=bad)
    with pytest.raises(ValidationError):
        CaseAssetLink(**{**base, "role": "Dataset"}, position=1)
    with pytest.raises(ValidationError):
        CaseAssetLink(**{**base, "role": "produced"}, position=1)


def test_links_are_immutable_and_round_trip():
    link = link_assets(case_id(), MINIMAL, T0)[0]
    with pytest.raises(ValidationError):
        link.position = 2
    assert CaseAssetLink.model_validate(link.to_wire()) == link
    assert link.to_wire()["role"] == "dataset"
