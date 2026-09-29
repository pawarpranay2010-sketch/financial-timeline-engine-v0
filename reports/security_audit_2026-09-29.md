# Platrixa — Security Audit / Weakness Discovery

**Date:** 2026-09-29
**Commit audited:** `87ff8e12a1865d082afb2211d2c4ab8ce74ab30a` (main)
**Method:** source review, data-flow tracing, empirical probes against the real code
**Code changed:** none. No production file, dataset, or evaluation artifact was modified.

All findings below were verified against code, not inferred from documentation. Findings
that could not be proven are marked **UNCONFIRMED** with what would be required.

---

## SECURITY POSTURE

The **model→authority boundary is genuinely strong**, and stronger than the audit brief
assumed. The **network/API boundary is genuinely weak**. Those two facts are in tension,
and the tension is the headline.

Empirically established (`/tmp` probes, real `Kernel.process()`):

> The deterministic accounting output is a **pure function of `raw_input`**. The model's
> `amounts`, `transaction_type_enum`, and `payment_method` fields are **inert** — flipping
> any of them across all valid enum values left `status=VERIFIED` and the debit/credit
> lines byte-identical.

```
INPUT: Bought machinery for ₹90,000 cash from Iyer and Co.

  gold PURCHASE     status=VERIFIED  DR=[('Machinery','90000')] CR=[('Cash','90000')]
  flipped->SALE     status=VERIFIED  DR=[('Machinery','90000')] CR=[('Cash','90000')]
  flipped->EXPENSE  status=VERIFIED  DR=[('Machinery','90000')] CR=[('Cash','90000')]
  flipped->GST      status=VERIFIED  DR=[('Machinery','90000')] CR=[('Cash','90000')]
  flipped->UNKNOWN  status=VERIFIED  DR=[('Machinery','90000')] CR=[('Cash','90000')]
```

`_best_description()` returns `raw_input`; `_ExistingAccountingAdapter.process()`
re-parses the amount from the description and ignores the passed amount
(`amount=0`, `amount=999999`, `amount=1e9` all still produced 90000).

**Consequence:** the Phase H model's 62.67% grounding score has **no path to a wrong
posting** through this pipeline. A compromised or jailbroken model cannot change the
accounting. This is the single strongest control in the codebase.

**But:** the compensating weakness is that anyone on the internet can reach the pipeline
at all (§1), repeatedly, for free.

---

## CRITICAL FINDINGS

None. No finding demonstrated a path to an incorrect `VERIFIED` result or cross-tenant
data disclosure. The financial-truth guarantees held under every probe I could construct.

---

## HIGH FINDINGS

### H-01 — Entire `/api/v1/*` surface is unauthenticated
- **SEVERITY:** HIGH · **CONFIDENCE:** CONFIRMED · **STATUS:** CONFIRMED
- **COMPONENT:** `api/routes/{kernel,health,intelligence,market}.py`, `api/main.py`
- **ATTACKER:** Unauthenticated internet client
- **PRECONDITION:** Reachability of the Render host (direct or via the Cloudflare proxy)
- **CODE LOCATION:** `api/main.py:108-125` mounts all four routers with **no
  `dependencies=[...]`**. Verified by sweeping every `@router` decorator in `api/routes/`:
  only `async_api.py`, `developer.py`, `developer_keys.py`, `observability.py` declare
  `Depends(...)`; `kernel.py:179`, `health.py:12,33`, `intelligence.py:12,35`,
  `market.py:12,29` declare none.

| Endpoint | Unauthenticated effect |
|---|---|
| `POST /api/v1/kernel/process` | Triggers model inference **and a database write** per call |
| `POST /api/v1/intelligence/analyze` | Runs `AgenticRAGOrchestrator`, a **retrieval loop** |
| `POST /api/v1/db/init` | **State-changing**: creates/verifies DB schema |
| `GET /api/v1/market/{ticker}` | Fans out to external paid providers |
| `GET /api/v1/providers/status` | Discloses env-var names + key-presence (see Q-01) |
| `GET /api/v1/health` | Discloses DB reachability, uptime |

