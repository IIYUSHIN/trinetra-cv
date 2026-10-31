"""IMP-0100: I4 acceptance evidence - intake -> Case -> Preflight, the honest walking skeleton.

This module is an Author-tested evidence index plus one in-process journey over the real database. It is
NOT acceptance: formal acceptance needs independent review and CI, recorded elsewhere. Everything from
IMP-0041 on is also contingent on the I1/I2 gates, which this module does not and cannot establish.
"""

from __future__ import annotations

import ast
import io
import re
from pathlib import Path

import pytest

from trinetra.api.composition import WalkingSkeleton
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.enums import PackageSlot
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.case_assets import CaseAssetRole
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.decisions import CommandId
from trinetra.domain.timing import UtcTimestamp

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
NOW = UtcTimestamp.parse("2026-10-31T10:00:00Z")

# Each I4 packet and the test module that carries its evidence (shared modules name every packet they cover).
EVIDENCE = {
    "IMP-0071": TESTS / "application" / "test_imp0071_intake_session.py",
    "IMP-0072": TESTS / "application" / "test_imp0072_intake_transfer.py",
    "IMP-0073": TESTS / "application" / "test_imp0073_slot_contract.py",
    "IMP-0074": TESTS / "application" / "test_imp0074_create_intake.py",
    "IMP-0075": TESTS / "application" / "test_imp0075_receive_transfer.py",
    "IMP-0076": TESTS / "application" / "test_imp0076_transfer_validation.py",
    "IMP-0077": TESTS / "application" / "test_imp0077_transfer_admission.py",
    "IMP-0078": TESTS / "application" / "test_imp0078_commit_contract.py",
    "IMP-0079": TESTS / "application" / "test_imp0079_commit_idempotency.py",
    "IMP-0080": TESTS / "domain" / "test_imp0080_assurance_case.py",
    "IMP-0081": TESTS / "domain" / "test_imp0080_assurance_case.py",
    "IMP-0082": TESTS / "domain" / "test_imp0080_assurance_case.py",
    "IMP-0083": HERE / "test_imp0083_migration_0002.py",
    "IMP-0084": HERE / "test_imp0084_case_repository.py",
    "IMP-0085": HERE / "test_imp0084_case_repository.py",
    "IMP-0086": TESTS / "domain" / "test_imp0086_capability_snapshot.py",
    "IMP-0087": TESTS / "domain" / "test_imp0086_capability_snapshot.py",
    "IMP-0088": TESTS / "domain" / "test_imp0086_capability_snapshot.py",
    "IMP-0089": TESTS / "domain" / "test_imp0089_reference_records.py",
    "IMP-0090": TESTS / "domain" / "test_imp0089_reference_records.py",
    "IMP-0091": HERE / "test_imp0091_reference_repository.py",
    "IMP-0092": TESTS / "application" / "test_imp0092_case_context.py",
    "IMP-0093": TESTS / "application" / "test_imp0093_capability_planner.py",
    "IMP-0094": TESTS / "application" / "test_imp0094_preflight_projection.py",
    "IMP-0095": HERE / "test_imp0095_case_store.py",
    "IMP-0096": HERE / "test_imp0096_case_revision_store.py",
    "IMP-0097": HERE / "test_imp0097_partial_commit_recovery.py",
    "IMP-0098": HERE / "test_imp0098_reference_not_approval.py",
    "IMP-0099": HERE / "test_imp0099_restart_skeleton.py",
}

CLAIMY = re.compile(
    r"\b(passed?|ready|safe|trusted|certified|verified|assured|compliant|validated)\b", re.IGNORECASE
)


def command(n):
    return CommandId.from_entropy(lambda size: bytes([n]) * size)


def upload(sk, intake, n, slot, *, fmt, media, data):
    return sk.receive_transfer.receive(
        ReceiveTransferCommand(
            intake_id=intake,
            command_id=command(n),
            slot=slot,
            declared_format=DeclaredFormat(fmt) if fmt else None,
            logical_identifier=None,
            filename=OriginalFilename(value=f"file{n}.bin"),
            media_type=media,
            stream=io.BytesIO(data),
        )
    )


class Clock:
    def now(self):
        return NOW


@pytest.fixture
def journey(state_root):
    """One dataset, one model, two references and one note, committed; returns what a restart must keep."""
    with WalkingSkeleton.open(state_root, clock=Clock()) as sk:
        intake = sk.create_intake.create(
            CreateIntakeCommand(command_id=command(1), case_label="Harbour night")
        ).intake_id
        data = upload(
            sk,
            intake,
            2,
            PackageSlot.DATASET,
            fmt="coco",
            media="application/zip",
            data=b"PK\x03\x04" + b"d" * 40,
        )
        model = upload(
            sk,
            intake,
            3,
            PackageSlot.MODEL,
            fmt="onnx",
            media="application/octet-stream",
            data=b"\x08\x07" + b"m" * 30,
        )
        ref1 = upload(
            sk,
            intake,
            4,
            PackageSlot.REFERENCE,
            fmt=None,
            media="application/zip",
            data=b"PK\x03\x04" + b"1" * 20,
        )
        note = upload(
            sk,
            intake,
            5,
            PackageSlot.CONTRIBUTOR_METADATA,
            fmt=None,
            media="text/plain",
            data=b"collected at the quay",
        )
        ref2 = upload(
            sk,
            intake,
            6,
            PackageSlot.REFERENCE,
            fmt=None,
            media="application/zip",
            data=b"PK\x03\x04" + b"2" * 20,
        )
        result = sk.commit_intake.commit(intake)
        view = sk.queries.preflight(result.case.case_id)
        return {
            "case": result.case,
            "view": view,
            "assets": (data, model, ref1, note, ref2),
            "events": [e.event_type.text for e in _events(sk)],
        }


