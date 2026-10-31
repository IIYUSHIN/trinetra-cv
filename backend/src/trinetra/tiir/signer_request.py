"""IMP-0305: signer request fields, with no paths, negotiation or payload authority.

IMP-0306 must bound the entire encoded IPC frame to 64 KiB before decoding.
This schema's necessary message bound does not replace that transport bound,
and does not assign an unapproved integer-key IPC wire map.
"""
from typing import Literal

from pydantic import Field

from trinetra.domain.base import DomainModel


class TiirSignerRequestSchema(DomainModel):
    protocol_version: int = Field(ge=1, le=1)
    key_fingerprint: bytes = Field(min_length=32, max_length=32)
    purpose: Literal["TIIR_SIGNING"]
    to_be_signed: bytes = Field(max_length=65536)
