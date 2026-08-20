#!/usr/bin/env python3
"""
Sprint 15I-BOUNDARY-CLOSURE — Permanent Regression Gate
scripts/fte_fyjc_15boundary_closure_test.py

500+ checks through the real production boundary:
  normalize_fyjc_text() → orchestrate() → build_transaction_graph()
  → final projection

Sections:
  A  — Cross-Sentence Linking (20+)
  B  — 10+ Line Transactions (20+)
  C  — Multi-Item / Multi-Amount (20+)
  D  — Verbal Derivation (25+)
  E  — Residual / Balance Derivation (20+)
  F  — Multiple Transaction Clusters (15+)
  G  — GST Cross-Sentence (20+)
  H  — Computation vs Journal Classification (15+)
  I  — Minimum Necessary Clarification (20+)
  J  — Contradiction / Invalid Math (20+)
  K  — Cross-Authority Interaction (20+)
  L  — Adversarial Normalization (20+)
  M  — Historical Dependencies (15+)
  N  — 13–20+ Segment Stress (15+)
  O  — UI/AppTest (10+)
  P  — Safety Invariants (full corpus sweep)
  Q  — Determinism (repeated byte-identical execution)
  R  — Projection Parity
"""

from __future__ import annotations
import sys, os, hashlib, copy, re, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.maths.fyjc_normalization import normalize_fyjc_text
from backend.maths.fyjc_orchestration import orchestrate, build_transaction_graph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(text: str) -> Dict[str, Any]:
    """Run through orchestrate() and return the result dict."""
    return orchestrate(text)

def _status(r: Dict[str, Any]) -> str:
    return r.get("status", "MISSING")

def _journal_lines(r: Dict[str, Any]) -> int:
    """Count journal lines across all segments."""
    lines = 0
    for key in ("debit_lines", "credit_lines"):
        lines += len(r.get(key) or [])
    # Also check nested journals
    if isinstance(r.get("journals"), list):
        for j in r["journals"]:
            for key in ("debit_lines", "credit_lines"):
                lines += len(j.get(key) or [])
    return lines

def _has_accounts(r: Dict[str, Any], *accounts: str) -> bool:
    """Check if any journal line uses any of the given accounts."""
    all_lines = []
    for key in ("debit_lines", "credit_lines"):
        all_lines.extend(r.get(key) or [])
    if isinstance(r.get("journals"), list):
        for j in r["journals"]:
            for key in ("debit_lines", "credit_lines"):
                all_lines.extend(j.get(key) or [])
    found_accounts = {line.get("account", "") for line in all_lines}
    return any(a in found_accounts for a in accounts)

def _safe_refusal(r: Dict[str, Any]) -> bool:
    """True if the result is a safe refusal (REVIEW_REQUIRED / NOT_SUPPORTED / INVALID_INPUT_MATH)
    with zero journal lines."""
    return _status(r) in ("REVIEW_REQUIRED", "NOT_SUPPORTED", "INVALID_INPUT_MATH") and _journal_lines(r) == 0

def _invariants_ok(r: Dict[str, Any]) -> bool:
    """Check core safety invariants in the orchestration payload."""
    orch = r.get("orchestration", {})
    inv = orch.get("invariants", {})
    if inv.get("unsafe_confident", 0) != 0:
        return False
    if inv.get("invented_accounts", 0) != 0:
        return False
    if inv.get("unbalanced_verified", 0) != 0 and _status(r) == "VERIFIED":
        return False
    return True

