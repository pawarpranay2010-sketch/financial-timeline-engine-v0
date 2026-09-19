"""
Platrixa
Authority Expansion (2026-09) - Capability Registry
backend/maths/capability_registry.py

The authoritative map of "what can Platrixa currently prove and execute"
across the THREE separated authorities:

    ACCOUNTING_KERNEL       deterministic accounting operations
    FORMULA_AUTHORITY       deterministic mathematical/financial calculation
    FINANCE_KNOWLEDGE       verified financial concepts/context (PLANNED)

This module is a REGISTRY, not an execution engine. It never computes
anything; it describes implemented capability and points at the code and
tests that prove each entry. Derivation over duplication:

* FORMULA_AUTHORITY entries are DERIVED from the live extended formula
  registry (EXTENDED_REGISTRY) at import time - a formula added there in
  a future batch appears here automatically and the two views cannot
  drift. No formula metadata is copied.
* ACCOUNTING_KERNEL entries are DERIVED from the orchestrator's
  AUTHORITIES registry plus explicit UNSUPPORTED records for
  known-refused topics (each carries the refusal evidence).
* FINANCE_KNOWLEDGE holds no SUPPORTED entry by construction - the
  authority does not exist yet, and the registry fails closed: a model
  generated financial claim can never route to a SUPPORTED knowledge
  capability that is not registered here.

Statuses: SUPPORTED (implemented + tests), PARTIAL (implemented with a
documented boundary), UNSUPPORTED (provably refused), PLANNED (declared,
not implemented - never executable).

Pure module: no I/O, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.maths.extended_registry import (
    EXTENDED_FORMULA_METADATA,
    EXTENDED_REGISTRY,
)

# ---------------------------------------------------------------------------
# Authority + status vocabulary (canonical, fail-closed)
# ---------------------------------------------------------------------------

AUTHORITY_ACCOUNTING_KERNEL = "ACCOUNTING_KERNEL"
AUTHORITY_FORMULA = "FORMULA_AUTHORITY"
AUTHORITY_FINANCE_KNOWLEDGE = "FINANCE_KNOWLEDGE"

_AUTHORITIES = frozenset({
    AUTHORITY_ACCOUNTING_KERNEL,
    AUTHORITY_FORMULA,
    AUTHORITY_FINANCE_KNOWLEDGE,
})

_STATUS_SUPPORTED = "SUPPORTED"
_STATUS_PARTIAL = "PARTIAL"
_STATUS_UNSUPPORTED = "UNSUPPORTED"
_STATUS_PLANNED = "PLANNED"

_STATUSES = frozenset({
    _STATUS_SUPPORTED, _STATUS_PARTIAL,
    _STATUS_UNSUPPORTED, _STATUS_PLANNED,
})

# capability_id prefix per authority - the id namespace is authoritative
# so an id can never silently migrate between authorities.
_ID_PREFIX = {
    AUTHORITY_ACCOUNTING_KERNEL: "KERNEL.",
    AUTHORITY_FORMULA: "FORMULA.",
    AUTHORITY_FINANCE_KNOWLEDGE: "KNOWLEDGE.",
}


class RegistrationError(ValueError):
    """A capability registration violated the registry contract."""


# ---------------------------------------------------------------------------
# Capability record (compact, data-driven; no prose blobs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    capability_id: str
    authority: str
    canonical_name: str
    supported_status: str
    description: str = ""
    required_inputs: Tuple[str, ...] = ()
    deterministic_op: str = ""       # calculation / treatment reference
    implementation_ref: str = ""     # module (path / symbol) that executes it
    test_ref: str = ""               # deterministic test that proves it
    source_ref: str = ""             # provenance (definition source)
    jurisdiction: str = "general"
    framework: str = ""
    version: str = "1.0"
    limitations: Tuple[str, ...] = field(default_factory=tuple)

    def to_metadata(self) -> Dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "authority": self.authority,
            "canonical_name": self.canonical_name,
            "supported_status": self.supported_status,
            "description": self.description,
            "required_inputs": list(self.required_inputs),
            "deterministic_op": self.deterministic_op,
            "implementation_ref": self.implementation_ref,
            "test_ref": self.test_ref,
            "source_ref": self.source_ref,
            "jurisdiction": self.jurisdiction,
            "framework": self.framework,
            "version": self.version,
            "limitations": list(self.limitations),
        }


# ---------------------------------------------------------------------------
# Registry store + contract-checked registration
# ---------------------------------------------------------------------------

CAPABILITIES: Dict[str, Capability] = {}


def register(capability: Capability) -> Capability:
    """Register one capability under the fail-closed registry contract."""
    if capability.capability_id in CAPABILITIES:
        raise RegistrationError(
            f"Capability {capability.capability_id!r} is already registered."
        )
    if capability.authority not in _AUTHORITIES:
        raise RegistrationError(
            f"Capability {capability.capability_id!r}: unknown authority "
            f"{capability.authority!r}."
        )
    if capability.supported_status not in _STATUSES:
        raise RegistrationError(
            f"Capability {capability.capability_id!r}: invalid status "
            f"{capability.supported_status!r}."
        )
    prefix = _ID_PREFIX[capability.authority]
    if not capability.capability_id.startswith(prefix):
        raise RegistrationError(
            f"Capability {capability.capability_id!r}: ids for authority "
            f"{capability.authority} must start with {prefix!r}."
        )
    # Provenance presence: an executable claim must name its proof.
    if capability.supported_status in (_STATUS_SUPPORTED, _STATUS_PARTIAL):
        if not capability.implementation_ref or not capability.test_ref:
            raise RegistrationError(
                f"Capability {capability.capability_id!r}: "
                f"{capability.supported_status} requires implementation_ref "
                "and test_ref (registry entries must be provable)."
            )
    if capability.supported_status == _STATUS_UNSUPPORTED \
            and not capability.limitations:
        raise RegistrationError(
            f"Capability {capability.capability_id!r}: UNSUPPORTED requires "
            "at least one documented limitation/refusal evidence."
        )
    CAPABILITIES[capability.capability_id] = capability
    return capability


def get(capability_id: str) -> Optional[Capability]:
    return CAPABILITIES.get(capability_id)


def by_authority(authority: str) -> List[Capability]:
    return sorted(
        (c for c in CAPABILITIES.values() if c.authority == authority),
        key=lambda c: c.capability_id,
    )


def by_status(status: str) -> List[Capability]:
    return sorted(
        (c for c in CAPABILITIES.values() if c.supported_status == status),
        key=lambda c: c.capability_id,
    )


def summary() -> Dict[str, Dict[str, int]]:
    """Authority x status counts (the capability matrix at a glance)."""
    out: Dict[str, Dict[str, int]] = {}
    for authority in sorted(_AUTHORITIES):
        out[authority] = {s: 0 for s in sorted(_STATUSES)}
    for c in CAPABILITIES.values():
        out[c.authority][c.supported_status] += 1
    return out


# ---------------------------------------------------------------------------
# FORMULA_AUTHORITY - derived from the live extended registry (no copies)
# ---------------------------------------------------------------------------

_FORMULA_IMPL_REF = (
    "backend/maths/formula_registry.py (definitions) + "
    "backend/maths/solver.py + C++ authority formula_engine/formula_engine.cpp"
)
_FORMULA_TEST_REF = (
    "scripts/fte_maths_student_production_gate_test.py; "
    "scripts/fte_authority_formula_phase_e_test.py"
)

# Forward-only growth policy: the legacy C++ growth entries compute the
# current/prior form with no registered inverse; the negative-base sign
# interpretation is a documented limitation, never silently reinterpreted.
_GROWTH_LIMITATIONS = (
    "forward-only: no reverse solve is registered (C++ growth policy)",
    "a non-positive prior-period base passes the zero check; the sign "
    "meaning of growth from a negative base is a documented limitation",
)


def _register_formula_capabilities() -> None:
    for fid in sorted(EXTENDED_REGISTRY.all_ids()):
        d = EXTENDED_REGISTRY.get(fid)
        if d is None:  # pragma: no cover - registry invariant
            continue
        meta = EXTENDED_FORMULA_METADATA.get(fid) or {}
        limitations: Tuple[str, ...] = ()
        if d.period_mode == "different":
            limitations = _GROWTH_LIMITATIONS
        register(Capability(
            capability_id=f"FORMULA.{fid}",
            authority=AUTHORITY_FORMULA,
            canonical_name=meta.get("name", d.target),
            supported_status=_STATUS_SUPPORTED,
            description=d.description,
            required_inputs=tuple(d.dependencies),
            deterministic_op=d.expression,
            implementation_ref=_FORMULA_IMPL_REF,
            test_ref=_FORMULA_TEST_REF,
            source_ref=d.source_ref,
            version=d.version,
            limitations=limitations,
        ))


# ---------------------------------------------------------------------------
# ACCOUNTING_KERNEL - derived from the orchestrator authority registry
# ---------------------------------------------------------------------------

_KERNEL_IMPL_REF = "backend/maths/fyjc_orchestration.py (AUTHORITIES)"
_KERNEL_TEST_REF = (
    "scripts/fte_fyjc_bk_p0_test.py; "
    "scripts/fte_authority_expansion_batch1_test.py"
)


def _register_kernel_capabilities() -> None:
    # Delayed import avoids a circular import at module load time
    # (fyjc_orchestration pulls the reasoning engine).
    from backend.maths.fyjc_orchestration import AUTHORITIES

    for aid, meta in sorted(AUTHORITIES.items()):
        implemented = bool(meta.get("implemented"))
        register(Capability(
            capability_id=f"KERNEL.{aid}",
            authority=AUTHORITY_ACCOUNTING_KERNEL,
            canonical_name=str(meta.get("name", aid)),
            supported_status=(
                _STATUS_SUPPORTED if implemented else _STATUS_PLANNED
            ),
            description=str(meta.get("scope", "")),
            implementation_ref=_KERNEL_IMPL_REF if implemented else "",
            test_ref=_KERNEL_TEST_REF if implemented else "",
            source_ref="FYJC Book-Keeping & Accountancy (Maharashtra "
                       "State Bureau of Textbook Production), Ch.1-3 "
                       "transaction semantics",
            framework="FYJC Book-Keeping",
            jurisdiction="IN",
            limitations=() if implemented
            else ("declared in the orchestrator registry, not implemented "
                  "- routing to it fails closed"),
        ))

    # Explicit UNSUPPORTED records: topics the kernel provably refuses
    # (refusal evidence pinned by the batch-1 gate / boundary suites).
    for unsupported in (
        Capability(
            capability_id="KERNEL.DEPRECIATION",
            authority=AUTHORITY_ACCOUNTING_KERNEL,
            canonical_name="Depreciation adjustment",
            supported_status=_STATUS_UNSUPPORTED,
            description=("Periodic depreciation adjustment entries; the "
                         "kernel refuses with NOT_SUPPORTED and never "
                         "guesses a split or rate."),
            implementation_ref=_KERNEL_IMPL_REF,
            test_ref="scripts/fte_authority_expansion_batch1_test.py",
            framework="FYJC Book-Keeping",
            jurisdiction="IN",
            limitations=(
                "reason_bk_question returns NOT_SUPPORTED for depreciation "
                "wordings; the ADJUSTMENT_AUTHORITY (PLANNED) owns this "
                "topic once implemented",
            ),
        ),
        Capability(
            capability_id="KERNEL.MULTI_CURRENCY_FX",
            authority=AUTHORITY_ACCOUNTING_KERNEL,
            canonical_name="Multi-currency / FX treatment",
            supported_status=_STATUS_UNSUPPORTED,
            description=("Foreign-currency transaction treatment "
                         "(exchange rates, realized/unrealized FX "
                         "differences). No FX semantics are implemented."),
            implementation_ref=_KERNEL_IMPL_REF,
            test_ref="scripts/fte_authority_expansion_batch1_test.py",
            framework="FYJC Book-Keeping",
            jurisdiction="IN",
            limitations=(
                "currency symbols in input are parsed to bare numbers; "
                "the resulting journal carries NO currency semantics - "
                "explicit FX treatment is never applied",
            ),
        ),
    ):
        register(unsupported)


# ---------------------------------------------------------------------------
# FINANCE_KNOWLEDGE - intentionally EMPTY of executable entries
# ---------------------------------------------------------------------------


def _register_knowledge_capabilities() -> None:
    """Phase F: the Finance Knowledge Authority is now implemented and
    tested (backend/maths/finance_knowledge.py, 27 verified records with
    mandatory provenance + 1 honestly UNVERIFIED), so the Phase C root
    flips PLANNED -> SUPPORTED and the verified foundation is registered.
    The capability DERIVES from the live knowledge authority - it does not
    restate its metadata; the Phase F gate checks the correspondence.
    """
    from backend.maths.finance_knowledge import build_knowledge_authority

    _authority = build_knowledge_authority()
    _status_counts = _authority.counts_by_status()
    register(Capability(
        capability_id="KNOWLEDGE.AUTHORITY_ROOT",
        authority=AUTHORITY_FINANCE_KNOWLEDGE,
        canonical_name="Verified Finance Knowledge Authority",
        supported_status=_STATUS_SUPPORTED,
        description=("Verified financial concepts, definitions, terminology, "
                     "relationships and rule references with mandatory "
                     "provenance (FACT vs CALCULATION vs ACCOUNTING RULE vs "
                     "INTERPRETATION distinction enforced). Deterministic "
                     "registry only: it never executes accounting or "
                     "calculation, so model-generated financial claims can "
                     "never become authority through it."),
        implementation_ref="backend/maths/finance_knowledge.py:KnowledgeAuthority",
        test_ref="scripts/fte_authority_phase_f_test.py",
        source_ref=("IASB IAS 1/IAS 7/IAS 37 (verified via published "
                    "scope summaries); BCBS/Basel II risk families (BIS)"),
        jurisdiction="international",
        framework="IFRS/IAS; Basel",
        version="1.0",
        limitations=(
            f"verified foundation of {_status_counts.get('SUPPORTED', 0)} "
            "records from 4 verified source families; compact by design",
            "one record (Ind AS mapping) honestly UNVERIFIED - primary "
            "MCA source not verifiable at registration time",
            "retrieval is deterministic registry lookup, not semantic search",
        ),
    ))
    register(Capability(
        capability_id="KNOWLEDGE.FS_PURPOSE_ELEMENTS",
        authority=AUTHORITY_FINANCE_KNOWLEDGE,
        canonical_name="Financial statement purpose and elements",
        supported_status=_STATUS_SUPPORTED,
        description=("Knowledge authority answers what financial statements "
                     "are for and which elements they present: structured "
                     "information about financial position, performance and "
                     "cash flows, categorised into assets, liabilities, "
                     "income, expenses, owner contributions/distributions "
                     "and cash flows (verified record fk_fs_purpose + "
                     "fk_fs_elements). The amounts and postings themselves "
                     "remain owned by the Formula Authority and the "
                     "Accounting Kernel respectively."),
        required_inputs=("knowledge_id", "topic", "knowledge_type"),
        deterministic_op="KnowledgeAuthority.query/get (deterministic registry lookup)",
        implementation_ref="backend/maths/finance_knowledge.py:build_knowledge_authority",
        test_ref="scripts/fte_authority_phase_f_test.py",
        source_ref=("IASB (IFRS Foundation), IAS 1 Presentation of "
                    "Financial Statements - published scope summary "
                    "cross-checked 2026-09-19"),
        jurisdiction="international",
        framework="IFRS/IAS",
        version="1.0",
        limitations=(
            "concept-level knowledge only; never executes postings or "
            "calculations",
            "authoritative standard text is IFRS-Foundation-licensed; only "
            "concise interpretations are stored",
        ),
    ))


_register_formula_capabilities()
_register_kernel_capabilities()
_register_knowledge_capabilities()
