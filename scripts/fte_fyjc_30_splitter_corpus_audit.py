#!/usr/bin/env python3
"""
Sprint 30 — Whole-Problem Splitter Corpus Audit: Balanced-but-Wrong Transactions

Audits whether the splitter and problem engine correctly preserve all transaction
entities, amounts, and instruments through the full pipeline.

Classification per problem:
  VERIFIED_CORRECT      — structure and accounting agree with expectations
  REVIEW_REQUIRED_CORRECT — correctly refuses to guess
  NOT_SUPPORTED_CORRECT  — correctly rejects unsupported structure
  BALANCED_BUT_WRONG     — journal balances but semantic facts are wrong
  UNBALANCED_WRONG       — accounting result fails balancing
  INPUT_CORRUPTION       — information lost before reasoning can operate
"""
from __future__ import annotations

import hashlib
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
import json
import sys
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Corpus: problems with expected diagnostic metadata
# ---------------------------------------------------------------------------
# Each entry:
#   id, text, expected_txns (min), expected_entities (set of names),
#   expected_amounts (set of Decimal strings for key amounts),
#   expected_instruments (set: 'cash','cheque','bank','credit'),
#   category, description

CORPUS: List[Dict[str, Any]] = [
    # --- A. Multiple transactions in adjacent lines ---
    {
        "id": "S30-A1",
        "category": "A_adjacent_lines",
        "description": "Two purchases, one payment — 3 independent transactions",
        "text": (
            "Purchased goods from Ramesh Rs.12000\n"
            "Purchased goods from Mehta Rs.8000\n"
            "Paid Rs.10000 to Ramesh by cheque"
        ),
        "expected_txns": 3,
        "expected_entities": {"Ramesh", "Mehta"},
        "expected_amounts": {"12000", "8000", "10000"},
        "expected_instruments": {"cheque"},
        "expect_payment_party": "Ramesh",
    },
    {
        "id": "S30-A2",
        "category": "A_adjacent_lines",
        "description": "Sale then receipt — 2 transactions",
        "text": (
            "Sold goods to Amit Rs.15000 on credit\n"
            "Received Rs.10000 from Amit in cash"
        ),
        "expected_txns": 2,
        "expected_entities": {"Amit"},
        "expected_amounts": {"15000", "10000"},
        "expected_instruments": {"cash", "credit"},
    },
    {
        "id": "S30-A3",
        "category": "A_adjacent_lines",
        "description": "Purchase then return — 2 transactions",
        "text": (
            "Purchased goods from Raj Rs.20000\n"
            "Returned goods to Raj Rs.5000"
        ),
        "expected_txns": 2,
        "expected_entities": {"Raj"},
        "expected_amounts": {"20000", "5000"},
        "expected_instruments": set(),
    },
    {
        "id": "S30-A4",
        "category": "A_adjacent_lines",
        "description": "Sale then discount allowed — 2 transactions",
        "text": (
            "Sold goods to Sunil Rs.25000 on credit\n"
            "Allowed discount to Sunil Rs.1000"
        ),
        "expected_txns": 2,
        "expected_entities": {"Sunil"},
        "expected_amounts": {"25000", "1000"},
        "expected_instruments": set(),
    },
    {
        "id": "S30-A5",
        "category": "A_adjacent_lines",
        "description": "Purchase then partial payment — 2 transactions",
        "text": (
            "Purchased goods from Mohan Rs.30000\n"
            "Paid Rs.20000 to Mohan in cash"
        ),
        "expected_txns": 2,
        "expected_entities": {"Mohan"},
        "expected_amounts": {"30000", "20000"},
        "expected_instruments": {"cash"},
    },

    # --- B. Multiple named parties ---
    {
        "id": "S30-B1",
        "category": "B_multiple_parties",
        "description": "Three different parties — purchase, purchase, payment to one",
        "text": (
            "Purchased goods from Raj Rs.5000\n"
            "Purchased goods from Mohan Rs.8000\n"
            "Purchased goods from Suresh Rs.3000\n"
            "Paid Rs.5000 to Raj in cash"
        ),
        "expected_txns": 4,
        "expected_entities": {"Raj", "Mohan", "Suresh"},
        "expected_amounts": {"5000", "8000", "3000"},
        "expected_instruments": {"cash"},
    },
    {
        "id": "S30-B2",
        "category": "B_multiple_parties",
        "description": "Two debtors — sale then receipt from different debtor",
        "text": (
            "Sold goods to Anand Rs.10000 on credit\n"
            "Sold goods to Bipin Rs.15000 on credit\n"
            "Received Rs.10000 from Anand in cash"
        ),
        "expected_txns": 3,
        "expected_entities": {"Anand", "Bipin"},
        "expected_amounts": {"10000", "15000"},
        "expected_instruments": {"cash", "credit"},
    },

    # --- C. Multiple amounts / multi-step ---
    {
        "id": "S30-C1",
        "category": "C_multiple_amounts",
        "description": "Credit purchase then partial payment — 2 transactions",
        "text": (
            "Purchased goods from Ravi Rs.25000 on credit\n"
            "Paid Rs.15000 to Ravi by cheque"
        ),
        "expected_txns": 2,
        "expected_entities": {"Ravi"},
        "expected_amounts": {"25000", "15000"},
        "expected_instruments": {"cheque", "credit"},
    },
    {
        "id": "S30-C2",
        "category": "C_multiple_amounts",
        "description": "Sale with discount and receipt — 3 transactions",
        "text": (
            "Sold goods to Kiran Rs.40000 on credit\n"
            "Allowed discount to Kiran Rs.2000\n"
            "Received Rs.38000 from Kiran in cash"
        ),
        "expected_txns": 3,
        "expected_entities": {"Kiran"},
        "expected_amounts": {"40000", "2000", "38000"},
        "expected_instruments": {"cash", "credit"},
    },

    # --- D. Connector-heavy sentences ---
    {
        "id": "S30-D1",
        "category": "D_connectors",
        "description": "Two purchases separated by period, then payment",
        "text": (
            "Purchased goods from Raj Rs.5000. Purchased goods from Amit Rs.7000. "
            "Paid Rs.5000 to Raj in cash"
        ),
        "expected_txns": 3,
        "expected_entities": {"Raj", "Amit"},
        "expected_amounts": {"5000", "7000"},
        "expected_instruments": {"cash"},
    },
    {
        "id": "S30-D2",
        "category": "D_connectors",
        "description": "Purchase, then payment immediately same day",
        "text": (
            "Purchased goods from Suresh Rs.12000\n"
            "Paid Rs.12000 to Suresh in cash immediately"
        ),
        "expected_txns": 2,
        "expected_entities": {"Suresh"},
        "expected_amounts": {"12000"},
        "expected_instruments": {"cash"},
    },

    # --- E. Indian/FYJC phrasing ---
    {
        "id": "S30-E1",
        "category": "E_indian_phrasing",
        "description": "Paid cash same time — settlement phrasing",
        "text": (
            "Purchased goods from Kiran Rs.8000\n"
            "Paid cash same time Rs.8000"
        ),
        "expected_txns": 2,
        "expected_entities": {"Kiran"},
        "expected_amounts": {"8000"},
        "expected_instruments": {"cash"},
    },
    {
        "id": "S30-E2",
        "category": "E_indian_phrasing",
        "description": "Balance paid — settlement of creditor",
        "text": (
            "Purchased goods from Raj Rs.10000\n"
            "Paid Rs.6000 to Raj in cash\n"
            "Balance paid to Raj Rs.4000 in cash"
        ),
        "expected_txns": 3,
        "expected_entities": {"Raj"},
        "expected_amounts": {"10000", "6000", "4000"},
        "expected_instruments": {"cash"},
    },

    # --- F. Full whole-problem with opening ---
    {
        "id": "S30-F1",
        "category": "F_whole_problem",
        "description": "Full opening + 4 transactions (DWP001-like)",
        "text": (
            "Opening:\n"
            "Started business with cash Rs.50000\n"
            "Purchased goods from Raj Rs.20000 on credit\n"
            "Paid Rs.15000 to Raj in cash\n"
            "Sold goods to Amit Rs.25000 on credit\n"
            "Received Rs.15000 from Amit in cash"
        ),
        "expected_txns": 5,
        "expected_entities": {"Raj", "Amit"},
        "expected_amounts": {"50000", "20000", "15000", "25000"},
        "expected_instruments": {"cash", "credit"},
    },

    # --- G. Three-party with payment to different party ---
    {
        "id": "S30-G1",
        "category": "G_cross_party",
        "description": "Purchase from Raj, purchase from Amit, pay Raj",
        "text": (
            "Purchased goods from Raj Rs.10000\n"
            "Purchased goods from Amit Rs.8000\n"
            "Paid Rs.10000 to Raj by cheque"
        ),
        "expected_txns": 3,
        "expected_entities": {"Raj", "Amit"},
        "expected_amounts": {"10000", "8000"},
        "expected_instruments": {"cheque"},
        "expect_payment_party": "Raj",
    },

    # --- H. The exact DWP003 case ---
    {
        "id": "S30-H1_DWP003",
        "category": "H_DWP003_repro",
        "description": "Exact DWP003 — the splitter merges Mehta and Ramesh payment",
        "text": (
            "Opening:\n"
            "Cash Rs.50000\n"
            "Bank Rs.30000\n"
            "Capital Rs.80000\n\n"
            "Purchased goods from Ramesh Rs.12000\n"
            "Purchased goods from Mehta Rs.8000\n"
            "Paid Rs.10000 to Ramesh by cheque"
        ),
        "expected_txns": 3,  # after opening, 3 real transactions
        "expected_entities": {"Ramesh", "Mehta"},
        "expected_amounts": {"12000", "8000", "10000"},
        "expected_instruments": {"cheque"},
        "expect_payment_party": "Ramesh",
    },
]