- **EVIDENCE:** `intelligence.py:22-26` — `svc.run_analysis(ticker=req.ticker, goal=req.goal,
  max_iterations=req.max_iterations)` reaches the agentic loop with no authentication
  check. See CORRECTION 1: `max_iterations` is already bounded to 1–5 by the request
  schema, so the exposure is **unbounded request count**, not unbounded per-request work.
- **IMPACT:** Unauthenticated cost amplification against paid providers; unauthenticated
  durable state creation; unauthenticated schema mutation; a free inference oracle.
- **CURRENT MITIGATION:** None on the server.
- **WHY NOT SUFFICIENT:** The Pages proxy (`frontend/functions/api/[[path]].js`) forwards
  `/api/*` without adding credentials, and the Render host answers directly. Cloudflare is
  not an authentication control.
- **RECOMMENDED FIX:** Put `/api/v1/*` behind the same metered gate as `/v1/*`, or
  explicitly document it as a public demo surface and move the model/DB/RAG paths to `/v1/*`.
  Minimum: authenticate `/db/init` and `/intelligence/analyze`. (`max_iterations` is
  already bounded to 1–5; no change needed — see CORRECTION 1.)
- **REGRESSION TEST:** Unauthenticated `POST /api/v1/db/init` must not reach the service.

### H-02 — Unauthenticated endpoints permit repeated request admission without an independent request-rate limiter
- **SEVERITY:** HIGH · **CONFIDENCE:** CONFIRMED · **STATUS:** CONFIRMED
- **SCOPE (see CORRECTION 3):** unbounded request COUNT on the unauthenticated
  `/api/v1/*` surface. Per-request work is already bounded (CORRECTIONS 1 and 2), and
  tenant quota remains in force where it already applied.
- **COMPONENT:** `api/`, `backend/auth/`
- **EVIDENCE:** `grep -rniE "rate.?limit|ratelimit|slowapi|limiter" api/ backend/auth/`
  returns **zero matches**. The only admission control is per-tenant monthly quota
  (`backend/auth/gate.py`), which by design does **not** apply to `/api/v1/*`.
  See CORRECTION 3: an earlier draft of this audit used a faulty detector that matched
  the literal string `"429"`, which appears in `developer.py` as the HTTP status for
  `QUOTA_EXHAUSTED`. That was a **false positive in the detector**, not evidence of a
  limiter. The corrected detector (limiter constructs only) confirms no request-rate
  limiter exists.
- **IMPACT:** H-01 becomes trivially exploitable at scale: an unauthenticated caller can
  issue an unlimited number of requests against state-changing and provider-backed
  routes. Per-request cost is bounded, but the request count is not.
- **RECOMMENDED FIX:** Per-IP and per-token rate limits on `/v1/*`; a separate, much lower
  limit on `/api/v1/*`. (`max_iterations` is already bounded 1–5; `raw_input` is already
  bounded to 2000 characters — see CORRECTIONS 1 and 2. Neither needs a fix.)
- **REGRESSION TEST:** Burst N requests → `429`.

### H-03 — Webhook delivery follows redirects (SSRF)
- **SEVERITY:** HIGH · **CONFIDENCE:** CONFIRMED at code level · **STATUS:** CONFIRMED
- **COMPONENT:** `api/routes/async_api.py`
- **ATTACKER:** Any developer holding a valid API key (tenant-scoped, but a real tenant)
- **CODE LOCATION:** `async_api.py:417` — `requests.post(endpoint.url, data=body,
  headers=headers, timeout=5)` with **no `allow_redirects=False`**.
- **EVIDENCE:** Verified empirically in this environment:
  `inspect.signature(requests.Session.request).parameters['allow_redirects'].default`
  → `True`, and `requests.post` is a thin wrapper over `Session.request`.
  So a registered `https://evil.example/hook` that answers `302 Location:
  http://169.254.169.254/latest/meta-data/` is followed.
