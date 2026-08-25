"""
Platrixa
Sprint 15I-VY - Linguistic Normalization & Contradiction Safety Hardening
backend/maths/fyjc_normalization.py

Deterministic linguistic normalization of messy student language BEFORE
structured transaction interpretation.

Supported high-confidence normalizations (FYJC accounting context):
  * 'gds'            -> 'goods'            (common shorthand)
  * '10k'/'1.5k'     -> '10,000'/'1,500'   (informal numeric notation)
  * 'td'/'t.d.'      -> 'trade discount'   (FYJC discount abbreviation)
  * 'cd'/'c.d.'      -> 'cash discount'    (FYJC discount abbreviation)
  * whitespace/casing collapse             (harmless formatting)

Every applied rule records its provenance (original text, normalized
representation, rule id, confidence, semantic-change flag) so the caller
can show exactly what was normalized and why.

Safety boundary (hard rule):
  * The layer NEVER invents an account, amount, rate, transaction type
    or party identity.
  * A token whose meaning cannot be established deterministically (an
    unknown abbreviation, or a single-letter initial) is surfaced as a
    CONCERN - the caller must return REVIEW_REQUIRED, never guess.
  * 'raam' is never silently promoted to 'Ram': party tokens are never
    rewritten, so the existing deterministic party-resolution rules stay
    the ONLY authority on identity.

Sprint 15I-VY also hosts the global mathematical contradiction
validation (math_contradiction) and the production wrapper (vy_harden)
that every entry - Study / Verify, student flow, QuestionBank reference -
routes through:

  raw input -> linguistic normalization -> safety concerns ->
  global math contradiction validation -> hardened authority ->
  debit/credit balancing invariant -> canonical result

A mathematically contradictory transaction can never reach VERIFIED
merely because its resulting journal happens to balance.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

# Sprint 15I-DISC: the INVALID_INPUT_MATH status is the 15I-VY
# contradiction layer's verdict constant (defined by the hardened engine
# and used by math_contradiction here). It is re-exported from this
# boundary module so the Discrepancy Authority - which runs the SAME
# contradiction gate first - reads the status from the same place. The
# import is cycle-free: the hardened engine and its module-scope imports
# never import this module at module scope.
from backend.maths.fyjc_bk_reasoning import INVALID_INPUT_MATH  # noqa: E402

# ---------------------------------------------------------------------------
# Normalization tables (deterministic)
# ---------------------------------------------------------------------------

_GOODS_RE = re.compile(r"\bgds\.?\b", re.IGNORECASE)

# 'frm' -> 'from' (common student abbreviation)
_FRM_RE = re.compile(r"\bfrm\.?\b", re.IGNORECASE)

# 'chq' -> 'cheque' (common student abbreviation)
_CHQ_RE = re.compile(r"\bchq\.?\b", re.IGNORECASE)

# '10k' / '1.5k' -> thousands. The 'k' must be directly attached to the
# number and end at a word boundary, so '20kg' or '20 k' never match.
_K_SUFFIX_RE = re.compile(r"\b(\d+(?:\.\d+)?)k\b", re.IGNORECASE)

# 'td' / 't.d.' / 'TD' / 'T.D.' -> trade discount. The trailing dot is
# consumed when present (a dotted abbreviation's final '.' is part of the
# token, not a sentence boundary; if it DID terminate the sentence, the
# boundary is restored by the substitution logic).
_TD_RE = re.compile(r"\bt\.?d\.?(?=\s|$|[^A-Za-z0-9.])", re.IGNORECASE)

# 'cd' / 'c.d.' / 'CD' / 'C.D.' -> cash discount (same rule).
_CD_RE = re.compile(r"\bc\.?d\.?(?=\s|$|[^A-Za-z0-9.])", re.IGNORECASE)

# Sprint 15I-BILLS: 'p.a.' / 'P.A.' -> 'per annum' (annual rate, an
# unambiguous abbreviation in the FYJC accounting context - a bare 'p.'
# would otherwise trip the single-letter safety gate below). The
# trailing dot is consumed when present; the lookahead (not \b) accepts
# a following space / punctuation / sentence end.
_PA_RE = re.compile(r"\bp\.a\.?(?=\s|$|[^A-Za-z0-9.])", re.IGNORECASE)

_WS_RE = re.compile(r"[ \t\r\n]+")

# Short tokens that are safe, ordinary English / FYJC vocabulary and are
# NEVER treated as unknown abbreviations. Vowel-less two/three-letter
# lowercase tokens outside this set look like abbreviations ('xd', 'gt')
# and force REVIEW_REQUIRED.
_SAFE_SHORT_TOKENS = frozenset({
    # currency / units / titles / accounting
    "rs", "inr", "dr", "cr", "mr", "mrs", "ms", "st", "vs", "gst",
    "cgst", "sgst", "igst",
    "amt", "qty", "kg", "ltd", "nos",
    # articles / prepositions / conjunctions / pronouns / common verbs
    "a", "an", "the", "on", "at", "of", "in", "for", "by", "to", "from",
    "and", "or", "per", "net", "less", "not", "but", "so", "if", "as",
    "be", "is", "it", "he", "she", "we", "us", "our", "my", "me", "you",
    "your", "up", "down", "off", "out", "own", "new", "old", "any",
    "all", "one", "two", "via", "had", "has", "was", "did", "due",
    "pay", "tax", "sum", "add", "use", "get", "got", "may", "can",
    "who", "how", "why", "etc", "i", "neft",
})

# Single letters that are always safe (article 'a' / first person 'i').
_SAFE_SINGLE_LETTERS = frozenset({"a", "i"})

# A single letter directly adjacent to '/' is part of a title like
# 'M/s Sharma', 'A/c', 'S/o', 'W/o' - never an ambiguous initial; a
# letter adjacent to an apostrophe is part of a possessive/contraction
# ('Federer's', "D'Souza") - never an ambiguous initial either.
_SINGLE_LETTER_RE = re.compile(
    r"(?<![A-Za-z0-9/'’])[A-Za-z](?![A-Za-z0-9/'’])\.?", re.IGNORECASE)

# Unknown-abbreviation candidate: 2-3 lowercase letters, no vowels, not a
# safe short token. Slash-adjacent tokens ('M/s') and digits never match.
_VOWEL_LESS_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9/])[a-z]{2,3}(?![A-Za-z0-9/])")


@dataclass
class NormalizationResult:
    """Normalized text + full provenance + any safety concerns."""
    text: str
    provenance: List[Dict[str, Any]] = field(default_factory=list)
    concerns: List[str] = field(default_factory=list)


def _has_vowel(tok: str) -> bool:
    return any(ch in "aeiou" for ch in tok)


def _record(provenance: List[Dict[str, Any]], rule: str,
            original: str, normalized: str) -> str:
    """Append a provenance record and return the normalized text."""
    if original == normalized:
        return original
    provenance.append({
        "original": original,
        "normalized": normalized,
        "rule": rule,
        "confidence": "high",
        "semantic_change": False,
    })
    return normalized


def normalize_fyjc_text(text: str) -> NormalizationResult:
    """Deterministic normalization of one transaction description.

    Returns the normalized text, the full provenance of every applied
    rule, and any safety concerns (unknown abbreviations / single-letter
    initials) that must force REVIEW_REQUIRED downstream.
    """
    raw = str(text or "")
    if not raw:
        return NormalizationResult(text=raw)

    provenance: List[Dict[str, Any]] = []
    out = raw

    # 1) 'gds' -> 'goods'
    out = _GOODS_RE.sub(
        lambda m: _record(provenance, "BK_NORM_GOODS",
                          m.group(0), "goods"), out)

    # 1b) 'frm' -> 'from'
    out = _FRM_RE.sub(
        lambda m: _record(provenance, "BK_NORM_FROM",
                          m.group(0), "from"), out)

    # 1c) 'chq' -> 'cheque'
    out = _CHQ_RE.sub(
        lambda m: _record(provenance, "BK_NORM_CHEQUE",
                          m.group(0), "cheque"), out)

    # 2) '10k' / '1.5k' -> '10,000' / '1,500'
    def _repl_k(match: "re.Match[str]") -> str:
        try:
            value = Decimal(match.group(1)) * Decimal(1000)
        except (InvalidOperation, ValueError):
            return match.group(0)
        return f"{value:,.0f}"
    out = _K_SUFFIX_RE.sub(
        lambda m: _record(provenance, "BK_NORM_NUMERIC_K",
                          m.group(0), _repl_k(m)), out)

    # 3) 'td' / 't.d.' -> 'trade discount'; 'cd' / 'c.d.' -> 'cash
    # discount'. When the dotted form's final '.' also terminated the
    # sentence ('15% T.D. He issued ...'), the boundary is restored.
    def _dotted_sub(match: "re.Match[str]", rule: str,
                    replacement: str) -> str:
        rest = out[match.end():]
        trailing = match.group(0)[-1] == "."
        normalized = replacement
        if trailing and re.match(r"\s+[A-Z]", rest):
            normalized += "."
        return _record(provenance, rule, match.group(0), normalized)

    out = _TD_RE.sub(lambda m: _dotted_sub(m, "BK_NORM_TRADE_DISCOUNT",
                                           "trade discount"), out)
    out = _CD_RE.sub(lambda m: _dotted_sub(m, "BK_NORM_CASH_DISCOUNT",
                                           "cash discount"), out)
    out = _PA_RE.sub(lambda m: _dotted_sub(m, "BK_NORM_PER_ANNUM",
                                           "per annum"), out)
    out = _PA_RE.sub(lambda m: _dotted_sub(m, "BK_NORM_PER_ANNUM",
                                           "per annum"), out)

    # Sprint 34-FIX3: Convert numeric fractions to word forms BEFORE
    # date consumption so '1/3rd' is recognized as a fraction, not as
    # ordinal date '3rd'.  The existing _FRACTION_WORDS mechanism then
    # handles the word form.  '1/3rd' leaks '1' and '3' into
    # _extract_amounts as phantom monetary amounts without this.
    _NUMERIC_FRACTION_RE = re.compile(
        r"\b(\d+)\s*/\s*(\d+)(?:st|nd|rd|th)?\b", re.IGNORECASE)
    _FRAC_MAP = {
        (1, 2): "half", (1, 3): "one-third", (2, 3): "two-thirds",
        (1, 4): "one-fourth", (3, 4): "three-fourths",
        (1, 5): "one-fifth", (2, 5): "two-fifths",
        (3, 5): "three-fifths", (4, 5): "four-fifths",
        (1, 6): "one-sixth", (5, 6): "five-sixths",
    }
    def _replace_frac(m: "re.Match[str]") -> str:
        num, den = int(m.group(1)), int(m.group(2))
        word = _FRAC_MAP.get((num, den))
        if word is None:
            return m.group(0)
        return _record(provenance, "BK_NORM_NUMERIC_FRACTION",
                       m.group(0), word)
    out = _NUMERIC_FRACTION_RE.sub(_replace_frac, out)

    # Sprint 34-FIX1: Consume date tokens so their digits cannot leak
    # into monetary amount extraction.  '1st April 2026' contains '1'
    # and '2026' which _NUMBER_TOKEN matches as phantom monetary amounts,
    # causing REVIEW_REQUIRED when only one real amount exists.
    # Dates are never monetary amounts in FYJC accounting problems.
    _ORDINAL_DATE_RE = re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)
    out = _ORDINAL_DATE_RE.sub(
        lambda m: _record(provenance, "BK_NORM_DATE_ORDINAL",
                          m.group(0), "<DATE>"), out)
    _YEAR_RE = re.compile(
        r"\b(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{4})\b",
        re.IGNORECASE)
    out = _YEAR_RE.sub(
        lambda m: _record(provenance, "BK_NORM_DATE_YEAR",
                          m.group(0), m.group(1) + " <YEAR>"), out)

    # 4) Convert newlines to sentence boundaries before collapsing
    #    horizontal whitespace.  The existing splitter uses '.' as a
    #    sentence separator, so this restores the structural information
    #    that student line-breaks carry without changing any intra-line
    #    normalisation.
    out = out.replace("\n", ". ")
    # 4b) harmless whitespace collapse (horizontal only after newline handling)
    collapsed = re.sub(r"[ \t]+", " ", out).strip()
    if collapsed != out:
        provenance.append({
            "original": out,
            "normalized": collapsed,
            "rule": "BK_NORM_WHITESPACE",
            "confidence": "high",
            "semantic_change": False,
        })
        out = collapsed

    # -- safety concerns ----------------------------------------------------
    concerns: List[str] = []
    seen: set = set()

    # unknown abbreviation: vowel-less 2-3 letter token not in the safe set
    for match in _VOWEL_LESS_TOKEN_RE.finditer(out):
        tok = match.group(0)
        if _has_vowel(tok) or tok in _SAFE_SHORT_TOKENS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        concerns.append(
            f"'{tok}' looks like an abbreviation whose meaning Platrixa cannot "
            "establish deterministically in the FYJC accounting context. "
            "It never guesses - replace it with the full word.")

    # single-letter initial / placeholder ('X.', 'R.', 'M'): identity or
    # meaning cannot be established. 'a'/'i' and title pieces like
    # 'M/s Sharma' / 'A/c' are never flagged.
    seen.clear()
    for match in _SINGLE_LETTER_RE.finditer(out):
        tok = match.group(0)
        base = tok.rstrip(".")
        if base.lower() in _SAFE_SINGLE_LETTERS:
            # 'a'/'i' are safe as an article or first-person pronoun, but
            # in a PARTY position ('to A', 'from A') a bare CAPITAL can
            # be an ambiguous initial. A lowercase 'a' after 'to'/'from'
            # followed by a space and then a word is always an article
            # ('to a customer'), never a party name.
            head = out[:match.start()]
            tail = out[match.end():]
            if not re.search(r"\b(?:to|from)\s+$", head):
                continue
            # After 'to'/'from': lowercase 'a' (article) followed by a
            # space and then a word character is an article, not a party.
            # Uppercase 'A' is still flagged as a party initial.
            if base == "a" and re.match(r"\s+[A-Za-z]", tail):
                continue
        if base in seen:
            continue
        seen.add(base)
        concerns.append(
            f"'{tok}' looks like a single-letter abbreviation or initial "
            "whose meaning (for example a party identity) Platrixa cannot "
            "establish safely. It never guesses - type the full name or "
            "word.")

    return NormalizationResult(text=out, provenance=provenance,
                               concerns=concerns)


# ---------------------------------------------------------------------------
# Sprint 15I-VY - global mathematical contradiction validation
# ---------------------------------------------------------------------------

_C_AMT = (r"(?:rs\.?|₹|inr)?\s*(\d[\d,]*(?:\.\d+)?)"
          r"(?!\s*%|\s+percent|\s+per\s+cent)")

_ROUND_2 = Decimal("0.01")


def _dec(group: str) -> Optional[Decimal]:
    try:
        return Decimal(group.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


# Direct, position-precise rate patterns. The percent-token labels used
# elsewhere carry a +/- 24 char window, which can bleed a nearby 'gst' or
# 'discount' onto an unrelated rate; these regexes bind a rate ONLY to
# its immediately adjacent keyword, so the TD rate can never leak into
# the GST rate set (or vice versa).
_GST_RATE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:gst|igst|cgst|sgst)", re.IGNORECASE)
_GST_RATE_RE2 = re.compile(
    r"\bgst\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_TD_RATE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:trade\s+)?discount", re.IGNORECASE)
_TD_RATE_RE2 = re.compile(
    r"(?:trade\s+)?discount\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE)


def _rates_for(low: str, kind: str) -> List[Decimal]:
    """Every stated rate of one kind in the text: kind='gst' (any GST
    tax rate) or kind='td' (trade-discount rate). Dotted/shortened
    forms were already normalized ('td' -> 'trade discount') before
    this layer runs. A rate may appear in 'N% kind' or 'kind N%'
    word order; both are captured deterministically."""
    if kind == "gst":
        first, second = _GST_RATE_RE, _GST_RATE_RE2
    else:
        first, second = _TD_RATE_RE, _TD_RATE_RE2
    rates: List[Decimal] = []
    for m in first.finditer(low):
        value = _dec(m.group(1))
        if value is not None:
            rates.append(value)
    for m in second.finditer(low):
        value = _dec(m.group(1))
        if value is not None:
            rates.append(value)
    return rates


def _amount_near(low: str, words: str,
                 window: int = 20, mode: str = "both") -> Optional[Decimal]:
    """The money amount NEAREST to any occurrence of the keywords, or
    None. mode='both' checks immediately before and after (preferring
    the closest match, so 'outstanding and Rs.6,000 was paid' cannot
    steal the paid figure); mode='before' / mode='after' restrict to one
    side (for 'less Rs.X trade discount' vs 'trade discount on Rs.Y')."""
    best: Optional[Decimal] = None
    best_dist = 10 ** 9
    for m in re.finditer(r"\b(?:" + words + r")\b", low):
        if mode in ("both", "after"):
            after = low[m.end():m.end() + window]
            am = re.search(_C_AMT, after)
            if am:
                dist = am.start()
                value = _dec(am.group(1))
                if value is not None and dist < best_dist:
                    best_dist = dist
                    best = value
        if mode in ("both", "before"):
            head = low[max(0, m.start() - window):m.start()]
            bm = re.search(r"(?:^|[^0-9])" + _C_AMT
                           + r"(?=[^0-9]{0,12}$)", head)
            if bm:
                dist = len(head) - bm.end()
                value = _dec(bm.group(1))
                if value is not None and dist < best_dist:
                    best_dist = dist
                    best = value
    return best


def math_contradiction(text: str) -> Optional[Dict[str, Any]]:
    """Deterministic global mathematical contradiction validation.

    Returns a full INVALID_INPUT_MATH refusal (zero journal lines) when
    explicitly stated facts contradict each other mathematically, or a
    REVIEW_REQUIRED refusal for the recognized-but-unmerged digit
    payment/outstanding split, or None when no contradiction is
    established by the wording. Only facts actually stated are compared;
    missing information is never promoted to a contradiction.
    """
    from backend.maths.fyjc_bk_reasoning import (  # lazy - avoid cycle
        INVALID_INPUT_MATH,
        REVIEW_REQUIRED,
        _extract_amounts,
        _fmt_amt,
        _refusal,
    )
    low = " " + str(text or "").lower() + " "
    amounts, _ = _extract_amounts(text)

    # -- Rule A: payment + outstanding partition vs transaction value ---
    paid = _amount_near(low, "paid")
    # The outstanding figure must be stated with an unambiguous keyword.
    # 'outstanding/remains/remaining/unpaid/still due/amount due' claim a
    # nearby amount; a bare 'balance (due)' WITHOUT an attached figure is
    # never read as an outstanding amount - the nearest preceding payment
    # ('Rs.2,000 in cash, balance due') is a payment, not an outstanding
    # balance, and must not be misread into a false contradiction.
    outstanding = _amount_near(
        low, r"outstanding|remains|unpaid|still\s+due|amount\s+due")
    # Sprint 15I-BOUNDARY-CLOSURE: 'remaining' without an explicit figure
    # (e.g. 'Remaining to Raj', 'Remaining due') means the balance is owed,
    # not that a specific amount is outstanding.  Only match 'remaining' when
    # it has an explicit figure: 'remaining 15000' or 'remaining Rs.10,000'.
    if outstanding is None:
        outstanding = _amount_near(
            low, r"remaining\s+(?:rs\.?\s*)?\d", mode="after")
    if outstanding is None:
        outstanding = _amount_near(low, r"balance(?:\s+of)?", window=10, mode="after")
    # Sprint 15I-BOUNDARY-CLOSURE: 'Remaining X by NEFT/cash/cheque/bank'
    # is a PAYMENT step, not an outstanding balance.  Exclude it so a
    # multi-payment transaction is never falsely flagged as a contradiction.
    if outstanding is not None:
        _pay_meth = re.search(
            r"remaining\s+\d[\d,]*(?:\.\d+)?\s+(?:by|in|via|through)\s+"
            r"(?:neft|cash|cheque|chq|bank|draft|upi|rtgs)",
            low, re.I)
        if _pay_meth:
            outstanding = None
    if paid is not None and outstanding is not None and paid != outstanding:
        # Sprint 15I-CAPABILITY-CLOSURE: remove only ONE occurrence of
        # paid and ONE of outstanding from the amounts list, not ALL
        # matching values.  In a multi-payment transaction like
        # 'Paid ₹40,000 cash. Paid ₹30,000 cheque. Balance ₹30,000 due.'
        # there are TWO ₹30,000 amounts — the cheque payment and the
        # balance.  Removing all ₹30,000 values would leave only the
        # transaction value (₹100,000) as a candidate, producing a false
        # INVALID_INPUT_MATH when the payments actually reconcile:
        # ₹40,000 + ₹30,000 (cheque) + ₹30,000 (balance) = ₹100,000.
        remaining = list(amounts)
        _removed_paid = False
        _removed_outstanding = False
        for _i in range(len(remaining)):
            if not _removed_paid and remaining[_i] == paid:
                remaining[_i] = None
                _removed_paid = True
            elif not _removed_outstanding and remaining[_i] == outstanding:
                remaining[_i] = None
                _removed_outstanding = True
        candidates = [a for a in remaining if a is not None]
        if len(candidates) == 1:
            total = candidates[0]
            components = paid + outstanding
            if components != total:
                return _refusal(
                    INVALID_INPUT_MATH,
                    (f"INVALID_INPUT_MATH: the stated payment "
                     f"(Rs.{_fmt_amt(paid)}) plus the stated outstanding "
                     f"amount (Rs.{_fmt_amt(outstanding)}) equals "
                     f"Rs.{_fmt_amt(components)}, which contradicts the "
                     f"stated transaction value of "
                     f"Rs.{_fmt_amt(total)}. Platrixa never journals a "
                     "mathematically contradictory transaction."),
                    "Correct the stated amounts so the payment and "
                    "outstanding components reconcile with the transaction "
                    "value.")
            return _refusal(
                REVIEW_REQUIRED,
                (f"The stated payment (Rs.{_fmt_amt(paid)}) and "
                 f"outstanding balance (Rs.{_fmt_amt(outstanding)}) "
                 f"exactly cover the stated transaction value "
                 f"(Rs.{_fmt_amt(total)}), but Platrixa does not merge a "
                 "stated digit payment/outstanding split into one journal "
                 "yet. Enter the two transactions separately."),
                "Enter the transaction in two steps, e.g. 'Sold goods to "
                "Ram on credit for Rs.10,000.' then 'Received Rs.6,000 "
                "from Ram in part settlement.'")

    # -- Rule B: trade-discount rate vs explicitly stated discount ------
    # Only an EXPLICIT discount AMOUNT ('trade discount of Rs.X',
    # 'less Rs.X trade discount') is compared against the rate - an
    # amount after 'discount on Rs.Y' is the BASE (the pre-discount
    # value), never a stated discount figure.
    trade_amount = _amount_near(
        low, r"trade\s+discount\s+(?:of|amounting\s+to|is|:)",
        window=18, mode="after")
    if trade_amount is None:
        # 'less Rs.X trade discount' / 'Rs.X as trade discount': the
        # stated figure sits BEFORE the phrase.
        trade_amount = _amount_near(low, r"trade\s+discount",
                                    window=18, mode="before")
    if trade_amount is not None:
        worth = re.search(r"\bworth\s+" + _C_AMT, low)
        base = None
        if worth:
            base = _dec(worth.group(1))
        if base is None and len(amounts) == 2:
            base = next((a for a in amounts if a != trade_amount), None)
        if base is not None:
            for rate in _rates_for(low, "td"):
                expected = (base * rate / Decimal(100)).quantize(_ROUND_2)
                if expected != trade_amount:
                    return _refusal(
                        INVALID_INPUT_MATH,
                        (f"INVALID_INPUT_MATH: the stated trade "
                         f"discount (Rs.{_fmt_amt(trade_amount)}) "
                         f"contradicts the stated rate ({rate}% of "
                         f"Rs.{_fmt_amt(base)} = "
                         f"Rs.{_fmt_amt(expected)}). Platrixa never "
                         "journals a contradictory discount."),
                        "Correct the discount amount or the rate so "
                        "they reconcile.")
                break

    # -- Rule C: explicit GST component amounts vs stated rate ----------
    # The GST taxable base is net of any stated trade discount (the
    # FYJC convention the hardened authority already applies), so a
    # valid '18% GST on the net of 10% TD' split can never be mistaken
    # for a contradiction.
    cgst = _amount_near(low, r"cgst", window=16)
    sgst = _amount_near(low, r"sgst", window=16)
    igst = _amount_near(low, r"igst", window=16)
    gst_components: Optional[Decimal] = None
    if cgst is not None and sgst is not None:
        gst_components = cgst + sgst
    elif igst is not None:
        gst_components = igst
    if gst_components is not None:
        component_amts = {cgst, sgst, igst} - {None}
        candidates = [a for a in amounts if a not in component_amts]
        gst_rates = _rates_for(low, "gst")
        if len(candidates) == 1 and gst_rates:
            base = candidates[0]
            # trade discount is applied to the base before GST
            td_rates = _rates_for(low, "td")
            if trade_amount is not None:
                base = base - trade_amount
            elif td_rates:
                for rate in td_rates:
                    base = (base * (Decimal(100) - rate)
                            / Decimal(100)).quantize(_ROUND_2)
            combined = sum(gst_rates)
            expected = (base * combined / Decimal(100)).quantize(_ROUND_2)
            if gst_components != expected:
                labels = ("CGST + SGST" if cgst is not None
                          else "IGST")
                return _refusal(
                    INVALID_INPUT_MATH,
                    (f"INVALID_INPUT_MATH: the stated GST components "
                     f"({labels} Rs.{_fmt_amt(gst_components)}) "
                     f"contradict the stated GST rate "
                     f"({combined}% of the Rs.{_fmt_amt(base)} taxable "
                     f"base = Rs.{_fmt_amt(expected)}). Platrixa never "
                     "journals inconsistent tax components."),
                    "Correct the GST amounts or the rate so they "
                    "reconcile.")

    # -- Rule D: full settlement can never exceed the settled account ---
    if re.search(r"\b(?:full\s+)?settlement\b", low):
        account = _amount_near(
            low, r"(?:his|her|their|the)\s+account\s+of", window=16)
        received = _amount_near(low, r"received", window=16)
        if account is not None and received is not None \
                and received > account:
            return _refusal(
                INVALID_INPUT_MATH,
                (f"INVALID_INPUT_MATH: the amount received in full "
                 f"settlement (Rs.{_fmt_amt(received)}) exceeds the stated "
                 f"account balance it settles (Rs.{_fmt_amt(account)}). A "
                 "full settlement can never exceed the account - Platrixa "
                 "never invents a negative discount."),
                "Correct the received amount or the account balance.")

        # -- Rule E: a stated cash-discount AMOUNT must reconcile the
        # full settlement (received + stated discount == account). Only
        # 'full settlement' establishes the complete-split claim; a
        # partial payment against an account never triggers this rule.
        if re.search(r"\bfull\s+settlement\b", low) \
                and account is not None and received is not None:
            cash_disc = _amount_near(
                low, r"cash\s+discount\s+(?:of|amounting\s+to|is|:)",
                window=18, mode="after")
            if cash_disc is None:
                cash_disc = _amount_near(low, r"cash\s+discount",
                                         window=18, mode="before")
            if cash_disc is not None \
                    and received + cash_disc != account:
                return _refusal(
                    INVALID_INPUT_MATH,
                    (f"INVALID_INPUT_MATH: the amount received in full "
                     f"settlement (Rs.{_fmt_amt(received)}) plus the "
                     f"stated cash discount (Rs.{_fmt_amt(cash_disc)}) "
                     f"equals Rs.{_fmt_amt(received + cash_disc)}, which "
                     f"does not settle the stated account of "
                     f"Rs.{_fmt_amt(account)}. Platrixa never journals an "
                     "inconsistent discount."),
                    "Correct the received amount, the discount, or the "
                    "account balance so they reconcile.")
    return None


def vy_harden(question: str, amount: Any = None) -> Dict[str, Any]:
    """Sprint 15I-VY production wrapper over the hardened authority.

    Pipeline: raw input -> linguistic normalization -> safety concerns
    -> global math contradiction validation -> reason_bk_question on the
    normalized text -> debit/credit balancing invariant. Attaches the
    normalization provenance to every result.

    Any result from the authority is passed through UNCHANGED when no
    normalization / contradiction / imbalance applies, so historical
    behavior is byte-identical for clean inputs.
    """
    from backend.maths.fyjc_bk_reasoning import (  # lazy - avoid cycle
        INVALID_INPUT_MATH,
        REVIEW_REQUIRED,
        _refusal,
        reason_bk_question,
    )
    raw = str(question or "")
    norm = normalize_fyjc_text(raw)

    # unknown abbreviations / single-letter initials: REVIEW_REQUIRED,
    # never a guess.
    if norm.concerns:
        return _refusal(
            REVIEW_REQUIRED,
            norm.concerns[0],
            "Replace the abbreviation or initial with its full meaning "
            "and re-type the transaction.")

    # global mathematical contradiction: INVALID_INPUT_MATH, zero lines.
    contradiction = math_contradiction(norm.text)
    if contradiction is not None:
        contradiction["normalization"] = norm.provenance
        if contradiction.get("status") == INVALID_INPUT_MATH:
            contradiction["status_label"] = "🔴 INVALID INPUT (MATH)"
        return contradiction

    result = reason_bk_question(norm.text, amount)

    # Step-7 balancing invariant: a VERIFIED journal must balance. This is
    # a backstop - the contradiction validator above is the primary
    # detector of semantic arithmetic contradictions.
    if result.get("status") == "VERIFIED":
        journal = result.get("journal") or {}
        if journal.get("balanced") is False:
            return _refusal(
                REVIEW_REQUIRED,
                ("The resolved journal does not balance (debit total "
                 "differs from credit total). Platrixa never reports an "
                 "unbalanced entry as verified."),
                "Re-check the stated amounts and re-type the transaction.")

    result["normalization"] = norm.provenance
    return result
