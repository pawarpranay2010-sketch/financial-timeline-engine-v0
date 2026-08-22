"""
Platrixa
Sprint 13 - FYJC Student Maths Readiness
backend/maths/fyjc_maths.py

The production student calculation path (unchanged from Sprint 12F):

    Student Input
      -> Python ingestion / extraction / normalization  (12D)
      -> structured facts / problem representation
      -> existing registry / orchestration               (12A-12F)
      -> C++ Mathematical Authority                      (arithmetic happens here)
      -> structured result
      -> student-facing explanation / refusal

Sprint 13 deliberately adds NO new formulas and NO second maths engine:
only the relationships the existing 12A-12F registries already compute are
supported. Anything else is refused deterministically (UNSUPPORTED /
BLOCKED) with a student-readable explanation - never a guessed value,
never a silent substitution, never a Python fallback calculation.

This module exposes:

  * fyjc_maths_surface()      - the read-only FYJC maths surface (a
                                deterministic view over EXTENDED_REGISTRY)
  * is_supported_metric()     - membership check with student spellings
  * solve_strict()            - the shared C++-authority strict solve used
                                by both the maths and the book-keeping layer
  * verify_maths_answer()     - Question -> student answer -> C++ verify ->
                                correct/incorrect/refusal with explanation
  * student_checklist()       - the Sprint 13 section 15 acceptance wording

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.formula_engine_cpp import cpp_available
from backend.maths.authority import ENGINE_UNAVAILABLE_REASON
from backend.maths.fyjc_canonical import FYJC_FORMULA_REGISTRY
from backend.maths.fyjc_derivation import (
    derivation_refusal_outcome,
    describe_derivation,
    ensure_derivation_valid,
)
from backend.maths.fact_model import build_fact_graph
from backend.maths.normalization import parse_numeric_text
from backend.maths.solver import Solution, Solver, format_value
from backend.maths.status import (
    BLOCKED,
    DERIVED,
    RECONCILED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
    STATUS_LABELS,
)
from backend.maths.student_sandbox import (
    STATUS_WORDS,
    _attach_document_facts,
    _parse_text_facts,
    extract_prose_facts,
)

# ---------------------------------------------------------------------------
# Authority states (same vocabulary as authority.py, plus bookkeeping)
# ---------------------------------------------------------------------------

AUTHORITY_CPP = "cpp"
AUTHORITY_UNSUPPORTED = "unsupported"
AUTHORITY_UNAVAILABLE = "engine_unavailable"

# Sprint 13 statuses are expressed in the existing six-tier system. For the
# FYJC surface the engine ALSO uses this explicit refusal state when a
# student requests a metric the existing registries do not cover.
UNSUPPORTED = "UNSUPPORTED"

# ---------------------------------------------------------------------------
# Student-friendly metric aliases (deterministic, additive)
# ---------------------------------------------------------------------------

METRIC_ALIASES: Dict[str, str] = {
    "roe": "ROE",
    "return on equity": "ROE",
    "roa": "ROA",
    "return on assets": "ROA",
    "eps": "EPS",
    "earnings per share": "EPS",
    "cagr": "CAGR",
    "profit margin": "Profit Margin",
    "gross profit": "Gross Profit",
    "gross margin": "Gross Margin",
    "net margin": "Net Margin",
    "net profit margin": "Net Margin",
    "operating margin": "Operating Margin",
    "ebitda margin": "EBITDA Margin",
    "working capital": "Working Capital",
    "current ratio": "Current Ratio",
    "quick ratio": "Quick Ratio",
    "debt to equity": "Debt to Equity",
    "debt to assets": "Debt to Assets",
    "interest coverage": "Interest Coverage",
    "asset turnover": "Asset Turnover",
    "equity multiplier": "Equity Multiplier",
    "inventory turnover": "Inventory Turnover",
    "receivables turnover": "Receivables Turnover",
    "payables turnover": "Payables Turnover",
    "profit": "Profit",
    "loss": "Loss",
    "return on total assets": "ROA",
    # Sprint 15D commercial arithmetic (additive)
    "commission": "Commission",
    "profit %": "Profit Percent",
    "profit percentage": "Profit Percent",
    "profit percent": "Profit Percent",
    "loss %": "Loss Percent",
    "loss percentage": "Loss Percent",
    "loss percent": "Loss Percent",
    "selling price": "Selling Price",
    "cost price": "Cost Price",
    "trade discount": "Trade Discount",
    "cash discount": "Cash Discount",
    "net price": "Net Price",
    "cash paid": "Cash Paid",
    "creditor balance": "Creditor Balance",
    "debtor balance": "Debtor Balance",
}

# ---------------------------------------------------------------------------
# FYJC maths surface - read-only view over the existing registry.
# No formula is added here; this table only names what 12A-12F already
# compute so the student layer can present it and refuse everything else.
# ---------------------------------------------------------------------------


def _norm(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


def fyjc_maths_surface() -> Dict[str, Dict[str, Any]]:
    """Deterministic map: student metric name -> {concept, formula_ids,
    dependencies, unit_kind, description}. Built from EXTENDED_REGISTRY -
    the exact production registry the strict C++ authority uses."""
    surface: Dict[str, Dict[str, Any]] = {}
    for fid in sorted(FYJC_FORMULA_REGISTRY.all_ids()):
        definition = FYJC_FORMULA_REGISTRY.get(fid)
        if definition is None:
            continue
        concept = definition.target or definition.formula_id
        key = _norm(concept)
        entry = surface.setdefault(key, {
            "concept": concept,
            "formula_ids": [],
            "dependencies": [],
            "unit_kind": definition.unit_kind,
            "description": definition.description,
        })
        if fid not in entry["formula_ids"]:
            entry["formula_ids"].append(fid)
        for dep in definition.dependencies:
            if dep not in entry["dependencies"]:
                entry["dependencies"].append(dep)
    return surface


_KNOWN_CONCEPTS_CACHE: Optional[frozenset] = None
_KNOWN_DISPLAY_CACHE: Optional[Dict[str, str]] = None


def _known_concepts() -> frozenset:
    """Normalised set of every concept the existing registry can talk
    about: formula ids plus their declared dependencies (so reverse
    'find the missing figure' questions resolve through the strict C++
    authority). No new formula is added here - only names already
    present in the 12A-12F registries."""
    global _KNOWN_CONCEPTS_CACHE
    if _KNOWN_CONCEPTS_CACHE is None:
        names = set(FYJC_FORMULA_REGISTRY.all_ids())
        for fid in FYJC_FORMULA_REGISTRY.all_ids():
            definition = FYJC_FORMULA_REGISTRY.get(fid)
            if definition is not None:
                names.update(definition.dependencies or [])
        _KNOWN_CONCEPTS_CACHE = frozenset({_norm(n) for n in names})
    return _KNOWN_CONCEPTS_CACHE


def known_concept_names() -> List[str]:
    """Sorted list of normalized names for every concept the registry
    can talk about (targets + dependencies). Public counterpart of
    _known_concepts()."""
    return sorted(_known_concepts())


def known_concept_display(metric: str) -> Optional[str]:
    """Canonical display name for any concept the registry knows
    (targets + dependencies), e.g. 'expenses' -> 'Expenses'."""
    global _KNOWN_DISPLAY_CACHE
    if _KNOWN_DISPLAY_CACHE is None:
        names: Dict[str, str] = {}
        for fid in FYJC_FORMULA_REGISTRY.all_ids():
            definition = FYJC_FORMULA_REGISTRY.get(fid)
            if definition is None:
                continue
            names[_norm(definition.target)] = definition.target
            for dep in definition.dependencies or []:
                names[_norm(dep)] = dep
        _KNOWN_DISPLAY_CACHE = names
    if not metric:
        return None
    return _KNOWN_DISPLAY_CACHE.get(_norm(str(metric)))


_SURFACE_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _surface() -> Dict[str, Dict[str, Any]]:
    global _SURFACE_CACHE
    if _SURFACE_CACHE is None:
        _SURFACE_CACHE = fyjc_maths_surface()
    return _SURFACE_CACHE


def resolve_metric(metric: str) -> Optional[Dict[str, Any]]:
    """Resolve a student metric spelling to a surface entry (or None)."""
    if not metric:
        return None
    key = _norm(metric)
    direct = _surface().get(key)
    if direct is not None:
        return direct
    alias = METRIC_ALIASES.get(key)
    if alias is not None:
        return _surface().get(_norm(alias))
    return None


def is_supported_metric(metric: str) -> bool:
    return resolve_metric(metric) is not None


def supported_metric_names() -> List[str]:
    return sorted(_surface().keys())


# ---------------------------------------------------------------------------
# Strict C++-authority solve (shared by maths + book-keeping)
# ---------------------------------------------------------------------------


def _coerce_facts(facts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Accept plain values ({'Revenue': 1000}) as well as pipeline-shaped
    fact dicts, mirroring the student-sandbox convention. A plain value
    becomes {'value': ..., 'reporting_period': 'FY2025'} - nothing is
    fabricated, only the established default metadata is attached."""
    out: Dict[str, Any] = {}
    for key, value in (facts or {}).items():
        if isinstance(value, dict):
            out[key] = dict(value)
        else:
            out[key] = {
                "value": value,
                "reporting_period": "FY2025",
                "provenance_tier": "STUDENT_INPUT",
            }
    return out


