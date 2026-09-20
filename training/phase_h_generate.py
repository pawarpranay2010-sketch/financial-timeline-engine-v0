#!/usr/bin/env python3
"""phase_h_generate.py — Phase H financial semantic dataset generator.

Produces training_data/phase_h_v01_8000.jsonl (+ manifest + docs/phase_h/
artifacts). Target ~8,000 validated examples exercising the THREE EXISTING
authorities only (Phases A-F). No authority, contract, or production change.

Architecture (measured, not assumed — see PLATRIXA_PHASE_H_REPORT.md):

    template registry (event families from Phase G ontology)
        ↓  design-time KERNEL PRE-FLIGHT: every supported family must prove
        ↓  kernel-VERIFIED wordings before it enters the pool (families that
        ↓  fail are demoted to defensive/REVIEW_REQUIRED and logged)
    deterministic style engine (formal/business/narration/abbrev/OCR/API/
        number-words/currency/clutter — semantics-preserving unless the
        style is deliberately degrading, which makes it a defensive class)
        ↓
    FYJCAISpecialist.parse()          ← structural 18-field machinery
        ↓
    kernel-wording family consensus   ← honest classification layer
        ↓   (the heuristic specialist provably cannot label RECEIPT/PAYMENT
        ↓    families; the accounting kernel wordings are the ground truth
        ↓    for classification, metadata records label_source)
    schema_verifier (allow_expanded)  ← production contract validation
        ↓
    ExpandedGroundingGate.ground()    ← production grounding (fail-closed)
        ↓
    duplicate / near-dup / leakage / party-sanity / metadata-consistency
        ↓
    accepted row (18-field output + STRICTLY SEPARATE Phase G metadata)

Non-goals enforced in code:
  - the model target NEVER claims VERIFIED (the runtime decides status);
    expected_status lives in METADATA only
  - no new 18-field fields, enums, or authority capabilities
  - LOAN / interest-on-loan / refundable-deposit families are EXCLUDED
    entirely: the live kernel verifies basic loan postings while the frozen
    Phase G ontology marks the family gap=true — a Phase G ↔ implementation
    conflict (stop condition §20.3). Training either boundary would be
    improvising; the conflict is documented for a future phase instead.
  - no OCR engine, no document parser, no LLM generation (deterministic only)

Determinism: PYTHONHASHSEED=0 pinned before imports (Phase 21 R1),
random.Random(SEED) only, sorted iteration, no timestamps, byte-identical
rebuild. Secrets: none read, none written.
"""

from __future__ import annotations

import os
import sys

# Determinism pin (Phase 21 finding R1): pin BEFORE importing the runtime.
if os.environ.get("PYTHONHASHSEED", "") != "0":
    os.execve(sys.executable, [sys.executable] + sys.argv,
              dict(os.environ, PYTHONHASHSEED="0"))

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from backend.maths.fyjc_ai_specialist import (  # noqa: E402
    FYJCAISpecialist,
    _detect_payment_method,
    _detect_transaction_type,
    _extract_parties,
    _extract_amounts,
)
from backend.maths.schema_verifier import validate_structured_interpretation  # noqa: E402
from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate  # noqa: E402
from backend.maths.fyjc_contract import (  # noqa: E402
    VALID_TRANSACTION_TYPES,
    VALID_PAYMENT_METHODS,
    VALID_AMBIGUITY_TYPES,
    VALID_SAFETY_FLAGS,
    VALID_SCOPE_FLAGS,
)
from training.phase20_generate import (  # noqa: E402  (reused primitives)
    normalise,
    token_set,
    jaccard,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEED = 20260919
TARGET = 8000
POOL_FACTOR = 2.4            # ~140% surplus; validators reject the rest
NEAR_DUP_JACCARD = 0.75      # same threshold as Phase 20/22
# Paraphrase-abundance caps: quality is enforced by the production validators
# and near-duplicate gates, NOT by these guards; they only prevent a single
# event cell from dominating. The original corpus reached 321 rows/signature
# (Phase 20 note); Phase H permits 150 to train many-expressions-one-semantics.
SAME_EVENT_CAP = 150         # max rows per normalised event signature
FAMILY_CELL_CAP = 300        # max rows per (family, tx_enum, pm_enum) cell
MIN_ABISTENTION_SHARE = 0.15  # Phase G guardrail: >=15% abstention/review

OUT_PATH = _PROJECT_ROOT / "training_data" / "phase_h_v01_8000.jsonl"
MANIFEST_PATH = _PROJECT_ROOT / "training_data" / "phase_h_v01_manifest.json"
PHASE_H_DOCS = _PROJECT_ROOT / "docs" / "phase_h"

GENERATOR_VERSION = "phaseh-generator-1.0.0"
VALIDATOR_VERSION = "phaseh-validator-1.0.0"

FORBIDDEN_OUTPUT_KEYS = {
    "journal", "debit_lines", "credit_lines", "ledger", "balances",
    "debit_account", "credit_account", "journal_entry",
}

# Prior corpora for leakage control (locked sets are read, NEVER modified).
PRIOR_CORPORA = [
    "training_data/fyjc_specialist_1000.jsonl",
    "training_data/fyjc_specialist_train.jsonl",
    "training_data/fyjc_specialist_validation.jsonl",
    "training_data/fyjc_specialist_test.jsonl",
    "training_data/fyjc_hardcore_1000.jsonl",
    "training_data/phase22_v02_train.jsonl",
    "training_data/phase24_foundation_scout.jsonl",
    "training/phase17_benchmark.jsonl",
]

# Phase G artifacts hashed into the manifest (source-of-truth lineage).
PHASE_G_ARTIFACTS = [
    "PLATRIXA_PHASE_G_REPORT.md",
    "docs/phase_g/market_use_cases.json",
    "docs/phase_g/financial_input_taxonomy.json",
    "docs/phase_g/financial_event_ontology.json",
    "docs/phase_g/relationship_taxonomy.json",
    "docs/phase_g/defensive_taxonomy.json",
    "docs/phase_g/18_field_coverage_matrix.json",
    "docs/phase_g/dataset_architecture.json",
]

# ---------------------------------------------------------------------------
# Shared vocabulary pools (fixed order — never iterate a set when emitting)
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "Rahul", "Mohan", "Suresh", "Kavita", "Amit", "Priya", "Vijay", "Sunil",
    "Anita", "Rakesh", "Meena", "Farhan", "Deepak", "Sunita", "Manoj",
    "Pooja", "Arjun", "Neha", "Sanjay", "Rekha", "Vikram", "Shreya",
    "Naveen", "Divya", "Ajay", "Swati", "Rohit", "Kiran", "Prakash",
    "Lata", "Sameer", "Geeta", "Nitin", "Anjali", "Harish", "Sarita",
    "Yogesh", "Bhavna", "Dinesh", "Komal", "Gaurav", "Preeti", "Alok",
    "Madhuri", "Ramesh", "Sneha", "Tarun", "Isha", "Vimal", "Ritu",
    "Ashok", "Kalyani", "Girish", "Trupti", "Mahesh", "Bela",
]

FIRM_NAMES = [
    "Sharma Traders", "Gupta and Sons", "Verma Enterprises", "Iyer and Co",
    "Patil Stores", "Desai Brothers", "Khan Exports", "Mehta Agencies",
    "Reddy and Company", "Joshi Suppliers", "Bose Traders",
    "Nair Enterprises", "Kapoor Retail", "Menon and Company",
    "Chopra Deals", "Sinha Traders", "Rao and Rao", "Bhatia Mart",
    "Sethi Hardware", "Pillai Textiles", "Dutta and Daughters",
    "Agrawal Supplies", "Kulkarni Agro", "Zaveri Jewellers",
]

GOODS = [
    "stationery", "groceries", "medicines", "packaging materials",
    "computer accessories", "sports equipment", "readymade garments",
    "school uniforms", "kitchenware", "electrical fittings", "toys",
    "leather bags", "cosmetics", "hardware items",
]

ASSETS = ["office furniture", "machinery", "computers", "printer",
          "delivery van", "air conditioner", "water purifier"]

EXPENSES = [
    ("rent", "Rent"), ("salary", "Salaries"), ("salaries", "Salaries"),
    ("wages", "Wages"), ("insurance", "Insurance Premium"),
    ("electricity bill", "Electricity Charges"),
    ("telephone bill", "Telephone Charges"),
    ("carriage inwards", "Carriage Inwards"),
    ("carriage outwards", "Carriage Outwards"),
    ("printing charges", "Printing and Stationery"),
    ("postage", "Postage Expenses"),
    ("advertisement", "Advertisement Expenses"),
    ("conveyance", "Conveyance Expenses"),
    ("office expenses", "Office Expenses"),
    ("audit fees", "Audit Fees"),
    ("commission", "Commission Paid"),
]

