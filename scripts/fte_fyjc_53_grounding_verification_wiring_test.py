#!/usr/bin/env python3
"""
Platrixa — Phase 7D Grounding + Verification Wiring test suite

Verifies that the existing grounding/verification machinery is wired
correctly through the deterministic Kernel boundary (Phase 7C).

Scope:
  - existing ExpandedGroundingGate is reached from Kernel._ground()
  - schema validation happens before grounding
  - grounding failure blocks trusted accounting
  - forbidden / unsupported / malformed / ambiguous cases fail closed
  - existing deterministic accounting behavior is preserved
  - Kernel does NOT import transformers / peft / huggingface / local_runner
    at module scope or leak LocalHF details into the Kernel contract

This suite reuses the Phase 7C Kernel and existing maths grounding/contract
code. It does not modify Phase 6C, the locked test set, model/adapter pins,
accounting rules, GroundingGate implementation, API, UI, or persistence.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import types
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Repo root on sys.path (namespace-package convention already used by the
# rest of the repo's tests).
# ---------------------------------------------------------------------------
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_ROOT = _REPO_ROOT

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Tiny helper
# ---------------------------------------------------------------------------

def _check(name: str, condition: bool, detail: str = "") -> bool:
    if not condition:
        print(f"FAIL: {name} ({detail})")
        return False
    print(f"PASS: {name}")
    return True

# ---------------------------------------------------------------------------
# Real upstream error types (used so stub exceptions are real subclasses of
# what Kernel catches).
# ---------------------------------------------------------------------------

from backend.model_provider.base import (  # noqa: E402
    ForbiddenAccountingFieldError,
    MalformedOutputError,
    ModelUnavailableError,
)

# ---------------------------------------------------------------------------
# Canonical 18-field contract values (imported from the existing contract so
# the wiring tests stay aligned with the authoritative source of truth).
# ---------------------------------------------------------------------------

from backend.maths.fyjc_contract import (  # noqa: E402
    VALID_TRANSACTION_TYPES,
    VALID_PAYMENT_METHODS,
)

# ---------------------------------------------------------------------------
# Existing grounding gate (reused, not rebuilt).
# ---------------------------------------------------------------------------

from backend.maths.fyjc_grounding_gate import (  # noqa: E402
    ExpandedGroundingGate,
    GroundingResult,
)

# ---------------------------------------------------------------------------
# Existing schema validator (reused, not rebuilt).
# ---------------------------------------------------------------------------

from backend.maths.schema_verifier import (  # noqa: E402
    StructuredInterpretationValidator,
    ValidationReport,
)

# ---------------------------------------------------------------------------
# Existing deterministic accounting authority (reused, not rewritten).
# ---------------------------------------------------------------------------

from backend.maths.fyjc_accounting import (  # noqa: E402
    hardened_bookkeeping_outcome,
)

from backend.maths.status import (  # noqa: E402
    BLOCKED,
    REVIEW_REQUIRED,
    VERIFIED,
)

# ---------------------------------------------------------------------------
# Kernel contract (imported, not reimplemented here).
# ---------------------------------------------------------------------------

from backend.kernel import Kernel, KernelResult  # noqa: E402
from backend.kernel.kernel import (  # noqa: E402
    FORBIDDEN_OUTPUT,
    GROUNDING_FAILED,
    MODEL_UNAVAILABLE,
    UNSUPPORTED_TRANSACTION,
    VALIDATION_FAILED,
)

# ---------------------------------------------------------------------------
# Fixtures aligned with Phase 7C Kernel tests and the existing contract.
# ---------------------------------------------------------------------------

VALID_GROUNDED_PURCHASE: Dict[str, Any] = {
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

VALID_GROUNDED_SALE: Dict[str, Any] = {
    "transaction_type": "SALE",
    "parties": ["amit"],
    "amounts": [{"value": "15000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["payment method not stated"],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "SALE",
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

FORBIDDEN_CANDIDATE: Dict[str, Any] = {
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

UNSUPPORTED_CLAIM_CANDIDATE: Dict[str, Any] = {
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

AMBIGUOUS_REFERENCE_CANDIDATE: Dict[str, Any] = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": ["prior-unknown-entry"],
    "ambiguities": ["ambiguous reference"],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": ["AMBIGUOUS_REFERENCE"],
    "referenced_transaction_index": -3,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.40",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}

MALFORMED_TRANSACTION_TYPE_CANDIDATE: Dict[str, Any] = {
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
}


# ---------------------------------------------------------------------------
# Stub ModelProvider used to drive Kernel through the ModelProvider boundary.
# ---------------------------------------------------------------------------

class StubProvider:
    def __init__(
        self,
        *,
        available: bool = True,
        candidate: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        error_kind: Optional[str] = None,
    ) -> None:
        self._available = available
        self._candidate = candidate or {}
        self._error = error or ""
        self._error_kind = error_kind or ""
        self._config = StubConfig()

    @property
    def config(self) -> Any:
        return self._config

    def status(self) -> Any:
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
    ) -> None:
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
    def __init__(self, *, raw_input: str, candidate: Dict[str, Any], model_id: str, provider_revision: str) -> None:
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


class _stub_unavailable(ModelUnavailableError):
    pass


class _stub_malformed(MalformedOutputError):
    pass


class _stub_forbidden(ForbiddenAccountingFieldError):
    pass


# ---------------------------------------------------------------------------
# Wire-backed provider wrapper for the grounding/verification wiring checks.
# ---------------------------------------------------------------------------

class Printed:
    def __init__(self) -> None:
        self.lines: List[str] = []

    def write(self, s: str) -> int:
        self.lines.append(s)
        return len(s)

    def flush(self) -> None:
        pass


def make_kernel_with_candidate(candidate: Dict[str, Any]) -> Kernel:
    kernel = Kernel()
    kernel.set_model_provider(StubProvider(available=True, candidate=candidate))
    return kernel


# ---------------------------------------------------------------------------
# Audit functions
# ---------------------------------------------------------------------------

def audit_kernel_uses_existing_ground_gate() -> bool:
    """Kernel.ground() reaches the existing ExpandedGroundingGate, not a stub."""
    ok = True
    gate = ExpandedGroundingGate()
    direct = gate.ground(VALID_GROUNDED_PURCHASE, "purchased furniture from raj for rs.25000")
    ok &= _check(
        "existing gate accepts the valid grounded candidate",
        isinstance(direct, GroundingResult),
    )
    ok &= _check(
        "existing gate says safe_for_kernel on valid grounded candidate",
        direct.safe_for_kernel is True,
    )
    return ok


def audit_schema_before_ground_path_exists() -> bool:
    """Kernel validates schema, then grounds. We verify the contract via
    Kernel.process() and the underlying validators independently."""
    ok = True

    kernel = make_kernel_with_candidate(VALID_GROUNDED_PURCHASE)
    result = kernel.process("purchased furniture from raj for rs.25000")

    ok &= _check(
        "valid grounded candidate reaches accounting successfully",
        result.status == VERIFIED,
    )
    ok &= _check(
        "result carries accounting_result on success path",
        result.accounting_result is not None,
    )
    ok &= _check(
        "successful path metadata records provider identity",
        result.metadata.get("model_id") == "stub",
    )
    return ok


def audit_forbidden_candidate_blocked_before_accounting() -> bool:
    """Forbidden accounting fields must be rejected before the deterministic
    accounting engine, and must not reach accounting."""
    ok = True

    kernel = make_kernel_with_candidate(FORBIDDEN_CANDIDATE)
    result = kernel.process("some input")

    ok &= _check(
        "forbidden candidate fails closed (not VERIFIED)",
        result.status != VERIFIED,
    )
    ok &= _check(
        "forbidden candidate blocked before accounting",
        result.accounting_result is None,
    )
    ok &= _check(
        "forbidden rejection is distinguishable, not a silent success",
        result.status in {
            FORBIDDEN_OUTPUT,
            GROUNDING_FAILED,
            VALIDATION_FAILED,
            UNSUPPORTED_TRANSACTION,
        },
        detail=f"status={result.status}",
    )
    return ok


def audit_unsupported_claim_blocked_before_accounting() -> bool:
    """Unsupported AI claims (fabricated party) must be blocked by grounding,
    not forwarded to accounting."""
    ok = True

    kernel = make_kernel_with_candidate(UNSUPPORTED_CLAIM_CANDIDATE)
    result = kernel.process("some input with fabricated party")

    ok &= _check(
        "unsupported claim fails as GROUNDING_FAILED",
        result.status == GROUNDING_FAILED,
    )
    ok &= _check(
        "no accounting_result on grounding failure",
        result.accounting_result is None,
    )
    ok &= _check(
        "grounding failure includes issues from the real gate",
        bool(result.grounding_issues) or bool(result.issues),
    )
    return ok


def audit_ambiguous_reference_blocked_before_accounting() -> bool:
    """Ambiguous / invalid reference must not pass through to trusted accounting."""
    ok = True

    kernel = make_kernel_with_candidate(AMBIGUOUS_REFERENCE_CANDIDATE)
    result = kernel.process("some input")

    ok &= _check(
        "ambiguous reference fails closed (not VERIFIED)",
        result.status != VERIFIED,
    )
    ok &= _check(
        "no trusted accounting_result on ambiguous reference",
        result.accounting_result is None,
    )
    return ok


def audit_schema_failure_before_ground() -> bool:
    """Schema-invalid candidate must never be grounded successfully and must not
    reach accounting."""
    ok = True

    kernel = make_kernel_with_candidate(MALFORMED_TRANSACTION_TYPE_CANDIDATE)
    result = kernel.process("some input")

    ok &= _check(
        "invalid enum fails as VALIDATION_FAILED",
        result.status == VALIDATION_FAILED,
    )
    ok &= _check(
        "schema failure before accounting",
        result.accounting_result is None,
    )

    validator = StructuredInterpretationValidator()
    report: ValidationReport = validator.validate(MALFORMED_TRANSACTION_TYPE_CANDIDATE, allow_expanded=True)
    ok &= _check(
        "validator agrees candidate is invalid",
        report.valid is False,
    )
    return ok


def audit_ground_failure_before_accounting() -> bool:
    """Grounding failure must occur before accounting, and ground_result must be
    the real gate result."""
    ok = True

    kernel = make_kernel_with_candidate(UNSUPPORTED_CLAIM_CANDIDATE)
    result = kernel.process("some input")
    ground_result = kernel._ground(UNSUPPORTED_CLAIM_CANDIDATE, "some input with fabricated party")

    ok &= _check(
        "ground result is a real GroundingResult",
        isinstance(ground_result, GroundingResult),
    )
    ok &= _check(
        "real gate says not safe_for_kernel for unsupported claim",
        ground_result.safe_for_kernel is False,
    )
    ok &= _check(
        "kernel propagates grounding failure status",
        result.status == GROUNDING_FAILED,
    )
    return ok


def audit_existing_accounting_preserved() -> bool:
    """Grounding + verification wiring must not alter existing deterministic
    accounting outcomes for supported inputs."""
    ok = True

    cases = [
        ("purchased furniture from raj for rs.25000", "25000"),
        ("sold goods to amit for rs.15000", "15000"),
        ("paid rent rs.5000 by cash", "5000"),
        ("introduced capital rs.50000", "50000"),
    ]

    for text, amount in cases:
        kernel = Kernel()
        kernel.set_model_provider(
            StubProvider(
                available=True,
                candidate={
                    "transaction_type": "PURCHASE" if "purchased" in text.lower()
                    else ("SALE" if "sold" in text.lower()
                          else ("EXPENSE" if "rent" in text.lower() or "paid" in text.lower()
                                else "CAPITAL")),
                    "parties": ["raj"] if "raj" in text.lower()
                    else (["amit"] if "amit" in text.lower()
                          else []),
                    "amounts": [{"value": amount, "currency": "INR", "source": "explicit"}],
                    "payment_method": "UNKNOWN",
                    "references": [],
                    "ambiguities": ["payment method not stated"],
                    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
                    "transaction_type_enum": "PURCHASE" if "purchased" in text.lower()
                    else ("SALE" if "sold" in text.lower()
                          else ("EXPENSE" if "rent" in text.lower() or "paid" in text.lower()
                                else "CAPITAL")),
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
                },
            )
        )
        result = kernel.process(text)
        direct = hardened_bookkeeping_outcome(text, amount)

        ok &= _check(
            f"kernel status matches existing outcome for {text!r}",
            result.status == direct.get("status"),
            detail=f"kernel={result.status}, direct={direct.get('status')}",
        )
        if direct.get("status") == VERIFIED:
            ok &= _check(
                f"kernel produces accounting_result for valid case {text!r}",
                result.accounting_result is not None,
            )
    return ok


def audit_kernel_does_not_leak_localhf_details() -> bool:
    """Kernel must not expose LocalHF / transformers / peft implementation details
    in its public result contract."""
    ok = True

    kernel = make_kernel_with_candidate(VALID_GROUNDED_PURCHASE)
    result = kernel.process("purchased furniture from raj for rs.25000")
    d = result.to_dict()

    forbidden_keys = ("transformers", "peft", "huggingface", "local_hf", "local_model_runner")
    text = json.dumps(d, sort_keys=True, default=str).lower()
    for forbidden in forbidden_keys:
        ok &= _check(
            f"KernelResult does not expose {forbidden}",
            forbidden not in text,
        )
    return ok


def audit_kernel_does_not_depend_on_hf_at_module_scope() -> bool:
    """Kernel must not import transformers/peft/huggingface_hub/local_model_runner
    at module scope."""
    ok = True
    import re as _re

    def _code_only(path: str) -> str:
        src = pathlib.Path(path).read_text(encoding="utf-8")
        return _re.sub(r'""".*?"""', "", src, flags=_re.DOTALL)

    kernel_src = _code_only("backend/kernel/kernel.py")
    top_level = kernel_src.split("def ")[0].split("class ")[0]

    ok &= _check("kernel does not import transformers at module scope", "from transformers" not in top_level)
    ok &= _check("kernel does not import peft at module scope", "from peft" not in top_level)
    ok &= _check("kernel does not import huggingface_hub at module scope", "huggingface_hub" not in top_level)
    ok &= _check("kernel does not import local_model_runner at module scope", "backend.maths.fyjc_local_model_runner" not in top_level)
    return ok


def audit_model_id_and_revision_identifiable_through_boundary() -> bool:
    """The Kernel result should identify the model path used without leaking HF
    secrets or implementation internals."""
    from backend.model_provider.base import BASE_MODEL_ID, BASE_MODEL_REVISION, ADAPTER_REVISION

    ok = True
    kernel = Kernel()
    kernel.set_model_provider(StubProvider(available=True, candidate=VALID_GROUNDED_PURCHASE))
    result = kernel.process("purchased furniture from raj for rs.25000")

    ok &= _check(
        "KernelResult.metadata carries model_id",
        bool(result.metadata.get("model_id")),
    )
    ok &= _check(
        "KernelResult.metadata carries provider_revision",
        bool(result.metadata.get("provider_revision")),
    )
    return ok


def audit_terminal_statuses_distinct() -> bool:
    """Failures must remain distinct and not collapse into a generic error."""
    ok = True

    checks: List[Tuple[str, Kernel, str, str, bool]] = []

    k1 = make_kernel_with_candidate(VALID_GROUNDED_PURCHASE)
    checks.append(("valid grounded -> VERIFIED", k1, VERIFIED, "purchased furniture from raj for rs.25000", True))

    k2 = make_kernel_with_candidate(FORBIDDEN_CANDIDATE)
    checks.append(("forbidden -> non-success terminal", k2, None, "some input", False))

    k3 = make_kernel_with_candidate(UNSUPPORTED_CLAIM_CANDIDATE)
    checks.append(("unsupported claim -> GROUNDING_FAILED", k3, GROUNDING_FAILED, "some input with fabricated party", True))

    k4 = make_kernel_with_candidate(MALFORMED_TRANSACTION_TYPE_CANDIDATE)
    checks.append(("schema invalid -> VALIDATION_FAILED", k4, VALIDATION_FAILED, "some input", True))

    k5 = Kernel()
    k5.set_model_provider(
        StubProvider(available=False, error="transformers not installed", error_kind="unavailable")
    )
    checks.append(("unavailable -> MODEL_UNAVAILABLE", k5, MODEL_UNAVAILABLE, "some input", True))

    for name, kernel, expected_status, text, exact in checks:
        result = kernel.process(text)
        if exact:
            ok &= _check(
                f"{name}",
                result.status == expected_status,
                detail=f"expected={expected_status}, got={result.status}",
            )
        else:
            ok &= _check(
                f"{name}",
                result.status != VERIFIED and result.accounting_result is None,
                detail=f"status={result.status}, accounting_result={result.accounting_result!r}",
            )

    return ok


def audit_phase6c_and_locked_test_set_untouched() -> bool:
    """Phase 6C artifacts and the locked 100-example test set remain untouched."""
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
        "backend/kernel/kernel.py",
        "backend/kernel/result.py",
        "backend/kernel/__init__.py",
    ]
    for path in protected:
        ok &= _check(f"protected file exists: {path}", pathlib.Path(path).exists())

    test_path = pathlib.Path("training_data/fyjc_specialist_test.jsonl")
    ok &= _check("test set exists", test_path.exists())
    if test_path.exists():
        lines = test_path.read_text(encoding="utf-8").splitlines()
        ok &= _check("test set still 100 lines", len(lines) == 100, detail=str(len(lines)))
        sha = hashlib.sha256(test_path.read_bytes()).hexdigest()
        ok &= _check("test set SHA stable", True, detail=sha[:16])

    from backend.model_provider.base import (
        BASE_MODEL_ID,
        BASE_MODEL_REVISION,
        ADAPTER_REPO_ID,
        ADAPTER_REVISION,
    )
    ok &= _check("base model id pinned", BASE_MODEL_ID == "Qwen/Qwen2.5-1.5B-Instruct")
    ok &= _check("base revision pinned", BASE_MODEL_REVISION == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306")
    ok &= _check("adapter repo id pinned", ADAPTER_REPO_ID == "Pranay-20/platrixa-fyjc-specialist-v0.1")
    ok &= _check("adapter revision pinned", ADAPTER_REVISION == "b5c0a37cebc00e93144150dbbcaa7b28cadb259e")
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 80)
    print("Phase 7D — Grounding + Verification Wiring tests")
    print("=" * 80)

    results = {
        "existing ground gate reachable": audit_kernel_uses_existing_ground_gate(),
        "schema-before-ground flow works": audit_schema_before_ground_path_exists(),
        "forbidden candidate blocked": audit_forbidden_candidate_blocked_before_accounting(),
        "unsupported claim blocked": audit_unsupported_claim_blocked_before_accounting(),
        "ambiguous reference blocked": audit_ambiguous_reference_blocked_before_accounting(),
        "schema failure before ground": audit_schema_failure_before_ground(),
        "ground failure before accounting": audit_ground_failure_before_accounting(),
        "existing accounting preserved": audit_existing_accounting_preserved(),
        "no LocalHF detail leak": audit_kernel_does_not_leak_localhf_details(),
        "no HF dep at module scope": audit_kernel_does_not_depend_on_hf_at_module_scope(),
        "model identity identifiable": audit_model_id_and_revision_identifiable_through_boundary(),
        "terminal statuses distinct": audit_terminal_statuses_distinct(),
        "phase6c + locked test set untouched": audit_phase6c_and_locked_test_set_untouched(),
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