def solve_strict(concept: str,
                 facts: Optional[Dict[str, Any]] = None) -> Solution:
    """Solve ONE concept through the Sprint 12F strict path.

    Every atomic financial step is executed by the C++ mathematical
    authority; a registered formula that is not covered, or a missing
    binary, fails closed (UNSUPPORTED / ENGINE_UNAVAILABLE). Python never
    performs a fallback calculation.
    """
    graph = build_fact_graph(_coerce_facts(facts))
    solver = Solver(
        FYJC_FORMULA_REGISTRY, prefer_cpp=True, cpp_authority=True,
    )
    return solver.solve(str(concept), graph)


# ---------------------------------------------------------------------------
# Outcome shaping (student-understandable, Sprint 13 sections 4-5)
# ---------------------------------------------------------------------------


def _inputs_rows(sol: Solution) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in (sol.inputs or []):
        rows.append({
            "concept": item.concept,
            "value": float(item.value) if item.value is not None else None,
            "display_value": item.display_value,
            "status": item.status,
            "provenance_tier": item.provenance_tier,
            "source": item.source,
            "document": getattr(item, "document_name", None) or item.source,
            "page": item.page,
            "evidence": item.evidence,
        })
    return rows


def _refusal_outcome(metric: str, state: str, status: str, reason: str,
                     next_action: str) -> Dict[str, Any]:
    return {
        "metric": str(metric),
        "resolved": False,
        "verdict": "REFUSED",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "authority_state": state,
        "what": f"{metric} could not be calculated.",
        "how": "—",
        "inputs": [],
        "where": [],
        "value": None,
        "display_value": "—",
        "student_answer": None,
        "student_display": None,
        "correct_answer": None,
        "why_not": reason,
        "next_action": next_action,
        "formula": None,
        "formula_id": None,
        "verification_hint": (
            "Not yet - first provide the missing/confirmed evidence listed "
            "under 'Next action'."
        ),
    }


