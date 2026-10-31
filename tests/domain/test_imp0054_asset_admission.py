"""IMP-0054 tests: admission profile is total, pure, and refuses contradictory claims."""

from __future__ import annotations

import pytest

from trinetra.domain.asset_admission import (
    DEFAULT_PROFILE,
    AdmissionProfile,
    AdmissionProfileError,
    KindRule,
    sniff_media_type,
)
from trinetra.domain.assets import AssetKind

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
ZIP = b"PK\x03\x04" + b"\x00" * 12


@pytest.mark.parametrize(("head", "expected"), [
    (PNG, "image/png"), (JPEG, "image/jpeg"), (b"GIF89a" + b"\x00" * 10, "image/gif"),
    (b"GIF87a", "image/gif"), (b"BM" + b"\x00" * 4 + b"\x00\x00\x00\x00" + b"\x00" * 6, "image/bmp"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"), (b"II*\x00" + b"\x00" * 12, "image/tiff"),
    (b"MM\x00*" + b"\x00" * 12, "image/tiff"), (b"%PDF-1.7", "application/pdf"), (ZIP, "application/zip"),
    (b"PK\x05\x06" + b"\x00" * 12, "application/zip"), (b"\x1f\x8b\x08", "application/gzip"),
    (b"", None), (b"plain text", None), (b"BM", None), (b"RIFF\x00\x00\x00\x00WAVE", None),
])
def test_sniffing_known_signatures(head, expected):
    assert sniff_media_type(head) == expected


def test_the_default_profile_covers_every_kind_exactly_once():
    assert {k for k, _ in DEFAULT_PROFILE.rules} == set(AssetKind)
    assert len(DEFAULT_PROFILE.rules) == len(list(AssetKind))


def test_an_incomplete_or_duplicated_profile_is_refused():
    rule = KindRule(frozenset({"text/plain"}), 10)
    with pytest.raises(AdmissionProfileError, match="every AssetKind"):
        AdmissionProfile(((AssetKind.MODEL, rule),))
    full = tuple((k, rule) for k in AssetKind)
    with pytest.raises(AdmissionProfileError, match="every AssetKind"):
        AdmissionProfile((*full, (AssetKind.MODEL, rule)))


def test_a_kind_rule_must_be_non_empty_and_non_negative():
    with pytest.raises(AdmissionProfileError):
        KindRule(frozenset(), 10)
    with pytest.raises(AdmissionProfileError):
        KindRule(frozenset({"text/plain"}), -1)
    assert KindRule(frozenset({"text/plain"}), 0).max_bytes == 0


def test_a_png_dataset_image_is_admitted():
    DEFAULT_PROFILE.check(AssetKind.DATASET, "image/png", len(PNG), PNG)


def test_an_unknown_but_honest_octet_stream_is_admitted_where_allowed():
    DEFAULT_PROFILE.check(AssetKind.MODEL, "application/octet-stream", 100, b"\x80\x02}q\x00")


def test_a_disallowed_media_type_is_refused():
    with pytest.raises(AdmissionProfileError, match="not allowed"):
        DEFAULT_PROFILE.check(AssetKind.INFERENCE_RECEIPT, "image/png", 10, b"")
    with pytest.raises(AdmissionProfileError, match="not allowed"):
        DEFAULT_PROFILE.check(AssetKind.MODEL, "text/html", 10, b"")


def test_a_claim_that_contradicts_the_bytes_is_refused():
    with pytest.raises(AdmissionProfileError, match="contradicts"):
        DEFAULT_PROFILE.check(AssetKind.DATASET, "image/jpeg", len(PNG), PNG)


def test_octet_stream_cannot_smuggle_a_disallowed_format():
    with pytest.raises(AdmissionProfileError, match="which is not allowed"):
        DEFAULT_PROFILE.check(AssetKind.INFERENCE_RECEIPT, "application/octet-stream", len(PNG), PNG)
    with pytest.raises(AdmissionProfileError, match="which is not allowed"):
        DEFAULT_PROFILE.check(AssetKind.MODEL, "application/octet-stream", len(PNG), PNG)


def test_octet_stream_may_carry_an_allowed_recognised_format():
    DEFAULT_PROFILE.check(AssetKind.MODEL, "application/octet-stream", len(ZIP), ZIP)
    DEFAULT_PROFILE.check(AssetKind.DATASET, "application/octet-stream", len(PNG), PNG)


def test_size_ceiling_is_inclusive_and_zero_is_allowed():
    ceiling = DEFAULT_PROFILE.rule_for(AssetKind.INFERENCE_RECORD).max_bytes
    DEFAULT_PROFILE.check(AssetKind.INFERENCE_RECORD, "application/json", ceiling, b"{")
    DEFAULT_PROFILE.check(AssetKind.INFERENCE_RECORD, "application/json", 0, b"")
    with pytest.raises(AdmissionProfileError, match="ceiling"):
        DEFAULT_PROFILE.check(AssetKind.INFERENCE_RECORD, "application/json", ceiling + 1, b"{")
    with pytest.raises(AdmissionProfileError, match="ceiling"):
        DEFAULT_PROFILE.check(AssetKind.INFERENCE_RECORD, "application/json", -1, b"{")


def test_only_the_leading_bytes_are_inspected():
    DEFAULT_PROFILE.check(AssetKind.DATASET, "image/png", 100, PNG + b"\xff\xd8\xff" * 1000)


def test_json_content_without_a_signature_is_not_contradicted():
    DEFAULT_PROFILE.check(AssetKind.INFERENCE_RECORD, "application/json", 2, b"{}")


def test_error_carries_machine_readable_details_not_content():
    with pytest.raises(AdmissionProfileError) as info:
        DEFAULT_PROFILE.check(AssetKind.DATASET, "image/jpeg", len(PNG), PNG)
    assert info.value.details == {"claimed": "image/jpeg", "detected": "image/png"}


def test_a_custom_profile_overrides_the_defaults():
    tight = AdmissionProfile(tuple((k, KindRule(frozenset({"text/plain"}), 5)) for k in AssetKind))
    tight.check(AssetKind.MODEL, "text/plain", 5, b"hello")
    with pytest.raises(AdmissionProfileError):
        tight.check(AssetKind.MODEL, "text/plain", 6, b"hello!")
