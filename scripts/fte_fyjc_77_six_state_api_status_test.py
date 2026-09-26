#!/usr/bin/env python3
"""
Phase 5A — Stable Developer API Contract: six-state public status
mapping — evidence suite (fte_fyjc_77).

Proves that the stable v1 developer API contract now carries the
six-state PUBLIC API status (PROCESSING / VERIFIED / REVIEW_REQUIRED /
UNSUPPORTED / INVALID_INPUT / FAILED) as a deterministic transport-layer
mapping, WITHOUT weakening anything:

  * The engine terminal state is carried verbatim (``status`` and the new
    ``engine_status`` field) — the Kernel remains the only status
    authority; the HTTP layer relabels, never rewrites.
  * No engine state maps to VERIFIED except VERIFIED.
  * No accounting, grounding, or schema logic was added to the transport
    layer (existing Phase 62/65 suites must keep passing).

Sections:
  A  api.status module contract (closed vocabulary, total functions)
  B  engine-state → six-state mapping (all terminal states)
  C  error-code → six-state mapping (transport rejections)
  D  /v1/process success paths carry api_status + verbatim engine state
  E  /v1/process failure states map without upgrading (fail-closed)
  F  /v1/process/document carries the same mapping
  G  error envelopes carry api_status (400/413/422/503 paths)
  H  back-compat: pre-existing response fields byte-stable
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The workspace .env may configure the Phase 16 metered gate; the hosted-
# API suites (Phase 62/65 convention) exercise the zero-config boundary,
# so gate activation variables are neutralized in-process BEFORE the API
# is imported. Gate state is re-read at request time, so this is effective.
_GATE_ENV_VARS = ("PLATRIXA_METERING_DATABASE_URL", "PLATRIXA_DEV_API_KEY")
for _var in _GATE_ENV_VARS:
    os.environ.pop(_var, None)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from api.status import (  # noqa: E402
    LABEL_BY_PUBLIC_STATUS,
    PUBLIC_STATES,
    STATUS_BY_ENGINE_STATE,
    STATUS_BY_ERROR_CODE,
    is_retryable_status,
    is_success_status,
    public_status_for_engine,
    public_status_for_error_code,
    reason_code_for_engine,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Fixtures (same shapes as the Phase 62 suite — pure transport stubs)
# ---------------------------------------------------------------------------

_VALID_CANDIDATE = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["payment method not stated"],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.50",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}


class _Result:
    """Attribute-shaped stub of a public-interface result."""

    def __init__(self, status: str, rid: str = "fixed-req") -> None:
        self.status = status
        self.status_label = status
        self.success = status == "VERIFIED"
        self.request_id = rid
        self.next_action = None
        self.issues: list = [] if status == "VERIFIED" else ["issue-for-" + status]
        self.grounding_issues: list = (
            [] if status == "VERIFIED" else ["grounding-for-" + status]
        )
        self.rule_evidence: list = []
        self.interpretation = dict(_VALID_CANDIDATE)
        self.accounting = {"status": status}
        self.raw_input = "x"
        self.metadata: dict = {}


class TransportStub:
    """Deterministic client stub for pure-transport tests."""

    def __init__(self, status: str = "VERIFIED", fail_process: bool = False) -> None:
        self._status = status
        self._fail = fail_process

    def process(self, text, request_id=None):
        if self._fail:
            from platrixa.errors import ProviderError

            raise ProviderError("provider down")
        return _Result(self._status, request_id or "fixed-req")

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return None


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Section A — api.status module contract
# ---------------------------------------------------------------------------


def section_a() -> None:
    print("\nA — api.status module contract (closed vocabulary, total functions)")
    check("A1 exactly six public states", len(PUBLIC_STATES) == 6, str(PUBLIC_STATES))
    check(
        "A2 expected members",
        set(PUBLIC_STATES)
        == {"PROCESSING", "VERIFIED", "REVIEW_REQUIRED", "UNSUPPORTED", "INVALID_INPUT", "FAILED"},
    )
    check(
        "A3 every mapped engine state resolves into the closed vocabulary",
        all(s in PUBLIC_STATES for s in STATUS_BY_ENGINE_STATE.values()),
    )
    check(
        "A4 every mapped error code resolves into the closed vocabulary",
        all(s in PUBLIC_STATES for s in STATUS_BY_ERROR_CODE.values()),
    )
    # Total functions: unknown input fails closed, never raises.
    check("A5 unknown engine state fails closed to FAILED", public_status_for_engine("SOMETHING_NEW") == "FAILED")
    check("A6 unknown error code fails closed to FAILED", public_status_for_error_code("WHO_KNOWS") == "FAILED")
    check("A7 VERIFIED is the only success state", all(not is_success_status(s) for s in PUBLIC_STATES if s != "VERIFIED"))
    check("A8 only PROCESSING is retryable", [s for s in PUBLIC_STATES if is_retryable_status(s)] == ["PROCESSING"])
    check(
        "A9 labels exist for every public state",
        all(LABEL_BY_PUBLIC_STATUS.get(s) for s in PUBLIC_STATES),
    )
    check(
        "A10 engine_status_verbatim rejects non-engine strings",
        __import__("api.status", fromlist=["engine_status_verbatim"]).engine_status_verbatim("MADE_UP") is None,
    )
    check(
        "A11 reason codes only for documented engine states",
        reason_code_for_engine("VERIFIED") is None
        and reason_code_for_engine("REVIEW_REQUIRED") is None
        and reason_code_for_engine("BLOCKED") == "SAFETY_BOUNDARY",
    )


# ---------------------------------------------------------------------------
# Section B — engine-state mapping (the full terminal taxonomy)
# ---------------------------------------------------------------------------


def section_b() -> None:
    print("\nB — engine-state → six-state mapping (all terminal states)")
    expected = {
        "VERIFIED": "VERIFIED",
        "REVIEW_REQUIRED": "REVIEW_REQUIRED",
        "BLOCKED": "UNSUPPORTED",
        "UNSUPPORTED_TRANSACTION": "UNSUPPORTED",
        "FORBIDDEN_OUTPUT": "UNSUPPORTED",
        "VALIDATION_FAILED": "FAILED",
        "GROUNDING_FAILED": "FAILED",
        "MODEL_UNAVAILABLE": "PROCESSING",
    }
    for engine_state, want in expected.items():
        got = public_status_for_engine(engine_state)
        check(f"B {engine_state} → {want}", got == want, got)
    # Authority invariants
    check(
        "B no engine state upgrades to VERIFIED except VERIFIED",
        all(
            mapped != "VERIFIED" or engine == "VERIFIED"
            for engine, mapped in STATUS_BY_ENGINE_STATE.items()
        ),
    )
    check(
        "B REVIEW_REQUIRED never downgraded to FAILED or UNSUPPORTED",
        public_status_for_engine("REVIEW_REQUIRED") == "REVIEW_REQUIRED",
    )


# ---------------------------------------------------------------------------
# Section C — error-code mapping
# ---------------------------------------------------------------------------


def section_c() -> None:
    print("\nC — error-code → six-state mapping (transport rejections)")
    expected = {
        "REQUEST_MALFORMED": "INVALID_INPUT",
        "REQUEST_TOO_LARGE": "INVALID_INPUT",
        "INPUT_INVALID": "INVALID_INPUT",
        "UNAUTHORIZED": "INVALID_INPUT",
        "QUOTA_EXHAUSTED": "INVALID_INPUT",
        "FILE_TOO_LARGE": "INVALID_INPUT",
        "PROVIDER_UNAVAILABLE": "PROCESSING",
        "METERING_UNAVAILABLE": "PROCESSING",
    }
    for code, want in expected.items():
        got = public_status_for_error_code(code)
        check(f"C {code} → {want}", got == want, got)
    check("C unknown code fails closed (never VERIFIED/PROCESSING)", public_status_for_error_code("X") == "FAILED")


# ---------------------------------------------------------------------------
# Section D — /v1/process success paths
# ---------------------------------------------------------------------------


def section_d(client: TestClient) -> None:
    print("\nD — /v1/process success paths carry api_status + verbatim engine state")
    developer.set_client(TransportStub("VERIFIED"))
    try:
        r = client.post("/v1/process", json={"raw_input": "Purchased furniture for cash ₹15,000"})
        body = r.json()
        check("D1 VERIFIED → 200", r.status_code == 200, str(r.status_code))
        check("D2 api_status = VERIFIED", body.get("api_status") == "VERIFIED", str(body.get("api_status")))
        check("D3 api_status_label present", body.get("api_status_label") == "Verified")
        check("D4 engine state verbatim in status", body.get("status") == "VERIFIED")
        check("D5 engine_status field verbatim", body.get("engine_status") == "VERIFIED")
        check("D6 success true only here", body.get("success") is True and body.get("retryable") is False)
        check("D7 reason_code None on VERIFIED", body.get("reason_code") is None)
    finally:
        developer.reset_client()


# ---------------------------------------------------------------------------
# Section E — /v1/process failure/terminal states (no upgrade, fail-closed)
# ---------------------------------------------------------------------------


def section_e(client: TestClient) -> None:
    print("\nE — terminal states map without upgrading (fail-closed)")
    cases = [
        ("REVIEW_REQUIRED", 200, "REVIEW_REQUIRED"),
        ("BLOCKED", 200, "UNSUPPORTED"),
        ("UNSUPPORTED_TRANSACTION", 422, "UNSUPPORTED"),
        ("FORBIDDEN_OUTPUT", 422, "UNSUPPORTED"),
        ("VALIDATION_FAILED", 422, "FAILED"),
        ("GROUNDING_FAILED", 422, "FAILED"),
        ("MODEL_UNAVAILABLE", 503, "PROCESSING"),
    ]
    for engine_state, want_http, want_api in cases:
        developer.set_client(TransportStub(engine_state))
        try:
            r = client.post("/v1/process", json={"raw_input": "x"})
            body = r.json()
            ok = (
                r.status_code == want_http
                and body.get("api_status") == want_api
                and body.get("engine_status") == engine_state
                and body.get("status") == engine_state
                and body.get("success") is False
            )
            check(
                f"E {engine_state} → HTTP {want_http} / api_status {want_api}",
                ok,
                f"got {r.status_code}/{body.get('api_status')}/{body.get('engine_status')}",
            )
        finally:
            developer.reset_client()

    # Provider runtime exception → 503 error envelope with api_status PROCESSING
    developer.set_client(TransportStub("VERIFIED", fail_process=True))
    try:
        r = client.post("/v1/process", json={"raw_input": "x"})
        body = r.json()
        check(
            "E provider failure envelope → api_status PROCESSING",
            r.status_code == 503 and body["error"]["code"] == "PROVIDER_UNAVAILABLE" and body["error"]["api_status"] == "PROCESSING",
            str(body.get("error")),
        )
    finally:
        developer.reset_client()


# ---------------------------------------------------------------------------
# Section F — /v1/process/document carries the same mapping
# ---------------------------------------------------------------------------


def section_f(client: TestClient) -> None:
    print("\nF — /v1/process/document carries the same mapping")
    developer.set_client(TransportStub("REVIEW_REQUIRED"))
    try:
        r = client.post(
            "/v1/process/document",
            json={"raw_input": "Invoice: Net ₹2,026, GST ₹8, Total ₹2,034"},
        )
        body = r.json()
        check(
            "F1 document path api_status mapping",
            r.status_code == 200
            and body.get("api_status") == "REVIEW_REQUIRED"
            and body.get("engine_status") == "REVIEW_REQUIRED",
            f"{r.status_code} {body.get('api_status')}",
        )
    finally:
        developer.reset_client()

    developer.set_client(TransportStub("UNSUPPORTED_TRANSACTION"))
    try:
        r = client.post("/v1/process/document", json={"raw_input": "mystery document"})
        body = r.json()
        check(
            "F2 document path UNSUPPORTED mapping",
            r.status_code == 422 and body.get("api_status") == "UNSUPPORTED" and body.get("engine_status") == "UNSUPPORTED_TRANSACTION",
            f"{r.status_code} {body.get('api_status')}",
        )
    finally:
        developer.reset_client()


# ---------------------------------------------------------------------------
# Section G — error envelopes carry api_status
# ---------------------------------------------------------------------------


def section_g(client: TestClient) -> None:
    print("\nG — error envelopes carry api_status (transport rejections)")
    r = client.post("/v1/process", json={"wrong": 1})
    body = r.json()
    check(
        "G1 400 REQUEST_MALFORMED → INVALID_INPUT",
        r.status_code == 400
        and body["error"]["code"] == "REQUEST_MALFORMED"
        and body["error"]["api_status"] == "INVALID_INPUT"
        and body["error"]["retryable"] is False,
        str(body.get("error")),
    )
    r = client.post("/v1/process", json={"raw_input": "x"}, headers={"content-length": str(64 * 1024 + 1)})
    check(
        "G2 413 REQUEST_TOO_LARGE → INVALID_INPUT",
        r.status_code == 413 and r.json()["error"]["api_status"] == "INVALID_INPUT",
        str(r.json().get("error")),
    )
    # 503 gate path (metering unavailable) maps to PROCESSING
    from api.routes import developer as dev_mod

    resp = dev_mod._error_response(503, "METERING_UNAVAILABLE", "metering down")
    check("G3 metering unavailable envelope → PROCESSING", resp.status_code == 503 and resp.body and b"PROCESSING" in resp.body, "")


# ---------------------------------------------------------------------------
# Section H — back-compat: pre-existing fields byte-stable
# ---------------------------------------------------------------------------


def section_h(client: TestClient) -> None:
    print("\nH — back-compat: pre-existing response fields unchanged")
    developer.set_client(TransportStub("VERIFIED"))
    try:
        r = client.post("/v1/process", json={"raw_input": "x"})
        body = r.json()
        preexisting = [
            "api_version", "request_id", "status", "status_label", "success",
            "next_action", "issues", "grounding_issues", "rule_evidence",
            "interpretation", "accounting",
        ]
        check("H1 all pre-existing fields still present", all(k in body for k in preexisting), str([k for k in preexisting if k not in body]))
        check("H2 new fields are additive only", set(body) - set(preexisting) == {"api_status", "api_status_label", "retryable", "reason_code", "engine_status"}, str(set(body) - set(preexisting)))
    finally:
        developer.reset_client()


def main() -> int:
    print("=" * 70)
    print("PHASE 5A — SIX-STATE PUBLIC API STATUS CONTRACT (fte_fyjc_77)")
    print("=" * 70)
    section_a()
    section_b()
    section_c()
    client = _client()
    section_d(client)
    section_e(client)
    section_f(client)
    section_g(client)
    section_h(client)

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
