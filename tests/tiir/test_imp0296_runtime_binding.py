"""IMP-0296: exact runtime identity bytes and unaltered provider/device observations."""
import pytest
from pydantic import ValidationError

from trinetra.domain.digests import DigestSha256
from trinetra.tiir.deterministic_cbor import encode_cbor
from trinetra.tiir.runtime_binding import RuntimeBinding


def binding(**changes):
    fields = {"runner_image_digest": DigestSha256.of(b"observed OCI bytes"),
              "resource_profile_digest": DigestSha256.of(b"approved resource profile"),
              "runtime_provider": "ONNX Runtime/CPU", "requested_device": "cuda:0"}
    return RuntimeBinding(**(fields | changes))


def test_image_and_profile_have_separate_binary_bindings():
    value = binding()
    assert value.to_cbor_value()[1] == DigestSha256.of(b"observed OCI bytes").raw
    assert value.to_cbor_value()[2] == DigestSha256.of(b"approved resource profile").raw
    changed = binding(runner_image_digest=DigestSha256.of(b"different image"))
    assert encode_cbor(value.to_cbor_value()) != encode_cbor(changed.to_cbor_value())
    assert not hasattr(value, "attested")
    assert value.requested_device == "cuda:0"


@pytest.mark.parametrize("bad", ["latest", "vendor/model:v1", "sha256:bad", None])
def test_container_tag_is_not_a_digest(bad):
    with pytest.raises(ValidationError):
        binding(runner_image_digest=bad)


@pytest.mark.parametrize("bad", ["", "é" * 513, "x" * 1025, "\ud800", b"cpu"])
def test_provider_and_device_representation_limits(bad):
    for field in ("runtime_provider", "requested_device"):
        with pytest.raises(ValidationError):
            binding(**{field: bad})


def test_values_are_preserved_and_no_extra_authority_can_be_injected():
    value = binding(runtime_provider=" Δ/Provider ", requested_device="Vendor Device #1")
    assert value.to_cbor_value()[3] == " Δ/Provider "
    assert value.to_cbor_value()[4] == "Vendor Device #1"
    with pytest.raises(ValidationError):
        binding(hardware_attested=True)
