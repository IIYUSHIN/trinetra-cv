"""IMP-0301: receipt uniqueness is not a live freshness verification result."""
import secrets
from typing import Self

from pydantic import field_validator

from trinetra.domain.base import DomainModel


class ChallengeNonceField(DomainModel):
    receipt_nonce: bytes
    freshness_challenge: bytes | None = None

    @field_validator("receipt_nonce")
    @classmethod
    def nonce_size(cls, value: bytes) -> bytes:
        if len(value) != 16:
            raise ValueError("receipt nonce requires exactly 16 bytes")
        return value

    @field_validator("freshness_challenge")
    @classmethod
    def challenge_size(cls, value: bytes | None) -> bytes | None:
        if value is not None and len(value) != 32:
            raise ValueError("freshness challenge requires exactly 32 bytes")
        return value

    @classmethod
    def generate(cls, *, freshness_challenge: bytes | None = None) -> Self:
        """Only receipt uniqueness is generated here; challenges belong to the verifier."""
        return cls(receipt_nonce=secrets.token_bytes(16), freshness_challenge=freshness_challenge)
