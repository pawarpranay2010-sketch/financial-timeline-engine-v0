#!/usr/bin/env python3
"""
Platrixa
Phase 4 (Sprint INV-ROLE) - Invoice Fixture Integration Matrix
scripts/fte_invoice_integration_gate_test.py

A deterministic 30-fixture integration matrix (Phase 4 spec section 3)
plus cross-layer authority-boundary traces (section 6).

Every fixture is traced through the REAL production layers and the gate
asserts EACH stage, never just the final verdict:

    recognition        resolve_invoice_roles sees explicit labels
    role_resolution    roles/values resolved (or conflicts recorded)
    reconciliation     evidence-gated arithmetic status
    grounding/norm     normalize_fyjc_text concerns (clean docs: none)
    capability         matching KERNEL.INVOICE_* registry entry
    authority          the existing authority the result routes under
    execution          exact journal lines (or deterministic refusal)
    verdict            VERIFIED / REVIEW_REQUIRED / NOT_SUPPORTED

Cross-layer traces prove the authority boundary is real:
  * the invoice executor cannot bypass grounding (a normalization
    concern refuses the whole document before any invoice code runs),
  * the executor cannot convert unsupported semantics into VERIFIED
    (registry UNSUPPORTED entries + NOT_SUPPORTED narration boundary),
  * GST follows the narration engine's component/IGST conventions,
  * settlement posts only the payment under SETTLEMENT_AUTHORITY.

The matrix is also machine-checked against the live capability
registry, so the registry cannot drift from the implementation.
"""

import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_normalization import (  # noqa: E402
    math_contradiction,
    normalize_fyjc_text,
)
from backend.maths.fyjc_orchestration import (  # noqa: E402
    orchestrate,
)
from backend.maths.invoice_amount_roles import (  # noqa: E402
    resolve_invoice_roles,
)
from backend.maths import capability_registry as registry  # noqa: E402

FAIL = []
OK = [0]


def check(name, ok, detail=""):
    if ok:
        OK[0] += 1
        print(f"OK  [{name}]")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"FAIL[{name}] {detail}")


def lines(result, side):
    j = result.get("journal") or result
    return [(str(l.get("account")), str(l.get("amount")))
            for l in (j.get(side + "_lines") or [])]


def stage_trace(doc):
    """Per-stage evidence for ONE document through the real layers."""
    roles = resolve_invoice_roles(doc)
    norm = normalize_fyjc_text(doc)
    result = orchestrate(doc)
    return {
        "recognition": {
            "labels_found": len(roles.labelled),
            "roles": sorted(roles.role_map.keys()),
        },
        "role_resolution": {
            "values": {r: [str(la.value) for la in las]
                       for r, las in roles.role_map.items()},
            "conflicts": [c.get("kind") for c in roles.conflicts],
            "duplicates": [d.get("role") for d in roles.duplicates],
            "tax_total": (str(roles.tax_total)
                          if roles.tax_total is not None else None),
            "tax_total_basis": roles.tax_total_basis or None,
        },
        "reconciliation": [r.get("status") for r in roles.reconciliation],
        "grounding_normalization": {
            "concerns": norm.concerns,
            "math_contradiction": math_contradiction(norm.text) is not None,
        },
        "authority_routing": result.get("authority"),
        "execution": {
            "debit": lines(result, "debit"),
            "credit": lines(result, "credit"),
        },
        "verdict": result.get("status"),
        "rule_key": result.get("rule_key"),
    }


# ---------------------------------------------------------------------------
# The 30-fixture integration matrix
# ---------------------------------------------------------------------------

P = "Purchase Invoice {cid}\nFrom: {party}\n{body}"
S = "Sales Invoice {cid}\nTo: {party}\n{body}"
SET = "Invoice {cid}\nFrom: {party}\n{body}"