def _expected_number(sol: Solution) -> Optional[float]:
    """The expected answer in the student's display units: percentages as
    the number the student would write (e.g. 20 for 20.00%)."""
    if sol.value is None:
        return None
    # The extended registry stores percent-kind values IN display units
    # (e.g. 20.00 for 20%), so no scaling is applied here.
    return float(sol.value)


def _answer_verdict(sol: Solution, student_answer: Any,
                    tolerance: float) -> Dict[str, Any]:
    """Compare the student answer to the C++-authoritative result.

    Deterministic: both numbers are compared in display units with an
    absolute tolerance (default 0.01, exam-friendly for hand rounding).
    Nothing is fabricated when the answer is missing/unparseable."""
    parsed = parse_numeric_text(student_answer) if student_answer not in (None, "") \
        else None
    expected = _expected_number(sol)
    if expected is None:
        return {
            "verdict": "NOT_APPLICABLE",
            "student_display": None,
            "correct_answer": None,
            "mismatch": None,
        }
    if parsed is None or parsed.value is None:
        return {
            "verdict": "NOT_APPLICABLE",
            "student_display": str(student_answer),
            "correct_answer": _fmt_number(expected, sol),
            "mismatch": "The answer you entered could not be read as a "
                        "number - re-enter it (e.g. 20 or 20.00).",
        }
    student_num = float(parsed.value)
    if getattr(sol, "unit_kind", None) == "percent":
        student_num = student_num  # already in display units
    delta = abs(student_num - expected)
    correct = delta <= float(tolerance)
    return {
        "verdict": "CORRECT" if correct else "INCORRECT",
        "student_display": _fmt_number(student_num, sol),
        "correct_answer": _fmt_number(expected, sol),
        "mismatch": None if correct else (
            f"Your answer {_fmt_number(student_num, sol)} differs from the "
            f"C++-verified value {_fmt_number(expected, sol)} (difference "
            f"{_fmt_number(delta, sol)}). Re-check your working - the "
            f"registered formula is used below."
        ),
    }


