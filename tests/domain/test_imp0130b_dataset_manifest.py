"""IMP-0130B tests: the manifest is a deterministic, self-verifying semantic identity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.assets import AssetId
from trinetra.domain.dataset_manifest import (
    ADAPTER_ALLOWLIST,
    MAX_MANIFEST_ENTRIES,
    AdapterId,
    DatasetManifest,
    ManifestSample,
)
from trinetra.domain.digests import DigestSha256
from trinetra.domain.ontology import ClassOntology, OntologyClass, OntologyId
from trinetra.domain.orientation import OrientationPolicy

ASSET = AssetId.from_entropy(lambda n: b"\x01" * n)
PACKAGE = DigestSha256.of(b"package")
ONTOLOGY = ClassOntology(
    ontology_id=OntologyId("coco_2017"),
    classes=(OntologyClass(canonical_index=0, source_id="1", name="person"),
             OntologyClass(canonical_index=1, source_id="2", name="bicycle")),
)  # fmt: skip


def sample(path="images/a.jpg", w=640, h=480, data=b"a"):
    return ManifestSample(member_path=path, image_member_digest=DigestSha256.of(data),
                          effective_width=w, effective_height=h)  # fmt: skip


def fields(**over):
    base = {
        "schema_version": 1,
        "dataset_package_asset_id": ASSET,
        "dataset_package_digest": PACKAGE,
        "adapter_id": AdapterId("coco_detection_v1"),
        "adapter_version": "1.0.0",
        "orientation_policy": OrientationPolicy.IGNORE_EXIF,
        "ontology": ONTOLOGY,
        "samples": (sample("images/a.jpg"), sample("images/b.jpg", data=b"b")),
        "unused_members": ("notes/readme.txt",),
        "provenance_manifest_digest": None,
    }
    return base | over


def seal(**over):
    return DatasetManifest.seal(**fields(**over))


def test_a_sealed_manifest_carries_the_digest_of_its_own_content():
    m = seal()
    wire = m.to_wire()
    assert m.manifest_digest == DatasetManifest.compute_digest(wire)
    assert DigestSha256.parse(m.manifest_digest.to_wire()) == m.manifest_digest


def test_the_digest_is_sha256_of_jcs_of_the_manifest_without_the_digest():
    import hashlib

    from trinetra.jcs import canonicalize

    m = seal()
    body = {k: v for k, v in m.to_wire().items() if k != "manifest_digest"}
    assert m.manifest_digest.raw == hashlib.sha256(canonicalize(body)).digest()
    assert "manifest_digest" not in body


def test_the_same_semantic_content_always_gives_the_same_digest():
    assert seal().manifest_digest == seal().manifest_digest
    assert seal().canonical_bytes() == seal().canonical_bytes()


def test_wire_round_trip_through_json_reproduces_the_manifest():
    m = seal()
    again = DatasetManifest.model_validate_json(m.canonical_bytes())
    assert again == m
    assert again.manifest_digest == m.manifest_digest


@pytest.mark.parametrize(
    "change",
    [
        {"dataset_package_digest": DigestSha256.of(b"other")},
        {"dataset_package_asset_id": AssetId.from_entropy(lambda n: b"\x02" * n)},
        {"adapter_id": AdapterId("yolo_detection_v1")},
        {"adapter_version": "1.0.1"},
        {"orientation_policy": OrientationPolicy.APPLY_EXIF},
        {"samples": (sample("images/a.jpg"),)},
        {"samples": (sample("images/a.jpg", w=641), sample("images/b.jpg", data=b"b"))},
        {"samples": (sample("images/a.jpg", data=b"changed"), sample("images/b.jpg", data=b"b"))},
        {"unused_members": ()},
        {"provenance_manifest_digest": DigestSha256.of(b"prov")},
    ],
)
def test_changing_any_semantic_field_changes_the_manifest_digest(change):
    assert seal(**change).manifest_digest != seal().manifest_digest


def test_changing_the_ontology_changes_the_digest():
    renamed = OntologyClass(canonical_index=0, source_id="1", name="human")
    other = ClassOntology(ontology_id=OntologyId("coco_2017"), classes=(renamed,))
    assert seal(ontology=other).manifest_digest != seal().manifest_digest


def test_a_manifest_whose_digest_does_not_match_its_content_is_refused():
    m = seal()
    with pytest.raises(ValidationError, match="does not match"):
        DatasetManifest(**{**fields(), "manifest_digest": DigestSha256.of(b"forged")})
    with pytest.raises(ValidationError, match="does not match"):
        m.evolve(adapter_version="9.9")  # content changed, digest not recomputed


def test_seal_refuses_a_caller_supplied_digest():
    with pytest.raises(ValueError, match="computes manifest_digest"):
        DatasetManifest.seal(**fields(), manifest_digest=PACKAGE)


def test_the_unsealed_context_cannot_smuggle_in_an_arbitrary_digest():
    with pytest.raises(ValidationError, match="placeholder"):
        DatasetManifest.model_validate({**fields(), "manifest_digest": PACKAGE}, context="unsealed")


def test_the_adapter_must_be_on_the_allowlist():
    assert [a.text for a in ADAPTER_ALLOWLIST] == ["coco_detection_v1", "yolo_detection_v1"]
    with pytest.raises(ValidationError, match="allowlist"):
        seal(adapter_id=AdapterId("pascal_voc_v1"))


@pytest.mark.parametrize("version", ["", "-1", ".1", "a b", "v" * 33, "1.0/2", 1])
def test_adapter_versions_are_bounded_visible_text(version):
    with pytest.raises(ValidationError):
        seal(adapter_version=version)


def test_schema_version_is_exactly_one():
    for bad in (0, 2, True, 1.0, "1"):
        with pytest.raises(ValidationError):
            seal(schema_version=bad)


def test_samples_must_be_unique_and_ascending_by_member_path():
    with pytest.raises(ValidationError):
        seal(samples=(sample("images/b.jpg"), sample("images/a.jpg")))
    with pytest.raises(ValidationError):
        seal(samples=(sample("images/a.jpg"), sample("images/a.jpg", data=b"x")))


def test_unused_members_are_unique_ascending_and_disjoint_from_samples():
    with pytest.raises(ValidationError):
        seal(unused_members=("b", "a"))
    with pytest.raises(ValidationError):
        seal(unused_members=("a", "a"))
    with pytest.raises(ValidationError, match="both a sample and unused"):
        seal(unused_members=("images/a.jpg",))


@pytest.mark.parametrize(
    "path",
    ["", "/abs/x.jpg", "a//b", "a/./b", "a/../b", "../x", "a/", "./a", "a\\b", "a\x00b", "a\nb", "a b ",
     "x" * 513, "é.jpg", "a/b\u200b"],
)  # fmt: skip
def test_unsafe_or_non_normalized_member_paths_are_rejected(path):
    with pytest.raises(ValidationError):
        seal(unused_members=(path,))
    with pytest.raises(ValidationError):
        seal(samples=(sample(path),))


GOOD_PATHS = ["a", "images/a.jpg", "dir with space/file name.png", "犬/猫.jpg", "x" * 512]


@pytest.mark.parametrize("path", [" a", "a /b", "a/ b", "a "])
def test_segments_may_not_start_or_end_with_a_space(path):
    with pytest.raises(ValidationError):
        seal(samples=(), unused_members=(path,))


@pytest.mark.parametrize("path", GOOD_PATHS)
def test_ordinary_member_paths_are_valid(path):
    assert seal(samples=(), unused_members=(path,)).unused_members == (path,)


def test_empty_samples_are_a_valid_manifest():
    assert seal(samples=(), unused_members=()).samples == ()


def test_the_entry_bound_is_the_zip_profile_bound():
    assert MAX_MANIFEST_ENTRIES == 250_000


def test_no_local_paths_or_timestamps_exist_to_leak_into_the_identity():
    names = set(DatasetManifest.model_fields)
    assert names.isdisjoint({"path", "extraction_path", "mtime", "inode", "created_at", "temp_dir"})


def test_the_manifest_is_immutable_and_closed():
    m = seal()
    with pytest.raises(ValidationError):
        m.samples = ()
    with pytest.raises(ValidationError):
        DatasetManifest(**fields(), manifest_digest=m.manifest_digest, extra="x")


def test_the_entry_bound_counts_samples_and_unused_members_together():
    one_sample = (sample("images/a.jpg"),)
    fits = tuple(f"u/{i:06d}" for i in range(MAX_MANIFEST_ENTRIES - 1))
    assert len(seal(samples=one_sample, unused_members=fits).unused_members) == MAX_MANIFEST_ENTRIES - 1
    with pytest.raises(ValidationError, match="too many"):
        seal(samples=one_sample, unused_members=(*fits, "u/zzzzzz"))
