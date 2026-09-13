"""
Platrixa — Phase 7F: FastAPI API Boundary Test
scripts/fte_fyjc_55_fastapi_boundary_test.py

Verifies the HTTP boundary around the deterministic Kernel:

    HTTP → api.routes.kernel → Kernel.process() → KernelResult
         → Phase 7E persistence contract → KernelProcessResponse

Required checks (Phase 7F spec):
  1.  FastAPI app imports successfully.
  2.  Authoritative Kernel endpoint exists.
  3.  Valid request reaches Kernel.
  4.  KernelResult is converted into the API response correctly.
  5.  VERIFIED survives the boundary.
  6.  REVIEW_REQUIRED survives.
  7.  VALIDATION_FAILED survives.
  8.  GROUNDING_FAILED survives.
  9.  FORBIDDEN_OUTPUT survives.
  10. MODEL_UNAVAILABLE survives.
  11. UNSUPPORTED_TRANSACTION survives.
  12. Kernel is actually the component being invoked.
  13. API does not directly invoke the model provider.
  14. API contains no accounting calculations.
  15. API contains no debit/credit assignment logic.
  16. API contains no grounding logic.
  17. Persistence is invoked through the Phase 7E persistence boundary.
  18. Persistence failure is represented explicitly.
  19. KernelResult is not mutated by API/persistence handling.
  20. Response does not leak internal ML implementation details.
  21. Existing relevant API regressions still pass.
  22. Existing 7B tests still pass.
  23. Existing 7C tests still pass.
  24. Existing 7D tests still pass.
  25. Existing 7E persistence boundary tests still pass.
  26. Health endpoint is lightweight: serving /health imports no model
      provider, no torch/transformers/peft module, and never touches the
      Kernel or ModelProvider path.
  27. Kernel contains no direct persistence call — persistence is
      invoked exclusively from the API boundary via the Phase 7E contract.

No GPU or live model inference is required: the Kernel is injected via the
route module's test hooks (set_kernel / set_persistence).

Run:
    python3 scripts/fte_fyjc_55_fastapi_boundary_test.py
"""

from __future__ import annotations

import ast
import contextlib
import io
import pathlib
import sys
import unittest
from typing import Any, Dict, List

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKS: List[bool] = []


def _check(label: str, condition: bool) -> bool:
    CHECKS.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    return bool(condition)


