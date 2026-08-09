"""
Financial Timeline Engine
Sprint 13 - FYJC Student Maths & Book-Keeping Readiness
backend/maths/fyjc_question.py

Deterministic FYJC question classification and intermediate
representation.

A student submits a question in ANY supported form (typed, pasted text,
OCR-extracted from a photo, PDF text). This layer answers two questions
without any AI:

    1. Which domain does the question belong to?
         maths         - a registered financial metric is requested
         bookkeeping   - a journal / ledger / trial-balance /
                         transaction task
         unrecognised  - no deterministic signal (refused)

    2. What structured representation can be extracted?
         metric        - the registered metric name when detected
         facts         - "Concept: value" / "Concept = value" pairs
                         parsed through the 12D normalizer (never
                         guessed)

The classification is purely keyword/registry based and deterministic:
it routes, it never calculates. Numerical execution still goes through
the C++ mathematical authority (see fyjc_maths.solve_strict).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.maths.fyjc_maths import METRIC_ALIASES, supported_metric_names

# ---------------------------------------------------------------------------
# Domain / kind vocabulary
# ---------------------------------------------------------------------------

DOMAIN_MATHS = "maths"
DOMAIN_BOOKKEEPING = "bookkeeping"
DOMAIN_UNRECOGNISED = "unrecognised"

KIND_METRIC = "metric"
KIND_JOURNAL = "journal"
KIND_LEDGER = "ledger"
KIND_TRIAL_BALANCE = "trial_balance"
KIND_TRANSACTION = "transaction"
KIND_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Deterministic signal tables (ordered; first match wins)
# ---------------------------------------------------------------------------

_JOURNAL_HINTS = (
    "journal entry", "journal entries", "journalise", "journalize",
    "pass the journal", " in the journal ", " journal ",
)
_LEDGER_HINTS = (
    "ledger", "post the following", "posting", "post the transactions",
    "balance the following accounts", "open the following accounts",
    "balance of the account", "ledger account",
)
_TRIAL_BALANCE_HINTS = (
    "trial balance", "trial-balance", "prepare a trial balance",
)
_TRANSACTION_VERBS = (
    "purchased", "bought", "sold", "paid", "received", "started business",
    "commenced business", "withdrew", "deposited into", "returned goods",
    "commission", "rent ", "salary", "salaries", "wages", "insurance",
    "interest on drawings", "interest on capital",
    "loan", "drawings", "discount allowed",
    "discount received", "goods for personal use", "free samples",
)
_METRIC_HINTS = (
    "calculate", "compute", "find", "ratio", "margin", "percent", " %",
    "earnings per share",
)

_METRIC_WORDS_CACHE: Optional[List[str]] = None


def _metric_words() -> List[str]:
    """Registered metric names + aliases, longest first (deterministic)."""
    global _METRIC_WORDS_CACHE
    if _METRIC_WORDS_CACHE is None:
        words = set(supported_metric_names())
        for alias in METRIC_ALIASES.values():
            words.add(" ".join(str(alias).lower().split()))
        words = {w for w in words if len(w) >= 3}
        _METRIC_WORDS_CACHE = sorted(words, key=lambda w: (-len(w), w))
    return _METRIC_WORDS_CACHE


def classify_fyjc_question(question: str) -> Dict[str, Any]:
    """Deterministically classify one FYJC question.

    Returns {domain, kind, metric, reason}. `metric` is the registered
    metric name when the question requests one (None otherwise). The
    result is a routing decision only - no calculation happens here.
    """
    low = " " + str(question or "").strip().lower() + " "

    # 1) registered metric request (longest name first)
    for name in _metric_words():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return {
                "domain": DOMAIN_MATHS,
                "kind": KIND_METRIC,
                "metric": name,
                "reason": (
                    f"Question asks for the registered metric '{name}'."
                ),
            }

    # 2) book-keeping task kinds
    if any(h in low for h in _TRIAL_BALANCE_HINTS):
        return {
            "domain": DOMAIN_BOOKKEEPING,
            "kind": KIND_TRIAL_BALANCE,
            "metric": None,
            "reason": "Trial Balance wording detected.",
        }
    if any(h in low for h in _JOURNAL_HINTS):
        return {
            "domain": DOMAIN_BOOKKEEPING,
            "kind": KIND_JOURNAL,
            "metric": None,
            "reason": "Journal wording detected.",
        }
    if any(h in low for h in _LEDGER_HINTS):
        return {
            "domain": DOMAIN_BOOKKEEPING,
            "kind": KIND_LEDGER,
            "metric": None,
            "reason": "Ledger wording detected.",
        }

    # 3) transaction wording
    if any(v in low for v in _TRANSACTION_VERBS):
        return {
            "domain": DOMAIN_BOOKKEEPING,
            "kind": KIND_TRANSACTION,
            "metric": None,
            "reason": "Transaction wording detected.",
        }

    # 4) generic numerical wording (metric not registered -> the solver
    #    will refuse UNSUPPORTED deterministically)
    if any(h in low for h in _METRIC_HINTS):
        return {
            "domain": DOMAIN_MATHS,
            "kind": KIND_METRIC,
            "metric": None,
            "reason": (
                "Numerical/metric wording detected but no registered "
                "metric matched."
            ),
        }

    return {
        "domain": DOMAIN_UNRECOGNISED,
        "kind": KIND_UNKNOWN,
        "metric": None,
        "reason": "No deterministic domain signal detected.",
    }


def extract_facts_from_question(question: str) -> Dict[str, Any]:
    """Extract 'Concept: value' / 'Concept = value' facts from free text
    deterministically (12D normalizer; nothing is guessed). Returns {} when
    nothing parses cleanly - the caller then reports BLOCKED."""
    from backend.maths.student_sandbox import _parse_text_facts

    return _parse_text_facts(str(question or ""))
