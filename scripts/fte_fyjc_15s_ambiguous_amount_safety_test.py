#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-S - Ambiguous Multi-Amount Safety Gate
scripts/fte_fyjc_15s_ambiguous_amount_safety_test.py

Locks in the fix for the reported unsafe behaviour:

    "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000."

previously produced a confident journal (Purchases Dr ₹20,000 /
Rahul Cr ₹20,000) even though a second amount (₹18,000) was present
with no deterministically established role. The fix is at the
accounting authority boundary: resolve_transaction_amounts() now
requires EVERY stated figure to be consumed by a deterministic role
(list price, an explicit trade/cash discount amount, a stated payment,
a full-settlement pair, or a started-business asset component). A
figure with no role makes the transaction REVIEW_REQUIRED - Platrixa never
picks one amount over another by position ('first amount wins' is
forbidden), and the multi-transaction merge path surfaces the merged
refusal instead of a stale per-segment status.

This gate proves, through the REAL production path:

  A. The exact failing input refuses REVIEW_REQUIRED with ZERO journal
     lines at every layer (reason_bk_question -> hardened adapter ->
     run_fyjc_accounting_flow -> run_fyjc_student_flow), with an
     explanation naming both figures, and NEVER shows VERIFIED.
  B. The reversed-order variant refuses identically.
  C. An equivalent transaction whose wording gives each amount a clear
     role still verifies.
  D. Trade discount (list price + rate) still verifies to the canonical
     net journal.
  E. GST (taxable + CGST + SGST) still verifies unchanged.
  F. Cash-discount settlement still verifies unchanged.
  G. Partial settlement still verifies unchanged.
  H. Multi-transaction questions still verify unchanged.
  I. A battery of two-or-more-amount variants whose roles cannot be
     established all refuse REVIEW_REQUIRED with zero journal lines
     (including 'paid Rs.X and Rs.Y' lists, which bind both figures to
     the payment verb).
  J. Adversarial amount ordering - the first stated amount is never
     picked just because it comes first.
  K. Safety invariants: no VERIFIED carries an unresolved amount, every
     refusal has zero lines, every VERIFIED journal balances, all
     outcomes are deterministic across repeated runs, and the Study /
     Verify flow verdict equals the hardened engine verdict for every
     case (the adapter never reinterprets amounts).
  L. The REAL Streamlit Study / Verify page renders the exact failing
     input as REVIEW_REQUIRED with no fabricated journal, while a
     legitimate TD question still renders VERIFIED with the canonical
     journal.

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
# The exact failing input (Sprint 15I-S regression target)
# ---------------------------------------------------------------------------
EXACT = "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000."
REVERSED = "Purchased goods for ₹18,000 from Rahul on credit and ₹20,000."


# ---------------------------------------------------------------------------
# A. Exact failure: REVIEW_REQUIRED, zero lines, at EVERY layer
# ---------------------------------------------------------------------------

def test_a_exact_failure_all_layers():
    print("PART A - EXACT FAILURE AT EVERY LAYER")

    hard = reason_bk_question(EXACT)
    check("A.1 reason_bk_question REVIEW_REQUIRED",
          hard.get("status") == "REVIEW_REQUIRED", hard.get("status"))
    check("A.2 no VERIFIED", hard.get("status") != "VERIFIED", "")
    check("A.3 zero journal lines", lines(hard) == [], str(lines(hard)))
    why = " ".join(str(hard.get("why_not") or "").lower().split())
    check("A.4 explanation names both figures",
          "20,000" in why and "18,000" in why, why[:200])

    adapted = hardened_bookkeeping_outcome(EXACT)
    check("A.5 hardened adapter REVIEW_REQUIRED",
          adapted.get("status") == "REVIEW_REQUIRED", adapted.get("status"))
    check("A.6 adapter zero lines", lines(adapted) == [], str(lines(adapted)))

    flow = run_fyjc_accounting_flow(EXACT)
    check("A.7 run_fyjc_accounting_flow REVIEW_REQUIRED",
          flow.get("status") == "REVIEW_REQUIRED", flow.get("status"))
    check("A.8 flow zero lines", lines(flow) == [], str(lines(flow)))

    student = run_fyjc_student_flow(EXACT)
    check("A.9 run_fyjc_student_flow REVIEW_REQUIRED",
          student.get("status") == "REVIEW_REQUIRED", student.get("status"))
    check("A.10 student flow zero lines", lines(student) == [],
          str(lines(student)))


# ---------------------------------------------------------------------------
# B. Reversed order
# ---------------------------------------------------------------------------