AMOUNTS = [500, 600, 750, 800, 900, 1100, 1200, 1400, 1500, 1800, 2000,
           2200, 2400, 2500, 2800, 3000, 3500, 4000, 4500, 5000, 5500,
           6000, 6500, 7000, 7500, 8000, 8500, 9000, 9500, 10000, 11000,
           12000, 13000, 14000, 15000, 16000, 18000, 20000, 22000, 25000,
           28000, 30000, 35000, 40000, 45000, 50000, 55000, 60000, 75000,
           90000, 100000, 120000, 150000, 180000, 200000, 250000, 300000,
           350000, 400000, 500000]

# NOTE: payment-channel vocabulary lives in the production detector
# (fyjc_ai_specialist._detect_payment_method) — the single source of truth
# this generator uses for labels. No duplicate channel table is kept here.
# Production semantics: NEFT/RTGS/IMPS/online-transfer wordings resolve to
# the BANK enum (documented finding in PLATRIXA_PHASE_H_REPORT.md).

NUMBER_WORDS = {
    500: "five hundred", 800: "eight hundred", 1200: "one thousand two hundred",
    1500: "fifteen hundred", 2000: "two thousand", 2500: "two thousand five hundred",
    3000: "three thousand", 4000: "four thousand", 5000: "five thousand",
    6000: "six thousand", 7500: "seven thousand five hundred",
    8000: "eight thousand", 9000: "nine thousand", 10000: "ten thousand",
    12000: "twelve thousand", 15000: "fifteen thousand", 18000: "eighteen thousand",
    20000: "twenty thousand", 25000: "twenty five thousand",
    30000: "thirty thousand", 40000: "forty thousand", 50000: "fifty thousand",
}

DISTRACTORS = [
    "The shop was near the bus stand.",
    "Their accountant was on leave that week.",
    "It was the last working day of the month.",
    "The market was unusually crowded that day.",
    "The delivery van arrived late in the evening.",
    "Power went off in the evening.",
]

OUT_OF_DOMAIN = [
    "The quick brown fox jumps over the lazy dog near the river bank every morning.",
    "def total = sum(line.amount for line in invoice.lines)",
    "Roses are red, violets are blue, the ledger is balanced, thanks to you.",
    "Meeting minutes: the team discussed the upcoming product launch timeline.",
    "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id;",
]

REFS = ["1024", "2048", "INV-88", "INV-105", "CN-31", "DN-17", "4521",
        "7012", "INV-214", "BIL-77", "5309", "1180", "PO-55", "CN-42",
        "9021", "INV-33"]

# ---------------------------------------------------------------------------
# Style engine — ONE generic implementation
# ---------------------------------------------------------------------------
# Each style is (name, kind): kind="preserve" keeps semantics identical so the
# template's expectations hold; kind="degrade" intentionally removes/ corrupts
# information and the row becomes a defensive example whose expected behavior
# comes from the live production interpreter (honest degradation).

STYLE_LIST: List[Tuple[str, str]] = [
    ("plain", "preserve"),
    ("currency_rs", "preserve"),
    ("currency_inr", "preserve"),
    ("currency_rupees", "preserve"),
    ("punctuation_loose", "preserve"),
    ("reorder_tail", "preserve"),
    ("business", "preserve"),
    ("short_desc", "preserve"),
    ("journal_style", "preserve"),
    ("api_like", "preserve"),
    ("email_line", "preserve"),
    ("narration", "preserve"),
    ("abbreviated", "preserve"),
    ("date_prefix", "preserve"),
    ("ack_suffix", "preserve"),
    ("clutter", "preserve"),
    ("number_words", "degrade"),   # amounts as words -> MISSING_AMOUNT honest
    ("ocr_noisy", "degrade"),      # digit/char corruption -> honest degradation
]

DATE_TOKENS = ["01/06", "12/04", "25/03", "07/07", "18/11", "29/01"]
ACK_TOKENS = [" — ack sent.", " (accounts section)", " [verified copy]",
              " — entry recorded.", " (attached scan)"]

_CURRENCY_MAP = {
    "currency_rs": ("Rs. ", lambda v: f"Rs. {v:,}"),
    "currency_inr": ("INR ", lambda v: f"INR {v:,}"),
    # word-BEFORE-digits order: the only form the production amount detector
    # recognises for rupees-word notation
    "currency_rupees": (" rupees", lambda v: f"rupees {v:,}"),
}


def apply_style(text: str, style: str, rng: random.Random,
                amount: int, ref: str) -> str:
    """Apply one style transform. ONE generic implementation."""
    if style == "plain":
        return text
    if style in _CURRENCY_MAP:
        # rewrite the first ₹-amount to an alternate currency notation
        return re.sub(r"₹\s*[\d,]+", _CURRENCY_MAP[style][1](amount), text, count=1)
    if style == "punctuation_loose":
        return text.replace(".", "").replace(",", "")
    if style == "reorder_tail":
        m = re.search(r"([^.]*\.)$", text)
        if m and ". " in text:
            head, _, tail = text.rpartition(". ")
            if len(tail) > 8:
                return tail.rstrip(".") + ", " + head.lower() + "."
        return text
    if style == "business":
        return text.replace("Received ₹", "We have received ₹").replace(
            "Paid ₹", "Please note we paid ₹")
    if style == "short_desc":
        t = text.replace("worth ", "").replace(" for ", " ")
        return t if len(t) < len(text) else text
    if style == "journal_style":
        return text  # tagged by template; identity here, pattern in template
    if style == "api_like":
        return f'{{"event":"txn","note":"{text}"}}'
    if style == "email_line":
        return f"FYI — {text}"
    if style == "narration":
        return text.upper().replace("₹", "RS ").replace(".", "")
    if style == "abbreviated":
        t = text.replace("Received ", "RCVD ").replace("Paid ", "PAID ")
        t = t.replace(" by cheque", " cheque").replace(" by NEFT", " NEFT")
        t = t.replace(" via UPI", " UPI").replace(" in cash", " CASH")
        return t
    if style == "date_prefix":
        return f"On {rng.choice(DATE_TOKENS)}: {text}"
    if style == "ack_suffix":
        return text + rng.choice(ACK_TOKENS)
    if style == "clutter":
        return text + " " + rng.choice(DISTRACTORS)
    if style == "number_words":
        words = NUMBER_WORDS.get(amount)
        if not words:
            return text
        return re.sub(r"₹\s*[\d,]+", words, text, count=1)
    if style == "ocr_noisy":
        t = text
        t = t.replace("R", "R", 1)
        # deterministic single corruption: one digit -> lookalike letter
        m = re.search(r"\d", t)
        if m:
            i = m.start()
            swap = {"0": "O", "1": "l", "5": "S", "8": "B"}.get(t[i], "")
            if swap:
                t = t[:i] + swap + t[i + 1:]
        return t
    return text


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------
# Every supported-family template below was PROVEN against the live kernel in
# pre-flight (see PRE_FLIGHT_PROBES); pre-flight failure demotes the family.

Template = Dict[str, Any]

def T(tid: str, family: str, input_type: str, patterns: List[str],
      tx: str, pm: str = "channel", status: str = "extract",
      defensive: Optional[str] = None, authority: str = "ACCOUNTING_KERNEL",
      knowledge: Optional[str] = None) -> Template:
    return {
        "id": tid, "family": family, "input_type": input_type,
        "patterns": patterns, "tx": tx, "pm": pm, "status": status,
        "defensive": defensive, "authority": authority, "knowledge": knowledge,
    }


# -- supported families (kernel pre-flight required) -------------------------

