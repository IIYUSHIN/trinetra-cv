"""IMP-0068 tests: no name, digest or planted link can steer the CAS outside its own tree."""

from __future__ import annotations

import hashlib
import io
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from trinetra.domain.asset_record import ActorId
from trinetra.domain.assets import AssetKind
from trinetra.domain.digests import DigestSha256
from trinetra.errors import TrinetraError
from trinetra.infrastructure.asset_admission_service import AdmissionRequest, AssetAdmissionService
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.cas_layout import CasLayout, CasLayoutError, locator_for
from trinetra.infrastructure.cas_read import read_object
from trinetra.infrastructure.cas_store import CasStore
from trinetra.infrastructure.cas_verify import scan_objects, verify_object

DATA = b"secret-free payload"
DIGEST = DigestSha256.of(DATA)
ACTOR = ActorId("core")


def request(name="note.txt"):
    return AdmissionRequest(
        kind=AssetKind.CONTRIBUTOR_METADATA, media_type="text/plain", supplied_name=name, created_by=ACTOR
    )


def admit(store, service, data=DATA, name="note.txt"):
    with store.unit_of_work() as uow:
        try:
            return service.admit(uow, io.BytesIO(data), request(name))
        finally:
            uow.rollback()


@pytest.fixture
def layout(state_root):
    made = CasLayout.for_state_root(state_root)
    made.ensure()
    return made


@pytest.fixture
def service(layout):
    return AssetAdmissionService(CasStore(layout), AssetRepository())


@pytest.fixture
def outside(tmp_path):
    """A directory the CAS must never touch, with a canary file."""
    directory = tmp_path / "outside"
    directory.mkdir()
    (directory / "canary").write_text("untouched")
    return directory


def untouched(outside):
    names = sorted(p.name for p in outside.iterdir())
    return names == ["canary"] and (outside / "canary").read_text() == "untouched"