def test_b_reversed():
    print("PART B - REVERSED ORDER")
    for layer_name, fn in [
        ("reason_bk_question", reason_bk_question),
        ("run_fyjc_accounting_flow", run_fyjc_accounting_flow),
        ("run_fyjc_student_flow", run_fyjc_student_flow),
    ]:
        res = fn(REVERSED)
        check(f"B {layer_name} REVIEW_REQUIRED",
              res.get("status") == "REVIEW_REQUIRED", res.get("status"))
        check(f"B {layer_name} zero lines", lines(res) == [],
              str(lines(res)))


# ---------------------------------------------------------------------------
# C/D/E/F/G/H. Resolvable structured amounts keep verifying
# ---------------------------------------------------------------------------

def test_structured_verified():
    print("PART C-H - RESOLVABLE STRUCTURED AMOUNTS KEEP VERIFYING")

    verified_cases = [
        # C. explicitly structured amount (list price + TD amount)
        ("C structured TD amount",
         "Purchased goods for ₹20,000 from Rahul on credit with "
         "₹2,000 trade discount.",
         "VERIFIED"),
        # D. trade discount list price + rate -> net ₹22,500
        ("D trade discount",
         "Purchased goods with a list price of ₹25,000 at 10% trade "
         "discount from Rahul on credit.",
         "VERIFIED"),
        # E. GST taxable + CGST + SGST
        ("E GST",
         "Purchased goods for ₹10,000 plus CGST 6% and SGST 6% from "
         "Ravi on credit.",
         "VERIFIED"),
        # F. cash-discount settlement
        ("F cash discount settlement",
         "Received ₹9,500 from Rahul in full settlement of his account "
         "of ₹10,000.",
         "VERIFIED"),
        # G. partial settlement (established corpus case)
        ("G partial settlement",
         "Sold goods to Ram ₹12,000. Received ₹5,000 from him.",
         "VERIFIED"),
        # H. multi-transaction
        ("H multi-transaction",
         "Purchased goods from Ravi for ₹10,000 on credit. Sold goods "
         "to Kavita for ₹8,000 on credit.",
         "VERIFIED"),
    ]
    for label, q, expected in verified_cases:
        hard = reason_bk_question(q)
        check(f"{label} hardened status",
              hard.get("status") == expected, hard.get("status"))
        check(f"{label} no unresolved-amount concern",
              all("cannot assign every" not in str(c)
                  for c in (hard.get("concerns") or [])),
              str(hard.get("concerns")))
        flow = run_fyjc_student_flow(q)
        check(f"{label} flow routes to hardened engine",
              flow.get("status") == hard.get("status"),
              f"flow={flow.get('status')} hardened={hard.get('status')}")
        if hard.get("status") == "VERIFIED":
            check(f"{label} canonical journal equality",
                  lines(flow) == lines(hard),
                  f"flow={lines(flow)} hard={lines(hard)}")
            check(f"{label} balanced VERIFIED journal", balanced(flow))

    # D must net to the canonical ₹22,500 purchase
    hard = reason_bk_question(verified_cases[1][1])
    purchases = [l for l in (hard.get("debit_lines") or [])
                 if l.get("account") == "Purchases"]
    check("D net amount 22,500",
          any(str(l.get("amount")) in ("22500", "22500.00", "22500.0")
              for l in purchases), str(purchases))


# ---------------------------------------------------------------------------
# I. Genuine ambiguity variants - all refuse with zero lines
# ---------------------------------------------------------------------------

AMBIGUOUS_CASES = [
    "Purchased goods for ₹10,000 and ₹8,000 from Rahul on credit.",
    "Sold goods to Rahul for ₹10,000 and received ₹8,000.",
    "Paid ₹9,000 to Mohan and ₹8,000 to Rahul.",
    "Received ₹9,000 from Mohan and ₹8,000 from Rahul.",
    "Bought goods ₹20,000 and ₹18,000 from Rahul on credit.",
    "Paid ₹20,000 and ₹18,000 to Rahul.",
    "Paid ₹18,000 and ₹20,000 to Rahul.",
    "Paid to Rahul ₹20,000 and ₹18,000.",
    "Paid him ₹20,000 and ₹18,000.",
    "Received ₹20,000 and ₹18,000 from Rahul.",
    "Received from Rahul ₹20,000 and ₹18,000.",
    # multi-transaction: the FIRST segment is the exact failing input -
    # the per-segment gate must refuse the whole question (Sprint 15I-S
    # never hides an ambiguous segment behind a clean second one).
    "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000. "
    "Sold goods to Kavita for ₹8,000 on credit.",
    # payment-step merge: 'paid him X and Y' binds BOTH figures to the
    # payment - the merged journal must never pick one positionally.
    "Purchased goods from Rahul ₹20,000 on credit. Paid him ₹9,000 and "
    "₹8,000.",
    # payment-step merge: a second stated figure with no role ('gave a
    # discount of ₹500') must refuse, never vanish from the journal.
    "Purchased goods from Rahul ₹20,000 on credit. Paid him ₹9,000 and "
    "gave a discount of ₹500.",
]


