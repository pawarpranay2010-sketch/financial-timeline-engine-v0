#!/usr/bin/env python3
"""
PLATRIXA — SECURITY REGRESSION: API AUTHN / ADMISSION CONTROL (fte_sec_02)
=========================================================================

Captures two findings from the 2026-09-29 security audit
(reports/security_audit_2026-09-29.md) BEFORE any fix is applied.

  H-01  The entire /api/v1/* surface is UNAUTHENTICATED. Verified in the
        audit by sweeping every @router decorator: kernel.py, health.py,
        intelligence.py and market.py declare no `dependencies=[...]`.
        Exposed anonymously:
            POST /api/v1/db/init                 (state-changing)
            POST /api/v1/intelligence/analyze    (agentic RAG loop)
            POST /api/v1/kernel/process          (model inference + DB write)
            GET  /api/v1/market/{ticker}         (paid provider fanout)
            GET  /api/v1/market/{ticker}/price
            GET  /api/v1/providers/status         (env-var/key fingerprint)
            GET  /api/v1/health

  H-02  There is NO rate limiting anywhere in the application. The only
        admission control is the per-tenant monthly quota in
        backend/auth/gate.py, which by design does not apply to
        /api/v1/*. A cheap HTTP request can trigger arbitrarily expensive
        downstream work (the agentic RAG loop fans out to many provider
        calls per request).

SAFETY DESIGN — NO REAL EXPENSIVE WORK IS EVER PERFORMED
--------------------------------------------------------
Each expensive downstream service is replaced with a CANARY that records
"work was attempted" and returns a trivial response. The test therefore
proves *whether authentication was enforced* without ever calling a real
LLM, a real provider, or a real database.

  * If the endpoint REJECTS anonymous access  -> canary never fires -> PASS
  * If the endpoint ALLOWS anonymous access  -> canary fires -> FAIL

This makes the suite safe to run on any machine, online or offline.

These tests are EXPECTED TO FAIL on the current tree. They define what
"fixed" means for H-01 and H-02. Do not weaken them.

Run:  python3 scripts/fte_sec_02_api_authz_admission_test.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Tuple

warnings.filterwarnings("ignore")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import api.services as svc  # noqa: E402
from api.main import create_app  # noqa: E402

# ---------------------------------------------------------------------------
# Shared check machinery (house style, mirrors fte_fyjc_57 / fte_fyjc_59)
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_MESSAGES: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
        _MESSAGES.append(f"{name} — {detail}")
    return ok


def expected_failure(name: str, detail: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL] {name} — {detail}")
    _MESSAGES.append(f"{name} — {detail}")


def note(text: str) -> None:
    print(f"  [NOTE] {text}")


# ---------------------------------------------------------------------------
# Canary machinery — proves work was attempted without doing it
# ---------------------------------------------------------------------------

_FIRED: List[str] = []


class _CanaryInstalled(Exception):
    """Raised only if a canary is somehow invoked outside the harness."""


def _arm_canaries() -> Dict[str, Any]:
    """Replace every expensive downstream service with a recording stub."""
    _FIRED.clear()

    def _canary(name: str):
        def _fn(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            _FIRED.append(name)
            # Minimal shape the route can serialize; deliberately not a real
            # provider/database result.
            return {"success": False, "error": "canary", "canary": name}
        return _fn

    originals = {
        "run_analysis": svc.run_analysis,
        "fetch_market_snapshot": svc.fetch_market_snapshot,
        "initialize_database_schema": svc.initialize_database_schema,
    }
    svc.run_analysis = _canary("run_analysis")
    svc.fetch_market_snapshot = _canary("fetch_market_snapshot")
    svc.initialize_database_schema = _canary("initialize_database_schema")
    return originals


def _restore_canaries(originals: Dict[str, Any]) -> None:
    svc.run_analysis = originals["run_analysis"]
    svc.fetch_market_snapshot = originals["fetch_market_snapshot"]
    svc.initialize_database_schema = originals["initialize_database_schema"]


def _fired() -> List[str]:
    return list(_FIRED)


# ---------------------------------------------------------------------------
# Credential fixtures
# ---------------------------------------------------------------------------

NO_CREDENTIALS: Dict[str, str] = {}
MALFORMED_CREDENTIALS: Dict[str, str] = {
    "X-Platrixa-API-Key": "",                          # empty
    "X-Platrixa-Management-Token": "",
}
INVALID_CREDENTIALS: Dict[str, str] = {
    "X-Platrixa-API-Key": "plx_not_a_real_key_0000000000000000000000000000",
    "X-Platrixa-Management-Token": "not-a-real-management-token",
}
# A syntactically plausible runtime key. With no metering store configured
# the gate cannot know it, so it MUST still be rejected.
RUNTIME_CREDENTIALS: Dict[str, str] = {
    "X-Platrixa-API-Key": "plx_" + "a" * 43,
}
# Management-plane credential. Must NOT be accepted on the data plane.
MANAGEMENT_CREDENTIALS: Dict[str, str] = {
    "X-Platrixa-Management-Token": "operator-management-token",
}

CREDENTIAL_SETS: List[Tuple[str, Dict[str, str]]] = [
    ("no credentials", NO_CREDENTIALS),
    ("malformed credentials", MALFORMED_CREDENTIALS),
    ("invalid credentials", INVALID_CREDENTIALS),
    ("valid runtime credentials", RUNTIME_CREDENTIALS),
    ("management credentials", MANAGEMENT_CREDENTIALS),
]

# Endpoints that must never be usable anonymously. (method, path, body,
# canary that proves downstream work was reached)
PROTECTED_ENDPOINTS: List[Tuple[str, str, Dict[str, Any], str]] = [
    ("POST", "/api/v1/db/init", {}, "initialize_database_schema"),
    ("POST", "/api/v1/intelligence/analyze",
     {"ticker": "AAPL", "goal": "analyse revenue", "max_iterations": 1},
     "run_analysis"),
    ("GET", "/api/v1/market/REGRESSION-CANARY", None, "fetch_market_snapshot"),
    ("GET", "/api/v1/market/REGRESSION-CANARY/price", None, "fetch_market_snapshot"),
]


# ---------------------------------------------------------------------------
# A. Enumerate the real surface (no hardcoded assumptions)
# ---------------------------------------------------------------------------


def test_enumerate_api_v1_surface() -> None:
    print("\n--- A. Enumerate the live /api/v1 surface ---")
    spec = create_app().openapi()
    paths = sorted(p for p in spec["paths"] if p.startswith("/api/v1"))
    check("A1 /api/v1 routes discovered from the live app", bool(paths),
          f"count={len(paths)}")
    for p in paths:
        methods = sorted(m.upper() for m in spec["paths"][p])
        print(f"        {','.join(methods):<12} {p}")

    discovered = {f"{m.upper()} {p}" for p in paths for m in spec["paths"][p]}
    for method, path, _b, _c in PROTECTED_ENDPOINTS:
        # market/{ticker} is a path template in the spec
        spec_path = path.split("REGRESSION-CANARY")[0] + "{ticker}"
        spec_path = spec_path.replace("/price/price", "/price")
        if method.upper() == "GET" and "market" in path:
            check(f"A2 {method} {path} is a real route",
                  f"{method.upper()} {spec_path}" in discovered,
                  f"spec={spec_path}")
        else:
            check(f"A2 {method} {path} is a real route",
                  f"{method.upper()} {path}" in discovered)


# ---------------------------------------------------------------------------
# B. H-01 — anonymous access to protected /api/v1 endpoints
# ---------------------------------------------------------------------------


def test_anonymous_access_rejected() -> None:
    print("\n--- B. H-01: anonymous access to protected endpoints "
          "(EXPECTED TO FAIL today) ---")
    originals = _arm_canaries()
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        for method, path, body, canary in PROTECTED_ENDPOINTS:
            resp = client.request(method, path, json=body) if body is not None \
                else client.request(method, path)

            work = canary in _fired()
            rejected = resp.status_code in (401, 403)

            if work or not rejected:
                expected_failure(
                    f"B {method} {path} rejects anonymous access",
                    f"H-01: anonymous request reached the endpoint "
                    f"(HTTP {resp.status_code}); downstream work "
                    f"{'WAS' if work else 'was not'} performed "
                    f"(canary={canary})",
                )
            else:
                check(f"B {method} {path} rejects anonymous access", True,
                      f"HTTP {resp.status_code}")
    finally:
        _restore_canaries(originals)


# ---------------------------------------------------------------------------
# C. H-01 — no credential class grants access either
# ---------------------------------------------------------------------------


def test_credential_classes_rejected() -> None:
    print("\n--- C. H-01: every credential class must be rejected ---")
    originals = _arm_canaries()
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        for label, headers in CREDENTIAL_SETS:
            if label == "no credentials":
                continue  # already covered in section B
            for method, path, body, canary in PROTECTED_ENDPOINTS:
                _FIRED.clear()
                resp = client.request(method, path, json=body, headers=headers) \
                    if body is not None else client.request(method, path, headers=headers)
                work = canary in _fired()
                if work:
                    expected_failure(
                        f"C {label}: {method} {path}",
                        f"H-01: request with {label} reached downstream work "
                        f"(HTTP {resp.status_code})",
                    )
    finally:
        _restore_canaries(originals)

    # Summary line so an all-pass run is unambiguous.
    if not any(m.startswith("C ") for m in _MESSAGES):
        check("C every credential class rejected on every protected endpoint",
              True, "no downstream work reached")


# ---------------------------------------------------------------------------
# D. H-01 — information disclosure on the public /api/v1 surface
# ---------------------------------------------------------------------------


def test_information_disclosure() -> None:
    print("\n--- D. H-01: information disclosure (EXPECTED TO FAIL today) ---")
    client = TestClient(create_app(), raise_server_exceptions=False)

    # D1 — /providers/status discloses provider env-var NAMES and whether a
    # key is configured. Anonymous enumeration of the deployment's secrets.
    resp = client.get("/api/v1/providers/status")
    body = resp.text
    if resp.status_code == 200 and "env_var" in body:
        expected_failure(
            "D1 /api/v1/providers/status must not be anonymous",
            "H-01: unauthenticated caller obtained provider env-var names and "
            f"key-presence flags ({len(body)} bytes)",
        )
    else:
        check("D1 /api/v1/providers/status must not be anonymous", True,
              f"HTTP {resp.status_code}")

    # D2 — /api/v1/health is DELIBERATELY PUBLIC.
    #
    # CORRECTION to the original assertion: requiring a credential here
    # would break the deployment. render.yaml sets
    #   healthCheckPath: /api/v1/health
    # and the production UI polls HEALTH_ENDPOINT = "/api/v1/health".
    # Gating it would make Render mark the service unhealthy.
    #
    # The correct security property is therefore NOT "must require auth" but
    # "must be public AND must not disclose sensitive internals". This is a
    # content assertion, not a status-code one.
    resp = client.get("/api/v1/health")
    if resp.status_code != 200:
        expected_failure(
            "D2 /api/v1/health must remain reachable (Render health check)",
            f"health endpoint returned HTTP {resp.status_code}; render.yaml "
            "declares healthCheckPath: /api/v1/health, so a non-200 marks "
            "the service unhealthy",
        )
    else:
        check("D2 /api/v1/health must remain reachable (Render health check)",
              True, "HTTP 200")

    health_body = resp.text or ""
    forbidden_in_health = [
        marker for marker in
        ("DATABASE_URL", "PLATRIXA_METERING_DATABASE_URL", "postgresql://",
         "password", "psycopg2", "Traceback", "/srv/", "sk-", "hf_")
        if marker.lower() in health_body.lower()
    ]
    if forbidden_in_health:
        expected_failure(
            "D2b /api/v1/health must not disclose credentials or internals",
            f"H-01: public health response leaked {forbidden_in_health}",
        )
    else:
        check("D2b /api/v1/health must not disclose credentials or internals",
              True, f"body={health_body[:100]}")

    # D3 — the OpenAPI schema is served anonymously, enumerating every route
    # and payload schema. (Often intentional; documented, not asserted as a
    # defect unless the deployment expects it closed.)
    spec_resp = client.get("/openapi.json")
    if spec_resp.status_code == 200:
        note("GET /openapi.json is served anonymously (route/schema enumeration) "
             "— documented observation, not asserted as a defect")


# ---------------------------------------------------------------------------
# E. H-02 — admission control / rate limiting
# ---------------------------------------------------------------------------


def test_admission_control_exists() -> None:
    print("\n--- E. H-02: admission control on expensive endpoints "
          "(EXPECTED TO FAIL today) ---")

    # E0 — does the application implement ANY rate limiting at all?
    #
    # NOTE: a naive marker search gives a FALSE POSITIVE here. The literal
    # "429" appears in api/routes/developer.py as the HTTP status for
    # QUOTA_EXHAUSTED, which is a monthly quota response, NOT rate
    # limiting. The markers below therefore name limiter constructs only.
    limiter_markers = (
        "RateLimit", "SlowAPI", "slowapi", "Limiter", "limiter",
        "rate_limit", "ratelimit", "rate_limit_window",
        "requests_per_minute", "burst_limit",
    )
    import api as api_pkg
    import backend.auth as auth_pkg
    hits: List[str] = []
    for mod in (api_pkg, auth_pkg):
        root = Path(mod.__file__).parent
        for p in root.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            try:
                body = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in limiter_markers:
                if m in body:
                    hits.append(f"{p.relative_to(root)}:{m}")
    if hits:
        check("E0 an application-level rate limiter exists", True,
              f"markers={hits[:4]}")
    else:
        expected_failure(
            "E0 an application-level rate limiter exists",
            "H-02: no rate limiting anywhere in api/ or backend/auth/ — the "
            "only admission control is the per-tenant monthly quota, which "
            "does not apply to /api/v1/*. (The 429 status in developer.py is "
            "QUOTA_EXHAUSTED, not a rate limiter.)",
        )

    # E1 — repeated anonymous calls must eventually be refused (429/403/401).
    originals = _arm_canaries()
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        statuses: List[int] = []
        for _ in range(25):
            resp = client.post("/api/v1/intelligence/analyze",
                               json={"ticker": "AAPL", "goal": "abuse",
                                     "max_iterations": 1})
            statuses.append(resp.status_code)
        refused = any(s in (401, 403, 429) for s in statuses)
        if refused:
            check("E1 repeated anonymous calls are refused", True,
                  f"statuses={sorted(set(statuses))}")
        else:
            expected_failure(
                "E1 repeated anonymous calls are refused",
                f"H-02: 25 consecutive anonymous calls to the agentic RAG "
                f"endpoint were all admitted (statuses={sorted(set(statuses))}); "
                "unbounded expensive work per caller",
            )
    finally:
        _restore_canaries(originals)

    # E2 — max_iterations must be server-bounded.
    #
    # CORRECTION: the original audit claimed max_iterations was
    # client-controlled with no upper bound. That was WRONG — pydantic
    # constrains it to Ge(1)/Le(5). This test therefore PASSES today and
    # exists to keep the bound from being loosened.
    from api.schemas import AnalyzeRequest
    try:
        field = AnalyzeRequest.model_fields.get("max_iterations")
        constraint = None
        if field is not None:
            for m in (getattr(field, "metadata", []) or []):
                le = getattr(m, "le", None)
                ge = getattr(m, "ge", None)
                if le is not None or ge is not None:
                    constraint = f"ge={ge}, le={le}"
        if constraint:
            check("E2 max_iterations is server-bounded", True,
                  f"{constraint} (default={getattr(field, 'default', None)})")
        else:
            expected_failure(
                "E2 max_iterations is server-bounded",
                "H-02: max_iterations has no upper bound; one request can "
                "drive an arbitrarily long agentic retrieval loop",
            )
    except Exception as exc:
        expected_failure("E2 max_iterations is server-bounded",
                         f"could not introspect AnalyzeRequest: {exc}")

    # E3 — oversized input must be rejected on /api/v1/*.
    #
    # CORRECTION: the original audit implied a 2 MiB body would be
    # accepted because the body-size guard in api/main.py only covers
    # /v1/*. That was WRONG — pydantic MaxLen bounds raw_input to 2000
    # chars, so the request is refused with 422. This test keeps that
    # field bound from being removed.
    from api.schemas import KernelProcessRequest as _KPR
    kr_field = _KPR.model_fields["raw_input"]
    kr_bounds = [(type(m).__name__, getattr(m, "max_length", None))
                 for m in (getattr(kr_field, "metadata", []) or [])]
    has_len_bound = any(
        getattr(m, "max_length", None) is not None
        for m in (getattr(kr_field, "metadata", []) or [])
    )
    if has_len_bound:
        check("E3 raw_input is length-bounded by the request schema", True,
              f"constraints={kr_bounds}")
    else:
        expected_failure(
            "E3 raw_input is length-bounded by the request schema",
            "H-02: no length bound on raw_input for the unauthenticated "
            "/api/v1/kernel/process route",
        )

    originals = _arm_canaries()
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        big = "x" * (2 * 1024 * 1024)   # 2 MiB
        resp = client.post("/api/v1/kernel/process", json={"raw_input": big})
        if resp.status_code in (400, 413, 422):
            check("E4 oversized /api/v1 body is rejected at HTTP", True,
                  f"HTTP {resp.status_code}")
        else:
            expected_failure(
                "E4 oversized /api/v1 body is rejected at HTTP",
                f"H-02: a 2 MiB body was accepted on /api/v1/kernel/process "
                f"(HTTP {resp.status_code})",
            )
    finally:
        _restore_canaries(originals)


# ---------------------------------------------------------------------------


    # E5 — the genuinely PUBLIC expensive route.
    #
    # /api/v1/kernel/process stays anonymous on purpose (the production UI
    # posts there with no credential), so request-count limiting is its only
    # admission control. This asserts the ceiling actually engages.
    from api import rate_limit as _rl

    _rl.reset()
    originals = _arm_canaries()
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        statuses = []
        for _ in range(80):
            resp = client.post("/api/v1/kernel/process",
                               json={"raw_input": "Bought machinery for Rs. 90,000 cash."})
            statuses.append(resp.status_code)
        got_429 = 429 in statuses
        if got_429:
            check("E5 public /api/v1/kernel/process is rate limited", True,
                  f"429 after {statuses.index(429)} requests")
        else:
            expected_failure(
                "E5 public /api/v1/kernel/process is rate limited",
                f"H-02: 80 consecutive anonymous calls to the PUBLIC kernel "
                f"route were all admitted (statuses={sorted(set(statuses))}); "
                "unbounded request count on an unauthenticated endpoint",
            )
    finally:
        _rl.reset()
        _restore_canaries(originals)


# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("PLATRIXA — SECURITY REGRESSION: API AUTHN / ADMISSION CONTROL")
    print("Captures audit findings H-01 and H-02 (pre-fix).")
    print("Canaries prevent any real provider/LLM/database call.")
    print("=" * 78)

    test_enumerate_api_v1_surface()
    test_anonymous_access_rejected()
    test_credential_classes_rejected()
    test_information_disclosure()
    test_admission_control_exists()

    print()
    print("=" * 78)
    if _FAIL == 0:
        print(f"RESULT: PASS — {_PASS}/{_PASS + _FAIL} checks passed")
    else:
        print(f"RESULT: FAIL — {_PASS} passed, {_FAIL} failed (expected pre-fix)")
        print("\nOutstanding defects (audit findings this suite locks in):")
        for m in _MESSAGES:
            print(f"  - {m}")
    print("=" * 78)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
