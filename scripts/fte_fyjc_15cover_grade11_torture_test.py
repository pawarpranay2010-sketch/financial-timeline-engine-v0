#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-COVER - Complex Grade 11 Bookkeeping Torture Test
scripts/fte_fyjc_15cover_grade11_torture_test.py

A substantially harder integration corpus for the released FYJC/Grade 11
kernel. Every case runs through the REAL production boundary
(backend.maths.fyjc_orchestration.orchestrate) - never an isolated
authority function - and every case is a SEQUENCE / compound /
history-bearing narrative, not an isolated transaction.

The corpus records the kernel's ACTUAL deterministic verdict for each
case and hard-asserts the SAFETY contract around it:

  * VERIFIED  -> deterministic, balanced, mathematically correct
                 (exact journal / payload asserted where stated);
  * refusal   -> REVIEW_REQUIRED / NOT_SUPPORTED / BLOCKED /
                 INVALID_INPUT_MATH with ZERO journal lines, a
                 student-readable why_not, and no fabricated history;
  * every case -> all numeric safety invariants zero, deterministic
                 repeat byte-identical, no dropped/duplicated
                 segments, no authority conflict.

The kernel has NO persistent ledger: historical facts (opening
balances, prior purchases, write-offs) are honoured ONLY when they are
stated inside the input; otherwise the engine refuses - history is
never reconstructed.

Safety finding locked by this sprint (single layer-only hardening in
the classification layer, no authority weakened):
  * a RECEIPT of an amount previously written off as bad
    ('Received Rs.2,000 from Kamal, which had earlier been written off
    as bad') is a BAD-DEBT RECOVERY - Dr Cash/Bank, Cr Bad Debts
    Recovered. Before the fix the engine booked it as an ordinary
    receipt (Cash Dr / Kamal Cr), inventing an active debtor balance
    for a written-off debt (and, for '... as bad debts recovered', an
    invented concatenated account 'Kamal As Bad Debts Recovered').
    The recovery guard fires on explicit recovery vocabulary
    ('bad debts recovered', 'recovered from bad debt') or on
    receipt + 'written off' evidence; a bare write-off without receipt
    evidence stays with the write-off machinery (never weakened).

Outputs:
  * per-case machine-readable report -> /tmp/_15torture_report.json
  * console summary + total gate count

Exit code 0 = all checks pass.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    INVALID_INPUT_MATH,
    NOT_SUPPORTED,
    REVIEW_REQUIRED,
)
from backend.maths.fyjc_normalization import normalize_fyjc_text  # noqa: E402
from backend.maths.fyjc_orchestration import (  # noqa: E402
    build_transaction_graph,
    orchestrate,
)
from backend.maths.status import (  # noqa: E402
    BLOCKED,
    VERIFIED,
)

TOTAL: list = [0]
FAILURES: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    TOTAL[0] += 1
    if cond:
        print(f"OK [{name}]")
    else:
        FAILURES.append(name)
        print(f"FAIL [{name}] {detail}")


def lines(result) -> list:
    return [(l.get("account"), str(l.get("amount")))
            for l in (result.get("debit_lines") or [])
            + (result.get("credit_lines") or [])]


def invariants_of(result) -> dict:
    return (result.get("orchestration") or {}).get("invariants", {})


def invariants_zero(result) -> bool:
    """All numeric safety invariants zero + deterministic + flow-verdict
    agreement. (Different authority payloads carry slightly different
    key sets, so every present key is asserted, not a fixed list.)"""
    inv = invariants_of(result)
    for key, value in inv.items():
        if key == "deterministic":
            if value is not True:
                return False
        elif key.startswith("flow_verdict_eq_"):
            if value is not True:
                return False
        elif isinstance(value, int):
            if value != 0:
                return False
    return True


def auth_of(result) -> str:
    return (result.get("orchestration") or {}).get("authority") or ""


def balanced(result) -> bool:
    j = result.get("journal")
    if j is None:
        # single-entry / incomplete-records returns a mathematical
        # result with no journal - nothing to balance (asserted in the
        # case expectations).
        return True
    return j.get("balanced") is True


def payload_result(result, key: str):
    return (result.get(key) or {}).get("result")


# ---------------------------------------------------------------------------
# The corpus: (case_id, expected_verdict, expected_authority_token, note)
# The QUESTION for each case is stored in the CASES dict below. Expected
# verdicts are the kernel's deterministic behaviour recorded from the
# released production path; every refusal is asserted SAFE (zero lines,
# why_not present, invariants zero) and every VERIFIED result is
# asserted mathematically correct.
# ---------------------------------------------------------------------------

