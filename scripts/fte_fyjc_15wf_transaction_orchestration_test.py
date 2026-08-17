#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 15I-WF - Unified Transaction Orchestration & Authority Composition
scripts/fte_fyjc_15wf_transaction_orchestration_test.py

Locks in the Sprint 15I-WF orchestration layer:

  * the transaction graph (segments, stated facts with provenance,
    authority routing, dependencies, amount ownership) never lets
    segmentation silently discard a fact;
  * every segment is routed to EXACTLY one base authority (with
    explicitly cooperating GST / Settlement authorities); a segment
    routed to an unimplemented authority (Consignment, Joint Venture,
    Single Entry, Adjustment, Bills, Discrepancy) is refused - never
    resolved by guessing;
  * amount ownership: every stated amount receives one deterministic
    role; two different amounts claiming the same role in one segment
    force REVIEW_REQUIRED with zero journal lines;
  * segment completeness: a VERIFIED multi-transaction result must carry
    exactly one journal per graph segment - a silently dropped segment
    forces REVIEW_REQUIRED;
  * the deterministic merge stage preserves ordering + provenance,
    rejects duplicate postings and conflicting authority amounts, and
    verifies debit == credit;
  * the orchestrator only ever NARROWS: it never creates a VERIFIED
    output the hardened authority would refuse, and clean historical
    inputs pass through byte-identically.

New safety boundaries introduced by 15I-WF (all refuse, never guess):
  * dishonour / cheque-bounce - the stated fact must never silently
    disappear from a VERIFIED journal;
  * bills of exchange - never booked as cash;
  * asset transactions carrying GST or trade-discount wording that the
    Asset Authority does not consume (authority-boundary conflict).

Sprint 15I-BILLS (implemented) extends the bills boundary: the Bills
Authority now resolves the bill lifecycle (drawing / acceptance,
discounting, endorsement, collection, honour / dishonour with noting
charges) and books a bill as Bills Receivable / Bills Payable - never
as cash. PART J and PART P.5 are updated to lock in that post-15I-BILLS
surface; a missing prior bill state still refuses.

Sprint 15I-DISC (implemented) extends the dishonour boundary: the
Discrepancy Authority now resolves a dishonour whose prior receipt is
ESTABLISHED in the input (reversal + customer-balance reinstatement,
plus the sale is never dropped), while a dishonour with no reliable
prior record still refuses - history is never invented. PART G, PART L,
PART P.4 and PART Q are updated to lock in that post-15I-DISC surface;
all non-discrepancy expectations are unchanged.

