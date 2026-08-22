#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-R - Student-Facing Explanation & UI Simplification Gate
scripts/fte_fyjc_15r_ui_test.py

Proves the 15I-R student-facing changes on the REAL production path
(Study / Verify -> run_fyjc_student_flow -> run_fyjc_accounting_flow ->
hardened_bookkeeping_outcome -> reason_bk_question -> canonical journal):

  A. Informational vs blocking concerns - the book-keeping 'numbers are
     facts' note is INFORMATIONAL (info_notes, never the blocking
     'Almost there' panel), while genuine blocking concerns (unregistered
     maths rate, uncertain request) still raise the panel.
  B. Answer-first result - the exact TD case is VERIFIED with the
     canonical ₹22,500 journal; the engine's WHY text names the party as
     a Personal account with 'Credit the giver' (never 'credit incomes
     and gains'); the step-2 classification shows 'Ravi Kumar -> Personal
     Account' (never 'None'); the trade-discount breakdown is produced
     from the engine's own calculation records.
  C. Reasoning/authority untouched - the canonical journal (accounts,
     sides, amounts, verdict) is byte-identical to the hardened engine
     and to the pre-15I-R canonical entries; safety invariants hold
     (unsafe confident = 0, invented accounts = 0, unbalanced VERIFIED =
     0, legacy override = 0, ambiguous never guessed).
  D. Behavioural regressions - GST, cash-discount settlement, ordinary
     purchase/sale, lowercase and multi-word parties, and refusal
     behaviour all still route through the hardened engine unchanged.
  E. Verify-your-answer - exact journal -> CORRECT; wrong amount ->
     INCORRECT (15I-P exact comparison preserved).
  F. Real Streamlit AppTest - the exact TD case renders without the
     blocking 'Almost there' panel, shows VERIFIED + ₹22,500 + the
     journal table, the Platrixa verified badge, the 'Why?' explanation with
     'Credit the giver', and the 'Show detailed reasoning' / 'Check my
     answer' expanders; the verify widgets still work (exact CORRECT,
     wrong amount INCORRECT).

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

TD_Q = ("Purchased goods with a list price of \u20b925,000 at 10% trade "
        "discount from ravi kumar on credit.")
TD_LINES = [("Purchases", "22500.00"), ("Ravi Kumar", "22500.00")]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    if not ok:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL [{name}] {detail}")
    else:
        print(f"OK  [{name}]")


def flow_lines(flow):
    outcome = flow.get("outcome") or {}
    lines = []
    for line in (outcome.get("debit_lines") or []) + \
            (outcome.get("credit_lines") or []):
        account = line.get("account")
        if account:
            lines.append((str(account), str(line.get("amount"))))
    return sorted(lines)


def hard_lines(res):
    lines = []
    for line in (res.get("debit_lines") or []) + (res.get("credit_lines") or []):
        account = line.get("account")
        if account:
            lines.append((str(account), str(line.get("amount"))))
    return sorted(lines)


def test_a_concern_split():
    print("PART A - INFORMATIONAL VS BLOCKING CONCERNS")
    u = build_understanding(TD_Q)
    check("A.1 exact TD: no blocking concerns",
          (u.get("concerns") or []) == [], str(u.get("concerns")))
    check("A.2 exact TD: informational note present",
          len(u.get("info_notes") or []) == 1
          and "book-keeping question" in u.get("info_notes")[0],
          str(u.get("info_notes")))

    # a genuine blocking concern (unregistered maths rate) still raises it
    maths = ("Calculate the gross profit. List price Rs.20,000 at 10% "
             "trade discount and cost Rs.15,000.")
    u2 = build_understanding(maths)
    check("A.3 maths rate stays a blocking concern",
          any("rate/discount" in c for c in (u2.get("concerns") or [])),
          str(u2.get("concerns")))

    # an unclear / multi-figure request stays blocking
    u3 = build_understanding("What is the figure? Profit Rs.10,000 or Rs.20,000?")
    check("A.4 unclear request stays blocking",
          bool(u3.get("concerns")), str(u3.get("concerns")))