def test_i_ambiguity():
    print("PART I - GENUINE AMBIGUITY REFUSES (never guessed)")
    for i, q in enumerate(AMBIGUOUS_CASES):
        hard = reason_bk_question(q)
        check(f"I.{i + 1} {q[:44]!r} REVIEW_REQUIRED",
              hard.get("status") == "REVIEW_REQUIRED", hard.get("status"))
        check(f"I.{i + 1} zero lines", lines(hard) == [], str(lines(hard)))
        flow = run_fyjc_student_flow(q)
        check(f"I.{i + 1} flow agrees",
              flow.get("status") == "REVIEW_REQUIRED",
              flow.get("status"))
        check(f"I.{i + 1} flow zero lines", lines(flow) == [],
              str(lines(flow)))


# ---------------------------------------------------------------------------
# J. Adversarial amount ordering - first amount is never 'the' amount
# ---------------------------------------------------------------------------

def test_j_adversarial_ordering():
    print("PART J - ADVERSARIAL AMOUNT ORDERING")
    cases = [
        "Purchased goods from Rahul on credit for ₹18,000 and ₹20,000.",
        "Purchased goods on credit from Rahul for ₹18,000 and ₹20,000.",
        "Bought goods from Rahul on credit, paid ₹20,000 and ₹18,000.",
    ]
    for i, q in enumerate(cases):
        hard = reason_bk_question(q)
        check(f"J.{i + 1} REVIEW_REQUIRED (never first-amount-wins)",
              hard.get("status") == "REVIEW_REQUIRED", hard.get("status"))
        check(f"J.{i + 1} zero lines", lines(hard) == [], str(lines(hard)))


# ---------------------------------------------------------------------------
# K. Safety invariants: determinism, balance, no invented amounts
# ---------------------------------------------------------------------------

def test_k_safety_invariants():
    print("PART K - SAFETY INVARIANTS")

    all_cases = ([EXACT, REVERSED] + [c[1] for c in [
        ("C", "Purchased goods for ₹20,000 from Rahul on credit with "
              "₹2,000 trade discount."),
        ("D", "Purchased goods with a list price of ₹25,000 at 10% trade "
              "discount from Rahul on credit."),
        ("E", "Purchased goods for ₹10,000 plus CGST 6% and SGST 6% from "
              "Ravi on credit."),
        ("F", "Received ₹9,500 from Rahul in full settlement of his account "
              "of ₹10,000."),
        ("G", "Sold goods to Ram ₹12,000. Received ₹5,000 from him."),
        ("H", "Purchased goods from Ravi for ₹10,000 on credit. Sold goods "
              "to Kavita for ₹8,000 on credit."),
    ]] + AMBIGUOUS_CASES)

    # the canonical-lineage layer never treats a VERIFIED multi-
    # transaction question as ambiguous just because the whole-text
    # resolver sees several amounts (per-segment resolution is the
    # authority; Sprint 15I-S keeps the resolver's refusal at the
    # authority boundary, not in the lineage metadata).
    from backend.maths.fyjc_15g import canonicalize_bk
    multi_verified = [
        "Started business with cash Rs.1,00,000. Purchased goods for cash "
        "Rs.20,000. Purchased furniture for Rs.10,000 from Rahul. Paid "
        "rent Rs.5,000.",
        "Sold goods to Meena for cash Rs.12,000; received commission "
        "Rs.500; paid salaries Rs.6,000.",
        "Returned goods to Suresh worth Rs.2,000; purchased goods from "
        "Suresh for cash Rs.8,000.",
        "Sold goods to Mohan for Rs.10,000 on credit. Received from him "
        "Rs.9,800 in full settlement of his account, discount allowed "
        "Rs.200.",
    ]
    for i, q in enumerate(multi_verified):
        canon = canonicalize_bk(q)
        check(f"M.{i + 1} multi-transaction lineage stays VERIFIED/HIGH",
              canon.get("status") == "VERIFIED"
              and canon.get("confidence") == "HIGH",
              f"{canon.get('status')}/{canon.get('confidence')}")

    unsafe_confident = 0
    unbalanced_verified = 0
    from backend.maths.fyjc_bk_reasoning import (
        _split_transactions,
        resolve_transaction_amounts,
    )
    for q in all_cases:
        hard = reason_bk_question(q)
        if hard.get("status") == "VERIFIED":
            if not balanced(hard):
                unbalanced_verified += 1
                unsafe_confident += 1
            # a single-transaction VERIFIED journal is only legal when
            # the authority boundary itself resolved every stated amount
            # (trade-discount netting means a list price need not
            # literally appear in the journal - the boundary verdict is
            # the source of truth, never a naive line-amount match).
            # Multi-transaction questions resolve per-segment inside the
            # engine (every segment must journal VERIFIED), so the
            # whole-question resolution boundary legitimately refuses.
            if len(_split_transactions(q)) == 1:
                resolution = resolve_transaction_amounts(q)
                if resolution.get("status") != "VERIFIED":
                    unsafe_confident += 1
        else:
            # refusal never carries journal lines (canonical journal can
            # never be manufactured from unresolved amounts)
            if lines(hard):
                unsafe_confident += 1

    check("K.1 unsafe confident = 0 (unresolved amount never journaled)",
          unsafe_confident == 0, f"unsafe_confident={unsafe_confident}")
    check("K.2 unbalanced VERIFIED = 0", unbalanced_verified == 0,
          f"unbalanced={unbalanced_verified}")

    # determinism across repeated runs
    deterministic = True
    for q in all_cases[:6]:
        r1 = reason_bk_question(q)
        r2 = reason_bk_question(q)
        if (r1.get("status"), lines(r1)) != (r2.get("status"), lines(r2)):
            deterministic = False
    check("K.3 deterministic across repeated runs", deterministic)

    # flow verdict == hardened verdict for every case (adapter is a pure
    # presentation translation, never an authority)
    adapter_agrees = True
    for q in all_cases:
        h = reason_bk_question(q)
        f = run_fyjc_student_flow(q)
        if h.get("status") != f.get("status"):
            adapter_agrees = False
    check("K.4 flow verdict == hardened verdict for every case",
          adapter_agrees)


