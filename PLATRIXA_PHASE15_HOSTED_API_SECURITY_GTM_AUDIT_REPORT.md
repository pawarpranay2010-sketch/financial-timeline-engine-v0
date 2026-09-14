# PLATRIXA — PHASE 15 REPORT
## Hosted API Security, Tenant Isolation & Public Launch Audit

**Date:** 2026-09-14
**Classification:** **PARTIAL** (one non-critical area incomplete: per-developer key issuance does not exist and was NOT fabricated)

---

## 1. Executive summary

The hosted developer API (`/v1/*`) was audited as an untrusted-Internet
boundary. The audit **found and fixed two real gaps** (framework-default
422 for malformed HTTP instead of a deterministic 400; no authentication
mechanism at all), **caught and fixed one production bug** introduced
during hardening (`/v1/ready` broken by a signature change — caught by
the Phase 13 regression suite), and **proves** with a dedicated 47-check
suite that hostile/malformed/unauthorized requests cannot reach the
Kernel, that rule/hook configuration cannot be injected or cross-contaminated
between requests (including concurrent interleaving), and that the
VERIFIED authority chain is intact. GTM claims in the README were
rewritten to match only what is verified. Public launch of the current
single-key endpoint is defensible; per-developer key issuance/metering
does **not** exist and is reported as the main launch limitation.

## 2. Current hosted architecture (verified, not assumed)

- App: `api/main.py` `create_app()` (FastAPI), routes `/api/v1/*`
  (browser, Phase 7F) and `/v1/*` (developer, Phase 13).
- Public endpoints: `POST /v1/process`, `GET /v1/health`, `GET /v1/ready`.
- Request schema: `KernelProcessRequest` — **exactly one field**,
  `raw_input: str (1..2000)` (proven: `model_fields == {"raw_input"}`).
- Auth: **none existed before Phase 15** (verified by repository scan:
  only provider env-var *names* matched "API_KEY"). Added in this phase
  (see §5).
- Rule configuration: **server-side env only**
  (`PLATRIXA_RULE_PACK_PATH`). The HTTP surface carries no `rules_path`,
  no rule config, no hooks — so §4/§8/§9 of the phase spec's
  request-supplied-rule attack surface **does not exist by design**; the
  audit proves that instead of inventing it.
- Kernel: constructed per `Platrixa` client; one `Kernel.process` call
  site in the route; no module-level Kernel singleton in the API layer.
- Deployment: Render auto-deploys `main` (current build `d64e49e`).

## 3. Threat model

Adversary = an unknown Internet developer who can send arbitrary HTTP to
`/v1/*`: malformed payloads, oversized bodies, header manipulation,
attempts to read server files via rule paths, attempts to inject
executable rule code, attempts to force `VERIFIED`, and concurrent
request patterns probing for cross-request state bleed.

## 4. Input-malformation audit (fixed)

**Before (measured):** FastAPI/Pydantic default → **422** for invalid
JSON, empty body, wrong content type, missing/wrong-typed fields.
**After:** a `/v1`-scoped `RequestValidationError` handler normalizes all
of these to **400** with the deterministic envelope
`{"api_version":"v1","error":{"code":"REQUEST_MALFORMED","fields":[{field,reason}]}}`
— field paths and error types only; **raw input values are never echoed**
(the Pydantic default included attacker input, e.g. `input: 12345`).
Browser `/api/v1` keeps framework behavior (Phase 7F suite 61/61 green —
no silent contract change). Domain-level rejections keep their documented
statuses: whitespace-only text → 422 `INPUT_INVALID` (facade contract),
oversized → 413, provider down → 503, unexpected → 500 (type name only).
Valid request → 200 with exactly one Kernel call.

## 5. Authentication audit (implemented + audited)

New, minimal, HTTP-boundary-only: `PLATRIXA_DEV_API_KEY` (server env).

| Check | Result |
|---|---|
| No key configured | endpoint open (documented zero-config local mode) |
| Key configured, request without key | **401** before any processing (Kernel calls = 0) |
| Empty / wrong / trailing-space key | **401** |
| Valid key | 200, exactly one Kernel call |
| Constant-time comparison | yes (SHA-256 + `hmac.compare_digest` — length-independent) |
| Key in response / logs / errors / evidence | never (B6/B10/B11 prove) |
| Secrets loaded from intended source | server env at request time; no hardcoded values; no `os.environ` writes |

**Reported honestly (not fabricated):** there is **no per-developer key
issuance, sandbox-key flow, usage metering, or billing**. The phase
prompt's example header `X-Platrixa-API-Key` is now real, but the README
does not invent a `sandbox_token_free` credential and the quickstart
references the operator's own env var.

## 6. Tenant-isolation audit

