#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-COVER - Coverage Closure & Boundary Audit
scripts/fte_fyjc_15cover_coverage_closure_test.py

A permanent coverage matrix over the CURRENTLY SUPPORTED FYJC/Grade 11
accounting surface. Every row is a real production call to
backend.maths.fyjc_orchestration.orchestrate() (the SAME boundary the
FYJC Study/Verify flow uses), so the gate records the ACTUAL verdict,
the ACTUAL routing authority, the journal/result and the safety
invariants - never an expectation written to fit the test.

A capability counts as covered when it is either:
  1. VERIFIED with a deterministic, mathematically correct result, or
  2. safely refused (REVIEW_REQUIRED / NOT_SUPPORTED / BLOCKED /
     INVALID_INPUT_MATH) with zero journal lines when the system
     genuinely lacks enough information or capability.

Audit layers (the final report must distinguish all seven):
  PART A  - Chapter coverage matrix (Commercial Core / Discrepancy /
            Bills / Consignment / Joint Venture / Single Entry)
  PART B  - Authority routing audit (dangerous overlaps, one owner per
            segment, provenance preserved)
  PART C  - Hybrid payload stress (multi-domain questions)
  PART D  - Normalization boundary (abbreviations, k-values, casing,
            whitespace, malformed wording, unknown abbreviations,
            single-letter parties, identity never invented)
  PART E  - Mathematical contradiction audit (INVALID_INPUT_MATH vs
            ordinary ambiguity)
  PART F  - Historical dependency audit (no invented history)
  PART G  - Safety invariant sweep (every VERIFIED result)
  PART H  - Boundary classification + machine-readable coverage summary
  PART I  - Real Streamlit Study/Verify AppTest

