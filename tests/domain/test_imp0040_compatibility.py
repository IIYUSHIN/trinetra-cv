"""IMP-0040: primitive property / serialization compatibility suite.

Package-wide checks over everything in trinetra.domain, discovered by import so a new primitive
cannot escape them: enum dimensions are closed and never interchangeable, every model is immutable
and round-trips through its deterministic wire form, and no generic status / magic score exists.
Author-run evidence only; not independent review.
"""

from __future__ import annotations

import importlib
import inspect
import itertools
import json
import operator
import pkgutil
import unicodedata
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

import trinetra.domain as domain_pkg
from trinetra.domain.asset_refs import AssetRef
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.base import FORBIDDEN_FIELD_NAMES, SCORE_FIELD_EXEMPTIONS, DomainModel
from trinetra.domain.capability import CapabilityName, CapabilityRequirement, CapabilityState
from trinetra.domain.digests import ContentIdentity, DigestSha256
from trinetra.domain.entity import EntityRef, EntityType
from trinetra.domain.enums import WireEnum
from trinetra.domain.ids import IdentifierError, registered_id_types
from trinetra.domain.limitations import Limitation, LimitationCode, LimitationId
from trinetra.domain.metrics import MetricId
from trinetra.domain.references import ReferenceId
from trinetra.domain.scoped_metrics import MetricUnit, ScopedMetric

GOLDEN = Path(__file__).parent / "golden" / "enum_wire_forms.json"


def _modules():
    return [
        importlib.import_module(info.name)
        for info in pkgutil.walk_packages(domain_pkg.__path__, domain_pkg.__name__ + ".")
    ]


def _subclasses(base):
    found = {}
    for module in _modules():
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, base) and obj is not base and obj.__module__.startswith("trinetra.domain"):
                found[obj.__qualname__] = obj
    return found


ENUMS = _subclasses(WireEnum)
MODELS = _subclasses(DomainModel)


# --------------------------------------------------------------------------- enum dimensions
def test_enum_catalog_matches_the_golden_manifest():
    actual = {name: [m.value for m in cls] for name, cls in sorted(ENUMS.items())}
    assert json.loads(GOLDEN.read_text()) == actual, "register new/changed enums in enum_wire_forms.json"


def test_every_assurance_dimension_the_contract_names_exists():
    expected = {"TestVerdict", "ClaimState", "CoverageStatus", "AssuranceSufficiency", "Disposition",
                "FreshnessState", "CapabilityState", "ExecutionState", "SystemReadiness"}
    assert expected <= set(ENUMS)


def test_no_enum_member_is_equal_to_a_member_of_another_enum_even_with_shared_spelling():
    for (_, a), (_, b) in itertools.combinations(sorted(ENUMS.items()), 2):
        for x, y in itertools.product(a, b):
            clash = f"{a.__name__}.{x.name} confused with {b.__name__}.{y.name}"
            assert x != y and operator.eq(x, y) is False, clash


def test_shared_spellings_exist_so_the_check_above_is_not_vacuous():
    spellings: dict[str, set[str]] = {}
    for name, cls in ENUMS.items():
        for m in cls:
            spellings.setdefault(m.value, set()).add(name)
    shared = {s: n for s, n in spellings.items() if len(n) > 1}
    assert {"LIMITED", "INCONCLUSIVE", "UNAVAILABLE", "NOT_ASSESSED"} <= set(shared)


def test_enum_members_cannot_key_the_same_dict_slot_across_dimensions():
    seen = {}
    for cls in ENUMS.values():
        for m in cls:
            seen[m] = cls
    assert len(seen) == sum(len(c) for c in ENUMS.values())


# --------------------------------------------------------------------------- model conventions
def test_models_were_discovered():
    assert {"Limitation", "ScopedMetric", "EntityRef", "AssetRef", "CapabilityRequirement"} <= set(MODELS)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_no_model_declares_a_generic_status_or_score(name):
    model = MODELS[name]
    for field in model.model_fields:
        if (f"{model.__module__}.{model.__qualname__}", field) in SCORE_FIELD_EXEMPTIONS:
            continue  # the one scoped exemption (ERR-CL-020): Detection.score is a raw detector output
        assert field not in FORBIDDEN_FIELD_NAMES and "score" not in field and field != "confidence"


@pytest.mark.parametrize("name", sorted(MODELS))
def test_every_model_is_frozen_strict_and_closed(name):
    cfg = MODELS[name].model_config
    assert cfg["frozen"] is True and cfg["strict"] is True and cfg["extra"] == "forbid"


