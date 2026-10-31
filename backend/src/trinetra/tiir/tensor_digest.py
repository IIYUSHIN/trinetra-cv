"""IMP-0291: tensor byte and JCS semantic identities; ERR-CL-023 provisional tokens."""
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import MAX_SIZE_BYTES, DigestSha256
from trinetra.jcs import canonicalize
from trinetra.tiir.errors import TiirSchemaError

Dimension = Annotated[int, Field(ge=1, le=MAX_SIZE_BYTES)]
TensorShape = tuple[Dimension, Dimension, Dimension, Dimension]
TensorSource = Literal["SUPERVISOR_CONSTRUCTED", "RUNNER_REPORTED"]


class TensorDescriptor(DomainModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    dtype: Literal["FLOAT32"] = "FLOAT32"
    shape: TensorShape
    layout: Literal["NCHW"] = "NCHW"
    byte_order: Literal["LITTLE_ENDIAN"] = "LITTLE_ENDIAN"

    @model_validator(mode="after")
    def mvp_shape(self) -> Self:
        if self.shape[:2] != (1, 3):
            raise TiirSchemaError("MVP tensor requires batch one and three channels")
        return self

    @property
    def byte_length(self) -> int:
        return 4 * self.shape[0] * self.shape[1] * self.shape[2] * self.shape[3]

    def identity_value(self, tensor_bytes_sha256: DigestSha256) -> dict[str, object]:
        return {"schema_version": self.schema_version, "dtype": self.dtype,
                "shape": list(self.shape), "layout": self.layout, "byte_order": self.byte_order,
                "tensor_bytes_sha256": tensor_bytes_sha256.to_wire()}


class TensorDigest(TensorDescriptor):
    tensor_bytes_sha256: DigestSha256
    tensor_identity_digest: DigestSha256
    source: TensorSource

    @model_validator(mode="after")
    def consistent_identity(self) -> Self:
        expected = DigestSha256.of(canonicalize(self.identity_value(self.tensor_bytes_sha256)))
        if expected != self.tensor_identity_digest:
            raise TiirSchemaError("tensor semantic identity does not match descriptor and byte digest")
        return self

    @classmethod
    def bind(cls, descriptor: TensorDescriptor, data: bytes, *, source: TensorSource) -> Self:
        if type(descriptor) is not TensorDescriptor or type(data) is not bytes:
            raise TiirSchemaError("tensor binding requires a canonical descriptor and exact bytes")
        if len(data) != descriptor.byte_length:
            raise TiirSchemaError("tensor byte length does not match dtype and shape")
        byte_digest = DigestSha256.of(data)
        identity = DigestSha256.of(canonicalize(descriptor.identity_value(byte_digest)))
        return cls(schema_version=descriptor.schema_version, dtype=descriptor.dtype, shape=descriptor.shape,
                   layout=descriptor.layout, byte_order=descriptor.byte_order,
                   tensor_bytes_sha256=byte_digest,
                   tensor_identity_digest=identity, source=source)
