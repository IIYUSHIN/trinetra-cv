"""Run Python in a separate process against the same state root (restart, crash and concurrency tests).

Child scripts import only production code, so they exercise exactly what a real Core process would.
`PREAMBLE` opens the store and wires the I3 services; a script then appends its own body.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import trinetra

SOURCE_ROOT = str(Path(trinetra.__file__).resolve().parent.parent)

PREAMBLE = textwrap.dedent(
    """
    import io, itertools, json, os, sys
    from pathlib import Path
    from trinetra.domain.asset_record import ActorId
    from trinetra.domain.assets import AssetKind
    from trinetra.domain.audit import AuditEventType, AuditOutcome, ObjectRef, ObjectType
    from trinetra.domain.digests import DigestSha256
    from trinetra.infrastructure.asset_admission_service import AdmissionRequest, AssetAdmissionService
    from trinetra.infrastructure.asset_repository import AssetRepository
    from trinetra.infrastructure.audit_coupling import run_audited
    from trinetra.infrastructure.audit_repository import AuditDraft, AuditRepository
    from trinetra.infrastructure.audit_verify import verify_chain
    from trinetra.infrastructure.cas_layout import CasLayout
    from trinetra.infrastructure.cas_store import CasStore
    from trinetra.infrastructure.migration_runner import upgrade_to_head
    from trinetra.infrastructure.sqlite import DatabaseConfig
    from trinetra.infrastructure.unit_of_work import CoreStore

    root = Path(sys.argv[1])
    ACTOR = ActorId("core")

    def ingest_work(service, data, name="note.txt", hook=None):
        def work(uow):
            admitted = service.admit(uow, io.BytesIO(data), AdmissionRequest(
                kind=AssetKind.CONTRIBUTOR_METADATA, media_type="text/plain",
                supplied_name=name, created_by=ACTOR))
            if hook is not None:
                hook()
            record = admitted.record
            subject = ObjectRef(object_type=ObjectType("asset_record"), object_id=str(record.record_id),
                                record_digest=DigestSha256.parse(record.canonical_digest()))
            return admitted, AuditDraft(event_type=AuditEventType("asset_ingested"),
                                        outcome=AuditOutcome.SUCCESS, created_by=ACTOR,
                                        subject_refs=(subject,))
        return work
    """
)


def run_python(body: str, *args: object, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Run PREAMBLE + `body` in a fresh interpreter; arguments arrive as sys.argv[1:]."""
    env = {**os.environ, "PYTHONPATH": SOURCE_ROOT}
    command = [sys.executable, "-c", PREAMBLE + textwrap.dedent(body), *map(str, args)]
    return subprocess.run(command, check=False, env=env, capture_output=True, text=True, timeout=timeout)
