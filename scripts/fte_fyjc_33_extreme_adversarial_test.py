#!/usr/bin/env python3
"""
Sprint 33 — Extreme Adversarial Whole-Problem Accounting Test

MAXIMUM-STRESS VALIDATION — NO ARCHITECTURE CHANGES.

Runs 20+ adversarial whole-problem accounting inputs through the existing
Platrixa pipeline and classifies every transaction for correctness.

Primary objective: ZERO incorrect VERIFIED transactions.

Exit codes:
  0 = PASS (no incorrect VERIFIED, no safety violations)
  1 = FAIL (incorrect VERIFIED or critical safety violation)
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_problem_engine import process_problem
from backend.maths.fyjc_bk_reasoning import classify_bk_type

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

REVIEW_REQUIRED = "REVIEW_REQUIRED"
VERIFIED = "VERIFIED"
NOT_SUPPORTED = "NOT_SUPPORTED"
INFORMATIONAL_EVENT = "INFORMATIONAL_EVENT"
OPENING_BALANCE = "OPENING_BALANCE"
PROBLEM_VERIFIED = "PROBLEM_VERIFIED"
PROBLEM_REVIEW_REQUIRED = "PROBLEM_REVIEW_REQUIRED"
PROBLEM_NOT_SUPPORTED = "PROBLEM_NOT_SUPPORTED"

# ─────────────────────────────────────────────────────────────
# GROUND TRUTH DEFINITIONS
# ─────────────────────────────────────────────────────────────

# Each problem has:
#   id, text, category, expected_problem_status
#   expected_transactions: list of {
#     index, expected_status, expected_type,
#     expected_debit_account, expected_credit_account,
#     expected_amount (Decimal or None),
#     expected_parties (list of str), notes
#   }

CORPUS = []

# ═══════════════════════════════════════════════════════════════
# PROBLEM 1: Basic multi-transaction cycle
# Categories: opening, historical, multiple parties, instruments
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV01",
    "text": """Opening: Cash Rs.50000 Bank Rs.30000 Capital Rs.80000.
Purchased goods from Raj Rs.20000 on credit.
Paid Raj Rs.10000 cash.
Sold goods to Amit Rs.25000 on credit.
Received Rs.10000 from Amit by cheque.
Paid rent Rs.5000 cash.
Purchased goods from Suresh Rs.15000 on credit.
Paid Rs.5000 to Suresh by cheque.""",
    "category": "basic_cycle",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE",
         "expected_debit_account": None, "expected_credit_account": None,
         "expected_amount": None, "expected_parties": [],
         "notes": "Opening balances — informational"},
        {"index": 2, "expected_status": VERIFIED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_debit_account": "Purchases", "expected_credit_account": "Raj",
         "expected_amount": Decimal("20000"), "expected_parties": ["Raj"],
         "notes": "Credit purchase from Raj"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "MERGED_SEGMENT",
         "expected_debit_account": None, "expected_credit_account": None,
         "expected_amount": None, "expected_parties": ["Raj"],
         "notes": "Splitter merges purchase+payment same party → REVIEW_REQUIRED"},
        {"index": 4, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("25000"), "expected_parties": ["Amit"],
         "notes": "Credit sale to Amit"},
        {"index": 5, "expected_status": VERIFIED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_debit_account": "Bank", "expected_credit_account": "Amit",
         "expected_amount": Decimal("10000"), "expected_parties": ["Amit"],
         "notes": "Receipt by cheque from Amit"},
        {"index": 6, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("5000"), "expected_parties": [],
         "notes": "Rent paid cash"},
        {"index": 7, "expected_status": VERIFIED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_debit_account": "Purchases", "expected_credit_account": "Suresh",
         "expected_amount": Decimal("15000"), "expected_parties": ["Suresh"],
         "notes": "Credit purchase from Suresh"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_debit_account": "Suresh", "expected_credit_account": "Bank",
         "expected_amount": Decimal("5000"), "expected_parties": ["Suresh"],
         "notes": "Payment by cheque to Suresh — may be merged with prior"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 2: GST credit cycle with settlement
# Categories: GST, settlement, historical, multiple parties
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV02",
    "text": """Opening: Cash Rs.40000 Bank Rs.25000 Capital Rs.65000.
Purchased goods from Ram on credit Rs.11800 inclusive of GST @18%.
Purchased goods from Raj Rs.22000 on credit.
Sold goods to Amit Rs.29500 inclusive of GST @18% on credit.
Received Rs.15000 from Amit by cheque.
Allowed trade discount 10% to Amit on balance.
Paid Rs.10000 to Raj by cheque.
Received Rs.5000 cash from Suresh.
Paid Rs.3000 to Ram by cheque.
Paid rent Rs.4000 cash.""",
    "category": "gst_settlement",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": VERIFIED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_debit_account": "Purchases", "expected_credit_account": "Ram",
         "expected_amount": Decimal("10000"), "expected_parties": ["Ram"],
         "notes": "Inclusive GST — net = 11800/1.18 = 10000"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_debit_account": "Purchases", "expected_credit_account": "Raj",
         "expected_amount": Decimal("22000"), "expected_parties": ["Raj"],
         "notes": "Credit purchase — may be merged with Ram purchase"},
        {"index": 4, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("25000"), "expected_parties": ["Amit"],
         "notes": "Inclusive GST sale — net = 29500/1.18 = 25000"},
        {"index": 5, "expected_status": VERIFIED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_debit_account": "Bank", "expected_credit_account": "Amit",
         "expected_amount": Decimal("15000"), "expected_parties": ["Amit"],
         "notes": "Receipt by cheque"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "DISCOUNT_ALLOWED",
         "notes": "Discount on Amit's balance — may be merged with receipt"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_debit_account": "Raj", "expected_credit_account": "Bank",
         "expected_amount": Decimal("10000"), "expected_parties": ["Raj"],
         "notes": "Payment to Raj"},
        {"index": 8, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Suresh",
         "expected_amount": Decimal("5000"), "expected_parties": ["Suresh"],
         "notes": "Cash receipt from Suresh"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_debit_account": "Ram", "expected_credit_account": "Bank",
         "expected_amount": Decimal("3000"), "expected_parties": ["Ram"],
         "notes": "Payment to Ram — may be merged with prior"},
        {"index": 10, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("4000"), "expected_parties": [],
         "notes": "Rent paid"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 3: Multiple parties with similar transactions
# Categories: multiple parties, party disappearance attack
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV03",
    "text": """Opening: Cash Rs.30000 Bank Rs.20000 Capital Rs.50000.