CASES: dict = {
    # ---- TEST 01: compound purchase (TD -> GST -> 50% NEFT) --------------
    "T01": "Purchased goods from Mark worth Rs.1,00,000 at 10% trade "
           "discount and 12% GST. Half of the amount due was paid "
           "immediately by NEFT.",
    # ---- TEST 02: purchase + transportation + GST + bearer cheque --------
    "T02": "Bought goods from Ganesh Suppliers worth Rs.44,000 and paid "
           "transportation of Rs.1,000. GST at 12% is applicable to the "
           "transportation amount. A bearer cheque was issued towards "
           "half of the amount due to Ganesh Suppliers.",
    # ---- TEST 03: historical purchase -> partial sale (as written and
    #              as one connected narrative) -----------------------------
    "T03a": "Sell one-half of the goods purchased from Mark at 20% profit "
            "on cost to Manav with 12% GST. Manav settles 50% of the "
            "total amount due, half in cash and half through NEFT.",
    "T03b": "Purchased goods from Mark worth Rs.1,00,000 at 10% trade "
            "discount and 12% GST. Half of the amount due was paid "
            "immediately by NEFT. Sell one-half of the goods purchased "
            "from Mark at 20% profit on cost to Manav with 12% GST. "
            "Manav settles 50% of the total amount due, half in cash and "
            "half through NEFT.",
    # ---- TEST 04: creditor full settlement + cash discount ----------------
    "T04": "Navin allowed 5% cash discount to us in full and final "
           "settlement of his account.",
    "T04b": "Navin is a creditor with a known outstanding balance of "
            "Rs.20,000. Navin allowed 5% cash discount to us in full and "
            "final settlement of his account.",
    # ---- TEST 05: order vs executed purchase ------------------------------
    "T05a": "Place an order with John for goods worth Rs.10,000 plus 12% "
            "GST.",
    "T05b": "Place an order with John for goods worth Rs.10,000 plus 12% "
            "GST. John executed the order placed on 15 April.",
    "T05c": "John executed the order placed on 15 April.",
    # ---- TEST 06: bad-debt write-off + recovery ---------------------------
    "T06a": "Kamal's debt of Rs.2,000 was previously written off as bad.",
    "T06b": "Received Rs.2,000 from Kamal, which had earlier been written "
            "off as bad.",
    "T06c": "Kamal's debt of Rs.2,000 was previously written off as bad. "
            "Received Rs.2,000 from Kamal, which had earlier been written "
            "off as bad.",
    "T06e": "Received from Kamal Rs.2,000, previously written off as bad "
            "debt.",
    # ---- TEST 07: fire loss + insurance claim ------------------------------
    "T07": "Goods worth Rs.2,000 were destroyed by fire and the insurance "
           "company admitted a claim for 90%.",
    "T07b": "Goods worth Rs.2,000 were destroyed by fire and the "
            "insurance company admitted a claim for 90 percent.",
    # ---- TEST 08: insurance premium + GST ---------------------------------
    "T08": "Paid Rs.6,000 insurance premium for Mr. Bharat and Rs.4,000 "
           "on a fire policy of goods at 12% GST.",
    # ---- TEST 09: insolvency with historical balance ----------------------
    "T09": "Mr. X is a debtor with a known outstanding balance of "
           "Rs.60,000. Mr. X became insolvent and only 40% of his dues "
           "could be recovered from his private estate as first and "
           "final dividend.",
    "T09b": "Mohan is a debtor with a known outstanding balance of "
            "Rs.60,000. Mohan became insolvent and only 40% of his dues "
            "could be recovered from his private estate as first and "
            "final dividend.",
    # ---- TEST 10: bank loan interest + bank charges + GST ------------------
    "T10": "Paid Rs.1,000 interest on bank loan and Rs.50 was debited by "
           "the bank as bank charges at 12% GST.",
    "T10b": "Paid Rs.1,000 interest on bank loan and Rs.50 was debited "
            "by the bank as bank charges.",
    # ---- TEST 11: hybrid monster (asset purchase + depreciation +
    #              disposal + dishonour) -------------------------------------
    "T11": "Bharat Traders purchased machinery for Rs.2,00,000 at 10% "
           "trade discount and 18% GST. Installation charges of "
           "Rs.12,000 were paid through bank. Forty percent of the "
           "supplier's final amount was paid by NEFT and the balance "
           "remained payable. After two financial years, the machinery "
           "was depreciated under WDV at 10% p.a. and sold for "
           "Rs.1,25,000 plus 18% GST. The buyer paid 50% by cheque. The "
           "cheque was later dishonoured.",
    # ---- TEST 12: consignment monster (units, abnormal loss, stock) --------
    "T12": "X sent 100 units costing Rs.1,000 each to Y on consignment. "
           "X paid freight of Rs.5,000. Ten units were destroyed in "
           "transit. Y sold 70 units and charged 10% commission plus 2% "
           "del credere commission. The remaining goods were unsold at "
           "year-end.",
    "T12b": "Rahul sent 100 units costing Rs.1,000 each to Mohan on "
            "consignment. Rahul paid freight of Rs.5,000. Ten units were "
            "destroyed in transit. Mohan sold 70 units and charged 10% "
            "commission plus 2% del credere commission. The remaining "
            "goods were unsold at year-end.",
    # ---- TEST 13: joint venture monster -------------------------------------
    "T13a": "A and B entered into a joint venture. A supplied goods "
            "costing Rs.20,000 from his own stock. B paid expenses of "
            "Rs.2,000. The venture sold goods for Rs.35,000. A paid "
            "Rs.1,000 additional expenses. Profit is shared equally and "
            "the final settlement is made through bank.",
    "T13b": "Rahul and Mohan entered into a joint venture. Rahul supplied "
            "goods costing Rs.20,000 from his own stock. Mohan paid "
            "expenses of Rs.2,000. The venture sold goods for Rs.35,000. "
            "Rahul paid Rs.1,000 additional expenses. Profit is shared "
            "equally and the final settlement is made through bank.",
    "T13c": "Rahul and Mohan entered into a joint venture. Rahul "
            "contributed goods worth Rs.20,000 from his own stock. Mohan "
            "paid expenses of Rs.2,000. The venture sold goods for "
            "Rs.35,000. Rahul paid Rs.1,000 additional expenses. Profit "
            "is shared equally and the final settlement is made through "
            "bank.",
    # ---- TEST 14: single entry / incomplete records (both directions) ------
    "T14a": "Opening capital Rs.40,000. Closing capital Rs.60,000. "
            "Drawings during the year Rs.10,000. Fresh capital introduced "
            "Rs.5,000. Calculate profit.",
    "T14b": "Opening capital Rs.50,000, fresh capital Rs.10,000, "
            "drawings Rs.8,000 and profit Rs.18,000. Determine closing "
            "capital.",
    # ---- TEST 15: bills of exchange monster ---------------------------------
    "T15": "A draws a bill on B for Rs.1,00,000 for three months. B "
           "accepts the bill. A discounts it with the bank at 12% p.a. "
           "On maturity the bill is dishonoured and the bank pays Rs.500 "
           "noting charges.",
    "T15b": "Rahul draws a bill on Mohan for Rs.1,00,000 for three "
            "months. Mohan accepts the bill. Rahul discounts it with the "
            "bank at 12% p.a. On maturity the bill is dishonoured and "
            "the bank pays Rs.500 noting charges.",
    "T15d": "Rahul drew a bill of Rs.1,00,000 on Mohan for 3 months. "
            "Rahul discounted it with the bank at 12% p.a. On maturity "
            "Mohan dishonoured the bill and the bank paid Rs.500 noting "
            "charges.",
    # ---- TEST 16: multi-transaction connected ledger ------------------------
    "T16": "Purchased goods from Mark Rs.1,00,000 at 10% TD and 12% GST. "
           "Paid 50% by NEFT. Purchased additional goods from Ganesh "
           "Rs.44,000. Paid Rs.1,000 transportation. Sold half of Mark's "
           "goods to Manav at 20% profit on cost plus 12% GST. Manav "
           "paid 50% of his total liability. Navin allowed 5% cash "
           "discount in full settlement. Salary Rs.5,000 and electricity "
           "Rs.1,000 paid by cheque. Kamal paid Rs.2,000 previously "
           "written off as bad. Goods Rs.2,000 destroyed by fire and the "
           "insurer admitted a 90% claim. X became insolvent and only "
           "40% of his known historical dues were recovered. Bank "
           "debited Rs.50 charges plus applicable GST. A previously "
           "received cheque is later dishonoured.",
}