Exit code 0 = all checks pass.
"""

import json
import os
import sys

sys.path.insert(0, os.getcwd())

from backend.maths.fyjc_accounting import (  # noqa: E402
    hardened_bookkeeping_outcome,
)
from backend.maths.fyjc_bk_reasoning import reason_bk_question  # noqa: E402
from backend.maths.fyjc_discrepancy import (  # noqa: E402
    discrepancy_outcome,
)
from backend.maths.fyjc_normalization import vy_harden  # noqa: E402
from backend.maths.fyjc_orchestration import (  # noqa: E402
    authority_report,
    build_transaction_graph,
    orchestrate,
)
from backend.maths.fyjc_student_flow import (  # noqa: E402
    run_fyjc_accounting_flow,
    run_fyjc_student_flow,
)
from backend.maths.fyjc_bk_reasoning import NOT_SUPPORTED  # noqa: E402
from backend.maths.status import (  # noqa: E402
    BLOCKED,
    REVIEW_REQUIRED,
    VERIFIED,
)

TOTAL: list = [0]
FAILURES: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    TOTAL[0] += 1
    if cond:
        print(f"OK [{name}]")
    else:
        FAILURES.append(name)
        print(f"FAIL [{name}] {detail}")


def lines(result) -> list:
    return [(l.get("account"), str(l.get("amount")))
            for l in (result.get("debit_lines") or [])
            + (result.get("credit_lines") or [])]


def graph_of(q: str):
    from backend.maths.fyjc_normalization import normalize_fyjc_text
    n = normalize_fyjc_text(q)
    return build_transaction_graph(q, normalized=n.text,
                                   normalization=n.provenance)


def violations_of(result, kind: str) -> list:
    orch = result.get("orchestration") or {}
    return [v for v in orch.get("violations", []) if v["kind"] == kind]


def invariants_of(result) -> dict:
    orch = result.get("orchestration") or {}
    return orch.get("invariants", {})


_NUMERIC_INVARIANTS = (
    "unsafe_confident",
    "dropped_valid_segments",
    "unresolved_amounts_guessed",
    "duplicated_amount_ownership",
    "authority_conflicts_verified",
    "invented_accounts",
    "unbalanced_verified",
)


def invariants_zero(inv: dict) -> bool:
    """Every safety counter is zero and the two boolean invariants hold."""
    return (all(inv.get(k, 0) == 0 for k in _NUMERIC_INVARIANTS)
            and inv.get("flow_verdict_eq_hardened") is True
            and inv.get("deterministic") is True)


# ---------------------------------------------------------------------------
# PART A - ordinary purchase
# ---------------------------------------------------------------------------
def test_a_ordinary_purchase():
    print("PART A - ORDINARY PURCHASE")
    q = "Purchased goods from Ram on credit for Rs.10,000"
    r = orchestrate(q)
    check("A.1 purchase VERIFIED", r.get("status") == VERIFIED,
          r.get("status"))
    check("A.2 purchase journal",
          lines(r) == [("Purchases", "10000"), ("Ram", "10000")],
          str(lines(r)))
    g = graph_of(q)
    check("A.3 single segment", len(g.segments) == 1, str(len(g.segments)))
    check("A.4 base authority COMMERCIAL_CORE",
          g.segments[0].base_authority == "COMMERCIAL_CORE",
          g.segments[0].base_authority)
    check("A.5 amount ownership",
          any(o["role"] == "transaction_value" and o["amount"] == "10000"
              for o in g.ownership),
          str(g.ownership))
    inv = invariants_of(r)
    check("A.6 invariants all zero",
          invariants_zero(inv), str(inv))


# ---------------------------------------------------------------------------
# PART B - ordinary sale
# ---------------------------------------------------------------------------
def test_b_ordinary_sale():
    print("PART B - ORDINARY SALE")
    q = "Sold goods to Ram on credit for Rs.10,000"
    r = orchestrate(q)
    check("B.1 sale VERIFIED", r.get("status") == VERIFIED,
          r.get("status"))
    check("B.2 sale journal",
          lines(r) == [("Ram", "10000"), ("Sales", "10000")],
          str(lines(r)))


# ---------------------------------------------------------------------------
# PART C - sale + TD + GST + partial payment (facts preserved even when
# the engine's verified surface refuses)
# ---------------------------------------------------------------------------
def test_c_sale_td_gst_partial_payment():
    print("PART C - SALE + TD + GST + PARTIAL PAYMENT")
    q = ("Sold goods to Ram for Rs.20,000 at 10% trade discount and 18% "
         "GST, received half immediately")
    r = orchestrate(q)
    check("C.1 no unsafe VERIFIED", r.get("status") != VERIFIED,
          r.get("status"))
    check("C.2 zero journal lines", lines(r) == [], str(lines(r)))
    check("C.3 student-readable refusal",
          bool(r.get("why_not")), str(r.get("why_not"))[:80])
    g = graph_of(q)
    facts = [(f.kind, str(f.value)) for f in g.segments[0].facts]
    check("C.4 sale fact preserved", ("party", "Ram") in facts,
          str(facts))
    check("C.5 value fact preserved", ("amount", "20000") in facts,
          str(facts))
    check("C.6 TD rate preserved", ("rate", "10") in facts, str(facts))
    check("C.7 GST rate preserved", ("rate", "18") in facts, str(facts))
    check("C.8 payment fraction preserved", ("fraction", "50") in facts,
          str(facts))
    check("C.9 GST + Settlement cooperating",
          "GST_AUTHORITY" in g.segments[0].cooperating
          and "SETTLEMENT_AUTHORITY" in g.segments[0].cooperating,
          str(g.segments[0].cooperating))


# ---------------------------------------------------------------------------
# PART D - purchase + TD + GST + full payment (safe refusal, facts kept)
# ---------------------------------------------------------------------------
def test_d_purchase_td_gst_full_payment():
    print("PART D - PURCHASE + TD + GST + FULL PAYMENT")
    q = ("Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
         "18% GST, paid the full amount immediately")
    r = orchestrate(q)
    check("D.1 no unsafe VERIFIED", r.get("status") != VERIFIED,
          r.get("status"))
    check("D.2 zero journal lines", lines(r) == [], str(lines(r)))
    g = graph_of(q)
    facts = [(f.kind, str(f.value)) for f in g.segments[0].facts]
    check("D.3 value preserved", ("amount", "20000") in facts, str(facts))
    check("D.4 TD rate preserved", ("rate", "10") in facts, str(facts))
    check("D.5 GST rate preserved", ("rate", "18") in facts, str(facts))
    # positive control: purchase + TD + GST with explicit components stays
    # VERIFIED through the orchestrator
    q2 = ("Purchased goods from Ram for Rs.20,000 at 10% trade discount "
          "and 18% GST with CGST Rs.1,620 and SGST Rs.1,620")
    r2 = orchestrate(q2)
    check("D.6 explicit CGST+SGST VERIFIED",
          r2.get("status") == VERIFIED, r2.get("status"))
    check("D.7 GST consumed the TD-net base",
          lines(r2) == [("Purchases", "18000.00"), ("Input CGST", "1620"),
                        ("Input SGST", "1620"), ("Ram", "21240.00")],
          str(lines(r2)))


# ---------------------------------------------------------------------------
# PART E - machinery / asset transactions (Asset Authority)
# ---------------------------------------------------------------------------
def test_e_machinery_gst_payment():
    print("PART E - MACHINERY + GST + PAYMENT")
    q1 = "Purchased machinery for Rs.60,000 by cheque"
    r1 = orchestrate(q1)
    check("E.1 asset purchase VERIFIED", r1.get("status") == VERIFIED,
          r1.get("status"))
    check("E.2 machinery journal",
          lines(r1) == [("Machinery", "60000"), ("Bank", "60000")],
          str(lines(r1)))
    g1 = graph_of(q1)
    check("E.3 routed to ASSET_AUTHORITY",
          g1.segments[0].base_authority == "ASSET_AUTHORITY",
          g1.segments[0].base_authority)
    q2 = ("Sold old machinery for Rs.40,000, charged 18% GST, received "
          "half by cheque")
    r2 = orchestrate(q2)
    check("E.4 asset+GST refuses (NOT_SUPPORTED)",
          r2.get("status") == NOT_SUPPORTED, r2.get("status"))
    check("E.5 zero journal lines", lines(r2) == [], str(lines(r2)))
    check("E.6 authority-boundary note reported",
          len(violations_of(r2, "authority_boundary")) >= 1,
          str(violations_of(r2, "authority_boundary")))
    g2 = graph_of(q2)
    check("E.7 asset base + GST cooperating",
          g2.segments[0].base_authority == "ASSET_AUTHORITY"
          and "GST_AUTHORITY" in g2.segments[0].cooperating,
          f"{g2.segments[0].base_authority} {g2.segments[0].cooperating}")
    check("E.8 machinery value preserved",
          ("amount", "40000") in [(f.kind, str(f.value))
                                  for f in g2.segments[0].facts],
          str([(f.kind, str(f.value)) for f in g2.segments[0].facts]))


# ---------------------------------------------------------------------------
# PART F - return chain
# ---------------------------------------------------------------------------
def test_f_return_chain():
    print("PART F - RETURN CHAIN")
    q1 = ("Purchased goods from Rahul on credit Rs.20,000, returned goods "
          "worth Rs.1,000 to him")
    r1 = orchestrate(q1)
    check("F.1 purchase+return VERIFIED", r1.get("status") == VERIFIED,
          r1.get("status"))
    check("F.2 purchase+return journal",
          lines(r1) == [("Purchases", "20000"), ("Rahul", "1000"),
                        ("Rahul", "20000"), ("Purchase Returns", "1000")],
          str(lines(r1)))
    q2 = ("Sold goods to Ram for Rs.10,000 on credit. Ram returned goods "
          "worth Rs.2,000.")
    r2 = orchestrate(q2)
    check("F.3 sale+return VERIFIED", r2.get("status") == VERIFIED,
          r2.get("status"))
    check("F.4 sale+return journal",
          lines(r2) == [("Ram", "10000"), ("Sales Returns", "2000"),
                        ("Sales", "10000"), ("Ram", "2000")],
          str(lines(r2)))


# ---------------------------------------------------------------------------
# PART G - payment + later dishonour (Discrepancy Authority implemented)
# ---------------------------------------------------------------------------
def test_g_payment_dishonour():
    print("PART G - PAYMENT + LATER DISHONOUR (Discrepancy Authority)")
    # 15I-DISC: a dishonour whose prior receipt is ESTABLISHED in the
    # input is resolved by the Discrepancy Authority - the receipt is
    # reversed and the customer balance reinstated. It is never treated
    # as a new unrelated receipt.
    q1 = ("Received a cheque from Ram for Rs.10,000 which was later "
          "dishonoured")
    r1 = orchestrate(q1)
    check("G.1 dishonour VERIFIED (prior receipt established)",
          r1.get("status") == VERIFIED, r1.get("status"))
    check("G.2 receipt + reversal journal",
          lines(r1) == [("Bank", "10000"), ("Ram", "10000"),
                        ("Ram", "10000"), ("Bank", "10000")],
          str(lines(r1)))
    check("G.3 resolved by Discrepancy Authority (no unresolved event)",
          (r1.get("orchestration") or {}).get("authority")
          == "discrepancy-authority"
          and len(violations_of(r1, "unresolved_event_fact")) == 0,
          str(violations_of(r1, "unresolved_event_fact")))
    q2 = ("Sold goods to Ram for Rs.10,000 and received a cheque which "
          "was dishonoured")
    r2 = orchestrate(q2)
    check("G.4 sale + dishonour VERIFIED (sale never dropped)",
          r2.get("status") == VERIFIED, r2.get("status"))
    check("G.5 sale + receipt + reversal journal",
          lines(r2) == [("Ram", "10000"), ("Bank", "10000"),
                        ("Ram", "10000"), ("Sales", "10000"),
                        ("Ram", "10000"), ("Bank", "10000")],
          str(lines(r2)))
    # 15I-DISC section-6 history gate: no reliable prior record -> the
    # orchestrator still refuses and never reconstructs the history.
    q3 = "Ram's cheque of Rs.5,000 was dishonoured"
    r3 = orchestrate(q3)
    check("G.6 missing-history dishonour refuses",
          r3.get("status") == REVIEW_REQUIRED, r3.get("status"))
    check("G.7 zero journal lines", lines(r3) == [], str(lines(r3)))
    # control: a plain cheque settlement still VERIFIEDs
    q4 = ("Received from Ram Rs.10,000 by cheque in full settlement of "
          "his account of Rs.10,000")
    r4 = orchestrate(q4)
    check("G.8 plain cheque settlement VERIFIED",
          r4.get("status") == VERIFIED, r4.get("status"))
    check("G.9 cheque journal",
          lines(r4) == [("Bank", "10000"), ("Ram", "10000")],
          str(lines(r4)))


# ---------------------------------------------------------------------------
# PART H - multi-transaction question (merge + completeness)
# ---------------------------------------------------------------------------
def test_h_multi_transaction():
    print("PART H - MULTI-TRANSACTION QUESTION")
    q = ("Started business with cash Rs.1,00,000. Purchased goods for "
         "cash Rs.20,000. Paid rent Rs.5,000.")
    r = orchestrate(q)
    check("H.1 multi VERIFIED", r.get("status") == VERIFIED,
          r.get("status"))
    check("H.2 all three segments journaled",
          lines(r) == [("Cash", "100000"), ("Purchases", "20000"),
                       ("Rent", "5000"), ("Capital", "100000"),
                       ("Cash", "20000"), ("Cash", "5000")],
          str(lines(r)))
    orch = r.get("orchestration") or {}
    check("H.3 graph has 3 segments",
          len(orch.get("segments", [])) == 3,
          str(len(orch.get("segments", []))))
    check("H.4 merge balanced",
          (orch.get("merge") or {}).get("balanced") is True,
          str((orch.get("merge") or {}).get("balanced")))
    check("H.5 merge carries per-segment provenance",
          len((orch.get("merge") or {}).get("lines", [])) == 6,
          str(len((orch.get("merge") or {}).get("lines", []))))
    inv = invariants_of(r)
    check("H.6 invariants all zero",
          invariants_zero(inv), str(inv))
    check("H.7 flow == hardened",
          run_fyjc_accounting_flow(q).get("status")
          == hardened_bookkeeping_outcome(q).get("status"),
          "parity")


# ---------------------------------------------------------------------------
# PART I - ambiguous multi-amount question
# ---------------------------------------------------------------------------
def test_i_ambiguous_multi_amount():
    print("PART I - AMBIGUOUS MULTI-AMOUNT QUESTION")
    q = "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000."
    r = orchestrate(q)
    check("I.1 ambiguous refuses", r.get("status") == REVIEW_REQUIRED,
          r.get("status"))
    check("I.2 zero journal lines", lines(r) == [], str(lines(r)))
    check("I.3 duplicated_amount_ownership reported",
          len(violations_of(r, "duplicated_amount_ownership")) >= 1,
          str(violations_of(r, "duplicated_amount_ownership")))
    check("I.4 explanation names both amounts",
          "20,000" in (r.get("why_not") or "")
          and "18,000" in (r.get("why_not") or ""),
          str(r.get("why_not"))[:120])


# ---------------------------------------------------------------------------
# PART J - unsupported authority segment
# ---------------------------------------------------------------------------
def test_j_unsupported_authority():
    print("PART J - UNSUPPORTED AUTHORITY SEGMENT")
    cases = [
        ("Consigned goods worth Rs.50,000 to Mohan on consignment basis.",
         "CONSIGNMENT_AUTHORITY"),
        ("Entered into a joint venture with Shyam, contributing Rs.20,000.",
         "JOINT_VENTURE_AUTHORITY"),
        ("Provided depreciation on machinery Rs.5,000.",
         "ADJUSTMENT_AUTHORITY"),
    ]
    for q, authority in cases:
        r = orchestrate(q)
        check(f"J.{authority} refuses",
              r.get("status") == NOT_SUPPORTED, r.get("status"))
        check(f"J.{authority} zero lines", lines(r) == [], str(lines(r)))
        g = graph_of(q)
        check(f"J.{authority} routed correctly",
              g.segments[0].base_authority == authority,
              g.segments[0].base_authority)
    # a bill of exchange is NEVER booked as cash - the Bills Authority
    # (Sprint 15I-BILLS) books it as Bills Receivable
    r = orchestrate("Received a bill of exchange from Ram for Rs.10,000.")
    check("J.bills never cash (Bills Receivable, not cash)",
          r.get("status") == VERIFIED
          and "Bills Receivable" in [a for a, _ in lines(r)]
          and "Cash" not in str(lines(r))
          and "Bank" not in str(lines(r)),
          str(lines(r)))
    # an everyday bill (electricity / mobile recharge) is NOT a bills-of-
    # exchange event and keeps its existing handling
    r2 = orchestrate("Paid his mobile recharge bill Rs.500.")
    check("J.everyday bill not flagged as bills authority",
          r2.get("status") != VERIFIED,
          f"{r2.get('status')} {str(lines(r2))}")
    check("J.authority registry complete",
          {a["authority"] for a in authority_report()}
          >= {"COMMERCIAL_CORE", "ASSET_AUTHORITY", "GST_AUTHORITY",
              "SETTLEMENT_AUTHORITY", "DISCREPANCY_AUTHORITY",
              "ADJUSTMENT_AUTHORITY", "CONSIGNMENT_AUTHORITY",
              "JOINT_VENTURE_AUTHORITY", "SINGLE_ENTRY_AUTHORITY",
              "BILLS_AUTHORITY"},
          str(authority_report()))


# ---------------------------------------------------------------------------
# PART K - duplicated amount ownership
# ---------------------------------------------------------------------------
def test_k_duplicated_ownership():
    print("PART K - DUPLICATED AMOUNT OWNERSHIP")
    cases = [
        "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000.",
        ("Purchased goods worth Rs.20,000 from Ram at 10% trade discount "
         "and paid Rs.20,000 in cash"),
    ]
    for q in cases:
        r = orchestrate(q)
        check(f"K.dup refuses ({q[:40]})",
              r.get("status") == REVIEW_REQUIRED, r.get("status"))
        check(f"K.dup zero lines ({q[:40]})",
              lines(r) == [], str(lines(r)))
        check(f"K.dup reported ({q[:40]})",
              len(violations_of(r, "duplicated_amount_ownership")) >= 1,
              str(violations_of(r, "duplicated_amount_ownership")))
    # control: the SAME amount in two different roles is NOT a conflict
    q2 = "Received Rs.5,000 from Ram against his account of Rs.10,000"
    r2 = orchestrate(q2)
    check("K.control partial settlement VERIFIED",
          r2.get("status") == VERIFIED, r2.get("status"))
    check("K.control no duplicated-ownership violation",
          len(violations_of(r2, "duplicated_amount_ownership")) == 0,
          str(violations_of(r2, "duplicated_amount_ownership")))
    check("K.control journal",
          lines(r2) == [("Cash", "5000"), ("Ram", "5000")],
          str(lines(r2)))


# ---------------------------------------------------------------------------
# PART L - dropped segment detection
# ---------------------------------------------------------------------------
def test_l_dropped_segment():
    print("PART L - DROPPED SEGMENT DETECTION")
    # 15I-DISC: the Discrepancy Authority now resolves the dishonour
    # chain, so sale + cheque receipt + reversal are ALL journaled - the
    # sale is never dropped. The hardened authority alone would still
    # journal only the cheque receipt (checked below).
    q = ("Sold goods to Ram for Rs.10,000 and received a cheque which was "
         "dishonoured")
    r = orchestrate(q)
    check("L.1 sale + dishonour VERIFIED (sale never dropped)",
          r.get("status") == VERIFIED, r.get("status"))
    check("L.2 full chain journaled (sale + receipt + reversal)",
          lines(r) == [("Ram", "10000"), ("Bank", "10000"),
                       ("Ram", "10000"), ("Sales", "10000"),
                       ("Ram", "10000"), ("Bank", "10000")],
          str(lines(r)))
    raw = reason_bk_question(q)
    check("L.3 raw authority alone would have dropped it",
          raw.get("status") == VERIFIED
          and "Sales" not in [l.get("account")
                              for l in (raw.get("debit_lines") or [])
                              + (raw.get("credit_lines") or [])],
          str(lines(raw)))
    # multi-transaction completeness: 3 segments -> exactly 3 journals
    q2 = ("Started business with cash Rs.1,00,000. Purchased goods for "
          "cash Rs.20,000. Paid rent Rs.5,000.")
    r2 = orchestrate(q2)
    orch = r2.get("orchestration") or {}
    check("L.4 journal-count parity",
          len(orch.get("segments", [])) == 3
          and len((orch.get("merge") or {}).get("lines", [])) == 6,
          f"{len(orch.get('segments', []))} segments")
    check("L.5 no dropped-segment violation",
          len(violations_of(r2, "dropped_valid_segment")) == 0,
          str(violations_of(r2, "dropped_valid_segment")))


# ---------------------------------------------------------------------------
# PART M - conflicting authority result
# ---------------------------------------------------------------------------
def test_m_conflicting_authority():
    print("PART M - CONFLICTING AUTHORITY RESULT")
    # trade discount is a Commercial Core rule; the Asset Authority does
    # not consume it - the boundary conflict must refuse, never resolve
    q1 = "Sold old machinery for Rs.40,000 at 10% trade discount"
    r1 = orchestrate(q1)
    check("M.1 asset+TD refuses", r1.get("status") != VERIFIED,
          r1.get("status"))
    check("M.2 zero journal lines", lines(r1) == [], str(lines(r1)))
    check("M.3 authority-boundary note reported",
          len(violations_of(r1, "authority_boundary")) >= 1,
          str(violations_of(r1, "authority_boundary")))
    # GST on an asset disposal is outside the GST Authority's verified
    # surface - NOT_SUPPORTED, never a goods-GST journal on machinery
    q2 = ("Sold old machinery for Rs.40,000, charged 18% GST, received "
          "half by cheque")
    r2 = orchestrate(q2)
    check("M.4 asset+GST refuses", r2.get("status") == NOT_SUPPORTED,
          r2.get("status"))
    check("M.5 zero journal lines", lines(r2) == [], str(lines(r2)))
    check("M.6 GST boundary explained",
          "GST" in (r2.get("why_not") or ""),
          str(r2.get("why_not"))[:120])
    # a cooperating GST+settlement on a GOODS transaction is fine
    q3 = ("Purchased goods from Ram for Rs.20,000 at 10% trade discount "
          "and 18% GST with CGST Rs.1,620 and SGST Rs.1,620")
    r3 = orchestrate(q3)
    check("M.7 goods GST+TD VERIFIED (cooperating)",
          r3.get("status") == VERIFIED, r3.get("status"))


# ---------------------------------------------------------------------------
# PART N - dependency propagation
# ---------------------------------------------------------------------------
def test_n_dependency_propagation():
    print("PART N - DEPENDENCY PROPAGATION")
    q = ("Purchased goods from Ram for Rs.20,000 at 10% trade discount "
         "and 18% GST with CGST Rs.1,620 and SGST Rs.1,620")
    r = orchestrate(q)
    check("N.1 VERIFIED", r.get("status") == VERIFIED, r.get("status"))
    # the GST authority consumed the TD-net base (18,000), not the list
    # price - downstream consumed upstream output
    check("N.2 downstream consumed TD-net",
          lines(r) == [("Purchases", "18000.00"), ("Input CGST", "1620"),
                       ("Input SGST", "1620"), ("Ram", "21240.00")],
          str(lines(r)))
    g = graph_of(q)
    deps = set(g.dependencies)
    check("N.3 TD -> net edge",
          ("S0:trade_discount", "S0:net_value") in deps, str(deps))
    check("N.4 net -> GST base edge",
          ("S0:net_value", "S0:gst_taxable_base") in deps, str(deps))
    # dishonour dependency: payment -> dishonour
    g2 = graph_of("Received a cheque from Ram for Rs.10,000 which was "
                  "later dishonoured")
    deps2 = set(g2.dependencies)
    check("N.5 payment -> dishonour edge",
          ("S0:payment", "S0:dishonour") in deps2, str(deps2))


# ---------------------------------------------------------------------------
# PART O - deterministic repeated execution
# ---------------------------------------------------------------------------
def test_o_determinism():
    print("PART O - DETERMINISTIC REPEATED EXECUTION")
    corpus = [
        "Purchased goods from Ram on credit for Rs.10,000",
        "Sold goods to Ram for Rs.10,000 and received a cheque which was "
        "dishonoured",
        "Started business with cash Rs.1,00,000. Purchased goods for cash "
        "Rs.20,000. Paid rent Rs.5,000.",
        "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
        "18% GST with CGST Rs.1,620 and SGST Rs.1,620",
        "Consigned goods worth Rs.50,000 to Mohan on consignment basis.",
        "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000.",
    ]
    for i, q in enumerate(corpus):
        a = json.dumps(orchestrate(q), default=str, sort_keys=True)
        b = json.dumps(orchestrate(q), default=str, sort_keys=True)
        check(f"O.{i} byte-identical ({q[:40]})", a == b,
              "mismatch on repeated run")


# ---------------------------------------------------------------------------
# PART P - real Streamlit Study/Verify path (AppTest)
# ---------------------------------------------------------------------------
def test_p_streamlit():
    print("PART P - REAL STREAMLIT STUDY/VERIFY PATH")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # pragma: no cover
        check("P.0 apptest available", False, str(exc))
        return
    at = AppTest.from_file("app (1) (9).py", default_timeout=120)
    at.run()
    check("P.1 app entrance", not at.exception,
          [e.stack_trace for e in at.exception])
    at.button(key="fte_btn_signin").click().run()
    at.text_input(key="fte_email").set_value("analyst@example.com")
    at.text_input(key="fte_password").set_value("secret123")
    at.button(key="fte_btn_continue").click().run()
    at.button(key="fte_ws_professional").click().run()
    at.segmented_control(key="fte_page").set_value("FYJC Study").run()
    check("P.2 FYJC Study page paints", not at.exception,
          [e.stack_trace for e in at.exception])
    at.radio(key="fte_fyjc_mode").set_value("\u270d\ufe0f Enter Question").run()

    def ask(q):
        at.text_area(key="fte_fyjc_question").set_value(q).run()
        at.button(key="fte_fyjc_go").click().run()
        return " ".join(m.value for m in at.markdown)

    md = ask("Sold gds to Ram 10k on credit 5% td")
    check("P.3 normalized input VERIFIED (pass-through)",
          "VERIFIED" in md.upper() and not at.exception,
          [e.stack_trace for e in at.exception] + [md[:120]])
    md = ask("Received a cheque from Ram for Rs.10,000 which was later "
             "dishonoured")
    check("P.4 dishonour VERIFIED (reversal, no Almost there)",
          "VERIFIED" in md.upper() and "Almost there" not in md
          and not at.exception,
          [e.stack_trace for e in at.exception] + [md[:160]])
    md = ask("Received a bill of exchange from Ram for Rs.10,000")
    check("P.5 bill of exchange VERIFIED (Bills Receivable, never cash)",
          "VERIFIED" in md.upper() and "NOT SUPPORTED" not in md.upper()
          and not at.exception,
          [e.stack_trace for e in at.exception] + [md[:160]])
    md = ask("Purchased goods from Ram for Rs.20,000 at 10% trade discount "
             "and 18% GST with CGST Rs.1,620 and SGST Rs.1,620")
    check("P.6 CGST+SGST still VERIFIED", "VERIFIED" in md.upper(),
          md[:120])
    md = ask("Started business with cash Rs.1,00,000. Purchased goods for "
             "cash Rs.20,000. Paid rent Rs.5,000.")
    check("P.7 multi-transaction VERIFIED", "VERIFIED" in md.upper(),
          md[:120])


# ---------------------------------------------------------------------------
# PART Q - safety invariant sweep
# ---------------------------------------------------------------------------
def test_q_invariant_sweep():
    print("PART Q - SAFETY INVARIANT SWEEP")
    verified_corpus = [
        "Purchased goods from Ram on credit for Rs.10,000",
        "Sold goods to Ram on credit for Rs.10,000",
        "Purchased goods from Ram for Rs.20,000 at 10% trade discount and "
        "18% GST with CGST Rs.1,620 and SGST Rs.1,620",
        "Sold goods to Ram on credit for Rs.10,000. Received Rs.5,000 "
        "from him in part settlement.",
        "Started business with cash Rs.1,00,000. Purchased goods for cash "
        "Rs.20,000. Paid rent Rs.5,000.",
        "Purchased goods from Rahul on credit Rs.20,000, returned goods "
        "worth Rs.1,000 to him",
        "Received from Ram Rs.10,000 by cheque in full settlement of his "
        "account of Rs.10,000",
        "Sold goods for Rs.10,000 to Ram at 10% TD and received the "
        "amount by cheque",
        "Received Rs.5,000 from Ram against his account of Rs.10,000",
        "Purchased machinery for Rs.60,000 by cheque",
        "Paid rent Rs.5,000",
    ]
    for i, q in enumerate(verified_corpus):
        r = orchestrate(q)
        check(f"Q.V.{i} VERIFIED ({q[:40]})",
              r.get("status") == VERIFIED, r.get("status"))
        inv = invariants_of(r)
        check(f"Q.V.{i} invariants all zero ({q[:40]})",
              invariants_zero(inv), str(inv))
        check(f"Q.V.{i} journal balances ({q[:40]})",
              (r.get("journal") or {}).get("balanced") is not False,
              "balanced flag")
    refusal_corpus = [
        "Sold goods for ₹30,000 to Rahul at 10% TD and received 50% by "
        "cheque.",
        "Consigned goods worth Rs.50,000 to Mohan on consignment basis.",
        "Purchased goods for ₹20,000 from Rahul on credit and ₹18,000.",
        "Entered into a joint venture with Shyam, contributing Rs.20,000.",
        "Provided depreciation on machinery Rs.5,000.",
    ]
    for i, q in enumerate(refusal_corpus):
        r = orchestrate(q)
        check(f"Q.R.{i} refuses ({q[:40]})",
              r.get("status") != VERIFIED, r.get("status"))
        check(f"Q.R.{i} zero lines ({q[:40]})",
              lines(r) == [], str(lines(r)))
    # narrowing: the orchestrator never VERIFIEDs what the hardened
    # authority refuses, and never invents accounts. 15I-DISC exception:
    # discrepancy-routed inputs resolve through the Discrepancy Authority
    # (the designed superset of the hardened plain path), so their lines
    # must equal the authority's own deterministic output exactly.
    broad = verified_corpus + refusal_corpus + [
        "Received a cheque from Ram for Rs.10,000 which was later "
        "dishonoured",
        "Sold goods to Ram for Rs.10,000 and received a cheque which was "
        "dishonoured",
    ]
    for i, q in enumerate(broad):
        r = orchestrate(q)
        hard = vy_harden(q)
        if (r.get("orchestration") or {}).get("authority") \
                == "discrepancy-authority":
            disc = discrepancy_outcome(q)
            check(f"Q.N.{i} discrepancy authority resolved ({q[:40]})",
                  r.get("status") == VERIFIED
                  and disc.get("status") == VERIFIED
                  and lines(r) == lines(disc)
                  and json.dumps(orchestrate(q), default=str,
                                 sort_keys=True)
                  == json.dumps(r, default=str, sort_keys=True),
                  f"orchestrator={r.get('status')} disc={disc.get('status')}")
        elif r.get("status") == VERIFIED:
            check(f"Q.N.{i} narrowing holds ({q[:40]})",
                  hard.get("status") == VERIFIED
                  and lines(r) == lines(hard),
                  f"hardened={hard.get('status')}")
        else:
            check(f"Q.N.{i} safe refusal ({q[:40]})",
                  lines(r) == [], str(lines(r)))


# ---------------------------------------------------------------------------
# PART R - contradiction state in the transaction graph
# ---------------------------------------------------------------------------
def test_r_contradiction_state():
    print("PART R - CONTRADICTION STATE IN GRAPH")
    q = ("Sold goods for Rs.10,000. Buyer paid Rs.6,000 immediately and "
         "Rs.5,000 remains outstanding.")
    r = orchestrate(q)
    check("R.1 contradiction refuses (INVALID_INPUT_MATH)",
          r.get("status") == "INVALID_INPUT_MATH", r.get("status"))
    check("R.2 zero journal lines", lines(r) == [], str(lines(r)))
    orch = r.get("orchestration") or {}
    contras = orch.get("contradictions", [])
    check("R.3 graph carries the contradiction",
          len(contras) == 1
          and contras[0]["kind"] == "math_contradiction"
          and contras[0]["status"] == "INVALID_INPUT_MATH"
          and "6,000" in contras[0]["reason"]
          and "5,000" in contras[0]["reason"],
          str(contras))
    # control: a valid split is NOT flagged as a contradiction
    q2 = "Received Rs.5,000 from Ram against his account of Rs.10,000"
    r2 = orchestrate(q2)
    orch2 = r2.get("orchestration") or {}
    check("R.4 valid split not flagged",
          r2.get("status") == VERIFIED
          and orch2.get("contradictions", []) == [],
          str(orch2.get("contradictions", [])))


def main():
    test_a_ordinary_purchase()
    test_b_ordinary_sale()
    test_c_sale_td_gst_partial_payment()
    test_d_purchase_td_gst_full_payment()
    test_e_machinery_gst_payment()
    test_f_return_chain()
    test_g_payment_dishonour()
    test_h_multi_transaction()
    test_i_ambiguous_multi_amount()
    test_j_unsupported_authority()
    test_k_duplicated_ownership()
    test_l_dropped_segment()
    test_m_conflicting_authority()
    test_n_dependency_propagation()
    test_o_determinism()
    test_p_streamlit()
    test_q_invariant_sweep()
    test_r_contradiction_state()
    print(f"\n15I-WF gate: {TOTAL[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
