"""IMP-0416: evaluator-only AES-256-GCM envelope with commitment-bound AAD."""

from __future__ import annotations

import base64
import secrets
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.jcs import canonicalize, loads_strict
from trinetra_eval.hidden import PrivateRecord, PrivateSchema, private_document
from trinetra_eval.overlap import EvaluationPopulation


def _decode(text: str) -> bytes:
    value = base64.b64decode(text, validate=True)
    if base64.b64encode(value).decode() != text:
        raise ValueError("noncanonical sealed envelope encoding")
    return value


class SealedTruthEnvelope(DomainModel):
    schema_version: Literal[1] = 1
    population: EvaluationPopulation
    population_commitment_digest: DigestSha256
    algorithm: Literal["AES-256-GCM"] = "AES-256-GCM"
    nonce_b64: str
    ciphertext_b64: str

    @model_validator(mode="after")
    def profile(self) -> SealedTruthEnvelope:
        if self.population not in (EvaluationPopulation.BLIND, EvaluationPopulation.FINAL):
            raise ValueError("only Blind/Final truth is sealed")
        if len(_decode(self.nonce_b64)) != 12 or len(_decode(self.ciphertext_b64)) < 16:
            raise ValueError("invalid AES-GCM envelope lengths")
        return self


class _BundleSchema(PrivateSchema):
    population_manifest: dict[str, object]
    hidden_truth_records: tuple[dict[str, object], ...]
    oracle_records: tuple[dict[str, object], ...]


class PrivateTruthBundle(PrivateRecord):
    __slots__ = ()
    SCHEMA = _BundleSchema


def _aad(digest: DigestSha256) -> bytes:
    return b"trinetra-sealed-truth-v1" + str(digest).encode("utf-8")


def seal_truth(
    bundle: PrivateTruthBundle, population: EvaluationPopulation, commitment_digest: DigestSha256, key: bytes
) -> SealedTruthEnvelope:
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    if type(commitment_digest) is not DigestSha256 or type(bundle) is not PrivateTruthBundle:
        raise ValueError("invalid private sealing contract")
    if population not in (EvaluationPopulation.BLIND, EvaluationPopulation.FINAL):
        raise ValueError("only Blind/Final truth is sealed")
    if private_document(bundle)["population_manifest"].get("population") != population.value:
        raise ValueError("private bundle population mismatch")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, bundle.evaluator_private_bytes(), _aad(commitment_digest))
    return SealedTruthEnvelope(
        population=population,
        population_commitment_digest=commitment_digest,
        nonce_b64=base64.b64encode(nonce).decode(),
        ciphertext_b64=base64.b64encode(ciphertext).decode(),
    )


def open_truth(envelope: SealedTruthEnvelope, key: bytes) -> PrivateTruthBundle:
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    # Validate the closed envelope again so mutation through low-level APIs is not trusted.
    checked = SealedTruthEnvelope(**envelope.to_wire())
    plaintext: bytes | None = None
    try:
        plaintext = AESGCM(key).decrypt(
            _decode(checked.nonce_b64),
            _decode(checked.ciphertext_b64),
            _aad(checked.population_commitment_digest),
        )
    except InvalidTag:
        plaintext = None
    if plaintext is None:
        raise ValueError("sealed truth authentication failed")
    doc = loads_strict(plaintext)
    if canonicalize(doc) != plaintext or not isinstance(doc, dict):
        raise ValueError("sealed truth plaintext is not canonical")
    if doc["population_manifest"].get("population") != checked.population.value:
        raise ValueError("sealed truth population mismatch")
    # JSON arrays become tuples only at the explicit private parsing boundary.
    return PrivateTruthBundle(
        population_manifest=doc["population_manifest"],
        hidden_truth_records=tuple(doc["hidden_truth_records"]),
        oracle_records=tuple(doc["oracle_records"]),
    )
