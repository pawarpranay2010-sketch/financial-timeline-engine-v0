#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-TX - FYJC Accounting Capability Expansion & Transaction
Completeness
scripts/fte_fyjc_15tx_capability_expansion_test.py

Locks in the Sprint 15I-TX capability expansion. The engine changes are
all in the single hardened authority (backend/maths/fyjc_bk_reasoning.py);
this gate proves them through the REAL production path (reason_bk_question
-> hardened adapter -> run_fyjc_accounting_flow -> run_fyjc_student_flow)
plus the REAL Streamlit Study/Verify page.

Capabilities verified:
  A. Multi-transaction completeness - a customer-return + supplier-return
     chain ('X returned us goods ... and the same were returned to Y')
     journals BOTH return entries (the second return is NEVER silently
     dropped), and a purchase + return-of-the-same-goods keeps both
     entries. A standalone 'the same were returned' continuation refuses
     (the goods identity is not established).
  B. Explicit GST-mode resolution - CGST + SGST, IGST, intra/inter-state
     evidence and input/output direction verify; a rate alone (no mode
     evidence) stays REVIEW_REQUIRED; a GST transaction carrying a
     partial payment step refuses instead of dropping the payment.
  C. Business/personal splits - a bank withdrawal with an explicit
     personal-use portion and a goods purchase with an explicit
     personal-use goods value both journal a deterministic compound
     entry; every stated amount is consumed by a role.
  D. Special-purpose transactions - donated goods (Donation Dr /
     Purchases Cr), cash donations, and free samples verify; a donation
     carrying a stated profit element refuses (the profit treatment is
     not deterministically established).
  E. Contextual expense resolution - 'paid Rs.X for mobile recharge',
     'paid Rs.X for shop rent', 'paid interest for loan by cheque'
     resolve to the registered expense account; a possessive-pronoun
     bill ('paid his mobile bill') is never silently booked as a
     business expense.
  F. Deliberate surface expansion - a settlement by cheque
     ('Settled X's account by issuing him a cheque of Rs.Y') verifies as
     Dr party / Cr Bank; placing an order is REVIEW_REQUIRED (an order is
     not a transaction).
  G. Safety invariants - zero unsafe-confident journals, zero invented
     accounts, zero unbalanced VERIFIED, no silently dropped segments,
     and the flow verdict equals the hardened verdict for every case.

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
# A. Multi-transaction completeness (Test 9-style return chains)
# ---------------------------------------------------------------------------

def test_a_multi_transaction_completeness():
    print("PART A - MULTI-TRANSACTION COMPLETENESS (RETURN CHAINS)")

    # Test 9-style: customer returns goods, the same goods are then
    # returned to the supplier. BOTH return entries must survive.
    t9 = ("Mr. Novak Djokovic returned us goods worth Rs.6,500 (net), "
          "and the same were returned to Mr. Roger Federer.")
    res = reason_bk_question(t9)
    check("A.1 return chain VERIFIED", res.get("status") == "VERIFIED",
          res.get("status"))
    got = lines(res)
    want = sorted([
        ("Sales Returns", "6500"), ("Mr. Novak Djokovic", "6500"),
        ("Mr. Roger Federer", "6500"), ("Purchase Returns", "6500"),
    ])
    check("A.2 BOTH return entries survive (0 dropped segments)",
          got == want, str(got))
    check("A.3 return chain balances", balanced(res))
    check("A.4 two journals in the chain",
          len(res.get("journals") or []) == 2,
          str(len(res.get("journals") or [])))

    # Lowercase + no titles variant.
    t9b = "Mohan returned us goods worth Rs.6,500, and the same were returned to Rahul."
    res_b = reason_bk_question(t9b)
    check("A.5 lowercase return chain VERIFIED",
          res_b.get("status") == "VERIFIED", res_b.get("status"))
    check("A.6 lowercase chain keeps both entries",
          lines(res_b) == sorted([
              ("Sales Returns", "6500"), ("Mohan", "6500"),
              ("Rahul", "6500"), ("Purchase Returns", "6500")]),
          str(lines(res_b)))

    # Purchase + return of the same goods: both entries survive.
    t9c = ("Purchased goods from Rahul on credit Rs.20,000, and the same "
           "were returned to Rahul.")
    res_c = reason_bk_question(t9c)
    check("A.7 purchase + same-goods return VERIFIED",
          res_c.get("status") == "VERIFIED", res_c.get("status"))
    check("A.8 purchase and return both journaled",
          lines(res_c) == sorted([
              ("Purchases", "20000"), ("Rahul", "20000"),
              ("Rahul", "20000"), ("Purchase Returns", "20000")]),
          str(lines(res_c)))

    # A standalone continuation has no identified goods -> refuse.
    t9d = "The same were returned to Rahul for Rs.1,000."
    res_d = reason_bk_question(t9d)
    check("A.9 standalone continuation REVIEW_REQUIRED",
          res_d.get("status") == "REVIEW_REQUIRED", res_d.get("status"))
    check("A.10 standalone continuation zero lines",
          lines(res_d) == [], str(lines(res_d)))

    # A continuation after a non-goods transaction refuses (never invents).
    t9e = ("Started business with cash Rs.50,000. The same were returned "
           "to Rahul.")
    res_e = reason_bk_question(t9e)
    check("A.11 continuation after non-return REVIEW_REQUIRED",
          res_e.get("status") == "REVIEW_REQUIRED", res_e.get("status"))

    # Plain single returns keep working (no regression).
    for q in ("Returned goods worth Rs.1,000 to Rahul.",
              "Mohan returned goods worth Rs.800."):
        r = reason_bk_question(q)
        check(f"A.12 single return VERIFIED: {q[:40]}",
              r.get("status") == "VERIFIED", r.get("status"))


# ---------------------------------------------------------------------------
# B. Explicit GST-mode resolution
# ---------------------------------------------------------------------------

def test_b_gst_mode():
    print("PART B - EXPLICIT GST-MODE RESOLUTION")

    g_cgst = ("Purchased goods from Rahul on credit Rs.20,000 with CGST "
              "@ 9% and SGST @ 9%.")
    r = reason_bk_question(g_cgst)
    check("B.1 explicit CGST+SGST VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("B.2 CGST+SGST journal amounts",
          lines(r) == sorted([
              ("Purchases", "20000"), ("Input CGST", "1800.00"),
              ("Input SGST", "1800.00"), ("Rahul", "23600.00")]),
          str(lines(r)))

    g_igst = ("Purchased goods from Rahul on credit Rs.20,000 with "
              "IGST @ 18%.")
    r = reason_bk_question(g_igst)
    check("B.3 explicit IGST VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("B.4 IGST journal amounts",
          lines(r) == sorted([
              ("Purchases", "20000"), ("Input IGST", "3600.00"),
              ("Rahul", "23600.00")]), str(lines(r)))

    g_intra = ("Purchased goods for cash Rs.20,000, GST @ 18%, "
               "intra-state.")
    r = reason_bk_question(g_intra)
    check("B.5 intra-state marker resolves CGST+SGST",
          r.get("status") == "VERIFIED", r.get("status"))

    g_inter = ("Purchased goods for cash Rs.20,000, GST @ 18%, "
               "inter-state.")
    r = reason_bk_question(g_inter)
    check("B.6 inter-state marker resolves IGST",
          r.get("status") == "VERIFIED", r.get("status"))
    check("B.7 inter-state uses Input IGST",
          any(l[0] == "Input IGST" for l in lines(r)), str(lines(r)))

    g_input = ("Purchased goods for cash Rs.20,000, input GST @ 18%, "
               "CGST and SGST.")
    r = reason_bk_question(g_input)
    check("B.8 explicit input GST direction VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))

    g_sale = ("Sold goods to Mohan on credit Rs.20,000 with IGST @ 18%.")
    r = reason_bk_question(g_sale)
    check("B.9 sale output IGST VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("B.10 sale uses Output IGST",
          any(l[0] == "Output IGST" for l in lines(r)), str(lines(r)))

    # GST rate alone (no mode evidence) stays REVIEW_REQUIRED.
    g_rate = ("Purchased goods from Rahul on credit Rs.20,000 with "
              "GST @ 18%.")
    r = reason_bk_question(g_rate)
    check("B.11 GST rate alone REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", r.get("status"))
    check("B.12 rate-alone zero lines", lines(r) == [], str(lines(r)))

    # GST + partial payment step: refuse instead of dropping the payment.
    g_pay = ("Purchased goods from Rahul on credit Rs.20,000 with CGST "
             "and SGST @ 9% each, and issued a cheque for 50% of the "
             "amount.")
    r = reason_bk_question(g_pay)
    check("B.13 GST + partial payment REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", r.get("status"))
    check("B.14 GST + partial payment zero lines",
          lines(r) == [], str(lines(r)))
    why = " ".join(str(r.get("why_not") or "").lower().split())
    check("B.15 refusal names the payment step", "payment" in why
          or "cheque" in why, why[:120])

    # GST expense still verifies.
    g_exp = ("Paid rent Rs.5,000 with CGST and SGST @ 9%.")
    r = reason_bk_question(g_exp)
    check("B.16 GST expense VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))


# ---------------------------------------------------------------------------
# C. Business / personal splits
# ---------------------------------------------------------------------------

def test_c_business_personal_splits():
    print("PART C - EXPLICIT BUSINESS / PERSONAL SPLITS")

    b1 = ("Withdrew Rs.10,000 from Bank, out of which Rs.3,500 were used "
          "by Mr. Carlos Alcaraz for personal use, and the rest for "
          "office use.")
    res = reason_bk_question(b1)
    check("C.1 bank-withdrawal split VERIFIED",
          res.get("status") == "VERIFIED", res.get("status"))
    check("C.2 split journal (Cash+Drawings / Bank+Cash)",
          lines(res) == sorted([
              ("Cash", "10000"), ("Drawings", "3500"),
              ("Bank", "10000"), ("Cash", "3500")]), str(lines(res)))
    check("C.3 split journal balances", balanced(res))

    b1b = ("Withdrew Rs.10,000 from Bank, out of which Rs.3,500 were "
           "used for personal use and the rest for office use.")
    res_b = reason_bk_question(b1b)
    check("C.4 split without party VERIFIED",
          res_b.get("status") == "VERIFIED", res_b.get("status"))

    b2 = ("Purchased goods for cash Rs.10,000, out of which goods worth "
          "Rs.2,000 were taken for personal use.")
    res2 = reason_bk_question(b2)
    check("C.5 goods-personal split (cash) VERIFIED",
          res2.get("status") == "VERIFIED", res2.get("status"))
    check("C.6 goods-personal split journal",
          lines(res2) == sorted([
              ("Purchases", "8000"), ("Drawings", "2000"),
              ("Cash", "10000")]), str(lines(res2)))

    b3 = ("Purchased goods from Rahul on credit Rs.10,000, out of which "
          "goods worth Rs.2,000 were taken for personal use.")
    res3 = reason_bk_question(b3)
    check("C.7 goods-personal split (credit) VERIFIED",
          res3.get("status") == "VERIFIED", res3.get("status"))
    check("C.8 credit split journal credits the party",
          lines(res3) == sorted([
              ("Purchases", "8000"), ("Drawings", "2000"),
              ("Rahul", "10000")]), str(lines(res3)))

    # No role for the second figure -> the 15I-S gate still refuses.
    bad = "Withdrew Rs.10,000 from Bank and Rs.3,500 for office use."
    res_bad = reason_bk_question(bad)
    check("C.9 unanchored split REVIEW_REQUIRED",
          res_bad.get("status") == "REVIEW_REQUIRED",
          res_bad.get("status"))


# ---------------------------------------------------------------------------
# D. Special-purpose transactions
# ---------------------------------------------------------------------------

def test_d_special_purpose():
    print("PART D - SPECIAL-PURPOSE TRANSACTIONS")

    d1 = "Donated goods worth Rs.7,500 to charity."
    r = reason_bk_question(d1)
    check("D.1 donated goods VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("D.2 Donation Dr / Purchases Cr",
          lines(r) == sorted([
              ("Donation", "7500"), ("Purchases", "7500")]), str(lines(r)))

    d2 = "Donated Rs.5,000 to charity."
    r = reason_bk_question(d2)
    check("D.3 cash donation VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("D.4 cash donation journal",
          lines(r) == sorted([("Donation", "5000"), ("Cash", "5000")]),
          str(lines(r)))

    # A stated profit element is refused (never invented).
    d3 = "Donated goods worth Rs.7,500 (including profit of 25%) to charity."
    r = reason_bk_question(d3)
    check("D.5 donation with profit REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", r.get("status"))
    check("D.6 profit variant zero lines", lines(r) == [],
          str(lines(r)))
    why = " ".join(str(r.get("why_not") or "").lower().split())
    check("D.7 refusal names the profit element", "profit" in why,
          why[:120])

    # Free samples unchanged.
    d4 = "Distributed goods worth Rs.2,000 as free samples."
    r = reason_bk_question(d4)
    check("D.8 free samples VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("D.9 Advertisement Dr / Purchases Cr",
          lines(r) == sorted([
              ("Advertisement", "2000"), ("Purchases", "2000")]),
          str(lines(r)))


# ---------------------------------------------------------------------------
# E. Contextual expense resolution
# ---------------------------------------------------------------------------

def test_e_contextual_expenses():
    print("PART E - CONTEXTUAL EXPENSE / ACCOUNT RESOLUTION")

    e1 = "Paid Rs.500 for mobile recharge."
    r = reason_bk_question(e1)
    check("E.1 mobile recharge VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("E.2 Telephone Expenses Dr / Cash Cr",
          lines(r) == sorted([
              ("Telephone Expenses", "500"), ("Cash", "500")]),
          str(lines(r)))

    e2 = "Paid Rs.4,000 for shop rent."
    r = reason_bk_question(e2)
    check("E.3 shop rent VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("E.4 Rent Dr / Cash Cr",
          lines(r) == sorted([("Rent", "4000"), ("Cash", "4000")]),
          str(lines(r)))

    e3 = "Paid interest for loan by cheque Rs.2,000."
    r = reason_bk_question(e3)
    check("E.5 interest-for-loan VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))
    check("E.6 Interest Paid Dr / Bank Cr",
          lines(r) == sorted([
              ("Interest Paid", "2000"), ("Bank", "2000")]),
          str(lines(r)))

    # A possessive-pronoun bill is a personal bill - never booked.
    e4 = "Paid his mobile recharge bill Rs.500."
    r = reason_bk_question(e4)
    check("E.7 possessive bill not a business expense",
          r.get("status") != "VERIFIED", r.get("status"))
    check("E.8 possessive bill zero lines", lines(r) == [],
          str(lines(r)))


# ---------------------------------------------------------------------------
# F. Deliberate FYJC surface expansion (settlement cheque + orders)
# ---------------------------------------------------------------------------

def test_f_surface_expansion():
    print("PART F - DELIBERATE FYJC SURFACE EXPANSION")

    f1 = ("Settled Mr. Roger Federer's account by issuing him a cheque "
          "of Rs.41,500.")
    r = reason_bk_question(f1)
    check("F.1 settlement cheque VERIFIED", r.get("status") == "VERIFIED",
          r.get("status"))
    check("F.2 Dr party / Cr Bank",
          lines(r) == sorted([
              ("Mr. Roger Federer", "41500"), ("Bank", "41500")]),
          str(lines(r)))

    f2 = "Settled the account of Mr. Roger Federer by cheque Rs.41,500."
    r = reason_bk_question(f2)
    check("F.3 'account of' form VERIFIED",
          r.get("status") == "VERIFIED", r.get("status"))

    f3 = ("Placed an order for goods to Mr. Jannik Sinner worth "
          "Rs.70,000 subject to 15% T.D. & C.D. of Rs.2,500 & 18% GST.")
    r = reason_bk_question(f3)
    check("F.4 placing an order REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", r.get("status"))
    check("F.5 order zero lines", lines(r) == [], str(lines(r)))
    why = " ".join(str(r.get("why_not") or "").lower().split())
    check("F.6 refusal explains orders are not transactions",
          "order" in why and "not a transaction" in why, why[:120])


# ---------------------------------------------------------------------------
# G. Safety invariants + layer agreement over every case
# ---------------------------------------------------------------------------

def test_g_safety_invariants():
    print("PART G - SAFETY INVARIANTS + LAYER AGREEMENT")

    all_cases = [
        "Mr. Novak Djokovic returned us goods worth Rs.6,500 (net), and the same were returned to Mr. Roger Federer.",
        "Mohan returned us goods worth Rs.6,500, and the same were returned to Rahul.",
        "Purchased goods from Rahul on credit Rs.20,000, and the same were returned to Rahul.",
        "The same were returned to Rahul for Rs.1,000.",
        "Withdrew Rs.10,000 from Bank, out of which Rs.3,500 were used by Mr. Carlos Alcaraz for personal use, and the rest for office use.",
        "Purchased goods for cash Rs.10,000, out of which goods worth Rs.2,000 were taken for personal use.",
        "Purchased goods from Rahul on credit Rs.10,000, out of which goods worth Rs.2,000 were taken for personal use.",
        "Donated goods worth Rs.7,500 to charity.",
        "Donated Rs.5,000 to charity.",
        "Donated goods worth Rs.7,500 (including profit of 25%) to charity.",
        "Distributed goods worth Rs.2,000 as free samples.",
        "Paid Rs.500 for mobile recharge.",
        "Paid Rs.4,000 for shop rent.",
        "Paid interest for loan by cheque Rs.2,000.",
        "Paid his mobile recharge bill Rs.500.",
        "Settled Mr. Roger Federer's account by issuing him a cheque of Rs.41,500.",
        "Settled the account of Mr. Roger Federer by cheque Rs.41,500.",
        "Placed an order for goods to Mr. Jannik Sinner worth Rs.70,000.",
        "Purchased goods from Rahul on credit Rs.20,000 with CGST @ 9% and SGST @ 9%.",
        "Purchased goods from Rahul on credit Rs.20,000 with IGST @ 18%.",
        "Purchased goods from Rahul on credit Rs.20,000 with GST @ 18%.",
        "Purchased goods from Rahul on credit Rs.20,000 with CGST and SGST @ 9% each, and issued a cheque for 50% of the amount.",
        "Purchased goods for cash Rs.20,000, GST @ 18%, intra-state.",
        "Purchased goods for cash Rs.20,000, GST @ 18%, inter-state.",
        "Sold goods to Mohan on credit Rs.20,000 with IGST @ 18%.",
        "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000.",
    ]

    unsafe_confident = 0
    unbalanced_verified = 0
    invented = 0
    adapter_agrees = True

    from backend.maths.fyjc_bk_reasoning import (
        canonical_account,
        CLASS_PERSONAL,
        _TRADITIONAL_OVERRIDES,
        _split_transactions,
        resolve_transaction_amounts,
    )

    for q in all_cases:
        hard = reason_bk_question(q)
        if hard.get("status") == "VERIFIED":
            if not balanced(hard):
                unbalanced_verified += 1
                unsafe_confident += 1
            for line in (hard.get("debit_lines") or []) + \
                    (hard.get("credit_lines") or []):
                acc = line.get("account")
                if not acc:
                    continue
                # a legitimate journal account is either in the canonical
                # chart, a known FYJC account from the engine's override
                # table (e.g. Donation A/c), or a party the engine
                # classified Personal through its party context (an
                # honorific name like 'Mr. Roger Federer' is still a
                # Personal account). Anything else would be an invented
                # account.
                if canonical_account(acc) is not None:
                    continue
                if acc in _TRADITIONAL_OVERRIDES:
                    continue
                if line.get("class") == CLASS_PERSONAL:
                    continue
                invented += 1
            # a single-transaction VERIFIED journal must be supported by
            # the authority boundary's own amount resolution.
            if len(_split_transactions(q)) == 1:
                resolution = resolve_transaction_amounts(q)
                if resolution.get("status") != "VERIFIED" \
                        and resolution.get("split") is None:
                    unsafe_confident += 1
        else:
            if lines(hard):
                unsafe_confident += 1
        # flow verdict == hardened verdict (the adapter never
        # reinterprets a result).
        flow = run_fyjc_student_flow(q)
        if flow.get("status") != hard.get("status"):
            adapter_agrees = False

    check("G.1 unsafe confident = 0", unsafe_confident == 0,
          f"unsafe_confident={unsafe_confident}")
    check("G.2 unbalanced VERIFIED = 0", unbalanced_verified == 0,
          f"unbalanced={unbalanced_verified}")
    check("G.3 invented accounts = 0", invented == 0, f"invented={invented}")
    check("G.4 flow verdict == hardened verdict for every case",
          adapter_agrees)

    # Determinism across repeated runs.
    deterministic = True
    for q in all_cases[:8]:
        r1 = reason_bk_question(q)
        r2 = reason_bk_question(q)
        if (r1.get("status"), lines(r1)) != (r2.get("status"), lines(r2)):
            deterministic = False
    check("G.5 deterministic across repeated runs", deterministic)


# ---------------------------------------------------------------------------
# H. Real Streamlit AppTest (Study/Verify page)
# ---------------------------------------------------------------------------

def test_h_apptest():
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

    # Return chain: VERIFIED with BOTH return entries, no dropped segment.
    md = ask("Mr. Novak Djokovic returned us goods worth Rs.6,500 (net), "
             "and the same were returned to Mr. Roger Federer.")
    check("H.3 return chain renders without exception", not at.exception,
          [e.stack_trace for e in at.exception])
    check("H.4 return chain shows VERIFIED", "VERIFIED" in md, md[:200])
    check("H.5 return chain shows BOTH return entries",
          "Sales Returns" in md and "Purchase Returns" in md, md[:300])
    check("H.6 no misleading Almost-there panel on a VERIFIED question",
          "Almost there" not in md, md[:200])

    # Business/personal split.
    md = ask("Withdrew Rs.10,000 from Bank, out of which Rs.3,500 were "
             "used for personal use, and the rest for office use.")
    check("H.7 split renders VERIFIED", "VERIFIED" in md, md[:200])
    check("H.8 split shows Drawings and Cash amounts",
          "Drawings" in md and "3,500" in md and "10,000" in md, md[:300])

    # Donated goods.
    md = ask("Donated goods worth Rs.7,500 to charity.")
    check("H.9 donation renders VERIFIED", "VERIFIED" in md, md[:200])
    check("H.10 donation shows Donation/Purchases",
          "Donation" in md and "Purchases" in md, md[:300])

    # Ambiguous amount (15I-S regression): REVIEW, no fabricated journal.
    md = ask("Purchased goods for Rs.20,000 from Rahul on credit and "
             "Rs.18,000.")
    check("H.11 ambiguous multi-amount not VERIFIED",
          "VERIFIED" not in md, md[:200])
    check("H.12 ambiguous multi-amount shows clarification",
          "REVIEW" in md.upper() or "clarity" in md or "clarify" in md,
          md[:200])

    # Settlement cheque.
    md = ask("Settled Mr. Roger Federer's account by issuing him a cheque "
             "of Rs.41,500.")
    check("H.13 settlement cheque renders VERIFIED",
          "VERIFIED" in md, md[:200])
    check("H.14 settlement cheque shows the party",
          "Roger Federer" in md, md[:200])


def main():
    test_a_multi_transaction_completeness()
    test_b_gst_mode()
    test_c_business_personal_splits()
    test_d_special_purpose()
    test_e_contextual_expenses()
    test_f_surface_expansion()
    test_g_safety_invariants()
    test_h_apptest()

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
