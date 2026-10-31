"""IMP-0284: immutable TIIR v1 profile; configuration cannot negotiate algorithms."""
from typing import Literal

from pydantic import field_validator

from trinetra.domain.base import DomainModel


class TiirErrorsProfile(DomainModel):
    """Fixed wire constants from the TIIR cryptographic receipt source profile."""

    profile_id: Literal["trinetra_tiir_cose_v1"] = "trinetra_tiir_cose_v1"
    profile_version: Literal[1] = 1
    cose_tag: Literal[18] = 18
    cose_algorithm: Literal[-8] = -8
    curve: Literal["Ed25519"] = "Ed25519"
    external_aad: Literal[b""] = b""
    payload_mode: Literal["embedded"] = "embedded"
    max_receipt_bytes: Literal[65536] = 65536
    max_payload_bytes: Literal[49152] = 49152

    @field_validator("profile_version", "cose_tag", "cose_algorithm",
                     "max_receipt_bytes", "max_payload_bytes", mode="before")
    @classmethod
    def exact_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("profile integer constants require exact integers")
        return value