def _events(sk):
    with sk.store.unit_of_work() as uow:
        page = sk.audit.list_page(uow, limit=100)
        uow.rollback()
    return page.events


# ------------------------------------------------------------------ the evidence index
def test_every_i4_packet_has_an_existing_test_module_that_names_it():
    assert sorted(EVIDENCE) == [f"IMP-{n:04d}" for n in range(71, 100)]
    for packet, path in EVIDENCE.items():
        assert path.is_file(), packet
        text = path.read_text()
        assert text.count("def test_") >= 1, packet
        number = packet.removeprefix("IMP-")
        assert number in path.name or number in text[:1500], packet


# --- acceptance: one dataset and one model become a PREFLIGHT Case
def test_one_dataset_and_one_model_became_a_persistent_preflight_case(journey):
    case = journey["case"]
    assert case.lifecycle_state is CaseLifecycleState.PREFLIGHT
    assert case.case_state_revision.value == 1
    roles = [lk.role for lk in case.asset_links]
    assert roles.count(CaseAssetRole.DATASET) == roles.count(CaseAssetRole.MODEL) == 1


def test_optional_metadata_and_references_linked_correctly(journey):
    data, model, ref1, note, ref2 = journey["assets"]
    got = [(lk.role, lk.ordinal, lk.asset_id) for lk in journey["case"].asset_links]
    assert got == [
        (CaseAssetRole.DATASET, 0, data.reserved_asset_id),
        (CaseAssetRole.MODEL, 0, model.reserved_asset_id),
        (CaseAssetRole.CONTRIBUTOR_METADATA, 0, note.reserved_asset_id),
        (CaseAssetRole.REFERENCE, 0, ref1.reserved_asset_id),
        (CaseAssetRole.REFERENCE, 1, ref2.reserved_asset_id),
    ]


def test_restart_preserves_ids_revision_and_the_view(journey, state_root):
    case, view = journey["case"], journey["view"]
    with WalkingSkeleton.open(state_root, clock=Clock()) as sk:
        again = sk.cases.get(case.case_id)
        assert again == case
        assert again.case_state_revision.value == 1
        assert sk.queries.preflight(case.case_id).canonical_bytes() == view.canonical_bytes()


# --- acceptance: truthful capabilities, no invented findings
def test_capabilities_are_truthful_and_nothing_is_limited_or_invented(journey):
    view = journey["view"]
    shown = {c.capability.text: c for g in view.capability_groups for c in g.capabilities}
    assert len(shown) == 8
    available = [n for n, c in shown.items() if c.capability_state.value == "AVAILABLE"]
    assert available == ["asset_identity"]
    assert {c.capability_state.value for n, c in shown.items() if n != "asset_identity"} == {"UNAVAILABLE"}
    assert {c.reason_code.text for n, c in shown.items() if n != "asset_identity"} == {
        "not_implemented_in_current_build_stage"
    }
    assert view.planned_tests == () and all(c.planned_test_refs == () for c in shown.values())
    assert [o.capability.text for o in view.unavailable_obligations] == sorted(
        n for n in shown if n != "asset_identity"
    )


def test_every_conclusion_field_is_null_and_start_assessment_is_off(journey):
    context = journey["view"].case_context.to_wire()
    for name in (
        "principal_claim",
        "assurance_sufficiency",
        "policy_recommendation",
        "analyst_disposition",
        "freshness",
        "attention_category",
        "attention_reason",
        "coverage_summary",
    ):
        assert context[name] is None, name
    [action] = journey["view"].available_actions
    assert (action.allowed, action.reason_code.text) == (False, "assessment_runtime_not_implemented")
    [limitation] = journey["view"].critical_limitations
    assert limitation.summary.endswith("No assurance conclusion has been produced.")


def test_no_text_in_the_view_claims_readiness_safety_or_a_pass(journey):
    texts = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                texts.append(str(key))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            texts.append(node)

    walk(journey["view"].to_wire())
    assert [t for t in texts if CLAIMY.search(t)] == []


# ------------------------------------------------------------------ acceptance: forbidden things are absent
def test_there_is_no_ready_lifecycle_and_no_generic_source_revision():
    assert "READY" not in {m.name for m in CaseLifecycleState}
    assert "ready" not in {m.value for m in CaseLifecycleState}
    src = HERE.parents[1] / "backend" / "src" / "trinetra"
    used = []
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, ast.arg) or (isinstance(node, ast.keyword) and node.arg):
                names = [node.arg]
            if "source_revision" in names:
                used.append(path.name)
    assert used == []


def test_the_database_holds_only_preflight_cases_no_results_and_no_references(journey, state_root):
    with WalkingSkeleton.open(state_root, clock=Clock()) as sk, sk.store.unit_of_work() as uow:
        states = {r[0] for r in uow.connection.exec_driver_sql("SELECT DISTINCT lifecycle_state FROM cases")}
        tables = {
            r[0]
            for r in uow.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        refs = uow.connection.exec_driver_sql("SELECT COUNT(*) FROM reference_records").scalar_one()
        uow.rollback()
    assert states == {"preflight"}
    assert refs == 0  # two reference files were uploaded; none was approved
    assert not {t for t in tables if re.search(r"result|evidence|claim|verdict|finding", t)}


def test_the_audit_trail_names_exactly_what_happened(journey):
    assert journey["events"] == ["asset_ingested"] * 5 + ["case_created"]