def test_b_answer_first_and_classification():
    print("PART B - ANSWER-FIRST DATA + PARTY CLASSIFICATION")
    hard = reason_bk_question(TD_Q)
    flow = run_fyjc_student_flow(TD_Q)

    check("B.1 hardened engine VERIFIED", hard.get("status") == "VERIFIED",
          str(hard.get("status")))
    check("B.2 flow VERIFIED and agrees with engine",
          flow.get("status") == "VERIFIED"
          and flow.get("status") == hard.get("status"),
          f"flow={flow.get('status')} hardened={hard.get('status')}")
    check("B.3 canonical journal Purchases 22,500 / Ravi Kumar 22,500",
          flow_lines(flow) == sorted(TD_LINES), str(flow_lines(flow)))

    # party classification metadata (engine WHY text)
    party_why = None
    for line in (hard.get("credit_lines") or []):
        if line.get("account") == "Ravi Kumar":
            party_why = line.get("why") or ""
            check("B.4 party class is Personal",
                  line.get("class") == "Personal", str(line.get("class")))
            check("B.5 party rule is 'Credit the giver'",
                  "credit the giver" in (line.get("rule") or "").lower(),
                  str(line.get("rule")))
    check("B.6 party WHY never says 'credit incomes and gains'",
          party_why is not None and "income/gain" not in party_why
          and "Credit incomes and gains" not in party_why,
          str(party_why))

    # step 2 classification display
    step2 = next((s.get("body") for s in (flow.get("steps") or [])
                  if s.get("number") == 2), [])
    check("B.7 step 2 shows 'Ravi Kumar -> Personal Account'",
          any("Ravi Kumar \u2192 Personal Account" in b for b in step2),
          str(step2))
    check("B.8 step 2 never shows 'None (FYJC class'",
          not any("None" in b for b in step2), str(step2))

    # trade-discount breakdown data flows from the engine
    records = (flow.get("outcome") or {}).get("calculation_records") or []
    ids = {r.get("calculation_id") for r in records}
    check("B.9 TD calculation records reach the flow",
          {"BK_LIST_PRICE", "BK_TRADE_DISCOUNT_AMOUNT",
           "BK_NET_TRANSACTION_VALUE"} <= ids,
          str(sorted(ids)))
    td_rec = next((r for r in records
                   if r.get("calculation_id") == "BK_TRADE_DISCOUNT_AMOUNT"),
                  {})
    check("B.10 TD rate and amount in records",
          Decimal(str((td_rec.get("inputs") or {}).get("trade_discount_rate")))
          == Decimal("10")
          and Decimal(str(td_rec.get("result"))) == Decimal("2500.00"),
          str(td_rec))

    # verification summary present
    check("B.11 flow verification balanced 22,500 = 22,500",
          (flow.get("verification") or {}).get("balanced") is True
          and float((flow.get("verification") or {}).get("total_debit"))
          == 22500.0
          and float((flow.get("verification") or {}).get("total_credit"))
          == 22500.0,
          str(flow.get("verification")))


def test_c_authority_untouched():
    print("PART C - CANONICAL AUTHORITY UNCHANGED")
    # canonical journals must be identical to the hardened engine
    cases = [
        "Purchased goods from Rahul on credit for Rs.10,000.",
        "Sold goods to Mohan for cash Rs.20,000 allowed 2% cash discount.",
        "Received \u20b910,000 from amit in full settlement of his account "
        "of \u20b910,500.",
        "Paid \u20b99,500 to mehta in full settlement of his account of "
        "\u20b910,000.",
        "Purchased goods from Rahul for Rs.20,000 plus CGST 9% and SGST 9%.",
        "Purchased goods with a list price of \u20b925,000 at 10% trade "
        "discount from ravi kumar on credit.",
    ]
    for q in cases:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        check(f"C.1 flow == hardened [{q[:40]}]",
              flow.get("status") == hard.get("status")
              and flow_lines(flow) == hard_lines(hard),
              f"flow={flow.get('status')} {flow_lines(flow)} vs "
              f"hardened={hard.get('status')} {hard_lines(hard)}")

    # safety invariants
    invented = []
    unbalanced = []
    overrides = []
    guessed = []
    for q in cases:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        if flow.get("flow") != "accounting":
            continue
        if flow.get("status") != hard.get("status"):
            overrides.append((q, flow.get("status"), hard.get("status")))
        if flow.get("status") == "VERIFIED" and hard.get("status") != "VERIFIED":
            guessed.append((q, hard.get("status")))
        if hard.get("status") != "VERIFIED":
            continue
        canon = {str(l.get("account"))
                 for l in (hard.get("debit_lines") or []) +
                 (hard.get("credit_lines") or [])}
        outcome = flow.get("outcome") or {}
        for line in (outcome.get("debit_lines") or []) + \
                (outcome.get("credit_lines") or []):
            if str(line.get("account")) not in canon:
                invented.append((q, line.get("account")))
        ver = flow.get("verification") or {}
        if ver:
            td, tc = ver.get("total_debit"), ver.get("total_credit")
            if td is None or tc is None or abs(float(td) - float(tc)) > 1e-6:
                unbalanced.append((q, td, tc))
    check("C.2 unsafe confident = 0", not guessed, str(guessed[:3]))
    check("C.3 invented accounts = 0", not invented, str(invented[:3]))
    check("C.4 unbalanced VERIFIED = 0", not unbalanced, str(unbalanced[:3]))
    check("C.5 legacy authority override = 0", not overrides,
          str(overrides[:3]))

    # ambiguous inputs never guessed
    for q in ["Purchased goods for \u20b920,000 and paid \u20b910,000.",
              "Purchased goods for cash on credit from Rahul Rs.20,000."]:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        check(f"C.6 ambiguous never guessed [{q[:36]}]",
              hard.get("status") == "REVIEW_REQUIRED"
              and flow.get("status") == hard.get("status")
              and flow.get("status") != "VERIFIED",
              f"flow={flow.get('status')} hardened={hard.get('status')}")


