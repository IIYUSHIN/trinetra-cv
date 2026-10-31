"""IMP-0301: uniqueness nonce is separate from a verifier freshness challenge."""
import pytest
from pydantic import ValidationError

from trinetra.tiir.nonce import ChallengeNonceField


def test_receipt_nonce_and_optional_live_challenge_have_distinct_shapes():
    archive = ChallengeNonceField(receipt_nonce=b"n" * 16)
    assert archive.freshness_challenge is None
    live = ChallengeNonceField(receipt_nonce=b"n" * 16, freshness_challenge=b"c" * 32)
    assert live.freshness_challenge == b"c" * 32
    assert not hasattr(live, "freshness_verified")
    with pytest.raises(ValidationError):
        live.receipt_nonce = b"x" * 16


@pytest.mark.parametrize(("field", "value"), [
    ("receipt_nonce", b""), ("receipt_nonce", b"n" * 15), ("receipt_nonce", b"n" * 17),
    ("receipt_nonce", "n" * 16), ("freshness_challenge", b"n" * 16),
    ("freshness_challenge", b"c" * 31), ("freshness_challenge", b"c" * 33),
])
def test_field_sizes_are_not_interchangeable(field, value):
    data = {"receipt_nonce": b"n" * 16, field: value}
    with pytest.raises(ValidationError):
        ChallengeNonceField(**data)


def test_entropy_is_requested_at_exact_size_and_validated(monkeypatch):
    calls = []
    def entropy(size):
        calls.append(size)
        return b"n" * size
    monkeypatch.setattr("trinetra.tiir.nonce.secrets.token_bytes", entropy)
    assert ChallengeNonceField.generate().receipt_nonce == b"n" * 16
    assert calls == [16]
    monkeypatch.setattr("trinetra.tiir.nonce.secrets.token_bytes", lambda _size: b"bad")
    with pytest.raises(ValidationError):
        ChallengeNonceField.generate()
