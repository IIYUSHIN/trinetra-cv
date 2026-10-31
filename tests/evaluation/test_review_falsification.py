"""Independent final-review boundary repros; only synthetic canaries/artifacts."""

import base64
import io
import json
import zipfile

import pytest
from pydantic import TypeAdapter

from trinetra_eval.canary import LeakageCanary
from trinetra_eval.production_firewall import production_firewall_violations
from trinetra_eval.release_scan import LeakageCanaryViolation, assert_no_leakage
from trinetra_eval.visible import VisibleManifestValidationError, VisibleScenarioManifest


def test_foreign_json_parser_cannot_echo_rejected_private_input():
    marker = "SYNTHETIC_REVIEW_PRIVATE_CANARY"
    with pytest.raises(VisibleManifestValidationError) as error:
        TypeAdapter(VisibleScenarioManifest).validate_json('{"scenario_id":"' + marker + '",')
    assert marker not in str(error.value)
    if hasattr(error.value, "errors"):
        assert marker not in json.dumps(error.value.errors())


def test_prefixed_but_valid_zip_is_scanned(tmp_path):
    canary = LeakageCanary()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("record.txt", canary.evaluator_private_token())
    payload = b"#!/bin/sh\n" + stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.read("record.txt") == canary.evaluator_private_token()
    artifact = tmp_path / "launcher.bundle"
    artifact.write_bytes(payload)
    with pytest.raises(LeakageCanaryViolation):
        assert_no_leakage((artifact,), (canary,))


def test_base64_container_alignment_does_not_hide_canary(tmp_path):
    canary = LeakageCanary(lambda _n: bytes(range(16)))
    artifact = tmp_path / "document.b64"
    artifact.write_bytes(
        base64.b64encode(json.dumps({"x": canary.evaluator_private_token().decode()}).encode())
    )
    with pytest.raises(LeakageCanaryViolation):
        assert_no_leakage((artifact,), (canary,))


def test_overwritten_duplicate_json_member_is_still_scanned(tmp_path):
    canary = LeakageCanary()
    escaped = canary.evaluator_private_token().replace(b"TF-", b"\\u0054F-")
    artifact = tmp_path / "duplicate.json"
    artifact.write_bytes(b'{"x":"' + escaped + b'","x":"clean"}')
    with pytest.raises(LeakageCanaryViolation):
        assert_no_leakage((artifact,), (canary,))


def test_unreadable_source_subtree_is_not_a_clean_firewall_result(tmp_path):
    package = tmp_path / "product"
    hidden = package / "denied"
    hidden.mkdir(parents=True)
    (hidden / "module.py").write_text("import trinetra_eval")
    hidden.chmod(0)
    try:
        assert production_firewall_violations(package)
    finally:
        hidden.chmod(0o700)


def test_foreign_adapter_configuration_cannot_reenable_raw_json_leaks():
    marker = "SYNTHETIC_REVIEW_PRIVATE_CANARY"
    with pytest.raises(VisibleManifestValidationError) as error:
        TypeAdapter(VisibleScenarioManifest, config={"arbitrary_types_allowed": True}).validate_json(
            '{"scenario_id":"' + marker + '",'
        )
    assert marker not in str(error.value)
