# Codex A — L-A-1 completed implementation, review pending

Branch: codex-a/A1-tiir-wire
Original base: 2a5db1a39402d8285ce48da71cd3436bce0db91a
Imported principal-builder contracts: a2f616de64199370ccf71f8c7dfa11607ff4dd3b
(descends from pinned I3/DetectionSet hinge a0efce858971426da8a7e14242a3fdd652b60d16).
Published history preserved; no main/integration merge and no force push.

## Scope

All original 21 leased packets are implemented: IMP-0284..0297 and IMP-0299..0305.
IMP-0298 is supplied by the principal builder and reused; no competing semantic
output digest was authored. No next lease has been started.

Every Codex packet has test-first evidence, happy/negative/boundary cases,
deliberate mutation detected/restored, full seven-gate checks and full pytest
before its packet commit. Initial test-import and lint failures plus repairs are
retained in raw evidence. IMP-0288 has an additional committed security repair
bounding pending validation work for branching cycles.

All work remains DOUBLE-CONTINGENT / OUT-OF-ORDER-CONTINGENT. No independent
acceptance, formal ratification or complete I10 qualification is asserted.

## Contract decisions

- ERR-CL-019: provisional separate ReceiptId/StreamId; 122-bit UUIDv4 generation,
  length-only acceptance including non-v4; plain bstr16 signed values and distinct
  canonical lowercase JSON text. Earlier identifier number 016 was mistaken;
  016..018 belong to I3. Historical logs retain that numbering correction.
- ERR-CL-020: principal-builder DetectionSet/geometry contracts, imported unchanged.
- ERR-CL-021: principal builder corrected the earlier float64-CBOR instruction.
  semantic_output_digest uses versioned JCS content; receipt carries raw digest
  bytes and remains no-float CBOR. Model/ontology scope question remains an owner
  question in that provisional record, not a hidden claim of coverage.
- ERR-CL-023: delegated closed FLOAT32/NCHW/LITTLE_ENDIAN tensor vocabulary,
  exact bounded UTF-8 metadata text and explicit nested CBOR map assignments.
  See LANE_REQUESTS/L-A-1-004.md. PROPOSED, not ratified.

L-A-1-001 is provisionally implemented under 019. L-A-1-002 was clarified by the
principal builder: the missing producer records are not implementation blockers
for pure TIIR bindings; actual producer conformance remains NOT_RUN. L-A-1-003 is
resolved provisionally by the imported 0298 implementation under 021.

## Final validation

Tensor byte hash and semantic JCS hash are distinct; a Node-cross-checked tensor
project vector verifies the chosen preimage. Canonicalization preserves exact
float32 bits and rejects unsupported casts/reshapes/layouts/byte order/storage.
Model, runtime, preprocessing, config and raw/semantic output slots carry separate
binary identities. Distinct fixture values prevent swapped-slot tests from passing
accidentally. The payload parser closes every nested map and retains separate
encoding/schema/version failures; unobserved effective input cannot invent a hash.
An actual ephemeral Ed25519/COSE payload roundtrip and tampering negative execute;
a valid signature can coexist with a different independently expected model.
No global trust score or hardware attestation is introduced.

Baseline before new packets: full imported gate PASS after isolating an intermittent
/tmp/.git mount-placeholder interference from concurrent restrictive sandboxes.
The first attempt's one source-tree guard error is retained, not dismissed as PASS.
Use the supported escalated executor and PYTHONUNBUFFERED=1; no test guards weakened.
Other repairs: line-length/import formatting, explicit nullable memoryview shape
comparison, and a colocated test-fixture import (no shared pytest config changes).

## NOT_RUN / limitations

Actual model/runtime/RawAdapterOutputRecord producer conformance, persistent signer
IPC and key registry/lifecycle, receipt persistence and stateful ten-dimensional
verification/replay/restart beyond this wire lease, independent review, real CI,
formal acceptance, R1–R23, real models/datasets, GPU and offline second-host
qualification are NOT_RUN. Python 3.11 and three opt-in network checks are skipped;
two ownership/device-node negatives require root and are skipped, not passes.

Official Ed25519 and CBOR example bytes plus independent project vectors are used;
author tests do not by themselves establish cryptographic correctness. No real
signing keys, private evaluation truth or private source packs are committed.

Next dependency-ready work: principal-builder review of this lease and ERR-CL-023;
independent replay/falsification, then the next lease only when assigned by Claude.

IMP-0304: implemented TIIR payload validation; provisional under ERR-CL-023.
RED recorded before implementation. Mutation 1/1 killed and restored.
Final full validation after IMP-0304: 2954 passed, 6 skipped in 36.11s; seven quality gates PASS; both commands exit 0.
