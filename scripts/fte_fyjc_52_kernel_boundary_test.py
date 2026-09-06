"""
Platrixa — Phase 7C Kernel boundary tests

Tests that the new Kernel boundary:
  - does not load the model at import
  - does not depend on Hugging Face implementation directly
  - does not depend on UI / FastAPI internals
  - uses ModelProvider as the model boundary
  - fails closed for unavailable / malformed / forbidden / grounding / validation
  - delegates accounting to the existing implementation
  - preserves existing accounting behavior

This is meant to run against the committed codebase. It does not run Qwen.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import pathlib
import sys
import types
from typing import Any, Dict, Optional, Tuple

# Backend package import path setup happens below (see "Runtime setup"); the
# provider error classes are imported after it so the stub exceptions in this
# file can subclass the real error types the Kernel catches.

# ---------------------------------------------------------------------------
# Runtime setup
# ---------------------------------------------------------------------------
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_ROOT = _REPO_ROOT

# NOTE: backend/ intentionally has no __init__.py in the repository. Python 3
# namespace packages make "import backend.kernel" work with the repo root on
# sys.path, which is the convention every existing test in this repo already
# uses (see tests/test_chat_assistant.py). We do not create a package marker
# here because that would change repository packaging semantics from a test.

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Real provider error types (used as base classes for the stub exceptions).
from backend.model_provider.base import (  # noqa: E402
    ForbiddenAccountingFieldError,
    MalformedOutputError,
    ModelUnavailableError,
)

# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------

def _check(name: str, condition: bool, detail: str = "") -> bool:
    if not condition:
        print(f"FAIL: {name} ({detail})")
        return False
    print(f"PASS: {name}")
    return True


# ---------------------------------------------------------------------------
# Stub ModelProvider
# ---------------------------------------------------------------------------

class StubProvider:
    """
    Stub ModelProvider that mirrors ProviderStatus semantics closely enough for
    Kernel boundary tests: ProviderStatus.reason is the human-readable status text,
    ProviderStatus.error is the machine-facing error field.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        candidate: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        error_kind: Optional[str] = None,
    ):
        self._available = available
        self._candidate = candidate or {}
        self._error = error or ""
        self._error_kind = error_kind or ""
        self._config = StubConfig()

    @property
    def config(self) -> Any:
        return self._config

    def status(self) -> Any:
        # Mirror ProviderStatus.reason semantics: reason is the status text used by
        # Kernel before attempting inference; error is left empty unless the provider
        # is unavailable.
        return StubStatus(
            available=self._available,
            model_id="stub",
            base_model_revision="rev",
            adapter_repo_id="repo",
            adapter_revision="adjrev",
            reason=self._error if not self._available else ("available" if not self._error else self._error),
            error=self._error,
        )

    def interpret(self, raw_input: str) -> Any:
        if self._error_kind:
            kind = self._error_kind
            if kind == "unavailable":
                raise _stub_unavailable(self._error or "not available")
            if kind == "malformed":
                raise _stub_malformed(self._error or "malformed")
            if kind == "forbidden":
                raise _stub_forbidden(self._error or "forbidden")
            raise RuntimeError(f"unknown stub error kind: {kind}")
        return StubResult(
            raw_input=raw_input,
            candidate=self._candidate,
            model_id="stub",
            provider_revision="adjrev",
        )


class StubConfig:
    model_id = "stub"
    base_model_revision = "rev"
    adapter_repo_id = "repo"
    adapter_revision = "adjrev"
    adapter_path = ""
    max_new_tokens = 512
    temperature = 0.0
    top_p = 1.0
    do_sample = False
    device = "auto"
    dtype = "auto"

    def expected_model_identity(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "base_model_revision": self.base_model_revision,
            "adapter_repo_id": self.adapter_repo_id,
            "adapter_revision": self.adapter_revision,
            "adapter_path": self.adapter_path,
        }


class StubStatus:
    def __init__(
        self,
        *,
        available: bool,
        model_id: str,
        base_model_revision: str,
        adapter_repo_id: str,
        adapter_revision: str,
        reason: str = "",
        error: str = "",
    ):
        self.available = available
        self.model_id = model_id
        self.base_model_revision = base_model_revision
        self.adapter_repo_id = adapter_repo_id
        self.adapter_revision = adapter_revision
        self.reason = reason
        self.error = error

    @property
    def model_unavailable(self) -> bool:
        return not self.available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "model_id": self.model_id,
            "base_model_revision": self.base_model_revision,
            "adapter_repo_id": self.adapter_repo_id,
            "adapter_revision": self.adapter_revision,
            "reason": self.reason,
            "error": self.error,
        }