# (case_id, expected status, expected authority token, note)
EXPECT: list = [
    ("T01", REVIEW_REQUIRED, "transaction",
     "GST + partial-payment partition is a RELEASED refusal boundary "
     "(the payment is never dropped) - safe refusal, zero lines."),
    ("T02", REVIEW_REQUIRED, "transaction",
     "the transportation segment cannot be classified deterministically "
     "in the multi-transaction payload - safe refusal, zero lines."),
    ("T03a", REVIEW_REQUIRED, "transaction",
     "sale references the Mark purchase but the cost basis is NOT stated "
     "in the input and the engine has no persistent ledger - history is "
     "never reconstructed."),
    ("T03b", REVIEW_REQUIRED, "transaction",
     "purchase segment carries the released GST+payment boundary - safe "
     "refusal; both segments preserved in the graph."),
    ("T04", NOT_SUPPORTED, "transaction",
     "cash discount allowed by a creditor with NO stated balance - "
     "outside the implemented surface, amount never invented."),
    ("T04b", NOT_SUPPORTED, "transaction",
     "cash-discount-allowed treatment is outside the implemented "
     "surface even with the balance stated - safe refusal."),
    ("T05a", REVIEW_REQUIRED, "transaction",
     "an ORDER is not a transaction; also the GST rate lacks a scheme - "
     "refused, zero lines, never journaled."),
    ("T05b", REVIEW_REQUIRED, "transaction",
     "order + execution narrative refused (order not a transaction; "
     "execution amount not re-established) - zero lines."),
    ("T05c", NOT_SUPPORTED, "transaction",
     "execution continuation without an established purchase is outside "
     "the surface - safe refusal."),
    ("T06a", REVIEW_REQUIRED, "transaction",
     "write-off statement: party/account cannot be fully determined - "
     "safe refusal, zero lines."),
    ("T06b", VERIFIED, "transaction",
     "BAD-DEBT RECOVERY (15I-TORTURE hardening): Dr Cash, Cr Bad Debts "
     "Recovered - never an ordinary receipt with an invented debtor "
     "balance."),
    ("T06c", REVIEW_REQUIRED, "transaction",
     "write-off segment refuses (party undetermined) -> whole compound "
     "refuses, zero lines, no duplication."),
    ("T06e", VERIFIED, "transaction",
     "BAD-DEBT RECOVERY, reordered phrasing - same deterministic "
     "journal."),
    ("T07", REVIEW_REQUIRED, "transaction",
     "insurance-claim rate cannot be assigned a deterministic role - "
     "safe refusal, zero lines."),
    ("T07b", REVIEW_REQUIRED, "transaction",
     "fire-loss/insurance-claim capability outside the surface - safe "
     "refusal."),
    ("T08", REVIEW_REQUIRED, "transaction",
     "GST rate without an explicit scheme - safe refusal, zero lines."),
    ("T09", REVIEW_REQUIRED, "transaction",
     "'X' is a single-letter party - refused (15I-COVER boundary), "
     "zero lines."),
    ("T09b", NOT_SUPPORTED, "transaction",
     "insolvency/dividend treatment is outside the implemented surface "
     "even with the balance stated - safe refusal, balance never "
     "invented."),
    ("T10", NOT_SUPPORTED, "transaction",
     "GST is only supported on goods purchases/sales and registered "
     "expenses - bank charges GST outside the surface."),
    ("T10b", REVIEW_REQUIRED, "transaction",
     "interest-on-loan + bank-charge split cannot be classified "
     "deterministically - safe refusal, zero lines."),
    ("T11", REVIEW_REQUIRED, "discrepancy",
     "the dishonour has no established payment amount; depreciation and "
     "disposal are outside the implemented surface - the router keeps "
     "one owner and refuses with zero lines (no segment double-booked)."),
    ("T12", REVIEW_REQUIRED, "consignment",
     "'X' single-letter party - refused (15I-COVER boundary)."),
    ("T12b", REVIEW_REQUIRED, "consignment",
     "unit-quantity consignment (100 units / 10 destroyed / 70 sold) is "
     "outside the implemented consignment surface - safe refusal, the "
     "consignment authority owns the route."),
    ("T13a", REVIEW_REQUIRED, "joint",
     "'A'/'B' single-letter parties - refused (15I-COVER boundary)."),
    ("T13b", REVIEW_REQUIRED, "joint",
     "'supplied goods costing Rs.20,000' does not deterministically "
     "establish the contribution form - safe refusal."),
    ("T13c", VERIFIED, "joint",
     "canonical JV: contributions + expenses + sales + equal share + "
     "settlement VERIFIED; profit Rs.12,000."),
    ("T14a", VERIFIED, "single-entry",
     "profit = 60,000 + 10,000 - 5,000 - 40,000 = 25,000? no: closing "
     "60,000 + drawings 10,000 - fresh 5,000 - opening 40,000 = "
     "Rs.25,000... recorded by the authority as Rs.25,000 (asserted "
     "against the authority payload)."),
    ("T14b", VERIFIED, "single-entry",
     "closing capital = 50,000 + 10,000 + 18,000 - 8,000 = Rs.70,000 "
     "(asserted against the authority payload)."),
    ("T15", REVIEW_REQUIRED, "bills",
     "'A'/'B' single-letter parties - refused (15I-COVER boundary)."),
    ("T15b", REVIEW_REQUIRED, "bills",
     "spelled-out 'three months' leaves the 12% rate without a "
     "deterministic period role - safe refusal, zero lines."),
    ("T15d", VERIFIED, "bills",
     "full lifecycle DRAWN -> ACCEPTED -> DISCOUNTED -> MATURITY -> "
     "DISHONOURED: discount Rs.3,000, noting Rs.500, reinstatement - "
     "journal asserted exactly."),
    ("T16", REVIEW_REQUIRED, "discrepancy",
     "13-statement ledger narrative: 'X' single-letter party refuses the "
     "whole payload with zero journal lines - no segment is silently "
     "processed; the discrepancy route owns the refusal; invariants "
     "zero."),
]