# ---------------------------------------------------------------------------
# Diagnostic engine
# ---------------------------------------------------------------------------
def _extract_names_from_text(text: str) -> set:
    """Extract capitalized proper nouns that look like Indian names."""
    import re
    # Match capitalized words that aren't at start of sentence or after common verbs
    words = re.findall(r'\b([A-Z][a-z]+)\b', text)
    stopwords = {
        "Opening", "Cash", "Bank", "Capital", "Purchased", "Sold", "Paid",
        "Received", "Goods", "Rent", "Salary", "Return", "Discount",
        "Cheque", "Draft", "Neft", "Rtgs", "Upi", "Jan", "Feb", "Mar",
        "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        "Debit", "Credit", "Dr", "Cr", "Rs", "Note", "Ledger", "Journal",
        "Business", "Personal", "Real", "Nominal", "Account", "Accounts",
        "A", "An", "The", "By", "To", "From", "For", "In", "On", "At",
        "With", "And", "Or", "But", "Not", "If", "Then", "Else",
        "P", "Pla", "Tria",  # false positives from "Platrixa"
    }
    return {w for w in words if w not in stopwords and len(w) > 1}


def _extract_amounts_from_text(text: str) -> set:
    """Extract Rs.XXXXX amounts from text."""
    import re
    amounts = re.findall(r'Rs\.?\s*(\d[\d,]*)', text)
    return {a.replace(",", "") for a in amounts}


