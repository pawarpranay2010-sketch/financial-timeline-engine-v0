#!/usr/bin/env python3
"""
Phase 13 — Hosted Developer API Boundary — evidence suite (fte_fyjc_62).

Proves that the versioned developer API (/v1/*) is a pure transport
boundary over the Phase 12 public developer interface, which forwards to
Kernel.process exactly once. The financial truth architecture underneath
is untouched.

Sections:
  A  API imports/starts without a live remote provider
  B  Valid request reaches the public Platrixa interface
  C  Public interface invokes Kernel exactly once
  D  HTTP handler does not call Kernel internals directly
  E  VERIFIED can only originate from Kernel (no status literals in HTTP)
  F  REVIEW_REQUIRED is preserved
  G  BLOCKED is preserved
  H  provider unavailable maps correctly
  I  malformed requests are rejected
  J  invalid/unsupported input is rejected
  K  RulePack works through the API
  L  RuleHook works through the API
  M  malicious hook attempting VERIFIED remains sanitized
  N  UNAVAILABLE/ERROR never becomes success
  O  secrets are absent from responses/logging
  P  health does not trigger model loading
  Q  readiness correctly reflects required dependency state
  R  JSON response is deterministic
  S  end-to-end integration (real facade + real Kernel + stub provider)
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The workspace .env may configure the Phase 16 metered gate; this suite
# exercises the zero-config hosted-API boundary, so gate activation
# variables are neutralized in-process BEFORE the API is imported. Gate
# state is re-read at request time, so this is effective (same convention
# as fte_fyjc_77). Assertions are unchanged.
for _var in ("PLATRIXA_METERING_DATABASE_URL", "PLATRIXA_DEV_API_KEY"):
    os.environ.pop(_var, None)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from platrixa import Platrixa, PlatrixaConfig  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Fixtures
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
        self.issues: list = []
        self.grounding_issues: list = []
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
        self.process_calls = 0
        self.last_request_id: str | None = None

    def process(self, text, request_id=None):
        self.process_calls += 1
        self.last_request_id = request_id
        if self._fail:
            from platrixa.errors import ProviderError

            raise ProviderError("provider down")
        return _Result(self._status, request_id or "fixed-req")

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return None


class NotReadyStub(TransportStub):
    def provider_status(self):
        return {"available": False, "loadable": False, "model_id": "", "reason": "hard failure"}


# Real facade + real Kernel with a stub provider (integration section S)
def _make_real_client():
    from backend.model_provider.base import ProviderConfig, ProviderStatus
    from platrixa import Platrixa, PlatrixaConfig

    class StubProvider:
        def __init__(self) -> None:
            self._config = ProviderConfig()
            self.interpret_calls = 0

        @property
        def config(self):
            return self._config

        def status(self):
            return ProviderStatus(
                available=True,
                model_id="stub-model",
                base_model_revision="stub-base",
                adapter_repo_id="stub-adapter",
                adapter_revision="stub-rev",
                reason="stub",
                loadable=True,
            )

        def interpret(self, raw_input: str):
            self.interpret_calls += 1
            from backend.model_provider.base import InterpretationResult

            return InterpretationResult(
                raw_input=raw_input,
                candidate=dict(_VALID_CANDIDATE),
                model_id="stub-model",
                provider_revision="stub-rev",
                generated_profile={},
            )

    class CountingFacade(Platrixa):
        """Real facade whose kernel is instrumented — config preserved."""

        def __init__(self, config) -> None:
            super().__init__(config)
            self.kernel_calls = 0
            real = self._kernel

            class _Counting:
                def __init__(self, k):
                    self._k = k

                def process(self, *a, **kw):
                    self.outer.kernel_calls += 1
                    return self._k.process(*a, **kw)

            counting = _Counting(real)
            counting.outer = self
            self._kernel = counting

    return CountingFacade, StubProvider


def _pack(tmp: Path, name: str, body: str) -> str:
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _router_paths(app) -> set:
    """Collect mounted route paths, unwrapping _IncludedRouter wrappers."""
    paths = set()
    for rt in app.routes:
        path = getattr(rt, "path", None)
        if path:
            paths.add(path)
        original = getattr(rt, "original_router", None)
        if original is not None:
            paths.update(getattr(r, "path", "") for r in original.routes)
    return paths


def section_a() -> TestClient:
    print("\nA — API imports/starts without a live remote provider")
    app = create_app()  # must not import torch/transformers or hit network
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/v1/health")
    check("A1 app constructs and /v1/health responds", r.status_code == 200, str(r.status_code))
    body = r.json()
    check("A2 health body shape", body.get("status") == "ok" and body.get("api_version") == "v1")
    heavy = [m for m in sys.modules if m.split(".")[0] in ("torch", "transformers", "psycopg2")]
    check("A3 no heavy modules loaded at API import", heavy == [], str(heavy))
    check("A4 /v1/process route is mounted", "/v1/process" in _router_paths(app))
    return client


def section_b_c(client: TestClient) -> None:
    print("\nB/C — request reaches the public interface; Kernel invoked exactly once")
    CountingFacade, StubProvider = _make_real_client()

    facade = CountingFacade(PlatrixaConfig(provider="auto"))
    # Swap the real provider for the stub (model-free integration).
    facade._kernel._k._model_provider = StubProvider()

    developer.set_client(facade)
    try:
        r = client.post(
            "/v1/process",
            json={"raw_input": "purchased furniture from raj for rs.25000"},
            headers={"x-request-id": "bc-1"},
        )
        check("B1 valid request → 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        check("B2 request reached the public interface (interpret called)", getattr(facade._kernel._k._model_provider, "interpret_calls", 0) >= 1)
        check("C1 public interface invoked Kernel exactly once", facade.kernel_calls == 1, str(facade.kernel_calls))
        body = r.json()
        check("B3 response is the versioned contract", body.get("api_version") == "v1" and body.get("status") == "VERIFIED")
        check("C2 no persistence performed at the API layer", "persisted" not in body)
    finally:
        developer.reset_client()


def section_d() -> None:
    print("\nD — HTTP handler does not call Kernel internals directly")
    src = Path("api/routes/developer.py").read_text(encoding="utf-8")
    forbidden = [
        "hardened_bookkeeping", "fyjc_grounding_gate",
        "StructuredInterpretationValidator", "RuleEngine",
        "backend.kernel", "backend.model_provider", "backend.rules",
        "backend.persistence", "psycopg", ".interpret(",
        "PostgresResultPersistence", "model_provider",
    ]
    hits = [p for p in forbidden if p in src]
    check("D1 no backend-internal references in the route", hits == [], str(hits))
    platrixa_imports = [
        line.strip() for line in src.splitlines()
        if line.strip().startswith("from platrixa")
    ]
    ok = all(
        ("errors import InputError" in l) or ("import Platrixa" in l) or ("PlatrixaConfig" in l)
        for l in platrixa_imports
    )
    check("D2 only public-interface imports from platrixa", ok and len(platrixa_imports) >= 1, str(platrixa_imports))


def section_e(client: TestClient) -> None:
    print("\nE — VERIFIED can only originate from Kernel")
    src = Path("api/routes/developer.py").read_text(encoding="utf-8")
    # Strip docstrings/comments before scanning: documenting the Kernel-owned
    # taxonomy in prose is legitimate; executable status literals are not.
    import ast

    tree = ast.parse(src)
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstring_nodes.add(id(body[0].value))
    code_parts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes:
            code_parts.append(node.value)
    executable_text = "\n".join(code_parts)
    literals = [s for s in ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED") if s in executable_text]
    check("E1 zero status literals in executable route code", literals == [], str(literals))
    # Dynamic: state passes through verbatim from the public interface.
    developer.set_client(TransportStub("VERIFIED"))
    try:
        r = client.post("/v1/process", json={"raw_input": "x"})
        check("E2 kernel VERIFIED passes through verbatim", r.json()["status"] == "VERIFIED")
    finally:
        developer.reset_client()


def sections_f_g(client: TestClient) -> None:
    print("\nF/G — REVIEW_REQUIRED and BLOCKED preserved")
    for status in ("REVIEW_REQUIRED", "BLOCKED"):
        developer.set_client(TransportStub(status))
        try:
            r = client.post("/v1/process", json={"raw_input": "x"})
            body = r.json()
            check(
                f"{'F' if status == 'REVIEW_REQUIRED' else 'G'}1 {status} preserved verbatim",
                r.status_code == 200 and body["status"] == status and body["success"] is False,
                f"{r.status_code} {body.get('status')}",
            )
        finally:
            developer.reset_client()


def section_h(client: TestClient) -> None:
    print("\nH — provider unavailable maps correctly")
    developer.set_client(TransportStub("VERIFIED", fail_process=True))
    try:
        r = client.post("/v1/process", json={"raw_input": "x"})
        body = r.json()
        check("H1 PlatrixaError → 503", r.status_code == 503, str(r.status_code))
        check("H2 error envelope is machine-readable", body.get("error", {}).get("code") == "PROVIDER_UNAVAILABLE")
        check("H3 failure is never a success", "status" not in body or body.get("status") != "VERIFIED")
    finally:
        developer.reset_client()
    # Kernel-level MODEL_UNAVAILABLE (fail-closed state, not an exception)
    developer.set_client(TransportStub("MODEL_UNAVAILABLE"))
    try:
        r = client.post("/v1/process", json={"raw_input": "x"})
        check("H4 MODEL_UNAVAILABLE state → 503 with state verbatim", r.status_code == 503 and r.json()["status"] == "MODEL_UNAVAILABLE")
    finally:
        developer.reset_client()


def section_i(client: TestClient) -> None:
    print("\nI — malformed requests rejected")
    # Phase 15 contract: network-level structural/parsing failures are
    # normalized to 400 (REQUEST_MALFORMED envelope); this is a deliberate,
    # documented change from the earlier framework-default 422.
    r = client.post("/v1/process", json={"wrong": 1})
    check("I1 missing raw_input → 400 (Phase 15 normalization)", r.status_code == 400 and r.json()["error"]["code"] == "REQUEST_MALFORMED", str(r.status_code))
    r = client.post("/v1/process", json={"raw_input": 12345})
    check("I2 non-string raw_input → 400 (Phase 15 normalization)", r.status_code == 400, str(r.status_code))
    r = client.post(
        "/v1/process", json={"raw_input": "x"}, headers={"content-length": str(64 * 1024 + 1)}
    )
    check("I3 oversized body → 413 before processing", r.status_code == 413, str(r.status_code))
    r = client.post("/v1/process", content=b"{not json", headers={"content-type": "application/json"})
    check("I4 invalid JSON → 400 (Phase 15 normalization)", r.status_code == 400 and r.json()["error"]["code"] == "REQUEST_MALFORMED", str(r.status_code))


def section_j(client: TestClient) -> None:
    print("\nJ — invalid/unsupported input rejected (public input contract)")

    class InputRaising(TransportStub):
        def process(self, text, request_id=None):
            from platrixa.errors import InputError

            self.process_calls += 1
            raise InputError("input must be a non-empty transaction string")

    stub = InputRaising()
    developer.set_client(stub)
    try:
        r = client.post("/v1/process", json={"raw_input": "   "})
        body = r.json()
        check("J1 empty/whitespace input → 422 INPUT_INVALID", r.status_code == 422 and body["error"]["code"] == "INPUT_INVALID")
        check("J2 invalid input never reached Kernel processing", stub.process_calls == 1 and "status" not in body)
    finally:
        developer.reset_client()


def _rules_section(client: TestClient) -> None:
    print("\nK/L/M — RulePack + RuleHooks through the API; VERIFIED smuggling sanitized")
    from backend.rules.contract import RuleDecision

    CountingFacade, StubProvider = _make_real_client()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Pack K-pass: threshold the stub candidate's amount (25000 < 100000 → pass)
        pack_pass = _pack(
            tmp_path, "pass.yaml",
            "rules:\n"
            "  - id: amount_ceiling\n"
            "    type: threshold\n"
            "    field: amounts.0.value\n"
            "    operator: '<'\n"
            "    value: 100000\n",
        )
        # Pack K-fail: require a field the stub candidate does not have
        pack_fail = _pack(
            tmp_path, "fail.yaml",
            "rules:\n"
            "  - id: invoice_required\n"
            "    type: required\n"
            "    field: invoice_reference\n",
        )
        # Malformed pack must fail closed at construction
        pack_bad = _pack(tmp_path, "bad.yaml", "rules:\n  - id: x\n    type: not_a_type\n")

        # K — RulePack through the API
        facade = CountingFacade(PlatrixaConfig(provider="auto", rule_pack=pack_pass))
        facade._kernel._k._model_provider = StubProvider()
        developer.set_client(facade)
        try:
            r = client.post("/v1/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
            body = r.json()
            check("K1 valid pack: deterministic success preserved", r.status_code == 200 and body["status"] == "VERIFIED", str(body.get("status")))
            check("K2 rule evidence surfaced in the API response", len(body.get("rule_evidence", [])) == 1)
        finally:
            developer.reset_client()

        facade = CountingFacade(PlatrixaConfig(provider="auto", rule_pack=pack_fail))
        facade._kernel._k._model_provider = StubProvider()
        developer.set_client(facade)
        try:
            r = client.post("/v1/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
            body = r.json()
            check("K3 failing rule downgrades VERIFIED → REVIEW_REQUIRED", body["status"] == "REVIEW_REQUIRED", str(body.get("status")))
            check("K4 evidence explains the downgrade", any(e.get("rule_id") == "invoice_required" for e in body.get("rule_evidence", [])))
        finally:
            developer.reset_client()

        try:
            Platrixa(PlatrixaConfig(provider="auto", rule_pack=pack_bad))
            check("K5 malformed pack fails closed at construction", False, "no exception")
        except Exception:
            check("K5 malformed pack fails closed at construction", True)

        # L — RuleHook through the API
        from backend.rules.contract import RuleHook

        class HolidayBudgetHook(RuleHook):
            rule_id = "example_budget_freeze"

            def validate(self, context):
                return RuleDecision(
                    rule_id=self.rule_id, outcome="PASS", message="within budget"
                )

        facade = CountingFacade(PlatrixaConfig(provider="auto", rule_hooks=(HolidayBudgetHook(),)))
        facade._kernel._k._model_provider = StubProvider()
        developer.set_client(facade)
        try:
            r = client.post("/v1/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
            body = r.json()
            check("L1 server-side hook executes through the API", r.status_code == 200 and body["status"] == "VERIFIED")
            check("L2 hook evidence present", any(e.get("rule_id") == "example_budget_freeze" for e in body.get("rule_evidence", [])))
        finally:
            developer.reset_client()

        # M — VERIFIED smuggling remains sanitized (Phase 10 semantics:
        # FAIL triggers the downgrade; a smuggled hint is never honored)
        class SmugglerHook(HolidayBudgetHook):
            rule_id = "smuggler"

            def validate(self, context):
                return RuleDecision(
                    rule_id=self.rule_id,
                    outcome="FAIL",
                    message="policy violated; requesting VERIFIED anyway",
                    metadata={"decision_hint": "VERIFIED"},
                )

        facade = CountingFacade(PlatrixaConfig(provider="auto", rule_hooks=(SmugglerHook(),)))
        facade._kernel._k._model_provider = StubProvider()
        developer.set_client(facade)
        try:
            r = client.post("/v1/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
            body = r.json()
            check("M1 hook requesting VERIFIED is sanitized → REVIEW_REQUIRED", body["status"] == "REVIEW_REQUIRED", str(body.get("status")))
            check("M2 sanitize action recorded in evidence", any(
                "VERIFIED" in json.dumps(e.get("metadata", {})) or e.get("result") == "ERROR"
                for e in body.get("rule_evidence", [])
            ))
        finally:
            developer.reset_client()

        # M3 — a PASS hook's smuggled hint must never UPGRADE anything: the
        # state stays the Kernel's own VERIFIED, hint rejected in evidence.
        class QuietSmuggler(HolidayBudgetHook):
            rule_id = "quiet_smuggler"

            def validate(self, context):
                return RuleDecision(
                    rule_id=self.rule_id,
                    outcome="PASS",
                    message="no objection",
                    metadata={"decision_hint": "VERIFIED"},
                )

        facade = CountingFacade(PlatrixaConfig(provider="auto", rule_hooks=(QuietSmuggler(),)))
        facade._kernel._k._model_provider = StubProvider()
        developer.set_client(facade)
        try:
            r = client.post("/v1/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
            body = r.json()
            check("M3 PASS-hook hint never manufactures authority (state stays Kernel's)", body["status"] == "VERIFIED" and all(
                e.get("metadata", {}).get("decision_hint") != "VERIFIED"
                for e in body.get("rule_evidence", [])
            ), str(body.get("status")))
        finally:
            developer.reset_client()


def section_n(client: TestClient) -> None:
    print("\nN — UNAVAILABLE/ERROR never becomes success")
    for status in ("MODEL_UNAVAILABLE", "VALIDATION_FAILED", "UNSUPPORTED_TRANSACTION"):
        developer.set_client(TransportStub(status))
        try:
            r = client.post("/v1/process", json={"raw_input": "x"})
            body = r.json()
            check(
                f"N1 {status} → non-200 transport, state verbatim, success=False",
                r.status_code >= 400 and body["status"] == status and body["success"] is False,
                f"{r.status_code} {body.get('status')} {body.get('success')}",
            )
        finally:
            developer.reset_client()


def section_o(client: TestClient) -> None:
    print("\nO — secrets absent from responses/logging")
    developer.set_client(TransportStub("VERIFIED"))
    try:
        r = client.post(
            "/v1/process",
            json={"raw_input": "purchased furniture for cash 15000"},
            headers={"x-request-id": "sec-1"},
        )
        body = json.dumps(r.json()).lower()
        check("O1 no credential material in response", not any(
            s in body for s in ("hf_", "token", "password", "postgres://", "authorization")
        ))
        # Logging: the financial content must never be logged
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        api_logger = logging.getLogger("platrixa.api")
        api_logger.addHandler(handler)
        api_logger.setLevel(logging.INFO)
        try:
            client.post("/v1/process", json={"raw_input": "SECRET-TRANSACTION-12345"}, headers={"x-request-id": "sec-2"})
        finally:
            api_logger.removeHandler(handler)
        logs = stream.getvalue()
        check("O2 financial content never logged", "SECRET-TRANSACTION-12345" not in logs)
        check("O3 safe metadata IS logged", "sec-2" in logs and "/v1/process" in logs)
    finally:
        developer.reset_client()


def section_p(client: TestClient) -> None:
    print("\nP — health does not trigger model loading")
    # No client injected: health must still work (proves it constructs nothing)
    marker = object()
    developer._get_client._override = marker  # sentinel: if health touches it, it breaks
    try:
        r = client.get("/v1/health")
        check("P1 /v1/health works without any client", r.status_code == 200)
        src = Path("api/routes/developer.py").read_text(encoding="utf-8")
        health_src = src.split("def health_v1")[1].split("@router.get(\"/v1/ready\"")[0]
        check("P2 health handler never calls _get_client", "_get_client" not in health_src)
    finally:
        developer._get_client._override = None


def section_q(client: TestClient) -> None:
    print("\nQ — readiness reflects dependency state")
    developer.set_client(TransportStub("VERIFIED"))
    try:
        r = client.get("/v1/ready")
        check("Q1 ready when provider is available/loadable", r.status_code == 200 and r.json()["status"] == "ready")
    finally:
        developer.reset_client()
    developer.set_client(NotReadyStub())
    try:
        r = client.get("/v1/ready")
        body = r.json()
        check("Q2 not_ready when provider is hard-failed", body["status"] == "not_ready" and body.get("reason"))
        check("Q3 dependency failure is not hidden", body.get("provider", {}).get("available") is False)
    finally:
        developer.reset_client()


def section_r(client: TestClient) -> None:
    print("\nR — JSON response is deterministic")
    developer.set_client(TransportStub("VERIFIED"))
    try:
        headers = {"x-request-id": "det-1"}
        r1 = client.post("/v1/process", json={"raw_input": "same input"}, headers=headers)
        r2 = client.post("/v1/process", json={"raw_input": "same input"}, headers=headers)
        check("R1 identical requests → byte-identical bodies", r1.content == r2.content)
        check("R2 no object reprs/memory addresses", "0x" not in r1.text and "object at" not in r1.text)
    finally:
        developer.reset_client()


def section_s(client: TestClient) -> None:
    print("\nS — end-to-end integration (real facade + real Kernel + stub provider)")
    CountingFacade, StubProvider = _make_real_client()
    facade = CountingFacade(PlatrixaConfig(provider="auto"))
    facade._kernel._k._model_provider = StubProvider()
    developer.set_client(facade)
    try:
        r = client.post(
            "/v1/process",
            json={"raw_input": "purchased furniture from raj for rs.25000"},
            headers={"x-request-id": "e2e-1"},
        )
        body = r.json()
        check("S1 HTTP → interface → Kernel → deterministic result → HTTP", r.status_code == 200 and body["status"] == "VERIFIED", f"{r.status_code} {body.get('status')}")
        acct = body.get("accounting") or {}
        check("S2 real deterministic accounting result present", bool(acct))
        check("S3 interpretation is the schema-validated candidate", (body.get("interpretation") or {}).get("transaction_type") == "PURCHASE")
        check("S4 exactly one Kernel.process per request", facade.kernel_calls == 1, str(facade.kernel_calls))
        check("S5 provider called exactly once via the Kernel", getattr(facade._kernel._k._model_provider, "interpret_calls", 0) == 1)
        # Real facade input contract through HTTP: empty string is rejected
        # by request-schema validation (422); whitespace-only passes the
        # schema and is rejected by the public interface's InputError
        # contract (422 INPUT_INVALID envelope).
        r = client.post("/v1/process", json={"raw_input": ""})
        check("S6 empty input → 400 (Phase 15 schema normalization)", r.status_code == 400, str(r.status_code))
        r = client.post("/v1/process", json={"raw_input": "   "})
        check("S7 whitespace input → 422 INPUT_INVALID (facade contract)", r.status_code == 422 and r.json()["error"]["code"] == "INPUT_INVALID", str(r.status_code))
    finally:
        developer.reset_client()


def main() -> int:
    print("=" * 78)
    print("PHASE 13 — HOSTED DEVELOPER API BOUNDARY — EVIDENCE SUITE")
    print("=" * 78)

    client = section_a()
    section_b_c(client)
    section_d()
    section_e(client)
    sections_f_g(client)
    section_h(client)
    section_i(client)
    section_j(client)
    _rules_section(client)
    section_n(client)
    section_o(client)
    section_p(client)
    section_q(client)
    section_r(client)
    section_s(client)

    failed = [c for c in CHECKS if not c[1]]
    print("\n" + "=" * 78)
    print(f"RESULT: {'PASS' if not failed else 'FAIL'} — {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    print("=" * 78)
    for name, _, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
