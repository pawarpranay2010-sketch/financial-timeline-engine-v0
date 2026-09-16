#!/usr/bin/env python3
"""Phase 20 — Hardcore FYJC candidate dataset generator + validator.

Dataset engineering ONLY. Produces training_data/fyjc_hardcore_1000.jsonl.

Architecture (per PHASE 20 spec §8):

    original 1,000 (read-only)  →  pattern/category extraction (audit)
        ↓
    coverage-gap targeted scenario families (deterministic, seeded)
        ↓
    candidate input text
        ↓
    FYJCAISpecialist.parse()            ← the ONLY interpretation authority
        ↓
    schema_verifier (allow_expanded)    ← production contract validation
        ↓
    ExpandedGroundingGate.ground()      ← production grounding validation
        ↓
    duplicate / near-duplicate / consistency / leakage validators
        ↓
    accepted candidate  →  exactly 1,000 rows + manifest

The LLM (Groq, via the EXISTING backend/gateway/providers/groq_adapter.py)
may only reword input text (--enrich). Reworded candidates re-enter the
identical validation gauntlet. No API key → enrichment is skipped with a
clear configuration message; generation never fails open.

The generator NEVER:
  - writes accounting truth (journal/ledger/status) — output comes only
    from the specialist + validators,
  - modifies any existing file,
  - lowers a validation bar to reach 1,000 (§10: fail closed instead).

Determinism: all randomness flows through random.Random(SEED). External LLM
enrichment is off by default; when enabled it is recorded in the manifest
and results are NOT claimed reproducible (§16).
"""

from __future__ import annotations

import os
import sys

# Determinism pin (§16): the deterministic specialist iterates keyword SETS,
# so Python hash randomisation changes 'first matching keyword' order across
# processes. Pin the hash seed BEFORE importing the runtime; re-exec once.
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

from backend.maths.fyjc_ai_specialist import FYJCAISpecialist  # noqa: E402
from backend.maths.schema_verifier import validate_structured_interpretation  # noqa: E402
from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate  # noqa: E402
from backend.maths.fyjc_contract import (  # noqa: E402
    VALID_TRANSACTION_TYPES,
    VALID_PAYMENT_METHODS,
    VALID_AMBIGUITY_TYPES,
    VALID_SAFETY_FLAGS,
    VALID_SCOPE_FLAGS,
)

SEED = 20260916
TARGET = 1000
POOL_FACTOR = 1.6          # generate ~60% surplus; validators reject the rest
NEAR_DUP_JACCARD = 0.75     # reject candidate with >= this Jaccard vs any accepted/original
SAME_EVENT_CAP = 24         # max rows per normalised event signature
                            # (original corpus reaches 321/signature; 24 still forces
                            #  >=40 distinct event cells in the final 1,000)
FAMILY_CELL_CAP = 45        # max rows per (family, tx_enum, pm_enum) cell

ORIGINAL_PATH = _PROJECT_ROOT / "training_data" / "fyjc_specialist_1000.jsonl"
OUT_PATH = _PROJECT_ROOT / "training_data" / "fyjc_hardcore_1000.jsonl"
MANIFEST_PATH = _PROJECT_ROOT / "training_data" / "fyjc_hardcore_1000.manifest.json"

GENERATOR_VERSION = "phase20-generator-1.0.0"
VALIDATOR_VERSION = "phase20-validator-1.0.0"

FORBIDDEN_OUTPUT_KEYS = {
    "journal", "debit_lines", "credit_lines", "ledger", "balances",
    "debit_account", "credit_account", "journal_entry",
}

# ---------------------------------------------------------------------------
# Vocabulary pools (fixed order — never iterate a set when emitting text)
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "Rahul", "Mohan", "Suresh", "Kavita", "Amit", "Priya", "Vijay", "Sunil",
    "Anita", "Rakesh", "Meena", "Farhan", "Deepak", "Sunita", "Manoj", "Pooja",
    "Arjun", "Neha", "Sanjay", "Rekha", "Vikram", "Shreya", "Naveen", "Divya",
    "Ajay", "Swati", "Rohit", "Kiran", "Prakash", "Lata", "Sameer", "Geeta",
    "Nitin", "Anjali", "Harish", "Sarita", "Yogesh", "Bhavna", "Dinesh", "Komal",
]

FIRM_NAMES = [
    "Sharma Traders", "Gupta and Sons", "Verma Enterprises", "Iyer and Co",
    "Patil Stores", "Desai Brothers", "Khan Exports", "Mehta Agencies",
    "Reddy and Company", "Joshi Suppliers", "Bose Traders", "Nair Enterprises",
    "Kapoor Retail", "Menon and Company", "Chopra Deals", "Sinha Traders",
]

