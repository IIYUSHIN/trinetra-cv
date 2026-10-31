"""IMP-0083 tests: migration 0002 (cases, case asset links, reference records and state events),
exercised with raw SQL so the database's own laws are what is tested."""

from __future__ import annotations

import pytest

from persistence_support import opaque, put_asset

T0, T1, T2 = "2026-10-31T10:00:00Z", "2026-10-31T10:00:05Z", "2026-10-31T10:01:00Z"
INSERT_CASE = (
    "INSERT INTO cases (case_id, case_label, lifecycle_state, state_revision, created_at, updated_at) "
    "VALUES (:case_id, :case_label, :lifecycle_state, :state_revision, :created_at, :updated_at)"
)
INSERT_LINK = (
    "INSERT INTO case_asset_links (case_id, asset_id, role, ordinal, linked_at) "
    "VALUES (:case_id, :asset_id, :role, :ordinal, :linked_at)"
)
INSERT_REF = (
    "INSERT INTO reference_records (reference_id, asset_id, purpose, authority, valid_from, valid_until, "
    "created_at) VALUES (:reference_id, :asset_id, :purpose, :authority, :valid_from, :valid_until, "
    ":created_at)"
)
INSERT_EVENT = (
    "INSERT INTO reference_state_events (event_id, reference_id, sequence, state, occurred_at, "
    "superseded_by_reference_id, reason_code, reason_text) VALUES (:event_id, :reference_id, :sequence, "
    ":state, :occurred_at, :superseded_by_reference_id, :reason_code, :reason_text)"
)


def case_row(n: int = 1, **over):
    return {"case_id": opaque("case", n), "case_label": None, "lifecycle_state": "preflight",
            "state_revision": 1, "created_at": T0, "updated_at": T0, **over}  # fmt: skip


def link_row(case: int, asset_id: str, role: str, ordinal: int = 0, **over):
    return {"case_id": opaque("case", case), "asset_id": asset_id, "role": role, "ordinal": ordinal,
            "linked_at": T0, **over}  # fmt: skip


def ref_row(n: int, asset_id: str, **over):
    return {"reference_id": opaque("ref", n), "asset_id": asset_id, "purpose": "ground_truth",
            "authority": "Harbour QA board", "valid_from": T0, "valid_until": None, "created_at": T0,
            **over}  # fmt: skip


def sup(n: int) -> dict:
    return {"superseded_by_reference_id": opaque("ref", n)}


def event_row(n: int, ref: int, seq: int, state: str = "ACTIVE", at: str = T0, **over):
    return {"event_id": opaque("refevt", n), "reference_id": opaque("ref", ref), "sequence": seq,
            "state": state, "occurred_at": at, "superseded_by_reference_id": None, "reason_code": None,
            "reason_text": None, **over}  # fmt: skip


@pytest.fixture
def world(raw):
    """One case with its dataset and model, and one reference asset with a reference record."""
    kinds = ["dataset", "model", "reference", "contributor_metadata", "assessment_profile"]
    ids = {k: put_asset(raw, i, k) for i, k in enumerate(kinds, start=1)}
    raw.run(INSERT_CASE, case_row(1))
    raw.run(INSERT_LINK, link_row(1, ids["dataset"], "dataset"))
    raw.run(INSERT_LINK, link_row(1, ids["model"], "model"))
    raw.run(INSERT_REF, ref_row(1, ids["reference"]))
    raw.run(INSERT_EVENT, event_row(1, 1, 1))
    raw.ids = ids
    return raw


# ------------------------------------------------------------------------ happy path
def test_a_complete_case_with_a_reference_is_accepted(world):
    assert world.rows("SELECT lifecycle_state, state_revision FROM cases") == [("preflight", 1)]
    rows = world.rows("SELECT role, ordinal FROM case_asset_links ORDER BY role")
    assert rows == [("dataset", 0), ("model", 0)]
    assert world.rows("SELECT state FROM reference_state_events") == [("ACTIVE",)]


def test_uploading_a_reference_asset_creates_no_reference_authority(raw):
    put_asset(raw, 1, "reference")
    assert raw.rows("SELECT count(*) FROM reference_records") == [(0,)]
    assert raw.rows("SELECT count(*) FROM reference_state_events") == [(0,)]