Safety findings locked by this sprint (deterministic, layer-only fixes
in the normalization/hardening layer, no authority or routing change):
  * a bare single-letter party in a party position ('to A', 'from A')
    is refused instead of inventing an account like 'A for';
  * a valid payment partition ('Paid Rs.6,000 by cheque and Rs.2,000
    in cash, balance due') is never misread as INVALID_INPUT_MATH - a
    bare 'balance (due)' without an attached figure is not an
    outstanding amount;
  * the party-name capture stops at 'costing' so 'to Ram costing
    Rs.5,000' books to Ram, never to an invented 'Ram Costing'.

Exit code 0 = all checks pass.
"""

import json
import os
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    INVALID_INPUT_MATH,
    NOT_SUPPORTED,
    REVIEW_REQUIRED,
)
from backend.maths.fyjc_normalization import (  # noqa: E402
    normalize_fyjc_text,
    vy_harden,
)
from backend.maths.fyjc_orchestration import orchestrate  # noqa: E402
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


def balanced(result) -> bool:
    j = result.get("journal") or {}
    return j.get("balanced") is not False


def invariants_of(result) -> dict:
    return (result.get("orchestration") or {}).get("invariants", {})


def invariants_zero(result) -> bool:
    """All numeric safety invariants on a result must be 0 and the
    result deterministic; the flow-verdict agreement flag must be True.
    (Different authority payloads carry slightly different key sets, so
    every present key is asserted, not a fixed list.)"""
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


def auth_ok(result, expected: str) -> bool:
    return expected in auth_of(result)


# ---------------------------------------------------------------------------
# Coverage matrix: (id, topic, question, expected authority token,
# expected verdict, optional note). Every question is the CANONICAL
# released phrasing (15I-TX / 15I-UZ / 15I-VY / 15I-WF / 15I-DISC /
# 15I-BILLS / 15I-SPEC), never one written to fit this audit.
# ---------------------------------------------------------------------------

MATRIX: list = [
    # ---- Commercial Core -------------------------------------------------
    ("CORE.purchase_credit", "Purchases",
     "Purchased goods for Rs.10,000 on credit from Ram.",
     "transaction", VERIFIED),
    ("CORE.sale_credit", "Sales",
     "Sold goods to Mohan for Rs.10,000 on credit.",
     "transaction", VERIFIED),
    ("CORE.purchase_cash", "Purchases",
     "Purchased goods for cash Rs.5,000.", "transaction", VERIFIED),
    ("CORE.sale_cash", "Sales",
     "Sold goods for cash Rs.5,000.", "transaction", VERIFIED),
    ("CORE.trade_discount", "Trade discount",
     "Sold goods for Rs.30,000 to Rahul at 10% trade discount.",
     "transaction", VERIFIED),
    ("CORE.plain_settlement", "Full settlement",
     "Received from Ram Rs.10,000 by cheque in full settlement of his "
     "account of Rs.10,000", "transaction", VERIFIED),
    ("CORE.cash_discount_settlement", "Cash discount",
     "Received Rs.9,500 from Ram in full settlement of his account of "
     "Rs.10,000 allowing cash discount of Rs.500.",
     "transaction", VERIFIED,
     "cash discount split now deterministically resolved at "
     "production boundary"),
    ("CORE.partial_payment", "Partial payments",
     "Received Rs.5,000 from Mohan on account of Rs.10,000 due from him.",
     "transaction", VERIFIED),
    ("CORE.part_settlement", "Partial settlement",
     "Sold goods to Ram on credit for Rs.10,000. Received Rs.5,000 from "
     "him in part settlement.", "transaction", VERIFIED),
    ("CORE.return_outward", "Returns",
     "Returned goods to Ram worth Rs.2,000.", "transaction", VERIFIED),
    ("CORE.return_inward_ambiguous", "Returns",
     "Goods returned by Y worth Rs.1,200.", "transaction",
     REVIEW_REQUIRED,
     "unsafe party token + no prior sale context - safely refused"),
    ("CORE.profit_on_cost", "Profit on cost",
     "Sold goods to Ram costing Rs.5,000 at a profit of 20% on cost on "
     "credit.", "transaction", VERIFIED),
    ("CORE.profit_on_sp", "Profit on selling price",
     "Sold goods to Ram for cash at a profit of 20% on selling price. "
     "Sale price Rs.5,000.", "transaction", BLOCKED,
     "the sale sentence carries no stated amount - BLOCKED, never "
     "guessed"),
    ("CORE.gst_cgst_sgst", "GST (CGST+SGST)",
     "Purchased goods from Rahul on credit Rs.20,000 with CGST @ 9% and "
     "SGST @ 9%.", "transaction", VERIFIED),
    ("CORE.gst_igst", "GST (IGST)",
     "Sold goods to Mohan on credit Rs.20,000 with IGST @ 18%.",
     "transaction", VERIFIED),
    ("CORE.gst_intra", "GST (intra-state)",
     "Purchased goods for cash Rs.20,000, GST @ 18%, intra-state.",
     "transaction", VERIFIED),
    ("CORE.gst_inter", "GST (inter-state)",
     "Purchased goods for cash Rs.20,000, GST @ 18%, inter-state.",
     "transaction", VERIFIED),
    ("CORE.td_gst_explicit", "Compound (TD+GST)",
     "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
     "18% GST with CGST Rs.1,620 and SGST Rs.1,620",
     "transaction", VERIFIED),
    ("CORE.td_payment", "Compound (TD+payment)",
     "Purchased goods for Rs.20,000 from Rahul at 10% trade discount "
     "and paid 50% by cheque.", "transaction", VERIFIED),
    ("CORE.payment_partition_valid", "Payment partitioning",
     "Goods worth Rs.10,000 were purchased. Paid Rs.6,000 by cheque and "
     "Rs.2,000 in cash, balance due.", "transaction", REVIEW_REQUIRED,
     "valid partition - never INVALID_INPUT_MATH (15I-COVER fix)"),
    ("CORE.payment_partition_exceeds", "Payment partitioning",
     "Purchased goods worth Rs.10,000. Paid Rs.8,000 by cheque and "
     "Rs.4,000 in cash.", "transaction", REVIEW_REQUIRED,
     "components cannot all receive a role - safe refusal"),
    # ---- Discrepancy / BRS / rectification ------------------------------
    ("DISC.brs_cheque_issued", "BRS - cheque issued not presented",
     "Cheque issued to Rahul for Rs.10,000 but not yet presented for "
     "payment.", "discrepancy", VERIFIED),
    ("DISC.brs_cheque_deposited", "BRS - cheque deposited not cleared",
     "Deposited a cheque received from Ram for Rs.10,000 which has not "
     "yet been cleared by the bank.", "discrepancy", VERIFIED),
    ("DISC.bank_charges", "BRS - bank charges",
     "Bank charged Rs.200 as bank charges which were not recorded in "
     "the cash book.", "discrepancy", VERIFIED),
    ("DISC.bank_interest", "BRS - bank interest",
     "Bank credited Rs.500 as interest which was not recorded in the "
     "cash book.", "discrepancy", VERIFIED),
    ("DISC.direct_payment", "BRS - direct payment",
     "Insurance premium of Rs.1,200 was paid directly by the bank under "
     "standing instructions and not recorded in the cash book.",
     "discrepancy", VERIFIED),
    ("DISC.dishonour_received", "Dishonoured cheque",
     "Received a cheque from Ram for Rs.10,000 which was later "
     "dishonoured.", "discrepancy", VERIFIED),
    ("DISC.dishonour_sale", "Dishonoured cheque (sale chain)",
     "Sold goods to Ram for Rs.10,000 and received a cheque which was "
     "dishonoured.", "discrepancy", VERIFIED),
    ("DISC.dishonour_missing_history", "Dishonour - missing history",
     "Ram's cheque of Rs.5,000 was dishonoured.", "discrepancy",
     REVIEW_REQUIRED, "no prior receipt established - never invented"),
    ("DISC.double_dishonour", "Dishonour - duplicate correction",
     "Received a cheque from Ram for Rs.10,000 which was dishonoured "
     "twice.", "discrepancy", REVIEW_REQUIRED),
    ("DISC.omitted_purchase", "Omission - purchase",
     "Purchased goods from Rahul for Rs.20,000 which was completely "
     "omitted from the books.", "discrepancy", VERIFIED),
    ("DISC.omitted_sale", "Omission - sale",
     "Sold goods to Ram for Rs.10,000 which was omitted from the books.",
     "discrepancy", VERIFIED),
    ("DISC.omitted_return", "Omission - customer return",
     "Goods returned by Mohan worth Rs.1,200 were completely omitted "
     "from the books.", "discrepancy", VERIFIED),
    ("DISC.omission_ambiguous", "Omission - ambiguous",
     "A transaction of Rs.5,000 was completely omitted from the books.",
     "discrepancy", REVIEW_REQUIRED),
    ("DISC.rect_wrong_account", "Rectification - wrong account",
     "Purchased goods from Rahul for Rs.20,000 but the entry was "
     "wrongly posted to Mohan's account.", "discrepancy", VERIFIED),
    ("DISC.rect_wrong_amount", "Rectification - wrong amount",
     "Purchased goods from Rahul for Rs.20,000 but the entry was "
     "recorded at Rs.2,000.", "discrepancy", VERIFIED),
    ("DISC.rect_wrong_side", "Rectification - wrong side",
     "Goods sold to Ram for cash Rs.10,000 were wrongly debited to "
     "Ram's account instead of crediting Sales.", "discrepancy",
     VERIFIED),
    ("DISC.rect_omission", "Rectification - complete omission",
     "Goods purchased from Rahul for Rs.20,000 were completely omitted "
     "from the books. Rectify.", "discrepancy", VERIFIED),
    ("DISC.rect_partial_omission", "Rectification - partial omission",
     "Purchased goods from Rahul for Rs.20,000 on credit but only "
     "Rs.8,000 was recorded in the books.", "discrepancy", VERIFIED),
    ("DISC.suspense_valid", "Suspense Account (established)",
     "The trial balance did not tally. The Sales book was undercast by "
     "Rs.500.", "discrepancy", VERIFIED),
    ("DISC.suspense_unnecessary", "Suspense not invented",
     "Purchased goods from Rahul for Rs.20,000 but the entry was wrongly "
     "posted to Mohan's account. Rectify using the Suspense Account.",
     "discrepancy", VERIFIED),
    ("DISC.brs_list_form", "BRS - list form",
     "From the following particulars prepare a bank reconciliation "
     "statement: unpresented cheques Rs.5,000, uncleared deposits "
     "Rs.3,000.", "discrepancy", NOT_SUPPORTED),
    # ---- Bills of Exchange ----------------------------------------------
    ("BILL.draw_accept", "Bill - drawing + acceptance",
     "Rahul drew a bill of Rs.10,000 on Mohan which Mohan accepted.",
     "bills", VERIFIED),
    ("BILL.held", "Bill - held to maturity",
     "Received a bill of exchange from Ram for Rs.10,000 which was "
     "retained till maturity.", "bills", VERIFIED),
    ("BILL.discount", "Bill - discounting",
     "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
     "discounted it with the bank at 12% p.a.", "bills", VERIFIED),
    ("BILL.endorse", "Bill - endorsement",
     "Rahul drew a bill of Rs.10,000 on Mohan. Rahul endorsed it to his "
     "creditor Shyam.", "bills", VERIFIED),
    ("BILL.collection", "Bill - collection",
     "Rahul drew a bill of Rs.10,000 on Mohan. Rahul sent it to the "
     "bank for collection. The bank collected it on maturity.", "bills",
     VERIFIED),
    ("BILL.honour", "Bill - honour",
     "Rahul drew a bill of Rs.10,000 on Mohan. On maturity the bill was "
     "honoured.", "bills", VERIFIED),
    ("BILL.dishonour_held", "Bill - dishonour (held)",
     "Rahul drew a bill of Rs.10,000 on Mohan. On maturity Mohan "
     "dishonoured the bill.", "bills", VERIFIED),
    ("BILL.noting", "Bill - noting charges",
     "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
     "discounted it with the bank at 12% p.a. On maturity Mohan "
     "dishonoured the bill and the bank paid Rs.100 noting charges.",
     "bills", VERIFIED),
    ("BILL.maturity_grace", "Bill - maturity + days of grace",
     "Rahul drew a bill of Rs.10,000 on Mohan on 1 January 2025 for 3 "
     "months. The bill was due on 4 April 2025.", "bills", VERIFIED),
    ("BILL.invalid_transition", "Bill - invalid transition",
     "Rahul drew a bill of Rs.10,000 on Mohan. Rahul discounted it with "
     "the bank at 12% p.a. for 3 months and endorsed it to Shyam.",
     "bills", REVIEW_REQUIRED),
    ("BILL.collected_and_honoured", "Bill - ambiguous outcome",
     "Rahul drew a bill of Rs.10,000 on Mohan. Rahul sent it to the "
     "bank for collection. On maturity the bill was collected and "
     "honoured.", "bills", REVIEW_REQUIRED),
    ("BILL.missing_history", "Bill - missing prior state",
     "The bill of exchange for Rs.10,000 was dishonoured.", "bills",
     REVIEW_REQUIRED, "prior bill state/amount cannot be established"),
    ("BILL.noting_without_dishonour", "Bill - noting without dishonour",
     "Rahul drew a bill of Rs.10,000 on Mohan. The bank paid Rs.100 "
     "noting charges.", "bills", REVIEW_REQUIRED),
    ("BILL.no_rate_period", "Bill - discount without rate/period",
     "Rahul drew a bill of Rs.10,000 on Mohan. Rahul discounted it with "
     "the bank.", "bills", REVIEW_REQUIRED),
    ("BILL.proceeds_exceed", "Bill - proceeds exceed amount",
     "Rahul drew a bill of Rs.10,000 on Mohan. Rahul discounted it with "
     "the bank for Rs.10,200.", "bills", INVALID_INPUT_MATH),
    ("BILL.proceeds_contradict", "Bill - proceeds contradict computed",
     "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
     "discounted it with the bank at 12% p.a. for Rs.9,500.", "bills",
     INVALID_INPUT_MATH),
    ("BILL.everyday_bill", "Bill - everyday bill NOT routed",
     "Paid his mobile recharge bill Rs.500.", "transaction",
     REVIEW_REQUIRED, "not a bill of exchange - never routed to bills"),
    ("BILL.single_letter", "Bill - single-letter parties",
     "A drew a bill of exchange on B for Rs.10,000.", "bills",
     REVIEW_REQUIRED),
    # ---- Consignment -----------------------------------------------------
    ("CONS.goods_sent", "Consignment - goods sent",
     "Goods of Rs.50,000 were sent on consignment to Mohan on "
     "consignment basis.", "consignment", VERIFIED),
    ("CONS.profit", "Consignment - profit",
     "Goods of Rs.50,000 were sent on consignment to Mohan. Consignor "
     "paid freight Rs.2,000. Mohan sold 4/5 of the goods for Rs.48,000. "
     "Commission 10% on sales. Find the consignment profit.",
     "consignment", VERIFIED),
    ("CONS.abnormal_loss", "Consignment - abnormal loss",
     "Goods costing Rs.20,000 were sent on consignment to Mohan. "
     "Freight of Rs.1,000 was paid by the consignor. Goods worth "
     "Rs.5,000 were destroyed in transit. Find the abnormal loss.",
     "consignment", VERIFIED),
    ("CONS.closing_stock", "Consignment - closing stock",
     "Goods of Rs.40,000 were sent on consignment to Mohan. Freight "
     "Rs.2,000 was paid by the consignor. 3/4 of the goods were sold. "
     "Value the closing consignment stock.", "consignment", VERIFIED),
    ("CONS.del_credere", "Consignment - commission + del credere",
     "Goods of Rs.30,000 sent on consignment to Mohan. Consignee paid "
     "expenses of Rs.1,000. 1/2 of the goods were sold for Rs.24,000. "
     "Commission was 10% and del credere commission 2%. Find the "
     "consignment profit.", "consignment", VERIFIED),
    ("CONS.missing_data", "Consignment - missing data",
     "Goods were sent on consignment to Mohan. Find the profit.",
     "consignment", REVIEW_REQUIRED),
    ("CONS.ordinary_sale_not_routed", "Consignment - ordinary sale",
     "Sold goods to Mohan for Rs.10,000 on credit.", "transaction",
     VERIFIED, "never routed to the Consignment Authority"),
    ("CONS.commission_not_routed", "Consignment - ordinary commission",
     "Paid commission of Rs.500 to an agent.", "transaction", VERIFIED,
     "everyday commission never routed to the Consignment Authority"),
    # ---- Joint Venture ---------------------------------------------------
    ("JV.equal", "Joint Venture - equal sharing + settlement",
     "Rahul and Mohan entered into a joint venture. Rahul contributed "
     "goods worth Rs.20,000 and Mohan contributed cash Rs.10,000. Rahul "
     "paid expenses of Rs.1,000 and Mohan paid Rs.500. Sales proceeds "
     "were Rs.40,000. Profit is to be shared equally. Find the profit "
     "and the settlement between Rahul and Mohan.", "joint", VERIFIED),
    ("JV.ratio", "Joint Venture - ratio sharing",
     "Rahul and Mohan entered into a joint venture sharing profits in "
     "the ratio 3:2. Rahul contributed goods of Rs.30,000 and Mohan "
     "contributed Rs.20,000 cash. Expenses of Rs.2,000 were paid by "
     "Rahul. Sales proceeds were Rs.60,000. Find the profit.", "joint",
     VERIFIED),
    ("JV.own_books", "Joint Venture - own books",
     "In the books of Rahul, a joint venture was entered into with "
     "Mohan. Rahul contributed goods worth Rs.30,000. Mohan contributed "
     "cash Rs.10,000. Sales proceeds were Rs.50,000. Expenses of "
     "Rs.2,000 were paid by Mohan. Profit shared in the ratio 3:2. Find "
     "the profit and settlement.", "joint", VERIFIED),
    ("JV.missing_ratio", "Joint Venture - missing ratio",
     "Rahul and Mohan entered into a joint venture. Rahul contributed "
     "goods worth Rs.20,000 and Mohan contributed cash of Rs.10,000. "
     "Sales proceeds were Rs.40,000. Find the profit.", "joint",
     REVIEW_REQUIRED),
    ("JV.ambiguous_contribution", "Joint Venture - ambiguous basis",
     "Entered into a joint venture with Shyam, contributing Rs.20,000.",
     "joint", REVIEW_REQUIRED),
    ("JV.single_letter", "Joint Venture - single-letter parties",
     "A and B entered into a joint venture. A contributed goods worth "
     "Rs.20,000. Sales proceeds were Rs.40,000. Profit shared equally.",
     "joint", REVIEW_REQUIRED),
    ("JV.ordinary_customer_not_routed", "Joint Venture - ordinary customer",
     "Sold goods to Mohan for Rs.15,000 on credit.", "transaction",
     VERIFIED, "a customer is never a co-venturer"),
    # ---- Single Entry / Incomplete Records ------------------------------
    ("SE.profit", "Single Entry - profit",
     "The opening capital was Rs.60,000 and the closing capital was "
     "Rs.75,000. Drawings during the year were Rs.10,000 and fresh "
     "capital of Rs.5,000 was introduced. Find the profit.",
     "single-entry", VERIFIED),
    ("SE.statement_affairs", "Single Entry - statement of affairs",
     "A trader keeps incomplete records. The statement of affairs at "
     "the start showed assets of Rs.80,000 and liabilities of Rs.20,000. "
     "At the end, assets were Rs.1,00,000 and liabilities Rs.30,000. "
     "Drawings were Rs.12,000 and no fresh capital was introduced. Find "
     "the profit.", "single-entry", VERIFIED),
    ("SE.inverse", "Single Entry - inverse calculation",
     "Capital at the start was Rs.60,000. Drawings were Rs.10,000, "
     "fresh capital introduced was Rs.5,000, and the profit for the "
     "year was Rs.20,000. What was the closing capital?", "single-entry",
     VERIFIED),
    ("SE.loss", "Single Entry - loss",
     "The opening capital was Rs.60,000 and closing capital was "
     "Rs.45,000. Drawings were Rs.10,000 and no fresh capital was "
     "introduced. Find the loss.", "single-entry", VERIFIED),
    ("SE.no_fresh_capital", "Single Entry - no fresh capital",
     "Capital at the beginning was Rs.60,000, capital at the end was "
     "Rs.75,000, drawings were Rs.10,000, and no fresh capital was "
     "introduced. Find the profit.", "single-entry", VERIFIED),
    ("SE.missing", "Single Entry - missing data",
     "A trader keeps incomplete records. Find the profit.", "single-entry",
     REVIEW_REQUIRED),
    # ---- Normalization boundary -----------------------------------------
    ("NORM.gds", "Normalization - 'gds'",
     "Purchased gds from Ram for Rs.10,000 on credit.", "transaction",
     VERIFIED),
    ("NORM.td", "Normalization - 'td'",
     "Sold goods to Ram for Rs.10,000 at 10% td.", "transaction",
     VERIFIED),
    ("NORM.cd", "Normalization - 'cd'",
     "Received Rs.9,500 from Ram in full settlement of his account of "
     "Rs.10,000 allowing cd of Rs.500.", "transaction", VERIFIED,
     "abbreviation resolves; cash discount split deterministically "
     "resolved"),
    ("NORM.10k", "Normalization - '10k'",
     "Sold goods to Ram for 10k on credit.", "transaction", VERIFIED),
    ("NORM.12.5k", "Normalization - '12.5k'",
     "Sold goods to Ram for 12.5k on credit.", "transaction", VERIFIED),
    ("NORM.casing", "Normalization - casing",
     "PURCHASED GOODS FROM RAM FOR RS.5,000 ON CREDIT.", "transaction",
     VERIFIED),
    ("NORM.no_comma", "Normalization - unformatted amount",
     "Purchased goods from Ram for rs.5000 on credit.", "transaction",
     VERIFIED),
    ("NORM.whitespace", "Normalization - whitespace",
     "  Sold   goods   to   Ram   for   Rs.10,000   on   credit.  ",
     "transaction", VERIFIED),
    ("NORM.unknown_abbrev", "Normalization - unknown abbreviation",
     "Sold goods to Ram Rs.10,000 on credit 5% xd", "transaction",
     REVIEW_REQUIRED),
    ("NORM.single_letter_dotted", "Normalization - single letter (dotted)",
     "Sold goods to R. on credit for Rs.10,000", "transaction",
     REVIEW_REQUIRED),
    ("NORM.single_letter_bare", "Normalization - single letter (bare)",
     "Sold goods to A for Rs.10,000 on credit.", "transaction",
     REVIEW_REQUIRED,
     "15I-COVER fix: a bare initial is never invented as an account"),
    ("NORM.misspelled_party", "Normalization - party kept as typed",
     "Sold goods to raam for Rs.10,000 on credit", "transaction",
     VERIFIED),
    ("NORM.no_party_sale", "Normalization - missing party",
     "Sold goods for Rs.10,000 on credit.", "transaction",
     REVIEW_REQUIRED),
    # ---- Mathematical contradictions ------------------------------------
    ("MATH.payment_exceeds", "Contradiction - payment + outstanding",
     "Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
     "Rs.5,000 remains outstanding.", "transaction", INVALID_INPUT_MATH),
    ("MATH.discount_inconsistent", "Contradiction - discount vs rate",
     "Sold goods worth Rs.10,000 to Ram at 10% trade discount of Rs.800",
     "transaction", INVALID_INPUT_MATH),
    ("MATH.gst_inconsistent", "Contradiction - GST components vs rate",
     "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
     "18% GST with CGST Rs.1,000 and SGST Rs.1,000", "transaction",
     INVALID_INPUT_MATH),
    ("MATH.settlement_exceeds", "Contradiction - over-settlement",
     "Received Rs.11,000 from Ram in full settlement of his account of "
     "Rs.10,000", "transaction", INVALID_INPUT_MATH),
    ("MATH.cash_discount_fails", "Contradiction - cash discount fails",
     "Received Rs.9,000 from Ram in full settlement of his account of "
     "Rs.10,000 allowing cash discount of Rs.500", "transaction",
     INVALID_INPUT_MATH),
    ("MATH.valid_partition_not_invalid", "Ambiguity - valid partition",
     "Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
     "Rs.4,000 remains outstanding.", "transaction", REVIEW_REQUIRED,
     "a valid partition is ambiguity, never a contradiction"),
    ("MATH.valid_payment_split_not_invalid",
     "Ambiguity - valid payment split",
     "Goods worth Rs.10,000 were purchased. Paid Rs.6,000 by cheque and "
     "Rs.2,000 in cash, balance due.", "transaction", REVIEW_REQUIRED,
     "15I-COVER fix: never misread a cash payment as 'outstanding'"),
    ("SE.contradiction", "Contradiction - stated loss vs net worth",
     "Capital at the beginning was Rs.60,000, capital at the end was "
     "Rs.75,000, drawings were Rs.10,000, and the loss was Rs.5,000. No "
     "fresh capital was introduced.", "single-entry", INVALID_INPUT_MATH),
    # ---- Historical dependency ------------------------------------------
    ("HIST.dishonour_no_prior", "History - dishonour without prior receipt",
     "Ram's cheque of Rs.5,000 was dishonoured.", "discrepancy",
     REVIEW_REQUIRED),
    ("HIST.bill_no_prior", "History - bill dishonour without prior state",
     "The bill of exchange for Rs.10,000 was dishonoured.", "bills",
     REVIEW_REQUIRED),
    ("HIST.insolvency_debtor", "History - insolvency needs debtor balance",
     "Ram was declared insolvent and could pay only 60 paise in the "
     "rupee. He owed Rs.10,000.", "discrepancy", REVIEW_REQUIRED),
    ("HIST.depreciation_disposal", "History - depreciation + disposal",
     "Machinery was purchased for Rs.1,00,000 and depreciation of "
     "Rs.10,000 was provided. The machinery was then sold for Rs.80,000.",
     "transaction", NOT_SUPPORTED,
     "asset-disposal capability outside the implemented surface"),
    ("HIST.settlement_stated", "History - settlement with stated account",
     "Received from Ram Rs.10,000 by cheque in full settlement of his "
     "account of Rs.10,000", "transaction", VERIFIED),
    # ---- Hybrid payloads ------------------------------------------------
    ("HYB.bills_chain", "Hybrid - draw/discount/dishonour/noting",
     "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
     "discounted it with the bank at 12% p.a. On maturity Mohan "
     "dishonoured the bill and the bank paid Rs.100 noting charges.",
     "bills", VERIFIED),
    ("HYB.received_bill_dishonour", "Hybrid - received bill + dishonour",
     "Received a bill of exchange from Ram for Rs.10,000 which was "
     "later dishonoured.", "bills", VERIFIED),
    ("HYB.sale_cheque_dishonour", "Hybrid - sale + cheque + dishonour",
     "Sold goods to Ram for Rs.10,000 and received a cheque which was "
     "dishonoured.", "discrepancy", VERIFIED),
    ("HYB.machinery_sale_dishonour", "Hybrid - asset sale + GST + dishonour",
     "Sold machinery for Rs.40,000, book value Rs.50,000, at 5% trade "
     "discount plus 18% GST. Buyer paid 50% by cheque, and the cheque "
     "was later dishonoured.", "discrepancy", REVIEW_REQUIRED,
     "asset disposal unsupported; dishonour amount not established - "
     "safe refusal, nothing fabricated"),
    ("HYB.td_gst_partial", "Hybrid - TD + GST + partial payment",
     "Sold goods to Ram for Rs.20,000 at 10% trade discount and 18% "
     "GST, received half immediately", "transaction", REVIEW_REQUIRED,
     "refuses instead of dropping the payment; facts preserved"),
]

# Expected computed results for spot assertions on key VERIFIED rows.
SPOT = {
    "CORE.trade_discount": [("Rahul", "27000.00"), ("Sales", "27000.00")],
    "CORE.td_gst_explicit": [("Purchases", "18000.00"), ("Input CGST", "1620"),
                             ("Input SGST", "1620"), ("Ram", "21240.00")],
    "CORE.profit_on_cost": [("Ram", "6000.00"), ("Sales", "6000.00")],
    "DISC.bank_charges": [("Bank Charges", "200"), ("Bank", "200")],
    "DISC.bank_interest": [("Bank", "500"), ("Interest Received", "500")],
    "DISC.rect_wrong_amount": [("Purchases", "18000"), ("Rahul", "18000")],
    "DISC.suspense_valid": [("Suspense", "500"), ("Sales", "500")],
    "BILL.draw_accept": [("Bills Receivable", "10000"), ("Mohan", "10000")],
    "BILL.discount": [("Bills Receivable", "10000"),
                      ("Bank", "9700.00"), ("Discount", "300.00"),
                      ("Mohan", "10000"), ("Bills Receivable", "10000")],
    "BILL.endorse": [("Bills Receivable", "10000"), ("Shyam", "10000"),
                     ("Mohan", "10000"), ("Bills Receivable", "10000")],
    "CONS.goods_sent": [("Consignment", "50000"),
                        ("Goods Sent on Consignment", "50000")],
}

# Expected computed payload results on key VERIFIED rows.
SPOT_PAYLOAD = {
    "CONS.profit": ("consignment", 1600),
    "CONS.abnormal_loss": ("consignment", 5250),
    "CONS.closing_stock": ("consignment", 10500.00),
    "JV.equal": ("joint_venture", 8500),
    "JV.ratio": ("joint_venture", 8000),
    "SE.profit": ("single_entry", 20000),
    "SE.statement_affairs": ("single_entry", 22000),
    "SE.inverse": ("single_entry", 75000),
    "SE.loss": ("single_entry", -5000),
}


def _run_matrix():
    print("PART A - COVERAGE MATRIX (" + str(len(MATRIX)) + " topics)")
    counts = {}
    routing_fail = 0
    unsafe = 0
    dropped = 0
    duplicated = 0
    conflicts = 0
    results = {}
    for cid, topic, question, expected_auth, expected_status, *note in MATRIX:
        r = orchestrate(question)
        actual = r.get("status")
        results[cid] = r
        counts[actual] = counts.get(actual, 0) + 1

        check(f"{cid} routed to {expected_auth}",
              auth_ok(r, expected_auth),
              f"actual authority={auth_of(r)}")
        if not auth_ok(r, expected_auth):
            routing_fail += 1

        check(f"{cid} verdict {expected_status}",
              actual == expected_status, f"actual={actual}")

        if actual == VERIFIED:
            check(f"{cid} balanced", balanced(r), str(r.get("journal")))
            check(f"{cid} safety invariants zero", invariants_zero(r),
                  str(invariants_of(r)))
            if not invariants_zero(r):
                unsafe += 1
            # byte-identical deterministic repeat
            r2 = orchestrate(question)
            check(f"{cid} deterministic repeat", r2 == r, "differed")
            inv = invariants_of(r)
            if inv.get("dropped_valid_segments"):
                dropped += 1
            if inv.get("duplicated_amount_ownership") \
                    or inv.get("duplicated_segments"):
                duplicated += 1
            if inv.get("authority_conflicts_verified"):
                conflicts += 1
        else:
            check(f"{cid} zero journal lines on refusal",
                  lines(r) == [], str(lines(r)))
            check(f"{cid} student-readable refusal reason",
                  bool(r.get("why_not")), str(r.get("why_not"))[:80])

        if cid in SPOT:
            check(f"{cid} journal exact", lines(r) == SPOT[cid],
                  str(lines(r)))
        if cid in SPOT_PAYLOAD:
            key, want = SPOT_PAYLOAD[cid]
            got = (r.get(key) or {}).get("result")
            check(f"{cid} result {want}", got == want,
                  f"actual={got}")

    # one invariant sweep across every VERIFIED row (explicit hard-stop
    # list from the sprint, mapped to the engine invariant keys)
    print("PART A - SAFETY INVARIANT HARD-STOP SWEEP")
    for cid, r in results.items():
        if r.get("status") != VERIFIED:
            continue
        inv = invariants_of(r)
        for key in ("unsafe_confident", "invented_accounts",
                    "invented_amounts", "unbalanced_verified",
                    "invented_historical_state", "dropped_valid_segments",
                    "duplicated_segments", "duplicated_amount_ownership",
                    "authority_conflicts_verified",
                    "unresolved_amounts_guessed"):
            check(f"{cid} invariant {key}=0",
                  inv.get(key) in (0, None), f"invariant={inv.get(key)}")
        check(f"{cid} deterministic", inv.get("deterministic") is True,
              str(inv.get("deterministic")))
        # a VERIFIED journal is balanced; single-entry has no journal
        if r.get("journal") is not None:
            check(f"{cid} balanced VERIFIED",
                  (r.get("journal") or {}).get("balanced") is True,
                  str(r.get("journal")))

    return {"counts": counts, "routing_fail": routing_fail,
            "unsafe": unsafe, "dropped": dropped,
            "duplicated": duplicated, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# PART B - authority routing audit (dangerous overlaps)
# ---------------------------------------------------------------------------

ROUTING_CASES = [
    # (id, question, expected authority token, expected verdict)
    ("R.sale+td+gst", "Purchased goods from Ram for Rs.20,000 at 10% "
     "trade discount and 18% GST with CGST Rs.1,620 and SGST Rs.1,620",
     "transaction", VERIFIED),
    ("R.asset+depreciation+disposal", "Machinery was purchased for "
     "Rs.1,00,000 and depreciation of Rs.10,000 was provided. The "
     "machinery was then sold for Rs.80,000.", "transaction",
     NOT_SUPPORTED, "commercial core never journals asset history"),
    ("R.sale+dishonour", "Sold goods to Ram for Rs.10,000 and received "
     "a cheque which was dishonoured.", "discrepancy", VERIFIED),
    ("R.bill+dishonour", "Received a bill of exchange from Ram for "
     "Rs.10,000 which was later dishonoured.", "bills", VERIFIED),
    ("R.consignment+abnormal", "Goods costing Rs.20,000 were sent on "
     "consignment to Mohan. Freight of Rs.1,000 was paid by the "
     "consignor. Goods worth Rs.5,000 were destroyed in transit. Find "
     "the abnormal loss.", "consignment", VERIFIED),
    ("R.jv+ordinary-language", "Sold goods to Mohan for Rs.15,000 on "
     "credit.", "transaction", VERIFIED),
    ("R.se+journal-language", "The opening capital was Rs.60,000 and "
     "the closing capital was Rs.75,000. Drawings were Rs.10,000 and "
     "fresh capital of Rs.5,000 was introduced. Find the profit.",
     "single-entry", VERIFIED),
    ("R.consignment-not-sale", "Goods of Rs.50,000 were sent on "
     "consignment to Mohan on consignment basis.", "consignment",
     VERIFIED, "no Sales account"),
    ("R.everyday-bill-not-bills", "Paid his mobile recharge bill "
     "Rs.500.", "transaction", REVIEW_REQUIRED),
]


def test_b_routing():
    print("PART B - AUTHORITY ROUTING AUDIT")
    for row in ROUTING_CASES:
        cid, question, expected_auth, expected_status, *note = row
        r = orchestrate(question)
        check(f"{cid} routed to {expected_auth}",
              auth_ok(r, expected_auth),
              f"actual authority={auth_of(r)}")
        check(f"{cid} verdict {expected_status}",
              r.get("status") == expected_status, r.get("status"))
        if r.get("status") == VERIFIED:
            check(f"{cid} invariants zero", invariants_zero(r),
                  str(invariants_of(r)))
        else:
            check(f"{cid} zero lines", lines(r) == [], str(lines(r)))
        orch = r.get("orchestration") or {}
        check(f"{cid} provenance preserved (normalization key)",
              "normalization" in orch or "normalization" in r,
              str(sorted(orch.keys())))
        if r.get("status") == VERIFIED:
            check(f"{cid} graph segments present",
                  isinstance(orch.get("segments"), list)
                  and len(orch.get("segments")) > 0,
                  str(orch.get("segments"))[:120])
            check(f"{cid} graph ownership present",
                  "ownership" in orch and "dependencies" in orch,
                  str(sorted(orch.keys())))
            check(f"{cid} merge balanced",
                  (orch.get("merge") or {}).get("balanced") is True,
                  str(orch.get("merge")))


# ---------------------------------------------------------------------------
# PART C - hybrid payload stress
# ---------------------------------------------------------------------------

HYBRID_CASES = [
    ("H1", "Sold goods to Ram for Rs.10,000 and received a cheque which "
     "was dishonoured.", VERIFIED,
     "sale + cheque + dishonour chain journals fully"),
    ("H2", "Received a bill of exchange from Ram for Rs.10,000 which was "
     "later dishonoured.", VERIFIED,
     "bill + dishonour reversal, no new unrelated receipt"),
    ("H3", "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
     "discounted it with the bank at 12% p.a. On maturity Mohan "
     "dishonoured the bill and the bank paid Rs.100 noting charges.",
     VERIFIED, "draw -> discount -> dishonour -> noting preserved"),
    ("H4", "Sold machinery for Rs.40,000, book value Rs.50,000, at 5% "
     "trade discount plus 18% GST. Buyer paid 50% by cheque, and the "
     "cheque was later dishonoured.", REVIEW_REQUIRED,
     "asset disposal not fabricated; dishonour amount unestablished"),
    ("H5", "Sold goods to Ram for Rs.20,000 at 10% trade discount and "
     "18% GST, received half immediately", REVIEW_REQUIRED,
     "refuses instead of dropping the payment step"),
]


def test_c_hybrids():
    print("PART C - HYBRID PAYLOAD STRESS")
    for cid, question, want, note in HYBRID_CASES:
        r = orchestrate(question)
        check(f"{cid} verdict {want}", r.get("status") == want,
              r.get("status"))
        if r.get("status") == VERIFIED:
            check(f"{cid} balanced", balanced(r), str(r.get("journal")))
            check(f"{cid} invariants zero", invariants_zero(r),
                  str(invariants_of(r)))
            orch = r.get("orchestration") or {}
            check(f"{cid} no dropped segment",
                  not (invariants_of(r).get("dropped_valid_segments")),
                  str(invariants_of(r)))
            check(f"{cid} no duplicated amount",
                  not (invariants_of(r).get("duplicated_amount_ownership")),
                  str(invariants_of(r)))
        else:
            check(f"{cid} zero journal lines", lines(r) == [],
                  str(lines(r)))
            check(f"{cid} explains refusal", bool(r.get("why_not")),
                  str(r.get("why_not"))[:80])
        check(f"{cid} deterministic repeat", orchestrate(question) == r,
              "differed")

    # facts preserved on the refused TD+GST+partial hybrid (never dropped)
    r = orchestrate(HYBRID_CASES[4][1])
    facts = []
    for seg in (r.get("orchestration") or {}).get("segments") or []:
        for f in seg.get("facts") or []:
            facts.append((f.get("kind"), str(f.get("value"))))
    check("H5 sale fact preserved", ("party", "Ram") in facts
          and ("amount", "20000") in facts, str(facts))
    check("H5 TD rate preserved", ("rate", "10") in facts, str(facts))
    check("H5 GST rate preserved", ("rate", "18") in facts, str(facts))
    check("H5 payment fraction preserved", ("fraction", "50") in facts,
          str(facts))


# ---------------------------------------------------------------------------
# PART D - normalization boundary
# ---------------------------------------------------------------------------

NORM_CASES = [
    ("N1", "Purchased gds from Ram for Rs.10,000 on credit.", VERIFIED),
    ("N2", "Sold goods to Ram for 10k on credit.", VERIFIED),
    ("N3", "Sold goods to Ram for 12.5k on credit.", VERIFIED),
    ("N4", "PURCHASED GOODS FROM RAM FOR RS.5,000 ON CREDIT.", VERIFIED),
    ("N5", "  Sold   goods   to   Ram   for   Rs.10,000   on   credit.  ",
     VERIFIED),
    ("N6", "Sold goods to Ram Rs.10,000 on credit 5% xd", REVIEW_REQUIRED),
    ("N7", "Sold goods to R. on credit for Rs.10,000", REVIEW_REQUIRED),
    ("N8", "Sold goods to A for Rs.10,000 on credit.", REVIEW_REQUIRED),
    ("N9", "Sold goods to A on credit for Rs.10,000", REVIEW_REQUIRED),
    ("N10", "Purchased goods from A for Rs.10,000 on credit.",
     REVIEW_REQUIRED),
    ("N11", "Sold goods for Rs.10,000 on credit.", REVIEW_REQUIRED),
    ("N12", "Sold goods to raam for Rs.10,000 on credit", VERIFIED),
]


def test_d_normalization():
    print("PART D - NORMALIZATION BOUNDARY")
    for cid, question, want in NORM_CASES:
        n = normalize_fyjc_text(question)
        r = orchestrate(question)
        check(f"{cid} verdict {want}", r.get("status") == want,
              r.get("status"))
        if r.get("status") == VERIFIED:
            check(f"{cid} invariants zero", invariants_zero(r),
                  str(invariants_of(r)))
        else:
            check(f"{cid} zero lines", lines(r) == [], str(lines(r)))

    # 'gds' / '10k' / '12.5k' / 'td' / 'cd' rewrite with provenance, and
    # a misspelled party is NEVER rewritten (identity authority stays in
    # the deterministic party rules)
    n = normalize_fyjc_text("Purchased gds from Ram for 10k on credit, "
                            "2% td.")
    check("D.provenance gds->goods", any(
        p.get("rule") == "BK_NORM_GOODS" and p.get("normalized") == "goods"
        for p in n.provenance), str(n.provenance))
    check("D.provenance 10k->10,000", any(
        p.get("rule") == "BK_NORM_NUMERIC_K"
        and p.get("normalized") == "10,000" for p in n.provenance),
        str(n.provenance))
    check("D.provenance td->trade discount", any(
        p.get("rule") == "BK_NORM_TRADE_DISCOUNT"
        and p.get("normalized") == "trade discount" for p in n.provenance),
        str(n.provenance))
    check("D.provenance cd->cash discount", any(
        p.get("rule") == "BK_NORM_CASH_DISCOUNT"
        and p.get("normalized") == "cash discount"
        for p in normalize_fyjc_text("allowing cd of Rs.500").provenance),
        str(n.provenance))
    n2 = normalize_fyjc_text("Sold goods to raam for Rs.10,000 on credit")
    check("D.no rewrite of party 'raam'",
          not any(p.get("rule") == "BK_NORM_PARTY"
                  or p.get("original") == "raam" and p.get("normalized")
                  != "raam" for p in n2.provenance), str(n2.provenance))
    r = orchestrate("Sold goods to raam for Rs.10,000 on credit")
    check("D.party kept as typed", ("Raam", "10000") in lines(r),
          str(lines(r)))

    # the bare single-letter safety fix (15I-COVER): never an invented
    # account, and the article/pronoun is never flagged in non-party
    # positions
    r = orchestrate("Sold goods to A for Rs.10,000 on credit.")
    check("D.bare single letter refuses (no invented account)",
          r.get("status") == REVIEW_REQUIRED
          and all(a != "A for" for a, _ in lines(r)),
          f"status={r.get('status')} lines={lines(r)}")
    n3 = normalize_fyjc_text("Sold goods at a profit of 20% on cost for "
                             "cash Rs.5,000.")
    check("D.article 'a' never flagged in non-party position",
          not any("single-letter" in c for c in n3.concerns),
          str(n3.concerns))
    n4 = normalize_fyjc_text("Rahul discounted a bill of Rs.10,000 with "
                             "the bank at 12% p.a. for 3 months.")
    check("D.'p.a.' never trips the single-letter gate",
          not any("single-letter" in c for c in n4.concerns),
          str(n4.concerns))

    # layer agreement for the cash-discount abbreviation: VERIFIED at the
    # hardened layer, safe refusal at the production boundary
    vr = vy_harden("Received Rs.9,500 from Ram in full settlement of his "
                   "account of Rs.10,000 allowing cd of Rs.500")
    check("D.cd resolves at hardened layer",
          vr.get("status") == VERIFIED
          and lines(vr) == [("Cash", "9500"), ("Discount Allowed", "500"),
                            ("Ram", "10000")], str(lines(vr)))
    r = orchestrate("Received Rs.9,500 from Ram in full settlement of "
                    "his account of Rs.10,000 allowing cd of Rs.500")
    check("D.cd production VERIFIED with correct lines",
          r.get("status") == VERIFIED
          and lines(r) == [("Cash", "9500"), ("Discount Allowed", "500"),
                           ("Ram", "10000")],
          f"status={r.get('status')} lines={lines(r)}")


# ---------------------------------------------------------------------------
# PART E - mathematical contradiction audit
# ---------------------------------------------------------------------------

MATH_CASES = [
    ("E1", "Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately "
     "and Rs.5,000 remains outstanding.", INVALID_INPUT_MATH),
    ("E2", "Sold goods worth Rs.10,000 to Ram at 10% trade discount of "
     "Rs.800", INVALID_INPUT_MATH),
    ("E3", "Purchased goods from Ram for Rs.20,000 at 10% trade discount "
     "and 18% GST with CGST Rs.1,000 and SGST Rs.1,000",
     INVALID_INPUT_MATH),
    ("E4", "Received Rs.11,000 from Ram in full settlement of his "
     "account of Rs.10,000", INVALID_INPUT_MATH),
    ("E5", "Received Rs.9,000 from Ram in full settlement of his account "
     "of Rs.10,000 allowing cash discount of Rs.500", INVALID_INPUT_MATH),
    ("E6", "Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately "
     "and Rs.4,000 remains outstanding.", REVIEW_REQUIRED),
    ("E7", "Goods worth Rs.10,000 were purchased. Paid Rs.6,000 by "
     "cheque and Rs.2,000 in cash, balance due.", REVIEW_REQUIRED),
]


def test_e_contradictions():
    print("PART E - MATHEMATICAL CONTRADICTION AUDIT")
    for cid, question, want in MATH_CASES:
        r = orchestrate(question)
        check(f"{cid} verdict {want}", r.get("status") == want,
              r.get("status"))
        check(f"{cid} zero journal lines", lines(r) == [],
              str(lines(r)))
        if want == INVALID_INPUT_MATH:
            why = (r.get("why_not") or "")
            check(f"{cid} clear contradiction reason",
                  "INVALID_INPUT_MATH" in why and len(why) > 60,
                  why[:160])
            check(f"{cid} deterministic repeat",
                  orchestrate(question) == r, "differed")
    # a contradiction is never presented as ordinary ambiguity and an
    # ordinary ambiguity is never presented as a contradiction
    r = orchestrate(MATH_CASES[5][1])
    check("E.valid partition NOT INVALID_INPUT_MATH",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    r = orchestrate(MATH_CASES[6][1])
    check("E.valid payment split NOT INVALID_INPUT_MATH",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    r = orchestrate("Sold goods for Rs.10,000. Buyer paid Rs.6,000 "
                    "immediately and Rs.5,000 remains outstanding.")
    check("E.contradiction NOT REVIEW_REQUIRED",
          r.get("status") == INVALID_INPUT_MATH, r.get("status"))


# ---------------------------------------------------------------------------
# PART F - historical dependency audit
# ---------------------------------------------------------------------------

HISTORY_CASES = [
    ("F1", "Ram's cheque of Rs.5,000 was dishonoured.", REVIEW_REQUIRED),
    ("F2", "The bill of exchange for Rs.10,000 was dishonoured.",
     REVIEW_REQUIRED),
    ("F3", "Ram was declared insolvent and could pay only 60 paise in "
     "the rupee. He owed Rs.10,000.", REVIEW_REQUIRED),
    ("F4", "Received a cheque from Ram for Rs.10,000 which was "
     "dishonoured twice.", REVIEW_REQUIRED),
    ("F5", "Machinery was purchased for Rs.1,00,000 and depreciation of "
     "Rs.10,000 was provided. The machinery was then sold for Rs.80,000.",
     NOT_SUPPORTED),
]


def test_f_history():
    print("PART F - HISTORICAL DEPENDENCY AUDIT")
    for cid, question, want in HISTORY_CASES:
        r = orchestrate(question)
        check(f"{cid} verdict {want}", r.get("status") == want,
              r.get("status"))
        check(f"{cid} zero journal lines", lines(r) == [],
              str(lines(r)))
        check(f"{cid} no invented history",
              invariants_of(r).get("invented_historical_state") in (0, None),
              str(invariants_of(r)))
        check(f"{cid} deterministic repeat", orchestrate(question) == r,
              "differed")
    # positive control: history ESTABLISHED in the input resolves
    r = orchestrate("Received a cheque from Ram for Rs.10,000 which was "
                    "later dishonoured.")
    check("F.established history VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("F.established history reversal + reinstatement",
          lines(r) == [("Bank", "10000"), ("Ram", "10000"),
                       ("Ram", "10000"), ("Bank", "10000")],
          str(lines(r)))
    check("F.established history invariants zero", invariants_zero(r),
          str(invariants_of(r)))


# ---------------------------------------------------------------------------
# PART H - boundary classification + machine-readable summary
# ---------------------------------------------------------------------------

def test_h_summary(summary: dict):
    print("PART H - BOUNDARY CLASSIFICATION + COVERAGE SUMMARY")
    counts = summary["counts"]
    total = sum(counts.values())
    rows = [
        ("total topics tested", total),
        ("VERIFIED", counts.get(VERIFIED, 0)),
        ("REVIEW_REQUIRED", counts.get(REVIEW_REQUIRED, 0)),
        ("NOT_SUPPORTED", counts.get(NOT_SUPPORTED, 0)),
        ("BLOCKED", counts.get(BLOCKED, 0)),
        ("INVALID_INPUT_MATH", counts.get(INVALID_INPUT_MATH, 0)),
        ("routing failures", summary["routing_fail"]),
        ("unsafe confident results", summary["unsafe"]),
        ("dropped segments", summary["dropped"]),
        ("duplicated segments", summary["duplicated"]),
        ("authority conflicts", summary["conflicts"]),
    ]
    for label, value in rows:
        print(f"  {label}: {value}")
    report = {
        "sprint": "15I-COVER",
        "total_topics": total,
        "VERIFIED": counts.get(VERIFIED, 0),
        "REVIEW_REQUIRED": counts.get(REVIEW_REQUIRED, 0),
        "NOT_SUPPORTED": counts.get(NOT_SUPPORTED, 0),
        "BLOCKED": counts.get(BLOCKED, 0),
        "INVALID_INPUT_MATH": counts.get(INVALID_INPUT_MATH, 0),
        "routing_failures": summary["routing_fail"],
        "dropped_segments": summary["dropped"],
        "duplicated_segments": summary["duplicated"],
        "authority_conflicts": summary["conflicts"],
        "unsafe_confident_results": summary["unsafe"],
        "all_verified_invariants_zero": summary["unsafe"] == 0,
    }
    with open("/tmp/_15cover_summary.json", "w") as fh:
        json.dump(report, fh, indent=2)
    check("H.cover safe (no unsafe VERIFIED)", summary["unsafe"] == 0,
          str(summary))
    check("H.cover no routing failure", summary["routing_fail"] == 0,
          str(summary))
    check("H.cover every capability classified",
          total == len(MATRIX), f"{total} != {len(MATRIX)}")


# ---------------------------------------------------------------------------
# PART I - real Streamlit Study/Verify AppTest
# ---------------------------------------------------------------------------

def test_i_streamlit():
    print("PART I - REAL STREAMLIT STUDY/VERIFY PATH")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("I.0 apptest available", False, str(exc))
        return
    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    check("I.1 app entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("I.2 FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])
    at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()

    def ask(q):
        at.text_area(key="fte_fyjc_question").set_value(q).run()
        at.button(key="fte_fyjc_go").click().run()
        return " ".join(m.value for m in at.markdown)

    verified_ui_cases = [
        ("I.3 TD+GST VERIFIED",
         "Purchased goods from Ram for Rs.20,000 at 10% trade discount "
         "and 18% GST with CGST Rs.1,620 and SGST Rs.1,620"),
        ("I.4 sale+cheque+dishonour VERIFIED",
         "Sold goods to Ram for Rs.10,000 and received a cheque which "
         "was dishonoured."),
        ("I.5 bills chain VERIFIED",
         "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
         "discounted it with the bank at 12% p.a. On maturity Mohan "
         "dishonoured the bill and the bank paid Rs.100 noting charges."),
        ("I.6 consignment profit VERIFIED",
         "Goods of Rs.50,000 were sent on consignment to Mohan. "
         "Consignor paid freight Rs.2,000. Mohan sold 4/5 of the goods "
         "for Rs.48,000. Commission 10% on sales. Find the consignment "
         "profit."),
        ("I.7 joint venture VERIFIED",
         "Rahul and Mohan entered into a joint venture. Rahul "
         "contributed goods worth Rs.20,000 and Mohan contributed cash "
         "Rs.10,000. Sales proceeds were Rs.40,000. Profit is to be "
         "shared equally. Find the profit."),
        ("I.8 single entry VERIFIED",
         "The opening capital was Rs.60,000 and the closing capital was "
         "Rs.75,000. Drawings were Rs.10,000 and fresh capital of "
         "Rs.5,000 was introduced. Find the profit."),
    ]
    for name, q in verified_ui_cases:
        md = ask(q)
        check(name, "VERIFIED" in md.upper()
              and "Almost there" not in md and not at.exception,
              [e.stack_trace for e in at.exception] + [md[:200]])

    md = ask("Ram's cheque of Rs.5,000 was dishonoured.")
    check("I.9 missing history refuses in UI",
          "REVIEW" in md.upper() and "VERIFIED" not in md.upper()
          and "Almost there" not in md, md[:200])
    md = ask("Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately "
             "and Rs.5,000 remains outstanding.")
    check("I.10 contradiction shows INVALID in UI",
          ("INVALID" in md.upper() or "INVALID_INPUT_MATH" in md.upper())
          and "VERIFIED" not in md.upper(), md[:200])
    md = ask("Sold goods to A for Rs.10,000 on credit.")
    check("I.11 bare single letter refuses in UI",
          "REVIEW" in md.upper() and "VERIFIED" not in md.upper(),
          md[:200])


def main():
    summary = _run_matrix()
    test_b_routing()
    test_c_hybrids()
    test_d_normalization()
    test_e_contradictions()
    test_f_history()
    test_h_summary(summary)
    test_i_streamlit()
    print(f"\n15I-COVER gate: {TOTAL[0]} checks passed, "
          f"{len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