GOODS = [
    "stationery", "groceries", "medicines", "packaging materials",
    "computer accessories", "sports equipment", "readymade garments",
    "school uniforms", "kitchenware", "electrical fittings", "toys",
    "leather bags", "cosmetics", "hardware items", "furniture",
]

RENT_WORDS = ["rent", "shop rent", "office rent", "godown rent"]
SALARY_WORDS = ["salary", "salaries", "wages"]
OTHER_EXPENSES = [
    "electricity bill", "telephone bill", "carriage inwards", "carriage outwards",
    "printing and stationery", "insurance premium", "advertisement expenses",
    "conveyance expenses", "postage expenses", "office expenses",
]

REASONS_RETURN = [
    "as they were defective", "because they were damaged",
    "as the wrong items were supplied", "as they were of poor quality",
    "because the sizes were incorrect", "as excess goods were supplied",
]

DISTRACTORS = [
    "It was raining heavily that day.",
    "The shop was near the bus stand.",
    "Their accountant was on leave that week.",
    "The market was unusually crowded.",
    "This happened during the Diwali season.",
    "The delivery boy came on a scooty.",
    "The invoice book was almost over.",
    "It was the last working day of the month.",
    "The shopkeeper was chatting with a neighbour.",
    "Power went off in the evening.",
]

AMOUNTS = [800, 1200, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 7500,
           8000, 9000, 10000, 12000, 15000, 18000, 20000, 25000, 30000,
           40000, 50000, 60000, 75000, 90000, 100000, 150000, 200000, 250000]


def fmt_amount(rng: random.Random, value: int) -> str:
    style = rng.randrange(4)
    if style == 0:
        return f"\u20b9{value:,}"
    if style == 1:
        return f"Rs.{value:,}"
    if style == 2:
        return f"Rs. {value}"
    return f"{value:,} rupees"


def one(rng: random.Random, seq: List[str]) -> str:
    return seq[rng.randrange(len(seq))]


# ---------------------------------------------------------------------------
# Scenario families — each yields input text; difficulty intent + style is
# recorded per row for metadata. All within the FYJC contract.
# ---------------------------------------------------------------------------

def fam_basic_single(rng, _ctx) -> Tuple[str, str, str, str]:
    """A — realistic single transactions across all core types."""
    kind = rng.randrange(6)
    name = one(rng, FIRST_NAMES)
    firm = one(rng, FIRM_NAMES)
    amount = fmt_amount(rng, one(rng, AMOUNTS))
    goods = one(rng, GOODS)
    if kind == 0:
        text = rng.choice([
            f"Purchased {goods} from {firm} for {amount} cash.",
            f"Bought {goods} worth {amount} from {name} and paid immediately.",
            f"Purchased {goods} for {amount} on credit from {firm}.",
        ])
        fam = "basic_purchase"
    elif kind == 1:
        text = rng.choice([
            f"Sold {goods} to {name} for {amount} cash.",
            f"Sold {goods} to {firm} for {amount} on credit.",
            f"Delivered {goods} worth {amount} to {name}.",
        ])
        fam = "basic_sale"
    elif kind == 2:
        text = rng.choice([
            f"Paid {one(rng, RENT_WORDS)} {amount} by cheque.",
            f"Paid {one(rng, SALARY_WORDS)} {amount} in cash.",
            f"Paid for {one(rng, OTHER_EXPENSES)} {amount} by UPI.",
            f"Paid {firm} {amount} towards {one(rng, OTHER_EXPENSES)} via NEFT.",
        ])
        fam = "basic_expense"
    elif kind == 3:
        text = rng.choice([
            f"Received {amount} cash from {name} against our bill.",
            f"Received a cheque of {amount} from {firm}.",
            f"{firm} sent {amount} by NEFT towards dues.",
        ])
        fam = "basic_receipt"
    elif kind == 4:
        text = rng.choice([
            f"Started business with cash of {amount}.",
            f"Introduced capital of {amount} in the business.",
            f"Owner invested {amount} cash into the firm.",
        ])
        fam = "basic_capital"
    else:
        text = rng.choice([
            f"Withdrew {amount} from the business for personal use.",
            f"Owner withdrew goods worth {amount} for household use.",
            f"Took {amount} cash from the shop for personal expenses.",
        ])
        fam = "basic_drawing"
    return text, fam, "single", "standard"