# ------------------------------------------------------------------------ cases
@pytest.mark.parametrize("over", [
    {"lifecycle_state": "assessing"}, {"state_revision": 2}, {"updated_at": T1}, {"lifecycle_state": "ready"},
    {"lifecycle_state": "PREFLIGHT"}, {"state_revision": 0}, {"case_label": ""}, {"case_label": "x" * 257},
    {"case_id": "case_" + "A" * 32}, {"case_id": "case_" + "a" * 31}, {"case_id": "cas_" + "a" * 32},
    {"created_at": "2026-10-31 10:00:00"}, {"created_at": "2026-10-31T10:00:00+00:00"},
])
def test_a_case_must_be_born_valid(raw, over):
    row = case_row(1, **over)
    if "updated_at" not in over:
        row["updated_at"] = row["created_at"]
    assert raw.attempt(INSERT_CASE, row) != ""
    assert raw.rows("SELECT count(*) FROM cases") == [(0,)]


def test_a_label_of_exactly_256_characters_is_stored(raw):
    assert raw.attempt(INSERT_CASE, case_row(1, case_label="x" * 256)) == ""


def test_the_revision_advances_by_exactly_one(world):
    up = "UPDATE cases SET state_revision = :r, updated_at = :t WHERE case_id = :c"
    c = opaque("case", 1)
    assert world.attempt(up, {"r": 2, "t": T1, "c": c}) == ""
    assert "exactly one" in world.attempt(up, {"r": 2, "t": T1, "c": c})  # stale: 2 -> 2
    assert "exactly one" in world.attempt(up, {"r": 4, "t": T1, "c": c})  # a skip
    assert "exactly one" in world.attempt(up, {"r": 1, "t": T1, "c": c})  # backwards
    assert world.attempt(up, {"r": 3, "t": T1, "c": c}) == ""  # equal time is allowed
    assert world.rows("SELECT state_revision FROM cases") == [(3,)]


def test_time_never_goes_backwards_and_identity_is_fixed(world):
    c = opaque("case", 1)
    world.run("UPDATE cases SET state_revision = 2, updated_at = :t", {"t": T2})
    assert "exactly one" in world.attempt(
        "UPDATE cases SET state_revision = 3, updated_at = :t", {"t": T1})
    assert "identity" in world.attempt(
        "UPDATE cases SET state_revision = 3, case_id = :n", {"n": opaque("case", 9)})
    assert "identity" in world.attempt(
        "UPDATE cases SET state_revision = 3, created_at = :t", {"t": T1})
    assert world.rows("SELECT case_id, created_at, state_revision FROM cases") == [(c, T0, 2)]


def test_a_case_can_never_be_deleted(world):
    assert "never deleted" in world.attempt("DELETE FROM cases")


def test_a_case_cannot_be_replaced_into_a_new_history(world):
    forged = case_row(1, case_label="forged")
    sql = INSERT_CASE.replace("INSERT INTO", "INSERT OR REPLACE INTO")
    assert "cannot be replaced" in world.attempt(sql, forged)
    assert "cannot be replaced" in world.attempt(INSERT_CASE, forged)  # a plain duplicate insert too
    assert world.rows("SELECT case_label, state_revision FROM cases") == [(None, 1)]


def test_replace_cannot_reset_a_case_revision(world):
    world.run("UPDATE cases SET state_revision = 2, updated_at = :t", {"t": T1})
    sql = INSERT_CASE.replace("INSERT INTO", "INSERT OR REPLACE INTO")
    assert "cannot be replaced" in world.attempt(sql, case_row(1))
    assert world.rows("SELECT state_revision FROM cases") == [(2,)]


# ------------------------------------------------------------------------ case asset links
def test_a_role_must_equal_the_asset_kind(world):
    ids = world.ids
    msg = "role must equal"
    assert msg in world.attempt(INSERT_LINK, link_row(1, ids["reference"], "contributor_metadata", 0))
    assert msg in world.attempt(INSERT_LINK, link_row(1, ids["contributor_metadata"], "reference", 0))
    assert msg in world.attempt(INSERT_LINK, link_row(1, ids["assessment_profile"], "inference_record", 0))
    assert world.attempt(INSERT_LINK, link_row(1, ids["reference"], "reference", 0)) == ""


def test_an_asset_holds_one_role_per_case(world):
    # the same dataset asset again, under the right role at another ordinal, is still a duplicate
    assert world.attempt(INSERT_LINK, link_row(1, world.ids["dataset"], "dataset", 1)) != ""


@pytest.mark.parametrize("role", ["dataset", "model", "contributor_metadata", "assessment_profile"])
def test_singular_roles_sit_at_ordinal_zero(raw, role):
    raw.run(INSERT_CASE, case_row(1))
    asset_id = put_asset(raw, 1, role)
    assert "CHECK" in raw.attempt(INSERT_LINK, link_row(1, asset_id, role, 1))
    assert raw.attempt(INSERT_LINK, link_row(1, asset_id, role, 0)) == ""