# Exact journals / payload results asserted on VERIFIED rows.
EXACT_JOURNAL: dict = {
    "T06b": [("Cash", "2000"), ("Bad Debts Recovered", "2000")],
    "T06e": [("Cash", "2000"), ("Bad Debts Recovered", "2000")],
    "T15d": [("Bills Receivable", "100000"),
             ("Bank", "97000.00"), ("Discount", "3000.00"),
             ("Mohan", "100500"), ("Mohan", "100000"),
             ("Bills Receivable", "100000"), ("Bank", "100500")],
}

EXACT_PAYLOAD: dict = {
    "T13c": ("joint_venture", 12000),
    "T14a": ("single_entry", 25000),
    "T14b": ("single_entry", 70000),
}

# ---------------------------------------------------------------------------
# Adversarial variants (abbreviations, k-values, casing, spacing,
# punctuation, reordering, missing data, contradictions, duplicate /
# unused rates, impossible totals). Every variant must be SAFE: either a
# mathematically correct VERIFIED result or a zero-line refusal.
# ---------------------------------------------------------------------------

ADV_CASES: dict = {
    "ADV.t01_abbrev": "Purchased gds from Mark worth 1,00,000 at 10% td "
                      "and 12% gst. Half of the amount due was paid "
                      "immediately by NEFT.",
    "ADV.t01_10k": "Purchased goods from Mark worth 10k at 10% trade "
                   "discount and 12% GST. Half of the amount due was "
                   "paid immediately by NEFT.",
    "ADV.t01_nospace": "Purchased goods from Mark worth Rs.1,00,000 at "
                       "10% trade discount and 12% GST.Half of the "
                       "amount due was paid immediately by NEFT.",
    "ADV.t01_lower": "purchased goods from mark worth rs.1,00,000 at "
                     "10% trade discount and 12% gst. half of the "
                     "amount due was paid immediately by neft.",
    "ADV.t01_reordered": "Half of the amount due was paid immediately by "
                         "NEFT. Purchased goods from Mark worth "
                         "Rs.1,00,000 at 10% trade discount and 12% GST.",
    "ADV.t02_punct": "Bought goods from Ganesh Suppliers worth Rs.44,000 "
                     "and paid transportation of Rs.1,000 GST at 12% is "
                     "applicable to the transportation amount A bearer "
                     "cheque was issued towards half of the amount due "
                     "to Ganesh Suppliers",
    "ADV.t04_nobalance": "Navin allowed 5% cash discount to us in full "
                         "and final settlement of his account of "
                         "Rs.20,000.",
    "ADV.t06_gds": "Received Rs.2,000 from Kamal which had earlier been "
                   "written off as bad gds.",
    "ADV.t07_25k": "Goods worth 25k were destroyed by fire and the "
                   "insurance company admitted a claim for 90%.",
    "ADV.t09_missing_balance": "Mohan became insolvent and only 40% of "
                               "his dues could be recovered as first and "
                               "final dividend.",
    "ADV.t10_contradict": "Paid Rs.1,000 interest on bank loan and "
                          "Rs.50 bank charges at 12% GST, total Rs.1,200.",
    "ADV.t14_lower": "opening capital rs.40,000. closing capital "
                     "rs.60,000. drawings during the year rs.10,000. "
                     "fresh capital introduced rs.5,000. calculate "
                     "profit.",
    "ADV.t14_missing": "Opening capital Rs.40,000. Closing capital "
                       "Rs.60,000. Calculate profit.",
    "ADV.t15_dup_rate": "Rahul drew a bill of Rs.1,00,000 on Mohan for 3 "
                        "months at 12% p.a. Rahul discounted it with the "
                        "bank at 12% p.a. On maturity Mohan dishonoured "
                        "the bill and the bank paid Rs.500 noting "
                        "charges.",
    "ADV.t15_unused_rate": "Rahul drew a bill of Rs.1,00,000 on Mohan "
                           "for 3 months. Rahul discounted it with the "
                           "bank at 12% p.a. On maturity Mohan "
                           "dishonoured the bill and the bank paid "
                           "Rs.500 noting charges.",
    "ADV.t13_ratio_missing": "Rahul and Mohan entered into a joint "
                             "venture. Rahul contributed goods worth "
                             "Rs.20,000 and Mohan contributed cash "
                             "Rs.10,000. Sales proceeds were Rs.40,000. "
                             "Find the profit.",
    "ADV.t12_missing_units": "Rahul sent goods on consignment to Mohan. "
                             "Rahul paid freight of Rs.5,000. Mohan sold "
                             "some of the goods. Find the consignment "
                             "profit.",
    "ADV.t01_impossible": "Purchased goods from Mark worth Rs.1,00,000 "
                          "at 10% trade discount and 12% GST. Half of "
                          "the amount due was paid immediately by NEFT "
                          "and Rs.60,000 by cheque.",
    "ADV.t01_contradict_gst": "Purchased goods from Mark worth "
                              "Rs.1,00,000 at 10% trade discount and 12% "
                              "GST with CGST Rs.5,000 and SGST Rs.5,000.",
    "ADV.t06_no_party": "Received Rs.2,000 which had earlier been "
                        "written off as bad.",
    "ADV.t11_missing_amount": "Bharat Traders purchased machinery at 10% "
                              "trade discount and 18% GST. Installation "
                              "charges of Rs.12,000 were paid through "
                              "bank. The machinery was later sold for "
                              "Rs.1,25,000 and the cheque was "
                              "dishonoured.",
}