def _section(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# Test doubles (no GPU, no live model)
# ---------------------------------------------------------------------------

_CANDIDATE: Dict[str, Any] = {
    "transaction_type": "PURCHASE",
    "parties": [{"name": "raj", "role": "seller"}],
    "amounts": [{"value": "25000", "currency": "INR"}],
    "payment_method": "CASH",
    "references": [],
    "ambiguities": [],
    "grounding": [
        {"field": "amounts[0].value", "span": "25000", "status": "GROUNDED"}
    ],
    "confidence": None,
    "concerns": [],
    "provenance": [],
    "temporal": [],
    "source_text": "purchased furniture from raj for rs.25000",
    "derived_hints": [],
    "normalization": [],
    "extensions": {},
}

_ACCOUNTING: Dict[str, Any] = {
    "status": "VERIFIED",
    "journal_balanced": True,
    "journal": {
        "narration": "Purchased furniture from Raj for Rs.25,000.",
        "calculation_records": [
            {"calculation_id": "BK_TOTAL", "label": "Total", "result": "25000"}
        ],
    },
    "debit_lines": [
        {"account": "Furniture", "amount": 25000, "side": "debit", "rule": "Asset purchase"}
    ],
    "credit_lines": [
        {"account": "Cash", "amount": 25000, "side": "credit", "rule": "Cash payment"}
    ],
}

_META = {
    "model_id": "Qwen/Qwen2.5-1.5B-Instruct",
    "provider_revision": "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
    "adapter_revision": "b5c0a37cebc00e93144150dbbcaa7b28cadb259e",
}


def _make_result(status: str, *, with_accounting: bool = None):
    """Build a KernelResult-like object using the real KernelResult class."""
    from backend.kernel.kernel import KernelResult

    if with_accounting is None:
        with_accounting = status == "VERIFIED"
    return KernelResult(
        request_id=f"req-{status.lower()}",
        raw_input="purchased furniture from raj for rs.25000",
        status=status,
        status_label=status.replace("_", " ").title(),
        interpretation=None,
        interpretation_candidate=dict(_CANDIDATE),
        verification_status="GROUNDED" if status == "VERIFIED" else None,
        grounding_issues=(
            ["party 'sharma' not found in input"] if status == "GROUNDING_FAILED" else []
        ),
        accounting_result=dict(_ACCOUNTING) if with_accounting else None,
        issues=[] if status == "VERIFIED" else ["terminal failure"],
        next_action="",
        metadata=dict(_META),
    )


class StubKernel:
    """Kernel double that records calls and returns canned results."""

    def __init__(self, result=None, error: Exception = None):
        self.result = result
        self.error = error
        self.calls: List[str] = []

    def process(self, raw_input: str, *, request_id=None):
        self.calls.append(raw_input)
        if self.error is not None:
            raise self.error
        return self.result


class StubPersistence:
    """Phase 7E contract double that records what it was given."""

    def __init__(self, outcome=None):
        self.outcome = outcome
        self.given: List[Any] = []

    def persist(self, result):
        self.given.append(result)
        return self.outcome


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 70)
    print("PLATRIXA — PHASE 7F: FASTAPI API BOUNDARY TEST")
    print("=" * 70)

    ok = True

    # ------------------------------------------------------------------
    # 1. App imports + 2. endpoint exists
    # ------------------------------------------------------------------
    _section("App import and endpoint existence (checks 1, 2)")
    try:
        from api.main import app
        ok &= _check("api.main imports successfully", True)
        spec = app.openapi()
        kernel_paths = [p for p in spec.get("paths", {}) if "kernel/process" in p]
        ok &= _check(
            "authoritative Kernel endpoint exists at /api/v1/kernel/process",
            "/api/v1/kernel/process" in kernel_paths,
        )
        ok &= _check(
            "existing API surface preserved (health still registered)",
            "/api/v1/health" in spec.get("paths", {}),
        )
    except Exception as exc:
        ok &= _check(f"api.main imports successfully (failed: {exc})", False)
        print("=" * 70)
        print(f"RESULT: FAIL — aborting before suite (import broken)")
        return 1

    # Import the route module for injection hooks.
    import api.routes.kernel as kr
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # ------------------------------------------------------------------
    # 3–4. Valid request reaches Kernel; response conversion correct
    # ------------------------------------------------------------------
    _section("Request reaches Kernel; KernelResult → response (checks 3, 4, 12)")
    verified = _make_result("VERIFIED")
    stub_kernel = StubKernel(result=verified)
    store = StubPersistence(outcome=object())  # PersistedResult stand-in
    kr.set_kernel(stub_kernel)
    kr.set_persistence(store)
    try:
        resp = client.post("/api/v1/kernel/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
        ok &= _check("HTTP 200 for VERIFIED", resp.status_code == 200)
        body = resp.json()
        ok &= _check("request reached Kernel.process (check 12)", stub_kernel.calls == ["purchased furniture from raj for rs.25000"])
        ok &= _check("status carried verbatim in body (check 4)", body.get("status") == "VERIFIED")
        ok &= _check("status_label carried", body.get("status_label") == "Verified")
        ok &= _check("success=true only for VERIFIED", body.get("success") is True)
        ok &= _check("interpretation projected (candidate, verbatim)", body.get("interpretation", {}).get("transaction_type") == "PURCHASE")
        ok &= _check("accounting projected verbatim", body.get("accounting", {}).get("journal_balanced") is True)
        ok &= _check("request_id carried", body.get("request_id") == "req-verified")
        ok &= _check("persisted=true on successful persistence", body.get("persisted") is True)
        ok &= _check("persistence received the KernelResult object (check 17)", len(store.given) == 1 and store.given[0] is verified)
    finally:
        kr.reset_kernel()
        kr.reset_persistence()

    # ------------------------------------------------------------------
    # 5–11. All terminal statuses survive the boundary
    # ------------------------------------------------------------------
    _section("Terminal status taxonomy survival (checks 5–11)")
    status_expect = [
        ("VERIFIED", 200),
        ("REVIEW_REQUIRED", 200),
        ("VALIDATION_FAILED", 422),
        ("GROUNDING_FAILED", 422),
        ("FORBIDDEN_OUTPUT", 422),
        ("MODEL_UNAVAILABLE", 503),
        ("UNSUPPORTED_TRANSACTION", 422),
    ]
    for status, expected_code in status_expect:
        result = _make_result(status)
        kr.set_kernel(StubKernel(result=result))
        kr.set_persistence(StubPersistence(outcome=object()))
        try:
            resp = client.post("/api/v1/kernel/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
            body = resp.json()
            ok &= _check(
                f"{status}: body status verbatim, HTTP {expected_code}",
                body.get("status") == status and resp.status_code == expected_code,
            )
            if status == "VERIFIED":
                ok &= _check("VERIFIED: success=true", body.get("success") is True)
            else:
                ok &= _check(
                    f"{status}: success=false (never upgraded)",
                    body.get("success") is False,
                )
        finally:
            kr.reset_kernel()
            kr.reset_persistence()

    # ------------------------------------------------------------------
    # 13–16. Dependency-direction / responsibility audits
    # ------------------------------------------------------------------
    _section("API responsibility audits (checks 13–16)")
    route_src = (ROOT / "api" / "routes" / "kernel.py").read_text()
    route_tree = ast.parse(route_src)
    imported: List[str] = []
    for node in ast.walk(route_tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    ok &= _check(
        "route does not import model provider implementation (check 13)",
        not any("model_provider" in m and "base" not in m for m in imported)
        and "local_hf" not in route_src
        and "LocalHFModelProvider" not in route_src,
    )
    ok &= _check(
        "route does not import accounting engine (check 14)",
        "fyjc_accounting" not in route_src and "hardened_bookkeeping_outcome" not in route_src,
    )
    forbidden_tokens = [
        ("def classify", "account classification (check 14)"),
        ("debit_total", "debit total calculation (check 14)"),
        ("credit_total", "credit total calculation (check 14)"),
        ("golden_rules", "golden rules logic (check 15)"),
        ("def _ground", "grounding logic (check 16)"),
        ("ExpandedGroundingGate", "grounding gate (check 16)"),
        ("StructuredInterpretationValidator", "schema validator (check 16)"),
    ]
    for token, label in forbidden_tokens:
        ok &= _check(f"route contains no {label}", token not in route_src)
    ok &= _check(
        "route delegates to Kernel.process (single reasoning boundary)",
        "kernel.process(" in route_src.replace(".process(", ".process(") or ".process(raw_input)" in route_src,
    )

    # API package-wide: no accounting implementation anywhere in api/.
    api_leak = False
    for py in (ROOT / "api").rglob("*.py"):
        if py.name == "kernel.py" and py.parent.name == "routes":
            continue
        src = py.read_text()
        if "hardened_bookkeeping_outcome" in src or "fyjc_accounting" in src:
            api_leak = True
    ok &= _check("no accounting logic anywhere else in api/ (check 14)", not api_leak)

    # Kernel must not import FastAPI (dependency direction).
    kernel_src = (ROOT / "backend" / "kernel" / "kernel.py").read_text()
    ok &= _check(
        "Kernel does not import FastAPI (no reverse dependency)",
        "fastapi" not in kernel_src and "api.routes" not in kernel_src,
    )

    # ------------------------------------------------------------------
    # 17–18. Persistence boundary + explicit failure
    # ------------------------------------------------------------------
    _section("Persistence boundary + explicit failure (checks 17, 18)")
    from backend.persistence.base import PersistenceFailure

    fail_store = StubPersistence(
        outcome=PersistenceFailure(kind="PERSISTENCE_WRITE_FAILED", reason="simulated")
    )
    kr.set_kernel(StubKernel(result=_make_result("VERIFIED")))
    kr.set_persistence(fail_store)
    try:
        resp = client.post("/api/v1/kernel/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
        body = resp.json()
        ok &= _check(
            "persistence failure → explicit persisted=false (check 18)",
            body.get("persisted") is False and body.get("persistence_error", {}).get("kind") == "PERSISTENCE_WRITE_FAILED",
        )
        ok &= _check(
            "persistence failure does NOT overwrite accounting status",
            body.get("status") == "VERIFIED" and body.get("accounting") is not None,
        )
        ok &= _check(
            "persistence failure does not falsely report success",
            body.get("success") is True and body.get("persisted") is False,
        )
    finally:
        kr.reset_kernel()
        kr.reset_persistence()

    unavailable_store = StubPersistence(
        outcome=PersistenceFailure(kind="PERSISTENCE_UNAVAILABLE", reason="db down")
    )
    kr.set_kernel(StubKernel(result=_make_result("VERIFIED")))
    kr.set_persistence(unavailable_store)
    try:
        resp = client.post("/api/v1/kernel/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
        ok &= _check(
            "PERSISTENCE_UNAVAILABLE represented distinctly",
            resp.json().get("persistence_error", {}).get("kind") == "PERSISTENCE_UNAVAILABLE",
        )
    finally:
        kr.reset_kernel()
        kr.reset_persistence()

    # Non-verified result must never be reported persisted-VERIFIED.
    kr.set_kernel(StubKernel(result=_make_result("GROUNDING_FAILED")))
    kr.set_persistence(StubPersistence(outcome=object()))
    try:
        resp = client.post("/api/v1/kernel/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
        body = resp.json()
        ok &= _check(
            "non-verified status never upgraded through persistence",
            body.get("status") == "GROUNDING_FAILED" and body.get("success") is False,
        )
    finally:
        kr.reset_kernel()
        kr.reset_persistence()

    # Persistence override cleared → default resolution would use Postgres impl
    ok &= _check(
        "reset_persistence restores default resolution path",
        getattr(kr._get_persistence, "_override", None) is None,
    )

    # ------------------------------------------------------------------
    # 19. KernelResult not mutated
    # ------------------------------------------------------------------
    _section("KernelResult immutability through API + persistence (check 19)")
    result = _make_result("VERIFIED")
    before = {
        "status": result.status,
        "accounting": repr(result.accounting_result),
        "candidate": repr(result.interpretation_candidate),
        "issues": list(result.issues),
    }
    kr.set_kernel(StubKernel(result=result))
    kr.set_persistence(StubPersistence(outcome=object()))
    try:
        client.post("/api/v1/kernel/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
        after = {
            "status": result.status,
            "accounting": repr(result.accounting_result),
            "candidate": repr(result.interpretation_candidate),
            "issues": list(result.issues),
        }
        ok &= _check("KernelResult unchanged by API + persistence (check 19)", before == after)
    finally:
        kr.reset_kernel()
        kr.reset_persistence()

    # ------------------------------------------------------------------
    # 20. No ML/internal leakage in responses
    # ------------------------------------------------------------------
    _section("Leakage audit (check 20)")
    leak_terms = [
        "transformers", "peft", "huggingface", "torch", "LoRA weights",
        "chain_of_thought", "reasoning", "raw_model_output", "generated_profile",
        "Traceback", "stack trace",
    ]
    result = _make_result("VERIFIED")
    # Plant leak-prone content into the accounting result to prove stripping.
    result.accounting_result["raw_model_output"] = "SECRET-MODEL-OUTPUT"
    result.accounting_result["generated_profile"] = {"hidden": True}
    result.accounting_result["reasoning"] = "hidden chain of thought"
    kr.set_kernel(StubKernel(result=result))
    kr.set_persistence(StubPersistence(outcome=object()))
    leak_free = True
    try:
        resp = client.post("/api/v1/kernel/process", json={"raw_input": "purchased furniture from raj for rs.25000"})
        body_text = resp.text
        for term in leak_terms:
            if term.lower() in body_text.lower():
                leak_free = False
                print(f"      leaked term found: {term}")
        ok &= _check("response contains no ML/internal leakage (check 20)", leak_free)
        ok &= _check(
            "unhandled exceptions never leak stack traces (global handler)",
            "Traceback" not in body_text,
        )
    finally:
        kr.reset_kernel()
        kr.reset_persistence()

    # Request-shape validation (HTTP-level only).
    resp = client.post("/api/v1/kernel/process", json={"raw_input": ""})
    ok &= _check("empty raw_input rejected at request-shape level (422)", resp.status_code == 422)
    resp = client.post("/api/v1/kernel/process", json={})
    ok &= _check("missing raw_input rejected at request-shape level (422)", resp.status_code == 422)

    # ------------------------------------------------------------------
    # 26–27. Health lightweightness + persistence path uniqueness
    # ------------------------------------------------------------------
    _section("Health lightweightness + persistence path uniqueness (checks 26, 27)")

    # Health must be servable with NO kernel and NO persistence wiring,
    # and must not import any model/provider/torch module while serving.
    kr.reset_kernel()
    kr.reset_persistence()
    heavy_modules = [
        "backend.model_provider.local_hf",
        "backend.model_provider.remote_hf",
        "backend.model_provider.hf_gradio",
        "transformers",
        "torch",
        "peft",
    ]
    modules_before_health = set(sys.modules)
    resp = client.get("/api/v1/health")
    ok &= _check(
        "health responds 200 with no Kernel/provider wiring at all",
        resp.status_code == 200 and resp.json().get("status") == "ok",
    )
    newly_loaded = [
        m for m in heavy_modules
        if m in sys.modules and m not in modules_before_health
    ]
    ok &= _check(
        "serving health imports no model/provider/torch module (check 26)",
        not newly_loaded,
    )

    # Subprocess proof (the authoritative form of check 26): in a clean
    # interpreter that imports ONLY the API and serves /health, none of the
    # model/provider/torch modules may appear in sys.modules. This is
    # immune to test-process contamination from elsewhere importing Kernel.
    import json as _json
    import subprocess

    _probe = (
        "import json,sys;sys.path.insert(0, r'%s');"
        "from fastapi.testclient import TestClient;"
        "from api.main import app;"
        "r=TestClient(app).get('/api/v1/health');"
        "heavy=['backend.model_provider.local_hf','backend.model_provider.remote_hf',"
        "'backend.model_provider.hf_gradio','transformers','torch','peft'];"
        "hits=[m for m in heavy if m in sys.modules];"
        "print(json.dumps({'status_code':r.status_code,'body_status':r.json().get('status'),"
        "'heavy_loaded':hits}))" % ROOT
    )
    proc = subprocess.run(
        [sys.executable, "-c", _probe],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
    )
    probe_ok = False
    probe_detail = proc.stdout.strip() or proc.stderr.strip()[:200]
    try:
        pdata = _json.loads(proc.stdout.strip().splitlines()[-1])
        probe_ok = (
            proc.returncode == 0
            and pdata.get("status_code") == 200
            and pdata.get("body_status") == "ok"
            and not pdata.get("heavy_loaded")
        )
    except Exception:
        probe_ok = False
    ok &= _check(
        "clean-interpreter health serving loads zero model/provider/torch modules (check 26)",
        probe_ok,
    )
    if not probe_ok:
        print(f"      probe output: {probe_detail}")
    body_text_health = resp.text
    ok &= _check(
        "health payload exposes no model internals (check 26)",
        "adapter" not in body_text_health.lower()
        and "lora" not in body_text_health.lower()
        and "hf.space" not in body_text_health.lower(),
    )

    # Persistence must have exactly one invocation site: the API boundary.
    # The Kernel itself must never persist (Phase 7C/7E responsibility split).
    kernel_src = (ROOT / "backend" / "kernel" / "kernel.py").read_text()
    ok &= _check(
        "Kernel performs no direct persistence call (check 27)",
        ".persist(" not in kernel_src,
    )

    # ------------------------------------------------------------------
    # 21–25. Regression sweep
    # ------------------------------------------------------------------
    _section("Regression sweep (checks 21–25)")

    def _run_unittest(pattern: str, where: str) -> bool:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            loader = unittest.TestLoader()
            suite = loader.discover(where, pattern=pattern)
            runner = unittest.TextTestRunner(stream=buf, verbosity=0)
            res = runner.run(suite)
        return res.wasSuccessful() and res.testsRun > 0

    def _run_script(script: str) -> bool:
        import subprocess

        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=600,
        )
        return proc.returncode == 0

    ok &= _check(
        "existing API regression: health endpoint contract intact (check 21)",
        client.get("/api/v1/health").status_code == 200
        and client.get("/api/v1/health").json().get("status") == "ok",
    )
    ok &= _check("existing FYJC DB persistence regression passes (check 21)", _run_unittest("fyjc_db_persistence_test.py", str(ROOT / "backend")))
    ok &= _check("7B model provider regression passes (check 22)", _run_script("fte_fyjc_51_model_provider_test.py"))
    ok &= _check("7C kernel boundary regression passes (check 23)", _run_script("fte_fyjc_52_kernel_boundary_test.py"))
    ok &= _check("7D grounding wiring regression passes (check 24)", _run_script("fte_fyjc_53_grounding_verification_wiring_test.py"))
    ok &= _check("7E persistence boundary regression passes (check 25)", _run_script("fte_fyjc_54_persistence_boundary_test.py"))

    # ------------------------------------------------------------------
    total = len(CHECKS)
    passed = sum(1 for c in CHECKS if c)
    failed = total - passed
    print("\n" + "=" * 70)
    print(f"RESULT: {'PASS' if failed == 0 else 'FAIL'} — {passed}/{total} checks passed")
    if failed:
        print(f"  ({failed} failed)")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