# --------------------------------------------------------------------------- id confusion
def test_every_id_type_has_a_unique_prefix_and_ids_never_cross_types():
    types = registered_id_types()
    assert len(types) == len({t.PREFIX for t in types.values()})
    for a, b in itertools.permutations(types.values(), 2):
        ident = a.generate()
        suffix = str(ident).split("_", 1)[1]
        twin = b.parse(f"{b.PREFIX}_{suffix}")  # same 128 bits, different type
        assert ident != twin and operator.eq(ident, twin) is False and hash(ident) != hash(twin)
        with pytest.raises(IdentifierError):
            b.parse(str(ident))


# --------------------------------------------------------------------------- property round-trips
def rebuild(cls, wire):
    def fix(v):
        if isinstance(v, list):
            return tuple(fix(i) for i in v)
        return v

    return cls(**{k: fix(v) for k, v in wire.items()})


tokens = st.from_regex(r"[a-z][a-z0-9]{0,8}(_[a-z0-9]{1,6}){0,3}", fullmatch=True)
texts = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), max_codepoint=0x2FFF),
    min_size=1, max_size=40,
).map(lambda t: unicodedata.normalize("NFC", t))
entity_refs = st.builds(
    EntityRef,
    entity_type=tokens.map(EntityType),
    entity_id=st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,30}", fullmatch=True),
    display_label=texts,
)
scopes = st.lists(entity_refs, min_size=1, max_size=4, unique=True).map(tuple)
limitations = st.builds(
    Limitation,
    limitation_id=st.builds(LimitationId.generate),
    code=tokens.map(LimitationCode),
    summary=texts,
    scope_refs=scopes,
    decision_relevant=st.booleans(),
    detail_available=st.booleans(),
)
metrics = st.builds(
    ScopedMetric,
    metric_id=st.builds(MetricId.generate),
    label=texts,
    value=st.one_of(st.integers(-(2**53 - 1), 2**53 - 1), st.floats(allow_nan=False, allow_infinity=False)),
    unit=tokens.map(MetricUnit),
    method_ref=st.builds(ReferenceId.generate),
    scope_refs=scopes,
    limitations=st.lists(st.builds(LimitationId.generate), max_size=3, unique=True).map(tuple),
)
asset_refs = st.builds(
    AssetRef,
    asset_id=st.builds(AssetId.generate),
    asset_kind=st.sampled_from(list(AssetKind)),
    content=st.builds(
        ContentIdentity,
        digest=st.binary(min_size=32, max_size=32).map(DigestSha256),
        size_bytes=st.integers(0, 2**40),
    ),
)
requirements = st.builds(
    CapabilityRequirement,
    capability=tokens.map(CapabilityName),
    minimum=st.sampled_from([CapabilityState.AVAILABLE, CapabilityState.LIMITED]),
)
ALL = st.one_of(entity_refs, limitations, metrics, asset_refs, requirements)


@settings(max_examples=150, deadline=None)
@given(ALL)
def test_wire_form_is_json_safe_and_rebuilds_an_equal_value_with_identical_bytes(model):
    wire = model.to_wire()
    assert json.loads(json.dumps(wire)) == wire
    again = rebuild(type(model), wire)
    assert again == model and hash(again) == hash(model)
    assert again.canonical_bytes() == model.canonical_bytes()
    assert again.canonical_digest() == model.canonical_digest()


@settings(max_examples=100, deadline=None)
@given(ALL)
def test_models_are_immutable_and_cannot_be_copied_around_validation(model):
    field = next(iter(type(model).model_fields))
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        setattr(model, field, getattr(model, field))
    with pytest.raises(Exception):  # noqa: B017 - closed API: any rejection is acceptable
        model.model_copy(update={field: None})


@settings(max_examples=100, deadline=None)
@given(ALL, ALL)
def test_distinct_values_never_share_a_canonical_digest(a, b):
    assert (a == b) == (a.canonical_digest() == b.canonical_digest() and type(a) is type(b))


def test_hash_is_salted_by_dimension_so_shared_spellings_do_not_collide():
    assert hash(CapabilityState.LIMITED) != hash(ENUMS["AssuranceSufficiency"].LIMITED)
    assert hash(ENUMS["TestVerdict"].INCONCLUSIVE) != hash(ENUMS["CoverageStatus"].INCONCLUSIVE)