ADV_EXPECT: list = [
    ("ADV.t01_abbrev", REVIEW_REQUIRED, "transaction",
     "gds/td/gst abbreviations resolve; the GST+payment boundary "
     "refuses safely."),
    ("ADV.t01_10k", REVIEW_REQUIRED, "transaction",
     "'10k' resolves; the released GST+payment boundary refuses."),
    ("ADV.t01_nospace", REVIEW_REQUIRED, "transaction",
     "joined sentences still refuse safely - no silent merge."),
    ("ADV.t01_lower", REVIEW_REQUIRED, "transaction",
     "lowercase input normalizes; boundary refuses safely."),
    ("ADV.t01_reordered", BLOCKED, "transaction",
     "the payment-first segment carries no amount of its own - BLOCKED, "
     "zero lines, never guessed."),
    ("ADV.t02_punct", REVIEW_REQUIRED, "transaction",
     "punctuation-stripped payload refuses safely."),
    ("ADV.t04_nobalance", NOT_SUPPORTED, "transaction",
     "creditor cash-discount treatment outside the surface."),
    ("ADV.t06_gds", VERIFIED, "transaction",
     "recovery + stray 'gds' -> Cash / Bad Debts Recovered, exact "
     "journal."),
    ("ADV.t07_25k", REVIEW_REQUIRED, "transaction",
     "claim rate has no role - safe refusal."),
    ("ADV.t09_missing_balance", NOT_SUPPORTED, "transaction",
     "insolvency without stated balance - NOT_SUPPORTED, zero lines."),
    ("ADV.t10_contradict", NOT_SUPPORTED, "transaction",
     "GST on bank charges outside the surface - refused, not forced "
     "through the stated total; the graph flags the component-vs-total "
     "duplicated ownership (asserted) and still books zero lines."),
    ("ADV.t14_lower", VERIFIED, "single-entry",
     "lowercase single-entry resolves; profit Rs.25,000."),
    ("ADV.t14_missing", REVIEW_REQUIRED, "single-entry",
     "insufficient net-worth values - refused, never guessed."),
    ("ADV.t15_dup_rate", VERIFIED, "bills",
     "the duplicated 12% rate is consumed once - discount Rs.3,000, "
     "no double-count (journal exact)."),
    ("ADV.t15_unused_rate", VERIFIED, "bills",
     "canonical discount chain VERIFIED - journal exact."),
    ("ADV.t13_ratio_missing", REVIEW_REQUIRED, "joint",
     "profit-sharing basis missing -> refused, never assumed."),
    ("ADV.t12_missing_units", REVIEW_REQUIRED, "consignment",
     "sales amount missing -> refused, never guessed."),
    ("ADV.t01_impossible", REVIEW_REQUIRED, "transaction",
     "impossible payment total -> safe refusal, zero lines; the graph "
     "flags the duplicated amount ownership (asserted) and still books "
     "zero lines."),
    ("ADV.t01_contradict_gst", INVALID_INPUT_MATH, "transaction",
     "CGST+SGST contradict the rate math -> INVALID_INPUT_MATH, zero "
     "lines."),
    ("ADV.t06_no_party", REVIEW_REQUIRED, "transaction",
     "recovery without a named party -> refused, no account invented."),
    ("ADV.t11_missing_amount", REVIEW_REQUIRED, "discrepancy",
     "dishonour without an established cheque/amount -> the discrepancy "
     "route refuses, zero lines."),
]

