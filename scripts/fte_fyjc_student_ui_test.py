#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 14 - FYJC Student End-to-End UI Integration Acceptance Gate
scripts/fte_fyjc_student_ui_test.py

The release gate for the student-facing FYJC Study / Verify workflow.
It proves the complete student journey works end to end:

    Photo / PDF / typed question
      -> what FT-E understood (editable)
      -> Maths | Book-Keeping flow (steps 1-6 / 1-8)
      -> C++ mathematical authority confirmation
      -> independent verification (correct + incorrect student work)
      -> refusal states (BLOCKED / REVIEW_REQUIRED / UNSUPPORTED)

Areas verified (Sprint 14 section 11):
    A. Input            - typed, PDF-text, photo-only (honest no-OCR),
                          invalid input
    B. Understanding    - correct extraction, ambiguous extraction,
                          user correction, missing values
    C. Maths            - supported question, formula selection, C++
                          result, reverse calculation, unsupported
                          formula, missing dependency, zero denominator,
                          conflicting inputs
    D. Accounting       - golden rule, account classification, journal
                          entry, ledger, trial balance, debit/credit
                          consistency, missing amount, ambiguous
                          transaction, incorrect student input
    E. Safety           - Python cannot bypass C++, no fabricated
                          result, no silent substitution, statuses
                          preserved, evidence/lineage preserved,
                          repeated-run determinism
    F. Usability        - 5 real FYJC questions walked through the full
                          journey; friction points are RECORDED, not
                          hidden; UI honesty strings are present

The flow layer under test (backend.maths.fyjc_student_flow) is PURE:
no Streamlit, no AI, no network. The C++ engine remains the sole
mathematical authority; Python never performs a fallback calculation.

Target: 100% deterministic PASS.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths import (  # noqa: E402
    BLOCKED,
    REVIEW_REQUIRED,
    VERIFIED,
    FYJC_ACCEPTANCE_CASES,
    FYJC_ACCOUNTING_CASES,
    FYJC_MATHS_CASES,
    build_understanding,
    build_trial_balance,
    classify_transaction,
    parse_trial_balance_lines,
    post_ledger,
    run_fyjc_accounting_flow,
    run_fyjc_maths_flow,
    run_fyjc_student_flow,
    verify_journal_entry,
    verify_student_journal,
    verify_student_ledger,
    verify_student_trial_balance,
    verify_maths_answer,
    fyjc_traditional_class,
    fyjc_study_topics,
)
from backend.maths.authority import engine_available  # noqa: E402
from backend.maths.fyjc_maths import (  # noqa: E402
    AUTHORITY_CPP,
    UNSUPPORTED,
)

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

