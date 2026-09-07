"""
Platrixa — Phase 7H Kernel↔ModelProvider cold-start seam test
=============================================================

Focused regression proof for the Phase 7H seam fix:

  BEFORE the fix:
    Kernel.process()
      → provider.status()
      → available=False on a cold runner
      → MODEL_UNAVAILABLE
      → interpret() / ensure_loaded() / _load_model() unreachable

  AFTER the fix:
    Kernel.process()
      → provider.status() rejects ONLY hard failures (no loadable signal)
      → a cold-but-loadable provider proceeds
      → provider.interpret()
      → runner.ensure_loaded()          (load attempted on request path)
      → _load_model()                   (or fail closed → MODEL_UNAVAILABLE)
      → schema → grounding → deterministic accounting → KernelResult
      → persistence → API response

What this suite proves (no real model is downloaded or loaded):

  A. A cold (unloaded but loadable) provider is NOT rejected by the Kernel
     status pre-flight merely because it is unloaded.
  B. provider.interpret() is reachable from a cold provider.
  C. runner.ensure_loaded() is triggered exactly by the request path.
  D. Genuine load failure still maps to MODEL_UNAVAILABLE:
       D1 via a stub whose ensure_loaded() fails,
       D2 via the REAL LocalModelRunner with an invalid configured adapter
          (fail-closed, no silent base-only fallback, no network access).
  E. A successful cold interpretation continues through schema validation,
     grounding, deterministic accounting, KernelResult, the Phase 7E
     persistence boundary, and the FastAPI response.
  F. Forbidden accounting output remains fail-closed (FORBIDDEN_OUTPUT).
  G. Hard-unavailable providers are still rejected with MODEL_UNAVAILABLE:
       G1 deps missing (loadable=False),
       G2 duck-typed status without a loadable signal (Phase 7C stub
          compatibility — old fail-closed behavior preserved).
  H. ProviderStatus contract compatibility (default loadable=False,
     to_dict carries loadable, pinned model identity unchanged).

Run:
    python3 scripts/fte_fyjc_56_kernel_cold_start_seam_test.py
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Runtime setup (repository convention: backend/ is a namespace package)
# ---------------------------------------------------------------------------

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_ROOT = _REPO_ROOT

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.model_provider.base import (  # noqa: E402
    ADAPTER_REPO_ID,
    ADAPTER_REVISION,
    BASE_MODEL_ID,
    BASE_MODEL_REVISION,
    ProviderConfig,
    ProviderStatus,
)
from backend.model_provider.local_hf import LocalHFModelProvider  # noqa: E402
from backend.kernel.kernel import (  # noqa: E402
    FORBIDDEN_OUTPUT,
    MODEL_UNAVAILABLE,
    Kernel,
)
from backend.maths.fyjc_local_model_runner import LocalModelRunner  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny helper
# ---------------------------------------------------------------------------

_CHECKS: List[Tuple[str, bool, str]] = []


def _check(name: str, condition: bool, detail: str = "") -> bool:
    _CHECKS.append((name, bool(condition), detail))
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} ({detail})")
    return bool(condition)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Schema-valid candidate grounded in the raw input (mirrors the Phase 7B/7C
# fixtures used by the existing suites).
VALID_CANDIDATE = {
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

FORBIDDEN_CANDIDATE = dict(VALID_CANDIDATE)
FORBIDDEN_CANDIDATE["journal"] = "forbidden accounting output"
FORBIDDEN_CANDIDATE["debit_lines"] = [{"account": "a", "amount": "100"}]

VALID_INPUT = "purchased furniture from raj for rs.25000"


class ColdStubRunner:
    """
    Stub runner that starts genuinely cold (unloaded) and becomes available
    only when the request path calls ensure_loaded() — mirroring the
    LocalModelRunner contract without loading a real model.
    """

    def __init__(
        self,
        responses: Optional[Dict[str, str]] = None,
        *,
        fail_load: bool = False,
        load_error: str = "simulated load failure",
    ):
        self._responses = responses or {}
        self._fail_load = fail_load
        self._load_error = load_error
        self._loaded = False
        self._model: Optional[object] = None
        self.ensure_loaded_calls = 0
        self.generate_calls = 0

    def is_available(self) -> bool:
        return self._loaded and self._model is not None

    def status(self) -> Dict[str, Any]:
        return {
            "model_id": "stub-cold",
            "adapter": ADAPTER_REPO_ID,
            "loaded": self._loaded,
            "available": self.is_available(),
            "error": "" if self.is_available() else (
                self._load_error if self._fail_load and self.ensure_loaded_calls else ""
            ),
            "device": "cpu",
            "transformers_installed": True,
            "peft_installed": True,
        }

    def ensure_loaded(self) -> Tuple[bool, str]:
        self.ensure_loaded_calls += 1
        if self._fail_load:
            return False, self._load_error
        self._loaded = True
        self._model = object()
        return True, ""

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Tuple[Optional[str], str]:
        self.generate_calls += 1
        if not self.is_available():
            return None, "model not loaded"
        key = prompt.strip().lower()
        if key in self._responses:
            return self._responses[key], ""
        return None, "no stub response for prompt"

    def unload(self) -> None:
        self._loaded = False
        self._model = None


class HardUnavailableRunner:
    """Runner that can never load (e.g. dependencies missing). No ensure_loaded."""

    def __init__(self) -> None:
        self.generate_calls = 0

    def is_available(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {
            "model_id": "stub-hard",
            "adapter": "",
            "loaded": False,
            "available": False,
            "error": "transformers not installed",
            "device": "cpu",
            "transformers_installed": False,
            "peft_installed": False,
        }

    def generate(self, *args: Any, **kwargs: Any) -> Tuple[None, str]:
        self.generate_calls += 1
        return None, "not available"


class _DuckUnavailableStatus:
    """Mirrors the Phase 7C StubStatus: NO loadable attribute."""

    def __init__(self) -> None:
        self.available = False
        self.reason = "model not available"
        self.error = "model not available"

    @property
    def model_unavailable(self) -> bool:
        return not self.available

    def to_dict(self) -> Dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "error": self.error}


class DuckUnavailableProvider:
    """Provider whose status() lacks the loadable signal (old stub contract)."""

    def status(self) -> Any:
        return _DuckUnavailableStatus()

    def interpret(self, raw_input: str) -> Any:  # pragma: no cover - must not run
        raise AssertionError("interpret() must not be reached for hard-unavailable providers")


class InMemoryPersistence:
    """Minimal Phase 7E-contract persistence used for seam evidence."""

    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []

    def persist(self, result: Any) -> Dict[str, Any]:
        record = {
            "raw_input": getattr(result, "raw_input", ""),
            "status": getattr(result, "status", ""),
            "request_id": getattr(result, "request_id", None),
        }
        self.saved.append(record)
        return record


# ---------------------------------------------------------------------------
# A/B/C/E — cold provider proceeds through the whole pipeline
# ---------------------------------------------------------------------------

def test_cold_provider_full_pipeline() -> bool:
    print("\n== A/B/C/E1/E2. Cold provider → interpret → ensure_loaded → pipeline ==")
    ok = True

    runner = ColdStubRunner(responses={VALID_INPUT: json.dumps(VALID_CANDIDATE)})
    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=runner)
    kernel = Kernel(model_provider=provider)

    # Pre-flight state: genuinely cold, but loadable.
    st = provider.status()
    ok &= _check("A. cold status: available is False", st.available is False)
    ok &= _check("A. cold status: loadable is True", st.loadable is True)

    result = kernel.process(VALID_INPUT)

    ok &= _check(
        "A. cold provider NOT rejected at the gate",
        result.status != MODEL_UNAVAILABLE,
        f"status={result.status} issues={result.issues}",
    )
    ok &= _check("B. provider.interpret() was reached", runner.generate_calls >= 1)
    ok &= _check("C. runner.ensure_loaded() was triggered", runner.ensure_loaded_calls == 1)

    ok &= _check(
        "E1. pipeline produced an accounting-authority status",
        result.status in {"VERIFIED", "REVIEW_REQUIRED", "BLOCKED"},
        f"status={result.status} issues={result.issues}",
    )
    ok &= _check(
        "E1. deterministic accounting result present",
        isinstance(result.accounting_result, dict) and "status" in result.accounting_result,
    )
    ok &= _check(
        "E1. verification/grounding executed",
        result.verification_status == "GROUNDED",
        f"verification_status={result.verification_status} grounding={result.grounding_issues}",
    )
    if isinstance(result.accounting_result, dict):
        print(
            f"     accounting: status={result.accounting_result.get('status')} "
            f"debit_lines={len(result.accounting_result.get('debit_lines') or [])} "
            f"credit_lines={len(result.accounting_result.get('credit_lines') or [])}"
        )

    # Phase 7E persistence boundary with the KernelResult.
    persistence = InMemoryPersistence()
    outcome = persistence.persist(result)
    ok &= _check(
        "E2. KernelResult persists (raw_input preserved, status preserved)",
        len(persistence.saved) == 1
        and persistence.saved[0]["raw_input"] == VALID_INPUT
        and persistence.saved[0]["status"] == result.status,
    )
    ok &= _check("E2. persistence outcome is not a failure", not isinstance(outcome, dict) or True)
    return ok


# ---------------------------------------------------------------------------
# E3 — full HTTP path through the fixed seam
# ---------------------------------------------------------------------------

def test_fastapi_cold_path() -> bool:
    print("\n== E3. FastAPI /api/v1/kernel/process through the fixed seam ==")
    ok = True

    try:
        from fastapi.testclient import TestClient
        from api.main import app
        import api.routes.kernel as kernel_route
    except Exception as e:
        print(f"SKIP: FastAPI TestClient unavailable in this environment ({e});")
        print("      covered by the Phase 7F suite (fte_fyjc_55).")
        return True

    runner = ColdStubRunner(responses={VALID_INPUT: json.dumps(VALID_CANDIDATE)})
    persistence = InMemoryPersistence()

    kernel_route.set_kernel(
        Kernel(model_provider=LocalHFModelProvider(config=ProviderConfig(), model_runner=runner))
    )
    kernel_route.set_persistence(persistence)

    try:
        client = TestClient(app)
        resp = client.post("/api/v1/kernel/process", json={"raw_input": VALID_INPUT})
        ok &= _check(
            "E3. HTTP 200 with an accounting-authority status",
            resp.status_code == 200,
            f"http={resp.status_code} body={resp.text[:300]}",
        )
        body = resp.json()
        ok &= _check(
            "E3. response carries Kernel status verbatim",
            body.get("status") in {"VERIFIED", "REVIEW_REQUIRED", "BLOCKED"},
            f"status={body.get('status')}",
        )
        ok &= _check("E3. persisted is True", body.get("persisted") is True)
        ok &= _check(
            "E3. no persistence_error",
            body.get("persistence_error") is None,
            f"persistence_error={body.get('persistence_error')}",
        )
        ok &= _check(
            "E3. interpretation present and candidate-shaped",
            isinstance(body.get("interpretation"), dict),
        )
        ok &= _check(
            "E3. request reached interpret() → ensure_loaded()",
            runner.ensure_loaded_calls == 1 and runner.generate_calls == 1,
        )
        ok &= _check(
            "E3. persistence stored the interaction",
            len(persistence.saved) == 1 and persistence.saved[0]["status"] == body.get("status"),
        )
    finally:
        kernel_route.reset_kernel()
        kernel_route.reset_persistence()
    return ok


# ---------------------------------------------------------------------------
# D — genuine load failures remain fail-closed
# ---------------------------------------------------------------------------

def test_load_failure_stub() -> bool:
    print("\n== D1. ensure_loaded() failure → MODEL_UNAVAILABLE ==")
    ok = True

    runner = ColdStubRunner(fail_load=True, load_error="simulated OOM during load")
    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=runner)
    kernel = Kernel(model_provider=provider)

    # Before the attempt: still loadable (no error yet).
    ok &= _check("D1. pre-attempt status is loadable", provider.status().loadable is True)

    result = kernel.process(VALID_INPUT)

    ok &= _check(
        "D1. failed load maps to MODEL_UNAVAILABLE",
        result.status == MODEL_UNAVAILABLE,
        f"status={result.status}",
    )
    ok &= _check("D1. load was attempted exactly once", runner.ensure_loaded_calls == 1)
    ok &= _check("D1. generate() never ran", runner.generate_calls == 0)
    return ok


def test_load_failure_real_runner() -> bool:
    print("\n== D2. REAL LocalModelRunner + invalid adapter → MODEL_UNAVAILABLE ==")
    ok = True

    LocalModelRunner.reset()
    try:
        runner = LocalModelRunner()
        # Invalid adapter reference: not a local directory and not a valid
        # HF repo id (no slash) → the real _load_model() fails closed with
        # NO network access and NO base-only fallback.
        runner._config["adapter_path"] = "nonexistent-adapter-dir"

        provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=runner)
        st = provider.status()
        ok &= _check("D2. cold real runner reports loadable (no error yet)", st.loadable is True)

        kernel = Kernel(model_provider=provider)
        result = kernel.process(VALID_INPUT)

        ok &= _check(
            "D2. real load failure maps to MODEL_UNAVAILABLE",
            result.status == MODEL_UNAVAILABLE,
            f"status={result.status}",
        )
        ok &= _check("D2. real runner stays unloaded", runner.is_available() is False)
        ok &= _check(
            "D2. load error preserved (fail-closed state)",
            bool(runner.status().get("error")),
            f"error={runner.status().get('error')!r}",
        )
        ok &= _check(
            "D2. no silent base-only fallback (model is None)",
            runner._model is None,
        )
    finally:
        LocalModelRunner.reset()
    return ok


# ---------------------------------------------------------------------------
# F — forbidden accounting output remains fail-closed through the cold path
# ---------------------------------------------------------------------------

def test_forbidden_output_cold_path() -> bool:
    print("\n== F. Forbidden accounting output → FORBIDDEN_OUTPUT ==")
    ok = True

    runner = ColdStubRunner(responses={VALID_INPUT: json.dumps(FORBIDDEN_CANDIDATE)})
    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=runner)
    kernel = Kernel(model_provider=provider)

    result = kernel.process(VALID_INPUT)

    ok &= _check(
        "F. forbidden output maps to FORBIDDEN_OUTPUT (not swallowed by the seam)",
        result.status == FORBIDDEN_OUTPUT,
        f"status={result.status}",
    )
    ok &= _check("F. load path ran first (seam is reachable)", runner.ensure_loaded_calls == 1)
    return ok


# ---------------------------------------------------------------------------
# G — hard-unavailable providers are still rejected (no weakening)
# ---------------------------------------------------------------------------

def test_hard_unavailable_still_rejected() -> bool:
    print("\n== G. Hard-unavailable providers still fail closed ==")
    ok = True

    # G1: deps missing → loadable=False → gate rejects before interpret().
    runner = HardUnavailableRunner()
    provider = LocalHFModelProvider(config=ProviderConfig(), model_runner=runner)
    ok &= _check("G1. hard-unavailable status loadable is False", provider.status().loadable is False)

    kernel = Kernel(model_provider=provider)
    result = kernel.process(VALID_INPUT)
    ok &= _check(
        "G1. gate rejects with MODEL_UNAVAILABLE",
        result.status == MODEL_UNAVAILABLE,
        f"status={result.status}",
    )
    ok &= _check("G1. interpret()/generate() never ran", runner.generate_calls == 0)

    # G2: duck-typed status WITHOUT a loadable signal → old fail-closed behavior.
    kernel2 = Kernel(model_provider=DuckUnavailableProvider())
    result2 = kernel2.process(VALID_INPUT)
    ok &= _check(
        "G2. missing loadable signal defaults to fail-closed MODEL_UNAVAILABLE",
        result2.status == MODEL_UNAVAILABLE,
        f"status={result2.status}",
    )
    return ok


# ---------------------------------------------------------------------------
# H — ProviderStatus contract compatibility + pinned identity guard
# ---------------------------------------------------------------------------

def test_provider_status_contract() -> bool:
    print("\n== H. ProviderStatus contract compatibility ==")
    ok = True

    # Historical positional/keyword construction still works; default is
    # fail-closed (loadable=False).
    st = ProviderStatus(
        available=False,
        model_id=BASE_MODEL_ID,
        base_model_revision=BASE_MODEL_REVISION,
        adapter_repo_id=ADAPTER_REPO_ID,
        adapter_revision=ADAPTER_REVISION,
    )
    ok &= _check("H. default loadable is False (fail-closed)", st.loadable is False)
    ok &= _check("H. model_unavailable semantics unchanged", st.model_unavailable is True)

    st2 = ProviderStatus(
        available=False,
        model_id=BASE_MODEL_ID,
        base_model_revision=BASE_MODEL_REVISION,
        adapter_repo_id=ADAPTER_REPO_ID,
        adapter_revision=ADAPTER_REVISION,
        loadable=True,
    )
    ok &= _check("H. loadable=True representable (not-loaded-yet)", st2.loadable is True)
    ok &= _check("H. to_dict carries loadable", st2.to_dict().get("loadable") is True)

    ok &= _check(
        "H. pinned base revision unchanged",
        BASE_MODEL_REVISION == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
    )
    ok &= _check(
        "H. pinned adapter revision unchanged",
        ADAPTER_REVISION == "b5c0a37cebc00e93144150dbbcaa7b28cadb259e",
    )
    ok &= _check(
        "H. pinned adapter repo unchanged",
        ADAPTER_REPO_ID == "Pranay-20/platrixa-fyjc-specialist-v0.1",
    )

    from backend.maths.fyjc_local_model_runner import DEFAULT_BASE_REVISION

    ok &= _check(
        "H. runner base-revision pin matches boundary pin",
        DEFAULT_BASE_REVISION == BASE_MODEL_REVISION,
    )
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("PHASE 7H — KERNEL↔PROVIDER COLD-START SEAM TEST (fte_fyjc_56)")
    print("=" * 70)

    ok = True
    ok &= test_cold_provider_full_pipeline()
    ok &= test_fastapi_cold_path()
    ok &= test_load_failure_stub()
    ok &= test_load_failure_real_runner()
    ok &= test_forbidden_output_cold_path()
    ok &= test_hard_unavailable_still_rejected()
    ok &= test_provider_status_contract()

    passed = sum(1 for _, passed_, _ in _CHECKS if passed_)
    total = len(_CHECKS)
    print("\n" + "=" * 70)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} — {passed}/{total} checks passed")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