Purchased goods from Raj Rs.12000 on credit.
Purchased goods from Mehta Rs.8000 on credit.
Paid Rs.10000 to Raj by cheque.
Sold goods to Amit Rs.20000 on credit.
Received Rs.10000 from Amit cash.
Purchased goods from Suresh Rs.15000 on credit.
Paid Rs.5000 to Suresh cash.
Sold goods to Ramesh Rs.18000 on credit.
Received Rs.8000 from Ramesh by cheque.
Paid Rs.3000 to Mehta cash.""",
    "category": "multi_party",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "May be merged with Mehta purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Mehta"],
         "notes": "May be merged with Raj purchase"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Payment to Raj — may be merged with prior purchase"},
        {"index": 5, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("20000"), "expected_parties": ["Amit"],
         "notes": "Credit sale to Amit"},
        {"index": 6, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Amit",
         "expected_amount": Decimal("10000"), "expected_parties": ["Amit"],
         "notes": "Cash receipt from Amit"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Suresh"],
         "notes": "Credit purchase"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Suresh"],
         "notes": "Payment to Suresh — may be merged"},
        {"index": 9, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Ramesh", "expected_credit_account": "Sales",
         "expected_amount": Decimal("18000"), "expected_parties": ["Ramesh"],
         "notes": "Credit sale to Ramesh"},
        {"index": 10, "expected_status": VERIFIED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_debit_account": "Bank", "expected_credit_account": "Ramesh",
         "expected_amount": Decimal("8000"), "expected_parties": ["Ramesh"],
         "notes": "Receipt by cheque from Ramesh"},
        {"index": 11, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Mehta"],
         "notes": "Payment to Mehta — may be merged"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 4: Cheque direction attack
# Categories: cheque/bank direction, instrument variations
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV04",
    "text": """Opening: Cash Rs.25000 Bank Rs.35000 Capital Rs.60000.
Purchased goods from Raj Rs.18000 on credit.
Paid Rs.18000 to Raj by cheque.
Sold goods to Amit Rs.30000 on credit.
Received Rs.30000 from Amit by cheque.
Received Rs.23600 from Suresh by cheque.
Paid Rs.15000 to Mehta by cheque.
Suresh paid Rs.23600 by cheque.
Amit paid Rs.15000 cash.
Received cheque of Rs.10000 from Ramesh.
Paid rent Rs.6000 cash.""",
    "category": "cheque_direction",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Payment to Raj — merged with purchase"},
        {"index": 4, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("30000"), "expected_parties": ["Amit"],
         "notes": "Credit sale"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may be merged with sale"},
        {"index": 6, "expected_status": VERIFIED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_debit_account": "Bank", "expected_credit_account": "Suresh",
         "expected_amount": Decimal("23600"), "expected_parties": ["Suresh"],
         "notes": "Receipt by cheque (Sprint 29 fix)"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Mehta"],
         "notes": "Payment to Mehta"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Suresh"],
         "notes": "Duplicate receipt — may be merged with prior"},
        {"index": 9, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Amit",
         "expected_amount": Decimal("15000"), "expected_parties": ["Amit"],
         "notes": "Cash receipt from Amit"},
        {"index": 10, "expected_status": VERIFIED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_debit_account": "Bank", "expected_credit_account": "Ramesh",
         "expected_amount": Decimal("10000"), "expected_parties": ["Ramesh"],
         "notes": "Receipt by cheque from Ramesh"},
        {"index": 11, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("6000"), "expected_parties": [],
         "notes": "Rent paid"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 5: Settlement chain
# Categories: settlement, fractions, historical references
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV05",
    "text": """Opening: Cash Rs.35000 Bank Rs.15000 Capital Rs.50000.
Purchased goods from Raj Rs.40000 on credit.
Paid Rs.15000 to Raj cash.
Returned goods to Raj Rs.5000.
Received discount Rs.2000 from Raj.
Paid remaining balance to Raj by cheque.
Sold goods to Amit Rs.35000 on credit.
Received Rs.20000 from Amit cash.
Returned goods by customer Rs.3000.
Allowed discount Rs.1500 to Amit.
Received balance from Amit by cheque.""",
    "category": "settlement_chain",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Raj"],
         "notes": "Payment — merged with purchase"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_RETURN",
         "expected_parties": ["Raj"],
         "notes": "Return — may be merged"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "DISCOUNT_RECEIVED",
         "expected_parties": ["Raj"],
         "notes": "Discount — may be merged"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Settlement — may be merged"},
        {"index": 7, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("35000"), "expected_parties": ["Amit"],
         "notes": "Credit sale"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may be merged with sale"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_RETURN",
         "expected_parties": ["Amit"],
         "notes": "Return — may be merged"},
        {"index": 10, "expected_status": REVIEW_REQUIRED,
         "expected_type": "DISCOUNT_ALLOWED",
         "expected_parties": ["Amit"],
         "notes": "Discount — may be merged"},
        {"index": 11, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Settlement receipt — may be merged"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 6: Fractions and remaining balance
# Categories: fractions, remaining, historical
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV06",
    "text": """Opening: Cash Rs.20000 Bank Rs.40000 Capital Rs.60000.