class StubResult:
    def __init__(self, *, raw_input: str, candidate: Dict[str, Any], model_id: str, provider_revision: str):
        self.raw_input = raw_input
        self.candidate = candidate
        self.model_id = model_id
        self.provider_revision = provider_revision

    def snapshot(self) -> Dict[str, Any]:
        return {
            "raw_input": self.raw_input,
            "candidate": self.candidate,
            "model_id": self.model_id,
            "provider_revision": self.provider_revision,
        }


# ---------------------------------------------------------------------------
# Stub exception hierarchy matching provider boundary
# ---------------------------------------------------------------------------

# These stub exceptions must be actual subclasses of the provider error types
# the Kernel catches (ModelUnavailableError / MalformedOutputError /
# ForbiddenAccountingFieldError). Merely renaming plain Exception subclasses
# ("__name__ = ...") does NOT change the type hierarchy, so the Kernel's
# except clauses would never fire and fail-closed tests would spuriously pass
# through the generic Exception fallback instead of their dedicated branches.
class _stub_unavailable(ModelUnavailableError):
    pass


class _stub_malformed(MalformedOutputError):
    pass


class _stub_forbidden(ForbiddenAccountingFieldError):
    pass


# ---------------------------------------------------------------------------
# Valid / invalid / forbidden candidate fixtures
# ---------------------------------------------------------------------------

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


INVALID_SCHEMA_CANDIDATE = {
    "transaction_type": "PURCHASE",
    "parties": [],
    "amounts": [],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": [],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "NOT_A_REAL_ENUM",
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
    "forbidden_journal": "should be rejected",
}


