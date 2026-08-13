#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-L - Deterministic Trade Discount & Cash Discount Engine
scripts/fte_fyjc_15l_test.py

Proves the 15I-L TD/CD layer against the REAL production pipeline
(backend.maths.fyjc_bk_reasoning) and the content/practice layers
(15I-G compiler metadata + 15I-H practice verification), and proves the
safety invariants across the full 15E+15F+15H corpus.

The TD/CD domain contract (every fact must be explicitly supported):
  * Trade Discount reduces the LIST price; the journal posts the NET
    amount and NEVER a separate Trade Discount account;
  * 'less 10%', 'less 10 percent', 'less Rs.2,000 trade discount',
    'trade discount of Rs.2,000', 'at 10% discount' and the 'TD'
    abbreviation are all deterministic trade-discount evidence;
  * Cash Discount is a SETTLEMENT concept: allowed on receipts from
    debtors (Cash Dr + Discount Allowed Dr / Party Cr), received on
    payments to creditors (Party Dr / Cash Cr + Discount Received Cr);
  * an explicit discount AMOUNT is anchored to the word 'discount'
    ('discount allowed Rs.200', 'allowed Rs.200 cash discount') - a
    plain receipt figure ('Received Rs.9,500 from him') is never
    misread as discount metadata;
  * a stated cash/paid figure plus a settlement-side RATE ('after
    allowing 2% cash discount') treats the stated figure as the NET
    amount - the rate applies to the amount due, never to the stated
    figure (no silently invented cash amounts); 'on the amount paid'
    anchors the rate to the paid figure itself;
  * a rate with no determinable amount due refuses (REVIEW_REQUIRED);
  * a discount is NEVER a standalone journal entry;
  * an impossible trade discount (>= list price) refuses;
  * TD + GST nets the taxable value; a settlement-side discount with
    GST stays REVIEW_REQUIRED (15I-K boundary);
  * existing VERIFIED behavior stays byte-identical (corpus
    differential unchanged) and the 15I-H CASH_DISCOUNT_ERROR /
    TRADE_DISCOUNT_ERROR mistake taxonomy fires deterministically.

Authority chain unchanged: every verdict comes from the deterministic
engine; this gate adds no accounting rules of its own.
"""

import hashlib
import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths import (  # noqa: E402
    fyjc_bk_15e_benchmark as _E,
    fyjc_bk_15f_benchmark as _F,
    fyjc_bk_15h_benchmark as _H,
)
from backend.maths.fyjc_accounting import canonical_account  # noqa: E402
from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    discount_evidence,
    reason_bk_question,
)
from backend.maths.fyjc_content_compiler import default_metadata  # noqa: E402
from backend.maths.fyjc_practice_engine import (  # noqa: E402
    OUTCOME_CORRECT,
    OUTCOME_INCORRECT,
    PracticeEngine,
)
from backend.maths.fyjc_question_bank import QuestionBank  # noqa: E402

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


# ---------------------------------------------------------------------------
# A. Trade discount - net journals, never a separate Trade Discount account
# ---------------------------------------------------------------------------

def test_a_trade_discount():
    expect_journal(
        "Purchased goods listed at Rs.20,000 less 10% trade discount "
        "for cash.",
        [["Purchases", "18000.00"]],
        [["Cash", "18000.00"]],
        "A.1 cash purchase less 10% TD")
    expect_journal(
        "Purchased goods from Ram for cash Rs.20,000 less 10 percent "
        "trade discount.",
        [["Purchases", "18000.00"]],
        [["Cash", "18000.00"]],
        "A.2 word-percent TD")
    expect_journal(
        "Purchased goods from Ram on credit Rs.20,000 less 10% TD.",
        [["Purchases", "18000.00"]],
        [["Ram", "18000.00"]],
        "A.3 TD abbreviation on credit")
    expect_journal(
        "Purchased goods from Ram for cash Rs.20,000 less Rs.2,000 "
        "trade discount.",
        [["Purchases", "18000"]],
        [["Cash", "18000"]],
        "A.4 explicit TD amount ('less Rs.2,000')")
    expect_journal(
        "Purchased goods from Ram for cash Rs.20,000, trade discount of "
        "Rs.2,000.",
        [["Purchases", "18000"]],
        [["Cash", "18000"]],
        "A.5 explicit TD amount ('discount of Rs.2,000')")
    expect_journal(
        "Sold goods to Ram on credit Rs.30,000 at 10% trade discount.",
        [["Ram", "27000.00"]],
        [["Sales", "27000.00"]],
        "A.6 credit sale at 10% TD -> net 27,000")
    expect_journal(
        "Sold goods to Ram for Rs.10,000 at 10% discount on credit.",
        [["Ram", "9000.00"]],
        [["Sales", "9000.00"]],
        "A.7 'at 10% discount' on a credit sale -> TD net 9,000")
    # a no-party credit sale refuses (never a guessed party) - the net
    # math is still deterministic inside resolve_transaction_amounts
    expect_status(
        "Sold goods worth Rs.30,000 at 10% trade discount on credit.",
        "REVIEW_REQUIRED", "A.8 no-party credit TD sale refuses")
    # no Trade Discount account is ever posted
    r = reason_bk_question(
        "Purchased goods listed at Rs.20,000 less 10% trade discount "
        "for cash.")
    all_accounts = [l.get("account") for l in r.get("debit_lines") or []
                    ] + [l.get("account") for l in r.get("credit_lines") or []]
    check("A.9 TD never posts a separate discount account",
          all(a.lower() != "trade discount" for a in all_accounts),
          str(all_accounts))


# ---------------------------------------------------------------------------
# B. Cash discount at settlement - explicit AMOUNT forms
# ---------------------------------------------------------------------------

def test_b_cash_discount_amount():
    # the spec's section-4 example 1: sale on credit + settlement with
    # discount -> the sale entry and the settlement entry (Cash +
    # Discount Allowed / Ram) both journal; the aggregate nets Ram.
    expect_journal(
        "Sold goods to Ram for Rs.10,000 on credit. Received Rs.9,800 "
        "from Ram and allowed Rs.200 cash discount.",
        [["Ram", "10000"], ["Cash", "9800"], ["Discount Allowed", "200"]],
        [["Sales", "10000"], ["Ram", "10000"]],
        "B.1 sale + settlement with discount allowed")
    # the spec's section-4 example 2: purchase + payment with discount ->
    # the COMPOUND journal, never a settlement-only entry.
    expect_journal(
        "Purchased goods from Rahul for Rs.10,000 on credit. Paid "
        "Rs.9,800 and received Rs.200 cash discount.",
        [["Purchases", "10000"]],
        [["Cash", "9800"], ["Discount Received", "200"]],
        "B.2 purchase + settlement with discount received")
    # standalone settlement forms (15F-era, preserved)
    expect_journal(
        "Received from Mohan Rs.9,800, discount allowed Rs.200.",
        [["Cash", "9800"], ["Discount Allowed", "200"]],
        [["Mohan", "10000"]],
        "B.3 receipt, discount allowed")
    expect_journal(
        "Paid to Amit Rs.9,800, discount received Rs.200.",
        [["Amit", "10000"]],
        [["Cash", "9800"], ["Discount Received", "200"]],
        "B.4 payment, discount received")
    # amount BEFORE the 'cash discount' noun (Sprint 15I-L)
    expect_journal(
        "Received from Mohan Rs.9,800, allowed Rs.200 cash discount.",
        [["Cash", "9800"], ["Discount Allowed", "200"]],
        [["Mohan", "10000"]],
        "B.5 before-noun allowed form")
    expect_journal(
        "Paid to Amit Rs.9,800, received Rs.200 cash discount.",
        [["Amit", "10000"]],
        [["Cash", "9800"], ["Discount Received", "200"]],
        "B.6 before-noun received form")
    # derived by subtraction when both figures are stated, no discount
    # word (deterministic arithmetic, never invented)
    expect_journal(
        "Received from Mohan Rs.5,000 in full settlement of his account "
        "of Rs.5,200.",
        [["Cash", "5000"], ["Discount Allowed", "200"]],
        [["Mohan", "5200"]],
        "B.7 in-full-settlement derived discount")
    # the receipt figure is NEVER relabelled as a discount amount
    r = reason_bk_question(
        "Received from Ram Rs.9,800, discount allowed Rs.200.")
    check("B.8 receipt amount never read as discount metadata",
          discount_evidence(
              "Received from Ram Rs.9,800, discount allowed Rs.200.")
          .get("discount_amount") == 200,
          str(discount_evidence(
              "Received from Ram Rs.9,800, discount allowed Rs.200.")))


# ---------------------------------------------------------------------------
# C. Rate-based cash discount
# ---------------------------------------------------------------------------

def test_c_cash_discount_rate():
    # fraction-derived payment: the rate applies to the derived payment
    # ('half paid immediately with 2% cash discount') - canonical 15B math
    expect_journal(
        "Purchased goods from Rahul for Rs.10,000 at 10% trade discount, "
        "half paid immediately with 2% cash discount.",
        [["Purchases", "9000.00"]],
        [["Cash", "4410.00"], ["Discount Received", "90.00"],
            ["Rahul", "4500.00"]],
        "C.1 TD + fraction-paid 2% CD")
    # 'on the amount paid' anchors the rate to the PAID figure itself
    expect_journal(
        "Purchased goods from Rahul for Rs.10,000, paid him Rs.3,000 "
        "immediately and 2% cash discount on the paid amount.",
        [["Purchases", "10000"]],
        [["Cash", "2940.00"], ["Discount Received", "60.00"],
            ["Rahul", "7000"]],
        "C.2 'on the amount paid' -> 2% of 3,000")
    # settlement-side rate after a sale: the stated receipt is the NET -
    # the rate applies to the amount due (folded, never inventing cash)
    expect_journal(
        "Sold goods to Ram for Rs.10,000 on credit. Received Rs.9,800 "
        "from Ram, after allowing 2 percent cash discount.",
        [["Cash", "9800"], ["Discount Allowed", "200.00"]],
        [["Sales", "10000"]],
        "C.3 sale settlement after allowing 2 percent")
    # same with a '%' symbol
    expect_journal(
        "Sold goods to Ram for Rs.10,000 on credit. Received Rs.9,800 "
        "from Ram, after allowing 2% cash discount.",
        [["Cash", "9800"], ["Discount Allowed", "200.00"]],
        [["Sales", "10000"]],
        "C.4 sale settlement after allowing 2%")
    # settlement-side rate after a purchase
    expect_journal(
        "Purchased goods from Rahul for Rs.10,000 on credit. Paid "
        "Rs.9,800, after receiving 2 percent cash discount.",
        [["Purchases", "10000"]],
        [["Cash", "9800"], ["Discount Received", "200.00"]],
        "C.5 purchase settlement after receiving 2 percent")
    # a stated full-cash value + CD rate: the rate reduces the value
    expect_journal(
        "Sold goods to Ram for cash Rs.10,000, allowed 2% cash discount.",
        [["Cash", "9800.00"], ["Discount Allowed", "200.00"]],
        [["Sales", "10000"]],
        "C.6 cash sale allowed 2% CD")
    # standalone rate with NO amount due is ambiguous -> refuse
    expect_status(
        "Received Rs.9,800 from Ram, after allowing 2 percent cash "
        "discount.",
        "REVIEW_REQUIRED", "C.7 standalone receipt rate refuses")
    expect_status(
        "Paid to Amit Rs.9,800, after receiving 2 percent cash discount.",
        "REVIEW_REQUIRED", "C.8 standalone payment rate refuses")
    # a cash-discount rate is never a trade discount (list-price) rate
    r = reason_bk_question(
        "Sold goods to Ram for cash Rs.10,000, allowed 2% cash discount.")
    check("C.9 CD rate never nets the list price",
          (r.get("journal") or {}).get("total_debit") == 10000,
          str((r.get("journal") or {}).get("total_debit")))


# ---------------------------------------------------------------------------
# D. GST + trade discount (15I-K boundary)
# ---------------------------------------------------------------------------

def test_d_gst_td():
    expect_journal(
        "Sold goods to Ram on credit Rs.20,000 less 10% trade discount, "
        "IGST @ 18%.",
        [["Ram", "21240.00"]],
        [["Sales", "18000.00"], ["Output IGST", "3240.00"]],
        "D.1 GST sale nets the TD before tax")
    expect_journal(
        "Purchased goods from Ram on credit Rs.20,000 less 10% trade "
        "discount, CGST @ 9% and SGST @ 9%.",
        [["Purchases", "18000.00"], ["Input CGST", "1620.00"],
         ["Input SGST", "1620.00"]],
        [["Ram", "21240.00"]],
        "D.2 GST purchase nets the TD before tax")
    # a settlement-side cash discount with GST stays out of scope
    expect_status(
        "Purchased goods from Ram on credit Rs.10,000 plus GST @ 18%, "
        "discount allowed Rs.200.",
        "REVIEW_REQUIRED", "D.3 GST + settlement discount refuses")


# ---------------------------------------------------------------------------
# E. Punctuation / formatting variants (same canonical journal as A.1)
# ---------------------------------------------------------------------------

def test_e_variants():
    ref_d = [["Purchases", "18000.00"]]
    ref_c = [["Cash", "18000.00"]]
    variants = [
        "Purchased goods listed at Rs.20,000 less 10% trade discount "
        "for cash.",
        "Purchased goods listed at Rs.20,000, less 10% trade discount, "
        "for cash.",
        "Purchased goods listed at \u20b920,000 less 10 percent trade "
        "discount for cash.",
        "Purchased goods listed at Rs 20,000 less 10 per cent trade "
        "discount for cash.",
        "Purchased goods listed at Rs.20,000 less 10 per-cent trade "
        "discount for cash.",
        "Purchased goods listed at Rs.20,000 less 10% trade discount, "
        "for cash.",
        "Purchased goods listed at Rs.20,000 less 10% TD for cash.",
        "Purchased goods listed at Rs.20,000 less 10 % trade discount "
        "for cash.",
    ]
    for i, v in enumerate(variants, 1):
        expect_journal(v, ref_d, ref_c, f"E.{i} {v[:44]}")
    # a newline is a P0-A transaction boundary - the 'less 10% trade
    # discount for cash' fragment cannot journal on its own and the
    # question refuses instead of silently absorbing it
    expect_status(
        "Purchased goods listed at Rs.20,000\nless 10% trade discount "
        "for cash.",
        "REVIEW_REQUIRED", "E.9 newline-split TD fragment refuses")


# ---------------------------------------------------------------------------
# F. REVIEW_REQUIRED refusal matrix
# ---------------------------------------------------------------------------

def test_f_review_required():
    cases = [
        # a discount is never a standalone journal entry
        "Discount received Rs.200.",
        "Allowed discount Rs.200.",
        # impossible trade discount (not positive and smaller than list)
        "Purchased goods for cash Rs.20,000 less Rs.25,000 trade "
        "discount.",
        # credit TD sale without a party - never a guessed party
        "Sold goods worth Rs.30,000 at 10% trade discount on credit.",
        # rate-based settlement with no amount due
        "Received Rs.9,800 from Ram, after allowing 2 percent cash "
        "discount.",
        "Paid to Amit Rs.9,800, after receiving 2 percent cash discount.",
        # GST + settlement-side discount (15I-K boundary)
        "Purchased goods from Ram on credit Rs.10,000 plus GST @ 18%, "
        "discount allowed Rs.200.",
    ]
    for i, q in enumerate(cases, 1):
        expect_status(q, "REVIEW_REQUIRED", f"F.{i} {q[:48]}")


# ---------------------------------------------------------------------------
# G. Content-compiler discount metadata (15I-L section 16)
# ---------------------------------------------------------------------------

def test_g_metadata():
    m = default_metadata(
        "Purchased goods listed at Rs.20,000 less 10% trade discount "
        "for cash.")
    check("G.1 TD metadata flags",
          m.get("trade_discount") == "YES" and m.get("cash_discount") == "NO",
          str(m.get("trade_discount")))
    check("G.2 TD percentage + gross/net",
          m.get("discount_percentage") == 10
          and m.get("gross_amount") == 20000
          and m.get("net_amount") == 18000.00,
          str({k: m.get(k) for k in ("discount_percentage", "gross_amount",
                                     "net_amount")}))
    m = default_metadata(
        "Received from Mohan Rs.9,800, discount allowed Rs.200.")
    check("G.3 CD metadata flags",
          m.get("cash_discount") == "YES"
          and m.get("discount_amount") == 200.0
          and m.get("settlement_amount") == 10000.0,
          str({k: m.get(k) for k in ("cash_discount", "discount_amount",
                                     "settlement_amount")}))
    m = default_metadata("Received from Mohan Rs.5,000.")
    check("G.4 no-discount metadata stays NONE/UNKNOWN",
          m.get("trade_discount") == "NONE"
          and m.get("cash_discount") == "NONE"
          and m.get("discount_amount") == "UNKNOWN",
          str(m))
    m = default_metadata("Purchased goods from Ram on credit Rs.20,000 "
                         "less 10% TD.")
    check("G.5 TD abbreviation metadata",
          m.get("trade_discount") == "YES"
          and m.get("discount_percentage") == 10,
          str({k: m.get(k) for k in ("trade_discount",
                                     "discount_percentage")}))
    # a plain receipt figure is never discount metadata (fix round 5)
    m = default_metadata("Received from Ram Rs.9,800, discount allowed "
                         "Rs.200.")
    check("G.6 receipt figure never discount metadata",
          m.get("discount_amount") == 200.0,
          str(m.get("discount_amount")))


# ---------------------------------------------------------------------------
# H. Practice-engine mistake taxonomy (15I-H TRADE/CASH_DISCOUNT_ERROR)
# ---------------------------------------------------------------------------

def test_h_practice_taxonomy():
    tmp = os.path.join(tempfile.gettempdir(),
                       f"fte_15l_practice_{uuid.uuid4().hex[:8]}")
    bank = QuestionBank(store_path=tmp + "_bank.json")
    qid_cd = bank.create_question(
        "Received from Mohan Rs.9,800, discount allowed Rs.200.",
        source_type="manual", source_reference="15l_cd")
    bank.compile_question(qid_cd)
    bank.validate_question(qid_cd)
    bank.approve_question(qid_cd)
    q_cd = bank.get_question(qid_cd)
    check("H.1 CD question APPROVED with discount metadata",
          q_cd.get("status") == "APPROVED"
          and q_cd.get("cash_discount") == "YES"
          and q_cd.get("discount_amount") == 200.0
          and q_cd.get("settlement_amount") == 10000.0,
          str({k: q_cd.get(k) for k in ("status", "cash_discount",
                                        "discount_amount",
                                        "settlement_amount")}))
    qid_td = bank.create_question(
        "Purchased goods listed at Rs.20,000 less 10% trade discount "
        "for cash.",
        source_type="manual", source_reference="15l_td")
    bank.compile_question(qid_td)
    bank.validate_question(qid_td)
    bank.approve_question(qid_td)
    q_td = bank.get_question(qid_td)
    check("H.2 TD question APPROVED with metadata",
          q_td.get("status") == "APPROVED"
          and q_td.get("trade_discount") == "YES",
          str(q_td.get("trade_discount")))

    eng = PracticeEngine(bank, store_path=tmp + "_store.json", rng_seed=7)
    sid = eng.create_session("student_l")

    def submit(qid, dr_a, dr_m, cr_a, cr_m):
        return eng.submit_answer(sid, qid, dr_a, dr_m, cr_a, cr_m,
                                 raw_response="")

    out = submit(qid_cd, ["Cash", "Discount Allowed"], [9800, 200],
                 ["Mohan"], [10000])
    check("H.3 correct settlement -> CORRECT",
          out.get("outcome") == OUTCOME_CORRECT, str(out.get("outcome")))
    # the student dropped the discount account entirely (balanced entry)
    out = submit(qid_cd, ["Cash"], [9800], ["Mohan"], [9800])
    check("H.4 dropped discount -> CASH_DISCOUNT_ERROR",
          out.get("outcome") == OUTCOME_INCORRECT
          and out.get("mistake_category") == "CASH_DISCOUNT_ERROR",
          str((out.get("outcome"), out.get("mistake_category"))))
    # the student used the WRONG discount account (Received vs Allowed)
    out = submit(qid_cd, ["Cash", "Discount Received"], [9800, 200],
                 ["Mohan"], [10000])
    check("H.5 wrong discount account -> CASH_DISCOUNT_ERROR",
          out.get("outcome") == OUTCOME_INCORRECT
          and out.get("mistake_category") == "CASH_DISCOUNT_ERROR",
          str((out.get("outcome"), out.get("mistake_category"))))
    # the student posted the gross instead of applying the trade discount
    out = submit(qid_td, ["Purchases"], [20000], ["Cash"], [20000])
    check("H.6 forgot TD -> TRADE_DISCOUNT_ERROR",
          out.get("outcome") == OUTCOME_INCORRECT
          and out.get("mistake_category") == "TRADE_DISCOUNT_ERROR",
          str((out.get("outcome"), out.get("mistake_category"))))
    out = submit(qid_td, ["Purchases"], [18000], ["Cash"], [18000])
    check("H.7 correct TD entry -> CORRECT",
          out.get("outcome") == OUTCOME_CORRECT, str(out.get("outcome")))


# ---------------------------------------------------------------------------
# I. Safety invariants over the 15L matrix + corpus differential
# ---------------------------------------------------------------------------

# The 15I-J baseline fingerprint - the non-discount corpus is
# byte-identical through the entire 15I-L layer (differential testing).
CORPUS_FINGERPRINT = ("36ee762a2d2a03a4273d40ee8921082289fad4cae3858226"
                      "fd0d619232f7bc25")

L_MATRIX = [
    "Purchased goods listed at Rs.20,000 less 10% trade discount for "
    "cash.",
    "Purchased goods from Ram on credit Rs.20,000 less 10% TD.",
    "Purchased goods from Ram for cash Rs.20,000 less Rs.2,000 trade "
    "discount.",
    "Sold goods to Ram on credit Rs.30,000 at 10% trade discount.",
    "Sold goods to Ram for Rs.10,000 on credit. Received Rs.9,800 from "
    "Ram and allowed Rs.200 cash discount.",
    "Purchased goods from Rahul for Rs.10,000 on credit. Paid Rs.9,800 "
    "and received Rs.200 cash discount.",
    "Received from Mohan Rs.9,800, discount allowed Rs.200.",
    "Paid to Amit Rs.9,800, discount received Rs.200.",
    "Received from Mohan Rs.9,800, allowed Rs.200 cash discount.",
    "Paid to Amit Rs.9,800, received Rs.200 cash discount.",
    "Received from Mohan Rs.5,000 in full settlement of his account of "
    "Rs.5,200.",
    "Purchased goods from Rahul for Rs.10,000 at 10% trade discount, "
    "half paid immediately with 2% cash discount.",
    "Purchased goods from Rahul for Rs.10,000, paid him Rs.3,000 "
    "immediately and 2% cash discount on the paid amount.",
    "Sold goods to Ram for Rs.10,000 on credit. Received Rs.9,800 from "
    "Ram, after allowing 2 percent cash discount.",
    "Purchased goods from Rahul for Rs.10,000 on credit. Paid Rs.9,800, "
    "after receiving 2 percent cash discount.",
    "Sold goods to Ram for cash Rs.10,000, allowed 2% cash discount.",
    "Sold goods to Ram on credit Rs.20,000 less 10% trade discount, "
    "IGST @ 18%.",
    "Purchased goods from Ram on credit Rs.20,000 less 10% trade "
    "discount, CGST @ 9% and SGST @ 9%.",
    "Discount received Rs.200.",
    "Allowed discount Rs.200.",
    "Purchased goods for cash Rs.20,000 less Rs.25,000 trade discount.",
    "Received Rs.9,800 from Ram, after allowing 2 percent cash discount.",
    "Paid to Amit Rs.9,800, after receiving 2 percent cash discount.",
    "Purchased goods from Ram on credit Rs.10,000 plus GST @ 18%, "
    "discount allowed Rs.200.",
    "Sold goods worth Rs.30,000 at 10% trade discount on credit.",
]


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


def test_i_invariants():
    questions = corpus_cases()
    blob = json.dumps(
        {q: compact(q) for q in sorted(questions)},
        sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    check("I.1 corpus differential unchanged", digest == CORPUS_FINGERPRINT,
          f"got {digest} want {CORPUS_FINGERPRINT}")
    check("I.2 corpus size stable", len(questions) == 325,
          str(len(questions)))
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
    check("I.3 zero unbalanced VERIFIED journals over corpus",
          unbalanced == 0, str(unbalanced))
    check("I.4 zero invented accounts over corpus", invented == 0,
          str(invented))
    check("I.5 zero exceptions over corpus", len(bad) == 0, str(bad[:3]))
    mat_bad = []
    mat_unbalanced = 0
    mat_invented = 0
    refusals_with_lines = 0
    for q in L_MATRIX:
        try:
            r = reason_bk_question(q)
        except Exception as exc:  # noqa: BLE001
            mat_bad.append((q, f"EXC {exc}"))
            continue
        if r.get("status") == "VERIFIED":
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
            if (r.get("debit_lines") or r.get("credit_lines")):
                refusals_with_lines += 1
                mat_bad.append((q, "refusal carries journal lines"))
    check("I.6 zero unbalanced VERIFIED journals over 15L matrix",
          mat_unbalanced == 0, str(mat_unbalanced))
    check("I.7 zero invented accounts over 15L matrix",
          mat_invented == 0, str(mat_invented))
    check("I.8 zero exceptions over 15L matrix", len(mat_bad) == 0,
          str(mat_bad[:3]))
    check("I.9 no refusal ever carries journal lines",
          refusals_with_lines == 0, str(refusals_with_lines))


def main():
    test_a_trade_discount()
    test_b_cash_discount_amount()
    test_c_cash_discount_rate()
    test_d_gst_td()
    test_e_variants()
    test_f_review_required()
    test_g_metadata()
    test_h_practice_taxonomy()
    test_i_invariants()
    print(f"\n15I-L gate: {OK[0]} checks passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
