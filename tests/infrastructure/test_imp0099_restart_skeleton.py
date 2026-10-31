"""IMP-0099: the walking skeleton across separate processes - ids, revisions and views survive a restart."""

from __future__ import annotations

import json

import pytest

from process_support import run_python

SETUP = """
    from trinetra.api.composition import WalkingSkeleton
    from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
    from trinetra.application.intake.enums import PackageSlot
    from trinetra.application.intake.records import OriginalFilename
    from trinetra.application.intake.slots import DeclaredFormat
    from trinetra.domain.case import CaseId
    from trinetra.domain.decisions import CommandId
    from trinetra.domain.timing import UtcTimestamp

    class Clock:
        def now(self):
            return UtcTimestamp.parse("2026-10-31T10:00:00Z")

    def command(n):
        return CommandId.from_entropy(lambda size: bytes([n]) * size)

    def upload(sk, intake, n, slot, fmt, media, data):
        return sk.receive_transfer.receive(ReceiveTransferCommand(
            intake_id=intake, command_id=command(n), slot=slot,
            declared_format=DeclaredFormat(fmt) if fmt else None, logical_identifier=None,
            filename=OriginalFilename(value=f"file{n}.bin"), media_type=media, stream=io.BytesIO(data)))
"""

WRITE = SETUP + """
    mode = sys.argv[2]
    with WalkingSkeleton.open(root, clock=Clock()) as sk:
        created = sk.create_intake.create(
            CreateIntakeCommand(command_id=command(1), case_label="Night patrol"))
        intake = created.intake_id
        upload(sk, intake, 2, PackageSlot.DATASET, "coco", "application/zip", b"PK\\x03\\x04" + b"d" * 40)
        upload(sk, intake, 3, PackageSlot.MODEL, "onnx", "application/octet-stream",
               b"\\x08\\x07" + b"m" * 30)
        upload(sk, intake, 4, PackageSlot.REFERENCE, None, "application/zip", b"PK\\x03\\x04" + b"r" * 20)
        if mode == "open_only":
            session = sk.registry.require(intake)
            print(json.dumps({"case_id": str(session.reserved_case_id), "intake_id": str(intake)}))
            sys.stdout.flush()
            os._exit(9)
        result = sk.commit_intake.commit(intake)
        case = result.case
        view = sk.queries.preflight(case.case_id)
        print(json.dumps({
            "case_id": str(case.case_id), "intake_id": str(intake),
            "revision": case.case_state_revision.value,
            "links": [[lk.role.to_wire(), lk.ordinal, str(lk.asset_id)] for lk in case.asset_links],
            "case_digest": case.canonical_digest(), "view": view.canonical_bytes().decode()}))
        sys.stdout.flush()
        if mode == "crash":
            os._exit(9)  # no close, no checkpoint
"""

READ = SETUP + """
    from trinetra.infrastructure.cas_layout import CasLayout
    from trinetra.infrastructure.cas_verify import scan_objects
    from trinetra.infrastructure.audit_verify import verify_chain
    case_id = CaseId.parse(sys.argv[2])
    with WalkingSkeleton.open(root, clock=Clock()) as sk:
        case = sk.cases.get(case_id)
        out = {"found": case is not None}
        if case is not None:
            out.update({
                "revision": case.case_state_revision.value, "label": case.case_label,
                "lifecycle": case.lifecycle_state.to_wire(), "case_digest": case.canonical_digest(),
                "links": [[lk.role.to_wire(), lk.ordinal, str(lk.asset_id)] for lk in case.asset_links],
                "view": sk.queries.preflight(case_id).canonical_bytes().decode()})
        with sk.store.unit_of_work() as uow:
            page = sk.audit.list_page(uow, limit=100)
            report = verify_chain(uow, page.events[0].journal_id) if page.events else None
            uow.rollback()
        scan = scan_objects(CasLayout.for_state_root(root))
        out.update({"events": [e.event_type.text for e in page.events],
                    "intact": report.intact if report else None, "cas_clean": scan.clean,
                    "objects": scan.verified})
        print(json.dumps(out))
"""


def last_json(done):
    assert done.returncode in (0, 9), done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(params=["clean", "crash"])
def first(request, state_root):
    done = run_python(WRITE, state_root, request.param)
    assert done.returncode == (9 if request.param == "crash" else 0), done.stderr
    return state_root, last_json(done)


def test_a_fresh_process_reads_the_same_case_with_the_same_ids_and_revision(first):
    root, wrote = first
    done = run_python(READ, root, wrote["case_id"])
    got = last_json(done)
    assert got["found"] is True
    assert got["revision"] == wrote["revision"] == 1
    assert got["links"] == wrote["links"]
    assert got["case_digest"] == wrote["case_digest"]
    assert (got["label"], got["lifecycle"]) == ("Night patrol", "preflight")


def test_the_preflight_view_is_byte_identical_after_a_restart(first):
    root, wrote = first
    got = last_json(run_python(READ, root, wrote["case_id"]))
    assert got["view"] == wrote["view"]


def test_the_restarted_view_still_shows_no_invented_findings(first):
    root, wrote = first
    view = json.loads(last_json(run_python(READ, root, wrote["case_id"]))["view"])
    states = {c["capability"]: c["capability_state"]
              for g in view["capability_groups"] for c in g["capabilities"]}
    assert states.pop("asset_identity") == "AVAILABLE"
    assert set(states.values()) == {"UNAVAILABLE"} and len(states) == 7
    assert view["planned_tests"] == []
    assert view["available_actions"][0]["allowed"] is False
    assert view["case_context"]["principal_claim"] is None


def test_the_journal_cas_and_events_are_whole_after_a_restart(first):
    root, wrote = first
    got = last_json(run_python(READ, root, wrote["case_id"]))
    assert got["events"] == ["asset_ingested", "asset_ingested", "asset_ingested", "case_created"]
    assert got["intact"] is True and got["cas_clean"] is True and got["objects"] == 3


def test_reading_twice_in_new_processes_gives_the_same_answer(first):
    root, wrote = first
    one = last_json(run_python(READ, root, wrote["case_id"]))
    two = last_json(run_python(READ, root, wrote["case_id"]))
    assert one == two


def test_an_open_intake_is_lost_in_a_crash_but_its_admitted_facts_are_not_invented(state_root):
    wrote = last_json(run_python(WRITE, state_root, "open_only"))
    got = last_json(run_python(READ, state_root, wrote["case_id"]))
    assert got["found"] is False  # no Case was ever created for the lost intake
    assert got["events"] == []  # uploads reached quarantine only; nothing was admitted yet
    assert got["intact"] is None
