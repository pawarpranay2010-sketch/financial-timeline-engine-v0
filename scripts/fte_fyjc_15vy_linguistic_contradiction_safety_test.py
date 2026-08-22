#!/usr/bin/env python3
"""
Platrixa
Sprint 15I-VY - Linguistic Normalization & Contradiction Safety Gate
scripts/fte_fyjc_15vy_linguistic_contradiction_safety_test.py

Locks in the Sprint 15I-VY hardening that sits between messy student
language and the deterministic bookkeeping authority:

  PART A - LINGUISTIC NORMALIZATION. 'gds' -> 'goods', '10k' -> '10,000',
  'td'/'t.d.' -> 'trade discount', 'cd'/'c.d.' -> 'cash discount', plus
  harmless whitespace/casing collapse - each with full provenance
  (original text, normalized representation, rule id, confidence,
  semantic-change flag). The layer NEVER rewrites a party token ('raam'
  is never promoted to 'Ram'), never invents an account/amount/rate/
  transaction type, and an unknown abbreviation or single-letter
  initial forces REVIEW_REQUIRED instead of a guess.

  PART B - NUMERIC NORMALIZATION. '10k' -> 10,000, '25k' -> 25,000,
  '1.5k' -> 1,500. Normalized values enter the existing amount
  ownership/consumption system (no ignored amounts, no first-amount
  wins, no fabricated values).

  PART C - MATHEMATICAL CONTRADICTION DETECTION. Before any VERIFIED is
  possible, a global validator compares explicitly stated facts:
    C1. payment + outstanding vs transaction value (INVALID_INPUT_MATH
        when the partition contradicts the total, both component
        orders);
    C2. stated trade-discount amount vs stated rate;
    C3. stated GST components (CGST+SGST / IGST) vs the stated GST rate
        applied to the trade-discount-net taxable base;
    C4. a full settlement that exceeds the account it settles.
  A mathematically contradictory transaction NEVER becomes VERIFIED
  merely because its resulting journal happens to balance, and emits
  zero journal lines.

  PART D - DISTINCT REFUSAL CLASSES. INVALID_INPUT_MATH (stated facts
  mathematically contradict) is distinct from REVIEW_REQUIRED
  (recognized-but-unmerged, or unknown abbreviation / unsafe party),
  from NOT_SUPPORTED (outside the FYJC boundary) and from BLOCKED
  (essential information missing). All four emit zero journal lines.

  PART E - INVARIANTS. unsafe confident = 0, ignored amounts = 0,
  ignored rates = 0, invented accounts = 0, invented discounts = 0,
  flow verdict == hardened authority verdict, repeated execution
  deterministic, and the real Streamlit Study/Verify path (AppTest)
  renders the same verdicts.

Historical behavior is locked: TD netting, CGST+SGST, IGST, partial
settlements, the 15I-S multi-amount refusal and every released gate
remain byte-identical for clean inputs (verified by the released
historical suite separately).

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
    BLOCKED,
    NOT_SUPPORTED,
    REVIEW_REQUIRED,
    VERIFIED,
)
from backend.maths.fyjc_normalization import (  # noqa: E402
    math_contradiction,
    normalize_fyjc_text,
    vy_harden,
)
from backend.maths.fyjc_student_flow import (  # noqa: E402
    run_fyjc_accounting_flow,
)

INVALID_INPUT_MATH = "INVALID_INPUT_MATH"

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


def nlines(res):
    if "outcome" in res:
        res = res.get("outcome") or {}
    return len((res.get("debit_lines") or []) + (res.get("credit_lines") or []))


def rules_used(prov):
    return sorted(p.get("rule") for p in prov)


def no_rewrite_of(prov, token):
    return all(str(p.get("original")) != token for p in prov)


# ---------------------------------------------------------------------------
# PART A - linguistic normalization + provenance
# ---------------------------------------------------------------------------
def test_a_normalization():
    print("PART A - LINGUISTIC NORMALIZATION")
    n = normalize_fyjc_text("Sold gds to raam 10k on credit 5% td")
    check("A.1 gds/10k/td normalized",
          n.text == "Sold goods to raam 10,000 on credit 5% trade discount",
          n.text)
    used = rules_used(n.provenance)
    check("A.2 all three rules recorded",
          "BK_NORM_GOODS" in used and "BK_NORM_NUMERIC_K" in used
          and "BK_NORM_TRADE_DISCOUNT" in used, str(used))
    check("A.3 no party rewrite ('raam' untouched)",
          no_rewrite_of(n.provenance, "raam"), str(n.provenance))
    for p in n.provenance:
        check(f"A.4 provenance {p.get('rule')} complete",
              all(k in p for k in ("original", "normalized", "rule",
                                   "confidence", "semantic_change")),
              str(p))
        check(f"A.5 {p.get('rule')} high confidence, no semantic change",
              p.get("confidence") == "high" and p.get("semantic_change") is False,
              str(p))

    # clean phrasing matches the normalized result byte-for-byte
    r = vy_harden("Sold goods to Ram Rs.10,000 on credit 5% TD")
    check("A.6 canonical form VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("A.7 TD applied (Ram 9,500 / Sales 9,500)",
          lines(r) == [("Ram", "9500.00"), ("Sales", "9500.00")],
          str(lines(r)))
    check("A.8 canonical form provenance has TD rule",
          "BK_NORM_TRADE_DISCOUNT" in rules_used(r.get("normalization") or []),
          str(rules_used(r.get("normalization") or [])))

    # mixed-case shorthand
    r = vy_harden("sold gds to RAM 10k on credit 5% td")
    check("A.9 mixed-case shorthand VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    # the engine's own deterministic party resolution canonicalizes the
    # case; the normalization layer itself never rewrote the token
    check("A.10 mixed-case amounts identical",
          lines(r) == [("Ram", "9500.00"), ("Sales", "9500.00")],
          str(lines(r)))

    # safe 'cd' abbreviation: cash discount never invented without a
    # settlement step the authority supports
    n = normalize_fyjc_text("Received Rs.9,500 from Ram in full settlement "
                            "of his account of Rs.10,000 allowing 5% cd")
    check("A.11 cd normalized to cash discount",
          "cash discount" in n.text and
          "BK_NORM_CASH_DISCOUNT" in rules_used(n.provenance), n.text)
    r = vy_harden("Received Rs.9,500 from Ram in full settlement of his "
                  "account of Rs.10,000 allowing 5% cd")
    check("A.12 cd settlement refuses safely (no invented discount)",
          r.get("status") == REVIEW_REQUIRED and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")

    # harmless whitespace collapse
    n = normalize_fyjc_text("Sold   goods   to   Ram   Rs.10,000   on "
                            "credit")
    check("A.13 whitespace collapsed",
          n.text == "Sold goods to Ram Rs.10,000 on credit", n.text)
    check("A.14 whitespace rule recorded",
          "BK_NORM_WHITESPACE" in rules_used(n.provenance),
          str(rules_used(n.provenance)))


# ---------------------------------------------------------------------------
# PART B - numeric normalization
# ---------------------------------------------------------------------------
def test_b_numeric():
    print("PART B - NUMERIC NORMALIZATION")
    r = vy_harden("Sold goods to Ram for 1.5k on credit")
    check("B.1 1.5k -> 1,500 VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B.2 1,500 journaled",
          lines(r) == [("Ram", "1500"), ("Sales", "1500")],
          str(lines(r)))

    r = vy_harden("Purchased goods from Rahul for 25k on credit")
    check("B.3 25k -> 25,000 VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B.4 25,000 journaled",
          lines(r) == [("Purchases", "25000"), ("Rahul", "25000")],
          str(lines(r)))

    r = vy_harden("Sold goods to Ram for 10k on credit at 5% td")
    check("B.5 k + td combined VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("B.6 10k net of 5% td = 9,500",
          lines(r) == [("Ram", "9500.00"), ("Sales", "9500.00")],
          str(lines(r)))

    # a 'k' that is NOT a thousand-suffix (attached letters, separated)
    n = normalize_fyjc_text("Sold goods to Ram for 20kg on credit")
    check("B.7 '20kg' never treated as thousands",
          "20kg" in n.text and "20,000" not in n.text, n.text)


# ---------------------------------------------------------------------------
# PART C - mathematical contradiction detection
# ---------------------------------------------------------------------------
def test_c_contradictions():
    print("PART C - CONTRADICTION DETECTION")
    # C1 - payment + outstanding contradict the transaction value
    r = vy_harden("Sold goods for Rs.10,000. Buyer paid Rs.6,000 "
                  "immediately and Rs.5,000 remains outstanding.")
    check("C.1 payment+outstanding > total -> INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")
    why = r.get("why_not") or ""
    check("C.2 refusal identifies both components",
          "6,000" in why and "5,000" in why and "11,000" in why, why[:160])

    # reversed component order - same refusal, no position bias
    r = vy_harden("Sold goods for Rs.10,000. Rs.5,000 remains outstanding; "
                  "the buyer paid Rs.6,000 immediately.")
    check("C.3 reversed order also INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")

    # a partition that exactly covers the total is NOT a contradiction -
    # it is REVIEW_REQUIRED with guidance (Platrixa never merges the split)
    r = vy_harden("Sold goods for Rs.10,000. Buyer paid Rs.6,000 "
                  "immediately and Rs.4,000 remains outstanding.")
    check("C.4 valid partition not INVALID (REVIEW_REQUIRED)",
          r.get("status") == REVIEW_REQUIRED, r.get("status"))
    check("C.5 valid partition zero lines",
          nlines(r) == 0, str(nlines(r)))

    # C2 - stated discount amount contradicts stated rate
    r = vy_harden("Sold goods worth Rs.10,000 to Ram at 10% trade "
                  "discount of Rs.800")
    check("C.6 inconsistent trade discount -> INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")
    check("C.7 refusal shows the expected discount",
          "1,000" in (r.get("why_not") or ""), (r.get("why_not") or "")[:160])

    # C3 - inconsistent GST components vs stated rate (net of TD)
    r = vy_harden("Purchased goods from Ram for Rs.20,000 at 10% trade "
                  "discount and 18% GST with CGST Rs.1,000 and SGST Rs.1,000")
    check("C.8 inconsistent GST components -> INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")
    check("C.9 GST refusal names the net taxable base",
          "18,000" in (r.get("why_not") or ""),
          (r.get("why_not") or "")[:160])

    # C4 - full settlement exceeding the account
    r = vy_harden("Received Rs.11,000 from Ram in full settlement of his "
                  "account of Rs.10,000")
    check("C.10 over-settlement -> INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")

    # valid counterparts must still VERIFY
    r = vy_harden("Sold goods worth Rs.10,000 to Ram at 10% trade discount")
    check("C.11 valid trade discount VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.12 valid TD journal (Ram 9,000 / Sales 9,000)",
          lines(r) == [("Ram", "9000.00"), ("Sales", "9000.00")],
          str(lines(r)))

    r = vy_harden("Purchased goods from Ram for Rs.20,000 at 10% trade "
                  "discount and 18% GST with CGST Rs.1,620 and SGST Rs.1,620")
    check("C.13 valid CGST+SGST VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.14 valid GST journal (net 18,000 + 1,620 + 1,620)",
          lines(r) == [("Input CGST", "1620"), ("Input SGST", "1620"),
                       ("Purchases", "18000.00"), ("Ram", "21240.00")],
          str(lines(r)))

    r = vy_harden("Purchased goods from Ram for Rs.20,000 at 10% trade "
                  "discount and 18% IGST with IGST Rs.3,240")
    check("C.15 valid IGST VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))

    # C5 - a stated cash-discount AMOUNT must reconcile the full
    # settlement (received + stated discount == account)
    r = vy_harden("Received Rs.9,000 from Ram in full settlement of his "
                  "account of Rs.10,000 allowing cash discount of Rs.500")
    check("C.16 inconsistent cash discount -> INVALID_INPUT_MATH",
          r.get("status") == INVALID_INPUT_MATH and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")
    r = vy_harden("Received Rs.9,500 from Ram in full settlement of his "
                  "account of Rs.10,000 allowing cash discount of Rs.500")
    check("C.17 consistent cash discount VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("C.18 consistent cash discount journal",
          lines(r) == [("Cash", "9500"), ("Discount Allowed", "500"),
                       ("Ram", "10000")], str(lines(r)))


# ---------------------------------------------------------------------------
# PART D - refusal classes / unknown input handling
# ---------------------------------------------------------------------------
def test_d_concerns():
    print("PART D - UNKNOWN ABBREVIATION / UNSAFE PARTY")
    # unknown abbreviation -> REVIEW_REQUIRED, never a guess
    n = normalize_fyjc_text("Sold goods to Ram Rs.10,000 on credit 5% xd")
    check("D.1 unknown abbreviation flagged as concern",
          any("xd" in c for c in n.concerns), str(n.concerns))
    r = vy_harden("Sold goods to Ram Rs.10,000 on credit 5% xd")
    check("D.2 unknown abbreviation refuses, zero lines",
          r.get("status") == REVIEW_REQUIRED and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")

    # single-letter initial (unsafe party identity) -> REVIEW_REQUIRED
    r = vy_harden("Sold goods to R. on credit for Rs.10,000")
    check("D.3 single-letter party refuses, zero lines",
          r.get("status") == REVIEW_REQUIRED and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")

    # misspelled party is never promoted by NORMALIZATION - the existing
    # deterministic party rules remain the only identity authority
    n = normalize_fyjc_text("Sold goods to raam for Rs.10,000 on credit")
    check("D.4 normalization never rewrites 'raam' -> 'Ram'",
          no_rewrite_of(n.provenance, "raam"), str(n.provenance))
    r = vy_harden("Sold goods to raam for Rs.10,000 on credit")
    check("D.5 misspelled party resolved by authority only",
          r.get("status") == VERIFIED, r.get("status"))
    check("D.6 party kept as typed (Raam)",
          lines(r) == [("Raam", "10000"), ("Sales", "10000")],
          str(lines(r)))


# ---------------------------------------------------------------------------
# PART E - safety invariants
# ---------------------------------------------------------------------------
def test_e_invariants():
    print("PART E - SAFETY INVARIANTS")
    # every refusal class emits zero canonical journal lines
    refusal_corpus = [
        ("Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
         "Rs.5,000 remains outstanding.", INVALID_INPUT_MATH),
        ("Sold goods worth Rs.10,000 to Ram at 10% trade discount of "
         "Rs.800", INVALID_INPUT_MATH),
        ("Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
         "18% GST with CGST Rs.1,000 and SGST Rs.1,000", INVALID_INPUT_MATH),
        ("Received Rs.11,000 from Ram in full settlement of his account of "
         "Rs.10,000", INVALID_INPUT_MATH),
        ("Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
         "Rs.4,000 remains outstanding.", REVIEW_REQUIRED),
        ("Sold goods to Ram Rs.10,000 on credit 5% xd", REVIEW_REQUIRED),
        ("Sold goods to R. on credit for Rs.10,000", REVIEW_REQUIRED),
        ("Sold goods for Rs.20,000 from Rahul on credit and Rs.18,000.",
         REVIEW_REQUIRED),
        ("Sold goods to Ram on credit", BLOCKED),
        ("Provided depreciation on machinery Rs.5,000", NOT_SUPPORTED),
    ]
    for i, (text, want) in enumerate(refusal_corpus):
        r = vy_harden(text)
        check(f"E.{i + 1}.1 '{text[:42]}...' == {want}",
              r.get("status") == want, r.get("status"))
        check(f"E.{i + 1}.2 zero journal lines",
              nlines(r) == 0, str(nlines(r)))
    check("E.11 unsafe confident = 0",
          all(vy_harden(t).get("status") != VERIFIED
              for t, _ in refusal_corpus), "")
    check("E.12 contradictory inputs never journaled",
          all(nlines(vy_harden(t)) == 0 for t, _ in refusal_corpus), "")

    # no invented discount (D4 regression): partial payment against an
    # account is NEVER a Discount Allowed / Discount Received
    r = vy_harden("Received Rs.5,000 from Ram against his account of "
                  "Rs.10,000")
    check("E.13 partial payment VERIFIED",
          r.get("status") == VERIFIED, r.get("status"))
    check("E.14 no invented discount lines",
          lines(r) == [("Cash", "5000"), ("Ram", "5000")], str(lines(r)))
    r = vy_harden("Received Rs.10,000 from Amit in full settlement of his "
                  "account of Rs.10,500")
    check("E.15 legitimate settlement discount kept",
          lines(r) == [("Amit", "10500"), ("Cash", "10000"),
                       ("Discount Allowed", "500")], str(lines(r)))

    # a stated rate is never silently ignored
    r = vy_harden("Sold goods to Ram for Rs.10,000 on credit at 5% cash "
                  "discount")
    check("E.16 unappliable cash-discount rate refuses",
          r.get("status") == REVIEW_REQUIRED and nlines(r) == 0,
          f"{r.get('status')} lines={nlines(r)}")

    # invented accounts = 0: every journal account is either on the
    # canonical chart or a party name resolved by the authority. A
    # lowercase/generic word promoted into an account (a classic
    # invented-account failure) can never pass this check.
    from backend.maths.fyjc_accounting import ACCOUNT_ROLES
    valid_corpus = [
        "Sold goods to Ram for Rs.10,000 on credit",
        "Purchased goods from Rahul for Rs.25,000 on credit",
        "Sold goods to Ram for Rs.10,000 on credit at 5% trade discount",
        "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
        "18% GST with CGST Rs.1,620 and SGST Rs.1,620",
        "Received Rs.5,000 from Ram against his account of Rs.10,000",
        "Sold goods to raam for Rs.10,000 on credit",
    ]
    for i, text in enumerate(valid_corpus):
        r = vy_harden(text)
        check(f"E.17.{i} VERIFIED", r.get("status") == VERIFIED,
              r.get("status"))
        for account, _ in lines(r):
            is_chart = account in ACCOUNT_ROLES
            is_party = (not is_chart and bool(account.strip())
                        and any(ch.isupper() for ch in account))
            check(f"E.17.{i} '{account}' canonical or party",
                  is_chart or is_party, account)
        # balance: debit-side sum == credit-side sum from the result
        debit_total = sum(float(l.get("amount") or 0)
                          for l in (r.get("debit_lines") or []))
        credit_total = sum(float(l.get("amount") or 0)
                           for l in (r.get("credit_lines") or []))
        check(f"E.18.{i} VERIFIED journal balances",
              abs(debit_total - credit_total) < 0.001,
              f"dr={debit_total} cr={credit_total}")


# ---------------------------------------------------------------------------
# PART F - flow verdict == hardened authority verdict
# ---------------------------------------------------------------------------
def test_f_flow_parity():
    print("PART F - FLOW VERDICT PARITY")
    corpus = [
        "Sold gds to Ram 10k on credit 5% td",
        "Sold goods to Ram for Rs.10,000 on credit",
        "Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
        "Rs.5,000 remains outstanding.",
        "Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
        "Rs.4,000 remains outstanding.",
        "Sold goods worth Rs.10,000 to Ram at 10% trade discount of Rs.800",
        "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
        "18% GST with CGST Rs.1,000 and SGST Rs.1,000",
        "Sold goods to R. on credit for Rs.10,000",
        "Sold goods to Ram Rs.10,000 on credit 5% xd",
        "Provided depreciation on machinery Rs.5,000",
        "Sold goods to Ram on credit",
    ]
    for i, text in enumerate(corpus):
        hardened = vy_harden(text).get("status")
        flow = run_fyjc_accounting_flow(text).get("status")
        check(f"F.{i + 1} flow == hardened ('{text[:40]}...')",
              flow == hardened, f"flow={flow} hardened={hardened}")


# ---------------------------------------------------------------------------
# PART G - determinism
# ---------------------------------------------------------------------------
def test_g_determinism():
    print("PART G - DETERMINISM")
    corpus = [
        "Sold gds to raam 10k on credit 5% td",
        "Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
        "Rs.5,000 remains outstanding.",
        "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
        "18% GST with CGST Rs.1,620 and SGST Rs.1,620",
        "Sold goods to R. on credit for Rs.10,000",
    ]
    for i, text in enumerate(corpus):
        first = json.dumps(vy_harden(text), sort_keys=True, default=str)
        second = json.dumps(vy_harden(text), sort_keys=True, default=str)
        check(f"G.{i + 1} repeated run byte-identical",
              first == second, "")


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

    # normalized valid input reaches VERIFIED normally
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold gds to Ram 10k on credit 5% td").run()
    at.button(key="fte_fyjc_go").click().run()
    check("H.3 normalized input renders without exception", not at.exception,
          [e.stack_trace for e in at.exception])
    md = " ".join(m.value for m in at.markdown)
    check("H.4 normalized input shows VERIFIED",
          "VERIFIED" in md.upper(), md[:160])
    # the page stylesheet itself contains the word 'blocked' (CSS class
    # fte-fyjc-blocked), so only the real verdict markers are asserted
    check("H.5 no misleading refusal/unsupported marker",
          "REVIEW_REQUIRED" not in md.upper()
          and "NOT SUPPORTED" not in md.upper()
          and "INVALID INPUT" not in md.upper(),
          md[:160])

    # unsafe party normalization -> REVIEW_REQUIRED, never VERIFIED
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold goods to R. on credit for Rs.10,000").run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("H.6 unsafe party refuses (no VERIFIED)",
          "VERIFIED" not in md.upper() and "REVIEW" in md.upper(), md[:160])

    # contradictory arithmetic -> INVALID_INPUT_MATH, never a journal
    at.text_area(key="fte_fyjc_question").set_value(
        "Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
        "Rs.5,000 remains outstanding.").run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("H.7 contradictory input shows INVALID INPUT (MATH)",
          "INVALID INPUT" in md.upper() or "INVALID_INPUT_MATH" in md.upper(),
          md[:160])
    check("H.8 contradictory input never shows VERIFIED",
          "VERIFIED" not in md.upper(), md[:160])

    # existing valid TD/CD/GST behavior unchanged in the UI
    at.text_area(key="fte_fyjc_question").set_value(
        "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
        "18% GST with CGST Rs.1,620 and SGST Rs.1,620").run()
    at.button(key="fte_fyjc_go").click().run()
    md = " ".join(m.value for m in at.markdown)
    check("H.9 valid CGST+SGST still VERIFIED in UI",
          "VERIFIED" in md.upper(), md[:160])


def main():
    test_a_normalization()
    test_b_numeric()
    test_c_contradictions()
    test_d_concerns()
    test_e_invariants()
    test_f_flow_parity()
    test_g_determinism()
    test_h_streamlit()
    print(f"\n15I-VY gate: {TOTAL[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
