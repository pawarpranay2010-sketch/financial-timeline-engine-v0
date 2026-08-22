#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-SPEC - Specialized Accounting Authorities
scripts/fte_fyjc_15spec_specialized_authority_test.py

Locks in the Sprint 15I-SPEC Consignment / Joint Venture / Incomplete
Records (Single Entry) authorities:

  PART A - Consignment Authority
    * genuine consignment recognition; ordinary sales and everyday
      commission expenses are NEVER routed here
    * goods sent on consignment are booked to Consignment A/c /
      Goods Sent on Consignment A/c - NEVER an ordinary sale
    * consignment profit (sales + closing stock - goods - expenses -
      commission - del credere - abnormal loss)
    * abnormal-loss valuation with deterministic pro-rata non-recurring
      expenses (goods worth Rs.5,000 destroyed + Rs.250 share of
      freight)
    * closing consignment stock valuation (cost + pro-rata
      non-recurring expenses) as a valuation-only answer (no profit
      journal)
    * unsold fraction derived as the complement of the sold fraction
    * commission AND del credere rates, single 'X% del credere' rate
      never double-counted
    * missing goods cost / sales / rate refuses with zero journal lines

  PART B - Joint Venture Authority
    * explicit recognition; co-venturers are NEVER ordinary
      suppliers/customers
    * contributions (goods/cash), expenses by each co-venturer, sales,
      profit, profit-sharing ratios, own-books structure, settlement
    * missing profit-sharing ratio refuses REVIEW_REQUIRED
    * single-letter parties still refuse (15I-VY refusal preserved)

  PART C - Single Entry / Incomplete Records Authority
    * Profit = Closing Capital + Drawings - Fresh Capital - Opening
      Capital, and its inverses
    * statement-of-affairs capital = assets - liabilities
    * 'no fresh capital' is a deterministic ZERO, never a guess
    * a stated profit that contradicts the net-worth movement is
      INVALID_INPUT_MATH
    * VERIFIED mathematical result with ZERO journal lines (the topic
      does not require journal entries)

  PART D - Routing & amount ownership (one authority per segment,
    every stated amount has exactly one role)

  PART E - Contradiction state surfaced in the transaction graph

  PART F - Deterministic repeated execution (byte-identical)

  PART G - Safety invariant sweep on every VERIFIED result:
    unsafe_confident = 0, invented_accounts = 0, invented_amounts = 0,
    invented_history = 0, unbalanced_verified = 0, dropped_segments = 0,
    duplicated_segments = 0, authority_conflicts = 0,
    flow verdict == specialized-authority verdict.

  PART H - Real Streamlit Study/Verify AppTest for the released path.

