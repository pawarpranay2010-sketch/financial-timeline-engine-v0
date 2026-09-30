# Phase 5J — Reconnaissance Record (written before any edit)

Branch `main` @ `208d5e4`. Pre-existing modifications (12) and untracked
files (76) recorded in the session and preserved; nothing reset, cleaned,
or reverted.

## Constraints derived from the code (not invented)

| # | Constraint | Evidence | Consequence for 5J |
|---|---|---|---|
| C1 | Admission reserves **exactly one** unit per call | `backend/auth/admission.py::admit` → `gate.authorize_request` | Bulk needs an atomic **multi-unit** reservation, not N calls |
| C2 | Quota reservation is one `UPDATE` whose WHERE clause carries the full predicate; refusal writes nothing | `backend/auth/gate.py::reserve_unit` (`usage < limit`, stale-bucket rollover) | Multi-unit is a safe extension: `usage + N <= limit` in the SAME statement → all-or-nothing, no partial batch |
| C3 | The guard authenticates only; reservation happens in the billable handler | Phase 5I §2 flow | Bulk must call `admit()` exactly once, after the idempotency claim |
| C4 | Idempotency persists only `request_hash` (SHA-256) — never the body — plus the returned envelope; PK `(tenant_id, key_hash)`; `ON CONFLICT` is the concurrency arbiter; 72 h retention | `backend/auth/idempotency.py::claim/complete` | Bulk reuses the mechanism with a new `endpoint` string + ordered-items fingerprint. **No schema change, no second idempotency system** |
| C5 | Canonical single-item execution is `client.process(raw_input, request_id=rid)` (public facade → Kernel, exactly once) + `build_process_result(...)` | `api/routes/developer.py::_process_v1_idempotent` | Extract ONE shared helper; bulk must not copy accounting/grounding logic |
| C6 | Item input cap is `raw_input: str, min_length=1, max_length=2000` | `api/schemas.py::KernelProcessRequest` | Reuse that model per item so limits/errors match the single-item path |
| C7 | Body guard for `/v1/*` (non-documents) is **64 KiB**; only `/v1/documents` gets 16 MiB | `api/main.py::_developer_body_size_guard` | Bulk inherits 64 KiB and the existing `413 REQUEST_TOO_LARGE` envelope |
| C8 | In-process provider admission is a `BoundedSemaphore(DEFAULT_MAX_INFLIGHT=4)` plus a generation lock; remote transport has no in-process cap | `backend/model_provider/llamacpp_local.py`, `remote_hf.py` | **Sequential** in-request execution — no fan-out, ordering preserved for free, no new contention |
| C9 | Remote timeouts are 60 s (http) / 120 s (gradio) | `DEPLOYMENT.md` env table | N sequential calls can exceed a gateway timeout → an explicit, documented wall-clock budget with a non-silent "not attempted" result |
| C10 | Six-state vocabulary is closed and owned by `api/status.py` | Phase 5A | Batch reuses the six states; no new competing contract |
| C11 | No bulk route, schema, or test exists anywhere | `grep -rn "process/bulk\|BulkRequest"` → none | Greenfield endpoint, greenfield suite |
| C12 | Trust boundary: `backend/kernel`, `backend/rules`, `backend/grounding`, `backend/maths`, `core`, `formula_engine`, `platrixa` | Phase 5I precedent | Untouched; bulk orchestrates only |

## Naming/limit decisions (derived, not invented)

* Path: `POST /v1/process/bulk` — the existing route is `POST /v1/process`, so
  the bulk sibling follows the established convention.
* `MAX_BATCH_ITEMS = 25`: far enough under the 64 KiB body guard (C7) that
  normal batches always fit, and bounded so a single request cannot fan out
  into an unbounded amount of provider work. A pathological all-max-size batch
  is refused cleanly by the existing 413 guard.
* Sequential execution (C8) — not a claim about throughput, which is not
  measured here.
* Quota: **one unit per item, reserved all-or-nothing** (C2). Refusal ⇒ 429 and
  zero units consumed.
* `BULK_TIME_BUDGET_SECONDS` default 120 (C9): items not attempted because the
  budget elapsed get an explicit retryable result — never a silent omission.
