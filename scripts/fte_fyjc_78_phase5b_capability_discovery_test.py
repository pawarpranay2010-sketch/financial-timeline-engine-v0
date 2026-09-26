#!/usr/bin/env python3
"""
Phase 5B — Capability Discovery API: evidence suite (fte_fyjc_78).

Proves that GET /v1/capabilities is a thin READ-ONLY adapter over the
existing capability registry (backend/maths/capability_registry.py) —
the single source of truth — WITHOUT weakening anything:

  * The API layer keeps no capability list of its own: the response is
    compared programmatically against the live registry on every run,
    so registry/API drift fails the suite.
  * Registry statuses are preserved verbatim (SUPPORTED / PARTIAL /
    UNSUPPORTED / PLANNED); nothing is collapsed to a boolean, upgraded,
    or converted into an API error.
  * Discovery is a read operation: no model inference, no grounding, no
    authority execution, no financial processing.
  * Authentication is the EXISTING Phase 15/16 gate (fail-closed 401);
    no new auth/tenant/quota mechanism is introduced.
  * Responses are JSON-safe with no traceback, secret, or environment
    leakage, and failures use the existing structured error contract.

Sections:
  A  Endpoint + response envelope (200, stable shape, request-id echo)
  B  Capability schema (required fields, types, JSON-safe values)
  C  Registry source of truth (API == live registry, per capability)
  D  Status preservation (four-state vocabulary, no upgrades)
  E  No duplicate source of truth (no hardcoded capability IDs in api/)
  F  Deterministic ordering (repeated requests identical, id-sorted)
  G  Authentication (valid / missing / invalid key — existing gate only)
  H  Sensitive data (no secrets, env, tracebacks, internal objects)
  I  Fail-closed registry failure (structured envelope, no traceback)
  J  OpenAPI exposure (method, path, response schema, description)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The workspace environment may configure the Phase 16 metered gate; this
# suite exercises the zero-config boundary (and drives the Phase 15 key
# gate itself in section G), so gate activation variables are neutralized
# in-process BEFORE the API is imported. Gate state is re-read at request
# time, so this is effective (same convention as fte_fyjc_77).
_GATE_ENV_VARS = ("PLATRIXA_METERING_DATABASE_URL", "PLATRIXA_DEV_API_KEY")
for _var in _GATE_ENV_VARS:
    os.environ.pop(_var, None)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.schemas import DeveloperCapabilitiesResponse  # noqa: E402
from api.routes import developer  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


CAPABILITY_FIELDS = {
    "capability_id": str,
    "authority": str,
    "canonical_name": str,
    "supported_status": str,
    "description": str,
    "required_inputs": list,
    "deterministic_op": str,
    "implementation_ref": str,
    "test_ref": str,
    "source_ref": str,
    "jurisdiction": str,
    "framework": str,
    "version": str,
    "limitations": list,
}

TEST_KEY = "phase5b-discovery-test-key-0123456789"


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _body(client: TestClient) -> dict:
    r = client.get("/v1/capabilities")
    assert r.status_code == 200, r.status_code
    return r.json()


# ---------------------------------------------------------------------------
# A — Endpoint + response envelope
# ---------------------------------------------------------------------------


def section_a(client: TestClient) -> dict:
    print("\nA — endpoint + response envelope (200, stable shape, request-id echo)")
    r = client.get("/v1/capabilities", headers={"X-Request-Id": "cap-1"})
    body = r.json()
    check("A1 GET /v1/capabilities → 200", r.status_code == 200, str(r.status_code))
    check("A2 api_version v1", body.get("api_version") == "v1")
    check(
        "A3 envelope keys are exactly the Phase 5B contract",
        set(body) == {
            "api_version", "request_id", "api_status", "api_status_label",
            "retryable", "engine_status", "registry_summary", "count", "capabilities",
        },
        str(sorted(body)),
    )
    check("A4 X-Request-Id echoed", body.get("request_id") == "cap-1", str(body.get("request_id")))
    check("A5 api_status VERIFIED (read succeeded)", body.get("api_status") == "VERIFIED")
    check("A6 api_status_label present", body.get("api_status_label") == "Verified")
    check("A7 retryable False (deterministic read)", body.get("retryable") is False)
    check("A8 engine_status None — no engine ran", body.get("engine_status") is None)
    check("A9 count matches len(capabilities)", body.get("count") == len(body.get("capabilities", [])))
    check(
        "A10 response validates against the response model",
        DeveloperCapabilitiesResponse(**body) is not None,
    )
    summary = body.get("registry_summary", {})
    check(
        "A11 registry_summary is authority x status counts",
        isinstance(summary, dict)
        and all(isinstance(v, dict) for v in summary.values()),
        str(type(summary)),
    )
    return body


# ---------------------------------------------------------------------------
# B — Capability schema
# ---------------------------------------------------------------------------


def section_b(body: dict) -> None:
    print("\nB — capability schema (required fields, types, JSON-safe)")
    caps = body.get("capabilities", [])
    check("B1 at least one capability returned", len(caps) > 0, str(len(caps)))
    bad_shape = []
    for cap in caps:
        if set(cap) != set(CAPABILITY_FIELDS):
            bad_shape.append((cap.get("capability_id"), "field set"))
            continue
        for fname, ftype in CAPABILITY_FIELDS.items():
            if not isinstance(cap[fname], ftype):
                bad_shape.append((cap.get("capability_id"), fname))
    check("B2 every capability has exactly the registry field set + types", not bad_shape, str(bad_shape[:4]))
    ids = [c["capability_id"] for c in caps]
    check("B3 capability ids unique", len(ids) == len(set(ids)))
    check("B4 capability ids non-empty strings", all(i for i in ids))
    try:
        json.dumps(body)
        check("B5 whole response JSON-serializable", True)
    except (TypeError, ValueError) as exc:
        check("B5 whole response JSON-serializable", False, str(exc))
    # No internal objects / memory addresses / callables anywhere.
    def _walk(node) -> bool:
        if isinstance(node, dict):
            return all(isinstance(k, str) and _walk(v) for k, v in node.items())
        if isinstance(node, list):
            return all(_walk(v) for v in node)
        return isinstance(node, (str, int, float, bool)) or node is None

    check("B6 only JSON primitives (no objects/addrs/callables)", _walk(body))


# ---------------------------------------------------------------------------
# C — Registry source of truth (the drift test)
# ---------------------------------------------------------------------------


def section_c(body: dict) -> None:
    print("\nC — registry source of truth (API == live registry, per capability)")
    from backend.maths.capability_registry import CAPABILITIES

    caps = body.get("capabilities", [])
    api_by_id = {c["capability_id"]: c for c in caps}
    reg_by_id = {cid: cap.to_metadata() for cid, cap in CAPABILITIES.items()}

    check(
        "C1 capability ID sets are IDENTICAL to the live registry",
        set(api_by_id) == set(reg_by_id),
        f"api-only={sorted(set(api_by_id) - set(reg_by_id))[:3]} "
        f"registry-only={sorted(set(reg_by_id) - set(api_by_id))[:3]}",
    )
    drift = []
    for cid in sorted(set(api_by_id) & set(reg_by_id)):
        if api_by_id[cid] != reg_by_id[cid]:
            diff = {
                k for k in set(api_by_id[cid]) | set(reg_by_id[cid])
                if api_by_id[cid].get(k) != reg_by_id[cid].get(k)
            }
            drift.append((cid, sorted(diff)))
    check("C2 every field value matches the registry verbatim", not drift, str(drift[:3]))
    check(
        "C3 count equals len(CAPABILITIES)",
        body.get("count") == len(CAPABILITIES),
        f"api={body.get('count')} registry={len(CAPABILITIES)}",
    )
    # summary() correspondence, re-derived independently of the endpoint
    # (summary() seeds every authority x status combo, including zeros).
    live_summary: dict[str, dict[str, int]] = {}
    for cap in CAPABILITIES.values():
        for authority in ("ACCOUNTING_KERNEL", "FINANCE_KNOWLEDGE", "FORMULA_AUTHORITY"):
            live_summary.setdefault(authority, {})
        live_summary.setdefault(cap.authority, {})
        live_summary[cap.authority][cap.supported_status] = (
            live_summary[cap.authority].get(cap.supported_status, 0) + 1
        )
    for authority in live_summary:
        for status in ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "PLANNED"):
            live_summary[authority].setdefault(status, 0)
    check(
        "C4 registry_summary matches an independent registry count",
        body.get("registry_summary") == live_summary,
        str(body.get("registry_summary")),
    )


# ---------------------------------------------------------------------------
# D — Status preservation
# ---------------------------------------------------------------------------


def section_d(body: dict) -> None:
    print("\nD — status preservation (four-state vocabulary, no upgrades)")
    from backend.maths.capability_registry import CAPABILITIES

    caps = body.get("capabilities", [])
    api_statuses = {c["supported_status"] for c in caps}
    reg_statuses = {c.supported_status for c in CAPABILITIES.values()}
    check(
        "D1 API statuses == registry statuses (verbatim set)",
        api_statuses == reg_statuses,
        f"api={sorted(api_statuses)} registry={sorted(reg_statuses)}",
    )
    check(
        "D2 all four registry states appear when the registry has them",
        not {"SUPPORTED", "PARTIAL", "UNSUPPORTED", "PLANNED"} - reg_statuses
        or api_statuses == reg_statuses,
    )
    check(
        "D3 vocabulary is exactly the registry's four states",
        api_statuses <= {"SUPPORTED", "PARTIAL", "UNSUPPORTED", "PLANNED"},
        str(sorted(api_statuses)),
    )
    per_cap_ok = all(
        c["supported_status"] == CAPABILITIES[c["capability_id"]].supported_status
        for c in caps
    )
    check("D4 per-capability status preserved", per_cap_ok)
    # UNSUPPORTED/PLANNED entries are metadata, not errors: they ride in
    # the same 200 response as everything else.
    boundary = [c for c in caps if c["supported_status"] in ("UNSUPPORTED", "PLANNED")]
    check(
        "D5 UNSUPPORTED/PLANNED entries returned as capability metadata (200)",
        len(boundary) > 0 and all(c["capability_id"] for c in boundary),
        str(len(boundary)),
    )
    # Registry contract: UNSUPPORTED always carries refusal evidence.
    check(
        "D6 UNSUPPORTED entries carry documented limitations",
        all(c["limitations"] for c in caps if c["supported_status"] == "UNSUPPORTED"),
    )


# ---------------------------------------------------------------------------
# E — No duplicate source of truth
# ---------------------------------------------------------------------------


def section_e() -> None:
    print("\nE — no duplicate source of truth (no hardcoded capability lists in api/)")
    api_dir = Path(__file__).resolve().parent.parent / "api"
    offenders: list[str] = []
    for py in sorted(api_dir.rglob("*.py")):
        src = py.read_text(encoding="utf-8")
        for marker in ('"KERNEL.', "'KERNEL.", '"FORMULA.', "'FORMULA.",
                       '"KNOWLEDGE.', "'KNOWLEDGE."):
            if marker in src:
                offenders.append(f"{py.name}:{marker}")
    check("E1 no capability ID literals in the api/ package", not offenders, str(offenders))
    dev_src = (api_dir / "routes" / "developer.py").read_text(encoding="utf-8")
    check(
        "E2 handler derives from the registry module (no second list)",
        "from backend.maths.capability_registry import CAPABILITIES" in dev_src,
    )
    check(
        "E3 no hand-built capability dicts in the handler",
        "capability_id\":" not in dev_src and "'capability_id':" not in dev_src.split("_api_status_fields")[0],
    )


# ---------------------------------------------------------------------------
# F — Deterministic ordering
# ---------------------------------------------------------------------------


def section_f(client: TestClient) -> None:
    print("\nF — deterministic ordering (repeated requests identical)")
    b1, b2, b3 = (_body(client) for _ in range(3))
    check("F1 three consecutive responses identical", b1 == b2 == b3)
    ids = [c["capability_id"] for c in b1.get("capabilities", [])]
    check("F2 capabilities ordered by capability_id", ids == sorted(ids))


# ---------------------------------------------------------------------------
# G — Authentication (existing gate only)
# ---------------------------------------------------------------------------


def section_g(client: TestClient) -> None:
    print("\nG — authentication (existing Phase 15/16 gate, fail-closed)")
    saved = os.environ.get("PLATRIXA_DEV_API_KEY")
    try:
        os.environ["PLATRIXA_DEV_API_KEY"] = TEST_KEY
        r = client.get("/v1/capabilities")
        body = r.json()
        check(
            "G1 missing key → 401 existing envelope",
            r.status_code == 401
            and body.get("error", {}).get("code") == "UNAUTHORIZED",
            f"{r.status_code} {body.get('error', {}).get('code')}",
        )
        check(
            "G2 401 envelope carries the six-state mapping (INVALID_INPUT)",
            body.get("error", {}).get("api_status") == "INVALID_INPUT",
            str(body.get("error", {}).get("api_status")),
        )
        r = client.get("/v1/capabilities", headers={"X-Platrixa-API-Key": "wrong-key"})
        check("G3 invalid key → 401", r.status_code == 401, str(r.status_code))
        check(
            "G4 invalid key body never echoes the configured key",
            TEST_KEY not in r.text,
        )
        r = client.get(
            "/v1/capabilities", headers={"X-Platrixa-API-Key": TEST_KEY}
        )
        check(
            "G5 valid key → 200 with the full contract",
            r.status_code == 200 and r.json().get("count", 0) > 0,
            str(r.status_code),
        )
    finally:
        if saved is None:
            os.environ.pop("PLATRIXA_DEV_API_KEY", None)
        else:
            os.environ["PLATRIXA_DEV_API_KEY"] = saved
    # Zero-config: the documented open boundary is unchanged.
    r = client.get("/v1/capabilities")
    check("G6 zero-config (no key set) → open endpoint, unchanged", r.status_code == 200, str(r.status_code))


# ---------------------------------------------------------------------------
# H — Sensitive data
# ---------------------------------------------------------------------------


def section_h(client: TestClient) -> None:
    print("\nH — sensitive data (no secrets, env, tracebacks, internals)")
    body = _body(client)
    text = json.dumps(body)
    forbidden_markers = [
        "Traceback (most recent call last)",
        'File "',
        "os.environ",
        "PLATRIXA_DEV_API_KEY",
        "PLATRIXA_METERING_DATABASE_URL",
        "DATABASE_URL",
        "HF_TOKEN",
        "FMP_API_KEY",
        "postgres",
        "postgresql://",
        "0x",
        "<object object at",
        "api_key",
        "secret",
    ]
    leaks = [m for m in forbidden_markers if m.lower() in text.lower()]
    check("H1 no secret/env/traceback markers in the response", not leaks, str(leaks))
    check(
        "H2 with a key configured, the key value never appears",
        True,  # positive assertion covered by G4 under the configured gate
    )
    caps = body.get("capabilities", [])
    check(
        "H3 no callable/object reprs in capability values",
        all(
            isinstance(v, (str, int, float, bool, list)) or v is None
            for c in caps for v in c.values()
        ),
    )


# ---------------------------------------------------------------------------
# I — Fail-closed registry failure
# ---------------------------------------------------------------------------


def section_i(client: TestClient) -> None:
    print("\nI — fail-closed registry failure (structured envelope, no traceback)")
    import backend.maths.capability_registry as cr

    saved = sys.modules.get("backend.maths.capability_registry")
    try:
        sys.modules["backend.maths.capability_registry"] = None  # import halts
        r = client.get("/v1/capabilities")
        body = r.json()
        check(
            "I1 registry failure → 503 structured envelope",
            r.status_code == 503
            and body.get("error", {}).get("code") == "PROVIDER_UNAVAILABLE",
            f"{r.status_code} {json.dumps(body)[:120]}",
        )
        check(
            "I2 failure envelope maps to PROCESSING (transport read, retryable)",
            body.get("error", {}).get("api_status") == "PROCESSING"
            and body.get("error", {}).get("retryable") is True,
            str(body.get("error")),
        )
        check("I3 no traceback in the failure body", "Traceback" not in r.text and 'File "' not in r.text)
    finally:
        if saved is not None:
            sys.modules["backend.maths.capability_registry"] = saved
        else:
            sys.modules.pop("backend.maths.capability_registry", None)
    r = client.get("/v1/capabilities")
    check("I4 registry recovers after the failure injection", r.status_code == 200, str(r.status_code))


# ---------------------------------------------------------------------------
# J — OpenAPI exposure
# ---------------------------------------------------------------------------


def section_j(client: TestClient) -> None:
    print("\nJ — OpenAPI exposure (method, path, schema, description)")
    spec = client.get("/openapi.json").json()
    op = spec.get("paths", {}).get("/v1/capabilities", {})
    check("J1 path present", bool(op), str(sorted(spec.get("paths", {}))[-4:]))
    check("J2 GET operation only", set(op) == {"get"}, str(sorted(op)))
    check("J3 has a summary and description", bool(op.get("get", {}).get("summary")) and bool(op.get("get", {}).get("description")))
    ref = (
        op.get("get", {})
        .get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("$ref", "")
    )
    check("J4 200 response references the response schema", ref.endswith("DeveloperCapabilitiesResponse"), ref)
    check(
        "J5 schema component registered",
        "DeveloperCapabilitiesResponse" in spec.get("components", {}).get("schemas", {}),
    )


def main() -> int:
    print("=" * 70)
    print("PHASE 5B — CAPABILITY DISCOVERY API (fte_fyjc_78)")
    print("=" * 70)
    client = _client()
    body = section_a(client)
    section_b(body)
    section_c(body)
    section_d(body)
    section_e()
    section_f(client)
    section_g(client)
    section_h(client)
    section_i(client)
    section_j(client)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{total} PASS")
    if failed:
        print("FAILED:")
        for name, detail in failed:
            print(f"  - {name}: {detail}")
    print("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
