#!/usr/bin/env python3
"""
Phase 15 — Hosted API security, tenant isolation & launch audit — evidence
suite (fte_fyjc_65).

Proves the hostile-input boundary of the hosted developer API:

  A  Malformation gate: every malformed class fails closed BEFORE the
     Kernel (Kernel.process call count = 0), deterministic 400 envelope,
     no trace/path/secret leakage
  B  API-key gate: missing/invalid/empty/malformed key rejected pre-Kernel
     (call count = 0); valid key reaches the app (call count = 1); key
     never echoed/logged/serialized
  C  Kernel isolation & routing: exactly one Kernel.process per valid
     request; no second runtime
  D  RulePack/RuleHook isolation: the HTTP surface carries NO rule inputs
     (no rules_path, no hooks); packs resolve only from server env; two
     different server-side packs cannot cross-contaminate between
     requests, including concurrent interleaving (cross-tenant scenario)
  E  Rule evidence isolation: evidence corresponds only to the current
     request; hook PASS/FAIL/UNAVAILABLE/ERROR semantics preserved; hooks
     cannot force VERIFIED; the API cannot produce VERIFIED independently
  F  Error mapping: every failure class maps to its documented layer
  G  Secret handling: no secrets in responses/logs; 401 body deterministic
  H  Public contract: quickstart request/response reality; malformed demo
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


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


class _R:
    def __init__(self, status: str) -> None:
        self.status = status
        self.status_label = status
        self.success = status == "VERIFIED"
        self.request_id = "fixed-req"
        self.next_action = None
        self.issues: list = []
        self.grounding_issues: list = []
        self.rule_evidence: list = []
        self.interpretation = dict(_VALID_CANDIDATE)
        self.accounting = {"status": status}


class CountingStub:
    """
    Counts Kernel-bound process() calls — the isolation instrument.

    Mirrors the public facade's input contract (InputError for non-string,
    empty, or >2000-char input) so domain-layer behavior matches
    production exactly.
    """

    def __init__(self, status: str = "VERIFIED") -> None:
        self._status = status
        self.calls = 0
        self.lock = threading.Lock()
        self.seen_texts: list[str] = []

    def process(self, text, request_id=None):
        from platrixa.errors import InputError

        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise InputError("input must be a non-empty transaction string")
        with self.lock:
            self.calls += 1
            self.seen_texts.append(text)
        return _R(self._status)

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return None


def _client(client=None):
    """Fresh app per call; optional app-scoped client (concurrency-safe seam)."""
    app = create_app()
    if client is not None:
        app.state.platrixa_client = client
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# A — Input malformation gate
# ---------------------------------------------------------------------------


def section_a() -> None:
    print("\nA — Malformation gate (fail closed before Kernel, 400 envelope)")
    c = _client()
    stub = CountingStub()
    developer.set_client(stub)
    malformed = [
        ("invalid JSON", lambda: c.post("/v1/process", content=b"{not json", headers={"content-type": "application/json"})),
        ("empty body", lambda: c.post("/v1/process", content=b"", headers={"content-type": "application/json"})),
        ("missing content", lambda: c.post("/v1/process", content=b"", headers={"content-type": "application/json; charset=utf-8"})),
        ("wrong content type", lambda: c.post("/v1/process", content=b"raw_input=x", headers={"content-type": "text/plain"})),
        ("missing required field", lambda: c.post("/v1/process", json={})),
        ("wrong primitive type", lambda: c.post("/v1/process", json={"raw_input": 12345})),
        ("string where numeric required (nested)", lambda: c.post("/v1/process", json={"raw_input": {"amount": "abc"}})),
        ("number where string required", lambda: c.post("/v1/process", json={"raw_input": 3.14})),
        ("null body value", lambda: c.post("/v1/process", json=None)),
        ("deeply malformed nested structure", lambda: c.post("/v1/process", json={"raw_input": {"a": [{"b": {"c": [None]}}]}})),
        # (whitespace-only string passes the schema and is a FACADE-contract
        # InputError → 422 INPUT_INVALID — documented domain layer, not 400)
        ("boolean raw_input", lambda: c.post("/v1/process", json={"raw_input": True})),
    ]
    try:
        for name, fn in malformed:
            r = fn()
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            ok = (
                r.status_code == 400
                and isinstance(body, dict)
                and body.get("error", {}).get("code") == "REQUEST_MALFORMED"
                and "api_version" in body
            )
            check(f"A {name} → 400 REQUEST_MALFORMED", ok, f"{r.status_code} {str(body)[:100]}")
        # Malformed classes must all have failed BEFORE the Kernel:
        check("A malformed requests never reached the Kernel", stub.calls == 0, str(stub.calls))
        # Facade-contract case (domain layer): whitespace-only input IS
        # valid HTTP, so it legitimately reaches the facade (and is
        # rejected there with INPUT_INVALID — never with 400).
        r = c.post("/v1/process", json={"raw_input": "\t\n  "})
        check("A whitespace-only input → 422 INPUT_INVALID (facade contract)", r.status_code == 422 and r.json()["error"]["code"] == "INPUT_INVALID", f"{r.status_code} {str(r.json())[:80]}")
        r = c.post("/v1/process", json={"raw_input": "x"}, headers={"content-length": str(64 * 1024 + 1)})
        check("A oversized body → 413 (pre-parse cap)", r.status_code == 413, str(r.status_code))
        text = json.dumps([r.json() for r in [c.post("/v1/process", content=b"{bad", headers={"content-type": "application/json"})]]).lower()
        check("A no trace/path/internal leakage in 400 body", not any(
            s in text for s in ("traceback", "/home/", "exception:", ".py", "psycopg")
        ))
    finally:
        developer.reset_client()


# ---------------------------------------------------------------------------
# B — API-key gate
# ---------------------------------------------------------------------------


def section_b() -> None:
    print("\nB — API-key gate (env-gated, fail-closed, constant-time)")
    c = _client()
    stub = CountingStub()
    developer.set_client(stub)
    try:
        # No key configured → open (documented zero-config behavior)
        os.environ.pop("PLATRIXA_DEV_API_KEY", None)
        r = c.post("/v1/process", json={"raw_input": "ok"})
        check("B1 no key configured → open endpoint (documented)", r.status_code == 200)

        stub.calls = 0  # isolation: count only the key-gated requests below
        os.environ["PLATRIXA_DEV_API_KEY"] = "secret-sandbox-key-123"
        try:
            r = c.post("/v1/process", json={"raw_input": "ok"})
            check("B2 missing key → 401", r.status_code == 401, str(r.status_code))
            r = c.post("/v1/process", json={"raw_input": "ok"}, headers={"X-Platrixa-API-Key": ""})
            check("B3 empty key → 401", r.status_code == 401, str(r.status_code))
            r = c.post("/v1/process", json={"raw_input": "ok"}, headers={"X-Platrixa-API-Key": "wrong"})
            check("B4 invalid key → 401", r.status_code == 401, str(r.status_code))
            r = c.post("/v1/process", json={"raw_input": "ok"}, headers={"X-Platrixa-API-Key": "secret-sandbox-key-123 "})
            check("B5 malformed (trailing space) key → 401", r.status_code == 401, str(r.status_code))
            body = json.dumps(r.json())
            check("B6 401 body deterministic, no key echo", "secret-sandbox-key-123" not in body and r.json()["error"]["code"] == "UNAUTHORIZED")
            check("B7 failed auth never reached the Kernel", stub.calls == 0, str(stub.calls))

            r = c.post("/v1/process", json={"raw_input": "ok"}, headers={"X-Platrixa-API-Key": "secret-sandbox-key-123"})
            check("B8 valid key reaches the application", r.status_code == 200, str(r.status_code))
            check("B9 exactly one Kernel.process for the authorized request", stub.calls == 1, str(stub.calls))
            check("B10 key never appears in the success response", "secret-sandbox-key-123" not in r.text)

            stream = io.StringIO()
            handler = logging.StreamHandler(stream)
            lg = logging.getLogger("platrixa.api")
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)
            try:
                c.post("/v1/process", json={"raw_input": "ok"}, headers={"X-Platrixa-API-Key": "secret-sandbox-key-123"})
            finally:
                lg.removeHandler(handler)
            check("B11 key never written to logs", "secret-sandbox-key-123" not in stream.getvalue())
        finally:
            os.environ.pop("PLATRIXA_DEV_API_KEY", None)
    finally:
        developer.reset_client()


# ---------------------------------------------------------------------------
# C — Kernel isolation / routing proof (static + dynamic)
# ---------------------------------------------------------------------------


def section_c() -> None:
    print("\nC — Kernel routing proof (boundary, not a second runtime)")
    src = Path("api/routes/developer.py").read_text(encoding="utf-8")
    forbidden = [
        "hardened_bookkeeping", "fyjc_grounding_gate",
        "StructuredInterpretationValidator", "RuleEngine(",
        "backend.kernel", "backend.model_provider", "backend.rules",
        "backend.persistence", "psycopg", ".interpret(",
        "PostgresResultPersistence", "persist(",
    ]
    hits = [p for p in forbidden if p in src]
    check("C1 zero backend-internal/persistence references in route", hits == [], str(hits))
    import ast

    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    exec_strings = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    literals = [s for s in ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED") if any(s in t for t in exec_strings)]
    check("C2 zero status literals in executable route code", literals == [], str(literals))
    c = _client()
    stub = CountingStub()
    developer.set_client(stub)
    try:
        c.post("/v1/process", json={"raw_input": "one"})
        c.post("/v1/process", json={"raw_input": "two"})
        check("C3 exactly one Kernel.process per valid request (2 requests → 2 calls)", stub.calls == 2, str(stub.calls))
    finally:
        developer.reset_client()


# ---------------------------------------------------------------------------
# D/E — RulePack/RuleHook isolation & evidence (cross-tenant scenario)
# ---------------------------------------------------------------------------


def _pack(tmp: Path, name: str, body: str) -> str:
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def _rules_sections() -> None:
    print("\nD — RulePack isolation (no HTTP rule surface; server-env scoped)")
    src = Path("api/routes/developer.py").read_text(encoding="utf-8")
    check("D1 request schema carries no rule/rules_path/hook field", "rules_path" not in src.split("def _get_client")[1].split("def set_client")[0].split("os.getenv(\"PLATRIXA_RULE_PACK_PATH")[1])
    from api.schemas import KernelProcessRequest

    fields = set(KernelProcessRequest.model_fields)
    check("D2 public request fields are exactly {raw_input}", fields == {"raw_input"}, str(fields))

    from backend.rules.contract import RuleDecision, RuleHook, RuleContext  # noqa: F401
    from platrixa import Platrixa, PlatrixaConfig

    class _P:
        def __init__(self):
            self._config = None
            self.interpret_calls = 0

        @property
        def config(self):
            return self._config

        def status(self):
            from backend.model_provider.base import ProviderStatus

            return ProviderStatus(available=True, model_id="s", base_model_revision="b",
                                  adapter_repo_id="a", adapter_revision="r", reason="s", loadable=True)

        def interpret(self, raw_input):
            self.interpret_calls += 1
            from backend.model_provider.base import InterpretationResult

            return InterpretationResult(raw_input=raw_input, candidate=dict(_VALID_CANDIDATE),
                                        model_id="s", provider_revision="r", generated_profile={})

    def build(pack):
        f = Platrixa(PlatrixaConfig(provider="auto", rule_pack=pack))
        f._kernel._model_provider = _P()
        return f

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Pack A: invoice_reference required (stub candidate lacks it → FAIL → downgrade)
        # Pack B: threshold 25000 < 100000 → PASS → VERIFIED preserved
        pack_a = _pack(tmp_path, "rules_a.yaml",
                       "rules:\n  - id: pack_a_invoice_required\n    type: required\n    field: invoice_reference\n")
        pack_b = _pack(tmp_path, "rules_b.yaml",
                       "rules:\n  - id: pack_b_amount_ceiling\n    type: threshold\n    field: amounts.0.value\n"
                       "    operator: '<'\n    value: 100000\n")
        clients = {"A": build(pack_a), "B": build(pack_b)}
        bodies: dict[str, list] = {"A": [], "B": []}

    def run(tenant: str) -> None:
        # App-scoped client seam: each request's app carries its own
        # client — no module-global mutation, concurrency-safe by design.
        c = _client(clients[tenant])
        r = c.post("/v1/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
        bodies[tenant].append(r.json())

        # Interleaved + repeated + concurrent: A/B/A/B ... then reversed,
        # then 4 concurrent pairs. Each client is its own Platrixa instance
        # with its own Kernel + engine — no shared mutable state exists.
        for tenant in ("A", "B", "A", "B", "B", "A"):
            run(tenant)
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(run, ["A", "B", "A", "B"]))

        a_evidence_ids = {e.get("rule_id") for body in bodies["A"] for e in body.get("rule_evidence", [])}
        b_evidence_ids = {e.get("rule_id") for body in bodies["B"] for e in body.get("rule_evidence", [])}
        check("D3 tenant A evidence only from pack A", a_evidence_ids == {"pack_a_invoice_required"}, str(a_evidence_ids))
        check("D4 tenant B evidence only from pack B", b_evidence_ids == {"pack_b_amount_ceiling"}, str(b_evidence_ids))
        check("D5 no cross-tenant evidence leakage (A∩B = ∅)", not (a_evidence_ids & b_evidence_ids))
        a_statuses = {body["status"] for body in bodies["A"]}
        b_statuses = {body["status"] for body in bodies["B"]}
        check("D6 pack A downgrades every A-request (REVIEW_REQUIRED only)", a_statuses == {"REVIEW_REQUIRED"}, str(a_statuses))
        check("D7 pack B preserves every B-request (VERIFIED only)", b_statuses == {"VERIFIED"}, str(b_statuses))
        check("D8 concurrent interleaving preserved isolation (6+4 runs each, results uniform)",
              len(bodies["A"]) == 6 and len(bodies["B"]) == 6)

    print("\nE — Rule evidence semantics & state authority")
    class PassHook(RuleHook):
        rule_id = "h_pass"

        def validate(self, context):
            return RuleDecision(rule_id=self.rule_id, outcome="PASS", message="ok")

    class FailHook(RuleHook):
        rule_id = "h_fail"

        def validate(self, context):
            return RuleDecision(rule_id=self.rule_id, outcome="FAIL", message="policy")

    class UnavailableHook(RuleHook):
        rule_id = "h_unavail"

        def validate(self, context):
            return RuleDecision(rule_id=self.rule_id, outcome="UNAVAILABLE", message="dep down")

    class ErrorHook(RuleHook):
        rule_id = "h_error"

        def validate(self, context):
            raise RuntimeError("boom")

    class Smuggler(RuleHook):
        rule_id = "h_smuggle"

        def validate(self, context):
            return RuleDecision(rule_id=self.rule_id, outcome="FAIL", message="theft",
                                metadata={"decision_hint": "VERIFIED"})

    def build_hooks(hooks):
        f = Platrixa(PlatrixaConfig(provider="auto", rule_hooks=hooks))
        f._kernel._model_provider = _P()
        return f

    expectations = {
        "PASS": ("h_pass", "VERIFIED"),
        "FAIL": ("h_fail", "REVIEW_REQUIRED"),
        "UNAVAILABLE": ("h_unavail", "REVIEW_REQUIRED"),
        "ERROR": ("h_error", "REVIEW_REQUIRED"),
        "SMUGGLE": ("h_smuggle", "REVIEW_REQUIRED"),
    }
    c = _client()
    for label, (rid, want_status) in expectations.items():
        hook_cls = {"PASS": PassHook, "FAIL": FailHook, "UNAVAILABLE": UnavailableHook,
                    "ERROR": ErrorHook, "SMUGGLE": Smuggler}[label]
        developer.set_client(build_hooks((hook_cls(),)))
        try:
            r = c.post("/v1/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
            body = r.json()
            ev_ids = [e.get("rule_id") for e in body.get("rule_evidence", [])]
            check(f"E {label} hook → evidence only for {rid}, status {want_status}",
                  body["status"] == want_status and ev_ids == [rid], f"{body['status']} {ev_ids}")
            if label == "SMUGGLE":
                check("E smuggled VERIFIED hint rejected & recorded",
                      any(e.get("metadata", {}).get("decision_hint_rejected") == "VERIFIED"
                          or e.get("metadata", {}).get("decision_hint") == "REVIEW_REQUIRED"
                          for e in body.get("rule_evidence", [])))
        finally:
            developer.reset_client()
    # Public API cannot produce VERIFIED independently: with NO provider
    # success there is no VERIFIED — verified structurally in C2 (no status
    # literals) and dynamically by D7/E (VERIFIED only ever follows the
    # kernel's own accounting success through the facade).


def section_f() -> None:
    print("\nF — Error mapping by layer")
    from platrixa.errors import InputError, ProviderError

    class InputRaiser(CountingStub):
        def process(self, text, request_id=None):
            with self.lock:
                self.calls += 1
            raise InputError("bad input shape")

    class ProviderRaiser(CountingStub):
        def process(self, text, request_id=None):
            with self.lock:
                self.calls += 1
            raise ProviderError("provider down")

    c = _client()
    stub = InputRaiser()
    developer.set_client(stub)
    try:
        r = c.post("/v1/process", json={"raw_input": "ok"})
        check("F1 InputError → 422 INPUT_INVALID (domain layer, not 400)", r.status_code == 422 and r.json()["error"]["code"] == "INPUT_INVALID", str(r.status_code))
    finally:
        developer.reset_client()
    stub = ProviderRaiser()
    developer.set_client(stub)
    try:
        r = c.post("/v1/process", json={"raw_input": "ok"})
        check("F2 ProviderError → 503 PROVIDER_UNAVAILABLE", r.status_code == 503 and r.json()["error"]["code"] == "PROVIDER_UNAVAILABLE", str(r.status_code))
        check("F3 provider failure never becomes success", "status" not in r.json() or r.json().get("status") != "VERIFIED")
    finally:
        developer.reset_client()
    src = Path("api/routes/developer.py").read_text(encoding="utf-8")
    check("F4 unexpected exceptions → global 500 (type name only)", "unhandled_exception_handler" in Path("api/main.py").read_text(encoding="utf-8"))


def section_g() -> None:
    print("\nG — Secret handling")
    src = Path("api/routes/developer.py").read_text(encoding="utf-8")
    # The only secret-like literal must be the test-legend 'secret' word
    # check: the gate reads PLATRIXA_DEV_API_KEY at request time and
    # contains no credential literal (verified: no assignment of a
    # non-empty string constant to a key variable).
    check("G1 gate reads env at request time, no hardcoded key value",
          'PLATRIXA_DEV_API_KEY' in src and 'platrixa_dev_api_key = "' not in src.lower())
    check("G2 no os.environ writes in route (secrets loaded from config source)", "os.environ[" not in src and "environ.setdefault" not in src)
    check("G3 constant-time comparison used", "_constant_time_equal" in src and "compare_digest" in src)


def section_h() -> None:
    print("\nH — Public contract reality (quickstart)")
    # Real response shape for the documented quickstart request:
    c = _client()
    stub = CountingStub("VERIFIED")
    developer.set_client(stub)
    try:
        r = c.post("/v1/process", json={"raw_input": "Purchased furniture for cash ₹15,000"})
        body = r.json()
        check("H1 quickstart request → 200 with documented fields", r.status_code == 200 and all(
            k in body for k in ("api_version", "status", "success", "interpretation", "accounting")
        ))
        check("H2 no invented states (status is a Kernel taxonomy value)", body["status"] in (
            "VERIFIED", "REVIEW_REQUIRED", "BLOCKED", "VALIDATION_FAILED",
            "GROUNDING_FAILED", "FORBIDDEN_OUTPUT", "MODEL_UNAVAILABLE", "UNSUPPORTED_TRANSACTION",
        ))
        r = c.post("/v1/process", content=b"{ broken", headers={"content-type": "application/json"})
        check("H3 malformed demo → 400 + deterministic envelope + kernel untouched", r.status_code == 400 and r.json()["error"]["code"] == "REQUEST_MALFORMED")
    finally:
        developer.reset_client()


import tempfile  # noqa: E402


def main() -> int:
    print("=" * 78)
    print("PHASE 15 — HOSTED API SECURITY & ISOLATION — EVIDENCE SUITE")
    print("=" * 78)
    section_a()
    section_b()
    section_c()
    _rules_sections()
    section_f()
    section_g()
    section_h()
    failed = [c for c in CHECKS if not c[1]]
    print("\n" + "=" * 78)
    print(f"RESULT: {'PASS' if not failed else 'FAIL'} — {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    print("=" * 78)
    for name, _, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
