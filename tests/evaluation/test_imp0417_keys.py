"""Generated disposable keys only; same material and cross-purpose uses fail."""

import json
import secrets

import pytest

from trinetra.domain.digests import DigestSha256
from trinetra_eval.key_purposes import EvaluationKeyPurpose as P
from trinetra_eval.keys import BlindFinalKeys, TruthKey
from trinetra_eval.overlap import EvaluationPopulation as E
from trinetra_eval.sealing import PrivateTruthBundle


def bundle():
    return PrivateTruthBundle(
        population_manifest={"synthetic": True, "population": "BLIND"},
        hidden_truth_records=(),
        oracle_records=(),
    )


def test_separate_keys_open_only_their_own_population():
    keys = BlindFinalKeys.generate()
    digest = DigestSha256.of(b"synthetic")
    envelope = keys.blind.seal(bundle(), E.BLIND, digest)
    assert keys.blind.open(envelope).evaluator_private_bytes() == bundle().evaluator_private_bytes()
    with pytest.raises(ValueError):
        keys.final.open(envelope)
    with pytest.raises(ValueError):
        keys.blind.open(envelope.evolve(population=E.FINAL))


def test_identical_material_cannot_be_registered_twice():
    key = secrets.token_bytes(32)
    with pytest.raises(ValueError, match="independent"):
        BlindFinalKeys(key, key)


@pytest.mark.parametrize("purpose", [P.EVALUATION_COMMITMENT_SIGNING, "BLIND_TRUTH_ENCRYPTION", None])
def test_untyped_or_signing_purposes_are_not_truth_keys(purpose):
    with pytest.raises(ValueError):
        TruthKey(secrets.token_bytes(32), purpose)


def test_keys_do_not_support_incidental_export_or_relabeling():
    key = TruthKey(secrets.token_bytes(32), P.BLIND_TRUTH_ENCRYPTION)
    for export in (json.dumps, vars, lambda k: k.__getstate__()):
        with pytest.raises(TypeError):
            export(key)
    with pytest.raises(AttributeError):
        key.purpose = P.FINAL_TRUTH_ENCRYPTION
