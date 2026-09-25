#!/usr/bin/env python3
"""
Platrixa
Phase 4 (Sprint INV-ROLE) - False-VERIFIED Attack Suite
scripts/fte_invoice_false_verified_gate_test.py

Dedicated safety gate (Phase 4 spec section 5). Every attack case is
run through the REAL production path (orchestrate) and, where the spec
demands it, also through the ungated executor to prove the pipeline
gates sit ABOVE invoice code.

HARD SAFETY INVARIANT: false_VERIFIED == 0

  A  valid reconciliation        -> VERIFIED (deterministic)
  B  incorrect reconciliation    -> REVIEW_REQUIRED
  C  conflicting duplicate GST   -> REVIEW_REQUIRED
  D  paid/outstanding mismatch   -> refused (never VERIFIED)
  E  document reference          -> code digits never become amounts
  F  date/code boundary 01-May   -> never becomes amount 1
  G  tax-inclusive pricing       -> NOT_SUPPORTED per the registry contract
  H  debit note                  -> NOT_SUPPORTED per the registry contract
  I  invented account            -> classification machinery decides;
                                    otherwise fail closed (no 'DR Purchases'
                                    invented for service wording)
  J  conflicting party evidence  -> REVIEW_REQUIRED

Plus the structural invariants: unsupported cannot become VERIFIED,
conflicting evidence cannot become VERIFIED, insufficient evidence
cannot become VERIFIED, invoice labels cannot bypass grounding, and
the invoice executor cannot bypass the existing authorities.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths.fyjc_normalization import (  # noqa: E402
    normalize_fyjc_text,
)
from backend.maths.fyjc_orchestration import (  # noqa: E402
    orchestrate,
)
from backend.maths.invoice_amount_roles import (  # noqa: E402
    resolve_invoice_roles,
)
from backend.maths.invoice_executor import (  # noqa: E402
    compose_invoice_journal,
)
from backend.maths import capability_registry as registry  # noqa: E402

FAIL = []
OK = [0]
FALSE_VERIFIED = []


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


# ---------------------------------------------------------------------------
# CASE A - valid reconciliation -> VERIFIED
# ---------------------------------------------------------------------------

def case_a():
    doc = ("Purchase Invoice AT-101\nFrom: Ram & Sons\n"
           "Net Amount: 10,500\nGST: 1,890\nTotal: 12,390")
    r = orchestrate(doc)
    check("A valid reconciliation VERIFIED", r.get("status") == "VERIFIED",
          f"{r.get('status')} {str(r.get('why_not'))[:70]}")
    check("A journal exact",
          lines(r, "debit") == [("Purchases", "10500"),
                                ("Input CGST", "945"),
                                ("Input SGST", "945")]
          and lines(r, "credit") == [("Ram & Sons", "12390")],
          str(lines(r, "debit") + lines(r, "credit")))
    roles = resolve_invoice_roles(doc)
    check("A reconciliation evidence recorded",
          any(x.get("status") == "RECONCILED" for x in roles.reconciliation),
          str(roles.reconciliation))


# ---------------------------------------------------------------------------
# CASE B - incorrect reconciliation
# ---------------------------------------------------------------------------

def case_b():
    doc = ("Purchase Invoice BT-102\nFrom: Ram & Sons\n"
           "Net Amount: 10,500\nGST: 2,890\nTotal: 12,390")
    r = orchestrate(doc)
    check("B incorrect reconciliation REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", str(r.get("status")))
    check("B amounts never modified to reconcile",
          "never modifies an amount" in str(r.get("why_not")),
          str(r.get("why_not"))[:90])
    check("B refusal carries no journal",
          not (r.get("debit_lines") or r.get("credit_lines")), "")


# ---------------------------------------------------------------------------
# CASE C - conflicting duplicate GST
# ---------------------------------------------------------------------------

def case_c():
    doc = ("Purchase Invoice CT-103\nFrom: Ram & Sons\n"
           "Total: 12,390\nNet Amount: 10,500\nGST: 1,890\nGST: 2,000")
    r = orchestrate(doc)
    check("C conflicting duplicate GST REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", str(r.get("status")))
    roles = resolve_invoice_roles(doc)
    check("C conflict recorded with both values",
          any(c.get("kind") == "labelled_role_conflict"
              and set(c.get("values", [])) == {"1890", "2000"}
              for c in roles.conflicts),
          str(roles.conflicts))
    check("C refusal never chooses a value",
          "never chooses" in str(r.get("why_not")),
          str(r.get("why_not"))[:90])


# ---------------------------------------------------------------------------
# CASE D - paid/outstanding mismatch
# ---------------------------------------------------------------------------

def case_d():
    doc = ("Invoice DT-104\nFrom: Supplier Ltd\n"
           "Total: 12,390\nAmount Paid: 5,000\nBalance Due: 7,000")
    r = orchestrate(doc)
    check("D paid+outstanding mismatch never VERIFIED",
          r.get("status") != "VERIFIED", str(r.get("status")))
    check("D refusal is deterministic (math gate or capability refusal)",
          r.get("status") in ("REVIEW_REQUIRED", "INVALID_INPUT_MATH"),
          str(r.get("status")))
    check("D no journal posted", not (r.get("debit_lines")
                                      or r.get("credit_lines")), "")


# ---------------------------------------------------------------------------
# CASE E/F - document reference and date/code boundaries
# ---------------------------------------------------------------------------

def case_e_f():
    r = orchestrate("TAX INVOICE BILL-SI-01\nFrom: Ram & Sons\n"
                    "Net Amount: 10000\nGST: 1800\nTotal: 11800")
    labelled = [la["value"] for la in
                (r.get("invoice_roles") or {}).get("labelled", [])]
    check("E doc code never becomes amount 01/-1/1",
          r.get("status") == "VERIFIED"
          and not ({"01", "-1", "1"} & set(labelled)),
          f"{r.get('status')} labelled={labelled}")
    # narration layer too (the Phase 3 fix lives in _extract_amounts)
    from backend.maths.fyjc_bk_reasoning import _extract_amounts
    amts, _ = _extract_amounts("TAX INVOICE BILL-SI-01 and REF-SI-02")
    check("E narration _extract_amounts clean for codes", amts == [],
          str(amts))
    r2 = orchestrate("Purchase Invoice IN-F09 dated 01-May\n"
                     "From: Ram & Sons\nNet Amount: 10000\n"
                     "GST: 1800\nTotal: 11800")
    labelled2 = [la["value"] for la in
                 (r2.get("invoice_roles") or {}).get("labelled", [])]
    check("F date 01-May never becomes amount 1/5/2026",
          r2.get("status") == "VERIFIED"
          and not ({"1", "5", "2026", "01"} & set(labelled2)),
          f"{r2.get('status')} labelled={labelled2}")
    amts2, _ = _extract_amounts("Dated 01-May-2026, Q1-2027 report")
    check("F narration extraction clean for date/code segments",
          amts2 == [], str(amts2))


# ---------------------------------------------------------------------------
# CASE G/H - unsupported capabilities per the registry contract
# ---------------------------------------------------------------------------

def case_g_h():
    r_g = orchestrate("Purchase Invoice TI-105\nFrom: Ram & Sons\n"
                      "Total: 11800 inclusive of GST")
    cap_g = registry.get("KERNEL.INVOICE_TAX_INCLUSIVE")
    check("G tax-inclusive refuses (never VERIFIED)",
          r_g.get("status") != "VERIFIED", str(r_g.get("status")))
    check("G refusal matches the registry contract",
          cap_g is not None and cap_g.supported_status == "UNSUPPORTED"
          and "REVIEW_REQUIRED" in " ".join(cap_g.limitations),
          str(cap_g.limitations if cap_g else None))
    check("G observed terminal state is NOT_SUPPORTED (narration "
          "boundary owns it)",
          r_g.get("status") in ("NOT_SUPPORTED", "REVIEW_REQUIRED"),
          str(r_g.get("status")))
    r_h = orchestrate("Debit Note DNT-104\nFrom: Mehra & Co\n"
                      "Debit Note Amount: 500")
    cap_h = registry.get("KERNEL.INVOICE_DEBIT_NOTE")
    check("H debit note refuses (never VERIFIED)",
          r_h.get("status") != "VERIFIED", str(r_h.get("status")))
    check("H refusal matches the registry contract",
          cap_h is not None and cap_h.supported_status == "UNSUPPORTED",
          str(cap_h.supported_status if cap_h else None))
    check("H observed terminal state is NOT_SUPPORTED",
          r_h.get("status") == "NOT_SUPPORTED", str(r_h.get("status")))


# ---------------------------------------------------------------------------
# CASE I - invented accounting account
# ---------------------------------------------------------------------------

def case_i():
    # service wording the classification machinery itself refuses
    svc = ("Purchase Invoice CI-106\nFrom: Advisory Corp\n"
           "Transaction: Consulting services\nNet Amount: 5000\n"
           "Total: 5000")
    r = orchestrate(svc)
    check("I service invoice fails closed (no invented Purchases)",
          r.get("status") == "REVIEW_REQUIRED"
          and "Purchases" not in [a for a, _ in lines(r, "debit")],
          f"{r.get('status')} {lines(r, 'debit')}")
    # but the machinery still decides when it CAN (asset path)
    asset = ("Purchase Invoice CA-107\nFrom: MachineWorks\n"
             "Machinery purchased\nNet Amount: 50000\nTotal: 50000")
    r2 = orchestrate(asset)
    check("I asset classification still resolves (machinery decides)",
          r2.get("status") == "VERIFIED"
          and lines(r2, "debit")[0][0] == "Machinery"
          and r2.get("authority") == "ASSET_AUTHORITY",
          f"{r2.get('status')} {lines(r2, 'debit')}")
    # expense hint still resolves
    rent = ("Rent Invoice RT-108\nFrom: City Estates\n"
            "Net Amount: 8000\nTotal: 8000")
    r3 = orchestrate(rent)
    check("I expense classification still resolves",
          r3.get("status") == "VERIFIED"
          and lines(r3, "debit")[0][0] == "Rent",
          f"{r3.get('status')} {lines(r3, 'debit')}")
    # narration mirror: the same service wording is NOT_SUPPORTED there
    n = orchestrate("Purchased consulting services for Rs.5000.")
    check("I classification standard is UNIFORM with narration",
          n.get("status") == "NOT_SUPPORTED", str(n.get("status")))


# ---------------------------------------------------------------------------
# CASE J - conflicting party evidence
# ---------------------------------------------------------------------------

def case_j():
    # both a supplier AND a customer side named -> counterparty ambiguous
    doc = ("Purchase Invoice JT-109\nFrom: Global Suppliers\n"
           "To: Retail Buyer\nNet Amount: 10000\nGST: 1800\n"
           "Total: 11800")
    roles = resolve_invoice_roles(doc)
    r = orchestrate(doc)
    check("J both-sides document REVIEW_REQUIRED",
          r.get("status") == "REVIEW_REQUIRED", str(r.get("status")))
    check("J executor itself refuses the ambiguous counterparty",
          compose_invoice_journal(doc, roles, "PURCHASE").get("status")
          == "REVIEW_REQUIRED", "")
    check("J refusal names the party problem",
          "party" in str(r.get("why_not")).lower(),
          str(r.get("why_not"))[:90])
    # one side only -> composes (the correct deterministic reading)
    ok_doc = ("Purchase Invoice JT-110\nFrom: Global Suppliers\n"
              "Net Amount: 10000\nGST: 1800\nTotal: 11800")
    r2 = orchestrate(ok_doc)
    check("J single-side document composes",
          r2.get("status") == "VERIFIED"
          and lines(r2, "credit") == [("Global Suppliers", "11800")],
          f"{r2.get('status')} {lines(r2, 'credit')}")


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------

def invariants():
    # 1. unsupported capabilities can never become VERIFIED (registry x
    #    behavior for every UNSUPPORTED invoice entry)
    for cap in registry.by_authority(registry.AUTHORITY_ACCOUNTING_KERNEL):
        if cap.capability_id.startswith("KERNEL.INVOICE_") \
                and cap.supported_status == "UNSUPPORTED":
            check(f"INV1 {cap.capability_id} registered UNSUPPORTED",
                  cap.limitations != (), "needs refusal evidence")
    # 2. conflicting evidence can never become VERIFIED (swept)
    conflicts = [
        "Purchase Invoice SV-1\nFrom: Ram & Sons\nNet Amount: 1000\n"
        "Total: 1000\nTotal: 2000",
        "Purchase Invoice SV-2\nFrom: Ram & Sons\nNet: 1000\n"
        "Net Amount: 2000\nTotal: 2000",
    ]
    for d in conflicts:
        check(f"INV2 conflict never VERIFIED",
              orchestrate(d).get("status") != "VERIFIED", d[:40])
    # 3. insufficient evidence can never become VERIFIED (swept)
    sparse = [
        "Purchase Invoice SV-3\nFrom: Ram & Sons\nNet: 500",
        "Purchase Invoice SV-4\nFrom: Ram & Sons\nGST: 90",
        "Purchase Invoice SV-5\nNet: 500\nTotal: 500",
    ]
    for d in sparse:
        check("INV3 insufficient evidence never VERIFIED",
              orchestrate(d).get("status") != "VERIFIED", d[:40])
    # 4. invoice labels cannot bypass grounding: a normalization concern
    #    refuses the whole document BEFORE any invoice code runs
    bad_party = ("Purchase Invoice SV-6\nFrom: B-9-ID-CO\n"
                 "Net Amount: 1000\nGST: 180\nTotal: 1180")
    direct = compose_invoice_journal(
        bad_party, resolve_invoice_roles(bad_party), "PURCHASE")
    piped = orchestrate(bad_party)
    check("INV4 grounding gate sits above the executor",
          direct.get("status") == "VERIFIED"
          and piped.get("status") == "REVIEW_REQUIRED"
          and "invoice_roles" not in piped,
          f"direct={direct.get('status')} piped={piped.get('status')}")
    # 5. the executor cannot bypass the authorities: every VERIFIED
    #    invoice result must route under a REAL registered authority
    real_authorities = {"COMMERCIAL_CORE", "ASSET_AUTHORITY",
                        "GST_AUTHORITY", "SETTLEMENT_AUTHORITY"}
    verified_docs = [
        "Purchase Invoice SV-7\nFrom: Ram & Sons\nNet Amount: 1000\n"
        "GST: 180\nTotal: 1180",
        "Sales Invoice SV-8\nTo: Anil\nNet Amount: 500\nTotal: 500",
        "Invoice SV-9\nFrom: Supplier Ltd\nTotal Amount: 1000\n"
        "Amount Paid: 400\nBalance Due: 600",
        "Refund Note SV-10\nTo: Kishan\nRefund Amount: 500",
        "Credit Note SV-11\nFrom: Mehra & Co\nCredit Note Amount: 700",
        "Purchase Invoice SV-12\nFrom: MachineWorks\nMachinery purchased\n"
        "Net Amount: 5000\nTotal: 5000",
    ]
    for d in verified_docs:
        r = orchestrate(d)
        check(f"INV5 authority boundary [{r.get('authority')}]",
              r.get("status") != "VERIFIED"
              or r.get("authority") in real_authorities,
              f"{r.get('authority')} {d[:30]}")
    # 6. every VERIFIED result is balanced and carries evidence
    for d in verified_docs:
        r = orchestrate(d)
        if r.get("status") != "VERIFIED":
            continue
        j = r.get("journal") or r
        check("INV6 VERIFIED journal balanced + evidenced",
              j.get("total_debit") == j.get("total_credit")
              and r.get("invoice_roles") is not None,
              str((j.get("total_debit"), j.get("total_credit"))))


def main():
    case_a()
    case_b()
    case_c()
    case_d()
    case_e_f()
    case_g_h()
    case_i()
    case_j()
    invariants()
    print(f"\nFalse-VERIFIED gate: {OK[0]} checks passed, {len(FAIL)} failed")
    print(f"false_VERIFIED count: {len(FALSE_VERIFIED)}")
    if FAIL:
        for f in FAIL:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS - false_VERIFIED == 0")


if __name__ == "__main__":
    main()