def test_references_and_inference_records_may_have_many_ordinals(raw):
    raw.run(INSERT_CASE, case_row(1))
    for i, ordinal in enumerate((0, 1, 2), start=1):
        link = link_row(1, put_asset(raw, i, "reference"), "reference", ordinal)
        assert raw.attempt(INSERT_LINK, link) == ""
    record = link_row(1, put_asset(raw, 9, "inference_record"), "inference_record", 0)
    assert raw.attempt(INSERT_LINK, record) == ""


@pytest.mark.parametrize("ordinal", [-1, 1000, 2**31])
def test_ordinals_are_bounded(raw, ordinal):
    raw.run(INSERT_CASE, case_row(1))
    link = link_row(1, put_asset(raw, 1, "reference"), "reference", ordinal)
    assert "CHECK" in raw.attempt(INSERT_LINK, link)


def test_two_assets_cannot_share_a_role_and_ordinal(world):
    other = put_asset(world, 20, "reference")
    first = put_asset(world, 21, "reference")
    assert world.attempt(INSERT_LINK, link_row(1, first, "reference", 0)) == ""
    assert "append-only" in world.attempt(INSERT_LINK, link_row(1, other, "reference", 0))  # same key
    assert world.attempt(INSERT_LINK, link_row(1, other, "reference", 1)) == ""


def test_links_need_a_real_case_and_a_real_asset(world):
    assert "FOREIGN KEY" in world.attempt(INSERT_LINK, link_row(7, world.ids["reference"], "reference"))
    assert world.attempt(INSERT_LINK, link_row(1, opaque("asset", 999), "reference")) != ""


def test_links_are_append_only(world):
    assert "append-only" in world.attempt("UPDATE case_asset_links SET ordinal = 0")
    assert "append-only" in world.attempt("DELETE FROM case_asset_links")
    sql = INSERT_LINK.replace("INSERT INTO", "INSERT OR REPLACE INTO")
    assert "append-only" in world.attempt(sql, link_row(1, world.ids["dataset"], "dataset"))
    assert world.rows("SELECT count(*) FROM case_asset_links") == [(2,)]


# ------------------------------------------------------------------------ reference records
def test_a_reference_record_needs_a_reference_asset(world):
    for kind in ("dataset", "model", "contributor_metadata"):
        assert "of kind reference" in world.attempt(INSERT_REF, ref_row(2, world.ids[kind]))
    assert "of kind reference" in world.attempt(INSERT_REF, ref_row(2, opaque("asset", 999)))


@pytest.mark.parametrize("over", [
    {"valid_until": T0}, {"valid_until": "2026-10-30T10:00:00Z"}, {"valid_until": "tomorrow"},
    {"valid_from": "2026-10-31"}, {"purpose": "Ground Truth"}, {"purpose": ""}, {"purpose": "9lives"},
    {"authority": ""}, {"authority": "x" * 129}, {"reference_id": "ref_" + "A" * 32},
    {"reference_id": "case_" + "a" * 32},
])
def test_reference_record_checks(world, over):
    assert world.attempt(INSERT_REF, ref_row(2, world.ids["reference"], **over)) != ""


def test_a_reference_may_have_an_open_or_a_bounded_validity(world):
    assert world.attempt(INSERT_REF, ref_row(2, world.ids["reference"], valid_until=T2)) == ""
    assert world.rows("SELECT valid_until FROM reference_records ORDER BY reference_id") == [(None,), (T2,)]


def test_reference_records_are_immutable(world):
    assert "append-only" in world.attempt("UPDATE reference_records SET purpose = 'other'")
    assert "append-only" in world.attempt("DELETE FROM reference_records")
    sql = INSERT_REF.replace("INSERT INTO", "INSERT OR REPLACE INTO")
    assert "append-only" in world.attempt(sql, ref_row(1, world.ids["reference"], authority="forged"))
    assert world.rows("SELECT authority FROM reference_records") == [("Harbour QA board",)]


# ------------------------------------------------------------------------ reference state events
def test_the_first_event_is_sequence_one_and_active(world):
    world.run(INSERT_REF, ref_row(2, world.ids["reference"]))
    assert world.attempt(INSERT_EVENT, event_row(2, 2, 1, "REVOKED")) != ""  # first event must be ACTIVE
    assert world.attempt(INSERT_EVENT, event_row(2, 2, 2, "ACTIVE")) != ""  # sequence 2 cannot start a chain
    assert world.attempt(INSERT_EVENT, event_row(2, 2, 1, "ACTIVE")) == ""


