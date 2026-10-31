"""IMP-0303: typed payload slots, complete binary bindings and source separation."""

import pytest
from payload_fixtures import sample_payload
from pydantic import ValidationError

from trinetra.domain.digests import DigestSha256
from trinetra.tiir.deterministic_cbor import encode_cbor
from trinetra.tiir.errors import ReceiptBindingError, TiirSchemaError
from trinetra.tiir.identifiers import ReceiptId, StreamId
from trinetra.tiir.payload import EffectiveInputBinding
from trinetra.tiir.sequence import ReceiptSequenceNumber
from trinetra.tiir.tensor_digest import TensorDescriptor, TensorDigest


def test_all_seventeen_slots_are_explicit_and_id_digest_values_are_binary():
    payload = sample_payload()
    wire = payload.to_cbor_value()
    assert set(wire) == set(range(1, 18))
    assert wire[2] == bytes(16) and wire[3] == b"s" * 16
    assert wire[7] == 1000
    assert wire[10][1] == payload.original_input.asset_id.to_wire()
    assert wire[12][1] == payload.model.model_asset_id.to_wire()
    assert wire[12][2] == payload.model.model_content_digest.raw
    assert wire[15][1] == payload.runtime.runner_image_digest.raw
    assert wire[13] == payload.preprocessing_digest.raw
    assert wire[14] == payload.inference_config_digest.raw
    assert wire[17] == payload.signing_key_fingerprint.raw
    assert wire[11] == {1: "ORIGINAL_BYTES_ONLY", 2: None}
    assert wire[16][4] == payload.output.semantic_output_digest.raw
    assert encode_cbor(wire)
    assert not hasattr(payload, "overall_verified")


def test_identifier_type_confusion_and_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        sample_payload(receipt_id=StreamId(bytes(16)))
    with pytest.raises(ValidationError):
        sample_payload(stream_id=ReceiptId(bytes(16)))
    with pytest.raises(ValidationError):
        sample_payload(overall_verified=True)


def test_genesis_and_non_genesis_have_distinct_previous_digest_requirements():
    with pytest.raises(ReceiptBindingError):
        sample_payload(previous_receipt_digest=DigestSha256.of(b"wrong genesis"))
    with pytest.raises(ReceiptBindingError):
        sample_payload(sequence=ReceiptSequenceNumber(sequence=2))
    value = sample_payload(
        sequence=ReceiptSequenceNumber(sequence=2), previous_receipt_digest=DigestSha256.of(b"previous")
    )
    assert value.to_cbor_value()[6] == DigestSha256.of(b"previous").raw


def test_measured_source_cannot_be_upgraded_or_fabricated_by_metadata():
    tensor = TensorDigest.bind(TensorDescriptor(shape=(1, 3, 1, 1)), bytes(12), source="RUNNER_REPORTED")
    effective = EffectiveInputBinding(observability_level="RUNTIME_REPORTED", tensor_digest=tensor)
    wire = sample_payload(effective_input=effective).to_cbor_value()
    assert wire[11][2][6] == tensor.tensor_bytes_sha256.raw
    assert wire[11][2][7] == tensor.tensor_identity_digest.raw
    for level in ("EXACT_SUPERVISOR_TENSOR", "ORIGINAL_BYTES_ONLY", "CONTRACT_ONLY", "UNAVAILABLE"):
        with pytest.raises(TiirSchemaError):
            EffectiveInputBinding(observability_level=level, tensor_digest=tensor)
    with pytest.raises(TiirSchemaError):
        EffectiveInputBinding(observability_level="EXACT_SUPERVISOR_TENSOR", tensor_digest=None)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"receipt_nonce": b"n" * 15},
        {"freshness_challenge": b"c" * 31},
        {"issued_at_unix_ms": -1},
        {"issued_at_unix_ms": 2**64},
    ],
)
def test_payload_scalar_boundaries(changes):
    with pytest.raises(ValidationError):
        sample_payload(**changes)
