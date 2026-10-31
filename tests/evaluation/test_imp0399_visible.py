"""Visible projection rejects truth channels and binds every permitted field."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.jcs import canonical_digest
from trinetra_eval.visible import VisibleScenarioManifest


def fields():
    return {
        "schema_version": 1,
        "scenario_id": "scn_" + "a" * 32,
        "assurance_package_ref": "pkg_01",
        "assurance_package_digest": "sha256:" + "0" * 64,
        "assessment_profile_ref": "profile_01",
        "visible_reference_refs": (),
        "declared_access_profile": "BLACK_BOX",
        "resource_hint": "CPU",
    }


def make(**changes):
    data = fields() | changes
    data["visible_manifest_digest"] = canonical_digest(json.loads(json.dumps(data)))
    return VisibleScenarioManifest(**data)


def test_manifest_exports_only_visible_fields_and_checks_digest():
    manifest = make()
    assert set(manifest.to_wire()) == set(fields()) | {"visible_manifest_digest"}
    assert "BLIND" not in manifest.model_dump_json()
    with pytest.raises(ValidationError):
        manifest.evolve(resource_hint="GPU")


@given(
    st.sampled_from(["cause", "split", "generation_seed", "oracle", "affected_entities", "evaluation_family"])
)
def test_hidden_fields_are_not_silently_discarded(field):
    with pytest.raises(ValidationError):
        make(**{field: "HIDDEN_CANARY"})


def test_unknown_schema_and_unstructured_payload_rejected():
    with pytest.raises(ValidationError):
        make(schema_version=2)
    with pytest.raises(ValidationError):
        make(resource_hint={"hidden": "CANARY"})
