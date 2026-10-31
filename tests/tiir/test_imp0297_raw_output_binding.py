"""IMP-0297: preserve upstream raw-output identity separately from semantic output."""

import pytest
from pydantic import ValidationError

from trinetra.domain.digests import DigestSha256
from trinetra.tiir.deterministic_cbor import encode_cbor
from trinetra.tiir.raw_output_binding import RawOutputDigestBinding


def test_upstream_digest_is_binary_and_never_rehashed_as_receipt_json():
    raw = bytes(range(32))
    value = RawOutputDigestBinding(raw_adapter_output_digest=DigestSha256(raw))
    assert value.to_cbor_value() == raw
    assert encode_cbor(value.to_cbor_value()) == b"\x58\x20" + raw
    changed = value.evolve(raw_adapter_output_digest=DigestSha256(bytes(reversed(raw))))
    assert changed.to_cbor_value() != value.to_cbor_value()


@pytest.mark.parametrize("bad", ["output.json", b"x" * 32, {"payload": []}, None, "sha256:bad"])
def test_no_filename_or_arbitrary_record_can_stand_in_for_the_upstream_digest(bad):
    with pytest.raises(ValidationError):
        RawOutputDigestBinding(raw_adapter_output_digest=bad)


def test_binding_is_closed_and_immutable():
    value = RawOutputDigestBinding(raw_adapter_output_digest=DigestSha256.of(b"record"))
    with pytest.raises(ValidationError):
        value.raw_adapter_output_digest = DigestSha256.of(b"replacement")
    with pytest.raises(ValidationError):
        RawOutputDigestBinding(
            raw_adapter_output_digest=value.raw_adapter_output_digest, semantic_digest="fake"
        )