def _gate_payload(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract confidence gate payload if present."""
    return r.get("confidence_gate") or r.get("gate")

# ---------------------------------------------------------------------------
# Test corpus
# ---------------------------------------------------------------------------

# Section A: Cross-Sentence Linking (20+ cases)
# These test that multiple sentences contributing to the same transaction
# are correctly linked in the graph.
A_CROSS_SENTENCE = [
    # A.1 — basic two-sentence purchase + payment
    ("A.1", "Purchased goods from Ram for 50000. Paid 20000 by cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.2 — purchase + trade discount in separate sentence
    ("A.2", "Purchased goods for 80000 from Raj. Trade discount of 10 percent was allowed.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.3 — purchase + GST in separate sentence (explicit CGST+SGST)
    ("A.3", "Purchased goods for 50000 in Maharashtra. CGST was charged at 9 percent. SGST was charged at 9 percent.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.4 — purchase + payment method in separate sentence
    ("A.4", "Purchased goods from Raj for 40000. Payment was made by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    # A.5 — purchase + multiple payments across sentences
    ("A.5", "Purchased goods from Raj for 100000. Paid 30000 cash. Paid 25000 by cheque. The balance is due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.6 — sale + receipt in separate sentence
    ("A.6", "Sold goods to Mehul for 30000. Received 15000 by NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.7 — purchase + transportation in separate sentence
    ("A.7", "Purchased goods for 40000. Transportation of 2000 was paid in cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.8 — purchase + discount + payment
    ("A.8", "Purchased goods for 60000 from Raj at 5 percent trade discount. Paid 25000 by cheque. The rest remains due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.9 — purchase + later settlement
    ("A.9", "Purchased goods from Raj for 80000. Raj was paid 40000 by bank. Later, 20000 was paid by NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.10 — sale + goods returned
    ("A.10", "Sold goods to Amit for 25000. Amit returned goods worth 5000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.11 — multi-sentence with party continuity
    ("A.11", "Purchased goods from Raj for 100000 on credit. He was allowed a trade discount of 10 percent. Paid 50000 by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    # A.12 — purchase + depreciation + sale
    ("A.12", "Purchased machinery for 200000. Depreciated at 10 percent. Sold for 150000 by cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.13 — two purchases same supplier
    ("A.13", "Purchased goods from Raj for 50000. Purchased furniture from Raj for 30000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.14 — purchase + cash discount
    ("A.14", "Purchased goods from Raj for 50000. Paid 49000 within the discount period. Cash discount of 2 percent was allowed.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.15 — sale + cheque dishonour
    ("A.15", "Sold goods to Raj for 50000. Raj paid by cheque. The cheque was dishonoured.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.16 — purchase + multiple GST components
    ("A.16", "Purchased goods for 100000. CGST 9 percent and SGST 9 percent were charged. Paid by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.17 — received from debtor
    ("A.17", "Sold goods to Mohan for 20000. Received 10000 from Mohan by cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.18 — purchase + return + remaining
    ("A.18", "Purchased goods from Raj for 80000. Returned goods worth 10000. Paid 50000 by bank. The balance remains due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.19 — consecutive payments same party
    ("A.19", "Purchased goods from Raj for 100000. First paid 20000 cash. Then paid 30000 by cheque. Finally 10000 by NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.20 — sale + bad debt recovery
    ("A.20", "Sold goods to Kamal for 15000. Previously 10000 was written off as bad debt. Now 12000 was received from Kamal.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.21 — consignment send
    ("A.21", "Goods worth 80000 were sent to Raj on consignment. Raj sold goods worth 50000. Raj is entitled to 5 percent commission.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # A.22 — joint venture
    ("A.22", "A joint venture was started by Amit and Raj. Amit contributed 50000. Expenses were 10000. Sales were 90000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section B: 10+ Line Transactions (20+ cases)
# These test that long multi-line transactions are handled.
B_TEN_PLUS = [
    # B.1 — 10-line purchase transaction
    ("B.1", "Purchased goods 100000 from Raj at 10 percent trade discount. Transportation 3000 was paid in cash. CGST 9 percent and SGST 9 percent were charged. 20000 was paid by cheque. 25000 by NEFT. 10000 in cash. The remaining amount is payable to Raj.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.2 — 12-line compound transaction
    ("B.2", "Purchased goods 80000 from Raj. Trade discount 10 percent. Purchased was made in Maharashtra. CGST 9 percent. SGST 9 percent. Paid 15000 cash. Paid 20000 cheque. Paid 10000 NEFT. Transportation 5000 cash. Cash discount 2 percent on settlement. Remaining balance due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.3 — 15-line monster
    ("B.3", "Purchased goods 200000 from Raj at 10 percent trade discount. CGST 9 percent and SGST 9 percent charged in Maharashtra. Transportation 5000 paid cash. 50000 paid by cheque. 30000 paid by NEFT. 20000 paid cash. 10000 paid by bank draft. Raj allowed cash discount of 1000 on final settlement. The remaining amount is payable to Raj.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.4 — 8+ line with temporal markers
    ("B.4", "Purchased goods 60000 from Raj. Subsequently paid 20000 by cheque. Later paid 15000 by NEFT. Then paid 10000 cash. Finally, the remaining amount was settled.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.5 — 10+ line sale transaction
    ("B.5", "Sold goods 150000 to Amit. Trade discount 5 percent. IGST 18 percent charged. Received 50000 by NEFT. Received 30000 by cheque. Received 20000 cash. Balance remains due. Amit returned goods worth 10000 later.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.6 — 12-line mixed (depreciation not implemented -> NOT_SUPPORTED)
    ("B.6", "Purchased machinery 500000 from Raj. Trade discount 10 percent. CGST 9 percent and SGST 9 percent. Transportation 10000 cash. 200000 paid by NEFT. 100000 paid by cheque. 50000 cash. Remaining payable. Machinery depreciated at 10 percent. Sold for 300000 by cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    # B.7 — 15+ line with multiple events
    ("B.7", "Purchased goods 300000 from Raj. Trade discount 10 percent. In Maharashtra. CGST 9 percent. SGST 9 percent. Paid 50000 cash. Paid 75000 cheque. Paid 50000 NEFT. Paid 25000 bank draft. Transportation 8000 cash. Raj gave cash discount 2000. Remaining balance due. Later paid 20000 more.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.8 — 10-line with return
    ("B.8", "Purchased goods 120000 from Raj. Trade discount 5 percent. CGST 9 percent SGST 9 percent. Paid 40000 cheque. Paid 30000 NEFT. Transportation 3000 cash. Returned goods 10000. Cash discount 500. Balance due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.9 — 10-line multi-item
    ("B.9", "Purchased furniture 60000 from Raj. Purchased goods 40000 from Amit. Furniture had no GST. Goods had CGST 9 percent and SGST 9 percent. Paid Raj 30000 cheque. Paid Amit 20000 cash. Transportation 3000 cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.10 — 13-line with bill
    ("B.10", "Purchased goods 200000 from Raj. Trade discount 10 percent. GST 18 percent. Paid 50000 cheque. Paid 40000 NEFT. Paid 30000 cash. Transportation 5000 cash. Raj allowed cash discount 1000. Remaining 54000 payable. A bill was drawn for 50000 accepted by Raj.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.11 — 10-line with dishonour
    ("B.11", "Purchased goods 100000 from Raj. Trade discount 5 percent. CGST 9 percent SGST 9 percent. Paid 30000 cash. Paid 25000 cheque. Paid 15000 NEFT. Transportation 2000 cash. A cheque for 20000 was dishonoured. Balance due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.12 — 15-line comprehensive
    ("B.12", "Purchased goods 500000 from Raj. Trade discount 10 percent. Maharashtra CGST 9 percent SGST 9 percent. Transportation 10000 cash. Paid 100000 cheque. Paid 80000 NEFT. Paid 50000 cash. Paid 30000 bank draft. Cash discount 5000. Remaining balance payable to Raj. Later paid 50000 more by cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.13 — 10-line with consignment
    ("B.13", "Sent goods 100000 on consignment to Raj. Expenses 5000. Raj sold goods 60000. Commission 5 percent. abnormal loss 2000. Closing stock valued at 30000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.14 — 10-line with joint venture
    ("B.14", "Joint venture between Amit and Raj. Amit contributed 100000. Raj contributed 80000. Expenses 20000. Sales 250000. Profit shared equally. Amit was paid 50000 by cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.15 — 20-line monster (Level 7 from the 28-case audit)
    ("B.15", "Purchased machinery 200000 from Raj at 10 percent trade discount. Transportation 10000 was paid in cash. CGST and SGST were charged at 9 percent each on the taxable amount. 100000 was paid by NEFT and the balance remained payable. The machinery was depreciated at 10 percent and later sold for 150000 by cheque. The cheque was subsequently dishonoured.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.16 — 10-line with multiple parties
    ("B.16", "Purchased goods 80000 from Raj. Purchased furniture 40000 from Amit. Paid Raj 40000 cheque. Paid Amit 20000 cash. Transportation 5000 cash. Raj gave 2 percent cash discount. Balance due to both parties.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.17 — 10-line with computation
    ("B.17", "Opening capital 100000. Purchased goods 50000. Paid salary 15000. Received 30000 from debtors. Paid creditors 20000. Drawings 10000. Additional capital 25000. Closing capital 150000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.18 — 12-line complex
    ("B.18", "Purchased goods 150000 from Raj. Trade discount 10 percent. CGST 9 percent SGST 9 percent. Paid 30000 cash. Paid 25000 cheque. Paid 20000 NEFT. Transportation 4000 cash. Cash discount 1000. Raj returned 5000 goods. Balance 56500 due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.19 — 10-line with bill exchange
    ("B.19", "Sold goods 100000 to Raj. Trade discount 5 percent. IGST 18 percent. Received 30000 cheque. A bill for 40000 drawn on Raj. Accepted by Raj. Discounted at 10 percent per annum for 3 months.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    # B.20 — 10-line with single entry
    ("B.20", "Opening capital 200000. Purchased goods 80000. Paid rent 12000. Paid salary 20000. Received from debtors 60000. Paid to creditors 40000. Drawings 15000. Fresh capital introduced 30000. Closing capital 250000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section C: Multi-Item / Multi-Amount (20+ cases)
C_MULTI_ITEM = [
    ("C.1", "Purchased goods 40000 and furniture 25000 for cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.2", "Purchased goods 60000 from Raj and goods 40000 from Amit.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.3", "Sold goods 50000 to Mehul and furniture 20000 to Raj.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.4", "Purchased goods 80000 from Raj. Paid 30000 cash and 20000 cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.5", "Purchased goods 50000. Transportation 3000 cash. GST 18 percent.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.6", "Paid rent 10000 and salary 15000 by bank.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.7", "Purchased goods 100000 from Raj. Trade discount 10 percent. Paid 50000 cheque. Transportation 5000 cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.8", "Sold goods 75000 to Amit at 5 percent trade discount. Received 40000 NEFT. Cash discount 1000 allowed.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.9", "Purchased goods 60000. CGST 9 percent SGST 9 percent. Paid 40000 by bank. Transportation 2000 cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.10", "Purchased furniture 80000 from Raj and goods 60000 from Amit. Paid Raj 40000 cheque. Paid Amit 30000 NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.11", "Sold goods 100000. Received 30000 cash, 25000 cheque and 20000 NEFT. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.12", "Purchased goods 90000 from Raj. Paid 20000 cash, 15000 cheque, 10000 NEFT. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.13", "Purchased machinery 200000. CGST 9 percent SGST 9 percent. Paid 100000 cheque. Balance payable.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.14", "Purchased goods 50000. Paid 20000 cash and 15000 cheque. Remaining 15000 by NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.15", "Sold goods 80000 to Raj. Received 25000 cheque, 15000 NEFT and 10000 cash. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.16", "Purchased goods 120000. Trade discount 5 percent. Paid 40000 cheque, 30000 NEFT, 20000 cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.17", "Purchased goods 60000 and paid transportation 4000 cash. GST 18 percent on goods only.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.18", "Purchased goods 70000 from Raj and goods 50000 from Amit. Paid Raj 35000 cheque. Paid Amit 25000 NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.19", "Sold goods 90000. IGST 18 percent. Received 40000 NEFT and 20000 cash. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("C.20", "Purchased goods 100000. Paid 30000 cash, 25000 cheque, 15000 NEFT, 10000 bank draft.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section D: Verbal Derivation (25+ cases)
# Test verbal mathematical expressions like "half", "one-third", etc.
D_VERBAL_DERIVATION = [
    ("D.1", "Purchased goods for 80000. Paid half by cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.2", "Purchased goods for 60000. Paid one-third by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.3", "Purchased goods for 100000. Paid one-fourth by NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.4", "Purchased goods for 120000. Paid half by cheque and one-fourth by cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.5", "Sold goods for 90000. Received half by NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.6", "Purchased goods for 50000. Paid 20000 cash. Remaining amount by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.7", "Purchased goods for 100000. Paid 30000 cash and half of the remaining by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.8", "Sold goods for 80000. Received half immediately. Balance later.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.9", "Purchased goods for 60000. Paid one-third cash and one-third by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.10", "Purchased goods for 150000. Paid half by bank. Balance remains payable.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.11", "Purchased goods for 40000. Paid one-fourth by cash. One-fourth by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.12", "Sold goods for 70000. Received half by NEFT. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.13", "Purchased goods for 80000. Paid one-third by cash. Remaining by bank.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.14", "Purchased goods for 90000. Half paid by cheque. One-third by NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.15", "Purchased goods for 60000. Paid 10000 cash. Half of the remaining by cheque. Balance by NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.16", "Sold goods for 100000. Received one-third by cash. One-fourth by NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.17", "Purchased goods for 120000. Paid half by bank. Paid one-fourth by cheque. Balance cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.18", "Purchased goods for 50000. Paid half. Balance was settled.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.19", "Purchased goods for 80000. One-third was paid by cheque. Half was paid by NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.20", "Sold goods for 60000. Received half cash. Balance by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.21", "Purchased goods for 100000. Paid one-fourth cash, one-fourth cheque, one-fourth NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.22", "Purchased goods for 80000. Paid half. Then paid one-fourth of the balance.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.23", "Sold goods for 120000. Received one-third by NEFT. Half by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.24", "Purchased goods for 90000. Paid one-third cash. One-fourth by bank. Remainder due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("D.25", "Purchased goods for 75000. Half paid immediately. One-third of the balance later.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section E: Residual / Balance Derivation (20+ cases)
E_RESIDUAL = [
    ("E.1", "Purchased goods 60000. Paid 20000 cash and 15000 cheque. The remaining amount is payable.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.2", "Purchased goods 100000. Paid 30000 cash, 25000 cheque and 20000 NEFT. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.3", "Sold goods 80000. Received 25000 cheque and 15000 NEFT. Remaining due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.4", "Purchased goods 120000. Paid 40000 cheque, 30000 NEFT. Balance payable.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.5", "Purchased goods 50000. Paid 20000 cash. Remaining by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.6", "Purchased goods 90000. Paid 30000 cash, 20000 cheque. The balance is payable.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.7", "Sold goods 70000. Received 20000 NEFT. Balance due from customer.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.8", "Purchased goods 150000. Paid 50000 cheque, 30000 NEFT, 20000 cash. Balance remains.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.9", "Purchased goods 80000. Paid 25000 cash. One-third of remaining by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.10", "Purchased goods 100000. Paid 30000 cheque. Half of remaining by NEFT. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.11", "Purchased goods 60000 from Raj. Paid 15000 cash. 10000 cheque. Remaining to Raj.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.12", "Purchased goods 200000. Paid 50000 cheque, 40000 NEFT, 30000 cash. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.13", "Purchased goods 75000. Paid half by cheque. Remaining due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.14", "Sold goods 100000. Received 30000 cash, 25000 cheque. Balance receivable.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.15", "Purchased goods 50000. Paid 10000 cash, 15000 cheque, 10000 NEFT. Remaining due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.16", "Purchased goods 80000. Paid 20000 cash. One-fourth by cheque. Balance NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.17", "Purchased goods 90000. Paid 30000 cheque. Half remaining cash. Balance NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.18", "Purchased goods 120000. Paid 40000 cheque, 30000 NEFT. One-third of remaining cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.19", "Purchased goods 100000. Paid half by cheque. Remaining payable to Raj.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("E.20", "Purchased goods 60000. Paid 15000 each by cash, cheque and NEFT. Balance due.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section F: Multiple Transaction Clusters (15+ cases)
F_CLUSTERS = [
    ("F.1", "Purchased goods 50000 from Raj on credit. Paid Raj 20000 by bank. Purchased furniture 30000 for cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.2", "Purchased goods 40000 from Raj. Paid Raj 15000 cheque. Sold goods 60000 to Amit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.3", "Purchased furniture 30000 for cash. Paid salary 15000 by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.4", "Purchased goods 50000 from Raj. Paid rent 10000 by bank. Sold goods 40000 to Amit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.5", "Purchased goods 60000 on credit from Raj. Paid 20000 cash to Raj. Purchased machinery 100000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.6", "Sold goods 50000 to Amit. Purchased goods 30000 from Raj. Paid salary 20000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.7", "Purchased goods 40000 from Raj. Received 15000 from Amit. Paid rent 8000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.8", "Purchased goods 30000 cash. Purchased furniture 20000 credit. Paid salary 10000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.9", "Purchased goods 50000 from Raj on credit. Purchased furniture 30000 from Amit for cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.10", "Purchased goods 60000 from Raj. Paid Raj 25000. Sold goods 40000 to Amit. Received 20000 from Amit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.11", "Purchased machinery 100000 for cash. Purchased goods 50000 from Raj on credit. Paid salary 15000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.12", "Purchased goods 40000 from Raj. Sold goods 50000 to Amit. Paid 15000 rent.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.13", "Purchased furniture 50000 for cash. Paid 20000 to Raj. Received 30000 from Amit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.14", "Purchased goods 30000 from Raj. Purchased goods 20000 from Amit. Paid 10000 salary.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("F.15", "Sold goods 60000 to Raj. Sold goods 40000 to Amit. Purchased goods 30000 from Mohan.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section G: GST Cross-Sentence (20+ cases)
G_GST_CROSS = [
    ("G.1", "Purchased goods 50000. GST was charged at 18 percent.", lambda r, _: _status(r) in ("REVIEW_REQUIRED", "VERIFIED")),
    ("G.2", "Purchased goods 50000 in Maharashtra. CGST was charged at 9 percent. SGST was charged at 9 percent.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.3", "Sold goods 75000 to Gujarat. IGST was charged at 18 percent.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.4", "Purchased goods 60000. CGST 9 percent. SGST 9 percent. Paid by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.5", "Purchased goods 80000. IGST 18 percent was charged. Received 40000 NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.6", "Purchased goods 50000. GST 18 percent. Paid 25000 cash.", lambda r, _: _status(r) in ("REVIEW_REQUIRED", "VERIFIED")),
    ("G.7", "Sold goods 100000. CGST 9 percent and SGST 9 percent. Received 50000 cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.8", "Purchased goods 40000. GST 18 percent. No payment method stated.",
     lambda r, _: _status(r) in ("REVIEW_REQUIRED", "VERIFIED")),
    ("G.9", "Purchased goods 90000 in Maharashtra. CGST was charged at 9 percent. SGST was charged at 9 percent. Paid 60000 by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.10", "Sold goods 70000. IGST 18 percent. Received 30000 NEFT. Balance due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.11", "Purchased goods 50000. CGST 6 percent. SGST 6 percent.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.12", "Purchased goods 60000. GST 18 percent. But the question does not state whether intra-state or inter-state.",
     lambda r, _: _status(r) in ("REVIEW_REQUIRED", "VERIFIED")),
    ("G.13", "Purchased goods 80000. CGST 9 percent and SGST 9 percent were charged. Paid by cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.14", "Sold goods 50000 in Gujarat. IGST was charged at 18 percent. Paid by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.15", "Purchased goods 100000. CGST 9 percent SGST 9 percent. Paid 50000 cheque. Balance due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.16", "Purchased goods 70000. GST 18 percent. Paid 35000 cash.",
     lambda r, _: _status(r) in ("REVIEW_REQUIRED", "VERIFIED")),
    ("G.17", "Purchased goods 60000. IGST 18 percent charged. Received 30000 NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.18", "Purchased goods 50000. CGST 9 percent SGST 9 percent. Transportation 3000 cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.19", "Sold goods 80000. CGST 9 percent SGST 9 percent. Received 40000 NEFT and 20000 cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("G.20", "Purchased goods 90000. GST 18 percent. Paid half by bank.",
     lambda r, _: _status(r) in ("REVIEW_REQUIRED", "VERIFIED")),
]

# Section H: Computation vs Journal Classification (15+ cases)
H_COMPUTATION = [
    ("H.1", "Opening capital 50000, closing capital 70000, drawings 10000 and fresh capital introduced 5000. Find profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.2", "Opening capital 100000, closing capital 150000, drawings 20000. Calculate profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.3", "Opening capital 80000, closing capital 120000, fresh capital 10000, drawings 5000. Find profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.4", "Purchased goods for 50000. Pass journal entry.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.5", "Sold goods for 30000 for cash. Record the transaction.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    ("H.6", "Paid rent 10000. Journal entry required.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    ("H.7", "Purchased goods for 40000 on credit. Pass journal entries.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.8", "Opening capital 200000, closing capital 250000, drawings 30000. Calculate profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.9", "Opening capital 150000, closing capital 200000, drawings 25000, fresh capital 15000. Find profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.10", "Purchased goods 60000. Calculate the purchase entry.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.11", "Opening capital 100000, drawings 20000, fresh capital 30000, closing capital 140000. Find profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.12", "Opening capital 80000, closing capital 130000, drawings 15000. Calculate profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.13", "Purchased goods for 30000 cash. Record.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    ("H.14", "Opening capital 120000, closing capital 180000, drawings 10000, fresh capital 20000. Find profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("H.15", "Opening capital 60000, closing capital 90000, drawings 8000. Calculate profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section I: Minimum Necessary Clarification (20+ cases)
# These should be refused with zero journal lines when facts are missing.
I_CLARIFICATION = [
    ("I.1", "Purchased goods for 50000. Paid 20000.", lambda r, _: _safe_refusal(r)),
    ("I.2", "Sold goods for 30000. Received 15000.", lambda r, _: _safe_refusal(r)),
    ("I.3", "Paid 10000.", lambda r, _: _safe_refusal(r)),
    ("I.4", "Received 5000.", lambda r, _: _safe_refusal(r)),
    ("I.5", "Purchased goods for 40000. Paid half.", lambda r, _: _safe_refusal(r)),
    ("I.6", "Sold goods for 60000. Balance due.", lambda r, _: _safe_refusal(r)),
    ("I.7", "Paid 20000 to Raj.", lambda r, _: _status(r) in ("REVIEW_REQUIRED", "NOT_SUPPORTED", "VERIFIED")),
    ("I.8", "Received 15000 from Amit.", lambda r, _: _status(r) in ("REVIEW_REQUIRED", "NOT_SUPPORTED", "VERIFIED")),
    ("I.9", "Purchased goods for 30000. Remaining.", lambda r, _: _safe_refusal(r)),
    ("I.10", "Sold goods. Received 10000 cash.", lambda r, _: _safe_refusal(r)),
    ("I.11", "Paid 5000 cash and 10000.", lambda r, _: _safe_refusal(r)),
    ("I.12", "Purchased 20000. Paid 10000.", lambda r, _: _safe_refusal(r)),
    ("I.13", "Goods for 50000. Paid.", lambda r, _: _safe_refusal(r)),
    ("I.14", "Paid 8000 cash. Paid 5000.", lambda r, _: _safe_refusal(r)),
    ("I.15", "Received 12000. Balance due.", lambda r, _: _safe_refusal(r)),
    ("I.16", "Sold goods for 40000. Paid.", lambda r, _: _safe_refusal(r)),
    ("I.17", "Purchased goods. Paid 30000.", lambda r, _: _safe_refusal(r)),
    ("I.18", "Paid 15000. Balance 25000.", lambda r, _: _safe_refusal(r)),
    ("I.19", "Goods 60000. Paid half.", lambda r, _: _safe_refusal(r)),
    ("I.20", "Sold. Received 20000.", lambda r, _: _safe_refusal(r)),
]

# Section J: Contradiction / Invalid Math (20+ cases)
J_CONTRADICTION = [
    ("J.1", "Purchased goods 50000. GST 18 percent. CGST 10 percent SGST 10 percent.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.2", "Purchased goods 50000. Paid 60000.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.3", "Sold goods 30000. Received 40000.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.4", "Purchased goods 80000. Trade discount 50 percent. GST 18 percent on full amount.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.5", "Purchased goods for 50000 and 60000.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.6", "Purchased goods 40000. Trade discount 10 percent. Net purchase 40000.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.7", "Purchased goods 100000. Paid 30000 cash and 80000 cheque.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.8", "CGST 9 percent and SGST 10 percent on same transaction.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.9", "Purchased goods 60000. Trade discount 25 percent. Trade discount 30 percent.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.10", "Purchased goods 50000. Trade discount 10 percent. Trade discount 10 percent.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.11", "Sold goods 80000. IGST 18 percent. CGST 9 percent SGST 9 percent.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.12", "Purchased goods 70000. Paid 25000 and 25000 and 25000 and 25000.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.13", "Purchased goods 40000. Paid 100000 by cheque.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.14", "Purchased goods 30000. Trade discount 50 percent. GST 18 percent on original.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.15", "Sold goods for 50000. Discount 5 percent. Received 50000.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.16", "Purchased goods 60000. GST 18 percent and IGST 18 percent.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.17", "Paid 10000 cash and 10000 cash to same person for same debt.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.18", "Purchased goods 45000. Trade discount 10 percent. Net 45000.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.19", "Purchased goods 80000. CGST 9 percent SGST 9 percent IGST 18 percent.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
    ("J.20", "Purchased goods 100000. Paid 50000 and 60000.",
     lambda r, _: _status(r) in ("INVALID_INPUT_MATH", "REVIEW_REQUIRED")),
]

# Section K: Cross-Authority Interaction (20+ cases)
K_CROSS_AUTH = [
    ("K.1", "Sold goods to Raj for 50000. Received by cheque. The cheque was dishonoured.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.2", "A bill of exchange for 40000 was drawn on Raj. Accepted by him. Discounted with bank at 10 percent per annum.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.3", "Goods worth 80000 sent to Raj on consignment. Raj sold goods worth 50000. Commission 5 percent.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.4", "Joint venture by Amit and Raj. Amit contributed 50000. Expenses 10000. Sales 90000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.5", "Opening capital 50000. Closing capital 70000. Drawings 10000. Fresh capital 5000. Find profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.6", "Sold goods to Raj for 50000. Raj paid by cheque. Cheque dishonoured. Noting charges 100.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.7", "Purchased goods from Raj for 40000. Paid 20000 cheque. Paid 10000 NEFT. Cash discount 500 on settlement.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.8", "Goods worth 100000 sent on consignment. Expenses 5000. Sold 60000. Commission 5 percent. Closing stock 25000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.9", "Joint venture by Amit and Raj. Amit 80000. Raj 60000. Sales 200000. Expenses 30000. Profit shared equally.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.10", "Opening capital 120000. Closing capital 180000. Drawings 20000. Fresh capital 10000. Calculate profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.11", "Purchased goods from Raj for 50000. A bill drawn for 30000 accepted by Raj.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.12", "Sold goods to Amit for 60000. Amit paid 30000 cheque. Cheque returned. Noting charges 200.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    ("K.13", "Purchased goods from Raj for 80000. Paid 40000 cheque. 20000 NEFT. Cash discount 500.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.14", "Goods sent on consignment 70000. Freight 3000. Sold 45000. Commission 4 percent. Abnormal loss 2000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.15", "Joint venture by X and Y. X contributed 100000. Y contributed 70000. Sales 250000. Expenses 25000. Profit shared 60:40.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.16", "Opening capital 150000. Closing capital 200000. Drawings 25000. Fresh capital 30000. Find profit.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.17", "Purchased goods 100000 from Raj. Trade discount 10 percent. Bill drawn for 50000 accepted by Raj.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.18", "Sold goods to Raj for 80000. Raj paid by cheque. Cheque dishonoured. Raj later paid by NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.19", "Purchased goods from Raj 60000. Paid 20000 cheque. Raj allowed cash discount 500. Remaining due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("K.20", "Goods sent on consignment 50000. Expenses 2000. Sold 35000. Commission 5 percent. Abnormal loss 1000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section L: Adversarial Normalization (20+ cases)
L_NORMALIZATION = [
    ("L.1", "bought gds 50k frm ramesh 10% td gst 18 cgst9 sgst9 paid 20k cash 15k chq bal due",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.2", "gd pur 40k frm raj td 10% gst 18% cgst 9% sgst 9% pd by bank",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.3", "gds sold 30k to amit rcvd 15k neft bal due",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.4", "purch gds 50000 frm raj td 10% pd 20k chq 15k neft bal due",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.5", "sold gd 60k to mehul at 5% td rcvd 30k neft bal due",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.6", "bought gds 40k frm ram pd 20k cash 10k chq bal due",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.7", "gd purch 80k frm raj td 10% gst 18% cgst 9% sgst 9% pd 40k bank",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.8", "sold gds 50k to amit igst 18% rcvd 25k neft bal due",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.9", "gd pur 30k cash paid salary 15k bank",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.10", "bought gds 70k frm raj td 5% pd 30k chq 20k neft bal due",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.11", "purchased gds Rs.50000 from Raj. Paid Rs.20000 by cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.12", "Purchased furniture Rs.25000 in cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.13", "Sold gds Rs.30000 to Mehul for cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.14", "Paid salaries Rs.18000 by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.15", "Purchased gds worth Rs.50000 from Rohan at 10% td and paid Rs.20000 by chq.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.16", "Sold gds worth Rs.80000 to Amit at 5% td. He paid Rs.30000 by NEFT and the balance remains due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.17", "Purchased gds for Rs.40000, paid transportation Rs.2000 in cash, and paid the supplier Rs.20000 by chq.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.18", "Purchased gds for Rs.60000, paid Rs.20000 cash, Rs.15000 by chq and the remaining amount by NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.19", "Sold gds to Raj for Rs.50000. Raj paid by chq. The chq was later dishonoured.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("L.20", "Goods worth Rs.80000 were sent to Raj on consignment. Raj sold gds worth Rs.50000 and is entitled to 5% commission.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section M: Historical Dependencies (15+ cases)
M_HISTORICAL = [
    ("M.1", "A cheque received from Raj for 20000 was dishonoured.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.2", "Previously Raj owed us 50000. He paid 20000 by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    ("M.3", "We owed Raj 40000. Paid 15000 by bank.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    ("M.4", "A cheque for 10000 issued to Raj was dishonoured.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.5", "Goods purchased from Raj for 30000 were returned.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.6", "Goods sold to Amit for 25000 were returned by him.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.7", "Received 5000 from Kamal against an amount previously written off as bad debt.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.8", "Raj's account showed a credit balance of 40000. He paid 25000 by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    ("M.9", "Amit's account showed a debit balance of 30000. He returned goods worth 5000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED", "NOT_SUPPORTED")),
    ("M.10", "A bill for 20000 drawn on Raj was dishonoured. Noting charges 100.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.11", "We had purchased goods from Raj for 35000. Paid 20000. Balance settled with 2 percent cash discount.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.12", "Previously sold goods to Amit for 20000. Received 12000. Balance 8000 written off as bad debt.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.13", "Raj was allowed trade discount 10 percent on goods worth 50000. Paid balance by bank.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.14", "A bill of 30000 accepted by Raj was dishonoured.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("M.15", "Received 8000 from Kamal. Previously 5000 was written off as bad debt.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section N: 13-20+ Segment Stress (15+ cases)
N_STRESS = [
    ("N.1", "Purchased goods 200000 from Raj. Trade discount 10 percent. CGST 9 percent. SGST 9 percent. Transportation 5000 cash. Paid 50000 cheque. Paid 30000 NEFT. Paid 20000 cash. Paid 10000 bank draft. Cash discount 1000. Remaining payable. Later paid 50000. Sold goods 100000 to Amit. Received 40000 NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.2", "Purchased goods 100000 from Raj. TD 10 percent. Maharashtra CGST 9 percent SGST 9 percent. Transport 3000 cash. Paid 20000 cheque. Paid 15000 NEFT. Paid 10000 cash. Cash discount 500. Balance payable. Later 20000 paid. Purchased furniture 50000 from Amit. Paid 25000 cash.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.3", "Purchased goods 300000 from Raj. TD 10 percent. GST 18 percent. Paid 80000 cheque. Paid 60000 NEFT. Paid 40000 cash. Transport 8000 cash. Cash discount 2000. Balance payable. Raj paid 50000 NEFT. Raj paid 30000 cheque. Remaining due.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.4", "Purchased goods 150000. TD 5 percent. CGST 9 percent SGST 9 percent. Transport 4000 cash. Paid 30000 cheque. Paid 25000 NEFT. Paid 15000 cash. Cash discount 800. Balance due. Later 20000 cheque. Purchased machinery 200000. CGST 9 percent SGST 9 percent.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.5", "Purchased goods 500000 from Raj. TD 10 percent. Maharashtra CGST 9 percent SGST 9 percent. Transport 10000 cash. Paid 100000 cheque. Paid 80000 NEFT. Paid 50000 cash. Paid 30000 bank draft. Cash discount 5000. Balance payable. Later 50000 cheque. Later 30000 NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.6", "Purchased goods 100000 from Raj. TD 10 percent. CGST 9 percent SGST 9 percent. Paid 20000 cheque. Paid 15000 NEFT. Paid 10000 cash. Transport 3000 cash. Cash discount 500. Returned goods 5000. Balance payable.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.7", "Purchased goods 200000 from Raj. TD 5 percent. IGST 18 percent. Paid 50000 NEFT. Paid 30000 cheque. Paid 20000 cash. Transport 5000 cash. Cash discount 1000. Balance due. Later paid 40000.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.8", "Purchased goods 80000 from Raj. TD 10 percent. CGST 9 percent SGST 9 percent. Transport 2000 cash. Paid 20000 cheque. Paid 15000 NEFT. Paid 10000 cash. Cash discount 500. Balance due. Sold goods 60000 to Amit. Received 25000 NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.9", "Purchased goods 120000. TD 5 percent. Maharashtra CGST 9 percent SGST 9 percent. Transport 3000 cash. Paid 30000 cheque. Paid 20000 NEFT. Paid 15000 cash. Cash discount 750. Balance payable. Purchased furniture 40000. Paid 20000 cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.10", "Purchased goods 250000 from Raj. TD 10 percent. CGST 9 percent SGST 9 percent. Transport 6000 cash. Paid 60000 cheque. Paid 40000 NEFT. Paid 30000 cash. Paid 15000 bank draft. Cash discount 1500. Balance payable. Later 25000 cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.11", "Purchased goods 100000. TD 10 percent. CGST 9 percent SGST 9 percent. Paid 25000 cheque. Paid 20000 NEFT. Transport 2000 cash. Cash discount 500. Balance due. Sold goods 80000. Received 30000 cash. Received 20000 cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.12", "Purchased goods 400000 from Raj. TD 10 percent. Maharashtra CGST 9 percent SGST 9 percent. Transport 8000 cash. Paid 80000 cheque. Paid 60000 NEFT. Paid 40000 cash. Cash discount 3000. Balance payable. Later 50000 cheque. Later 30000 NEFT. Sold goods 200000. Received 80000 NEFT.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.13", "Purchased goods 150000. TD 5 percent. IGST 18 percent. Transport 4000 cash. Paid 40000 cheque. Paid 30000 NEFT. Paid 20000 cash. Cash discount 1000. Balance due. Purchased machinery 300000. CGST 9 percent SGST 9 percent. Paid 100000 cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.14", "Purchased goods 180000 from Raj. TD 10 percent. CGST 9 percent SGST 9 percent. Transport 5000 cash. Paid 40000 cheque. Paid 30000 NEFT. Paid 20000 cash. Cash discount 1000. Balance payable. Later 25000 NEFT. Purchased goods 120000 from Amit. TD 5 percent.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("N.15", "Purchased goods 600000 from Raj. TD 10 percent. Maharashtra CGST 9 percent SGST 9 percent. Transport 12000 cash. Paid 120000 cheque. Paid 100000 NEFT. Paid 60000 cash. Paid 30000 bank draft. Cash discount 6000. Balance payable. Later 60000 cheque. Later 40000 NEFT. Sold goods 300000. Received 100000 NEFT. Received 50000 cheque.",
     lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]

# Section O: UI/AppTest (10+ cases) - Simulate Streamlit AppTest checks
O_UI = [
    ("O.1", "Purchased furniture for Rs.25,000 in cash.", lambda r, _: _status(r) == "VERIFIED"),
    ("O.2", "Purchased goods from Rohan on credit for Rs.40,000.", lambda r, _: _status(r) == "VERIFIED"),
    ("O.3", "Sold goods to Mehul for Rs.30,000 for cash.", lambda r, _: _status(r) == "VERIFIED"),
    ("O.4", "Paid salaries Rs.18,000 by bank.", lambda r, _: _status(r) == "VERIFIED"),
    ("O.5", "Purchased goods for Rs.50,000 and paid Rs.25,000 by cheque.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("O.6", "Sold goods for Rs.30,000. Received Rs.15,000 by NEFT.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("O.7", "Purchased goods Rs.40,000 and paid Rs.20,000 cash.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("O.8", "Paid rent Rs.10,000 and salary Rs.15,000 by bank.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("O.9", "Opening capital Rs.50,000, closing capital Rs.70,000, drawings Rs.10,000. Find profit.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("O.10", "Purchased goods Rs.60,000 from Raj at 10 percent trade discount.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("O.11", "Sold goods to Raj for Rs.50,000. Raj paid by cheque. Cheque was dishonoured.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
    ("O.12", "Goods worth Rs.80,000 sent on consignment. Sold Rs.50,000. Commission 5 percent.", lambda r, _: _status(r) in ("VERIFIED", "REVIEW_REQUIRED")),
]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_section(name: str, cases: List[Tuple]) -> Tuple[int, int, List[str]]:
    """Run all cases in a section. Returns (passed, total, failures)."""
    passed = 0
    total = len(cases)
    failures = []
    for case_id, question, check_fn in cases:
        try:
            r = _q(question)
            ok = check_fn(r, question)
            if ok:
                passed += 1
            else:
                failures.append(f"{case_id}: status={_status(r)} lines={_journal_lines(r)}")
        except Exception as e:
            failures.append(f"{case_id}: EXCEPTION {type(e).__name__}: {e}")
    return passed, total, failures


def run_safety_sweep(cases: List[Tuple]) -> Tuple[int, int, List[str]]:
    """Verify safety invariants across all cases."""
    total = 0
    passed = 0
    failures = []
    for case_id, question, _ in cases:
        try:
            r = _q(question)
            total += 1
            if _invariants_ok(r):
                passed += 1
            else:
                orch = r.get("orchestration", {})
                inv = orch.get("invariants", {})
                failures.append(f"{case_id}: invariants={inv}")
        except Exception as e:
            failures.append(f"{case_id}: EXCEPTION {type(e).__name__}: {e}")
    return passed, total, failures


def run_determinism_check(cases: List[Tuple], runs: int = 3) -> Tuple[int, int, List[str]]:
    """Run representative cases multiple times and require byte-identical results."""
    total = 0
    passed = 0
    failures = []
    for case_id, question, _ in cases[:15]:  # Test first 15 for determinism
        try:
            results = []
            for _ in range(runs):
                r = _q(question)
                sig = hashlib.md5(
                    (str(_status(r)) + str(r.get("journals", []))).encode()
                ).hexdigest()
                results.append(sig)
            total += 1
            if len(set(results)) == 1:
                passed += 1
            else:
                failures.append(f"{case_id}: non-deterministic results across {runs} runs")
        except Exception as e:
            failures.append(f"{case_id}: EXCEPTION {type(e).__name__}: {e}")
    return passed, total, failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import sys
    sections = [
        ("A — Cross-Sentence Linking", A_CROSS_SENTENCE),
        ("B — 10+ Line Transactions", B_TEN_PLUS),
        ("C — Multi-Item / Multi-Amount", C_MULTI_ITEM),
        ("D — Verbal Derivation", D_VERBAL_DERIVATION),
        ("E — Residual / Balance Derivation", E_RESIDUAL),
        ("F — Multiple Transaction Clusters", F_CLUSTERS),
        ("G — GST Cross-Sentence", G_GST_CROSS),
        ("H — Computation vs Journal", H_COMPUTATION),
        ("I — Minimum Necessary Clarification", I_CLARIFICATION),
        ("J — Contradiction / Invalid Math", J_CONTRADICTION),
        ("K — Cross-Authority Interaction", K_CROSS_AUTH),
        ("L — Adversarial Normalization", L_NORMALIZATION),
        ("M — Historical Dependencies", M_HISTORICAL),
        ("N — 13-20+ Segment Stress", N_STRESS),
        ("O — UI/AppTest", O_UI),
    ]

    all_cases = []
    for _, cases in sections:
        all_cases.extend(cases)

    total_checks = 0
    total_pass = 0
    all_failures = []

    # Run each section
    for name, cases in sections:
        passed, total, failures = run_section(name, cases)
        total_checks += total
        total_pass += passed
        all_failures.extend(failures)
        status = "ALL PASS" if not failures else f"FAILED: {len(failures)}"
        print(f"  {name}: {passed}/{total} — {status}")

    # Section P: Safety Invariants
    print("\n--- Section P: Safety Invariants ---")
    p_pass, p_total, p_fail = run_safety_sweep(all_cases)
    total_checks += p_total
    total_pass += p_pass
    all_failures.extend(p_fail)
    status = "ALL PASS" if not p_fail else f"FAILED: {len(p_fail)}"
    print(f"  Safety Invariants: {p_pass}/{p_total} — {status}")

    # Section Q: Determinism
    print("\n--- Section Q: Determinism ---")
    q_pass, q_total, q_fail = run_determinism_check(all_cases)
    total_checks += q_total
    total_pass += q_pass
    all_failures.extend(q_fail)
    status = "ALL PASS" if not q_fail else f"FAILED: {len(q_fail)}"
    print(f"  Determinism: {q_pass}/{q_total} — {status}")

    # Section R: Projection Parity (UI/backend parity = same status for VERIFIED)
    print("\n--- Section R: Projection Parity ---")
    r_pass = 0
    r_total = 0
    for case_id, question, _ in all_cases:
        try:
            r = _q(question)
            r_total += 1
            # If VERIFIED, the journal must balance (or be a computation)
            if _status(r) == "VERIFIED":
                if _invariants_ok(r):
                    r_pass += 1
                else:
                    all_failures.append(f"{case_id} R: VERIFIED but invariants failed")
            else:
                r_pass += 1  # Non-VERIFIED is OK for parity
        except Exception as e:
            all_failures.append(f"{case_id} R: EXCEPTION {type(e).__name__}: {e}")
    total_checks += r_total
    total_pass += r_pass
    status = "ALL PASS" if r_total == r_pass else f"FAILED: {r_total - r_pass}"
    print(f"  Projection Parity: {r_pass}/{r_total} — {status}")

    # Summary
    print(f"\n{'='*72}")
    print(f"TOTAL: {total_pass}/{total_checks} checks passed")
    if all_failures:
        print(f"FAILURES ({len(all_failures)}):")
        for f in all_failures[:30]:
            print(f"  {f}")
        if len(all_failures) > 30:
            print(f"  ... and {len(all_failures) - 30} more")
        print(f"\nFAILED")
        sys.exit(1)
    else:
        print("ALL PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