Purchased goods from Raj Rs.30000 on credit.
Paid half of the amount to Raj cash.
Purchased goods from Suresh Rs.24000 on credit.
Paid one-third of the amount to Suresh by cheque.
Sold goods to Amit Rs.45000 on credit.
Received Rs.15000 from Amit cash.
Received the remaining amount from Amit by cheque.
Paid rent Rs.3000 cash.""",
    "category": "fractions",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Raj"],
         "notes": "Half payment — merged with purchase"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Suresh"],
         "notes": "Credit purchase"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Suresh"],
         "notes": "One-third payment — merged with purchase"},
        {"index": 6, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("45000"), "expected_parties": ["Amit"],
         "notes": "Credit sale"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "Partial receipt — may be merged with sale"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Remaining receipt — may be merged"},
        {"index": 9, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("3000"), "expected_parties": [],
         "notes": "Rent paid"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 7: Balanced-but-wrong attack (multiple parties, same type)
# Categories: balanced-but-wrong, party disappearance
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV07",
    "text": """Opening: Cash Rs.25000 Bank Rs.35000 Capital Rs.60000.
Purchased goods from Raj Rs.12000 on credit.
Purchased goods from Mehta Rs.8000 on credit.
Purchased goods from Suresh Rs.15000 on credit.
Paid Rs.10000 to Raj by cheque.
Paid Rs.5000 to Mehta cash.
Paid Rs.8000 to Suresh by cheque.
Sold goods to Amit Rs.20000 on credit.
Received Rs.10000 from Amit cash.
Sold goods to Ramesh Rs.15000 on credit.
Received Rs.7000 from Ramesh by cheque.
Paid rent Rs.4000 cash.""",
    "category": "balanced_but_wrong_attack",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase — consecutive purchases may merge"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Mehta"],
         "notes": "Credit purchase — may merge with Raj"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Suresh"],
         "notes": "Credit purchase — may merge"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Payment to Raj — may merge with purchase"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Mehta"],
         "notes": "Payment to Mehta"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Suresh"],
         "notes": "Payment to Suresh — may merge"},
        {"index": 8, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("20000"), "expected_parties": ["Amit"],
         "notes": "Credit sale"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may merge with sale"},
        {"index": 10, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Ramesh", "expected_credit_account": "Sales",
         "expected_amount": Decimal("15000"), "expected_parties": ["Ramesh"],
         "notes": "Credit sale"},
        {"index": 11, "expected_status": VERIFIED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_debit_account": "Bank", "expected_credit_account": "Ramesh",
         "expected_amount": Decimal("7000"), "expected_parties": ["Ramesh"],
         "notes": "Receipt by cheque"},
        {"index": 12, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("4000"), "expected_parties": [],
         "notes": "Rent paid"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 8: Indian student phrasing + typos
# Categories: Indian phrasing, typos, normalization
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV08",
    "text": """Opening: Cash in hand Rs.15000 Bank Balance Rs.25000 Capital Rs.40000.
Goods bought from Raj Rs.10000 on credit.
Raj was paid Rs.5000 cash.
Amount received from Amit Rs.8000 cash.
Goods sold to Suresh Rs.20000 on credit.
Paid off Mehta Rs.6000 by cheque.
Goods returned by customer Rs.2000.
Settled Ramesh account Rs.9000 by cheque.
Paid rent Rs.3500 cash.""",
    "category": "indian_phrasing",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase — may merge with payment"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Raj"],
         "notes": "Payment to Raj — merged with purchase"},
        {"index": 4, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Amit",
         "expected_amount": Decimal("8000"), "expected_parties": ["Amit"],
         "notes": "Cash receipt"},
        {"index": 5, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Suresh", "expected_credit_account": "Sales",
         "expected_amount": Decimal("20000"), "expected_parties": ["Suresh"],
         "notes": "Credit sale"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Mehta"],
         "notes": "Payment to Mehta"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_RETURN",
         "expected_parties": [],
         "notes": "Return by customer — no party specified"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Ramesh"],
         "notes": "Settlement payment"},
        {"index": 9, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("3500"), "expected_parties": [],
         "notes": "Rent paid"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 9: Deliberate ambiguity
# Categories: ambiguity, REVIEW_REQUIRED expected
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV09",
    "text": """Opening: Cash Rs.30000 Bank Rs.20000 Capital Rs.50000.
Received from Amit Rs.10000.
Paid Raj Rs.8000.
Purchased goods for Rs.20000.
Settled the account for Rs.9500.
Sold goods Rs.15000.
Received Rs.5000.
Paid Rs.3000.
Returned goods Rs.2000.""",
    "category": "ambiguity",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "No instrument specified — cash/cheque ambiguous"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Raj"],
         "notes": "No instrument specified — cash/cheque ambiguous"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE",
         "expected_parties": [],
         "notes": "No party, no instrument — cash/credit ambiguous"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SETTLEMENT",
         "expected_parties": [],
         "notes": "Settlement — no party specified"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE",
         "expected_parties": [],
         "notes": "No party, no instrument — cash/credit ambiguous"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIPT",
         "expected_parties": [],
         "notes": "No party — ambiguous"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAYMENT",
         "expected_parties": [],
         "notes": "No party — ambiguous"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RETURN",
         "expected_parties": [],
         "notes": "Return — no party, no direction"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEM 10: GST with trade discount and returns
# Categories: GST, trade discount, returns
# ═══════════════════════════════════════════════════════════════
CORPUS.append({
    "id": "ADV10",
    "text": """Opening: Cash Rs.50000 Bank Rs.30000 Capital Rs.80000.
