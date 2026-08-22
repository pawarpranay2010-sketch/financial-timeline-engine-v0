#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-DISC - Discrepancy Authority
scripts/fte_fyjc_15disc_discrepancy_authority_test.py

Locks in the Sprint 15I-DISC Discrepancy Authority:

  PART A - BRS (bank reconciliation) single-case adjustments
    * cheque issued but not yet presented for payment
    * cheque deposited but not yet cleared
    * bank charges recorded by the bank but absent from the cash book
    * bank interest / direct credit recorded by the bank
    * direct bank payment
    * dishonoured cheque (bank-reconciliation context)

  PART B - Dishonour
    * valid historical cheque + dishonour (reversal + reinstatement)
    * missing historical cheque (refuses - history is never invented)
    * missing amount (refuses)
    * repeated / double dishonour (refuses - no duplicate correction)

  PART C - Omission
    * omitted purchase / sale / return (canonical effect generated)
    * ambiguous omitted transaction (refuses)
    * unsafe single-letter party token (15I-VY refusal preserved)

  PART D - Rectification ('recorded -> should -> correction')
    * wrong account, wrong amount, wrong side
    * complete omission, partial omission
    * valid Suspense (trial-balance difference explicitly established)
    * invalid / unnecessary Suspense (direct correction, no Suspense)

  PART E - Safety invariants on every VERIFIED discrepancy result
    * unused amount = 0, invented account = 0, invented historical
      state = 0, unbalanced VERIFIED = 0, duplicate correction = 0,
      dropped segment = 0, authority conflict = 0
    * deterministic repeated execution (byte-identical)

  PART F - Real Streamlit Study/Verify AppTest
    * valid discrepancy cases VERIFIED through the real UI
    * NO misleading 'Almost there' panel for VERIFIED discrepancy
      results; accurate refusals for the history-gate cases

