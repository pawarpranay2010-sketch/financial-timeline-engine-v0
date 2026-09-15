# PLATRIXA — PHASE 16: METERED GATE REPORT

**Date:** 2026-09-14
**Phase:** 16 — Multi-Tenant Developer Authentication + Quota Metering
**Classification:** ✅ **PASS** (51/51 evidence checks; 55/55 + 47/47 regressions)
**Evidence suite:** `scripts/fte_fyjc_66_metered_gate_test.py` (embedded real PostgreSQL)

---

## 1. Objective (restated)

Implement the authentication/resource-abuse boundary identified as PARTIAL
in the Phase 15 audit:

```
Developer request
    ↓
API-key authentication
    ↓
tenant resolution
    ↓
atomic quota check + reservation
    ↓
existing Phase 11 public/developer interface
    ↓
existing Kernel
    ↓
existing deterministic financial runtime
```

The metering gate is an enforcement boundary ONLY. It is not part of the
accounting engine and contains no financial logic.

## 2. What was implemented

### 2.1 `backend/auth/` — the admission-control boundary package

| File | Role |
|---|---|
| `gate.py` | HTTP-agnostic admission control: key hashing → tenant lookup → ATOMIC quota reservation → `TenantContext`. Reports reason codes (`OK`, `MISSING_KEY`, `UNKNOWN_KEY`, `INACTIVE`, `QUOTA_EXHAUSTED`, `METERING_UNAVAILABLE`); never touches the Kernel, model, accounting, grounding, or rules. |
| `models.py` | `TenantQuota` on the existing declarative `Base` (`backend/database/db.py`) — table `platrixa_tenant_quotas`, keyed by `api_key_hash` (SHA-256 hex, primary key, indexed). `usage_month` is a deterministic UTC `YYYY-MM` bucket, so August usage can never be counted as September usage. |
| `tokens.py` | `hash_token()` — deterministic SHA-256 of the whitespace-stripped key. The raw key never reaches the database, logs, responses, or errors. Honest security note included: fast-hash storage protects against casual exposure, not against a database attacker who also knows the key format — issued keys must be high-entropy (the seed tool generates 32-byte URL-safe tokens). |
| `init_metering.py` | Idempotent DDL init (repo convention: version-controlled SQL + init script; no Alembic exists). |
| `dev_seed_tenant.py` | Dev/test tenant seeding; prints the raw key exactly once, stores only the hash. Refuses to overwrite an existing hash. Not a production provisioning tool. |

### 2.2 Atomic quota reservation (the core design)

The reservation is ONE `UPDATE` whose `WHERE` clause carries the complete
admission predicate:

```
hash matches AND is_active AND
  ( same-month bucket AND usage < limit
    OR stale month bucket → rollover )
```

- PostgreSQL row-level locking on the primary key serializes concurrent
  UPDATEs → accepted reservations ≤ `monthly_limit` under arbitrary
  concurrency. No read-increment-write race, no retries, no compensation.
- Rollover happens INSIDE the same statement (`usage = 1` on bucket
  change) — no background scheduler.
- Zero rows updated ⇒ nothing written: authentication failures and quota
  failures consume zero quota.

### 2.3 HTTP wiring (`api/routes/developer.py`)

`/v1/process` gained a FastAPI dependency (`_metered_api_key_guard`) that
runs, in order: Phase 15 single-key gate → metered gate (when configured).
Gate rejections render through the existing deterministic `/v1` error
envelope (401/429/503 with `X-Platrixa-Error`), scoped to `/v1` so the
browser-facing `/api/v1` contract is untouched. Rejection happens strictly
before the public interface is resolved — a rejected request can never
load the model.

## 3. Boundary verification (evidence suite, sections A–H)

Backend: **real embedded PostgreSQL** (`pgserver`) — the concurrency and
atomicity proofs would be meaningless against SQLite or mocks.

- **A — Authentication matrix:** missing/unknown/deactivated/empty key →
  401 with 0 Kernel calls; valid key → 200, Kernel invoked exactly once,
  exactly one unit reserved; no key/hash material in any response.
- **B — Quota semantics:** under-limit 200s increment usage; exactly-at-limit →
  429 `QUOTA_EXHAUSTED` without reaching the Kernel; auth failures consume 0;
  an admitted request that later fails domain validation keeps its
  reservation (the unit paid for admission — documented policy).
- **C — Month boundary:** stale-bucket exhausted row (`2026-08`, 2/2) admits
  on first new-month request, bucket transitions with usage reset to
  exactly 1 in the same atomic statement; same-month exhausted row stays
  exhausted; `resolve_tenant` reports bucket usage consuming nothing.
- **D — Tenant isolation:** independent quotas; A's exhaustion never touches
  B; row modifications touch only their own row; client-supplied
  `tenant_id` cannot select another tenant (identity always comes from the
  credential hash).
- **E — Fail closed:** metering store unavailable → 503
  `METERING_UNAVAILABLE`, 0 Kernel calls; `authorize_request` never raises.
- **F — Secret hygiene:** deterministic 64-char lowercase hex hashes;
  whitespace stripping cannot mint second identities; raw key never
  persisted, never in responses, never in logs.
