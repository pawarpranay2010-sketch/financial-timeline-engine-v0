# Phase 5J — Bulk API

**Status: `READY_FOR_INTEGRATION_TESTING`**

Not production-ready: no live deployment, no real PostgreSQL, and no real
model provider were exercised. Every result below comes from in-process
suites with stubbed persistence and a stubbed engine. See *Known
limitations*.

---

## 1. Endpoint

```
POST /v1/process/bulk
```

Same `/v1/*` conventions as the rest of the surface. Registered behind the
existing Phase 5I guard `dependencies=[Depends(_metered_api_key_guard)]`
(proof: `fte_fyjc_66` G1c, which fails if the dependency is removed).

### Request

```json
{
  "items": [
    { "item_id": "inv-1", "raw_input": "Total revenue 120,000 EUR for Q3" },
    { "item_id": "inv-2", "raw_input": "Rent 2,400 EUR payable 2026-10-01" }
  ]
}
```

`item_id` is optional (`[A-Za-z0-9._:-]{1,64}`), echoed verbatim; when absent
the server assigns a deterministic `item_000`, `item_001`, … **Item ids are
correlation labels, not idempotency keys** — replay safety for the whole
batch comes only from the `Idempotency-Key` header.

`raw_input` uses the identical contract as single-item
`KernelProcessRequest` (1–2000 chars). The batch layer never repairs,
trims, or reinterprets an item's input.

### Response (200)

```json
{
  "api_version": "v1",
  "request_id": "corr-1",
  "batch_id": "bat_9f3c…",
  "status": "FAILED",
  "status_label": "Failed",
  "api_status": "FAILED",
  "api_status_label": "Failed",
  "engine_status": null,
  "success": false,
  "retryable": false,
  "reason_codes": ["MIXED_ITEM_OUTCOMES"],
  "next_action": "see reason_codes; retry only if the code is retryable",
  "total_items": 2,
  "attempted_items": 2,
  "not_attempted_items": 0,
  "partial_success": true,
  "counts": { "VERIFIED": 1, "REVIEW_REQUIRED": 1 },
  "counts_by_engine_status": {
    "VERIFIED": 1,
    "REVIEW_REQUIRED": 1
  },
  "quota": {
    "units_reserved": 2,
    "policy": "one unit per item, reserved atomically for the whole batch (all-or-nothing)"
  },
  "results": [ /* one entry per item, INPUT ORDER, always all N */ ],
  "metadata": { "engine_status": null, "processing_time_ms": 812, "…": "…" }
}
```

Each entry in `results`:

```json
{
  "index": 0,
  "item_id": "inv-1",
  "attempted": true,
  "request_id": "corr-1-000",
  "result": { /* verbatim Phase 5D envelope, or null */ },
  "error":  null,                 /* canonical error envelope, or null */
  "api_status": "VERIFIED",
  "engine_status": "VERIFIED",
  "status": "VERIFIED",
  "reason_codes": []
}
```

`result` is the **unmodified** `build_process_result` envelope — same
evidence, confidence, capability ids and lineage the single-item route
returns. Nothing is re-serialized or rewritten.

---

## 2. Batch-level vs item-level status

The batch reuses the **existing** six-state public contract
(`api/status.py`). There is no competing vocabulary. Aggregation
(`_bulk_aggregate_status`) is deterministic and fail-closed:

| Condition | Batch `api_status` |
|---|---|
| any item non-terminal (not attempted / PROCESSING) | `PROCESSING` |
| every item `VERIFIED` | `VERIFIED` |
| all items terminal, all the same non-VERIFIED state | that state |
| all items terminal, mixed | `FAILED` (`MIXED_ITEM_OUTCOMES`) |

A batch is **never** `VERIFIED` because *some* item verified, and
`REVIEW_REQUIRED` / `UNSUPPORTED` are never reported as success. Item
`engine_status` stays the engine's verbatim state (e.g.
`UNSUPPORTED_TRANSACTION`), keeping the taxonomy intact.

`engine_status` is `null` at batch level on purpose: the batch has no engine
verdict of its own, and inventing one would fabricate a state.

`partial_success` is true when the batch contains anything other than
all-VERIFIED — an explicit mixed-outcome signal, not a success flag.

---

## 3. Limits and why