CHECKS = []
FAILURES = []
FRICTION = []  # recorded usability friction points (section 11.F)


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def stable(obj):
    """Deterministic, JSON-normalized fingerprint for equality checks."""
    return json.dumps(obj, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Part A - input paths
# ---------------------------------------------------------------------------


def test_a_input():
    print("PART A - INPUT PATHS")

    # A1: typed manual question (with 'Concept: value' lines, the format
    # the 12D normalizer parses) routes to a Maths flow. A narrative
    # without parseable facts BLOCKs honestly - never a guessed value.
    flow = run_fyjc_student_flow(
        "Calculate the Profit Margin.",
        facts={"Profit": 200, "Revenue": 1000},
        student_answer=20,
    )
    check("A.typed question -> maths flow", flow.get("flow") == "maths",
          flow.get("flow"))
    check("A.typed question resolved by C++ authority",
          bool(flow.get("resolved"))
          and flow.get("authority_state") == AUTHORITY_CPP,
          f"{flow.get('resolved')} {flow.get('authority_state')}")

    # A2: PDF-text-shaped input (extracted document text) is consumed
    # through the same journey ('Concept: value' lines).
    flow = run_fyjc_student_flow(
        "Calculate the Current Ratio.",
        text="Current Assets: Rs.5,00,000\nCurrent Liabilities: Rs.2,50,000",
        student_answer=2,
    )
    check("A.PDF-text input -> maths flow",
          flow.get("flow") == "maths" and flow.get("resolved"),
          f"{flow.get('flow')} {flow.get('status')}")

    # A3: photo-only input (no readable text anywhere) is refused honestly.
    # The UI additionally shows a no-OCR notice (checked in Part F); the
    # flow itself must not invent a question from nothing.
    flow = run_fyjc_student_flow("")
    check("A.photo-only (no text) is never guessed",
          flow.get("flow") == "refusal"
          and flow.get("status") in ("UNSUPPORTED", BLOCKED),
          f"{flow.get('flow')} {flow.get('status')}")
    check("A.photo-only refusal is student-readable",
          bool(flow.get("why_not")) and bool(flow.get("next_action")),
          f"{flow.get('why_not')} / {flow.get('next_action')}")

    # A4: invalid input (garbage) refuses without crashing.
    flow = run_fyjc_student_flow("asdkjh 123 !!! ???")
    check("A.invalid input refuses deterministically",
          flow.get("flow") == "refusal",
          flow.get("flow"))

    # A5: manual facts dict (typed 'given' values) are Tier-1 labelled.
    flow = run_fyjc_maths_flow(
        "ROE", facts={"Net Profit": 200, "Equity": 1000}, student_answer=20,
    )
    tiers = {row.get("provenance_tier") for row in
             (flow.get("outcome") or {}).get("inputs") or []}
    check("A.typed facts carry STUDENT_INPUT provenance",
          "STUDENT_INPUT" in tiers, str(tiers))


# ---------------------------------------------------------------------------
# Part B - understanding stage
# ---------------------------------------------------------------------------


def test_b_understanding():
    print("PART B - UNDERSTANDING STAGE")

    # B1: correct extraction of facts + interpretation ('Concept: value'
    # lines are the deterministic format the 12D normalizer parses).
    u = build_understanding(
        "Calculate the Current Ratio.\n"
        "Current Assets: Rs.5,00,000\n"
        "Current Liabilities: Rs.2,50,000"
    )
    concepts = {f.get("concept") for f in u.get("facts") or []}
    check("B.correct extraction of both inputs",
          "Current Assets" in concepts and "Current Liabilities" in concepts,
          str(concepts))
    check("B.extracted values are lakh-correct",
          any(f.get("value") == 500000 for f in u.get("facts") or []),
          str(u.get("facts")))
    check("B.maths interpretation names the metric",
          "Current Ratio" in str(u.get("interpretation")),
          str(u.get("interpretation")))

    # B2: Sprint 15I-O - the FYJC book-keeping Study / Verify flow now
    # routes through the hardened FT-E engine, which nets trade / cash
    # discounts deterministically (15E/15I-L). A rate/discount token in a
    # BOOK-KEEPING question is therefore no longer a "FT-E will not net
    # it" concern: the flow resolves it and shows the canonical net
    # journal. The concern surface is retained for the MATHS domain
    # (where no discount-netting formula is registered).
    u = build_understanding(
        "Purchased goods from Rahul for Rs.10,000 on credit with 10% "
        "trade discount."
    )
    check("B.bookkeeping discount no longer flags a will-not-net concern",
          not any("registered formula" in c
                  for c in (u.get("concerns") or [])),
          str(u.get("concerns")))
    flow = run_fyjc_student_flow(
        "Purchased goods from Rahul for Rs.10,000 on credit with 10% "
        "trade discount."
    )
    check("B.bookkeeping discount resolved by the hardened engine",
          flow.get("status") == "VERIFIED"
          and any("Purchases" in row and "9,000" in row
                  for step in (flow.get("steps") or [])
                  for row in (step.get("body") or [])),
          str([(s.get("number"), s.get("body"))
               for s in (flow.get("steps") or []) if s.get("number") == 5]))

    # B3: user correction - re-running with corrected wording changes the
    # outcome deterministically (student edits -> re-interpret).
    flow_before = run_fyjc_student_flow(
        "Purchased goods for Rs.10,000."
    )
    flow_after = run_fyjc_student_flow(
        "Purchased goods for cash Rs.10,000."
    )
    check("B.user correction resolves an ambiguous transaction",
          flow_before.get("flow") == "accounting"
          and flow_before.get("status") == REVIEW_REQUIRED
          and flow_after.get("status") == VERIFIED,
          f"{flow_before.get('status')} -> {flow_after.get('status')}")

    # B4: missing values are visible (no fabrication in understanding).
    u = build_understanding(
        "Calculate the Current Ratio.\nCurrent Assets: Rs.5,00,000"
    )
    check("B.missing value is simply absent - never invented",
          "Current Liabilities" not in {f.get("concept")
                                        for f in u.get("facts") or []},
          str(u.get("facts")))


# ---------------------------------------------------------------------------
# Part C - maths flow
# ---------------------------------------------------------------------------


def test_c_maths():
    print("PART C - MATHS FLOW")

    # C1: supported question - formula selection + C++ result.
    flow = run_fyjc_maths_flow(
        "Profit Margin", facts={"Profit": 200, "Revenue": 1000},
        student_answer=20,
    )
    check("C.supported metric resolved", bool(flow.get("resolved")),
          flow.get("status"))
    check("C.formula is selected from the registry",
          bool(flow.get("audit", {}).get("formula_id")),
          str(flow.get("audit", {}).get("formula_id")))
    check("C.result comes from the C++ authority",
          flow.get("authority_state") == AUTHORITY_CPP,
          flow.get("authority_state"))
    check("C.steps contain Given/Required/Formula/Substitution/C++/Answer",
          [s.get("number") for s in flow.get("steps") or []]
          == [1, 2, 3, 4, 5, 6],
          str([s.get("title") for s in flow.get("steps") or []]))
    check("C.substitution step shows the actual values",
          any("Profit = 200" in line
              for step in flow.get("steps") or []
              for line in step.get("body") or []),
          str(flow.get("steps"))[:300])
    check("C.final answer is the C++-verified display value",
          flow.get("outcome", {}).get("display_value") == "20.00%",
          str(flow.get("outcome", {}).get("display_value")))

    # C2: reverse calculation (margin+revenue -> profit).
    flow = run_fyjc_maths_flow(
        "Profit", facts={"Profit Margin": 20, "Revenue": 1000},
        student_answer=200,
    )
    check("C.reverse calculation resolved via C++",
          bool(flow.get("resolved"))
          and flow.get("outcome", {}).get("display_value") == "200.00",
          f"{flow.get('resolved')} {flow.get('outcome', {}).get('display_value')}")

    # C3: unsupported formula -> UNSUPPORTED, never computed.
    flow = run_fyjc_maths_flow("Simple Interest", facts={"Principal": 10000})
    check("C.unsupported formula -> UNSUPPORTED",
          flow.get("status") == UNSUPPORTED,
          flow.get("status"))
    check("C.unsupported has no fabricated value",
          flow.get("outcome", {}).get("value") is None,
          str(flow.get("outcome", {}).get("value")))

    # C4: missing dependency -> BLOCKED with the exact missing input.
    flow = run_fyjc_maths_flow("ROE", facts={"Net Profit": 200})
    check("C.missing dependency -> BLOCKED",
          flow.get("status") == BLOCKED, flow.get("status"))
    check("C.blocked names the missing input",
          "Equity" in str(flow.get("outcome", {}).get("missing") or ""),
          str(flow.get("outcome", {}).get("missing")))
    check("C.blocked tells the student what to provide",
          "Upload the relevant page or enter the verified value"
          in str(flow.get("next_action")),
          str(flow.get("next_action"))[:200])

    # C5: zero denominator -> BLOCKED (no division by zero, no guess).
    flow = run_fyjc_maths_flow(
        "Profit Margin", facts={"Profit": 100, "Revenue": 0}
    )
    check("C.zero denominator -> BLOCKED",
          flow.get("status") == BLOCKED, flow.get("status"))

    # C6: conflicting inputs -> REVIEW_REQUIRED (never silently chosen).
    flow = run_fyjc_maths_flow(
        "ROE",
        documents=[
            {"document_name": "page-1.png", "tier": "DOCUMENT",
             "facts": {"Net Profit": 200, "Equity": 1000}},
            {"document_name": "page-2.png", "tier": "DOCUMENT",
             "facts": {"Equity": 1200}},
        ],
    )
    check("C.conflicting inputs -> REVIEW_REQUIRED",
          flow.get("status") == REVIEW_REQUIRED, flow.get("status"))
    check("C.conflict is never silently resolved",
          flow.get("resolved") is False, str(flow.get("resolved")))

    # C7: incorrect student answer gets a MISMATCH explanation.
    flow = run_fyjc_maths_flow(
        "Profit Margin", facts={"Profit": 200, "Revenue": 1000},
        student_answer=30,
    )
    check("C.incorrect student answer -> INCORRECT verdict",
          flow.get("verdict") == "INCORRECT", flow.get("verdict"))
    check("C.mismatch explains the difference",
          bool((flow.get("audit") or {}).get("mismatch")),
          str((flow.get("audit") or {}).get("mismatch")))


# ---------------------------------------------------------------------------
# Part D - accounting flow
# ---------------------------------------------------------------------------


def test_d_accounting():
    print("PART D - ACCOUNTING FLOW")

    # D1: golden rule + classification + journal + ledger + TB for a
    # verified transaction (acceptance case S02).
    flow = run_fyjc_accounting_flow(
        "Purchased goods from Rahul on credit for Rs.10,000."
    )
    check("D.transaction resolved", flow.get("status") == VERIFIED,
          flow.get("status"))
    steps = {s.get("number"): s for s in flow.get("steps") or []}
    check("D.all 8 steps present", sorted(steps) == list(range(1, 9)),
          str(sorted(steps)))
    body1 = " ".join(steps[1].get("body") or [])
    body2 = " ".join(steps[2].get("body") or [])
    check("D.step1 identifies Purchases and Rahul",
          "Purchases" in body1 and "Rahul" in body1, body1)
    check("D.step2 classifies Purchases -> Nominal, Rahul -> Personal",
          "Nominal" in body2 and "Personal" in body2, body2)
    check("D.step3 states the golden rule",
          bool(" ".join(steps[3].get("body") or [])),
          str(steps[3].get("body")))
    check("D.step4 explains WHY each side",
          all("Debit" in s or "Credit" in s
              for s in steps[4].get("body") or []),
          str(steps[4].get("body"))[:200])
    check("D.step5 produces the journal entry",
          any("Purchases" in s for s in steps[5].get("body") or []),
          str(steps[5].get("body")))
    check("D.step6 ledger effect balances",
          bool(flow.get("ledger")) and flow.get("ledger", {}).get("balanced"),
          str(flow.get("ledger")))
    check("D.step7 trial balance tallies",
          bool(flow.get("trial_balance"))
          and flow.get("trial_balance", {}).get("balanced"),
          str(flow.get("trial_balance")))
    check("D.step8 verification verdict is CORRECT",
          (flow.get("verification") or {}).get("verdict") == "CORRECT",
          str(flow.get("verification")))

    # D2: traditional class mapping is display-only and consistent.
    check("D.traditional class for expense is Nominal",
          fyjc_traditional_class("expense") == "Nominal", "")
    check("D.traditional class for named party is Personal",
          fyjc_traditional_class(None, "Rahul") == "Personal", "")

    # D3: missing amount -> BLOCKED (treatment clear, amount required).
    flow = run_fyjc_accounting_flow("Purchased goods from Rahul on credit.")
    check("D.missing amount -> BLOCKED", flow.get("status") == BLOCKED,
          flow.get("status"))
    check("D.missing amount next action says enter the amount",
          "amount" in str(flow.get("next_action")).lower(),
          str(flow.get("next_action")))

    # D4: ambiguous transaction -> REVIEW_REQUIRED (never assumed cash).
    flow = run_fyjc_accounting_flow("Purchased goods for Rs.10,000.")
    check("D.ambiguous cash/credit -> REVIEW_REQUIRED",
          flow.get("status") == REVIEW_REQUIRED, flow.get("status"))
    check("D.ambiguity explains what to add",
          "cash" in str(flow.get("why_not", "")).lower()
          or "credit" in str(flow.get("why_not", "")).lower(),
          str(flow.get("why_not"))[:200])

    # D5: incorrect student journal entry -> INCORRECT with difference.
    jv = verify_student_journal(
        "Purchased goods from Rahul on credit for Rs.10,000.",
        ["Purchases"], ["10000"], ["Cash"], ["12000"],
    )
    check("D.incorrect journal -> INCORRECT", jv.get("verdict") == "INCORRECT",
          jv.get("verdict"))
    check("D.incorrect journal explains the imbalance",
          bool(jv.get("why_not")), str(jv.get("why_not"))[:200])

    # D6: correct student journal -> CORRECT with golden rule.
    jv = verify_student_journal(
        "Purchased goods from Rahul on credit for Rs.10,000.",
        ["Purchases"], ["10000"], ["Rahul"], ["10000"],
    )
    check("D.correct journal -> CORRECT", jv.get("verdict") == "CORRECT",
          jv.get("verdict"))

    # D7: ledger balance + trial balance verification helpers.
    entries = [{"debits": [{"account": "Cash", "amount": 50000}],
                "credits": [{"account": "Capital", "amount": 50000}]}]
    lv = verify_student_ledger("Cash", "50000", "Dr", entries)
    check("D.correct ledger balance -> CORRECT",
          lv.get("verdict") == "CORRECT", lv.get("verdict"))
    tb = verify_student_trial_balance(
        "Cash, 50000, 0\nCapital, 0, 50000", entries,
    )
    check("D.correct trial balance -> CORRECT",
          tb.get("verdict") == "CORRECT", tb.get("verdict"))
    tb_bad = verify_student_trial_balance(
        "Cash, 40000, 0\nCapital, 0, 50000", entries,
    )
    check("D.wrong trial balance -> INCORRECT with discrepancy",
          tb_bad.get("verdict") == "INCORRECT"
          and tb_bad.get("discrepancy") is not None,
          f"{tb_bad.get('verdict')} {tb_bad.get('discrepancy')}")
    tb_unreadable = verify_student_trial_balance("this is not a line", entries)
    check("D.unreadable trial balance refuses, never guesses",
          tb_unreadable.get("verdict") == "REFUSED",
          tb_unreadable.get("verdict"))
    rows = parse_trial_balance_lines("Cash, 50000, 0\nCapital, 0, 50000")
    check("D.trial-balance line parser is deterministic",
          len(rows) == 2 and rows[0]["account"] == "Cash",
          str(rows))

    # D8: debit/credit consistency across the whole journey.
    flow = run_fyjc_accounting_flow("Sold goods on credit to Mohan Rs.15,000.")
    debits = {line.get("account") for line in
              (flow.get("outcome") or {}).get("debit_lines") or []}
    credits = {line.get("account") for line in
               (flow.get("outcome") or {}).get("credit_lines") or []}
    check("D.credit sale debits buyer, credits Sales",
          debits == {"Mohan"} and credits == {"Sales"},
          f"{debits} / {credits}")


# ---------------------------------------------------------------------------
# Part E - safety / correctness
# ---------------------------------------------------------------------------


def test_e_safety():
    print("PART E - SAFETY / CORRECTNESS")

    # E1: every resolved maths outcome is C++-authoritative.
    for case in [c for c in FYJC_MATHS_CASES
                 if c.get("expect_verdict") == "CORRECT"][:6]:
        flow = run_fyjc_maths_flow(
            case["metric"], facts=case.get("facts"),
            text=case.get("text"), student_answer=case.get("student_answer"),
        )
        check(f"E.safe {case['id']} authority is cpp",
              flow.get("authority_state") == AUTHORITY_CPP
              and bool(flow.get("resolved")),
              f"{flow.get('authority_state')} {flow.get('resolved')}")

    # E2: no fabricated result on refusal (value stays None).
    flow = run_fyjc_maths_flow("Current Ratio",
                               text="Current Assets: Rs.5,00,000")
    check("E.blocked has no fabricated value",
          (flow.get("outcome") or {}).get("value") is None,
          str((flow.get("outcome") or {}).get("value")))

    # E3: no silent substitution - conflicting evidence stays split.
    flow = run_fyjc_student_flow(
        "Calculate ROE.",
        documents=[
            {"document_name": "a.png", "tier": "DOCUMENT",
             "facts": {"Net Profit": 200, "Equity": 1000}},
            {"document_name": "b.png", "tier": "DOCUMENT",
             "facts": {"Equity": 1200}},
        ],
    )
    check("E.conflict stays REVIEW_REQUIRED through the journey",
          flow.get("status") == REVIEW_REQUIRED, flow.get("status"))

    # E4: UNSUPPORTED / BLOCKED / REVIEW_REQUIRED are never overridden.
    check("E.unsupported remains UNSUPPORTED",
          run_fyjc_student_flow(
              "Calculate the Simple Interest on Rs.10,000 at 10% for 2 years."
          ).get("status") == UNSUPPORTED, "")
    check("E.blocked remains BLOCKED",
          run_fyjc_student_flow(
              "Calculate ROE. Net Profit is Rs.200."
          ).get("status") == BLOCKED, "")

    # E5: evidence / lineage survive into the flow output.
    flow = run_fyjc_maths_flow(
        "Current Ratio",
        text="Current Assets: Rs.5,00,000\nCurrent Liabilities: Rs.2,50,000",
    )
    input_tiers = {row.get("provenance_tier") for row in
                   (flow.get("outcome") or {}).get("inputs") or []}
    check("E.inputs carry provenance through the flow",
          "DOCUMENT" in input_tiers, str(input_tiers))
    check("E.inputs carry source evidence",
          all(row.get("source") for row in
              (flow.get("outcome") or {}).get("inputs") or []),
          str((flow.get("outcome") or {}).get("inputs"))[:200])

    # E6: repeated execution is deterministic (identical payloads).
    q = "Purchased goods from Rahul on credit for Rs.10,000."
    f1 = run_fyjc_student_flow(q)
    f2 = run_fyjc_student_flow(q)
    check("E.repeated run identical (accounting)",
          stable(f1) == stable(f2), "")
    m1 = run_fyjc_maths_flow("Profit", facts={"Revenue": 1000, "Expenses": 600},
                             student_answer=400)
    m2 = run_fyjc_maths_flow("Profit", facts={"Revenue": 1000, "Expenses": 600},
                             student_answer=400)
    check("E.repeated run identical (maths)", stable(m1) == stable(m2), "")


# ---------------------------------------------------------------------------
# Part F - student usability walkthrough (friction recorded, not hidden)
# ---------------------------------------------------------------------------


def test_f_usability():
    print("PART F - STUDENT USABILITY WALKTHROUGH")

    # Five real FYJC-style questions covering the main journeys.
    walkthrough = [
        {
            "id": "F01",
            "label": "maths: current ratio from text",
            "question": "Calculate the Current Ratio. Current Assets "
                        "Rs.5,00,000 and Current Liabilities Rs.2,50,000.",
            "text": "Current Assets: Rs.5,00,000\n"
                    "Current Liabilities: Rs.2,50,000",
            "student_answer": 2,
        },
        {
            "id": "F02",
            "label": "maths: profit margin",
            "question": "Calculate the Profit Margin. Profit Rs.200 and "
                        "Revenue Rs.1,000.",
            "facts": {"Profit": 200, "Revenue": 1000},
            "student_answer": 20,
        },
        {
            "id": "F03",
            "label": "accounting: credit purchase journal",
            "question": "Purchased goods from Rahul on credit for Rs.10,000.",
        },
        {
            "id": "F04",
            "label": "accounting: started business with cash",
            "question": "Started business with cash Rs.50,000.",
        },
        {
            "id": "F05",
            "label": "maths: blocked - missing input",
            "question": "Calculate ROE. Net Profit is Rs.200.",
        },
    ]

    for case in walkthrough:
        flow = run_fyjc_student_flow(
            case["question"], text=case.get("text"),
            facts=case.get("facts"), student_answer=case.get("student_answer"),
        )
        # the journey always answers: what / why / next
        check(f"F.{case['id']} journey completes without error",
              flow.get("flow") in ("maths", "accounting", "refusal"),
              flow.get("flow"))
        check(f"F.{case['id']} has a readable next step",
              bool(flow.get("next_action"))
              or bool((flow.get("steps") or [])),
              str(flow.get("next_action"))[:160])
        check(f"F.{case['id']} explanation is student-facing",
              "Could not be calculated" not in str(flow.get("what", ""))
              and "registry" not in str(flow.get("what", "")),
              str(flow.get("what"))[:160])
        # record friction: steps with empty bodies or refusals without a
        # concrete next action are logged, not asserted away silently.
        empty_steps = [s.get("title") for s in (flow.get("steps") or [])
                       if not (s.get("body") or [])]
        if empty_steps:
            FRICTION.append(
                f"{case['id']}: empty explanation steps: {empty_steps}")
        if not flow.get("next_action"):
            FRICTION.append(f"{case['id']}: no concrete next action")

    # A blocked case must tell the student exactly what to provide next.
    blocked = run_fyjc_student_flow(
        "Calculate the Current Ratio.",
        text="Current Assets: Rs.5,00,000",
    )
    check("F.blocked names the missing input",
          "Current Liabilities" in str(blocked.get("why_not", "")),
          str(blocked.get("why_not"))[:200])
    check("F.blocked offers upload/enter-value guidance",
          "Upload" in str(blocked.get("next_action", ""))
          or "enter" in str(blocked.get("next_action", "")).lower(),
          str(blocked.get("next_action"))[:200])

    # The student can independently verify: correct and incorrect answers.
    check("F.verification MATCH path",
          run_fyjc_maths_flow(
              "Profit Margin", facts={"Profit": 200, "Revenue": 1000},
              student_answer=20,
          ).get("verdict") == "CORRECT", "")
    check("F.verification MISMATCH path explains difference",
          bool((run_fyjc_maths_flow(
              "Profit Margin", facts={"Profit": 200, "Revenue": 1000},
              student_answer=25,
          ).get("audit") or {}).get("mismatch")), "")

    # --- UI honesty strings (the UI + flow modules must communicate, not
    # fake: the no-OCR notice lives in the UI; the C++ confirmation lives
    # in the flow's step-5 body) ---
    ui_path = os.path.join(os.path.dirname(__file__), "..",
                           "backend", "fyjc_student_ui.py")
    flow_path = os.path.join(os.path.dirname(__file__), "..",
                             "backend", "maths", "fyjc_student_flow.py")
    combined = ""
    for path in (ui_path, flow_path):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                combined += fh.read()
    check("F.UI states the no-OCR honesty rule",
          "does not bundle" in combined
          and "OCR engine" in combined
          and "never guess" in combined, "")
    check("F.UI shows the C++ confirmation",
          "Deterministic calculation verified" in combined, "")
    check("F.UI offers verify-yourself paths",
          "Verify" in combined, "")
    check("F.UI offers Correct / Edit",
          "Correct / Edit" in combined, "")

    # --- App wiring (the workspace must expose the FYJC Study page) ---
    app_path = os.path.join(os.path.dirname(__file__), "..",
                            "app (1) (9).py")
    if os.path.exists(app_path):
        with open(app_path, encoding="utf-8") as fh:
            app_src = fh.read()
        check("F.app exposes FYJC Study in the workspace nav",
              app_src.count('"FYJC Study"') >= 2
              and "render_fyjc_student_ui(demo=False)" in app_src
              and "render_fyjc_student_ui(demo=True)" in app_src, "")

    # --- Recorded friction summary (section 11.F: record, don't fix) ---
    for item in FRICTION:
        print(f"  [usability note] {item}")


# ---------------------------------------------------------------------------
# Part G - py_compile + diff hygiene (section 12)
# ---------------------------------------------------------------------------


def test_g_compile():
    print("PART G - COMPILE & HYGIENE")

    root = os.path.join(os.path.dirname(__file__), "..")
    for rel in [
        "backend/maths/fyjc_student_flow.py",
        "backend/fyjc_student_ui.py",
        "backend/maths/__init__.py",
        "app (1) (9).py",
        "scripts/fte_fyjc_student_ui_test.py",
    ]:
        path = os.path.join(root, rel)
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", path],
            capture_output=True, text=True, cwd=root, timeout=120,
        )
        check(f"G.py_compile {rel}",
              proc.returncode == 0,
              (proc.stderr or proc.stdout or "")[-300:])

    # regression against the Sprint 13 FYJC gate (and it alone - the full
    # 12A-12F sweep is run by the release ritual).
    try:
        gate = os.path.join(os.path.dirname(__file__),
                            "fte_fyjc_readiness_test.py")
        proc = subprocess.run([sys.executable, gate], capture_output=True,
                              text=True, cwd=root, timeout=600)
        tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-200:]
        check("G.Sprint 13 FYJC gate stays green",
              proc.returncode == 0, tail.replace("\n", " | ")[-300:])
    except subprocess.TimeoutExpired:
        check("G.Sprint 13 FYJC gate stays green", False, "timed out")


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def verdict():
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 72)
    print(f"RESULT: {passed}/{total} checks passed")
    if FAILURES:
        print("FAILED CHECKS:")
        for f in FAILURES[:50]:
            print(f"  - {f}")
        print("=" * 72)
        print("SPRINT 14 FAIL - FYJC UI INTEGRATION NOT READY")
        return 1
    print("=" * 72)
    print("SPRINT 14 GATE: ALL CHECKS COMPLETE")
    if engine_available():
        print("14 PASS - FYJC STUDENT END-TO-END UI VERIFIED "
              "(C++ mathematical authority active)")
    else:
        print("14 CONDITIONAL PASS - FYJC JOURNEY VERIFIED "
              "(C++ authority not deployed - strict path BLOCKs)")
    return 0


def main():
    test_a_input()
    test_b_understanding()
    test_c_maths()
    test_d_accounting()
    test_e_safety()
    test_f_usability()
    test_g_compile()
    return verdict()


if __name__ == "__main__":
    sys.exit(main())
