#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-K - Deterministic GST Accounting Engine & Verified GST Coverage
scripts/fte_fyjc_15k_gst_test.py

Proves the 15I-K deterministic GST layer against the REAL production
pipeline (backend.maths.fyjc_bk_reasoning) and proves the safety
invariants across the full 15E+15F+15H corpus.

The GST domain contract (every fact must be explicitly supported):
  * CGST + SGST (intra-state) requires BOTH components named, or an
    explicit intra-state marker with a single GST rate;
  * IGST (inter-state) requires IGST named, or an explicit inter-state
    marker;
  * a bare 'GST @ r%' with no component/state evidence is REVIEW_REQUIRED;
  * no guessed rate, component, state classification, or tax account;
  * input/output side must match the underlying transaction direction;
  * inclusive/exclusive mode is extracted from explicit wording only;
  * GST on a transaction type outside the supported surface (capital,
    drawings, loans, returns, discount settlements, ...) is
    NOT_SUPPORTED (rule 8), never a guessed journal.

Sections:
  A. VERIFIED canonical GST journals - cash/credit purchases, sales,
     expenses, CGST+SGST / IGST, component amounts, inclusive mode.
  B. GST computation correctness - base / tax / total from the journal's
     'gst' evidence block (Decimal-exact).
  C. Stated component amounts - accepted when both/all present and
     consistent; contradictions are REVIEW_REQUIRED.
  D. Punctuation / formatting variants - the SAME canonical journal for
     periods, semicolons, newlines, bullets, currency symbols and
     spacing/case variants.
  E. Multi-transaction narratives with GST - each segment journals
     independently through its own GST path; nothing is absorbed.
  F. REVIEW_REQUIRED ambiguity matrix - every missing/contradictory GST
     fact refuses; ambiguity is never converted into a guess.
  G. NOT_SUPPORTED boundary - GST on unsupported transaction types.
  H. Safety invariants - over the GST matrix AND the full corpus: zero
     unbalanced VERIFIED journals, zero invented accounts, zero
     exceptions, the corpus compact-output fingerprint matches the
     15I-J baseline EXACTLY (differential testing), and no refusal ever
     carries journal lines.