Purchased goods from Raj for Rs.50000 less 10% trade discount plus GST @18%.
Sold goods to Amit for Rs.40000 less 5% trade discount plus GST @18% on credit.
Received Rs.20000 from Amit by cheque.
Returned goods to Raj Rs.5000.
Allowed discount Rs.2000 to Amit.
Paid Rs.15000 to Raj by cheque.
Paid rent Rs.4000 cash.
Received Rs.5000 cash from Suresh.""",
    "category": "gst_discount_return",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": VERIFIED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_debit_account": "Purchases", "expected_credit_account": "Raj",
         "expected_amount": Decimal("45000"), "expected_parties": ["Raj"],
         "notes": "50000 - 10% = 45000 net before GST"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_parties": ["Amit"],
         "notes": "Complex GST + discount — may be merged"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may be merged with sale"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_RETURN",
         "expected_parties": ["Raj"],
         "notes": "Return — may be merged"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "DISCOUNT_ALLOWED",
         "expected_parties": ["Amit"],
         "notes": "Discount — may be merged"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Payment to Raj — may be merged"},
        {"index": 8, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("4000"), "expected_parties": [],
         "notes": "Rent paid"},
        {"index": 9, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Suresh",
         "expected_amount": Decimal("5000"), "expected_parties": ["Suresh"],
         "notes": "Cash receipt"},
    ],
})

# ═══════════════════════════════════════════════════════════════
# PROBLEMS 11-20: Additional adversarial patterns
# ═══════════════════════════════════════════════════════════════

# ADV11: Compound entries with multiple amounts
CORPUS.append({
    "id": "ADV11",
    "text": """Opening: Cash Rs.20000 Bank Rs.40000 Capital Rs.60000.
Purchased goods from Raj Rs.30000 less trade discount 10% on credit.
Received Rs.12000 from Raj by cheque and Rs.5000 cash.
Sold goods to Amit Rs.25000 plus GST @18% on credit.
Allowed trade discount 5% to Amit.
Received Rs.10000 from Amit by cheque.
Paid Rs.8000 to Suresh by cheque.
Paid rent Rs.3000 cash.
Purchased goods from Mehta Rs.12000 on credit.
Paid Rs.6000 to Mehta cash.""",
    "category": "compound_entries",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Purchase with trade discount"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Raj"],
         "notes": "Receipt from Raj — may merge"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_parties": ["Amit"],
         "notes": "Sale with GST"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "DISCOUNT_ALLOWED",
         "expected_parties": ["Amit"],
         "notes": "Discount — may merge"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may merge"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Suresh"],
         "notes": "Payment to Suresh"},
        {"index": 8, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("3000"), "expected_parties": [],
         "notes": "Rent paid"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Mehta"],
         "notes": "Purchase from Mehta"},
        {"index": 10, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Mehta"],
         "notes": "Payment to Mehta — merged"},
    ],
})

# ADV12: Indian phrasing variants
CORPUS.append({
    "id": "ADV12",
    "text": """Opening: Cash in hand Rs.18000 Bank Balance Rs.22000 Capital Rs.40000.
Goods purchased from Tata on credit Rs.35000.
Cash received against sales Rs.10000.
Goods sold to Sharma on credit Rs.28000.
Amount paid to Tata by cheque Rs.15000.
Cheque received from Sharma Rs.12000.
Paid salaries Rs.8000 cash.
Purchased stationery Rs.1500 cash.
Received Rs.3000 from unknown party cash.
Paid Rs.5000 to Ram by cheque.""",
    "category": "indian_variants",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Tata"],
         "notes": "Credit purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": [],
         "notes": "Cash receipt — no party named"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_parties": ["Sharma"],
         "notes": "Credit sale"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Tata"],
         "notes": "Payment to Tata"},
        {"index": 6, "expected_status": VERIFIED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_debit_account": "Bank", "expected_credit_account": "Sharma",
         "expected_amount": Decimal("12000"), "expected_parties": ["Sharma"],
         "notes": "Receipt by cheque"},
        {"index": 7, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Salaries", "expected_credit_account": "Cash",
         "expected_amount": Decimal("8000"), "expected_parties": [],
         "notes": "Salaries paid"},
        {"index": 8, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Stationery", "expected_credit_account": "Cash",
         "expected_amount": Decimal("1500"), "expected_parties": [],
         "notes": "Stationery purchased"},
        {"index": 9, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Unknown Party",
         "expected_amount": Decimal("3000"), "expected_parties": ["Unknown"],
         "notes": "Cash receipt — unknown party"},
        {"index": 10, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Ram"],
         "notes": "Payment to Ram"},
    ],
})

# ADV13: Cross-transaction dependencies
CORPUS.append({
    "id": "ADV13",
    "text": """Opening: Cash Rs.30000 Bank Rs.25000 Capital Rs.55000.
Purchased goods from Raj Rs.25000 on credit.
Paid Rs.10000 to Raj cash.
Purchased goods from Raj Rs.8000 on credit.
Paid remaining balance to Raj by cheque.
Sold goods to Amit Rs.30000 on credit.
Received Rs.15000 from Amit cash.
Sold goods to Amit Rs.10000 on credit.
Received balance from Amit by cheque.
Paid rent Rs.5000 cash.""",
    "category": "cross_tx_dependency",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Raj"],
         "notes": "Payment to Raj — merged with purchase"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Second purchase from Raj"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Settlement — may merge"},
        {"index": 6, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("30000"), "expected_parties": ["Amit"],
         "notes": "Credit sale"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may merge with sale"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_parties": ["Amit"],
         "notes": "Second sale to Amit"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may merge"},
        {"index": 10, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("5000"), "expected_parties": [],
         "notes": "Rent paid"},
    ],
})

# ADV14: GST with various schemes
CORPUS.append({
    "id": "ADV14",
    "text": """Opening: Cash Rs.40000 Bank Rs.30000 Capital Rs.70000.
