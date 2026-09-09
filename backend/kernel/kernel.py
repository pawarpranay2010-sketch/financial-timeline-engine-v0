"""
Platrixa — Deterministic Kernel boundary (Phase 7C)

This package is the application-level orchestration boundary for the FYJC
student-processing path.

Target architecture (Phase 7C establishes the wiring; deeper grounding/
verification connection is intentionally deferred to Phase 7D):

    Student Input
        ↓
    FastAPI
        ↓
    Kernel
        ├── ModelProvider
        │       ↓
        │   semantic interpretation candidate
        │
        ├── Schema validation
        │
        ├── Grounding / verification extension point
        │
        └── Existing deterministic accounting implementation
                ↓
            AccountingResult
                ↓
            persistence (Phase 7E)

Kernel responsibility:
  - receive the request
  - obtain a semantic interpretation candidate through ModelProvider
  - validate it against the existing 18-field contract
  - run grounding/verification through the existing ground() gate
  - delegate the authoritative accounting treatment to the existing
    deterministic implementation
  - assemble one coherent KernelResult for API/UI consumption
  - fail closed when any required boundary fails

Kernel does NOT:
  - rewrite accounting logic
  - invent a new debit/credit engine
  - invent a new journal generator
  - decide accounting truth
  - persist anything
  - depend on Hugging Face, transformers, peft, huggingface_hub, or the
    LocalHF implementation directly
  - depend on FastAPI, HTTP request objects, frontend, React, Streamlit, UI
    state, or UI components

The existing deterministic accounting implementation remains the source of
truth. Kernel organizes and orchestrates it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.model_provider.base import (
    ForbiddenAccountingFieldError,
    InterpretationResult,
    MalformedOutputError,
    ModelProvider,
    ModelUnavailableError,
    ProviderConfig,
    ProviderStatus,
    contains_forbidden_accounting_fields,
    extract_json_candidate,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reuse existing accounting status vocabulary where possible.
# ---------------------------------------------------------------------------
# We keep the existing FYJC status words for accounting-authority outcomes
# (VERIFIED / BLOCKED / REVIEW_REQUIRED / NOT_SUPPORTED), and add our own
# Kernel-level terminal states for model/semantic/verification failures.
#
# This keeps the Kernel from inventing a new status system for accounting
# outcomes while still having explicit non-success states for the parts of
# the pipeline the existing status vocabulary was not designed to cover.
try:
    from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED
except Exception:
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    VERIFIED = "VERIFIED"

# Kernel-level terminal states (semantic/model/verification)
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
VALIDATION_FAILED = "VALIDATION_FAILED"
GROUNDING_FAILED = "GROUNDING_FAILED"
FORBIDDEN_OUTPUT = "FORBIDDEN_OUTPUT"
UNSUPPORTED_TRANSACTION = "UNSUPPORTED_TRANSACTION"


# ---------------------------------------------------------------------------
# Kernel result contract
# ---------------------------------------------------------------------------

class KernelResult:
    """
    One coherent result object for Kernel processing.

    It bundles:
      - request identity
      - interpretation candidate (when obtained)
      - verification/grounding outcome
      - accounting result (when obtained)
      - terminal state / status
      - issues / reasons
    """

    def __init__(
        self,
        *,
        request_id: Optional[str] = None,
        raw_input: str = "",
        status: str = "",
        status_label: str = "",
        interpretation: Optional[InterpretationResult] = None,
        interpretation_candidate: Optional[Dict[str, Any]] = None,
        verification_status: Optional[str] = None,
        grounding_issues: Optional[List[str]] = None,
        accounting_result: Optional[Dict[str, Any]] = None,
        issues: Optional[List[str]] = None,
        next_action: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.request_id = request_id
        self.raw_input = raw_input
        self.status = status
        self.status_label = status_label
        self.interpretation = interpretation
        self.interpretation_candidate = (
            interpretation_candidate
            or (interpretation.candidate if interpretation is not None else None)
        )
        self.verification_status = verification_status
        self.grounding_issues = grounding_issues or []
        self.accounting_result = accounting_result
        self.issues = issues or []
        self.next_action = next_action
        self.metadata = metadata or {}

    @property
    def success(self) -> bool:
        return self.status == VERIFIED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "raw_input": self.raw_input,
            "status": self.status,
            "status_label": self.status_label,
            "interpretation": (
                self.interpretation.snapshot()
                if self.interpretation is not None
                else None
            ),
            "interpretation_candidate": self.interpretation_candidate,
            "verification_status": self.verification_status,
            "grounding_issues": self.grounding_issues,
            "accounting_result": self.accounting_result,
            "issues": self.issues,
            "next_action": self.next_action,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Kernel
# ---------------------------------------------------------------------------

class Kernel:
    """
    Application-level deterministic kernel boundary for FYJC transactions.

    The Kernel:
      1. obtains a semantic interpretation through ModelProvider
      2. validates it against the existing 18-field contract
      3. runs grounding/verification through the existing ground() gate
      4. delegates accounting truth to the existing deterministic
         implementation via process_accounting()
      5. assembles one KernelResult

    The Kernel does NOT own accounting truth and does NOT persist.
    """

    def __init__(
        self,
        *,
        model_provider: Optional[ModelProvider] = None,
        provider_config: Optional[ProviderConfig] = None,
    ) -> None:
        if model_provider is not None:
            self._model_provider = model_provider
            self._provider_config = model_provider.config if hasattr(model_provider, "config") else None
        else:
            self._model_provider = None
            self._provider_config = provider_config or ProviderConfig()

        # Deferred imports for accounting/verification. These stay inside the
        # methods that need them so the Kernel does not eagerly load heavy
        # domain work at import time and so we keep the dependency graph
        # inspectable.
        self._accounting = None
        self._schema_validator = None
        self._grounding_gate = None

    # ------------------------------------------------------------------
    # ModelProvider access
    # ------------------------------------------------------------------

    def model_provider(self) -> ModelProvider:
        if self._model_provider is not None:
            return self._model_provider
        # Single selection point (Phase 7R): PLATRIXA_MODEL_ENDPOINT_URL set
        # → RemoteHFModelProvider (Modal endpoint); otherwise the existing
        # LocalHFModelProvider. The factory keeps this decision in ONE place
        # and leaves Kernel.process() unchanged.
        from backend.model_provider.remote_hf import get_model_provider

        self._model_provider = get_model_provider(config=self._provider_config)
        return self._model_provider

    def set_model_provider(self, provider: ModelProvider) -> None:
        self._model_provider = provider
        if hasattr(provider, "config"):
            self._provider_config = provider.config

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _label_for(status: str) -> str:
        labels: Dict[str, str] = {
            VERIFIED: "Verified",
            BLOCKED: "Blocked",
            REVIEW_REQUIRED: "Review Required",
            MODEL_UNAVAILABLE: "Model Unavailable",
            VALIDATION_FAILED: "Validation Failed",
            GROUNDING_FAILED: "Grounding Failed",
            FORBIDDEN_OUTPUT: "Forbidden Output",
            UNSUPPORTED_TRANSACTION: "Unsupported Transaction",
        }
        return labels.get(status, status)

    @staticmethod
    def _next_action_for(status: str) -> str:
        actions: Dict[str, str] = {
            MODEL_UNAVAILABLE: "Check model availability and try again.",
            VALIDATION_FAILED: "Review the model output and retry.",
            GROUNDING_FAILED: "Review the input and retry.",
            FORBIDDEN_OUTPUT: "Model output was rejected.",
            UNSUPPORTED_TRANSACTION: "Rephrase as a supported transaction.",
        }
        return actions.get(status, "")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def process(self, raw_input: str, *, request_id: Optional[str] = None) -> KernelResult:
        """
        Process one student input through the Kernel boundary.

        sequence:
          raw_input
            ↓
          ModelProvider.interpret()
            ↓
          schema validation
            ↓
          grounding/verification
            ↓
          deterministic accounting processing
            ↓
          KernelResult
        """
        request_id = request_id or "kernel:" + str(hash(raw_input) & 0xFFFFFFFF)

        if not raw_input or not raw_input.strip():
            return self._failed(
                status=VALIDATION_FAILED,
                request_id=request_id,
                raw_input=raw_input,
                issues=["Empty input"],
                next_action="Type a transaction description.",
            )

        provider = self.model_provider()

        # 1. Model availability without loading if possible.
        #
        # Fail closed ONLY on a hard provider failure: missing dependencies,
        # invalid configuration, or a previously failed load attempt. A
        # provider that is merely "not currently loaded" but loadable is NOT
        # rejected here — the request path below reaches provider.interpret(),
        # which attempts ensure_loaded() → _load_model(), and every genuine
        # load failure still maps to MODEL_UNAVAILABLE (fail-closed).
        #
        # loadable is an optional ProviderStatus field; getattr with a False
        # default preserves fail-closed behavior for stubs/providers that do
        # not report it (existing Phase 7C stub contract is unchanged).
        status = provider.status()
        if status.model_unavailable and not getattr(status, "loadable", False):
            return self._failed(
                status=MODEL_UNAVAILABLE,
                request_id=request_id,
                raw_input=raw_input,
                issues=[status.reason or "model not available"],
                next_action="Check model availability and try again.",
                metadata={"provider_status": status.to_dict()},
            )

        # 2. Semantic interpretation candidate.
        try:
            interpretation = provider.interpret(raw_input)
        except ModelUnavailableError as e:
            return self._failed(
                status=MODEL_UNAVAILABLE,
                request_id=request_id,
                raw_input=raw_input,
                issues=[str(e)],
                next_action="Check model availability and try again.",
            )
        except MalformedOutputError as e:
            return self._failed(
                status=VALIDATION_FAILED,
                request_id=request_id,
                raw_input=raw_input,
                issues=["malformed model output: " + str(e)],
                next_action="Review the model output and retry.",
            )
        except ForbiddenAccountingFieldError as e:
            return self._failed(
                status=FORBIDDEN_OUTPUT,
                request_id=request_id,
                raw_input=raw_input,
                issues=["forbidden accounting output: " + str(e)],
                next_action="Model output was rejected.",
            )
        except Exception as e:
            return self._failed(
                status=MODEL_UNAVAILABLE,
                request_id=request_id,
                raw_input=raw_input,
                issues=["provider error: " + str(e)],
                next_action="Check model availability and try again.",
            )

        candidate = interpretation.candidate

        # 3. Schema validation against existing contract.
        validator = self._get_schema_validator()
        report = validator.validate(candidate, allow_expanded=True)
        if not report.valid:
            return self._failed(
                status=VALIDATION_FAILED,
                request_id=request_id,
                raw_input=raw_input,
                interpretation=interpretation,
                issues=["schema validation: " + "; ".join(e.issue for e in report.errors)],
                next_action="Review the model output and retry.",
            )

        # 4. Grounding/verification extension point.
        ground_result = self._ground(candidate, raw_input)
        if not ground_result.safe_for_kernel:
            return self._failed(
                status=GROUNDING_FAILED,
                request_id=request_id,
                raw_input=raw_input,
                interpretation=interpretation,
                verification_status="GROUNDING_FAILED",
                grounding_issues=ground_result.issues,
                issues=["grounding: " + "; ".join(ground_result.issues)],
                next_action="Review the input and retry.",
            )

        # 5. Deterministic accounting processing.
        #
        # The existing flow is the authority for the accounting outcome. The
        # Kernel propagates the flow's own status (VERIFIED, REVIEW_REQUIRED,
        # BLOCKED, ...) instead of upgrading it: a REVIEW_REQUIRED decision
        # from the deterministic implementation must never be relabelled as
        # a trusted VERIFIED result at the Kernel boundary.
        accounting_result = self.process_accounting(candidate, raw_input)
        if accounting_result is None:
            return self._failed(
                status=UNSUPPORTED_TRANSACTION,
                request_id=request_id,
                raw_input=raw_input,
                interpretation=interpretation,
                issues=["unsupported transaction"],
                next_action="Rephrase as a supported transaction.",
            )

        flow_status = accounting_result.get("status")
        if not isinstance(flow_status, str) or not flow_status:
            # Defensive: the existing implementation always sets a status.
            # If that invariant ever breaks, fail closed rather than assume.
            return self._failed(
                status=UNSUPPORTED_TRANSACTION,
                request_id=request_id,
                raw_input=raw_input,
                interpretation=interpretation,
                issues=["accounting flow returned no status"],
                next_action="Rephrase as a supported transaction.",
            )

        return KernelResult(
            request_id=request_id,
            raw_input=raw_input,
            status=flow_status,
            status_label=self._label_for(flow_status),
            interpretation=interpretation,
            verification_status="GROUNDED",
            accounting_result=accounting_result,
            next_action="",
            metadata={
                "model_id": interpretation.model_id,
                "provider_revision": interpretation.provider_revision,
            },
        )

    # ------------------------------------------------------------------
    # Grounding / verification extension point
    # ------------------------------------------------------------------

    def _ground(self, candidate: Dict[str, Any], raw_input: str):
        gate = self._get_ground_gate()
        return gate.ground(candidate, raw_input)

    # ------------------------------------------------------------------
    # Accounting delegation
    # ------------------------------------------------------------------

    def process_accounting(
        self, candidate: Dict[str, Any], raw_input: str
    ) -> Optional[Dict[str, Any]]:
        """
        Delegate to the existing deterministic accounting implementation.

        This preserves existing behavior. It does not invent a second
        accounting engine. It returns the accounting result dict or None
        when the transaction is explicitly unsupported by the existing
        implementation.
        """
        accounting = self._get_accounting()
        # Present the validated semantic facts to the existing accounting
        # flow in the form it already expects: a description plus optional
        # resolved amounts/parties derived from the interpretation when
        # available. We keep this intentionally thin so the Kernel does not
        # become a second interpreter.
        description = _best_description(candidate, raw_input)
        amount = _best_amount(candidate, raw_input)
        result = accounting.process(description, amount)
        if result is None:
            return None
        return result

    # ------------------------------------------------------------------
    # Result helpers
    # ------------------------------------------------------------------

    def _failed(
        self,
        *,
        status: str,
        request_id: Optional[str],
        raw_input: str,
        issues: List[str],
        next_action: str,
        interpretation: Optional[InterpretationResult] = None,
        verification_status: Optional[str] = None,
        grounding_issues: Optional[List[str]] = None,
        accounting_result: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> KernelResult:
        return KernelResult(
            request_id=request_id,
            raw_input=raw_input,
            status=status,
            status_label=self._label_for(status),
            interpretation=interpretation,
            verification_status=verification_status,
            grounding_issues=grounding_issues,
            accounting_result=accounting_result,
            issues=issues,
            next_action=next_action,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Lazy domain accessors
    # ------------------------------------------------------------------

    def _get_accounting(self):
        if self._accounting is None:
            self._accounting = _ExistingAccountingAdapter()
        return self._accounting

    def _get_schema_validator(self):
        if self._schema_validator is None:
            from backend.maths.schema_verifier import StructuredInterpretationValidator

            self._schema_validator = StructuredInterpretationValidator()
        return self._schema_validator

    def _get_ground_gate(self):
        if self._grounding_gate is None:
            from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate

            self._grounding_gate = ExpandedGroundingGate()
        return self._grounding_gate


# ---------------------------------------------------------------------------
# Existing deterministic accounting adapter
# ---------------------------------------------------------------------------

class _ExistingAccountingAdapter:
    """
    Thin adapter that exposes the existing deterministic accounting
    implementation to the Kernel.

    This is intentionally a thin wrapper. It does not duplicate accounting
    rules. It does not reimplement debit/credit, journal, ledger, or
    trial-balance logic.

    The existing authoritative flow today is:

      hardened_bookkeeping_outcome -> orchestrate -> existing bk reasoning

    This adapter uses exactly that path and returns the existing result
    shape, so the Kernel does not replace the existing implementation.
    """

    def __init__(self) -> None:
        self._outcome_fn = None

    def process(self, description: str, amount: Any = None) -> Optional[Dict[str, Any]]:
        """
        Return the existing implementation's outcome dict unchanged.

        The Kernel result carries the authoritative existing shape as-is
        (status, debit_lines, credit_lines, why_not, calculation_records,
        bills, consignment, joint_venture, discrepancy, ...). Whitelisting
        or reshaping fields here would risk silently dropping information
        the API/UI may need, so this is a shallow-copy passthrough.
        """
        outcome = self._outcome(description, amount)
        if outcome is None:
            return None
        return dict(outcome)

    def _outcome(self, description: str, amount: Any = None) -> Optional[Dict[str, Any]]:
        if self._outcome_fn is None:
            from backend.maths.fyjc_accounting import hardened_bookkeeping_outcome

            self._outcome_fn = hardened_bookkeeping_outcome
        return self._outcome_fn(description, amount)


# ---------------------------------------------------------------------------
# Very thin helpers to preserve existing behavior without reinterpretation
# ---------------------------------------------------------------------------

def _best_description(candidate: Dict[str, Any], raw_input: str) -> str:
    """
    Return the description used for accounting processing.

    We prefer the raw student input because that is what the existing
    deterministic implementation is built around and tested against.
    The interpretation candidate is metadata, not a replacement for the
    original description.
    """
    return raw_input


def _best_amount(candidate: Dict[str, Any], raw_input: str) -> Any:
    """
    Return an optional resolved amount from the interpretation candidate
    when that amount is actually grounded in the original student input.

    The grounding gate requires amounts to be supported by the source text,
    so we only forward a candidate amount when its numeric value appears in
    the original input. Otherwise we pass None and let the existing
    deterministic accounting flow detect the amount itself from the
    description.

    This is intentionally conservative: we do not invent amounts and we do
    not forward values that cannot be traced back to the student's text.
    """
    amounts = candidate.get("amounts")
    if not isinstance(amounts, list) or not amounts:
        return None

    for entry in amounts:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if value is None:
            continue
        if isinstance(value, (int, float)):
            value = str(value)
        if value and value in raw_input:
            return value

    return None


# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------

__all__ = [
    "Kernel",
    "KernelResult",
    "MODEL_UNAVAILABLE",
    "VALIDATION_FAILED",
    "GROUNDING_FAILED",
    "FORBIDDEN_OUTPUT",
    "UNSUPPORTED_TRANSACTION",
]