def test_a_reference_can_be_revoked_once_after_being_active(world):
    assert world.attempt(INSERT_EVENT, event_row(2, 1, 2, "REVOKED", T1, reason_code="expired_by_policy",
                                                 reason_text="withdrawn by the board")) == ""  # fmt: skip
    assert world.rows("SELECT sequence, state FROM reference_state_events ORDER BY sequence") == [
        (1, "ACTIVE"), (2, "REVOKED")]  # fmt: skip


def test_nothing_follows_a_terminal_state_and_there_are_no_gaps(world):
    assert world.attempt(INSERT_EVENT, event_row(2, 1, 3, "REVOKED", T1)) != ""  # a gap
    world.run(INSERT_EVENT, event_row(2, 1, 2, "REVOKED", T1))
    assert "ACTIVE event" in world.attempt(INSERT_EVENT, event_row(3, 1, 3, "REVOKED", T2))
    assert "UNIQUE" in world.attempt(INSERT_EVENT, event_row(4, 1, 2, "REVOKED", T2))
    assert world.attempt(INSERT_EVENT, event_row(5, 1, 3, "ACTIVE", T2)) != ""


def test_supersession_names_a_different_existing_reference(world):
    world.run(INSERT_REF, ref_row(2, put_asset(world, 30, "reference")))
    bad_rows = [
        event_row(2, 1, 2, "SUPERSEDED", T1),  # no successor named
        event_row(2, 1, 2, "SUPERSEDED", T1, **sup(1)),  # itself
        event_row(2, 1, 2, "REVOKED", T1, **sup(2)),  # a successor on a non-supersession
        event_row(2, 1, 2, "SUPERSEDED", T1, **sup(77)),  # unknown successor
    ]
    for row in bad_rows:
        assert world.attempt(INSERT_EVENT, row) != ""
    assert world.attempt(INSERT_EVENT, event_row(2, 1, 2, "SUPERSEDED", T1, **sup(2))) == ""


def test_an_event_cannot_predate_the_one_before_it(world):
    world.run(INSERT_EVENT, event_row(8, 1, 2, "REVOKED", T1))
    # a second reference whose ACTIVE event is later than its would-be successor
    world.run(INSERT_REF, ref_row(2, put_asset(world, 31, "reference")))
    world.run(INSERT_EVENT, event_row(10, 2, 1, "ACTIVE", T2))
    assert "predate" in world.attempt(INSERT_EVENT, event_row(11, 2, 2, "REVOKED", T1))
    assert world.attempt(INSERT_EVENT, event_row(11, 2, 2, "REVOKED", T2)) == ""


@pytest.mark.parametrize("over", [
    {"reason_text": ""}, {"reason_text": "x" * 2049}, {"reason_code": "Bad Code"}, {"reason_code": ""},
    {"state": "EXPIRED"}, {"state": "active"}, {"event_id": "refevt_" + "A" * 32},
    {"occurred_at": "2026-10-31"},
])
def test_event_field_checks(world, over):
    row = event_row(2, 1, 2, "REVOKED", T1)
    assert world.attempt(INSERT_EVENT, {**row, **over}) != ""


def test_event_text_bounds_are_exact(world):
    assert world.attempt(INSERT_EVENT, event_row(2, 1, 2, "REVOKED", T1, reason_text="x" * 2048)) == ""


def test_reference_events_are_append_only(world):
    assert "append-only" in world.attempt("UPDATE reference_state_events SET reason_text = 'x'")
    assert "append-only" in world.attempt("DELETE FROM reference_state_events")
    sql = INSERT_EVENT.replace("INSERT INTO", "INSERT OR REPLACE INTO")
    assert "append-only" in world.attempt(sql, event_row(1, 1, 1, "ACTIVE", T1))
    assert world.rows("SELECT occurred_at FROM reference_state_events") == [(T0,)]


def test_events_need_a_real_reference(world):
    assert "FOREIGN KEY" in world.attempt(INSERT_EVENT, event_row(2, 9, 1, "ACTIVE"))


def test_there_is_no_capability_snapshot_table_yet(raw):
    names = {r[0] for r in raw.rows("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {n for n in names if "capabilit" in n}


def test_ready_can_never_be_persisted_as_a_lifecycle_state(world):
    up = "UPDATE cases SET lifecycle_state = :s, state_revision = 2, updated_at = :t"
    for bad in ("ready", "READY", "safe", "preflight_done"):
        assert "CHECK" in world.attempt(up, {"s": bad, "t": T1})
    assert world.rows("SELECT lifecycle_state, state_revision FROM cases") == [("preflight", 1)]
    # lifecycle transitions are not frozen at the database (later modules own them); the vocabulary is
    assert world.attempt(up, {"s": "assessing", "t": T1}) == ""