def fam_payment_mode_matrix(rng, _ctx) -> Tuple[str, str, str, str]:
    """Same underlying event across every supported payment mode."""
    pm_word, pm_tag = rng.choice([
        ("cash", "cash"), ("by cheque", "cheque"), ("via UPI", "upi"),
        ("through NEFT", "neft"), ("by bank transfer", "bank"),
        ("on credit", "credit"),
    ])
    name = one(rng, FIRST_NAMES)
    firm = one(rng, FIRM_NAMES)
    amount = fmt_amount(rng, one(rng, AMOUNTS))
    goods = one(rng, GOODS)
    kind = rng.randrange(3)
    if kind == 0:
        text = f"Purchased {goods} from {firm} for {amount} {pm_word}."
    elif kind == 1:
        text = f"Sold {goods} to {name} for {amount} {pm_word}."
    else:
        text = f"Paid {one(rng, OTHER_EXPENSES)} of {amount} to {firm} {pm_word}."
    return text, f"pm_matrix_{pm_tag}", "single", "standard"


def fam_compound_multi(rng, _ctx) -> Tuple[str, str, str, str]:
    """B/C — multi-clause, two-party, two-amount transactions."""
    n1, n2 = rng.sample(FIRST_NAMES, 2)
    a1 = fmt_amount(rng, one(rng, AMOUNTS[:20]))
    a2 = fmt_amount(rng, one(rng, AMOUNTS[:20]))
    g1, g2 = rng.sample(GOODS, 2)
    templates = [
        f"Bought {g1} from {n1} for {a1} cash and later sold {g2} to {n2} for {a2} on credit.",
        f"Paid {n1} {a1} for {one(rng, OTHER_EXPENSES)} and received {a2} from {n2} in cash.",
        f"Purchased {g1} worth {a1} from {n1} by cheque; the same day sold {g2} to {n2} for {a2} cash.",
        f"Withdrew {a1} for office use and paid {n2} {a2} for {one(rng, OTHER_EXPENSES)}.",
    ]
    return rng.choice(templates), "compound_multi", "multi", "standard"


def fam_returns(rng, _ctx) -> Tuple[str, str, str, str]:
    """J/K — purchase returns and sales returns with realistic reasons."""
    n1 = one(rng, FIRST_NAMES)
    amount = fmt_amount(rng, one(rng, AMOUNTS[:18]))
    goods = one(rng, GOODS)
    reason = one(rng, REASONS_RETURN)
    if rng.randrange(2) == 0:
        text = rng.choice([
            f"Returned {goods} worth {amount} to {n1} {reason}.",
            f"{n1} was sent back {goods} of {amount} {reason}.",
        ])
        fam = "purchase_return"
    else:
        text = rng.choice([
            f"{n1} returned {goods} of {amount} {reason}.",
            f"Goods worth {amount} returned by {n1} {reason}.",
        ])
        fam = "sales_return"
    return text, fam, "single", "standard"


def fam_settlements(rng, _ctx) -> Tuple[str, str, str, str]:
    """Q — full and partial settlements (two amounts, both explicit)."""
    n1 = one(rng, FIRST_NAMES)
    due = one(rng, [5000, 8000, 10000, 12000, 15000, 20000, 25000])
    paid = rng.choice([due, int(due * 0.95), int(due * 0.9), int(due * 0.5)])
    if paid == due:
        text = rng.choice([
            f"Paid {n1} {fmt_amount(rng, paid)} in full settlement of {fmt_amount(rng, due)}.",
            f"Settled the account of {n1} by paying {fmt_amount(rng, paid)} cash.",
        ])
        fam = "settlement_full"
    else:
        text = (
            f"Paid {n1} {fmt_amount(rng, paid)} against the dues of "
            f"{fmt_amount(rng, due)}; balance still payable."
        )
        fam = "settlement_partial"
    return text, fam, "single", "standard"


def fam_discounts(rng, _ctx) -> Tuple[str, str, str, str]:
    """F/G — trade discount and cash discount (amount form, FYJC scope)."""
    name = one(rng, FIRST_NAMES)
    firm = one(rng, FIRM_NAMES)
    price = one(rng, [10000, 12000, 15000, 20000, 25000, 40000, 50000])
    disc = int(price * rng.choice([0.05, 0.1, 0.15]))
    net = price - disc
    if rng.randrange(2) == 0:
        text = rng.choice([
            f"Purchased goods worth {fmt_amount(rng, price)} from {firm} at a trade discount of {fmt_amount(rng, disc)}.",
            f"Sold goods listed at {fmt_amount(rng, price)} to {name}; allowed trade discount {fmt_amount(rng, disc)}.",
        ])
        fam = "trade_discount"
    else:
        text = rng.choice([
            f"Received {fmt_amount(rng, net)} from {name} in full settlement of {fmt_amount(rng, price)} due to cash discount of {fmt_amount(rng, disc)}.",
            f"Paid {firm} {fmt_amount(rng, net)} for dues of {fmt_amount(rng, price)} after cash discount of {fmt_amount(rng, disc)}.",
        ])
        fam = "cash_discount"
    return text, fam, "single", "standard"