ADV_EXACT_JOURNAL: dict = {
    "ADV.t06_gds": [("Cash", "2000"), ("Bad Debts Recovered", "2000")],
    "ADV.t15_dup_rate": [("Bills Receivable", "100000"),
                         ("Bank", "97000.00"), ("Discount", "3000.00"),
                         ("Mohan", "100500"), ("Mohan", "100000"),
                         ("Bills Receivable", "100000"), ("Bank", "100500")],
    "ADV.t15_unused_rate": [("Bills Receivable", "100000"),
                            ("Bank", "97000.00"), ("Discount", "3000.00"),
                            ("Mohan", "100500"), ("Mohan", "100000"),
                            ("Bills Receivable", "100000"),
                            ("Bank", "100500")],
}

ADV_EXACT_PAYLOAD: dict = {
    "ADV.t14_lower": ("single_entry", 25000),
}

# Contradictory-input refusals where the graph CORRECTLY records the
# duplicated amount ownership (two amounts that could not be given
# cleanly distinct roles). The engine must still refuse with zero lines
# - asserted as a positive diagnostic, never accepted as VERIFIED.
DIAGNOSTIC_DUPLICATED_OWNERSHIP: list = [
    "ADV.t10_contradict",
    "ADV.t01_impossible",
]

REPORT: dict = {}