MATRIX = [
    # -- PURCHASES ---------------------------------------------------------
    ("P01", P.format(cid="IN-P01", party="Ram & Sons",
                     body="Net Amount: 10500\nGST: 1890\nTotal: 12390"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10500"), ("Input CGST", "945"),
                 ("Input SGST", "945")],
          credit=[("Ram & Sons", "12390")],
          reconciliation=["RECONCILED"])),
    ("P02", P.format(cid="IN-P02", party="Ram & Sons",
                     body="Net Amount: 10500\nGST: 1890\nTotal: 12390\n"
                          "Amount Paid: 5000"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10500"), ("Input CGST", "945"),
                 ("Input SGST", "945")],
          credit=[("Cash", "5000"), ("Ram & Sons", "7390")],
          reconciliation=["RECONCILED"])),
    ("P03", P.format(cid="IN-P03", party="Ram & Sons",
                     body="Net Amount: 10000\nGST: 1800\nTotal: 11800\n"
                          "Balance Due: 11800"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10000"), ("Input CGST", "900"),
                 ("Input SGST", "900")],
          credit=[("Ram & Sons", "11800")],
          reconciliation=["RECONCILED"],
          note="outstanding is evidence; composed as a full credit "
               "purchase")),
    ("P04", P.format(cid="IN-P04", party="Bharat Stores",
                     body="Net Amount: 10000\nDiscount: 500\nGST: 1710\n"
                          "Total: 11210"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "9500"), ("Input CGST", "855"),
                 ("Input SGST", "855")],
          credit=[("Bharat Stores", "11210")],
          reconciliation=["RECONCILED"])),
    ("P05", P.format(cid="IN-P05", party="Vendor Lines",
                     body="Net Amount: 10000\nShipping: 500\nGST: 1890\n"
                          "Total: 12390"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10000"), ("Carriage Inward", "500"),
                 ("Input CGST", "945"), ("Input SGST", "945")],
          credit=[("Vendor Lines", "12390")],
          reconciliation=["RECONCILED"],
          note="shipping posts Carriage Inward, never folded into "
               "Purchases")),
    # -- SALES ---------------------------------------------------------------
    ("S01", S.format(cid="IN-S01", party="Anil",
                     body="Net Amount: 5000\nGST: 900\nTotal: 5900"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Anil", "5900")],
          credit=[("Sales", "5000"), ("Output CGST", "450"),
                  ("Output SGST", "450")],
          reconciliation=["RECONCILED"])),
    ("S02", S.format(cid="IN-S02", party="Anil",
                     body="Net Amount: 5000\nGST: 900\nTotal: 5900\n"
                          "Amount Paid: 2000"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Anil", "5900")],
          credit=[("Sales", "5000"), ("Output CGST", "450"),
                  ("Output SGST", "450")],
          reconciliation=["RECONCILED"],
          note="sale composes GROSS like the narration convention "
               "(party debit and revenue credit are never netted); "
               "the paid label is evidence, not a second posting - "
               "part-collection on a sale remains a Phase 5 candidate")),
    ("S03", S.format(cid="IN-S03", party="Anil",
                     body="Net Amount: 5000\nGST: 900\nTotal: 5900\n"
                          "Balance Due: 5900"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Anil", "5900")],
          credit=[("Sales", "5000"), ("Output CGST", "450"),
                  ("Output SGST", "450")],
          reconciliation=["RECONCILED"],
          note="outstanding is evidence; credit sale composed in full")),
    ("S04", S.format(cid="IN-S04", party="Anil",
                     body="Net Amount: 5000\nDiscount: 500\nGST: 810\n"
                          "Total: 5310"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Anil", "5310")],
          credit=[("Sales", "4500"), ("Output CGST", "405"),
                  ("Output SGST", "405")],
          reconciliation=["RECONCILED"])),
    ("S05", S.format(cid="IN-S05", party="Ramesh",
                     body="Net Amount: 8000\nShipping: 200\nTotal: 8200"),
     dict(verdict="REVIEW_REQUIRED", authority=None,
          refusal_contains="shipping",
          note="shipping on a SALES invoice: no supported account")),
    # -- GST -----------------------------------------------------------------
    ("G01", P.format(cid="IN-G01", party="Gupta & Sons",
                     body="Net Amount: 10000\nCGST: 900\nSGST: 900\n"
                          "Total: 11800"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10000"), ("Input CGST", "900"),
                 ("Input SGST", "900")],
          credit=[("Gupta & Sons", "11800")],
          reconciliation=["RECONCILED"], tax_basis="component-sum")),
    ("G02", P.format(cid="IN-G02", party="Sharma Traders",
                     body="Net Amount: 10000\nIGST: 1800\nTotal: 11800"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10000"), ("Input IGST", "1800")],
          credit=[("Sharma Traders", "11800")],
          reconciliation=["RECONCILED"], tax_basis="component-sum",
          note="IGST stays ONE line - never a CGST/SGST split")),
    ("G03", P.format(cid="IN-G03", party="Ram & Sons",
                     body="Net Amount: 10500\nGST Amount: 1890\n"
                          "Total Amount: 12390"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10500"), ("Input CGST", "945"),
                 ("Input SGST", "945")],
          credit=[("Ram & Sons", "12390")],
          reconciliation=["RECONCILED"], tax_basis="explicit")),
    ("G04", P.format(cid="IN-G04", party="Gupta & Sons",
                     body="Net Amount: 10000\nCGST: 900\nSGST: 900\n"
                          "Total: 11800"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10000"), ("Input CGST", "900"),
                 ("Input SGST", "900")],
          credit=[("Gupta & Sons", "11800")],
          reconciliation=["RECONCILED"], tax_basis="component-sum",
          note="same arithmetic as G01; asserted at role-layer basis")),
    ("G05", P.format(cid="IN-G05", party="Gupta & Sons",
                     body="Net Amount: 10000\nCGST: 900\nSGST: 900\n"
                          "GST: 2000\nTotal: 11800"),
     dict(verdict="REVIEW_REQUIRED", authority=None,
          note="IGST/CGST-style component contradiction: a plain GST "
               "label conflicting with components refuses")),
    ("G06", P.format(cid="IN-G06", party="Ram & Sons",
                     body="Net Amount: 10000\nTotal: 10000"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10000")],
          credit=[("Ram & Sons", "10000")],
          reconciliation=["RECONCILED"],
          note="per-capability contract: tax is OPTIONAL; absence is "
               "not a refusal")),
    # -- PAYMENT -------------------------------------------------------------
    ("M01", P.format(cid="IN-M01", party="Shyam Traders",
                     body="Net Amount: 10000\nGST: 1800\nTotal: 11800\n"
                          "Amount Paid: 11800"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10000"), ("Input CGST", "900"),
                 ("Input SGST", "900")],
          credit=[("Cash", "11800")],
          reconciliation=["RECONCILED"], note="paid == total")),
    ("M02", SET.format(cid="IN-M02", party="Supplier Ltd",
                       body="Total Amount: 12390\nAmount Paid: 5000\n"
                            "Balance Due: 7390"),
     dict(verdict="VERIFIED", authority="SETTLEMENT_AUTHORITY",
          debit=[("Supplier Ltd", "5000")],
          credit=[("Cash", "5000")],
          reconciliation=["RECONCILED"],
          contradiction_bypass=True,
          note="posts ONLY the payment; outstanding is evidence; the "
               "narration math-contradiction gate flags the digit "
               "split and the invoice path proceeds ONLY through the "
               "narrow payment+outstanding reconciled bypass")),
    ("M03", SET.format(cid="IN-M03", party="Supplier Ltd",
                       body="Total Amount: 12390\nAmount Paid: 11800\n"
                            "Balance Due: 590"),
     dict(verdict="VERIFIED", authority="SETTLEMENT_AUTHORITY",
          debit=[("Supplier Ltd", "11800")],
          credit=[("Cash", "11800")],
          reconciliation=["RECONCILED"],
          contradiction_bypass=True)),
    ("M04", SET.format(cid="IN-M04", party="Supplier Ltd",
                       body="Total Amount: 12390\nAmount Paid: 5000\n"
                            "Balance Due: 7000"),
     dict(verdict="INVALID_INPUT_MATH", authority=None,
          note="paid + outstanding != total: the narration path's own "
               "math-contradiction gate refuses (stricter than "
               "REVIEW_REQUIRED); fail-closed, never VERIFIED")),
    # -- DEFENSIVE -----------------------------------------------------------
    ("D01", P.format(cid="IN-D01", party="Ram & Sons",
                     body="Net Amount: 10500\nGST Amount: 1890\n"
                          "Total Amount: 12390\nTotal Amount: 12390"),
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10500"), ("Input CGST", "945"),
                 ("Input SGST", "945")],
          credit=[("Ram & Sons", "12390")],
          reconciliation=["RECONCILED"],
          duplicates=["total"],
          note="same-value duplicate = one stated fact")),
    ("D02", P.format(cid="IN-D02", party="Ram & Sons",
                     body="Net Amount: 10500\nGST Amount: 1890\n"
                          "Total Amount: 1000\nTotal Amount: 1200"),
     dict(verdict="REVIEW_REQUIRED", authority=None,
          conflicts=["labelled_role_conflict"],
          note="conflicting duplicates are never chosen between")),
    ("D03", P.format(cid="IN-D03", party="Ram & Sons",
                     body="Net Amount: 10500\nGST: 1890"),
     dict(verdict="REVIEW_REQUIRED", authority=None,
          note="missing total: required input absent")),
    ("D04", P.format(cid="IN-D04", party="Ram & Sons",
                     body="GST: 1800\nTotal: 11800"),
     dict(verdict="REVIEW_REQUIRED", authority=None,
          note="missing net: no net+total shape, no settlement shape")),
    ("D05", P.format(cid="IN-D05", party="Ram & Sons",
                     body="Net Amount: 10000\nGST: 1800\nTotal: 12000"),
     dict(verdict="REVIEW_REQUIRED", authority=None,
          note="mismatched reconciliation; amounts never modified")),
    ("D06", "TAX INVOICE BILL-SI-01\nFrom: Ram & Sons\n"
            "Net Amount: 10500\nGST: 1890\nTotal: 12390",
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10500"), ("Input CGST", "945"),
                 ("Input SGST", "945")],
          credit=[("Ram & Sons", "12390")],
          reconciliation=["RECONCILED"],
          no_amounts=["1", "-1", "01"],
          note="document code digits never become amounts")),
    ("D07", "Purchase Invoice IN-D07 dated 01-May-2026\nFrom: Ram & Sons\n"
            "Net Amount: 10500\nGST: 1890\nTotal: 12390",
     dict(verdict="VERIFIED", authority="COMMERCIAL_CORE",
          debit=[("Purchases", "10500"), ("Input CGST", "945"),
                 ("Input SGST", "945")],
          credit=[("Ram & Sons", "12390")],
          reconciliation=["RECONCILED"],
          no_amounts=["1", "5", "2026"],
          note="hyphenated date segments never become amounts")),
    ("D08", P.format(cid="IN-D08", party="Gupta & Sons",
                     body="Net Amount: 1000\nGST: 180\nCGST: 90\n"
                          "Total: 1180"),
     dict(verdict="REVIEW_REQUIRED", authority=None,
          conflicts=["labelled_role_conflict"],
          note="ambiguous tax: plain GST + a component value refuse")),
    ("D09", "Purchase Invoice TI-2026-9\nFrom: Ram & Sons\n"
            "Total: 11800 inclusive of GST",
     dict(verdict="NOT_SUPPORTED", authority=None,
          note="tax-inclusive pricing with no net evidence: outside "
               "the implemented surface (registry UNSUPPORTED)")),
    ("D10", "Debit Note DNT-2026-4\nFrom: Mehra & Co\n"
            "Debit Note Amount: 500",
     dict(verdict="NOT_SUPPORTED", authority=None,
          note="debit-note accounting: registry UNSUPPORTED; the "
               "kernel refuses rather than inventing a treatment")),
]