SUPPORTED_TEMPLATES: List[Template] = [
    # PURCHASE
    T("pur_credit", "PURCHASE", "journal_description",
      ["Purchased goods worth ₹{amt} from {party} on credit.",
       "Bought {good} worth ₹{amt} from {party} on credit.",
       "Purchased {good} ₹{amt} credit from {party}."], "PURCHASE", "CREDIT"),
    T("pur_cash", "PURCHASE", "journal_description",
      ["Purchased goods worth ₹{amt} from {party} for cash.",
       "Bought {good} from {party} for ₹{amt} cash.",
       "Purchased {good} worth ₹{amt} in cash from {party}."], "PURCHASE", "CASH"),
    T("pur_chq", "PURCHASE", "bill",
      ["Paid {party} ₹{amt} by cheque for goods purchased.",
       "Bought {good} ₹{amt} from {party}, paid by cheque.",
       "Purchased {good} from {party} ₹{amt}; cheque no. {ref} issued."],
      "PURCHASE", "CHEQUE"),
    T("pur_upi", "PURCHASE", "payment_confirmation",
      ["Paid {party} ₹{amt} via UPI for goods.",
       "Bought {good} ₹{amt} from {party} by UPI."], "PURCHASE", "UPI"),
    T("pur_neft", "PURCHASE", "bank_narration",
      ["Paid {party} ₹{amt} by NEFT for goods purchased.",
       "Bought {good} from {party} — ₹{amt} NEFT."], "PURCHASE", "BANK"),
    T("pur_asset", "PURCHASE", "invoice",
      ["Paid ₹{amt} to {party} for {asset} by cheque.",
       "Purchased {asset} worth ₹{amt} from {party} on credit.",
       "Bought {asset} for ₹{amt} cash from {party}."], "PURCHASE", "channel"),
    # SALE
    T("sale_cash", "SALE", "journal_description",
      ["Sold goods to {party} for ₹{amt} cash.",
       "Sold {good} ₹{amt} for cash to {party}."], "SALE", "CASH"),
    T("sale_credit", "SALE", "invoice",
      ["Sold goods to {party} for ₹{amt} on credit.",
       "Sold {good} worth ₹{amt} to {party} on credit.",
       "Invoice {ref}: goods sold to {party}, ₹{amt} credit."], "SALE", "CREDIT"),
    T("sale_chq", "SALE", "journal_description",
      ["Sold goods to {party} for ₹{amt}, received cheque.",
       "Sold {good} to {party} ₹{amt} by cheque."], "SALE", "CHEQUE"),
    T("sale_upi", "SALE", "payment_confirmation",
      ["Sold goods to {party} ₹{amt}, payment received via UPI.",
       "Sold {good} to {party} for ₹{amt} by UPI."], "SALE", "UPI"),
    T("sale_neft", "SALE", "bank_narration",
      ["Sold goods to {party} ₹{amt} NEFT received.",
       "Sold {good} to {party} — ₹{amt} NEFT credit."], "SALE", "BANK"),
    T("sale_asset", "SALE", "journal_description",
      ["Sold old furniture by cheque ₹{amt} to {party}.",
       "Sold old machinery for ₹{amt} cash."], "SALE", "channel"),
    # RECEIPT (kernel wording family: received from)
    T("rec_cash", "RECEIPT", "journal_description",
      ["Received ₹{amt} cash from {party}.",
       "Received ₹{amt} in cash from {party}."], "RECEIPT", "CASH"),
    T("rec_chq", "RECEIPT", "journal_description",
      ["Received ₹{amt} from {party} by cheque.",
       "Received cheque ₹{amt} from {party}."], "RECEIPT", "CHEQUE"),
    T("rec_upi", "RECEIPT", "bank_narration",
      ["Received ₹{amt} from {party} via UPI.",
       "UPI credit ₹{amt} from {party}."], "RECEIPT", "UPI"),
    T("rec_neft", "RECEIPT", "bank_narration",
      ["Received ₹{amt} from {party} by NEFT.",
       "NEFT credit ₹{amt} — {party}."], "RECEIPT", "BANK"),
    T("rec_plain", "RECEIPT", "email_line",
      ["Received ₹{amt} from {party}.",
       "Received ₹{amt} from {party} against our bill {ref}."],
      "RECEIPT", "UNKNOWN"),
    # PAYMENT (kernel wording family: paid to)
    T("pay_cash", "PAYMENT", "journal_description",
      ["Paid ₹{amt} to {party} in cash.",
       "Paid {party} ₹{amt} cash."], "PAYMENT", "CASH"),
    T("pay_chq", "PAYMENT", "journal_description",
      ["Paid ₹{amt} to {party} by cheque.",
       "Cheque issued to {party} ₹{amt}."], "PAYMENT", "CHEQUE"),
    T("pay_upi", "PAYMENT", "bank_narration",
      ["Paid ₹{amt} to {party} via UPI.",
       "UPI debit ₹{amt} — paid {party}."], "PAYMENT", "UPI"),
    T("pay_neft", "PAYMENT", "bank_narration",
      ["Paid ₹{amt} to {party} by NEFT.",
       "NEFT debit ₹{amt} — {party}."], "PAYMENT", "BANK"),
    # EXPENSE (kernel EXPENSE_PAID wording family)
    T("exp_word", "EXPENSE", "journal_description",
      ["Paid {expense} ₹{amt} in cash.",
       "Paid ₹{amt} for {expense} by cheque.",
       "{expense_cap} paid ₹{amt} by NEFT.",
       "Paid {expense} ₹{amt} via UPI."], "EXPENSE", "channel"),
    # SETTLEMENT
    T("stl_full", "SETTLEMENT", "journal_description",
      ["Settled {party}'s account in full ₹{amt} by cheque.",
       "Paid {party} ₹{amt} in full settlement, cash.",
       "Settled {party}'s account fully ₹{amt} via UPI."], "SETTLEMENT", "channel"),
    T("stl_full_bank", "SETTLEMENT", "bank_narration",
      ["Full settlement paid to {party} ₹{amt} by NEFT.",
       "Settled {party} ₹{amt} by bank transfer."], "SETTLEMENT", "BANK"),
    # RETURN_OUT (goods returned to supplier)
    T("ret_out", "RETURN_OUT", "debit_note",
      ["Returned goods worth ₹{amt} to {party}.",
       "Returned defective goods ₹{amt} to {party}.",
       "Debit note {ref}: goods returned to {party} ₹{amt}."], "RETURN_OUT", "UNKNOWN"),
    # CAPITAL / DRAWING
    T("cap_in", "CAPITAL_CONTRIBUTION", "journal_description",
      ["Owner introduced capital of ₹{amt} in cash.",
       "Introduced additional capital ₹{amt} by cheque.",
       "Proprietor brought in ₹{amt} as capital, NEFT."], "CAPITAL", "channel"),
    T("drw_cash", "DRAWING", "journal_description",
      ["Withdrew ₹{amt} cash for personal use.",
       "Owner withdrew ₹{amt} for personal expenses."], "DRAWING", "CASH"),
    T("drw_bank", "DRAWING", "bank_narration",
      ["ATM withdrawal ₹{amt} for personal use.",
       "Withdrew ₹{amt} from bank for private expenses."], "DRAWING", "BANK"),
    # REFUND (kernel wording: refund of ... given to)
    T("refund_out", "REFUND", "credit_note",
      ["Refund of ₹{amt} given to {party} by cheque.",
       "Refund of ₹{amt} given to {party} in cash."], "PAYMENT", "channel"),
    # CREDIT NOTE (kernel wording: issued a credit note of)
    T("cn_issued", "CREDIT_NOTE", "credit_note",
      ["Issued a credit note of ₹{amt} to {party}.",
       "Credit note {ref} issued to {party} ₹{amt}."], "RETURN_OUT", "CREDIT"),
    T("cn_received", "CREDIT_NOTE", "credit_note",
      ["Received a credit note ₹{amt} from {party}.",
       "Credit note of ₹{amt} received from {party}."], "RETURN_OUT", "CREDIT"),
    # BANK FEE
    T("bank_fee", "BANK_FEE", "bank_narration",
      ["Paid bank charges ₹{amt}.",
       "Paid bank charges ₹{amt} by debit card.",
       "Paid commission charges for bank ₹{amt} cash."], "EXPENSE", "channel",
      knowledge="bank statement"),
    # BAD DEBT
    T("bad_debt_wo", "BAD_DEBT", "journal_description",
      ["Wrote off ₹{amt} owed by {party} as bad debts.",
       "Bad debts written off ₹{amt} — {party}."], "EXPENSE", "UNKNOWN"),
    T("bad_debt_rec", "BAD_DEBT", "journal_description",
      ["Bad debts recovered from {party} ₹{amt} in cash.",
       "Bad debts recovered ₹{amt} from {party} by cheque."], "RECEIPT", "channel"),
    # GST (basic inclusive treatment only)
    T("gst_purchase", "TAX_GST", "invoice",
      ["Purchased goods worth ₹{amt} plus 18% GST from {party} on credit.",
       "Bought {good} ₹{amt} plus 18% GST from {party}, paid cash."],
      "PURCHASE", "channel"),
    T("gst_sale", "TAX_GST", "invoice",
      ["Sold goods to {party} for ₹{amt} plus 18% GST on credit.",
       "Sold {good} ₹{amt} plus 18% GST to {party}, cash."], "SALE", "channel"),
    # TRANSFER (cash ↔ bank; deposit/withdrawal wordings verified in pre-flight)
    T("transfer_dep", "TRANSFER", "bank_narration",
      ["Deposited ₹{amt} cash into bank.",
       "Paid ₹{amt} cash into bank."], "RECEIPT", "channel",
      defensive="narration_gap"),
    # additional supported sub-families (expands genuine semantic diversity)
    T("pur_multi_unit", "PURCHASE", "invoice",
      ["Purchased 10 cartons of {good} from {party} for ₹{amt} cash.",
       "Bought 5 units of {good} ₹{amt} from {party} on credit."],
      "PURCHASE", "channel"),
    T("sale_invoice_ref", "SALE", "invoice",
      ["Sales invoice {ref}: sold {good} to {party} for ₹{amt} cash.",
       "Invoice {ref} — {good} sold to {party} ₹{amt} on credit."],
      "SALE", "channel"),
    T("rec_against_bill", "RECEIPT", "email_or_text_thread",
      ["Received ₹{amt} cash from {party} against our bill.",
       "Received ₹{amt} from {party} against invoice {ref}."],
      "RECEIPT", "CASH"),
    T("pay_against_bill", "PAYMENT", "payment_confirmation",
      ["Paid {party} ₹{amt} by cheque against their bill.",
       "Payment of ₹{amt} made to {party} by UPI against pending bills."],
      "PAYMENT", "channel"),
    T("exp_webhook", "EXPENSE", "api_payload_webhook",
      ["expense.paid webhook: ₹{amt} for {expense} via UPI.",
       "{{\"event\":\"expense\",\"amount\":{amt},\"head\":\"{expense}\"}} NEFT debit ₹{amt}."],
      "EXPENSE", "channel"),
    T("stl_discount", "SETTLEMENT", "journal_description",
      ["Settled {party}'s account in full; allowed ₹99 cash discount."],
      "SETTLEMENT", "channel"),
    T("ret_in_customer", "RETURN_IN", "credit_note",
      ["{party} returned defective goods worth ₹{amt}.",
       "Goods returned by {party} ₹{amt} — credit note {ref} issued."],
      "RETURN_OUT", "CREDIT"),
    T("cap_cheque", "CAPITAL_CONTRIBUTION", "bank_narration",
      ["Capital of ₹{amt} brought in by cheque.",
       "Owner deposited ₹{amt} into the firm's bank account as capital."],
      "CAPITAL", "CHEQUE"),
    T("drw_goods", "DRAWING", "journal_description",
      ["Goods worth ₹{amt} withdrawn by proprietor for personal use."],
      "DRAWING", "UNKNOWN"),
    T("gst_inclusive_sale", "TAX_GST", "invoice",
      ["Sold goods to {party} for ₹{amt} inclusive of 18% GST, cash."],
      "SALE", "CASH"),
    T("bad_debt_partial", "BAD_DEBT", "journal_description",
      ["₹{amt} due from {party} written off as irrecoverable.",
       "Part of {party}'s debt, ₹{amt}, written off as bad."],
      "EXPENSE", "UNKNOWN"),
    T("bank_fee_upi", "BANK_FEE", "bank_narration",
      ["UPI handling charges ₹{amt} debited by bank.",
       "Bank commission on draft ₹{amt} collected."],
      "EXPENSE", "UNKNOWN"),
    T("adv_asset_words", "PURCHASE", "short_transaction_desc",
      ["Paid for {asset} ₹{amt} by NEFT.",
       "Gave ₹{amt} cash for {asset} to {party}."],
      "PURCHASE", "channel", defensive="adversarial_wording"),
]

