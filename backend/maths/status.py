"""
Platrixa
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/status.py

Six-tier truth status system:

    VERIFIED          directly supported by accepted source evidence
    DERIVED           calculated exclusively from valid dependencies
    RECONCILED        obtained through a documented accounting
                      reconciliation / cross-statement relationship
    STUDENT_INPUT     explicitly entered by the user/student
    REVIEW_REQUIRED   discrepancy, ambiguity, unsupported adjustment,
                      or conflicting evidence exists
    BLOCKED           required information unavailable / invalid /
                      incompatible / mathematically undefined

Propagation rules (deterministic, weakest-link):
  * A downstream result NEVER claims stronger provenance than its weakest
    required dependency permits.
  * BLOCKED propagates upward: a blocked dependency prevents computation.
  * REVIEW_REQUIRED never silently becomes VERIFIED or DERIVED.
  * STUDENT_INPUT / RECONCILED also propagate upward when present.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

VERIFIED = "VERIFIED"
DERIVED = "DERIVED"
RECONCILED = "RECONCILED"
STUDENT_INPUT = "STUDENT_INPUT"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BLOCKED = "BLOCKED"

# All six statuses, weakest-first ordering used for propagation. BLOCKED
# (rank 0) is the weakest: any dependency that is BLOCKED forces the
# downstream quantity to be BLOCKED.
STATUS_RANK = {
    BLOCKED: 0,
    REVIEW_REQUIRED: 1,
    STUDENT_INPUT: 2,
    RECONCILED: 3,
    DERIVED: 4,
    VERIFIED: 5,
}

ALL_STATUSES = (
    VERIFIED,
    DERIVED,
    RECONCILED,
    STUDENT_INPUT,
    REVIEW_REQUIRED,
    BLOCKED,
)

# Friendly labels (mirroring the Platrixa status-label convention).
STATUS_LABELS = {
    VERIFIED: "🟢 VERIFIED",
    DERIVED: "🔵 DERIVED",
    RECONCILED: "🟣 RECONCILED",
    STUDENT_INPUT: "🟡 STUDENT_INPUT",
    REVIEW_REQUIRED: "🟠 REVIEW_REQUIRED",
    BLOCKED: "🔴 BLOCKED",
}

# Statuses that prevent a downstream computation from running at all.
HARD_BLOCK_STATUSES = frozenset({BLOCKED})

# Statuses that never silently upgrade to VERIFIED / DERIVED.
SOFT_BLOCK_STATUSES = frozenset({REVIEW_REQUIRED, STUDENT_INPUT, RECONCILED})


def is_status(value: str) -> bool:
    return str(value or "") in STATUS_RANK


def rank_of(status: str) -> int:
    """Deterministic weakness rank; unknown statuses rank as BLOCKED
    (fail closed - never assume an unknown status is trustworthy)."""
    return STATUS_RANK.get(str(status or ""), STATUS_RANK[BLOCKED])


def weaker(status_a: str, status_b: str) -> str:
    """Return the weaker of two statuses (deterministic)."""
    return status_a if rank_of(status_a) <= rank_of(status_b) else status_b


def propagate_statuses(statuses: Iterable[Optional[str]]) -> str:
    """Weakest-link propagation for a computed quantity.

    * any BLOCKED input                -> BLOCKED
    * else any REVIEW_REQUIRED input   -> REVIEW_REQUIRED
    * else any STUDENT_INPUT input     -> STUDENT_INPUT
    * else any RECONCILED input        -> RECONCILED
    * else (all inputs VERIFIED)       -> DERIVED
    * no inputs at all                 -> DERIVED (vacuously computable)

    A computed quantity can never be VERIFIED: only facts directly
    supported by evidence are VERIFIED.
    """
    ranks = [rank_of(s) for s in statuses if s is not None]
    if not ranks:
        return DERIVED
    weakest = min(ranks)
    # A computed quantity can never be VERIFIED: only facts directly
    # supported by evidence are VERIFIED. All-VERIFIED inputs produce a
    # DERIVED result.
    if weakest >= STATUS_RANK[VERIFIED]:
        return DERIVED
    for status, rank in STATUS_RANK.items():
        if rank == weakest:
            return status
    return DERIVED


def merge_status(status_a: Optional[str], status_b: Optional[str]) -> str:
    """Merge two statuses (weakest wins)."""
    return weaker(
        str(status_a or ""),
        str(status_b or ""),
    )


def is_computable(status: str) -> bool:
    """True when a fact with this status may feed a deterministic
    calculation (BLOCKED facts may not)."""
    return rank_of(status) > STATUS_RANK[BLOCKED]


# ---------------------------------------------------------------------------
# Platrixa compatibility
# ---------------------------------------------------------------------------

# Mapping from the existing Formula Engine status vocabulary to the
# six-tier model (used by the adapter when translating pipeline facts).
PIPELINE_TO_SIX_TIER = {
    "reported": VERIFIED,
    "derived": DERIVED,
    "external_derived": DERIVED,
    "blocked": BLOCKED,
    "unanalyzed": BLOCKED,  # no verified value -> fail closed
    "verified": VERIFIED,
}

# Provenance-tier based inference (same semantics as the existing
# student_workspace resolve_metric_status layer).
from backend.evidence_resolver import PROVENANCE_TIER  # noqa: E402


def status_from_provenance(provenance_tier: Optional[str],
                           extraction_state: Optional[str] = None) -> str:
    """Infer the six-tier status of a raw fact from its provenance tier
    and extraction reliability state. Deterministic; never upgrades an
    uncertain fact."""
    if extraction_state and str(extraction_state).lower() in (
        "review_required", "conflict", "unresolved_conflict",
    ):
        return REVIEW_REQUIRED
    tier = str(provenance_tier or "").upper()
    if tier == PROVENANCE_TIER.DOCUMENT:
        return VERIFIED
    if tier == PROVENANCE_TIER.APPENDIX:
        return VERIFIED
    if tier == PROVENANCE_TIER.REGULATORY_API:
        return VERIFIED  # approved structured provider evidence
    if tier == PROVENANCE_TIER.DERIVED:
        return DERIVED
    if tier == PROVENANCE_TIER.EXTERNAL_DERIVED:
        return DERIVED
    if tier == "STUDENT_INPUT":
        return STUDENT_INPUT
    if tier == PROVENANCE_TIER.BLOCKED:
        return BLOCKED
    return BLOCKED  # unanalyzed / unknown -> fail closed


def propagate_blocked_reason(target: str, blocked_inputs: List[str]) -> str:
    """Deterministic reason when a computation is blocked by its inputs."""
    if not blocked_inputs:
        return f"{target} is blocked: required information is unavailable."
    return (
        f"{target} is blocked because required dependency"
        + ("ies " if len(blocked_inputs) > 1 else " ")
        + ", ".join(sorted(blocked_inputs))
        + " is unavailable, invalid, or incompatible."
    )
