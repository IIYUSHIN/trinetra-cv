"""IMP-0293: preserve observed model identity separately from approval and I/O identity."""
import pytest
from pydantic import ValidationError

from trinetra.domain.assets import AssetId
from trinetra.domain.digests import DigestSha256
from trinetra.tiir.deterministic_cbor import encode_cbor
from trinetra.tiir.model_binding import ModelBinding


def binding(**changes):
    fields = {"model_asset_id": AssetId.from_entropy(lambda n: b"m" * n),
              "model_content_digest": DigestSha256.of(b"candidate model bytes"),
              "model_io_contract_digest": DigestSha256.of(b"different contract bytes"),
              "adapter_id": "vendor/ONNX adapter", "adapter_version": "1.2.3+CPU",
              "execution_assurance_class": "DATAFLOW_GRAPH"}
    return ModelBinding(**(fields | changes))


def test_content_and_contract_digest_are_bound_in_different_binary_slots():
    value = binding()
    wire = value.to_cbor_value()
    assert wire[2] == DigestSha256.of(b"candidate model bytes").raw
    assert wire[3] == DigestSha256.of(b"different contract bytes").raw
    assert wire[2] != wire[3]
    changed = binding(model_content_digest=DigestSha256.of(b"substituted model"))
    assert encode_cbor(changed.to_cbor_value()) != encode_cbor(wire)
    assert not hasattr(value, "approved") and not hasattr(value, "signature_valid")


@pytest.mark.parametrize("bad", ["model.onnx", "v1", b"x" * 32, None])
def test_filename_or_version_is_not_a_content_identity(bad):
    with pytest.raises(ValidationError):
        binding(model_content_digest=bad)


def test_assurance_class_is_closed_not_an_inferred_hardware_claim():
    with pytest.raises(ValidationError):
        binding(execution_assurance_class="HARDWARE_ATTESTED")
    with pytest.raises(ValidationError):
        binding().adapter_id = "new"


@pytest.mark.parametrize("bad", ["", "x" * 1025, "é" * 513, "\ud800", 1])
def test_binding_text_is_bounded_in_utf8_bytes_without_token_coercion(bad):
    with pytest.raises(ValidationError):
        binding(adapter_id=bad)


def test_punctuation_spaces_unicode_and_exact_case_survive():
    value = binding(adapter_id=" Vendor/Δ ", adapter_version="v1:CPU+build")
    assert value.adapter_id == " Vendor/Δ "
    assert value.to_cbor_value()[5] == "v1:CPU+build"