# -- defensive / abstention templates (honest degradation) -------------------

DEFENSIVE_TEMPLATES: List[Template] = [
    # unsupported financial concepts / operations (proven kernel refusals)
    T("unsup_depreciation", "DEPRECIATION", "journal_description",
      ["Depreciation on machinery ₹{amt}.",
       "Charge depreciation on furniture ₹{amt} for the year.",
       "Depreciation ₹{amt} on computers provided."], "UNKNOWN", "UNKNOWN",
      status="unsupported", defensive="unsupported_accounting_operations",
      authority="ACCOUNTING_KERNEL"),
    T("unsup_fx", "FX_EVENT", "bank_narration",
      ["Received USD 2,000 from a US client.",
       "Payment received $ 1,500 from overseas buyer.",
       "FX settlement received EUR 900 from importer."], "UNKNOWN", "UNKNOWN",
      status="unsupported", defensive="unsupported_financial_concepts",
      authority="ACCOUNTING_KERNEL"),
    T("unsup_accrual", "ACCRUAL", "journal_description",
      ["Accrued salary for March ₹{amt}.",
       "Provision for doubtful debts ₹{amt} created.",
       "Outstanding electricity expense ₹{amt} accrued."], "UNKNOWN", "UNKNOWN",
      status="unsupported", defensive="unsupported_accounting_operations",
      authority="ACCOUNTING_KERNEL", knowledge="IAS 37 provisions record"),
    T("unsup_payroll_concept", "PAYROLL", "email_or_text_thread",
      ["Processed payroll for March with PF and ESI deductions ₹{amt}.",
       "Payroll run completed: gross ₹{amt}, TDS deducted.",
       "Gratuity provision computed for the year ₹{amt}."], "UNKNOWN", "UNKNOWN",
      status="unsupported", defensive="unsupported_financial_concepts",
      authority="ACCOUNTING_KERNEL"),
    T("unsup_adjustment", "ADJUSTMENT_MISC", "email_or_text_thread",
      ["Prepaid insurance for next year ₹{amt} adjusted.",
       "Deferred revenue for the unearned portion ₹{amt} recognised.",
       "Reclassification between reserve accounts ₹{amt} made."], "UNKNOWN",
      "UNKNOWN", status="unsupported",
      defensive="unsupported_accounting_operations", authority="ACCOUNTING_KERNEL"),
    # missing information (model invention traps)
    T("trap_no_mode", "PAYMENT", "short_transaction_desc",
      ["Paid {party}.", "Sent amount to {party}.",
       "Paid {party} for goods."], "PAYMENT", "UNKNOWN",
      status="review", defensive="model_invention_traps"),
    T("trap_no_amount", "UNKNOWN", "short_transaction_desc",
      ["Paid cash to {party}.", "Received cash from {party}.",
       "Sold goods to {party}."], "UNKNOWN", "UNKNOWN",
      status="review", defensive="model_invention_traps"),
    T("trap_ref_only", "RECEIPT", "payment_confirmation",
      ["Payment received against invoice {ref}.",
       "Credit received, reference {ref}."], "RECEIPT", "UNKNOWN",
      status="review", defensive="model_invention_traps"),
    # ambiguous information
    T("amb_pronoun", "RECEIPT", "email_or_text_thread",
      ["He paid me ₹{amt} in cash after the meeting.",
       "She sent ₹{amt} by UPI yesterday."], "RECEIPT", "channel",
      status="review", defensive="ambiguous_information"),
    T("amb_two_parties", "PAYMENT", "email_or_text_thread",
      ["Paid ₹{amt} to {party} or maybe it was {party2}, records differ.",
       "Amount ₹{amt} settled with {party} and {party2} together."], "PAYMENT",
      "channel", status="review", defensive="ambiguous_information"),
    # contradictory information
    T("confl_pm", "PAYMENT", "email_or_text_thread",
      ["Paid ₹{amt} cash to {party} but the bill says NEFT.",
       "Sent ₹{amt} via UPI though the voucher shows cheque."], "PAYMENT",
      "UNKNOWN", status="conflict", defensive="contradictory_information"),
    T("confl_amount", "SALE", "invoice",
      ["Invoice {ref}: ₹{amt} billed, ₹{amt2} mentioned in the summary.",
       "Sold goods ₹{amt}; receipt however shows ₹{amt2}."], "SALE", "channel",
      status="conflict", defensive="contradictory_information"),
    # multiple events in one sentence
    T("multi_events", "PAYMENT", "journal_description",
      ["Paid ₹{amt} to {party} and received ₹{amt2} from {party2}.",
       "Sold goods ₹{amt} to {party} and bought goods ₹{amt2} from {party2}."],
      "UNKNOWN", "channel", status="review",
      defensive="multiple_events_one_sentence"),
    # duplicate documents
    T("dup_doc", "SALE", "invoice",
      ["Invoice {ref} ₹{amt} — duplicate copy received again.",
       "Same invoice {ref} ₹{amt} appears twice in the feed."], "SALE",
      "channel", status="review", defensive="duplicate_documents"),
    # out of domain
    T("ood_text", "UNKNOWN", "email_or_text_thread",
      OUT_OF_DOMAIN, "UNKNOWN", "UNKNOWN", status="review",
      defensive="out_of_domain", authority="none"),
    # temporal mismatch
    T("temporal", "SETTLEMENT", "email_or_text_thread",
      ["Received ₹{amt} from {party} today against last year's bill.",
       "Paid ₹{amt} to {party} now for the invoice due next quarter."],
      "PAYMENT", "channel", status="review", defensive="temporal_mismatch"),
    # entity name variants
    T("entity_variants", "RECEIPT", "bank_narration",
      ["Received ₹{amt} from M/s Sharma Traders.",
       "NEFT CR ₹{amt} SHARMA TRAD PVT LTD.",
       "Received ₹{amt} from Sharma Trading Co."], "RECEIPT", "BANK",
      status="review", defensive="entity_name_variants"),
    # unverifiable claims (BLOCKED class): invented references demanded
    T("unverifiable", "PURCHASE", "invoice",
      ["Invoice {ref} confirms purchase from {party} ₹{amt} — verify GSTIN.",
       "Bill {ref} claims payment of ₹{amt} made to {party} last week."],
      "PURCHASE", "channel", status="blocked", defensive="unverifiable_claims"),
    # adversarial wording (misleading lexical cases)
    T("adv_bank_word", "EXPENSE", "short_transaction_desc",
      ["Paid the bank commission ₹{amt} in cash.",
       "Paid bank commission ₹{amt} cash."], "EXPENSE", "CASH",
      defensive="adversarial_wording"),
    # messy language (formal ↔ colloquial span, defensive class 'messy_language')
    T("messy_business", "SALE", "email_or_text_thread",
      ["So basically we sold the whole batch of {good} to {party} for ₹{amt} cash, done deal.",
       "Just confirming — {party} picked up {good} worth ₹{amt}, paid by cheque."],
      "SALE", "channel", defensive="messy_language"),
    T("messy_narration", "RECEIPT", "bank_narration",
      ["NEFT-ICIC-R-0000 {party} ₹{amt} bill realization",
       "IMPS P2A {party} ₹{amt} towards inv {ref}"],
      "RECEIPT", "BANK", defensive="messy_language"),
    # cross-document relationship (metadata-only, no invented links)
    T("xdoc_po_bill", "PURCHASE", "invoice",
      ["As per purchase order PO-{ref}, received bill from {party} ₹{amt} credit.",
       "Bill received from {party} ₹{amt} against our PO-{ref}."],
      "PURCHASE", "CREDIT", defensive="cross_document_relationship"),
    T("xdoc_settle_ref", "SETTLEMENT", "payment_confirmation",
      ["Settled {party}'s bill {ref} in full ₹{amt} by cheque.",
       "Payment ₹{amt} NEFT — full and final settlement of {party}'s invoice {ref}."],
      "SETTLEMENT", "BANK", defensive="cross_document_relationship"),
    T("adv_office_furniture", "PURCHASE", "short_transaction_desc",
      ["Paid for office furniture ₹{amt} by cheque.",
       "Paid ₹{amt} for office furniture cash."], "PURCHASE", "channel",
      defensive="adversarial_wording"),
]