| Limit | Value | Rationale |
|---|---|---|
| `MAX_BATCH_ITEMS` | **25** | Fits the existing 64 KiB `/v1` body guard with headroom for 25 × 2000-char items, and keeps one request inside the platform's ~30 s synchronous ceiling. Larger batches belong in Phase 5K's durable async path. |
| per-item `raw_input` | 2000 chars | Identical to the single-item contract — no new limit invented. |
| body size | 64 KiB (existing guard) | Unchanged; a larger batch is refused `413 REQUEST_TOO_LARGE` before admission. |
| concurrency | **sequential, in input order** | The provider is a single shared endpoint. Parallel fan-out would multiply concurrent model calls against it and break the 5I engine/pool budget. Bounded, predictable, no task graph. |
| elapsed-time budget | 120 s (`PLATRIXA_BULK_TIME_BUDGET_SECONDS`) | Bounds one request's occupancy. On expiry the remaining items are returned **explicitly** as not-attempted. |
| consecutive infra failures | 2 | After two consecutive provider failures the batch stops — every later item would hit the same outage and would burn the tenant's reserved quota. |

No throughput or latency improvement is claimed. No benchmark supports one.

---

## 4. Quota and billing

**Policy: one unit per item, reserved atomically for the whole batch,
all-or-nothing.**

`gate.reserve_units(provided_key, units)` extends the existing
single-statement predicate rather than looping `reserve_unit`. It remains
ONE `UPDATE` whose `WHERE` clause carries the complete admission predicate:

```
hash matches AND is_active AND
( same-month bucket AND usage + units <= limit
  OR stale month bucket (rollover, and only when units itself fits) )
```

PostgreSQL serializes concurrent `UPDATE`s on the same primary-key row, so
`concurrent accepted reservations <= monthly_limit` holds and **a batch is
never partially reserved**. No read-modify-write race, no retry loop, no
post-hoc compensation, no partial charge.

- A batch that does not fit the remaining quota is refused whole:
  `429 QUOTA_EXHAUSTED`, **zero units consumed**, zero pipeline calls
  (suite C4/C5).
- Authentication failure consumes nothing (C9/C10).
- Rate limiting and malformed input are rejected before admission, so they
  are free (A12–A19).
- An idempotent replay never recharges — the claim happens *before* the
  reservation (D3).

`reserve_unit(key)` remains as the named single-unit wrapper, so every
non-bulk route is unaffected.

**The choke point is unchanged.** The guard still authenticates only; the
one `admission_boundary.admit(..., units=len(items))` call lives in the
handler. No route and no shared helper both reserve (C1, C7, C8). A static
check asserts the bulk handler body contains exactly one `admit(` call and
never calls the gate directly.

---

## 5. Idempotency

Reuses the existing Phase 5C store — no second idempotency system, no schema
change, no migration.

- The fingerprint is the **ordered** item list (`item_id`, `raw_input`).
  `canonical_fingerprint` serializes lists in order, so reordering the same
  items is a *different* canonical request and yields `409`, exactly like
  the single-item contract (D7).
- Replay returns the stored envelope verbatim with
  `Idempotent-Replayed: true` and a hashed key prefix (D1/D4).
- Conflict → `409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST`, charging
  nothing extra (D5/D6).
- Claim precedes reservation, so a replay never charges (D3).
- Concurrent duplicates resolve to a single winner; the loser gets `200
  PROCESSING` + `Retry-After: 2` and never executes (D8–D10).
- The claim is released on any downstream refusal or failure.

Item ids deliberately do **not** participate in deduplication — they are
labels, and treating them as keys would be a second, weaker contract.

---

## 6. Partial success and error behaviour

Every item gets a result. Nothing is omitted, in any case:

- An item that raises an unexpected exception is caught at the **bulk call
  site** (not inside `_execute_single_item`, so single-item 500 semantics are
  byte-identical) and reported as that item's `INTERNAL_ERROR` →
  `FAILED`. Other items are unaffected (B8).
- An input-invalid item does not suppress its neighbours (B4).
- Infrastructure failure is **never** downgraded to `INVALID_INPUT`
  (B9). Provider/infra failure → `PROCESSING`, retryable.
