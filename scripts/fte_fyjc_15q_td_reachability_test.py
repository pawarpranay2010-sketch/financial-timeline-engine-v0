#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-Q - Deterministic Trade-Discount Study/Verify Reachability Gate
scripts/fte_fyjc_15q_td_reachability_test.py

Proves that every supported Trade Discount (TD) form reaches the hardened
FT-E engine through the REAL Study / Verify production path:

  Streamlit UI -> run_fyjc_student_flow -> run_fyjc_accounting_flow ->
  hardened_bookkeeping_outcome -> reason_bk_question -> canonical journal

The 15I-Q diagnostic found that the exact reported failing input

  «purchased goods with a list price of ₹20,000 at 10% trade discount
    from rahul on credit»

ALREADY passes through the current production path (the lowercase-party
+ TD combination was made reachable by the 15I-P party-resolution fix).
No production fix is required; this gate permanently locks that
reachability in place so a future routing/party regression cannot
silently break it again.

Gate sections:

  A. Exact 15I-Q failing input - VERIFIED, Purchases Dr 18,000 / Rahul
     Cr 18,000, no stale '% is a rate/discount' concern, no separate
     Trade Discount account.
  B. Lowercase TD matrix - 'list price ... at 10%', 'less 10%', the
     established 15I-L 'less 10% TD' abbreviation, and capitalised
     equivalents all collapse to the same canonical net journal.
  C. Invalid / ambiguous TD - 110% TD refuses (never a negative
     purchase amount); ambiguous multi-amount refuses; no guessing.
  D. TD + GST - the 15I-K/15I-L supported case nets the TD before tax
     and stays VERIFIED (capitalised and lowercase party forms).
  E. Study/Verify exact amount verification (15I-P) - exact canonical
     journal -> CORRECT; wrong amount / wrong account / reversed
     direction -> INCORRECT.
  F. Cash-discount regression (15I-P) - 'received ... in full
     settlement' and 'paid ... in full settlement' keep the correct
     Discount Allowed / Discount Received direction.
  G. Natural-language party regression (15I-P) - lowercase and
     ALL-CAPS parties resolve; ordinary words ('the seller') refuse.
  H. Real Streamlit AppTest - the exact failing sentence solves
     VERIFIED on the real Study / Verify page with Rahul and 18,000,
     and the stale '10% is a rate/discount' warning is absent.