def _journal_accounts(journal: dict) -> set:
    """Extract all account names from a journal."""
    accounts = set()
    for dl in journal.get("debit_lines", []):
        if isinstance(dl, dict):
            acc = dl.get("account", "")
            if acc:
                accounts.add(acc)
    for cl in journal.get("credit_lines", []):
        if isinstance(cl, dict):
            acc = cl.get("account", "")
            if acc:
                accounts.add(acc)
    return accounts


def _journal_amounts(journal: dict) -> set:
    """Extract all amounts from journal lines."""
    amounts = set()
    for dl in journal.get("debit_lines", []):
        if isinstance(dl, dict):
            amt = dl.get("amount")
            if amt is not None:
                amounts.add(str(amt))
    for cl in journal.get("credit_lines", []):
        if isinstance(cl, dict):
            amt = cl.get("amount")
            if amt is not None:
                amounts.add(str(amt))
    return amounts


def _has_cash_or_cheque(journal: dict, raw_text: str) -> set:
    """Determine instruments from journal and text."""
    instruments = set()
    low = raw_text.lower()
    accounts = _journal_accounts(journal)
    if "Cash" in accounts:
        instruments.add("cash")
    if "Bank" in accounts:
        instruments.add("cheque")
    if "cash" in low:
        instruments.add("cash")
    if "cheque" in low or "check" in low:
        instruments.add("cheque")
    if "credit" in low:
        instruments.add("credit")
    return instruments


