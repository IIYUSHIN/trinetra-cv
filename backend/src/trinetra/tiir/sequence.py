"""IMP-0299: CBOR uint64 receipt sequence; no stream coordination or replay claim."""
from typing import Self

from pydantic import Field

from trinetra.domain.base import DomainModel
from trinetra.tiir.errors import ReceiptSequenceConflict

MAX_SEQUENCE = 2**64 - 1


class ReceiptSequenceNumber(DomainModel):
    """Sequence starts at one. Stream IDs and transactional allocation are separate."""

    sequence: int = Field(ge=1, le=MAX_SEQUENCE)

    def next(self) -> Self:
        if self.sequence == MAX_SEQUENCE:
            raise ReceiptSequenceConflict("sequence exhausted; a new stream is required")
        return type(self)(sequence=self.sequence + 1)

    def to_cbor_value(self) -> int:
        """Preserve uint64 for CBOR; the I-JSON Counter range is not the TIIR range."""
        return self.sequence
