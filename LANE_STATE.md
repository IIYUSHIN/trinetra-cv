# Codex A — L-A-1

Branch: codex-a/A1-tiir-wire
Exact base: 2a5db1a39402d8285ce48da71cd3436bce0db91a

## Author-implemented packets

IMP-0284, IMP-0286, IMP-0287, IMP-0288, IMP-0289, IMP-0290,
IMP-0294, IMP-0295, IMP-0299, IMP-0300, IMP-0301, IMP-0302, IMP-0305.
These are independent of the unresolved ReceiptId/StreamId contract.
No independent acceptance; DOUBLE-CONTINGENT and OUT-OF-ORDER-CONTINGENT
on upstream module gates. No frozen Bible edits or new identifier semantics.

IMP-0284: fixed immutable profile and 20 registered typed errors; 15 focused tests,
2/2 mutants killed. Every later packet: RED recorded, focused GREEN, at least
one decisive mutant killed and restored, full seven-gate check and full pytest
before its atomic commit. Raw logs and commit bodies preserve actual outcomes.

## Held packets and requests

- IMP-0285: ERR-CL-016 PROPOSED. User confirms conflict; prefers separate typed
  identities and 16-byte CBOR, but does not ratify UUIDv4 constraints or opaque
  byte generation. No prefixed strings enter a signed payload by assumption.
- IMP-0291/0292/0293/0296/0297: missing authoritative I6 interfaces, specified in
  LANE_REQUESTS/L-A-1-002.md. No competing canonical records were invented.
- IMP-0303/0304: blocked on identifier resolution and canonical binding contracts.
- IMP-0298 is outside this lease and separately blocked on DetectionSet.

Open: L-A-1-001 / ERR-CL-016 PROPOSED; L-A-1-002 (provider request).
Private exact source passages and recommended REV are outside Git in
CLAUDE_ERR_CL_016.md, prepared for authorized transfer to Claude.
Next dependency-ready work: IMP-0285 after REV, IMP-0291..0293 and 0296..0297
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

Next five held packets: IMP-0285, IMP-0291, IMP-0292, IMP-0293, IMP-0296.

IMP-0295: implemented Inference config digest binding; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.
Final full validation after IMP-0295: 2207 passed, 4 skipped in 17.38s; seven quality gates PASS; both commands exit 0.
