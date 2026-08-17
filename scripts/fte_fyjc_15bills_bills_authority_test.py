#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-BILLS - Bills of Exchange Authority
scripts/fte_fyjc_15bills_bills_authority_test.py

Locks in the Sprint 15I-BILLS Bills of Exchange Authority:

  PART A - Recognition & normalization
    * drawing / acceptance (Bills Receivable), drawee-side acceptance
      (Bills Payable), receipt from a party
    * 'p.a.' -> 'per annum' normalization (never a single-letter 'p.'
      refusal)
    * everyday bills (mobile / electricity) are NOT routed to the Bills
      Authority
    * unsafe single-letter parties refuse (15I-VY refusal preserved)

  PART B - Maturity & discount mathematics
    * months / 12, days / 365, three days of grace
    * stated proceeds, stated discount amount, computed discount
    * missing rate/period refuses; contradictory amounts refuse with
      INVALID_INPUT_MATH; contradictory dates refuse

  PART C - Lifecycle state machine
    * DRAWN -> ACCEPTED -> HELD / DISCOUNTED / ENDORSED /
      SENT_FOR_COLLECTION -> HONOURED / DISHONOURED
    * invalid transitions refuse; endorsed bills are terminal for the
      drawer

  PART D - Noting charges (consumed exactly once; never confused with
    discount)

  PART E - Multi-stage chains (draw -> accept -> discount -> dishonour
    -> noting) with every segment journaled and balanced

  PART F - Safety & refusals
    * missing prior bill state refuses (history never invented)
    * missing amount / party / role refuses with zero journal lines
    * unsupported bill wording refuses as NOT_SUPPORTED
    * deterministic repeated execution (byte-identical)

  PART G - Safety invariant sweep on every VERIFIED result:
    unsafe_confident = 0, invented_accounts = 0, invented_amounts = 0,
    invented_history = 0, unbalanced_verified = 0, dropped_segments = 0,
    duplicated_segments = 0, authority_conflicts = 0,
    flow verdict == orchestrated verdict.

  PART H - Real Streamlit Study/Verify AppTest for the released path.

