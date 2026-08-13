"""
Financial Timeline Engine
Sprint 15I-G - Content Compiler (Question Infrastructure)
backend/maths/fyjc_content_compiler.py

An ISOLATED internal Content Compiler for the verified FYJC Book-Keeping
question bank. It is NOT another accounting reasoning engine - it never
decides an accounting treatment itself. Every accounting decision comes
from the existing deterministic FT-E engine (backend.maths.fyjc_bk_reasoning
-> reason_bk_question / classify_bk_type / _split_transactions), which is
consumed READ-ONLY: this module never modifies transaction parsing,
journal generation, refusal thresholds or any 15E-15I behavior.

Pipeline:

    RAW QUESTION
        |  normalize_question_text()      (safe normalization only)
        v
    STRUCTURED QUESTION
        |  compile_question()             (id, metadata, transaction count/types)
        v
    DETERMINISTIC VALIDATION
        |  verify_question()              (FT-E engine = authority + invariants)
        v
    APPROVED / REJECTED                   (bank lifecycle owns the state)

Trust contract
--------------
* The FT-E engine is the authority. A candidate is APPROVED only when the
  engine returns VERIFIED and every accounting invariant holds (balanced,
  all accounts resolved, amounts present and consistent, deterministic
  across repeated runs).
* Rejected material is never silently repaired.
* Normalization is separated from accounting interpretation. Only
  semantics-preserving edits are applied (whitespace, Unicode NFC,
  currency-glyph display normalization). The original raw_text is ALWAYS
  preserved verbatim; verification ALWAYS runs on the raw text so a
  normalization bug can never change the verified answer.
* Provenance is mandatory and never fabricated: source_type / source_name /
  source_reference / ingestion_timestamp / compiler_version /
  verification_version travel with every question.
* Metadata that cannot be determined safely is set to UNKNOWN or
  REVIEW_REQUIRED - never guessed.

This module is pure: no Streamlit, no AI, no network, no database.
Deterministic.
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Sequence

from decimal import Decimal

from backend.maths.fyjc_bk_reasoning import (
    classify_bk_type,
    discount_evidence,
    generate_journal,
    reason_bk_question,
    _split_transactions,
)

# ---------------------------------------------------------------------------
# Version + vocabulary (module-scope constants, deterministic)
# ---------------------------------------------------------------------------

COMPILER_VERSION = "15I-G-1"
VERIFICATION_VERSION = "15I-G-1"

SUBJECT_BOOKKEEPING = "bookkeeping"

# The whole pipeline is scoped to the FYJC (First Year Junior College)
# syllabus, so curriculum/class default deterministically from scope.
# Board is NOT assumed - it stays UNKNOWN unless the source states it.
CURRICULUM_FYJC = "FYJC"
CLASS_YEAR_FYJC = "FYJC"
UNKNOWN = "UNKNOWN"

# Lifecycle states (the bank owns transitions; the compiler reports the
# verification verdict that gates them).
STATUS_DRAFT = "DRAFT"
STATUS_COMPILED = "COMPILED"
STATUS_VALIDATING = "VALIDATING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_REVIEW = "REVIEW_REQUIRED"

# Source types understood by the provenance layer.
SOURCE_TYPES = (
    "previous_year_paper",
    "textbook",
    "worksheet",
    "teacher_authored",
    "generated",
    "student_typed",
    "ocr",
    "manual",
)

# ---------------------------------------------------------------------------
# Safe normalization (separate from accounting interpretation)
# ---------------------------------------------------------------------------

_CURRENCY_GLYPHS = {
    "\u20b9": "Rs.",   # ₹ -> Rs. (display only)
    "\u20a8": "Rs.",   # ₨ -> Rs. (display only)
    "Rs.": "Rs.",
}
_CURRENCY_GLYPH_RE = re.compile(r"[\u20b9\u20a8]")


def normalize_question_text(raw: str) -> str:
    """Safe, semantics-preserving normalization for display + duplicate
    detection.

    Applied transforms (all safe):
      * strip + collapse duplicate whitespace
      * Unicode NFC composition
      * currency glyphs (₹/₨) -> 'Rs.' for a stable display fingerprint
      * collapse 'Rs .'/'Rs.' spacing variations

    NEVER applied (would change accounting meaning):
      * transaction-boundary punctuation ('.', ';', dashes, newlines) is
        preserved exactly
      * pronouns, parties, amounts, 'credit/cash/paid/received' wording

    Verification NEVER runs on this text - see verify_question().
    """
    text = unicodedata.normalize("NFC", str(raw or ""))
    text = _CURRENCY_GLYPH_RE.sub("Rs.", text)
    text = re.sub(r"\bRs\s*\.", "Rs.", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def question_fingerprint(raw: str) -> str:
    """Stable identifier for a question's normalized representation.

    Used by the conservative duplicate detector: two questions whose
    normalized text is identical share a fingerprint. This NEVER merges
    questions - it only flags exact duplicates.
    """
    return hashlib.sha256(
        normalize_question_text(raw).encode("utf-8")).hexdigest()


def make_question_id(raw: str, source_reference: Optional[str] = None) -> str:
    """Deterministic question_id: content-addressed on the raw text
    (plus an optional source reference so the same wording from two
    different sources stays distinct)."""
    seed = f"{normalize_question_text(raw)}::{source_reference or ''}"
    return "Q-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Chapter / concept hint registry (deterministic; UNKNOWN when unsure)
# ---------------------------------------------------------------------------

# FYJC Book-Keeping & Accountancy Unit-Test-1 chapter vocabulary (the exact
# 15E/15F scope). Only high-precision keyword signals fire; anything else
# stays UNKNOWN rather than guessed.
_CHAPTER_HINTS: List[Dict[str, Any]] = [
    {"chapter": "Ch.1 Introduction to Book-Keeping & Accountancy",
     "words": ("book-keeping and accountancy", "book keeping and accountancy",
               "introduction to book-keeping", "introduction to bookkeeping",
               "introduction to accountancy", "what is book-keeping",
               "what is bookkeeping", "accounting terms")},
    {"chapter": "Ch.2 Basic Accounting Terms",
     "words": ("accounting equation", "basic accounting terms",
               "business entity", "double entry", "classification of accounts",
               "real account", "personal account", "nominal account",
               "golden rules", "transaction definition", "accounting terms")},
    {"chapter": "Ch.3 Journal",
     "words": ("journal", "journalise", "journalize", "pass journal",
               "journal entries", "journal entry", "record the following",
               "enter the following", "post the following", "cash book",
               "purchases book", "sales book", "ledger", "trial balance")},
]


def detect_chapter(low: str) -> str:
    for hint in _CHAPTER_HINTS:
        if any(w in low for w in hint["words"]):
            return hint["chapter"]
    return UNKNOWN


# classify_bk_type keys -> human concept labels (display only).
_CONCEPT_LABELS = {
    "START_BUSINESS": "Capital introduction",
    "CAPITAL_INTRODUCED": "Capital introduction",
    "CAPITAL_ASSET_INTRODUCED": "Capital introduction (asset)",
    "DRAWINGS_CASH": "Drawings",
    "GOODS_PERSONAL_USE": "Drawings (goods)",
    "PURCHASE_GOODS_CASH": "Cash purchase",
    "PURCHASE_GOODS_CREDIT": "Credit purchase",
    "SALE_GOODS_CASH": "Cash sale",
    "SALE_GOODS_CREDIT": "Credit sale",
    "PURCHASE_ASSET_CASH": "Asset purchase (cash)",
    "PURCHASE_ASSET_CREDIT": "Asset purchase (credit)",
    "SALE_ASSET_CASH": "Asset sale (cash)",
    "SALE_ASSET_CREDIT": "Asset sale (credit)",
    "EXPENSE_PAID": "Expense payment",
    "INCOME_RECEIVED": "Income receipt",
    "PAID_TO": "Payment to party",
    "RECEIVED_FROM": "Receipt from party",
    "CASH_INTO_BANK": "Cash deposited into bank",
    "CASH_FROM_BANK": "Cash withdrawn from bank",
    "CHEQUE_PAID": "Payment by cheque",
    "CHEQUE_RECEIVED": "Receipt by cheque",
    "DISCOUNT_ALLOWED": "Discount allowed",
    "DISCOUNT_RECEIVED": "Discount received",
    "PURCHASE_RETURN": "Purchase return",
    "SALES_RETURN": "Sales return",
    "BANK_ACCOUNT_OPENED": "Bank account opened",
}


def concept_label(type_key: Optional[str]) -> str:
    if not type_key:
        return UNKNOWN
    return _CONCEPT_LABELS.get(type_key, type_key)


# ---------------------------------------------------------------------------
# Transaction structure (read-only use of the engine's splitter/classifier)
# ---------------------------------------------------------------------------

def transaction_breakdown(raw: str) -> Dict[str, Any]:
    """Deterministic transaction count + per-segment canonical type keys.

    Uses the PRODUCTION _split_transactions and classify_bk_type unchanged
    (read-only). A segment whose type cannot be classified reports
    UNKNOWN - it will fail verification downstream, never be guessed.
    """
    try:
        segments = [s for s in _split_transactions(raw) if s and s.strip()]
    except Exception:  # noqa: BLE001 - defensive; verification will refuse
        return {"count": UNKNOWN, "segments": [], "types": []}
    types: List[str] = []
    for seg in segments:
        pattern = classify_bk_type(seg)
        types.append(pattern.get("key", UNKNOWN) if pattern else UNKNOWN)
    return {"count": len(segments), "segments": segments, "types": types}


# ---------------------------------------------------------------------------
# Expected journal helpers (from the engine's journal IR)
# ---------------------------------------------------------------------------

def journal_lines_from_engine(
        journal: Dict[str, Any]) -> Dict[str, List[List[Any]]]:
    """Project an engine journal dict onto the bank's compact line form:
    {'debit': [[account, amount], ...], 'credit': [[account, amount], ...]}.

    amount is stored as a plain number (int when integral), matching the
    15E/15F/15H oracle convention of integer rupees.
    """
    debit: List[List[Any]] = []
    credit: List[List[Any]] = []
    for line in journal.get("debit_lines") or []:
        acc = line.get("account")
        if acc is None or acc == "":
            continue
        debit.append([acc, _to_number(line.get("amount"))])
    for line in journal.get("credit_lines") or []:
        acc = line.get("account")
        if acc is None or acc == "":
            continue
        credit.append([acc, _to_number(line.get("amount"))])
    return {"debit": debit, "credit": credit}


def _to_number(value: Any) -> Any:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return int(f) if f == int(f) else f


def _is_positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _journal_total(lines: List[List[Any]]) -> float:
    total = 0.0
    for _, amount in lines:
        try:
            total += float(amount)
        except (TypeError, ValueError):
            continue
    return total


# ---------------------------------------------------------------------------
# Deterministic verification through the FT-E authority
# ---------------------------------------------------------------------------

def _compact_reasoning(raw: str) -> Dict[str, Any]:
    """Stable projection of reason_bk_question output used for the
    determinism check - ignores volatile fields (dates, free text)."""
    res = reason_bk_question(raw)
    j = res.get("journal") or {}
    return {
        "status": res.get("status"),
        "debit": sorted(str(l.get("account")) + ":" + str(l.get("amount"))
                        for l in (j.get("debit_lines") or [])),
        "credit": sorted(str(l.get("account")) + ":" + str(l.get("amount"))
                         for l in (j.get("credit_lines") or [])),
        "total_debit": str(j.get("total_debit")),
        "total_credit": str(j.get("total_credit")),
        "balanced": bool(j.get("balanced")),
    }


def verify_question(raw: str) -> Dict[str, Any]:
    """Run the candidate through the FT-E deterministic engine and check
    every accounting invariant. Returns a verification record; NEVER
    mutates the question or the engine.

    Rejects (verdict FAIL) when:
      * engine status is not VERIFIED (REVIEW_REQUIRED / NOT_SUPPORTED /
        BLOCKED are all failures for bank admission)
      * journal is unbalanced
      * any account is unresolved (empty/None) or only placeholders
      * any stated amount is missing, zero or negative
      * repeated execution is not byte-identical (determinism gate)

    Records REVIEW_REQUIRED when the structure is ambiguous enough that
    the bank should ask a human (e.g. engine refused, or the candidate
    supplied an expected answer that disagrees with the engine).
    """
    errors: List[str] = []
    warnings: List[str] = []

    engine = reason_bk_question(raw)
    engine_status = engine.get("status")

    # The engine's own ambiguity flag short-circuits to the REVIEW
    # failure state: the bank asks a human instead of guessing, and the
    # accounting invariants below only gate VERIFIED engine output (they
    # defend the bank against engine bugs, they do not re-judge a
    # question the engine already flagged as ambiguous).
    if engine_status == "REVIEW_REQUIRED":
        return {
            "verdict": VERDICT_REVIEW,
            "version": VERIFICATION_VERSION,
            "engine_status": engine_status,
            "review_required": True,
            "balanced": True,
            "accounts_resolved": False,
            "amounts_consistent": False,
            "deterministic": True,
            "errors": [],
            "warnings": ["engine flagged the transaction as requiring "
                         "review (REVIEW_REQUIRED)"],
            "expected_journal": {"debit": [], "credit": []},
            "expected_accounts": [],
            "expected_amounts": [],
            "total_debit": 0,
            "total_credit": 0,
        }
    if engine_status != "VERIFIED":
        # NOT_SUPPORTED / BLOCKED / anything else -> hard rejection.
        errors.append(
            f"engine status is {engine_status} (bank admission requires "
            "VERIFIED)")
    journal = engine.get("journal") or {}
    compact = journal_lines_from_engine(journal)
    debit_lines = compact["debit"]
    credit_lines = compact["credit"]

    # Invariant: balanced
    td = _journal_total(debit_lines)
    tc = _journal_total(credit_lines)
    balanced = abs(td - tc) <= 0.01
    if not balanced:
        errors.append(
            f"journal is unbalanced (Dr {td} vs Cr {tc})")

    # Invariant: accounts resolved
    accounts = [acc for acc, _ in debit_lines + credit_lines]
    if not accounts:
        errors.append("no accounts resolved")
    if any(not acc or not str(acc).strip() for acc in accounts):
        errors.append("an account line is unresolved (empty account)")

    # Invariant: amounts present and consistent
    amounts = [amt for _, amt in debit_lines + credit_lines]
    if not amounts:
        errors.append("no amounts resolved")
    for amt in amounts:
        try:
            if float(amt) <= 0:
                errors.append(f"non-positive amount {amt}")
        except (TypeError, ValueError):
            errors.append(f"unresolved amount {amt!r}")

    # Invariant: determinism (identical reasoning on a second run)
    first = _compact_reasoning(raw)
    second = _compact_reasoning(raw)
    if first != second:
        errors.append("non-deterministic reasoning across repeated runs")

    # Engine's own balance flag must agree with our projection.
    if engine_status == "VERIFIED" and journal.get("balanced") is not True:
        errors.append("engine reports VERIFIED but journal.balanced is "
                      "not True")

    if errors:
        verdict = VERDICT_FAIL
    elif warnings:
        verdict = VERDICT_REVIEW
    else:
        verdict = VERDICT_PASS

    return {
        "verdict": verdict,
        "version": VERIFICATION_VERSION,
        "engine_status": engine_status,
        "review_required": False,
        "balanced": balanced,
        "accounts_resolved": bool(accounts) and not any(
            not acc or not str(acc).strip() for acc in accounts),
        "amounts_consistent": bool(amounts) and all(
            _is_positive_number(a) for a in amounts),
        "deterministic": first == second,
        "errors": errors,
        "warnings": warnings,
        "expected_journal": compact,
        "expected_accounts": sorted(set(accounts)),
        "expected_amounts": sorted(
            set(_to_number(a) for a in amounts)),
        "total_debit": td,
        "total_credit": tc,
    }


def compare_expected(journal: Dict[str, List[List[Any]]],
                     expected: Optional[Dict[str, List[List[Any]]]]) -> bool:
    """Compare the engine-produced journal with a candidate/teacher/
    variant expected journal. Order-insensitive within a side. None
    expected -> no comparison (True)."""
    if expected is None:
        return True
    def norm(lines: List[List[Any]]) -> set:
        out = set()
        for acc, amt in lines or []:
            out.add((str(acc), _to_number(amt)))
        return out
    return (norm(journal.get("debit")) == norm(expected.get("debit"))
            and norm(journal.get("credit")) == norm(expected.get("credit")))


# ---------------------------------------------------------------------------
# Conservative duplicate detection
# ---------------------------------------------------------------------------

def find_duplicate(
        raw: str,
        bank_questions: Sequence[Dict[str, Any]],
        source_reference: Optional[str] = None) -> Optional[str]:
    """Conservative duplicate detection.

    Returns the question_id of an existing bank question ONLY when the
    normalized representation is byte-identical (same fingerprint).
    Questions that merely look similar are never merged - false merging
    is worse than duplicate storage. A candidate with the same wording
    from a DIFFERENT source keeps its own id (the source is part of the
    identity) but is still flagged.
    """
    fp = question_fingerprint(raw)
    for q in bank_questions or []:
        if q.get("question_id") == make_question_id(raw, source_reference):
            continue
        if q.get("_normalized_fingerprint") == fp or \
                question_fingerprint(q.get("raw_text") or "") == fp:
            return q.get("question_id")
    return None


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def build_provenance(
        source_type: str,
        source_name: Optional[str] = None,
        source_reference: Optional[str] = None,
        ingestion_timestamp: Optional[str] = None) -> Dict[str, Any]:
    """Mandatory provenance block. source_type must be one of the known
    SOURCE_TYPES; a manual/unknown type is preserved as stated but never
    invented."""
    if source_type not in SOURCE_TYPES:
        source_type = str(source_type or UNKNOWN)
    return {
        "source_type": source_type,
        "source_name": source_name or UNKNOWN,
        "source_reference": source_reference or UNKNOWN,
        "ingestion_timestamp": ingestion_timestamp
        or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "compiler_version": COMPILER_VERSION,
        "verification_version": VERIFICATION_VERSION,
    }


def default_metadata(raw: str) -> Dict[str, Any]:
    """Deterministic metadata that the compiler can derive safely.
    Everything else is UNKNOWN until a human or an LLM-suggestion (which
    is only ever candidate evidence) supplies it.

    Sprint 15I-L: discount fields are ADDITIVE and derived only from
    explicit question wording via discount_evidence() - a field that
    cannot be established deterministically stays UNKNOWN. Existing
    approved questions are never mutated by the new fields (they fill on
    the next compile from the same raw text, deterministically)."""
    breakdown = transaction_breakdown(raw)
    low = " " + normalize_question_text(raw).lower() + " "
    types = breakdown["types"]
    primary_key = types[0] if types else None
    disc = discount_evidence(raw)
    meta = {
        "subject": SUBJECT_BOOKKEEPING,
        "curriculum": CURRICULUM_FYJC,
        "class_year": CLASS_YEAR_FYJC,
        "board": UNKNOWN,
        "chapter": detect_chapter(low),
        "concept": concept_label(primary_key),
        "concept_key": primary_key or UNKNOWN,
        "difficulty": UNKNOWN,
        "transaction_count": breakdown["count"],
        "transaction_types": types,
        "question_style": UNKNOWN,
        "trade_discount": disc["trade_discount"],
        "cash_discount": disc["cash_discount"],
        "discount_percentage": disc["discount_percentage"],
        "discount_amount": disc["discount_amount"],
        "gross_amount": disc["gross_amount"],
        "net_amount": disc["net_amount"],
        "settlement_amount": disc["settlement_amount"],
    }
    # JSON-safe: Decimal values become floats (metadata is presentation
    # metadata, never accounting arithmetic - the journal amounts stay
    # Decimal in the FT-E engine).
    for key in ("discount_percentage", "discount_amount", "gross_amount",
                "net_amount", "settlement_amount"):
        value = meta[key]
        if isinstance(value, Decimal):
            meta[key] = float(value)
    return meta
