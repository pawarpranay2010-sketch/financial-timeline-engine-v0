#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-UZ - Direction & Amount-Safety Gate
scripts/fte_fyjc_15uz_direction_safety_test.py

Locks in the Sprint 15I-UZ fix set that removes the remaining
incorrect-confident (Category D) outputs found by the 15I-TY audit:

  D1. DIRECTION. The transaction direction is decided by the VERB before
      any word-list match. A sale sentence ('Sold goods worth Rs.X to
      <party>') can never fall through to the 'goods worth' PURCHASE
      pattern (previously: a sale journaled as Purchases), and a sale
      with a purchase PROVENANCE clause ('Sold goods [purchased from X]
      ... to Y') resolves as a SALE whose party is the BUYER - never the
      supplier, and never a name artifact like 'Mr. Roger Federer Of'.
  D2. RATES/ABBREVIATIONS. 'T.D.'/'C.D.' are trade/cash-discount
      abbreviations - '12% T.D.' applies as a trade discount (net
      Rs.26,400 on a Rs.30,000 list price), and the dotted abbreviation
      is never a sentence boundary.
  D3. PROFIT. 'at Y% profit on cost price' is applied (cost x (1 + Y%)),
      'profit on selling price' as cost / (1 - Y%). A profit wording
      whose percentage or convention cannot be read deterministically
      forces REVIEW_REQUIRED - it is never silently dropped.
  D4. NO INVENTED DISCOUNT. A partial receipt/payment 'against his
      account' / 'in part payment of his account' is a partial
      settlement - the shortfall is NEVER an invented Discount
      Allowed/Received entry. 'Full settlement' wording still derives a
      discount deterministically from the two stated figures.
  D5. PAYMENT STEPS CONSUMED. A stated cheque/fraction payment step is
      consumed ('issued a cheque in his favour for 50% of the amount' ->
      net purchase split into Bank + creditor). A purchase paid in full
      by cheque credits Bank (the creditor is settled). A cheque ISSUED
      BY THE CUSTOMER in a sale is received only on deposit and is never
      booked as the business's own payment.
  D6. RATE CONSUMPTION INVARIANT. Every stated rate must be assigned a
      deterministic role (trade discount, cash discount, payment
      fraction, profit, or GST components) - an unassigned rate forces
      REVIEW_REQUIRED at the accounting authority boundary.

Historical behavior is locked: TD netting, CD settlements, explicit
CGST+SGST, partial settlements, multi-transaction return chains, and
'Started business' (Cash Dr / Capital Cr) are unchanged, and the 15J
corpus differential stays byte-identical.

Exit code 0 = all checks pass.
"""

import os
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_accounting import (  # noqa: E402
    hardened_bookkeeping_outcome,
)
from backend.maths.fyjc_bk_reasoning import reason_bk_question  # noqa: E402
from backend.maths.fyjc_student_flow import (  # noqa: E402
    run_fyjc_accounting_flow,
    run_fyjc_student_flow,
)

FAILURES = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    if not ok:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL [{name}] {detail}")
    else:
        print(f"OK [{name}]")


def lines(res):
    """(account, amount) pairs from a hardened result / flow outcome."""
    if "outcome" in res:
        res = res.get("outcome") or {}
    out = []
    for line in (res.get("debit_lines") or []) + (res.get("credit_lines") or []):
        account = line.get("account")
        if account:
            out.append((str(account), str(line.get("amount"))))
    return sorted(out)


def balanced(res):
    if "outcome" in res:
        res = res.get("outcome") or {}
    debits = sum(float(l.get("amount") or 0)
                 for l in (res.get("debit_lines") or []))
    credits = sum(float(l.get("amount") or 0)
                  for l in (res.get("credit_lines") or []))
    return abs(debits - credits) < 0.001


# ---------------------------------------------------------------------------
# Part A - D1 direction: a sale is never journaled as a purchase
# ---------------------------------------------------------------------------
def test_a_direction():
    print("PART A - SALE DIRECTION (D1)")
    r = reason_bk_question(
        "Sold goods to Mr. Andy Murray worth Rs.30,000 @ 12% T.D.")
    check("A.1 sale with trade discount VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))
    check("A.2 Sales credit 26,400",
          lines(r) == [("Mr. Andy Murray", "26400.00"),
                       ("Sales", "26400.00")], str(lines(r)))
    check("A.3 balanced", balanced(r), "")

    # the bare 'goods worth' purchase pattern must never fire on a sale
    r = reason_bk_question("Sold goods worth Rs.10,000 to Ram.")
    check("A.4 'sold goods worth ... to' VERIFIED (never a purchase)",
          r.get("status") == "VERIFIED", r.get("status"))
    check("A.5 Sales credit 10,000",
          lines(r) == [("Ram", "10000"), ("Sales", "10000")], str(lines(r)))


# ---------------------------------------------------------------------------
# Part B - D1 provenance sale: buyer is the party, supplier never is
# ---------------------------------------------------------------------------
def test_b_provenance():
    print("PART B - PROVENANCE SALE (D1)")
    q = ("Sold goods purchased from Mr. Roger Federer of Rs.25,000 "
         "(cost price) to Mr. Novak Djokovic at 30% profit on cost price.")
    r = reason_bk_question(q)
    check("B.1 provenance sale VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))
    check("B.2 buyer Dr / Sales Cr 32,500",
          lines(r) == [("Mr. Novak Djokovic", "32500.00"),
                       ("Sales", "32500.00")], str(lines(r)))
    check("B.3 supplier never on either side",
          "Roger" not in " ".join(a for a, _ in lines(r)), str(lines(r)))
    check("B.4 no name artifact ('Of')",
          all(not a.endswith(" Of") for a, _ in lines(r)), str(lines(r)))
    check("B.5 balanced", balanced(r), "")

    # the same structure through the production adapter layers
    for fn in (hardened_bookkeeping_outcome, run_fyjc_accounting_flow,
               run_fyjc_student_flow):
        out = fn(q)
        st = out.get("status") or (out.get("outcome") or {}).get("status")
        check(f"B.6 {fn.__name__} agrees VERIFIED", st == "VERIFIED",
              str(st))


# ---------------------------------------------------------------------------
# Part C - D3 profit conventions
# ---------------------------------------------------------------------------
def test_c_profit():
    print("PART C - PROFIT ON COST / SELLING (D3)")
    r = reason_bk_question(
        "Sold goods worth Rs.25,000 (cost price) to Mr. Novak Djokovic "
        "at 30% profit on cost price.")
    check("C.1 profit on cost VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))
    check("C.2 32,500 journal",
          lines(r) == [("Mr. Novak Djokovic", "32500.00"),
                       ("Sales", "32500.00")], str(lines(r)))

    r = reason_bk_question(
        "Sold goods worth Rs.25,000 (cost price) to Mr. Novak Djokovic "
        "at 25% profit on selling price.")
    check("C.3 profit on selling VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))
    check("C.4 33,333.33 journal",
          lines(r) == [("Mr. Novak Djokovic", "33333.33"),
                       ("Sales", "33333.33")], str(lines(r)))

    r = reason_bk_question("Sold goods to Ram worth Rs.10,000 at 20% profit.")
    check("C.5 ambiguous profit REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", r.get("status"))
    check("C.6 ambiguous profit zero lines", lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# Part D - D2 T.D. / C.D. abbreviations
# ---------------------------------------------------------------------------
def test_d_abbreviations():
    print("PART D - T.D. / C.D. ABBREVIATIONS (D2)")
    q = ("Purchased goods worth Rs.40,000 @ 15% T.D. from Mr. Roger "
         "Federer and issued a cheque in his favour for 50% of the amount.")
    r = reason_bk_question(q)
    check("D.1 T.D. + 50% cheque VERIFIED (one transaction)",
          r.get("status") == "VERIFIED", r.get("status"))
    check("D.2 net 34,000 split Bank 17,000 / creditor 17,000",
          lines(r) == [("Bank", "17000.00"),
                       ("Mr. Roger Federer", "17000.00"),
                       ("Purchases", "34000.00")], str(lines(r)))

    r = reason_bk_question(
        "Purchased goods worth Rs.40,000 @ 15% T.D. from Mr. Roger "
        "Federer and issued a cheque for the full amount.")
    check("D.3 full cheque with T.D. credits Bank",
          lines(r) == [("Bank", "34000.00"), ("Purchases", "34000.00")],
          str(lines(r)))


# ---------------------------------------------------------------------------
# Part E - D4 partial receipt/payment never invents a discount
# ---------------------------------------------------------------------------
def test_e_partial_settlement():
    print("PART E - PARTIAL SETTLEMENT, NO INVENTED DISCOUNT (D4)")
    r = reason_bk_question(
        "Sold goods to Ram Rs.12,000. Received Rs.5,000 from him against "
        "his account.")
    check("E.1 partial receipt VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))
    check("E.2 no Discount Allowed line",
          not any(a == "Discount Allowed" for a, _ in lines(r)),
          str(lines(r)))
    check("E.3 Cash Dr 5,000 / Ram Cr 5,000 on the receipt side",
          ("Cash", "5000") in lines(r) and ("Ram", "5000") in lines(r),
          str(lines(r)))

    r = reason_bk_question(
        "Sold goods to Ram Rs.12,000. Received Rs.5,000 from him in part "
        "payment of his account.")
    check("E.4 'in part payment of' no invented discount",
          not any(a == "Discount Allowed" for a, _ in lines(r)),
          str(lines(r)))

    # 'full settlement' with BOTH figures stated still derives the
    # discount deterministically (unchanged historical behavior).
    r = reason_bk_question(
        "Received Rs.9,500 from Rahul in full settlement of his account "
        "of Rs.10,000.")
    check("E.5 full settlement still derives Discount Allowed 500",
          lines(r) == [("Cash", "9500"), ("Discount Allowed", "500"),
                       ("Rahul", "10000")], str(lines(r)))


# ---------------------------------------------------------------------------
# Part F - D5 stated payment steps are consumed
# ---------------------------------------------------------------------------
def test_f_payment_steps():
    print("PART F - STATED PAYMENT STEPS CONSUMED (D5)")
    r = reason_bk_question(
        "Purchased goods worth Rs.40,000 @ 15% T.D. from Mr. Roger "
        "Federer and issued a cheque of Rs.17,000 in his favour.")
    check("F.1 explicit cheque amount consumed",
          ("Bank", "17000") in lines(r) and
          ("Mr. Roger Federer", "17000.00") in lines(r),
          str(lines(r)))

    r = reason_bk_question(
        "Purchased goods worth Rs.34,000 from Mr. Roger Federer and paid "
        "the full amount by cheque.")
    check("F.2 full payment by cheque credits Bank, not the creditor",
          lines(r) == [("Bank", "34000"), ("Purchases", "34000")],
          str(lines(r)))

    # a cheque ISSUED BY THE CUSTOMER is received only on deposit - it is
    # never booked as the business's own payment (the sale stays a
    # credit sale to the customer).
    r = reason_bk_question(
        "Sold goods to Mr. Andy Murray worth Rs.30,000. He issued a "
        "bearer cheque for the amount payable.")
    check("F.3 customer cheque never a business payment",
          lines(r) == [("Mr. Andy Murray", "30000"), ("Sales", "30000")],
          str(lines(r)))


# ---------------------------------------------------------------------------
# Part G - D6 rate-consumption invariant
# ---------------------------------------------------------------------------
def test_g_rate_consumption():
    print("PART G - RATE CONSUMPTION INVARIANT (D6)")
    r = reason_bk_question(
        "Purchased goods worth Rs.10,000 from Rahul on credit with a 4% "
        "charge.")
    check("G.1 unassigned rate REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", r.get("status"))
    check("G.2 unassigned rate zero lines", lines(r) == [], str(lines(r)))

    # GST rates are consumed by the GST authority - never "unassigned".
    r = reason_bk_question(
        "Purchased goods from Rahul on credit Rs.20,000 with CGST @ 9% "
        "and SGST @ 9%.")
    check("G.3 explicit CGST+SGST boundary agrees VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))


# ---------------------------------------------------------------------------
# Part H - historical behavior unchanged
# ---------------------------------------------------------------------------
def test_h_historical():
    print("PART H - HISTORICAL BEHAVIOR UNCHANGED")
    r = reason_bk_question(
        "Purchased goods at Rs.25,000 less 10% trade discount from Rahul "
        "on credit.")
    check("H.1 TD net 22,500 unchanged",
          lines(r) == [("Purchases", "22500.00"), ("Rahul", "22500.00")],
          str(lines(r)))

    r = reason_bk_question("Started business with cash Rs.50,000.")
    check("H.2 started business Cash Dr / Capital Cr",
          lines(r) == [("Capital", "50000"), ("Cash", "50000")],
          str(lines(r)))

    r = reason_bk_question("Brought additional capital in cash Rs.30,000.")
    check("H.3 additional capital Cash Dr / Capital Cr",
          lines(r) == [("Capital", "30000"), ("Cash", "30000")],
          str(lines(r)))

    r = reason_bk_question(
        "Mr. Novak Djokovic returned us goods worth Rs.6,500 (net), and "
        "the same were returned to Mr. Roger Federer.")
    check("H.4 return chain VERIFIED with both return entries",
          r.get("status") == "VERIFIED" and
          any(a.startswith("Sales Returns") for a, _ in lines(r)) and
          any(a.startswith("Purchase Returns") for a, _ in lines(r)),
          str(lines(r)))
    check("H.5 return chain balanced", balanced(r), "")

    r = reason_bk_question(
        "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000.")
    check("H.6 15I-S ambiguity still REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", r.get("status"))
    check("H.7 15I-S ambiguity zero lines", lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# Part I - safety invariants over the whole matrix
# ---------------------------------------------------------------------------
def test_i_safety():
    print("PART I - SAFETY INVARIANTS")
    cases = [
        "Sold goods to Mr. Andy Murray worth Rs.30,000 @ 12% T.D.",
        "Sold goods purchased from Mr. Roger Federer of Rs.25,000 (cost "
        "price) to Mr. Novak Djokovic at 30% profit on cost price.",
        "Sold goods worth Rs.10,000 to Ram.",
        "Sold goods to Ram Rs.12,000. Received Rs.5,000 from him against "
        "his account.",
        "Purchased goods worth Rs.40,000 @ 15% T.D. from Mr. Roger "
        "Federer and issued a cheque in his favour for 50% of the amount.",
        "Purchased goods worth Rs.34,000 from Mr. Roger Federer and paid "
        "the full amount by cheque.",
        "Sold goods to Ram worth Rs.10,000 at 20% profit.",
        "Purchased goods worth Rs.10,000 from Rahul on credit with a 4% "
        "charge.",
        "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000.",
        "Started business with cash Rs.50,000.",
        "Received Rs.9,500 from Rahul in full settlement of his account "
        "of Rs.10,000.",
    ]
    unbalanced = 0
    lines_on_refusal = 0
    adapter_mismatch = 0
    for q in cases:
        r = reason_bk_question(q)
        if r.get("status") == "VERIFIED":
            if not balanced(r):
                unbalanced += 1
        else:
            if lines(r):
                lines_on_refusal += 1
        flow = run_fyjc_student_flow(q)
        flow_status = (flow.get("status")
                       or (flow.get("outcome") or {}).get("status"))
        if flow_status != r.get("status"):
            adapter_mismatch += 1
    check("I.1 zero unbalanced VERIFIED", unbalanced == 0, str(unbalanced))
    check("I.2 zero refusals with journal lines", lines_on_refusal == 0,
          str(lines_on_refusal))
    check("I.3 flow verdict == hardened verdict for every case",
          adapter_mismatch == 0, str(adapter_mismatch))

    # determinism across repeated runs
    q = ("Sold goods purchased from Mr. Roger Federer of Rs.25,000 (cost "
         "price) to Mr. Novak Djokovic at 30% profit on cost price.")
    first = lines(reason_bk_question(q))
    same = all(lines(reason_bk_question(q)) == first for _ in range(3))
    check("I.4 deterministic across repeated runs", same, "")


# ---------------------------------------------------------------------------
# Part J - real Streamlit Study/Verify path
# ---------------------------------------------------------------------------
def test_j_streamlit():
    print("PART J - REAL STREAMLIT STUDY/VERIFY PATH")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("J.0 apptest available", False, str(exc))
        return

    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    check("J.1 app entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("J.2 FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])

    at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()

    # provenance sale renders VERIFIED with the canonical journal
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold goods purchased from Mr. Roger Federer of Rs.25,000 (cost "
        "price) to Mr. Novak Djokovic at 30% profit on cost price.").run()
    at.button(key="fte_fyjc_go").click().run()
    check("J.3 provenance sale renders without exception", not at.exception,
          [e.stack_trace for e in at.exception])
    md = " ".join(m.value for m in at.markdown)
    check("J.4 provenance sale shows VERIFIED",
          "VERIFIED" in md.upper(), md[:160])

    # partial receipt against an account: no invented discount
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold goods to Ram Rs.12,000. Received Rs.5,000 from him against "
        "his account.").run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("J.5 partial receipt shows VERIFIED without Discount Allowed",
          "VERIFIED" in md.upper() and "DISCOUNT ALLOWED" not in md.upper(),
          md[:160])

    # the unresolved multi-amount input still refuses with zero lines
    at.text_area(key="fte_fyjc_question").set_value(
        "Purchased goods for ₹20,000 from Rahul on credit and "
        "₹18,000.").run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("J.6 ambiguity refuses (no VERIFIED)",
          "VERIFIED" not in md.upper() and
          ("REVIEW" in md.upper() or "clar" in md), md[:160])


def main():
    test_a_direction()
    test_b_provenance()
    test_c_profit()
    test_d_abbreviations()
    test_e_partial_settlement()
    test_f_payment_steps()
    test_g_rate_consumption()
    test_h_historical()
    test_i_safety()
    test_j_streamlit()
    print(f"\n15I-UZ gate: {TOTAL[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