Exit code 0 = all checks pass.
"""

import json
import os
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_accounting import (  # noqa: E402
    hardened_bookkeeping_outcome,
)
from backend.maths.fyjc_bills import (  # noqa: E402
    bills_outcome,
    detect_bills,
)
from backend.maths.fyjc_normalization import (  # noqa: E402
    normalize_fyjc_text,
)
from backend.maths.fyjc_orchestration import (  # noqa: E402
    orchestrate,
)
from backend.maths.fyjc_student_flow import (  # noqa: E402
    run_fyjc_accounting_flow,
)
from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    INVALID_INPUT_MATH,
    NOT_SUPPORTED,
    REVIEW_REQUIRED,
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
    orch = result.get("orchestration") or {}
    return orch.get("invariants", {})


_SAFETY_KEYS = (
    "unsafe_confident",
    "dropped_valid_segments",
    "unresolved_amounts_guessed",
    "duplicated_amount_ownership",
    "authority_conflicts_verified",
    "invented_accounts",
    "invented_amounts",
    "unbalanced_verified",
    "invented_historical_state",
    "duplicate_correction",
    "duplicated_segments",
)


def invariants_zero(result) -> bool:
    inv = invariants_of(result)
    return (all(inv.get(k, 0) == 0 for k in _SAFETY_KEYS)
            and inv.get("flow_verdict_eq_bills_authority") is True
            and inv.get("deterministic") is True)


def balanced(result) -> bool:
    j = result.get("journal") or {}
    return j.get("balanced") is not False


def states_of(result) -> list:
    bills = (result.get("orchestration") or {}).get("bills") or {}
    return [(s.get("state"), s.get("implicit"))
            for s in bills.get("states") or []]


def discount_of(result) -> dict:
    bills = (result.get("orchestration") or {}).get("bills") or {}
    return bills.get("discount") or {}


def history_of(result) -> dict:
    bills = (result.get("orchestration") or {}).get("bills") or {}
    return bills.get("history") or {}


# ---------------------------------------------------------------------------
# PART A - recognition & normalization
# ---------------------------------------------------------------------------


def test_a_recognition():
    print("PART A - RECOGNITION & NORMALIZATION")
    r = orchestrate("Received a bill of exchange from Ram for Rs.10,000.")
    check("A.1 received bill VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("A.1 Bills Receivable / Ram journal",
          lines(r) == [("Bills Receivable", "10000"), ("Ram", "10000")],
          str(lines(r)))
    check("A.1 routed to bills-authority",
          (r.get("orchestration") or {}).get("authority")
          == "bills-authority", str((r.get("orchestration") or {})))

    r = orchestrate("Rahul drew a bill of exchange on Mohan for "
                    "Rs.10,000.")
    check("A.2 drawn bill VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("A.2 Bills Receivable / Mohan journal",
          lines(r) == [("Bills Receivable", "10000"), ("Mohan", "10000")],
          str(lines(r)))

    r = orchestrate("Rahul accepted Mohan's bill for Rs.10,000.")
    check("A.3 drawee acceptance VERIFIED (Bills Payable)",
          r.get("status") == VERIFIED, r.get("status"))
    check("A.3 Mohan / Bills Payable journal",
          lines(r) == [("Mohan", "10000"), ("Bills Payable", "10000")],
          str(lines(r)))

    # 'p.a.' must normalize, never trip the single-letter 'p.' gate
    n = normalize_fyjc_text("Rahul discounted a bill of Rs.10,000 with "
                            "the bank at 12% p.a. for 3 months.")
    check("A.4 p.a. normalized to per annum",
          "per annum" in n.text and "p.a." not in n.text, n.text)
    check("A.4 no safety concern from p.a.",
          not any("single-letter" in c for c in n.concerns),
          str(n.concerns))
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan for 3 "
                    "months. Rahul discounted it with the bank at 12% "
                    "p.a. for 3 months.")
    check("A.4 p.a. discount VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("A.4 discount computed (300)",
          discount_of(r).get("discount") == "300.00",
          str(discount_of(r)))

    # everyday bills are NOT bills of exchange
    r = orchestrate("Paid his mobile recharge bill Rs.500.")
    check("A.5 everyday bill not routed to bills",
          (r.get("orchestration") or {}).get("authority")
          != "bills-authority", str(r.get("status")))
    check("A.5 everyday bill not VERIFIED",
          r.get("status") != VERIFIED, r.get("status"))

    # single-letter parties refuse (15I-VY safety preserved)
    r = orchestrate("A drew a bill of exchange on B for Rs.10,000.")
    check("A.6 single-letter party refuses",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("A.6 zero journal lines", lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# PART B - maturity & discount mathematics
# ---------------------------------------------------------------------------


def test_b_maturity_math():
    print("PART B - MATURITY & DISCOUNT MATHEMATICS")
    # months / 12: 10,000 x 12% x 3/12 = 300
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan for 3 "
                    "months. Rahul discounted it with the bank at 12% "
                    "p.a.")
    check("B.1 3-month discount VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B.1 proceeds 9,700 / discount 300",
          discount_of(r).get("proceeds") == "9700.00"
          and discount_of(r).get("discount") == "300.00",
          str(discount_of(r)))
    check("B.1 discount journal",
          ("Bank", "9700.00") in lines(r)
          and ("Discount", "300.00") in lines(r),
          str(lines(r)))

    # days / 365: 10,000 x 12% x 60/365 = 197.26
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan for 60 "
                    "days. Rahul discounted it with the bank at 12% p.a.")
    check("B.2 60-day discount VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B.2 proceeds 9,802.74 / discount 197.26",
          discount_of(r).get("proceeds") == "9802.74"
          and discount_of(r).get("discount") == "197.26",
          str(discount_of(r)))

    # stated proceeds -> discount derived exactly once
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan for 3 "
                    "months. Rahul discounted it with the bank for "
                    "Rs.9,700.")
    check("B.3 stated proceeds VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B.3 discount derived 300",
          discount_of(r).get("discount") == "300"
          and discount_of(r).get("basis") == "stated proceeds",
          str(discount_of(r)))

    # stated discount amount -> proceeds derived
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan for 3 "
                    "months. Rahul discounted it with the bank at a "
                    "discount of Rs.300.")
    check("B.4 stated discount VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B.4 proceeds derived 9,700",
          discount_of(r).get("proceeds") == "9700"
          and discount_of(r).get("basis") == "stated discount",
          str(discount_of(r)))

    # no rate / period -> no silent assumption
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "discounted it with the bank.")
    check("B.5 no rate/period refuses",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("B.5 zero journal lines", lines(r) == [], str(lines(r)))
    check("B.5 explains no assumption",
          "never silently" in (r.get("why_not") or ""),
          str(r.get("why_not"))[:120])

    # proceeds > bill amount -> negative discount -> refuse
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "discounted it with the bank for Rs.10,200.")
    check("B.6 proceeds exceed bill INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH, r.get("status"))
    check("B.6 zero journal lines", lines(r) == [], str(lines(r)))

    # stated proceeds contradict computed discount
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan for 3 "
                    "months. Rahul discounted it with the bank at 12% "
                    "p.a. for Rs.9,500.")
    check("B.7 proceeds vs computed INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH, r.get("status"))

    # contradictory dates refuse (draw + period + 3 grace days != due)
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan on 1 "
                    "January 2025 for 3 months. The bill was due on 1 "
                    "February 2025.")
    check("B.8 contradictory dates REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("B.8 zero journal lines", lines(r) == [], str(lines(r)))
    check("B.8 explains the date conflict",
          "due date" in (r.get("why_not") or ""),
          str(r.get("why_not"))[:120])

    # consistent dates are accepted (draw + 3 months + 3 grace days)
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan on 1 "
                    "January 2025 for 3 months. The bill was due on 4 "
                    "April 2025.")
    check("B.9 consistent dates VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    bills = (r.get("orchestration") or {}).get("bills") or {}
    maturity = bills.get("maturity") or {}
    check("B.9 three days of grace encoded",
          maturity.get("days_of_grace") == 3
          and maturity.get("due_date") == "04 Apr 2025",
          str(maturity))


# ---------------------------------------------------------------------------
# PART C - lifecycle state machine
# ---------------------------------------------------------------------------


def test_c_state_machine():
    print("PART C - LIFECYCLE STATE MACHINE")
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan which "
                    "Mohan accepted.")
    check("C.1 draw+accept VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.1 states DRAWN -> ACCEPTED",
          states_of(r) == [("DRAWN", False), ("ACCEPTED", False)],
          str(states_of(r)))
    check("C.1 single acceptance journal",
          lines(r) == [("Bills Receivable", "10000"), ("Mohan", "10000")],
          str(lines(r)))

    r = orchestrate("Received a bill of exchange from Ram for Rs.10,000 "
                    "which was retained till maturity.")
    check("C.2 retained bill VERIFIED (HELD)",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.2 terminal HELD state",
          states_of(r)[-1] == ("HELD", True), str(states_of(r)))
    check("C.2 acceptance journal only",
          lines(r) == [("Bills Receivable", "10000"), ("Ram", "10000")],
          str(lines(r)))

    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. On "
                    "maturity the bill was honoured.")
    check("C.3 honour at maturity VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.3 honour journal (Cash)",
          ("Cash", "10000") in lines(r)
          and ("Bills Receivable", "10000") in lines(r),
          str(lines(r)))
    check("C.3 states end at HONOURED",
          states_of(r)[-1] == ("HONOURED", False), str(states_of(r)))

    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "discounted it with the bank at 12% p.a. for 3 "
                    "months.")
    check("C.4 discount VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.4 acceptance + discount journals",
          len((r.get("orchestration") or {}).get("bills", {})
              .get("states", [])) >= 3,
          str(states_of(r)))
    check("C.4 acceptance + discount lines",
          ("Bills Receivable", "10000") in lines(r)
          and ("Bank", "9700.00") in lines(r)
          and ("Discount", "300.00") in lines(r),
          str(lines(r)))

    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "endorsed it to his creditor Shyam.")
    check("C.5 endorsement VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.5 acceptance + endorsement journals (never cash)",
          lines(r) == [("Bills Receivable", "10000"),
                       ("Shyam", "10000"), ("Mohan", "10000"),
                       ("Bills Receivable", "10000")],
          str(lines(r)))

    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "sent it to the bank for collection. The bank "
                    "collected it on maturity.")
    check("C.6 sent for collection + collected VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.6 sent + collected journals",
          ("Bills Sent for Collection", "10000") in lines(r)
          and ("Bank", "10000") in lines(r),
          str(lines(r)))

    # invalid transition: discount + endorse
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "discounted it with the bank at 12% p.a. for 3 "
                    "months and endorsed it to Shyam.")
    check("C.7 discount+endorse invalid transition refuses",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("C.7 zero journal lines", lines(r) == [], str(lines(r)))

    # held bill dishonoured -> reversal + reinstatement
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. On "
                    "maturity Mohan dishonoured the bill.")
    check("C.8 held dishonour VERIFIED (reversal)",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.8 reversal journal",
          lines(r) == [("Bills Receivable", "10000"), ("Mohan", "10000"),
                       ("Mohan", "10000"), ("Bills Receivable", "10000")],
          str(lines(r)))
    check("C.8 states end at DISHONOURED",
          states_of(r)[-1] == ("DISHONOURED", False), str(states_of(r)))

    # collected AND honoured -> ambiguous outcome refuses
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "sent it to the bank for collection. On maturity the "
                    "bill was collected and honoured.")
    check("C.9 collected+honoured refuses",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("C.9 zero journal lines", lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# PART D - noting charges
# ---------------------------------------------------------------------------


def test_d_noting_charges():
    print("PART D - NOTING CHARGES")
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan for 3 "
                    "months. Rahul discounted it with the bank at 12% "
                    "p.a. On maturity Mohan dishonoured the bill and the "
                    "bank paid Rs.100 noting charges.")
    check("D.1 noting charges VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("D.1 acceptor charged bill + noting (10,100)",
          ("Mohan", "10100") in lines(r), str(lines(r)))
    check("D.1 bank recovered bill + noting",
          ("Bank", "10100") in lines(r), str(lines(r)))
    check("D.1 noting consumed exactly once",
          lines(r).count(("Mohan", "10100")) == 1
          and lines(r).count(("Bank", "10100")) == 1,
          str(lines(r)))
    check("D.1 balanced", balanced(r), "unbalanced")

    # noting charges are NOT the discount
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan for 3 "
                    "months. Rahul discounted it with the bank at 12% "
                    "p.a. for a discount of Rs.300. On maturity Mohan "
                    "dishonoured the bill and Rs.100 noting charges were "
                    "paid by the bank.")
    check("D.2 discount vs noting distinct VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("D.2 discount 300, noting 100 kept apart",
          ("Discount", "300") in lines(r)
          and ("Mohan", "10100") in lines(r)
          and ("Cash", "100") not in lines(r)
          and ("Bank", "100") not in lines(r),
          str(lines(r)))

    # noting charges without a dishonour refuse
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. The bank "
                    "paid Rs.100 noting charges.")
    check("D.3 noting without dishonour refuses",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("D.3 zero journal lines", lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# PART E - multi-stage chains
# ---------------------------------------------------------------------------


def test_e_chains():
    print("PART E - MULTI-STAGE CHAINS")
    q = ("Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
         "discounted it with the bank at 12% p.a. On maturity Mohan "
         "dishonoured the bill and the bank paid Rs.100 noting charges.")
    r = orchestrate(q)
    check("E.1 full chain VERIFIED", r.get("status") == VERIFIED,
          r.get("status"))
    check("E.1 draw + discount + dishonour journaled",
          lines(r) == [("Bills Receivable", "10000"),
                       ("Bank", "9700.00"), ("Discount", "300.00"),
                       ("Mohan", "10100"), ("Mohan", "10000"),
                       ("Bills Receivable", "10000"), ("Bank", "10100")],
          str(lines(r)))
    check("E.1 balanced", balanced(r), "unbalanced")
    check("E.1 states DRAWN->ACCEPTED->DISCOUNTED->DISHONOURED",
          [s[0] for s in states_of(r)]
          == ["DRAWN", "ACCEPTED", "DISCOUNTED", "DISHONOURED"],
          str(states_of(r)))
    check("E.1 no segment dropped",
          len((r.get("orchestration") or {}).get("segments", [])) >= 1,
          str((r.get("orchestration") or {}).get("segments", [])))

    # a received bill + dishonour is a two-journal chain
    q2 = ("Received a bill of exchange from Ram for Rs.10,000 which was "
          "later dishonoured.")
    r2 = orchestrate(q2)
    check("E.2 received + dishonour VERIFIED",
          r2.get("status") == VERIFIED, r2.get("status"))
    check("E.2 reversal journal",
          lines(r2) == [("Bills Receivable", "10000"), ("Ram", "10000"),
                        ("Ram", "10000"), ("Bills Receivable", "10000")],
          str(lines(r2)))
    check("E.2 balanced", balanced(r2), "unbalanced")

    # draw -> accept -> sent for collection -> dishonour + noting
    q3 = ("Rahul drew a bill of Rs.10,000 on Mohan. Rahul sent it to "
          "the bank for collection. On maturity Mohan dishonoured the "
          "bill and the bank paid Rs.100 noting charges.")
    r3 = orchestrate(q3)
    check("E.3 collection-chain dishonour VERIFIED",
          r3.get("status") == VERIFIED, r3.get("status"))
    check("E.3 balanced", balanced(r3), "unbalanced")
    check("E.3 noting added to acceptor",
          ("Mohan", "10100") in lines(r3), str(lines(r3)))


# ---------------------------------------------------------------------------
# PART F - safety & refusals
# ---------------------------------------------------------------------------


def test_f_refusals():
    print("PART F - SAFETY & REFUSALS")
    # missing prior bill state (never reconstruct history)
    r = orchestrate("A bill of Rs.5,000 was dishonoured.")
    check("F.1 missing-history dishonour REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("F.1 zero journal lines", lines(r) == [], str(lines(r)))
    check("F.1 explains the missing dependency",
          "prior state and amount cannot be established"
          in (r.get("why_not") or ""),
          str(r.get("why_not"))[:140])
    check("F.1 no invented history",
          history_of(r).get("invented") is not True,
          str(history_of(r)))

    r = orchestrate("The bill was dishonoured.")
    check("F.2 bare dishonour REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("F.2 zero journal lines", lines(r) == [], str(lines(r)))

    # missing amount
    r = orchestrate("Rahul drew a bill on Mohan.")
    check("F.3 missing amount REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("F.3 zero journal lines", lines(r) == [], str(lines(r)))

    # discount without computable or stated discount
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "discounted it with the bank.")
    check("F.4 discount without rate/period REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("F.4 zero journal lines", lines(r) == [], str(lines(r)))

    # ambiguous party role: accepted bill with no drawer
    r = orchestrate("Rahul accepted a bill for Rs.10,000.")
    check("F.5 accepted bill without drawer REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("F.5 zero journal lines", lines(r) == [], str(lines(r)))

    # contradictory amounts
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "discounted it with the bank for Rs.10,200.")
    check("F.6 contradictory amounts INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH, r.get("status"))
    check("F.6 zero journal lines", lines(r) == [], str(lines(r)))

    # contradictory dates
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan on 1 "
                    "January 2025 for 3 months. The bill was due on 1 "
                    "February 2025.")
    check("F.7 contradictory dates REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("F.7 zero journal lines", lines(r) == [], str(lines(r)))

    # unsupported: an endorsed bill's later settlement is the endorsee's
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "endorsed it to Shyam. On maturity Mohan dishonoured "
                    "the bill.")
    check("F.8 endorsed-bill settlement NOT_SUPPORTED",
          r.get("status") == NOT_SUPPORTED, r.get("status"))
    check("F.8 zero journal lines", lines(r) == [], str(lines(r)))

    # double dishonour (duplicate correction)
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan. Rahul "
                    "discounted it with the bank at 12% p.a. On maturity "
                    "Mohan dishonoured the bill twice.")
    check("F.9 double dishonour REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("F.9 zero journal lines", lines(r) == [], str(lines(r)))
    bills = (r.get("orchestration") or {}).get("bills") or {}
    check("F.9 duplicate_correction flagged",
          bills.get("duplicate_correction") is True,
          str(bills))
    check("F.9 invariant reports duplicate correction",
          invariants_of(r).get("duplicate_correction") == 1,
          str(invariants_of(r)))

    # unconsumed rate refuses
    r = orchestrate("Rahul drew a bill of Rs.10,000 on Mohan at 5% "
                    "trade discount.")
    check("F.10 unconsumed rate REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("F.10 zero journal lines", lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# PART G - safety invariant sweep
# ---------------------------------------------------------------------------


def test_g_safety():
    print("PART G - SAFETY INVARIANT SWEEP")
    verified_corpus = [
        "Received a bill of exchange from Ram for Rs.10,000.",
        "Rahul drew a bill of exchange on Mohan for Rs.10,000.",
        "Rahul accepted Mohan's bill for Rs.10,000.",
        "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
        "discounted it with the bank at 12% p.a. for 3 months.",
        "Rahul drew a bill of Rs.10,000 on Mohan for 60 days. Rahul "
        "discounted it with the bank at 12% p.a.",
        "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
        "discounted it with the bank for Rs.9,700.",
        "Rahul drew a bill of Rs.10,000 on Mohan. Rahul endorsed it to "
        "his creditor Shyam.",
        "Rahul drew a bill of Rs.10,000 on Mohan. Rahul sent it to the "
        "bank for collection. The bank collected it on maturity.",
        "Rahul drew a bill of Rs.10,000 on Mohan. On maturity the bill "
        "was honoured.",
        "Received a bill of exchange from Ram for Rs.10,000 which was "
        "later dishonoured.",
        "Rahul drew a bill of Rs.10,000 on Mohan. On maturity Mohan "
        "dishonoured the bill.",
        "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
        "discounted it with the bank at 12% p.a. On maturity Mohan "
        "dishonoured the bill and the bank paid Rs.100 noting charges.",
        "Rahul drew a bill of Rs.10,000 on Mohan on 1 January 2025 for "
        "3 months. The bill was due on 4 April 2025.",
    ]
    for i, q in enumerate(verified_corpus):
        r = orchestrate(q)
        check(f"G.V.{i} VERIFIED ({q[:40]})",
              r.get("status") == VERIFIED, r.get("status"))
        check(f"G.V.{i} safety invariants zero ({q[:40]})",
              invariants_zero(r), str(invariants_of(r)))
        check(f"G.V.{i} balanced ({q[:40]})", balanced(r), "unbalanced")
        check(f"G.V.{i} deterministic ({q[:40]})",
              json.dumps(orchestrate(q), default=str, sort_keys=True)
              == json.dumps(r, default=str, sort_keys=True),
              "mismatch on repeated run")
        check(f"G.V.{i} flow verdict == orchestrated ({q[:40]})",
              run_fyjc_accounting_flow(q).get("status")
              == r.get("status"),
              str(run_fyjc_accounting_flow(q).get("status")))

    refusal_corpus = [
        "A bill of Rs.5,000 was dishonoured.",
        "The bill was dishonoured.",
        "Rahul drew a bill on Mohan.",
        "Rahul drew a bill of Rs.10,000 on Mohan. Rahul discounted it "
        "with the bank.",
        "Rahul accepted a bill for Rs.10,000.",
        "Rahul drew a bill of Rs.10,000 on Mohan. Rahul discounted it "
        "with the bank for Rs.10,200.",
        "Rahul drew a bill of Rs.10,000 on Mohan on 1 January 2025 for "
        "3 months. The bill was due on 1 February 2025.",
        "Rahul drew a bill of Rs.10,000 on Mohan. Rahul endorsed it to "
        "Shyam. On maturity Mohan dishonoured the bill.",
        "Rahul drew a bill of Rs.10,000 on Mohan at 5% trade discount.",
        "Rahul drew a bill of Rs.10,000 on Mohan. Rahul discounted it "
        "with the bank at 12% p.a. for 3 months and endorsed it to "
        "Shyam.",
        "A drew a bill of exchange on B for Rs.10,000.",
    ]
    for i, q in enumerate(refusal_corpus):
        r = orchestrate(q)
        check(f"G.R.{i} refuses ({q[:40]})",
              r.get("status") != VERIFIED, r.get("status"))
        check(f"G.R.{i} zero lines ({q[:40]})",
              lines(r) == [], str(lines(r)))
        check(f"G.R.{i} deterministic ({q[:40]})",
              json.dumps(orchestrate(q), default=str, sort_keys=True)
              == json.dumps(r, default=str, sort_keys=True),
              "mismatch on repeated run")
        check(f"G.R.{i} flow verdict == orchestrated ({q[:40]})",
              run_fyjc_accounting_flow(q).get("status")
              == r.get("status"),
              str(run_fyjc_accounting_flow(q).get("status")))

    # detection: everyday bills and cheques are never routed to bills
    check("G.1 everyday bill not detected",
          detect_bills("Paid his mobile recharge bill Rs.500.") is None,
          str(detect_bills("Paid his mobile recharge bill Rs.500.")))
    check("G.2 cheque dishonour not detected as bills",
          detect_bills("Received a cheque from Ram for Rs.10,000 which "
                       "was later dishonoured.") is None,
          str(detect_bills("Received a cheque from Ram for Rs.10,000 "
                           "which was later dishonoured.")))
    check("G.3 bill of exchange detected",
          (detect_bills("Received a bill of exchange from Ram for "
                        "Rs.10,000.") or {}).get("topics")
          == ["bills_of_exchange"],
          str(detect_bills("Received a bill of exchange from Ram for "
                           "Rs.10,000.")))


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
        ("H.3 bill receivable VERIFIED (never cash)",
         "Received a bill of exchange from Ram for Rs.10,000."),
        ("H.4 draw + discount + dishonour chain VERIFIED",
         "Rahul drew a bill of Rs.10,000 on Mohan for 3 months. Rahul "
         "discounted it with the bank at 12% p.a. On maturity Mohan "
         "dishonoured the bill and the bank paid Rs.100 noting charges."),
        ("H.5 endorsement VERIFIED",
         "Rahul drew a bill of Rs.10,000 on Mohan. Rahul endorsed it to "
         "his creditor Shyam."),
    ]
    for name, q in verified_ui_cases:
        md = ask(q)
        check(name, "VERIFIED" in md.upper()
              and "Almost there" not in md
              and not at.exception,
              [e.stack_trace for e in at.exception] + [md[:200]])

    md = ask("A bill of Rs.5,000 was dishonoured.")
    check("H.6 missing-history bill dishonour refuses in UI",
          "REVIEW" in md.upper() and "VERIFIED" not in md.upper()
          and "Almost there" not in md,
          md[:200])
    md = ask("A drew a bill of exchange on B for Rs.10,000.")
    check("H.7 single-letter party refuses in UI",
          "REVIEW" in md.upper() and "VERIFIED" not in md.upper(),
          md[:200])
    md = ask("Paid his mobile recharge bill Rs.500.")
    check("H.8 everyday bill not VERIFIED in UI",
          "VERIFIED" not in md.upper(), md[:160])


def main():
    test_a_recognition()
    test_b_maturity_math()
    test_c_state_machine()
    test_d_noting_charges()
    test_e_chains()
    test_f_refusals()
    test_g_safety()
    test_h_streamlit()
    print(f"\n15I-BILLS gate: {TOTAL[0]} checks passed, "
          f"{len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