# ---------------------------------------------------------------------------
# L. Real Streamlit AppTest
# ---------------------------------------------------------------------------

def test_l_apptest():
    print("PART L - REAL STREAMLIT STUDY/VERIFY PATH")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("L.0 apptest available", False, str(exc))
        return

    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    check("L.1 app entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("L.2 FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])

    at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()

    # exact failing input: REVIEW_REQUIRED, no fabricated journal
    at.text_area(key="fte_fyjc_question").set_value(EXACT).run()
    at.button(key="fte_fyjc_go").click().run()
    check("L.3 exact input renders without exception", not at.exception,
          [e.stack_trace for e in at.exception])
    md = " ".join(m.value for m in at.markdown)
    check("L.4 shows REVIEW REQUIRED / clarification (no VERIFIED)",
          "VERIFIED" not in md and
          ("REVIEW" in md.upper() or "clarity" in md
           or "clarify" in md or "Almost there" in md), md[:300])
    check("L.5 no fabricated journal table",
          not ("Purchases" in md and "Rahul" in md and "20,000" in md),
          md[:300])

    # legitimate TD: VERIFIED with canonical ₹22,500 journal
    td_q = ("Purchased goods with a list price of ₹25,000 at 10% trade "
            "discount from Ravi Kumar on credit.")
    at.text_area(key="fte_fyjc_question").set_value(td_q).run()
    at.button(key="fte_fyjc_go").click().run()
    check("L.6 TD renders without exception", not at.exception,
          [e.stack_trace for e in at.exception])
    md2 = " ".join(m.value for m in at.markdown)
    check("L.7 TD shows VERIFIED", "VERIFIED" in md2, md2[:200])
    check("L.8 TD shows ₹22,500 journal", "22,500" in md2, md2[:200])

    # legitimate GST: still VERIFIED
    gst_q = ("Purchased goods for ₹10,000 plus CGST 6% and SGST 6% "
             "from Ravi on credit.")
    at.text_area(key="fte_fyjc_question").set_value(gst_q).run()
    at.button(key="fte_fyjc_go").click().run()
    check("L.9 GST renders without exception", not at.exception,
          [e.stack_trace for e in at.exception])
    md3 = " ".join(m.value for m in at.markdown)
    check("L.10 GST shows VERIFIED", "VERIFIED" in md3, md3[:200])


def main():
    test_a_exact_failure_all_layers()
    test_b_reversed()
    test_structured_verified()
    test_i_ambiguity()
    test_j_adversarial_ordering()
    test_k_safety_invariants()
    test_l_apptest()

    print(f"\nTOTAL: {TOTAL[0]} checks")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
