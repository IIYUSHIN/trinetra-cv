"""Synthetic Calibration metadata only; no real holdout population or truth."""

import json

import pytest

from trinetra.jcs import canonical_digest
from trinetra_eval.calibration import CalibrationPopulationManifest
from trinetra_eval.hidden import private_document
from trinetra_eval.overlap import EvaluationPopulation as P


def data():
    return {
        "population": P.CALIBRATION,
        "split_profile_digest": "sha256:" + "0" * 64,
        "scenario_ids": ("scn_" + "a" * 32,),
        "visible_manifest_digests": ("sha256:" + "1" * 64,),
        "eig_ids": ("eig_" + "b" * 32,),
        "claim_catalog_digest": "sha256:" + "2" * 64,
        "test_definition_catalog_digest": "sha256:" + "3" * 64,
        "policy_profile_digest": "sha256:" + "4" * 64,
        "scorer_profile_digest": "sha256:" + "5" * 64,
        "created_at": "2026-10-31T00:00:00Z",
    }


def make(**changes):
    body = data() | changes
    body["population_digest"] = canonical_digest(json.loads(json.dumps(body)))
    return CalibrationPopulationManifest(**body)


def test_membership_and_digest_are_private_and_reproducible():
    record = make()
    doc = private_document(record)
    assert doc["population"] == "CALIBRATION"
    assert doc["scenario_ids"] == ["scn_" + "a" * 32]
    assert "CALIBRATION" not in repr(record)
    with pytest.raises(TypeError):
        json.dumps(record)


@pytest.mark.parametrize(
    "change",
    [
        {"scenario_ids": ("scn_" + "a" * 32,) * 2},
        {"visible_manifest_digests": ()},
        {"eig_ids": ()},
        {"scorer_profile_digest": "unbound"},
        {"population": next(p for p in P if p is not P.CALIBRATION)},
    ],
)
def test_wrong_role_duplicate_members_and_unbound_catalogs_rejected(change):
    with pytest.raises(ValueError):
        make(**change)


def test_empty_population_is_recorded_without_claiming_qualification():
    doc = private_document(make(scenario_ids=(), visible_manifest_digests=(), eig_ids=()))
    assert doc["scenario_ids"] == []
    with pytest.raises(ValueError):
        CalibrationPopulationManifest(**data(), population_digest="sha256:" + "f" * 64)
