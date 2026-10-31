"""IMP-0287: purpose-scoped Ed25519 profile; RFC 8032 section 7.1 verification.

Vector independently retrieved from OpenSSL's RFC8032 test collection:
https://github.com/openssl/openssl/blob/master/test/recipes/30-test_evp_data/evppkey_ecx.txt
Only PUBLIC key and signature bytes are committed. Private test keys are ephemeral.
"""
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from trinetra.tiir.errors import SigningKeyPurposeMismatch
from trinetra.tiir.key_identity import TrustedKeyIdentityVsKid
from trinetra.tiir.key_purpose import Ed25519KeyPurposeProfile

PUBLIC = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
SIGNATURE = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
    "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)


def profile(purpose="TIIR_SIGNING"):
    return Ed25519KeyPurposeProfile(identity=TrustedKeyIdentityVsKid(raw_public_key=PUBLIC), purpose=purpose)


def test_rfc8032_vector_and_independent_tampering():
    public = profile().public_key_for("TIIR_SIGNING")
    public.verify(SIGNATURE, b"")
    with pytest.raises(InvalidSignature):
        public.verify(SIGNATURE, b"modified")
    with pytest.raises(InvalidSignature):
        public.verify(bytes([SIGNATURE[0] ^ 1]) + SIGNATURE[1:], b"")


@pytest.mark.parametrize("actual", ["TIIR_SIGNING", "CAPSULE_SIGNING", "RELEASE_SIGNING"])
@pytest.mark.parametrize("requested", ["TIIR_SIGNING", "CAPSULE_SIGNING", "RELEASE_SIGNING"])
def test_disjoint_purposes_cannot_authorize_each_other(actual, requested):
    key = profile(actual)
    if actual == requested:
        assert key.public_key_for(requested).public_bytes_raw() == PUBLIC
    else:
        with pytest.raises(SigningKeyPurposeMismatch):
            key.public_key_for(requested)


def test_generated_key_is_not_replaceable_and_wrong_key_fails():
    temporary = Ed25519PrivateKey.generate()
    with pytest.raises(InvalidSignature):
        profile().public_key_for("TIIR_SIGNING").verify(temporary.sign(b"test"), b"test")
    with pytest.raises(ValidationError):
        profile().purpose = "CAPSULE_SIGNING"


@pytest.mark.parametrize("bad", ["tiir_signing", "UNKNOWN", 1, None])
def test_unknown_purposes_fail_closed(bad):
    with pytest.raises(ValidationError):
        Ed25519KeyPurposeProfile(identity=profile().identity, purpose=bad)
    with pytest.raises(SigningKeyPurposeMismatch):
        profile().public_key_for(bad)


def test_one_key_pair_cannot_be_registered_for_two_purposes():
    from trinetra.tiir.key_purpose import validate_disjoint_key_purposes

    validate_disjoint_key_purposes((profile(), profile()))
    with pytest.raises(SigningKeyPurposeMismatch):
        validate_disjoint_key_purposes((profile(), profile("CAPSULE_SIGNING")))
