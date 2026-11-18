# Platrixa S4 — Multi-tenant worker claim architecture: decision

**Date:** 18 November 2026  
**Status:** DECIDED — retain one worker identity per tenant for the private beta.
No multi-tenant claim mechanism was implemented, because doing so safely needs a
new authorization primitive that does not exist today.

---

## 1. What the current authorization actually does (verified)

| Fact | Source |
|---|---|
| The claim tenant is derived from the authenticated API key, never from the request | `api/routes/inference_api.py` → `tenant = _resolve_idempotency_tenant(request)` → `backend/auth/developer.py::_resolve_idempotency_tenant` → `admission.authenticate_only(provided).tenant_id` |
| The claim statement is both tenant- and kind-scoped | `backend/auth/async_jobs.py::claim_next_job(kind=..., tenant_id=...)` appends fixed literal predicates with bound values |
| No worker route reads a tenant header | S4 test A4 scans `api/routes/inference_api.py` for `x-platrixa-tenant` → absent |
| Worker identity is only a lease-owner LABEL | `_claim_owner()` sanitizes `X-Platrixa-Worker-Id`; the tenant always comes from the key |
| A foreign job is indistinguishable from a missing one | result/lease/failure routes return 404 `JOB_NOT_FOUND` when `record.tenant_id` is not the caller's |
| Commits are fenced on owner + generation | `complete_job` / `renew_lease` / `fail_job` predicates |
| Quota is per key, idempotency is per tenant | `backend/auth/gate.py`, `backend/auth/idempotency.py` |

A worker process therefore serves exactly the tenant whose key it holds. That is
not a bug; it is the property that makes tenant isolation provable.

## 2. Options considered

### Option A — one worker identity (API key) per tenant  ← CHOSEN

Each tenant gets its own key; the deployment runs one worker process per active
tenant (or one worker per key, which is equivalent).

* Security: unchanged; every existing isolation and fencing guarantee holds.
* Scaling: N tenants ⇒ N worker processes on the device(s). For a private beta
  with a handful of tenants this is operationally acceptable and was already the
  observed deployment (`android-test-worker`, `termux-worker-test`, `termux-worker`).
* Cost: one process per tenant on the phone; no new server code.

### Option B — a dedicated multi-tenant worker credential

A new credential class (not a tenant key) that may claim across tenants, with the
server deciding which tenant a given job belongs to.

Required server-side prerequisites, none of which exist today:

1. A **worker-role credential** distinct from tenant API keys, with its own
   registration/rotation/revocation lifecycle and an owner-only issuance path.
2. A **server-controlled assignment** (pool/allow-list) mapping a worker
   credential to the tenants it may serve. The worker must never name a tenant.
3. **Result-ownership checks** that authorize a commit by (worker credential,
   job's tenant, lease owner, generation) together — not by tenant key alone.
4. **Per-tenant fairness** in the claim statement (round-robin or weighted), so a
   multi-tenant worker cannot starve a tenant.
5. **Quota attribution**: admission stays the tenant's, but usage/reporting must
   distinguish "tenant requested" from "worker served".
6. **Audit fields** recording which worker credential served which tenant's job.
7. Tests for: worker serves A then B without leakage; worker cannot commit to an
   unassigned tenant; two workers cannot duplicate a completion; revocation
   immediately stops claims.

Option B is a real authorization project (schema + routes + tests + rotation
runbook). Implementing it under this mission would have changed the security
model without a reviewable design, which rule 11 forbids.

## 3. Decision

**Retain Option A for the private beta.** The product claim must be “one worker
per tenant, each with its own credential”, not “100 developers on one worker”.

The measured S6 table shows monthly VOLUME is not the binding constraint at
plausible device speeds (45 s/job ⇒ ~17% duty for 100 developers × 100 jobs).
The binding constraints are:

* **burst latency**: one worker processes strictly one job at a time;
* **per-tenant pending bound (10)**: a burst beyond 10 is refused, by design;
* **unmeasured device throughput**: no GGUF inference was measured in S6.

## 4. Evidence (tests run in this mission)

| Check | Result |
|---|---|
| `scripts/platrixa_s4_multitenant_authorization_test.py` | **13/13** |
| ├ spoofed tenant headers do not surface another tenant’s job | PASS |
| ├ A’s key still claims A’s job despite a spoofed header | PASS |
| ├ no worker route reads a tenant header (source scan) | PASS |
| ├ concurrent claims hand the raced job to exactly one worker | PASS |
| ├ a foreign worker cannot submit a result (404) | PASS |
| ├ a stale lease generation is refused (409) | PASS |
| └ the same Idempotency-Key under two tenants is two jobs | PASS |
| `scripts/platrixa_phase1b_tenant_isolation_test.py` | **14/14** |
| `scripts/platrixa_stage2_two_worker_race_test.py` | **23/23** |
| `scripts/platrixa_stage2_lease_safety_integration_test.py` | **106/106** |

## 5. What a future Option B must not do

* Accept a client-supplied tenant id as authority (the current code never does).
* Let a worker commit a result for a tenant it is not assigned to.
* Allow a claim to widen beyond the assignment set.
* Reuse a tenant API key as a multi-tenant credential.
* Bypass the lease generation fence.