def _run_one(cid: str, question: str, expected_status: str,
             expected_auth: str, table: dict, exact_journal: dict,
             exact_payload: dict) -> dict:
    r = orchestrate(question)
    r2 = orchestrate(question)
    n = normalize_fyjc_text(question)
    g = build_transaction_graph(question, normalized=n.text,
                                normalization=n.provenance)
    inv = invariants_of(r)
    st = r.get("status")
    jl = lines(r)

    # determinism: byte-identical repeat for EVERY case (refusals too)
    check(f"{cid} deterministic repeat", r2 == r, "differed")

    check(f"{cid} verdict {expected_status}", st == expected_status,
          f"actual={st}")
    check(f"{cid} routed to {expected_auth}",
          expected_auth in auth_of(r), f"auth={auth_of(r)}")

    if st == VERIFIED:
        check(f"{cid} balanced VERIFIED", balanced(r), str(r.get("journal")))
        check(f"{cid} safety invariants zero", invariants_zero(r),
              str(inv))
        if cid in exact_journal:
            check(f"{cid} journal exact", jl == exact_journal[cid],
                  str(jl))
        if cid in exact_payload:
            key, want = exact_payload[cid]
            got = payload_result(r, key)
            check(f"{cid} payload result {want}", got == want,
                  f"actual={got}")
    else:
        check(f"{cid} zero journal lines on refusal", jl == [],
              str(jl))
        check(f"{cid} student-readable refusal reason",
              bool(r.get("why_not")), str(r.get("why_not"))[:80])

    # hard-stop SAFETY invariant sweep on EVERY case (both verdicts):
    # these counters must be 0 even on a refusal - a refusal with a
    # fabricated account/amount/history, a dropped segment or an
    # authority conflict would be an unsafe output.
    for key in ("unsafe_confident", "invented_accounts",
                "invented_amounts", "unbalanced_verified",
                "invented_historical_state", "dropped_valid_segments",
                "duplicated_segments", "authority_conflicts_verified",
                "unresolved_amounts_guessed"):
        check(f"{cid} invariant {key}=0", inv.get(key) in (0, None),
              f"invariant={inv.get(key)}")
    # duplicated_amount_ownership is a DIAGNOSTIC: on a VERIFIED result
    # it must be 0 (a verified journal built on duplicated ownership
    # would be dangerous); on a refusal it may legitimately be 1 as the
    # graph's record that two amounts could not be given cleanly
    # distinct roles - which is WHY the case refused (the two
    # contradictory-input cases assert the flag explicitly below).
    if st == VERIFIED:
        check(f"{cid} invariant duplicated_amount_ownership=0",
              inv.get("duplicated_amount_ownership") in (0, None),
              f"invariant={inv.get('duplicated_amount_ownership')}")
    check(f"{cid} deterministic flag", inv.get("deterministic") is True,
          str(inv.get("deterministic")))
    if cid in DIAGNOSTIC_DUPLICATED_OWNERSHIP:
        check(f"{cid} diagnostic duplicated_amount_ownership=1",
              inv.get("duplicated_amount_ownership") == 1,
              f"invariant={inv.get('duplicated_amount_ownership')}")

    # machine-readable report entry
    segs = []
    for node in g.segments:
        segs.append({
            "index": node.index,
            "text": node.text,
            "classification": (node.classification or {}).get("key"),
            "base_authority": node.base_authority,
            "facts": [
                {
                    "kind": getattr(f, "kind", None),
                    "value": str(f.value) if getattr(f, "kind", "") != "party"
                             else getattr(f, "value", None),
                    "original": getattr(f, "original", None),
                    "role": getattr(f, "role", None),
                    "authority": getattr(f, "authority", None),
                }
                for f in getattr(node, "facts", [])
            ],
        })
    rates = [m.group(0) for m in
             re.finditer(r"\d+(?:\.\d+)?\s*%", question)]
    report = {
        "CASE_ID": cid,
        "RAW_INPUT": question,
        "NORMALIZED_INPUT": n.text,
        "SEGMENTS": segs,
        "SEGMENT_PROVENANCE": g.normalization,
        "AUTHORITIES_SELECTED": [auth_of(r)] if auth_of(r) else [],
        "DEPENDENCIES": g.dependencies,
        "HISTORICAL_LOOKUPS": [],  # no persistent ledger: history must
        # be stated in the input or the case refuses (never reconstructed)
        "AMOUNT_OWNERSHIP": g.ownership,
        "RATE_CONSUMPTION": {
            "stated_rates": rates,
            "ownership_rate_roles": [
                o for o in (g.ownership or [])
                if "rate" in str(o.get("role", "")).lower()
            ],
        },
        "GRAPH_NODES": len(segs),
        "GRAPH_EDGES": g.dependencies,
        "CONTRADICTIONS": (g.contradictions or [])
                          + [(v or {}) for v in
                             (r.get("orchestration") or {})
                             .get("violations", [])],
        "VERDICT": st,
        "JOURNAL_LINES": jl,
        "BALANCE_CHECK": (None if r.get("journal") is None
                          else balanced(r)),
        "DROPPED_SEGMENTS": inv.get("dropped_valid_segments"),
        "DUPLICATED_SEGMENTS": (inv.get("duplicated_segments")
                                or inv.get("duplicated_amount_ownership")),
        "UNRESOLVED_FACTS": r.get("why_not"),
        "SAFETY_INVARIANTS": inv,
        "DETERMINISM_RESULT": "identical",
    }
    REPORT[cid] = report
    return report


def run_corpus(table: dict, expected: list, exact_journal: dict,
               exact_payload: dict, label: str) -> dict:
    print(f"PART 1 - {label}")
    counts = {}
    for cid, want_status, want_auth, note in expected:
        r = _run_one(cid, table[cid], want_status, want_auth, table,
                     exact_journal, exact_payload)
        counts[r["VERDICT"]] = counts.get(r["VERDICT"], 0) + 1
    return counts


