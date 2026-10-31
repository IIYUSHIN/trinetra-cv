"""IMP-0287: disjoint purposes and fixed Ed25519 public-key profile.

These are pure profile checks, not the persistent trusted-key registry or lifecycle
policy. A key fingerprint identifies bytes; it never establishes operational trust.
"""
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from trinetra.domain.base import DomainModel
from trinetra.tiir.errors import SigningKeyPurposeMismatch
from trinetra.tiir.key_identity import TrustedKeyIdentityVsKid

KeyPurpose = Literal["TIIR_SIGNING", "CAPSULE_SIGNING", "RELEASE_SIGNING"]

class Ed25519KeyPurposeProfile(DomainModel):
    identity: TrustedKeyIdentityVsKid
    purpose: KeyPurpose

    def public_key_for(self, purpose: object) -> Ed25519PublicKey:
        """Return the fixed-curve key only for this profile's exact purpose."""
        if type(purpose) is not str or purpose != self.purpose:
            raise SigningKeyPurposeMismatch()
        return Ed25519PublicKey.from_public_bytes(self.identity.raw_public_key)


def validate_disjoint_key_purposes(profiles: tuple[Ed25519KeyPurposeProfile, ...]) -> None:
    """Registry precondition: one full key fingerprint may have only one purpose."""
    purposes: dict[bytes, KeyPurpose] = {}
    for profile in profiles:
        fingerprint = profile.identity.key_fingerprint.raw
        existing = purposes.get(fingerprint)
        if existing is not None and existing != profile.purpose:
            raise SigningKeyPurposeMismatch()
        purposes[fingerprint] = profile.purpose
