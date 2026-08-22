#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-O - Unified Bookkeeping Verification Gate
scripts/fte_fyjc_15o_unified_verification_test.py

Proves that the FYJC Study / Verify bookkeeping flow uses the SAME
hardened Platrixa engine (backend.maths.fyjc_bk_reasoning.reason_bk_question)
that the QuestionBank / PracticeEngine path uses - there is ONE
authoritative bookkeeping reasoning path for the FYJC product.

Sections:
  A. Routing matrix - every Study/Verify input routes through the REAL
     production path (run_fyjc_student_flow) and its status matches
     reason_bk_question exactly (VERIFIED / REVIEW_REQUIRED / BLOCKED /
     NOT_SUPPORTED are never reinterpreted).
  B. Canonical journal equality - the flow's outcome (the UI's reference
     entries) is EXACTLY the hardened canonical journal (same accounts,
     sides, amounts). The adapter translates presentation, never
     accounting meaning.
  C. Journal-entry verification - the "verify your answer" check
     (verify_journal_entry) compares the student entry against the
     hardened canonical reference, not the legacy classifier.
  D. Safety invariants - no invented accounts, no unbalanced VERIFIED
     journals, refusals preserved, legacy classifier cannot override the
     hardened engine, canonical journal cannot be edited through the
     flow, ambiguous inputs are never guessed.
  E. Adversarial inputs - wrong amounts, reversed debit/credit, missing
     party, contradictory settlement, unsupported GST, ambiguous
     discount, multiple transactions, malformed text.
  F. Streamlit AppTest - the REAL app renders a settlement + GST +
     trade-discount question through the Study / Verify page with the
     hardened engine's canonical journal.

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


def flow_outcome_lines(flow):
    """(account, amount) pairs from the flow's outcome (UI reference)."""
    outcome = flow.get("outcome") or {}
    lines = []
    for line in (outcome.get("debit_lines") or []) + \
            (outcome.get("credit_lines") or []):
        account = line.get("account")
        if account:
            lines.append((str(account), str(line.get("amount"))))
    return sorted(lines)


def step_five(flow):
    for step in flow.get("steps") or []:
        if step.get("number") == 5:
            return step.get("body") or []
    return []


def test_a_routing_matrix():
    print("PART A - ROUTING MATRIX (real production path)")
    cases = [
        # Sprint-required real-world cases A-G
        "Purchased furniture for cash \u20b915,000.",
        "Purchased goods from Rohan on credit for \u20b920,000.",
        "Sold goods to Amit on credit for \u20b912,000.",
        "Received \u20b910,000 from Amit in full settlement of his account "
        "of \u20b910,500.",
        "Paid rent \u20b93,000 by cash.",
        "Received \u20b99,500 from Rahul in full settlement of his account "
        "of \u20b910,000.",
        "Paid \u20b99,500 to Mehta in full settlement of his account of "
        "\u20b910,000.",
        # GST (15I-K surface)
        "Purchased goods from Rahul for Rs.20,000 plus CGST 9% and SGST 9%.",
        "Sold goods to Amit Rs.30,000 plus IGST 18% on credit.",
        # Trade discount (15E/15I-L surface)
        "Purchased goods from Rohan Rs.20,000 at 10% trade discount.",
        # Cash discount (15E/15I-L surface)
        "Sold goods to Mohan for cash Rs.20,000 allowed 2% cash discount.",
        # Multi-transaction
        "Purchased goods from Rohan Rs.10,000; sold goods to Amit Rs.8,000 "
        "for cash.",
        # Natural-language variant
        "bought furniture for cash rs 15000 from sharma",
        "Purchased goods from Rahul on credit for Rs.10,000. Paid him "
        "Rs.4,000.",
        # Ambiguous / refusal surfaces
        "Purchased goods Rs.10,000.",
        "Sold goods to Ram Rs.12,000 for cash.",
        "Prepare a Balance Sheet of the partnership firm as at 31 March.",
    ]
    for q in cases:
        flow = run_fyjc_student_flow(q)
        hard = reason_bk_question(q)
        f_status = flow.get("status")
        h_status = hard.get("status")
        if f_status in ("BLOCKED", "REVIEW_REQUIRED", "NOT_SUPPORTED",
                        "UNSUPPORTED"):
            # the Study flow's own UNSUPPORTED gate may refuse before the
            # accounting engine; the accounting flow itself must never
            # reinterpret a hardened refusal.
            ok_status = True
            if flow.get("flow") == "accounting":
                ok_status = f_status == h_status
        else:
            ok_status = f_status == h_status
        check(f"status[{q[:45]}]",
              ok_status,
              f"flow={f_status} hardened={h_status}")
        if f_status == "VERIFIED":
            body = step_five(flow)
            check(f"journal[{q[:45]}]",
                  len(body) >= 2,
                  str(body))