def _fmt_number(value: float, sol: Optional[Solution] = None) -> str:
    suffix = ""
    if sol is not None and getattr(sol, "unit_kind", None) == "percent":
        suffix = "%"
    return f"{value:.2f}{suffix}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_maths_answer(
    metric: str,
    facts: Optional[Dict[str, Any]] = None,
    text: Optional[str] = None,
    documents: Optional[List[Dict[str, Any]]] = None,
    student_answer: Any = None,
    tolerance: float = 0.01,
) -> Dict[str, Any]:
    """Verify ONE student Maths answer through the C++ mathematical
    authority.

    Parameters
    ----------
    metric         : the requested metric (e.g. "Profit Margin", "ROE").
    facts          : {concept: value-or-fact} provided by the student.
    text           : free-text lines like "Revenue = 1000" (Tier-1 source).
    documents      : [{document_name, page, tier, facts, evidence}] for
                     uploaded photos/PDFs - Tier 1/2 evidence.
    student_answer : the student's numeric answer to verify (optional).
    tolerance      : absolute tolerance in display units for the verdict.

    Returns a student-readable outcome: what/how/inputs/where/status/
    why-not/next-action + verdict (CORRECT/INCORRECT/REFUSED) + the
    C++ authority state. A refusal (UNSUPPORTED / BLOCKED) is a valid
    outcome when the evidence or the registry cannot support the request.
    """
    entry = resolve_metric(metric)
    if entry is not None:
        concept = entry["concept"]
    else:
        # Dependency concepts (Expenses, Equity, Shares Outstanding ...)
        # are resolved to their canonical registry spelling so reverse
        # questions reach the registered inverse path (registry lookups
        # are case-sensitive and deterministic).
        canonical = known_concept_display(metric)
        concept = canonical if canonical is not None else str(metric)
        if _norm(concept) not in _known_concepts():
            supported = ", ".join(supported_metric_names()[:12])
            return _refusal_outcome(
                metric, AUTHORITY_UNSUPPORTED, UNSUPPORTED,
                f"'{metric}' is not one of the financial relationships Platrixa "
                f"can compute. Sprint 13 supports ONLY the existing "
                f"registered formulas - e.g. {supported} and the other "
                f"12A-12F relationships. No new formulas were added and no "
                f"calculation is invented.",
                "Choose a metric from the supported list (shown in the "
                "documentation) and re-submit.",
            )
    if not cpp_available():
        return _refusal_outcome(
            metric, AUTHORITY_UNAVAILABLE, BLOCKED,
            ENGINE_UNAVAILABLE_REASON,
            "Deploy the compiled C++ formula engine. Platrixa never "
            "calculates financial results in Python.",
        )

    # Sprint 15D derivation gate: when the requested figure belongs to a
    # registered canonical FYJC relationship, a validated derivation path
    # must exist BEFORE any execution. A covered concept with no validated
    # path is REVIEW_REQUIRED - Platrixa never guesses.
    deriv_ok, _deriv_path, _deriv_reason = ensure_derivation_valid(concept)
    if not deriv_ok:
        refusal = derivation_refusal_outcome(metric, concept)
        refusal["status"] = REVIEW_REQUIRED
        refusal["status_label"] = STATUS_LABELS.get(
            REVIEW_REQUIRED, REVIEW_REQUIRED)
        return refusal

    merged = _attach_document_facts(_coerce_facts(facts), documents)
    # narrative prose ('Revenue is Rs.10,000 ...') then the strict
    # 'Concept: value' lines (the canonical format wins on duplicates).
    merged.update(extract_prose_facts(text or ""))
    merged.update(_parse_text_facts(text or ""))
    if not merged:
        return _refusal_outcome(
            metric, AUTHORITY_CPP, BLOCKED,
            "No facts were provided. A metric cannot be calculated without "
            "its inputs.",
            "Enter the numbers from the question (e.g. 'Revenue = 1000') "
            "or upload the question photo/PDF.",
        )

    sol = solve_strict(concept, merged)

    # ------------------------------------------------------------------
    # Sprint 15 (Stage 4) hard invariant: a supplied fact is NEVER
    # presented as a calculated answer. A 'direct' solve means the
    # requested concept was itself an input (the solver echoed it with
    # formula_id=None). The only acceptable resolution is an independent
    # derivation of the SAME value through a registered formula (which
    # executes the C++ mathematical authority and carries a formula_id).
    # Otherwise Platrixa refuses - it never echoes, never labels a supplied
    # value as derived.
    # ------------------------------------------------------------------
    if getattr(sol, "kind", "") == "direct" and sol.value is not None:
        reported = sol.value
        remaining = {
            k: v for k, v in merged.items()
            if _norm(str(k)) != _norm(str(concept))
        }
        derived = solve_strict(concept, remaining) if remaining else None
        if derived is not None and getattr(derived, "kind", "") != "direct" \
                and derived.value is not None:
            if derived.value != reported:
                # registered derivation conflicts with the supplied value:
                # preserve the reported value, refuse - never silently choose
                refusal = _refusal_outcome(
                    metric, AUTHORITY_CPP, REVIEW_REQUIRED,
                    f"{concept} is supplied as "
                    f"{format_value(reported, 'amount', 2)}, but the "
                    f"registered formula {derived.formula_id} derives "
                    f"{format_value(derived.value, derived.unit_kind, 2)}. "
                    "The supplied value is preserved - review required, "
                    "never silently choose between them.",
                    "Confirm which value is correct, remove the incorrect "
                    "one, and re-submit so Platrixa can calculate the answer "
                    "through the registered formula.",
                )
                refusal["concept"] = concept
                refusal["formula"] = derived.formula_id
                refusal["formula_id"] = derived.formula_id
                refusal["missing"] = []
                return refusal
            sol = derived  # independent derivation agrees -> resolved
        else:
            refusal = _refusal_outcome(
                metric, AUTHORITY_CPP, BLOCKED,
                f"{concept} was supplied as an input, and Platrixa found no "
                "registered formula that can derive it from the other "
                "inputs. Platrixa never presents a supplied value as a "
                "calculated answer.",
                f"Remove the supplied '{concept}' value, then provide its "
                "inputs so the registered formula can derive it (re-type "
                "the question as 'Calculate <metric>' with the input values "
                "only).",
            )
            refusal["concept"] = concept
            refusal["missing"] = []
            return refusal

    status = sol.status or BLOCKED
    resolved = sol.value is not None and status not in (
        BLOCKED, REVIEW_REQUIRED, UNSUPPORTED,
    )

    if not resolved:
        reason = sol.reason or (
            f"{concept} could not be calculated from the "
            "evidence provided."
        )
        missing = ", ".join(sol.missing or []) or "unknown"
        state = getattr(sol, "sufficiency_state", None) or "BLOCKED"
        if state in ("UNSUPPORTED",):
            authority_state = AUTHORITY_UNSUPPORTED
        elif state in ("ENGINE_UNAVAILABLE",):
            authority_state = AUTHORITY_UNAVAILABLE
        else:
            authority_state = AUTHORITY_CPP
        return {
            "metric": str(metric),
            "concept": concept,
            "resolved": False,
            "verdict": "REFUSED",
            "status": status,
            "status_label": STATUS_WORDS.get(status, status),
            "authority_state": authority_state,
            "what": f"{metric} could not be calculated.",
            "how": sol.formula or "—",
            "inputs": _inputs_rows(sol),
            "where": _inputs_rows(sol),
            "value": None,
            # Sprint 15D invariant: a refusal never presents a numeric
            # display. The reported/conflicting value is preserved ONLY in
            # the reason text and inputs - never echoed as the answer.
            "display_value": "—",
            "student_answer": student_answer,
            "student_display": str(student_answer) if student_answer is not None
                              else None,
            "correct_answer": None,
            "why_not": reason,
            "missing": list(sol.missing or []),
            "next_action": (
                f"Provide the missing input(s): {missing}. "
                "Upload the relevant page or enter the verified value "
                "manually."
                if status == BLOCKED and missing != "unknown"
                else "Check the question for conflicting values and "
                     "re-submit with the confirmed numbers."
            ),
            "formula": sol.formula,
            "formula_id": sol.formula_id,
            "verification_hint": (
                "Not yet - first provide the missing/confirmed evidence "
                "listed under 'Next action'."
            ),
        }

    verdict = _answer_verdict(sol, student_answer, tolerance)
    return {
        "metric": str(metric),
        "concept": concept,
        "resolved": True,
        "verdict": verdict["verdict"],
        "status": status,
        "status_label": STATUS_WORDS.get(status, status),
        "authority_state": AUTHORITY_CPP,
        "what": f"{metric}: {sol.display_value}",
        "how": sol.formula or "—",
        "inputs": _inputs_rows(sol),
        "where": _inputs_rows(sol),
        "value": float(sol.value) if sol.value is not None else None,
        "display_value": sol.display_value,
        "student_answer": student_answer,
        "student_display": verdict["student_display"],
        "correct_answer": verdict["correct_answer"],
        "mismatch": verdict["mismatch"],
        "why_not": None,
        "next_action": (
            "Try the next question - or re-attempt this one and "
            "re-verify your working against the formula shown."
        ),
        "formula": sol.formula,
        "formula_id": sol.formula_id,
        "verification_hint": (
            f"Yes - recompute by hand using the registered formula "
            f"({sol.formula}) and the numbers listed above."
        ),
        "derivation": describe_derivation(concept, sol),
    }


