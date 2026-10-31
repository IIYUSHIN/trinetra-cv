"""IMP-0080/0081/0082 tests: Case aggregate, lifecycle and revision invariant, asset roles and links."""

from __future__ import annotations

import json

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
    validate_link_set,
)
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.timing import UtcTimestamp

T0 = UtcTimestamp.parse("2026-10-31T10:00:00Z")
T1 = UtcTimestamp.parse("2026-10-31T10:00:05Z")
R = CaseAssetRole


def case_id(n=1):
    return CaseId.from_entropy(lambda size: bytes([n]) * size)


def asset(n):
    return AssetId.from_entropy(lambda size: bytes([n]) * size)


MINIMAL = [(R.DATASET, asset(1)), (R.MODEL, asset(2))]


def links(entries=None, at=T0):
    return link_assets(MINIMAL if entries is None else entries, at)


def new_case(at=T0, **kw):
    return AssuranceCase.create(case_id(), links(), at, **kw)


def raw_case(**over):
    base = {"case_id": case_id(), "case_label": None, "lifecycle_state": INITIAL_LIFECYCLE,
            "case_state_revision": CaseStateRevision.first(), "created_at": T0, "updated_at": T0,
            "asset_links": links()}  # fmt: skip
    return AssuranceCase(**{**base, **over})


# ------------------------------------------------------------------ the Case aggregate
def test_a_new_case_is_in_preflight_at_revision_one_with_its_links():
    c = new_case()
    assert c.lifecycle_state is CaseLifecycleState.PREFLIGHT is INITIAL_LIFECYCLE
    assert c.case_state_revision == CaseStateRevision.first()
    assert c.created_at == c.updated_at == T0
    assert c.case_label is None and c.asset_links == links()


def test_a_label_is_optional_and_a_logical_identifier():
    assert new_case(label="Run 7 of the harbour set").case_label == "Run 7 of the harbour set"
    for bad in ("", " x", "x ", "a\nb", "x" * 129, 5):
        with pytest.raises(ValidationError):
            raw_case(case_label=bad)


def test_there_is_no_ready_lifecycle_state_no_generic_status_and_no_source_revision():
    assert "READY" not in {m.name for m in CaseLifecycleState}
    fields = set(AssuranceCase.model_fields)
    assert not fields & {"status", "state", "source_revision", "revision"}


def test_touch_moves_the_revision_by_exactly_one_and_keeps_everything_else():
    c = new_case()
    d = c.touch(T1)
    assert d.case_state_revision.value == 2 and d.updated_at == T1 and d.created_at == T0
    assert d.lifecycle_state is c.lifecycle_state and d.case_id == c.case_id
    assert d.asset_links == c.asset_links and d.case_label == c.case_label
    assert c.case_state_revision.value == 1  # the original is untouched
    assert d.touch(T1).case_state_revision.value == 3  # equal time is allowed


def test_time_cannot_move_backwards():
    c = new_case(T1)
    with pytest.raises(CaseInvariantError):
        c.touch(T0)
    with pytest.raises(ValidationError, match="updated_at"):
        raw_case(created_at=T1, updated_at=T0)


def test_the_aggregate_is_immutable_strict_and_closed():
    c = new_case()
    with pytest.raises(ValidationError):
        c.case_state_revision = CaseStateRevision(5)
    with pytest.raises(ValidationError):
        AssuranceCase.model_validate_json(json.dumps({**c.to_wire(), "extra": 1}))
    with pytest.raises(ValidationError):
        raw_case(lifecycle_state="ready")
    with pytest.raises(ValidationError):
        raw_case(case_state_revision=0)
    with pytest.raises(ValidationError):
        raw_case(asset_links=list(links()))


def test_a_case_without_a_complete_link_set_cannot_exist():
    with pytest.raises(ValidationError, match="required"):
        raw_case(asset_links=())
    with pytest.raises(ValidationError):
        raw_case(asset_links=links()[:1])


