"""IMP-0284: fixed profile and safe, typed TIIR failures."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.errors import TrinetraError, registered_error_codes
from trinetra.tiir import errors
from trinetra.tiir.profile import TiirErrorsProfile


def test_profile_fixes_the_wire_protocol():
    profile = TiirErrorsProfile()
    assert profile.profile_id == "trinetra_tiir_cose_v1"
    assert profile.profile_version == 1
    assert profile.cose_tag == 18
    assert profile.cose_algorithm == -8
    assert profile.curve == "Ed25519"
    assert profile.external_aad == b""
    assert profile.payload_mode == "embedded"
    assert profile.max_receipt_bytes == 65536
    assert profile.max_payload_bytes == 49152


@pytest.mark.parametrize(("field", "value"), [
    ("profile_version", True), ("profile_version", 1.0), ("profile_id", "unknown"),
    ("profile_version", 2), ("cose_tag", 0),
    ("cose_algorithm", -7), ("curve", "Ed448"), ("external_aad", b"aad"),
    ("payload_mode", "detached"), ("max_receipt_bytes", 65537),
    ("max_payload_bytes", 49153), ("unknown", True),
])
def test_profile_rejects_negotiation_and_unknown_fields(field, value):
    with pytest.raises(ValidationError):
        TiirErrorsProfile(**{field: value})


def test_profile_is_immutable():
    with pytest.raises(ValidationError):
        TiirErrorsProfile().cose_tag = 0


def test_typed_errors_are_registered_bounded_and_do_not_expose_crypto_text():
    classes = [value for value in vars(errors).values()
               if isinstance(value, type) and value.__module__ == errors.__name__
               and issubclass(value, TrinetraError)]
    assert len(classes) == 20
    for cls in classes:
        error = cls()
        assert isinstance(error, errors.TiirError)
        assert cls.__dict__["code"] in registered_error_codes()
        assert error.retryable is False
        assert error.message and "openssl" not in error.message.lower()
        with pytest.raises(ValueError, match="longer"):
            cls("x" * 1001)
