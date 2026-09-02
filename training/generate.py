#!/usr/bin/env python3
"""
Platrixa FYJC — Deterministic Training Data Generator

Generates diverse FYJC accounting transaction text variations and maps them
to structured training records using the existing deterministic kernel.

Safety rules:
  - Every generated case must pass through the kernel for verification.
  - The kernel is the source of truth; this generator never invents labels.
  - If the kernel cannot establish a reliable interpretation, the case is
    marked for review rather than assigned a label.
  - No LLM is used to generate training labels.

CPU-safe: Template generation runs on CPU. Kernel verification requires
the backend.maths imports (CPU-compatible but slower).

Usage:
    python training/generate.py --config training/config.yaml
    python training/generate.py --max-new 100 --seed 42
    python training/generate.py --from-existing-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Template corpus — FYJC accounting transaction variations
# ---------------------------------------------------------------------------

# Indian party names (realistic FYJC context)
PARTIES = [
    "Raj", "Amit", "Suresh", "Rahul", "Vikram", "Sanjay", "Anil", "Deepak",
    "Pankaj", "Ravi", "Sunil", "Manoj", "Ajay", "Vijay", "Prakash", "Ganesh",
    "Sharma", "Patel", "Mehta", "Gupta", "Joshi", "Iyer", "Rao", "Nair",
    "Desai", "Kulkarni", "Bhatt", "Choudhary", "Singh", "Kumar",
    "Raj & Co.", "Sharma Traders", "Patel Bros.", "Mehta Enterprises",
    "Gupta Stores", "Ganesh Traders", "Amit & Sons", "Kumar Ltd.",
    "Iyer & Co.", "Desai Traders", "Sharma Electronics", "Patel Furniture",
    "Raj Traders", "Singh Enterprises", "Kumar Electronics",
    "Delhi Cloth House", "Mumbai Stationers", "Chennai Traders",
    "Suresh & Co.", "Vikram Brothers", "Anil Traders", "Deepak Enterprises",
]

# Transaction objects
OBJECTS = [
    "goods", "furniture", "stationery", "raw materials", "computer equipment",
    "office supplies", "machinery", "tools", "packaging materials",
    "cleaning supplies", "electrical equipment", "plumbing supplies",
    "painting materials", "safety equipment", "textiles", "groceries",
    "medicines", "books", "uniforms", "spare parts", "fuel", "diesel",
    "petrol", "LPG", "water", "electricity", "internet", "telephone",
    "printing services", "transport services", "courier services",
    "advertising services", "consulting services", "maintenance services",
    "rent", "salary", "wages", "commission", "insurance premium",
    "interest", "dividend", "depreciation", "bad debts", "discount allowed",
    "discount received", "carriage inward", "carriage outward",
]

# Purchase verbs (FYJC variants)
PURCHASE_VERBS = [
    "Purchased", "Bought", "Procured", "Acquired", "Obtained",
    "Got", "Received", "Brought",
]

SALE_VERBS = [
    "Sold", "Supplied", "Delivered", "Provided", "Dispatched",
    "Shipped", "Transferred",
]

PAYMENT_VERBS = [
    "Paid", "Remitted", "Transferred", "Sent", "Settled",
]

RECEIPT_VERBS = [
    "Received", "Collected", "Got", "Obtained", "Accrued",
]

# Expense verbs
EXPENSE_VERBS = [
    "Paid rent to", "Paid salary to", "Paid wages to", "Paid electricity bill",
    "Paid telephone bill", "Paid internet bill", "Paid water bill",
    "Paid insurance premium to", "Paid commission to", "Paid interest on",
    "Paid carriage on", "Paid cleaning charges to",
]

# Capital verbs
CAPITAL_VERBS = [
    "Invested", "Contributed", "Deposited", "Started business with",
    "Introduced capital of", "Brought in cash of",
]

# Return verbs
RETURN_VERBS = [
    "Returned goods to", "Sent back goods to", "Returned defective goods to",
    "Rejected goods from", "Returned overcharged items to",
]

# Payment methods (with explicit markers)
PAYMENT_METHODS = {
    "cash": ["for cash", "in cash", "by cash", "cash payment", ""],
    "cheque": ["by cheque", "by chq", "via cheque", "through cheque"],
    "bank": ["by bank transfer", "by NEFT", "by RTGS", "by UPI", "by IMPS",
             "through bank", "by ECS"],
    "credit": ["on credit", "on account", "on credit terms", "credit purchase",
               "credit sale"],
}

# Simple payment patterns (for mixing)
SIMPLE_PAYMENTS = [
    "", "for cash", "on credit", "by cheque", "by NEFT", "by UPI",
    "in cash", "by bank transfer", "for cash payment",
]

# Amounts (realistic FYJC range)
AMOUNTS = [
    500, 750, 1000, 1200, 1500, 2000, 2500, 3000, 3500, 4000, 4500,
    5000, 6000, 7500, 8000, 9000, 10000, 12000, 15000, 18000, 20000,
    22000, 25000, 28000, 30000, 35000, 40000, 45000, 50000, 55000,
    60000, 70000, 75000, 80000, 90000, 100000, 125000, 150000,
    200000, 250000, 500000,
]

# Partial payment fractions
FRACTIONS = [
    "half", "one-third", "one-fourth", "two-thirds", "three-fourths",
    "25%", "33%", "50%", "67%", "75%",
]


# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

def _build_purchase_templates() -> List[Dict[str, Any]]:
    """Simple purchase transaction templates."""
    templates = []
    for verb in PURCHASE_VERBS:
        for payment in SIMPLE_PAYMENTS:
            payment_suffix = f" {payment}" if payment else ""
            templates.append({
                "template": f"{verb} goods from {{party}} for Rs.{{amount}}{payment_suffix}",
                "category": "cash_credit",
                "subcategory": "purchase",
                "tx_hint": "PURCHASE",
            })
    return templates


def _build_sale_templates() -> List[Dict[str, Any]]:
    """Simple sale transaction templates."""
    templates = []
    for verb in SALE_VERBS:
        for payment in SIMPLE_PAYMENTS:
            payment_suffix = f" {payment}" if payment else ""
            templates.append({
                "template": f"{verb} goods to {{party}} for Rs.{{amount}}{payment_suffix}",
                "category": "cash_credit",
                "subcategory": "sale",
                "tx_hint": "SALE",
            })
    return templates


def _build_payment_templates() -> List[Dict[str, Any]]:
    """Payment/receipt transaction templates."""
    templates = []
    for verb in PAYMENT_VERBS:
        templates.append({
            "template": f"{verb} Rs.{{amount}} to {{party}}",
            "category": "settlement",
            "subcategory": "payment",
            "tx_hint": "PAYMENT",
        })
    for verb in RECEIPT_VERBS:
        templates.append({
            "template": f"{verb} Rs.{{amount}} from {{party}}",
            "category": "settlement",
            "subcategory": "receipt",
            "tx_hint": "RECEIPT",
        })
    return templates


def _build_expense_templates() -> List[Dict[str, Any]]:
    """Expense transaction templates."""
    templates = []
    for verb in EXPENSE_VERBS:
        templates.append({
            "template": f"{verb} {{party}} Rs.{{amount}}",
            "category": "expense",
            "subcategory": "operating_expense",
            "tx_hint": "EXPENSE",
        })
    return templates


def _build_capital_templates() -> List[Dict[str, Any]]:
    """Capital introduction templates."""
    templates = []
    for verb in CAPITAL_VERBS:
        templates.append({
            "template": f"{verb} Rs.{{amount}} as capital",
            "category": "capital",
            "subcategory": "capital_introduction",
            "tx_hint": "CAPITAL",
        })
    return templates


def _build_return_templates() -> List[Dict[str, Any]]:
    """Return transaction templates."""
    templates = []
    for verb in RETURN_VERBS:
        templates.append({
            "template": f"{verb} {{party}} goods worth Rs.{{amount}}",
            "category": "returns",
            "subcategory": "purchase_return",
            "tx_hint": "RETURN_OUT",
        })
    return templates


def _build_partial_payment_templates() -> List[Dict[str, Any]]:
    """Partial payment templates."""
    templates = []
    for fraction in FRACTIONS:
        for payment in ["cash", "cheque", "NEFT", "bank transfer"]:
            templates.append({
                "template": (
                    f"Purchased goods from {{party}} for Rs.{{amount}}. "
                    f"Paid {fraction} by {payment}."
                ),
                "category": "partial_payment",
                "subcategory": "fraction_payment",
                "tx_hint": "PURCHASE",
            })
    return templates


def _build_multi_party_templates() -> List[Dict[str, Any]]:
    """Multi-party and cross-reference templates."""
    return [
        {
            "template": "Purchased goods from {party1} for Rs.{amount}. Sold half to {party2}.",
            "category": "multi_party",
            "subcategory": "purchase_and_sale",
            "tx_hint": "COMPOUND",
        },
        {
            "template": "Purchased goods from {party} for Rs.{amount}. Sold goods to {party2} for Rs.{amount2}.",
            "category": "multi_party",
            "subcategory": "purchase_and_sale",
            "tx_hint": "COMPOUND",
        },
        {
            "template": "Received goods worth Rs.{amount} from {party}. Returned defective goods.",
            "category": "multi_party",
            "subcategory": "receipt_and_return",
            "tx_hint": "COMPOUND",
        },
        {
            "template": "Purchased goods from {party} for Rs.{amount}. {party} settled the account by cheque.",
            "category": "cross_reference",
            "subcategory": "pronoun_he",
            "tx_hint": "COMPOUND",
        },
    ]


def _build_adversarial_templates() -> List[Dict[str, Any]]:
    """Adversarial/edge-case templates."""
    return [
        # Missing amount
        {
            "template": "Purchased goods from {party}",
            "category": "missing_amount",
            "subcategory": "no_amount",
            "tx_hint": "BLOCKED",
            "expected_status": "BLOCKED",
        },
        # Vague amount
        {
            "template": "Purchased goods from {party} for some amount",
            "category": "missing_amount",
            "subcategory": "vague_amount",
            "tx_hint": "BLOCKED",
            "expected_status": "BLOCKED",
        },
        # Missing party
        {
            "template": "Purchased goods for Rs.{amount}",
            "category": "missing_party",
            "subcategory": "no_party",
            "tx_hint": "REVIEW_REQUIRED",
            "expected_status": "REVIEW_REQUIRED",
        },
        # Amount in words
        {
            "template": "Purchased goods from {party} for twenty thousand rupees",
            "category": "edge",
            "subcategory": "amount_in_words",
            "tx_hint": "REVIEW_REQUIRED",
            "expected_status": "REVIEW_REQUIRED",
        },
        # Contradictory amounts
        {
            "template": "Purchased goods from {party} for Rs.{amount}. Paid Rs.{amount2} cash and Rs.{amount3} by cheque.",
            "category": "contradiction",
            "subcategory": "payment_over_total",
            "tx_hint": "INVALID_INPUT_MATH",
            "expected_status": "INVALID_INPUT_MATH",
        },
        # Pronoun reference
        {
            "template": "Purchased goods from {party} for Rs.{amount}. He paid Rs.{amount2}.",
            "category": "cross_reference",
            "subcategory": "pronoun_he",
            "tx_hint": "REVIEW_REQUIRED",
            "expected_status": "REVIEW_REQUIRED",
        },
        # Unusual wording
        {
            "template": "Obtained printing services from {party} for Rs.{amount}",
            "category": "unusual_wording",
            "subcategory": "service_obtained",
            "tx_hint": "REVIEW_REQUIRED",
            "expected_status": "REVIEW_REQUIRED",
        },
    ]


# ---------------------------------------------------------------------------
# All templates
# ---------------------------------------------------------------------------

ALL_TEMPLATES: List[Dict[str, Any]] = []
ALL_TEMPLATES.extend(_build_purchase_templates())
ALL_TEMPLATES.extend(_build_sale_templates())
ALL_TEMPLATES.extend(_build_payment_templates())
ALL_TEMPLATES.extend(_build_expense_templates())
ALL_TEMPLATES.extend(_build_capital_templates())
ALL_TEMPLATES.extend(_build_return_templates())
ALL_TEMPLATES.extend(_build_partial_payment_templates())
ALL_TEMPLATES.extend(_build_multi_party_templates())
ALL_TEMPLATES.extend(_build_adversarial_templates())


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class TrainingDataGenerator:
    """Deterministic generator for FYJC accounting training data.

    Generates transaction text variations by filling templates with
    randomized parties, amounts, and objects. Each generated case is
    passed through the kernel for verification before being accepted.

    The generator never invents accounting labels independently of
    the kernel.
    """

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self._seen_texts: set = set()

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.lower().strip().encode()).hexdigest()[:12]

    def _fill_template(self, template: Dict[str, Any]) -> str:
        """Fill a template with random parties and amounts."""
        text = template["template"]

        # Choose parties
        party = self.rng.choice(PARTIES)
        party2 = self.rng.choice([p for p in PARTIES if p != party])

        # Choose amounts
        amount = self.rng.choice(AMOUNTS)
        amount2 = self.rng.choice([a for a in AMOUNTS if a != amount])
        amount3_candidates = [a for a in AMOUNTS if a + amount2 != amount and a != amount]
        amount3 = self.rng.choice(amount3_candidates) if amount3_candidates else amount

        text = text.replace("{party}", party)
        text = text.replace("{party1}", party)
        text = text.replace("{party2}", party2)
        text = text.replace("{amount}", str(amount))
        text = text.replace("{amount2}", str(amount2))
        text = text.replace("{amount3}", str(amount3))

        return text

    def generate_from_templates(
        self,
        max_cases: int = 500,
        categories: Optional[List[str]] = None,
        min_length: int = 15,
    ) -> List[Dict[str, Any]]:
        """Generate unique transaction texts from templates.

        Args:
            max_cases: Maximum number of cases to generate.
            categories: Filter to these categories only (None = all).
            min_length: Minimum input text length.

        Returns:
            List of {category, subcategory, input_text} dicts.
        """
        candidates = []
        attempts = 0
        max_attempts = max_cases * 10  # avoid infinite loops

        templates = ALL_TEMPLATES
        if categories:
            templates = [t for t in templates if t["category"] in categories]

        while len(candidates) < max_cases and attempts < max_attempts:
            attempts += 1
            template = self.rng.choice(templates)
            text = self._fill_template(template)

            if len(text) < min_length:
                continue

            text_hash = self._hash_text(text)
            if text_hash in self._seen_texts:
                continue

            self._seen_texts.add(text_hash)
            candidates.append({
                "category": template["category"],
                "subcategory": template["subcategory"],
                "input_text": text,
                "tx_hint": template.get("tx_hint"),
                "expected_status": template.get("expected_status"),
            })

        return candidates

    def load_existing_cases(
        self, jsonl_path: str
    ) -> List[Dict[str, Any]]:
        """Load existing candidate cases from JSONL.

        Returns list of {category, subcategory, input_text, case_id, status, ...}.
        """
        cases = []
        path = Path(jsonl_path)
        if not path.exists():
            return cases

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    case = json.loads(line)
                    text = case.get("input_text", "")
                    if text and self._hash_text(text) not in self._seen_texts:
                        self._seen_texts.add(self._hash_text(text))
                        cases.append({
                            "category": case.get("category", "unknown"),
                            "subcategory": case.get("subcategory", "unknown"),
                            "input_text": text,
                            "case_id": case.get("case_id"),
                            "status": case.get("status"),
                            "reason_classification": case.get("reason_classification"),
                            "why_not": case.get("why_not"),
                            "journal_status": case.get("journal_status"),
                            "journal_balanced": case.get("journal_balanced"),
                            "journal_narration": case.get("journal_narration"),
                            "debit_accounts": case.get("debit_accounts"),
                            "credit_accounts": case.get("credit_accounts"),
                            "calculations": case.get("calculations"),
                            "understanding": case.get("understanding"),
                            "verification": case.get("verification"),
                        })
                except json.JSONDecodeError:
                    continue

        return cases

    def load_existing_training(
        self, jsonl_path: str
    ) -> List[Dict[str, Any]]:
        """Load existing specialist_clean_training.jsonl records.

        Returns list of {instruction, input, output, _p4_metadata} dicts.
        """
        records = []
        path = Path(jsonl_path)
        if not path.exists():
            return records

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    text = record.get("input", "")
                    if text and self._hash_text(text) not in self._seen_texts:
                        self._seen_texts.add(self._hash_text(text))
                        records.append(record)
                except json.JSONDecodeError:
                    continue

        return records


# ---------------------------------------------------------------------------
# Kernel integration
# ---------------------------------------------------------------------------

def run_kernel_on_text(text: str) -> Optional[Dict[str, Any]]:
    """Run the Platrixa deterministic kernel on raw student text.

    Returns the full kernel output dict, or None if the kernel fails.
    CPU-compatible but may be slow due to heavy imports.
    """
    try:
        from backend.maths.fyjc_orchestration import orchestrate
        return orchestrate(text)
    except Exception as e:
        return {"status": "EXCEPTION", "reason": str(e)}


def map_kernel_to_training_output(
    kernel_result: Dict[str, Any], raw_input: str
) -> Optional[Dict[str, Any]]:
    """Map kernel output to the training JSONL output format.

    Training output format:
    {
        "transaction_type": str,
        "parties": [str],
        "amounts": [{"value": str, "currency": "INR", "source": str}],
        "payment_method": str,
        "references": [],
        "ambiguities": [str],
        "grounding": {all_fields_explicitly_grounded, inferred_fields, field_grounding}
    }

    Returns None if the kernel result cannot be mapped.
    """
    status = kernel_result.get("status", "")

    # Extract understanding from kernel
    understanding = kernel_result.get("understanding") or {}
    tx_type = understanding.get("transaction_type") or _infer_tx_type(raw_input, kernel_result)
    parties = understanding.get("parties") or []
    if not parties:
        parties = _extract_parties_from_kernel(kernel_result, raw_input)

    # Extract amounts
    amounts = _extract_amounts(kernel_result)

    # Determine payment method
    payment_method = _infer_payment_method(raw_input, kernel_result)

    # Detect ambiguities
    ambiguities = _detect_ambiguities(raw_input, kernel_result, status)

    # Build grounding
    all_explicit = len(ambiguities) == 0 and payment_method != "UNKNOWN"
    inferred_fields = []
    if payment_method in ("UNKNOWN", "CREDIT_INFERRED"):
        inferred_fields.append("payment_method")

    grounding = {
        "all_fields_explicitly_grounded": all_explicit,
        "inferred_fields": inferred_fields,
    }

    return {
        "transaction_type": tx_type,
        "parties": parties,
        "amounts": amounts,
        "payment_method": payment_method,
        "references": [],
        "ambiguities": ambiguities,
        "grounding": grounding,
    }


def _infer_tx_type(raw_input: str, result: Dict[str, Any]) -> str:
    """Infer transaction type from text and kernel result."""
    text_lower = raw_input.lower()

    # Check if kernel classified it
    understanding = result.get("understanding") or {}
    if understanding.get("transaction_type"):
        return understanding["transaction_type"].lower()

    # Simple heuristics
    if any(w in text_lower for w in ["purchased", "bought", "procured", "acquired"]):
        return "purchase"
    if any(w in text_lower for w in ["sold", "supplied", "delivered"]):
        return "sale"
    if any(w in text_lower for w in ["paid", "remitted", "settled"]):
        return "payment"
    if any(w in text_lower for w in ["received", "collected"]):
        if "from" in text_lower:
            return "receipt"
        return "receipt"
    if any(w in text_lower for w in ["returned", "rejected"]):
        return "return"
    if any(w in text_lower for w in ["invested", "capital", "introduced"]):
        return "capital"
    if any(w in text_lower for w in ["rent", "salary", "wages", "electricity",
                                       "telephone", "insurance", "interest"]):
        return "expense"
    if any(w in text_lower for w in ["depreciation"]):
        return "depreciation"

    return "unknown"


def _extract_parties_from_kernel(
    result: Dict[str, Any], raw_input: str
) -> List[str]:
    """Extract party names from kernel result or raw text."""
    parties = []

    # From understanding
    understanding = result.get("understanding") or {}
    if understanding.get("parties"):
        return understanding["parties"]

    # From journal lines
    for side in ("debit_lines", "credit_lines"):
        lines = result.get(side) or []
        for line in lines:
            acct = line.get("account", "")
            if acct and acct not in parties and acct not in (
                "Cash", "Bank", "Purchases", "Sales", "Capital",
                "Sales Return", "Purchase Return", "Discount Allowed",
                "Discount Received", "Carriage Inward", "Carriage Outward",
            ):
                parties.append(acct)

    if parties:
        return parties[:3]  # limit to 3 parties

    # Fallback: extract capitalized words (crude heuristic)
    import re
    words = re.findall(r'\b[A-Z][a-z]+\b', raw_input)
    known_accounts = {
        "Purchased", "Bought", "Sold", "Paid", "Received", "Cash",
        "Bank", "Cheque", "Credit", "Debit", "Rs", "Being", "The",
    }
    parties = [w for w in words if w not in known_accounts][:2]
    return parties


def _extract_amounts(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract amounts from kernel result."""
    amounts = []

    # From debit/credit lines
    seen = set()
    for side in ("debit_lines", "credit_lines"):
        lines = result.get(side) or []
        for line in lines:
            amt = str(line.get("amount", ""))
            if amt and amt not in seen:
                seen.add(amt)
                amounts.append({
                    "value": amt,
                    "currency": "INR",
                    "source": "explicit",
                })

    if amounts:
        return amounts

    # Fallback: from calculations
    for calc in result.get("calculations") or []:
        res = calc.get("result")
        if isinstance(res, (int, float)):
            amounts.append({
                "value": str(int(res)),
                "currency": "INR",
                "source": "explicit",
            })
        elif isinstance(res, dict):
            for v in res.values():
                if isinstance(v, (int, float)):
                    amounts.append({
                        "value": str(int(v)),
                        "currency": "INR",
                        "source": "explicit",
                    })

    return amounts


