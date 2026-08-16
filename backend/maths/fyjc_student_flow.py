"""
Financial Timeline Engine
Sprint 14 - FYJC Student End-to-End Journey Orchestration
backend/maths/fyjc_student_flow.py

A thin, PURE, deterministic orchestration layer between the Sprint 13
FYJC capability modules and the student-facing UI. It does NOT add a
second maths/accounting engine: it shapes the existing Sprint 13
outputs (fyjc_maths, fyjc_accounting, fyjc_question) into the student
journey the UI renders:

    Question/photo -> understanding -> subject reasoning
        -> C++ mathematical authority -> explanation
        -> independent verification

Architectural rules (unchanged from Sprint 12F/13)
--------------------------------------------------
* C++ remains the sole mathematical authority. Every numerical result
  in a Maths flow comes from verify_maths_answer (-> solve_strict ->
  C++). Python never performs a fallback calculation.
* Accounting treatment comes from the hardened FT-E book-keeping
  engine (backend.maths.fyjc_bk_reasoning.reason_bk_question, routed
  through hardened_bookkeeping_outcome) - the SAME authority the
  QuestionBank / PracticeEngine path uses (Sprint 15I-O). The legacy
  Sprint 13 classifier is never the authority for FYJC book-keeping
  verification. The pure ledger/trial-balance arithmetic exposed here
  is VERIFICATION arithmetic over the student's own postings - it
  never calculates a financial result for the student.
* No fabricated values, no silent substitution, no open-web fallback.
  BLOCKED / REVIEW_REQUIRED / UNSUPPORTED are valid, student-readable
  outcomes.
* The "FYJC traditional class" (Personal / Real / Nominal) shown here
  is a presentation mapping over the Sprint 13 modern-approach role -
  it is classification display, never a new accounting rule.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.maths.fyjc_maths import verify_maths_answer
from backend.maths.fyjc_bk_reasoning import INVALID_INPUT_MATH
from backend.maths.fyjc_accounting import (
    hardened_bookkeeping_outcome,
    post_ledger,
    build_trial_balance,
    verify_arithmetic,
    verify_journal_entry,
    verify_ledger_balance,
    verify_trial_balance,
    account_role,
)
from backend.maths.fyjc_question import (
    DOMAIN_BOOKKEEPING,
    DOMAIN_MATHS,
    DOMAIN_UNRECOGNISED,
    KIND_JOURNAL,
    KIND_LEDGER,
    KIND_METRIC,
    KIND_TRANSACTION,
    KIND_TRIAL_BALANCE,
    classify_fyjc_question,
    extract_facts_from_question,
)
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED
from backend.maths.student_sandbox import STATUS_WORDS
from backend.maths.normalization import parse_numeric_text

# ---------------------------------------------------------------------------
# FYJC traditional account classes (presentation mapping over the Sprint 13
# modern-approach roles). Standard FYJC text-book classification:
#   Personal = persons / firms / capital / liabilities
#   Real     = assets / property
#   Nominal  = expenses / incomes
# The mapping is display-only - the accounting TREATMENT still comes from
# the Sprint 13 golden-rule engine.
# ---------------------------------------------------------------------------

TRADITIONAL_CLASS: Dict[str, str] = {
    "asset": "Real",
    "expense": "Nominal",
    "income": "Nominal",
    "liability": "Personal",
    "capital": "Personal",
    "contra_capital": "Personal (contra)",
    "contra_income": "Nominal (contra)",
    "contra_expense": "Nominal (contra)",
    "contra_asset": "Real (contra)",
}

# Percent tokens (e.g. "10% trade discount") are a signal that the
# description carries a discount/rate that no registered formula can
# deterministically net. FT-E surfaces it as a concern instead of
# silently computing around the C++ authority.
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")

_MULTI_AMOUNT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def fyjc_traditional_class(role: Optional[str], account: str = "") -> str:
    """Student-facing FYJC class for a modern-approach role (or a named
    party, which is always a Personal account). Display only."""
    if role is None:
        # A named party (Rahul, Mohan ...) is a Personal account.
        return "Personal"
    return TRADITIONAL_CLASS.get(str(role), str(role).title())


# ---------------------------------------------------------------------------
# Understanding stage (Sprint 14 section 3)
# ---------------------------------------------------------------------------


def _fmt_amount(value: Any) -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


def build_understanding(question: str) -> Dict[str, Any]:
    """What FT-E understood from the question text (never a guess).

    Returns {classified, domain, kind, metric, facts, interpretation,
    concerns, status}. `concerns` lists student-readability issues that
    the UI must surface before any calculation (e.g. a discount % the
    registered engine cannot net deterministically).
    """
    q = str(question or "").strip()
    classification = classify_fyjc_question(q) if q else {
        "domain": DOMAIN_UNRECOGNISED,
        "kind": "unknown",
        "metric": None,
        "reason": "No question was provided.",
    }
    facts = extract_facts_from_question(q) if q else {}
    fact_rows: List[Dict[str, Any]] = []
    for concept in sorted(facts):
        fact = facts[concept]
        if isinstance(fact, dict):
            fact_rows.append({
                "concept": concept,
                "value": fact.get("value"),
                "display_value": _fmt_amount(fact.get("value")),
                "source": str(fact.get("source") or "Question text"),
                "provenance_tier": str(fact.get("provenance_tier") or "DOCUMENT"),
            })

    concerns: List[str] = []
    # Sprint 15I-R: informational notes are kept SEPARATE from blocking
    # concerns. The UI presents blocking concerns in the 'Almost there'
    # clarification panel; informational notes are shown neutrally and
    # never stop a VERIFIED result from displaying.
    info_notes: List[str] = []
    # Sprint 15I-O: rate/discount and multi-amount concerns are only
    # raised for the MATHS domain. The FYJC book-keeping Study / Verify
    # flow now routes through the hardened FT-E engine, which handles
    # trade / cash discounts, GST and multi-amount settlements
    # deterministically (15E/15I-K/15I-L) - a stale 'FT-E will not
    # compute a discounted amount' warning next to a VERIFIED journal
    # would be wrong. If the hardened engine cannot resolve a
    # book-keeping question, it refuses with its own why_not /
    # next_action in the accounting section.
    is_bookkeeping = classification.get("domain") == DOMAIN_BOOKKEEPING
    if q and not is_bookkeeping:
        for m in _PERCENT_RE.finditer(q):
            concerns.append(
                f"'{m.group(0).strip()}' is a rate/discount. FT-E has no "
                "registered formula for netting it, so it will not compute "
                "a discounted amount. Enter the net amount if the question "
                "asks for one."
            )
        if len(_MULTI_AMOUNT_RE.findall(q)) > 1:
            concerns.append(
                "More than one amount appears in this sentence. If the "
                "amounts belong to different lines, enter the transaction "
                "in standard wording or submit the journal entry directly."
            )

    if classification.get("domain") == DOMAIN_BOOKKEEPING and fact_rows:
        info_notes.append(
            "This looks like a book-keeping question. The numbers are kept "
            "as facts, but the accounting treatment comes from the "
            "transaction wording, not from the numbers alone."
        )

    requested_uncertain = bool(classification.get("requested_uncertain"))
    if requested_uncertain:
        concerns.insert(0, str(classification.get("reason")
                               or "The requested figure is unclear."))

    return {
        "classified": classification.get("domain") != DOMAIN_UNRECOGNISED,
        "domain": classification.get("domain"),
        "kind": classification.get("kind"),
        "metric": classification.get("metric"),
        "requested": classification.get("metric"),
        "requested_uncertain": requested_uncertain,
        "reason": classification.get("reason"),
        "facts": fact_rows,
        "interpretation": _interpretation(classification, fact_rows, q),
        "concerns": concerns,
        "info_notes": info_notes,
        "status": (
            REVIEW_REQUIRED if (concerns or requested_uncertain) else
            (VERIFIED if classification.get("domain") != DOMAIN_UNRECOGNISED
             else REVIEW_REQUIRED)
        ),
    }


def _interpretation(classification: Dict[str, Any],
                    fact_rows: List[Dict[str, Any]],
                    question: str) -> str:
    """A one-paragraph student-readable reading of what FT-E understood."""
    if not question:
        return "No question was provided."
    domain = classification.get("domain")
    if domain == DOMAIN_MATHS:
        metric = classification.get("metric")
        if metric:
            from backend.maths.fyjc_maths import known_concept_display
            display = known_concept_display(metric) or metric
            return (
                f"FT-E reads this as a Maths question asking for "
                f"**{display}** (Requested: {display})."
            )
        if classification.get("requested_uncertain"):
            return (
                "FT-E sees a numerical Maths question, but the requested "
                "figure is unclear - it will ask which figure to calculate "
                "rather than guess."
            )
        return (
            "FT-E reads this as a numerical Maths question, but no "
            "registered metric was matched - it will refuse unless the "
            "metric is one FT-E can compute."
        )
    if domain == DOMAIN_BOOKKEEPING:
        kind = classification.get("kind")
        kind_label = {
            KIND_JOURNAL: "a journal-entry task",
            KIND_LEDGER: "a ledger-posting task",
            KIND_TRIAL_BALANCE: "a trial-balance task",
            KIND_TRANSACTION: "a transaction analysis",
        }.get(kind, "a book-keeping task")
        return (
            f"FT-E reads this as **{kind_label}** (Book-Keeping & "
            "Accountancy)."
        )
    return (
        "FT-E could not reliably identify the question type. It will not "
        "guess - type the question in standard wording, or ask for a "
        "metric FT-E supports."
    )


# ---------------------------------------------------------------------------
# Maths flow (Sprint 14 section 5)
# ---------------------------------------------------------------------------


def run_fyjc_maths_flow(metric: str,
                        facts: Optional[Dict[str, Any]] = None,
                        text: Optional[str] = None,
                        documents: Optional[List[Dict[str, Any]]] = None,
                        student_answer: Any = None) -> Dict[str, Any]:
    """Run the student Maths journey for one registered metric.

    Every number is produced by verify_maths_answer -> solve_strict ->
    the C++ mathematical authority. The result is shaped into the
    student steps: Given / Required / Formula / Substitution / C++
    confirmation / Final answer, plus a technical audit payload for the
    expandable 'audit' section.
    """
    outcome = verify_maths_answer(
        metric, facts=facts, text=text, documents=documents,
        student_answer=student_answer,
    )
    status = outcome.get("status") or BLOCKED
    resolved = bool(outcome.get("resolved"))

    inputs = outcome.get("inputs") or []
    substitution = [
        f"{row.get('concept')} = {_fmt_amount(row.get('value'))}"
        for row in inputs if row.get("value") is not None
    ]

    steps: List[Dict[str, Any]] = [
        {
            "number": 1,
            "title": "Given",
            "body": (
                [f"{row.get('concept')}: {_fmt_amount(row.get('value'))}"
                 for row in inputs]
                or ["No verified inputs were found for this question."]
            ),
        },
        {
            "number": 2,
            "title": "Required",
            "body": [str(outcome.get("concept")
                          or outcome.get("metric") or metric)],
        },
        {
            "number": 3,
            "title": "Formula",
            "body": [str(outcome.get("formula") or "—"),
                     f"Formula ID: {str(outcome.get('formula_id') or '—')}"],
        },
        {
            "number": 4,
            "title": "Substitution",
            "body": substitution or ["—"],
        },
        {
            "number": 5,
            "title": "C++ Calculation",
            "body": (
                ["⚙️ Deterministic calculation verified - the arithmetic "
                 "was executed by the C++ mathematical authority."]
                if resolved
                else ["No calculation was performed (see the refusal below)."]
            ),
        },
        {
            "number": 6,
            "title": "Final Answer",
            "body": [str(outcome.get("display_value") or "—")],
        },
    ]

    return {
        "flow": "maths",
        "metric": str(outcome.get("metric") or metric),
        "concept": outcome.get("concept"),
        "resolved": resolved,
        "verdict": outcome.get("verdict"),
        "status": status,
        "status_label": STATUS_WORDS.get(status, status),
        "authority_state": outcome.get("authority_state"),
        "steps": steps,
        "outcome": outcome,
        "why_not": outcome.get("why_not"),
        "next_action": outcome.get("next_action"),
        "verification_hint": outcome.get("verification_hint"),
        "audit": {
            "formula_id": outcome.get("formula_id"),
            "formula": outcome.get("formula"),
            "inputs": inputs,
            "result": outcome.get("display_value"),
            "status": status,
            "authority": "cpp" if resolved else outcome.get("authority_state"),
            "student_answer": outcome.get("student_answer"),
            "student_display": outcome.get("student_display"),
            "correct_answer": outcome.get("correct_answer"),
            "mismatch": outcome.get("mismatch"),
        },
    }


# ---------------------------------------------------------------------------
# Accounting flow (Sprint 14 section 6 - steps 1-8)
# ---------------------------------------------------------------------------


def _entry_from_classification(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a Sprint 13 classified transaction into a journal entry dict
    accepted by post_ledger / build_trial_balance."""
    return {
        "debits": [
            {"account": line.get("account"), "amount": line.get("amount")}
            for line in outcome.get("debit_lines") or []
            if line.get("account")
        ],
        "credits": [
            {"account": line.get("account"), "amount": line.get("amount")}
            for line in outcome.get("credit_lines") or []
            if line.get("account")
        ],
    }