- After 2 consecutive infra failures the remainder is marked
  `attempted: false` with the reason (B10/B11), and the batch is
  `PROCESSING`/retryable rather than `FAILED`.

New error codes, all mapped in `api/status.py` to existing six-state values:

| Code | Public status | Meaning |
|---|---|---|
| `BATCH_EMPTY` | INVALID_INPUT | empty `items` |
| `BATCH_TOO_LARGE` | INVALID_INPUT | > `MAX_BATCH_ITEMS` |
| `BATCH_DUPLICATE_ITEM_ID` | INVALID_INPUT | repeated `item_id` |
| `BATCH_ITEM_INVALID` / `BATCH_ITEM_ID_INVALID` | INVALID_INPUT | malformed item |
| `RESULT_PENDING` | **PROCESSING** | item not attempted (budget/provider) |
| `INTERNAL_ERROR` | **FAILED** | unexpected item-level failure |

`RESULT_PENDING` and `INTERNAL_ERROR` are **fixes**, not additions: without
them, unmapped codes fall through `public_status_for_error_code` to
`FAILED`, so a budget-short-circuited batch was misreported as a failure
instead of the retryable state it is (caught by E5).

The Phase 5I canonical envelope is preserved for all batch-level
rejections (F7).

---

## 7. Observability and retention

- The batch writes one request-log row at `/v1/process/bulk` (F1).
- Each **non-VERIFIED** item writes its own row at
  `/v1/process/bulk(item)` with a deterministic item request id
  `<request id>-<NNN>` (F2/F3). Verified items are not recorded, to avoid
  flooding the history surface with successes.
- No raw financial payload is written (F4) and no full API key is logged —
  only the bounded 12-character identification prefix introduced in 5I
  (F6). The prefix is asserted to be `≤12` and never to contain the full
  key.
- No tenant identity is echoed in the response (F10/C12). The `quota.mode`
  field was removed from the response for exactly this reason.
- Retention is unchanged from Phase 5H — bulk introduces no new store and no
  new persisted raw data. Documents are still not persisted by this route.

---

## 8. Security and tenant isolation

- **Trust boundary untouched.** `git diff` over `backend/kernel`,
  `backend/rules`, `backend/grounding`, `backend/maths`, `core`,
  `formula_engine`, `platrixa` is **empty**. Bulk calls the same public
  interface (`client.process`) that `/v1/process` calls; there is no second
  accounting path, no relaxed schema verification, no alternative route to
  `VERIFIED`.
- Authentication precedes processing via the shared 5I guard and the single
  admission choke point; unauthenticated bulk is refused `401` (C9).
- Tenant identity end-to-end; two keys resolve to distinct tenants (C11),
  and one tenant's request cannot read or bill another's (C12).
- No secret or raw payload in responses or logs (C13/F4/F6).
- Body-size and rate-limit middleware apply unchanged, before the route.

`VERIFIED` continues to mean "this input was validated by the deterministic
pipeline", not tax, legal, regulatory, or accounting compliance.

---

## 9. Tests

All executed against the final tree; commands are the exact invocations.

| Suite | Result |
|---|---|
| `fte_fyjc_87_phase5j_bulk_api_test.py` | **76/76** (×3 runs) |
| `fte_fyjc_85_phase5i_admission_edge_test.py` | 70/70 |
| `fte_fyjc_86_phase5i_proxy_contract_test.py` | 11/11 |
| `fte_fyjc_77` (6A six-state) | 53/53 |
| `fte_fyjc_78` (5B capabilities) | 50/50 |
| `fte_fyjc_79` (5C idempotency) | 59/59 |
| `fte_fyjc_80` (5D result contract) | 54/54 |
| `fte_fyjc_81` (5E async documents) | 52/52 |
| `fte_fyjc_82` (5F OpenAPI) | 26/26 |
| `fte_fyjc_83` (5G API keys) | 44/44 |
| `fte_fyjc_84` (5H observability) | 45/45 |
| `fte_fyjc_66_metered_gate_test.py` | 53/53 (×2 runs) |
| `fte_sec_01_grounding_fail_closed` | 24/24 |
| `fte_sec_02_api_authz_admission` | 19/19 |
| `fte_sec_03_webhook_ssrf_seal` | 24/24 |
| `fte_sec_04_error_disclosure` | 16/16 |
| `fte_sec_05_dependency_policy` | 20/20 |
| `security_baseline_test.py` | 16/16 "BASELINE HOLDS" |
| `fte_modal_resource_profile_test.py` | 68/68 |