def test_b_canonical_equality():
    print("PART B - CANONICAL JOURNAL EQUALITY")
    cases = [
        "Received \u20b910,000 from Amit in full settlement of his account "
        "of \u20b910,500.",
        "Purchased goods from Rahul for Rs.20,000 plus CGST 9% and SGST 9%.",
        "Purchased goods from Rohan Rs.20,000 at 10% trade discount.",
        "Sold goods to Mohan for cash Rs.20,000 allowed 2% cash discount.",
        "Purchased goods from Rohan Rs.10,000; sold goods to Amit Rs.8,000 "
        "for cash.",
    ]
    for q in cases:
        hard = reason_bk_question(q)
        if hard.get("status") != "VERIFIED":
            continue
        flow = run_fyjc_student_flow(q)
        expected = canonical_lines(hard)
        actual = flow_outcome_lines(flow)
        check(f"canonical[{q[:45]}]",
              actual == expected,
              f"flow={actual} hardened={expected}")


def test_c_journal_verification():
    print("PART C - JOURNAL-ENTRY VERIFICATION (verify your answer)")
    desc = ("Received \u20b910,000 from Amit in full settlement of his "
            "account of \u20b910,500.")
    good = {
        "debits": [{"account": "Cash", "amount": 10000},
                   {"account": "Discount Allowed", "amount": 500}],
        "credits": [{"account": "Amit", "amount": 10500}],
    }
    r = verify_journal_entry(desc, good)
    check("correct settlement entry -> CORRECT", r.get("verdict") == "CORRECT",
          str(r.get("verdict")))

    wrong_direction = {
        "debits": [{"account": "Amit", "amount": 10500}],
        "credits": [{"account": "Cash", "amount": 10000},
                    {"account": "Discount Allowed", "amount": 500}],
    }
    r = verify_journal_entry(desc, wrong_direction)
    check("reversed direction -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    wrong_account = {
        "debits": [{"account": "Cash", "amount": 10000},
                   {"account": "Discount Received", "amount": 500}],
        "credits": [{"account": "Amit", "amount": 10500}],
    }
    r = verify_journal_entry(desc, wrong_account)
    check("wrong discount account -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    unbalanced = {
        "debits": [{"account": "Cash", "amount": 10000}],
        "credits": [{"account": "Amit", "amount": 10000}],
    }
    r = verify_journal_entry(desc, unbalanced)
    check("unbalanced -> INCORRECT", r.get("verdict") == "INCORRECT",
          str(r.get("verdict")))

    td = verify_journal_entry(
        "Purchased goods from Rohan Rs.20,000 at 10% trade discount.",
        {"debits": [{"account": "Purchases", "amount": 18000}],
         "credits": [{"account": "Rohan", "amount": 18000}]},
    )
    check("trade-discount net entry -> CORRECT",
          td.get("verdict") == "CORRECT", str(td.get("verdict")))


def test_d_safety_invariants():
    print("PART D - SAFETY INVARIANTS")
    cases = [
        "Purchased furniture for cash \u20b915,000.",
        "Received \u20b910,000 from Amit in full settlement of his account "
        "of \u20b910,500.",
        "Purchased goods from Rahul for Rs.20,000 plus CGST 9% and SGST 9%.",
        "Purchased goods from Rohan Rs.20,000 at 10% trade discount.",
        "Sold goods to Mohan for cash Rs.20,000 allowed 2% cash discount.",
        "Purchased goods from Rohan Rs.10,000; sold goods to Amit Rs.8,000 "
        "for cash.",
        "Sold goods to Ram on credit Rs.12,000. Paid him Rs.5,000.",
        "Purchased goods Rs.10,000.",
        "Paid to Krishna Rs.9,800 in full settlement of his account of "
        "Rs.10,000.",
    ]
    invented = []
    unbalanced = []
    overrides = []
    for q in cases:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        if flow.get("flow") != "accounting":
            continue
        # status must never be reinterpreted by the flow
        if flow.get("status") != hard.get("status"):
            overrides.append((q, flow.get("status"), hard.get("status")))
        if hard.get("status") != "VERIFIED":
            continue
        canon_accounts = {
            str(l.get("account"))
            for l in (hard.get("debit_lines") or []) + (hard.get("credit_lines") or [])
        }
        outcome = flow.get("outcome") or {}
        for line in (outcome.get("debit_lines") or []) + \
                (outcome.get("credit_lines") or []):
            if str(line.get("account")) not in canon_accounts:
                invented.append((q, line.get("account")))
        ver = flow.get("verification") or {}
        if flow.get("status") == "VERIFIED" and ver:
            td, tc = ver.get("total_debit"), ver.get("total_credit")
            if td is None or tc is None or abs(float(td) - float(tc)) > 1e-6:
                unbalanced.append((q, td, tc))
    check("no invented accounts", not invented, str(invented[:3]))
    check("no unbalanced VERIFIED journals", not unbalanced,
          str(unbalanced[:3]))
    check("legacy classifier cannot override hardened status", not overrides,
          str(overrides[:3]))
    # canonical journal cannot be edited through the flow: outcome is
    # exactly the hardened lines (no mutation path exists)
    hard = reason_bk_question(
        "Received \u20b910,000 from Amit in full settlement of his account "
        "of \u20b910,500.")
    flow = run_fyjc_student_flow(
        "Received \u20b910,000 from Amit in full settlement of his account "
        "of \u20b910,500.")
    check("canonical journal not editable",
          flow_outcome_lines(flow) == canonical_lines(hard),
          f"{flow_outcome_lines(flow)} vs {canonical_lines(hard)}")
    # ambiguous stays REVIEW_REQUIRED (never guessed)
    amb = run_fyjc_student_flow("Purchased goods Rs.10,000.")
    check("ambiguous stays REVIEW_REQUIRED",
          amb.get("status") == "REVIEW_REQUIRED", str(amb.get("status")))


