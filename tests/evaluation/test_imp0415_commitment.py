"""Independent Ed25519 verification and substitution negatives, synthetic keys only."""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trinetra.jcs import canonical_digest
from trinetra_eval.commitment import commit_population, verify_commitment
from trinetra_eval.key_purposes import EvaluationKeyPurpose as P
from trinetra_eval.overlap import EvaluationPopulation
from trinetra_eval.populations import DevelopmentPopulationManifest


def manifest():
    body = {
        "population": EvaluationPopulation.DEVELOPMENT,
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
    return DevelopmentPopulationManifest(
        **body, population_digest=canonical_digest(json.loads(json.dumps(body)))
    )


def test_commitment_is_verifiable_without_private_membership():
    key = Ed25519PrivateKey.generate()
    signed = commit_population(manifest(), key, P.EVALUATION_COMMITMENT_SIGNING)
    key.public_key().verify(base64.b64decode(signed.signature_b64), signed.body.canonical_bytes())
    assert verify_commitment(signed, key.public_key(), P.EVALUATION_COMMITMENT_SIGNING)
    assert signed.body.scenario_count == 1 and signed.body.eig_count == 1
    assert "scn_" not in signed.model_dump_json() and "DEVELOPMENT" not in signed.model_dump_json()


def test_wrong_key_count_digest_and_signature_are_rejected():
    key = Ed25519PrivateKey.generate()
    signed = commit_population(manifest(), key, P.EVALUATION_COMMITMENT_SIGNING)
    assert not verify_commitment(
        signed, Ed25519PrivateKey.generate().public_key(), P.EVALUATION_COMMITMENT_SIGNING
    )
    for changed in (
        signed.evolve(body=signed.body.evolve(scenario_count=2)),
        signed.evolve(body=signed.body.evolve(population_digest="sha256:" + "f" * 64)),
        signed.evolve(signature_b64="not-base64"),
    ):
        assert not verify_commitment(changed, key.public_key(), P.EVALUATION_COMMITMENT_SIGNING)


def test_truth_keys_have_no_commitment_authority():
    with pytest.raises(ValueError):
        commit_population(manifest(), Ed25519PrivateKey.generate(), P.BLIND_TRUTH_ENCRYPTION)


def test_synthetic_known_answer_binds_the_exact_transcript():
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    signed = commit_population(manifest(), key, P.EVALUATION_COMMITMENT_SIGNING)
    expected_message = (
        b'{"eig_count":1,"population_digest":"sha256:9da2658946c24ac5c5bf6'
        b'12592c6f9502246316f1a2e51840e74c0a5245fba7d","purpose":"EVALUATI'
        b'ON_COMMITMENT_SIGNING","scenario_count":1,"schema_version":1}'
    )
    expected_signature = bytes.fromhex(
        "b83b26f9bc196cda9dc880fd8bc4889348859f402ab8fb5767e5ea2df1ecfa381607fa190cba41f5eab438050e8b97e367159bce5253927095e927b47c2ff603"
    )
    assert base64.b64decode(signed.signature_b64) == expected_signature
    key.public_key().verify(expected_signature, expected_message)
