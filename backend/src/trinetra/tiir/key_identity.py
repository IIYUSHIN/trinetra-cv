"""IMP-0286: public-key fingerprint and kid; neither confers operational trust."""
from pydantic import field_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256


class TrustedKeyIdentityVsKid(DomainModel):
    """Immutable Ed25519 public-key identity, separate from registry authorization."""

    raw_public_key: bytes

    @field_validator("raw_public_key")
    @classmethod
    def exact_public_key(cls, value: bytes) -> bytes:
        if len(value) != 32:
            raise ValueError("Ed25519 public keys require exactly 32 bytes")
        return value

    @property
    def key_fingerprint(self) -> DigestSha256:
        return DigestSha256.of(self.raw_public_key)

    @property
    def kid(self) -> bytes:
        """Routing hint only; candidates still require full identity and trust checks."""
        return self.key_fingerprint.raw[:16]