def run_fyjc_accounting_flow(description: str,
                             amount: Any = None) -> Dict[str, Any]:
    """Run the student Book-Keeping journey for one transaction.

    Sprint 15I-O: the accounting TREATMENT comes exclusively from the
    hardened FT-E engine (reason_bk_question via
    hardened_bookkeeping_outcome) - the same authority the QuestionBank
    / PracticeEngine path uses. Steps 5-8 are the journal entry, ledger
    effect, trial-balance effect and verification over that SAME
    canonical treatment. No financial result is ever calculated by
    Python, and no accounting rule lives in this module.
    """
    outcome = hardened_bookkeeping_outcome(description, amount)
    status = outcome.get("status")
    resolved = status == VERIFIED

    steps: List[Dict[str, Any]] = []

    # Step 1 - identify accounts
    accounts = [line.get("account") for line in
                (outcome.get("debit_lines") or []) + (outcome.get("credit_lines") or [])
                if line.get("account")]
    steps.append({
        "number": 1,
        "title": "Identify Accounts",
        "body": accounts or ["FT-E could not identify the accounts."],
    })

    # Step 2 - classify accounts (modern role + FYJC traditional class)
    classification_rows = []
    for line in (outcome.get("debit_lines") or []) + (outcome.get("credit_lines") or []):
        account = line.get("account")
        if not account:
            continue
        role = line.get("role") or account_role(account)
        classification_rows.append({
            "account": account,
            "modern_role": role,
            "traditional_class": fyjc_traditional_class(role, account),
        })
    steps.append({
        "number": 2,
        "title": "Classify Accounts",
        "body": [
            # Sprint 15I-R: a party (no modern role) is displayed as a
            # Personal Account - never 'None (FYJC class: Personal)'.
            (f"{row['account']} → Personal Account"
             if row["modern_role"] is None
             else f"{row['account']} → {row['modern_role']} "
                  f"(FYJC class: {row['traditional_class']})")
            for row in classification_rows
        ] or ["—"],
    })

    # Step 3 - golden rule
    steps.append({
        "number": 3,
        "title": "Apply the Golden Rule",
        "body": [str(outcome.get("rule") or "—")],
    })

    # Step 4 - debit / credit decision with WHY
    dr_cr_rows = [
        {
            "account": line.get("account"),
            "side": line.get("side"),
            "amount": _fmt_amount(line.get("amount")),
            "why": line.get("side_hint") or (
                "Debit the receiver / credit the giver (personal account)."
            ),
        }
        for line in (outcome.get("debit_lines") or []) + (outcome.get("credit_lines") or [])
    ]
    steps.append({
        "number": 4,
        "title": "Debit / Credit Decision",
        "body": [
            (f"{row['side'].upper()} {row['account']} "
             f"({row['amount']}) — {row['why']}")
            for row in dr_cr_rows
        ] or ["The debit/credit treatment could not be determined."],
    })

    # Step 5 - journal entry
    journal_rows = [
        {
            "account": line.get("account"),
            "side": line.get("side"),
            "amount": _fmt_amount(line.get("amount")),
            "role": line.get("role"),
        }
        for line in (outcome.get("debit_lines") or []) + (outcome.get("credit_lines") or [])
    ]
    steps.append({
        "number": 5,
        "title": "Journal Entry",
        "body": (
            [
                (f"{row['side'].upper()}  {row['account']}  "
                 f"{row['amount']}")
                for row in journal_rows
            ]
            if journal_rows
            else ["The journal entry could not be produced."]
        ),
    })

    # Steps 6-8 - ledger / trial balance / verification over the treatment
    entry = _entry_from_classification(outcome)
    ledger = None
    trial_balance = None
    verification = None
    if resolved and entry.get("debits") and entry.get("credits"):
        ledger = post_ledger([entry])
        trial_balance = build_trial_balance([entry])
        verification = verify_arithmetic([
            {"side": line.get("side"), "amount": line.get("amount")}
            for line in (outcome.get("debit_lines") or []) +
                        (outcome.get("credit_lines") or [])
        ])

    steps.append({
        "number": 6,
        "title": "Ledger Effect",
        "body": (
            [
                (f"{account}: Dr {row.get('debit'):,.2f} / "
                 f"Cr {row.get('credit'):,.2f} → balance "
                 f"{row.get('balance_side')} {row.get('balance'):,.2f}")
                for account, row in sorted((ledger or {}).get("accounts", {}).items())
            ]
            if ledger
            else ["Ledger posting is not available for this outcome."]
        ),
    })

    steps.append({
        "number": 7,
        "title": "Trial Balance Effect",
        "body": (
            [
                f"Total Dr {trial_balance.get('total_debit'):,.2f} = "
                f"Total Cr {trial_balance.get('total_credit'):,.2f} "
                + ("✓ TALLIES" if trial_balance.get("balanced")
                   else "✗ DOES NOT TALLY")
            ]
            if trial_balance
            else ["Trial-balance construction requires a resolved entry."]
        ),
    })

    steps.append({
        "number": 8,
        "title": "Verification",
        "body": (
            [
                (f"Debit total {verification.get('total_debit'):,.2f} = "
                 f"Credit total {verification.get('total_credit'):,.2f} → "
                 f"{verification.get('verdict')}")
            ]
            if verification
            else ["Verification is not applicable to this outcome."]
        ),
    })

    return {
        "flow": "accounting",
        "status": status,
        "status_label": outcome.get("status_label"),
        "rule_key": outcome.get("rule_key"),
        "resolved": resolved,
        "steps": steps,
        "outcome": outcome,
        "ledger": ledger,
        "trial_balance": trial_balance,
        "verification": verification,
        "why_not": outcome.get("why_not"),
        "next_action": outcome.get("next_action"),
        "audit": {
            "rule_key": outcome.get("rule_key"),
            "rule": outcome.get("rule"),
            "debit_lines": outcome.get("debit_lines"),
            "credit_lines": outcome.get("credit_lines"),
            "status": status,
            "authority": "bookkeeping",
        },
    }


