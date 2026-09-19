#!/usr/bin/env python3
"""
Platrixa
Authority Expansion - Batch 1 pinned regression gate
scripts/fte_authority_expansion_batch1_test.py

Deterministic gate for Authority Expansion batch 1 (2026-09). Locks down
the newly expanded declarative kernel capabilities WITHOUT new machinery:

  B1-1  Bank charges - 'Paid Rs.500 as bank charges' / 'Bank charges
        Rs.200 paid by cheque' debit the canonical Bank Charges expense
        and credit Cash/Bank; the pinned BRS routing to the Discrepancy
        Authority is unaffected.
  B1-2  Accrued income - income earned but not yet received debits the
        Accrued Income asset and credits the SPECIFIC income account
        (accrual basis, IAS 1 / Ind AS 1); the later RECEIPT of the same
        accrued income settles the receivable (Dr Cash / Cr Accrued
        Income) and must never be re-booked as new income
        (income double-count guard).
  B1-3  Income received in advance - liability treatment (credit
        Unearned Income; IAS 37 / Ind AS 37 present-obligation concept);
        'interest received' inside 'interest received in advance' must
        not be claimed by the generic income receipt (fail-closed
        not_when ordering).
  B1-4  Credit notes - issued to a customer = sales-return treatment
        (Dr Sales Returns / Cr the customer); received from a supplier
        = purchase-return treatment (Dr the supplier / Cr Purchase
        Returns). Document form of the return, no new account family.
  B1-5  Head-noun 'paid ... for ...' resolution - ONE generic resolver
        (_for_clause_object) decides what the clause bought:
        'office furniture' buys the asset (Furniture Dr / Bank Cr, the
        party is the seller - never Office Expenses); 'vehicle
        insurance' / 'furniture repairs' expense the operative noun
        even though an asset word appears as modifier.
  B1-6  Negative guards - asset-word presence never hijacks sale or
        return wordings ('sold old furniture by cheque' stays a SALE;
        'returned furniture to Raj' does not produce a guessed journal).

Every expectation goes through reason_bk_question (the FULL pipeline:
classification -> normalization -> journal generation -> validation), so
the assertions pin the resolved journal, not a classification fragment.
"""

import sys

sys.path.insert(0, ".")

from backend.maths.fyjc_bk_reasoning import reason_bk_question  # noqa: E402
from backend.maths.status import VERIFIED  # noqa: E402

# (input, expected status, expected debit accounts, expected credit accounts)
CASES = [
    # --- B1-1 bank charges --------------------------------------------------
    ("Paid Rs.500 as bank charges.",
     "VERIFIED", {"Bank Charges"}, {"Cash"}),
    ("Bank charges Rs.200 paid by cheque.",
     "VERIFIED", {"Bank Charges"}, {"Bank"}),
    ("Paid the bank commission Rs.300 in cash.",
     "VERIFIED", {"Bank Charges"}, {"Cash"}),
    # --- B1-2 accrued income --------------------------------------------------
    ("Interest accrued but not received Rs.300.",
     "VERIFIED", {"Accrued Income"}, {"Interest Received"}),
    ("Rent due but not received Rs.1,200.",
     "VERIFIED", {"Accrued Income"}, {"Rent Received"}),
    ("Received Rs.500 interest accrued but not received earlier.",
     "VERIFIED", {"Cash"}, {"Accrued Income"}),
    ("Received accrued interest Rs.500.",
     "VERIFIED", {"Cash"}, {"Accrued Income"}),
    # --- B1-3 income received in advance ---------------------------------------
    ("Interest received in advance Rs.500.",
     "VERIFIED", {"Cash"}, {"Unearned Income"}),
    ("Received Rs.1,000 rent in advance.",
     "VERIFIED", {"Cash"}, {"Unearned Income"}),
    # --- B1-4 credit notes -------------------------------------------------------
    ("Issued a credit note to Amit for Rs.500.",
     "VERIFIED", {"Sales Returns"}, {"Amit"}),
    ("Received a credit note from Mohan Rs.700.",
     "VERIFIED", {"Mohan"}, {"Purchase Returns"}),
    # --- B1-5 head-noun 'paid ... for ...' resolution -----------------------------
    ("Paid Rs.500 to Raj for office furniture by cheque.",
     "VERIFIED", {"Furniture"}, {"Bank"}),
    ("Paid Rs.1,500 for vehicle insurance by cheque.",
     "VERIFIED", {"Insurance"}, {"Bank"}),
    ("Paid Rs.800 for furniture repairs in cash.",
     "VERIFIED", {"Repairs"}, {"Cash"}),
    ("Paid Rs.2,000 for machinery insurance.",
     "VERIFIED", {"Insurance"}, {"Cash"}),
    # --- B1-6 negative guards --------------------------------------------------------
    ("Sold old furniture by cheque for Rs.2,000.",
     "VERIFIED", {"Bank"}, {"Furniture"}),
    ("Returned furniture to Raj Rs.1,000.",
     "NOT_VERIFIED", None, None),  # refuses - never a guessed journal
]

# Pre-existing pinned behaviors that must survive the batch unchanged.
LEGACY_CASES = [
    ("Paid Rs.500 for mobile recharge.", "VERIFIED", {"Telephone Expenses"}),
    ("Paid Rahul Rs.8,000 in cash.", "VERIFIED", {"Rahul"}),
    ("Paid Rs.4,000 for shop rent.", "VERIFIED", {"Rent"}),
    ("Received commission Rs.500.", "VERIFIED", {"Cash"}),
    ("Commission received Rs.500.", "VERIFIED", {"Cash"}),
]

checks = 0
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{label}{(' :: ' + detail) if detail else ''}")


def accounts(lines) -> set:
    return {str(line.get("account")) for line in (lines or [])}


for text, want_status, want_debit, want_credit in CASES:
    r = reason_bk_question(text) or {}
    status = r.get("status")
    if want_status == "NOT_VERIFIED":
        check(f"refusal[{text}]", status != VERIFIED, f"status={status}")
        continue
    check(f"status[{text}]", status == VERIFIED, f"status={status}")
    if status != VERIFIED:
        continue
    debit = accounts(r.get("debit_lines"))
    credit = accounts(r.get("credit_lines"))
    check(f"debit[{text}]", debit == want_debit,
          f"got {debit!r} want {want_debit!r}")
    check(f"credit[{text}]", credit == want_credit,
          f"got {credit!r} want {want_credit!r}")

for text, want_status, want_debit in LEGACY_CASES:
    r = reason_bk_question(text) or {}
    status = r.get("status")
    check(f"legacy-status[{text}]", status == VERIFIED, f"status={status}")
    if status != VERIFIED:
        continue
    check(f"legacy-debit[{text}]", accounts(r.get("debit_lines")) == want_debit,
          f"got {accounts(r.get('debit_lines'))!r} want {want_debit!r}")

print(f"AUTHORITY EXPANSION BATCH 1 GATE: {checks - len(failures)}/{checks}"
      f" checks passed")
if failures:
    for f in failures:
        print(f"  FAIL - {f}")
    print("BATCH 1 FAIL - SEE FAILURES ABOVE")
    sys.exit(1)
print("BATCH 1 PASS - EXPANDED KERNEL CAPABILITIES PINNED")
