"""IMP-0294: bind an upstream canonical semantic digest as binary receipt data.

The trusted producer supplies the frozen JCS profile/config identity. This module
does not invent an upstream record schema or substitute CBOR for that JCS identity.
"""
from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256


class PreprocessingProfileDigestBinding(DomainModel):
    preprocessing_contract_digest: DigestSha256

    def to_cbor_value(self) -> bytes:
        """CBOR carries the exact 32-byte identity, never its prefixed text spelling."""
        return self.preprocessing_contract_digest.raw
