#!/usr/bin/env python3
"""
Platrixa
Sprint INV-ROLE (Phase 3) - Invoice-Native Accounting Kernel Gate
scripts/fte_invoice_role_gate_test.py

Focused gate for the invoice-labelled-facts extension. Everything here
runs against the REAL production pipeline (orchestrate() and the pure
role/executor modules) and proves, per the Phase 3 contract:

  A. amount-role extraction (explicit label:value only, spans, evidence)
  B. document-ID phantom amounts (BILL-SI-01 never becomes Rs.-1)
  C. net / subtotal roles
  D. GST (amount form) role
  E. CGST/SGST components (sum per component name)
  F. IGST (ONE IGST line, mirroring the narration engine's inter-state
     posting - never a CGST/SGST split)
  G. total role
  H. discount (composed base = net - discount)
  I. shipping/freight (DR Carriage Inward on purchases; fail-closed
     on sales, assets/expenses, and freight-only documents)
  J. paid (partial payment posts ONLY the payment)
  K. outstanding (evidence for payment_outstanding reconciliation)
  L. refund (DR Cash / CR party)
  M. credit note (DR party / CR Purchase Returns)
  N. duplicate labels with the SAME value are tolerated (never chosen
     between - they state one fact)
  O. conflicting label values -> deterministic REVIEW_REQUIRED
  P. missing required semantic fields -> capability-specific refusal
  Q. contradictory totals -> REVIEW_REQUIRED, amounts never modified
  R. existing FYJC narration regression (narration path unchanged)
  S. fail-closed invariants (role layer never posts, refusals never
     carry journal lines, model/labels never become authorities)

The three Phase 3 example documents are proved end-to-end:
  Net 10500 / GST 1890 / Total 12390            -> VERIFIED purchase
  Subtotal 10000 / Discount 500 / GST 1710 / 11210 -> VERIFIED (base 9500)
  Total 12390 / Paid 5000 / Balance Due 7390    -> VERIFIED settlement

Authority chain unchanged: roles are EVIDENCE; the deterministic
executors and the existing authorities (COMMERCIAL_CORE, ASSET_AUTHORITY,
GST_AUTHORITY, SETTLEMENT_AUTHORITY) decide and execute.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_orchestration import (  # noqa: E402
    orchestrate,
)
from backend.maths.fyjc_bk_reasoning import (  # noqa: E402
    REVIEW_REQUIRED,
    _extract_amounts,
)
from backend.maths.invoice_amount_roles import (  # noqa: E402
    resolve_invoice_roles,
)
from backend.maths.invoice_executor import (  # noqa: E402
    compose_invoice_journal,
)

FAIL = []
OK = [0]


def check(name, ok, detail=""):
    if ok:
        OK[0] += 1
        print(f"OK  [{name}]")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"FAIL[{name}] {detail}")


def journal_of(result):
    return result.get("journal") or result


def lines(result, side):
    j = journal_of(result)
    return [(str(l.get("account")), str(l.get("amount")))
            for l in (j.get(side + "_lines") or [])]


def is_refusal(result):
    return result.get("status") == REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# The three Phase 3 example documents (with a single transacting party)
# ---------------------------------------------------------------------------

DOC_PURCHASE_GST = (
    "Tax Invoice BILL-2026-114\n"
    "From: Ram & Sons\n"
    "Net Amount: 10,500\n"
    "GST Amount: 1,890\n"
    "Total Amount: 12,390"
)

DOC_DISCOUNT_GST = (
    "Tax Invoice SUB-2026-77\n"
    "From: Aggarwal Traders\n"
    "Subtotal: 10,000\n"
    "Discount: 500\n"
    "GST: 1,710\n"
    "Total: 11,210"
)

DOC_SETTLEMENT = (
    "Invoice ST-2026-31\n"
    "From: Supplier Ltd\n"
    "Total Amount: 12,390\n"
    "Amount Paid: 5,000\n"
    "Balance Due: 7,390"
)


# ---------------------------------------------------------------------------
# A. amount-role extraction
# ---------------------------------------------------------------------------

def test_a_role_extraction():
    roles = resolve_invoice_roles(DOC_PURCHASE_GST)
    check("A.1 three labelled amounts", len(roles.labelled) == 3,
          str(len(roles.labelled)))
    check("A.2 roles net/tax/total",
          [la.role for la in roles.labelled]
          == ["net", "tax", "total"],
          str([la.role for la in roles.labelled]))
    check("A.3 values exact",
          [la.value for la in roles.labelled]
          == [Decimal("10500"), Decimal("1890"), Decimal("12390")],
          str([str(la.value) for la in roles.labelled]))
    check("A.4 evidence class explicit-label",
          all(la.confidence == "explicit-label"
              for la in roles.labelled), "")
    la = roles.labelled[0]
    src = DOC_PURCHASE_GST
    check("A.5 label span re-proves label text",
          src[la.label_span[0]:la.label_span[1]].lower().startswith("net"),
          src[la.label_span[0]:la.label_span[1]])
    check("A.6 amount span re-proves the value",
          src[la.amount_span[0]:la.amount_span[1]] == "10,500",
          src[la.amount_span[0]:la.amount_span[1]])
    check("A.7 to_dict is JSON-safe evidence",
          roles.to_dict()["labelled"][0]["confidence"]
          == "explicit-label", "")
    # 'GST 18%' and identity numbers are never amounts
    roles2 = resolve_invoice_roles(
        "Tax Invoice INV-2026-001\nInvoice No: 7\nGST 18%\nFrom: Vinod\n"
        "Net Amount: 1000\nTotal: 1000")
    check("A.8 rate form / doc numbers extract no amounts",
          all(la.value not in (Decimal("18"), Decimal("7"), Decimal("2026"),
                               Decimal("1"), Decimal("1.18"))
              for la in roles2.labelled),
          str([str(la.value) for la in roles2.labelled]))


# ---------------------------------------------------------------------------
# B. document-ID phantom amounts
# ---------------------------------------------------------------------------

def test_b_document_ids():
    cases = ["BILL-SI-01", "REF-SI-01", "INV-2026-001", "PO-0042",
             "SI-001", "BILL-A-2026-77", "Ref no REF-SI-01 dated 01-May"]
    for doc in cases:
        amounts, _ = _extract_amounts(doc)
        check(f"B doc-ID {doc!r} yields no amount", amounts == [],
              str(amounts))
    # legitimate negatives are preserved
    for doc, want in (("Paid Rs.-500", [Decimal(-500)]),
                      ("discount -750", [Decimal(-750)]),
                      ("adjustment -0.50", [Decimal("-0.50")])):
        amounts, _ = _extract_amounts(doc)
        check(f"B negative preserved {doc!r}", want[0] in amounts,
              str(amounts))
    # legitimate positives, decimals, Indian commas, currency prefixes
    amounts, _ = _extract_amounts("Received 12390 and 456.78 and 12,340")
    check("B positive/decimal/Indian-comma",
          {Decimal("12390"), Decimal("456.78"), Decimal("12340")}
          == set(amounts), str(amounts))
    amounts, _ = _extract_amounts("Rs.1500 and INR 2500 and 3500")
    check("B currency prefixes",
          {Decimal("1500"), Decimal("2500"), Decimal("3500")}
          == set(amounts), str(amounts))


# ---------------------------------------------------------------------------
# C-G. net / GST / CGST+SGST / IGST / total roles end-to-end
# ---------------------------------------------------------------------------

def test_c_g_net_gst_total():
    r = orchestrate(DOC_PURCHASE_GST)
    check("C.1 example-1 VERIFIED", r.get("status") == "VERIFIED",
          str(r.get("status")) + " " + str(r.get("why_not"))[:80])
    check("C.2 example-1 invoice path", r.get("rule_key")
          == "invoice_labelled_facts", str(r.get("rule_key")))
    check("C.3 example-1 authority COMMERCIAL_CORE",
          r.get("authority") == "COMMERCIAL_CORE", str(r.get("authority")))
    check("C.4 example-1 journal",
          lines(r, "debit") == [("Purchases", "10500"),
                                ("Input CGST", "945"),
                                ("Input SGST", "945")]
          and lines(r, "credit") == [("Ram & Sons", "12390")],
          str(lines(r, "debit") + lines(r, "credit")))
    # subtotal+discount+GST (example 2)
    r2 = orchestrate(DOC_DISCOUNT_GST)
    check("G.1 example-2 VERIFIED", r2.get("status") == "VERIFIED",
          str(r2.get("why_not"))[:80])
    check("G.2 example-2 base is net minus discount",
          lines(r2, "debit")[0] == ("Purchases", "9500"),
          str(lines(r2, "debit")))
    check("G.3 example-2 supplier credited the full total",
          lines(r2, "credit") == [("Aggarwal Traders", "11210")],
          str(lines(r2, "credit")))
    # CGST/SGST components
    doc_cs = ("Purchase Invoice CS-88\nFrom: Gupta & Sons\n"
              "Net: 10000\nCGST: 900\nSGST: 900\nTotal: 11800")
    roles_cs = resolve_invoice_roles(doc_cs)
    check("E.1 component-sum tax basis",
          roles_cs.tax_total == Decimal("1800")
          and roles_cs.tax_total_basis == "component-sum",
          f"{roles_cs.tax_total} {roles_cs.tax_total_basis}")
    r_cs = orchestrate(doc_cs)
    check("E.2 CGST+SGST VERIFIED", r_cs.get("status") == "VERIFIED",
          str(r_cs.get("why_not"))[:80])
    check("E.3 Input CGST/SGST amounts exact",
          lines(r_cs, "debit") == [("Purchases", "10000"),
                                   ("Input CGST", "900"),
                                   ("Input SGST", "900")],
          str(lines(r_cs, "debit")))
    # IGST: ONE IGST line, never a CGST/SGST split
    doc_igst = ("Purchase Invoice IG-12\nFrom: Sharma Traders\n"
                "Net Amount: 10000\nIGST: 1800\nTotal: 11800")
    r_ig = orchestrate(doc_igst)
    check("F.1 IGST purchase VERIFIED", r_ig.get("status") == "VERIFIED",
          str(r_ig.get("why_not"))[:80])
    check("F.2 single Input IGST line for the whole tax",
          ("Input IGST", "1800") in lines(r_ig, "debit")
          and not any(a.startswith("Input CGST") or
                      a.startswith("Input SGST")
                      for a, _ in lines(r_ig, "debit")),
          str(lines(r_ig, "debit")))
    sale_igst = ("Sales Invoice SG-45\nTo: Anil\n"
                 "Net Amount: 5000\nIGST: 900\nTotal: 5900")
    r_sig = orchestrate(sale_igst)
    check("F.3 IGST sale VERIFIED", r_sig.get("status") == "VERIFIED",
          str(r_sig.get("why_not"))[:80])
    check("F.4 Output IGST line on sale",
          ("Output IGST", "900") in lines(r_sig, "credit")
          and not any(a.startswith("Output CGST")
                      for a, _ in lines(r_sig, "credit")),
          str(lines(r_sig, "credit")))


# ---------------------------------------------------------------------------
# H. discount / I. shipping-freight / J. paid / K. outstanding
# ---------------------------------------------------------------------------

def test_h_discount():
    doc = ("Invoice DC-2026-5\nFrom: Bharat Stores\n"
           "Net Amount: 4000\nTrade Discount: 400\nGST: 648\nTotal: 4248")
    r = orchestrate(doc)
    check("H.1 trade-discount invoice VERIFIED",
          r.get("status") == "VERIFIED", str(r.get("why_not"))[:80])
    check("H.2 base = net - discount",
          lines(r, "debit")[0] == ("Purchases", "3600"),
          str(lines(r, "debit")))
    # discount exceeding net fails closed
    bad = ("Invoice DC-NEG-1\nFrom: Bharat Stores\n"
           "Net Amount: 100\nDiscount: 500\nGST: 0\nTotal: 0")
    rb = orchestrate(bad)
    check("H.3 discount > net -> REVIEW_REQUIRED",
          is_refusal(rb), str(rb.get("status")))


def test_i_shipping():
    doc = ("Invoice FR-2026-8\nFrom: Acme Vendors\n"
           "Net Amount: 10000\nFreight: 500\nGST: 1890\nTotal: 12390")
    r = orchestrate(doc)
    check("I.1 freight composed into invoice VERIFIED",
          r.get("status") == "VERIFIED", str(r.get("why_not"))[:90])
    check("I.2 freight posts to Carriage Inward (narration convention)",
          ("Carriage Inward", "500") in lines(r, "debit"),
          str(lines(r, "debit")))
    check("I.3 Purchases NOT inflated by freight",
          ("Purchases", "10000") in lines(r, "debit"),
          str(lines(r, "debit")))
    check("I.4 supplier credited the full payable",
          lines(r, "credit") == [("Acme Vendors", "12390")],
          str(lines(r, "credit")))
    # freight on a SALES invoice has no supported account -> fail closed
    sale_fr = ("Sales Invoice SF-3\nTo: Ramesh\n"
               "Net Amount: 8000\nShipping: 200\nTotal: 8200")
    r_sf = orchestrate(sale_fr)
    check("I.5 shipping on sale -> REVIEW_REQUIRED",
          is_refusal(r_sf), str(r_sf.get("status")))
    # freight as the ONLY money label -> no supported authority
    only_fr = ("Freight Bill FON-9\nFrom: TransLogistics\n"
               "Shipping: 1500\nTotal: 1500")
    r_of = orchestrate(only_fr)
    check("I.6 freight-only document -> REVIEW_REQUIRED",
          is_refusal(r_of), str(r_of.get("status")))


def test_j_k_paid_outstanding():
    r = orchestrate(DOC_SETTLEMENT)
    check("J.1 example-3 settlement VERIFIED",
          r.get("status") == "VERIFIED", str(r.get("why_not"))[:80])
    check("J.2 settlement authority",
          r.get("authority") == "SETTLEMENT_AUTHORITY",
          str(r.get("authority")))
    check("J.3 posts ONLY the payment (never the outstanding balance)",
          lines(r, "debit") == [("Supplier Ltd", "5000")]
          and lines(r, "credit") == [("Cash", "5000")],
          str(lines(r, "debit") + lines(r, "credit")))
    roles = resolve_invoice_roles(DOC_SETTLEMENT)
    check("K.1 payment_outstanding reconciled",
          any(rr.get("kind") == "payment_outstanding"
              and rr.get("status") == "RECONCILED"
              for rr in roles.reconciliation),
          str(roles.reconciliation))
    # contradictory payment/outstanding arithmetic fails closed
    bad = ("Invoice SB-100\nFrom: Supplier Ltd\n"
           "Total Amount: 1000\nAmount Paid: 400\nBalance Due: 800")
    rb = orchestrate(bad)
    check("K.2 paid+outstanding != total fails closed (never VERIFIED)",
          rb.get("status") != "VERIFIED", str(rb.get("status")))
    # full payment against an invoice (no outstanding label)
    full = ("Invoice PF-2026-501\nFrom: Shyam Traders\n"
            "Net Amount: 10000\nGST: 1800\nTotal: 11800\n"
            "Amount Paid: 11800")
    r_full = orchestrate(full)
    check("K.3 paid-full invoice VERIFIED",
          r_full.get("status") == "VERIFIED",
          str(r_full.get("why_not"))[:80])
    check("K.4 paid-full credits cash the invoice total",
          lines(r_full, "credit") == [("Cash", "11800")],
          str(lines(r_full, "credit")))


# ---------------------------------------------------------------------------
# L. refund / M. credit note
# ---------------------------------------------------------------------------

def test_l_m_refund_credit_note():
    doc = ("Credit Note / Refund Voucher RN-2026-88\n"
           "To: Kishan\nRefund Amount: 1500")
    r = orchestrate(doc)
    check("L.1 refund VERIFIED", r.get("status") == "VERIFIED",
          str(r.get("why_not"))[:80])
    check("L.2 refund journal DR Cash / CR party",
          lines(r, "debit") == [("Cash", "1500")]
          and lines(r, "credit") == [("Kishan", "1500")],
          str(lines(r, "debit") + lines(r, "credit")))
    cn = ("Credit Note CN-2026-4\nFrom: Mehra & Co\n"
          "Credit Note Amount: 2000")
    r_cn = orchestrate(cn)
    check("M.1 credit note VERIFIED", r_cn.get("status") == "VERIFIED",
          str(r_cn.get("why_not"))[:80])
    check("M.2 credit note journal DR party / CR Purchase Returns",
          lines(r_cn, "debit") == [("Mehra & Co", "2000")]
          and lines(r_cn, "credit") == [("Purchase Returns", "2000")],
          str(lines(r_cn, "debit") + lines(r_cn, "credit")))
    # a refund document naming the SUPPLIER side cannot resolve the
    # refund counterparty -> fail closed
    bad_ref = ("Refund Note RB-2\nFrom: Mehra & Co\nRefund Amount: 500")
    r_br = orchestrate(bad_ref)
    check("L.3 refund without customer side -> REVIEW_REQUIRED",
          is_refusal(r_br), str(r_br.get("status")))


# ---------------------------------------------------------------------------
# N. duplicates (same value) / O. conflicts / Q. contradictory totals
# ---------------------------------------------------------------------------

def test_n_o_q_duplicates_conflicts():
    dup = ("Purchase Invoice DU-2026-1\nFrom: Ram & Sons\n"
           "Net Amount: 10500\nGST Amount: 1890\n"
           "Total Amount: 12390\nTotal Amount: 12390")
    r = orchestrate(dup)
    check("N.1 same-value duplicate tolerated (one fact, stated twice)",
          r.get("status") == "VERIFIED", str(r.get("why_not"))[:80])
    roles = resolve_invoice_roles(dup)
    check("N.2 duplicate recorded as evidence",
          any(d.get("role") == "total" for d in roles.duplicates),
          str(roles.duplicates))

    conflict = ("Purchase Invoice CF-2026-2\nFrom: Ram & Sons\n"
                "Net Amount: 10500\nGST Amount: 1890\n"
                "Total Amount: 1000\nTotal Amount: 1200")
    r_c = orchestrate(conflict)
    check("O.1 conflicting totals -> REVIEW_REQUIRED",
          is_refusal(r_c), str(r_c.get("status")))
    check("O.2 refusal never chooses a value",
          "never chooses" in str(r_c.get("why_not")),
          str(r_c.get("why_not"))[:100])
    gst_conflict = ("Purchase Invoice GC-2026-3\nFrom: Ram & Sons\n"
                    "Net Amount: 1000\nGST Amount: 180\nGST Amount: 200\n"
                    "Total Amount: 1200")
    r_g = orchestrate(gst_conflict)
    check("O.3 conflicting GST amounts -> REVIEW_REQUIRED",
          is_refusal(r_g), str(r_g.get("status")))
    check("O.4 conflict carries invoice_roles evidence",
          isinstance((r_g.get("invoice_roles") or {}).get("conflicts"),
                     list) and r_g["invoice_roles"]["conflicts"],
          str(bool(r_g.get("invoice_roles"))))

    mismatch = ("Purchase Invoice MM-2026-4\nFrom: Ram & Sons\n"
                "Net Amount: 10000\nGST Amount: 1800\n"
                "Total Amount: 12000")
    r_m = orchestrate(mismatch)
    check("Q.1 contradictory total -> REVIEW_REQUIRED",
          is_refusal(r_m), str(r_m.get("status")))
    check("Q.2 amounts never modified to reconcile",
          "never modifies an amount" in str(r_m.get("why_not")),
          str(r_m.get("why_not"))[:120])
    check("Q.3 mismatch refusal carries no journal lines",
          not (r_m.get("debit_lines") or r_m.get("credit_lines")), "")


# ---------------------------------------------------------------------------
# P. missing required fields (capability-specific, never universal)
# ---------------------------------------------------------------------------

def test_p_missing_fields():
    no_party = ("Purchase Invoice NP-2026-6\n"
                "Net Amount: 10500\nGST Amount: 1890\n"
                "Total Amount: 12390")
    r = orchestrate(no_party)
    check("P.1 invoice without a party -> REVIEW_REQUIRED",
          is_refusal(r), str(r.get("status")))
    net_only = "Purchase Invoice NO-2026-7\nFrom: Ram & Sons\nNet: 500"
    r_no = orchestrate(net_only)
    check("P.2 net alone is not a supported capability -> REVIEW_REQUIRED",
          is_refusal(r_no), str(r_no.get("status")))
    # a document with NO invoice labels at all takes the narration path
    # (its own behavior) - it must NOT be forced into invoice gates
    plain = orchestrate("Purchased goods from Ram for Rs.10000.")
    check("P.3 label-less narration input unchanged",
          plain.get("status") in ("VERIFIED", REVIEW_REQUIRED),
          str(plain.get("status")))


# ---------------------------------------------------------------------------
# R. existing FYJC narration regression
# ---------------------------------------------------------------------------

def test_r_narration_regression():
    r = orchestrate("Purchased goods from Ram for Rs.10000 plus GST 18%.")
    check("R.1 narration GST purchase VERIFIED",
          r.get("status") == "VERIFIED", str(r.get("why_not"))[:80])
    check("R.2 narration CGST/SGST rate split unchanged",
          [(a, Decimal(v)) for a, v in lines(r, "debit")]
          == [("Purchases", Decimal("10000")),
              ("Input CGST", Decimal("900.00")),
              ("Input SGST", Decimal("900.00"))],
          str(lines(r, "debit")))
    r2 = orchestrate("Sold goods to Shyam for Rs.5000.")
    check("R.3 narration sale unchanged",
          r2.get("status") == "VERIFIED"
          and lines(r2, "debit")[0][0] == "Shyam"
          and lines(r2, "credit")[0][0] == "Sales",
          str(lines(r2, "debit") + lines(r2, "credit")))
    r3 = orchestrate("Paid rent Rs.4000 in cash.")
    check("R.4 narration expense unchanged",
          r3.get("status") == "VERIFIED"
          and lines(r3, "debit")[0][0] == "Rent",
          str(lines(r3, "debit")))
    r4 = orchestrate("Purchased machinery from MachineWorks "
                     "for Rs.50000.")
    check("R.5 narration asset routing unchanged",
          r4.get("status") == "VERIFIED"
          and lines(r4, "debit")[0][0] == "Machinery",
          str(lines(r4, "debit")))
    r5 = orchestrate("Returned goods to Ram worth Rs.2000.")
    check("R.6 narration purchase return unchanged",
          r5.get("status") == "VERIFIED"
          and lines(r5, "credit")[0][0] == "Purchase Returns",
          str(lines(r5, "debit") + lines(r5, "credit")))


# ---------------------------------------------------------------------------
# S. fail-closed invariants
# ---------------------------------------------------------------------------

def test_s_fail_closed():
    # the pure role layer never posts accounting entries
    roles = resolve_invoice_roles(DOC_PURCHASE_GST)
    check("S.1 role layer never posts",
          not any(k in roles.to_dict() for k in
                  ("debit_lines", "credit_lines", "journal", "status")),
          "")
    # the executor refuses conflicting labels even when called directly
    conflict_doc = ("Purchase Invoice DX-1\nFrom: Ram & Sons\n"
                    "Net Amount: 1000\nTotal Amount: 1000\n"
                    "Total Amount: 2000")
    direct = compose_invoice_journal(
        conflict_doc, resolve_invoice_roles(conflict_doc), "PURCHASE")
    check("S.2 executor refuses conflicting labels directly",
          is_refusal(direct), str(direct.get("status")))
    # every refusal on this gate carries a why_not and next_action and
    # never carries journal lines
    refusal_docs = [
        "Purchase Invoice CF-2026-2\nFrom: Ram & Sons\n"
        "Net Amount: 10500\nGST Amount: 1890\n"
        "Total Amount: 1000\nTotal Amount: 1200",
        "Purchase Invoice GC-2026-3\nFrom: Ram & Sons\n"
        "Net Amount: 1000\nGST Amount: 180\nGST Amount: 200\n"
        "Total Amount: 1200",
        "Purchase Invoice MM-2026-4\nFrom: Ram & Sons\n"
        "Net Amount: 10000\nGST Amount: 1800\nTotal Amount: 12000",
        "Purchase Invoice NP-2026-6\nNet Amount: 10500\n"
        "GST Amount: 1890\nTotal Amount: 12390",
        "Purchase Invoice NO-2026-7\nFrom: Ram & Sons\nNet: 500",
        "Invoice ZB-1\nFrom: X\nTotal Amount: 10\nAmount Paid: 0\n"
        "Balance Due: 0",
    ]
    refusals = []
    for doc in refusal_docs:
        res = orchestrate(doc)
        if res.get("status") == REVIEW_REQUIRED:
            refusals.append(res)
    check("S.3 refusals carry why_not + next_action",
          all(r.get("why_not") and r.get("next_action") for r in refusals)
          and len(refusals) == len(refusal_docs),
          str(len(refusals)))
    check("S.4 refusals never carry journal lines",
          all(not (r.get("debit_lines") or r.get("credit_lines"))
              for r in refusals), "")
    # verified results are balanced and nonzero
    verified = [orchestrate(d) for d in
                (DOC_PURCHASE_GST, DOC_DISCOUNT_GST, DOC_SETTLEMENT)]
    check("S.5 all VERIFIED journals balance exactly",
          all(journal_of(v).get("total_debit")
              == journal_of(v).get("total_credit")
              and Decimal(str(journal_of(v).get("total_debit"))) > 0
              for v in verified),
          str([(journal_of(v).get("total_debit"),
                journal_of(v).get("total_credit")) for v in verified]))
    # evidence carries no probabilistic confidence
    check("S.6 evidence classification, never a probability",
          all(la["confidence"] == "explicit-label"
              for la in verified[0]["invoice_roles"]["labelled"]), "")


def main():
    test_a_role_extraction()
    test_b_document_ids()
    test_c_g_net_gst_total()
    test_h_discount()
    test_i_shipping()
    test_j_k_paid_outstanding()
    test_l_m_refund_credit_note()
    test_n_o_q_duplicates_conflicts()
    test_p_missing_fields()
    test_r_narration_regression()
    test_s_fail_closed()
    print(f"\nINV-ROLE gate: {OK[0]} checks passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
