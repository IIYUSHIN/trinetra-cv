"""Capability snapshot digest (IMP-0088).

Two digests, both SHA-256 over RFC 8785 canonical JSON (no new canonicalisation):
* `capability_snapshot_digest` binds the whole snapshot (when, which Case and revision, every item);
* `capability_items_digest` binds only the capability facts, so "did anything about what the build can
  do change?" can be answered even though `captured_at` differs.
A schema tag is inside the hashed bytes, so a digest of one kind can never be mistaken for the other or
for any other record's digest.
"""

from __future__ import annotations

from typing import Final

from trinetra.domain.capability_snapshot import CapabilitySnapshot
from trinetra.domain.digests import DigestSha256
from trinetra.jcs import canonicalize

SNAPSHOT_SCHEMA: Final = "trinetra_capability_snapshot_v1"
ITEMS_SCHEMA: Final = "trinetra_capability_items_v1"


def capability_snapshot_digest(snapshot: CapabilitySnapshot) -> DigestSha256:
    return DigestSha256.of(canonicalize({"schema": SNAPSHOT_SCHEMA, "snapshot": snapshot.to_wire()}))


def capability_items_digest(snapshot: CapabilitySnapshot) -> DigestSha256:
    wire = snapshot.to_wire()["items"]
    return DigestSha256.of(canonicalize({"schema": ITEMS_SCHEMA, "items": wire}))


__all__ = ["ITEMS_SCHEMA", "SNAPSHOT_SCHEMA", "capability_items_digest", "capability_snapshot_digest"]
