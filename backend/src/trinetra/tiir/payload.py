"""IMP-0303: closed TIIR v1 payload, explicit binary mapping under ERR-CL-023.

Semantic identities are supplied JCS digests. The receipt carries their raw bytes;
it never embeds tensors, detections, configuration floats or an overall trust score.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from trinetra.domain.assets import AssetId
from trinetra.domain.base import DomainModel
from trinetra.domain.detection import MAX_DETECTIONS
from trinetra.domain.digests import DigestSha256
from trinetra.domain.tokens import CanonicalToken
from trinetra.tiir.errors import TiirSchemaError
from trinetra.tiir.identifiers import ReceiptId, StreamId
from trinetra.tiir.model_binding import ModelBinding
from trinetra.tiir.nonce import ChallengeNonceField
from trinetra.tiir.original_input import OriginalInputBinding
from trinetra.tiir.previous import PreviousReceiptDigest
from trinetra.tiir.runtime_binding import RuntimeBinding
from trinetra.tiir.semantic_output import SEMANTIC_OUTPUT_SCHEMA
from trinetra.tiir.sequence import ReceiptSequenceNumber
from trinetra.tiir.tensor_digest import TensorDigest

Observability = Literal[
    "EXACT_SUPERVISOR_TENSOR", "RUNTIME_REPORTED", "ORIGINAL_BYTES_ONLY", "CONTRACT_ONLY", "UNAVAILABLE"
]


class EffectiveInputBinding(DomainModel):
    observability_level: Observability
    tensor_digest: TensorDigest | None

    @model_validator(mode="after")
    def measured_source(self) -> Self:
        required = {
            "EXACT_SUPERVISOR_TENSOR": "SUPERVISOR_CONSTRUCTED",
            "RUNTIME_REPORTED": "RUNNER_REPORTED",
        }
        source = required.get(self.observability_level)
        if source is None:
            if self.tensor_digest is not None:
                raise TiirSchemaError("unobserved effective input cannot carry a fabricated tensor digest")
        elif self.tensor_digest is None or self.tensor_digest.source != source:
            raise TiirSchemaError("tensor source does not match effective-input observability")
        return self

    def to_cbor_value(self) -> dict[int, object]:
        tensor = self.tensor_digest
        value = (
            None
            if tensor is None
            else {
                1: tensor.schema_version,
                2: tensor.dtype,
                3: list(tensor.shape),
                4: tensor.layout,
                5: tensor.byte_order,
                6: tensor.tensor_bytes_sha256.raw,
                7: tensor.tensor_identity_digest.raw,
                8: tensor.source,
            }
        )
        return {1: self.observability_level, 2: value}


class OutputBinding(DomainModel):
    raw_output_asset_id: AssetId
    raw_adapter_output_digest: DigestSha256
    semantic_output_asset_id: AssetId
    semantic_output_digest: DigestSha256
    semantic_output_schema: Literal["trinetra_detection_set_v1"] = SEMANTIC_OUTPUT_SCHEMA
    detection_count: int = Field(ge=0, le=MAX_DETECTIONS)

    def to_cbor_value(self) -> dict[int, object]:
        return {
            1: self.raw_output_asset_id.to_wire(),
            2: self.raw_adapter_output_digest.raw,
            3: self.semantic_output_asset_id.to_wire(),
            4: self.semantic_output_digest.raw,
            5: self.semantic_output_schema,
            6: self.detection_count,
        }


class TiirPayloadSchema(ChallengeNonceField):
    schema_version: int = Field(default=1, ge=1, le=1)
    receipt_id: ReceiptId
    stream_id: StreamId
    producer_id: CanonicalToken
    sequence: ReceiptSequenceNumber
    previous_receipt_digest: DigestSha256 | None
    issued_at_unix_ms: int = Field(ge=0, le=2**64 - 1)
    original_input: OriginalInputBinding
    effective_input: EffectiveInputBinding
    model: ModelBinding
    preprocessing_digest: DigestSha256
    inference_config_digest: DigestSha256
    runtime: RuntimeBinding
    output: OutputBinding
    signing_key_fingerprint: DigestSha256

    @model_validator(mode="after")
    def continuity_shape(self) -> Self:
        PreviousReceiptDigest(sequence=self.sequence, previous_receipt_digest=self.previous_receipt_digest)
        return self

    def to_cbor_value(self) -> dict[int, object]:
        """Only explicit protocol values enter CBOR; JSON serializers are not used."""
        return {
            1: self.schema_version,
            2: self.receipt_id.raw,
            3: self.stream_id.raw,
            4: self.producer_id.to_wire(),
            5: self.sequence.to_cbor_value(),
            6: None if self.previous_receipt_digest is None else self.previous_receipt_digest.raw,
            7: self.issued_at_unix_ms,
            8: self.receipt_nonce,
            9: self.freshness_challenge,
            10: {
                1: self.original_input.asset_id.to_wire(),
                2: self.original_input.content_digest.raw,
                3: self.original_input.byte_size,
            },
            11: self.effective_input.to_cbor_value(),
            12: self.model.to_cbor_value(),
            13: self.preprocessing_digest.raw,
            14: self.inference_config_digest.raw,
            15: self.runtime.to_cbor_value(),
            16: self.output.to_cbor_value(),
            17: self.signing_key_fingerprint.raw,
        }