def test_the_wire_form_is_deterministic_and_round_trips():
    c = new_case(label="x")
    wire = c.to_wire()
    assert set(wire) == {"case_id", "case_label", "lifecycle_state", "case_state_revision", "created_at",
                         "updated_at", "asset_links"}  # fmt: skip
    assert wire["lifecycle_state"] == "preflight" and wire["case_state_revision"] == 1
    assert wire["asset_links"][0]["role"] == "dataset" and wire["asset_links"][0]["ordinal"] == 0
    again = AssuranceCase.model_validate_json(c.canonical_bytes())
    assert again == c and again.canonical_bytes() == c.canonical_bytes()


def test_the_revision_counter_stops_at_its_limit_instead_of_wrapping():
    top = raw_case(case_state_revision=CaseStateRevision(2**53 - 1))
    with pytest.raises(Exception, match="case_state_revision"):
        top.touch(T1)


# ------------------------------------------------------------------ roles and cardinality
def test_cardinality_matches_the_intake_slot_contracts():
    got = {r.value: ROLE_CARDINALITY[r] for r in R}
    want = {s.value.removesuffix("s"): (c.minimum, c.maximum) for s, c in SLOT_CONTRACTS.items()}
    assert got == want


def test_every_package_kind_has_exactly_one_role_and_produced_kinds_have_none():
    mapped = {k: role_for_kind(k) for k in AssetKind if k.value in {r.value for r in R}}
    assert {k.value: r.value for k, r in mapped.items()} == {k.value: k.value for k in mapped}
    assert len(mapped) == 6
    for produced in (AssetKind.INFERENCE_OUTPUT, AssetKind.INFERENCE_RECEIPT):
        with pytest.raises(CaseAssetLinkError) as exc:
            role_for_kind(produced)
        assert exc.value.details["kind"] == produced.to_wire()


# ------------------------------------------------------------------ building the link set
def test_links_are_numbered_from_zero_per_role_in_the_given_order():
    entries = [(R.DATASET, asset(1)), (R.MODEL, asset(2)), (R.REFERENCE, asset(3)),
               (R.REFERENCE, asset(4)), (R.CONTRIBUTOR_METADATA, asset(5)),
               (R.REFERENCE, asset(6))]  # fmt: skip
    got = links(entries)
    assert [(lk.role, lk.asset_id, lk.ordinal) for lk in got] == [
        (R.DATASET, asset(1), 0), (R.MODEL, asset(2), 0), (R.CONTRIBUTOR_METADATA, asset(5), 0),
        (R.REFERENCE, asset(3), 0), (R.REFERENCE, asset(4), 1), (R.REFERENCE, asset(6), 2),
    ]  # canonical order: role, then ordinal  # fmt: skip
    assert all(lk.linked_at == T0 for lk in got)


def test_dataset_and_model_always_sit_at_ordinal_zero():
    assert [lk.ordinal for lk in links()] == [0, 0]


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
    [(R.DATASET, asset(1)), (R.MODEL, asset(2)), (R.REFERENCE, asset(2))],  # model reused as reference
])  # fmt: skip
def test_an_invalid_link_set_cannot_be_built(entries):
    with pytest.raises(CaseAssetLinkError):
        links(entries)


def test_the_missing_role_is_named_in_the_error():
    with pytest.raises(CaseAssetLinkError) as exc:
        links([(R.MODEL, asset(2))])
    assert exc.value.details["role"] == "dataset"


