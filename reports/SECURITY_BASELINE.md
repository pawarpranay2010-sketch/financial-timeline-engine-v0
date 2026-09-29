# Platrixa Security Baseline

**Frozen at commit `87ff8e12a1865d082afb2211d2c4ab8ce74ab30a` (branch `main`).**

This document is a **durable statement of what has actually been verified**, not a
claim that the system is secure or production-ready. Every property below cites the
test that establishes it. Anything not established by a test appears in
[§9 Explicitly Untested](#9-explicitly-untested) and is marked **NOT VERIFIED / NOT
TESTED** — neither secure nor vulnerable.

Execute the whole baseline with:

```
python3 scripts/security_baseline_test.py
```

**Classification vocabulary**

| Term | Meaning |
|---|---|
| **VERIFIED** | A test executes this property against the real code path and it holds. |
| **PARTIALLY VERIFIED** | A narrower property than the name suggests is established; the remainder is recorded as residual risk. |
| **NOT VERIFIED / NOT TESTED** | No test establishes it. Neither asserted safe nor asserted unsafe. |

---

## 1. Financial Truth Boundary

The architectural claim: *the model interprets, deterministic authorities decide and
execute.* These properties test that the interpretation layer cannot become authority.

### FT-01 — A model-provided `transaction_type` cannot independently dictate accounting execution
**VERIFIED** · `scripts/_sec_invariant_probe.py` → `INV1`, `INV2`, `INV3`, `INV4`

A candidate claiming `transaction_type_enum="SALE"` over an input reading "Bought
machinery" yields `safe_for_kernel=False`, `grounded=False`, kernel status
`GROUNDING_FAILED`, and **no accounting result at all** (`accounting=none`).

*Supporting evidence beyond the probe:* `fte_sec_01` `B1`–`B7`, `C2`, `C3`.

### FT-02 — A model-provided amount cannot alter the deterministic accounting posting
**VERIFIED** · `scripts/_sec_invariant_probe.py` → `INV7`

Candidate amount `9,000` against source `Rs. 90,000` is rejected at grounding
(`GROUNDING_FAILED`, `INV6`), so no posting is produced from it.

*Mechanism note (stated precisely, because the probe output is `debit=[]`):* the
post-fix evidence is **rejection before accounting**, and the assertion
`all(amount == 90000)` is satisfied over an empty list. The complementary evidence is
that *before* the M-02 fix, passing `amount=0`, `90`, `500`, `999999`, `1e9` directly
into the accounting adapter **still produced 90,000** — the deterministic flow re-derives
the amount from `raw_input` and ignores the model field. Both directions were measured;
neither is inferred.

### FT-03 — Multi-field model tampering cannot dictate the deterministic posting
**VERIFIED** · `scripts/_sec_invariant_probe.py` → `INV8`

Simultaneously tampering `transaction_type_enum`, `payment_method_enum` and `amounts`
produces no posting.

*Same mechanism note as FT-02.* Pre-fix, flipping `transaction_type_enum` across all 15
valid enums left the debit/credit lines byte-identical (`Machinery` 90000 / `Cash` 90000
in every case) — the model field is inert in the accounting path.

### FT-04 — Valid candidates can still reach `VERIFIED`
**VERIFIED** · `scripts/_sec_invariant_probe.py` → `INV0` · `fte_sec_01` → `C1`, `D5`

This is a deliberate anti-regression property. The security suites must never pass by
rejecting everything. A faithful candidate reaches `status=VERIFIED`; a faithful amount
still reaches `VERIFIED` (`D5`, "no over-tightening").

### FT-05 — The invoice false-VERIFIED gate remains closed
**VERIFIED** · `scripts/fte_invoice_false_verified_gate_test.py`

**`false_VERIFIED = 0`** across 50 checks. This is the existing Platrixa gate, not a
security-suite invention.

---

## 2. Grounding / VERIFIED Boundary

### GV-01 — Failed `transaction_type` grounding produces `grounded=False`
**VERIFIED** · `fte_sec_01` → `B2`, `B7`

### GV-02 — Failed grounding cannot result in `safe_for_kernel=True`
**VERIFIED** · `fte_sec_01` → `B3`, `B4` · `_sec_invariant_probe` → `INV1`

`B4` asserts the exact historical defect shape is gone: a `FieldGrounding(grounded=False)`
may no longer coexist with an aggregate `grounded=True`.

### GV-03 — Failed grounding produces `GROUNDING_FAILED` and cannot become `VERIFIED`
**VERIFIED** · `fte_sec_01` → `C2`, `B5`, `B6` · `_sec_invariant_probe` → `INV3`

### GV-04 — Amount substring mismatches do not ground
**VERIFIED** · `fte_sec_01` → `D2` (five cases)

Against source `₹90,000`, none of `90`, `0`, `9`, `000`, `9,000` grounds. Each is asserted
individually.

### GV-05 — Valid amount representations still ground where the existing contract allows
**VERIFIED** · `fte_sec_01` → `D1`, `D5`

`90000` and `90,000` continue to ground, and the true amount still reaches `VERIFIED`.

**Contract boundary — do not overstate.** `fte_sec_01` records these as **notes, not
assertions**, because the current contract does not establish semantics for them:

| Form | Current behaviour |
|---|---|
| `Rs. 90,000` | not grounded (currency-word form) |
| `90000.00` | not grounded |
| `90,000.00` | not grounded |

The baseline does **not** claim these are supported, and the security work did not add
support for them.

---

## 3. API Authentication and Admission

### API-01 — Protected `/api/v1` routes cannot be reached anonymously
**VERIFIED** · `fte_sec_02` → `B` (4 endpoints), `C` (5 credential classes), `D1` · `_sec_invariant_probe` → `INV12`

Protected: `POST /api/v1/db/init`, `POST /api/v1/intelligence/analyze`,
`GET /api/v1/market/{ticker}`, `GET /api/v1/market/{ticker}/price`,
`GET /api/v1/providers/status`. All return `401` to an anonymous caller. Five credential
classes (none / malformed / invalid / runtime / management) are each rejected, and no
downstream work is reached in any combination.

### API-02 — Authentication is tested with canaries, not real providers or databases
**VERIFIED** · `fte_sec_02` → `_arm_canaries()`

`svc.run_analysis`, `svc.fetch_market_snapshot` and `svc.initialize_database_schema` are
replaced with recording stubs. The suites therefore prove *whether authentication was
enforced* without ever calling an LLM, a paid provider, or a real database.

### API-03 — Rate limiting happens before expensive downstream routing
**PARTIALLY VERIFIED** · `fte_sec_02` → `E5`, `E0` · `_sec_invariant_probe` → `INV15`

The limiter is an HTTP middleware registered before routing, so a throttled request
returns `429` without entering a route body. Anonymous traffic to
`/api/v1/kernel/process` is refused at request #30 (`E5`) and request #30 (`INV15`).

*Why only partial:* the tests observe the `429` at the HTTP boundary. They do **not**
instrument the middleware ordering against a route that performs real expensive work, so
"before expensive work" is established structurally (middleware position) plus the
observed `429`, not by a direct ordering assertion.

### API-04 — Anonymous request count is bounded
**VERIFIED** · `fte_sec_02` → `E5` (30/60s) · `_sec_invariant_probe` → `INV15` (30/60s)

### API-05 — Rate-limit keying does not rely on client-supplied headers
**PARTIALLY VERIFIED** · implementation: `api/rate_limit.py` → `_client_key()`

The bucket key is the transport peer address, or a forwarded address **only** when
`PLATRIXA_TRUSTED_PROXY_COUNT` is explicitly configured. No client-supplied identifier
is used as a key.

*Why only partial:* no test asserts that mutating `X-Forwarded-For` or an API-key header
does not change the bucket. This is established by implementation inspection, which the
baseline does not treat as equivalent to a test.

### API-06 — Tenant quota remains separate from the request-rate limiter
**VERIFIED (separation)** · `fte_fyjc_66_metered_gate_test` (52/52)

The limiter is a per-peer-IP volume ceiling in `api/rate_limit.py`. The tenant quota is
the authoritative per-tenant control in `backend/auth/gate.py` (atomic reservation).
The limiter does not reserve quota and the quota does not rate-limit. `fte_fyjc_66`
confirms quota behaviour is unchanged.

### API-07 — Two `/api/v1` routes remain anonymous **by contract**
**VERIFIED (deliberate, not a gap)** · `fte_sec_02` → `D2`, `D2b` · `_sec_invariant_probe` → `INV13`, `INV14`

| Route | Why public | Control instead |
|---|---|---|
| `GET /api/v1/health` | `render.yaml` sets it as `healthCheckPath`; the production UI polls it (only inspects the HTTP status) | exempt from rate limiting; content restricted (API-07a) |
| `POST /api/v1/kernel/process` | the shipped production UI (`frontend/app.js` `ENDPOINT`) posts here with no credential | rate limited (`E5`, `INV15`) |

> **public route ≠ unprotected route.** A public route is a route the deployment
> contract requires to stay anonymous. It is rate-limited and content-restricted, not
> left without controls. The security suites assert both halves: that health stays
> `200` (`D2`, `INV13`) **and** that it discloses nothing sensitive (`D2b`, `INV14`).

**API-07a** — public `/api/v1/health` exposes neither the database driver exception nor
the provider credential inventory. `D2b` and `INV14` assert the absence of
`psycopg2`, `postgresql://`, `password`, and `traceback` in the response.

### API-08 — Per-request input bounds are unchanged (controls, not vulnerabilities)
**VERIFIED (regression guard)** · `fte_sec_02` → `E2`, `E3`, `E4`

`max_iterations` is `Ge(1)/Le(5)`; `raw_input` is `MinLen(1)/MaxLen(2000)`; a 2 MiB body
is refused with `422`. These are pre-existing protections. The security work did not
change them, and the tests now prevent them being loosened.

---

## 4. Webhook Security

### WH-01 — Webhook delivery does not follow redirects
**VERIFIED** · `fte_sec_03` → `B0`, `B` (3 redirect classes) · `_sec_invariant_probe` → `INV9`

`allow_redirects=False` is pinned in `_post_webhook`. Against a local mock server, three
redirect routes (internal/private, metadata-style, localhost) were each **not** followed;
only the approved path was requested.

### WH-02 — Delivery targets are validated against non-public address classes
**VERIFIED** · `fte_sec_03` → `B5` (5 cases) · `_sec_invariant_probe` → `INV10`

`_assert_safe_delivery_target` rejects loopback literal, `localhost`, two RFC1918
addresses, and the link-local/metadata address.

### WH-03 — The control is property-based, not a literal metadata-IP blocklist
**VERIFIED** · `fte_sec_03` → `B0b`, `B5` (5 distinct address classes)

The check resolves the hostname and rejects on address **properties** (`is_private`,
`is_loopback`, `is_link_local`, `is_reserved`, `is_multicast`, `is_unspecified`). Five
different non-public addresses are rejected, which a single literal blocklist could not
achieve. `B0b` confirms delivery re-validates at delivery time, not only at registration.

### WH-04 — Webhook secret ciphertext tampering is rejected
**VERIFIED** · `fte_sec_03` → `C2` · `_sec_invariant_probe` → `INV11`

A one-bit ciphertext modification is rejected. The construction is AES-256-GCM with
HKDF-SHA256 key derivation (`C4` confirms AEAD + `InvalidTag` verification).

### WH-05 — Legacy `v1` webhook seals are refused, not silently accepted
**VERIFIED** · `fte_sec_03` → `C5`

> **Migration consequence.** Existing `v1` webhook secrets **must be re-registered**
> (`POST /v1/webhook-endpoints`) to obtain a `v2` seal. A `v1` seal is refused rather
> than accepted, because accepting it would restore the ciphertext malleability the
> change removes. The consequence is bounded: webhook delivery is best-effort by design
> and a refused seal is logged, never raised into the job path. This is a deliberate
> fail-closed behaviour change, not a defect.

---

## 5. Error / Secret Disclosure

A controlled canary exception carrying distinctive marker strings is raised inside a
route handler, and the response is inspected for each marker.

| ID | Property | Test |
|---|---|---|
| **ER-01** | Internal exceptions are not returned verbatim | `fte_sec_04` → `A4`, `B2` |
| **ER-02** | Database DSNs/credentials are not exposed | `fte_sec_04` → `A2 db_dsn`; `fte_sec_02` → `D2b` |
| **ER-03** | SQL is not exposed | `fte_sec_04` → `A2 sql` |
| **ER-04** | Filesystem paths are not exposed | `fte_sec_04` → `A2 abs_path` |
| **ER-05** | Prompts are not exposed | `fte_sec_04` → `A2 prompt` |
| **ER-06** | API keys / provider secrets are not exposed through tested public paths | `fte_sec_04` → `A2 api_key`, `A2 token`; `fte_sec_02` → `D1` |
| **ER-07** | Public `/health` exposes neither driver errors nor provider key inventory | `fte_sec_02` → `D2b`; `_sec_invariant_probe` → `INV14` |
| **ER-08** | Stack traces are not exposed | `fte_sec_04` → `A2 trace` |

All **VERIFIED**.

`A0` is an **anti-vacuousness guard**: it asserts the failure path was genuinely
exercised (`HTTP 500` with `{"detail":"Analysis failed"}`) using a configured test
credential, so the disclosure assertions cannot pass merely because the route rejected
the caller before the handler ran.

---

## 6. Supply Chain / Dependency Controls

### SC-01 — Model revisions are pinned to exact 40-hex commit SHAs
**VERIFIED** · `fte_sec_05` → `D1`, `D2`, `D3`, `D4`, `D5`, `D6`

Base `989aa798…aa306`, adapter `b5c0a37c…ad259e`, mirrored in
`backend/model_provider/base.py`. `D3` confirms `revision=` is passed at load;
`D6` confirms no bare `from_pretrained(BASE_MODEL_ID)` without a revision.

### SC-02 — The PEP 508 environment-marker false positive is corrected
**VERIFIED** · `fte_sec_05` → `B3` (5 parser cases)

`pgserver>=0.1 ; sys_platform != "win32"` is classified by its specifier, not as
unpinned. The earlier "1 unpinned dependency" was a **test bug**, not a production
finding. Unpinned count is now `0`.

### SC-03 — Newly introduced dependencies are declared
**VERIFIED** · `fte_sec_05` → `B1b` (×2)

`cryptography` (required by the M-03 AEAD change) is declared in both
`requirements.txt` and `requirements-core.txt`.

### SC-04 — Dependency reproducibility
**PARTIALLY CONTROLLED / RESIDUAL RISK** · `fte_sec_05` → `B1`, `B2`, `C1`

State, stated accurately:

- **42** dependencies use version **ranges** (`>=`). This is the project's existing
  policy — there is no lockfile, no `pyproject.toml`, no pinned requirements file, and
  none of these constraints have ever been exact.
- **0** dependencies are completely unconstrained (`B2`).
- **No lockfile exists** (`A1` notes: `pyproject.toml`, `Pipfile`, `poetry.lock`,
  `Cargo.lock` all absent).
- The Render build runs `pip install -r requirements.txt`, so **builds are not
  reproducible** and a new or hijacked release is installed silently.
- **No `--hash` entries** in any requirements file (`C1`, note only).

**This is not classified as a vulnerability** — the project has not declared ranges
forbidden. It is recorded as residual risk. The security work did not mass-pin the
tree: rewriting 42 constraints to `==` without a verified resolution set would be a
larger and riskier change than the finding it would close. Recommended separately:
generate a lockfile (`pip-compile` / `uv lock`) and pin the result.

---

## 7. Regression Contract

`scripts/security_baseline_test.py` orchestrates the suites below. It **reuses the
existing suites** and does not duplicate their assertions.

### Security regression (required)

| Suite | Result |
|---|---|
| `fte_sec_01_grounding_fail_closed_test` | 24/24 |
| `fte_sec_02_api_authz_admission_test` | 19/19 |
| `fte_sec_03_webhook_ssrf_seal_test` | 24/24 |
| `fte_sec_04_error_disclosure_test` | 16/16 |
| `fte_sec_05_dependency_policy_test` | 20/20 |

### Invariant probe (required)

| Suite | Result |
|---|---|
| `_sec_invariant_probe` | 16/16 |

### Financial truth (required)

| Suite | Result |
|---|---|
| `fte_fyjc_grounding_verifier_test` | 16/16 |
| `fte_fyjc_53_grounding_verification_wiring_test` | 13/13 |
| `fte_fyjc_52_kernel_boundary_test` | 20/20 |
| `fte_invoice_false_verified_gate_test` | 50/50 — **false_VERIFIED = 0** |

### API contract (required)

| Suite | Result |
|---|---|
| `fte_fyjc_59_status_contract_test` | 20/20 |
| `fte_fyjc_80_phase5d_result_contract_test` | 54/54 |
| `fte_fyjc_81_phase5e_async_documents_test` | 52/52 |
| `fte_fyjc_66_metered_gate_test` | 52/52 |
| `fte_fyjc_83_phase5g_api_key_lifecycle_test` | 44/44 |
| `fte_fyjc_84_phase5h_observability_test` | 45/45 |

> **Resolved 2026-09-29 — `fte_fyjc_66` was intermittently failing H4.**
> Section H fires 40 concurrent threads and asserts every rejection is a
> clean 429. It was intermittently returning 503 instead. Root cause was **not**
> pool sizing: `backend/auth/gate.py::_session_factory` looked up its engine
> cache with the raw env URL but stored the entry under the normalized
> `postgresql+psycopg2://` URL, so the cache never hit. Every metering call
> built a fresh Engine + ConnectionPool; section H created 132 engines,
> exhausted the server's 100-connection cap (`too many clients already`), and
> the resulting `OperationalError` surfaced as fail-closed 503s.
>
> Fixed by normalizing the URL **before** the cache lookup, plus a lock so
> concurrent cold-starts build exactly one engine. Verified 52/52 on three
> consecutive runs. This defect was pre-existing at `HEAD` and unrelated to
> the security work; it failed in the safe direction (503 = never admitted —
> no auth or quota bypass) but was an availability amplification. A recurrence
> of H4 is now a genuine regression.

### Additional supporting suites (verified, not orchestrated as gates)

`fte_fyjc_47_grounding_migration_test` (65/65), `fte_fyjc_54_persistence_boundary_test`
(80/80), `fte_fyjc_15boundary_closure_test` (852/852), `fte_fyjc_57_remote_provider_test`
(43/43).

### Anti-vacuousness requirements on the runner

The baseline is **not** considered passing if tests silently disappear. The runner
enforces:

1. Every required suite file exists.
2. Every required suite actually executes.
3. **Zero executed tests is a failure** — a suite that collects nothing fails the run.
4. A missing suite is a failure.
5. Import / collection errors are failures.
6. A non-zero subprocess exit is a failure.
7. A required suite that reports no recognizable pass count is a failure.

---

## 8. Known Limitations

Residual limitations of the verified controls. These are **not** asserted to be
vulnerabilities.

1. **Rate limiting is per-process and in-memory.** Counters live in the process dict
   `api/rate_limit._BUCKETS`.
2. **Multiple instances do not share a global bucket.** A multi-instance deployment
   enforces the ceiling *per instance*, so the effective global limit scales with
   instance count.
3. **A shared store such as Redis would be required for a global ceiling.**
4. **Behind a proxy without `PLATRIXA_TRUSTED_PROXY_COUNT`**, peer-address keying can
   cause many distinct clients to share one bucket (or one client to rotate buckets,
   depending on proxy topology). The forwarded-header path is opt-in precisely because
   the header is attacker-controlled.
5. **Existing `v1` webhook seals fail closed and require re-registration** (see WH-05).
6. **Dependency ranges remain** (42), and **no lockfile exists** (see SC-04).
7. **M-01 and M-02 make grounding stricter**, so a model output that previously reached
   accounting via an unverifiable transaction type or a substring-matching amount is
   now refused. This is the intended fail-closed direction, but it does raise the
   review load: `REVIEW_REQUIRED`/`GROUNDING_FAILED` volume will increase for
   low-quality model output.

---

## 9. Explicitly Untested

The following are **NOT VERIFIED / NOT TESTED**. No test in this repository establishes
them. They are neither asserted secure nor asserted vulnerable.

| Area | Why not established |
|---|---|
| Prompt injection against a reachable production-equivalent model | No model is reachable in the test environment: the Modal endpoint is undeployed (404), HF ZeroGPU quota is exhausted, and no model weights are present locally. |
| Malicious OCR / document payloads | The `/v1/documents` path was reviewed but not executed with hostile documents. |
| Document bombs / decompression / resource exhaustion | Body size is bounded (16 MiB for `/v1/documents`), but downstream extractor expansion ratios were not measured. |
| Live multi-tenant isolation against the real database | Code review shows correct tenant scoping and parameterized SQL; no two-tenant integration test against a live metering store. |
| Production Render environment behaviour | The deployed environment, its env vars, and its proxy topology are not observable from the development environment. |
| Dynamic DNS rebinding race | `_assert_safe_delivery_target` was unit-tested against real non-public address classes. A live rebind race (resolve → connect → re-resolve) was not exercised. |
| Multi-instance rate-limit behaviour | Only a single-process environment was tested. |
| Global / shared rate-limit behaviour | No shared store exists, so there is nothing to test. |
| Real provider compromise or failure modes | All provider calls in the suites are canaries or absent. |
| Webhook signature verification by a real consumer | `verify_event_signature` exists and is documented; a real end-to-end consumer round-trip was not executed. |
| Cloudflare Pages proxy behaviour | The Pages function proxy was not exercised; only the Render-side application was tested. |
| Render/cloud metadata reachability in production | The SSRF control is unit-verified; no live metadata service was contacted, by design. |

---

*This baseline records tested properties only. It does not assert that Platrixa is
secure or production-ready. Generated from the security hardening pass at commit
`87ff8e1`; re-run `scripts/security_baseline_test.py` to verify it still holds.*