def fam_gst_aware(rng, _ctx) -> Tuple[str, str, str, str]:
    """GST-tagged FYJC invoices (totals only; no split computation claimed)."""
    firm = one(rng, FIRM_NAMES)
    amount = fmt_amount(rng, one(rng, [5900, 11800, 17700, 23600, 35400, 47200]))
    goods = one(rng, GOODS)
    text = rng.choice([
        f"Purchased {goods} from {firm} for {amount} including GST.",
        f"Sold {goods} to {firm} for {amount} plus 18% GST by cheque.",
        f"Purchased {goods} worth {amount} against GST invoice, paid by UPI.",
    ])
    return text, "gst_aware", "single", "standard"


def fam_ambiguous(rng, _ctx) -> Tuple[str, str, str, str]:
    """U — pronoun references and genuinely unclear parties."""
    amount = fmt_amount(rng, one(rng, AMOUNTS[:18]))
    goods = one(rng, GOODS)
    templates = [
        f"He sold {goods} for {amount} cash.",
        f"They purchased {goods} worth {amount} on credit.",
        f"She paid the bill of {amount} by cheque.",
        f"He received {amount} from the customer in cash.",
        f"They deposited {amount} into the bank.",
    ]
    return rng.choice(templates), "ambiguous_pronoun", "reference", "standard"


def fam_incomplete(rng, _ctx) -> Tuple[str, str, str, str]:
    """T — missing amount / party / payment mode."""
    n1 = one(rng, FIRST_NAMES)
    goods = one(rng, GOODS)
    kind = rng.randrange(4)
    if kind == 0:
        text = f"Purchased {goods} from {n1} for cash."          # no amount
        fam = "incomplete_no_amount"
    elif kind == 1:
        amount = fmt_amount(rng, one(rng, AMOUNTS[:16]))
        text = f"Bought {goods} for {amount} on credit."          # no party
        fam = "incomplete_no_party"
    elif kind == 2:
        text = f"Sold {goods} to {n1}."                            # nothing else
        fam = "incomplete_minimal"
    else:
        amount = fmt_amount(rng, one(rng, AMOUNTS[:16]))
        text = f"Paid {n1} {amount} for repairs."                  # no mode
        fam = "incomplete_no_mode"
    return text, fam, "single", "conversational"


def fam_contradictory(rng, _ctx) -> Tuple[str, str, str, str]:
    """Contradictory payment claims (mirrors the original corpus convention)."""
    n1 = one(rng, FIRST_NAMES)
    goods = one(rng, GOODS)
    amount = fmt_amount(rng, one(rng, AMOUNTS[:20]))
    templates = [
        f"Bought {goods} for {amount} cash but paid it through bank.",
        f"Purchased {goods} {amount} on credit but settled cash immediately.",
        f"Sold goods to {n1} for {amount} cash though the amount is still due.",
    ]
    return rng.choice(templates), "contradictory_payment", "adversarial", "standard"


def fam_adversarial_contrast(rng, ctx) -> Tuple[str, str, str, str]:
    """W/X — near-identical pairs where ONE word changes the meaning.

    Templates keep party + amount + explicit payment so a correctly
    classified row lands as difficulty=adversarial; a few party-less
    variants are kept deliberately for incomplete/adversarial contrast.
    """
    variant = ctx.get("contrast_slot", rng.randrange(4))
    n1 = one(rng, FIRST_NAMES)
    firm = one(rng, FIRM_NAMES)
    goods = one(rng, GOODS)
    amount = fmt_amount(rng, one(rng, AMOUNTS[:20]))
    if variant == 0:      # cash vs credit counterfactual (purchase side)
        mode = "cash" if rng.randrange(2) == 0 else "on credit"
        text = f"Purchased {goods} from {firm} for {amount} {mode}."
    elif variant == 1:    # cash vs credit counterfactual (sale side)
        mode = "cash" if rng.randrange(2) == 0 else "on credit"
        text = f"Sold {goods} to {n1} for {amount} {mode}."
    elif variant == 2:    # expense vs drawing (one phrase changes treatment)
        text = rng.choice([
            f"Paid shop rent {amount} to {firm} by cheque.",
            f"Withdrew {amount} cash from the shop for personal use.",
        ])
    else:                 # goods vs furniture, purchase vs sale
        text = rng.choice([
            f"Purchased furniture from {firm} for {amount} cash.",
            f"Purchased goods from {firm} for {amount} cash.",
            f"Sold goods to {n1} for {amount} cash.",
        ])
    return text, "contrast_pair", "adversarial", "standard"