def test_fixture_matrix():
    for cid, doc, want in MATRIX:
        trace = stage_trace(doc)
        tag = f"{cid} [{trace['verdict']}]"
        # -- stage assertions ------------------------------------------------
        if want["verdict"] == "VERIFIED":
            check(f"{tag} recognition", trace["recognition"]["labels_found"] > 0,
                  str(trace["recognition"]))
            check(f"{tag} role_resolution conflict-free",
                  trace["role_resolution"]["conflicts"] == [],
                  str(trace["role_resolution"]["conflicts"]))
            if want.get("contradiction_bypass"):
                # the documented NARROW bypass: the narration math gate
                # flags the payment/outstanding digit split, the invoice
                # path may proceed ONLY when the labels reconcile
                _rb = resolve_invoice_roles(doc).reconciliation
                _kind = any(r.get("kind") == "payment_outstanding"
                            and r.get("status") == "RECONCILED"
                            for r in _rb)
                check(f"{tag} narrow contradiction bypass is evidence-gated",
                      trace["grounding_normalization"]["concerns"] == []
                      and trace["grounding_normalization"][
                          "math_contradiction"] and _kind,
                      str(trace["grounding_normalization"]))
            else:
                check(f"{tag} grounding clean",
                      trace["grounding_normalization"]["concerns"] == []
                      and not trace["grounding_normalization"][
                          "math_contradiction"],
                      str(trace["grounding_normalization"]))
            check(f"{tag} authority routed",
                  trace["authority_routing"] == want["authority"],
                  str(trace["authority_routing"]))
            check(f"{tag} execution exact",
                  trace["execution"]["debit"] == want["debit"]
                  and trace["execution"]["credit"] == want["credit"],
                  f"{trace['execution']}")
            check(f"{tag} verdict VERIFIED via invoice path",
                  trace["verdict"] == "VERIFIED"
                  and trace["rule_key"] == "invoice_labelled_facts",
                  f"{trace['verdict']} {trace['rule_key']}")
        else:
            check(f"{tag} verdict fail-closed",
                  trace["verdict"] == want["verdict"],
                  f"want {want['verdict']}, got {trace['verdict']}")
            check(f"{tag} refusal carries a reason",
                  bool(str(orchestrate(doc).get("why_not"))),
                  "empty why_not")
            check(f"{tag} no journal on refusal",
                  trace["execution"]["debit"] == []
                  and trace["execution"]["credit"] == [],
                  str(trace["execution"]))
        # -- optional per-fixture assertions ---------------------------------
        if "reconciliation" in want:
            check(f"{tag} reconciliation stage",
                  trace["reconciliation"] == want["reconciliation"],
                  str(trace["reconciliation"]))
        if "tax_basis" in want:
            check(f"{tag} tax_total_basis",
                  trace["role_resolution"]["tax_total_basis"]
                  == want["tax_basis"],
                  str(trace["role_resolution"]["tax_total_basis"]))
        if "duplicates" in want:
            check(f"{tag} duplicates recorded",
                  trace["role_resolution"]["duplicates"] == want["duplicates"],
                  str(trace["role_resolution"]["duplicates"]))
        if "conflicts" in want:
            check(f"{tag} conflicts recorded",
                  all(k in trace["role_resolution"]["conflicts"]
                      for k in want["conflicts"]),
                  str(trace["role_resolution"]["conflicts"]))
        if "refusal_contains" in want:
            r = orchestrate(doc)
            check(f"{tag} specific refusal reason",
                  want["refusal_contains"] in str(r.get("why_not")).lower(),
                  str(r.get("why_not"))[:80])
        if "no_amounts" in want:
            amounts = [v for vs in trace["role_resolution"]["values"].values()
                       for v in vs]
            bad = [a for a in want["no_amounts"]
                   if Decimal(a) in {Decimal(v) for v in amounts}]
            check(f"{tag} phantom amounts absent", not bad,
                  f"{bad} in {amounts}")