def test_the_reference_cap_is_enforced_exactly():
    many = [(R.REFERENCE, AssetId.from_entropy(lambda s, i=i: i.to_bytes(4, "big") * (s // 4)))
            for i in range(1, MAX_LINKS_PER_ROLE + 1)]  # fmt: skip
    ok = links([*MINIMAL, *many])
    assert max(lk.ordinal for lk in ok if lk.role is R.REFERENCE) == MAX_LINKS_PER_ROLE - 1
    extra = (R.REFERENCE, AssetId.from_entropy(lambda s: b"\xff" * s))
    with pytest.raises(CaseAssetLinkError):
        links([*MINIMAL, *many, extra])


def test_validate_link_set_rejects_gaps_duplicates_and_misplaced_ordinals():
    d, m = links()
    ref0 = CaseAssetLink(asset_id=asset(3), role=R.REFERENCE, ordinal=0, linked_at=T0)
    ref2 = CaseAssetLink(asset_id=asset(4), role=R.REFERENCE, ordinal=2, linked_at=T0)
    ref1 = CaseAssetLink(asset_id=asset(5), role=R.REFERENCE, ordinal=1, linked_at=T0)
    validate_link_set([d, m, ref0, ref1])  # accepted in canonical order only
    with pytest.raises(CaseAssetLinkError, match="canonical order"):
        validate_link_set([ref1, m, d, ref0])
    with pytest.raises(CaseAssetLinkError, match="canonical order"):
        validate_link_set([m, d])
    with pytest.raises(CaseAssetLinkError, match="ordinals"):
        validate_link_set([d, m, ref0, ref2])  # gap
    with pytest.raises(CaseAssetLinkError, match="ordinals"):
        validate_link_set([d, m, ref0, ref0.__class__(asset_id=asset(9), role=R.REFERENCE, ordinal=0,
                                                       linked_at=T0)])  # fmt: skip
    with pytest.raises(CaseAssetLinkError, match="ordinals"):
        validate_link_set([CaseAssetLink(asset_id=asset(1), role=R.DATASET, ordinal=1, linked_at=T0), m])


def test_a_link_validates_its_own_ordinal_and_role():
    base = {"asset_id": asset(1), "role": R.DATASET, "linked_at": T0}
    assert CaseAssetLink(**base, ordinal=0).ordinal == 0
    assert CaseAssetLink(**base, ordinal=MAX_LINKS_PER_ROLE - 1).ordinal == MAX_LINKS_PER_ROLE - 1
    for bad in (-1, MAX_LINKS_PER_ROLE, True, "0", 0.0):
        with pytest.raises(ValidationError):
            CaseAssetLink(**base, ordinal=bad)
    for bad_role in ("Dataset", "produced", "DATASET"):
        with pytest.raises(ValidationError):
            CaseAssetLink(**{**base, "role": bad_role}, ordinal=0)


def test_links_are_immutable_and_round_trip():
    link = links()[0]
    with pytest.raises(ValidationError):
        link.ordinal = 2
    assert CaseAssetLink.model_validate(link.to_wire()) == link
    assert link.to_wire()["role"] == "dataset"


def test_validate_link_set_rejects_a_second_singular_role_even_when_numbered_correctly():
    d, m = links()
    second_model = CaseAssetLink(asset_id=asset(7), role=R.MODEL, ordinal=1, linked_at=T0)
    with pytest.raises(CaseAssetLinkError, match="too many") as exc:
        validate_link_set([d, m, second_model])
    assert exc.value.details["role"] == "model"


def test_the_same_package_in_any_arrival_order_gives_the_same_case_bytes():
    a = [(R.DATASET, asset(1)), (R.MODEL, asset(2)), (R.CONTRIBUTOR_METADATA, asset(5))]
    b = [(R.CONTRIBUTOR_METADATA, asset(5)), (R.MODEL, asset(2)), (R.DATASET, asset(1))]
    ca = AssuranceCase.create(case_id(), links(a), T0)
    cb = AssuranceCase.create(case_id(), links(b), T0)
    assert ca.canonical_bytes() == cb.canonical_bytes()


def test_a_case_rejects_links_that_are_not_in_canonical_order():
    shuffled = tuple(reversed(links()))
    with pytest.raises(ValidationError, match="canonical order"):
        raw_case(asset_links=shuffled)
