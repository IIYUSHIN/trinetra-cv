"""IMP-0415: digest/count-only commitment, detached Ed25519 signature.

The proposed commitment transcript is JCS of the closed CommitmentBody below.
It signs no scenario IDs, truth or split. See LANE_REQUESTS/B1-001.md; this is
contingent wire-profile engineering, not independent scientific acceptance.
"""

from __future__ import annotations

import base64
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import Field

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra_eval.blind import BlindPopulationManifest
from trinetra_eval.calibration import CalibrationPopulationManifest
from trinetra_eval.final import FinalPopulationManifest
from trinetra_eval.key_purposes import EvaluationKeyPurpose, require_purpose
from trinetra_eval.populations import DevelopmentPopulationManifest
from trinetra_eval.private import private_document

PopulationManifest = (
    DevelopmentPopulationManifest
    | CalibrationPopulationManifest
    | BlindPopulationManifest
    | FinalPopulationManifest
)


class CommitmentBody(DomainModel):
    schema_version: Literal[1] = 1
    purpose: Literal["EVALUATION_COMMITMENT_SIGNING"] = "EVALUATION_COMMITMENT_SIGNING"
    population_digest: DigestSha256
    scenario_count: int = Field(ge=0, le=2**53 - 1)
    eig_count: int = Field(ge=0, le=2**53 - 1)


class PopulationCommitment(DomainModel):
    body: CommitmentBody
    signature_b64: str


def commit_population(
    manifest: PopulationManifest, key: Ed25519PrivateKey, purpose: EvaluationKeyPurpose
) -> PopulationCommitment:
    require_purpose(purpose, EvaluationKeyPurpose.EVALUATION_COMMITMENT_SIGNING)
    doc = private_document(manifest)
    body = CommitmentBody(
        population_digest=doc["population_digest"],
        scenario_count=len(doc["scenario_ids"]),
        eig_count=len(doc["eig_ids"]),
    )
    return PopulationCommitment(
        body=body, signature_b64=base64.b64encode(key.sign(body.canonical_bytes())).decode()
    )


def verify_commitment(
    commitment: PopulationCommitment, key: Ed25519PublicKey, purpose: EvaluationKeyPurpose
) -> bool:
    require_purpose(purpose, EvaluationKeyPurpose.EVALUATION_COMMITMENT_SIGNING)
    try:
        signature = base64.b64decode(commitment.signature_b64, validate=True)
        if base64.b64encode(signature).decode() != commitment.signature_b64:
            return False
        key.verify(signature, commitment.body.canonical_bytes())
    except (InvalidSignature, ValueError):
        return False
    return True