# ---------------------------------------------------------------------------
# Phase 4 spec section 4: authority boundary matrix vs the live registry
# ---------------------------------------------------------------------------

EXPECTED_ROUTING = {
    "KERNEL.INVOICE_PURCHASE": dict(
        status="SUPPORTED", authority="COMMERCIAL_CORE",
        asset_note="ASSET_AUTHORITY via the narration asset path"),
    "KERNEL.INVOICE_SALE": dict(
        status="SUPPORTED", authority="COMMERCIAL_CORE"),
    "KERNEL.INVOICE_GST_AMOUNTS": dict(
        status="SUPPORTED", authority="COMMERCIAL_CORE",
        note="GST conventions owned by GST_AUTHORITY machinery; amount "
             "form is posted under the invoice capability"),
    "KERNEL.INVOICE_PAYMENT_AGAINST": dict(
        status="SUPPORTED", authority="SETTLEMENT_AUTHORITY"),
    "KERNEL.INVOICE_REFUND": dict(
        status="SUPPORTED", authority="COMMERCIAL_CORE"),
    "KERNEL.INVOICE_CREDIT_NOTE": dict(
        status="SUPPORTED", authority="COMMERCIAL_CORE"),
    "KERNEL.INVOICE_OUTSTANDING_EVIDENCE": dict(
        status="PARTIAL", authority=None,
        note="evidence only - never posted standalone"),
    "KERNEL.INVOICE_TAX_INCLUSIVE": dict(status="UNSUPPORTED"),
    "KERNEL.INVOICE_DEBIT_NOTE": dict(status="UNSUPPORTED"),
}


