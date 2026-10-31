"""Fixtures: a freshly migrated Core store in a private state root."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from persistence_support import RawDb
from trinetra.infrastructure.migration_runner import upgrade_to_head
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.unit_of_work import CoreStore


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    return root


@pytest.fixture
def store(state_root: Path) -> Iterator[CoreStore]:
    with CoreStore.open(DatabaseConfig(state_root)) as opened:
        upgrade_to_head(opened)
        yield opened


@pytest.fixture
def raw(store: CoreStore) -> RawDb:
    return RawDb(store)


@pytest.fixture
def rig(store: CoreStore, state_root: Path):
    from commit_support import Rig

    return Rig(store, state_root)
