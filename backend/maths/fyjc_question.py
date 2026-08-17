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

Sprint 15 (Stage 4) - requested-concept resolution
--------------------------------------------------
The requested metric comes from the QUESTION'S ACTUAL INTENT, not from
the first registered word anywhere in the text. A question such as
"Find the missing figure: Expenses." with "Profit: 200" in the facts
must resolve to `expenses` (a registered inverse of the Profit formula),
never to `profit` merely because that word appears in a fact line.

The classifier therefore:
  * extracts the object of ask-clauses ("Calculate X", "Find X",
    "What is X?", "missing figure: X", "X = ?");
  * resolves it against the registry's known concepts (formula targets
    AND their dependencies, so reverse questions work);
  * marks the request `requested_uncertain` when several concepts are
    plausible (never guesses);
  * falls back to a cautious whole-text scan ONLY when no instruction
    clause exists, and only when exactly ONE known concept is present.

The classification is purely keyword/registry based and deterministic:
it routes, it never calculates. Numerical execution still goes through
the C++ mathematical authority (see fyjc_maths.solve_strict).

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_maths import (
    METRIC_ALIASES,
    known_concept_display,
    known_concept_names,
)

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
    # Sprint 15I-TX: the expanded FYJC surface - donations, return
    # chains ('the same were returned'), settlement cheques, orders and
    # goods-return variants. Routing only; the accounting authority is
    # still the single hardened engine.
    "donated", "donation", "donating",
    "returned us goods", "returned us stock", "goods returned by",
    "returned stock", "the same were returned", "the same goods",
    "settled", "settlement", "cheque", "cheques",
    "placed an order", "order for goods", "order of goods",
    "executed our order",
    # Sprint 15I-DISC: discrepancy / reconciliation / rectification
    # wording routes to the Book-Keeping flow, where the Discrepancy
    # Authority resolves it deterministically (BRS adjustments, a
    # dishonoured cheque, an omitted transaction, a rectification
    # entry). Routing only; the accounting authority is unchanged.
    "dishonoured", "dishonour", "bounced", "cheque was returned",
    "bank charges", "bank reconciliation", "reconciliation statement",
    "pass book", "cash book", "omitted", "rectify", "rectification",
    "wrongly", "suspense account", "suspense a/c", "undercast",
    "overcast", "standing instruction",
    # Sprint 15I-BILLS: bills-of-exchange wording routes to the
    # Book-Keeping flow, where the Bills Authority resolves the lifecycle
    # deterministically (drawing / acceptance, discounting, endorsement,
    # collection, honour / dishonour, noting charges). Routing only; the
    # accounting authority is unchanged.
    "bill of exchange", "bills of exchange", "bills receivable",
    "bills payable", "drawer", "drawee", "acceptor", "payee",
    "accepted the bill", "bill was accepted", "drew a bill",
    "discounted the bill", "discounted a bill", "endorsed the bill",
    "endorsed a bill", "endorsed it", "sent for collection",
    "noting charges", "bill was dishonoured", "bill dishonoured",
    "bill was honoured", "bill honoured", "retained till maturity",
    "retained until maturity",
    # Sprint 15I-SPEC: specialized-authority wording routes to the
    # Book-Keeping flow, where the Consignment / Joint Venture / Single
    # Entry authorities resolve it deterministically. Routing only; the
    # accounting authority is unchanged.
    "consignment", "consigned", "consignee", "consignor", "del credere",
    "joint venture", "co-venturer", "co venturer", "venture account",
    "venture a/c", "incomplete records", "single entry",
    "statement of affairs", "opening capital", "closing capital",
    "fresh capital", "additional capital",
)
_METRIC_HINTS = (
    "calculate", "compute", "find", "ratio", "margin", "percent", " %",
    "earnings per share",
)

# Ask-clause verbs. The object of these clauses is the requested concept.
_ASK_VERB_RE = re.compile(
    r"\b(?:calculate|compute|find|determine|evaluate|work\s+out|obtain)\b"
    r"(?P<obj>[^.\n]{1,90})",
    re.IGNORECASE,
)
_WHAT_IS_RE = re.compile(
    r"\bwhat\s+(?:is|are|will be|would be)\s+(?P<obj>[^.\n]{1,90})",
    re.IGNORECASE,
)
_MISSING_STANDALONE_RE = re.compile(
    r"\bmissing\s+(?:figure|value|item|amount)\s*[:：]?\s*"
    r"(?P<obj>[A-Za-z][A-Za-z &'/\-]{0,50})",
    re.IGNORECASE,
)
_BARE_QUESTION_RE = re.compile(
    r"(?:^|[\n;])\s*(?P<obj>[A-Za-z][A-Za-z0-9 &'/\-]{1,50}?)\s*[:=]?\s*\?",
    re.IGNORECASE,
)

