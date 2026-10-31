"""Audit event hash/link projection (IMP-0061).

Pure functions that turn an AuditEvent into the three digests the audit tables store:

  body_digest = SHA-256(canonical JSON body)                                  (what the event says)
  link_digest = SHA-256(b"trinetra.audit.link.v1\\0" || prev_link || body_digest)   (where it sits)

The first event of a journal chains to a genesis digest derived from the journal id with its own
domain tag, so two journals never share a starting point and a genesis can never be mistaken for a
link. Digest inputs are raw 32-byte values, never hex text, so there is no encoding ambiguity. The
chain proves order and tamper-evidence within one journal; it is not a signature and not
inter-process attestation (no key is involved), and it says nothing about the truth of any event.

PROPOSED engineering decisions (ERR-CL-018): both domain tags, raw-byte concatenation, and the
genesis derivation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from trinetra.domain.audit import AuditEvent, JournalId
from trinetra.domain.digests import DigestSha256

LINK_DOMAIN: Final = b"trinetra.audit.link.v1\x00"
GENESIS_DOMAIN: Final = b"trinetra.audit.genesis.v1\x00"


def genesis_link(journal_id: JournalId) -> DigestSha256:
    return DigestSha256(hashlib.sha256(GENESIS_DOMAIN + journal_id.text.encode("ascii")).digest())


def body_digest(event: AuditEvent) -> DigestSha256:
    return DigestSha256.of(event.canonical_bytes())


def link_digest(previous_link: DigestSha256, body: DigestSha256) -> DigestSha256:
    return DigestSha256(hashlib.sha256(LINK_DOMAIN + previous_link.raw + body.raw).digest())


@dataclass(frozen=True, slots=True)
class AuditProjection:
    """An event together with everything the database stores about its place in the chain."""

    event: AuditEvent
    body_json: str
    body_digest: DigestSha256
    prev_link_digest: DigestSha256
    link_digest: DigestSha256


def project(event: AuditEvent, previous_link: DigestSha256 | None) -> AuditProjection:
    """Chain `event` after `previous_link`; the first event of a journal passes None."""
    if event.journal_sequence == 1:
        if previous_link is not None:
            msg = "the first event of a journal chains to the genesis digest, not to a previous link"
            raise ValueError(msg)
        previous = genesis_link(event.journal_id)
    else:
        if previous_link is None:
            msg = "an event after the first needs the link digest of its predecessor"
            raise ValueError(msg)
        previous = previous_link
    body = body_digest(event)
    return AuditProjection(event, event.body_json(), body, previous, link_digest(previous, body))


__all__ = [
    "GENESIS_DOMAIN",
    "LINK_DOMAIN",
    "AuditProjection",
    "body_digest",
    "genesis_link",
    "link_digest",
    "project",
]