Shared-state inventory (route module): `router` (immutable), `API_VERSION`
(constant), `_REQUEST_ID_RE` (constant), `logger` (safe). Two client
seams exist:

- **App-scoped `app.state.platrixa_client`** — the preferred, concurrency-
  safe seam (each app instance carries its own fully-built client).
- Module-level `_get_client._override` — **classified UNSAFE-SHARED for
  multi-tenant serving** (the cross-tenant test initially exposed a race
  through it); retained ONLY as a legacy single-tenant test seam, and the
  isolation suite now exercises the app-scoped seam.

The Kernel/RuleEngine stack itself is fully per-instance: two clients
with different packs hold distinct engines with disjoint rule sets
(demonstrated), and evidence lists are built per request (Phase 10).

## 7. RulePack isolation audit (cross-tenant adversarial test)

Because the HTTP surface cannot carry rule inputs, tenant isolation is
proven at the configuration layer: tenant A (pack A: required-field rule
that FAILS the candidate → downgrade) and tenant B (pack B: threshold
rule that PASSES → VERIFIED preserved) were exercised **interleaved
A/B/A/B…, reversed, and 4-concurrent**. Results: A's evidence contains
only `pack_a_invoice_required`, B's only `pack_b_amount_ceiling`
(A∩B = ∅), A always REVIEW_REQUIRED, B always VERIFIED — no configuration
or evidence crossed tenants.

## 8. Rule-path security audit

`rules_path` over HTTP is **structurally impossible**: no request field,
no query parameter, no header is read into rule resolution (only
`PLATRIXA_RULE_PACK_PATH` from server env). Arbitrary filesystem
traversal via the public API cannot occur. Verified: `KernelProcessRequest`
has exactly one field; the route reads no rule input from the request.

## 9. Python-hook security audit

The public API cannot select, import, or execute Python code: the request
carries no hook/module field, and the route has zero references to the
rules package or import machinery. Hooks remain **server-side trusted
configuration only** (Kernel constructor), consistent with Phase 10/13.
Hook semantics through the API re-proven: PASS preserves the kernel
state; FAIL/UNAVAILABLE/ERROR all downgrade (never success); a hook
smuggling `decision_hint: VERIFIED` is rejected and recorded
(`decision_hint_rejected`) — the public API cannot independently produce
VERIFIED (no status literals in executable route code — AST-proven).

## 10. Concurrency audit

Concurrent interleaving of two differently-configured tenants (4 threads)
produced zero cross-contamination (§7). The one shared-state hazard found
(module-global test override) is documented and bypassed by the app-scoped
seam. No global request/rule state exists in the route module.

## 11. Error-handling audit (layer-correct mapping)

| Layer | Status |
|---|---|
| Malformed HTTP (parse/shape) | 400 `REQUEST_MALFORMED` |
| Body too large (pre-parse) | 413 `REQUEST_TOO_LARGE` |
| Domain input contract (facade) | 422 `INPUT_INVALID` |
| Missing/invalid API key | 401 `UNAUTHORIZED` |
| Kernel failure states | verbatim body status; 200/422/503 per Phase 7F mapping |
| Provider runtime failure | 503 `PROVIDER_UNAVAILABLE` |
| Unexpected exception | 500, type name only (global handler) |

Deterministic, machine-readable, no stack traces/paths/secrets
(leakage scan on 400/401 bodies passed).

## 12. Secret-handling audit

No secrets in: responses (incl. 401/400 bodies), logs (financial content
and keys excluded by design — proven by log capture), evidence, or this
report. The gate reads `PLATRIXA_DEV_API_KEY` at request time; no
credential literals exist in the route; no env writes.

## 13. Resource-abuse audit

Existing limits (verified): body ≤ 64 KiB pre-parse (middleware); input
≤ 2000 chars (schema); **exactly one runtime invocation site** in the
route; no loops around processing; no retries anywhere; no rule data
loading from requests. Missing limits (documented): no per-key rate
limiting / metering (platform-level controls only); model latency/quota
on the HF path remains the practical throughput bound. No quota/billing
system was built (per phase scope).

## 14. Kernel routing proof

Dynamic: malformed/auth-failed requests → **Kernel calls = 0**; each
valid request → **exactly 1**; 2 valid requests → 2 (no hidden second
invocation). Static: the route references no backend internals
(accounting/grounding/schema/rules/persistence/providers), performs no
persistence, holds no status literals — the boundary routes; it does not
re-implement.

## 15. Test results

**New Phase 15 suite `fte_fyjc_65`: 47/47 PASS** (A malformation gate ·
B auth · C routing · D rule-pack isolation · E evidence/authority ·
F error mapping · G secrets · H quickstart reality).

## 16. Regression results

