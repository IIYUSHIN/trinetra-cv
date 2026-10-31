"""Builders shared by the projection and preflight tests (not a test module)."""

from __future__ import annotations

from trinetra.application.preflight.catalog import BuildStageCapabilitySource
from trinetra.application.preflight.planner import PreflightPlanner
from trinetra.domain.asset_record import ActorId, AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.case import CaseId
from trinetra.domain.case_assets import CaseAssetRole, link_assets
from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp

T0 = UtcTimestamp.parse("2026-10-31T10:00:00Z")
T1 = UtcTimestamp.parse("2026-10-31T10:00:07Z")


def entropy_of(n: int):
    return lambda size: bytes([n]) * size


CASE_ID = CaseId.from_entropy(entropy_of(1))
DATASET_ID = AssetId.from_entropy(entropy_of(2))
MODEL_ID = AssetId.from_entropy(entropy_of(3))
OTHER_ID = AssetId.from_entropy(entropy_of(4))


class FixedClock:
    def __init__(self, moment=T1):
        self.moment = moment

    def now(self):
        return self.moment


def make_case(label=None, **over):
    case = AssuranceCase.create(
        CASE_ID,
        link_assets([(CaseAssetRole.DATASET, DATASET_ID), (CaseAssetRole.MODEL, MODEL_ID)], T0),
        T0, label=label,
    )
    return case.evolve(**over) if over else case


def record(asset_id, kind, name):
    return AssetRecord(
        record_id=asset_id, asset_kind=kind, blob_digest=DigestSha256.of(name.encode()), size_bytes=5,
        media_type="application/octet-stream", supplied_name=name, created_at=T0, created_by=ActorId("core"),
    )


def dataset_record(name="coco.zip"):
    return record(DATASET_ID, AssetKind.DATASET, name)


def model_record(name="yolo.onnx"):
    return record(MODEL_ID, AssetKind.MODEL, name)


def build_stage_snapshot(case=None, approved=()):
    return PreflightPlanner(BuildStageCapabilitySource(), FixedClock()).plan(case or make_case(), approved)