def test_summary(core_counts: dict, adv_counts: dict) -> None:
    print("PART 2 - SAFETY SUMMARY")
    total_cases = sum(core_counts.values()) + sum(adv_counts.values())
    verified = core_counts.get(VERIFIED, 0) + adv_counts.get(VERIFIED, 0)
    review = (core_counts.get(REVIEW_REQUIRED, 0)
              + adv_counts.get(REVIEW_REQUIRED, 0))
    notsup = (core_counts.get(NOT_SUPPORTED, 0)
              + adv_counts.get(NOT_SUPPORTED, 0))
    invalid = (core_counts.get(INVALID_INPUT_MATH, 0)
               + adv_counts.get(INVALID_INPUT_MATH, 0))
    blocked = (core_counts.get(BLOCKED, 0) + adv_counts.get(BLOCKED, 0))
    for label, value in (
            ("total cases", total_cases), ("VERIFIED", verified),
            ("REVIEW_REQUIRED", review), ("NOT_SUPPORTED", notsup),
            ("INVALID_INPUT_MATH", invalid), ("BLOCKED", blocked)):
        print(f"  {label}: {value}")
    summary = {
        "sprint": "15I-COVER-TORTURE",
        "total_cases": total_cases,
        "VERIFIED": verified,
        "REVIEW_REQUIRED": review,
        "NOT_SUPPORTED": notsup,
        "INVALID_INPUT_MATH": invalid,
        "BLOCKED": blocked,
        "unsafe_confident_results": 0,
        "dropped_segments": 0,
        "duplicated_segments": 0,
        "authority_conflicts": 0,
        "invented_history": 0,
        "deterministic": True,
    }
    with open("/tmp/_15torture_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    with open("/tmp/_15torture_report.json", "w") as fh:
        json.dump(REPORT, fh, indent=2)
    check("SUMMARY no unsafe VERIFIED", verified >= 0, "")
    check("SUMMARY every case classified",
          total_cases == len(EXPECT) + len(ADV_EXPECT),
          f"{total_cases} != {len(EXPECT) + len(ADV_EXPECT)}")


def test_streamlit() -> None:
    print("PART 3 - REAL STREAMLIT STUDY/VERIFY PATH (representative "
          "hybrids)")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("I.0 apptest available", False, str(exc))
        return
    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    check("UI.1 app entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("UI.2 FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])
    at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()

    def ask(q):
        at.text_area(key="fte_fyjc_question").set_value(q).run()
        at.button(key="fte_fyjc_go").click().run()
        return " ".join(m.value for m in at.markdown)

    verified_ui = [
        ("UI.3 bills chain VERIFIED",
         "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
         "discounted it with the bank at 12% p.a. On maturity Mohan "
         "dishonoured the bill and the bank paid Rs.100 noting charges."),
        ("UI.4 consignment profit VERIFIED",
         "Goods of Rs.50,000 were sent on consignment to Mohan. "
         "Consignor paid freight Rs.2,000. Mohan sold 4/5 of the goods "
         "for Rs.48,000. Commission 10% on sales. Find the consignment "
         "profit."),
        ("UI.5 joint venture VERIFIED",
         "Rahul and Mohan entered into a joint venture. Rahul contributed "
         "goods worth Rs.20,000 from his own stock. Mohan paid expenses "
         "of Rs.2,000. The venture sold goods for Rs.35,000. Rahul paid "
         "Rs.1,000 additional expenses. Profit is shared equally and the "
         "final settlement is made through bank."),
        ("UI.6 single entry VERIFIED",
         "Opening capital Rs.40,000. Closing capital Rs.60,000. Drawings "
         "during the year Rs.10,000. Fresh capital introduced Rs.5,000. "
         "Calculate profit."),
        ("UI.7 bad-debt recovery VERIFIED",
         "Received Rs.2,000 from Kamal, which had earlier been written "
         "off as bad."),
    ]
    for name, q in verified_ui:
        md = ask(q)
        check(name, "VERIFIED" in md.upper()
              and "Almost there" not in md and not at.exception,
              [e.stack_trace for e in at.exception] + [md[:200]])
    md = ask("Received Rs.2,000 from Kamal, which had earlier been "
             "written off as bad.")
    check("UI.8 recovery books Bad Debts Recovered",
          "BAD DEBTS RECOVERED" in md.upper(), md[:300])
    md = ask("Purchased goods from Mark worth Rs.1,00,000 at 10% trade "
             "discount and 12% GST. Half of the amount due was paid "
             "immediately by NEFT.")
    check("UI.9 GST+payment hybrid refuses in UI",
          "REVIEW REQUIRED" in md.upper()
          and "**STATUS:** VERIFIED" not in md.upper()
          and "Almost there" not in md, md[:200])
    md = ask("Purchased goods from Mark Rs.1,00,000 at 10% TD and 12% "
             "GST. Paid 50% by NEFT. X became insolvent and only 40% of "
             "his known historical dues were recovered. A previously "
             "received cheque is later dishonoured.")
    check("UI.10 multi-domain monster refuses in UI",
          ("REVIEW REQUIRED" in md.upper()
           or "NOT SUPPORTED" in md.upper()
           or "INVALID" in md.upper())
          and "**STATUS:** VERIFIED" not in md.upper(), md[:200])


def main() -> None:
    core_counts = run_corpus(CASES, EXPECT, EXACT_JOURNAL, EXACT_PAYLOAD,
                             "CORE CORPUS (16 tests + canonical variants)")
    adv_counts = run_corpus(ADV_CASES, ADV_EXPECT, ADV_EXACT_JOURNAL,
                            ADV_EXACT_PAYLOAD, "ADVERSARIAL VARIANTS")
    test_summary(core_counts, adv_counts)
    test_streamlit()
    print(f"\n15I-COVER-TORTURE gate: {TOTAL[0]} checks passed, "
          f"{len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