def fam_irrelevant_info(rng, _ctx) -> Tuple[str, str, str, str]:
    """V — valid transaction buried in irrelevant real-world context."""
    n1 = one(rng, FIRST_NAMES)
    firm = one(rng, FIRM_NAMES)
    amount = fmt_amount(rng, one(rng, AMOUNTS[:20]))
    goods = one(rng, GOODS)
    distractor = one(rng, DISTRACTORS)
    lead = rng.randrange(2) == 0
    core = rng.choice([
        f"Purchased {goods} from {firm} for {amount} cash.",
        f"Sold {goods} to {n1} for {amount} on credit.",
        f"Paid {one(rng, OTHER_EXPENSES)} of {amount} by cheque.",
    ])
    text = f"{distractor} {core}" if lead else f"{core} {distractor}"
    return text, "irrelevant_context", "distractor", "standard"


def fam_informal_noisy(rng, _ctx) -> Tuple[str, str, str, str]:
    """Y — informal, imperfect, developer-realistic user English."""
    n1 = one(rng, FIRST_NAMES)
    amount = fmt_amount(rng, one(rng, AMOUNTS[:18]))
    goods = one(rng, GOODS)
    templates = [
        f"bought {goods} from {n1}, {amount} cash",
        f"paid {n1} {amount} by upi for repairs",
        f"{n1} gave us {amount} for old {goods}",
        f"sold some {goods} for {amount}, cash only",
        f"took {amount} from shop counter for home",
        f"paid shop rent {amount} in cash to {n1}",
    ]
    return rng.choice(templates), "informal_noisy", "single", "noisy"


def fam_reference_history(rng, _ctx) -> Tuple[str, str, str, str]:
    """Timing/history-dependent phrasing that needs prior context."""
    n1 = one(rng, FIRST_NAMES)
    amount = fmt_amount(rng, one(rng, AMOUNTS[:18]))
    templates = [
        f"Paid {n1} {amount} for last month's bill today.",
        f"Received {amount} from {n1} towards the old outstanding.",
        f"Paid the balance of {amount} to {n1} by cheque.",
        f"Adjusted {amount} from {n1}'s advance against this month's bill.",
    ]
    return rng.choice(templates), "reference_history", "reference", "conversational"


def fam_paraphrase_set(rng, ctx) -> Tuple[str, str, str, str]:
    """Z — same underlying event, different sentence structures."""
    slot = ctx.get("paraphrase_slot", rng.randrange(4))
    n1 = one(rng, FIRST_NAMES)
    amount = fmt_amount(rng, one(rng, AMOUNTS[:20]))
    goods = one(rng, GOODS)
    variants = [
        f"{n1} was paid {amount} cash for {one(rng, OTHER_EXPENSES)}.",
        f"For {one(rng, OTHER_EXPENSES)}, {amount} was paid to {n1} in cash.",
        f"Payment of {amount} made in cash to {n1} towards {one(rng, OTHER_EXPENSES)}.",
        f"{n1} received {amount} cash from us for {one(rng, OTHER_EXPENSES)}.",
    ]
    return variants[slot % len(variants)], "paraphrase_set", "single", "standard"


FAMILIES = [
    (fam_basic_single, 300),
    (fam_payment_mode_matrix, 80),
    (fam_compound_multi, 140),
    (fam_returns, 80),
    (fam_settlements, 70),
    (fam_discounts, 60),
    (fam_gst_aware, 30),
    (fam_ambiguous, 60),
    (fam_incomplete, 60),
    (fam_contradictory, 30),
    (fam_adversarial_contrast, 90),
    (fam_irrelevant_info, 40),
    (fam_informal_noisy, 70),
    (fam_reference_history, 30),
    (fam_paraphrase_set, 40),
]


# ---------------------------------------------------------------------------
# Normalisation / dedup helpers
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def token_set(text: str) -> frozenset:
    return frozenset(normalise(text).split())


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def event_signature(meta: Dict[str, Any]) -> str:
    return "|".join([
        str(meta["transaction_type"]),
        str(meta["payment_method"]),
        "P" if meta["has_party"] else "-",
        "A" if meta["has_amount"] else "-",
        "M" if meta["is_multi_transaction"] else "-",
        str(meta["category"]),
    ])


# ---------------------------------------------------------------------------
# Metadata derivation (deterministic, from specialist output + family intent)
# ---------------------------------------------------------------------------

MISSING_FLAGS = {"MISSING_AMOUNT", "MISSING_PARTY", "MISSING_PAYMENT_MODE"}


def normalise_amount_format(output: Dict[str, Any]) -> None:
    """Store amounts in the corpus-standard integer-string convention.

    The original 1,000-row dataset stores amount values as integer strings
    ('15000' — zero of 1,011 amount entries end in '.0'). The deterministic
    specialist emits float strings ('15000.0'), which the production
    grounding gate's digit normalisation cannot match against input text
    like '₹15,000'. The schema verifier accepts both forms; we conform to
    the established dataset convention here (data-side only — the runtime
    is deliberately untouched by Phase 20).
    """
    for amt in output.get("amounts", []):
        value = amt.get("value")
        if isinstance(value, str) and value.endswith(".0"):
            amt["value"] = value[:-2]
        elif isinstance(value, float) and value.is_integer():
            amt["value"] = str(int(value))