# ---------------------------------------------------------------------------
# Master journey (Sprint 14 sections 3-7)
# ---------------------------------------------------------------------------


def run_fyjc_student_flow(question: str,
                          text: Optional[str] = None,
                          facts: Optional[Dict[str, Any]] = None,
                          documents: Optional[List[Dict[str, Any]]] = None,
                          student_answer: Any = None,
                          amount: Any = None) -> Dict[str, Any]:
    """One entry point for the whole student journey.

    question   - the question as typed / pasted / extracted.
    text       - optional 'Concept: value' lines (Tier-1 evidence).
    facts      - optional {concept: value} facts (Tier-1 evidence).
    documents  - optional uploaded-document evidence records.
    student_answer - optional student number to verify (Maths only).
    amount     - optional explicit amount for a transaction (Book-Keeping).

    Returns a flow dict (maths / accounting) or a refusal dict
    (BLOCKED / REVIEW_REQUIRED / UNSUPPORTED) - always student-readable.
    """
    understanding = build_understanding(question)
    domain = understanding.get("domain")

    if domain == DOMAIN_UNRECOGNISED:
        return {
            "flow": "refusal",
            "status": "UNSUPPORTED",
            "status_label": "🟡 NOT SUPPORTED YET",
            "authority_state": "unsupported",
            "understanding": understanding,
            "what": "FT-E could not reliably identify the question type.",
            "why_not": (
                "FT-E only answers supported Maths metrics and Book-Keeping "
                "& Accountancy questions (journal, ledger, trial balance, "
                "transaction analysis). No answer was generated - it never "
                "guesses."
            ),
            "next_action": (
                "Re-type the question in standard wording - e.g. 'Calculate "
                "the Current Ratio. Current Assets Rs.5,00,000 and Current "
                "Liabilities Rs.2,50,000.' or 'Purchased goods from Rahul on "
                "credit for Rs.10,000.'"
            ),
            "audit": {"authority": "unsupported"},
        }

    if domain == DOMAIN_MATHS:
        metric = understanding.get("metric")
        if not metric:
            if understanding.get("requested_uncertain"):
                # Sprint 15: an uncertain requested concept is REVIEW_
                # REQUIRED - FT-E never guesses which figure is asked for.
                return {
                    "flow": "refusal",
                    "status": REVIEW_REQUIRED,
                    "status_label": STATUS_WORDS.get(
                        REVIEW_REQUIRED, REVIEW_REQUIRED),
                    "authority_state": "review_required",
                    "understanding": understanding,
                    "what": (
                        "FT-E is not certain which figure this question "
                        "asks for."
                    ),
                    "why_not": (
                        understanding.get("reason")
                        or "Multiple possible requested figures were found. "
                        "FT-E does not guess."
                    ),
                    "next_action": (
                        "Re-type the question naming the figure explicitly - "
                        "e.g. 'Calculate the Profit Margin' or 'Find the "
                        "missing figure: Expenses'."
                    ),
                    "audit": {"authority": "review_required"},
                }
            return {
                "flow": "refusal",
                "status": "UNSUPPORTED",
                "status_label": "🟡 NOT SUPPORTED YET",
                "authority_state": "unsupported",
                "understanding": understanding,
                "what": "No supported Maths metric was detected.",
                "why_not": (
                    "This looks like a numerical Maths question, but FT-E "
                    "supports only the registered financial relationships "
                    "(profit, margins, ratios, EPS, CAGR ...). No new "
                    "formulas were added and no calculation is invented."
                ),
                "next_action": (
                    "Name the metric explicitly (e.g. 'Calculate the Profit "
                    "Margin') or choose a supported topic from the FYJC "
                    "study list."
                ),
                "audit": {"authority": "unsupported"},
            }
        flow = run_fyjc_maths_flow(
            metric, facts=facts, text=text or question,
            documents=documents, student_answer=student_answer,
        )
        flow["understanding"] = understanding
        return flow

    if domain == DOMAIN_BOOKKEEPING:
        kind = understanding.get("kind")
        if kind in (KIND_LEDGER, KIND_TRIAL_BALANCE):
            # The treatment of a ledger/trial-balance task still flows
            # through the transaction analysis path when the description
            # contains transactions; otherwise a student-readable refusal.
            flow = run_fyjc_accounting_flow(question, amount)
            flow["understanding"] = understanding
            flow["task_kind"] = kind
            return flow
        flow = run_fyjc_accounting_flow(question, amount)
        flow["understanding"] = understanding
        return flow

    return {
        "flow": "refusal",
        "status": BLOCKED,
        "status_label": STATUS_WORDS.get(BLOCKED, BLOCKED),
        "authority_state": "blocked",
        "understanding": understanding,
        "what": "FT-E could not proceed with this question.",
        "why_not": str(understanding.get("reason") or ""),
        "next_action": "Re-type the question in standard wording.",
        "audit": {"authority": "blocked"},
    }


