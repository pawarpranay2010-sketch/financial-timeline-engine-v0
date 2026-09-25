#!/usr/bin/env python3
"""
Platrixa
Phase 4 (Sprint INV-ROLE) - Invoice Party-ID Boundary Test
scripts/fte_invoice_party_boundary_test.py

Characterizes the single-letter party-ID concern ('B-001-TEST-BUYER')
through the REAL production path and proves where the refusal lives.

Findings encoded here (all probe results reproduced on every run):

  * The concern is raised by fyjc_normalization.normalize_fyjc_text
    (_SINGLE_LETTER_RE safety sweep), NOT by invoice parsing, the role
    layer, the executor, or any accounting authority.
  * 'B-001-TEST-BUYER' and 'B-001' trigger it because the isolated
    'B'/'S' before a hyphen matches the single-letter-initial sweep.
  * Real company names ('ABC LTD', 'ABC PVT LTD', 'A BUYER', 'BUYER')
    do NOT trigger it and compose to VERIFIED through the invoice path.
  * The invoice layer never sees a concerned document: the orchestrator
    hands it to the narration path verbatim (no invoice evidence in the
    result) - the grounding/normalization boundary is REAL.

Phase 4 conclusion (recorded in docs/phase_4/party_boundary_audit.json):

  B. Existing party validation is intentionally correct - it is pinned
     by multiple pre-Phase-3 safety suites (15I-VY / 15I-COVER / bills
     authority: 'single-letter party refuses').
  C. The benchmark fixture's synthetic 'B-001-TEST-BUYER' IDs are the
     trigger; they are IDENTIFIER-shaped values sitting in a party
     NAME field. No invoice-specific bypass is created.

Therefore the party rule is left UNTOUCHED and this gate pins the
boundary: identifier-shaped party values must keep refusing, real
names must keep composing, and the refusal must always be owned by
normalization - never converted into a VERIFIED by any invoice code.
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

FAIL = []
OK = [0]


def check(name, ok, detail=""):
    if ok:
        OK[0] += 1
        print(f"OK  [{name}]")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"FAIL[{name}] {detail}")


def invoice_with_party(party: str) -> str:
    return (f"Purchase Invoice PB-2026-01\nFrom: {party}\n"
            "Net Amount: 1000\nGST: 180\nTotal: 1180")


# ---------------------------------------------------------------------------
# 1. probe matrix through the REAL production path (orchestrate)
# ---------------------------------------------------------------------------

ID_SHAPED = ["B-001-TEST-BUYER", "B-001", "S-001-TEST-SELLER"]
REAL_NAMES = ["BUYER", "ABC LTD", "ABC PVT LTD", "A BUYER", "Anil",
              "Ram & Sons"]


def test_probe_matrix():
    for party in ID_SHAPED:
        r = orchestrate(invoice_with_party(party))
        check(f"1.1 ID-shaped party {party!r} does NOT reach VERIFIED",
              r.get("status") != "VERIFIED", str(r.get("status")))
        check(f"1.2 {party!r} refusal is the party-ID concern",
              "single-letter abbreviation" in str(r.get("why_not")),
              str(r.get("why_not"))[:60])
    for party in REAL_NAMES:
        r = orchestrate(invoice_with_party(party))
        check(f"1.3 real name {party!r} composes VERIFIED",
              r.get("status") == "VERIFIED"
              and r.get("rule_key") == "invoice_labelled_facts",
              f"{r.get('status')} {r.get('rule_key')}")


# ---------------------------------------------------------------------------
# 2. WHERE the refusal lives (layer-by-layer localization)
# ---------------------------------------------------------------------------

def test_refusal_location():
    doc = invoice_with_party("B-001-TEST-BUYER")

    # Layer 1: document parsing / role resolution sees NO problem.
    roles = resolve_invoice_roles(doc)
    check("2.1 role layer resolves the party-ID document cleanly",
          roles.safe and roles.has_any_role, str(roles.conflicts))

    # Layer 2: the concern is raised by normalize_fyjc_text.
    norm = normalize_fyjc_text(doc)
    check("2.2 normalization raises the concern",
          len(norm.concerns) == 1
          and "single-letter abbreviation" in norm.concerns[0],
          str(norm.concerns)[:90])

    # Layer 3: schema/grounding layers are NOT the trigger - the input
    # never reaches them with a concern present (orchestrator gate).
    r = orchestrate(doc)
    check("2.3 orchestrator hands the concern to narration verbatim",
          r.get("status") == "REVIEW_REQUIRED"
          and "invoice_roles" not in r
          and r.get("rule_key") is None,
          f"{r.get('status')} rule={r.get('rule_key')}")
    check("2.4 narration path owns the refusal text",
          "single-letter abbreviation" in str(r.get("why_not")), "")

    # Layer 4: the invoice executor itself has NO party-ID rule - when
    # called directly (ungated) it composes. This is proof the refusal
    # is NOT invented by invoice code and cannot be removed by invoice
    # code either (it sits upstream, before grounding).
    direct = compose_invoice_journal(doc, roles, "PURCHASE")
    check("2.5 refusal is upstream of the invoice executor",
          direct.get("status") == "VERIFIED",
          str(direct.get("status")))

    # Layer 5: no accounting authority is involved in the refusal.
    check("2.6 refusal carries no authority and no journal",
          r.get("authority") is None
          and not (r.get("debit_lines") or r.get("credit_lines")), "")


# ---------------------------------------------------------------------------
# 3. the boundary is uniform across input domains (no invoice bypass)
# ---------------------------------------------------------------------------

def test_domain_uniformity():
    # the SAME letter-initial concern fires on narration input too
    n = normalize_fyjc_text("Paid Rs.500 to B-001-TEST-BUYER for goods.")
    check("3.1 narration domain: same concern for the same ID",
          any("single-letter abbreviation" in c for c in n.concerns),
          str(n.concerns)[:80])
    # and on a plain sale document
    n2 = normalize_fyjc_text("Sold goods to S-001-TEST-SELLER for Rs.900.")
    check("3.2 narration sale: same concern for the same ID",
          any("single-letter abbreviation" in c for c in n2.concerns),
          str(n2.concerns)[:80])
    # graph normalization provenance carries the concern to every path
    check("3.3 normalized text is produced (provenance intact)",
          bool(norm.text) if (norm := normalize_fyjc_text(
              invoice_with_party("B-001-TEST-BUYER"))) else False, "")


# ---------------------------------------------------------------------------
# 4. pinned pre-existing safety behavior (must NOT regress)
# ---------------------------------------------------------------------------

def test_pinned_safety():
    # 15I-COVER boundary: bare single-letter party refuses
    r = orchestrate("Purchased goods from X for Rs.5000.")
    check("4.1 bare single-letter party refuses (15I-COVER pinned)",
          r.get("status") == "REVIEW_REQUIRED"
          and "single-letter abbreviation" in str(r.get("why_not")),
          str(r.get("status")))
    # dotted initial refuses
    r2 = orchestrate("Purchased goods from R. for Rs.5000.")
    check("4.2 dotted initial party refuses",
          r2.get("status") == "REVIEW_REQUIRED"
          and "single-letter abbreviation" in str(r2.get("why_not")),
          str(r2.get("status")))
    # safe tokens stay safe ('a'/'i', M/s, p.a.)
    n = normalize_fyjc_text("M/s Sharma and p.a. interest to a customer.")
    check("4.3 safe single-letter tokens never flagged",
          not any("single-letter" in c for c in n.concerns),
          str(n.concerns)[:80])


# ---------------------------------------------------------------------------
# 5. the invoice layer cannot rescue a concerned document (boundary)
# ---------------------------------------------------------------------------

def test_no_invoice_rescue():
    doc = invoice_with_party("B-001-TEST-BUYER")
    r = orchestrate(doc)
    check("5.1 no invoice evidence in a concerned result",
          "invoice_roles" not in r and r.get("rule_key") is None,
          str(r.get("rule_key")))
    # even a perfectly reconciled document stays refused
    check("5.2 reconciliation cannot override the concern",
          r.get("status") == "REVIEW_REQUIRED", str(r.get("status")))
    # and the harness input shape (buyer AND seller + IDs) refuses too
    bench = ("TAX INVOICE BILL-SI-01\nFrom: B-001-TEST-BUYER\n"
             "To: S-001-TEST-SELLER\nNet Amount: 10500\nGST: 1890\n"
             "Total: 12390")
    rb = orchestrate(bench)
    check("5.3 benchmark ID shape refuses on party evidence alone",
          rb.get("status") == "REVIEW_REQUIRED"
          and "single-letter abbreviation" in str(rb.get("why_not")),
          str(rb.get("status")))


def main():
    test_probe_matrix()
    test_refusal_location()
    test_domain_uniformity()
    test_pinned_safety()
    test_no_invoice_rescue()
    print(f"\nParty-boundary gate: {OK[0]} checks passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(" -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
