# Codex A — L-A-1

Branch: codex-a/A1-tiir-wire
Exact base: 2a5db1a39402d8285ce48da71cd3436bce0db91a

## Author-implemented packets

IMP-0284, IMP-0285, IMP-0286, IMP-0287, IMP-0288, IMP-0289, IMP-0290,
IMP-0294, IMP-0295, IMP-0299, IMP-0300, IMP-0301, IMP-0302, IMP-0305.
IMP-0285 is provisional under the principal-builder ERR-CL-019 resolution.
No independent acceptance; DOUBLE-CONTINGENT and OUT-OF-ORDER-CONTINGENT
on upstream module gates. No frozen Bible edits or new identifier semantics.

IMP-0284: fixed immutable profile and 20 registered typed errors; 15 focused tests,
2/2 mutants killed. Every later packet: RED recorded, focused GREEN, at least
one decisive mutant killed and restored, full seven-gate check and full pytest
before its atomic commit. Raw logs and commit bodies preserve actual outcomes.

## Held packets and requests

- IMP-0285 is provisionally implemented, not held. Earlier local ERR-CL-016
  identifier references are corrected to ERR-CL-019; 016..018 belong to I3.
- IMP-0291/0292/0293/0296/0297: missing authoritative I6 interfaces, specified in
  LANE_REQUESTS/L-A-1-002.md. No competing canonical records were invented.
- IMP-0303/0304: blocked on identifier resolution and canonical binding contracts.
- IMP-0298 is outside this lease and separately blocked on DetectionSet.

Open: L-A-1-001 / ERR-CL-019 PROPOSED; L-A-1-002 (provider request).
Private exact source passages and recommended REV are outside Git in
CLAUDE_ERR_CL_019_UPDATE.md, prepared for authorized transfer to Claude.
Next dependency-ready work: IMP-0291..0293 and 0296..0297
after provider contracts, then IMP-0303/0304. No next lease started.

## Validation and repairs

Baseline: Python 3.12.14; uv 0.12.23; uv sync --frozen --extra persistence --extra
crypto passed (27 installed). Corrected baseline 2023 passed, 4 skipped (exit 0);
all seven quality gates PASS (exit 0).
The restricted executor masks /tmp/.git and denies local sockets and uv cache
writes: initial suite 38 failed, 1744 passed, 6 skipped, 239 errors (exit 1).
Supported escalated execution removes that interference. Buffered execution then
had 1 failed, 2022 passed, 4 skipped: the os._exit(9) child lost its stdout marker.
PYTHONUNBUFFERED=1 fixes it with no test/dependency changes.

Meaningful repairs: import ordering; exact object-boundary typing for requested
purposes; a closed Literal purpose profile avoids inventing a domain state enum.
A proposed shared enum catalog entry was removed before commit when its domain-only
collector correctly rejected it; shared tests/configuration were not changed.
The mutation harness deletes only the mutated module's generated .pyc before each
run to prevent same-size, same-second cached mutants surviving source restoration.
Failed attempts and repairs remain in execution logs; assertions were preserved.
One early IMP-0284 human-summary gate failed pytest without retained detail;
subsequent standalone and detailed runs passed, intermediate cause unestablished.

Independent vectors: RFC 8032 public verification vector from OpenSSL's maintained
RFC8032 collection; RFC CBOR Appendix A exact bytes from cbor/test-vectors. No
private test keys are committed. Generated throwaway keys are test-local.
COSE fixed-byte Signature1 tests follow RFC 9052 structure; full external COSE
interoperability is NOT_RUN. Profile checks are not cryptographic acceptance.

## NOT_RUN and boundaries

Python 3.11 unavailable; three opt-in network checks skipped. Held packets,
I6 producer-to-binding conformance, persistent key registry/lifecycle, signer IPC
complete-frame limit (IMP-0306), receipt issuance/stateful ten-dimension verifier,
real CI, independent review, I10 acceptance, R1–R23, real model/dataset qualification
and hardware attestation are NOT_RUN. No overall trust score was introduced.
Private packs, research excerpts, and raw logs are outside Git.

Next five held packets: IMP-0291, IMP-0292, IMP-0293, IMP-0296, IMP-0297.

IMP-0295: implemented Inference config digest binding; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.
Final full validation after IMP-0295: 2207 passed, 4 skipped in 17.38s; seven quality gates PASS; both commands exit 0.

Security follow-up for IMP-0288: final review found that branching cycles could
expand the pending-work stack faster than the visited-node budget. A measured
branching-cycle regression failed (1 failed, 52 passed, exit 1). Include pending
nodes in the lower-bound byte budget, keeping stack growth bounded. Add an
untrusted CBOR shared-reference cycle negative. Preserve the published packet
commit; deliver the repair as a separate atomic IMP-0288 follow-up.

IMP-0288: implemented Bound CBOR pending validation work; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.
Final full validation after IMP-0288: 2209 passed, 4 skipped in 17.35s; seven quality gates PASS; both commands exit 0.

IMP-0285: 50 focused tests pass; six distinct mutants killed and restored:
version mask, variant mask, length, cross-type equality, lowercase parsing, and
incorrect version-gated acceptance. Generated identities have 122 random bits;
non-v4 16-byte values remain accepted. JSON/text and signed CBOR are distinct.

Hinge handoff inspected: archive SHA-256
596d1d2aa5f7cc1a4a22423824e3594521c86a6ca526ee00098f61c489208ce2.
It contains documents, not patches. Actual fetched principal-builder Git commit:
a0efce858971426da8a7e14242a3fdd652b60d16 (merged below). It supplies I3 and
DetectionSet/geometry/dataset contracts, not tensor/model/runtime/raw-output types.
ERR-CL-020 remains PROPOSED. Semantic digest discrepancy is ERR-CL-021 PROPOSED;
see LANE_REQUESTS/L-A-1-003.md. No new float CBOR profile is implemented locally.

IMP-0285 full validation: 2259 passed, 4 skipped (exit 0); all seven gates PASS
(exit 0). Affected existing components 0285/0288/0299/0300/0305: 142 passed,
exit 0. Full payload/persistence/replay retests remain NOT_RUN, not inferred.

Principal-builder integration: feature-branch merge of exact hinge
 a0efce858971426da8a7e14242a3fdd652b60d16, preserving published TIIR history.
I3 and DetectionSet/geometry/dataset files originate from Claude's commits; Codex
has not authored replacements in shared directories. Golden error manifest merged
automatically, retaining both owners' entries. No dependency/CI changes involved.
The hinge supplies DetectionSet but not the outstanding tensor/model/runtime/raw
producer contracts. ERR-CL-020 remains PROPOSED, no independent acceptance.

Pinned hinge integration validated: 2831 passed, 6 skipped in the full suite,
exit 0; all seven quality gates PASS, exit 0; hinge plus TIIR component run:
513 passed, exit 0. Additional skips are ownership/device-node negatives needing
root, not passes. Shared domain/adapters/infrastructure/CI/dependency files are
byte-identical to the principal-builder parent; the error catalog is the sorted
union of both parents. This integration is not independent module acceptance.
IMP-0298 remains held on ERR-CL-021's semantic digest substrate/content decision,
not on absence of the now-available DetectionSet types. Other provider requests
in L-A-1-002 remain open. Seven packets of the original 21 remain held.

IMP-0291: implemented Effective input TensorDigest; provisional under ERR-CL-023.
RED recorded before implementation. Mutation 1/1 killed and restored.
Final full validation after IMP-0291: 2869 passed, 6 skipped in 36.01s; seven quality gates PASS; both commands exit 0.