def derive_difficulty(ambiguity_flags: List[str], family: str) -> str:
    flags = [f for f in ambiguity_flags if f != "NONE"]
    if not flags:
        return "adversarial" if family in {
            "contrast_pair", "contradictory_payment",
        } else "clear"
    if "CONFLICTING_INFORMATION" in flags:
        return "contradictory"
    if flags and set(flags) <= MISSING_FLAGS:
        return "incomplete"
    return "ambiguous"


def build_metadata(output: Dict[str, Any], family: str, category: str,
                   style: str) -> Dict[str, Any]:
    pm = output["payment_method_enum"]
    flags = output["ambiguity_flags"]
    scope = output["scope_flags"]
    return {
        "difficulty": derive_difficulty(flags, family),
        "language_style": style,
        "category": category,
        "family": family,
        "transaction_type": output["transaction_type_enum"],
        "payment_method": pm,
        "has_party": bool(output["parties"]),
        "has_amount": bool(output["amounts"]),
        "has_payment": pm != "UNKNOWN",
        "is_ambiguous": any(f != "NONE" for f in flags),
        "is_contradictory": "CONFLICTING_INFORMATION" in flags,
        "is_unsupported": False,
        "is_multi_transaction": "MULTI_TRANSACTION" in scope,
        "has_reference": bool(output["references"]),
    }


# ---------------------------------------------------------------------------
# Validation gauntlet (production validators + Phase 20 checks)
# ---------------------------------------------------------------------------

def validate_candidate(record: Dict[str, Any], gate: ExpandedGroundingGate,
                       seen_inputs: set, original_token_sets: List[frozenset],
                       ) -> Tuple[bool, str]:
    rid = record["id"]
    text = record["input"]
    output = record["output"]
    meta = record["metadata"]

    # 0. text sanity
    if not text or not text.strip():
        return False, f"{rid}: empty_input"
    if len(text) > 240:
        return False, f"{rid}: input_too_long"

    # 1. duplicate control (before content checks: a duplicate is rejected
    #    as a duplicate regardless of the output's own validity)
    norm = normalise(text)
    if norm in seen_inputs:
        return False, f"{rid}: duplicate_input"
    tokens = token_set(text)
    for other in original_token_sets:
        if jaccard(tokens, other) >= NEAR_DUP_JACCARD:
            return False, f"{rid}: near_dup_of_original"

    # 2. schema compliance (production verifier, expanded contract)
    report = validate_structured_interpretation(output, allow_expanded=True)
    if not report.valid:
        return False, f"{rid}: schema:{[e.issue for e in report.errors][:2]}"

    # 2. grounding gate (production) — also enforces no VERIFIED claim,
    #    no forbidden accounting fields, all facts text-supported
    grounding = gate.ground(output, text)
    if not grounding.safe_for_kernel:
        return False, f"{rid}: gate:{list(grounding.issues)[:2]}"

    # 3. leakage: output must never carry accounting-truth keys
    bad_keys = FORBIDDEN_OUTPUT_KEYS & set(output.keys())
    if bad_keys:
        return False, f"{rid}: forbidden_fields:{sorted(bad_keys)}"

    # 4. VERIFIED must never appear anywhere in the output
    if output.get("suggested_status") == "VERIFIED":
        return False, f"{rid}: claimed_VERIFIED"
    if "VERIFIED" in json.dumps(output):
        return False, f"{rid}: VERIFIED_string"

    # 5. enum membership straight from the contract
    if output["transaction_type_enum"] not in VALID_TRANSACTION_TYPES:
        return False, f"{rid}: bad_tx_enum"
    if output["payment_method_enum"] not in VALID_PAYMENT_METHODS:
        return False, f"{rid}: bad_pm_enum"
    if not set(output["ambiguity_flags"]) <= VALID_AMBIGUITY_TYPES:
        return False, f"{rid}: bad_ambiguity_enum"
    if not set(output["safety_flags"]) <= VALID_SAFETY_FLAGS:
        return False, f"{rid}: bad_safety_enum"
    if not set(output["scope_flags"]) <= VALID_SCOPE_FLAGS:
        return False, f"{rid}: bad_scope_enum"

    # 7b. a CLEAR input the specialist cannot classify is a runtime
    #     limitation (§13): reject the example, never train on it.
    #     Genuinely ambiguous/incomplete/adversarial UNKNOWN rows are kept.
    if meta["difficulty"] == "clear" and output["transaction_type_enum"] == "UNKNOWN":
        return False, f"{rid}: unclassifiable_clear_input"

    # 6c. classification evidence must be genuinely present in the input
    #     (§13/§14). The recorded keyword must appear in the text exactly or
    #     as an inflected word form (standard English suffixes only: -ed,
    #     -d, -es, -s, -ing). A keyword landing inside an unrelated word
    #     (e.g. 'paid off' matching inside 'paid office') is spurious —
    #     reject the row and report the runtime limitation, never train on it.
    for fc in output.get("field_confidences", []):
        if fc.get("field_name") == "transaction_type":
            src = (fc.get("source_text") or "").strip()
            if src and not re.search(
                    r"\b" + re.escape(src.lower()) + r"(?:ed|d|es|s|ing)?\b",
                    text.lower()):
                return False, f"{rid}: keyword_substring_mismatch"
            break

    # 7. metadata ↔ output consistency (derived-rule assertion)  # noqa: E501
    recomputed = build_metadata(output, meta["family"], meta["category"],
                                meta["language_style"])
    for k in ("difficulty", "category", "language_style", "family",
              "transaction_type", "payment_method", "has_party", "has_amount",
              "has_payment", "is_ambiguous", "is_contradictory",
              "is_unsupported", "is_multi_transaction", "has_reference"):
        if meta[k] != recomputed[k]:
            return False, f"{rid}: metadata_mismatch:{k}"

    return True, "ok"