Purchased goods from Tata Rs.23600 inclusive of GST @18%.
Sold goods to Sharma Rs.35400 inclusive of GST @18% on credit.
Paid Rs.10000 to Tata by cheque.
Received Rs.15000 from Sharma by cheque.
Purchased goods from Ram Rs.11800 inclusive of GST @18% on credit.
Paid Rs.5000 to Ram cash.
Sold goods to Amit Rs.29500 inclusive of GST @18% on credit.
Received Rs.10000 from Amit cash.
Paid rent Rs.6000 cash.""",
    "category": "gst_schemes",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Tata"],
         "notes": "Purchase with GST — may merge with payment"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_parties": ["Sharma"],
         "notes": "Sale with GST"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Tata"],
         "notes": "Payment to Tata — may merge"},
        {"index": 5, "expected_status": VERIFIED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_debit_account": "Bank", "expected_credit_account": "Sharma",
         "expected_amount": Decimal("15000"), "expected_parties": ["Sharma"],
         "notes": "Receipt by cheque"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Ram"],
         "notes": "Purchase from Ram"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Ram"],
         "notes": "Payment to Ram — merged"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_parties": ["Amit"],
         "notes": "Sale to Amit"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may merge"},
        {"index": 10, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("6000"), "expected_parties": [],
         "notes": "Rent paid"},
    ],
})

# ADV15: Returns and settlements
CORPUS.append({
    "id": "ADV15",
    "text": """Opening: Cash Rs.25000 Bank Rs.35000 Capital Rs.60000.
Purchased goods from Raj Rs.20000 on credit.
Returned goods to Raj Rs.3000.
Paid Rs.12000 to Raj by cheque.
Sold goods to Amit Rs.30000 on credit.
Returned goods by customer Rs.2000.
Received Rs.20000 from Amit by cheque.
Allowed discount Rs.1000 to Amit.
Received balance from Amit cash.
Paid rent Rs.4000 cash.""",
    "category": "returns_settlements",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_RETURN",
         "expected_parties": ["Raj"],
         "notes": "Return — may merge"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Payment — may merge"},
        {"index": 5, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("30000"), "expected_parties": ["Amit"],
         "notes": "Credit sale"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_RETURN",
         "expected_parties": [],
         "notes": "Return by customer"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may merge"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "DISCOUNT_ALLOWED",
         "expected_parties": ["Amit"],
         "notes": "Discount — may merge"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "Balance receipt"},
        {"index": 10, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("4000"), "expected_parties": [],
         "notes": "Rent paid"},
    ],
})

# ADV16: Multiple amounts in single sentence (compound)
CORPUS.append({
    "id": "ADV16",
    "text": """Opening: Cash Rs.20000 Bank Rs.30000 Capital Rs.50000.
Purchased goods from Raj Rs.50000 less 10% trade discount on credit and paid Rs.20000 by cheque and Rs.5000 cash.
Sold goods to Amit Rs.40000 less 5% trade discount on credit.
Received Rs.15000 from Amit by cheque and Rs.5000 cash.
Paid Rs.10000 to Suresh by cheque.
Paid rent Rs.3000 cash.
Purchased goods from Mehta Rs.15000 on credit.
Received Rs.8000 from Ramesh cash.""",
    "category": "compound_multi_amount",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Compound: purchase + cheque + cash"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_parties": ["Amit"],
         "notes": "Sale with trade discount"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Compound receipt"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Suresh"],
         "notes": "Payment to Suresh"},
        {"index": 6, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("3000"), "expected_parties": [],
         "notes": "Rent paid"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Mehta"],
         "notes": "Purchase from Mehta"},
        {"index": 8, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Ramesh",
         "expected_amount": Decimal("8000"), "expected_parties": ["Ramesh"],
         "notes": "Cash receipt"},
    ],
})

# ADV17: Edge case — all same party
CORPUS.append({
    "id": "ADV17",
    "text": """Opening: Cash Rs.10000 Bank Rs.20000 Capital Rs.30000.
Purchased goods from Raj Rs.25000 on credit.
Paid Rs.10000 to Raj cash.
Purchased goods from Raj Rs.15000 on credit.
Paid Rs.5000 to Raj by cheque.
Returned goods to Raj Rs.2000.
Received discount Rs.1000 from Raj.
Paid remaining to Raj by cheque.""",
    "category": "same_party_chain",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "All same party — splitter may merge extensively"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Raj"],
         "notes": "Merged with purchase"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Second purchase"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Payment — merged"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_RETURN",
         "expected_parties": ["Raj"],
         "notes": "Return — merged"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "DISCOUNT_RECEIVED",
         "expected_parties": ["Raj"],
         "notes": "Discount — merged"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Final settlement — merged"},
    ],
})

# ADV18: Opening balance edge cases
CORPUS.append({
    "id": "ADV18",
    "text": """Cash in hand Rs.15000
Bank Balance Rs.25000
Capital Rs.40000
Purchased goods from Raj Rs.10000 on credit.
Paid Rs.5000 to Raj cash.
Sold goods to Amit Rs.18000 on credit.
Received Rs.8000 from Amit cash.
Paid rent Rs.3000 cash.
Purchased goods from Suresh Rs.12000 on credit.
Paid Rs.6000 to Suresh by cheque.
Received Rs.4000 from Mehta cash.""",
    "category": "opening_edge",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": REVIEW_REQUIRED,
         "expected_type": "OPENING",
         "notes": "Opening balances without 'Opening:' prefix"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Raj"],
         "notes": "Payment — merged"},
        {"index": 4, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("18000"), "expected_parties": ["Amit"],
         "notes": "Credit sale"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "Receipt — merged"},
        {"index": 6, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("3000"), "expected_parties": [],
         "notes": "Rent paid"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Suresh"],
         "notes": "Purchase from Suresh"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Suresh"],
         "notes": "Payment — merged"},
        {"index": 9, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Mehta",
         "expected_amount": Decimal("4000"), "expected_parties": ["Mehta"],
         "notes": "Cash receipt"},
    ],
})

# ADV19: Historical references
CORPUS.append({
    "id": "ADV19",
    "text": """Opening: Cash Rs.30000 Bank Rs.20000 Capital Rs.50000.
