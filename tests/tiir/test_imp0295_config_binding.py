"""IMP-0295: digest binding preserves upstream JCS identity and binary wire form."""
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.digests import DigestSha256
from trinetra.jcs import canonicalize
from trinetra.tiir.config_binding import InferenceConfigDigestBinding
from trinetra.tiir.deterministic_cbor import encode_cbor


def test_exact_binary_digest_is_not_rehashed_or_encoded_as_text():
    raw = bytes.fromhex("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    binding = InferenceConfigDigestBinding(inference_config_digest=DigestSha256(raw))
    assert binding.to_cbor_value() == raw
    assert encode_cbor(binding.to_cbor_value()) == b"\x58\x20" + raw


@given(st.integers(1, 10000))
def test_changed_upstream_record_identity_changes_bound_bytes(value):
    # Synthetic upstream records exercise binding, not approval/calibration policy.
    first = canonicalize({"version": value})
    second = canonicalize({"version": value + 1})
    binding = InferenceConfigDigestBinding(inference_config_digest=DigestSha256.of(first))
    changed = InferenceConfigDigestBinding(inference_config_digest=DigestSha256.of(second))
    assert binding.to_cbor_value() == hashlib.sha256(first).digest()
    assert binding.to_cbor_value() != changed.to_cbor_value()


@pytest.mark.parametrize("bad", [b"x" * 32, "0" * 64, "sha256:" + "0" * 63, None])
def test_unknown_digest_spellings_are_not_coerced(bad):
    with pytest.raises(ValidationError):
        InferenceConfigDigestBinding(inference_config_digest=bad)


def test_other_binding_fields_cannot_be_accepted_and_binding_is_immutable():
    digest = DigestSha256.of(b"profile")
    with pytest.raises(ValidationError):
        InferenceConfigDigestBinding(other_binding_digest=digest)
    binding = InferenceConfigDigestBinding(inference_config_digest=digest)
    with pytest.raises(ValidationError):
        binding.inference_config_digest = DigestSha256.of(b"changed")