# Cut the object phrase at the first strong delimiter, or at a filler word
# that introduces INPUTS rather than the ask. ("of" / "to" / "on" / "and"
# are deliberately NOT cut: they appear inside concepts like "Cost of
# Sales", "Debt to Equity", "Return on Equity", and "Profit and Loss" must
# stay intact so the ambiguity guard can see both figures.)
_PHRASE_CUT_RE = re.compile(
    r"[:=;?.,\n]"
    r"|\s+(?:is|are|was|were|will be|when|where|given|using|if|assuming|"
    r"suppose|having|with|without|from|for|as|after|before|per|p\.a\.?)\s+"
)

# Leading words stripped before concept matching (articles, possessives).
_LEAD_STRIP_RE = re.compile(
    r"^(?:(?:the|a|an|its|your|his|her|our|their|[a-z]+'s)\s+)+"
)
_MISSING_PREFIX_RE = re.compile(
    r"^(?:missing\s+(?:figure|value|item|amount)\s*[:：]?\s*)"
)
_VALUE_PREFIX_RE = re.compile(
    r"^(?:value|amount|figure|total|ratio)\s+of\s+"
)

_CONCEPT_WORDS_CACHE: Optional[List[str]] = None


def _concept_words() -> List[str]:
    """Registered concept names (formula targets + their dependencies)
    plus student aliases, longest first. This is the vocabulary the
    classifier may RESOLVE the ask against - reverse questions name
    dependencies (Expenses, Equity, Shares Outstanding ...)."""
    global _CONCEPT_WORDS_CACHE
    if _CONCEPT_WORDS_CACHE is None:
        words = set(known_concept_names())
        for alias in METRIC_ALIASES.values():
            words.add(" ".join(str(alias).lower().split()))
        words = {w for w in words if len(w) >= 3}
        _CONCEPT_WORDS_CACHE = sorted(words, key=lambda w: (-len(w), w))
    return _CONCEPT_WORDS_CACHE


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _longest_concept_prefix(phrase: str) -> Optional[str]:
    """Longest registered concept that is a word-boundary PREFIX of the
    phrase (e.g. 'profit margin' of 'profit margin when ...')."""
    p = _norm(phrase)
    if not p:
        return None
    for name in _concept_words():
        if p == name or p.startswith(name + " "):
            return name
    return None


def _concepts_in_text(text: str) -> List[str]:
    """Every registered concept mentioned in the text (longest first,
    word-boundary aware, deduplicated)."""
    low = " " + " ".join(str(text or "").lower().split()) + " "
    found: List[str] = []
    for name in _concept_words():
        if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", low):
            if name not in found:
                found.append(name)
    return found


def _candidates_in_phrase(phrase: str) -> List[str]:
    """Resolve one ask-clause object phrase to the concepts it names.

    Returns [] when the phrase names no registered concept, one concept
    when it is unambiguous, or several when the phrase names more than
    one possible requested figure ('Find Profit and Loss') - the caller
    then marks the request uncertain instead of guessing."""
    p = _norm(phrase)
    p = _LEAD_STRIP_RE.sub("", p)
    p = _MISSING_PREFIX_RE.sub("", p)
    p = _VALUE_PREFIX_RE.sub("", p)
    p = _PHRASE_CUT_RE.split(p, maxsplit=1)[0]
    p = p.strip(" .:;,")
    if not p:
        return []
    resolved = _longest_concept_prefix(p)
    if resolved is None:
        return []
    out = [resolved]
    # Ambiguity guard: if the remainder of the clause names ANOTHER
    # concept, the request is for more than one figure.
    rest = p[len(resolved):].strip()
    for other in _concepts_in_text(rest):
        if other != resolved and other not in out:
            out.append(other)
    return out


def _resolve_requested_concept(question: str) -> Tuple[Optional[str], List[str]]:
    """The requested concept from the question's ask-clauses.

    Returns (metric, candidates): metric is set when the ask names
    exactly ONE distinct registered concept; candidates lists every
    distinct concept the ask-clauses name (used to report uncertainty).
    """
    text = str(question or "")
    candidates: List[str] = []
    for m in _WHAT_IS_RE.finditer(text):
        candidates += _candidates_in_phrase(m.group("obj"))
    for m in _ASK_VERB_RE.finditer(text):
        candidates += _candidates_in_phrase(m.group("obj"))
    for m in _MISSING_STANDALONE_RE.finditer(text):
        candidates += _candidates_in_phrase(m.group("obj"))
    for m in _BARE_QUESTION_RE.finditer(text):
        candidates += _candidates_in_phrase(m.group("obj"))
    distinct: List[str] = []
    for c in candidates:
        if c not in distinct:
            distinct.append(c)
    if len(distinct) == 1:
        return distinct[0], distinct
    return None, distinct


def _maths_verdict(metric: Optional[str], reason: str,
                   uncertain: bool) -> Dict[str, Any]:
    return {
        "domain": DOMAIN_MATHS,
        "kind": KIND_METRIC,
        "metric": metric,
        "requested_uncertain": uncertain,
        "reason": reason,
    }


def _bookkeeping_verdict(kind: str, reason: str) -> Dict[str, Any]:
    return {
        "domain": DOMAIN_BOOKKEEPING,
        "kind": kind,
        "metric": None,
        "requested_uncertain": False,
        "reason": reason,
    }