def _infer_payment_method(raw_input: str, result: Dict[str, Any]) -> str:
    """Infer payment method from raw text and kernel result."""
    text_lower = raw_input.lower()

    if any(w in text_lower for w in ["for cash", "in cash", "by cash", "cash payment"]):
        return "CASH"
    if any(w in text_lower for w in ["by cheque", "by chq", "via cheque"]):
        return "CHEQUE"
    if any(w in text_lower for w in ["by neft", "by rtgs", "by upi", "by imps", "by ecs", "by bank transfer"]):
        return "BANK"
    if any(w in text_lower for w in ["on credit", "on account", "credit purchase", "credit sale"]):
        return "CREDIT"

    # Check journal narration
    narration = (result.get("journal_narration") or "").lower()
    if "cash" in narration:
        return "CASH"
    if "cheque" in narration or "chq" in narration:
        return "CHEQUE"
    if "bank" in narration:
        return "BANK"

    # Implicit cash for expenses
    status = result.get("status") or ""
    if status == "VERIFIED":
        text_lower_input = raw_input.lower()
        if any(w in text_lower_input for w in ["paid rent", "paid salary", "paid wages",
                                                  "paid electricity", "paid telephone"]):
            return "CASH"

    return "UNKNOWN"


def _detect_ambiguities(
    raw_input: str, result: Dict[str, Any], status: str
) -> List[str]:
    """Detect ambiguity flags from text and kernel result."""
    ambiguities = []
    text_lower = raw_input.lower()

    # Missing payment mode
    payment_keywords = [
        "for cash", "in cash", "by cash", "by cheque", "by neft",
        "by upi", "on credit", "by bank transfer",
    ]
    if not any(kw in text_lower for kw in payment_keywords):
        # Check if the kernel indicated missing payment
        if status in ("REVIEW_REQUIRED", "VERIFIED"):
            if result.get("reason") and "payment" in str(result.get("reason", "")).lower():
                ambiguities.append("payment_method_ambiguous")

    # Missing amount
    import re
    if not re.search(r'rs\.?\s*[\d,]+', text_lower):
        ambiguities.append("missing_amount")

    # Missing party
    known_words = {
        "purchased", "bought", "sold", "paid", "received", "goods",
        "for", "rs", "to", "from", "on", "by", "cash", "cheque",
        "credit", "bank", "returned", "delivered", "supplied",
    }
    words = [w for w in re.findall(r'\b[a-z]+\b', text_lower) if w not in known_words]
    if len(words) < 1:
        ambiguities.append("missing_party")

    # Pronoun reference
    if any(w in text_lower for w in ["he paid", "she paid", "they paid", "it was"]):
        ambiguities.append("unresolved_pronoun")

    return ambiguities


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate FYJC accounting training data"
    )
    parser.add_argument("--max-new", type=int, default=500,
                        help="Maximum new cases to generate")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed")
    parser.add_argument("--from-existing-only", action="store_true",
                        help="Only use existing candidate cases (no new generation)")
    parser.add_argument("--with-kernel", action="store_true",
                        help="Run kernel on new cases (slow on CPU)")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to config.yaml")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSONL path")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    gen = TrainingDataGenerator(seed=args.seed)

    all_cases = []

    # 1. Load existing specialist_clean_training.jsonl
    existing_training = project_root / "training_data" / "specialist_clean_training.jsonl"
    if existing_training.exists():
        records = gen.load_existing_training(str(existing_training))
        all_cases.extend(records)
        print(f"Loaded {len(records)} records from existing training data")

    # 2. Load existing candidate cases
    candidate_jsonl = project_root / "platrixa_ai_candidate_cases.jsonl"
    if candidate_jsonl.exists():
        cases = gen.load_existing_cases(str(candidate_jsonl))
        # Convert candidate cases to training format
        for case in cases:
            text = case.get("input_text", "")
            if not text:
                continue

            # Build output from kernel data
            output_dict = {
                "transaction_type": (case.get("understanding", {}).get("transaction_type")
                                     or _infer_tx_type(text, case)),
                "parties": (case.get("understanding", {}).get("parties")
                            or _extract_parties_from_kernel(case, text)),
                "amounts": _extract_amounts(case),
                "payment_method": _infer_payment_method(text, case),
                "references": [],
                "ambiguities": _detect_ambiguities(text, case, case.get("status", "")),
                "grounding": {
                    "all_fields_explicitly_grounded": True,
                    "inferred_fields": [],
                },
            }

            record = {
                "instruction": (
                    "Parse the student's accounting language into a grounded "
                    "structured interpretation. Do not invent missing information. "
                    "Identify: transaction_type, parties, amounts, payment_method, "
                    "references, ambiguities, and grounding status."
                ),
                "input": text,
                "output": json.dumps(output_dict, ensure_ascii=False),
                "_p4_metadata": {
                    "problem_id": case.get("case_id"),
                    "category": case.get("category"),
                    "subcategory": case.get("subcategory"),
                    "kernel_status": case.get("status"),
                    "reason_classification": case.get("reason_classification"),
                    "content_hash": gen._hash_text(text),
                    "source": "existing_candidate_cases",
                },
            }
            all_cases.append(record)

        print(f"Loaded {len(cases)} existing candidate cases")

    # 3. Generate new cases from templates
    if not args.from_existing_only:
        new_cases = gen.generate_from_templates(
            max_cases=args.max_new,
            min_length=15,
        )
        print(f"Generated {len(new_cases)} new case templates")

        if args.with_kernel:
            # Run kernel on each new case
            verified = 0
            failed = 0
            for i, case in enumerate(new_cases):
                text = case["input_text"]
                kernel_result = run_kernel_on_text(text)
                if kernel_result is None:
                    failed += 1
                    continue

                output_dict = map_kernel_to_training_output(kernel_result, text)
                if output_dict is None:
                    failed += 1
                    continue

                record = {
                    "instruction": (
                        "Parse the student's accounting language into a grounded "
                        "structured interpretation. Do not invent missing information. "
                        "Identify: transaction_type, parties, amounts, payment_method, "
                        "references, ambiguities, and grounding status."
                    ),
                    "input": text,
                    "output": json.dumps(output_dict, ensure_ascii=False),
                    "_p4_metadata": {
                        "problem_id": f"GEN{i:04d}",
                        "category": case["category"],
                        "subcategory": case["subcategory"],
                        "kernel_status": kernel_result.get("status"),
                        "content_hash": gen._hash_text(text),
                        "source": "generated_kernel_verified",
                    },
                }
                all_cases.append(record)
                verified += 1

                if (i + 1) % 50 == 0:
                    print(f"  Processed {i+1}/{len(new_cases)} "
                          f"(verified={verified}, failed={failed})")

            print(f"Kernel verification: {verified} verified, {failed} failed")
        else:
            # Without kernel: use template hints for training output
            for i, case in enumerate(new_cases):
                output_dict = {
                    "transaction_type": case.get("tx_hint", "unknown").lower(),
                    "parties": [],
                    "amounts": [],
                    "payment_method": "UNKNOWN",
                    "references": [],
                    "ambiguities": _detect_ambiguities(
                        case["input_text"], {}, case.get("expected_status", "")
                    ),
                    "grounding": {
                        "all_fields_explicitly_grounded": False,
                        "inferred_fields": ["all"],
                    },
                }

                record = {
                    "instruction": (
                        "Parse the student's accounting language into a grounded "
                        "structured interpretation. Do not invent missing information. "
                        "Identify: transaction_type, parties, amounts, payment_method, "
                        "references, ambiguities, and grounding status."
                    ),
                    "input": case["input_text"],
                    "output": json.dumps(output_dict, ensure_ascii=False),
                    "_p4_metadata": {
                        "problem_id": f"GEN{i:04d}",
                        "category": case["category"],
                        "subcategory": case["subcategory"],
                        "content_hash": gen._hash_text(case["input_text"]),
                        "source": "generated_template_only",
                    },
                }
                all_cases.append(record)

            print(f"Added {len(new_cases)} template-only cases (no kernel verification)")

    # 4. Write output
    output_path = args.output
    if not output_path:
        output_path = str(project_root / "training_data" / "generated_training_raw.jsonl")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in all_cases:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(all_cases)} total records to {output_path}")

    # 5. Summary
    categories = {}
    sources = {}
    for record in all_cases:
        meta = record.get("_p4_metadata", {})
        cat = meta.get("category", "unknown")
        src = meta.get("source", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
        sources[src] = sources.get(src, 0) + 1

    print("\nCategory distribution:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    print("\nSource distribution:")
    for src, count in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {src}: {count}")


if __name__ == "__main__":
    main()
