"""Synthetic authenticated sealing; wrong key/AAD/ciphertext/schema never reveal plaintext."""

import base64
import json
import secrets

import pytest

from trinetra.domain.digests import DigestSha256
from trinetra_eval.hidden import private_document
from trinetra_eval.overlap import EvaluationPopulation as P
from trinetra_eval.sealing import PrivateTruthBundle, open_truth, seal_truth


def bundle(population="BLIND"):
    return PrivateTruthBundle(
        population_manifest={"synthetic": True, "population": population},
        hidden_truth_records=({"canary": "PRIVATE"},),
        oracle_records=(),
    )


def test_round_trip_is_private_and_nonces_are_fresh():
    key = secrets.token_bytes(32)
    digest = DigestSha256.of(b"synthetic-commitment")
    first = seal_truth(bundle(), P.BLIND, digest, key)
    second = seal_truth(bundle(), P.BLIND, digest, key)
    assert first.nonce_b64 != second.nonce_b64
    assert "PRIVATE" not in first.model_dump_json()
    opened = open_truth(first, key)
    assert private_document(opened)["hidden_truth_records"] == [{"canary": "PRIVATE"}]
    with pytest.raises(TypeError):
        json.dumps(opened)


def test_ciphertext_key_and_commitment_tampering_fail_authentication():
    key = secrets.token_bytes(32)
    envelope = seal_truth(bundle("FINAL"), P.FINAL, DigestSha256.of(b"synthetic"), key)
    raw = bytearray(base64.b64decode(envelope.ciphertext_b64))
    raw[0] ^= 1
    variants = (
        envelope.evolve(ciphertext_b64=base64.b64encode(raw).decode()),
        envelope.evolve(population_commitment_digest=DigestSha256.of(b"other")),
    )
    for changed in variants:
        with pytest.raises(ValueError, match="authentication"):
            open_truth(changed, key)
    with pytest.raises(ValueError, match="authentication"):
        open_truth(envelope, secrets.token_bytes(32))


@pytest.mark.parametrize("size", [0, 16, 24, 31, 33])
def test_non_aes256_keys_rejected(size):
    with pytest.raises(ValueError):
        seal_truth(bundle(), P.BLIND, DigestSha256.of(b"synthetic"), secrets.token_bytes(size))


def test_non_holdout_population_rejected():
    with pytest.raises(ValueError):
        seal_truth(bundle(), P.DEVELOPMENT, DigestSha256.of(b"synthetic"), secrets.token_bytes(32))


def test_envelope_population_cannot_be_relabelled_with_the_same_raw_key():
    key = secrets.token_bytes(32)
    envelope = seal_truth(bundle(), P.BLIND, DigestSha256.of(b"synthetic"), key)
    with pytest.raises(ValueError, match="population"):
        open_truth(envelope.evolve(population=P.FINAL), key)


def test_external_aesgcm_decrypts_exact_plaintext_with_frozen_aad():
    import hashlib

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = bytes(range(32))
    envelope = seal_truth(bundle(), P.BLIND, DigestSha256.of(b"synthetic"), key)
    aad = b"trinetra-sealed-truth-v1sha256:" + hashlib.sha256(b"synthetic").hexdigest().encode()
    plaintext = AESGCM(key).decrypt(
        base64.b64decode(envelope.nonce_b64), base64.b64decode(envelope.ciphertext_b64), aad
    )
    assert plaintext == (
        b'{"hidden_truth_records":[{"canary":"PRIVATE"}],"oracle_records":'
        b'[],"population_manifest":{"population":"BLIND","synthetic":true}'
        b"}"
    )
