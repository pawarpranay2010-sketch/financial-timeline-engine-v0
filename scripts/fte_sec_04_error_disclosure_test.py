#!/usr/bin/env python3
"""
PLATRIXA — SECURITY REGRESSION: ERROR DISCLOSURE (fte_sec_04)
==============================================================

Captures audit finding M-04 from the 2026-09-29 security audit
(reports/security_audit_2026-09-29.md) BEFORE any fix is applied.

  M-04  api/routes/intelligence.py catches a bare ``Exception`` and
        returns

            detail=f"Analysis failed: {type(exc).__name__}: {str(exc)[:300]}"

        to the CALLER. That route is UNAUTHENTICATED (audit H-01), so an
        arbitrary internet client receives up to 300 characters of raw
        internal exception text: filesystem paths, library versions,
        database driver messages, and potentially connection detail.

THE SECURITY PROPERTY UNDER TEST
-------------------------------
  A client must receive the documented generic error contract and nothing
  more. Raw exception text — class names beyond the contract, messages,
  stack frames, paths, SQL, credentials, prompts or model output — must
  never reach the response body.

HOW IT IS FORCED
----------------
A controlled canary exception carrying distinctive MARKER strings is
raised from inside the handler. The test then asserts none of those
markers appear anywhere in the response (status line, headers, body).
No real failure is induced and no real service is contacted.

This test is EXPECTED TO FAIL on the current tree. Do not weaken it to a
status-code-only assertion: the defect IS the body content.

Run:  python3 scripts/fte_sec_04_error_disclosure_test.py
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List

warnings.filterwarnings("ignore")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import api.services as svc  # noqa: E402
from api.main import create_app  # noqa: E402

# The analyzed route is credential-protected (audit H-01). Without a
# configured credential it returns 401 and the exception path is never
# reached, which would make every disclosure assertion below pass
# VACUOUSLY. A test-only key is configured so the handler genuinely runs.
TEST_API_KEY = "fte-sec-04-test-key-not-a-real-credential"
os.environ.setdefault("PLATRIXA_DEV_API_KEY", TEST_API_KEY)

# ---------------------------------------------------------------------------
# Shared check machinery (house style)
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
# Canary exception carrying unmistakable markers
# ---------------------------------------------------------------------------

# Distinctive, non-secret strings. If any appears in a response, internal
# exception text has leaked to the client.
MARKERS = {
    "abs_path": "/srv/platrixa/internal/secret_module.py",
    "db_dsn": "postgresql://plx_admin:Hunter2Correct@db.internal:5432/platrixa",
    "sql": "SELECT api_key_hash FROM platrixa_tenant_quotas WHERE tenant_id = 'acme'",
    "prompt": "Below is an instruction that describes a task, paired with an input",
    "api_key": "plx_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
    "token": "hf_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
    "trace": "Traceback (most recent call last): File \"/srv/platrixa/x.py\", line 42",
}

CANARY_MESSAGE = (
    f"boom {MARKERS['abs_path']} {MARKERS['db_dsn']} {MARKERS['sql']} "
    f"{MARKERS['prompt']} {MARKERS['api_key']} {MARKERS['token']} {MARKERS['trace']}"
)


class _CanaryError(RuntimeError):
    pass


def _body_text(response: Any) -> str:
    try:
        return response.text or ""
    except Exception:
        return ""


def _headers_text(response: Any) -> str:
    try:
        return json.dumps({k: v for k, v in response.headers.items()})
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# A. /api/v1/intelligence/analyze — the audited leak
# ---------------------------------------------------------------------------


def test_analyze_route_does_not_leak_exception_text() -> None:
    print("\n--- A. /api/v1/intelligence/analyze (EXPECTED TO FAIL today) ---")
    original = svc.run_analysis

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise _CanaryError(CANARY_MESSAGE)

    svc.run_analysis = _explode
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        resp = client.post("/api/v1/intelligence/analyze",
                           json={"ticker": "AAPL", "goal": "disclosure probe",
                                 "max_iterations": 1},
                           headers={"X-Platrixa-API-Key": TEST_API_KEY})
    finally:
        svc.run_analysis = original

    # Guard against a vacuous pass: the handler must actually have raised.
    check("A0 the failure path was genuinely exercised",
          resp.status_code >= 400 and "Analysis failed" in (resp.text or ""),
          f"HTTP {resp.status_code} body={(resp.text or '')[:90]}")

    body = _body_text(resp)
    heads = _headers_text(resp)
    blob = f"{body}\n{heads}"

    # A1 — the response must be an error, not a success.
    check("A1 analyze returns an error status",
          resp.status_code >= 400, f"HTTP {resp.status_code}")

    # A2..A8 — no marker may appear anywhere.
    for name, marker in MARKERS.items():
        if marker in blob:
            expected_failure(
                f"A2 response must not contain {name}",
                f"M-04: internal {name} leaked to the client "
                f"({marker[:44]}...) in a {resp.status_code} response",
            )
        else:
            check(f"A2 response must not contain {name}", True)

    # A3 — the body must be the documented error contract, not a string dump.
    try:
        parsed = resp.json()
        structured = isinstance(parsed, dict)
    except Exception:
        parsed = None
        structured = False
    check("A3 response body is a structured error contract", structured,
          f"body={body[:120]}")

    # A4 — the free-text detail must not embed the raw exception message.
    if isinstance(parsed, dict):
        detail = str(parsed.get("detail", ""))
        if "CanaryError" in detail or CANARY_MESSAGE[:20] in detail:
            expected_failure(
                "A4 error detail must not embed the raw exception",
                f"M-04: detail = {detail[:160]}",
            )
        else:
            check("A4 error detail must not embed the raw exception", True,
                  f"detail={detail[:80]}")
    else:
        expected_failure("A4 error detail must not embed the raw exception",
                         "response body is not a JSON object")


# ---------------------------------------------------------------------------
# B. Whole-app error handling — no route may echo internals
# ---------------------------------------------------------------------------


def test_no_route_echoes_internals() -> None:
    print("\n--- B. Unhandled exception handler (app-wide) ---")
    client = TestClient(create_app(), raise_server_exceptions=False)

    # Force the app-level handler by raising inside a route we can reach
    # without external services: /api/v1/market with an exploding service.
    original = svc.fetch_market_snapshot

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise _CanaryError(CANARY_MESSAGE)

    svc.fetch_market_snapshot = _explode
    try:
        resp = client.get("/api/v1/market/LEAKPROBE",
                          headers={"X-Platrixa-API-Key": TEST_API_KEY})
    finally:
        svc.fetch_market_snapshot = original

    blob = f"{_body_text(resp)}\n{_headers_text(resp)}"
    leaked = [n for n, m in MARKERS.items() if m in blob]
    if leaked:
        expected_failure(
            "B1 market route must not leak internal exception text",
            f"M-04: leaked markers {leaked} in HTTP {resp.status_code}",
        )
    else:
        check("B1 market route must not leak internal exception text", True,
              f"HTTP {resp.status_code}")

    # B2 — the app-level handler's own shape. It returns
    # {"detail": "Unhandled error: <TypeName>"}. A type name is a small,
    # bounded disclosure; assert it does not carry a message.
    if "Unhandled error:" in blob and "CanaryError" in blob:
        expected_failure(
            "B2 app-level handler must not name the exception class",
            "M-04: the global handler returns the concrete exception class "
            "name to the client, which aids targeted reconnaissance",
        )
    else:
        check("B2 app-level handler must not name the exception class", True)


# ---------------------------------------------------------------------------
# C. Response headers must not carry internals
# ---------------------------------------------------------------------------


def test_no_debug_headers() -> None:
    print("\n--- C. Debug / internal headers ---")
    client = TestClient(create_app(), raise_server_exceptions=False)
    for path in ("/api/v1/health", "/api/v1/providers/status", "/openapi.json"):
        try:
            resp = client.get(path)
        except Exception as exc:
            note(f"{path} unreachable in test env: {type(exc).__name__}")
            continue
        heads = _headers_text(resp).lower()
        bad = [h for h in ("x-powered-by", "x-debug", "x-traceback",
                           "x-exception", "x-sql", "server-timing-detail")
               if h in heads]
        if bad:
            expected_failure(
                f"C1 {path} must not emit debug headers",
                f"M-04: internal headers present: {bad}",
            )
        else:
            check(f"C1 {path} must not emit debug headers", True)


# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("PLATRIXA — SECURITY REGRESSION: ERROR DISCLOSURE")
    print("Captures audit finding M-04 (pre-fix).")
    print("=" * 78)

    test_analyze_route_does_not_leak_exception_text()
    test_no_route_echoes_internals()
    test_no_debug_headers()

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