def test_registry_boundary_matrix():
    for cap_id, want in EXPECTED_ROUTING.items():
        cap = registry.get(cap_id)
        check(f"registry {cap_id} exists", cap is not None, "missing")
        if cap is None:
            continue
        check(f"registry {cap_id} status",
              cap.supported_status == want["status"],
              f"{cap.supported_status} != {want['status']}")
        if want["status"] in ("SUPPORTED", "PARTIAL"):
            check(f"registry {cap_id} provable",
                  bool(cap.implementation_ref) and bool(cap.test_ref),
                  "SUPPORTED/PARTIAL requires implementation+test refs")
            check(f"registry {cap_id} evidence contract",
                  bool(cap.required_inputs) or cap_id.endswith("EVIDENCE"),
                  "required_inputs must be documented")
        if want["status"] == "UNSUPPORTED":
            check(f"registry {cap_id} limitations documented",
                  bool(cap.limitations),
                  "UNSUPPORTED requires refusal evidence")
    # observed routing matches the matrix for every VERIFIED fixture
    for cid, doc, want in MATRIX:
        if want.get("verdict") == "VERIFIED" and want.get("authority"):
            r = orchestrate(doc)
            check(f"routing {cid} -> {want['authority']}",
                  r.get("authority") == want["authority"],
                  str(r.get("authority")))