def _norm_amt(value) -> Decimal:
    return Decimal(str(value))


def test_d_behavioural_regressions():
    print("PART D - BEHAVIOURAL REGRESSIONS (hardened engine)")
    expected = [
        ("D.1 GST purchase",
         "Purchased goods from Rahul for Rs.20,000 plus CGST 9% and SGST 9%.",
         [("Input CGST", "1800"), ("Input SGST", "1800"),
          ("Purchases", "20000"), ("Rahul", "23600")]),
        ("D.2 CD settlement receipt",
         "Received \u20b910,000 from amit in full settlement of his account "
         "of \u20b910,500.",
         [("Amit", "10500"), ("Cash", "10000"), ("Discount Allowed", "500")]),
        ("D.3 CD settlement payment",
         "Paid \u20b99,500 to mehta in full settlement of his account of "
         "\u20b910,000.",
         [("Cash", "9500"), ("Discount Received", "500"), ("Mehta", "10000")]),
        ("D.4 ordinary credit purchase",
         "Purchased goods from Rahul on credit for Rs.10,000.",
         [("Purchases", "10000"), ("Rahul", "10000")]),
        ("D.5 ordinary cash sale",
         "Sold goods for cash Rs.8,000.",
         [("Cash", "8000"), ("Sales", "8000")]),
        ("D.6 lowercase multi-word party",
         "Purchased goods from ravi kumar on credit for \u20b912,000.",
         [("Purchases", "12000"), ("Ravi Kumar", "12000")]),
    ]
    for label, q, lines in expected:
        hard = reason_bk_question(q)
        flow = run_fyjc_student_flow(q)
        expected_lines = sorted((a, _norm_amt(v)) for a, v in lines)
        check(f"{label}: VERIFIED", hard.get("status") == "VERIFIED",
              str(hard.get("status")))
        check(f"{label}: flow == hardened",
              flow.get("status") == hard.get("status")
              and sorted((a, _norm_amt(v)) for a, v in flow_lines(flow))
              == expected_lines,
              f"{flow_lines(flow)}")