# ---------------------------------------------------------------------------
# Kernel pre-flight: prove every supported family before it can generate
# ---------------------------------------------------------------------------

def _probe_text(t: Template) -> str:
    """Materialise one deterministic probe sentence for a template."""
    text = t["patterns"][0]
    return (text.replace("{amt}", "5,000").replace("{amt2}", "6,500")
                .replace("{party}", "Sharma Traders")
                .replace("{party2}", "Gupta and Sons")
                .replace("{good}", "stationery")
                .replace("{asset}", "office furniture")
                .replace("{expense}", "rent").replace("{expense_cap}", "Rent paid")
                .replace("{ref}", "INV-88"))


def run_kernel_preflight() -> Dict[str, Any]:
    """Probe EVERY pattern of every supported template against the live kernel.

    Per-pattern verdict: the kernel must resolve a deterministic journal
    (status VERIFIED) for that exact wording. Templates keep only their
    verified patterns; a template with NO verified pattern is demoted to a
    defensive REVIEW_REQUIRED family (its semantics remain plain, but the
    kernel wording coverage is absent — the 'model understands != kernel
    executes' boundary, Phase G defensive class
    unsupported_accounting_operations). Nothing is silently trained against
    a refused wording.
    """
    from backend.maths import fyjc_bk_reasoning as bk
    results: Dict[str, Any] = {}
    for t in SUPPORTED_TEMPLATES:
        verdicts: List[str] = []
        kept: List[str] = []
        for pattern in t["patterns"]:
            probe = _probe_text({"patterns": [pattern]})
            try:
                r = bk.reason_bk_question(probe)
                ok = r.get("status") == "VERIFIED" and bool(r.get("debit_lines"))
                verdict = "VERIFIED" if ok else f"REFUSED:{r.get('status')}"
            except Exception as exc:
                verdict = f"ERROR:{type(exc).__name__}"
            verdicts.append(verdict)
            if ok:
                kept.append(pattern)
        if kept:
            t["patterns"] = kept
        results[t["id"]] = {
            "patterns_kept": len(kept), "patterns_total": len(t["patterns"]),
            "demoted": not kept, "verdicts": verdicts,
        }
    return {"results": results,
            "kernel": "backend.maths.fyjc_bk_reasoning.reason_bk_question"}


# ---------------------------------------------------------------------------
# Slot rendering
# ---------------------------------------------------------------------------

def render_slots(rng: random.Random) -> Dict[str, str]:
    party = rng.choice(FIRST_NAMES + FIRM_NAMES)
    party2 = rng.choice([n for n in FIRST_NAMES + FIRM_NAMES if n != party])
    amt = rng.choice(AMOUNTS)
    amt2 = rng.choice(AMOUNTS)
    ref = rng.choice(REFS)
    good = rng.choice(GOODS)
    asset = rng.choice(ASSETS)
    expense, expense_cap = rng.choice(EXPENSES)
    return {"party": party, "party2": party2, "amt": f"{amt:,}",
            "amt2": f"{amt2:,}", "ref": ref, "good": good, "asset": asset,
            "expense": expense, "expense_cap": expense_cap,
            "_amt_int": amt, "_amt2_int": amt2}


def resolve_pm(template: Template, text: str) -> str:
    """Ground-truth payment method for a rendered row.

    The production detector (_detect_payment_method, the exact vocabulary the
    grounding gate accepts) runs on the FINAL rendered text — the label must
    describe the text as rendered, never the template as designed.

    Rule (one generic rule, no special cases):
    - template pm == "UNKNOWN"  -> stays UNKNOWN: by-design unconfirmed rows
      (conflict/ambiguity/trap families) must not adopt a channel word that
      the sentence itself disputes;
    - otherwise                 -> the detector's result on the rendered text
      (UNKNOWN when a style wiped the channel word — the honest degradation,
      surfaced as MISSING_PAYMENT_MODE rather than a mislabel).
    """
    detected, _, _ = _detect_payment_method(text)
    if template["pm"] == "UNKNOWN":
        return "UNKNOWN"
    return detected


def render_text(template: Template, style: str, rng: random.Random,
                slots: Dict[str, str]) -> str:
    text = rng.choice(template["patterns"])
    for k, v in slots.items():
        if not k.startswith("_"):
            text = text.replace("{" + k + "}", v)
    return apply_style(text, style, rng, slots["_amt_int"], slots["ref"])


# ---------------------------------------------------------------------------
# Label assembly — honest, contract-exact 18-field targets
# ---------------------------------------------------------------------------

TX_REASON = {
    "PURCHASE": "goods/assets acquired for the business",
    "SALE": "goods/assets sold by the business",
    "RECEIPT": "money received by the business",
    "PAYMENT": "money paid out by the business",
    "EXPENSE": "business expense incurred and paid",
    "SETTLEMENT": "account settled with the party",
    "RETURN_OUT": "goods returned to the supplier",
    "CAPITAL": "capital brought into the business",
    "DRAWING": "cash withdrawn for personal use",
    "UNKNOWN": "transaction type not determinable from text",
}

PM_REASON = {
    "CASH": "cash settlement mentioned in text",
    "CHEQUE": "cheque settlement mentioned in text",
    "BANK": "bank/NEFT/RTGS settlement mentioned in text",
    "UPI": "UPI settlement mentioned in text",
    "CREDIT": "credit terms mentioned in text",
    "UNKNOWN": "no payment mode stated in text",
}

