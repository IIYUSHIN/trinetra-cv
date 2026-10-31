"""IMP-0046 tests: digest to path mapping is pure, canonical and hostile-input safe."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.cas_layout import CasLayout, CasLayoutError, digest_hex, locator_for

LOCATOR_RE = re.compile(r"^cas/sha256/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$")


def test_known_vector_maps_to_sharded_path():
    d = DigestSha256.of(b"hello")
    h = hashlib.sha256(b"hello").hexdigest()
    assert locator_for(d) == f"cas/sha256/{h[:2]}/{h[2:4]}/{h}"
    assert locator_for(d.to_wire()) == locator_for(d)


@given(st.binary(max_size=64))
def test_locator_matches_the_migration_grammar_for_any_content(data):
    assert LOCATOR_RE.match(locator_for(DigestSha256.of(data)))


@pytest.mark.parametrize("bad", [
    "", "sha256:", "sha256:" + "A" * 64, "sha256:" + "a" * 63, "sha256:" + "a" * 65, "md5:" + "a" * 64,
    "../../etc/passwd", "sha256:" + "a" * 62 + "/..", "sha256:" + "a" * 62 + "\x00a", "a" * 64,
    "sha256:" + "ａ" * 64,
])
def test_non_canonical_digests_are_refused(bad):
    with pytest.raises(CasLayoutError):
        locator_for(bad)
    with pytest.raises(CasLayoutError):
        digest_hex(bad)


def test_layout_creates_private_fixed_directories(state_root):
    layout = CasLayout.for_state_root(state_root)
    layout.ensure()
    for d in (layout.root, layout.objects, layout.quarantine):
        assert d.is_dir()
        assert d.stat().st_mode & 0o777 == 0o700
    layout.ensure()  # idempotent


def test_object_path_stays_under_objects_and_matches_locator(state_root):
    layout = CasLayout.for_state_root(state_root)
    d = DigestSha256.of(b"x")
    assert layout.object_path(d) == state_root / locator_for(d)
    assert layout.objects in layout.object_path(d).parents


def test_shard_directory_is_created_with_private_mode(state_root):
    layout = CasLayout.for_state_root(state_root)
    layout.ensure()
    shard = layout.shard_directory(DigestSha256.of(b"x"))
    assert shard.is_dir()
    assert shard.stat().st_mode & 0o777 == 0o700
    assert shard.parent.stat().st_mode & 0o777 == 0o700


def test_a_symlinked_cas_directory_is_refused(state_root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (state_root / "cas").symlink_to(elsewhere)
    with pytest.raises(CasLayoutError):
        CasLayout.for_state_root(state_root).ensure()


def test_a_group_writable_directory_is_refused(state_root):
    layout = CasLayout.for_state_root(state_root)
    layout.ensure()
    os.chmod(layout.objects, 0o770)  # noqa: S103 - deliberately unsafe fixture
    with pytest.raises(CasLayoutError, match="writable"):
        layout.ensure()


def test_a_file_in_place_of_a_directory_is_refused(state_root):
    (state_root / "cas").write_text("x")
    with pytest.raises(CasLayoutError):
        CasLayout.for_state_root(state_root).ensure()
    assert isinstance(state_root, Path)


def test_a_directory_owned_by_someone_else_is_refused(state_root, monkeypatch):
    layout = CasLayout.for_state_root(state_root)
    layout.ensure()
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 12345)
    with pytest.raises(CasLayoutError, match="owned"):
        layout.check_directory(layout.objects)


def test_the_state_root_itself_must_be_trustworthy(tmp_path):
    from trinetra.infrastructure.sqlite import DatabaseConfigError

    with pytest.raises(DatabaseConfigError):
        CasLayout.for_state_root(tmp_path / "does-not-exist")
    with pytest.raises(DatabaseConfigError):
        CasLayout.for_state_root(Path("relative/state"))