### Anti-vacuity

The suite was proven non-vacuous by mutating the implementation and
confirming the suite fails. Each mutation was reverted immediately.

| Mutation | Outcome |
|---|---|
| Aggregate upgrades a mixed batch to `VERIFIED` | caught (A8) |
| Reservation charged 0 units for a batch | caught (A16) |
| Item exception allowed to propagate | caught (B8 collapses the batch) |
| Bulk replay re-executes | caught (D1, D2, D3, D4) |
| `fte_fyjc_66` G1c with the guard removed | caught (G1c) |

Two assertions were corrected rather than weakened, because the originals
contradicted the shipped contract:
- C7 measured the wrong source region (split on a path literal that appears
  in a docstring) — re-anchored on the handler body.
- F6 asserted "no key prefix at all", which contradicts 5I's deliberate
  bounded-prefix design — now asserts *no full key* and prefix `≤12`.

### Suites updated (contract genuinely moved, not to force green)

- `fte_fyjc_85` / `fte_fyjc_66`: their gate stubs replaced
  `authorize_request`, which 5J no longer calls on the bulk path; they now
  stub `authorize_units` (and alias `authorize_request` to it). Counting
  semantics and every assertion are unchanged.
- `fte_fyjc_66` G1: the anti-bypass allowlist gained `/v1/process/bulk` as a
  third permitted processing route, and a **new** G1c asserts bulk is behind
  the guard (proven non-vacuous).

---

## 10. Known limitations

- No live deployment, real PostgreSQL, or real provider was exercised. The
  atomicity argument for `reserve_units` rests on PostgreSQL row-level
  `UPDATE` serialization, verified by reading the SQL — not by a concurrency
  test against a real database.
- Synchronous only. A batch that cannot finish inside the 120 s budget
  returns explicit not-attempted items; there is no durable resume.
- Sequential execution: no throughput gain over N single calls, by design.
- `MAX_BATCH_ITEMS = 25` and the 120 s budget are reasoned defaults, not
  measured against production traffic.
- Per-item request-log rows exist only for non-VERIFIED items; a fully
  verified batch's individual items are traceable only via the response.

## 11. Deferred to later phases

- **5K** — durable queue/worker, lease renewal, worker startup recovery.
  A batch that outlives its request currently cannot be resumed.
- **5K/5L** — asynchronous bulk submission (submit → job → result → webhook).
  This phase is synchronous by design.
- **Audit findings from the 5I report remain open**: webhook delivery and
  `document.processing` reachability, webhook retry, document retention,
  per-key usage, pool sizing, and the unmeasured model-transport gates
  (llama.cpp/GGUF/Modal/HF ZeroGPU — untouched here).
- Bulk-specific webhooks: not implemented. The endpoint is synchronous, so
  there is nothing to notify about.

---

## 12. Files changed

| File | Change |
|---|---|
| `api/routes/developer.py` | `POST /v1/process/bulk`; `_execute_single_item` extracted and now shared by `/v1/process` and bulk; bulk limits, aggregation, quota call, per-item isolation, observability |
| `api/schemas.py` | `BulkItem`, `BulkProcessRequest`, `DeveloperBulkResultEnvelope` |
| `api/status.py` | bulk transport codes; `RESULT_PENDING` → PROCESSING; `INTERNAL_ERROR` → FAILED |
| `backend/auth/gate.py` | `reserve_units(key, units)` atomic multi-unit reservation; `reserve_unit`/`authorize_request` as wrappers |
| `backend/auth/admission.py` | `admit(..., units=N)` |
| `scripts/fte_fyjc_87_phase5j_bulk_api_test.py` | **new** — 76 checks |
| `scripts/fte_fyjc_85`, `fte_fyjc_66` | stub entry point + anti-bypass allowlist |
| `api/routes/developer.py` (`/v1/process`) | routed through the shared `_execute_single_item`; behaviour verified unchanged (A20) |

Untouched: kernel, rules, grounding, maths, `core`, `formula_engine`,
`platrixa`, the async worker, and the webhook plane.
