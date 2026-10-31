"""IMP-0070: I3 acceptance evidence.

This module is an Author-tested evidence index plus one three-process lifecycle. It is not acceptance:
formal acceptance needs independent review and CI, which are recorded elsewhere.
"""

from __future__ import annotations

import json
from pathlib import Path

from process_support import run_python

HERE = Path(__file__).resolve().parent
DOMAIN = HERE.parent / "domain"

# Each I3 packet and the test module that carries its evidence.
EVIDENCE = {
    "IMP-0058": HERE / "test_imp0058_asset_immutability.py",
    "IMP-0059": HERE / "test_imp0059_admission_service.py",
    "IMP-0060": DOMAIN / "test_imp0060_audit_event.py",
    "IMP-0061": DOMAIN / "test_imp0061_audit_link.py",
    "IMP-0062": HERE / "test_imp0062_audit_repository.py",
    "IMP-0063": HERE / "test_imp0063_audit_verifier.py",
    "IMP-0064": HERE / "test_imp0064_audit_coupling.py",
    "IMP-0065": HERE / "test_imp0065_restart.py",
    "IMP-0066": HERE / "test_imp0066_crash.py",
    "IMP-0067": HERE / "test_imp0067_concurrent_admission.py",
    "IMP-0068": HERE / "test_imp0068_path_security.py",
    "IMP-0069": HERE / "test_imp0069_integrity_suite.py",
}

WRITE = """
    with CoreStore.open(DatabaseConfig(root)) as store:
        upgrade_to_head(store)
        service = AssetAdmissionService(CasStore(CasLayout.for_state_root(root)), AssetRepository())
        audit = AuditRepository()
        for data in sys.argv[2].encode().split(b","):
            run_audited(store, audit, ingest_work(service, data), on_failure=None)
"""

CHECK = """
    from trinetra.infrastructure.cas_read import read_object
    from trinetra.infrastructure.cas_verify import scan_objects
    with CoreStore.open(DatabaseConfig(root)) as store:
        upgrade_to_head(store)
        layout = CasLayout.for_state_root(root)
        repository = AssetRepository()
        with store.unit_of_work() as uow:
            count = repository.count(uow)
            report = verify_chain(uow, AuditRepository().head(uow).journal_id)
            uow.rollback()
        scan = scan_objects(layout)
        print(json.dumps({"records": count, "events": report.events_checked, "intact": report.intact,
                          "cas_clean": scan.clean, "objects": scan.verified}))
"""


def test_every_i3_packet_has_an_existing_non_trivial_test_module():
    for packet, path in EVIDENCE.items():
        assert path.is_file(), packet
        assert path.read_text().count("def test_") >= 1, packet
        assert packet.replace("IMP-", "imp") in path.name, packet


def test_three_separate_processes_see_one_consistent_history(tmp_path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    assert run_python(WRITE, root, "one,two").returncode == 0
    mid = run_python(CHECK, root)
    assert mid.returncode == 0, mid.stderr
    assert json.loads(mid.stdout.strip().splitlines()[-1]) == {
        "records": 2, "events": 2, "intact": True, "cas_clean": True, "objects": 2,
    }  # fmt: skip
    assert run_python(WRITE, root, "two,three").returncode == 0  # one duplicate content, one new
    end = run_python(CHECK, root)
    assert json.loads(end.stdout.strip().splitlines()[-1]) == {
        "records": 4, "events": 4, "intact": True, "cas_clean": True, "objects": 3,
    }  # fmt: skip
