"""IMP-0304: bounded canonical CBOR payload parsing, closed maps and typed failures."""

from typing import Any

from pydantic import ValidationError

from trinetra.domain.assets import AssetId
from trinetra.domain.digests import DigestSha256
from trinetra.errors import TrinetraError
from trinetra.tiir.deterministic_cbor import decode_cbor, encode_cbor
from trinetra.tiir.errors import TiirSchemaError, UnsupportedTiirVersion
from trinetra.tiir.identifiers import ReceiptId, StreamId
from trinetra.tiir.model_binding import ModelBinding
from trinetra.tiir.original_input import OriginalInputBinding
from trinetra.tiir.payload import EffectiveInputBinding, OutputBinding, TiirPayloadSchema
from trinetra.tiir.runtime_binding import RuntimeBinding
from trinetra.tiir.sequence import ReceiptSequenceNumber
from trinetra.tiir.tensor_digest import TensorDigest


def _map(value: object, count: int) -> dict[int, Any]:
    if type(value) is not dict or set(value) != set(range(1, count + 1)):
        raise TiirSchemaError("payload map has missing or unknown fields")
    if any(type(key) is not int for key in value):
        raise TiirSchemaError("payload map keys must be exact integers")
    return value


def _bytes(value: object, size: int) -> bytes:
    if type(value) is not bytes or len(value) != size:
        raise TiirSchemaError("payload binary field has incorrect type or length")
    return value


def _digest(value: object) -> DigestSha256:
    return DigestSha256(_bytes(value, 32))


def _tensor(value: object) -> TensorDigest | None:
    if value is None:
        return None
    fields = _map(value, 8)
    if type(fields[3]) is not list:
        raise TiirSchemaError("tensor shape must be an array")
    return TensorDigest.model_validate(
        {
            "schema_version": fields[1],
            "dtype": fields[2],
            "shape": tuple(fields[3]),
            "layout": fields[4],
            "byte_order": fields[5],
            "tensor_bytes_sha256": _digest(fields[6]),
            "tensor_identity_digest": _digest(fields[7]),
            "source": fields[8],
        }
    )


def encode_payload(payload: TiirPayloadSchema) -> bytes:
    if type(payload) is not TiirPayloadSchema:
        raise TiirSchemaError("payload must be an exact TiirPayloadSchema")
    try:
        checked = TiirPayloadSchema.model_validate(payload)
    except (ValidationError, TrinetraError, ValueError, TypeError) as exc:
        raise TiirSchemaError("invalid TIIR payload schema") from exc
    return encode_cbor(checked.to_cbor_value())


def decode_payload(data: bytes) -> TiirPayloadSchema:
    fields = _map(decode_cbor(data), 17)
    if type(fields[1]) is not int:
        raise TiirSchemaError("payload version must be an exact integer")
    if fields[1] != 1:
        raise UnsupportedTiirVersion()
    try:
        original = _map(fields[10], 3)
        effective = _map(fields[11], 2)
        model = _map(fields[12], 6)
        runtime = _map(fields[15], 4)
        output = _map(fields[16], 6)
        return TiirPayloadSchema.model_validate(
            {
                "schema_version": fields[1],
                "receipt_id": ReceiptId(_bytes(fields[2], 16)),
                "stream_id": StreamId(_bytes(fields[3], 16)),
                "producer_id": fields[4],
                "sequence": ReceiptSequenceNumber(sequence=fields[5]),
                "previous_receipt_digest": None if fields[6] is None else _digest(fields[6]),
                "issued_at_unix_ms": fields[7],
                "receipt_nonce": _bytes(fields[8], 16),
                "freshness_challenge": None if fields[9] is None else _bytes(fields[9], 32),
                "original_input": OriginalInputBinding.model_validate(
                    {
                        "asset_id": AssetId.parse(original[1]),
                        "content_digest": _digest(original[2]),
                        "byte_size": original[3],
                    }
                ),
                "effective_input": EffectiveInputBinding.model_validate(
                    {"observability_level": effective[1], "tensor_digest": _tensor(effective[2])}
                ),
                "model": ModelBinding.model_validate(
                    {
                        "model_asset_id": AssetId.parse(model[1]),
                        "model_content_digest": _digest(model[2]),
                        "model_io_contract_digest": _digest(model[3]),
                        "adapter_id": model[4],
                        "adapter_version": model[5],
                        "execution_assurance_class": model[6],
                    }
                ),
                "preprocessing_digest": _digest(fields[13]),
                "inference_config_digest": _digest(fields[14]),
                "runtime": RuntimeBinding.model_validate(
                    {
                        "runner_image_digest": _digest(runtime[1]),
                        "resource_profile_digest": _digest(runtime[2]),
                        "runtime_provider": runtime[3],
                        "requested_device": runtime[4],
                    }
                ),
                "output": OutputBinding.model_validate(
                    {
                        "raw_output_asset_id": AssetId.parse(output[1]),
                        "raw_adapter_output_digest": _digest(output[2]),
                        "semantic_output_asset_id": AssetId.parse(output[3]),
                        "semantic_output_digest": _digest(output[4]),
                        "semantic_output_schema": output[5],
                        "detection_count": output[6],
                    }
                ),
                "signing_key_fingerprint": _digest(fields[17]),
            }
        )
    except (ValidationError, TrinetraError, ValueError, TypeError) as exc:
        raise TiirSchemaError("invalid TIIR payload schema") from exc