- **MITIGATION PRESENT:** `_is_https_url()` (`async_api.py:87-108`) requires `https`,
  rejects `localhost`/`127.0.0.1`/`0.0.0.0`/`::1`/`*.local`, and rejects raw IP literals.
  Applied at **registration only** (`:933`), never at delivery.
- **WHY NOT SUFFICIENT:** (a) redirects are followed after validation;
  (b) **DNS rebinding** — a public-looking hostname that resolves to `169.254.169.254` at
  delivery time passes validation, because the check never resolves DNS;
  (c) scheme downgrade `https` → `http` via redirect;
  (d) cloud metadata `169.254.169.254` is reachable as a *redirect target*.
- **IMPACT:** Read of cloud instance metadata (IAM credentials on GCP/Render-style hosts)
  from the Render worker, plus internal-network port scanning.
- **RECOMMENDED FIX:** `allow_redirects=False`; re-validate the resolved IP at connect time
  against a denylist including link-local/metadata ranges; pin to HTTPS.
- **REGRESSION TEST:** Register a host that 302s to `169.254.169.254`; assert the worker
  does not connect.

---

## MEDIUM FINDINGS

### M-01 — Grounding gate fails OPEN on `transaction_type` (defect; currently low impact)
- **SEVERITY:** MEDIUM (latent HIGH) · **CONFIDENCE:** CONFIRMED · **STATUS:** CONFIRMED
- **COMPONENT:** `backend/maths/fyjc_grounding_gate.py` Rule 5 (`:316-352`)
- **CODE LOCATION:** the unverified branch appends a `FieldGrounding(grounded=False)` but
  **never appends to `issues`**. Aggregation is `grounded = len(issues) == 0` (`:404`).
- **EVIDENCE:**
  ```
  FABRICATED transaction_type=SALE on a purchase input
    grounded        : True
    safe_for_kernel : True
    issues          : []
    transaction_type marked grounded? [False]     <-- internal contradiction
  INVENTED enum=CONSIGNMENT_WINDING
    grounded: True | safe_for_kernel: True | issues: []
  ```
  Every other rule (parties, amounts, payment method, references) *does* append to
  `issues` and correctly fails closed. Rule 5 is the sole exception.
- **IMPACT:** The gate returns `safe_for_kernel=True` while its own `field_results` record
  says the field is ungrounded. The contradiction is silently discarded.
- **WHY MEDIUM NOT HIGH:** Probed the full kernel — flipping `transaction_type_enum` to all
  15 valid enums left the posting and status **identical**. The accounting ignores it. There
  is no current path to a wrong result.
- **WHY IT STILL MATTERS:** (a) it is a *fail-open* in a safety gate — the class of bug that
  becomes critical the moment another field starts routing on transaction type;
  (b) the gate's own output is self-contradictory, so any future consumer of
  `field_results` vs `grounded` disagrees with it.
- **RECOMMENDED FIX:** Append to `issues` in the unverified branch (one line, matching every
  other rule).
- **REGRESSION TEST:** Assert `grounded is False` for an unverifiable `transaction_type_enum`.

### M-02 — Amount grounding is substring-based and trivially satisfiable
- **SEVERITY:** MEDIUM · **CONFIDENCE:** CONFIRMED · **STATUS:** CONFIRMED
- **COMPONENT:** `fyjc_grounding_gate.py:120-141` (`_amount_in_text`)
- **EVIDENCE:** `val in text_digits_only` against `"Bought machinery for ₹90,000"`.
  Confirmed: `amount="90"` → `grounded=True`; `amount="0"` → `grounded=True` — both
  produced `status=VERIFIED`. A claimed amount that is any digit-substring of a real number
  passes. A 1000× understatement ("90" for 90,000) grounds clean.
- **IMPACT:** Grounding does not currently constrain the amount — but the accounting
  re-derives it from `raw_input`, so no wrong posting. Latent risk if that ever changes.
- **RECOMMENDED FIX:** Match on token boundaries with a parsed-numeric comparison
  (normalize `90,000`/`90000`/`₹90,000` then compare equality, not containment).
- **REGRESSION TEST:** `amount="90"` and `amount="0"` against a 90,000 input must fail closed.

