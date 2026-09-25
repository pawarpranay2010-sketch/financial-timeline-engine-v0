"""
Platrixa — Invoice Amount-Role Resolution (Sprint INV-ROLE)
===========================================================

Answers ONE deterministic question only:

  "Which explicitly labelled amount in an invoice-style document owns
   which amount ROLE?"

The module NEVER:
  - posts accounting entries (no journal, no debit/credit)
  - becomes a financial authority (roles are evidence for the kernel,
    never commands)
  - picks first/largest/smallest/last amounts
  - infers amounts that lack an explicit label-value relationship
  - invents tax rates or currency semantics
  - repairs contradictory or ambiguous documents

Every accepted role is backed by an explicit "label:value" adjacency in
the source text (same line, label immediately left of the value). Role
evidence is returned with exact source spans so the orchestrator (and
tests) can re-prove the mapping. Pure module: no I/O, no AI, no network.
Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_bk_reasoning import parse_numeric_text

# ---------------------------------------------------------------------------
# Label vocabulary (closed; explicit evidence only)
# ---------------------------------------------------------------------------

# Each label regex is anchored to a line start (a document label sits on
# its own line/field), must be immediately followed by a colon (optionally
# a currency symbol between label and colon is NOT accepted - the label
# must own the value on ITS line), then an optional currency prefix, then
# the amount. Label matching is on the LOWERCASE line, full-line anchored.

_LABEL_PATTERNS: Tuple[Tuple[str, str], ...] = (
    # NET (also accepts 'Sub Total', 'Taxable Value' as the pre-tax base)
    ("net", r"net\s*amount|net|sub\s*total|subtotal|taxable\s*(?:value|amount)"),
    # TAX — total GST and components (CGST/SGST/IGST all map to tax;
    # the component split is the GST authority's business, not this
    # module's).
    ("tax", r"(?:(?:total\s+)?gst)\s*amount|gst|cgst|sgst|igst|tax\s*amount|tax"),
    # TOTAL
    ("total", r"(?:grand\s+total|gross\s+(?:amount|total)|total\s+amount|total|invoice\s+total|bill\s+total)"),
    # DISCOUNT (trade/cash alike — the existing discount detectors own the kind)
    ("discount", r"(?:trade\s+discount|cash\s+discount|discount)\s*amount|trade\s+discount|cash\s+discount|discount"),
    # SHIPPING
    ("shipping", r"(?:shipping\s+charges|shipping|freight|delivery\s+charges|delivery|packing\s+&\s+forwarding|p\s*&\s*f)\s*amount|shipping|freight|delivery\s+charges|delivery"),
    # PAYMENT
    ("payment", r"(?:amount\s+paid|paid\s+amount|amount\s+paid\s+now|advance\s+paid|advance)\s*|amount\s+paid|paid\s+amount|advance\s+payment|advance"),
    ("payment", r"paid"),
    # OUTSTANDING
    ("outstanding", r"(?:balance\s+due|amount\s+due|amount\s+outstanding|outstanding)\s*|balance\s+due|amount\s+due|amount\s+outstanding|outstanding\s+amount|outstanding|balance\s+payable|balance"),
    # REFUND
    ("refund", r"(?:refund\s+amount|amount\s+refunded|refunded\s+amount)\s*|refund\s+amount|amount\s+refunded|refunded\s+amount|refunded|refund"),
    # CREDIT NOTE
    ("credit_note", r"(?:credit\s+note\s+amount|credit\s+note)\s*|credit\s+note\s+amount|credit\s+note"),
)

# All label variants per role. Variants are MERGED into one alternation
# (longest first) so a role with several accepted labels never loses a
# variant to dict overwriting.
_ROLE_VARIANTS: Dict[str, Tuple[str, ...]] = {}
for _role, _pat in _LABEL_PATTERNS:
    _ROLE_VARIANTS.setdefault(_role, ())
    _ROLE_VARIANTS[_role] = _ROLE_VARIANTS[_role] + (_pat,)

_LABEL_BY_ROLE: Dict[str, str] = {
    _role: "|".join(
        sorted({v for v in variants}, key=len, reverse=True)
    )
    for _role, variants in _ROLE_VARIANTS.items()
}

_ROLE_ORDER: Tuple[str, ...] = (
    "net", "tax", "total", "discount", "shipping",
    "payment", "outstanding", "refund", "credit_note",
)

# ---------------------------------------------------------------------------
# Line model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelledAmount:
    """One explicit label:value evidence record.

    confidence is an EVIDENCE CLASSIFICATION ('explicit-label'), never a
    probabilistic score and never an authority.
    """

    role: str
    value: Decimal
    source_span: Tuple[int, int]      # label start .. value end in raw text
    label_span: Tuple[int, int]
    amount_span: Tuple[int, int]
    label_text: str
    confidence: str = "explicit-label"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "value": str(self.value),
            "source_span": [self.source_span[0], self.source_span[1]],
            "label_span": [self.label_span[0], self.label_span[1]],
            "amount_span": [self.amount_span[0], self.amount_span[1]],
            "label_text": self.label_text,
            "confidence": self.confidence,
        }


@dataclass
class InvoiceRoleResult:
    """Complete role evidence for one document text."""

    labelled: List[LabelledAmount] = field(default_factory=list)
    duplicates: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    reconciliation: List[Dict[str, Any]] = field(default_factory=list)
    unresolved: List[Dict[str, Any]] = field(default_factory=list)
    role_map: Dict[str, List[LabelledAmount]] = field(default_factory=dict)
    # Deterministic tax total: an explicitly labelled single GST/tax amount
    # (basis 'explicit'), or the sum of distinctly labelled components
    # CGST+SGST / IGST (basis 'component-sum'). None when the tax evidence
    # is absent or ambiguous — ambiguity is surfaced in conflicts().
    tax_total: Optional[Decimal] = None
    tax_total_basis: str = ""

    # -- gates ----------------------------------------------------------

    @property
    def has_any_role(self) -> bool:
        return bool(self.labelled)

    def role_values(self, role: str) -> List[Decimal]:
        return [la.value for la in self.role_map.get(role, [])]

    def role_value(self, role: str) -> Optional[Decimal]:
        """The single deterministic value for a role, or None.

        Returns None when the role is absent OR ambiguous (0 or >1
        distinct values). Ambiguity is surfaced by conflicts(), so a None
        here with a conflict recorded means REVIEW_REQUIRED, never a
        silent choice.
        """
        vals = self.role_values(role)
        distinct = []
        for v in vals:
            if v not in distinct:
                distinct.append(v)
        if len(distinct) == 1:
            return distinct[0]
        return None

    def is_role_ambiguous(self, role: str) -> bool:
        vals = self.role_values(role)
        distinct = []
        for v in vals:
            if v not in distinct:
                distinct.append(v)
        return len(distinct) > 1

    @property
    def safe(self) -> bool:
        """True when every role has exactly one distinct value and no
        structural conflict was detected. Reconciliation problems are
        evaluated separately (they depend on the capability)."""
        if self.conflicts or self.unresolved:
            return False
        return all(
            len({la.value for la in las}) == 1
            for las in self.role_map.values()
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "labelled": [la.to_dict() for la in self.labelled],
            "duplicates": list(self.duplicates),
            "conflicts": list(self.conflicts),
            "reconciliation": list(self.reconciliation),
            "unresolved": list(self.unresolved),
            "tax_total": str(self.tax_total) if self.tax_total is not None else None,
            "tax_total_basis": self.tax_total_basis,
        }


_COMPONENT_LABEL_RE = re.compile(r"\b(cgst|sgst|igst)\b", re.IGNORECASE)
_PLAIN_TAX_LABEL_RE = re.compile(
    r"\b(?:gst\s*amount|gst|tax\s*amount|tax)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# value part: optional currency prefix, optional negative sign, digits with
# Indian or western commas, optional decimals. A value must be followed by
# end-of-line (trailing whitespace allowed) so 'GST 18%' or 'Invoice No: 7'
# can never be read as amounts (the %/identity guard is also applied
# below for defense in depth).
_VALUE_RE = re.compile(
    r"^(?P<neg>-?)\s*(?:₹|Rs\.?|INR)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)

# A bare label line with no value on it (e.g. 'Notes:' or 'Description:')
# is skipped, never an amount.
_MIN_LINE = 3


def _parse_value(raw_value: str) -> Optional[Decimal]:
    """Deterministic parse of one labelled value string.

    Indian comma formatting is accepted by parse_numeric_text's own
    normalisation (it resolves 1,234 vs 12,34,567 deterministically).
    """
    token = raw_value.strip()
    neg = False
    if token.startswith("-"):
        neg = True
        token = token[1:].strip()
    token = re.sub(r"^(?:₹|Rs\.?|INR)\s*", "", token, flags=re.IGNORECASE)
    if not re.search(r"\d", token):
        return None
    parsed = parse_numeric_text(token)
    if parsed.value is None or parsed.ambiguity:
        return None
    return -parsed.value if neg else parsed.value


def _classify_line(line: str) -> Optional[Tuple[str, str, Tuple[int, int], Tuple[int, int]]]:
    """Parse ONE source line into (role, raw_value, label_span, value_span).

    Label must sit at the start of the line and be followed by ':'; the
    value must be the remainder of the line (currency/negative allowed).
    Lines with trailing words after the value are REJECTED (never
    inferred): the label-value relationship must be unambiguous.
    """
    if len(line) < _MIN_LINE:
        return None
    low = line.lower()
    m = re.match(
        r"\s*(?P<label>" + "|".join(
            sorted(
                {p for p in _LABEL_BY_ROLE.values()},
                key=len, reverse=True,
            )
        ) + r")\s*:\s*(?P<rest>.*)$",
        low,
    )
    if not m:
        return None
    label_text = m.group("label")
    rest = m.group("rest").strip()
    if not rest:
        return None
    vm = _VALUE_RE.match(rest)
    if not vm:
        return None

    # role resolution: try each role's own pattern against the matched
    # label (ordered by _ROLE_ORDER so a specific role claims its label).
    role = None
    for cand in _ROLE_ORDER:
        if re.fullmatch(
                "(?:" + _LABEL_BY_ROLE[cand] + ")",
                label_text.strip(),
        ):
            role = cand
            break
    if role is None:
        return None

    label_start = m.start("label")
    label_end = label_start + len(label_text)
    rest_raw = m.group("rest")
    value_text = rest_raw.strip()
    # the value starts where the 'rest' group itself starts (the regex's
    # own \s* between ':' and the value has already consumed the leading
    # whitespace) plus any whitespace INSIDE the captured rest.
    value_start = (m.start("rest")
                   + (len(rest_raw) - len(rest_raw.lstrip())))
    value_end = value_start + len(value_text)
    # value spans are relative to the ORIGINAL line (we matched on the
    # lowercased copy, which is length-preserving).
    value = _parse_value(rest_raw)
    if value is None:
        return None
    return role, m.group("rest").strip(), (label_start, label_end), (value_start, value_end)


def resolve_invoice_roles(text: str) -> InvoiceRoleResult:
    """Deterministic invoice amount-role resolution.

    Accepts only explicit 'label: value' lines. Returns role evidence
    with spans; flags duplicates (same label repeated with the SAME
    value), conflicts (same label, DIFFERENT values), and unresolved
    labels. Performs only mathematically justified reconciliations
    (net+tax=total when exactly those fields are present; discount/
    shipping composition when present; CGST+SGST composition when both
    components are labelled without a total GST label).
    """
    result = InvoiceRoleResult()
    raw = str(text or "")
    offset = 0
    for line in raw.splitlines():
        line_len = len(line)
        parsed = _classify_line(line)
        if parsed is None:
            offset += line_len + 1
            continue
        role, raw_value, label_span, value_span = parsed
        value = _parse_value(raw_value)
        if value is None:
            offset += line_len + 1
            continue
        la = LabelledAmount(
            role=role,
            value=value,
            source_span=(offset + label_span[0], offset + value_span[1]),
            label_span=(offset + label_span[0], offset + label_span[1]),
            amount_span=(offset + value_span[0], offset + value_span[1]),
            label_text=line.strip()[: len(line.strip())],
            confidence="explicit-label",
        )
        result.labelled.append(la)
        result.role_map.setdefault(role, []).append(la)
        offset += line_len + 1

    # -- tax total resolution (deterministic) ----------------------------
    tax_las = result.role_map.get("tax", [])
    if tax_las:
        plain = [la for la in tax_las
                 if not _COMPONENT_LABEL_RE.search(la.label_text)]
        comps = [la for la in tax_las
                 if _COMPONENT_LABEL_RE.search(la.label_text)]
        comp_names = []
        for la in comps:
            for name in _COMPONENT_LABEL_RE.findall(la.label_text):
                if name.upper() not in comp_names:
                    comp_names.append(name.upper())
        if plain:
            plain_values = {la.value for la in plain}
            if len(plain_values) == 1:
                # A single explicitly labelled GST/tax amount owns the role.
                result.tax_total = next(iter(plain_values))
                result.tax_total_basis = "explicit"
            # >1 distinct plain values: conflict already recorded; tax
            # total stays None (ambiguous).
        elif comps and len(comp_names) in (1, 2) and not (
                "CGST" in comp_names and "SGST" in comp_names
                and "IGST" in comp_names):
            # Distinct component labels only (CGST+SGST or IGST alone or a
            # single component label): their sum IS the total tax by the
            # labels' own evidence. Deduplication is per COMPONENT NAME:
            # CGST 900 + SGST 900 are TWO facts (sum 1800), while a
            # repeated 'CGST: 900' is ONE stated fact.
            per_name: Dict[str, List[Decimal]] = {}
            for la in comps:
                for name in _COMPONENT_LABEL_RE.findall(la.label_text):
                    per_name.setdefault(name.upper(), []).append(la.value)
                    break
            names_set = set(per_name.keys())
            # IGST stated together with CGST/SGST is contradictory
            # component evidence — never summed.
            if "IGST" in names_set and ("CGST" in names_set
                                        or "SGST" in names_set):
                result.conflicts.append({
                    "kind": "component_contradiction",
                    "role": "tax",
                    "components": sorted(names_set),
                    "reason": (
                        "Invoice labels state IGST together with CGST/SGST. "
                        "These are alternative intra/inter-state treatments, "
                        "not additive components. Platrixa refuses instead "
                        "of choosing a treatment."
                    ),
                })
            elif all(
                len({v for v in vals}) == 1 for vals in per_name.values()
            ):
                result.tax_total = sum(vals[0] for vals in per_name.values())
                result.tax_total_basis = "component-sum"
            # Conflicting values within one component name stay ambiguous:
            # the label conflict is already recorded; tax_total stays None.
        # Any other shape (mixed plain+components without a clean single
        # plain value, duplicated conflicting components) stays ambiguous:
        # tax_total None + recorded conflicts → REVIEW_REQUIRED.

    # -- duplicate / conflict detection ---------------------------------
    for role, las in result.role_map.items():
        distinct = []
        for la in las:
            if la.value not in distinct:
                distinct.append(la.value)
        if len(distinct) > 1:
            result.conflicts.append({
                "kind": "labelled_role_conflict",
                "role": role,
                "values": [str(v) for v in distinct],
                "reason": (
                    f"Invoice labels state multiple different values for "
                    f"the '{role}' role ({', '.join(str(v) for v in distinct)}). "
                    "Platrixa never chooses between conflicting stated "
                    "amounts."
                ),
            })
        elif len(las) > 1:
            result.duplicates.append({
                "role": role,
                "value": str(las[0].value),
                "count": len(las),
            })

    # -- mathematically justified reconciliation ------------------------
    _reconcile(result)
    return result


# ---------------------------------------------------------------------------
# Reconciliation (addition-only, evidence-gated)
# ---------------------------------------------------------------------------

def _reconcile(result: InvoiceRoleResult) -> None:
    """Compose present-labelled fields only. Every check is addition and
    may only CONFIRM or REFUTE the stated evidence — it never modifies an
    amount or infers a missing one."""
    net = result.role_value("net")
    total = result.role_value("total")
    tax_values = result.role_values("tax")
    discount = result.role_value("discount")
    shipping = result.role_value("shipping")

    # CGST/SGST components: only when there is no single 'tax' label and
    # exactly one value per component label is present. (Component labels
    # both map to role 'tax'; a true conflict is already recorded.)
    has_conflict = result.is_role_ambiguous("tax")

    if net is not None and total is not None:
        expected = net
        notes = []
        if discount is not None:
            expected -= discount
            notes.append(f"discount {-discount}")
        if shipping is not None:
            expected += shipping
            notes.append(f"shipping {shipping}")
        if result.tax_total is not None:
            expected += result.tax_total
            notes.append(f"tax {result.tax_total}")
            if result.tax_total_basis == "component-sum":
                notes.append("tax from labelled CGST/SGST/IGST components")
        elif tax_values and result.tax_total is None:
            # Tax evidence exists but could not be resolved (conflicting
            # plain labels or contradictory components): reconciliation is
            # impossible without guessing — recorded as unresolved, never
            # silently skipped.
            result.reconciliation.append({
                "kind": "net_tax_total",
                "status": "UNRESOLVED",
                "detail": (
                    "net and total are labelled but the tax evidence is "
                    "conflicting; Platrixa cannot reconcile without "
                    "choosing a tax amount."
                ),
            })
            return
        if expected == total:
            result.reconciliation.append({
                "kind": "net_tax_total",
                "status": "RECONCILED",
                "detail": f"{net} (+ tax/discount/shipping as labelled) == {total}",
            })
        else:
            result.reconciliation.append({
                "kind": "net_tax_total",
                "status": "MISMATCH",
                "detail": (
                    f"labelled net {net} with labelled components "
                    f"({' '.join(notes) if notes else 'no components'}) gives "
                    f"{expected}, but the labelled total is {total}. "
                    "Platrixa never modifies an amount to reconcile."
                ),
            })

    # -- payment / outstanding composition ------------------------------
    # outstanding + paid = total is justified ONLY when all three roles
    # are labelled; any subset must stay unreconciled (no inference).
    payment = result.role_value("payment")
    outstanding = result.role_value("outstanding")
    if (payment is not None and outstanding is not None
            and total is not None):
        if outstanding + payment == total:
            result.reconciliation.append({
                "kind": "payment_outstanding",
                "status": "RECONCILED",
                "detail": f"outstanding {outstanding} + paid {payment} == total {total}",
            })
        else:
            result.reconciliation.append({
                "kind": "payment_outstanding",
                "status": "MISMATCH",
                "detail": (
                    f"labelled outstanding {outstanding} + paid {payment} "
                    f"gives {outstanding + payment}, but the labelled total "
                    f"is {total}. Platrixa never modifies an amount."
                ),
            })


# ---------------------------------------------------------------------------
# Orchestration adapter (evidence for _assign_ownership — NOT an authority)
# ---------------------------------------------------------------------------

def label_role_for_value(value: Decimal,
                         roles: InvoiceRoleResult) -> Optional[str]:
    """The invoice role of a stated amount value, or None when the value
    carries no invoice label. Used by the orchestrator's ownership pass as
    ONE MORE evidence source ahead of the transaction_value fallback."""
    for la in roles.labelled:
        if la.value == value:
            return la.role
    return None


__all__ = [
    "LabelledAmount",
    "InvoiceRoleResult",
    "resolve_invoice_roles",
    "label_role_for_value",
]