Exit code 0 = all checks pass.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_accounting import verify_journal_entry  # noqa: E402
from backend.maths.fyjc_bk_reasoning import reason_bk_question  # noqa: E402
from backend.maths.fyjc_student_flow import (  # noqa: E402
    build_understanding,
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
        print(f"OK  [{name}]")


def _norm_amount(value) -> Decimal:
    """Normalise an engine amount ('18000', '18000.00', Decimal) to its
    Decimal value so equivalent spellings compare equal."""
    return Decimal(str(value))


def flow_lines(flow):
    """(account, Decimal amount) pairs from the flow's UI outcome."""
    outcome = flow.get("outcome") or {}
    lines = []
    for line in (outcome.get("debit_lines") or []) + \
            (outcome.get("credit_lines") or []):
        account = line.get("account")
        if account:
            lines.append((str(account), _norm_amount(line.get("amount"))))
    return sorted(lines)


def hard_lines(res):
    lines = []
    for line in (res.get("debit_lines") or []) + (res.get("credit_lines") or []):
        account = line.get("account")
        if account:
            lines.append((str(account), _norm_amount(line.get("amount"))))
    return sorted(lines)


def canonical_q18k(party="Rahul"):
    return [("Purchases", Decimal("18000")), (party, Decimal("18000"))]


def test_a_exact_failing_input():
    print("PART A - EXACT 15I-Q FAILING INPUT (lowercase TD + party)")
    q = ("purchased goods with a list price of \u20b920,000 at 10% trade "
         "discount from rahul on credit")

    hard = reason_bk_question(q)
    check("A.1 hardened engine VERIFIED", hard.get("status") == "VERIFIED",
          str(hard.get("status")))

    flow = run_fyjc_student_flow(q)
    check("A.2 flow routes to hardened engine (status agrees)",
          flow.get("status") == hard.get("status"),
          f"flow={flow.get('status')} hardened={hard.get('status')}")
    check("A.3 flow VERIFIED through production path",
          flow.get("status") == "VERIFIED", str(flow.get("status")))
    check("A.4 canonical journal Purchases 18,000 / Rahul 18,000",
          flow_lines(flow) == canonical_q18k("Rahul"),
          str(flow_lines(flow)))

    u = build_understanding(q)
    stale = [c for c in (u.get("concerns") or [])
             if "rate/discount" in c or "registered formula" in c]
    check("A.5 stale '% is a rate/discount' concern absent",
          not stale, str(stale))

    all_accounts = [a for a, _ in hard_lines(hard)]
    check("A.6 no separate Trade Discount account posted",
          all(a.lower() != "trade discount" for a in all_accounts),
          str(all_accounts))


def test_b_lowercase_td_matrix():
    print("PART B - TD WORDING MATRIX (real production path)")
    cases = [
        # (label, question, party)
        ("B.1 lowercase 'list price at 10%'",
         "purchased goods with a list price of \u20b920,000 at 10% trade "
         "discount from rahul on credit", "Rahul"),
        ("B.2 capitalised 'list price at 10%'",
         "Purchased goods with a list price of \u20b920,000 at 10% trade "
         "discount from Rahul on credit.", "Rahul"),
        ("B.3 lowercase 'less 10%'",
         "purchased goods \u20b920,000 less 10% trade discount from rahul "
         "on credit", "Rahul"),
        ("B.4 capitalised 'less 10%'",
         "Purchased goods \u20b920,000 less 10% trade discount from Rahul "
         "on credit.", "Rahul"),
        ("B.5 lowercase 'less 10% TD' abbreviation",
         "purchased goods from rahul on credit \u20b920,000 less 10% TD",
         "Rahul"),
        ("B.6 15I-L 'less 10% TD' abbreviation (capitalised)",
         "Purchased goods from Ram on credit Rs.20,000 less 10% TD.",
         "Ram"),
        ("B.7 word-percent '10 percent'",
         "Purchased goods from Ram for cash Rs.20,000 less 10 percent "
         "trade discount.", "Cash"),
        ("B.8 explicit TD amount 'less Rs.2,000'",
         "Purchased goods from Ram for cash Rs.20,000 less Rs.2,000 trade "
         "discount.", "Cash"),
        ("B.9 credit sale at 10% TD",
         "Sold goods to Ram on credit Rs.30,000 at 10% trade discount.",
         "Ram"),
    ]
    for label, q, counterpart in cases:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        check(f"{label}: hardened VERIFIED",
              hard.get("status") == "VERIFIED", str(hard.get("status")))
        check(f"{label}: flow routes to hardened engine",
              flow.get("status") == hard.get("status"),
              f"flow={flow.get('status')} hardened={hard.get('status')}")
        if hard.get("status") == "VERIFIED":
            # the net is deterministic per 15I-L: 20,000 - 10% = 18,000
            # (30,000 - 10% = 27,000 for the sale); cash forms credit Cash.
            expected = {
                "B.9 credit sale at 10% TD": [("Ram", Decimal("27000")),
                                              ("Sales", Decimal("27000"))],
            }.get(label)
            if expected is None:
                expected = [("Purchases", Decimal("18000")),
                            (counterpart, Decimal("18000"))]
            check(f"{label}: canonical journal {expected[0][0]} 18,000 / "
                  f"{expected[1]}",
                  hard_lines(hard) == sorted(expected),
                  str(hard_lines(hard)))
            check(f"{label}: flow journal equals hardened journal",
                  flow_lines(flow) == sorted(expected),
                  f"{flow_lines(flow)}")


def test_c_invalid_and_ambiguous():
    print("PART C - INVALID / AMBIGUOUS TD SAFETY")
    # an impossible trade discount (>= list price) refuses - never a
    # negative or invalid purchase amount
    q = ("Purchased goods with a list price of \u20b920,000 at 110% "
         "trade discount from Rahul on credit")
    hard = reason_bk_question(q)
    flow = run_fyjc_student_flow(q)
    check("C.1 110% TD -> REVIEW_REQUIRED",
          hard.get("status") == "REVIEW_REQUIRED", str(hard.get("status")))
    check("C.2 flow refuses identically",
          flow.get("status") == hard.get("status"),
          f"flow={flow.get('status')} hardened={hard.get('status')}")
    check("C.3 refusal leaks no journal lines",
          len(hard_lines(hard)) == 0 and len(flow_lines(flow)) == 0,
          f"{hard_lines(hard)} {flow_lines(flow)}")
    check("C.4 refusal is honest (mentions impossible discount)",
          "impossible" in str(hard.get("why_not") or "").lower(),
          str(hard.get("why_not"))[:120])

    # ambiguous multi-amount: the wording never establishes cash vs credit
    amb = "Purchased goods for \u20b920,000 and paid \u20b910,000."
    hard = reason_bk_question(amb)
    flow = run_fyjc_student_flow(amb)
    check("C.5 ambiguous multi-amount -> REVIEW_REQUIRED",
          hard.get("status") == "REVIEW_REQUIRED",
          str(hard.get("status")))
    check("C.6 flow never guesses on ambiguity",
          flow.get("status") == hard.get("status")
          and flow.get("status") != "VERIFIED",
          f"flow={flow.get('status')} hardened={hard.get('status')}")

    # 15I-L established refusal: a no-party credit sale never guesses a party
    nop = "Sold goods worth Rs.30,000 at 10% trade discount on credit."
    hard = reason_bk_question(nop)
    check("C.7 no-party credit TD sale refuses (15I-L A.8)",
          hard.get("status") == "REVIEW_REQUIRED", str(hard.get("status")))


def test_d_td_gst():
    print("PART D - TD + GST (15I-K/15I-L supported case)")
    expected = [("Input CGST", Decimal("1620")), ("Input SGST", Decimal("1620")),
                ("Purchases", Decimal("18000")), ("Ram", Decimal("21240"))]
    cases = [
        ("D.1 capitalised TD + CGST/SGST",
         "Purchased goods from Ram on credit Rs.20,000 less 10% trade "
         "discount, CGST @ 9% and SGST @ 9%."),
        ("D.2 lowercase TD + CGST/SGST",
         "purchased goods from ram on credit rs.20,000 less 10% trade "
         "discount, cgst @ 9% and sgst @ 9%"),
    ]
    for label, q in cases:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        check(f"{label}: hardened VERIFIED",
              hard.get("status") == "VERIFIED", str(hard.get("status")))
        check(f"{label}: flow routes to hardened engine",
              flow.get("status") == hard.get("status"),
              f"flow={flow.get('status')} hardened={hard.get('status')}")
        check(f"{label}: GST nets the TD before tax (18,000 + 1,620 + 1,620 "
              "= 21,240)",
              hard_lines(hard) == sorted(expected),
              str(hard_lines(hard)))
        check(f"{label}: flow journal equals canonical",
              flow_lines(flow) == sorted(expected), str(flow_lines(flow)))


def test_e_amount_verification():
    print("PART E - STUDY/VERIFY EXACT AMOUNT VERIFICATION (15I-P)")
    q = ("Purchased goods with a list price of \u20b920,000 at 10% trade "
         "discount from Rahul on credit.")
    exact = {
        "debits": [{"account": "Purchases", "amount": 18000}],
        "credits": [{"account": "Rahul", "amount": 18000}],
    }
    r = verify_journal_entry(q, exact)
    check("E.1 exact canonical journal -> CORRECT",
          r.get("verdict") == "CORRECT", str(r.get("verdict")))

    wrong_amount = {
        "debits": [{"account": "Purchases", "amount": 20000}],
        "credits": [{"account": "Rahul", "amount": 20000}],
    }
    r = verify_journal_entry(q, wrong_amount)
    check("E.2 wrong amount (20,000) -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    wrong_account = {
        "debits": [{"account": "Purchases", "amount": 18000}],
        "credits": [{"account": "Amit", "amount": 18000}],
    }
    r = verify_journal_entry(q, wrong_account)
    check("E.3 wrong account (Amit) -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    reversed_sides = {
        "debits": [{"account": "Rahul", "amount": 18000}],
        "credits": [{"account": "Purchases", "amount": 18000}],
    }
    r = verify_journal_entry(q, reversed_sides)
    check("E.4 reversed direction -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    unbalanced = {
        "debits": [{"account": "Purchases", "amount": 18000}],
        "credits": [{"account": "Rahul", "amount": 17000}],
    }
    r = verify_journal_entry(q, unbalanced)
    check("E.5 unbalanced entry -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))


def test_f_cd_regression():
    print("PART F - CASH DISCOUNT REGRESSION (15I-P)")
    # receipt: Cash Dr / Discount Allowed Dr / party Cr
    q1 = ("Received \u20b910,000 from amit in full settlement of his "
          "account of \u20b910,500.")
    flow = run_fyjc_student_flow(q1)
    check("F.1 settlement receipt VERIFIED through flow",
          flow.get("status") == "VERIFIED", str(flow.get("status")))
    check("F.2 Cash 10,000 / Discount Allowed 500 / Amit 10,500",
          flow_lines(flow) == [("Amit", Decimal("10500")),
                               ("Cash", Decimal("10000")),
                               ("Discount Allowed", Decimal("500"))],
          str(flow_lines(flow)))

    # payment: party Dr / Cash Cr / Discount Received Cr
    q2 = ("Paid \u20b99,500 to mehta in full settlement of his account "
          "of \u20b910,000.")
    flow = run_fyjc_student_flow(q2)
    check("F.3 settlement payment VERIFIED through flow",
          flow.get("status") == "VERIFIED", str(flow.get("status")))
    check("F.4 Mehta 10,000 / Cash 9,500 / Discount Received 500",
          flow_lines(flow) == [("Cash", Decimal("9500")),
                               ("Discount Received", Decimal("500")),
                               ("Mehta", Decimal("10000"))],
          str(flow_lines(flow)))


def test_g_party_regression():
    print("PART G - NATURAL-LANGUAGE PARTY REGRESSION (15I-P)")
    cases = [
        ("G.1 lowercase party",
         "Purchased goods from rahul on credit for \u20b910,000.",
         [("Purchases", Decimal("10000")), ("Rahul", Decimal("10000"))]),
        ("G.2 ALL-CAPS party",
         "Received \u20b98,000 from AMIT in full settlement of his account "
         "of \u20b98,500.",
         [("Amit", Decimal("8500")), ("Cash", Decimal("8000")),
          ("Discount Allowed", Decimal("500"))]),
    ]
    for label, q, expected in cases:
        flow = run_fyjc_student_flow(q)
        check(f"{label}: VERIFIED", flow.get("status") == "VERIFIED",
              str(flow.get("status")))
        check(f"{label}: canonical journal",
              flow_lines(flow) == sorted(expected), str(flow_lines(flow)))

    refused = [
        "Purchased goods from the seller on credit for \u20b910,000.",
        "Purchased goods from cash on credit for \u20b910,000.",
        "Received \u20b910,000 from the bank.",
    ]
    for q in refused:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        check(f"G.3 ordinary word never a party [{q[:40]}]",
              hard.get("status") not in ("VERIFIED",)
              and flow.get("status") == hard.get("status"),
              f"flow={flow.get('status')} hardened={hard.get('status')}")


def test_h_apptest():
    print("PART H - REAL STREAMLIT STUDY/VERIFY APPTEST")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("apptest available", False, str(exc))
        return

    app = "app (1) (9).py"
    at = AppTest.from_file(app, default_timeout=120)
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

    def run_question(q, label):
        at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()
        at.text_area(key="fte_fyjc_question").set_value(q).run()
        at.button(key="fte_fyjc_go").click().run()
        check(f"{label} renders without exception", not at.exception,
              [e.stack_trace for e in at.exception])
        return " ".join(m.value for m in at.markdown)

    # The exact 15I-Q failing input solves on the real page.
    text = run_question(
        "purchased goods with a list price of \u20b920,000 at 10% trade "
        "discount from rahul on credit",
        "H.3 exact failing input")
    check("H.4 shows VERIFIED", "VERIFIED" in text, text[:200])
    check("H.5 shows Rahul (normalised party)", "Rahul" in text, text[:200])
    check("H.6 shows net amount 18,000", "18,000" in text, text[:200])
    check("H.7 stale rate/discount warning absent",
          "'10%' is a rate/discount" not in text
          and "will not compute a discounted amount" not in text,
          text[:200])

    # The lowercase CD settlement also solves on the real page.
    text = run_question(
        "Received \u20b910,000 from amit in full settlement of his account "
        "of \u20b910,500.",
        "H.8 CD settlement")
    check("H.9 CD shows VERIFIED", "VERIFIED" in text, text[:200])
    check("H.10 CD shows Discount Allowed", "Discount Allowed" in text,
          text[:200])


def main():
    test_a_exact_failing_input()
    test_b_lowercase_td_matrix()
    test_c_invalid_and_ambiguous()
    test_d_td_gst()
    test_e_amount_verification()
    test_f_cd_regression()
    test_g_party_regression()
    test_h_apptest()

    failed = len(FAILURES)
    print("=" * 72)
    if FAILURES:
        for f in FAILURES:
            print("FAILED:", f)
    print(f"15I-Q gate: {TOTAL[0]} checks, {failed} failed")
    print("ALL PASS" if not FAILURES else "FAILURES PRESENT")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
