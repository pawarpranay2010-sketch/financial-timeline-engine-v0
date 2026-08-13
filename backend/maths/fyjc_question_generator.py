"""
Financial Timeline Engine
Sprint 15I-M - Verified Automatic Question Generation
backend/maths/fyjc_question_generator.py

A deterministic, auditable candidate-generation pipeline on top of the
existing 15I-G Content Compiler / Question Bank, 15I-H Practice/Mastery
system and the released 15I-J/K/L reasoning capabilities.

Authority chain (unchanged; this module adds NO accounting rules):

    GENERATION REQUEST
        -> CANDIDATE GENERATOR            (untrusted candidate producer)
        -> PARAMETER VALIDATION           (deterministic; invalid -> REJECTED)
        -> CONTENT COMPILER / BANK        (15I-G lifecycle, reused unchanged)
        -> FT-E DETERMINISTIC ENGINE      (verify_question = sole authority)
        -> QUALITY / SAFETY GATES
        -> DUPLICATE / VARIANT ANALYSIS
        -> APPROVED / REVIEW_REQUIRED / REJECTED / DUPLICATE

The generator NEVER:
  * determines the canonical journal (FT-E does; a generator/LLM-supplied
    expected journal is CANDIDATE EVIDENCE only - disagreement forces
    REVIEW_REQUIRED, never a silent override);
  * marks a question APPROVED directly (only QuestionBank.approve_question
    can, and it re-runs deterministic verification);
  * modifies the reasoning engine or historical fixtures;
  * bypasses the Content Compiler or the QuestionBank lifecycle;
  * generates concepts the engine cannot deterministically verify
    (unsupported requests are refused honestly at request level).

Reproducibility: the same GenerationRequest + seed + generator version
produce the same candidate set and the same batch statistics. Every
candidate persists its request, seed, generator version, candidate
fingerprint and verification fingerprint with the question.

Dry-run mode: generate -> compile -> verify -> gates -> duplicate analysis
with NO mutation of the permanent QuestionBank (the lifecycle runs on an
isolated scratch store).

Deterministic difficulty assignment (documented rule, not a guess):
  * band 1: single-entry, two-account transactions;
  * band 2: entries with a deterministic adjustment (GST, trade discount,
    or a simple split);
  * band 3: compound/settlement entries (cash discount settlements and
    multi-transaction narratives).
A difficulty that cannot be established by this rule stays UNKNOWN.

This module is pure: no Streamlit, no AI calls (the LLM adapter is an
injected, optional, untrusted callable), no network. Deterministic.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend.maths.fyjc_accounting import canonical_account
from backend.maths.fyjc_content_compiler import (
    UNKNOWN,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_REVIEW,
    compare_expected,
    concept_label,
    normalize_question_text,
    question_fingerprint,
    transaction_breakdown,
    verify_question,
)
from backend.maths.fyjc_question_bank import (
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
    STATUS_VALIDATING,
    QuestionBank,
)

# ---------------------------------------------------------------------------
# Version + scope constants
# ---------------------------------------------------------------------------

GENERATOR_VERSION = "15I-M-1"
GENERATOR_TYPE_DETERMINISTIC = "deterministic"
GENERATOR_TYPE_LLM = "llm"
SOURCE_TYPE_GENERATED = "generated"
DIFFICULTY_BANDS = (1, 2, 3)

# Conservative set of party names drawn from the verified corpus (15E/15F/
# 15H) - the party resolver has proven coverage for these exact names.
PARTIES = ("Ram", "Mohan", "Rahul", "Amit", "Anil", "Vijay", "Ramesh",
           "Suresh")

# Expense word -> canonical account (mirrors the engine's own deterministic
# expense map; the engine remains the authority - a mismatch surfaces as
# REVIEW_REQUIRED through the teacher-expected comparison).
EXPENSES: Tuple[Tuple[str, str], ...] = (
    ("rent", "Rent"), ("salaries", "Salaries"), ("wages", "Wages"),
    ("electricity", "Electricity"), ("stationery", "Stationery"),
    ("telephone", "Telephone Expenses"), ("postage", "Postage"),
    ("insurance", "Insurance"), ("carriage", "Carriage Inward"),
    ("conveyance", "Conveyance"), ("repairs", "Repairs"),
    ("printing", "Printing"), ("mobile", "Telephone Expenses"),
)

# Income word -> canonical account (engine resolution: 'Received X' ->
# 'X Received').
INCOMES: Tuple[Tuple[str, str], ...] = (
    ("commission", "Commission Received"), ("interest", "Interest Received"),
    ("rent", "Rent Received"), ("dividend", "Dividend Received"),
)

# Asset word -> canonical account (engine resolves 'computer' -> Equipment).
ASSETS: Tuple[Tuple[str, str], ...] = (
    ("furniture", "Furniture"), ("machinery", "Machinery"),
    ("computer", "Equipment"), ("equipment", "Equipment"),
)

# Deterministic difficulty rule (documented; see module docstring).
_DIFFICULTY_BY_FAMILY = {
    "START_BUSINESS": 1, "CAPITAL_INTRODUCED": 1, "DRAWINGS_CASH": 1,
    "PURCHASE_GOODS_CASH": 1, "PURCHASE_GOODS_CREDIT": 1,
    "SALE_GOODS_CASH": 1, "SALE_GOODS_CREDIT": 1, "EXPENSE_PAID": 1,
    "INCOME_RECEIVED": 1, "PAID_TO": 1, "RECEIVED_FROM": 1,
    "CASH_INTO_BANK": 1, "CASH_FROM_BANK": 1, "CHEQUE_PAID": 1,
    "CHEQUE_RECEIVED": 1, "PURCHASE_RETURN": 1, "SALES_RETURN": 1,
    "DISCOUNT_ALLOWED": 1, "DISCOUNT_RECEIVED": 1,
    "PURCHASE_ASSET_CASH": 1, "PURCHASE_ASSET_CREDIT": 1,
    "SALE_ASSET_CASH": 1, "SALE_ASSET_CREDIT": 1,
    "GOODS_PERSONAL_USE": 1, "FREE_SAMPLES": 1,
    "INTEREST_ON_CAPITAL": 1, "INTEREST_ON_DRAWINGS": 1,
    "LOAN_TAKEN": 1, "LOAN_REPAID": 1,
    "GST_PURCHASE_CASH": 2, "GST_PURCHASE_CREDIT": 2,
    "GST_SALE_CASH": 2, "GST_SALE_CREDIT": 2, "GST_EXPENSE": 2,
    "TD_PURCHASE_CASH": 2, "TD_PURCHASE_CREDIT": 2, "TD_SALE_CREDIT": 2,
    "TD_PURCHASE_CASH_AMOUNT": 2,
    "CD_RECEIVE_ALLOWED": 3, "CD_PAY_RECEIVED": 3,
    "CD_SALE_SETTLEMENT": 3, "CD_PURCHASE_SETTLEMENT": 3,
}

# ---------------------------------------------------------------------------
# GenerationRequest
# ---------------------------------------------------------------------------


@dataclass
class GenerationRequest:
    """A reproducible generation request.

    Unknown fields are preserved as None (never invented). board stays
    UNKNOWN unless supplied. count is the number of candidates requested.
    """
    count: int = 1
    seed: int = 0
    subject: Optional[str] = "bookkeeping"
    curriculum: Optional[str] = "FYJC"
    board: Optional[str] = None
    class_year: Optional[str] = "FYJC"
    chapter: Optional[str] = None
    concept: Optional[str] = None
    transaction_type: Optional[str] = None
    difficulty: Optional[Any] = None
    question_style: Optional[str] = None
    transaction_count: Optional[int] = None
    language: Optional[str] = None
    canonical_id: Optional[str] = None
    source_reference: Optional[str] = None
    tags: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        """Content-addressed request identity (all fields; deterministic)."""
        payload = json.dumps(self.to_dict(), sort_keys=True,
                             separators=(",", ":"))
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GeneratorParameterError(ValueError):
    """Invalid generated parameters - the candidate is REJECTED, never
    silently repaired."""


class UnsupportedGenerationRequest(ValueError):
    """The request asks for content FT-E cannot deterministically verify.
    Refused honestly at request level."""


# ---------------------------------------------------------------------------
# Parameter drawing + validation (seeded, deterministic)
# ---------------------------------------------------------------------------


def _fmt_rupees(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


def _num(value: Any) -> Decimal:
    return Decimal(str(value))


def _draw_amount(rng: random.Random) -> int:
    # Round hundreds keep every generated journal clean (whole rupees).
    return rng.randrange(2000, 20001, 500)


def _draw_params(family: Dict[str, Any], variant: Dict[str, Any],
                 rng: random.Random) -> Dict[str, Any]:
    """Draw a validated parameter set for one variant. Values come from
    bounded generators; a combination that cannot be stated coherently
    raises GeneratorParameterError (-> REJECTED)."""
    params: Dict[str, Any] = {}
    for name in variant.get("params", []):
        if name == "amount":
            params["amount"] = _draw_amount(rng)
        elif name == "party":
            params["party"] = rng.choice(PARTIES)
        elif name == "expense":
            params["expense"], params["expense_account"] = rng.choice(
                EXPENSES)
        elif name == "income":
            params["income"], params["income_account"] = rng.choice(INCOMES)
        elif name == "asset":
            params["asset"], params["asset_account"] = rng.choice(ASSETS)
        elif name == "pct":
            params["pct"] = rng.choice((5, 10, 15, 20))
        elif name == "disc":
            amount = params.get("amount")
            if amount is None:
                raise GeneratorParameterError(
                    "cash-discount parameter requires an amount first")
            disc = rng.choice((50, 100, 150, 200))
            if disc >= amount:
                raise GeneratorParameterError(
                    f"discount {disc} not smaller than amount {amount}")
            params["disc"] = disc
            params["cash"] = int(_num(amount) - disc)
        else:
            raise GeneratorParameterError(
                f"unknown parameter {name!r} in family {family['key']}")
    _validate_params(family, params)
    return params


def _validate_params(family: Dict[str, Any], params: Dict[str, Any]) -> None:
    """Deterministic parameter validation (spec section 5). Invalid data
    is REJECTED, never silently repaired. Returns None or raises."""
    key = family["key"]
    amount = params.get("amount")
    if amount is not None:
        try:
            if _num(amount) <= 0:
                raise GeneratorParameterError("amount must be positive")
        except (InvalidOperation, ValueError):
            raise GeneratorParameterError("amount is not numeric")
    if "pct" in params:
        pct = _num(params["pct"])
        if not (0 < pct < 100):
            raise GeneratorParameterError(
                "discount percentage must be in (0, 100)")
        # net value must stay a positive whole-rupee figure
        net = _num(params["amount"]) - _num(params["amount"]) * pct / 100
        if net <= 0 or net != net.to_integral_value():
            raise GeneratorParameterError(
                "trade discount makes a non-positive/non-integral net")
    if "disc" in params:
        disc = _num(params["disc"])
        amount = _num(params["amount"])
        if not (0 < disc < amount):
            raise GeneratorParameterError(
                "cash discount must be positive and smaller than the "
                "transaction value")
        if amount != (amount - disc) + disc:
            raise GeneratorParameterError("contradictory settlement amounts")
    if key.startswith("GST_"):
        # templates use the verified surface only: CGST @ 9% + SGST @ 9%
        # (total 18%) or IGST @ 18%. Component amounts must be exact
        # hundredths (round rupees).
        total = _num(params["amount"]) * Decimal("18") / Decimal("100")
        if total != total.quantize(Decimal("0.01")):
            raise GeneratorParameterError("GST amount is not exact")
    if key.startswith("CD_"):
        amount = _num(params["amount"])
        cash = params.get("cash")
        disc = params.get("disc")
        if cash is None or disc is None:
            raise GeneratorParameterError("CD requires cash and discount")
        if _num(cash) + _num(disc) != amount:
            raise GeneratorParameterError(
                "cash + discount must equal the transaction value")


# ---------------------------------------------------------------------------
# Template family registry
# ---------------------------------------------------------------------------
# Every wording is drawn from the VERIFIED corpus (15E/15F/15H) or the
# 15I-K/15I-L gates - no invented phrasing enters the bank. Each family
# declares its transaction_count and the account surface it may produce
# (used by the no-invented-account gate).

FAMILIES: List[Dict[str, Any]] = [
    {"key": "START_BUSINESS", "concept": "Capital introduction", "count": 1,
     "accounts": ["Cash", "Bank", "Capital"],
     "variants": [
         {"template": "Started business with cash Rs.{amount}.",
          "params": ["amount"]}]},
    {"key": "CAPITAL_INTRODUCED", "concept": "Capital introduction", "count": 1,
     "accounts": ["Cash", "Bank", "Capital"],
     "variants": [
         {"template": "Brought in additional capital of Rs.{amount} in cash.",
          "params": ["amount"]}]},
    {"key": "DRAWINGS_CASH", "concept": "Drawings", "count": 1,
     "accounts": ["Drawings", "Cash", "Bank"],
     "variants": [
         {"template": "Withdrew cash Rs.{amount} for personal use.",
          "params": ["amount"]}]},
    {"key": "PURCHASE_GOODS_CASH", "concept": "Cash purchase", "count": 1,
     "accounts": ["Purchases", "Cash", "Bank"],
     "variants": [
         {"template": "Purchased goods for cash Rs.{amount}.",
          "params": ["amount"]},
         {"template": "Purchased goods for cash Rs.{amount} from {party}.",
          "params": ["party", "amount"]}]},
    {"key": "PURCHASE_GOODS_CREDIT", "concept": "Credit purchase", "count": 1,
     "accounts": ["Purchases"],
     "variants": [
         {"template": "Purchased goods from {party} on credit Rs.{amount}.",
          "params": ["party", "amount"]}]},
    {"key": "SALE_GOODS_CASH", "concept": "Cash sale", "count": 1,
     "accounts": ["Cash", "Bank", "Sales"],
     "variants": [
         {"template": "Sold goods for cash Rs.{amount}.",
          "params": ["amount"]}]},
    {"key": "SALE_GOODS_CREDIT", "concept": "Credit sale", "count": 1,
     "accounts": ["Sales"],
     "variants": [
         {"template": "Sold goods to {party} on credit Rs.{amount}.",
          "params": ["party", "amount"]}]},
    {"key": "EXPENSE_PAID", "concept": "Expense payment", "count": 1,
     "accounts": ["Cash", "Bank", "Rent", "Salaries", "Wages",
                   "Electricity", "Stationery", "Telephone Expenses",
                   "Postage", "Insurance", "Carriage Inward", "Conveyance",
                   "Repairs", "Printing"],
     "variants": [
         {"template": "Paid {expense} Rs.{amount}.",
          "params": ["expense", "amount"]},
         {"template": "Paid {expense} Rs.{amount} in cash.",
          "params": ["expense", "amount"]}]},
    {"key": "INCOME_RECEIVED", "concept": "Income receipt", "count": 1,
     "accounts": ["Cash", "Bank", "Commission Received",
                   "Interest Received", "Rent Received", "Dividend Received"],
     "variants": [
         {"template": "Received {income} Rs.{amount}.",
          "params": ["income", "amount"]}]},
    {"key": "PAID_TO", "concept": "Payment to party", "count": 1,
     "accounts": ["Cash", "Bank"],
     "variants": [
         {"template": "Paid to {party} Rs.{amount} in cash.",
          "params": ["party", "amount"]}]},
    {"key": "RECEIVED_FROM", "concept": "Receipt from party", "count": 1,
     "accounts": ["Cash", "Bank"],
     "variants": [
         {"template": "Received Rs.{amount} from {party} in cash.",
          "params": ["party", "amount"]}]},
    {"key": "CASH_INTO_BANK", "concept": "Cash deposited into bank",
     "count": 1, "accounts": ["Bank", "Cash"],
     "variants": [
         {"template": "Deposited cash into bank Rs.{amount}.",
          "params": ["amount"]}]},
    {"key": "CASH_FROM_BANK", "concept": "Cash withdrawn from bank",
     "count": 1, "accounts": ["Cash", "Bank"],
     "variants": [
         {"template": "Withdrew cash from bank Rs.{amount}.",
          "params": ["amount"]}]},
    {"key": "CHEQUE_PAID", "concept": "Payment by cheque", "count": 1,
     "accounts": ["Bank"],
     "variants": [
         {"template": "Paid {party} by cheque Rs.{amount}.",
          "params": ["party", "amount"]}]},
    {"key": "CHEQUE_RECEIVED", "concept": "Receipt by cheque", "count": 1,
     "accounts": ["Bank"],
     "variants": [
         {"template": "Received a cheque from {party} Rs.{amount}.",
          "params": ["party", "amount"]}]},
    {"key": "PURCHASE_RETURN", "concept": "Purchase return", "count": 1,
     "accounts": ["Purchase Returns"],
     "variants": [
         {"template": "Returned goods to {party} Rs.{amount}.",
          "params": ["party", "amount"]}]},
    {"key": "SALES_RETURN", "concept": "Sales return", "count": 1,
     "accounts": ["Sales Returns"],
     "variants": [
         {"template": "Goods returned by {party} Rs.{amount}.",
          "params": ["party", "amount"]}]},
    {"key": "DISCOUNT_ALLOWED", "concept": "Discount allowed", "count": 1,
     "accounts": ["Discount Allowed"],
     "variants": [
         {"template": "Discount allowed to {party} Rs.{amount}.",
          "params": ["party", "amount"]}]},
    {"key": "DISCOUNT_RECEIVED", "concept": "Discount received", "count": 1,
     "accounts": ["Discount Received"],
     "variants": [
         {"template": "Discount received from {party} Rs.{amount}.",
          "params": ["party", "amount"]}]},
    {"key": "PURCHASE_ASSET_CASH", "concept": "Asset purchase (cash)",
     "count": 1, "accounts": ["Cash", "Bank", "Furniture", "Machinery",
                              "Equipment"],
     "variants": [
         {"template": "Bought {asset} for cash Rs.{amount}.",
          "params": ["asset", "amount"]}]},
    {"key": "PURCHASE_ASSET_CREDIT", "concept": "Asset purchase (credit)",
     "count": 1, "accounts": ["Furniture", "Machinery", "Equipment"],
     "variants": [
         {"template": "Bought {asset} from {party} on credit Rs.{amount}.",
          "params": ["asset", "party", "amount"]}]},
    {"key": "SALE_ASSET_CASH", "concept": "Asset sale (cash)", "count": 1,
     "accounts": ["Cash", "Bank", "Furniture", "Machinery", "Equipment"],
     "variants": [
         {"template": "Sold old {asset} for cash Rs.{amount}.",
          "params": ["asset", "amount"]}]},
    {"key": "SALE_ASSET_CREDIT", "concept": "Asset sale (credit)", "count": 1,
     "accounts": ["Furniture", "Machinery", "Equipment"],
     "variants": [
         {"template": "Sold old {asset} to {party} on credit Rs.{amount}.",
          "params": ["asset", "party", "amount"]}]},
    {"key": "GOODS_PERSONAL_USE", "concept": "Drawings (goods)", "count": 1,
     "accounts": ["Drawings", "Purchases"],
     "variants": [
         {"template": "Withdrew goods worth Rs.{amount} for personal use.",
          "params": ["amount"]}]},
    {"key": "FREE_SAMPLES", "concept": "Goods as free samples", "count": 1,
     "accounts": ["Advertisement", "Purchases"],
     "variants": [
         {"template": "Goods distributed as free samples Rs.{amount}.",
          "params": ["amount"]}]},
    {"key": "INTEREST_ON_CAPITAL", "concept": "Interest on capital",
     "count": 1, "accounts": ["Interest on Capital", "Capital"],
     "variants": [
         {"template": "Interest on capital allowed Rs.{amount}.",
          "params": ["amount"]}]},
    {"key": "INTEREST_ON_DRAWINGS", "concept": "Interest on drawings",
     "count": 1, "accounts": ["Drawings", "Interest on Drawings"],
     "variants": [
         {"template": "Interest on drawings charged Rs.{amount}.",
          "params": ["amount"]}]},
    {"key": "LOAN_TAKEN", "concept": "Loan taken", "count": 1,
     "accounts": ["Bank", "Cash", "Loan"],
     "variants": [
         {"template": "Took a loan from bank Rs.{amount}.",
          "params": ["amount"]}]},
    {"key": "LOAN_REPAID", "concept": "Loan repaid", "count": 1,
     "accounts": ["Loan", "Bank", "Cash"],
     "variants": [
         {"template": "Repaid the bank loan Rs.{amount}.",
          "params": ["amount"]}]},
    # ---- 15I-K GST surface (gate-verified wordings only) ----
    {"key": "GST_PURCHASE_CASH", "concept": "GST cash purchase",
     "base_keys": ["PURCHASE_GOODS_CASH"], "count": 1,
     "accounts": ["Purchases", "Input CGST", "Input SGST", "Cash", "Bank"],
     "variants": [
         {"template": "Purchased goods for cash Rs.{amount}, CGST @ 9% "
                      "and SGST @ 9%.",
          "params": ["amount"]}]},
    {"key": "GST_PURCHASE_CREDIT", "concept": "GST credit purchase",
     "base_keys": ["PURCHASE_GOODS_CREDIT"], "count": 1,
     "accounts": ["Purchases", "Input CGST", "Input SGST"],
     "variants": [
         {"template": "Purchased goods from {party} on credit Rs.{amount}, "
                      "CGST @ 9% and SGST @ 9%.",
          "params": ["party", "amount"]}]},
    {"key": "GST_SALE_CASH", "concept": "GST cash sale",
     "base_keys": ["SALE_GOODS_CASH"], "count": 1,
     "accounts": ["Cash", "Bank", "Sales", "Output CGST", "Output SGST"],
     "variants": [
         {"template": "Sold goods for cash Rs.{amount}, CGST @ 9% and "
                      "SGST @ 9%.",
          "params": ["amount"]}]},
    {"key": "GST_SALE_CREDIT", "concept": "GST credit sale",
     "base_keys": ["SALE_GOODS_CREDIT"], "count": 1,
     "accounts": ["Sales", "Output IGST"],
     "variants": [
         {"template": "Sold goods to {party} on credit Rs.{amount}, "
                      "IGST @ 18%.",
          "params": ["party", "amount"]}]},
    {"key": "GST_EXPENSE", "concept": "GST expense",
     "base_keys": ["EXPENSE_PAID"], "count": 1,
     "accounts": ["Input CGST", "Input SGST", "Cash", "Bank",
                   "Rent", "Salaries", "Wages", "Electricity",
                   "Stationery", "Telephone Expenses", "Postage",
                   "Insurance", "Carriage Inward", "Conveyance",
                   "Repairs", "Printing"],
     "variants": [
         {"template": "Paid {expense} Rs.{amount}, CGST @ 9% and SGST @ 9%.",
          "params": ["expense", "amount"]}]},
    # ---- 15I-L Trade Discount surface (gate-verified wordings only) ----
    {"key": "TD_PURCHASE_CASH", "concept": "Trade discount purchase",
     "base_keys": ["PURCHASE_GOODS_CASH"], "count": 1,
     "accounts": ["Purchases", "Cash", "Bank"],
     "variants": [
         {"template": "Purchased goods listed at Rs.{amount} less {pct}% "
                      "trade discount for cash.",
          "params": ["amount", "pct"]}]},
    {"key": "TD_PURCHASE_CREDIT", "concept": "Trade discount purchase",
     "base_keys": ["PURCHASE_GOODS_CREDIT"], "count": 1,
     "accounts": ["Purchases"],
     "variants": [
         {"template": "Purchased goods from {party} on credit Rs.{amount} "
                      "less {pct}% TD.",
          "params": ["party", "amount", "pct"]}]},
    {"key": "TD_SALE_CREDIT", "concept": "Trade discount sale",
     "base_keys": ["SALE_GOODS_CREDIT"], "count": 1,
     "accounts": ["Sales"],
     "variants": [
         {"template": "Sold goods to {party} on credit Rs.{amount} at "
                      "{pct}% trade discount.",
          "params": ["party", "amount", "pct"]}]},
    {"key": "TD_PURCHASE_CASH_AMOUNT", "concept": "Trade discount purchase",
     "base_keys": ["PURCHASE_GOODS_CASH"], "count": 1,
     "accounts": ["Purchases", "Cash", "Bank"],
     "variants": [
         {"template": "Purchased goods from {party} for cash Rs.{amount} "
                      "less Rs.{disc} trade discount.",
          "params": ["party", "amount", "disc"]}]},
    # ---- 15I-L Cash Discount surface (gate-verified wordings only) ----
    {"key": "CD_RECEIVE_ALLOWED", "concept": "Cash discount allowed",
     "base_keys": ["RECEIVED_FROM"], "count": 1,
     "accounts": ["Cash", "Bank", "Discount Allowed"],
     "variants": [
         {"template": "Received from {party} Rs.{cash}, discount allowed "
                      "Rs.{disc}.",
          "params": ["party", "amount", "disc"]}]},
    {"key": "CD_PAY_RECEIVED", "concept": "Cash discount received",
     "base_keys": ["PAID_TO"], "count": 1,
     "accounts": ["Cash", "Bank", "Discount Received"],
     "variants": [
         {"template": "Paid to {party} Rs.{cash}, discount received "
                      "Rs.{disc}.",
          "params": ["party", "amount", "disc"]}]},
    {"key": "CD_SALE_SETTLEMENT", "concept": "Credit sale settlement",
     "base_keys": ["SALE_GOODS_CREDIT"], "count": 2,
     "accounts": ["Cash", "Bank", "Discount Allowed", "Sales"],
     "variants": [
         {"template": "Sold goods to {party} for Rs.{amount} on credit. "
                      "Received Rs.{cash} from {party} and allowed "
                      "Rs.{disc} cash discount.",
          "params": ["party", "amount", "disc"]}]},
    {"key": "CD_PURCHASE_SETTLEMENT", "concept": "Credit purchase settlement",
     "base_keys": ["PURCHASE_GOODS_CREDIT"], "count": 2,
     "accounts": ["Purchases", "Cash", "Bank",
                              "Discount Received"],
     "variants": [
         {"template": "Purchased goods from {party} for Rs.{amount} on "
                      "credit. Paid Rs.{cash} and received Rs.{disc} cash "
                      "discount.",
          "params": ["party", "amount", "disc"]}]},
]

_FAMILY_BY_KEY = {f["key"]: f for f in FAMILIES}
_SUPPORTED_CONCEPT_KEYS = frozenset(_FAMILY_BY_KEY)


# ---------------------------------------------------------------------------
# Candidate evidence: the template's own expected journal (NEVER authority)
# ---------------------------------------------------------------------------


def expected_journal_for(family_key: str,
                         params: Dict[str, Any]) -> Dict[str, Any]:
    """The template's deterministic expected journal from its parameters.

    This is CANDIDATE EVIDENCE passed to the bank's teacher-expected slot.
    The bank compares it against the FT-E canonical journal at validation:
    disagreement forces REVIEW_REQUIRED - the engine result always wins.
    """
    a = _num(params["amount"])
    if family_key == "START_BUSINESS":
        return {"debit": [["Cash", int(a)]], "credit": [["Capital", int(a)]]}
    if family_key == "CAPITAL_INTRODUCED":
        return {"debit": [["Cash", int(a)]], "credit": [["Capital", int(a)]]}
    if family_key == "DRAWINGS_CASH":
        return {"debit": [["Drawings", int(a)]],
                "credit": [["Cash", int(a)]]}
    if family_key in ("PURCHASE_GOODS_CASH", "PURCHASE_ASSET_CASH"):
        account = params.get("asset_account", "Purchases")
        return {"debit": [[account, int(a)]],
                "credit": [["Cash", int(a)]]}
    if family_key == "PURCHASE_GOODS_CREDIT":
        return {"debit": [["Purchases", int(a)]],
                "credit": [[params["party"], int(a)]]}
    if family_key in ("SALE_GOODS_CASH", "SALE_ASSET_CASH"):
        account = params.get("asset_account", "Sales")
        return {"debit": [["Cash", int(a)]],
                "credit": [[account, int(a)]]}
    if family_key == "SALE_GOODS_CREDIT":
        return {"debit": [[params["party"], int(a)]],
                "credit": [["Sales", int(a)]]}
    if family_key == "EXPENSE_PAID":
        account = params["expense_account"]
        return {"debit": [[account, int(a)]],
                "credit": [["Cash", int(a)]]}
    if family_key == "GST_EXPENSE":
        account = params["expense_account"]
        comp = _q(a * Decimal("18") / 200)
        return {"debit": [[account, int(a)],
                          ["Input CGST", comp], ["Input SGST", comp]],
                "credit": [["Cash", int(a + 2 * comp)]]}
    if family_key == "INCOME_RECEIVED":
        return {"debit": [["Cash", int(a)]],
                "credit": [[params["income_account"], int(a)]]}
    if family_key == "PAID_TO" or family_key == "CHEQUE_PAID":
        credit = "Cash" if family_key == "PAID_TO" else "Bank"
        return {"debit": [[params["party"], int(a)]],
                "credit": [[credit, int(a)]]}
    if family_key == "RECEIVED_FROM" or family_key == "CHEQUE_RECEIVED":
        debit = "Cash" if family_key == "RECEIVED_FROM" else "Bank"
        return {"debit": [[debit, int(a)]],
                "credit": [[params["party"], int(a)]]}
    if family_key == "CASH_INTO_BANK":
        return {"debit": [["Bank", int(a)]], "credit": [["Cash", int(a)]]}
    if family_key == "CASH_FROM_BANK":
        return {"debit": [["Cash", int(a)]], "credit": [["Bank", int(a)]]}
    if family_key == "PURCHASE_RETURN":
        return {"debit": [[params["party"], int(a)]],
                "credit": [["Purchase Returns", int(a)]]}
    if family_key == "SALES_RETURN":
        return {"debit": [["Sales Returns", int(a)]],
                "credit": [[params["party"], int(a)]]}
    if family_key == "DISCOUNT_ALLOWED":
        return {"debit": [["Discount Allowed", int(a)]],
                "credit": [[params["party"], int(a)]]}
    if family_key == "DISCOUNT_RECEIVED":
        return {"debit": [[params["party"], int(a)]],
                "credit": [["Discount Received", int(a)]]}
    if family_key == "PURCHASE_ASSET_CREDIT":
        return {"debit": [[params["asset_account"], int(a)]],
                "credit": [[params["party"], int(a)]]}
    if family_key == "SALE_ASSET_CREDIT":
        return {"debit": [[params["party"], int(a)]],
                "credit": [[params["asset_account"], int(a)]]}
    if family_key == "GOODS_PERSONAL_USE":
        return {"debit": [["Drawings", int(a)]],
                "credit": [["Purchases", int(a)]]}
    if family_key == "FREE_SAMPLES":
        return {"debit": [["Advertisement", int(a)]],
                "credit": [["Purchases", int(a)]]}
    if family_key == "INTEREST_ON_CAPITAL":
        return {"debit": [["Interest on Capital", int(a)]],
                "credit": [["Capital", int(a)]]}
    if family_key == "INTEREST_ON_DRAWINGS":
        return {"debit": [["Drawings", int(a)]],
                "credit": [["Interest on Drawings", int(a)]]}
    if family_key == "LOAN_TAKEN":
        return {"debit": [["Bank", int(a)]], "credit": [["Loan", int(a)]]}
    if family_key == "LOAN_REPAID":
        return {"debit": [["Loan", int(a)]], "credit": [["Bank", int(a)]]}
    # ---- 15I-K GST (verified surface: CGST @ 9% + SGST @ 9%, IGST @ 18%) ----
    if family_key in ("GST_PURCHASE_CASH", "GST_PURCHASE_CREDIT"):
        comp = _q(a * Decimal("18") / 200)
        debit = [["Purchases", int(a)], ["Input CGST", comp],
                 ["Input SGST", comp]]
        if family_key == "GST_PURCHASE_CASH":
            return {"debit": debit, "credit": [["Cash", int(a + 2 * comp)]]}
        return {"debit": debit,
                "credit": [[params["party"], int(a + 2 * comp)]]}
    if family_key == "GST_SALE_CASH":
        comp = _q(a * Decimal("18") / 200)
        return {"debit": [["Cash", int(a + 2 * comp)]],
                "credit": [["Sales", int(a)], ["Output CGST", comp],
                           ["Output SGST", comp]]}
    if family_key == "GST_SALE_CREDIT":
        tax = Decimal("18") / Decimal("100")
        return {"debit": [[params["party"], int(a + a * tax)]],
                "credit": [["Sales", int(a)],
                           ["Output IGST", int(a * tax)]]}
    # ---- 15I-L Trade Discount ----
    if family_key in ("TD_PURCHASE_CASH", "TD_PURCHASE_CREDIT",
                      "TD_SALE_CREDIT", "TD_PURCHASE_CASH_AMOUNT"):
        if family_key == "TD_PURCHASE_CASH_AMOUNT":
            net = int(a - _num(params["disc"]))
        else:
            net = int(a - a * _num(params["pct"]) / 100)
        if family_key == "TD_PURCHASE_CASH":
            return {"debit": [["Purchases", net]],
                    "credit": [["Cash", net]]}
        if family_key == "TD_PURCHASE_CREDIT":
            return {"debit": [["Purchases", net]],
                    "credit": [[params["party"], net]]}
        if family_key == "TD_SALE_CREDIT":
            return {"debit": [[params["party"], net]],
                    "credit": [["Sales", net]]}
        return {"debit": [["Purchases", net]],
                "credit": [["Cash", net]]}
    # ---- 15I-L Cash Discount ----
    if family_key == "CD_RECEIVE_ALLOWED":
        cash = int(a - _num(params["disc"]))
        disc = int(_num(params["disc"]))
        return {"debit": [["Cash", cash], ["Discount Allowed", disc]],
                "credit": [[params["party"], int(a)]]}
    if family_key == "CD_SALE_SETTLEMENT":
        # two-segment narrative: the credit-sale leg (party Dr / Sales Cr)
        # AND the settlement leg (Cash + Discount Allowed Dr / party Cr).
        cash = int(a - _num(params["disc"]))
        disc = int(_num(params["disc"]))
        return {"debit": [[params["party"], int(a)],
                          ["Cash", cash], ["Discount Allowed", disc]],
                "credit": [["Sales", int(a)],
                           [params["party"], int(a)]]}
    if family_key in ("CD_PAY_RECEIVED", "CD_PURCHASE_SETTLEMENT"):
        cash = int(a - _num(params["disc"]))
        disc = int(_num(params["disc"]))
        debit = ([[params["party"], int(a)]]
                 if family_key == "CD_PAY_RECEIVED"
                 else [["Purchases", int(a)]])
        return {"debit": debit,
                "credit": [["Cash", cash], ["Discount Received", disc]]}
    raise GeneratorParameterError(
        f"no expected journal rule for family {family_key}")


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Candidate generation (deterministic, seeded)
# ---------------------------------------------------------------------------


def _select_families(request: GenerationRequest) -> List[Dict[str, Any]]:
    """Filter the registry by the request's transaction_type / concept /
    difficulty / transaction_count. An unsupported request is refused
    honestly (UnsupportedGenerationRequest), never approximated."""
    if request.transaction_type is not None:
        wanted = str(request.transaction_type).strip().upper()
        if wanted not in _FAMILY_BY_KEY:
            raise UnsupportedGenerationRequest(
                f"transaction_type {request.transaction_type!r} is not a "
                "supported generation family (FT-E cannot deterministically "
                "verify it)")
        families = [_FAMILY_BY_KEY[wanted]]
    else:
        families = list(FAMILIES)
    if request.concept is not None:
        wanted = str(request.concept).strip().lower()
        matched = [f for f in families
                   if f["concept"].lower() == wanted
                   or concept_label(f["key"]).lower() == wanted]
        if not matched:
            raise UnsupportedGenerationRequest(
                f"concept {request.concept!r} is not supported by any "
                "generation family")
        families = matched
    if request.difficulty is not None:
        band = request.difficulty
        matched = [f for f in families
                   if _DIFFICULTY_BY_FAMILY[f["key"]] == band]
        if not matched:
            raise UnsupportedGenerationRequest(
                f"no generation family supports difficulty band {band}")
        families = matched
    if request.transaction_count is not None:
        matched = [f for f in families
                   if f["count"] == request.transaction_count]
        if not matched:
            raise UnsupportedGenerationRequest(
                f"no generation family supports transaction_count "
                f"{request.transaction_count}")
        families = matched
    return families


def _render_template(template: str, params: Dict[str, Any]) -> str:
    """Templates already carry the currency/percent signs ("Rs.{amount}",
    "{pct}%"); placeholders render the bare formatted value."""
    out = template
    for key, value in params.items():
        if key in ("amount", "disc", "cash"):
            out = out.replace("{" + key + "}", _fmt_rupees(value))
        else:
            out = out.replace("{" + key + "}", str(value))
    return out


def generate_candidates(request: GenerationRequest) -> List[Dict[str, Any]]:
    """Deterministic candidate generation.

    Same request + seed + generator version -> same candidate set. Each
    candidate carries its raw_text, family, parameters, candidate
    fingerprint and the template's expected journal (candidate evidence).
    Invalid parameter draws are recorded as REJECTED candidates with a
    reason - never silently repaired.
    """
    if request is None:
        raise ValueError("generate_candidates: request is required")
    if not isinstance(request, GenerationRequest):
        request = GenerationRequest(**dict(request))
    count = int(request.count)
    if count < 0:
        raise ValueError("count must be >= 0")
    families = _select_families(request)
    rng = random.Random(request.seed)
    candidates: List[Dict[str, Any]] = []
    seen: set = set()
    for i in range(count):
        family = families[i % len(families)]
        variant = rng.choice(family["variants"])
        params: Dict[str, Any] = {}
        raw = None
        reason = None
        # bounded retry: rotate parameters so a batch never contains the
        # identical normalized question twice
        for _ in range(25):
            try:
                params = _draw_params(family, variant, rng)
                raw = _render_template(variant["template"], params)
                fp = question_fingerprint(raw)
                if fp not in seen:
                    seen.add(fp)
                    break
                raw = None
            except GeneratorParameterError as exc:
                reason = f"invalid_parameters: {exc}"
                break
        if raw is None and reason is None:
            reason = "duplicate within request (parameter rotation exhausted)"
        entry = {
            "index": i,
            "family": family["key"],
            "concept": family["concept"],
            "params": params,
            "raw_text": raw,
            "candidate_fingerprint": (question_fingerprint(raw)
                                      if raw else None),
            "expected_journal": (expected_journal_for(family["key"], params)
                                 if raw and params else None),
            "rejected_reason": reason,
            "valid": raw is not None,
        }
        candidates.append(entry)
    return candidates


# ---------------------------------------------------------------------------
# Quality / safety gates (spec section 9)
# ---------------------------------------------------------------------------


def _account_is_valid(account: str, family: Dict[str, Any],
                      raw: str) -> bool:
    """A resolved account is valid when (a) its name appears verbatim in
    the wording (party accounts always do - the engine can never name a
    party that is not in the question), or (b) it is a chart account that
    is part of the family's declared account surface (covers resolved
    aliases such as computer -> Equipment). Anything else is treated as
    invented - the gate rejects rather than approves."""
    if not account or not str(account).strip():
        return False
    low = " " + str(raw or "").lower() + " "
    if re.search(r"\b" + re.escape(account.lower()) + r"\b", low):
        return True
    if canonical_account(account) is None:
        return False
    return account in family.get("accounts", [])


def _run_quality_gates(request: GenerationRequest, family: Dict[str, Any],
                       question: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """The A-L quality gates for one candidate (run on a VALIDATING
    question before approval). Returns (ok, reasons)."""
    reasons: List[str] = []
    verification = question.get("verification") or {}
    # A. accounting validity
    if verification.get("verdict") != VERDICT_PASS:
        reasons.append("A.accounting validity: verification is not PASS")
    # B. structural validity
    breakdown = transaction_breakdown(question.get("raw_text") or "")
    if breakdown.get("count") == UNKNOWN or not breakdown.get("segments"):
        reasons.append("B.structural validity: segments unresolved")
    if any(t == UNKNOWN for t in (breakdown.get("types") or [])):
        reasons.append("B.structural validity: an unclassified segment")
    # C. amount consistency
    if not verification.get("amounts_consistent"):
        reasons.append("C.amount consistency: non-positive/unresolved amounts")
    # D. transaction count consistency
    if (request.transaction_count is not None
            and breakdown.get("count") != request.transaction_count):
        reasons.append(
            f"D.transaction count: {breakdown.get('count')} != "
            f"requested {request.transaction_count}")
    # E. supported concept (the bank's concept_key is the ENGINE's
    # classify_bk_type key - an enhanced family may map to one base key)
    base_keys = family.get("base_keys") or [family["key"]]
    if question.get("concept_key") not in base_keys:
        reasons.append(f"E.supported concept: {question.get('concept_key')} "
                       f"not in {base_keys} for family {family['key']}")
    # F. supported difficulty
    band = _DIFFICULTY_BY_FAMILY.get(family["key"])
    if band not in DIFFICULTY_BANDS:
        reasons.append(f"F.supported difficulty: band {band!r} unknown")
    if (request.difficulty is not None and band != request.difficulty):
        reasons.append(f"F.supported difficulty: {band} != requested "
                       f"{request.difficulty}")
    # G. no unresolved ambiguity
    if question.get("status") == STATUS_REVIEW_REQUIRED or \
            verification.get("review_required"):
        reasons.append("G.ambiguity: engine flagged REVIEW_REQUIRED")
    # H. no invented account
    for acc in (verification.get("expected_accounts") or []):
        if not _account_is_valid(str(acc), family,
                                 question.get("raw_text") or ""):
            reasons.append(f"H.invented account: {acc!r}")
            break
    # I. no unbalanced canonical journal
    if not verification.get("balanced"):
        reasons.append("I.balanced: canonical journal is unbalanced")
    # J. no unsafe confident result
    if verification.get("engine_status") != "VERIFIED":
        reasons.append("J.unsafe confident: engine status not VERIFIED")
    # K. supported content (concept honesty)
    if request.concept is not None:
        wanted = str(request.concept).strip().lower()
        if (family["concept"].lower() != wanted
                and concept_label(family["key"]).lower() != wanted):
            reasons.append(f"K.supported content: family {family['key']} "
                           f"does not match requested concept {wanted}")
    # L. provenance complete
    src = question.get("source") or {}
    gen = question.get("generation") or {}
    if src.get("source_type") != SOURCE_TYPE_GENERATED:
        reasons.append("L.provenance: source_type is not 'generated'")
    for required in ("generator_version", "generation_seed",
                     "request_fingerprint", "candidate_fingerprint",
                     "verification_fingerprint"):
        if gen.get(required) is None:
            reasons.append(f"L.provenance: missing generation.{required}")
    return (not reasons, reasons)


# ---------------------------------------------------------------------------
# Duplicate / variant analysis (conservative, never merging)
# ---------------------------------------------------------------------------


def find_variant_of(question: Dict[str, Any],
                    existing: Sequence[Dict[str, Any]]) -> Optional[str]:
    """Same canonical journal + equivalent wording -> variant of the first
    existing APPROVED question that matches. Different canonical journals
    are never merged (false merging is worse than duplicate storage)."""
    mine = question.get("expected_journal")
    my_fp = question_fingerprint(question.get("raw_text") or "")
    for qa in existing or []:
        if qa.get("status") != STATUS_APPROVED:
            continue
        if qa.get("question_id") == question.get("question_id"):
            continue
        if question_fingerprint(qa.get("raw_text") or "") == my_fp:
            continue
        if compare_expected(mine, qa.get("expected_journal")):
            return qa.get("question_id")
    return None


def duplicate_fingerprints(
        questions: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """fingerprint -> question_id for the existing corpus (used to reject
    exact normalized duplicates without touching the bank)."""
    out: Dict[str, str] = {}
    for q in questions or []:
        fp = question_fingerprint(q.get("raw_text") or "")
        if fp:
            out.setdefault(fp, q.get("question_id") or "")
    return out


# ---------------------------------------------------------------------------
# LLM adapter boundary (untrusted candidate producer, optional)
# ---------------------------------------------------------------------------


def generate_llm_candidates(request: GenerationRequest,
                            llm_fn: Callable[[GenerationRequest], Any]
                            ) -> List[Dict[str, Any]]:
    """Optional LLM candidate interface.

    llm_fn(request) must return a list of candidate dicts with at most:
      raw_text (required), suggestions (teacher-editable metadata dict),
      expected_journal (compact journal dict - candidate evidence only).

    The adapter is UNTRUSTED: every candidate still flows through the
    Content Compiler + FT-E verification; any expected journal that
    disagrees with the engine forces REVIEW_REQUIRED. No API key is
    required - llm_fn is injected by the caller.
    """
    if llm_fn is None:
        return []
    raw = llm_fn(request)
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw or []):
        text = str((item or {}).get("raw_text") or "").strip()
        out.append({
            "index": idx,
            "family": "LLM",
            "concept": None,
            "params": {},
            "raw_text": text or None,
            "candidate_fingerprint": (question_fingerprint(text)
                                      if text else None),
            "expected_journal": (item or {}).get("expected_journal"),
            "suggestions": (item or {}).get("suggestions") or {},
            "rejected_reason": None if text else "empty llm candidate",
            "valid": bool(text),
        })
    return out


# ---------------------------------------------------------------------------
# Batch orchestration (the shared generate -> compile -> verify -> gate ->
# dedup -> approve pipeline)
# ---------------------------------------------------------------------------


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clean_compact(journal: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """JSON-safe compact journal: Decimal amounts become int (when
    integral) or float. The bank persists this dict, so no Decimal may
    survive into the store."""
    if not journal:
        return journal
    out: Dict[str, Any] = {}
    for side in ("debit", "credit"):
        lines: List[List[Any]] = []
        for line in (journal.get(side) or []):
            if not line or len(line) < 2:
                continue
            account, amount = line[0], line[1]
            if isinstance(amount, Decimal):
                amount = (int(amount) if amount == amount.to_integral_value()
                          else float(amount))
            lines.append([account, amount])
        out[side] = lines
    return out


def _verification_fingerprint(verification: Dict[str, Any]) -> str:
    projection = {
        "verdict": verification.get("verdict"),
        "engine_status": verification.get("engine_status"),
        "balanced": verification.get("balanced"),
        "accounts_resolved": verification.get("accounts_resolved"),
        "amounts_consistent": verification.get("amounts_consistent"),
        "deterministic": verification.get("deterministic"),
        "expected_journal": verification.get("expected_journal"),
        "total_debit": verification.get("total_debit"),
        "total_credit": verification.get("total_credit"),
    }
    payload = json.dumps(projection, sort_keys=True, separators=(",", ":"),
                         default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def generate_batch(
        request: Optional[GenerationRequest] = None,
        candidates: Optional[Sequence[Dict[str, Any]]] = None,
        bank: Optional[QuestionBank] = None,
        llm_fn: Optional[Callable[[GenerationRequest], Any]] = None,
        dry_run: bool = True,
        now_fn: Optional[Callable[[], str]] = None) -> Dict[str, Any]:
    """Run the full generation pipeline for one batch.

    request + llm_fn  -> LLM adapter path (candidate evidence).
    request alone     -> deterministic template path.
    candidates        -> direct candidate ingestion (adversarial tests).

    dry_run=True (default): the lifecycle runs on an isolated scratch
    QuestionBank and the provided `bank` is used READ-ONLY for duplicate /
    variant analysis. dry_run=False requires `bank` and appends APPROVED
    questions to it through the normal lifecycle.

    Returns the deterministic batch report (approved / rejected /
    review_required / duplicates / variants / evidence / provenance).
    """
    if request is None and candidates is None:
        raise ValueError("generate_batch: request or candidates required")
    if request is None:
        request = GenerationRequest()
    if not dry_run and bank is None:
        raise ValueError("generate_batch: live mode requires a bank")

    generator_type = GENERATOR_TYPE_DETERMINISTIC
    if llm_fn is not None:
        generated = generate_llm_candidates(request, llm_fn)
        generator_type = GENERATOR_TYPE_LLM
    elif candidates is not None:
        generated = list(candidates)
        generator_type = GENERATOR_TYPE_LLM if any(
            c.get("family") == "LLM" for c in candidates) \
            else GENERATOR_TYPE_DETERMINISTIC
    else:
        generated = generate_candidates(request)

    # Lifecycle store: scratch (dry-run) or the provided bank (live).
    scratch = None
    if dry_run:
        scratch = os.path.join(tempfile.gettempdir(),
                               f"fte_15im_scratch_{os.getpid()}_"
                               f"{abs(hash(request.fingerprint()))}.json")
        lifecycle_bank = QuestionBank(store_path=scratch)
    else:
        lifecycle_bank = bank

    # Duplicate corpus: the provided bank's questions (read-only in
    # dry-run) + anything already created in this batch.
    existing: List[Dict[str, Any]] = []
    if bank is not None:
        existing = bank.list_questions(include_internal=False)
    elif not dry_run:
        existing = lifecycle_bank.list_questions(include_internal=False)

    known_fps = duplicate_fingerprints(existing)
    # approved questions from the corpus (for variant analysis)
    approved_corpus = [q for q in existing
                       if q.get("status") == STATUS_APPROVED]

    report: Dict[str, Any] = {
        "generator_version": GENERATOR_VERSION,
        "generator_type": generator_type,
        "request": request.to_dict(),
        "request_fingerprint": request.fingerprint(),
        "seed": request.seed,
        "dry_run": bool(dry_run),
        "requested": len(generated),
        "generated_at": (now_fn or _now)(),
        "candidates": 0,
        "approved": 0,
        "rejected": 0,
        "review_required": 0,
        "duplicates": 0,
        "variants": 0,
        "rejected_reasons": {},
        "candidate_records": [],
        "verification_evidence": {"verified": 0, "review_required": 0,
                                  "failed": 0},
        "generation_stats": {"families": {}},
        "provenance": {
            "source_type": SOURCE_TYPE_GENERATED,
            "generator_type": generator_type,
            "generator_version": GENERATOR_VERSION,
            "seed": request.seed,
            "request_fingerprint": request.fingerprint(),
        },
    }
    reason_counts: Dict[str, int] = {}
    family_counts: Dict[str, int] = {}

    def _bump(reason_key: Optional[str]) -> None:
        if reason_key:
            reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1

    for candidate in generated:
        raw = str(candidate.get("raw_text") or "").strip()
        family_key = candidate.get("family") or "LLM"
        family = _FAMILY_BY_KEY.get(family_key) or {
            "key": family_key, "concept": concept_label(family_key),
            "count": 1, "accounts": []}
        family_counts[family_key] = family_counts.get(family_key, 0) + 1
        report["candidates"] += 1

        if not raw:
            report["rejected"] += 1
            reason = (candidate.get("rejected_reason")
                      or "empty candidate text")
            _bump(reason)
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "REJECTED", "reason": reason,
                "raw_text": None, "question_id": None, "canonical_id": None,
                "candidate_fingerprint": None,
                "verification_fingerprint": None})
            continue

        fp = question_fingerprint(raw)

        # -- exact normalized duplicate (corpus + in-batch) ---------------
        if fp in known_fps:
            report["duplicates"] += 1
            _bump("duplicate: exact normalized text already exists")
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "DUPLICATE",
                "reason": "exact normalized text already exists",
                "raw_text": raw, "question_id": None, "canonical_id": None,
                "candidate_fingerprint": fp,
                "verification_fingerprint": None})
            continue

        # -- FT-E verification (sole authority) ---------------------------
        verification = verify_question(raw)
        engine_verdict = verification.get("verdict")
        if engine_verdict == VERDICT_FAIL:
            report["rejected"] += 1
            report["verification_evidence"]["failed"] += 1
            reason = (verification.get("errors") or ["engine failed"])
            reason_str = "; ".join(str(e) for e in reason[:2])
            _bump(f"engine_fail: {reason_str[:80]}")
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "REJECTED", "reason": reason_str,
                "raw_text": raw, "question_id": None, "canonical_id": None,
                "candidate_fingerprint": fp,
                "verification_fingerprint":
                    _verification_fingerprint(verification)})
            continue
        if engine_verdict == VERDICT_REVIEW:
            report["review_required"] += 1
            report["verification_evidence"]["review_required"] += 1
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "REVIEW_REQUIRED",
                "reason": (verification.get("warnings")
                           or ["engine flagged review"])[:1],
                "raw_text": raw, "question_id": None, "canonical_id": None,
                "candidate_fingerprint": fp,
                "verification_fingerprint":
                    _verification_fingerprint(verification)})
            continue
        report["verification_evidence"]["verified"] += 1

        # -- lifecycle through the Content Compiler + bank ----------------
        try:
            qid = lifecycle_bank.create_question(
                raw,
                source_type=SOURCE_TYPE_GENERATED,
                source_name=f"generator:{GENERATOR_VERSION}",
                source_reference=request.source_reference or None,
                expected=_clean_compact(candidate.get("expected_journal")),
                tags=list(request.tags or []) + [family_key,
                                                 "generated:15I-M"],
            )
        except ValueError as exc:
            report["duplicates"] += 1
            _bump(f"duplicate: {exc}")
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "DUPLICATE", "reason": str(exc),
                "raw_text": raw, "question_id": None, "canonical_id": None,
                "candidate_fingerprint": fp,
                "verification_fingerprint":
                    _verification_fingerprint(verification)})
            continue
        lifecycle_bank.compile_question(qid)
        lifecycle_bank.validate_question(qid)
        q = lifecycle_bank.get_question(qid)

        # LLM-suggested metadata is CANDIDATE EVIDENCE: applied through the
        # bank's own suggestion API (teacher-editable metadata only, never
        # raw_text / expected_journal / status). A malicious suggestion is
        # REJECTED, never silently dropped.
        suggestions = candidate.get("suggestions") or {}
        if suggestions:
            try:
                lifecycle_bank.apply_llm_suggestions(qid, suggestions)
            except ValueError as exc:
                lifecycle_bank.reject_question(
                    qid, reason=f"invalid llm suggestion: {exc}")
                report["rejected"] += 1
                _bump(f"llm_suggestion_rejected: {exc}")
                report["candidate_records"].append({
                    "index": candidate.get("index"),
                    "family": family_key, "status": "REJECTED",
                    "reason": f"invalid llm suggestion: {exc}",
                    "raw_text": raw, "question_id": qid,
                    "canonical_id": None, "candidate_fingerprint": fp,
                    "verification_fingerprint":
                        _verification_fingerprint(verification)})
                continue
        q = lifecycle_bank.get_question(qid)

        # Persist generation provenance with the question.
        lifecycle_bank.set_generation_metadata(qid, {
            "generator_type": generator_type,
            "generator_version": GENERATOR_VERSION,
            "generation_seed": request.seed,
            "request_fingerprint": request.fingerprint(),
            "candidate_fingerprint": fp,
            "verification_fingerprint":
                _verification_fingerprint(verification),
            "family": family_key,
            "parameters": {k: v for k, v in (candidate.get("params")
                                             or {}).items()
                           if k not in ("amount", "cash", "disc", "pct")
                           or True},
            "generated_at": (now_fn or _now)(),
        })

        if q.get("status") == STATUS_REJECTED:
            report["rejected"] += 1
            reason = "; ".join(str(e) for e
                               in (q.get("validation_errors") or [])[:2])
            _bump(f"bank_rejected: {reason[:80]}")
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "REJECTED", "reason": reason,
                "raw_text": raw, "question_id": qid,
                "canonical_id": None, "candidate_fingerprint": fp,
                "verification_fingerprint":
                    _verification_fingerprint(verification)})
            continue
        if q.get("status") == STATUS_REVIEW_REQUIRED:
            report["review_required"] += 1
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "REVIEW_REQUIRED",
                "reason": (q.get("validation_errors")
                           or q.get("validation_warnings") or [])[:1],
                "raw_text": raw, "question_id": qid,
                "canonical_id": None, "candidate_fingerprint": fp,
                "verification_fingerprint":
                    _verification_fingerprint(verification)})
            continue

        # -- quality gates (only a gated candidate may be approved) -------
        # LLM/unknown-family candidates are gated against the family that
        # matches the ENGINE's resolved concept (never a 'LLM' placeholder).
        gate_family = (_FAMILY_BY_KEY.get(q.get("concept_key"))
                       if family_key == "LLM" else family)
        if gate_family is None:
            gate_family = family
        ok_gates, gate_reasons = _run_quality_gates(
            request, gate_family, lifecycle_bank.get_question(qid))
        if not ok_gates:
            lifecycle_bank.reject_question(qid,
                                           reason="; ".join(gate_reasons))
            report["rejected"] += 1
            _bump("quality_gate: " + "; ".join(gate_reasons[:1]))
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "REJECTED", "reason": "; ".join(gate_reasons),
                "raw_text": raw, "question_id": qid,
                "canonical_id": None, "candidate_fingerprint": fp,
                "verification_fingerprint":
                    _verification_fingerprint(verification)})
            continue

        # -- variant scope: a requested canonical journal must match ------
        canonical_id = None
        if request.canonical_id is not None:
            try:
                canonical_q = lifecycle_bank.get_question(request.canonical_id)
            except KeyError:
                canonical_q = None
            if canonical_q is None:
                # look in the existing corpus (read-only)
                canonical_q = next(
                    (q for q in existing
                     if q.get("question_id") == request.canonical_id), None)
            if canonical_q is None:
                lifecycle_bank.reject_question(
                    qid, reason="canonical_id does not exist")
                report["rejected"] += 1
                _bump("quality_gate: unknown canonical_id")
                report["candidate_records"].append({
                    "index": candidate.get("index"), "family": family_key,
                    "status": "REJECTED",
                    "reason": "requested canonical_id does not exist",
                    "raw_text": raw, "question_id": qid,
                    "canonical_id": None, "candidate_fingerprint": fp,
                    "verification_fingerprint":
                        _verification_fingerprint(verification)})
                continue
            if not compare_expected(
                    (q or {}).get("expected_journal"),
                    canonical_q.get("expected_journal")):
                lifecycle_bank.reject_question(
                    qid, reason="meaning-changing variant: canonical "
                                "journal differs from requested canonical")
                report["rejected"] += 1
                _bump("quality_gate: meaning-changing variant")
                report["candidate_records"].append({
                    "index": candidate.get("index"), "family": family_key,
                    "status": "REJECTED",
                    "reason": "meaning-changing variant",
                    "raw_text": raw, "question_id": qid,
                    "canonical_id": None, "candidate_fingerprint": fp,
                    "verification_fingerprint":
                        _verification_fingerprint(verification)})
                continue
            canonical_id = request.canonical_id
        else:
            # conservative variant identification vs the existing corpus
            candidate_q = lifecycle_bank.get_question(qid)
            variant_of = find_variant_of(candidate_q, approved_corpus)
            if variant_of is not None:
                canonical_id = variant_of

        # -- approve (bank re-verifies deterministically) -----------------
        try:
            lifecycle_bank.approve_question(qid)
        except ValueError as exc:
            report["rejected"] += 1
            _bump(f"approval_denied: {exc}")
            report["candidate_records"].append({
                "index": candidate.get("index"), "family": family_key,
                "status": "REJECTED", "reason": str(exc),
                "raw_text": raw, "question_id": qid,
                "canonical_id": None, "candidate_fingerprint": fp,
                "verification_fingerprint":
                    _verification_fingerprint(verification)})
            continue

        report["approved"] += 1
        if canonical_id is not None:
            report["variants"] += 1
        report["candidate_records"].append({
            "index": candidate.get("index"), "family": family_key,
            "status": "APPROVED", "reason": None,
            "raw_text": raw, "question_id": qid,
            "canonical_id": canonical_id,
            "candidate_fingerprint": fp,
            "verification_fingerprint":
                _verification_fingerprint(verification)})

    # Persist the lifecycle store in live mode (the bank owns persistence).
    if not dry_run and lifecycle_bank is not None:
        lifecycle_bank.save()

    report["rejected_reasons"] = dict(
        sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    report["generation_stats"]["families"] = dict(
        sorted(family_counts.items()))
    return report


# ---------------------------------------------------------------------------
# Replay + dry-run helpers
# ---------------------------------------------------------------------------


def replay_batch(request: GenerationRequest,
                 bank: Optional[QuestionBank] = None) -> Dict[str, Any]:
    """Reproducibility check: re-run the deterministic pipeline with the
    same request + seed + generator version and return the normalized
    projection used for equivalence (stats + per-candidate fingerprints +
    statuses)."""
    report = generate_batch(request=request, bank=bank, dry_run=True)
    return {
        "request_fingerprint": report["request_fingerprint"],
        "seed": report["seed"],
        "requested": report["requested"],
        "candidates": report["candidates"],
        "approved": report["approved"],
        "rejected": report["rejected"],
        "review_required": report["review_required"],
        "duplicates": report["duplicates"],
        "variants": report["variants"],
        "rejected_reasons": report["rejected_reasons"],
        "fingerprints": [
            (c["family"], c["status"], c["candidate_fingerprint"])
            for c in report["candidate_records"]],
    }