def near_dup_vs_accepted(tokens: frozenset, accepted_tokens: List[frozenset]) -> bool:
    for other in accepted_tokens:
        if jaccard(tokens, other) >= NEAR_DUP_JACCARD:
            return True
    return False


# ---------------------------------------------------------------------------
# Optional Groq enrichment (fail-closed, reuses the EXISTING gateway adapter)
# ---------------------------------------------------------------------------

def groq_reword(text: str) -> Optional[str]:
    """Reword via existing GroqAdapter. Returns None on ANY failure.

    Fail-closed by design: the candidate is simply skipped when the key is
    missing or the call errors — never accepted unvalidated.
    """
    import os
    key = (os.getenv("GROQ_API_KEY", "") or "").strip()
    if not key:
        return None
    try:
        from backend.gateway.providers.groq_adapter import GroqAdapter
        adapter = GroqAdapter(api_key=key)
        prompt = (
            "Reword this accounting transaction sentence using different, "
            "natural sentence structure. Keep every fact identical: same "
            "parties, same amount, same payment mode, same transaction "
            "meaning. Reply with ONLY the reworded sentence.\nSentence: "
            + text
        )
        resp = adapter.execute(prompt, temperature=0.2, max_tokens=120)
        if resp.error or not resp.content:
            return None
        reworded = resp.content.strip().strip('"')
        if not reworded or len(reworded) > 240:
            return None
        return reworded
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main generation pipeline
# ---------------------------------------------------------------------------

