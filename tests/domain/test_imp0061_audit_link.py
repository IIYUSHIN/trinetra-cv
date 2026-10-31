"""IMP-0061 tests: the link scheme is deterministic, domain-separated and sensitive to every input."""

from __future__ import annotations

import hashlib
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trinetra.domain.asset_record import ActorId
from trinetra.domain.audit import AuditEvent, AuditEventId, AuditEventType, AuditOutcome, JournalId
from trinetra.domain.audit_link import (
    GENESIS_DOMAIN,
    LINK_DOMAIN,
    body_digest,
    genesis_link,
    link_digest,
    project,
)
from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp


def make(sequence=1, journal="local_main", etype="asset_ingested", rid=1):
    return AuditEvent(
        record_id=AuditEventId.from_entropy(lambda n: bytes([rid]) * n),
        created_at=UtcTimestamp.parse("2026-10-31T08:00:00Z"),
        created_by=ActorId("core"),
        journal_id=JournalId(journal),
        journal_sequence=sequence,
        event_type=AuditEventType(etype),
        outcome=AuditOutcome.SUCCESS,
    )


def sha(data):
    return DigestSha256(hashlib.sha256(data).digest())


def test_the_domain_tags_are_the_documented_constants():
    assert LINK_DOMAIN == b"trinetra.audit.link.v1\x00"
    assert GENESIS_DOMAIN == b"trinetra.audit.genesis.v1\x00"
    assert LINK_DOMAIN != GENESIS_DOMAIN


def test_the_body_digest_is_the_hash_of_the_canonical_body_text():
    event = make()
    assert body_digest(event) == sha(event.body_json().encode("utf-8"))
    assert body_digest(event).to_wire() == event.canonical_digest()


def test_genesis_is_derived_from_the_journal_id_with_its_own_tag():
    assert genesis_link(JournalId("local_main")) == sha(GENESIS_DOMAIN + b"local_main")
    assert genesis_link(JournalId("local_main")) != genesis_link(JournalId("other_journal"))
    assert genesis_link(JournalId("local_main")) != sha(b"local_main")


def test_the_link_hashes_raw_digest_bytes_with_the_link_tag():
    prev, body = sha(b"p"), sha(b"b")
    assert link_digest(prev, body) == sha(LINK_DOMAIN + prev.raw + body.raw)
    assert link_digest(prev, body) != sha(LINK_DOMAIN + prev.hex.encode() + body.hex.encode())


def test_link_order_matters_and_every_input_changes_the_result():
    a, b, c = sha(b"a"), sha(b"b"), sha(b"c")
    assert link_digest(a, b) != link_digest(b, a)
    assert link_digest(a, b) != link_digest(a, c) != link_digest(c, b)


def test_the_first_event_chains_to_genesis():
    event = make(sequence=1)
    projection = project(event, None)
    assert projection.prev_link_digest == genesis_link(event.journal_id)
    assert projection.body_digest == body_digest(event)
    assert projection.link_digest == link_digest(projection.prev_link_digest, projection.body_digest)
    assert projection.body_json == event.body_json() and projection.event == event


def test_later_events_chain_to_the_previous_link():
    first = project(make(sequence=1), None)
    second = project(make(sequence=2, rid=2), first.link_digest)
    assert second.prev_link_digest == first.link_digest
    assert second.link_digest != first.link_digest


def test_the_chain_position_rules_are_enforced():
    with pytest.raises(ValueError, match="genesis"):
        project(make(sequence=1), sha(b"x"))
    with pytest.raises(ValueError, match="predecessor"):
        project(make(sequence=2), None)


def test_projection_is_deterministic_and_sensitive_to_the_event():
    assert project(make(), None) == project(make(), None)
    base = project(make(), None).link_digest
    assert project(make(etype="asset_rejected"), None).link_digest != base
    assert project(make(rid=2), None).link_digest != base
    assert project(make(journal="other_journal"), None).link_digest != base


def test_the_projected_body_is_valid_json_equal_to_the_wire_form():
    projection = project(make(), None)
    assert json.loads(projection.body_json) == projection.event.to_wire()


def test_two_journals_never_share_a_chain_start():
    a = project(make(journal="journal_a"), None)
    b = project(make(journal="journal_b"), None)
    assert a.prev_link_digest != b.prev_link_digest and a.link_digest != b.link_digest


@given(st.lists(st.binary(min_size=1, max_size=20), min_size=2, max_size=8, unique=True))
def test_reordering_a_chain_changes_its_head(blobs):
    def head(order):
        link = genesis_link(JournalId("local_main"))
        for blob in order:
            link = link_digest(link, sha(blob))
        return link

    assert head(blobs) != head(list(reversed(blobs)))
    assert head(blobs) != head(blobs[:-1])