# Classification evidence per event family: wording that must appear in the
# final text for a clear-row transaction_type label to be GROUNDED.
FAMILY_WORDINGS: Dict[str, Tuple[str, ...]] = {
    "PURCHASE": ("purchased", "bought", "received bill", "received bill from", "bill received", "received bill"),
    "SALE": ("sold", "sale"),
    "RECEIPT": ("received", "rcvd", "realization"),
    "PAYMENT": ("paid", "payment"),
    "EXPENSE": ("paid", "expenses", "expense.paid", "expense"),
    "SETTLEMENT": ("settled", "settlement"),
    "RETURN_OUT": ("returned", "return"),
    "RETURN_IN": ("returned", "return"),
    "CAPITAL_CONTRIBUTION": ("capital",),
    "DRAWING": ("withdrew", "withdrawal", "withdrawn"),
    "REFUND": ("refund",),
    "CREDIT_NOTE": ("credit note",),
    "BANK_FEE": ("bank charges", "bank commission", "commission charges", "handling charges", "commission on draft"),
    "BAD_DEBT": ("bad debts", "written off", "written off as irrecoverable", "written off as bad"),
    "TAX_GST": ("gst",),
    "TRANSFER": ("deposited", "withdrawal"),
    "DEPRECIATION": ("depreciation",),
    "FX_EVENT": ("usd", "eur", "overseas"),
    "ACCRUAL": ("accrued", "provision", "outstanding"),
    "PAYROLL": ("payroll", "gratuity"),
    "ADJUSTMENT_MISC": ("prepaid", "deferred", "reclassification"),
    "UNKNOWN": (),
}


def _fc(field: str, value: Any, conf: str, grounded: bool,
        source: str, reasoning: str) -> Dict[str, Any]:
    return {"field_name": field, "value": str(value), "confidence": conf,
            "grounding": "GROUNDED" if grounded else "UNRESOLVED",
            "source_text": source, "reasoning": reasoning}


def _production_facts(text: str, pm_enum: str) -> Dict[str, Any]:
    """One production-source re-derivation of every extracted fact.

    Uses the SAME production functions the runtime uses on arbitrary text:
    _extract_parties / _extract_amounts / _detect_payment_method. The label
    must describe the text AS RENDERED — never the template as designed.
    """
    return {
        "parties": _extract_parties(text),
        "amounts": _extract_amounts(text),
        "tx": _detect_transaction_type(text),
        "pm": pm_enum,
    }


