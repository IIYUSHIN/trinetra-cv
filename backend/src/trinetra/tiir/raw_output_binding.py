"""IMP-0297: bind the upstream RawAdapterOutputRecord JCS digest, never arbitrary JSON."""
from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256


class RawOutputDigestBinding(DomainModel):
    raw_adapter_output_digest: DigestSha256

    def to_cbor_value(self) -> bytes:
        return self.raw_adapter_output_digest.raw
