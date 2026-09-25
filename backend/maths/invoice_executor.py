"""
Platrixa — Invoice Labelled-Facts Executor (Sprint INV-ROLE)
============================================================

DETERMINISTIC journal composition from invoice LABEL evidence.

The model never executes; the labels never execute. The invariant chain
required for this module to run at all is enforced by the orchestrator:

  schema-valid 18-field candidate
    → deterministic grounding (ExpandedGroundingGate)
    → clean transaction graph (no contradiction/violation)
    → explicit, unambiguous invoice label evidence
    → supported invoice capability
    → THIS deterministic composition
    → one VERIFIED journal

Composition reuses the hardened engine's own journal conventions
(generate_journal) so no new accounting rule is invented:

  purchase narration:  DR Purchases N / CR Party N
  sale narration:      DR Party N / CR Sales N
  expense narration:   DR Expense N / CR Cash N          (payment method
                       NEFT/UPI/cheque/bank still posts CR Cash — the
                       narration engine does the same)
  asset purchase:      DR Asset / CR Party (ASSET_AUTHORITY routing)
  narration refund:    DR Cash R / CR Party R
  narration return:    DR Party R / CR Purchase Returns R (party side
                       is reused for the credit-note counterparty)
  narration GST:       CGST/SGST split at rate/2; labels provide the
                       AMOUNTS instead of a rate (amount form is what the
                       narration engine refuses).

A party is required for every composed journal (from the document's
From/To/Buyer/Seller lines); a missing/contradictory party fails closed
to REVIEW_REQUIRED. Amounts come ONLY from resolved invoice labels;
amounts are never modified, derived, or selected heuristically.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_bk_reasoning import (
    REVIEW_REQUIRED,
    _fmt_amt,
)
from backend.maths.invoice_amount_roles import (
    InvoiceRoleResult,
    LabelledAmount,
    _COMPONENT_LABEL_RE,
)

# Payment-method vocabulary for the money account. The narration engine
# posts CR Cash for every instrument ('by NEFT' → CR Cash); labels follow
# the same deterministic treatment. Method words are matched in the
# document's Payment line only; absence → on-credit semantics.
_PAYMENT_LINE_RE = re.compile(
    r"^\s*payment(?:\s+mode|\s+method|\s+terms)?\s*:\s*(.+)$",
    re.IGNORECASE,
)
_PAYMENT_INSTRUMENTS = (
    ("neft", "NEFT"), ("rtgs", "NEFT"), ("imps", "NEFT"),
    ("upi", "UPI"), ("cheque", "cheque"), ("chq", "cheque"),
    ("bank transfer", "bank transfer"), ("cash", "cash"),
    ("credit", "credit"), ("card", "card"),
)
# Sprint 15I-VY reuses _SINGLE_LETTER_RE upstream; here the boundary that
# matters is the GOODS surface: generate_journal refuses service wording
# ('consulting services', 'software services') as outside the FYJC
# goods-only transaction domain. The invoice path must NOT become a
# wider classification standard than that machinery (no 'DR Purchases'
# for services merely because the label exists).
_SERVICE_WORDING_RE = re.compile(
    r"\b(?:services?|consulting|consultancy|professional\s+fees|"
    r"software\s+(?:services|license|licence)|subscriptions?)\b",
    re.IGNORECASE,
)

# Party lines: document-style From/To/Buyer/Seller/Vendor/Supplier labels.
# From/Vendor/Supplier → supplier side; To/Buyer/Customer → customer side.
_PARTY_SUPPLIER_RE = re.compile(
    r"^\s*(?:from|vendor|supplier)\s*(?:\(.{1,40}?\))?\s*:\s*(.+)$",
    re.IGNORECASE,
)
_PARTY_CUSTOMER_RE = re.compile(
    r"^\s*(?:to|buyer|customer)\s*(?:\(.{1,40}?\))?\s*:\s*(.+)$",
    re.IGNORECASE,
)


def _fmt(value: Decimal) -> str:
    """Amount formatting identical to _fmt_amt (string, no trailing zeros
    beyond what the narration engine itself would print)."""
    return str(value)


def _party_line(lines: List[str]) -> Optional[Tuple[str, str]]:
    """(side, party) from the document's party lines.

    Both sides named → None (ambiguous who transacts — REVIEW_REQUIRED).
    One side named → that side. Deterministic.
    """
    supplier: Optional[str] = None
    customer: Optional[str] = None
    for line in lines:
        m = _PARTY_SUPPLIER_RE.match(line)
        if m and supplier is None:
            candidate = m.group(1).strip()
            if candidate:
                supplier = candidate
            continue
        m = _PARTY_CUSTOMER_RE.match(line)
        if m and customer is None:
            candidate = m.group(1).strip()
            if candidate:
                customer = candidate
    if supplier and customer:
        return None
    if supplier:
        return ("supplier", supplier)
    if customer:
        return ("customer", customer)
    return None


def _payment_instrument(lines: List[str]) -> Optional[str]:
    for line in lines:
        m = _PAYMENT_LINE_RE.match(line)
        if m:
            rest = m.group(1).lower()
            for needle, label in _PAYMENT_INSTRUMENTS:
                if needle in rest:
                    return label
            return None
    return None


def _fmt_amt_str(value: Decimal) -> str:
    """Matches the narration engine's displayed amount format.

    generate_journal displays Decimal(10000) as '10000' and computed
    amounts as two-decimal strings; the Decimal(str(x)) double-typed
    pattern reproduces both. Kept local to avoid coupling to a private
    helper.
    """
    return str(value)


def _journal_payload(
    lines: List[Dict[str, str]],
    narration: str,
    *,
    authority: str,
) -> Dict[str, Any]:
    """A generate_journal-shaped VERIFIED journal payload."""
    debit_total = sum((Decimal(l["amount"]) for l in lines if l["side"] == "debit"),
                      Decimal(0))
    credit_total = sum((Decimal(l["amount"]) for l in lines if l["side"] == "credit"),
                       Decimal(0))
    return {
        "status": "VERIFIED",
        "narration": narration,
        "debit_lines": [{"account": l["account"], "amount": l["amount"]}
                        for l in lines if l["side"] == "debit"],
        "credit_lines": [{"account": l["account"], "amount": l["amount"]}
                         for l in lines if l["side"] == "credit"],
        "calculation_records": [],
        "total_debit": str(debit_total),
        "total_credit": str(credit_total),
        "balanced": debit_total == credit_total,
        "authority": authority,
        "why_not": None,
        "next_action": None,
        "source": "invoice_labelled_facts",
    }


def _refusal(reason: str, action: str) -> Dict[str, Any]:
    return {
        "status": REVIEW_REQUIRED,
        "why_not": reason,
        "next_action": action,
        "debit_lines": [],
        "credit_lines": [],
        "authority": "invoice_capability",
        "source": "invoice_labelled_facts",
    }


def _split_party(journal: Dict[str, Any], party: str,
                 amount: Decimal) -> Tuple[Decimal, Decimal]:
    """Existing party line amount vs remaining cash/bank amount from a
    generate_journal VERIFIED payload (partial payment semantics)."""
    party_amt = Decimal(0)
    for side in ("debit_lines", "credit_lines"):
        for line in (journal.get(side) or []):
            if (line.get("account") or "").strip() == party:
                party_amt = Decimal(str(line.get("amount")))
                break
    remaining = amount - party_amt
    return party_amt, remaining


def compose_invoice_journal(
    raw_text: str,
    roles: InvoiceRoleResult,
    classification_key: str,
    *,
    asset_hint: bool = False,
    expense_hint: Optional[str] = None,
    return_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Compose the deterministic journal for an invoice-labelled document.

    Every rule here mirrors generate_journal's own narration behavior;
    nothing is invented. Any missing/ambiguous/contradictory evidence →
    REVIEW_REQUIRED with the specific deterministic reason.
    """
    lines = raw_text.splitlines()

    # -- gates: labels must be present, unambiguous, and reconciled --------
    if not roles.has_any_role:
        return _refusal(
            "The document carries no explicit invoice amount labels; "
            "Platrixa will not choose an amount without label evidence.",
            "Provide the amounts as explicit labels (e.g. 'Net Amount: "
            "Rs.1,000', 'Total: Rs.1,180').")
    if roles.conflicts:
        first = roles.conflicts[0]
        return _refusal(
            first.get("reason")
            or "Invoice labels state conflicting amounts.",
            "Correct the conflicting amounts so each label states exactly "
            "one value.")
    if roles.unresolved:
        return _refusal(
            roles.unresolved[0].get("reason")
            or "Invoice label evidence is incomplete.",
            "Complete the labelled amounts so the document can be "
            "deterministically interpreted.")
    bad_recon = [r for r in roles.reconciliation
                 if r.get("status") in ("MISMATCH", "UNRESOLVED")]
    if bad_recon:
        return _refusal(
            bad_recon[0].get("detail")
            or "Labelled amounts do not reconcile.",
            "Correct the amounts so the labelled arithmetic agrees; "
            "Platrixa never modifies an amount to make totals reconcile.")

    party_info = _party_line(lines)
    if party_info is None:
        return _refusal(
            "The document names both a supplier and a customer side (or "
            "neither), so the transacting party cannot be determined "
            "deterministically.",
            "State one transacting party (the supplier for a purchase/"
            "expense invoice, the customer for a sales invoice).")

    side, party = party_info
    if roles.is_role_ambiguous("total") or roles.is_role_ambiguous("net"):
        return _refusal(
            "Invoice labels state multiple different values for a role "
            "required by this document type.",
            "Each label must state exactly one value.")

    net = roles.role_value("net")
    tax_total = roles.tax_total
    total = roles.role_value("total")
    discount = roles.role_value("discount")
    payment = roles.role_value("payment")
    outstanding = roles.role_value("outstanding")
    refund = roles.role_value("refund")
    credit_note = roles.role_value("credit_note")
    shipping = roles.role_value("shipping")

    payment_method = _payment_instrument(lines)

    # ------------------------------------------------------------------
    # Capability A/B/C/E: PURCHASE / SALES / EXPENSE invoice (+GST)
    # ------------------------------------------------------------------
    if net is not None and total is not None:
        # required: net + total; tax optional (labelled when present)
        if tax_total is None and roles.role_values("tax"):
            return _refusal(
                "Tax labels are present but conflicting; the tax amount "
                "cannot be resolved.",
                "State exactly one value for the tax label(s).")
        # Discount composition (identical arithmetic to the role layer's
        # own reconciliation): the taxable base is net minus the labelled
        # discount. Never an inference — the composition is the one the
        # labels' own arithmetic proves (net − discount + tax == total).
        base = net - (discount or Decimal(0))
        if base < 0:
            return _refusal(
                f"The labelled discount (Rs.{discount}) exceeds the "
                f"labelled net amount (Rs.{net}).",
                "Check the discount against the net amount.")
        is_sale = bool(classification_key) and \
            "SALE" in (classification_key or "").upper()
        if not is_sale:
            # classify_bk_type is narration-shaped; an invoice-style
            # sales document ('Sales Invoice ... To: Anil') can classify
            # to None there. The document's OWN party side is the
            # deterministic fallback evidence: a document naming a
            # CUSTOMER ('To:') bills a customer, so it is a sale.
            is_sale = side == "customer"
        # Account-classification boundary (Sprint INV-ROLE Phase 4):
        # service wording is refused by the narration engine's goods
        # surface. When the document establishes NO specific supported
        # account (no asset/expense hint), composing the default goods
        # account would invent a classification the existing machinery
        # does not make - fail closed instead. The classification
        # machinery (asset path via generate_journal, expense hints)
        # still decides whenever it CAN.
        if _SERVICE_WORDING_RE.search(raw_text or "") and not (
                asset_hint or expense_hint):
            return _refusal(
                "The document describes services rather than goods, and "
                "names no specific supported account; the deterministic "
                "classification machinery refuses service transactions, "
                "so the invoice path will not invent 'Purchases' or "
                "'Sales' for them.",
                "Record the service through a supported expense account "
                "on the document (e.g. 'Rent', 'Salary') or keep it out "
                "of the goods journal.")
        if is_sale:
            if shipping is not None:
                return _refusal(
                    "The document labels a shipping/freight amount on a "
                    "sales invoice; the FYJC narration domain has no "
                    "deterministic account for shipping billed on a sale "
                    "(carriage outward is a seller expense, never a "
                    "billed invoice line).",
                    "Remove the shipping line or record the freight "
                    "through an expense narration.")
            # mirrors narration sale (net of trade discount):
            # DR party total / CR Sales base / CR Output CGST+SGST tax
            debit = [{"account": party, "side": "debit",
                      "amount": _fmt_amt_str(total)}]
            credit = [{"account": "Sales", "side": "credit",
                       "amount": _fmt_amt_str(base)}]
            if tax_total is not None and tax_total != 0:
                for acct, amt in _gst_component_lines(tax_total, roles,
                                                      "Output"):
                    credit.append({"account": acct,
                                   "side": "credit",
                                   "amount": _fmt_amt_str(amt)})
            lines_payload = debit + credit
            authority = "COMMERCIAL_CORE"
        else:
            # purchase/expense narration: DR base / DR Input GST /
            # CR party total (or CR Cash paid + CR party remainder)
            is_asset = bool(asset_hint) or "ASSET" in (classification_key
                                                       or "").upper()
            if expense_hint:
                base_account = expense_hint
            elif is_asset:
                base_account = None  # resolved from the narration engine
            else:
                base_account = "Purchases"
            if base_account is None:
                # ASSET_AUTHORITY: reuse the narration engine's own asset
                # posting for the SAME narration it would accept, then
                # splice the labelled tax/party amounts onto it. The
                # narration engine classifies the asset account.
                from backend.maths.fyjc_bk_reasoning import generate_journal
                narration_text = (
                    f"Purchased {asset_hint or 'the asset'} from {party} "
                    f"for Rs.{_fmt_amt_str(base)}."
                )
                engine_journal = generate_journal(narration_text)
                if engine_journal.get("status") != "VERIFIED":
                    return _refusal(
                        "The document's asset classification could not be "
                        "resolved by the deterministic engine.",
                        "State the asset clearly (e.g. 'Purchased "
                        "machinery from <party>').")
                base_account = (engine_journal.get("debit_lines") or [{}])[0].get("account")
                if not base_account:
                    return _refusal(
                        "The document's asset account could not be "
                        "determined deterministically.",
                        "Name the asset purchased.")
            carriage_line: Optional[Dict[str, Any]] = None
            if shipping is not None:
                if base_account == "Purchases":
                    # The narration engine's own freight account: 'paid
                    # carriage Rs.X' -> DR Carriage Inward. An invoice's
                    # labelled shipping line joins the debit side the
                    # same way (NEVER folded into Purchases); the
                    # supplier credit carries the whole payable.
                    carriage_line = {
                        "account": "Carriage Inward",
                        "side": "debit",
                        "amount": _fmt_amt_str(shipping),
                    }
                else:
                    return _refusal(
                        "The document labels a shipping/freight amount "
                        "alongside a non-inventory purchase; no supported "
                        "authority composes freight with an asset or "
                        "expense debit.",
                        "Record the freight through a carriage/freight "
                        "narration instead.")
            debit = [{"account": base_account, "side": "debit",
                      "amount": _fmt_amt_str(base)}]
            if carriage_line is not None:
                debit.append(carriage_line)
            if tax_total is not None and tax_total != 0:
                for acct, amt in _gst_component_lines(tax_total, roles,
                                                      "Input"):
                    debit.append({"account": acct,
                                  "side": "debit",
                                  "amount": _fmt_amt_str(amt)})
            if payment is not None and payment > 0:
                debit_total = base + (tax_total or Decimal(0)) \
                    + (shipping or Decimal(0))
                if payment > debit_total:
                    return _refusal(
                        f"The labelled payment (Rs.{payment}) exceeds the "
                        f"labelled invoice total (Rs.{debit_total}).",
                        "Check the payment amount against the invoice "
                        "total.")
                credit = [{"account": "Cash", "side": "credit",
                           "amount": _fmt_amt_str(payment)}]
                remaining = debit_total - payment
                if remaining > 0:
                    credit.append({"account": party, "side": "credit",
                                   "amount": _fmt_amt_str(remaining)})
            elif payment is not None and payment < 0:
                return _refusal(
                    "The invoice states a negative paid amount, which is "
                    "not a payable purchase payment.",
                    "Enter refunds through the refund capability, not as "
                    "a negative payment.")
            else:
                credit = [{"account": party, "side": "credit",
                           "amount": _fmt_amt_str(
                               base + (tax_total or Decimal(0))
                               + (shipping or Decimal(0)))}]
            lines_payload = debit + credit
            authority = ("ASSET_AUTHORITY" if is_asset
                         else "COMMERCIAL_CORE")

        # reconciliation gate re-check: labelled total must equal
        # net + tax (+/- labelled discount/shipping) — the role layer
        # already records this; a MISMATCH refusal above proves it.
        payload = _journal_payload(
            lines_payload,
            f"Invoice-labelled {'sale' if is_sale else 'purchase/expense'}: "
            f"net {_fmt_amt_str(net)}"
            + (f", tax {_fmt_amt_str(tax_total)}" if tax_total else "")
            + (f", shipping {_fmt_amt_str(shipping)}"
               if shipping is not None else "")
            + f", total {_fmt_amt_str(total)}"
            + (f", paid {_fmt_amt_str(payment)}" if payment is not None else ""),
            authority=authority,
        )
        return payload

    # ------------------------------------------------------------------
    # Capability H boundary: SHIPPING / FREIGHT as the only labelled fact
    # ------------------------------------------------------------------
    # Freight accompanying a labelled net amount is composed into the
    # invoice above (DR Carriage Inward, the narration engine's own
    # freight account). A document whose only money labels are
    # shipping/freight (+ total) has NO supported authority for the
    # debit side — deterministic REVIEW_REQUIRED, never a guessed debit.
    if (shipping is not None and net is None and payment is None
            and refund is None and credit_note is None
            and total is not None):
        return _refusal(
            "The document's only labelled money fields are shipping/"
            f"freight (Rs.{_fmt_amt_str(shipping)}) and total "
            f"(Rs.{_fmt_amt_str(total)}); no supported authority "
            "classifies a standalone freight charge (carriage-in, "
            "expense, or supplier charge). Freight accompanying a "
            "labelled net amount IS supported and is composed into the "
            "invoice.",
            "Send the freight as part of a labelled invoice (net, tax, "
            "total) or record it through an expense narration.")

    # ------------------------------------------------------------------
    # Capability D/J: PAYMENT AGAINST INVOICE / OUTSTANDING BALANCE
    # ------------------------------------------------------------------
    if payment is not None and outstanding is not None and total is not None:
        if payment < 0 or outstanding < 0:
            return _refusal(
                "Negative paid/outstanding amounts cannot be settled "
                "deterministically against an invoice total.",
                "Enter refunds through the refund capability.")
        # The settlement posts ONLY the payment fact (the narration
        # engine's own convention: 'Paid Rs.5,000 to Ram' → DR Party /
        # CR Cash; 'Received Rs.5,000 from Shyam' → DR Cash / CR Party).
        # The labelled outstanding/total are EVIDENCE (they reconcile
        # payment + outstanding == total in the role layer) — they are
        # never posted, because posting them would book the remaining
        # balance as a NEW receivable/payable.
        if side == "supplier":
            lines_payload = [
                {"account": party, "side": "debit",
                 "amount": _fmt_amt_str(payment)},
                {"account": "Cash", "side": "credit",
                 "amount": _fmt_amt_str(payment)},
            ]
        else:
            lines_payload = [
                {"account": "Cash", "side": "debit",
                 "amount": _fmt_amt_str(payment)},
                {"account": party, "side": "credit",
                 "amount": _fmt_amt_str(payment)},
            ]
        return _journal_payload(
            lines_payload,
            f"Invoice settlement: paid {_fmt_amt_str(payment)} against "
            f"total {_fmt_amt_str(total)}, outstanding "
            f"{_fmt_amt_str(outstanding)}",
            authority="SETTLEMENT_AUTHORITY")

    # ------------------------------------------------------------------
    # Capability I: REFUND (mirrors narration 'Received refund ...')
    # ------------------------------------------------------------------
    if refund is not None:
        if side != "customer":
            return _refusal(
                "The document does not name the customer receiving the "
                "refund; the refund counterparty cannot be resolved.",
                "State the refund recipient on the document.")
        if refund < 0:
            return _refusal(
                "A refund amount is stated negatively; enter it as a "
                "positive refund amount.",
                "State the refund as a positive amount.")
        # narration refund: DR Cash R / CR Party R — direction from the
        # document's own party side (customer side receives).
        return _journal_payload(
            [{"account": "Cash", "side": "debit",
              "amount": _fmt_amt_str(refund)},
             {"account": party, "side": "credit",
              "amount": _fmt_amt_str(refund)}],
            f"Invoice refund: {_fmt_amt_str(refund)} to {party}",
            authority="COMMERCIAL_CORE")

    # ------------------------------------------------------------------
    # Capability G: CREDIT NOTE (mirrors narration purchase return)
    # ------------------------------------------------------------------
    if credit_note is not None:
        if side != "supplier":
            return _refusal(
                "The document does not name the supplier issuing the "
                "credit note; the credit-note counterparty cannot be "
                "resolved.",
                "State the supplier on the credit note.")
        if credit_note < 0:
            return _refusal(
                "A credit-note amount is stated negatively; enter it as "
                "a positive reduction.",
                "State the credit note as a positive amount.")
        return _journal_payload(
            [{"account": party, "side": "debit",
              "amount": _fmt_amt_str(credit_note)},
             {"account": "Purchase Returns", "side": "credit",
              "amount": _fmt_amt_str(credit_note)}],
            f"Credit note against {party}: {_fmt_amt_str(credit_note)}",
            authority="COMMERCIAL_CORE")

    return _refusal(
        "The document's labelled amounts do not form a supported "
        "invoice capability (no net+total, payment+outstanding, refund, "
        "or credit-note shape).",
        "Provide the amounts as explicit labels for a supported invoice "
        "capability.")


