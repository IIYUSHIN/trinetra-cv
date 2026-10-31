"""IMP-0302: signed local wall-clock milliseconds, never trusted time or freshness.

The trusted caller supplies this observation. No clock is read and no conversion
or rounding policy is invented here; domain timestamp text formatting is unchanged.
"""
from pydantic import Field

from trinetra.domain.base import DomainModel


class IssuedAtFreshnessFields(DomainModel):
    issued_at_unix_ms: int = Field(ge=0, le=2**64 - 1)

    def to_cbor_value(self) -> int:
        return self.issued_at_unix_ms