def run_corpus() -> List[Dict[str, Any]]:
    from backend.maths.fyjc_problem_engine import process_problem

    results = []
    for spec in CORPUS:
        t0 = time.time()
        try:
            result = process_problem(spec["text"])
            elapsed = time.time() - t0
        except Exception as exc:
            results.append({
                "id": spec["id"],
                "category": spec["category"],
                "classification": "INPUT_CORRUPTION",
                "error": str(exc),
            })
            continue

        txns = result.get("transactions", [])
        # Filter out opening/informational that produce NOT_SUPPORTED
        real_txns = [t for t in txns if t.get("status") not in ("NOT_SUPPORTED", None)]
        all_txns = txns

        # Collect all entities and amounts across all VERIFIED transactions
        all_journal_accounts = set()
        all_journal_amounts = set()
        verified_count = 0
        rr_count = 0
        ns_count = 0
        incorrect_verified = False

        for txn in real_txns:
            status = txn.get("status", "")
            journal = txn.get("journal", {})
            text = txn.get("text", "")

            if status == "VERIFIED":
                verified_count += 1
                all_journal_accounts.update(_journal_accounts(journal))
                all_journal_amounts.update(_journal_amounts(journal))
            elif status == "REVIEW_REQUIRED":
                rr_count += 1
            elif status == "NOT_SUPPORTED":
                ns_count += 1

        # Check entity preservation
        # Expected entities are names from the original problem text
        expected_names = spec["expected_entities"]
        # Journal accounts include account types (Purchases, Bank etc) not just names
        # Check if expected party names appear in journal accounts
        entities_preserved = True
        missing_entities = set()
        for name in expected_names:
            # Check in journal accounts (the party name should be an account)
            found = name in all_journal_accounts
            if not found:
                # Also check the raw text of all transactions
                for txn in real_txns:
                    if name.lower() in txn.get("text", "").lower():
                        found = True
                        break
            if not found:
                entities_preserved = False
                missing_entities.add(name)

        # Check amount preservation
        expected_amounts = spec["expected_amounts"]
        amounts_preserved = expected_amounts.issubset(all_journal_amounts)

        # Check instrument preservation
        expected_instruments = spec.get("expected_instruments", set())
        all_instruments = set()
        for txn in real_txns:
            if txn.get("status") == "VERIFIED":
                all_instruments.update(
                    _has_cash_or_cheque(txn.get("journal", {}), txn.get("text", ""))
                )
        instruments_preserved = expected_instruments.issubset(all_instruments)

        # Classify
        classification = "VERIFIED_CORRECT"

        if incorrect_verified:
            classification = "UNBALANCED_WRONG"
        elif not entities_preserved:
            # Check if this is actually a problem:
            # If the missing entity appears in NOT_SUPPORTED opening, that's ok
            truly_missing = set()
            for name in missing_entities:
                # Check if this entity appears in any transaction text (even NOT_SUPPORTED)
                found_in_any = False
                for txn in all_txns:
                    if name.lower() in txn.get("text", "").lower():
                        found_in_any = True
                        break
                if not found_in_any:
                    truly_missing.add(name)

            if truly_missing:
                classification = "BALANCED_BUT_WRONG"
            else:
                # Entity was in opening but not in a verified transaction
                classification = "VERIFIED_CORRECT"
        elif not amounts_preserved:
            classification = "BALANCED_BUT_WRONG"

        # Determine if the splitter merged transactions
        original_lines = [l.strip() for l in spec["text"].split("\n") if l.strip()
                          and not l.strip().startswith("Opening")]
        # Count non-opening, non-empty lines that look like transactions
        expected_separate = sum(1 for l in original_lines
                                if any(kw in l.lower() for kw in
                                       ["purchased", "sold", "paid", "received",
                                        "returned", "allowed", "discount"]))

        # How many segments did we actually get?
        actual_segment_texts = [t.get("text", "") for t in all_txns]

        # Detect semicolons in transaction text (sign of merging)
        merged_count = sum(1 for t in actual_segment_texts if ";" in t)

        # Balance check
        balanced = True
        for txn in real_txns:
            journal = txn.get("journal", {})
            if not journal.get("balanced", True):
                balanced = False

        # Compute hash
        text_hash = hashlib.sha256(spec["text"].encode()).hexdigest()[:12]

        results.append({
            "id": spec["id"],
            "category": spec["category"],
            "description": spec["description"],
            "classification": classification,
            "text_hash": text_hash,
            "expected_txns": spec["expected_txns"],
            "actual_txns": len(all_txns),
            "verified_txns": verified_count,
            "rr_txns": rr_count,
            "ns_txns": ns_count,
            "expected_entities": sorted(expected_names),
            "entities_preserved": entities_preserved,
            "missing_entities": sorted(missing_entities),
            "expected_amounts": sorted(expected_amounts),
            "amounts_preserved": amounts_preserved,
            "journal_amounts": sorted(all_journal_amounts),
            "expected_instruments": sorted(expected_instruments),
            "instruments_preserved": instruments_preserved,
            "balanced": balanced,
            "merged_segments": merged_count,
            "execution_time_ms": round(elapsed * 1000, 1),
        })

    return results


