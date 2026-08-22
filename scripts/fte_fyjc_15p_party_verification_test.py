#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-P - Study/Verify Verification Hardening + Natural-Language
Party Resolution
scripts/fte_fyjc_15p_party_verification_test.py

Proves the two 15I-P fixes on the REAL production path
(Study / Verify -> run_fyjc_student_flow -> run_fyjc_accounting_flow ->
hardened_bookkeeping_outcome -> reason_bk_question -> canonical journal
-> verify_journal_entry):

  A. Natural-language party resolution - ordinary personal names
     ('rahul', 'RAHUL', 'RaHuL', 'ravi kumar') are extracted
     deterministically from the surrounding transaction structure
     ('from <party>', 'received ... from <party>', 'paid ... to
     <party>', 'sold ... to <party>') and normalised to the canonical
     party account ('Rahul', 'Amit', 'Mehta', 'Kavita', 'Ravi Kumar').
     Ordinary words ('the seller', 'cash', 'the shop', 'the bank') can
     NEVER become a party - the question stays REVIEW_REQUIRED.

  B. Amount-exact Study/Verify verification - verify_journal_entry
     compares the student entry against the hardened canonical journal
     on account identity, debit/credit side, EXACT amount and line
     multiplicity (duplicate lines are never collapsed). A balanced
     entry with the right accounts but a wrong amount is INCORRECT.

  C. Historical preservation - the canonical 15I-L / 15I-O entries
     (trade discount, cash discount, GST, settlement) still verify
     CORRECT unchanged, and equivalent amount spellings parse equal.

  D. Safety invariants - unsafe confident = 0, invented accounts = 0,
     unbalanced VERIFIED = 0, ambiguous inputs never guessed, the
     legacy classifier is never the verification authority, canonical
     journal is never editable through the flow.

  E. Streamlit AppTest - the REAL Study / Verify page solves a
     lowercase-party question and verifies an exact journal
     (CORRECT) and a wrong-amount journal (INCORRECT).

Exit code 0 = all checks pass.
"""

import os
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_accounting import verify_journal_entry  # noqa: E402
from backend.maths.fyjc_bk_reasoning import reason_bk_question  # noqa: E402
from backend.maths.fyjc_student_flow import run_fyjc_student_flow  # noqa: E402

FAILURES = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    if not ok:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL [{name}] {detail}")
    else:
        print(f"OK [{name}]")


def canonical_lines(res):
    """(account, amount) pairs for the hardened canonical journal."""
    lines = []
    for line in (res.get("debit_lines") or []) + (res.get("credit_lines") or []):
        account = line.get("account")
        if account:
            lines.append((str(account), str(line.get("amount"))))
    return sorted(lines)


def flow_lines(flow):
    """(account, amount) pairs from the flow's outcome (UI reference)."""
    outcome = flow.get("outcome") or {}
    lines = []
    for line in (outcome.get("debit_lines") or []) + \
            (outcome.get("credit_lines") or []):
        account = line.get("account")
        if account:
            lines.append((str(account), str(line.get("amount"))))
    return sorted(lines)


def test_a_party_resolution():
    print("PART A - NATURAL-LANGUAGE PARTY RESOLUTION (real production path)")
    # (question, expected_status, expected_lines [(side, account, amount)])
    resolved = [
        ("Purchased goods from rahul on credit for Rs.10,000.",
         [("debit", "Purchases", "10000"), ("credit", "Rahul", "10000")]),
        ("Purchased goods from RAHUL on credit for Rs.10,000.",
         [("debit", "Purchases", "10000"), ("credit", "Rahul", "10000")]),
        ("Purchased goods from RaHuL on credit for Rs.10,000.",
         [("debit", "Purchases", "10000"), ("credit", "Rahul", "10000")]),
        ("Purchased goods from Rahul on credit for Rs.10,000.",
         [("debit", "Purchases", "10000"), ("credit", "Rahul", "10000")]),
        ("Purchased goods from ravi kumar on credit for Rs.10,000.",
         [("debit", "Purchases", "10000"),
          ("credit", "Ravi Kumar", "10000")]),
        ("Received Rs.10,000 from amit.",
         [("debit", "Cash", "10000"), ("credit", "Amit", "10000")]),
        ("Received Rs.10,000 from AMIT.",
         [("debit", "Cash", "10000"), ("credit", "Amit", "10000")]),
        ("Paid Rs.9,500 to mehta.",
         [("debit", "Mehta", "9500"), ("credit", "Cash", "9500")]),
        ("Paid Rs.9,500 to MEHTA.",
         [("debit", "Mehta", "9500"), ("credit", "Cash", "9500")]),
        ("Sold goods to kavita on credit for Rs.8,000.",
         [("debit", "Kavita", "8000"), ("credit", "Sales", "8000")]),
        ("Sold goods to KaViTa on credit for Rs.8,000.",
         [("debit", "Kavita", "8000"), ("credit", "Sales", "8000")]),
    ]
    for q, expected in resolved:
        flow = run_fyjc_student_flow(q)
        check(f"resolve[{q[:44]}]",
              flow.get("status") == "VERIFIED" and flow.get("flow") == "accounting",
              f"flow={flow.get('status')}")
        if flow.get("status") == "VERIFIED":
            actual = [(l.get("side"), l.get("account"), str(l.get("amount")))
                      for l in (flow.get("outcome") or {}).get("debit_lines") or []
                      ] + [(l.get("side"), l.get("account"), str(l.get("amount")))
                           for l in (flow.get("outcome") or {}).get("credit_lines") or []]
            check(f"canonical[{q[:44]}]", actual == expected,
                  f"{actual} != {expected}")

    # ordinary words can never become a party (deterministic refusal)
    refused = [
        "Purchased goods from the seller on credit for Rs.10,000.",
        "Purchased goods from cash on credit for Rs.10,000.",
        "Sold goods to the shop on credit for Rs.10,000.",
        "Received Rs.10,000 from the bank.",
        "Paid Rs.9,500 to the firm.",
        "Purchased goods from seller on credit for Rs.10,000.",
    ]
    for q in refused:
        flow = run_fyjc_student_flow(q)
        hard = reason_bk_question(q)
        check(f"refuse[{q[:44]}]",
              flow.get("status") in ("REVIEW_REQUIRED", "BLOCKED")
              and hard.get("status") not in ("VERIFIED",),
              f"flow={flow.get('status')} hardened={hard.get('status')}")

    # a lowercase party must never leak into a non-party position
    leak = run_fyjc_student_flow("Purchased goods for cash Rs.10,000.")
    check("no party invented for a cash purchase",
          leak.get("status") == "VERIFIED"
          and all((l.get("account") or "") != "cash"
                  for l in ((leak.get("outcome") or {}).get("debit_lines") or [])
                  + ((leak.get("outcome") or {}).get("credit_lines") or [])),
          str(leak.get("status")))


def test_b_amount_exact_verification():
    print("PART B - AMOUNT-EXACT VERIFICATION (verify your answer)")
    desc = ("Received Rs.10,000 from Amit in full settlement of his "
            "account of Rs.10,500.")
    exact = {
        "debits": [{"account": "Cash", "amount": 10000},
                   {"account": "Discount Allowed", "amount": 500}],
        "credits": [{"account": "Amit", "amount": 10500}],
    }
    r = verify_journal_entry(desc, exact)
    check("exact canonical entry -> CORRECT", r.get("verdict") == "CORRECT",
          str(r.get("verdict")))

    # equivalent amount spellings parse to the same Decimal
    spelled = {
        "debits": [{"account": "Cash", "amount": "10,000"},
                   {"account": "Discount Allowed", "amount": "500.00"}],
        "credits": [{"account": "Amit", "amount": "10500"}],
    }
    r = verify_journal_entry(desc, spelled)
    check("equivalent amount spellings -> CORRECT",
          r.get("verdict") == "CORRECT", str(r.get("verdict")))

    wrong_amount = {
        "debits": [{"account": "Cash", "amount": 9000},
                   {"account": "Discount Allowed", "amount": 500}],
        "credits": [{"account": "Amit", "amount": 9500}],
    }
    r = verify_journal_entry(desc, wrong_amount)
    check("correct accounts, wrong amount -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))
    check("wrong-amount explanation names the amount",
          "Expected" in (r.get("why_not") or ""),
          str(r.get("why_not"))[:160])

    reversed_sides = {
        "debits": [{"account": "Amit", "amount": 10500}],
        "credits": [{"account": "Cash", "amount": 10000},
                    {"account": "Discount Allowed", "amount": 500}],
    }
    r = verify_journal_entry(desc, reversed_sides)
    check("reversed debit/credit -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    wrong_account = {
        "debits": [{"account": "Cash", "amount": 10000},
                   {"account": "Discount Received", "amount": 500}],
        "credits": [{"account": "Amit", "amount": 10500}],
    }
    r = verify_journal_entry(desc, wrong_account)
    check("wrong account -> INCORRECT", r.get("verdict") == "INCORRECT",
          str(r.get("verdict")))

    missing_line = {
        "debits": [{"account": "Cash", "amount": 10000}],
        "credits": [{"account": "Amit", "amount": 10000}],
    }
    r = verify_journal_entry(desc, missing_line)
    check("missing line -> INCORRECT", r.get("verdict") == "INCORRECT",
          str(r.get("verdict")))

    extra_line = {
        "debits": [{"account": "Cash", "amount": 10000},
                   {"account": "Discount Allowed", "amount": 500},
                   {"account": "Drawings", "amount": 100}],
        "credits": [{"account": "Amit", "amount": 10600}],
    }
    r = verify_journal_entry(desc, extra_line)
    check("extra line -> INCORRECT", r.get("verdict") == "INCORRECT",
          str(r.get("verdict")))

    # duplicate lines are never collapsed into one
    duplicated = {
        "debits": [{"account": "Cash", "amount": 5000},
                   {"account": "Cash", "amount": 5000},
                   {"account": "Discount Allowed", "amount": 500}],
        "credits": [{"account": "Amit", "amount": 10500}],
    }
    r = verify_journal_entry(desc, duplicated)
    check("duplicate lines never collapsed -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    unbalanced = {
        "debits": [{"account": "Cash", "amount": 9000}],
        "credits": [{"account": "Amit", "amount": 10000}],
    }
    r = verify_journal_entry(desc, unbalanced)
    check("unbalanced entry -> INCORRECT", r.get("verdict") == "INCORRECT",
          str(r.get("verdict")))

    # a genuinely ambiguous canonical transaction never manufactures an
    # expected journal: balanced stays BALANCED, never CORRECT/INCORRECT
    amb = verify_journal_entry(
        "Purchased goods for Rs.20,000 and paid Rs.10,000.",
        {"debits": [{"account": "Purchases", "amount": 20000}],
         "credits": [{"account": "Cash", "amount": 20000}]},
    )
    check("ambiguous canonical -> never CORRECT",
          amb.get("verdict") in ("BALANCED", "REFUSED"),
          str(amb.get("verdict")))


def test_c_historical_preservation():
    print("PART C - HISTORICAL CANONICAL ENTRIES STILL CORRECT")
    cases = [
        ("Purchased goods from Rohan Rs.20,000 at 10% trade discount.",
         {"debits": [{"account": "Purchases", "amount": 18000}],
          "credits": [{"account": "Rohan", "amount": 18000}]}),
        ("Sold goods to Mohan for cash Rs.20,000 allowed 2% cash discount.",
         {"debits": [{"account": "Cash", "amount": 19600},
                     {"account": "Discount Allowed", "amount": 400}],
          "credits": [{"account": "Sales", "amount": 20000}]}),
        ("Purchased goods from Rahul for Rs.20,000 plus CGST 9% and SGST 9%.",
         {"debits": [{"account": "Purchases", "amount": 20000},
                     {"account": "Input CGST", "amount": 1800},
                     {"account": "Input SGST", "amount": 1800}],
          "credits": [{"account": "Rahul", "amount": 23600}]}),
        ("Received Rs.10,000 from Amit in full settlement of his account "
         "of Rs.10,500.",
         {"debits": [{"account": "Cash", "amount": 10000},
                     {"account": "Discount Allowed", "amount": 500}],
          "credits": [{"account": "Amit", "amount": 10500}]}),
    ]
    for q, entry in cases:
        r = verify_journal_entry(q, entry)
        check(f"historical[{q[:44]}]",
              r.get("verdict") == "CORRECT", str(r.get("verdict")))


def test_d_safety_invariants():
    print("PART D - SAFETY INVARIANTS")
    cases = [
        "Purchased goods from rahul on credit for Rs.10,000.",
        "Received Rs.10,000 from amit.",
        "Paid Rs.9,500 to mehta.",
        "Purchased goods from Rohan Rs.20,000 at 10% trade discount.",
        "Purchased goods for Rs.20,000 and paid Rs.10,000.",
        "Purchased goods for cash on credit from Rahul Rs.20,000.",
        "Purchased goods from the seller on credit for Rs.10,000.",
        "Received Rs.10,000 from the bank.",
        "Sold goods to the shop on credit for Rs.10,000.",
        "Purchased goods Rs.10,000.",
    ]
    invented = []
    unbalanced = []
    overrides = []
    guessed = []
    for q in cases:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        if flow.get("flow") != "accounting":
            continue
        # the flow must never reinterpret a hardened status
        if flow.get("status") != hard.get("status"):
            overrides.append((q, flow.get("status"), hard.get("status")))
        # a VERIFIED flow while the hardened engine refuses = unsafe confidence
        if flow.get("status") == "VERIFIED" and hard.get("status") != "VERIFIED":
            guessed.append((q, hard.get("status")))
        if hard.get("status") != "VERIFIED":
            continue
        canon_accounts = {
            str(l.get("account"))
            for l in (hard.get("debit_lines") or []) +
            (hard.get("credit_lines") or [])
        }
        outcome = flow.get("outcome") or {}
        for line in (outcome.get("debit_lines") or []) + \
                (outcome.get("credit_lines") or []):
            if str(line.get("account")) not in canon_accounts:
                invented.append((q, line.get("account")))
        ver = flow.get("verification") or {}
        if ver:
            td, tc = ver.get("total_debit"), ver.get("total_credit")
            if td is None or tc is None or abs(float(td) - float(tc)) > 1e-6:
                unbalanced.append((q, td, tc))
    check("no invented accounts", not invented, str(invented[:3]))
    check("no unbalanced VERIFIED journals", not unbalanced,
          str(unbalanced[:3]))
    check("legacy classifier never overrides hardened status", not overrides,
          str(overrides[:3]))
    check("unsafe confident = 0", not guessed, str(guessed[:3]))

    # canonical journal not editable through the flow
    hard = reason_bk_question("Purchased goods from rahul on credit for "
                              "Rs.10,000.")
    flow = run_fyjc_student_flow("Purchased goods from rahul on credit for "
                                 "Rs.10,000.")
    check("canonical journal not editable",
          flow_lines(flow) == canonical_lines(hard),
          f"{flow_lines(flow)} vs {canonical_lines(hard)}")

    # ambiguous inputs are never guessed (explicit safety wording)
    for q in ["Purchased goods for Rs.20,000 and paid Rs.10,000.",
              "Purchased goods for cash on credit from Rahul Rs.20,000."]:
        flow = run_fyjc_student_flow(q)
        check(f"ambiguous never guessed[{q[:40]}]",
              flow.get("status") == "REVIEW_REQUIRED",
              str(flow.get("status")))


def test_e_apptest():
    print("PART E - STREAMLIT APPTEST (real Study / Verify page)")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("apptest available", False, str(exc))
        return
    app = "app (1) (9).py"
    at = AppTest.from_file(app, default_timeout=120)
    at.run()
    check("app entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])

    # lowercase party through the real page
    at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()
    at.text_area(key="fte_fyjc_question").set_value(
        "Purchased goods from rahul on credit for Rs.10,000."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    check("lowercase question renders", not at.exception,
          [e.stack_trace for e in at.exception])
    text = " ".join(m.value for m in at.markdown)
    check("lowercase question shows VERIFIED", "VERIFIED" in text, text[:200])
    check("lowercase party normalised to Rahul", "Rahul" in text, text[:200])

    # exact journal -> CORRECT
    at.text_input(key="fte_fyjc_jd1a").set_value("Purchases")
    at.text_input(key="fte_fyjc_jd1v").set_value("10000")
    at.text_input(key="fte_fyjc_jc1a").set_value("Rahul")
    at.text_input(key="fte_fyjc_jc1v").set_value("10000")
    at.button(key="fte_fyjc_jv_btn").click().run()
    check("exact journal verified", not at.exception,
          [e.stack_trace for e in at.exception])
    success_text = " ".join(s.value for s in at.success)
    check("exact journal -> CORRECT message", "correct" in success_text.lower(),
          success_text[:200])

    # wrong-amount journal -> INCORRECT
    at.text_input(key="fte_fyjc_jd1a").set_value("Purchases")
    at.text_input(key="fte_fyjc_jd1v").set_value("9000")
    at.text_input(key="fte_fyjc_jc1a").set_value("Rahul")
    at.text_input(key="fte_fyjc_jc1v").set_value("9000")
    at.button(key="fte_fyjc_jv_btn").click().run()
    check("wrong-amount journal verified", not at.exception,
          [e.stack_trace for e in at.exception])
    error_text = " ".join(e.value for e in at.error)
    check("wrong-amount journal -> INCORRECT", "INCORRECT" in error_text
          or "expected" in error_text.lower(), error_text[:200])


def main():
    test_a_party_resolution()
    test_b_amount_exact_verification()
    test_c_historical_preservation()
    test_d_safety_invariants()
    test_e_apptest()

    failed = len(FAILURES)
    print("=" * 72)
    if FAILURES:
        for f in FAILURES:
            print("FAILED:", f)
    print(f"15I-P gate: {TOTAL[0]} checks, {failed} failed")
    print("ALL PASS" if not FAILURES else "FAILURES PRESENT")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