def build_output(template: Template, text: str, style: str,
                 slots: Dict[str, str], pm_enum: str,
                 specialist: FYJCAISpecialist) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Assemble the 18-field target for one candidate.

    Clear (preserve-style) rows: kernel-wording family is the classification
    ground truth; specialist supplies structural extraction; the two must
    agree with template expectations or the row is flagged for rejection.
    Defensive/degraded rows: the target is the HONEST degraded reading
    (UNKNOWN where the text no longer determines the field), never an
    invented value.
    """
    base = specialist.parse(text)
    degraded = style in ("number_words", "ocr_noisy")
    is_defensive_family = template["status"] != "extract"
    if template["status"] == "review" and template.get("demoted"):
        is_defensive_family = True  # kernel-wording gap -> honest review row
    flags: List[str] = []
    safety: List[str] = ["NONE"]
    scope: List[str] = ["SINGLE_TRANSACTION"]

    # ONE production-source re-derivation of every extracted fact (DRY): the
    # label must describe the text AS RENDERED, never the template as designed
    prod = _production_facts(text, pm_enum)
    amounts = prod["amounts"]
    parties = prod["parties"]
    tx_detected, tx_source, _ = prod["tx"]
    # integer-string corpus convention BEFORE confidence records are built
    for a in amounts:
        v = a.get("value")
        if isinstance(v, str) and v.endswith(".0"):
            a["value"] = v[:-2]

    # Party repair: the production party regex greedily absorbs trailing
    # settlement/mode words ('Suresh in cash', 'Anil by NEFT') — the exact
    # Phase-22-audited defect class. Deterministic repair: split on the
    # settlement word, keep the name token(s). Applied uniformly.
    repaired: List[str] = []
    for p in parties:
        head = re.split(
            r"\s+(?:in|by|via|through|for|against|but|maybe|or)\s+",
            p, flags=re.IGNORECASE)[0].strip(" ,.")
        if head and len(head.split()) <= 4:
            repaired.append(head)
    parties = repaired

    tx_enum = template["tx"]
    tx_evidence = ""
    if degraded or is_defensive_family:
        # honest degradation: derive from what the production interpreter sees
        tx_enum = base.get("transaction_type_enum", "UNKNOWN")
        if template["status"] == "unsupported":
            tx_enum = "UNKNOWN"
            flags.append("MULTIPLE_INTERPRETATIONS")
            scope.append("EDGE_CASE")
        tx_source = ""

    if template["tx"] in ("UNKNOWN",):
        tx_enum = "UNKNOWN"
    if tx_enum == "UNKNOWN" and "MULTIPLE_INTERPRETATIONS" not in flags:
        flags.append("MULTIPLE_INTERPRETATIONS")
    if not amounts:
        flags.append("MISSING_AMOUNT")
    if not parties:
        flags.append("MISSING_PARTY")
    if pm_enum == "UNKNOWN" and "MULTIPLE_INTERPRETATIONS" not in flags:
        flags.append("MISSING_PAYMENT_MODE")
    # family wording must be present for a clear-row tx label to be GROUNDED
    words = FAMILY_WORDINGS.get(template["family"], ())
    if not degraded and not is_defensive_family and words:
        matched = next((w for w in words if w in text.lower()), None)
        if matched is None:
            flags.append("MULTIPLE_INTERPRETATIONS")
            tx_evidence = ""
        else:
            tx_evidence = matched  # the matched wording IS the text evidence
    elif tx_detected != "UNKNOWN":
        tx_evidence = tx_source
    if not flags:
        flags.append("NONE")

    status = {
        "extract": "REVIEW_REQUIRED",
        "review": "REVIEW_REQUIRED",
        "conflict": "REVIEW_REQUIRED",
        "blocked": "BLOCKED",
        "unsupported": "UNSUPPORTED",
    }[template["status"]]

    if template["status"] == "conflict":
        flags.append("CONFLICTING_INFORMATION")
        scope.append("EDGE_CASE")
    if template["status"] == "unsupported":
        safety = ["UNRESOLVED_FIELDS"]

    fcs = [
        _fc("transaction_type", tx_enum,
            "0.85" if tx_evidence else "0.30", bool(tx_evidence), tx_evidence,
            (f"detected from '{tx_evidence}'" if tx_evidence
             else TX_REASON.get(tx_enum, "no keyword match"))),
    ]
    if parties:
        fcs.append(_fc("parties", parties, "0.85", True, ", ".join(parties),
                       "extracted from party markers"))
    else:
        fcs.append(_fc("parties", [], "0.30", False, "", "no party named in text"))
    if amounts:
        fcs.append(_fc("amounts", amounts, "0.90", True,
                       str([a["value"] for a in amounts]),
                       "amounts present in text"))
    else:
        fcs.append(_fc("amounts", [], "0.30", False, "", "no numeric amount in text"))
    fcs.append(_fc("payment_method", pm_enum, "0.90" if pm_enum != "UNKNOWN" else "0.15",
                   pm_enum != "UNKNOWN", "", PM_REASON[pm_enum]))

    legacy_amb = [f.replace("_", " ").lower() for f in flags if f != "NONE"]
    grounded_all = all(fc["grounding"] == "GROUNDED" for fc in fcs)

    out = {
        "transaction_type": tx_enum.lower() if tx_enum != "UNKNOWN" else "",
        "parties": parties,
        "amounts": [{"value": a["value"], "currency": a.get("currency", "INR")}
                    for a in amounts],
        "payment_method": pm_enum.lower() if pm_enum != "UNKNOWN" else "",
        "references": base.get("references", []),
        "ambiguities": legacy_amb,
        "grounding": {"all_fields_explicitly_grounded": grounded_all,
                      "inferred_fields": [fc["field_name"] for fc in fcs
                                          if fc["grounding"] != "GROUNDED"]},
        "transaction_type_enum": tx_enum,
        "payment_method_enum": pm_enum,
        "ambiguity_flags": flags,
        "field_confidences": fcs,
        "overall_confidence": ("0.85" if grounded_all and tx_enum != "UNKNOWN"
                               else "0.40"),
        "referenced_transaction_index": None,
        "referenced_party": None,
        "referenced_amount": None,
        "safety_flags": safety,
        "scope_flags": scope,
        "suggested_status": status,
    }
    meta_hint = {"degraded": degraded, "defensive": template["defensive"],
                 "template_status": template["status"]}
    return out, meta_hint


# ---------------------------------------------------------------------------
# Metadata (Phase G schema) — strictly separate from the model target
# ---------------------------------------------------------------------------

DEFENSIVE_TO_EXPECTED = {
    "extract": "REVIEW_REQUIRED",      # runtime decides; model never claims
    "review": "REVIEW_REQUIRED",
    "conflict": "REVIEW_REQUIRED",
    "blocked": "BLOCKED",
    "unsupported": "UNSUPPORTED",
}


def build_metadata(template: Template, text: str, style: str,
                   output: Dict[str, Any], meta_hint: Dict[str, Any],
                   leakage_group: str, idx: int) -> Dict[str, Any]:
    pm = output["payment_method_enum"]
    flags = output["ambiguity_flags"]
    return {
        "event_family": template["family"],
        "input_type": template["input_type"],
        "authority_dependency": template["authority"],
        "expected_status": DEFENSIVE_TO_EXPECTED[template["status"]],
        "ambiguity_class": template["defensive"] or "normal",
        "conflict_class": ("contradictory_information"
                           if "CONFLICTING_INFORMATION" in flags else "none"),
        "defensive_class": template["defensive"] or "none",
        "difficulty": derive_difficulty(flags, template["status"],
                                        meta_hint["degraded"]),
        "language_style": style,
        "label_source": ("specialist_honest_degradation"
                         if meta_hint["degraded"]
                         else "template_wording_family_kernel_gap"
                         if template.get("demoted")
                         else "kernel_wording_family"),
        "jurisdiction": "IN",
        "framework": "FYJC/double-entry",
        "knowledge_reference": template["knowledge"],
        "transaction_type": output["transaction_type_enum"],
        "payment_method": pm,
        "has_party": bool(output["parties"]),
        "has_amount": bool(output["amounts"]),
        "has_payment": pm != "UNKNOWN",
        "is_ambiguous": any(f != "NONE" for f in flags),
        "is_contradictory": "CONFLICTING_INFORMATION" in flags,
        "is_unsupported": template["status"] == "unsupported",
        "leakage_group": leakage_group,
        "template_id": template["id"],
        "generation_seed": SEED,
        "split": assign_split(leakage_group),
    }


def derive_difficulty(flags: List[str], status: str, degraded: bool) -> str:
    real = [f for f in flags if f != "NONE"]
    if status in ("unsupported", "blocked") or "CONFLICTING_INFORMATION" in flags:
        return "hard"
    if degraded or len(real) >= 2:
        return "hard"
    if real:
        return "medium"
    return "easy"


def assign_split(leakage_group: str) -> str:
    h = hashlib.md5(leakage_group.encode()).hexdigest()
    return "dev" if int(h[:8], 16) % 100 < 15 else "train"


def event_signature(meta: Dict[str, Any]) -> str:
    return "|".join(str(meta.get(k, "")) for k in
                    ("event_family", "transaction_type", "payment_method",
                     "has_party", "has_amount", "has_payment"))


def leakage_group_for(template_id: str, slots: Dict[str, str]) -> str:
    """Variants of one underlying situation share a leakage group."""
    return f"lg_{template_id}_{slots['party']}_{slots['_amt_int']}"


# ---------------------------------------------------------------------------
# Validation gauntlet (production validators + Phase H checks)
# ---------------------------------------------------------------------------

PARTY_BLEED = re.compile(
    r"\b(cash|bank|upi|neft|cheque|chq|rtgs|imps|inr|rs\.?|rupees|via|but|"
    r"however|also|maybe|against|yesterday|today)\b", re.IGNORECASE)


def party_sanity(output: Dict[str, Any]) -> Optional[str]:
    for p in output.get("parties", []):
        if len(p) > 40 or len(p.split()) > 4:
            return f"party_too_long:{p[:24]}"
        if PARTY_BLEED.search(p):
            return f"party_bleed:{p[:24]}"
    return None


def validate_candidate(record: Dict[str, Any], gate: ExpandedGroundingGate,
                       seen_inputs: set, prior_tokens: List[frozenset],
                       accepted_tokens: List[frozenset],
                       is_clear: bool) -> Tuple[bool, str]:
    rid = record["id"]
    text = record["input"]
    output = record["output"]
    meta = record["metadata"]

    if not text or not text.strip():
        return False, "empty_input"
    if len(text) > 300:
        return False, "input_too_long"

    norm = normalise(text)
    if norm in seen_inputs:
        return False, "duplicate_input"
    tokens = token_set(text)
    for other in prior_tokens:
        if jaccard(tokens, other) >= NEAR_DUP_JACCARD:
            return False, "near_dup_of_prior_corpus"
    for other in accepted_tokens:
        if jaccard(tokens, other) >= NEAR_DUP_JACCARD:
            return False, "near_dup_within_candidate"

    report = validate_structured_interpretation(output, allow_expanded=True)
    if not report.valid:
        return False, f"schema:{[e.issue for e in report.errors][:2]}"

    grounding = gate.ground(output, text)
    if not grounding.safe_for_kernel:
        return False, f"gate:{list(grounding.issues)[:2]}"

    bad_keys = FORBIDDEN_OUTPUT_KEYS & set(output.keys())
    if bad_keys:
        return False, f"forbidden_fields:{sorted(bad_keys)}"
    if output.get("suggested_status") == "VERIFIED" or "VERIFIED" in json.dumps(output):
        return False, "claimed_VERIFIED"

    if output["transaction_type_enum"] not in VALID_TRANSACTION_TYPES:
        return False, "bad_tx_enum"
    if output["payment_method_enum"] not in VALID_PAYMENT_METHODS:
        return False, "bad_pm_enum"
    if not set(output["ambiguity_flags"]) <= VALID_AMBIGUITY_TYPES:
        return False, "bad_ambiguity_enum"
    if not set(output["safety_flags"]) <= VALID_SAFETY_FLAGS:
        return False, "bad_safety_enum"
    if not set(output["scope_flags"]) <= VALID_SCOPE_FLAGS:
        return False, "bad_scope_enum"

    ps = party_sanity(output)
    if ps:
        return False, f"party_sanity:{ps}"

    # a CLEAR input the interpreter cannot classify is rejected (Phase 20 rule)
    if is_clear and output["transaction_type_enum"] == "UNKNOWN" and not meta["is_ambiguous"]:
        return False, "unclassifiable_clear_input"

    # classification evidence must be present in the input
    for fc in output.get("field_confidences", []):
        if fc.get("field_name") == "transaction_type":
            src = (fc.get("source_text") or "").strip()
            if src and not re.search(
                    r"\b" + re.escape(src.lower()) + r"(?:ed|d|es|s|ing)?\b",
                    text.lower()):
                return False, "keyword_substring_mismatch"
            break

    # status consistency: model target never claims VERIFIED; UNSUPPORTED
    # only where metadata says so; BLOCKED only for unverifiable-claims class
    if meta["is_unsupported"] and output["suggested_status"] != "UNSUPPORTED":
        return False, "status_mismatch_unsupported"
    if not meta["is_unsupported"] and output["suggested_status"] == "UNSUPPORTED":
        return False, "status_mismatch_unsupported_flag"
    if meta["defensive_class"] == "unverifiable_claims" and output["suggested_status"] != "BLOCKED":
        return False, "status_mismatch_blocked"

    # abstention integrity: conflict rows must carry the conflict flag
    if meta["defensive_class"] == "contradictory_information" and \
            "CONFLICTING_INFORMATION" not in output["ambiguity_flags"]:
        return False, "missing_conflict_flag"

    return True, "ok"


# ---------------------------------------------------------------------------
# Prior-corpus loading (leakage control)
# ---------------------------------------------------------------------------

def load_prior_tokens() -> Tuple[List[frozenset], set, Dict[str, str]]:
    tokens: List[frozenset] = []
    norms: set = set()
    hashes: Dict[str, str] = {}
    for rel in PRIOR_CORPORA:
        p = _PROJECT_ROOT / rel
        if not p.exists():
            hashes[rel] = "MISSING"
            continue
        hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = row.get("input")
            if isinstance(text, str) and text.strip():
                norms.add(normalise(text))
                tokens.append(token_set(text))
    return tokens, norms, hashes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Phase H dataset generator")
    parser.add_argument("--target", type=int, default=TARGET)
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    rng = random.Random(SEED)
    specialist = FYJCAISpecialist()
    gate = ExpandedGroundingGate()

    pre = run_kernel_preflight()
    demoted = [tid for tid, r in pre["results"].items() if r["demoted"]]
    print(f"pre-flight: {len(pre['results']) - len(demoted)} templates verified,"
          f" {len(demoted)} demoted: {demoted}")
    if args.preflight_only:
        print(json.dumps(pre["results"], indent=2, sort_keys=True))
        return 0

    # demoted templates: kernel wording gap -> honest REVIEW_REQUIRED rows
    demoted_set = {tid for tid, r in pre["results"].items() if r["demoted"]}
    for t in SUPPORTED_TEMPLATES:
        if t["id"] in demoted_set:
            t["status"] = "review"
            t["defensive"] = "unsupported_accounting_operations"
            t["demoted"] = True

    prior_tokens, prior_norms, prior_hashes = load_prior_tokens()
    print(f"leakage baseline: {len(prior_norms)} prior inputs from "
          f"{sum(1 for h in prior_hashes.values() if h != 'MISSING')} corpora")

    phase_g_hashes = {rel: hashlib.sha256((_PROJECT_ROOT / rel).read_bytes()).hexdigest()
                      for rel in PHASE_G_ARTIFACTS}

    # ---- build the pool ------------------------------------------------------
    # Quotas are capability-driven (Phase G ontology + measured authority):
    # supported families ~80%, defensive/abstention ~20% (>=15% guardrail).
    supported_quota = 6350
    defensive_quota = 1650
    all_templates: List[Template] = []
    for t in SUPPORTED_TEMPLATES:
        if t["id"] in demoted_set:
            t = dict(t, status="review", defensive="narration_gap")
        all_templates.append(t)
    all_templates.extend(DEFENSIVE_TEMPLATES)

    # weight supported templates by their family quota
    FAMILY_WEIGHTS = {
        "PURCHASE": 900, "SALE": 900, "RECEIPT": 800, "PAYMENT": 800,
        "EXPENSE": 650, "SETTLEMENT": 450, "RETURN_OUT": 400,
        "CAPITAL_CONTRIBUTION": 300, "DRAWING": 300, "REFUND": 250,
        "CREDIT_NOTE": 250, "BANK_FEE": 250, "DISCOUNT": 100, "BAD_DEBT": 200,
        "TAX_GST": 250, "TRANSFER": 100,
    }
    DEFENSIVE_WEIGHTS = {
        "DEPRECIATION": 150, "FX_EVENT": 200, "ACCRUAL": 150, "PAYROLL": 150,
        "ADJUSTMENT_MISC": 100, "UNKNOWN": 100,
    }

    def weight_of(t: Template) -> int:
        if t["status"] == "extract":
            return FAMILY_WEIGHTS.get(t["family"], 100)
        if t["family"] in DEFENSIVE_WEIGHTS:
            return DEFENSIVE_WEIGHTS[t["family"]]
        return 120  # ambiguity/conflict/trap/ood/etc.

    total_w = sum(weight_of(t) for t in all_templates)
    pool_target = int(args.target * POOL_FACTOR)

    pool: List[Tuple[Template, str, Dict[str, str]]] = []
    for t in all_templates:
        want = max(4, round(pool_target * weight_of(t) / total_w))
        for _ in range(want):
            slots = render_slots(rng)
            style, kind = STYLE_LIST[rng.randrange(len(STYLE_LIST))]
            if t["status"] == "extract" and kind == "degrade":
                # degraded styles on clear templates -> defensive class
                t2 = dict(t, status="review",
                          defensive="ocr_corruption" if style == "ocr_noisy"
                          else "missing_information")
            else:
                t2 = t
            pool.append((t2, style, slots))
    rng.shuffle(pool)
    print(f"pool: {len(pool)} candidates from {len(all_templates)} templates")

    # ---- validate + select ---------------------------------------------------
    seen_inputs = set(prior_norms)
    accepted: List[Dict[str, Any]] = []
    accepted_tokens: List[frozenset] = []
    event_counts: Counter = Counter()
    cell_counts: Counter = Counter()
    rejections: Counter = Counter()
    fam_accepted: Counter = Counter()

    for template, style, slots in pool:
        if len(accepted) >= args.target:
            break
        text = render_text(template, style, rng, slots)
        pm_enum = resolve_pm(template, text)
        output, meta_hint = build_output(template, text, style, slots,
                                         pm_enum, specialist)
        lg = leakage_group_for(template["id"], slots)
        meta = build_metadata(template, text, style, output, meta_hint, lg, 0)
        rid = f"ph_{len(accepted) + 1:05d}_pending"
        record = {"id": rid, "input": text, "output": output, "metadata": meta}
        is_clear = (meta_hint["template_status"] == "extract"
                    and style not in ("number_words", "ocr_noisy"))
        ok, reason = validate_candidate(record, gate, seen_inputs,
                                        prior_tokens, accepted_tokens, is_clear)
        if not ok:
            key = reason.split(":")[0]
            rejections[key] += 1
            continue
        sig = event_signature(meta)
        if event_counts[sig] >= SAME_EVENT_CAP:
            rejections["event_signature_cap"] += 1
            continue
        cell = (meta["event_family"], meta["transaction_type"], meta["payment_method"])
        if cell_counts[cell] >= FAMILY_CELL_CAP:
            rejections["family_cell_cap"] += 1
            continue

        seen_inputs.add(normalise(text))
        accepted_tokens.append(token_set(text))
        event_counts[sig] += 1
        cell_counts[cell] += 1
        fam_accepted[meta["event_family"]] += 1
        record["id"] = f"ph_{len(accepted) + 1:05d}"
        accepted.append(record)

    accepted_n = len(accepted)
    abstention_n = sum(1 for r in accepted
                       if r["metadata"]["expected_status"] != "REVIEW_REQUIRED"
                       or r["metadata"]["defensive_class"] != "none")
    print(f"accepted {accepted_n}/{args.target}; abstention-oriented rows: "
          f"{abstention_n} ({abstention_n * 100 // max(accepted_n, 1)}%)")
    print("rejections:", dict(sorted(rejections.items())))
    if accepted_n < args.target * 0.95:
        print("FAILED to reach target — refusing to lower standards "
              "(fail closed). Inspect rejections above.")
        return 3

    # ---- write dataset -------------------------------------------------------
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=False) for r in accepted]
    payload = "\n".join(lines) + "\n"
    out_path = Path(args.out)
    out_path.write_text(payload, encoding="utf-8")
    out_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def dist(field: str) -> Dict[str, int]:
        c = Counter(str(r["metadata"][field]) for r in accepted)
        return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))

    manifest = {
        "dataset_name": "phase_h_v01_8000",
        "version": "phase-h-v01",
        "row_count": accepted_n,
        "sha256": out_hash,
        "generation_seed": SEED,
        "generator_version": GENERATOR_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "determinism": {"python_hash_seed_pinned": True,
                        "llm_used": False,
                        "note": "deterministic across processes "
                                "(PYTHONHASHSEED=0, random.Random(SEED))"},
        "contract": "exact 18-field CandidateSemanticIR (unchanged)",
        "metadata_schema": "docs/phase_g/dataset_architecture.json",
        "phase_g_artifact_sha256": phase_g_hashes,
        "prior_corpus_sha256": prior_hashes,
        "label_architecture": {
            "clear_rows": "kernel_wording_family pre-flight + specialist "
                          "structural machinery + full production validation",
            "defensive_rows": "specialist honest degradation (fail-closed)",
            "excluded_families": ["LOAN", "INTEREST", "DEPOSIT(refundable)"],
            "exclusion_reason": "Phase G ontology marks these gap=true while "
                                "the live kernel verifies basic loan postings "
                                "— stop condition §20.3; documented in report",
        },
        "pre_flight": pre,
        "distributions": {
            "event_family": dist("event_family"),
            "input_type": dist("input_type"),
            "difficulty": dist("difficulty"),
            "language_style": dist("language_style"),
            "transaction_type": dist("transaction_type"),
            "payment_method": dist("payment_method"),
            "defensive_class": dist("defensive_class"),
            "expected_status": dist("expected_status"),
            "authority_dependency": dist("authority_dependency"),
            "split": dist("split"),
        },
        "rejection_statistics": dict(sorted(rejections.items())),
        "validation": {
            "schema_verifier": "validate_structured_interpretation"
                               "(allow_expanded=True) — all rows pass",
            "grounding_gate": "ExpandedGroundingGate.safe_for_kernel — all rows pass",
            "verified_claims": 0,
            "abstention_share": round(abstention_n / max(accepted_n, 1), 4),
            "near_duplicate_threshold_jaccard": NEAR_DUP_JACCARD,
            "status": "ACCEPTED_CANDIDATE",
        },
    }
    Path(args.manifest).write_text(json.dumps(manifest, indent=2) + "\n",
                                   encoding="utf-8")
    print(f"OK: wrote {accepted_n} rows -> {out_path}")
    print(f"    sha256 {out_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
