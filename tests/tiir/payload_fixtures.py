"""Synthetic payload assembled from actual binding types; no production assurance claims."""

from trinetra.domain.assets import AssetId
from trinetra.domain.detection import DetectionSet
from trinetra.domain.digests import DigestSha256
from trinetra.tiir.identifiers import ReceiptId, StreamId
from trinetra.tiir.model_binding import ModelBinding
from trinetra.tiir.original_input import OriginalInputBinding
from trinetra.tiir.payload import EffectiveInputBinding, OutputBinding, TiirPayloadSchema
from trinetra.tiir.runtime_binding import RuntimeBinding
from trinetra.tiir.semantic_output import semantic_output_digest
from trinetra.tiir.sequence import ReceiptSequenceNumber


def sample_payload(**changes):
    asset = AssetId.from_entropy(lambda n: b"a" * n)
    digest = DigestSha256.of(b"synthetic upstream identity")
    detections = DetectionSet(image_member_digest=digest, image_width=1, image_height=1, detections=())
    values = {
        "receipt_id": ReceiptId(bytes(16)),
        "stream_id": StreamId(b"s" * 16),
        "producer_id": "trusted_core",
        "sequence": ReceiptSequenceNumber(sequence=1),
        "previous_receipt_digest": None,
        "issued_at_unix_ms": 1000,
        "receipt_nonce": b"n" * 16,
        "freshness_challenge": None,
        "original_input": OriginalInputBinding.from_bytes(asset, b"input"),
        "effective_input": EffectiveInputBinding(
            observability_level="ORIGINAL_BYTES_ONLY", tensor_digest=None
        ),
        "model": ModelBinding(
            model_asset_id=AssetId.from_entropy(lambda n: b"m" * n),
            model_content_digest=DigestSha256.of(b"model content"),
            model_io_contract_digest=DigestSha256.of(b"model IO contract"),
            adapter_id="onnx",
            adapter_version="1.0",
            execution_assurance_class="DATAFLOW_GRAPH",
        ),
        "preprocessing_digest": DigestSha256.of(b"preprocessing"),
        "inference_config_digest": DigestSha256.of(b"inference config"),
        "runtime": RuntimeBinding(
            runner_image_digest=DigestSha256.of(b"OCI image"),
            resource_profile_digest=DigestSha256.of(b"resource profile"),
            runtime_provider="ONNX Runtime",
            requested_device="cpu",
        ),
        "output": OutputBinding(
            raw_output_asset_id=AssetId.from_entropy(lambda n: b"r" * n),
            raw_adapter_output_digest=DigestSha256.of(b"raw output record"),
            semantic_output_asset_id=AssetId.from_entropy(lambda n: b"s" * n),
            semantic_output_digest=semantic_output_digest(detections),
            detection_count=0,
        ),
        "signing_key_fingerprint": DigestSha256.of(b"synthetic public key fingerprint"),
    }
    return TiirPayloadSchema(**(values | changes))