Exit code 0 = all checks pass.
"""

import json
import os
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_accounting import (  # noqa: E402
    hardened_bookkeeping_outcome,
)
from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    NOT_SUPPORTED,
    REVIEW_REQUIRED,
    VERIFIED,
)
from backend.maths.fyjc_discrepancy import (  # noqa: E402
    detect_discrepancy,
    discrepancy_outcome,
)
from backend.maths.fyjc_student_flow import (  # noqa: E402
    run_fyjc_accounting_flow,
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


def discrepancy_of(result) -> dict:
    return result.get("discrepancy") or {}


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
    "unbalanced_verified",
    "invented_historical_state",
    "duplicate_correction",
)


def invariants_zero(result) -> bool:
    inv = invariants_of(result)
    return (all(inv.get(k, 0) == 0 for k in _SAFETY_KEYS)
            and inv.get("flow_verdict_eq_discrepancy_authority") is True
            and inv.get("deterministic") is True)


def balanced(result) -> bool:
    td = sum(float(l.get("amount") or 0)
             for l in (result.get("debit_lines") or []))
    tc = sum(float(l.get("amount") or 0)
             for l in (result.get("credit_lines") or []))
    return abs(td - tc) < 1e-9


# ---------------------------------------------------------------------------
# PART A - BRS single-case adjustments
# ---------------------------------------------------------------------------


def test_a_brs():
    print("PART A - BRS")
    cases = [
        ("A1",
         "Cheque issued to Rahul for Rs.10,000 but not yet presented for "
         "payment.",
         "cheque_issued_not_presented",
         [], "Pass Book", "add"),
        ("A2",
         "Deposited a cheque received from Ram for Rs.10,000 which has not "
         "yet been cleared by the bank.",
         "cheque_deposited_not_cleared",
         [], "Pass Book", "deduct"),
        ("A3",
         "Bank charged Rs.200 as bank charges which were not recorded in "
         "the cash book.",
         "bank_charges",
         [("Bank Charges", "200"), ("Bank", "200")], "Cash Book", "deduct"),
        ("A4",
         "Bank credited Rs.500 as interest which was not recorded in the "
         "cash book.",
         "bank_interest_credit",
         [("Bank", "500"), ("Interest Received", "500")],
         "Cash Book", "add"),
        ("A5",
         "Insurance premium of Rs.1,200 was paid directly by the bank "
         "under standing instructions and not recorded in the cash book.",
         "direct_bank_payment",
         [("Insurance", "1200"), ("Bank", "1200")], "Cash Book", "deduct"),
        ("A6",
         "A cheque received from Ram for Rs.5,000 was dishonoured as per "
         "pass book and not recorded in the cash book.",
         "dishonoured_cheque",
         [("Bank", "5000"), ("Ram", "5000"), ("Ram", "5000"),
          ("Bank", "5000")], "Cash Book", "deduct"),
    ]
    for label, q, case_id, expected, book, direction in cases:
        r = hardened_bookkeeping_outcome(q)
        check(f"{label}.{case_id} VERIFIED",
              r.get("status") == VERIFIED, r.get("status"))
        check(f"{label}.{case_id} journal",
              lines(r) == expected, str(lines(r)))
        check(f"{label}.{case_id} balanced",
              balanced(r), "unbalanced")
        disc = discrepancy_of(r)
        # the dishonour-in-BRS-context case (A6) is resolved by the
        # dishonour authority with the BRS reconciliation effect attached
        check(f"{label}.{case_id} topic BRS or dishonour",
              disc.get("topic") in ("brs", "dishonour"),
              str(disc.get("topic")))
        check(f"{label}.{case_id} case id",
              disc.get("case") == case_id, str(disc.get("case")))
        recon = disc.get("reconciliation") or []
        check(f"{label}.{case_id} reconciliation effect",
              len(recon) == 1
              and recon[0].get("book") == book
              and recon[0].get("direction") == direction,
              str(recon))
        check(f"{label}.{case_id} safety invariants",
              invariants_zero(r), str(invariants_of(r)))
    # a full BRS from a particulars list is NOT supported yet (refuses,
    # never invents the statement)
    r = hardened_bookkeeping_outcome(
        "From the following particulars prepare a bank reconciliation "
        "statement: unpresented cheques Rs.5,000, uncleared deposits "
        "Rs.3,000.")
    check("A7 list-form BRS NOT_SUPPORTED",
          r.get("status") == NOT_SUPPORTED, r.get("status"))
    check("A7 zero journal lines", lines(r) == [], str(lines(r)))
    check("A7 explains one-adjustment-at-a-time",
          "ONE adjustment" in (r.get("why_not") or ""),
          str(r.get("why_not"))[:120])


# ---------------------------------------------------------------------------
# PART B - Dishonour
# ---------------------------------------------------------------------------


def test_b_dishonour():
    print("PART B - DISHONOUR")
    r = hardened_bookkeeping_outcome(
        "Received a cheque from Ram for Rs.10,000 which was later "
        "dishonoured.")
    check("B1 valid receipt + dishonour VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B1 reversal journal",
          lines(r) == [("Bank", "10000"), ("Ram", "10000"),
                       ("Ram", "10000"), ("Bank", "10000")],
          str(lines(r)))
    check("B1 balanced", balanced(r), "unbalanced")
    hist = discrepancy_of(r).get("history") or {}
    check("B1 history established",
          hist.get("established") is True
          and hist.get("direction") == "received", str(hist))
    check("B1 amount preserved",
          "10,000" in str(discrepancy_of(r).get("notes")), "amount")
    check("B1 safety invariants",
          invariants_zero(r), str(invariants_of(r)))

    r = hardened_bookkeeping_outcome(
        "Sold goods to Ram for Rs.10,000 and received a cheque which was "
        "dishonoured.")
    check("B2 sale + cheque + dishonour VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B2 full chain journal",
          lines(r) == [("Ram", "10000"), ("Bank", "10000"),
                       ("Ram", "10000"), ("Sales", "10000"),
                       ("Ram", "10000"), ("Bank", "10000")],
          str(lines(r)))
    check("B2 balanced", balanced(r), "unbalanced")
    check("B2 customer balance reinstated",
          "reinstat" in str(discrepancy_of(r).get("notes")),
          "reversal note")
    check("B2 safety invariants",
          invariants_zero(r), str(invariants_of(r)))

    r = hardened_bookkeeping_outcome(
        "A cheque issued to Rahul for Rs.10,000 was dishonoured.")
    check("B6 issued cheque dishonour VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B6 issued reversal journal",
          lines(r) == [("Rahul", "10000"), ("Bank", "10000"),
                       ("Bank", "10000"), ("Rahul", "10000")],
          str(lines(r)))
    check("B6 balanced", balanced(r), "unbalanced")

    r = hardened_bookkeeping_outcome(
        "Received a cheque from Ram for Rs.10,000. The cheque was later "
        "dishonoured.")
    check("B7 two-segment dishonour VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    authority_result = discrepancy_outcome(
        "Received a cheque from Ram for Rs.10,000. The cheque was later "
        "dishonoured.")
    check("B7 both segments journaled (no dropped segment)",
          len((r.get("orchestration") or {}).get("segments", [])) == 2
          and len(authority_result.get("journals") or []) == 2,
          f"segments={(r.get('orchestration') or {}).get('segments', [])} "
          f"journals={len(authority_result.get('journals') or [])}")
    check("B7 balanced", balanced(r), "unbalanced")

    # -- missing historical cheque: section-6 gate --------------------------
    r = hardened_bookkeeping_outcome("Ram's cheque of Rs.5,000 was "
                                     "dishonoured.")
    check("B3 missing history REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("B3 zero journal lines", lines(r) == [], str(lines(r)))
    check("B3 explains the missing dependency",
          "no reliable record" in (r.get("why_not") or ""),
          str(r.get("why_not"))[:120])
    check("B3 never reconstructs history",
          discrepancy_of(r).get("invented_history") is False,
          str(discrepancy_of(r)))

    # -- missing amount -----------------------------------------------------
    r = hardened_bookkeeping_outcome(
        "The cheque received from Ram was dishonoured.")
    check("B5 missing amount REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("B5 zero journal lines", lines(r) == [], str(lines(r)))

    # -- repeated / double dishonour ----------------------------------------
    r = hardened_bookkeeping_outcome(
        "Received a cheque from Ram for Rs.10,000 which was dishonoured "
        "twice.")
    check("B4 double dishonour REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("B4 zero journal lines", lines(r) == [], str(lines(r)))
    check("B4 duplicate_correction flagged",
          discrepancy_of(r).get("duplicate_correction") is True,
          str(discrepancy_of(r)))
    check("B4 invariant reports duplicate correction",
          invariants_of(r).get("duplicate_correction") == 1,
          str(invariants_of(r)))


# ---------------------------------------------------------------------------
# PART C - Omission
# ---------------------------------------------------------------------------


def test_c_omission():
    print("PART C - OMISSION")
    r = hardened_bookkeeping_outcome(
        "Purchased goods from Rahul for Rs.20,000 which was completely "
        "omitted from the books.")
    check("C1 omitted purchase VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C1 missing entry generated",
          lines(r) == [("Purchases", "20000"), ("Rahul", "20000")],
          str(lines(r)))
    check("C1 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    r = hardened_bookkeeping_outcome(
        "Sold goods to Ram for Rs.10,000 which was omitted from the books.")
    check("C2 omitted sale VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C2 missing entry generated",
          lines(r) == [("Ram", "10000"), ("Sales", "10000")],
          str(lines(r)))
    check("C2 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    r = hardened_bookkeeping_outcome(
        "Goods returned by Mohan worth Rs.1,200 were completely omitted "
        "from the books.")
    check("C3 omitted return VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C3 customer-return entry",
          lines(r) == [("Sales Returns", "1200"), ("Mohan", "1200")],
          str(lines(r)))
    check("C3 case id",
          discrepancy_of(r).get("case") == "omitted_customer_return",
          str(discrepancy_of(r).get("case")))
    check("C3 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    r = hardened_bookkeeping_outcome(
        "A transaction of Rs.5,000 was completely omitted from the books.")
    check("C4 ambiguous omission REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("C4 zero journal lines", lines(r) == [], str(lines(r)))

    r = hardened_bookkeeping_outcome(
        "Goods returned by Y worth Rs.1,200 were completely omitted from "
        "the books.")
    check("C5 unsafe party token REVIEW_REQUIRED (15I-VY preserved)",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("C5 zero journal lines", lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# PART D - Rectification
# ---------------------------------------------------------------------------


def test_d_rectification():
    print("PART D - RECTIFICATION")
    # wrong account (implicit correct account from the transaction)
    r = hardened_bookkeeping_outcome(
        "Purchased goods from Rahul for Rs.20,000 but the entry was "
        "wrongly posted to Mohan's account.")
    check("D1 wrong account VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("D1 correction journal",
          lines(r) == [("Mohan", "20000"), ("Rahul", "20000")],
          str(lines(r)))
    model = discrepancy_of(r).get("correction_model") or {}
    check("D1 recorded->should model",
          any(x.get("account") == "Mohan" for x in model.get("recorded"))
          and any(x.get("account") == "Rahul" for x in model.get("should")),
          str(model))
    check("D1 no Suspense invented",
          model.get("suspense_used") is False
          and "Suspense" not in [l.get("account") for l in
                                 (r.get("debit_lines") or [])
                                 + (r.get("credit_lines") or [])],
          str(model))
    check("D1 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    # wrong amount
    r = hardened_bookkeeping_outcome(
        "Purchased goods from Rahul for Rs.20,000 but the entry was "
        "recorded at Rs.2,000.")
    check("D2 wrong amount VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("D2 difference correction",
          lines(r) == [("Purchases", "18000"), ("Rahul", "18000")],
          str(lines(r)))
    model = discrepancy_of(r).get("correction_model") or {}
    check("D2 model holds both amounts",
          any(str(x.get("amount")) == "2000"
              for x in model.get("recorded"))
          and any(str(x.get("amount")) == "20000"
                  for x in model.get("should")),
          str(model))
    check("D2 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    # wrong side (sale for cash wrongly debited to the party)
    r = hardened_bookkeeping_outcome(
        "Goods sold to Ram for cash Rs.10,000 were wrongly debited to "
        "Ram's account instead of crediting Sales.")
    check("D3 wrong side VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("D3 correction journal",
          lines(r) == [("Cash", "10000"), ("Ram", "10000")],
          str(lines(r)))
    check("D3 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    # complete omission framed as a rectification
    r = hardened_bookkeeping_outcome(
        "Goods purchased from Rahul for Rs.20,000 were completely omitted "
        "from the books. Rectify.")
    check("D4 complete omission rectification VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("D4 full entry generated",
          lines(r) == [("Purchases", "20000"), ("Rahul", "20000")],
          str(lines(r)))
    check("D4 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    # partial omission
    r = hardened_bookkeeping_outcome(
        "Purchased goods from Rahul for Rs.20,000 on credit but only "
        "Rs.8,000 was recorded in the books.")
    check("D5 partial omission VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("D5 shortfall correction",
          lines(r) == [("Purchases", "12000"), ("Rahul", "12000")],
          str(lines(r)))
    check("D5 case id",
          discrepancy_of(r).get("case") == "partial_omission",
          str(discrepancy_of(r).get("case")))
    check("D5 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    # valid Suspense: trial-balance difference explicitly established
    r = hardened_bookkeeping_outcome(
        "The trial balance did not tally. The Sales book was undercast by "
        "Rs.500.")
    check("D6 valid Suspense VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("D6 suspense correction",
          lines(r) == [("Suspense", "500"), ("Sales", "500")],
          str(lines(r)))
    model = discrepancy_of(r).get("correction_model") or {}
    check("D6 Suspense used (TB discrepancy established)",
          model.get("suspense_used") is True, str(model))
    check("D6 balanced + invariants",
          balanced(r) and invariants_zero(r), "safety")

    # unnecessary Suspense: direct correction, no Suspense
    r = hardened_bookkeeping_outcome(
        "Purchased goods from Rahul for Rs.20,000 but the entry was "
        "wrongly posted to Mohan's account. Rectify using the Suspense "
        "Account.")
    check("D7 unnecessary Suspense VERIFIED (direct correction)",
          r.get("status") == VERIFIED, r.get("status"))
    check("D7 no Suspense posted",
          "Suspense" not in [l.get("account") for l in
                             (r.get("debit_lines") or [])
                             + (r.get("credit_lines") or [])],
          str(lines(r)))
    model = discrepancy_of(r).get("correction_model") or {}
    check("D7 Suspense NOT used",
          model.get("suspense_used") is False, str(model))
    check("D7 note explains why no Suspense",
          "Suspense is NOT used" in str(discrepancy_of(r).get("notes")),
          str(discrepancy_of(r).get("notes")))

    # no error established
    r = hardened_bookkeeping_outcome(
        "Purchased goods from Ram for Rs.10,000. Rectify.")
    check("D8 no error REVIEW_REQUIRED",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("D8 zero journal lines", lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# PART E - Safety invariant sweep
# ---------------------------------------------------------------------------


def test_e_safety():
    print("PART E - SAFETY INVARIANT SWEEP")
    verified_corpus = [
        "Received a cheque from Ram for Rs.10,000 which was later "
        "dishonoured.",
        "Sold goods to Ram for Rs.10,000 and received a cheque which was "
        "dishonoured.",
        "Cheque issued to Rahul for Rs.10,000 but not yet presented for "
        "payment.",
        "Deposited a cheque received from Ram for Rs.10,000 which has not "
        "yet been cleared by the bank.",
        "Bank charged Rs.200 as bank charges which were not recorded in "
        "the cash book.",
        "Bank credited Rs.500 as interest which was not recorded in the "
        "cash book.",
        "Insurance premium of Rs.1,200 was paid directly by the bank "
        "under standing instructions and not recorded in the cash book.",
        "Purchased goods from Rahul for Rs.20,000 which was completely "
        "omitted from the books.",
        "Sold goods to Ram for Rs.10,000 which was omitted from the books.",
        "Goods returned by Mohan worth Rs.1,200 were completely omitted "
        "from the books.",
        "Purchased goods from Rahul for Rs.20,000 but the entry was "
        "wrongly posted to Mohan's account.",
        "Purchased goods from Rahul for Rs.20,000 but the entry was "
        "recorded at Rs.2,000.",
        "Goods sold to Ram for cash Rs.10,000 were wrongly debited to "
        "Ram's account instead of crediting Sales.",
        "Purchased goods from Rahul for Rs.20,000 on credit but only "
        "Rs.8,000 was recorded in the books.",
        "The trial balance did not tally. The Sales book was undercast by "
        "Rs.500.",
        "Purchased goods from Rahul for Rs.20,000 but the entry was "
        "wrongly posted to Mohan's account. Rectify using the Suspense "
        "Account.",
        "Received a cheque from Ram for Rs.10,000. The cheque was later "
        "dishonoured.",
    ]
    for i, q in enumerate(verified_corpus):
        r = hardened_bookkeeping_outcome(q)
        check(f"E.V.{i} VERIFIED ({q[:40]})",
              r.get("status") == VERIFIED, r.get("status"))
        check(f"E.V.{i} safety invariants zero ({q[:40]})",
              invariants_zero(r), str(invariants_of(r)))
        check(f"E.V.{i} balanced ({q[:40]})",
              balanced(r), "unbalanced")
        check(f"E.V.{i} deterministic ({q[:40]})",
              json.dumps(hardened_bookkeeping_outcome(q), default=str,
                         sort_keys=True)
              == json.dumps(r, default=str, sort_keys=True),
              "mismatch on repeated run")
        check(f"E.V.{i} flow verdict == authority ({q[:40]})",
              run_fyjc_accounting_flow(q).get("status")
              == r.get("status"),
              "flow mismatch")
        # no invented account: every posted account is canonical or a
        # stated party
        for side in ("debit_lines", "credit_lines"):
            for ln in r.get(side) or []:
                acct = ln.get("account") or ""
                check(f"E.V.{i} canonical account {acct} ({q[:40]})",
                      acct in ("Bank", "Cash", "Sales", "Purchases",
                               "Sales Returns", "Purchase Returns",
                               "Interest Received", "Interest Paid",
                               "Bank Charges", "Insurance", "Electricity",
                               "Rent", "Salaries", "Suspense",
                               "Ram", "Rahul", "Mohan"),
                      acct)

    # refusal corpus: never a VERIFIED with dropped facts
    refusal_corpus = [
        "Ram's cheque of Rs.5,000 was dishonoured.",
        "The cheque received from Ram was dishonoured.",
        "Received a cheque from Ram for Rs.10,000 which was dishonoured "
        "twice.",
        "A transaction of Rs.5,000 was completely omitted from the books.",
        "Goods returned by Y worth Rs.1,200 were completely omitted from "
        "the books.",
        "Purchased goods from Ram for Rs.10,000. Rectify.",
        "From the following particulars prepare a bank reconciliation "
        "statement: unpresented cheques Rs.5,000, uncleared deposits "
        "Rs.3,000.",
    ]
    for i, q in enumerate(refusal_corpus):
        r = hardened_bookkeeping_outcome(q)
        check(f"E.R.{i} refuses ({q[:40]})",
              r.get("status") != VERIFIED, r.get("status"))
        check(f"E.R.{i} zero journal lines ({q[:40]})",
              lines(r) == [], str(lines(r)))
        check(f"E.R.{i} deterministic ({q[:40]})",
              json.dumps(hardened_bookkeeping_outcome(q), default=str,
                         sort_keys=True)
              == json.dumps(r, default=str, sort_keys=True),
              "mismatch on repeated run")


# ---------------------------------------------------------------------------
# PART F - real Streamlit Study/Verify path (AppTest)
# ---------------------------------------------------------------------------


def test_f_streamlit():
    print("PART F - REAL STREAMLIT STUDY/VERIFY PATH")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("F.0 apptest available", False, str(exc))
        return
    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    check("F.1 app entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("F.2 FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])
    at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()

    def ask(q):
        at.text_area(key="fte_fyjc_question").set_value(q).run()
        at.button(key="fte_fyjc_go").click().run()
        return " ".join(m.value for m in at.markdown)

    verified_ui_cases = [
        ("F.3 dishonour VERIFIED (no Almost there)",
         "Received a cheque from Ram for Rs.10,000 which was later "
         "dishonoured."),
        ("F.4 BRS timing difference VERIFIED (no Almost there)",
         "Cheque issued to Rahul for Rs.10,000 but not yet presented for "
         "payment."),
        ("F.5 omitted return VERIFIED (no Almost there)",
         "Goods returned by Mohan worth Rs.1,200 were completely omitted "
         "from the books."),
        ("F.6 rectification VERIFIED (no Almost there)",
         "Purchased goods from Rahul for Rs.20,000 but the entry was "
         "wrongly posted to Mohan's account."),
    ]
    for name, q in verified_ui_cases:
        md = ask(q)
        check(name, "VERIFIED" in md.upper()
              and "Almost there" not in md
              and not at.exception,
              [e.stack_trace for e in at.exception] + [md[:200]])

    md = ask("Ram's cheque of Rs.5,000 was dishonoured.")
    check("F.7 missing-history dishonour refuses in UI",
          "REVIEW" in md.upper() and "VERIFIED" not in md.upper()
          and "Almost there" not in md,
          md[:200])
    md = ask("Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately "
             "and Rs.5,000 remains outstanding.")
    check("F.8 contradiction still INVALID INPUT (MATH) in UI",
          "INVALID" in md.upper() and "VERIFIED" not in md.upper(),
          md[:200])


def main():
    test_a_brs()
    test_b_dishonour()
    test_c_omission()
    test_d_rectification()
    test_e_safety()
    test_f_streamlit()
    print(f"\n15I-DISC gate: {TOTAL[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