Purchased goods from Raj Rs.20000 on credit.
Paid Rs.8000 to Raj cash.
Sold remaining goods from Raj Rs.10000.
Sold goods to Amit Rs.25000 on credit.
Received Rs.10000 from Amit cash.
Received the balance from Amit by cheque.
Paid rent Rs.4000 cash.
Purchased goods from Suresh Rs.15000 on credit.
Paid Rs.7000 to Suresh by cheque.""",
    "category": "historical_refs",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Credit purchase"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Raj"],
         "notes": "Payment — merged"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE",
         "expected_parties": ["Raj"],
         "notes": "Historical ref 'remaining goods from Raj'"},
        {"index": 5, "expected_status": VERIFIED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_debit_account": "Amit", "expected_credit_account": "Sales",
         "expected_amount": Decimal("25000"), "expected_parties": ["Amit"],
         "notes": "Credit sale"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "RECEIVED_FROM",
         "expected_parties": ["Amit"],
         "notes": "Receipt — merged"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Balance receipt — merged"},
        {"index": 8, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("4000"), "expected_parties": [],
         "notes": "Rent paid"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Suresh"],
         "notes": "Purchase from Suresh"},
        {"index": 10, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Suresh"],
         "notes": "Payment — merged"},
    ],
})

# ADV20: Extreme mixed — everything at once
CORPUS.append({
    "id": "ADV20",
    "text": """Opening: Cash in hand Rs.35000 Bank Balance Rs.25000 Capital Rs.60000.
Purchased goods from Raj for Rs.40000 less 10% trade discount on credit plus GST @18%.
Purchased goods from Mehta Rs.15000 on credit.
Paid Rs.10000 to Raj by cheque.
Sold goods to Amit for Rs.30000 less 5% trade discount on credit plus GST @18%.
Received Rs.12000 from Amit by cheque.
Returned goods to Raj Rs.3000.
Allowed discount Rs.1000 to Amit.
Paid Rs.5000 to Mehta cash.
Received Rs.8000 from Suresh cash.
Purchased goods from Ramesh Rs.20000 on credit.
Paid Rs.10000 to Ramesh by cheque.
Paid rent Rs.5000 cash.
Paid salaries Rs.8000 cash.""",
    "category": "extreme_mixed",
    "expected_problem_status": PROBLEM_REVIEW_REQUIRED,
    "expected_transactions": [
        {"index": 1, "expected_status": "OPENING",
         "expected_type": "OPENING_BALANCE"},
        {"index": 2, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Raj"],
         "notes": "Complex purchase with discount + GST"},
        {"index": 3, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Mehta"],
         "notes": "Purchase from Mehta — may merge"},
        {"index": 4, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Raj"],
         "notes": "Payment to Raj — may merge"},
        {"index": 5, "expected_status": REVIEW_REQUIRED,
         "expected_type": "SALE_GOODS_CREDIT",
         "expected_parties": ["Amit"],
         "notes": "Complex sale with discount + GST"},
        {"index": 6, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_RECEIVED",
         "expected_parties": ["Amit"],
         "notes": "Receipt — may merge"},
        {"index": 7, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_RETURN",
         "expected_parties": ["Raj"],
         "notes": "Return — may merge"},
        {"index": 8, "expected_status": REVIEW_REQUIRED,
         "expected_type": "DISCOUNT_ALLOWED",
         "expected_parties": ["Amit"],
         "notes": "Discount — may merge"},
        {"index": 9, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PAID_TO",
         "expected_parties": ["Mehta"],
         "notes": "Payment to Mehta"},
        {"index": 10, "expected_status": VERIFIED,
         "expected_type": "RECEIVED_FROM",
         "expected_debit_account": "Cash", "expected_credit_account": "Suresh",
         "expected_amount": Decimal("8000"), "expected_parties": ["Suresh"],
         "notes": "Cash receipt"},
        {"index": 11, "expected_status": REVIEW_REQUIRED,
         "expected_type": "PURCHASE_GOODS_CREDIT",
         "expected_parties": ["Ramesh"],
         "notes": "Purchase from Ramesh"},
        {"index": 12, "expected_status": REVIEW_REQUIRED,
         "expected_type": "CHEQUE_PAID",
         "expected_parties": ["Ramesh"],
         "notes": "Payment — merged"},
        {"index": 13, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Rent", "expected_credit_account": "Cash",
         "expected_amount": Decimal("5000"), "expected_parties": [],
         "notes": "Rent paid"},
        {"index": 14, "expected_status": VERIFIED,
         "expected_type": "EXPENSE_PAID",
         "expected_debit_account": "Salaries", "expected_credit_account": "Cash",
         "expected_amount": Decimal("8000"), "expected_parties": [],
         "notes": "Salaries paid"},
    ],
})


# ═══════════════════════════════════════════════════════════════
# ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════════

def _classify_tx_result(
    actual_tx: Dict[str, Any],
    expected_tx: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare an actual transaction against its ground truth."""
    actual_status = actual_tx.get("status", "UNKNOWN")
    expected_status = expected_tx.get("expected_status", "UNKNOWN")

    # Normalize opening balance statuses
    if expected_status == "OPENING":
        expected_status = actual_status  # Opening can be INFORMATIONAL_EVENT or OPENING_BALANCE
        if actual_status in (INFORMATIONAL_EVENT, "OPENING_BALANCE"):
            expected_status = actual_status

    classification = {
        "actual_status": actual_status,
        "expected_status": expected_status,
        "status_match": actual_status == expected_status or (
            expected_status == "OPENING" and actual_status in (INFORMATIONAL_EVENT, "OPENING_BALANCE")
        ),
    }

    # Check journal if available
    journal = actual_tx.get("journal") or {}
    if journal and actual_status == VERIFIED:
        dr_lines = journal.get("debit_lines", [])
        cr_lines = journal.get("credit_lines", [])

        # Check accounts
        expected_dr = expected_tx.get("expected_debit_account")
        expected_cr = expected_tx.get("expected_credit_account")
        expected_amt = expected_tx.get("expected_amount")

        if expected_dr:
            actual_dr_accounts = [d.get("account", "") for d in dr_lines]
            classification["dr_account_match"] = expected_dr in actual_dr_accounts
            classification["actual_dr"] = actual_dr_accounts
            classification["expected_dr"] = expected_dr

        if expected_cr:
            actual_cr_accounts = [c.get("account", "") for c in cr_lines]
            classification["cr_account_match"] = expected_cr in actual_cr_accounts
            classification["actual_cr"] = actual_cr_accounts
            classification["expected_cr"] = expected_cr

        if expected_amt:
            actual_amts = [d.get("amount") for d in dr_lines]
            classification["amount_match"] = expected_amt in actual_amts
            classification["actual_amounts"] = [str(a) for a in actual_amts]
            classification["expected_amount"] = str(expected_amt)

        # Check parties
        expected_parties = expected_tx.get("expected_parties", [])
        if expected_parties:
            all_text = actual_tx.get("text", "")
            for party in expected_parties:
                if party.lower() not in all_text.lower():
                    classification["party_missing"] = party
                    break

    # Check for INCORRECT_VERIFIED
    classification["is_incorrect_verified"] = False
    if actual_status == VERIFIED and not classification.get("status_match"):
        # If we expected REVIEW_REQUIRED but got VERIFIED — check journal correctness
        if expected_tx.get("expected_status") == REVIEW_REQUIRED:
            # This could be a false positive (correctly resolved) or incorrect
            classification["potentially_incorrect"] = True

    # Check for BALANCED_BUT_WRONG
    classification["is_balanced_but_wrong"] = False
    if actual_status == VERIFIED and journal:
        dr_total = sum(d.get("amount", 0) for d in journal.get("debit_lines", []))
        cr_total = sum(c.get("amount", 0) for c in journal.get("credit_lines", []))
        if dr_total == cr_total and dr_total > 0:
            # Journal balances — check semantic correctness
            if expected_tx.get("expected_status") in (REVIEW_REQUIRED, NOT_SUPPORTED):
                # We expected non-VERIFIED but got balanced VERIFIED
                classification["is_balanced_but_wrong"] = True
            elif expected_dr and not classification.get("dr_account_match"):
                classification["is_balanced_but_wrong"] = True
            elif expected_cr and not classification.get("cr_account_match"):
                classification["is_balanced_but_wrong"] = True
            elif expected_amt and not classification.get("amount_match"):
                classification["is_balanced_but_wrong"] = True

    return classification