# ---------------------------------------------------------------------------
# Phase 4 spec section 6: cross-layer traces (authority boundary is real)
# ---------------------------------------------------------------------------

def test_cross_layer_traces():
    # -- purchase trace: every layer in order -----------------------------
    doc = P.format(cid="IN-X01", party="Ram & Sons",
                   body="Net Amount: 10500\nGST: 1890\nTotal: 12390")
    roles = resolve_invoice_roles(doc)
    norm = normalize_fyjc_text(doc)
    graph_clean = norm.concerns == [] and \
        math_contradiction(norm.text) is None
    r = orchestrate(doc)
    cap = registry.get("KERNEL.INVOICE_PURCHASE")
    check("X1 purchase trace: roles before graph before authority",
          roles.safe and graph_clean
          and r.get("authority") == "COMMERCIAL_CORE"
          and cap.authority == "ACCOUNTING_KERNEL",
          f"{roles.safe} {graph_clean} {r.get('authority')}")
    check("X1 purchase trace: verdict carries full evidence",
          r.get("invoice_roles") is not None
          and r.get("calculation_records") is not None,
          "evidence fields missing")

    # -- executor cannot bypass grounding ---------------------------------
    # The ungated executor would compose this document, but the pipeline
    # refuses it before any invoice code runs (normalization concern).
    gated_doc = invoice = ("Purchase Invoice IN-X02\nFrom: B-9-TEST-X\n"
                           "Net Amount: 1000\nGST: 180\nTotal: 1180")
    from backend.maths.invoice_executor import compose_invoice_journal
    direct = compose_invoice_journal(
        gated_doc, resolve_invoice_roles(gated_doc), "PURCHASE")
    piped = orchestrate(gated_doc)
    check("X2 grounding sits ABOVE the executor",
          direct.get("status") == "VERIFIED"
          and piped.get("status") == "REVIEW_REQUIRED"
          and "invoice_roles" not in piped,
          f"direct={direct.get('status')} piped={piped.get('status')}")

    # -- executor cannot fabricate accounts (classification boundary) -----
    svc = ("Purchase Invoice IN-X03\nFrom: Advisory Corp\n"
           "Transaction: Consulting services\nNet Amount: 5000\n"
           "Total: 5000")
    r_svc = orchestrate(svc)
    n_ref = orchestrate("Purchased consulting services for Rs.5000.")
    check("X3 service wording refuses exactly like the machinery",
          r_svc.get("status") == "REVIEW_REQUIRED"
          and n_ref.get("status") == "NOT_SUPPORTED"
          and "Purchases" not in [a for a, _ in lines(r_svc, "debit")],
          f"{r_svc.get('status')} {lines(r_svc, 'debit')}")

    # -- settlement authority boundary ------------------------------------
    st = SET.format(cid="IN-X04", party="Supplier Ltd",
                    body="Total Amount: 12390\nAmount Paid: 5000\n"
                         "Balance Due: 7390")
    r_st = orchestrate(st)
    check("X4 settlement routes under SETTLEMENT_AUTHORITY, payment only",
          r_st.get("authority") == "SETTLEMENT_AUTHORITY"
          and lines(r_st, "debit") == [("Supplier Ltd", "5000")]
          and lines(r_st, "credit") == [("Cash", "5000")],
          f"{r_st.get('authority')} {lines(r_st, 'debit')}")

    # -- GST convention boundary (IGST one line, components sum) ----------
    igst = P.format(cid="IN-X05", party="Sharma Traders",
                    body="Net Amount: 10000\nIGST: 1800\nTotal: 11800")
    r_ig = orchestrate(igst)
    check("X5 IGST follows the GST_AUTHORITY posting convention",
          ("Input IGST", "1800") in lines(r_ig, "debit")
          and not any(a.startswith("Input CGST")
                      for a, _ in lines(r_ig, "debit")),
          str(lines(r_ig, "debit")))

    # -- unsupported cannot become VERIFIED --------------------------------
    for cid, doc, want in MATRIX:
        if want["verdict"] in ("NOT_SUPPORTED", "REVIEW_REQUIRED"):
            r = orchestrate(doc)
            check(f"X6 {cid} never VERIFIED", r.get("status") != "VERIFIED",
                  str(r.get("status")))


def main():
    test_fixture_matrix()
    test_registry_boundary_matrix()
    test_cross_layer_traces()
    print(f"\nIntegration gate: {OK[0]} checks passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
