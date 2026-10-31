"""IMP-0290: exact original input content binding supplied by authoritative Core."""
from typing import Self

from pydantic import Field

from trinetra.domain.assets import AssetId
from trinetra.domain.base import DomainModel
from trinetra.domain.digests import MAX_SIZE_BYTES, DigestSha256
from trinetra.tiir.errors import TiirSchemaError


class OriginalInputBinding(DomainModel):
    """Asset identity plus exact content identity; no input correctness assertion."""

    asset_id: AssetId
    content_digest: DigestSha256
    byte_size: int = Field(ge=0, le=MAX_SIZE_BYTES)

    @classmethod
    def from_bytes(cls, asset_id: AssetId, data: bytes) -> Self:
        """Trusted caller supplies CAS bytes; filenames/record serialization are excluded."""
        if type(data) is not bytes:
            raise TiirSchemaError("original input must be exact bytes")
        return cls(asset_id=asset_id, content_digest=DigestSha256.of(data), byte_size=len(data))