def classify_fyjc_question(question: str) -> Dict[str, Any]:
    """Deterministically classify one FYJC question.

    Returns {domain, kind, metric, requested_uncertain, reason}.
    `metric` is the registered concept (formula target OR dependency)
    the question actually asks for; `requested_uncertain` is True when
    the ask is ambiguous and must be REVIEW_REQUIRED, never guessed.
    The result is a routing decision only - no calculation happens here.
    """
    text = str(question or "").strip()
    low = " " + text.lower() + " "

    if not text:
        return {
            "domain": DOMAIN_UNRECOGNISED,
            "kind": KIND_UNKNOWN,
            "metric": None,
            "requested_uncertain": False,
            "reason": "No question was provided.",
        }

    # 0) strong book-keeping task wording (structural - wins over a
    #    generic metric word inside the task).
    if any(h in low for h in _TRIAL_BALANCE_HINTS):
        return _bookkeeping_verdict(
            KIND_TRIAL_BALANCE, "Trial Balance wording detected.")
    if any(h in low for h in _JOURNAL_HINTS):
        return _bookkeeping_verdict(
            KIND_JOURNAL, "Journal wording detected.")
    if any(h in low for h in _LEDGER_HINTS):
        return _bookkeeping_verdict(
            KIND_LEDGER, "Ledger wording detected.")

    # 0.5) Sprint 15I-SPEC specialized-authority wording (structural - a
    #    consignment / joint-venture / single-entry question is a
    #    Book-Keeping topic even when it asks for a registered metric
    #    like 'profit'). Routing only; the authority is unchanged.
    if re.search(r"\bconsign\w*\b|\bdel\s+credere\b", low):
        return _bookkeeping_verdict(
            KIND_TRANSACTION, "Consignment wording detected.")
    if re.search(r"\bjoint\s+venture\b|\bco-?venturer\b|"
                 r"\bventure\s+(?:a/c|account)\b", low):
        return _bookkeeping_verdict(
            KIND_TRANSACTION, "Joint-venture wording detected.")
    if re.search(r"\bsingle\s+entry\b|\bincomplete\s+records\b|"
                 r"\bstatement\s+of\s+affairs\b", low):
        return _bookkeeping_verdict(
            KIND_TRANSACTION, "Single-entry / incomplete-records wording "
            "detected.")
    if re.search(r"\bopening\s+capital\b", low) and re.search(
            r"\bclosing\s+capital\b", low):
        return _bookkeeping_verdict(
            KIND_TRANSACTION, "Change-in-net-worth wording detected.")

    # 1) the requested concept from the ask-clause (the actual intent).
    metric, candidates = _resolve_requested_concept(text)
    if metric is not None:
        display = known_concept_display(metric) or metric
        return _maths_verdict(
            metric,
            f"Requested: {display}. Registered financial relationship "
            "detected.",
            uncertain=False,
        )

    # 2) transaction wording (book-keeping analysis).
    if any(v in low for v in _TRANSACTION_VERBS):
        return _bookkeeping_verdict(
            KIND_TRANSACTION, "Transaction wording detected.")

    # 3) cautious fallback: no ask-clause. Use a whole-text concept scan
    #    ONLY when exactly one registered concept is present; several
    #    possible figures -> uncertain (REVIEW_REQUIRED), never guess.
    if not candidates:
        candidates = _concepts_in_text(text)
    distinct: List[str] = []
    for c in candidates:
        if c not in distinct:
            distinct.append(c)
    if len(distinct) == 1:
        display = known_concept_display(distinct[0]) or distinct[0]
        return _maths_verdict(
            distinct[0],
            f"Requested: {display}. Registered financial relationship "
            "detected.",
            uncertain=False,
        )
    if len(distinct) > 1:
        return _maths_verdict(
            None,
            "Requested figure is unclear - the question mentions several "
            "financial figures ("
            + ", ".join(known_concept_display(c) or c for c in distinct[:4])
            + "). FT-E will ask which one to calculate rather than guess.",
            uncertain=True,
        )

    # 4) generic numerical wording (metric not registered -> the solver
    #    will refuse UNSUPPORTED deterministically).
    if any(h in low for h in _METRIC_HINTS):
        return _maths_verdict(
            None,
            "Numerical/metric wording detected but no registered metric "
            "matched.",
            uncertain=False,
        )

    return {
        "domain": DOMAIN_UNRECOGNISED,
        "kind": KIND_UNKNOWN,
        "metric": None,
        "requested_uncertain": False,
        "reason": "No deterministic domain signal detected.",
    }


def extract_facts_from_question(question: str) -> Dict[str, Any]:
    """Extract facts from free text deterministically (nothing is
    guessed): the strict 'Concept: value' / 'Concept = value' lines first
    (12D normalizer), plus narrative-prose 'Concept is Rs.X' statements
    anchored to registered concept names (Sprint 15). Returns {} when
    nothing parses cleanly - the caller then reports BLOCKED."""
    from backend.maths.student_sandbox import (
        _parse_text_facts,
        extract_prose_facts,
    )

    q = str(question or "")
    out = extract_prose_facts(q)
    out.update(_parse_text_facts(q))
    return out
