"""
Platrixa — Deterministic Kernel boundary (Phase 7C)

This package is the application-level orchestration boundary for financial
transaction processing. (It evolved out of the early FYJC student-practice
slice; FYJC-style accounting language is one evaluated domain, not the
product's overall scope.)

Target architecture (Phase 7C establishes the wiring; deeper grounding/
verification connection is intentionally deferred to Phase 7D):

    Transaction Input
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

# Phase 17: typed semantic IR boundary. The model's output is a CANDIDATE
# semantic IR; only deterministic grounding can convert it into a
# GROUNDED semantic IR, and deterministic accounting consumes ONLY the
# grounded representation. The Kernel wires these together — it performs
# no grounding or accounting of its own.
from backend.semantics import (
    CandidateSemanticIR,
    GroundedSemanticIR,
    SCHEMA_VERSION,
    SemanticIRError,
)
from backend.semantics.evidence import (
    ExecutionEvidence,
    prompt_identity,
    rule_pack_identity,
    sha256_of_text,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reuse existing accounting status vocabulary where possible.
# ---------------------------------------------------------------------------
# We keep the existing FYJC-lineage status words for accounting-authority outcomes
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
        rule_evidence: Optional[List[Dict[str, Any]]] = None,
        evidence: Optional["ExecutionEvidence"] = None,
        candidate_ir: Optional["CandidateSemanticIR"] = None,
        grounded_ir: Optional["GroundedSemanticIR"] = None,
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
        # Phase 10: structured evidence from the developer rule boundary
        # (empty when no rule pack is configured).
        self.rule_evidence = rule_evidence or []
        # Phase 17: versioned execution evidence chain binding this result to
        # the actual configuration that produced it, plus the typed IRs the
        # runtime actually passed between boundaries.
        self.evidence = evidence
        self.candidate_ir = candidate_ir
        self.grounded_ir = grounded_ir

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
            "rule_evidence": self.rule_evidence,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }


# ---------------------------------------------------------------------------
# Kernel
# ---------------------------------------------------------------------------

class Kernel:
    """
    Application-level deterministic kernel boundary for financial transactions.

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
        rule_pack: Optional[str] = None,
        rule_hooks: Any = None,
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

        # Phase 10: optional developer rule boundary (default OFF — behavior
        # is byte-identical when neither a YAML rule pack nor hooks are
        # provided). Rules may only downgrade a deterministic success state;
        # see backend/rules/engine.py for the authority invariant.
        self._rule_pack_path = rule_pack
        self._rule_hooks = tuple(rule_hooks) if rule_hooks else ()
        self._rule_engine: Optional[Any] = None
        # Fail closed at construction: a malformed rule pack must prevent the
        # Kernel from being built at all rather than surfacing on the first
        # request
        if self._rule_pack_path or self._rule_hooks:
            self._get_rule_engine()

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

        # 2.5 Phase 17: bind the model output as a typed CANDIDATE semantic
        # IR. This is the model's PROPOSED meaning — it has no authority and
        # cannot reach accounting. Construction fails closed on any
        # accounting-truth fields smuggled into the candidate.
        try:
            candidate_ir = CandidateSemanticIR(
                raw_input=raw_input,
                fields=candidate,
                model_id=interpretation.model_id,
                model_revision=interpretation.provider_revision,
            )
        except SemanticIRError as e:
            return self._failed(
                status=FORBIDDEN_OUTPUT,
                request_id=request_id,
                raw_input=raw_input,
                interpretation=interpretation,
                issues=["candidate IR rejected: " + str(e)],
                next_action="Model output was rejected.",
            )

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
                candidate_ir=candidate_ir,
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
                candidate_ir=candidate_ir,
            )

        # 4.5 Phase 17: the passing grounding result is the ONLY authority
        # that converts the candidate into a GROUNDED semantic IR. From here
        # accounting sees exclusively the grounded representation.
        try:
            grounded_ir = GroundedSemanticIR.from_candidate(candidate_ir, ground_result)
        except SemanticIRError as e:
            return self._failed(
                status=GROUNDING_FAILED,
                request_id=request_id,
                raw_input=raw_input,
                interpretation=interpretation,
                verification_status="GROUNDING_FAILED",
                grounding_issues=[str(e)],
                issues=["grounded IR construction refused: " + str(e)],
                next_action="Review the input and retry.",
            )

        # 5. Deterministic accounting processing.
        #
        # The existing flow is the authority for the accounting outcome. The
        # Kernel propagates the flow's own status (VERIFIED, REVIEW_REQUIRED,
        # BLOCKED, ...) instead of upgrading it: a REVIEW_REQUIRED decision
        # from the deterministic implementation must never be relabelled as
        # a trusted VERIFIED result at the Kernel boundary.
        #
        # Phase 17: accounting consumes ONLY the grounded representation —
        # the grounded IR's admission payload (source text + grounded fields),
        # never the raw candidate. The candidate cannot reach this point.
        accounting_result = self.process_accounting(grounded_ir)
        if accounting_result is None:
            return self._failed(
                status=UNSUPPORTED_TRANSACTION,
                request_id=request_id,
                raw_input=raw_input,
                interpretation=interpretation,
                issues=["unsupported transaction"],
                next_action="Rephrase as a supported transaction.",
                candidate_ir=candidate_ir,
                grounded_ir=grounded_ir,
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

        # 6. Developer rule boundary (Phase 10, optional, default off).
        #
        # Runs ONLY on accounting-produced success states, so rules can
        # never repair a failure or manufacture a success. The engine's
        # policy is downgrade-only: a blocking rule outcome downgrades
        # VERIFIED to REVIEW_REQUIRED/BLOCKED per its decision hint; no
        # decision can upgrade any state or produce VERIFIED.
        rule_evidence: List[Dict[str, Any]] = []
        flow_label = self._label_for(flow_status)
        engine = self._get_rule_engine()
        if engine is not None:
            from backend.rules.contract import RuleContext

            context = RuleContext(
                request_id=request_id,
                raw_input=raw_input,
                interpretation=candidate,
            )
            flow_status, flow_label, rule_results = engine.evaluate(
                context, status=flow_status, status_label=self._label_for(flow_status)
            )
            rule_evidence = [r.to_dict() for r in rule_results]

        # 7. Phase 17 execution evidence: bind THIS result to the actual
        # configuration that executed. Every value is captured from live
        # runtime objects after processing — never asserted independently
        # (§8: recorded evidence == actual execution).
        evidence = ExecutionEvidence(
            request_id=request_id or "",
            input_hash=sha256_of_text(raw_input),
            candidate_interpretation_hash=candidate_ir.content_digest,
            grounded_interpretation_hash=grounded_ir.content_digest,
            model_identity={
                "model_id": interpretation.model_id,
                "provider_revision": interpretation.provider_revision,
            },
            adapter_identity={
                "adapter_repo_id": getattr(self._provider_config, "adapter_repo_id", "") or "",
                "adapter_revision": getattr(self._provider_config, "adapter_revision", "") or "",
            }
            if self._provider_config is not None
            else {},
            schema_version=SCHEMA_VERSION,
            prompt_version=self._prompt_version(),
            rule_pack_hash=rule_pack_identity(self._rule_pack_path),
            rule_evidence=rule_evidence,
            final_state=flow_status,
        )

        return KernelResult(
            request_id=request_id,
            raw_input=raw_input,
            status=flow_status,
            status_label=flow_label,
            interpretation=interpretation,
            verification_status="GROUNDED",
            accounting_result=accounting_result,
            next_action="",
            metadata={
                "model_id": interpretation.model_id,
                "provider_revision": interpretation.provider_revision,
            },
            rule_evidence=rule_evidence,
            evidence=evidence,
            candidate_ir=candidate_ir,
            grounded_ir=grounded_ir,
        )

    def _prompt_version(self) -> str:
        """Identity of the exact prompt template the runtime executes.

        The production provider (LocalHFModelProvider) receives its prompt
        via the Kernel's provider construction; when a prompt template is
        configured on the provider it is hashed. Providers that manage their
        own default prompt record an empty prompt_version rather than an
        invented identity — no unmeasured hash is ever claimed (§8).
        """
        provider = self._model_provider
        prompt = getattr(provider, "_system_prompt", None) if provider is not None else None
        return prompt_identity(prompt)

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
        self,
        grounded: "GroundedSemanticIR",
    ) -> Optional[Dict[str, Any]]:
        """
        Delegate to the existing deterministic accounting implementation.

        Phase 17 boundary: accounting accepts ONLY a GroundedSemanticIR.
        Passing a CandidateSemanticIR (or any other type) raises at the
        boundary — ``accounting(candidate_ir)`` is not an accepted normal
        execution path. The existing deterministic flow itself is UNCHANGED.
        """
        if not isinstance(grounded, GroundedSemanticIR):
            raise SemanticIRError(
                "process_accounting accepts ONLY a GroundedSemanticIR "
                f"(got {type(grounded).__name__}); ungrounded candidates "
                "cannot reach deterministic accounting"
            )
        accounting = self._get_accounting()
        # Present the grounded semantic facts to the existing accounting
        # flow in the form it already expects: a description plus optional
        # resolved amounts/parties derived from the GROUNDED interpretation
        # (every value here was verified against the source by the grounding
        # gate before this point). We keep this intentionally thin so the
        # Kernel does not become a second interpreter.
        payload = grounded.for_accounting()
        description = _best_description(payload)
        amount = _best_amount(payload)
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
        candidate_ir: Optional["CandidateSemanticIR"] = None,
        grounded_ir: Optional["GroundedSemanticIR"] = None,
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
            candidate_ir=candidate_ir,
            grounded_ir=grounded_ir,
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

    def _get_rule_engine(self) -> Optional[Any]:
        """Lazy rule engine (Phase 10). None when no rule pack/hooks configured."""
        if self._rule_engine is None and (self._rule_pack_path or self._rule_hooks):
            from backend.rules.engine import RuleEngine

            rules = ()
            if self._rule_pack_path:
                from backend.rules.loader import load_yaml_rule_pack

                rules = load_yaml_rule_pack(self._rule_pack_path)  # fails closed on malformed packs
            self._rule_engine = RuleEngine(rules=rules, hooks=self._rule_hooks)
        return self._rule_engine


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

def _best_description(payload: Dict[str, Any]) -> str:
    """
    Return the description used for accounting processing.

    We use the grounded payload's raw_input (the student's own text, which
    deterministic grounding verified the interpretation against) because that
    is what the existing deterministic implementation is built around and
    tested against. The interpretation is metadata, not a replacement for the
    original description.
    """
    return str(payload.get("raw_input") or "")


def _best_amount(payload: Dict[str, Any]) -> Any:
    """
    Return an optional resolved amount from the GROUNDED payload.

    Every amount here already passed deterministic grounding against the
    source text (the grounding gate rejects amounts unsupported by the
    input), so the former textual re-verification of the candidate against
    raw_input is no longer required at this boundary — the grounded IR is
    the admission contract. Amounts are forwarded in order; None lets the
    existing deterministic flow detect the amount itself from the
    description.
    """
    amounts = payload.get("amounts")
    if not isinstance(amounts, list) or not amounts:
        return None

    for entry in amounts:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str) and value.strip():
            return value.strip()

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