Authority chain unchanged: every verdict comes from the deterministic
engine; this gate adds no accounting rules of its own.
"""

import hashlib
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths import (  # noqa: E402
    fyjc_bk_15e_benchmark as _E,
    fyjc_bk_15f_benchmark as _F,
    fyjc_bk_15h_benchmark as _H,
)
from backend.maths.fyjc_accounting import canonical_account  # noqa: E402
from backend.maths.fyjc_bk_reasoning import reason_bk_question  # noqa: E402

FAIL = []
OK = [0]


def check(name, ok, detail=""):
    if ok:
        OK[0] += 1
        print(f"OK  [{name}]")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"FAIL[{name}] {detail}")


def lines_of(result, side):
    return [(str(l.get("account")), str(l.get("amount")))
            for l in result.get(side + "_lines", [])]


def journal_of(result):
    return sorted(lines_of(result, "debit")), sorted(lines_of(result, "credit"))


def account_is_valid(account, question):
    if canonical_account(account) is not None:
        return True
    return str(account).lower() in str(question).lower()


def expect_journal(q, debit, credit, name):
    r = reason_bk_question(q)
    if r.get("status") != "VERIFIED":
        check(name, False, f"{q!r} -> {r.get('status')} "
              f"{r.get('why_not') or ''}")
        return r
    d, c = journal_of(r)
    want_d = sorted(tuple(x) for x in debit)
    want_c = sorted(tuple(x) for x in credit)
    ok = d == want_d and c == want_c
    check(name, ok, f"{q!r} got D{d} C{c} want D{want_d} C{want_c}")
    return r


def expect_status(q, status, name):
    r = reason_bk_question(q)
    check(name, r.get("status") == status,
          f"{q!r} -> {r.get('status')} want {status} "
          f"({r.get('why_not') or ''})")
    return r


def expect_gst_verified(q, name, scheme=None):
    """VERIFIED plus a journal['gst'] evidence block; base + tax == total."""
    r = reason_bk_question(q)
    if r.get("status") != "VERIFIED":
        check(name, False, f"{q!r} -> {r.get('status')} {r.get('why_not') or ''}")
        return r
    journal = r.get("journal") or {}
    gst = journal.get("gst")
    if gst is None:
        check(name, False, f"{q!r} VERIFIED but no journal['gst'] block")
        return r
    ok = True
    detail = ""
    if scheme is not None and gst.get("scheme") != scheme:
        ok = False
        detail += f" scheme={gst.get('scheme')} want {scheme}"
    base = gst.get("base")
    tax = gst.get("gst_total")
    total = gst.get("total")
    if not (isinstance(base, Decimal) and isinstance(tax, Decimal)
            and isinstance(total, Decimal)):
        ok = False
        detail += " non-Decimal gst fields"
    elif base <= 0 or tax <= 0:
        ok = False
        detail += " non-positive base/tax"
    elif (base + tax).quantize(Decimal("0.01")) != total.quantize(
            Decimal("0.01")):
        ok = False
        detail += f" base+tax={base + tax} != total={total}"
    check(name, ok, detail)
    return r


# ---------------------------------------------------------------------------
# A. VERIFIED canonical GST journals
# ---------------------------------------------------------------------------

def test_a_verified():
    # purchase on credit, CGST + SGST @ 9% each
    expect_journal(
        "Purchased goods from Ram on credit Rs.10,000, CGST @ 9% and "
        "SGST @ 9%.",
        [["Purchases", "10000"], ["Input CGST", "900.00"],
         ["Input SGST", "900.00"]],
        [["Ram", "11800.00"]],
        "A.1 credit purchase CGST+SGST")
    # purchase for cash, single total GST rate + intra-state marker
    expect_journal(
        "Purchased goods for cash Rs.10,000 plus GST @ 18%, intra-state.",
        [["Purchases", "10000"], ["Input CGST", "900.00"],
         ["Input SGST", "900.00"]],
        [["Cash", "11800.00"]],
        "A.2 cash purchase total rate + intra-state")
    # credit sale, IGST @ 18%
    expect_journal(
        "Sold goods to Mohan on credit Rs.20,000, IGST @ 18%.",
        [["Mohan", "23600.00"]],
        [["Sales", "20000"], ["Output IGST", "3600.00"]],
        "A.3 credit sale IGST")
    # cash sale, CGST + SGST
    expect_journal(
        "Sold goods for cash Rs.10,000, CGST @ 9% and SGST @ 9%.",
        [["Cash", "11800.00"]],
        [["Sales", "10000"], ["Output CGST", "900.00"],
         ["Output SGST", "900.00"]],
        "A.4 cash sale CGST+SGST")
    # cash purchase, single total rate + inter-state marker -> IGST
    expect_journal(
        "Purchased goods for cash Rs.10,000, GST @ 18% inter-state.",
        [["Purchases", "10000"], ["Input IGST", "1800.00"]],
        [["Cash", "11800.00"]],
        "A.5 cash purchase inter-state -> IGST")
    # expense + GST (rent)
    expect_journal(
        "Paid rent Rs.5,000, CGST @ 9% and SGST @ 9%.",
        [["Rent", "5000"], ["Input CGST", "450.00"],
         ["Input SGST", "450.00"]],
        [["Cash", "5900.00"]],
        "A.6 expense CGST+SGST")
    # stated component amounts (no rate) - used as-is
    expect_journal(
        "Purchased goods from Rahul on credit Rs.10,000, CGST Rs.900 and "
        "SGST Rs.900.",
        [["Purchases", "10000"], ["Input CGST", "900"],
         ["Input SGST", "900"]],
        [["Rahul", "11800"]],
        "A.7 credit purchase stated component amounts")
    # cash sale, IGST
    expect_journal(
        "Sold goods to Mohan for cash Rs.10,000, IGST @ 18%.",
        [["Cash", "11800.00"]],
        [["Sales", "10000"], ["Output IGST", "1800.00"]],
        "A.8 cash sale IGST")
    # stated IGST amount
    expect_journal(
        "Purchased goods for cash Rs.10,000, IGST Rs.1,800.",
        [["Purchases", "10000"], ["Input IGST", "1800"]],
        [["Cash", "11800"]],
        "A.9 cash purchase stated IGST amount")
    # inclusive of GST @ 18% -> base extracted deterministically
    expect_journal(
        "Purchased goods for cash Rs.11,800 inclusive of GST @ 18%, "
        "CGST and SGST.",
        [["Purchases", "10000.00"], ["Input CGST", "900.00"],
         ["Input SGST", "900.00"]],
        [["Cash", "11800.00"]],
        "A.10 inclusive amount base extraction")
    # inclusive with stated component amounts
    expect_journal(
        "Purchased goods for cash Rs.11,800 inclusive of GST, "
        "CGST Rs.900 and SGST Rs.900.",
        [["Purchases", "10000"], ["Input CGST", "900"],
         ["Input SGST", "900"]],
        [["Cash", "11800"]],
        "A.11 inclusive amount with stated components")
    # a second expense family (electricity) - same GST treatment
    expect_journal(
        "Paid electricity Rs.5,000, CGST @ 9% and SGST @ 9%.",
        [["Electricity", "5000"], ["Input CGST", "450.00"],
         ["Input SGST", "450.00"]],
        [["Cash", "5900.00"]],
        "A.12 electricity expense CGST+SGST")


# ---------------------------------------------------------------------------
# B. GST computation correctness (journal['gst'] block)
# ---------------------------------------------------------------------------

def test_b_computation():
    r = expect_gst_verified(
        "Purchased goods for cash Rs.11,800 inclusive of GST @ 18%, "
        "CGST and SGST.",
        "B.1 inclusive 11800 @ 18%", scheme="CGST_SGST")
    gst = (r.get("journal") or {}).get("gst") or {}
    if gst:
        check("B.1a base == 10000", gst.get("base") == Decimal("10000.00"),
              str(gst.get("base")))
        check("B.1b tax == 1800", gst.get("gst_total") == Decimal("1800.00"),
              str(gst.get("gst_total")))
        check("B.1c rate == 18", gst.get("rate") == Decimal("18"),
              str(gst.get("rate")))
        check("B.1d mode inclusive", gst.get("mode") == "inclusive",
              str(gst.get("mode")))
    r = expect_gst_verified(
        "Purchased goods for cash Rs.5,900 inclusive of GST @ 18%, "
        "CGST and SGST.",
        "B.2 inclusive 5900 @ 18%", scheme="CGST_SGST")
    gst = (r.get("journal") or {}).get("gst") or {}
    if gst:
        check("B.2a base == 5000", gst.get("base") == Decimal("5000.00"),
              str(gst.get("base")))
    r = expect_gst_verified(
        "Sold goods for cash Rs.23,600 inclusive of GST @ 18%, IGST.",
        "B.3 inclusive sale IGST", scheme="IGST")
    gst = (r.get("journal") or {}).get("gst") or {}
    if gst:
        check("B.3a base == 20000", gst.get("base") == Decimal("20000.00"),
              str(gst.get("base")))
        check("B.3b tax == 3600", gst.get("gst_total") == Decimal("3600.00"),
              str(gst.get("gst_total")))
    r = expect_gst_verified(
        "Purchased goods for cash Rs.10,000, IGST @ 12%.",
        "B.4 exclusive IGST 12%", scheme="IGST")
    gst = (r.get("journal") or {}).get("gst") or {}
    if gst:
        check("B.4a tax == 1200", gst.get("gst_total") == Decimal("1200.00"),
              str(gst.get("gst_total")))
        check("B.4b total == 11200",
              gst.get("total") == Decimal("11200.00"),
              str(gst.get("total")))
        check("B.4c mode exclusive", gst.get("mode") == "exclusive",
              str(gst.get("mode")))
    r = expect_gst_verified(
        "Purchased goods for cash Rs.10,000, CGST @ 2.5% and SGST @ 2.5%.",
        "B.5 CGST+SGST 2.5% each", scheme="CGST_SGST")
    gst = (r.get("journal") or {}).get("gst") or {}
    if gst:
        check("B.5a tax == 500", gst.get("gst_total") == Decimal("500.00"),
              str(gst.get("gst_total")))


# ---------------------------------------------------------------------------
# C. Stated component amounts - consistency checks
# ---------------------------------------------------------------------------

def test_c_component_amounts():
    # both amounts stated, no rate -> used as-is
    r = expect_gst_verified(
        "Purchased goods from Ram on credit Rs.10,000, CGST Rs.900 and "
        "SGST Rs.900.",
        "C.1 both component amounts as-is", scheme="CGST_SGST")
    gst = (r.get("journal") or {}).get("gst") or {}
    if gst:
        check("C.1a components CGST/SGST 900",
              gst.get("components") == [("CGST", Decimal("900")),
                                        ("SGST", Decimal("900"))],
              str(gst.get("components")))
    # output IGST amount on a sale
    expect_journal(
        "Sold goods to Mohan for cash Rs.10,000, Output IGST Rs.1,800.",
        [["Cash", "11800"]],
        [["Sales", "10000"], ["Output IGST", "1800"]],
        "C.2 output IGST amount on sale")
    # stated IGST amount contradicts the stated rate -> REVIEW_REQUIRED
    expect_status(
        "Purchased goods from Ram on credit Rs.10,000, IGST @ 18%, "
        "IGST Rs.1,000.",
        "REVIEW_REQUIRED", "C.3 stated IGST amount contradicts rate")
    # CGST amount without SGST amount -> REVIEW_REQUIRED
    expect_status(
        "Purchased goods from Ram on credit Rs.10,000, CGST Rs.900.",
        "REVIEW_REQUIRED", "C.4 CGST amount without SGST amount")


# ---------------------------------------------------------------------------
# D. Punctuation / formatting variants (same canonical journal as A.1)
# ---------------------------------------------------------------------------

def test_d_variants():
    ref_d = ([["Purchases", "10000"], ["Input CGST", "900.00"],
              ["Input SGST", "900.00"]],
             [["Ram", "11800.00"]])
    variants = [
        "Purchased goods from Ram on credit Rs.10,000. CGST @ 9% and "
        "SGST @ 9%.",
        "Purchased goods from Ram on credit Rs.10,000; CGST @ 9% and "
        "SGST @ 9%.",
        "Purchased goods from Ram on credit Rs.10,000\nCGST @ 9% and "
        "SGST @ 9%.",
        "Purchased goods from Ram on credit Rs.10,000\n• CGST @ 9% and "
        "SGST @ 9%.",
        "Purchased goods from Ram on credit ₹10,000, CGST @ 9% and "
        "SGST @ 9%.",
        "Purchased goods from Ram on credit Rs 10,000, CGST @ 9% and "
        "SGST @ 9%.",
        "Purchased goods from Ram on credit Rs.10000, CGST @ 9% and "
        "SGST @ 9%.",
        "Purchased goods from Ram on credit Rs.10,000, GST at 18%, "
        "intra-state.",
        "Purchased goods from Ram on credit Rs.10,000, GST 18%, "
        "intra-state.",
        "Purchased goods from Ram on credit Rs.10,000, cgst @ 9% and "
        "sgst @ 9%.",
        "Purchased goods from Ram on credit Rs.10,000 , CGST @ 9% and "
        "SGST @ 9%.",
    ]
    for i, v in enumerate(variants, 1):
        expect_journal(v, *ref_d, f"D.{i} {v[:44]}")
    # GST fragment rejoined from the NEXT sentence, not journaled alone
    expect_journal(
        "Purchased goods from Ram on credit Rs.10,000. CGST @ 9% and "
        "SGST @ 9%.",
        *ref_d, "D.13 fragment rejoin (period)")
    expect_journal(
        "Purchased goods from Ram on credit Rs.10,000\n• CGST @ 9% and "
        "SGST @ 9%.",
        *ref_d, "D.14 fragment rejoin (bullet)")


# ---------------------------------------------------------------------------
# E. Multi-transaction narratives with GST
# ---------------------------------------------------------------------------

def test_e_multi():
    # GST purchase + GST sale - each journals through its own GST path
    r = reason_bk_question(
        "Purchased goods from Ram on credit Rs.10,000, CGST @ 9% and "
        "SGST @ 9%. Sold goods to Mohan for cash Rs.20,000, IGST @ 18%.")
    if r.get("status") == "VERIFIED":
        journals = r.get("journals") or []
        d, c = journal_of(r)
        want_d = sorted(tuple(x) for x in (("Purchases", "10000"),
                                           ("Input CGST", "900.00"),
                                           ("Input SGST", "900.00"),
                                           ("Cash", "23600.00")))
        want_c = sorted(tuple(x) for x in (("Ram", "11800.00"),
                                           ("Sales", "20000"),
                                           ("Output IGST", "3600.00")))
        check("E.1 two GST journals",
              len(journals) == 2 and d == want_d and c == want_c,
              f"D{d} C{c}")
        check("E.1a schemes", len(journals) == 2
              and journals[0].get("gst", {}).get("scheme") == "CGST_SGST"
              and journals[1].get("gst", {}).get("scheme") == "IGST",
              str([j.get("gst", {}).get("scheme") for j in journals]))
        check("E.1b balanced",
              r.get("journal", {}).get("balanced") is True,
              str(r.get("journal", {}).get("total_debit")) + " vs "
              + str(r.get("journal", {}).get("total_credit")))
    else:
        check("E.1 two GST journals", False, r.get("status"))
    # semicolon-separated GST purchase + GST purchase
    r = reason_bk_question(
        "Purchased goods from Ram on credit Rs.10,000, CGST @ 9% and "
        "SGST @ 9%; Purchased goods for cash Rs.20,000, IGST @ 18%.")
    if r.get("status") == "VERIFIED":
        check("E.2 semicolon two GST journals",
              len(r.get("journals") or []) == 2,
              str(len(r.get("journals") or [])))
    else:
        check("E.2 semicolon two GST journals", False, r.get("status"))
    # plain purchase + GST sale (sentence boundary after a plain amount)
    r = reason_bk_question(
        "Purchased goods from Ram on credit Rs.10,000. Sold goods to "
        "Mohan for cash Rs.20,000, IGST @ 18%.")
    if r.get("status") == "VERIFIED":
        journals = r.get("journals") or []
        check("E.3 plain + GST sale",
              len(journals) == 2
              and journals[0].get("debit_lines", [{}])[0].get("account")
              == "Purchases"
              and journals[1].get("credit_lines", [{}])[0].get("account")
              == "Sales",
              str([(j.get("debit_lines") or [{}])[0].get("account")
                   for j in journals]))
    else:
        check("E.3 plain + GST sale", False, r.get("status"))
    # GST expense + plain purchase
    r = reason_bk_question(
        "Paid rent Rs.5,000, CGST @ 9% and SGST @ 9%. Purchased goods "
        "for cash Rs.10,000.")
    if r.get("status") == "VERIFIED":
        check("E.4 GST expense + plain purchase",
              len(r.get("journals") or []) == 2,
              str(len(r.get("journals") or [])))
    else:
        check("E.4 GST expense + plain purchase", False, r.get("status"))


# ---------------------------------------------------------------------------
# F. REVIEW_REQUIRED ambiguity matrix
# ---------------------------------------------------------------------------

def test_f_review_required():
    cases = [
        # bare total rate, no components/state -> which scheme?
        "Purchased goods from Ram on credit Rs.10,000, GST @ 18%.",
        # GST with no rate at all
        "Purchased goods from Ram on credit Rs.10,000, GST.",
        # CGST without SGST (never invent the missing component)
        "Purchased goods from Ram on credit Rs.10,000, CGST @ 9%.",
        # SGST without CGST
        "Purchased goods from Ram on credit Rs.10,000, SGST @ 9%.",
        # IGST + CGST mixed schemes
        "Purchased goods from Ram on credit Rs.10,000, IGST @ 18% and "
        "CGST @ 9%.",
        # inclusive without rate or stated GST amount
        "Purchased goods from Ram on credit Rs.11,800 inclusive of "
        "GST @ 18%.",
        # unequal CGST/SGST rates
        "Purchased goods from Ram on credit Rs.10,000, CGST @ 9% and "
        "SGST @ 5%.",
        # output tax named on a purchase (wrong side)
        "Purchased goods from Ram on credit Rs.10,000, Output CGST @ 9% "
        "and Output SGST @ 9%.",
        # input tax named on a sale (wrong side)
        "Sold goods to Mohan on credit Rs.10,000, Input IGST @ 18%.",
        # GST combined with a discount (15I-L scope)
        "Purchased goods from Ram on credit Rs.10,000 plus GST @ 18%, "
        "discount allowed Rs.200.",
        # contradictory total rates
        "Purchased goods from Ram on credit Rs.10,000, GST @ 18% and "
        "GST @ 12%, intra-state.",
        # credit purchase without party/mode resolution
        "Purchased goods Rs.10,000, CGST @ 9% and SGST @ 9%.",
        # both inclusive AND exclusive markers
        "Purchased goods from Ram on credit Rs.10,000 inclusive of GST "
        "and GST extra, CGST and SGST.",
        # both intra-state AND inter-state markers
        "Purchased goods from Ram on credit Rs.10,000, GST @ 18% "
        "intra-state and inter-state.",
        # CGST+SGST contradicted by an inter-state marker
        "Purchased goods from Ram on credit Rs.10,000, CGST @ 9% and "
        "SGST @ 9%, inter-state.",
        # IGST contradicted by an intra-state marker
        "Purchased goods from Ram on credit Rs.10,000, IGST @ 18%, "
        "intra-state.",
        # stated IGST amount contradicts the stated rate
        "Purchased goods from Ram on credit Rs.10,000, IGST @ 18%, "
        "IGST Rs.1,000.",
        # only one component amount stated
        "Purchased goods from Ram on credit Rs.10,000, CGST Rs.900.",
        # impossible rate
        "Purchased goods from Ram on credit Rs.10,000, GST @ 150%, "
        "intra-state.",
        # zero rate is not a supported GST rate
        "Purchased goods from Ram on credit Rs.10,000, GST @ 0%, "
        "intra-state.",
    ]
    for i, q in enumerate(cases, 1):
        expect_status(q, "REVIEW_REQUIRED", f"F.{i} {q[:48]}")


# ---------------------------------------------------------------------------
# G. NOT_SUPPORTED boundary - GST on unsupported transaction types
# ---------------------------------------------------------------------------

def test_g_not_supported():
    cases = [
        "Started business with cash Rs.50,000, GST @ 18% intra-state.",
        "Withdrew cash Rs.5,000 for personal use, GST @ 18%.",
        "Deposited cash into bank Rs.10,000, GST @ 18%.",
        "Took a loan from bank Rs.20,000, IGST @ 18%.",
        "Returned goods to Ram Rs.1,000, GST @ 18%.",
        "Received commission Rs.1,000, GST @ 18%.",
        "Received from Ram Rs.5,000, GST @ 18%.",
    ]
    for i, q in enumerate(cases, 1):
        expect_status(q, "NOT_SUPPORTED", f"G.{i} {q[:48]}")


# ---------------------------------------------------------------------------
# H. Safety invariants over the full matrix + corpus + fingerprint
# ---------------------------------------------------------------------------

# The 15I-J baseline fingerprint - proving the non-GST corpus is
# byte-identical through the entire 15I-K layer (differential testing).
CORPUS_FINGERPRINT = ("36ee762a2d2a03a4273d40ee8921082289fad4cae3858226"
                      "fd0d619232f7bc25")


def corpus_cases():
    out = []
    for mod in (_E, _F, _H):
        for name in dir(mod):
            v = getattr(mod, name)
            if isinstance(v, list) and v and isinstance(v[0], dict) \
                    and "question" in v[0]:
                out.extend(str(c.get("question") or "").strip()
                           for c in v)
    return [q for q in dict.fromkeys(out) if q]


def compact(q):
    r = reason_bk_question(q)
    return {
        "status": r.get("status"),
        "debit": tuple(sorted(
            (str(l.get("account")), str(l.get("amount")))
            for l in (r.get("debit_lines") or []))),
        "credit": tuple(sorted(
            (str(l.get("account")), str(l.get("amount")))
            for l in (r.get("credit_lines") or []))),
    }


GST_MATRIX = [
    "Purchased goods from Ram on credit Rs.10,000, CGST @ 9% and "
    "SGST @ 9%.",
    "Purchased goods for cash Rs.10,000 plus GST @ 18%, intra-state.",
    "Sold goods to Mohan on credit Rs.20,000, IGST @ 18%.",
    "Sold goods for cash Rs.10,000, CGST @ 9% and SGST @ 9%.",
    "Purchased goods for cash Rs.10,000, GST @ 18% inter-state.",
    "Paid rent Rs.5,000, CGST @ 9% and SGST @ 9%.",
    "Purchased goods from Rahul on credit Rs.10,000, CGST Rs.900 and "
    "SGST Rs.900.",
    "Sold goods to Mohan for cash Rs.10,000, IGST @ 18%.",
    "Purchased goods for cash Rs.11,800 inclusive of GST @ 18%, "
    "CGST and SGST.",
    "Purchased goods from Ram on credit Rs.10,000, GST @ 18%.",
    "Purchased goods from Ram on credit Rs.10,000, GST.",
    "Purchased goods from Ram on credit Rs.10,000, CGST @ 9%.",
    "Purchased goods from Ram on credit Rs.10,000, IGST @ 18% and "
    "CGST @ 9%.",
    "Purchased goods from Ram on credit Rs.10,000, CGST @ 9% and "
    "SGST @ 5%.",
    "Purchased goods from Ram on credit Rs.10,000 plus GST @ 18%, "
    "discount allowed Rs.200.",
    "Started business with cash Rs.50,000, GST @ 18% intra-state.",
    "Purchased goods from Ram on credit Rs.10,000, CGST @ 9% and "
    "SGST @ 9%. Sold goods to Mohan for cash Rs.20,000, IGST @ 18%.",
]


def test_h_invariants():
    # -- corpus differential (unchanged from the 15I-J baseline) ---------
    questions = corpus_cases()
    blob = json.dumps(
        {q: compact(q) for q in sorted(questions)},
        sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    check("H.1 corpus differential unchanged", digest == CORPUS_FINGERPRINT,
          f"got {digest} want {CORPUS_FINGERPRINT}")
    check("H.2 corpus size stable", len(questions) == 325,
          str(len(questions)))
    # -- invariants over the corpus --------------------------------------
    bad = []
    unbalanced = 0
    invented = 0
    for q in questions:
        try:
            r = reason_bk_question(q)
        except Exception as exc:  # noqa: BLE001
            bad.append((q, f"EXC {exc}"))
            continue
        if r.get("status") != "VERIFIED":
            continue
        debit = lines_of(r, "debit")
        credit = lines_of(r, "credit")
        td = sum(float(a) for _, a in debit)
        tc = sum(float(a) for _, a in credit)
        if abs(td - tc) > 0.01:
            unbalanced += 1
            bad.append((q, f"unbalanced {td} vs {tc}"))
        for acc, _ in debit + credit:
            if not account_is_valid(acc, q):
                invented += 1
                bad.append((q, f"invented account {acc!r}"))
    check("H.3 zero unbalanced VERIFIED journals over corpus",
          unbalanced == 0, str(unbalanced))
    check("H.4 zero invented accounts over corpus", invented == 0,
          str(invented))
    check("H.5 zero exceptions over corpus", len(bad) == 0,
          str(bad[:3]))
    # -- invariants over the GST matrix -----------------------------------
    mat_bad = []
    mat_unbalanced = 0
    mat_invented = 0
    refusals_with_lines = 0
    for q in GST_MATRIX:
        try:
            r = reason_bk_question(q)
        except Exception as exc:  # noqa: BLE001
            mat_bad.append((q, f"EXC {exc}"))
            continue
        status = r.get("status")
        if status == "VERIFIED":
            debit = lines_of(r, "debit")
            credit = lines_of(r, "credit")
            td = sum(float(a) for _, a in debit)
            tc = sum(float(a) for _, a in credit)
            if abs(td - tc) > 0.01:
                mat_unbalanced += 1
                mat_bad.append((q, f"unbalanced {td} vs {tc}"))
            for acc, _ in debit + credit:
                if not account_is_valid(acc, q):
                    mat_invented += 1
                    mat_bad.append((q, f"invented account {acc!r}"))
        else:
            # a refusal must NEVER carry journal lines (no guess)
            if (r.get("debit_lines") or r.get("credit_lines")):
                refusals_with_lines += 1
                mat_bad.append((q, "refusal carries journal lines"))
    check("H.6 zero unbalanced VERIFIED journals over GST matrix",
          mat_unbalanced == 0, str(mat_unbalanced))
    check("H.7 zero invented accounts over GST matrix",
          mat_invented == 0, str(mat_invented))
    check("H.8 zero exceptions over GST matrix", len(mat_bad) == 0,
          str(mat_bad[:3]))
    check("H.9 no refusal ever carries journal lines",
          refusals_with_lines == 0, str(refusals_with_lines))


def main():
    test_a_verified()
    test_b_computation()
    test_c_component_amounts()
    test_d_variants()
    test_e_multi()
    test_f_review_required()
    test_g_not_supported()
    test_h_invariants()
    print(f"\n15I-K gate: {OK[0]} checks passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