def test_e_adversarial():
    print("PART E - ADVERSARIAL INPUTS")
    cases = [
        # malformed / empty
        "",
        "   ",
        "asdkjh 123 !!! ???",
        # missing party
        "Sold goods on credit for Rs.10,000.",
        # contradictory settlement (debtor paid instead of settling)
        "Sold goods to Ram on credit Rs.12,000. Paid him Rs.5,000.",
        # unsupported GST evidence
        "Purchased goods from Rahul Rs.10,000 GST 9%.",
        # ambiguous discount (rate with no net figure and no mode)
        "Sold goods Rs.10,000 at 5% discount.",
        # multiple transactions with a non-payment second step
        "Purchased goods from Rahul Rs.10,000. Paid rent Rs.4,000.",
        # wrong direction
        "Paid to Mehta Rs.10,000 in full settlement of his account of "
        "Rs.9,500.",
    ]
    for q in cases:
        flow = run_fyjc_student_flow(q)
        hard = reason_bk_question(q)
        f_status = flow.get("status")
        # every outcome must be an honest refusal or a resolved journal,
        # never a crash and never a guessed journal for a refusal
        check(f"adversarial[{q[:40]}]",
              f_status in ("VERIFIED", "BLOCKED", "REVIEW_REQUIRED",
                           "NOT_SUPPORTED", "UNSUPPORTED")
              and not (f_status == "VERIFIED" and hard.get("status") != "VERIFIED"
                       and flow.get("flow") == "accounting"),
              f"flow={f_status} hardened={hard.get('status')}")
    # reversed debit/credit through the verify path
    r = verify_journal_entry(
        "Paid rent \u20b93,000 by cash.",
        {"debits": [{"account": "Cash", "amount": 3000}],
         "credits": [{"account": "Rent", "amount": 3000}]},
    )
    check("reversed dr/cr detected", r.get("verdict") == "INCORRECT",
          str(r.get("verdict")))


def test_f_apptest():
    print("PART F - STREAMLIT APPTEST (real Study / Verify page)")
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

    def run_question(q, label):
        at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()
        at.text_area(key="fte_fyjc_question").set_value(q).run()
        at.button(key="fte_fyjc_go").click().run()
        check(f"{label} renders without exception", not at.exception,
              [e.stack_trace for e in at.exception])
        text = " ".join(m.value for m in at.markdown)
        return text

    text = run_question(
        "Received \u20b910,000 from Amit in full settlement of his account "
        "of \u20b910,500.",
        "settlement question")
    check("settlement shows VERIFIED through the app", "VERIFIED" in text, text[:200])
    check("settlement shows Discount Allowed", "Discount Allowed" in text,
          text[:200])

    text = run_question(
        "Purchased goods from Rahul for Rs.20,000 plus CGST 9% and SGST 9%.",
        "GST question")
    check("GST shows VERIFIED through the app", "VERIFIED" in text, text[:200])
    check("GST shows Input CGST", "Input CGST" in text, text[:200])

    text = run_question(
        "Purchased goods from Rohan Rs.20,000 at 10% trade discount.",
        "trade-discount question")
    check("trade discount shows VERIFIED through the app",
          "VERIFIED" in text, text[:200])
    check("trade discount shows net amount 18,000", "18,000" in text,
          text[:200])


def main():
    test_a_routing_matrix()
    test_b_canonical_equality()
    test_c_journal_verification()
    test_d_safety_invariants()
    test_e_adversarial()
    test_f_apptest()

    failed = len(FAILURES)
    print("=" * 72)
    if FAILURES:
        for f in FAILURES:
            print("FAILED:", f)
    print(f"15I-O gate: {TOTAL[0]} checks, {failed} failed")
    print("ALL PASS" if not FAILURES else "FAILURES PRESENT")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