def tree(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


# ----------------------------------------------------------------- names never reach the filesystem
ACCEPTED_LABELS = ["note.txt", "..", ".", "~root", "-rf", "CON", "weirdé", "a..b", ".hidden"]
REFUSED_LABELS = [
    "../../etc/passwd", "dir/file", "a\\b", "..\\..\\x", "nul\x00byte", "line\nbreak",
    "x" * 256, " spaced ", "trailing ",
]  # fmt: skip


@pytest.mark.parametrize("name", ACCEPTED_LABELS)
def test_a_supplied_name_is_only_a_label_and_never_a_path(store, service, layout, name):
    result = admit(store, service, name=name)
    assert result.record.supplied_name == name
    created = tree(layout.root.parent) - {"cas", "cas/sha256", "cas/quarantine"}
    fragment = hashlib.sha256(DATA).hexdigest()
    cas_entries = {e for e in created if e.startswith("cas/")}
    assert all(e.endswith(("/" + fragment[:2], "/" + fragment[2:4], "/" + fragment)) for e in cas_entries)
    assert name not in {part for entry in created for part in Path(entry).parts}


@pytest.mark.parametrize("name", REFUSED_LABELS)
def test_names_that_look_like_paths_or_controls_are_refused_before_any_write(store, service, layout, name):
    with pytest.raises((ValidationError, TrinetraError, ValueError)):
        admit(store, service, name=name)
    assert scan_objects(layout).verified == 0 and list(layout.quarantine.iterdir()) == []


# ------------------------------------------------------------------- digests cannot encode a path
@pytest.mark.parametrize(
    "hostile",
    [
        "sha256:../../etc/passwd",
        "sha256:" + "../" * 21 + "ab",
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "sha256:" + "a" * 63 + "/",
        "sha256:" + "a" * 62 + "\x00a",
        "SHA256:" + "a" * 64,
        "md5:" + "a" * 32,
        "",
        "/etc/passwd",
        "a" * 64,
    ],
)
def test_only_a_canonical_digest_can_name_an_object(layout, hostile):
    with pytest.raises(CasLayoutError):
        layout.object_path(hostile)
    with pytest.raises(CasLayoutError):
        locator_for(hostile)
    with pytest.raises(TrinetraError):
        read_object(layout, hostile, max_bytes=100)
    with pytest.raises(TrinetraError):
        verify_object(layout, hostile)


def test_every_valid_digest_maps_strictly_inside_the_objects_tree(layout):
    for n in range(50):
        path = layout.object_path(DigestSha256.of(bytes([n])))
        assert layout.objects in path.parents and ".." not in path.parts
        assert path.relative_to(layout.objects).parts[0] == path.name[:2]


# ------------------------------------------------------------------------------- planted symlinks
def test_a_symlinked_shard_directory_is_not_followed(store, service, layout, outside):
    first = layout.objects / DIGEST.hex[:2]
    first.symlink_to(outside, target_is_directory=True)
    with pytest.raises(TrinetraError):
        admit(store, service)
    assert untouched(outside)


def test_a_symlinked_second_level_shard_is_not_followed(store, service, layout, outside):
    first = layout.objects / DIGEST.hex[:2]
    first.mkdir(mode=0o700)
    (first / DIGEST.hex[2:4]).symlink_to(outside, target_is_directory=True)
    with pytest.raises(TrinetraError):
        admit(store, service)
    assert untouched(outside)


def test_a_symlinked_quarantine_directory_is_refused(state_root, layout, outside):
    os.rmdir(layout.quarantine)
    layout.quarantine.symlink_to(outside, target_is_directory=True)
    with pytest.raises(TrinetraError):
        CasStore(layout)
    assert untouched(outside)


def test_a_symlinked_objects_directory_is_refused(layout, outside):
    os.rmdir(layout.objects)
    layout.objects.symlink_to(outside, target_is_directory=True)
    with pytest.raises(TrinetraError):
        layout.ensure()
    assert untouched(outside)


def test_a_symlink_planted_at_the_object_path_is_never_read_or_trusted(store, service, layout, outside):
    target = outside / "payload"
    target.write_bytes(DATA)  # even with the RIGHT bytes behind it ...
    target.chmod(0o444)  # ... and a shape that would otherwise pass every other check
    layout.shard_directory(DIGEST)
    layout.object_path(DIGEST).symlink_to(target)
    with pytest.raises(TrinetraError):
        admit(store, service)
    with pytest.raises(TrinetraError):
        read_object(layout, DIGEST, max_bytes=1000)
    assert verify_object(layout, DIGEST).status != "VERIFIED"
    assert target.read_bytes() == DATA and sorted(p.name for p in outside.iterdir()) == ["canary", "payload"]


def test_an_object_hard_linked_to_an_outside_file_is_refused(store, service, layout, outside):
    target = outside / "payload"
    target.write_bytes(DATA)
    layout.shard_directory(DIGEST)
    os.link(target, layout.object_path(DIGEST))  # nlink == 2: someone else can still write it
    with pytest.raises(TrinetraError):
        admit(store, service)
    with pytest.raises(TrinetraError):
        read_object(layout, DIGEST, max_bytes=1000)


def test_a_directory_in_place_of_an_object_is_refused(store, service, layout):
    layout.shard_directory(DIGEST)
    layout.object_path(DIGEST).mkdir()
    with pytest.raises(TrinetraError):
        admit(store, service)
    with pytest.raises(TrinetraError):
        read_object(layout, DIGEST, max_bytes=1000)


def test_a_group_writable_cas_directory_is_refused(layout):
    layout.objects.chmod(0o770)
    with pytest.raises(CasLayoutError, match="writable"):
        layout.check_directory(layout.objects)


def test_a_symlinked_state_root_is_refused(tmp_path, outside):
    link = tmp_path / "linked_state"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(TrinetraError):
        CasLayout.for_state_root(link)
    assert untouched(outside)


def test_planted_stage_symlinks_in_quarantine_are_not_swept_through(layout, outside):
    from trinetra.infrastructure.cas_quarantine import Quarantine

    quarantine = Quarantine(layout)
    staged = quarantine.begin()
    name = Path(staged.path).name
    staged.discard()
    (layout.quarantine / name).symlink_to(outside / "canary")
    assert quarantine.sweep(older_than_seconds=0, now=9e12) == []  # a link is not a regular stage file
    assert untouched(outside)


def test_admission_after_every_refusal_still_works_and_stays_inside(store, service, layout):
    for bad in ("../x", "a/b"):
        with pytest.raises((ValidationError, TrinetraError, ValueError)):
            admit(store, service, name=bad)
    result = admit(store, service)
    assert layout.object_path(result.record.blob_digest).is_file()
    assert scan_objects(layout).clean


def test_a_read_only_hard_link_to_an_outside_file_is_still_refused(store, service, layout, outside):
    target = outside / "payload"
    target.write_bytes(DATA)
    target.chmod(0o444)  # otherwise the writable-object check would refuse first and mask the link check
    layout.shard_directory(DIGEST)
    os.link(target, layout.object_path(DIGEST))
    with pytest.raises(TrinetraError, match="hard link"):
        read_object(layout, DIGEST, max_bytes=1000)
    with pytest.raises(TrinetraError):
        admit(store, service)


def test_a_regular_file_in_place_of_a_cas_directory_is_refused(layout):
    os.rmdir(layout.quarantine)
    layout.quarantine.write_text("not a directory")
    layout.quarantine.chmod(0o600)
    with pytest.raises(CasLayoutError, match="real directory"):
        layout.ensure()


def test_a_cas_directory_owned_by_someone_else_is_refused(layout):
    try:
        os.chown(layout.objects, 54321, 54321)
    except PermissionError:
        pytest.skip("changing ownership needs root (NOT RUN here, not PASS)")
    with pytest.raises(CasLayoutError, match="owned"):
        layout.check_directory(layout.objects)


def test_a_writable_object_is_refused_even_with_the_right_bytes(store, service, layout):
    admitted = admit(store, service)  # rolled back: the blob stays, which is all this test needs
    path = layout.object_path(admitted.record.blob_digest)
    path.chmod(0o644)
    with pytest.raises(TrinetraError, match="writable"):
        read_object(layout, DIGEST, max_bytes=1000)


def test_a_device_node_at_an_object_path_is_not_a_regular_file(layout):
    layout.shard_directory(DIGEST)
    try:
        os.mknod(layout.object_path(DIGEST), 0o444 | stat.S_IFCHR, os.makedev(1, 3))  # /dev/null
    except PermissionError:
        pytest.skip("creating a device node needs root (NOT RUN here, not PASS)")
    with pytest.raises(TrinetraError, match="regular file"):
        read_object(layout, DIGEST, max_bytes=1000)
