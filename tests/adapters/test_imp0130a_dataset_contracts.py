"""IMP-0130A tests: adapters are allowlisted, declare capabilities, probe honestly and obey the profile."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.adapters.dataset_contracts import (
    DATASET_ZIP_MVP_V1,
    GIB,
    MAX_PROBE_REASONS,
    AdapterCapabilities,
    AdapterProbe,
    DatasetAdapter,
    DatasetPackageProfile,
)
from trinetra.domain.dataset_manifest import AdapterId
from trinetra.domain.orientation import OrientationPolicy

COCO = AdapterId("coco_detection_v1")
ALL = tuple(sorted(OrientationPolicy, key=lambda p: p.value))


def caps(adapter_id=COCO, policies=ALL, zero=True, version="1.0.0"):
    return AdapterCapabilities(
        adapter_id=adapter_id, adapter_version=version,
        supported_orientation_policies=policies, supports_zero_object_images=zero,
    )  # fmt: skip


def test_the_mvp_profile_has_exactly_the_frozen_bounds():
    p = DATASET_ZIP_MVP_V1
    assert p.profile_id == "dataset_zip_mvp_v1"
    assert p.max_compressed_bytes == 8 * GIB
    assert p.max_entries == 250_000
    assert p.max_extracted_bytes == 32 * GIB
    assert p.max_member_bytes == 1 * GIB
    assert p.max_member_path_chars == 512
    assert p.extract_nested_archives is False
    assert GIB == 1_073_741_824


def test_a_profile_cannot_allow_a_member_larger_than_the_total():
    with pytest.raises(ValidationError):
        DatasetPackageProfile(
            profile_id="x", max_compressed_bytes=1, max_entries=1, max_extracted_bytes=5,
            max_member_bytes=6, max_member_path_chars=1, extract_nested_archives=False,
        )  # fmt: skip


@pytest.mark.parametrize("field", ["max_compressed_bytes", "max_entries", "max_extracted_bytes",
                                   "max_member_bytes", "max_member_path_chars"])  # fmt: skip
@pytest.mark.parametrize("bad", [0, -1, 1.5, True, 2**53])
def test_profile_bounds_are_positive_strict_integers(field, bad):
    values = DATASET_ZIP_MVP_V1.model_dump() | {field: bad}
    with pytest.raises(ValidationError):
        DatasetPackageProfile(**values)


def test_the_profile_is_immutable_and_not_extensible_to_nested_extraction_by_accident():
    with pytest.raises(ValidationError):
        DATASET_ZIP_MVP_V1.extract_nested_archives = True
    with pytest.raises(ValidationError):
        DatasetPackageProfile(**{**DATASET_ZIP_MVP_V1.model_dump(), "extract_nested_archives": "no"})


def test_capabilities_name_an_allowlisted_adapter_and_its_orientation_support():
    c = caps(policies=(OrientationPolicy.APPLY_EXIF, OrientationPolicy.IGNORE_EXIF))
    assert c.supports(OrientationPolicy.APPLY_EXIF)
    assert not c.supports(OrientationPolicy.REQUIRE_IDENTITY)


def test_unknown_adapters_cannot_describe_themselves():
    with pytest.raises(ValidationError, match="allowlist"):
        caps(adapter_id=AdapterId("my_custom_v1"))


@pytest.mark.parametrize(
    "policies",
    [(), (OrientationPolicy.IGNORE_EXIF, OrientationPolicy.IGNORE_EXIF),
     (OrientationPolicy.IGNORE_EXIF, OrientationPolicy.APPLY_EXIF)],  # not sorted by wire form
)  # fmt: skip
def test_policy_lists_must_be_non_empty_unique_and_canonically_sorted(policies):
    with pytest.raises(ValidationError):
        caps(policies=policies)


def test_zero_object_support_is_a_declared_boolean():
    assert caps(zero=False).supports_zero_object_images is False
    with pytest.raises(ValidationError):
        caps(zero="yes")


def test_a_recognizing_probe_needs_no_reason_a_rejecting_one_needs_one():
    assert AdapterProbe(adapter_id=COCO, recognized=True, reasons=()).recognized
    with pytest.raises(ValidationError, match="reason"):
        AdapterProbe(adapter_id=COCO, recognized=False, reasons=())
    assert AdapterProbe(adapter_id=COCO, recognized=False, reasons=("no annotation file",)).reasons


def test_probe_reasons_are_bounded_and_validated_text():
    AdapterProbe(adapter_id=COCO, recognized=False, reasons=("r",) * MAX_PROBE_REASONS)
    with pytest.raises(ValidationError):
        AdapterProbe(adapter_id=COCO, recognized=False, reasons=("r",) * (MAX_PROBE_REASONS + 1))
    with pytest.raises(ValidationError):
        AdapterProbe(adapter_id=COCO, recognized=False, reasons=("",))
    with pytest.raises(ValidationError):
        AdapterProbe(adapter_id=COCO, recognized=False, reasons=("bad\x00text",))


def test_a_probe_for_a_non_allowlisted_adapter_is_refused():
    with pytest.raises(ValidationError, match="allowlist"):
        AdapterProbe(adapter_id=AdapterId("voc_v1"), recognized=True, reasons=())


class _GoodAdapter:
    capabilities = caps()

    def probe(self, package):
        return AdapterProbe(adapter_id=COCO, recognized=True, reasons=())

    def parse(self, package, profile, contributor_manifest):
        return ("parsed", package, profile.profile_id, contributor_manifest)


class _NoParse:
    capabilities = caps()

    def probe(self, package):
        return AdapterProbe(adapter_id=COCO, recognized=True, reasons=())


def test_the_protocol_accepts_a_complete_adapter_and_rejects_an_incomplete_one():
    assert isinstance(_GoodAdapter(), DatasetAdapter)
    assert not isinstance(_NoParse(), DatasetAdapter)
    assert not isinstance(object(), DatasetAdapter)
    result = _GoodAdapter().parse("pkg", DATASET_ZIP_MVP_V1, None)
    assert result == ("parsed", "pkg", "dataset_zip_mvp_v1", None)


def test_a_profile_may_allow_a_member_as_large_as_the_whole_extraction_bound():
    p = DatasetPackageProfile(
        profile_id="x", max_compressed_bytes=1, max_entries=1, max_extracted_bytes=5,
        max_member_bytes=5, max_member_path_chars=1, extract_nested_archives=False,
    )  # fmt: skip
    assert p.max_member_bytes == p.max_extracted_bytes


def test_the_probe_reason_bound_is_sixteen():
    assert MAX_PROBE_REASONS == 16