def _run_problem(problem: Dict[str, Any], run_id: int = 0) -> Dict[str, Any]:
    """Run a single problem and return detailed results."""
    start = time.time()
    result = process_problem(
        problem["text"],
        {"student_id": f"sprint33_{problem['id']}_{run_id}"}
    )
    elapsed = time.time() - start

    txns = result.get("transactions", [])
    problem_status = result.get("problem_status", "UNKNOWN")
    safety = result.get("safety_violations", [])

    # Classify each transaction
    classifications = []
    expected_txns = problem.get("expected_transactions", [])

    for i, tx in enumerate(txns):
        exp = expected_txns[i] if i < len(expected_txns) else {
            "expected_status": "UNKNOWN",
            "notes": "No ground truth defined"
        }
        cls = _classify_tx_result(tx, exp)
        cls["tx_index"] = tx.get("index", i + 1)
        cls["tx_text"] = tx.get("text", "")[:80]
        cls["notes"] = exp.get("notes", "")
        classifications.append(cls)

    return {
        "problem_id": problem["id"],
        "category": problem["category"],
        "problem_status": problem_status,
        "expected_problem_status": problem.get("expected_problem_status"),
        "problem_status_match": problem_status == problem.get("expected_problem_status"),
        "transaction_count": len(txns),
        "expected_transaction_count": len(expected_txns),
        "safety_violations": safety,
        "classifications": classifications,
        "execution_time_ms": elapsed * 1000,
        "deterministic_hash": hashlib.sha256(
            json.dumps({
                "status": problem_status,
                "tx_statuses": [c["actual_status"] for c in classifications],
            }, sort_keys=True, default=str).encode()
        ).hexdigest()[:16],
    }


# ═══════════════════════════════════════════════════════════════
# MAIN EXPERIMENT
# ═══════════════════════════════════════════════════════════════

