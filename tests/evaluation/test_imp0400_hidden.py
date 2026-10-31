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
        "mutation_truth": {},
        "behavioural_truth": {},
        "generation_truth": {"canary": CANARY},
        "affected_entity_truth": (),
        "benign_entity_truth": (),
        "expected_method_targets": (),
        "expected_claims": (),
        "expected_coverage": (),
        "expected_policy": {},
        "expected_scope": {},
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
    assert TypeAdapter(VisibleScenarioManifest).validate_json(record.model_dump_json()) == record
    operations = (
        lambda: VisibleScenarioManifest.model_validate_strings({"scenario_id": CANARY}),
        lambda: TypeAdapter(VisibleScenarioManifest).validate_python({"scenario_id": CANARY}),
        lambda: TypeAdapter(VisibleScenarioManifest).validate_python(CANARY),
    )
    for operation in operations:
        with pytest.raises(VisibleManifestValidationError) as error:
            operation()
        assert CANARY not in str(error.value)
        assert error.value.__context__ is None
