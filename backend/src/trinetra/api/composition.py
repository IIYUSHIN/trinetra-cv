"""Composition root of the walking skeleton (IMP-0099): wires the use cases to the real adapters.

Opening a `WalkingSkeleton` opens the Core store (taking the sole-writer lock), applies pending migrations,
and builds every use case over the real database, CAS, audit journal and intake quarantine. It contains no
logic of its own and no transport: an HTTP layer calls these use cases, nothing more. The clock, the id
factory and the audit repository can be replaced so tests control time and identifiers.

The intake registry is process-local by design (EIB-04): OPEN intakes do not survive a restart; committed
assets, Cases, references and audit events do.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from trinetra.application.cases.revision import CaseUpdateService
from trinetra.application.intake.admission import TransferAdmissionService
from trinetra.application.intake.commit_admission import CommitAdmissionStep
from trinetra.application.intake.commit_case import CommitIntakeService
from trinetra.application.intake.receive import ReceiveTransferService
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.application.ports.clock import ClockPort
from trinetra.application.ports.ids import IdFactoryPort
from trinetra.application.preflight.catalog import BuildStageCapabilitySource
from trinetra.application.preflight.planner import PreflightPlanner
from trinetra.application.projections.queries import PreflightQueryService
from trinetra.infrastructure.asset_admission_service import AssetAdmissionService
from trinetra.infrastructure.asset_reader import StoreAssetRecords
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.audit_repository import AuditRepository
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_store import CasStore
from trinetra.infrastructure.case_repository import CaseRepository
from trinetra.infrastructure.case_store import AuditedCaseStore
from trinetra.infrastructure.intake_adapters import (
    AuditedAssetAdmission,
    IntakeQuarantine,
    StoreAssetLookup,
    SystemClock,
    SystemIdFactory,
)
from trinetra.infrastructure.migration_runner import upgrade_to_head
from trinetra.infrastructure.reference_approvals import StoreApprovedReferences
from trinetra.infrastructure.reference_repository import ReferenceRepository
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.unit_of_work import CoreStore


@dataclass(frozen=True, slots=True)
class WalkingSkeleton:
    store: CoreStore
    registry: InMemoryIntakeRegistry
    create_intake: CreateIntakeService
    receive_transfer: ReceiveTransferService
    commit_intake: CommitIntakeService
    update_case: CaseUpdateService
    queries: PreflightQueryService
    cases: AuditedCaseStore
    audit: AuditRepository

    @classmethod
    @contextmanager
    def open(
        cls, state_root: Path, *, clock: ClockPort | None = None, ids: IdFactoryPort | None = None,
        audit: AuditRepository | None = None,
    ) -> Iterator[WalkingSkeleton]:
        clock = clock or SystemClock()
        ids = ids or SystemIdFactory()
        journal = audit or AuditRepository()
        with CoreStore.open(DatabaseConfig(state_root)) as store:
            upgrade_to_head(store)
            yield cls._wire(store, state_root, clock, ids, journal)

    @classmethod
    def _wire(
        cls, store: CoreStore, state_root: Path, clock: ClockPort, ids: IdFactoryPort,
        journal: AuditRepository,
    ) -> WalkingSkeleton:
        registry = InMemoryIntakeRegistry()
        quarantine = IntakeQuarantine.for_state_root(state_root)
        assets = AssetRepository()
        admission_service = AssetAdmissionService(CasStore(CasLayout.for_state_root(state_root)), assets)
        admission = AuditedAssetAdmission(store, journal, admission_service, quarantine)
        lookup = StoreAssetLookup(store, assets)
        transfers = TransferAdmissionService(clock, registry, quarantine, admission, lookup)
        cases = AuditedCaseStore(store, CaseRepository(), journal)
        commit = CommitIntakeService(CommitAdmissionStep(registry, transfers), registry, cases, clock)
        queries = PreflightQueryService(
            cases, StoreAssetRecords(store, assets), StoreApprovedReferences(store, ReferenceRepository()),
            PreflightPlanner(BuildStageCapabilitySource(), clock), clock,
        )
        return cls(
            store=store, registry=registry,
            create_intake=CreateIntakeService(clock, ids, registry),
            receive_transfer=ReceiveTransferService(clock, ids, registry, quarantine),
            commit_intake=commit, update_case=CaseUpdateService(cases, clock), queries=queries,
            cases=cases, audit=journal,
        )


__all__ = ["WalkingSkeleton"]