# ---------------------------------------------------------------------------
# Independent verification helpers (Sprint 14 section 8)
# ---------------------------------------------------------------------------

# Trial-balance line: "Account,Dr-amount,Cr-amount" or
# "Account | Dr amount | Cr amount". Deterministic, never guesses.
_TB_LINE_RE = re.compile(
    r"^\s*(.+?)\s*(?:,|\||;)\s*(?:(?:Dr|Dr\.|Debit)\s*)?([\d,]+(?:\.\d+)?)\s*"
    r"(?:,|\||;)\s*(?:(?:Cr|Cr\.|Credit)\s*)?([\d,]+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)


def parse_trial_balance_lines(text: str) -> List[Dict[str, Any]]:
    """Parse student trial-balance lines 'Account, Dr, Cr' into rows."""
    rows: List[Dict[str, Any]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TB_LINE_RE.match(line)
        if not m:
            continue
        account, debit_s, credit_s = m.group(1).strip(), m.group(2), m.group(3)
        try:
            debit = float(debit_s.replace(",", ""))
            credit = float(credit_s.replace(",", ""))
        except ValueError:
            continue
        rows.append({"account": account, "debit": debit, "credit": credit})
    return rows


def verify_student_journal(description: str,
                           debit_accounts: List[str],
                           debit_amounts: List[Any],
                           credit_accounts: List[str],
                           credit_amounts: List[Any]) -> Dict[str, Any]:
    """Verify a student's journal entry (from UI line inputs)."""
    entry = {
        "debits": [
            {"account": a, "amount": v}
            for a, v in zip(debit_accounts, debit_amounts)
            if str(a or "").strip() and v not in (None, "")
        ],
        "credits": [
            {"account": a, "amount": v}
            for a, v in zip(credit_accounts, credit_amounts)
            if str(a or "").strip() and v not in (None, "")
        ],
    }
    return verify_journal_entry(description, entry)


def verify_student_ledger(account: str, student_balance: Any,
                          student_side: str,
                          entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify a student's ledger balance against engine-posted entries."""
    return verify_ledger_balance(
        account, student_balance, student_side, entries
    )


def verify_student_trial_balance(text: str,
                                 entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify a student's trial balance (typed lines) against the ledger."""
    rows = parse_trial_balance_lines(text)
    if not rows:
        return {
            "verdict": "REFUSED",
            "status": BLOCKED,
            "status_label": STATUS_WORDS.get(BLOCKED, BLOCKED),
            "what": "No trial-balance lines could be read.",
            "why_not": (
                "Enter one account per line as: "
                "Account, Dr amount, Cr amount (e.g. 'Cash, 50000, 0')."
            ),
            "next_action": "Re-enter the trial balance in that format.",
            "discrepancy": None,
        }
    return verify_trial_balance(rows, entries)


def parse_numeric(value: Any) -> Optional[float]:
    """Parse a student-entered number through the 12D normalizer."""
    if value in (None, ""):
        return None
    parsed = parse_numeric_text(value)
    if parsed is None or parsed.value is None or parsed.ambiguity:
        return None
    return float(parsed.value)


# ---------------------------------------------------------------------------
# Study surface (Sprint 14 section 5 - 'choose a supported topic')
# ---------------------------------------------------------------------------


def fyjc_study_topics() -> Dict[str, List[str]]:
    """Student-facing list of what FT-E can verify for the FYJC exam."""
    from backend.maths.fyjc_maths import supported_metric_names
    return {
        "maths": sorted(supported_metric_names()),
        "bookkeeping": [
            "Golden Rules & Debit/Credit reasoning",
            "Account classification",
            "Journal Entries",
            "Ledger Posting & balances",
            "Trial Balance & tally",
            "Arithmetic / error verification",
        ],
    }
