"""IMP-0300: exact previous receipt digest with the genesis relationship preserved."""
from typing import Self

from pydantic import model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.tiir.cose_profile import decode_sign1
from trinetra.tiir.errors import ReceiptBindingError
from trinetra.tiir.sequence import ReceiptSequenceNumber


class PreviousReceiptDigest(DomainModel):
    sequence: ReceiptSequenceNumber
    previous_receipt_digest: DigestSha256 | None

    @model_validator(mode="after")
    def genesis_relationship(self) -> Self:
        if (self.sequence.sequence == 1) != (self.previous_receipt_digest is None):
            raise ReceiptBindingError("only sequence one has no previous receipt digest")
        return self

    @classmethod
    def from_previous_receipt(
        cls, sequence: ReceiptSequenceNumber, previous_receipt: bytes | None = None,
    ) -> Self:
        digest = None
        if previous_receipt is not None:
            decode_sign1(previous_receipt)
            digest = DigestSha256.of(previous_receipt)
        return cls(sequence=sequence, previous_receipt_digest=digest)

    def to_cbor_value(self) -> bytes | None:
        """This does not establish that the predecessor signature or stream is trusted."""
        return None if self.previous_receipt_digest is None else self.previous_receipt_digest.raw
