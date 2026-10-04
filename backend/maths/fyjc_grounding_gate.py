"""
Platrixa — ExpandedInterpretation Grounding Gate (Phase 4)
==========================================================

Validates that an ExpandedInterpretation dict is sufficiently grounded
in the supplied student input text before it can proceed to the
deterministic accounting kernel.

The gate answers:
  "Is this interpretation grounded in the source text and safe for
   downstream deterministic accounting?"

It does NOT:
  - perform accounting calculations
  - generate journals
  - decide debit/credit
  - modify accounting state

Grounding rules:
  1. Parties must be supported by input text
  2. Amounts must be supported by input text
  3. Payment method explicitly stated or marked UNKNOWN
  4. Transaction type supported by input text
  5. References valid (if present)
  6. Ambiguity flags preserved (not erased)
  7. Forbidden accounting fields rejected
  8. Model cannot claim VERIFIED
  9. Confidence cannot override grounding
  10. High-confidence hallucination is still ungrounded
  11. Ungrounded interpretation remains REVIEW_REQUIRED
  12. A claimed payment method with no known evidence vocabulary is
      unverifiable and fails closed — it is never vacuously grounded
  13. Every field verdict carries an evidence classification, the method
      used to reach it, and the source span when — and only when — one was
      actually located. Provenance is descriptive: it never grants
      admission and never overrides the booleans in rules 1-12

Architecture:
    FYJCLLMSpecialist.interpret()
        ↓
    ExpandedInterpretation dict (18 fields)
        ↓
    validate_structured_interpretation()  (Phase 1 schema)
        ↓
    ExpandedGroundingGate.ground()       ← THIS MODULE
        ↓
    GroundingResult
        ↓
    if safe_for_kernel → deterministic kernel
    if not → REVIEW_REQUIRED

Safe module: no Streamlit, no AI model, no network. Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Evidence provenance contract (Phase 2 — additive)
# ---------------------------------------------------------------------------
#
# The gate's admission booleans are UNCHANGED by this layer. Evidence
# metadata is descriptive provenance only: it explains WHY a field was
# accepted or rejected. It never grants admission, and it never overrides
# ``grounded`` / ``safe_for_kernel``.
#
# Version is bumped independently of GROUNDING_VERSION (backend/semantics/
# evidence.py), which is asserted verbatim by fte_fyjc_67 and is left alone.

EVIDENCE_CONTRACT_VERSION = "grounding-evidence-2"


class EvidenceClass(str, Enum):
    """How the source supports (or fails to support) one claimed field.

    DIRECT       the claimed value occurs verbatim in the source text
    DERIVED      computed deterministically from grounded inputs
                 (no field is DERIVED today — reserved, never faked)
    SEMANTIC     supported by a controlled vocabulary table, not verbatim
    AMBIGUOUS    the candidate asserts nothing, so no evidence exists to
                 verify; the field is undetermined, not accepted-as-known
    UNSUPPORTED  the candidate asserts a value that the source does not
                 contain
    CONTRADICTED the candidate asserts something the source or the
                 accounting authority contract forbids
    """

    DIRECT = "DIRECT"
    DERIVED = "DERIVED"
    SEMANTIC = "SEMANTIC"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"


# Evidence classes that may accompany ``grounded=True`` for a field the
# candidate actually ASSERTS. Anything else that is asserted is rejected.
ACCEPTING_EVIDENCE_CLASSES = frozenset({
    EvidenceClass.DIRECT,
    EvidenceClass.DERIVED,
    EvidenceClass.SEMANTIC,
})

# Evidence classes that must never be silently promoted to accepted evidence.
REJECTING_EVIDENCE_CLASSES = frozenset({
    EvidenceClass.UNSUPPORTED,
    EvidenceClass.CONTRADICTED,
})


class Uncertainty(str, Enum):
    """Residual uncertainty attached to one field verdict."""

    NONE = "NONE"                    # claim fully evidenced
    NO_CLAIM = "NO_CLAIM"            # candidate asserted nothing here
    UNRESOLVED = "UNRESOLVED"        # evidenced, but no exact span locatable
    REJECTED = "REJECTED"            # evidence absent or contradictory


# ---------------------------------------------------------------------------
# Grounding Result
# ---------------------------------------------------------------------------

@dataclass
class FieldGrounding:
    """Grounding status for a single extracted field.

    Fields 1-3 are the original public contract and are unchanged. Every field
    below is ADDITIVE and defaulted, so all existing construction sites and
    all existing consumers keep working byte-for-byte.
    """
    field_name: str
    grounded: bool
    reason: str
    source_evidence: str = ""  # the substring from input that supports this

    # --- additive provenance (Phase 2) ----------------------------------
    evidence_class: EvidenceClass = EvidenceClass.UNSUPPORTED
    resolution: str = ""          # how the verdict was reached
    span_text: str = ""           # the exact matched text from the source
    source_span: Optional[Tuple[int, int]] = None  # real offsets, or None
    span_note: str = ""           # why a span is unavailable, when it is
    uncertainty: Uncertainty = Uncertainty.NONE

    def to_dict(self) -> Dict[str, Any]:
        """Serializable provenance record (additive; nothing else uses this)."""
        return {
            "field_name": self.field_name,
            "grounded": self.grounded,
            "reason": self.reason,
            "source_evidence": self.source_evidence,
            "evidence_class": self.evidence_class.value
            if isinstance(self.evidence_class, EvidenceClass)
            else str(self.evidence_class),
            "resolution": self.resolution,
            "span_text": self.span_text,
            "source_span": list(self.source_span) if self.source_span else None,
            "span_note": self.span_note,
            "uncertainty": self.uncertainty.value
            if isinstance(self.uncertainty, Uncertainty)
            else str(self.uncertainty),
        }


@dataclass
class GroundingResult:
    """Complete grounding decision for an ExpandedInterpretation."""
    grounded: bool
    safe_for_kernel: bool
    review_required: bool
    issues: List[str] = field(default_factory=list)
    field_results: List[FieldGrounding] = field(default_factory=list)
    suggested_status: str = "REVIEW_REQUIRED"

    # --- additive provenance (Phase 2) ----------------------------------
    evidence_version: str = EVIDENCE_CONTRACT_VERSION
    evidence_counts: Dict[str, int] = field(default_factory=dict)
    uncertain_fields: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.issues:
            return "All grounding checks passed"
        return "; ".join(self.issues)

    def to_dict(self) -> Dict[str, Any]:
        """Serializable decision record including provenance (additive)."""
        out = {
            "grounded": self.grounded,
            "safe_for_kernel": self.safe_for_kernel,
            "review_required": self.review_required,
            "suggested_status": self.suggested_status,
            "issues": list(self.issues),
            "evidence_version": self.evidence_version,
            "evidence_counts": dict(self.evidence_counts),
            "uncertain_fields": list(self.uncertain_fields),
            "field_results": [f.to_dict() for f in self.field_results],
        }
        return out


def _record(
    field_name: str,
    grounded: bool,
    reason: str,
    *,
    evidence_class: EvidenceClass,
    resolution: str,
    source_evidence: str = "",
    span: Optional[Tuple[int, int]] = None,
    span_text: str = "",
    span_note: str = "",
    uncertainty: Optional[Uncertainty] = None,
) -> FieldGrounding:
    """Build a FieldGrounding with provenance attached.

    A span is recorded only when it was actually located in the source text.
    When no span can be identified, ``span_note`` says so explicitly — offsets
    and matched text are never invented.
    """
    if uncertainty is None:
        if grounded:
            uncertainty = (Uncertainty.NONE if span is not None
                           else Uncertainty.UNRESOLVED)
        else:
            uncertainty = Uncertainty.REJECTED
    return FieldGrounding(
        field_name=field_name,
        grounded=grounded,
        reason=reason,
        source_evidence=source_evidence,
        evidence_class=evidence_class,
        resolution=resolution,
        span_text=span_text,
        source_span=span,
        span_note=span_note,
        uncertainty=uncertainty,
    )


def _evidence_counts(field_results: List[FieldGrounding]) -> Dict[str, int]:
    counts = {member.value: 0 for member in EvidenceClass}
    for fr in field_results:
        cls = fr.evidence_class
        key = cls.value if isinstance(cls, EvidenceClass) else str(cls)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _uncertain_fields(field_results: List[FieldGrounding]) -> List[str]:
    return [fr.field_name for fr in field_results
            if isinstance(fr.evidence_class, EvidenceClass)
            and fr.evidence_class is EvidenceClass.AMBIGUOUS]


# ---------------------------------------------------------------------------
# Helper: text containment check
# ---------------------------------------------------------------------------

def _text_contains(text: str, candidate: str) -> bool:
    """Check if candidate is approximately contained in text.

    Uses case-insensitive substring matching with fuzzy normalization.
    Handles common variations: Rs./₹/INR, commas, periods.
    """
    if not candidate or not text:
        return False

    # Normalize both strings
    def _norm(s: str) -> str:
        s = s.lower().strip()
        # Remove currency prefixes before stripping punctuation
        s = re.sub(r"rs\.?\s*", "", s)
        s = re.sub(r"inr\s*", "", s)
        s = re.sub(r"₹\s*", "", s)
        s = re.sub(r"rupees?\s*", "", s)
        # Strip all non-alphanumeric chars (commas, dots, spaces)
        s = re.sub(r"[^a-z0-9]", "", s)
        return s.strip()

    text_norm = _norm(text)
    candidate_norm = _norm(candidate)

    if not candidate_norm:
        return False

    return candidate_norm in text_norm


# ---------------------------------------------------------------------------
# Payment-method evidence vocabulary
# ---------------------------------------------------------------------------
#
# This is DOMAIN KNOWLEDGE, kept out of the control flow so it can be extended
# (or generalized into a per-domain evidence profile) without touching the gate.
#
# INVARIANT (audit M-03): every non-UNKNOWN PaymentMethodEnum value in
# backend/maths/fyjc_contract.py MUST have an entry here. A claimed method
# with no entry is UNVERIFIABLE and fails closed; it is never vacuously
# grounded (see _resolve_payment_method_evidence and Rule 4).
PAYMENT_METHOD_EVIDENCE: Dict[str, Tuple[str, ...]] = {
    "cash": ("cash",),
    "credit": ("credit", "on account"),
    "cheque": ("cheque", "check"),
    # "bank" keeps its pre-M-03 vocabulary verbatim, including the instrument
    # words: narrowing it would reject candidates the gate used to accept.
    "bank": ("bank", "bank transfer", "neft", "rtgs", "imps"),
    "neft": ("neft", "rtgs", "imps", "bank transfer", "net banking"),
    "upi": ("upi", "phonepe", "paytm", "gpay", "google pay"),
}

# Spellings that resolve onto a canonical method above. Vocabulary is taken
# from the mappings already used elsewhere in the repo (fyjc_contract.py:176-178,
# invoice_executor.py:63-66).
PAYMENT_METHOD_ALIASES: Dict[str, str] = {
    "rtgs": "neft",
    "imps": "neft",
    "online transfer": "neft",
    "bank transfer": "bank",
    "banktransfer": "bank",
    "cheq": "cheque",
    "check": "cheque",
    "phonepe": "upi",
    "paytm": "upi",
    "gpay": "upi",
    "google pay": "upi",
}


def _resolve_payment_method_evidence(claim: Any) -> Optional[Tuple[str, Tuple[str, ...]]]:
    """Map a claimed payment method onto its evidence vocabulary.

    Returns ``(canonical_method, keywords)``, or ``None`` when the claim is not
    a recognised payment method.

    ``None`` is NOT an implicit pass. Before audit M-03 the gate looked the
    claim up in an inline table and, when the lookup missed, ``keywords`` was
    the empty list — so the ``if keywords and ...`` guard short-circuited to
    ``grounded=True``. Any value missing from the table therefore passed the
    payment-method rule with no evidence at all: e.g. a candidate claiming
    ``NEFT`` against the source text ``Paid 1250 cash`` was reported as
    grounded and admitted to the deterministic kernel. The caller now fails
    closed on ``None`` and on an empty keyword set.
    """
    if not isinstance(claim, str):
        return None
    key = re.sub(r"[^a-z0-9]+", " ", claim.lower()).strip()
    if not key:
        return None
    if key in PAYMENT_METHOD_EVIDENCE:
        return key, PAYMENT_METHOD_EVIDENCE[key]
    alias = PAYMENT_METHOD_ALIASES.get(key)
    if alias is not None:
        return alias, PAYMENT_METHOD_EVIDENCE[alias]
    # Free-text legacy values such as "by cash" / "cash payment".
    for canonical, keywords in PAYMENT_METHOD_EVIDENCE.items():
        if re.search(rf"\b{re.escape(canonical)}\b", key):
            return canonical, keywords
    return None


# ---------------------------------------------------------------------------
# Source-span location and token-boundary entity matching (Phase 2)
# ---------------------------------------------------------------------------
#
# A span is reported ONLY when it can be located exactly in the original
# source text. When matching relies on normalization (punctuation folding,
# currency stripping), no offsets are produced — the limitation is stated in
# ``span_note`` instead of being papered over with invented offsets.

# Separators tolerated between the tokens of a multi-word entity.
_ENTITY_SEP = r"[^A-Za-z0-9]+"


# A candidate whose every token is a currency marker is not an entity.
# Without this, token matching accepts party="Rs" whenever the source happens
# to carry an "Rs." prefix — the currency word is not a counterparty.
_CURRENCY_TOKENS = frozenset({"rs", "inr", "rupee", "rupees"})


def _entity_pattern(candidate: str) -> Optional["re.Pattern[str]"]:
    """Token-boundary-safe pattern for a candidate entity string.

    Each alphanumeric token of the candidate must appear as a whole token in
    the source, with any run of non-alphanumeric characters allowed between
    them (so "Iyer and Co" still matches "Iyer and Co."). Word boundaries at
    both ends are what makes "Ravi" stop matching "Ravikesh" (audit D6).

    Returns None for a candidate that is only a currency marker, or that has
    no alphanumeric token at all — neither can be a grounded counterparty.
    """
    if not isinstance(candidate, str):
        return None
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", candidate.lower()) if t]
    if not tokens:
        return None
    if all(t in _CURRENCY_TOKENS for t in tokens):
        return None
    body = _ENTITY_SEP.join(re.escape(t) for t in tokens)
    return re.compile(rf"\b{body}\b", re.IGNORECASE)


def _locate_entity(text: str, candidate: str) -> Optional[Tuple[int, int, str]]:
    """Token-boundary-safe containment check that also returns the span.

    Returns ``(start, end, matched_text)`` or None.
    """
    pattern = _entity_pattern(candidate)
    if not text or pattern is None:
        return None
    match = pattern.search(text)
    if match is None:
        return None
    return match.start(), match.end(), match.group(0)


def _entity_in_text(text: str, candidate: str) -> bool:
    """Token-boundary-safe replacement for _text_contains in grounding rules.

    _text_contains (kept unchanged for its existing callers and tests)
    normalizes by stripping every non-alphanumeric character and then does
    substring containment, so "Ravi" matched "Ravikesh" and "acme" matched
    "Acme Corp" — fabricated-looking parties grounded clean. This matcher
    requires whole-token equality instead.
    """
    return _locate_entity(text, candidate) is not None


def _locate_numeric_token(text: str, canonical_digits: str) -> Optional[Tuple[int, int, str]]:
    """Locate the numeric token whose canonical digits equal ``canonical_digits``."""
    if not text or not canonical_digits:
        return None
    for match in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        digits = re.sub(r"[^0-9]", "", match.group(0))
        if digits and (digits.lstrip("0") or "0") == canonical_digits:
            return match.start(), match.end(), match.group(0)
    return None


def _locate_amount(text: str, amount_value: str) -> Tuple[Optional[Tuple[int, int]], str, str]:
    """Best-effort span for a grounded amount.

    Returns ``(span, span_text, span_note)``. The span is None whenever the
    amount only grounds through a derived form ("5 thousand", "5k") or a
    normalized spelling — in that case the note states why.
    """
    val = (amount_value or "").replace(",", "").replace(".", "").strip()
    if val and not re.search(r"[A-Za-z₹$€£]", amount_value or ""):
        digits = re.sub(r"[^0-9]", "", val)
        if digits:
            canonical = digits.lstrip("0") or "0"
            found = _locate_numeric_token(text, canonical)
            if found:
                start, end, matched = found
                return (start, end), matched, ""
    try:
        num = int(val)
    except (ValueError, TypeError):
        return None, "", "amount spans are unavailable for non-numeric candidate values"
    if num >= 1000 and num % 1000 == 0:
        thousands = num // 1000
        for phrase in (rf"\b{thousands}\s+thousand\b", rf"\b{thousands}\s*k\b"):
            found = re.search(phrase, text or "", re.IGNORECASE)
            if found:
                return None, found.group(0), (
                    "amount grounds through the derived form "
                    f"{found.group(0)!r}; no single numeric token in the source "
                    "denotes the amount, so no offsets are claimed"
                )
    return None, "", "amount grounded after normalization; no exact source token located"


def _locate_keyword(text: str, keywords: Tuple[str, ...]) -> Optional[Tuple[int, int, str]]:
    """Locate the first evidence keyword occurring in the source text."""
    for kw in keywords:
        found = re.search(re.escape(kw), text or "", re.IGNORECASE)
        if found:
            return found.start(), found.end(), found.group(0)
    return None


def _numeric_tokens(text: str) -> set:
    """Every numeric token in ``text``, normalized to a canonical digit string.

    A token is a maximal run of digits (with optional internal thousands
    separators and an optional decimal part). Normalization drops separators
    and leading zeros, so "90,000", "₹90000" and "090000" all canonicalize to
    "90000".
    """
    out = set()
    for match in re.finditer(r"\d[\d,]*(?:\.\d+)?", text or ""):
        digits = re.sub(r"[^0-9]", "", match.group(0))
        if digits:
            out.add(digits.lstrip("0") or "0")
    return out


def _amount_in_text(text: str, amount_value: str) -> bool:
    """Check if a monetary amount is supported by the input text.

    Handles: 25000, 25,000, Rs.25000, ₹25,000, 25k, 90 thousand, etc.

    SECURITY (audit M-02, 2026-09-29)
    ---------------------------------
    This function previously used ``val in text_digits_only`` — pure
    SUBSTRING containment over the digits of the source. Against
    "Rs. 90,000" that made "90" (1000x understated), "0", "9", "000" and
    even the plausible-looking "9,000" (10x wrong) all report as
    grounded. A smaller or different numeric substring was being accepted
    as proof that the candidate amount appears in the source.

    Matching is now TOKEN-BOUNDED NUMERIC EQUALITY: the candidate amount
    must correspond to a complete numeric token present in the source.
    Substrings of a longer number no longer ground.

    The accepted contract is unchanged, not widened:
      * plain digits and comma-grouped digits  -> supported
      * "X thousand" / "Xk"                    -> still supported
      * anything carrying a currency word       -> still NOT supported
        (unchanged; see fte_sec_01 D-section notes)
    """
    if not amount_value or not text:
        return False

    # Normalize the amount value (unchanged contract).
    val = amount_value.replace(",", "").replace(".", "").strip()

    # A candidate that contains letters or a currency symbol is not a plain
    # number. Those forms were never grounded; keep that behaviour.
    candidate_is_plain_number = bool(val) and not re.search(r"[A-Za-z₹$€£]", amount_value)

    if candidate_is_plain_number:
        candidate_digits = re.sub(r"[^0-9]", "", val)
        if candidate_digits:
            canonical = candidate_digits.lstrip("0") or "0"
            if canonical in _numeric_tokens(text):
                return True

    # Try "X thousand" / "Xk" format (unchanged).
    try:
        num = int(val)
        if num >= 1000:
            thousands = num // 1000
            remainder = num % 1000
            if remainder == 0:
                lowered = text.lower()
                if f"{thousands} thousand" in lowered:
                    return True
                if f"{thousands}k" in lowered:
                    return True
    except (ValueError, TypeError):
        pass

    return False


# ---------------------------------------------------------------------------
# ExpandedInterpretation Grounding Gate
# ---------------------------------------------------------------------------

class ExpandedGroundingGate:
    """Grounds an ExpandedInterpretation dict against the source text.

    Validates that every extracted fact is actually supported by the
    student's input text. Prevents hallucinated/fabricated data from
    reaching the deterministic accounting kernel.

    Usage:
        gate = ExpandedGroundingGate()
        result = gate.ground(interpretation_dict, source_text)
        if result.safe_for_kernel:
            # proceed to kernel
        else:
            # return REVIEW_REQUIRED
    """

    # Fields that MUST be grounded (not just inferred)
    REQUIRED_GROUNDED_FIELDS = {"transaction_type", "parties"}

    # Minimum number of grounded fields required
    MIN_GROUNDED_FIELDS = 1

    def ground(
        self,
        interpretation: Dict[str, Any],
        source_text: str,
    ) -> GroundingResult:
        """Validate grounding of an ExpandedInterpretation against source text.

        Args:
            interpretation: 18-field ExpandedInterpretation dict.
            source_text: The original student input text.

        Returns:
            GroundingResult with grounded status and field-level details.
        """
        issues: List[str] = []
        field_results: List[FieldGrounding] = []

        # --- Rule 0: Cannot claim VERIFIED ---
        suggested = interpretation.get("suggested_status", "REVIEW_REQUIRED")
        if suggested == "VERIFIED":
            issues.append(
                "AI attempted to claim VERIFIED status. "
                "Only the accounting kernel may produce VERIFIED."
            )

        # --- Rule 1: Forbidden accounting fields ---
        forbidden = {"journal", "debit_lines", "credit_lines", "ledger",
                     "balances", "debit_account", "credit_account", "journal_entry"}
        found_forbidden = forbidden & set(interpretation.keys())
        if found_forbidden:
            issues.append(
                f"Forbidden accounting truth fields present: {sorted(found_forbidden)}"
            )

        # --- Rule 2: Parties grounding ---
        parties = interpretation.get("parties", [])
        if parties:
            grounded_parties = []
            for party in parties:
                # Token-boundary matching (audit D6): _entity_in_text replaces
                # the substring check that let "Ravi" match "Ravikesh".
                located = _locate_entity(source_text, party)
                if located is not None:
                    start, end, matched = located
                    grounded_parties.append(party)
                    field_results.append(_record(
                        field_name=f"party:{party}",
                        grounded=True,
                        reason=f"Party '{party}' found in source text",
                        source_evidence=party,
                        evidence_class=EvidenceClass.DIRECT,
                        resolution="token_match",
                        span=(start, end),
                        span_text=matched,
                    ))
                else:
                    issues.append(f"Party '{party}' not supported by input text")
                    field_results.append(_record(
                        field_name=f"party:{party}",
                        grounded=False,
                        reason=f"Party '{party}' NOT found in source text — possibly fabricated",
                        evidence_class=EvidenceClass.UNSUPPORTED,
                        resolution="evidence_absent",
                        span_note=("no whole-token occurrence of the party in the "
                                   "source text (or the candidate is a currency "
                                   "marker rather than an entity)"),
                    ))
        else:
            # No parties extracted — this may be legitimate (e.g., minimal input)
            field_results.append(_record(
                field_name="parties",
                grounded=True,  # empty parties is valid (marked as MISSING_PARTY in ambiguity)
                reason="No parties claimed — ambiguity flag should reflect this",
                evidence_class=EvidenceClass.AMBIGUOUS,
                resolution="no_claim",
                span_note="candidate asserts no party, so there is nothing to locate in the source",
                uncertainty=Uncertainty.NO_CLAIM,
            ))

        # --- Rule 3: Amounts grounding ---
        amounts = interpretation.get("amounts", [])
        if amounts:
            for amt in amounts:
                val = amt.get("value", "") if isinstance(amt, dict) else str(amt)
                source = amt.get("source", "explicit") if isinstance(amt, dict) else "explicit"
                if _amount_in_text(source_text, val):
                    span, span_text, span_note = _locate_amount(source_text, val)
                    is_derived_form = bool(span_note) and not span
                    field_results.append(_record(
                        field_name=f"amount:{val}",
                        grounded=True,
                        reason=f"Amount {val} found in source text",
                        source_evidence=val,
                        evidence_class=(EvidenceClass.DERIVED if is_derived_form
                                        else EvidenceClass.DIRECT),
                        resolution=("derived_form" if is_derived_form
                                    else "numeric_token_match"),
                        span=span,
                        span_text=span_text,
                        span_note=span_note,
                        uncertainty=(Uncertainty.UNRESOLVED if span is None
                                     else Uncertainty.NONE),
                    ))
                else:
                    issues.append(f"Amount {val} not supported by input text")
                    field_results.append(_record(
                        field_name=f"amount:{val}",
                        grounded=False,
                        reason=f"Amount {val} NOT found in source text — possibly fabricated",
                        evidence_class=EvidenceClass.UNSUPPORTED,
                        resolution="evidence_absent",
                        span_note="no numeric token of the source denotes this amount",
                    ))
        else:
            field_results.append(_record(
                field_name="amounts",
                grounded=True,
                reason="No amounts claimed",
                evidence_class=EvidenceClass.AMBIGUOUS,
                resolution="no_claim",
                span_note="candidate asserts no amount, so there is nothing to locate in the source",
                uncertainty=Uncertainty.NO_CLAIM,
            ))

        # --- Rule 4: Payment method ---
        pm = interpretation.get("payment_method_enum", "")
        pm_legacy = interpretation.get("payment_method", "")
        effective_pm = pm or pm_legacy
        pm_is_unknown = isinstance(effective_pm, str) and effective_pm.strip().upper() == "UNKNOWN"
        if effective_pm and not pm_is_unknown:
            # FAIL CLOSED on a claim we cannot verify (audit M-03).
            #
            # The lookup below is now exhaustive-by-construction: an
            # unrecognised method yields keywords=None/() and is reported as an
            # issue instead of vacuously grounding. See
            # _resolve_payment_method_evidence for the failure this closes.
            resolved_pm = _resolve_payment_method_evidence(effective_pm)
            text_lower = source_text.lower()
            keywords: Tuple[str, ...] = resolved_pm[1] if resolved_pm else ()
            if not keywords:
                issues.append(
                    f"Payment method '{effective_pm}' is not a recognised "
                    "payment method — unverifiable claim cannot ground"
                )
                field_results.append(_record(
                    field_name="payment_method",
                    grounded=False,
                    reason=(
                        f"Payment method '{effective_pm}' has no evidence "
                        "vocabulary — claim cannot be verified against text"
                    ),
                    evidence_class=EvidenceClass.UNSUPPORTED,
                    resolution="no_evidence_vocabulary",
                    span_note="claim has no controlled vocabulary, so no evidence term can be searched",
                ))
            elif not any(kw in text_lower for kw in keywords):
                issues.append(
                    f"Payment method '{effective_pm}' not explicitly supported by input text"
                )
                field_results.append(_record(
                    field_name="payment_method",
                    grounded=False,
                    reason=f"Payment method '{effective_pm}' claimed but not mentioned in text",
                    evidence_class=EvidenceClass.UNSUPPORTED,
                    resolution="keyword_table",
                    span_note="no evidence term for this method occurs in the source text",
                ))
            else:
                located = _locate_keyword(source_text, keywords)
                span = (located[0], located[1]) if located else None
                field_results.append(_record(
                    field_name="payment_method",
                    grounded=True,
                    reason=f"Payment method '{effective_pm}' supported by text",
                    evidence_class=EvidenceClass.SEMANTIC,
                    resolution="keyword_table",
                    span=span,
                    span_text=located[2] if located else "",
                    span_note="" if located else "evidence term matched but no offset could be located",
                ))
        else:
            field_results.append(_record(
                field_name="payment_method",
                grounded=True,
                reason="Payment method is UNKNOWN — correctly not fabricated",
                evidence_class=EvidenceClass.AMBIGUOUS,
                resolution="no_claim",
                span_note="no instrument claimed; the source is silent on how payment was made",
                uncertainty=Uncertainty.NO_CLAIM,
            ))

        # --- Rule 5: Transaction type ---
        tx_type = interpretation.get("transaction_type_enum", "") or interpretation.get("transaction_type", "")
        if tx_type and tx_type != "UNKNOWN":
            # Check if transaction keywords are in text
            tx_keywords = {
                "purchase": ["purchased", "bought", "procured", "acquired"],
                "sale": ["sold", "supplied", "delivered"],
                "payment": ["paid", "payment", "settled"],
                "receipt": ["received", "receipt"],
                "capital": ["capital", "invested", "started business"],
                "expense": ["paid rent", "paid salary", "paid electricity", "paid wages"],
                "return": ["returned", "return", "purchase return", "sales return"],
                "drawing": ["withdrew", "drew", "drawing", "personal use"],
            }
            tx_lower = tx_type.lower()
            # Find matching category
            matched = False
            matched_keywords: Tuple[str, ...] = ()
            for cat, kws in tx_keywords.items():
                if cat in tx_lower or tx_lower in cat:
                    hit = tuple(kw for kw in kws if kw in source_text.lower())
                    if hit:
                        matched = True
                        matched_keywords = hit
                        break
            if not matched:
                # FAIL CLOSED (audit M-01, 2026-09-29).
                #
                # This branch previously recorded FieldGrounding(grounded=False)
                # but did NOT append to `issues`. Because the aggregate is
                # `grounded = len(issues) == 0`, the gate then reported a
                # self-contradictory result:
                #
                #     field_results: transaction_type.grounded == False
                #     grounded: True   issues: []   safe_for_kernel: True
                #
                # i.e. an unverifiable transaction_type was admitted to the
                # deterministic kernel while the gate's own field record said
                # it was ungrounded. Every other rule (parties, amounts,
                # payment method, references) already appended to `issues`;
                # Rule 5 was the sole exception.
                #
                # The field is still SEMANTICALLY CHECKED — it is not removed
                # from grounding. A type that cannot be corroborated by the
                # source text is now reported as an issue, which forces
                # grounded=False -> safe_for_kernel=False -> no VERIFIED.
                issues.append(
                    f"Transaction type '{tx_type}' could not be verified from "
                    "the input text — ungrounded transaction type is not "
                    "admitted to deterministic accounting"
                )
                field_results.append(_record(
                    field_name="transaction_type",
                    grounded=False,
                    reason=f"Transaction type '{tx_type}' could not be directly verified from text keywords",
                    evidence_class=EvidenceClass.UNSUPPORTED,
                    resolution="keyword_table",
                    span_note="no transaction-type evidence term occurs in the source text",
                ))
            else:
                located = _locate_keyword(source_text, matched_keywords)
                field_results.append(_record(
                    field_name="transaction_type",
                    grounded=True,
                    reason=f"Transaction type '{tx_type}' supported by text",
                    evidence_class=EvidenceClass.SEMANTIC,
                    resolution="keyword_table",
                    span=(located[0], located[1]) if located else None,
                    span_text=located[2] if located else "",
                    span_note="" if located else "evidence term matched but no offset could be located",
                ))
        else:
            field_results.append(_record(
                field_name="transaction_type",
                grounded=True,
                reason="Transaction type is UNKNOWN",
                evidence_class=EvidenceClass.AMBIGUOUS,
                resolution="no_claim",
                span_note="no transaction type claimed; the source supports no classification",
                uncertainty=Uncertainty.NO_CLAIM,
            ))

        # --- Rule 6: Reference validation ---
        ref_idx = interpretation.get("referenced_transaction_index")
        if ref_idx is not None:
            try:
                idx = int(ref_idx)
                if idx < 0:
                    issues.append(f"Invalid reference index: {idx}")
                    field_results.append(_record(
                        field_name="referenced_transaction_index",
                        grounded=False,
                        reason=f"Negative reference index {idx}",
                        evidence_class=EvidenceClass.CONTRADICTED,
                        resolution="structural_check",
                        span_note="structural validation of the candidate; no source span applies",
                    ))
                else:
                    field_results.append(_record(
                        field_name="referenced_transaction_index",
                        grounded=True,
                        reason=f"Reference index {idx} is structurally valid",
                        evidence_class=EvidenceClass.SEMANTIC,
                        resolution="structural_check",
                        span_note="structural validation of the candidate; no source span applies",
                        uncertainty=Uncertainty.NONE,
                    ))
            except (ValueError, TypeError):
                issues.append(f"Non-integer reference index: {ref_idx}")
                field_results.append(_record(
                    field_name="referenced_transaction_index",
                    grounded=False,
                    reason=f"Invalid reference index type: {ref_idx}",
                    evidence_class=EvidenceClass.CONTRADICTED,
                    resolution="structural_check",
                    span_note="structural validation of the candidate; no source span applies",
                ))

        # --- Rule 7: Ambiguity preservation ---
        ambig_flags = interpretation.get("ambiguity_flags", [])
        if not ambig_flags:
            field_results.append(_record(
                field_name="ambiguity_flags",
                grounded=True,
                reason="No ambiguity flags (NONE implied)",
                evidence_class=EvidenceClass.AMBIGUOUS,
                resolution="no_claim",
                span_note="candidate recorded no ambiguity flag, so nothing is evidenced here",
                uncertainty=Uncertainty.NO_CLAIM,
            ))

        # --- Rule 8: Confidence cannot bypass grounding ---
        oc = interpretation.get("overall_confidence", "0.0")
        try:
            conf = float(oc)
            if conf > 0.95 and issues:
                issues.append(
                    f"High confidence ({conf}) but grounding issues detected — "
                    "confidence does not override grounding"
                )
        except (ValueError, TypeError):
            pass

        # --- Determine result ---
        grounded = len(issues) == 0
        has_forbidden = bool(found_forbidden)
        has_claimed_verified = suggested == "VERIFIED"

        safe_for_kernel = (
            grounded
            and not has_forbidden
            and not has_claimed_verified
        )

        review_required = not safe_for_kernel

        status = "REVIEW_REQUIRED" if review_required else suggested

        return GroundingResult(
            grounded=grounded,
            safe_for_kernel=safe_for_kernel,
            review_required=review_required,
            issues=issues,
            field_results=field_results,
            suggested_status=status,
            evidence_version=EVIDENCE_CONTRACT_VERSION,
            evidence_counts=_evidence_counts(field_results),
            uncertain_fields=_uncertain_fields(field_results),
        )
