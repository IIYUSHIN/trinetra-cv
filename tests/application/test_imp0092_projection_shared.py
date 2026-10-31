"""IMP-0092 tests: ProjectionMeta and ActionAvailability, the pieces every projection shares."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from projection_support import T0, T1
from trinetra.application.projections.shared import (
    PROJECTION_SCHEMA_VERSION,
    ActionAvailability,
    ActionName,
    ActionReasonCode,
    ProjectionInconsistentError,
    ProjectionMeta,
    ProjectionSchemaVersion,
)
from trinetra.domain.entity import EntityRef, EntityType

GO = ActionName("go")
WHY = ActionReasonCode("blocked_for_a_reason")


def test_meta_carries_the_schema_version_and_the_generation_time():
    meta = ProjectionMeta.at(T1)
    assert meta.schema_version == ProjectionSchemaVersion("v1")
    assert PROJECTION_SCHEMA_VERSION.text == "v1"
    assert meta.projection_generated_at == T1
    assert meta.to_wire() == {"schema_version": "v1", "projection_generated_at": T1.to_wire()}


def test_meta_round_trips_through_canonical_json():
    meta = ProjectionMeta.at(T0)
    assert ProjectionMeta.model_validate_json(meta.canonical_bytes()) == meta


def test_meta_rejects_a_version_that_is_not_a_token():
    with pytest.raises(ValidationError):
        ProjectionMeta.model_validate_json(
            b'{"schema_version":"V 1","projection_generated_at":"2026-10-31T10:00:00Z"}')


def test_an_allowed_action_has_no_reason():
    action = ActionAvailability(action=GO, allowed=True)
    assert (action.reason_code, action.reason_text) == (None, None)
    assert (action.requires_confirmation, action.requires_reason, action.requires_fresh_revision) == (
        False, False, False)


@pytest.mark.parametrize("extra", [{"reason_code": WHY}, {"reason_text": "because"}, {"reason_code": WHY,
                                                                                         "reason_text": "x"}])
def test_an_allowed_action_may_not_carry_any_reason(extra):
    with pytest.raises(ValidationError):
        ActionAvailability(action=GO, allowed=True, **extra)


@pytest.mark.parametrize("given", [{}, {"reason_code": WHY}, {"reason_text": "because"}])
def test_a_disallowed_action_must_give_code_and_text(given):
    with pytest.raises(ValidationError):
        ActionAvailability(action=GO, allowed=False, **given)


def test_a_disallowed_action_with_both_is_valid():
    action = ActionAvailability(action=GO, allowed=False, reason_code=WHY, reason_text="because")
    assert action.to_wire()["reason_code"] == "blocked_for_a_reason"
    assert action.to_wire()["reason_text"] == "because"


def test_allowed_must_be_a_real_boolean():
    with pytest.raises(ValidationError):
        ActionAvailability.model_validate({"action": GO, "allowed": 1})


def test_the_flags_default_to_false_and_are_carried():
    action = ActionAvailability(action=GO, allowed=True, requires_confirmation=True, requires_reason=True,
                                requires_fresh_revision=True)
    wire = action.to_wire()
    assert (wire["requires_confirmation"], wire["requires_reason"], wire["requires_fresh_revision"]) == (
        True, True, True)


def test_an_asset_bound_action_carries_its_scope():
    scope = EntityRef(entity_type=EntityType("asset"), entity_id="asset_x", display_label="Asset x")
    action = ActionAvailability(action=GO, allowed=True, affected_scope_ref=scope)
    assert action.affected_scope_ref == scope
    assert ActionAvailability(action=GO, allowed=True).affected_scope_ref is None


def test_action_round_trips_through_canonical_json():
    action = ActionAvailability(action=GO, allowed=False, reason_code=WHY, reason_text="because",
                                requires_fresh_revision=True)
    assert ActionAvailability.model_validate_json(action.canonical_bytes()) == action


def test_the_inconsistency_error_has_its_stable_code():
    assert ProjectionInconsistentError.code == "PROJECTION_INCONSISTENT"
