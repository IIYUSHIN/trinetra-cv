"""IMP-0417: distinct opaque truth-key handles; no production signing authority."""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from trinetra.domain.digests import DigestSha256
from trinetra_eval.key_purposes import EvaluationKeyPurpose, require_purpose
from trinetra_eval.overlap import EvaluationPopulation
from trinetra_eval.sealing import PrivateTruthBundle, SealedTruthEnvelope, open_truth, seal_truth


class TruthKey:
    __slots__ = ("__key", "purpose")
    __key: bytes
    purpose: EvaluationKeyPurpose

    def __init__(self, material: bytes, purpose: EvaluationKeyPurpose) -> None:
        if type(material) is not bytes or len(material) != 32:
            raise ValueError("truth key must contain 32 bytes")
        if purpose not in (
            EvaluationKeyPurpose.BLIND_TRUTH_ENCRYPTION,
            EvaluationKeyPurpose.FINAL_TRUTH_ENCRYPTION,
        ):
            raise ValueError("not a truth-encryption purpose")
        object.__setattr__(self, "_TruthKey__key", material)
        object.__setattr__(self, "purpose", purpose)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("truth key is immutable")

    def __repr__(self) -> str:
        return "<private truth key>"

    def __getstate__(self) -> object:
        raise TypeError("truth keys cannot be serialized")

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError("truth keys cannot be serialized")

    def seal(
        self, bundle: PrivateTruthBundle, population: EvaluationPopulation, digest: DigestSha256
    ) -> SealedTruthEnvelope:
        require_purpose(self.purpose, _purpose(population))
        return seal_truth(bundle, population, digest, self.__key)

    def open(self, envelope: SealedTruthEnvelope) -> PrivateTruthBundle:
        require_purpose(self.purpose, _purpose(envelope.population))
        return open_truth(envelope, self.__key)


def _purpose(population: EvaluationPopulation) -> EvaluationKeyPurpose:
    if population is EvaluationPopulation.BLIND:
        return EvaluationKeyPurpose.BLIND_TRUTH_ENCRYPTION
    if population is EvaluationPopulation.FINAL:
        return EvaluationKeyPurpose.FINAL_TRUTH_ENCRYPTION
    raise ValueError("population has no sealed-truth key")


class BlindFinalKeys:
    __slots__ = ("blind", "final")
    blind: TruthKey
    final: TruthKey

    def __init__(self, blind: bytes, final: bytes) -> None:
        if hmac.compare_digest(blind, final):
            raise ValueError("Blind and Final require independent key material")
        object.__setattr__(self, "blind", TruthKey(blind, EvaluationKeyPurpose.BLIND_TRUTH_ENCRYPTION))
        object.__setattr__(self, "final", TruthKey(final, EvaluationKeyPurpose.FINAL_TRUTH_ENCRYPTION))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("truth key registry is immutable")

    @classmethod
    def generate(cls) -> BlindFinalKeys:
        return cls(secrets.token_bytes(32), secrets.token_bytes(32))