Exit code 0 = all checks pass.
"""

import json
import os
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_consignment import (  # noqa: E402
    consignment_outcome,
    detect_consignment,
)
from backend.maths.fyjc_joint_venture import (  # noqa: E402
    detect_joint_venture,
    joint_venture_outcome,
)
from backend.maths.fyjc_single_entry import (  # noqa: E402
    detect_single_entry,
    single_entry_outcome,
)
from backend.maths.fyjc_orchestration import (  # noqa: E402
    orchestrate,
)
from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    INVALID_INPUT_MATH,
    NOT_SUPPORTED,
    REVIEW_REQUIRED,
)
from backend.maths.status import (  # noqa: E402
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
    orch = result.get("orchestration") or {}
    return orch.get("invariants", {})


def invariants_zero(result) -> bool:
    inv = invariants_of(result)
    return (all(inv.get(k, 0) == 0 for k in (
        "unsafe_confident",
        "dropped_valid_segments",
        "unresolved_amounts_guessed",
        "duplicated_amount_ownership",
        "authority_conflicts_verified",
        "invented_accounts",
        "invented_amounts",
        "unbalanced_verified",
        "invented_historical_state",
        "duplicated_segments",
    )) and inv.get("deterministic") is True)


def balanced(result) -> bool:
    j = result.get("journal") or {}
    return j.get("balanced") is not False


# ---------------------------------------------------------------------------
# PART A - Consignment Authority
# ---------------------------------------------------------------------------


def test_a_consignment():
    print("PART A - CONSIGNMENT AUTHORITY")

    # A.1 recognition: genuine consignment wording routes; ordinary
    # sales and everyday commission expenses never route.
    check("A.1 consignment recognised",
          detect_consignment(
              "Goods of Rs.50,000 were sent on consignment to Mohan.") is not None)
    check("A.2 del credere recognised",
          detect_consignment(
              "Consignee gets 2% del credere commission on sales.") is not None)
    check("A.3 ordinary sale not routed",
          detect_consignment(
              "Sold goods to Mohan for Rs.10,000 on credit.") is None)
    check("A.4 ordinary commission not routed",
          detect_consignment(
              "Paid commission of Rs.500 to an agent.") is None)

    r = orchestrate("Sold goods to Mohan for Rs.10,000 on credit.")
    check("A.5 ordinary sale stays VERIFIED commercial",
          r.get("status") == VERIFIED and "Sales" in [a for a, _ in lines(r)],
          str(lines(r)))
    r = orchestrate("Paid commission of Rs.500 to an agent.")
    check("A.6 ordinary commission stays VERIFIED commercial",
          r.get("status") == VERIFIED, str(lines(r)))

    # A.7 goods sent: Consignment A/c / Goods Sent on Consignment A/c -
    # NEVER an ordinary sale (no Sales account, no consignee customer
    # entry).
    r = orchestrate("Goods of Rs.50,000 were sent on consignment to "
                    "Mohan on consignment basis.")
    accounts = [a for a, _ in lines(r)]
    check("A.7 goods-sent journal (never a sale)",
          r.get("status") == VERIFIED
          and "Consignment" in accounts
          and "Goods Sent on Consignment" in accounts
          and "Sales" not in accounts,
          str(lines(r)))

    # A.8 consignment profit (C1): sales 48,000 + stock 10,400 -
    # goods 50,000 - freight 2,000 - commission 4,800 = 1,600.
    r = orchestrate("Goods of Rs.50,000 were sent on consignment to "
                    "Mohan. Consignor paid freight Rs.2,000. Mohan sold "
                    "4/5 of the goods for Rs.48,000. Commission 10% on "
                    "sales. Find the consignment profit.")
    cg = r.get("consignment") or {}
    check("A.8 consignment profit VERIFIED",
          r.get("status") == VERIFIED and cg.get("result") == 1600,
          f"status={r.get('status')} result={cg.get('result')}")
    check("A.8 stock valued at 10,400",
          any(c.get("kind") == "closing_stock"
              and c.get("value") == "10400.0"
              for c in cg.get("calculations") or []),
          str(cg.get("calculations")))
    check("A.8 commission 4,800",
          any(c.get("kind") == "commission"
              and c.get("value") == "4800"
              for c in cg.get("calculations") or []),
          str(cg.get("calculations")))
    check("A.8 journal balanced", balanced(r))

    # A.9 abnormal loss with pro-rata non-recurring expenses: 5,000 +
    # 5,000/20,000 x 1,000 = 5,250.
    r = orchestrate("Goods costing Rs.20,000 were sent on consignment to "
                    "Mohan. Freight of Rs.1,000 was paid by the "
                    "consignor. Goods worth Rs.5,000 were destroyed in "
                    "transit. Find the abnormal loss.")
    cg = r.get("consignment") or {}
    check("A.9 abnormal loss 5,250 (pro-rata freight)",
          r.get("status") == VERIFIED and cg.get("result") == 5250,
          f"status={r.get('status')} result={cg.get('result')}")
    check("A.9 Abnormal Loss account in journal",
          "Abnormal Loss" in [a for a, _ in lines(r)], str(lines(r)))
    check("A.9 journal balanced", balanced(r))

    # A.10 closing stock valuation only: (40,000 + 2,000) x 1/4 = 10,500
    # - a valuation answer, no profit/loss transfer journal.
    r = orchestrate("Goods of Rs.40,000 were sent on consignment to "
                    "Mohan. Freight Rs.2,000 was paid by the consignor. "
                    "3/4 of the goods were sold. Value the closing "
                    "consignment stock.")
    cg = r.get("consignment") or {}
    accounts = [a for a, _ in lines(r)]
    check("A.10 closing stock 10,500",
          r.get("status") == VERIFIED and cg.get("result") == 10500.00,
          f"status={r.get('status')} result={cg.get('result')}")
    check("A.10 no profit journal on valuation-only",
          "Profit on Consignment" not in accounts
          and "Loss on Consignment" not in accounts,
          str(accounts))
    check("A.10 journal balanced", balanced(r))

    # A.11 'X/Y remained unsold' form: (50,000 + 2,000) x 1/5 = 10,400.
    r = orchestrate("Goods of Rs.50,000 were sent on consignment to "
                    "Mohan. Freight of Rs.2,000 was paid by the "
                    "consignor. 1/5 of the goods remained unsold. Value "
                    "the unsold stock.")
    cg = r.get("consignment") or {}
    check("A.11 unsold-remained stock 10,400",
          r.get("status") == VERIFIED and cg.get("result") == 10400.0,
          f"status={r.get('status')} result={cg.get('result')}")

    # A.12 commission AND del credere rates: 10% + 2% -> profit 5,120.
    r = orchestrate("Goods of Rs.30,000 sent on consignment to Mohan. "
                    "Consignee paid expenses of Rs.1,000. 1/2 of the "
                    "goods were sold for Rs.24,000. Commission was 10% "
                    "and del credere commission 2%. Find the consignment "
                    "profit.")
    cg = r.get("consignment") or {}
    check("A.12 commission + del credere profit 5,120",
          r.get("status") == VERIFIED and cg.get("result") == 5120.0,
          f"status={r.get('status')} result={cg.get('result')}")

    # A.13 a single '5% del credere' rate is never double-counted.
    r = orchestrate("Goods of Rs.30,000 sent on consignment to Mohan. "
                    "1/2 of the goods were sold for Rs.24,000. "
                    "Commission was 5% del credere. Find the consignment "
                    "profit.")
    cg = r.get("consignment") or {}
    check("A.13 single del credere rate profit 7,800",
          r.get("status") == VERIFIED and cg.get("result") == 7800.0,
          f"status={r.get('status')} result={cg.get('result')}")

    # A.14 missing data refuses with zero journal lines.
    for name, q in [
            ("A.14a missing goods cost",
             "Goods were sent on consignment to Mohan. Find the profit."),
            ("A.14b missing sales/rate",
             "Goods of Rs.50,000 were sent on consignment to Mohan. "
             "Commission 10%. Find the consignment profit."),
    ]:
        r = orchestrate(q)
        check(name,
              r.get("status") == REVIEW_REQUIRED and lines(r) == [],
              f"status={r.get('status')} lines={lines(r)}")


# ---------------------------------------------------------------------------
# PART B - Joint Venture Authority
# ---------------------------------------------------------------------------


def test_b_joint_venture():
    print("PART B - JOINT VENTURE AUTHORITY")

    # B.1 recognition
    check("B.1 JV recognised",
          detect_joint_venture(
              "Rahul and Mohan entered into a joint venture.") is not None)
    check("B.2 co-venturer recognised",
          detect_joint_venture(
              "Entered into a joint venture with Shyam.") is not None)
    check("B.3 ordinary customer not routed",
          detect_joint_venture(
              "Sold goods to Mohan for Rs.10,000 on credit.") is None)

    # B.4 equal sharing + full settlement (J1): profit 40,000 - 20,000
    # - 10,000 - 1,000 - 500 = 8,500; Mohan's share 4,250; settlement =
    # 10,000 cash + 500 expenses + 4,250 share = 14,750.
    r = orchestrate("Rahul and Mohan entered into a joint venture. Rahul "
                    "contributed goods worth Rs.20,000 and Mohan "
                    "contributed cash Rs.10,000. Rahul paid expenses of "
                    "Rs.1,000 and Mohan paid Rs.500. Sales proceeds were "
                    "Rs.40,000. Profit is to be shared equally. Find the "
                    "profit and the settlement between Rahul and Mohan.")
    jv = r.get("joint_venture") or {}
    calcs = [(c.get("label"), c.get("value"))
             for c in jv.get("calculations") or []]
    check("B.4 JV profit 8,500",
          r.get("status") == VERIFIED and jv.get("result") == 8500,
          f"status={r.get('status')} result={jv.get('result')}")
    check("B.4 equal share 4,250",
          any(label == "Co-venturer's share of profit"
              and value == "4250" for label, value in calcs),
          str(calcs))
    check("B.4 settlement entry books 14,750 to Mohan",
          any(a == "Mohan" and amt == "14750"
              for a, amt in lines(r)),
          str(lines(r)))
    check("B.4 journal balanced", balanced(r))

    # B.5 ratio 3:2 (J2): profit 60,000 - 30,000 - 20,000 - 2,000 =
    # 8,000; Mohan's share 3,200 (2/5).
    r = orchestrate("Rahul and Mohan entered into a joint venture sharing "
                    "profits in the ratio 3:2. Rahul contributed goods of "
                    "Rs.30,000 and Mohan contributed Rs.20,000 cash. "
                    "Expenses of Rs.2,000 were paid by Rahul. Sales "
                    "proceeds were Rs.60,000. Find the profit.")
    jv = r.get("joint_venture") or {}
    calcs = [(c.get("label"), c.get("value"))
             for c in jv.get("calculations") or []]
    check("B.5 JV profit 8,000 / share 3,200",
          r.get("status") == VERIFIED and jv.get("result") == 8000
          and any(label == "Co-venturer's share of profit"
                  and value == "3200" for label, value in calcs),
          f"result={jv.get('result')} calcs={calcs}")
    check("B.5 journal balanced", balanced(r))

    # B.6 own-books structure ('in the books of Rahul ... entered into
    # with Mohan').
    r = orchestrate("In the books of Rahul, a joint venture was entered "
                    "into with Mohan. Rahul contributed goods worth "
                    "Rs.30,000. Mohan contributed cash Rs.10,000. Sales "
                    "proceeds were Rs.50,000. Expenses of Rs.2,000 were "
                    "paid by Mohan. Profit shared in the ratio 3:2. Find "
                    "the profit and settlement.")
    jv = r.get("joint_venture") or {}
    check("B.6 own-books JV profit 8,000",
          r.get("status") == VERIFIED and jv.get("result") == 8000,
          f"status={r.get('status')} result={jv.get('result')}")
    check("B.6 settlement books 15,200 to Mohan",
          any(a == "Mohan" and amt == "15200"
              for a, amt in lines(r)),
          str(lines(r)))
    check("B.6 journal balanced", balanced(r))

    # B.7 missing profit-sharing ratio refuses (never invented).
    r = orchestrate("Rahul and Mohan entered into a joint venture. Rahul "
                    "contributed goods worth Rs.20,000 and Mohan "
                    "contributed cash of Rs.10,000. Sales proceeds were "
                    "Rs.40,000. Find the profit.")
    check("B.7 missing ratio refuses",
          r.get("status") == REVIEW_REQUIRED and lines(r) == [],
          f"status={r.get('status')} lines={lines(r)}")

    # B.8 single-letter parties still refuse (15I-VY preserved).
    r = orchestrate("A and B entered into a joint venture. A contributed "
                    "goods worth Rs.20,000. Sales proceeds were "
                    "Rs.40,000. Profit shared equally.")
    check("B.8 single-letter parties refuse",
          r.get("status") == REVIEW_REQUIRED and lines(r) == [],
          f"status={r.get('status')}")

    # B.9 an ordinary customer sale never routes to the JV authority.
    r = orchestrate("Sold goods to Mohan for Rs.15,000 on credit.")
    check("B.9 ordinary customer sale VERIFIED commercial",
          r.get("status") == VERIFIED
          and "Sales" in [a for a, _ in lines(r)],
          str(lines(r)))


# ---------------------------------------------------------------------------
# PART C - Single Entry / Incomplete Records Authority
# ---------------------------------------------------------------------------


def test_c_single_entry():
    print("PART C - SINGLE ENTRY / INCOMPLETE RECORDS")

    # C.1 recognition
    check("C.1 incomplete records recognised",
          detect_single_entry(
              "A trader keeps incomplete records. Find the profit.") is not None)
    check("C.2 opening+closing capital recognised",
          detect_single_entry(
              "The opening capital was Rs.60,000 and the closing capital "
              "was Rs.75,000. Find the profit.") is not None)

    # C.3 profit: 75,000 + 10,000 - 5,000 - 60,000 = 20,000 - VERIFIED
    # with ZERO journal lines (the topic needs no journal entry).
    r = orchestrate("The opening capital was Rs.60,000 and the closing "
                    "capital was Rs.75,000. Drawings during the year were "
                    "Rs.10,000 and fresh capital of Rs.5,000 was "
                    "introduced. Find the profit.")
    se = r.get("single_entry") or {}
    check("C.3 profit 20,000 VERIFIED",
          r.get("status") == VERIFIED and se.get("result") == 20000,
          f"status={r.get('status')} result={se.get('result')}")
    check("C.3 zero journal lines",
          lines(r) == [] and r.get("journal") is None, str(lines(r)))

    # C.4 statement of affairs: opening 80,000 - 20,000 = 60,000;
    # closing 100,000 - 30,000 = 70,000; profit = 70,000 + 12,000 - 0 -
    # 60,000 = 22,000.
    r = orchestrate("A trader keeps incomplete records. The statement of "
                    "affairs at the start showed assets of Rs.80,000 and "
                    "liabilities of Rs.20,000. At the end, assets were "
                    "Rs.1,00,000 and liabilities Rs.30,000. Drawings were "
                    "Rs.12,000 and no fresh capital was introduced. Find "
                    "the profit.")
    se = r.get("single_entry") or {}
    check("C.4 statement of affairs profit 22,000",
          r.get("status") == VERIFIED and se.get("result") == 22000,
          f"status={r.get('status')} result={se.get('result')}")

    # C.5 inverse: closing capital = 60,000 + 5,000 + 20,000 - 10,000
    # = 75,000.
    r = orchestrate("Capital at the start was Rs.60,000. Drawings were "
                    "Rs.10,000, fresh capital introduced was Rs.5,000, "
                    "and the profit for the year was Rs.20,000. What was "
                    "the closing capital?")
    se = r.get("single_entry") or {}
    check("C.5 inverse closing capital 75,000",
          r.get("status") == VERIFIED and se.get("result") == 75000
          and se.get("solved_for") == "closing capital",
          f"status={r.get('status')} result={se.get('result')}")

    # C.6 loss direction: 45,000 + 10,000 - 0 - 60,000 = -5,000.
    r = orchestrate("The opening capital was Rs.60,000 and closing "
                    "capital was Rs.45,000. Drawings were Rs.10,000 and "
                    "no fresh capital was introduced. Find the loss.")
    se = r.get("single_entry") or {}
    check("C.6 loss -5,000 direction loss",
          r.get("status") == VERIFIED and se.get("result") == -5000
          and se.get("direction") == "loss",
          f"status={r.get('status')} result={se.get('result')}")

    # C.7 'no fresh capital' is a deterministic ZERO: profit = 75,000 +
    # 10,000 - 0 - 60,000 = 25,000.
    r = orchestrate("Capital at the beginning was Rs.60,000, capital at "
                    "the end was Rs.75,000, drawings were Rs.10,000, and "
                    "no fresh capital was introduced. Find the profit.")
    se = r.get("single_entry") or {}
    check("C.7 no-fresh-capital zero profit 25,000",
          r.get("status") == VERIFIED and se.get("result") == 25000,
          f"status={r.get('status')} result={se.get('result')}")

    # C.8 a stated loss that contradicts the net-worth movement is
    # INVALID_INPUT_MATH with zero journal lines (no invented figure).
    r = orchestrate("Capital at the beginning was Rs.60,000, capital at "
                    "the end was Rs.75,000, drawings were Rs.10,000, and "
                    "the loss was Rs.5,000. No fresh capital was "
                    "introduced.")
    se = r.get("single_entry") or {}
    check("C.8 stated-loss contradiction INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH
          and se.get("contradiction") is True and lines(r) == [],
          f"status={r.get('status')} contradiction={se.get('contradiction')}")

    # C.9 missing data refuses.
    r = orchestrate("A trader keeps incomplete records. Find the profit.")
    check("C.9 missing data refuses",
          r.get("status") == REVIEW_REQUIRED and lines(r) == [],
          f"status={r.get('status')} lines={lines(r)}")


# ---------------------------------------------------------------------------
# PART D - routing & amount ownership
# ---------------------------------------------------------------------------


def test_d_routing():
    print("PART D - ROUTING & AMOUNT OWNERSHIP")
    # every stated amount in a VERIFIED specialized question must have
    # exactly one role (the authorities refuse on any leftover amount).
    for name, q in [
            ("D.1 consignment profit", "Goods of Rs.50,000 were sent on "
             "consignment to Mohan. Consignor paid freight Rs.2,000. "
             "Mohan sold 4/5 of the goods for Rs.48,000. Commission 10% "
             "on sales. Find the consignment profit."),
            ("D.2 JV equal", "Rahul and Mohan entered into a joint "
             "venture. Rahul contributed goods worth Rs.20,000 and Mohan "
             "contributed cash Rs.10,000. Rahul paid expenses of Rs.1,000 "
             "and Mohan paid Rs.500. Sales proceeds were Rs.40,000. "
             "Profit is to be shared equally. Find the profit."),
            ("D.3 single entry", "The opening capital was Rs.60,000 and "
             "the closing capital was Rs.75,000. Drawings were Rs.10,000 "
             "and fresh capital of Rs.5,000 was introduced. Find the "
             "profit."),
    ]:
        r = orchestrate(q)
        payload = r.get("consignment") or r.get("joint_venture") or \
            r.get("single_entry") or {}
        check(f"{name} no leftover ambiguous amounts",
              r.get("status") == VERIFIED
              and not (payload.get("unresolved") or []),
              str(payload.get("unresolved")))

    # the transaction-graph payload carries segments + ownership for the
    # specialized path.
    r = orchestrate("Goods of Rs.50,000 were sent on consignment to "
                    "Mohan. Mohan sold 4/5 of the goods for Rs.48,000. "
                    "Commission 10% on sales. Find the consignment "
                    "profit.")
    orch = r.get("orchestration") or {}
    check("D.4 graph carries segments",
          isinstance(orch.get("segments"), list)
          and len(orch.get("segments")) > 0, str(orch.get("segments")))
    check("D.5 graph carries ownership + dependencies",
          "ownership" in orch and "dependencies" in orch,
          str(sorted(orch.keys())))
    check("D.6 graph merge balanced",
          (orch.get("merge") or {}).get("balanced") is True,
          str(orch.get("merge")))


# ---------------------------------------------------------------------------
# PART E - contradiction state in the graph
# ---------------------------------------------------------------------------


def test_e_contradiction():
    print("PART E - CONTRADICTION STATE IN GRAPH")
    r = orchestrate("Capital at the beginning was Rs.60,000, capital at "
                    "the end was Rs.75,000, drawings were Rs.10,000, and "
                    "the loss was Rs.5,000. No fresh capital was "
                    "introduced.")
    orch = r.get("orchestration") or {}
    se = r.get("single_entry") or {}
    check("E.1 contradiction refuses INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH, r.get("status"))
    check("E.2 contradiction surfaced in single-entry payload",
          se.get("contradiction") is True
          and str((se.get("variables") or {}).get("profit_computed"))
          == "25000"
          and str((se.get("variables") or {}).get("profit_stated"))
          == "-5000",
          str(se.get("variables")))


# ---------------------------------------------------------------------------
# PART F - determinism
# ---------------------------------------------------------------------------


def test_f_determinism():
    print("PART F - DETERMINISTIC REPEATED EXECUTION")
    questions = [
        "Goods of Rs.50,000 were sent on consignment to Mohan. Consignor "
        "paid freight Rs.2,000. Mohan sold 4/5 of the goods for Rs.48,000. "
        "Commission 10% on sales. Find the consignment profit.",
        "Rahul and Mohan entered into a joint venture sharing profits in "
        "the ratio 3:2. Rahul contributed goods of Rs.30,000 and Mohan "
        "contributed Rs.20,000 cash. Expenses of Rs.2,000 were paid by "
        "Rahul. Sales proceeds were Rs.60,000. Find the profit.",
        "The opening capital was Rs.60,000 and the closing capital was "
        "Rs.75,000. Drawings were Rs.10,000 and fresh capital of Rs.5,000 "
        "was introduced. Find the profit.",
        "Goods costing Rs.20,000 were sent on consignment to Mohan. "
        "Freight of Rs.1,000 was paid by the consignor. Goods worth "
        "Rs.5,000 were destroyed in transit. Find the abnormal loss.",
    ]
    for i, q in enumerate(questions):
        a = orchestrate(q)
        b = orchestrate(q)
        check(f"F.{i + 1} byte-identical repeated result", a == b,
              "second run differed")


# ---------------------------------------------------------------------------
# PART G - safety invariant sweep
# ---------------------------------------------------------------------------


def test_g_safety():
    print("PART G - SAFETY INVARIANT SWEEP")
    verified = [
        ("Goods of Rs.50,000 were sent on consignment to Mohan. Consignor "
         "paid freight Rs.2,000. Mohan sold 4/5 of the goods for Rs.48,000. "
         "Commission 10% on sales. Find the consignment profit."),
        ("Goods costing Rs.20,000 were sent on consignment to Mohan. "
         "Freight of Rs.1,000 was paid by the consignor. Goods worth "
         "Rs.5,000 were destroyed in transit. Find the abnormal loss."),
        ("Goods of Rs.40,000 were sent on consignment to Mohan. Freight "
         "Rs.2,000 was paid by the consignor. 3/4 of the goods were sold. "
         "Value the closing consignment stock."),
        ("Goods of Rs.30,000 sent on consignment to Mohan. Consignee paid "
         "expenses of Rs.1,000. 1/2 of the goods were sold for Rs.24,000. "
         "Commission was 10% and del credere commission 2%. Find the "
         "consignment profit."),
        ("Rahul and Mohan entered into a joint venture. Rahul contributed "
         "goods worth Rs.20,000 and Mohan contributed cash Rs.10,000. "
         "Rahul paid expenses of Rs.1,000 and Mohan paid Rs.500. Sales "
         "proceeds were Rs.40,000. Profit is to be shared equally. Find "
         "the profit and the settlement between Rahul and Mohan."),
        ("Rahul and Mohan entered into a joint venture sharing profits in "
         "the ratio 3:2. Rahul contributed goods of Rs.30,000 and Mohan "
         "contributed Rs.20,000 cash. Expenses of Rs.2,000 were paid by "
         "Rahul. Sales proceeds were Rs.60,000. Find the profit."),
        ("The opening capital was Rs.60,000 and the closing capital was "
         "Rs.75,000. Drawings were Rs.10,000 and fresh capital of Rs.5,000 "
         "was introduced. Find the profit."),
        ("A trader keeps incomplete records. The statement of affairs at "
         "the start showed assets of Rs.80,000 and liabilities of "
         "Rs.20,000. At the end, assets were Rs.1,00,000 and liabilities "
         "Rs.30,000. Drawings were Rs.12,000 and no fresh capital was "
         "introduced. Find the profit."),
    ]
    for i, q in enumerate(verified):
        r = orchestrate(q)
        check(f"G.{i + 1} VERIFIED", r.get("status") == VERIFIED,
              r.get("status"))
        check(f"G.{i + 1} all safety invariants zero",
              invariants_zero(r), str(invariants_of(r)))
        check(f"G.{i + 1} journal balanced", balanced(r))
        # a VERIFIED journal is balanced; a mathematical result (single
        # entry) may carry no journal at all.
        if r.get("journal") is not None:
            check(f"G.{i + 1} balanced VERIFIED",
                  (r.get("journal") or {}).get("balanced") is True,
                  str(r.get("journal")))


# ---------------------------------------------------------------------------
# PART H - real Streamlit Study/Verify path (AppTest)
# ---------------------------------------------------------------------------


def test_h_streamlit():
    print("PART H - REAL STREAMLIT STUDY/VERIFY PATH")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("H.0 apptest available", False, str(exc))
        return
    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    check("H.1 app entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("H.2 FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])
    at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()

    def ask(q):
        at.text_area(key="fte_fyjc_question").set_value(q).run()
        at.button(key="fte_fyjc_go").click().run()
        return " ".join(m.value for m in at.markdown)

    verified_ui_cases = [
        ("H.3 consignment profit VERIFIED",
         "Goods of Rs.50,000 were sent on consignment to Mohan. "
         "Consignor paid freight Rs.2,000. Mohan sold 4/5 of the goods "
         "for Rs.48,000. Commission 10% on sales. Find the consignment "
         "profit."),
        ("H.4 joint venture VERIFIED",
         "Rahul and Mohan entered into a joint venture. Rahul contributed "
         "goods worth Rs.20,000 and Mohan contributed cash Rs.10,000. "
         "Sales proceeds were Rs.40,000. Profit is to be shared equally. "
         "Find the profit."),
        ("H.5 single entry profit VERIFIED",
         "The opening capital was Rs.60,000 and the closing capital was "
         "Rs.75,000. Drawings were Rs.10,000 and fresh capital of Rs.5,000 "
         "was introduced. Find the profit."),
        ("H.6 abnormal loss VERIFIED",
         "Goods costing Rs.20,000 were sent on consignment to Mohan. "
         "Freight of Rs.1,000 was paid by the consignor. Goods worth "
         "Rs.5,000 were destroyed in transit. Find the abnormal loss."),
    ]
    for name, q in verified_ui_cases:
        md = ask(q)
        check(name, "VERIFIED" in md.upper()
              and "Almost there" not in md
              and not at.exception,
              [e.stack_trace for e in at.exception] + [md[:200]])

    md = ask("Goods were sent on consignment to Mohan. Find the profit.")
    check("H.7 missing consignment data refuses in UI",
          "REVIEW" in md.upper() and "VERIFIED" not in md.upper()
          and "Almost there" not in md,
          md[:200])
    md = ask("A trader keeps incomplete records. Find the profit.")
    check("H.8 missing single-entry data refuses in UI",
          "REVIEW" in md.upper() and "VERIFIED" not in md.upper(),
          md[:200])


def main():
    test_a_consignment()
    test_b_joint_venture()
    test_c_single_entry()
    test_d_routing()
    test_e_contradiction()
    test_f_determinism()
    test_g_safety()
    test_h_streamlit()
    print(f"\n15I-SPEC gate: {TOTAL[0]} checks passed, "
          f"{len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