All suites green after the changes: 7B 15 · 7C 20 · 7D 13 · 7E 80 ·
7F 61 · 7G 46 · 7H 40 · 7R 43 · 7T 49 · Phase 9 20 · Phase 10 46 ·
Phase 12 65 · **Phase 13 55** (3 checks updated to the *documented*
Phase 15 contract change: malformed → 400, stated explicitly, not
silently) · legacy persistence 18 OK · **Phase 15 47**.
The regression run also **caught a real bug**: `/v1/ready` broke after
the `_get_client(request)` signature change (NameError → not_ready);
fixed and re-verified (suite 62 Q1/Q2 green).

## 17. README/GTM changes

`README.md` now leads with the verified positioning — **"Stop prompts
guessing math."** — the model-interprets/runtime-decides architecture,
and a hosted quickstart using the **real** request/response shapes
(`suggested_status: REVIEW_REQUIRED` vs final `VERIFIED` shown as the
deliberate authority distinction) plus the real malformed-input
demonstration (`REQUEST_MALFORMED`, 400). No invented states (no
`CONTAINMENT_TRIGGERED`), no fake credentials, no SLA/security claims,
no "production-ready" claim. Target users (FYJC students first) retained;
no unsubstantiated paying-customer claims. GTM structure
(infrastructure problem → hosted demo → developer CTA) matches what the
repo proves: AI interprets + Platrixa grounds + deterministic rules +
computation + verification + evidence.

## 18. Public launch checklist

| Area | Status |
|---|---|
| HTTP malformed input | **PASS** (400 normalization, fail-closed, Kernel=0) |
| Schema validation | **PASS** (single-field contract; typed; bounded) |
| Authentication | **PARTIAL** (real single-key gate, constant-time, fail-closed; no per-developer issuance/metering yet) |
| Token isolation | **PASS** (app-scoped seam; global seam demoted to test-only) |
| RulePack isolation | **PASS** (no HTTP rule surface; server-env scoped; cross-tenant interleaved/concurrent proof) |
| Rule path security | **PASS** (structurally impossible via HTTP) |
| Python hook security | **PASS** (no HTTP code-execution surface) |
| Kernel routing | **PASS** (exactly-once; zero internals; zero status literals) |
| VERIFIED authority | **PASS** (suggestion ≠ final state proven live; smuggle sanitized) |
| Concurrency | **PASS** (concurrent cross-tenant test clean) |
| Error handling | **PASS** (layer-correct, deterministic envelopes) |
| Secret handling | **PASS** (no leakage in responses/logs/evidence) |
| Resource abuse | **PARTIAL** (size/loop/retry bounds exist; no per-key rate limiting) |
| Quickstart curl | **PASS** (verified shapes; operator-supplied key env var) |
| README accuracy | **PASS** (claims audited against executed evidence) |
| Regression suite | **PASS** (14 suites + Phase 15, 0 failures) |

**Launch posture:** the current single-key endpoint is defensible for a
controlled public beta. **Not launch-ready** for multi-tenant public
availability: per-developer keys, metering, and rate limiting do not
exist (honestly reported, not fabricated).

## 19. Known limitations

1. Single shared API key; no per-developer issuance, rotation UX, metering,
   or billing.
2. No application-level rate limiting.
3. Module-level test seam (`set_client`) remains for older suites — must
   never be used for multi-tenant serving (documented in code).
4. Model latency/quota (ZeroGPU) bounds real-world throughput.
5. No wheel/packaging; sys.path-based deployment (Phase 11A finding).

## 20. Recommended next phase

**Phase 16 (evidence-backed): per-developer API keys + metered quota** —
the only PARTIAL security row (Authentication) and the only PARTIAL
resource-abuse row (rate limiting) share one root cause: no per-tenant
identity at the HTTP boundary. Issuing per-developer keys (hashed
storage), scoping request logs/evidence by key id, and a simple
per-key rate/quota check would complete both rows without touching the
Kernel, grounding, accounting, or rule authority. Secondary candidate:
persistence re-verification of the Phase 14 evidence on a stable DB
window (carries over unchanged from Phase 14).

## 21. Git safety (no commit, no push)

```
HEAD: d64e49e
tracked modifications (3):
  M api/main.py            (+9: handler registration)
  M api/routes/developer.py (+~150: 400 normalization, auth gate, app-scoped seam)
  M README.md              (+57: verified hosted-API quickstart/GTM section)
new files (2):
  scripts/fte_fyjc_65_hosted_api_security_test.py
  PLATRIXA_PHASE15_HOSTED_API_SECURITY_GTM_AUDIT_REPORT.md
updated (documented contract change): scripts/fte_fyjc_62_… (3 checks)
docs: docs/HOSTED_API.md (auth section, error table, examples, limits)
```

No `hf_space/`, no secrets, no `.env`, no unrelated artifacts staged or
touched. All 45+ pre-existing untracked artifacts untouched.