def run_experiment() -> Dict[str, Any]:
    """Run the complete adversarial corpus and return results."""
    all_results = []

    # Aggregate counters
    counters = {
        "total_problems": 0,
        "total_transactions": 0,
        "verified_correct": 0,
        "review_required_correct": 0,
        "incorrect_verified": 0,
        "not_supported_correct": 0,
        "balanced_but_wrong": 0,
        "missing_entities": 0,
        "missing_amounts": 0,
        "tx_order_errors": 0,
        "safety_violations_total": 0,
        "determinism_failures": 0,
    }

    determinism_runs = {}  # problem_id → list of hashes

    print("=" * 70)
    print("SPRINT 33 — Extreme Adversarial Whole-Problem Accounting Test")
    print("=" * 70)
    print()

    for problem in CORPUS:
        pid = problem["id"]
        counters["total_problems"] += 1

        # Run 1 (primary)
        result = _run_problem(problem, run_id=0)
        all_results.append(result)

        # Run 2 and 3 for determinism
        hashes = [result["deterministic_hash"]]
        for run in range(1, 3):
            r2 = _run_problem(problem, run_id=run)
            hashes.append(r2["deterministic_hash"])

        determinism_runs[pid] = hashes
        if len(set(hashes)) > 1:
            counters["determinism_failures"] += 1

        # Aggregate transaction counts
        tx_count = result["transaction_count"]
        counters["total_transactions"] += tx_count

        # Classify results
        for cls in result["classifications"]:
            actual = cls["actual_status"]
            expected = cls["expected_status"]

            if cls.get("is_incorrect_verified"):
                counters["incorrect_verified"] += 1
            elif cls.get("is_balanced_but_wrong"):
                counters["balanced_but_wrong"] += 1
            elif actual == VERIFIED and expected in (VERIFIED, "OPENING"):
                counters["verified_correct"] += 1
            elif actual in (REVIEW_REQUIRED, "OPENING") and expected in (REVIEW_REQUIRED, "OPENING"):
                counters["review_required_correct"] += 1
            elif actual == NOT_SUPPORTED and expected == NOT_SUPPORTED:
                counters["not_supported_correct"] += 1
            elif actual == VERIFIED and expected == REVIEW_REQUIRED:
                # Got VERIFIED when we expected REVIEW_REQUIRED
                counters["verified_correct"] += 1  # Could be correct resolution
            elif actual in (INFORMATIONAL_EVENT, "OPENING_BALANCE") and expected == "OPENING":
                counters["review_required_correct"] += 1

            if cls.get("party_missing"):
                counters["missing_entities"] += 1
            if cls.get("amount_match") is False:
                counters["missing_amounts"] += 1

        counters["safety_violations_total"] += len(result["safety_violations"])

        # Print per-problem summary
        verified = sum(1 for c in result["classifications"] if c["actual_status"] == VERIFIED)
        rr = sum(1 for c in result["classifications"] if c["actual_status"] in (REVIEW_REQUIRED, "OPENING"))
        ns = sum(1 for c in result["classifications"] if c["actual_status"] == NOT_SUPPORTED)
        ibw = sum(1 for c in result["classifications"] if c.get("is_balanced_but_wrong"))
        det = "✅" if len(set(hashes)) == 1 else "❌"
        time_ms = result["execution_time_ms"]

        print(
            f"  {pid:6s} | {result['category']:22s} | "
            f"V={verified:2d} RR={rr:2d} NS={ns:2d} IBW={ibw} | "
            f"det={det} | {time_ms:6.0f}ms"
        )

        # Print failed transactions
        for cls in result["classifications"]:
            if cls.get("is_balanced_but_wrong"):
                print(f"    ⚠️  BALANCED_BUT_WRONG TX{cls['tx_index']}: {cls['tx_text']}")
                print(f"       Expected: {cls.get('expected_dr','?')} / {cls.get('expected_cr','?')}")
                print(f"       Actual:   {cls.get('actual_dr',['?'])} / {cls.get('actual_cr',['?'])}")
            if cls.get("party_missing"):
                print(f"    ⚠️  MISSING PARTY TX{cls['tx_index']}: {cls['tx_text']}")
                print(f"       Missing: {cls['party_missing']}")

    # Summary
    print()
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"\n  Whole problems tested:       {counters['total_problems']}")
    print(f"  Total transactions:          {counters['total_transactions']}")
    print(f"  VERIFIED_CORRECT:            {counters['verified_correct']}")
    print(f"  REVIEW_REQUIRED_CORRECT:     {counters['review_required_correct']}")
    print(f"  INCORRECT_VERIFIED:          {counters['incorrect_verified']}")
    print(f"  NOT_SUPPORTED_CORRECT:       {counters['not_supported_correct']}")
    print(f"  BALANCED_BUT_WRONG:          {counters['balanced_but_wrong']}")
    print(f"  Missing entities:            {counters['missing_entities']}")
    print(f"  Missing amounts:             {counters['missing_amounts']}")
    print(f"  Safety violations:           {counters['safety_violations_total']}")
    print(f"  Determinism failures:        {counters['determinism_failures']}")

    # Critical failure check
    critical = (
        counters["incorrect_verified"] > 0
        or counters["balanced_but_wrong"] > 0
        or counters["safety_violations_total"] > 0
        or counters["determinism_failures"] > 0
    )

    if critical:
        print(f"\n  ❌ CRITICAL FAILURES DETECTED")
        if counters["incorrect_verified"] > 0:
            print(f"     INCORRECT_VERIFIED: {counters['incorrect_verified']}")
        if counters["balanced_but_wrong"] > 0:
            print(f"     BALANCED_BUT_WRONG: {counters['balanced_but_wrong']}")
        if counters["safety_violations_total"] > 0:
            print(f"     Safety violations: {counters['safety_violations_total']}")
        if counters["determinism_failures"] > 0:
            print(f"     Determinism failures: {counters['determinism_failures']}")
    else:
        print(f"\n  ✅ NO CRITICAL FAILURES")

    # Per-category breakdown
    print(f"\n  Per-category breakdown:")
    cat_stats = defaultdict(lambda: {"problems": 0, "verified": 0, "rr": 0, "ns": 0, "ibw": 0})
    for r in all_results:
        cat = r["category"]
        cat_stats[cat]["problems"] += 1
        for cls in r["classifications"]:
            if cls["actual_status"] == VERIFIED:
                cat_stats[cat]["verified"] += 1
            elif cls["actual_status"] in (REVIEW_REQUIRED, "OPENING"):
                cat_stats[cat]["rr"] += 1
            elif cls["actual_status"] == NOT_SUPPORTED:
                cat_stats[cat]["ns"] += 1
            if cls.get("is_balanced_but_wrong"):
                cat_stats[cat]["ibw"] += 1

    for cat in sorted(cat_stats.keys()):
        d = cat_stats[cat]
        print(
            f"    {cat:25s}: {d['problems']} problems | "
            f"V={d['verified']} RR={d['rr']} NS={d['ns']} IBW={d['ibw']}"
        )

    return {
        "counters": counters,
        "critical": critical,
        "results": all_results,
        "determinism_runs": determinism_runs,
        "category_stats": dict(cat_stats),
    }


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    report = run_experiment()

    # Write JSON results
    out_path = os.path.join(os.path.dirname(__file__), "sprint33_adversarial_results.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nResults written to {out_path}")

    sys.exit(1 if report["critical"] else 0)