- **G — No bypass / authority anchors:** `/v1/process` is the sole versioned
  processing endpoint (route walker handles FastAPI ≥0.141
  `_IncludedRouter` wrapping); route still flows through the public
  interface; no kernel/provider/persistence/rules imports in the route;
  `VERIFIED` passes through verbatim; the gate module contains no status
  literals; the Phase 15 single-key gate still works when metering is
  unconfigured — **proven with an ambient `DATABASE_URL` present** (see
  §4.1).
- **H — Concurrency proof:** 40 real threads, limit 10 → accepted ≤ 10,
  final usage == accepted count, all rejections 429, quota actually
  binding (some 429s occurred) and non-vacuous (some admissions occurred).

## 4. Defects found and fixed during closure

### 4.1 (Implementation) Implicit `DATABASE_URL` fallback — removed

The first draft resolved the metering store as
`PLATRIXA_METERING_DATABASE_URL` → fallback `DATABASE_URL`. The fallback
silently violated the documented Phase 15 contract: on any host whose
`.env` defines `DATABASE_URL`, "unconfigured" became unreachable and every
keyless request 401'd against a tenant table that was never seeded —
fail-closed by accident, brickable on deploy. This was caught by the Phase
13/15 regression suites (keyless requests returning 401 instead of the
documented open/401-per-key behavior). Fix: metering activates ONLY on its
dedicated variable (explicit over implicit; matches `init_metering.py` and
`dev_seed_tenant.py`). Proven by G6/G7 with an ambient `DATABASE_URL`
present.

### 4.2 (Implementation) Eager `models` import broke the clean-import contract

`backend/auth/__init__.py` re-exported `models` eagerly; `models` imports
`backend.database.db`, which creates a SQLAlchemy engine and loads
psycopg2 at API import time (and raises entirely on hosts without
`DATABASE_URL`). Caught by `fte_fyjc_62` A3 ("no heavy modules loaded at
API import"). Fix: `gate`/`tokens` imported eagerly (light), `models`
exposed lazily via module `__getattr__`.

### 4.3 (Test-only) Evidence-suite defects

All fixed without touching implementation semantics:

- **A7** asserted `stub.calls == 1` against `LockedStub`, whose counter is
  frozen **by design** (its purpose is "never reached" proofs). A7 now uses
  the counting facade-contract stub.
- **E** popped `PLATRIXA_METERING_DATABASE_URL` in its `finally` instead of
  restoring it, silently switching later sections to the (then-present)
  `DATABASE_URL` fallback → suite-wide 503 cascade. Now restores prior
  state.
- **G1** probed `route.path` flatly; FastAPI 0.141 wraps `include_router`
  results in `_IncludedRouter` (no `.path`). Added a version-tolerant
  route walker (`original_router` fallback).
- **G3** substring-probed for `grounding_` / `accounting(`, false-positive
  matching the sanctioned `grounding_issues` response field and
  `_safe_accounting(` output sanitizer. Replaced with a real-import scan
  (forbidden backend internals) plus engine/session symbols.
- **G7** had to neutralize the (then-existing) `DATABASE_URL` fallback to
  reach the documented "metering unconfigured" state; with §4.1 fixed it
  instead asserts the no-fallback property directly.

## 5. Documentation updated

- `docs/HOSTED_API.md` — Authentication section now documents both gates,
  the 401/429/503 table, quota semantics, activation variable, provisioning
  commands; "Known limits" and "Hosting limitations" updated.
- `README.md` — hosted-API auth paragraph now describes the metered gate.

## 6. Environment / configuration

| Variable | Meaning |
|---|---|
| `PLATRIXA_METERING_DATABASE_URL` | Enables the metered gate (PostgreSQL). Run `python -m backend.auth.init_metering` once. Absent → Phase 15 behavior, unchanged. |
| `PLATRIXA_DEV_API_KEY` | Phase 15 shared key; composes with (runs before) the metered gate. |

No new Python dependencies: `sqlalchemy` and `psycopg2-binary` are already
imported/declared by the repository's persistence layer; `pgserver` is a
test-only, already-present dependency of the evidence suites (like the
Phase 15 suite) and is not imported by any production module.

## 7. Validation summary

| Suite | Scope | Result |
|---|---|---|
| `fte_fyjc_66_metered_gate_test.py` | Phase 16 evidence (A–H) | **51/51 PASS** |
| `fte_fyjc_65_hosted_api_security_test.py` | Phase 15 regressions | **47/47 PASS** |
| `fte_fyjc_62_hosted_api_boundary_test.py` | Phase 13 regressions | **55/55 PASS** |

## 8. Remaining limitations (honest)

- Billing, automated key issuance, and a self-service developer portal do
  not exist; tenants are operator-provisioned.
- Metering is per-request admission metering (1 unit/request); it is not
  an idempotency or deduplication mechanism.
- SHA-256 key storage: safe against casual exposure; raw keys must be
  high-entropy (enforced by the seed tool's generator, not by the schema).
- `last_request_at` is informational; there is no usage-reporting API yet.