def load_original(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 20 hardcore dataset generator")
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--enrich", action="store_true",
                        help="enable optional Groq wording enrichment (needs GROQ_API_KEY)")
    parser.add_argument("--target", type=int, default=TARGET)
    args = parser.parse_args()

    out_path = Path(args.out)
    if out_path.exists():
        print(f"REFUSING to overwrite existing candidate: {out_path}")
        return 2

    rng = random.Random(SEED)
    specialist = FYJCAISpecialist()
    gate = ExpandedGroundingGate()

    originals = load_original(ORIGINAL_PATH)
    original_norms = {normalise(r["input"]) for r in originals}
    original_tokens = [token_set(r["input"]) for r in originals]
    original_hash = hashlib.sha256(ORIGINAL_PATH.read_bytes()).hexdigest()

    # ---- build candidate pool (quota-proportional, family-round-robin) ------
    pool: List[Tuple[str, str, str, str]] = []
    pool_target = int(args.target * POOL_FACTOR)
    total_quota = sum(q for _, q in FAMILIES)
    ctx: Dict[str, int] = {"contrast_slot": 0, "paraphrase_slot": 0}
    for fam_fn, fam_quota in FAMILIES:
        want = max(1, round(pool_target * fam_quota / total_quota))
        for i in range(want):
            ctx["contrast_slot"] = i % 4
            ctx["paraphrase_slot"] = i % 4
            text, family, category, style = fam_fn(rng, ctx)
            pool.append((text, family, category, style))
    rng.shuffle(pool)

    # ---- enrich (optional, fail-closed) --------------------------------------
    enriched_count = 0
    if args.enrich:
        import os
        if not (os.getenv("GROQ_API_KEY", "") or "").strip():
            print("CONFIGURATION: GROQ_API_KEY not set — enrichment disabled; "
                  "continuing with the deterministic corpus (fail-closed).")
        else:
            enriched: List[Tuple[str, str, str, str]] = []
            for text, family, category, style in pool[: len(pool) // 4]:
                reworded = groq_reword(text)
                if reworded:
                    enriched.append((reworded, family + "+llm", category, style))
                    enriched_count += 1
            pool.extend(enriched)
            print(f"enrichment: {enriched_count} candidates reworded via Groq")

    # ---- validate + select ---------------------------------------------------
    seen_inputs = set(original_norms)
    accepted: List[Dict[str, Any]] = []
    accepted_tokens: List[frozenset] = []
    event_counts: Counter = Counter()
    cell_counts: Counter = Counter()
    rejections: Counter = Counter()
    family_accepted: Counter = Counter()

    for text, family, category, style in pool:
        if len(accepted) >= args.target:
            break
        output = specialist.parse(text)
        normalise_amount_format(output)
        meta = build_metadata(output, family, category, style)
        rid = f"hc_{len(accepted) + 1:05d}_pending"
        record = {"id": rid, "input": text, "output": output, "metadata": meta}
        ok, reason = validate_candidate(record, gate, seen_inputs, original_tokens)
        if not ok:
            key = reason.split(":", 1)[1].split(":")[0] if ":" in reason else reason
            rejections[key] += 1
            continue
        sig = event_signature(meta)
        if event_counts[sig] >= SAME_EVENT_CAP:
            rejections["event_signature_cap"] += 1
            continue
        cell = (family, meta["transaction_type"], meta["payment_method"])
        if cell_counts[cell] >= FAMILY_CELL_CAP:
            rejections["family_cell_cap"] += 1
            continue
        tokens = token_set(text)
        if near_dup_vs_accepted(tokens, accepted_tokens):
            rejections["near_dup_within_candidate"] += 1
            continue

        seen_inputs.add(normalise(text))
        accepted_tokens.append(tokens)
        event_counts[sig] += 1
        cell_counts[cell] += 1
        family_accepted[family] += 1
        record["id"] = f"hc_{len(accepted) + 1:05d}"
        accepted.append(record)

    if len(accepted) < args.target:
        print(f"FAILED to reach {args.target} valid examples: accepted={len(accepted)} "
              f"from pool={len(pool)} — refusing to lower standards (fail closed).")
        print("rejections:", dict(rejections))
        return 3

    # ---- write outputs -------------------------------------------------------
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=False) for r in accepted]
    payload = "\n".join(lines) + "\n"
    out_path.write_text(payload, encoding="utf-8")
    out_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def dist(field: str) -> Dict[str, int]:
        return dict(sorted(Counter(str(r["metadata"][field]) for r in accepted).items(),
                           key=lambda kv: (-kv[1], kv[0])))

    manifest = {
        "dataset_name": "fyjc_hardcore_1000",
        "version": "phase20-candidate-v1",
        "row_count": len(accepted),
        "source_dataset": "training_data/fyjc_specialist_1000.jsonl",
        "source_dataset_sha256": original_hash,
        "candidate_dataset_sha256": out_hash,
        "generation_seed": SEED,
        "generator_version": GENERATOR_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "determinism": {
            "python_hash_seed_pinned": True,
            "note": "deterministic across processes with PYTHONHASHSEED=0; "
                    "Groq enrichment (off) would break full reproducibility",
        },
        "generation_model": None if not enriched_count else "groq:llama-3.3-70b-versatile",
        "generation_model_revision": None,
        "enriched_rows": enriched_count,
        "distributions": {
            "difficulty": dist("difficulty"),
            "category": dist("category"),
            "transaction_type": dist("transaction_type"),
            "payment_method": dist("payment_method"),
            "language_style": dist("language_style"),
            "family": dict(sorted(family_accepted.items())),
        },
        "rejection_statistics": dict(sorted(rejections.items())),
        "validation": {
            "schema_verifier": "validate_structured_interpretation(allow_expanded=True) — all rows pass",
            "grounding_gate": "ExpandedGroundingGate.safe_for_kernel — all rows pass",
            "verified_claims": 0,
            "duplicate_inputs_vs_original": 0,
            "near_duplicate_threshold_jaccard": NEAR_DUP_JACCARD,
            "status": "ACCEPTED_CANDIDATE",
        },
    }
    Path(args.manifest).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"OK: wrote {len(accepted)} rows -> {out_path}")
    print(f"    sha256 {out_hash}")
    print(f"    pool={len(pool)} rejections={dict(rejections)}")
    print(f"    difficulty={manifest['distributions']['difficulty']}")
    print(f"    tx_types={manifest['distributions']['transaction_type']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