# ---------------------------------------------------------------------------
# Sprint 13 section 15 - human-level acceptance checklist wording
# ---------------------------------------------------------------------------

FYJC_MATHS_CHECKLIST: List[Dict[str, str]] = [
    {
        "question": "What did Platrixa calculate?",
        "payload_field": "what",
        "explanation": "The metric name and its value (or a refusal).",
    },
    {
        "question": "What formula did it use?",
        "payload_field": "how",
        "explanation": "The registered accounting relationship, in plain text.",
    },
    {
        "question": "Which numbers were used?",
        "payload_field": "inputs",
        "explanation": "Each input with its value.",
    },
    {
        "question": "Where did those numbers come from?",
        "payload_field": "where",
        "explanation": "Document / page / source / evidence for each number.",
    },
    {
        "question": "Is my answer right?",
        "payload_field": "verdict",
        "explanation": "CORRECT / INCORRECT against the C++-verified value, "
                      "or REFUSED with the reason.",
    },
    {
        "question": "Why was something blocked?",
        "payload_field": "why_not",
        "explanation": "The exact missing / conflicting evidence.",
    },
    {
        "question": "What do I need to provide to continue?",
        "payload_field": "next_action",
        "explanation": "The concrete next step (e.g. upload the balance-"
                      "sheet page or enter the verified value).",
    },
]


def student_checklist() -> List[Dict[str, str]]:
    return [dict(item) for item in FYJC_MATHS_CHECKLIST]
