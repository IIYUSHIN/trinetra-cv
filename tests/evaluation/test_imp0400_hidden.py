"""Hidden truth fails closed under incidental JSON/CBOR/repr/log/pickle export."""

import dataclasses
import json
import logging
import pickle

import cbor2
import pytest

from trinetra.jcs import canonical_digest
from trinetra_eval.hidden import HiddenTruthRecord, private_document

CANARY = "SYNTHETIC_PRIVATE_TRUTH_0396_0420"


def fields():
    return {
        "schema_version": 1,
        "scenario_id": "scn_" + "a" * 32,
        "evaluation_family": "MODEL_BEHAVIOUR",
        "scenario_family": "synthetic",
        "cause_class": "SEEDED_ADVERSE_MUTATION",
        "mutation_truth": {
            "mutation_state": "NONE",
            "operation": "synthetic-noop",
            "before_digest": "sha256:" + "0" * 64,
            "after_digest": "sha256:" + "0" * 64,
            "affected_entity_refs": (),
        },
        "behavioural_truth": {
            "behavioural_state": "NO_MATERIAL_EFFECT",
            "criterion_ref": "sha256:" + "1" * 64,
            "observation_refs": (),
            "limitations": ("synthetic-only",),
        },
        "generation_truth": {"canary": CANARY},
        "affected_entity_truth": (),
        "benign_entity_truth": (),
        "expected_method_targets": (),
        "expected_claims": (),
        "expected_coverage": (),
        "expected_policy": {
            "decision_scope": (),
            "policy_profile_digest": "sha256:" + "2" * 64,
            "allowed_dispositions": ("REVIEW",),
            "forbidden_dispositions": (),
            "triggered_rule_ids": (),
            "reason_codes": (),
        },
        "expected_scope": {"required_scope_refs": (), "permitted_scope_refs": (), "forbidden_scope_refs": ()},
        "expected_lineage": {},
        "expected_capsule": {},
        "evaluation_isolation_group_id": "eig_" + "b" * 32,
        "generation_seed_ref": "synthetic-seed",
        "reproducibility_record_ref": "synthetic-repro",
        "created_at": "2026-10-31T00:00:00Z",
    }


def truth(**changes):
    body = fields() | changes
    # Independent fixture conversion for JCS arrays, not the hidden serializer.
    wire = json.loads(json.dumps(body))
    body["truth_digest"] = canonical_digest(wire)
    return HiddenTruthRecord(**body)


def test_private_digest_is_checked_and_input_is_not_retained_mutably():
    data = {"canary": CANARY}
    record = truth(generation_truth=data)
    data["canary"] = "changed"
    assert private_document(record)["generation_truth"]["canary"] == CANARY
    with pytest.raises(ValueError, match="private"):
        HiddenTruthRecord(**fields(), truth_digest="sha256:" + "0" * 64)


@pytest.mark.parametrize("encoder", [json.dumps, cbor2.dumps, vars, dict, pickle.dumps, dataclasses.asdict])
def test_incidental_serializers_never_receive_truth(encoder):
    with pytest.raises((TypeError, ValueError, cbor2.CBOREncodeError)) as error:
        encoder(truth())
    assert CANARY not in str(error.value)


def test_repr_logging_and_nested_exports_do_not_leak(caplog):
    record = truth()
    assert CANARY not in repr(record)
    with caplog.at_level(logging.WARNING):
        logging.getLogger("synthetic").warning("record=%r", record)
    assert CANARY not in caplog.text
    for obj in ({"hidden": record}, [record]):
        with pytest.raises(TypeError):
            json.dumps(obj)


def test_validation_error_has_no_truth_or_input_bearing_context():
    with pytest.raises(ValueError) as error:
        truth(scenario_id=CANARY)
    assert CANARY not in str(error.value)
    assert error.value.__context__ is None
    with pytest.raises(AttributeError):
        truth().extra = CANARY


def test_visible_validation_does_not_echo_rejected_private_input():
    from trinetra_eval.visible import VisibleManifestValidationError, VisibleScenarioManifest

    for operation in (
        lambda: VisibleScenarioManifest(scenario_id=CANARY),
        lambda: VisibleScenarioManifest.model_validate_json('{"scenario_id":"' + CANARY + '"}'),
    ):
        with pytest.raises(VisibleManifestValidationError) as error:
            operation()
        assert CANARY not in str(error.value)
        assert error.value.__context__ is None


def test_standard_getstate_hook_does_not_return_private_bytes():
    with pytest.raises(TypeError):
        truth().__getstate__()


def test_visible_round_trip_and_typeadapter_validation_are_safe():
    from pydantic import TypeAdapter

    from trinetra_eval.visible import VisibleManifestValidationError, VisibleScenarioManifest

    from .test_imp0399_visible import make

    record = make()
    assert VisibleScenarioManifest.model_validate_json(record.model_dump_json()) == record
    # Third-party raw parsers cannot participate in the sanitized public boundary.

    with pytest.raises(VisibleManifestValidationError):
        TypeAdapter(VisibleScenarioManifest)
    operations = (
        lambda: VisibleScenarioManifest.model_validate_strings({"scenario_id": CANARY}),
        lambda: VisibleScenarioManifest.model_validate({"scenario_id": CANARY}),
        lambda: VisibleScenarioManifest.model_validate(CANARY),
    )
    for operation in operations:
        with pytest.raises(VisibleManifestValidationError) as error:
            operation()
        assert CANARY not in str(error.value)
        assert error.value.__context__ is None


@pytest.mark.parametrize(
    "change",
    [
        {"evaluation_family": "ATTACK"},
        {"cause_class": "ACTIVE_EFFECT"},
        {"mutation_truth": {"mutation_state": "ACTIVE_EFFECT"}},
        {"behavioural_truth": {"behavioural_state": "APPLIED"}},
        {"expected_policy": {"expected_disposition": "SUPPORTED"}},
        {"evaluation_isolation_group_id": "not-an-eig"},
    ],
)
def test_hidden_record_composes_the_distinct_truth_contracts(change):
    with pytest.raises(ValueError):
        truth(**change)