def test_e_verify_your_answer():
    print("PART E - VERIFY-YOUR-ANSWER (15I-P exact comparison)")
    exact = {
        "debits": [{"account": "Purchases", "amount": 22500}],
        "credits": [{"account": "Ravi Kumar", "amount": 22500}],
    }
    r = verify_journal_entry(TD_Q, exact)
    check("E.1 exact canonical -> CORRECT", r.get("verdict") == "CORRECT",
          str(r.get("verdict")))

    wrong_amount = {
        "debits": [{"account": "Purchases", "amount": 20000}],
        "credits": [{"account": "Ravi Kumar", "amount": 20000}],
    }
    r = verify_journal_entry(TD_Q, wrong_amount)
    check("E.2 wrong amount -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    wrong_account = {
        "debits": [{"account": "Purchases", "amount": 22500}],
        "credits": [{"account": "Amit", "amount": 22500}],
    }
    r = verify_journal_entry(TD_Q, wrong_account)
    check("E.3 wrong account -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))

    reversed_sides = {
        "debits": [{"account": "Ravi Kumar", "amount": 22500}],
        "credits": [{"account": "Purchases", "amount": 22500}],
    }
    r = verify_journal_entry(TD_Q, reversed_sides)
    check("E.4 reversed direction -> INCORRECT",
          r.get("verdict") == "INCORRECT", str(r.get("verdict")))


def test_f_apptest():
    print("PART F - REAL STREAMLIT STUDY/VERIFY APPTEST")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("apptest available", False, str(exc))
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
    at.text_area(key="fte_fyjc_question").set_value(TD_Q).run()
    at.button(key="fte_fyjc_go").click().run()
    check("F.3 TD question renders without exception", not at.exception,
          [e.stack_trace for e in at.exception])
    md = " ".join(m.value for m in at.markdown)
    check("F.4 no blocking 'Almost there' panel", "Almost there" not in md,
          md[:200])
    check("F.5 shows VERIFIED", "VERIFIED" in md, md[:200])
    check("F.6 shows ₹22,500 journal", "22,500" in md, md[:200])
    check("F.7 journal table headers", "Debit" in md and "Credit" in md,
          md[:200])
    check("F.8 'Why?' explanation present", "Why?" in md, md[:200])
    check("F.9 party explained as 'Credit the giver' / Personal A/c",
          "Credit the giver" in md and "Personal A/c" in md, md[:200])
    check("F.10 no 'credit incomes and gains' for the party",
          "Credit incomes and gains" not in md, md[:200])
    check("F.11 trade discount breakdown shown",
          "List price" in md and "Net amount" in md, md[:200])
    check("F.12 Platrixa verified this entry", "Platrixa verified this entry" in md,
          md[:200])
    labels = [e.label for e in at.expander]
    check("F.13 detailed reasoning expander present",
          any("Show detailed reasoning" in l for l in labels), str(labels))
    check("F.14 'Check my answer' expander present",
          any("Check my answer" in l for l in labels), str(labels))
    check("F.15 verify widgets present",
          bool(at.text_input(key="fte_fyjc_jd1a"))
          and bool(at.button(key="fte_fyjc_jv_btn"))
          and bool(at.button(key="fte_fyjc_lv_btn"))
          and bool(at.button(key="fte_fyjc_tbv_btn")), "")

    # verify-your-answer through the real page
    at.text_input(key="fte_fyjc_jd1a").set_value("Purchases")
    at.text_input(key="fte_fyjc_jd1v").set_value("22500")
    at.text_input(key="fte_fyjc_jc1a").set_value("Ravi Kumar")
    at.text_input(key="fte_fyjc_jc1v").set_value("22500")
    at.button(key="fte_fyjc_jv_btn").click().run()
    check("F.16 exact journal verified", not at.exception,
          [e.stack_trace for e in at.exception])
    success_text = " ".join(s.value for s in at.success)
    check("F.17 exact journal -> CORRECT",
          "correct" in success_text.lower(), success_text[:200])

    at.text_input(key="fte_fyjc_jd1a").set_value("Purchases")
    at.text_input(key="fte_fyjc_jd1v").set_value("20000")
    at.text_input(key="fte_fyjc_jc1a").set_value("Ravi Kumar")
    at.text_input(key="fte_fyjc_jc1v").set_value("20000")
    at.button(key="fte_fyjc_jv_btn").click().run()
    check("F.18 wrong-amount journal verified", not at.exception,
          [e.stack_trace for e in at.exception])
    error_text = " ".join(e.value for e in at.error)
    check("F.19 wrong amount -> INCORRECT",
          "INCORRECT" in error_text or "expected" in error_text.lower(),
          error_text[:200])

    # a genuine refusal still shows the blocking panel
    at.text_area(key="fte_fyjc_question").set_value(
        "Purchased goods for \u20b920,000 and paid \u20b910,000."
    ).run()
    at.button(key="fte_fyjc_go").click().run()
    md2 = " ".join(m.value for m in at.markdown)
    check("F.20 genuine refusal still shows blocking 'Almost there'",
          "Almost there" in md2 or "REVIEW REQUIRED" in md2.upper()
          or "clarity" in md2, md2[:200])


def main():
    test_a_concern_split()
    test_b_answer_first_and_classification()
    test_c_authority_untouched()
    test_d_behavioural_regressions()
    test_e_verify_your_answer()
    test_f_apptest()

    failed = len(FAILURES)
    print("=" * 72)
    if FAILURES:
        for f in FAILURES:
            print("FAILED:", f)
    print(f"15I-R gate: {TOTAL[0]} checks, {failed} failed")
    print("ALL PASS" if not FAILURES else "FAILURES PRESENT")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