def _gst_split(tax_total: Decimal) -> List[Tuple[str, Decimal]]:
    """CGST/SGST split exactly as the narration engine's rate split:
    half each. INTRA-STATE amounts only — IGST labels must NOT reach
    this function (see _gst_component_lines, which keeps a labelled
    IGST amount as ONE IGST line, mirroring generate_journal's own
    inter-state posting of a single 'Input IGST'/'Output IGST' line).
    Amount-form GST without component labels follows the narration
    convention (CGST+SGST)."""
    half = tax_total / Decimal(2)
    return [("CGST", half), ("SGST", half)]


def _gst_component_lines(tax_total: Decimal, roles: InvoiceRoleResult,
                         prefix: str) -> List[Tuple[str, Decimal]]:
    """The labelled GST evidence as ('<prefix> <Component>', amount)
    journal lines. A document whose tax labels name IGST posts ONE
    '<prefix> IGST' line for the whole amount (the narration engine's
    own inter-state convention); CGST/SGST or component-less labels
    split half/half exactly like the narration rate split. Never a new
    accounting rule — only the account NAMING follows the labels.'"""
    comps = [la for la in roles.role_map.get("tax", [])
             if _COMPONENT_LABEL_RE.search(la.label_text)]
    names = set()
    for la in comps:
        for name in _COMPONENT_LABEL_RE.findall(la.label_text):
            names.add(name.upper())
    if names == {"IGST"}:
        return [(f"{prefix} IGST", tax_total)]
    return [(f"{prefix} {comp}", amt)
            for comp, amt in _gst_split(tax_total)]