### M-03 — Webhook secret "sealing" is unauthenticated encryption
- **SEVERITY:** MEDIUM · **CONFIDENCE:** CONFIRMED · **STATUS:** CONFIRMED
- **COMPONENT:** `backend/auth/async_jobs.py:106-144`
- **EVIDENCE:** `_seal_keystream` is `SHA256(key‖nonce‖counter)` XOR — a counter-mode
  keystream with **no MAC and no AEAD**. `unseal_webhook_secret` XORs and decodes; it
  verifies nothing.
- **IMPACT:** Ciphertext is **malteable** — flipping bit *n* of the ciphertext flips bit *n*
  of the plaintext secret, with no detection. Anyone with write access to the jobs table
  (DB credential compromise, or a SQLi elsewhere) can silently rewrite a tenant's webhook
  signing secret and forge events. The code comment calls it "sealed", which overstates it.
- **RECOMMENDED FIX:** AES-GCM (or `cryptography`'s AEAD) with the nonce and tag stored in
  the `v1:` blob; reject on tag mismatch.
- **REGRESSION TEST:** Bit-flip a sealed secret; assert unseal raises.

### M-04 — Unauthenticated exception text returned to the client
- **SEVERITY:** MEDIUM · **CONFIDENCE:** CONFIRMED · **STATUS:** CONFIRMED
- **CODE LOCATION:** `api/routes/intelligence.py:29-31` —
  `detail=f"Analysis failed: {type(exc).__name__}: {str(exc)[:300]}"` on an
  **unauthenticated** route.
- **IMPACT:** Leaks internal file paths, library versions, and (depending on driver error
  text) database connection detail.
- **RECOMMENDED FIX:** Log the detail server-side; return the exception *type* only.

### M-05 — All Python dependencies unpinned
- **SEVERITY:** MEDIUM · **CONFIDENCE:** CONFIRMED · **STATUS:** CONFIRMED
- **COMPONENT:** `requirements.txt`, `requirements-core.txt`
- **EVIDENCE:** every entry uses `>=` (`fastapi>=0.115`, `requests>=2.31`, `pypdf>=4.0`, …).
- **IMPACT:** Builds are not reproducible; a new or hijacked release is installed silently.
  Transitive deps are entirely uncontrolled.
- **RECOMMENDED FIX:** Pin exact versions with hashes; refresh deliberately.
- **NOTE:** Contradicts the model pins, which *are* exact (`revision=` at load).

---

## LOW FINDINGS

### L-01 — `key_prefix` persisted in the request log
`backend/auth/request_log.py:216` stores `str(key_prefix)[:20]`. A 20-character prefix of a
CSPRNG key leaks a small amount of key entropy and reduces brute-force space. Low because
the key is high-entropy and the prefix is a fraction of it — but a prefix is not needed for
the observability use case; store a hash prefix or nothing.

### L-02 — `_ExistingAccountingAdapter` accepts an inert `amount` parameter
`backend/kernel/kernel.py:629-632` passes an amount that is provably ignored. Dead
parameter that invites a future maintainer to trust it. Remove it or document it as
ignored-by-design.

### L-03 — Empty-string `referenced_transaction_index` fails grounding
`""` is not `None`, so Rule 6 tries `int("")` and raises a grounding issue. Fails *closed*
(the safe direction), but a schema-valid empty string should normalize to `None`.

---

## UNCONFIRMED

| Item | Why unconfirmed | What would confirm it |
|---|---|---|
| Prompt injection → unsafe IR | No model reachable: Modal undeployed (404), HF ZeroGPU quota exhausted, no Phase H weights locally | Deploy `training/modal_inference.py`, run adversarial text through `POST /interpret` |
| Document/OCR decompression bomb | Path exists (`/v1/documents`, 16 MB cap) but the extractor was not executed | Feed a zip-bomb/malformed PDF through the worker |
| Render production env | Not observable from here | `freebuff-deploy env list` / Render dashboard |
| Cross-tenant leak in the 5H/5G planes | Code review shows correct scoping; no live DB | Two-tenant integration test against a real metering store |
| SSRF via `market/{ticker}` | Ticker flows to providers; provider internals not traced | Trace yfinance/FMP URL construction |

---

## TEST GAPS

1. **No test asserts `transaction_type` grounding fails closed.** This is precisely the gap
   that let Rule 5 fail open while every other rule was covered. The suite proved the
   *other* rules work, which is what makes the missing one easy to miss.
2. **No amount-substring-collision test** (`"90"` vs `90,000`).
3. **No webhook redirect test** — the SSRF is invisible to the suite because only
   registration-time validation is tested.
4. **No rate-limit tests**, because no limiter exists.
5. **No authentication tests for `/api/v1/*`**, because it is unauthenticated by design —
   the absence is invisible rather than flagged.
6. **No tamper test for the webhook secret seal** (M-03 is unauthenticated encryption and no
   test would notice).
7. Suite-level note: `scripts/fte_fyjc_57_remote_provider_test.py` (43/43) validates the
   *Modal contract*, not adversarial model behavior. A green suite here is not evidence
   about injection resistance.

---

## AUDIT CORRECTIONS (added after regression-test evidence)

Three claims in the first draft of this audit were **wrong**. They were found by
`scripts/fte_sec_02_api_authz_admission_test.py` while writing regression tests, not by
re-review. The findings H-01 and H-02 **survive**; the supporting claims did not.

### CORRECTION 1 — `max_iterations` is bounded, not unbounded

**Wrong claim:** the first draft described `max_iterations` as client-controlled and
unbounded, and recommended clamping it server-side.

**Fact:** `api/schemas.py::AnalyzeRequest.max_iterations` carries
`Annotated[int, Ge(ge=1), Le(le=5)]` with `default=3`. It is already bounded to 1–5.

**Correction:** no clamp is needed and none is implemented. `fte_sec_02` check **E2**
asserts the bound exists, so a future change cannot silently loosen it.

### CORRECTION 2 — request bodies are already bounded

**Wrong claim:** the first draft implied a 2 MiB `raw_input` could be accepted because
the body-size guard in `api/main.py` only covers `/v1/*`.

**Fact:** `KernelProcessRequest.raw_input` carries `MaxLen(max_length=2000)`, plus
`MinLen(1)`. `ticker` is `MaxLen(20)` and `goal` is `MaxLen(1000)`. A 2 MiB body is
rejected with HTTP 422 by request-schema validation, before any handler runs.

**Correction:** body size is **not** a vulnerability and the body-size guard is **not**
reclassified as a defect. `fte_sec_02` checks **E3/E4** verify the existing bound.

### CORRECTION 3 — the rate-limit detector was a false positive

**Wrong claim:** the first draft's detector searched for the literal `"429"` among other
markers and therefore reported that a rate limiter existed.

**Fact:** `"429"` appears in `api/routes/developer.py` as the HTTP status for
`QUOTA_EXHAUSTED` — a monthly tenant quota response, not request-rate limiting. The
marker was a false positive in the *detector*.

**Correction:** the detector now matches limiter constructs only (`RateLimit`, `Limiter`,
`rate_limit`, `slowapi`, …). It confirms **no request-rate limiter exists** on the
affected surface, so **H-02 survives** — but its wording is now narrowed (below).

### H-02, restated narrowly

> **Unauthenticated endpoints permit repeated request admission without an
> independent request-rate limiter.**

H-02 does **not** claim: that `max_iterations` is unbounded; that individual requests
carry unlimited computational parameters; that request bodies are unbounded; or that
quota exhaustion demonstrates rate limiting. Per-request cost is already bounded by
schema constraints (CORRECTIONS 1 and 2). The exposure is **unbounded request COUNT**
against unauthenticated, state-changing, and provider-fanning-out routes.

---

## TOP 10 SECURITY RISKS

1. `/api/v1/*` unauthenticated (H-01)
2. No rate limiting (H-02)
3. Webhook redirect SSRF → cloud metadata (H-03)
4. Cost amplification: unauthenticated RAG loop reachable an unlimited number of times (H-01/H-02). Per-request work is bounded; request *count* is not.
5. Grounding gate fails open on `transaction_type` (M-01)
6. Amount grounding satisfiable by digit substring (M-02)
7. Webhook secret malleable — no AEAD (M-03)
8. Unauthenticated exception text disclosure (M-04)
9. Unpinned dependency supply chain (M-05)
10. Provider/env-var fingerprinting via `/providers/status` (Q-01 below)

---

## TOP 5 FIXES TO DO FIRST

1. **Authenticate or remove `/api/v1/db/init` and `/api/v1/intelligence/analyze`.** Smallest
   change with the largest blast-radius reduction.
2. **Add rate limiting** to `/v1/*` and a much tighter one to `/api/v1/*`.
3. **Add `allow_redirects=False` + connect-time IP re-validation** to webhook delivery.
   One line for the redirect; the re-validation closes DNS rebinding.
4. **Append to `issues` in grounding Rule 5** and add the regression test. One line, and it
   removes a fail-open from a safety gate.
5. **Stop returning `str(exc)` to unauthenticated clients** (M-04).

---

## SECURITY CONTROLS THAT ARE ALREADY STRONG

These are real, and the audit did not find a way around them:

- **Model→authority boundary: the strongest control in the system.** Accounting is a pure
  function of `raw_input`. Model fields cannot alter a posting. Verified by flipping every
  enum and by passing `amount=0`/`1e9`/`999999` directly to the adapter.
- **`process_accounting` type-gates its input** — a non-`GroundedSemanticIR` raises
  `SemanticIRError`; accounting cannot accept a raw candidate.
- **No SQL injection.** Every query is SQLAlchemy-parameterized. Zero raw f-string SQL in
  `api/`, `backend/`, `platrixa/`. `async_jobs.get_job` scopes by `tenant_id AND job_id` in
  the query itself.
- **Management plane is well built.** Constant-time `hmac.compare_digest` over SHA-256
  digests, tenant-bound, fail-closed on misconfiguration, and runtime API keys have no
  management power by construction.
- **Quota reservation is a single atomic UPDATE** carrying the whole admission predicate —
  no read-modify-write race.
- **No `shell=True` anywhere.** `subprocess` calls use argv lists; the C++ formula engine
  passes data on stdin, not argv.
- **API keys are SHA-256 hashed** and never persisted or logged; only a truncated prefix.
- **Request log stores metadata only** — raw financial input is never persisted.
- **Model identity is pinned by exact revision**, adapter load is fail-closed with no
  base-only fallback, and a mismatched identity fails closed.
- **Frontend management token is memory-only**, never in `localStorage`/`sessionStorage`,
  never logged.
- **Document `source_name` is sanitized and never used as a filesystem path.**

---

## SECURITY AREAS THAT REMAIN UNPROVEN

Prompt injection (no model reachable) · document/OCR resource exhaustion · live
multi-tenant isolation against a real database · provider-URL construction in the market
data path · production Render environment · whether the V2 frontend introduces anything
new.

---

## RELEASE BLOCKERS

**No release blocker on financial-truth grounds.** No path was found to a false `VERIFIED`,
to model output reaching the accounting authority, or to cross-tenant disclosure. The
model→authority boundary held under every probe.

**One operational blocker if public traffic is expected:**

- `/api/v1/*` is unauthenticated, unrated, and fans out to paid providers and the model
  (H-01 + H-02). Before exposing the Render host to the open internet, either put these
  behind the metered gate or accept unbounded third-party cost. This is an availability and
  cost risk, not a financial-integrity risk.

---

*Audit produced no code changes at the time of writing. Working tree unchanged at
`87ff8e1`; 69 pre-existing entries byte-identical. No commits, no pushes. The frozen
Phase H dataset and evaluation artifacts were not modified.*

*Corrections 1–3 were added after `scripts/fte_sec_02_api_authz_admission_test.py`
surfaced three errors in the original analysis. See "AUDIT CORRECTIONS" above.*