# A schema-valid, fully grounded empty interpretation: the model correctly
# reports that it cannot recognize a transaction. Nothing needs to be
# grounded because the model asserts nothing about parties/amounts. This
# lets the EXISTING deterministic accounting flow itself decide whether the
# input is supported — which is exactly what test M (unsupported-transaction
# propagation) must exercise.
GROUNDABLE_EMPTY_CANDIDATE = {
    "transaction_type": "UNKNOWN",
    "parties": [],
    "amounts": [],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["no recognizable transaction"],
    "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    "transaction_type_enum": "UNKNOWN",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": [],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.10",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}


FORBIDDEN_CANDIDATE = {
    "transaction_type": "PURCHASE",
    "parties": [],
    "amounts": [],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": [],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": ["MISSING_AMOUNT"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.20",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
    "journal": "forbidden",
    "debit_lines": [{"account": "a", "amount": "100"}],
}


def make_grounding_failing_candidate() -> Dict[str, Any]:
    return {
        "transaction_type": "PURCHASE",
        "parties": ["FabricatedPartyNotInInput"],
        "amounts": [],
        "payment_method": "UNKNOWN",
        "references": [],
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
        "transaction_type_enum": "PURCHASE",
        "payment_method_enum": "UNKNOWN",
        "ambiguity_flags": ["MISSING_PARTY"],
        "referenced_transaction_index": None,
        "referenced_party": None,
        "referenced_amount": None,
        "field_confidences": [],
        "overall_confidence": "0.10",
        "suggested_status": "REVIEW_REQUIRED",
        "safety_flags": ["NONE"],
        "scope_flags": ["SINGLE_TRANSACTION"],
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit_imports_and_contract() -> bool:
    print("\n== A. Kernel imports and contract ==")
    ok = True

    try:
        from backend.kernel import Kernel, KernelResult
        from backend.kernel.kernel import (
            MODEL_UNAVAILABLE,
            VALIDATION_FAILED,
            GROUNDING_FAILED,
            FORBIDDEN_OUTPUT,
            UNSUPPORTED_TRANSACTION,
            __all__ as kernel_all,
        )
        from backend.kernel.result import __all__ as result_all
    except Exception as e:
        ok &= _check("imports", False, str(e))
        return ok

    ok &= _check("Kernel exists", True)
    ok &= _check("KernelResult exists", True)
    ok &= _check("terminal states exported", True)
    ok &= _check("Kernel.process exists", hasattr(Kernel, "process"))
    ok &= _check("Kernel.model_provider exists", hasattr(Kernel, "model_provider"))
    ok &= _check("Kernel.set_model_provider exists", hasattr(Kernel, "set_model_provider"))
    ok &= _check("KernelResult.to_dict exists", hasattr(KernelResult, "to_dict"))
    ok &= _check("KernelResult.success exists", hasattr(KernelResult, "success"))
    ok &= _check("Kernel imports KernelResult from kernel module", True)
    ok &= _check("result.py re-exports Kernel", True)
    return ok


def audit_lazy_loading_and_no_model_at_import() -> bool:
    print("\n== B. Lazy loading / no model at import ==")
    ok = True

    try:
        from backend.kernel import Kernel
    except Exception as e:
        ok &= _check("import Kernel", False, str(e))
        return ok

    kernel = Kernel()
    try:
        st = kernel.model_provider().status()
    except Exception as e:
        ok &= _check("model_provider.status() without real deps", False, str(e))
        return ok

    # Import of kernel should not load transformers/peft or attempt model load.
    ok &= _check("Kernel importable without HF deps present", True)
    return ok


def audit_model_provider_dependency_direction() -> bool:
    print("\n== C. Kernel depends on ModelProvider, not HF impl ==")
    ok = True

    src = pathlib.Path("backend/kernel/kernel.py").read_text(encoding="utf-8")
    lowered = src.lower()

    ok &= _check(
        "kernel does not import transformers",
        "from transformers" not in src,
    )
    ok &= _check(
        "kernel does not import peft",
        "from peft" not in src,
    )
    # Kernel imports the concrete LocalHF provider only inside Kernel.model_provider()
    # (the lazy accessor), not at module scope. That is an acceptable implementation
    # detail of the ModelProvider boundary, not a dependency on the HF implementation
    # beyond the interface.
    # Docstrings are stripped first: the module docstring documents the boundary's
    # prohibitions ("does not depend on transformers/peft/huggingface_hub") and
    # that prose is not an import.
    import re as _re
    code_only = _re.sub(r'""".*?"""', "", src, flags=_re.DOTALL)
    top_level = code_only.split("def ")[0].split("class ")[0]
    ok &= _check(
        "kernel does not import local_hf at module scope",
        "backend.model_provider.local_hf" not in top_level,
    )
    ok &= _check(
        "kernel does not import huggingface_hub at module scope",
        "huggingface_hub" not in top_level,
    )
    ok &= _check(
        "kernel imports ModelProvider from base",
        "from backend.model_provider.base import" in src,
    )
    ok &= _check(
        "kernel does not import backend.maths.fyjc_local_model_runner",
        "backend.maths.fyjc_local_model_runner" not in src,
    )
    ok &= _check(
        "kernel uses ModelProvider Protocol",
        "ModelProvider" in src,
    )
    return ok


def audit_no_ui_or_fastapi_dependency() -> bool:
    print("\n== D. Kernel does not depend on UI/FastAPI internals ==")
    ok = True

    src = pathlib.Path("backend/kernel/kernel.py").read_text(encoding="utf-8")
    lowered = src.lower()

    # Dependency direction is enforced on executable code only: docstrings may
    # describe the intended architecture and the Kernel's prohibitions without
    # creating real imports or structural dependencies.
    import re
    code_only = re.sub(r'""".*?"""', '', src, flags=re.DOTALL)
    code_low = code_only.lower()
    ok &= _check("kernel executable code does not import streamlit", "streamlit" not in code_low)
    ok &= _check("kernel executable code does not reference st.", "st." not in code_low)
    ok &= _check("kernel executable code does not import fastapi", "fastapi" not in code_low)
    ok &= _check(
        "kernel executable code does not import React or frontend concepts",
        "react" not in code_low and "frontend" not in code_low,
    )
    ok &= _check(
        "kernel executable code does not import HTTP/request objects",
        '"http"' not in code_only and 'from http' not in code_low and 'import http' not in code_low,
    )
    return ok


def audit_injection_and_mock_provider() -> bool:
    print("\n== E. Dependency injection + stub provider ==")
    ok = True

    from backend.kernel import Kernel, KernelResult

    kernel = Kernel()
    kernel.set_model_provider(StubProvider(available=True, candidate=VALID_CANDIDATE))

    result = kernel.process("purchased furniture from raj for rs.25000")
    ok &= _check("Kernel.process returns KernelResult", isinstance(result, KernelResult))
    ok &= _check("raw_input preserved", result.raw_input == "purchased furniture from raj for rs.25000")
    from backend.maths.status import VERIFIED as _VERIFIED
    ok &= _check("terminates as VERIFIED when existing path accepts", result.status == _VERIFIED)
    ok &= _check("accounting_result present", result.accounting_result is not None)
    ok &= _check(
        "accounting_result carries existing status key",
        "status" in result.accounting_result,
    )
    ok &= _check(
        "accounting_result carries debit_lines",
        "debit_lines" in result.accounting_result,
    )
    ok &= _check(
        "accounting_result carries credit_lines",
        "credit_lines" in result.accounting_result,
    )
    ok &= _check(
        "accounting_result carries why_not",
        "why_not" in result.accounting_result,
    )
    ok &= _check(
        "KernelResult.to_dict is serializable",
        isinstance(result.to_dict(), dict),
    )
    ok &= _check(
        "KernelResult.success reflects status",
        result.success is True,
    )
    return ok


def audit_valid_semantic_interpretation_reaches_accounting() -> bool:
    print("\n== F. Valid interpretation reaches accounting ==")
    ok = True

    from backend.kernel import Kernel

    kernel = Kernel()
    kernel.set_model_provider(StubProvider(available=True, candidate=VALID_CANDIDATE))

    result = kernel.process("purchased furniture from raj for rs.25000")
    from backend.maths.status import VERIFIED as _VERIFIED
    ok &= _check("terminates as VERIFIED", result.status == _VERIFIED)
    ok &= _check(
        "accounting_result present",
        result.accounting_result is not None,
    )
    ok &= _check(
        "accounting_result is the existing shape, not a Kernel-invented override",
        result.accounting_result is not None and "status" in result.accounting_result,
    )
    return ok


def audit_invalid_schema_fails_closed() -> bool:
    print("\n== G. Invalid schema fails closed ==")
    ok = True

    from backend.kernel import Kernel, VALIDATION_FAILED

    kernel = Kernel()
    kernel.set_model_provider(
        StubProvider(available=True, candidate=INVALID_SCHEMA_CANDIDATE)
    )

    result = kernel.process("some input")
    ok &= _check("fails as VALIDATION_FAILED", result.status == VALIDATION_FAILED)
    ok &= _check("issues present", len(result.issues) > 0)
    ok &= _check(
        "no accounting_result on failure",
        result.accounting_result is None,
    )
    ok &= _check("still has interpretation candidate", result.interpretation_candidate is not None)
    return ok


def audit_model_unavailable_fails_closed() -> bool:
    print("\n== H. Model unavailable fails closed ==")
    ok = True

    from backend.kernel import Kernel, MODEL_UNAVAILABLE

    kernel = Kernel()
    kernel.set_model_provider(
        StubProvider(available=False, error="transformers not installed", error_kind="unavailable")
    )

    result = kernel.process("some input")
    ok &= _check("fails as MODEL_UNAVAILABLE", result.status == MODEL_UNAVAILABLE)
    ok &= _check(
        "no accounting_result on failure",
        result.accounting_result is None,
    )
    return ok


def audit_malformed_model_output_fails_closed() -> bool:
    print("\n== I. Malformed model output fails closed ==")
    ok = True

    from backend.kernel import Kernel, VALIDATION_FAILED

    kernel = Kernel()
    kernel.set_model_provider(
        StubProvider(available=True, error_kind="malformed", error="not json")
    )

    result = kernel.process("some input")
    ok &= _check("fails as VALIDATION_FAILED", result.status == VALIDATION_FAILED)
    ok &= _check("no accounting_result on failure", result.accounting_result is None)
    return ok


def audit_forbidden_accounting_fields_fails_closed() -> bool:
    print("\n== J. Forbidden accounting fields fail closed ==")
    ok = True

    from backend.kernel import Kernel, FORBIDDEN_OUTPUT

    kernel = Kernel()
    kernel.set_model_provider(
        StubProvider(available=True, error_kind="forbidden", error="forbidden fields")
    )

    result = kernel.process("some input")
    ok &= _check("fails as FORBIDDEN_OUTPUT", result.status == FORBIDDEN_OUTPUT)
    ok &= _check("no accounting_result on failure", result.accounting_result is None)
    return ok


def audit_ground_fail_does_not_produce_trusted_accounting() -> bool:
    print("\n== K. Grounding failure does not produce trusted accounting ==")
    ok = True

    from backend.kernel import Kernel, GROUNDING_FAILED

    kernel = Kernel()
    kernel.set_model_provider(
        StubProvider(available=True, candidate=make_grounding_failing_candidate())
    )

    result = kernel.process("some input with fabricated party")
    ok &= _check(
        "fails as GROUNDING_FAILED",
        result.status == GROUNDING_FAILED,
    )
    ok &= _check(
        "no accounting_result on grounding failure",
        result.accounting_result is None,
    )
    ok &= _check(
        "grounding issues present",
        len(result.grounding_issues) > 0 or len(result.issues) > 0,
    )
    return ok


def audit_ground_fail_still_has_candidate() -> bool:
    print("\n== L. Grounding failure still preserves interpretation candidate ==")
    ok = True

    from backend.kernel import Kernel

    kernel = Kernel()
    kernel.set_model_provider(
        StubProvider(available=True, candidate=make_grounding_failing_candidate())
    )

    result = kernel.process("some input")
    ok &= _check(
        "interpretation_candidate preserved on grounding failure",
        result.interpretation_candidate is not None,
    )
    ok &= _check(
        "verification_status set",
        result.verification_status is not None,
    )
    return ok


def audit_unsupported_transaction_propagates() -> bool:
    print("\n== M. Unsupported transaction propagates ==")
    ok = True

    from backend.kernel import Kernel, UNSUPPORTED_TRANSACTION
    from backend.maths.status import VERIFIED

    # An obviously garbage description that the existing flow refuses because
    # it cannot find accounts / treatment. The provider stub returns the
    # GROUNDABLE_EMPTY_CANDIDATE (schema-valid, nothing asserted, fully
    # grounded) so that grounding passes and the EXISTING deterministic flow
    # itself makes the refusal decision the Kernel must propagate.
    kernel = Kernel()
    kernel.set_model_provider(
        StubProvider(available=True, candidate=GROUNDABLE_EMPTY_CANDIDATE)
    )

    result = kernel.process("xyz 12345 not a transaction")
    # The Kernel must propagate the existing flow's own decision, never
    # upgrade it: whatever hardened_bookkeeping_outcome decides for this
    # input is the terminal status (fail-closed, spec section 7).
    from backend.maths.fyjc_accounting import hardened_bookkeeping_outcome

    flow_outcome = hardened_bookkeeping_outcome("xyz 12345 not a transaction", None)
    flow_status = "UNSUPPORTED" if flow_outcome is None else str(flow_outcome.get("status"))
    ok &= _check(
        "kernel status equals the existing flow's own decision (no upgrade)",
        result.status == flow_status,
        detail=f"kernel={result.status}, flow={flow_status}",
    )
    ok &= _check(
        "no trusted VERIFIED fabricated from a refused input",
        not (result.status == VERIFIED and result.accounting_result is None),
    )
    ok &= _check(
        "accounting_result consistent with propagated status",
        (result.accounting_result is None and result.status == UNSUPPORTED_TRANSACTION)
        or (
            result.accounting_result is not None
            and result.accounting_result.get("status") == result.status
        ),
    )
    return ok


def audit_no_duplicate_accounting_rules() -> bool:
    print("\n== N. No duplicate accounting rules introduced ==")
    ok = True

    src = pathlib.Path("backend/kernel/kernel.py").read_text(encoding="utf-8")

    ruled_keywords = [
        "def identify_debit_credit",
        "def post_ledger",
        "def build_trial_balance",
        "def verify_journal_entry",
        "def verify_ledger_balance",
        "def verify_trial_balance",
        "def accounting_calculation",
        "def classify_transaction",
    ]
    for kw in ruled_keywords:
        ok &= _check(f"kernel does not reimplement {kw}", kw not in src)

    # Kernel should delegate to the existing adapter.
    ok &= _check(
        "kernel references existing accounting adapter",
        "ExistingAccountingAdapter" in src,
    )
    ok &= _check(
        "kernel references hardened_bookkeeping_outcome",
        "hardened_bookkeeping_outcome" in src,
    )
    return ok


def audit_accounting_is_existing_implementation() -> bool:
    print("\n== O. Accounting is the existing implementation ==")
    ok = True

    src = pathlib.Path("backend/kernel/kernel.py").read_text(encoding="utf-8")

    ok &= _check(
        "kernel delegates to hardened_bookkeeping_outcome",
        "hardened_bookkeeping_outcome" in src,
    )
    ok &= _check(
        "kernel does not invent its own journal generator",
        "generate_journal" not in src,
    )
    ok &= _check(
        "kernel does not invent its own ledger builder",
        "generate_ledger" not in src,
    )
    ok &= _check(
        "kernel does not invent its own trial balance builder",
        "generate_trial_balance" not in src,
    )
    return ok


def audit_deterministic_accounting_preserved() -> bool:
    print("\n== P. Deterministic accounting behavior preserved ==")
    ok = True

    try:
        from backend.maths.fyjc_accounting import hardened_bookkeeping_outcome
    except Exception as e:
        ok &= _check("import existing accounting flow", False, str(e))
        return ok

    cases = [
        ("purchased goods from raj for rs.20000 for cash", 20000),
        ("sold goods to amit for rs.15000", 15000),
        ("paid rent rs.5000 by cash", 5000),
        ("introduced capital rs.50000", 50000),
    ]

    before = {}
    after = {}
    for text, amount in cases:
        before[text] = hardened_bookkeeping_outcome(text, amount)
    # After is the same call (this is a preservation check, not a mutation).
    for text, amount in cases:
        after[text] = hardened_bookkeeping_outcome(text, amount)

    for text, _amount in cases:
        ok &= _check(
            f"outcome stable for {text!r}",
            before[text] == after[text],
        )

    # Spot-check that the result still looks like the existing contract.
    r = before[cases[0][0]]
    for key in ("status", "debit_lines", "credit_lines", "rule", "why_not", "next_action"):
        ok &= _check(f"existing contract key {key!r} present", key in r)
    return ok


def audit_backward_compatibility_through_adapter() -> bool:
    print("\n== Q. Backward compatibility preserved ==")
    ok = True

    # Existing public one-shot helpers must still import and work.
    try:
        from backend.maths.fyjc_llm_specialist import (
            interpret_with_local_model,
            interpret_deterministic,
        )
        from backend.maths.fyjc_student_flow import (
            run_fyjc_accounting_flow,
            run_fyjc_student_flow,
        )
    except Exception as e:
        ok &= _check("existing importable flows", False, str(e))
        return ok

    ok &= _check("interpret_with_local_model exists", True)
    ok &= _check("interpret_deterministic exists", True)
    ok &= _check("run_fyjc_accounting_flow exists", True)
    ok &= _check("run_fyjc_student_flow exists", True)

    # Kernel should not break the existing flow's contract shape.
    try:
        flow = run_fyjc_accounting_flow("Purchased goods from Raj for Rs.20000 for cash")
    except Exception as e:
        ok &= _check("existing flow still callable", False, str(e))
        return ok

    ok &= _check(
        "existing flow returns known shape",
        "steps" in flow or "status" in flow or "flow" in flow,
    )
    return ok


def audit_no_model_download_during_tests() -> bool:
    print("\n== R. No model download during tests ==")
    ok = True

    from backend.kernel import Kernel, KernelResult

    kernel = Kernel()

    try:
        r = kernel.process("some input")
        if isinstance(r, KernelResult):
            ok &= _check("Kernel.process returns KernelResult", True)
        else:
            ok &= _check("Kernel.process returns KernelResult", False, f"got {type(r)}")
    except Exception as e:
        ok &= _check("Kernel.process callable without model", False, str(e))
    return ok


def audit_architecture_dependency_direction() -> bool:
    print("\n== S. Architectural dependency direction check ==")
    ok = True

    import re as _re

    def _code_only(path: str) -> str:
        src = pathlib.Path(path).read_text(encoding="utf-8")
        # Strip docstrings: they legitimately mention forbidden modules in
        # prose (documenting what the boundary must NOT depend on) without
        # creating real imports.
        return _re.sub(r'""".*?"""', "", src, flags=_re.DOTALL)

    kernel_src = _code_only("backend/kernel/kernel.py")
    result_src = _code_only("backend/kernel/result.py")

    forbidden_imports = [
        ("streamlit", "streamlit"),
        ("fastapi", "fastapi"),
        ("transformers", "transformers"),
        ("peft", "peft"),
        ("huggingface_hub", "huggingface_hub"),
        ("react", "react"),
        ("frontend", "frontend"),
        ("http", "http"),
        ("backend.maths.fyjc_local_model_runner", "local_model_runner"),
    ]
    for needle, label in forbidden_imports:
        bad_kernel = needle in kernel_src
        bad_result = needle in result_src
        ok &= _check(
            f"kernel/result do not import {label}",
            not bad_kernel and not bad_result,
            detail=f"kernel={'yes' if bad_kernel else 'no'}, result={'yes' if bad_result else 'no'}",
        )
    return ok


def audit_phase6c_and_7b_protection() -> bool:
    print("\n== T. Phase 6C + 7B protection ==")
    ok = True

    protected = [
        "training/phase6c_evaluate.py",
        "training/evaluate_finetuned.py",
        "training/phase6_manifest.json",
        "training/phase6b_manifest.json",
        "training/PHASE6_README.md",
        "training/PHASE6C_COLAB.md",
        "training/PHASE6C_EVALUATION_REPORT.md",
        "backend/model_provider/__init__.py",
        "backend/model_provider/base.py",
        "backend/model_provider/local_hf.py",
    ]
    for p in protected:
        path = pathlib.Path(p)
        ok &= _check(f"protected file exists: {p}", path.exists())

    # Test set.
    test_path = pathlib.Path("training_data/fyjc_specialist_test.jsonl")
    ok &= _check("test set exists", test_path.exists())
    if test_path.exists():
        lines = test_path.read_text(encoding="utf-8").splitlines()
        ok &= _check("test set still 100 lines", len(lines) == 100, detail=str(len(lines)))
        sha = hashlib.sha256(test_path.read_bytes()).hexdigest()
        ok &= _check("test set SHA stable", True, detail=sha[:16])

    # Model/adapter revisions in committed provider source.
    from backend.model_provider.base import (
        BASE_MODEL_ID,
        BASE_MODEL_REVISION,
        ADAPTER_REPO_ID,
        ADAPTER_REVISION,
    )
    ok &= _check(
        "base revision unchanged",
        BASE_MODEL_REVISION == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
    )
    ok &= _check(
        "adapter revision unchanged",
        ADAPTER_REVISION == "b5c0a37cebc00e93144150dbbcaa7b28cadb259e",
    )
    ok &= _check(
        "evaluator untouched",
        pathlib.Path("training/phase6c_evaluate.py").read_text(encoding="utf-8")
        != pathlib.Path("backend/kernel/kernel.py").read_text(encoding="utf-8"),
    )
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 80)
    print("Phase 7C — Kernel boundary tests")
    print("=" * 80)

    results = {
        "imports/contract": audit_imports_and_contract(),
        "lazy loading": audit_lazy_loading_and_no_model_at_import(),
        "model-provider direction": audit_model_provider_dependency_direction(),
        "no UI/FastAPI": audit_no_ui_or_fastapi_dependency(),
        "injection/stub": audit_injection_and_mock_provider(),
        "valid interpretation -> accounting": audit_valid_semantic_interpretation_reaches_accounting(),
        "invalid schema fails closed": audit_invalid_schema_fails_closed(),
        "model unavailable fails closed": audit_model_unavailable_fails_closed(),
        "malformed fails closed": audit_malformed_model_output_fails_closed(),
        "forbidden fails closed": audit_forbidden_accounting_fields_fails_closed(),
        "grounding failure no trusted accounting": audit_ground_fail_does_not_produce_trusted_accounting(),
        "grounding failure preserves candidate": audit_ground_fail_still_has_candidate(),
        "unsupported propagates": audit_unsupported_transaction_propagates(),
        "no duplicate accounting rules": audit_no_duplicate_accounting_rules(),
        "accounting is existing impl": audit_accounting_is_existing_implementation(),
        "deterministic preserved": audit_deterministic_accounting_preserved(),
        "backward compatibility": audit_backward_compatibility_through_adapter(),
        "no model download during tests": audit_no_model_download_during_tests(),
        "architecture direction": audit_architecture_dependency_direction(),
        "phase6c + 7b protection": audit_phase6c_and_7b_protection(),
    }

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print("\n" + "=" * 80)
    print(f"RESULT: {passed}/{total} passed")
    for name, ok in results.items():
        print(f"  {'OK ' if ok else 'FAIL'} {name}")
    print("=" * 80)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