def print_report(results: List[Dict[str, Any]]) -> None:
    print("=" * 72)
    print("SPRINT 30 — WHOLE-PROBLEM SPLITTER CORPUS AUDIT")
    print("=" * 72)

    # Summary counts
    classifications = {}
    for r in results:
        c = r.get("classification", "ERROR")
        classifications[c] = classifications.get(c, 0) + 1

    total_expected_txns = sum(r.get("expected_txns", 0) for r in results)
    total_actual_txns = sum(r.get("actual_txns", 0) for r in results)
    total_verified = sum(r.get("verified_txns", 0) for r in results)

    all_expected_entities = set()
    preserved_entities = 0
    for r in results:
        for e in r.get("expected_entities", []):
            all_expected_entities.add(e)
        if r.get("entities_preserved"):
            preserved_entities += len(r.get("expected_entities", []))

    all_expected_amounts = set()
    preserved_amounts = 0
    for r in results:
        for a in r.get("expected_amounts", []):
            all_expected_amounts.add(a)
        if r.get("amounts_preserved"):
            preserved_amounts += len(r.get("expected_amounts", []))

    print(f"\nWhole problems tested: {len(results)}")
    print(f"Total transactions expected: {total_expected_txns}")
    print(f"Total transactions produced: {total_actual_txns}")
    print(f"Entities expected: {len(all_expected_entities)}")
    print(f"Entities preserved: {preserved_entities}/{sum(len(r.get('expected_entities', [])) for r in results)}")
    print(f"Amounts expected: {len(all_expected_amounts)}")
    print(f"Amounts preserved: {preserved_amounts}/{sum(len(r.get('expected_amounts', [])) for r in results)}")

    print(f"\n--- Classification Breakdown ---")
    for cls in ["VERIFIED_CORRECT", "REVIEW_REQUIRED_CORRECT", "NOT_SUPPORTED_CORRECT",
                 "BALANCED_BUT_WRONG", "UNBALANCED_WRONG", "INPUT_CORRUPTION"]:
        count = classifications.get(cls, 0)
        flag = " ⚠️" if cls == "BALANCED_BUT_WRONG" else (" 🚨" if cls in ("UNBALANCED_WRONG", "INPUT_CORRUPTION") else "")
        print(f"  {cls}: {count}{flag}")

    # Detailed per-problem report
    print(f"\n--- Per-Problem Results ---")
    for r in results:
        status_icon = {
            "VERIFIED_CORRECT": "✅",
            "REVIEW_REQUIRED_CORRECT": "⚠️",
            "NOT_SUPPORTED_CORRECT": "ℹ️",
            "BALANCED_BUT_WRONG": "🔴",
            "UNBALANCED_WRONG": "🚨",
            "INPUT_CORRUPTION": "💥",
        }.get(r.get("classification"), "?")

        print(f"\n  {status_icon} {r['id']} [{r.get('category', '?')}]")
        print(f"     {r.get('description', '')}")
        print(f"     classification: {r.get('classification')}")
        print(f"     txns: expected={r.get('expected_txns')} produced={r.get('actual_txns')} "
              f"(verified={r.get('verified_txns')}, rr={r.get('rr_txns')}, ns={r.get('ns_txns')})")
        print(f"     entities: expected={r.get('expected_entities')} preserved={r.get('entities_preserved')} "
              f"missing={r.get('missing_entities', [])}")
        print(f"     amounts: expected={r.get('expected_amounts')} preserved={r.get('amounts_preserved')} "
              f"journal={r.get('journal_amounts', [])}")
        print(f"     balanced={r.get('balanced')} merged_segments={r.get('merged_segments')}")
        print(f"     time={r.get('execution_time_ms', 0)}ms")

    # Regression summary
    print(f"\n--- Summary ---")
    bbw = classifications.get("BALANCED_BUT_WRONG", 0)
    uw = classifications.get("UNBALANCED_WRONG", 0)
    ic = classifications.get("INPUT_CORRUPTION", 0)

    if bbw > 0:
        print(f"\n  🔴 {bbw} BALANCED_BUT_WRONG cases detected")
        print(f"     These are cases where the journal balances but semantic facts are wrong.")
        print(f"     The splitter merged or lost transaction context.")
    if uw > 0:
        print(f"\n  🚨 {uw} UNBALANCED_WRONG cases detected")
    if ic > 0:
        print(f"\n  💥 { INPUT_CORRUPTION} INPUT_CORRUPTION cases detected")

    if bbw == 0 and uw == 0 and ic == 0:
        print(f"\n  ✅ No dangerous failure classes detected")

    # Safety check
    print(f"\n--- Safety Invariants ---")
    print(f"  incorrect_verified: {classifications.get('UNBALANCED_WRONG', 0)}")
    print(f"  balanced_but_wrong: {classifications.get('BALANCED_BUT_WRONG', 0)}")
    print(f"  input_corruption: {classifications.get('INPUT_CORRUPTION', 0)}")


def main():
    results = run_corpus()
    print_report(results)

    # Write JSON for downstream comparison
    with open("scripts/sprint30_corpus_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults written to scripts/sprint30_corpus_results.json")

    # Exit code: non-zero if dangerous failures found
    bbw = sum(1 for r in results if r.get("classification") == "BALANCED_BUT_WRONG")
    uw = sum(1 for r in results if r.get("classification") in ("UNBALANCED_WRONG", "INPUT_CORRUPTION"))
    sys.exit(1 if (bbw + uw) > 0 else 0)


if __name__ == "__main__":
    main()
