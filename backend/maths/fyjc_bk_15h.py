"""
Platrixa
Sprint 15H - Real-World FYJC BK Validation & Adversarial Hardening
backend/maths/fyjc_bk_15h.py

Everything Sprint 15H adds ON TOP of the verified 15E/15F/15G baseline.
The 15H benchmark corpus (backend/maths/fyjc_bk_15h_benchmark.py) is an
INDEPENDENT hand-written golden set - it never consults the pattern
registry or the engine. This module only MEASURES the engine against that
oracle and provides the hardening machinery:

  1. FAILURE TAXONOMY (spec 8)  - the 15 primary categories, one per case.
  2. EXTRACTION BOUNDARY (spec 6) - a deterministic Good / Uncertain /
     Unusable gate over OCR/extraction signals. A flagged unreadable digit
     NEVER produces a parsed amount - Platrixa never invents a digit.
  3. STUDENT-ERROR CATEGORIES (spec 5) - wraps the 15F verifiers
     (verify_student_journal / verify_student_final / ledger / TB) and
     attaches the SPECIFIC error category + affected component - never a
     blanket 'Incorrect answer'.
  4. FAILURE CLASSIFIER (spec 8) - assigns one primary taxonomy category
     per corpus case (correct VERIFIED, correct refusal, incorrect
     confident answer, incorrect refusal, ...).
  5. COVERAGE REPORT (spec 9) - machine-readable separate counters
     (never a single accuracy percentage).
  6. REPLAY FAILURE CAPTURE (spec 7) - any case that reaches deterministic
     reasoning becomes a replay fixture; fixtures re-execute byte-
     identically, so a fixed failure stays a permanent regression.
  7. HARD-GATE SCAN (spec 12) - the absolute release gates, powered by the
     15G deterministic validators (validate_journal / validate_ledger /
     validate_trial_balance / validate_pipeline).

Deterministic. No AI. No network. No invented accounts/amounts.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.fyjc_15g import (
    OK_STATE,
    REPLAY_SCHEMA_VERSION,
    build_lineage,
    build_replay_record,
    deserialize_replay,
    replay_execute,
    serialize_replay,
    validate_pipeline,
)
from backend.maths.fyjc_accounting import (
    verify_ledger_balance,
    verify_trial_balance,
)
from backend.maths.fyjc_bk_15f import (
    verify_student_final,
    verify_student_journal,
)
from backend.maths.fyjc_bk_reasoning import (
    journal_to_entries,
    reason_bk_question,
)
from backend.maths.fyjc_bk_reasoning import NOT_SUPPORTED
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED

# ---------------------------------------------------------------------------
# 1. Failure taxonomy (spec section 8) - one primary category per case
# ---------------------------------------------------------------------------
FAILURE_TAXONOMY: Tuple[str, ...] = (
    "EXTRACTION_FAILURE",          # the extraction/OCR boundary mis-gated
    "INTENT_FAILURE",              # requested operation (journal/ledger/TB) misread
    "NORMALIZATION_FAILURE",       # equivalent wording did NOT converge / refused valid wording
    "ACCOUNT_IDENTIFICATION_FAILURE",  # wrong/missing/invented account vs oracle
    "TRANSACTION_TYPE_FAILURE",    # cash vs credit / purchase vs sale misclassified
    "PAYMENT_SPLIT_FAILURE",       # paid/credit split wrong (partial payment)
    "DISCOUNT_FAILURE",            # trade/cash discount amount or eligibility wrong
    "JOURNAL_FAILURE",             # journal lines/amounts wrong
    "LEDGER_FAILURE",              # ledger posting effect wrong
    "TRIAL_BALANCE_FAILURE",       # trial-balance effect wrong
    "C++_AUTHORITY_FAILURE",       # registered metric not routed / C++ result mismatch
    "DISCREPANCY_DETECTION_FAILURE",  # a discrepancy was missed or silently repaired
    "STUDENT_VERIFICATION_FAILURE",   # student answer check failed to classify
    "UNSAFE_CONFIDENCE",           # confident answer where the oracle refuses
    "EXPECTED_REFUSAL",            # correct refusal behaviour (not a failure)
)

# ---------------------------------------------------------------------------
# 2. Extraction / OCR boundary (spec section 6)
# ---------------------------------------------------------------------------
EXTRACT_GOOD = "GOOD"
EXTRACT_UNCERTAIN = "UNCERTAIN"
EXTRACT_UNUSABLE = "UNUSABLE"


def classify_extraction_quality(transcription: str,
                                signals: Optional[Dict[str, Any]] = None,
                                ) -> Dict[str, Any]:
    """Deterministic extraction-quality gate.

    signals (from an upstream photo->text layer; bools):
      unreadable_digit / unreadable_amount : a digit or amount could not
                                             be read reliably
      severe_blur / mild_blur / rotation / poor_lighting / partially_cropped
      missing_transaction_text : no transaction verb/text present
      contradictory_output     : OCR produced mutually contradictory text
      low_confidence_word      : some words have low confidence

    Returns {state: GOOD|UNCERTAIN|UNUSABLE, reasons, process}.

    GOOD        -> process normally
    UNCERTAIN   -> REVIEW_REQUIRED (ask for a cleaner photo/typed text)
    UNUSABLE    -> BLOCKED (request clearer input)

    A flagged unreadable digit NEVER yields a parsed amount: it forces at
    least UNCERTAIN, and UNUSABLE when combined with a structurally
    unusable signal. Platrixa never invents a digit.
    """
    signals = dict(signals or {})
    reasons: List[str] = []
    if not str(transcription or "").strip():
        return {"state": EXTRACT_UNUSABLE,
                "reasons": ["No text was extracted from the image."],
                "process": False}

    unusable: List[str] = []
    uncertain: List[str] = []

    if signals.get("unreadable_digit"):
        uncertain.append("A digit could not be read reliably - Platrixa will "
                         "not guess it.")
    if signals.get("severe_blur"):
        unusable.append("The image is severely blurred.")
    if signals.get("contradictory_output"):
        unusable.append("OCR produced contradictory text.")
    if signals.get("missing_transaction_text"):
        unusable.append("The transaction text is missing or incomplete.")
    if signals.get("mild_blur"):
        uncertain.append("The image is slightly blurred.")
    if signals.get("rotation"):
        uncertain.append("The image appears rotated.")
    if signals.get("poor_lighting"):
        uncertain.append("The lighting is imperfect.")
    if signals.get("partially_cropped"):
        uncertain.append("The question appears partially cropped.")
    if signals.get("low_confidence_word"):
        uncertain.append("Some words have low OCR confidence.")
    if signals.get("unreadable_amount"):
        uncertain.append("The amount could not be read cleanly.")

    if unusable and (uncertain or signals.get("unreadable_digit")):
        state = EXTRACT_UNUSABLE
    elif unusable:
        state = EXTRACT_UNUSABLE
    elif uncertain:
        state = EXTRACT_UNCERTAIN
    else:
        state = EXTRACT_GOOD
    return {"state": state, "reasons": unusable + uncertain,
            "process": state == EXTRACT_GOOD}


def _extraction_refusal(state: str, reasons: List[str]) -> Dict[str, Any]:
    """A refusal in the same shape reason_bk_question returns, with zero
    journal lines - the extraction layer never fabricates a transaction."""
    if state == EXTRACT_UNCERTAIN:
        return {
            "status": REVIEW_REQUIRED,
            "status_label": "REVIEW_REQUIRED",
            "resolved": False,
            "why_not": "The extracted text is uncertain: " + "; ".join(
                reasons) + ".",
            "next_action": ("Retake the photo (or type the transaction) so "
                            "every digit and word is clearly readable."),
            "journal": None, "ledger": None, "trial_balance": None,
            "debit_lines": [], "credit_lines": [], "calculation_records": [],
            "extraction_state": state,
        }
    return {
        "status": BLOCKED,
        "status_label": "BLOCKED",
        "resolved": False,
        "why_not": "The input is unusable: " + "; ".join(reasons) + ".",
        "next_action": ("Provide a clearer image or type the transaction "
                        "text directly. Platrixa does not guess unreadable "
                        "digits or missing transaction text."),
        "journal": None, "ledger": None, "trial_balance": None,
        "debit_lines": [], "credit_lines": [], "calculation_records": [],
        "extraction_state": state,
    }


def process_extraction(question: str,
                       signals: Optional[Dict[str, Any]] = None,
                       ) -> Dict[str, Any]:
    """Run a photo/typed transcription through the extraction boundary.

    GOOD         -> the full reasoning pipeline (reason_bk_question)
    UNCERTAIN    -> REVIEW_REQUIRED refusal (never a confident answer)
    UNUSABLE     -> BLOCKED refusal with a request for clearer input
    """
    quality = classify_extraction_quality(question, signals)
    if quality["state"] == EXTRACT_GOOD:
        return reason_bk_question(str(question or "").strip())
    return _extraction_refusal(quality["state"], quality["reasons"])


# ---------------------------------------------------------------------------
# 3. Student-error categories (spec section 5)
# ---------------------------------------------------------------------------
STUDENT_ERROR_CATEGORIES: Tuple[str, ...] = (
    "CORRECT", "WRONG_SIDE", "WRONG_ACCOUNT", "MISSING_ACCOUNT",
    "INVENTED_ACCOUNT", "WRONG_AMOUNT", "JOURNAL_UNBALANCED",
    "WRONG_CLASSIFICATION", "LEDGER_ERROR", "TRIAL_BALANCE_ERROR",
    "STUDENT_VERIFICATION_FAILURE",
)

_STUDENT_KINDS = ("journal", "final", "ledger", "tb")


def _reference_entries(out: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The full-pipeline journal entries used as the ledger/TB reference."""
    journals = out.get("journals") or [out.get("journal")] or []
    entries: List[Dict[str, Any]] = []
    for j in journals:
        if j and j.get("status") == VERIFIED:
            entries.extend(journal_to_entries(j))
    return entries


def _journal_error_detail(question: str,
                          submission: Dict[str, Any]) -> Dict[str, Any]:
    """The SPECIFIC first error for a journal submission: category +
    affected component + a human first-mistake message.

    All three are derived from the SAME root-cause ordering - side swap ->
    account presence (missing/invented/wrong) -> totals -> amounts ->
    classification - so they can never contradict each other, even for a
    submission with several simultaneous errors. An omitted account is
    reported as MISSING_ACCOUNT even though it also unbalances the journal
    (never a blanket 'wrong')."""
    from backend.maths.fyjc_bk_reasoning import generate_journal, \
        traditional_class_for
    reference = generate_journal(str(question or "").strip())
    if reference.get("status") != VERIFIED:
        return {"category": "STUDENT_VERIFICATION_FAILURE",
                "component": "student-submission",
                "first_mistake": "The reference journal could not be "
                                  "derived from the question."}
    debits: List[Tuple[str, Decimal]] = []
    credits: List[Tuple[str, Decimal]] = []
    classes: Dict[str, str] = {}
    try:
        for raw in (submission or {}).get("debits") or []:
            if isinstance(raw, (tuple, list)):
                debits.append((str(raw[0]), Decimal(str(raw[1]))))
            elif isinstance(raw, dict):
                debits.append((str(raw.get("account") or ""),
                               Decimal(str(raw.get("amount") or 0))))
                if raw.get("class"):
                    classes[str(raw.get("account") or "")] = str(
                        raw["class"])
        for raw in (submission or {}).get("credits") or []:
            if isinstance(raw, (tuple, list)):
                credits.append((str(raw[0]), Decimal(str(raw[1]))))
            elif isinstance(raw, dict):
                credits.append((str(raw.get("account") or ""),
                               Decimal(str(raw.get("amount") or 0))))
                if raw.get("class"):
                    classes[str(raw.get("account") or "")] = str(
                        raw["class"])
    except Exception:  # noqa: BLE001 - unreadable submission
        return {"category": "STUDENT_VERIFICATION_FAILURE",
                "component": "student-submission",
                "first_mistake": "The journal entry could not be read."}
    if not debits or not credits:
        return {"category": "STUDENT_VERIFICATION_FAILURE",
                "component": "student-submission",
                "first_mistake": "The journal has no debit or credit "
                                  "lines."}
    ref_debits = {l["account"] for l in reference["debit_lines"]}
    ref_credits = {l["account"] for l in reference["credit_lines"]}
    std_debits = {a for a, _ in debits}
    std_credits = {a for a, _ in credits}
    # a pure side swap (student debits exactly what the reference credits
    # and vice versa) is WRONG_SIDE; anything else is an account error.
    if std_debits == ref_credits and std_credits == ref_debits:
        return {"category": "WRONG_SIDE", "component": "journal:sides",
                "first_mistake": ("The debit and credit sides are swapped "
                                   "- debit what the reference credits and "
                                   "credit what it debits.")}
    # account presence is the ROOT cause: an omitted/invented/wrong account
    # is reported even when it also unbalances the journal.
    for side, got, ref in (("debit", std_debits, ref_debits),
                           ("credit", std_credits, ref_credits)):
        if got != ref:
            missing = sorted(ref - got)
            extra = sorted(got - ref)
            if missing and not extra:
                return {"category": "MISSING_ACCOUNT",
                        "component": f"journal:{side}",
                        "first_mistake": (f"The {side} side is missing an "
                                          f"account: {missing}.")}
            if extra and not missing:
                return {"category": "INVENTED_ACCOUNT",
                        "component": f"journal:{side}",
                        "first_mistake": (f"The {side} side has an account "
                                          f"that is not part of the "
                                          f"transaction: {extra}.")}
            return {"category": "WRONG_ACCOUNT",
                    "component": f"journal:{side}",
                    "first_mistake": (f"The {side} side has the wrong "
                                      f"accounts - expected {sorted(ref)}, "
                                      f"entered {sorted(got)}.")}
    total_debit = sum((a for _, a in debits), Decimal(0))
    total_credit = sum((a for _, a in credits), Decimal(0))
    if abs(total_debit - total_credit) > Decimal("0.01"):
        return {"category": "JOURNAL_UNBALANCED",
                "component": "journal:totals",
                "first_mistake": (f"The journal is not balanced - total "
                                  f"Debit {total_debit} must equal total "
                                  f"Credit {total_credit}.")}
    ref_pairs = {(l["account"], l["amount"])
                 for l in reference["debit_lines"]}
    ref_pairs |= {(l["account"], l["amount"])
                  for l in reference["credit_lines"]}
    std_pairs = set(debits) | set(credits)
    if ref_pairs != std_pairs:
        wrong = sorted((a, float(amt)) for a, amt in (std_pairs - ref_pairs))
        return {"category": "WRONG_AMOUNT",
                "component": "journal:amounts",
                "first_mistake": (f"The amounts are wrong - the reference "
                                  f"posts {sorted((a, float(v)) for a, v in ref_pairs)}, "
                                  f"your lines differ on {wrong}.")}
    ref_class = {l["account"]: l["class"] for l in
                 reference["debit_lines"] + reference["credit_lines"]}
    for account, _ in debits + credits:
        student_class = classes.get(account) or traditional_class_for(account)
        if student_class != ref_class.get(account):
            return {"category": "WRONG_CLASSIFICATION",
                    "component": "journal:classification",
                    "first_mistake": (f"The classification of '{account}' is "
                                      f"wrong - it is a "
                                      f"{ref_class.get(account)} Account "
                                      f"(Real/Personal/Nominal), not "
                                      f"{student_class}.")}
    return {"category": "CORRECT", "component": None,
            "first_mistake": None}


def _journal_error_category(question: str,
                            submission: Dict[str, Any]) -> str:
    """Back-compat: just the category string for a journal submission."""
    return _journal_error_detail(question, submission)["category"]


def _student_error_category(res: Dict[str, Any], kind: str,
                            question: str,
                            submission: Any) -> str:
    verdict = str(res.get("verdict") or "")
    if verdict == "CORRECT":
        return "CORRECT"
    if verdict == "REFUSED":
        return "STUDENT_VERIFICATION_FAILURE"
    if kind == "ledger":
        return "LEDGER_ERROR"
    if kind == "tb":
        return "TRIAL_BALANCE_ERROR"
    if kind == "journal":
        return _journal_error_category(question, submission or {})
    # final-answer checks: side vs amount
    why = str(res.get("why_not") or "") + " " + \
        str(res.get("first_mistake") or "")
    if "side is wrong" in why:
        return "WRONG_SIDE"
    if "answer is wrong" in why:
        return "WRONG_AMOUNT"
    return "STUDENT_VERIFICATION_FAILURE"


def _affected_component(res: Dict[str, Any], kind: str,
                        category: str) -> Optional[str]:
    if category == "CORRECT":
        return None
    if kind == "ledger":
        return "ledger:balance"
    if kind == "tb":
        return "trial_balance"
    if kind == "final":
        return str(res.get("given") or "final-answer")[:40]
    why = str(res.get("why_not") or "") + " " + \
        str(res.get("first_mistake") or "")
    if "debit side" in why:
        return "journal:debit"
    if "credit side" in why:
        return "journal:credit"
    if "classification" in why:
        return "journal:classification"
    if "not balanced" in why:
        return "journal:totals"
    if "amount" in why:
        return "journal:amounts"
    return "journal"


def verify_student_with_category(question: str,
                                 submission: Any,
                                 kind: str = "journal",
                                 what: Optional[str] = None,
                                 ) -> Dict[str, Any]:
    """Verify a student answer and attach the SPECIFIC error category and
    the affected component (spec section 5) - never just 'Incorrect'.

    kind:
      journal - submission {debits: [(acct, amt)...], credits: [...]}
      final   - a final number/answer, `what` selects the reference
                (journal_total | trial_balance_total | debit:<A> |
                 credit:<A> | balance:<A>)
      ledger  - submission {account, balance, side} (first ledger mistake)
      tb      - submission {rows: [{account, debit, credit}...]}

    Returns the wrapped 15F verdict plus `error_category` and
    `affected_component`.
    """
    if kind not in _STUDENT_KINDS:
        return {
            "verdict": "REFUSED",
            "status": BLOCKED,
            "authority_state": "bookkeeping",
            "first_mistake": None,
            "expected": None, "given": None,
            "why_not": f"Unknown student-check kind '{kind}'.",
            "next_action": None,
            "error_category": "STUDENT_VERIFICATION_FAILURE",
            "affected_component": "student-submission",
        }
    reference = reason_bk_question(str(question or "").strip())
    if kind == "journal":
        res = verify_student_journal(str(question or "").strip(), submission)
    elif kind == "final":
        res = verify_student_final(str(question or "").strip(), submission,
                                   what or "journal_total")
    elif kind == "ledger":
        entries = _reference_entries(reference)
        res = verify_ledger_balance(
            str((submission or {}).get("account") or ""),
            (submission or {}).get("balance"),
            str((submission or {}).get("side") or ""), entries)
    else:
        entries = _reference_entries(reference)
        res = verify_trial_balance((submission or {}).get("rows") or [],
                                   entries)
    category = _student_error_category(res, kind, question, submission)
    out = dict(res)
    out["error_category"] = category
    out["affected_component"] = _affected_component(res, kind, category)
    # journal kind: error_category and first_mistake must describe the SAME
    # first deterministic error. The 15F verifier checks totals before
    # accounts while the 15H category is root-cause ordered (accounts
    # before totals); for an INCORRECT journal, overwrite the message and
    # component with the category-consistent ones so the three never
    # contradict each other (Sprint 15H combined-error fix).
    if kind == "journal" and category in (
            "WRONG_SIDE", "WRONG_ACCOUNT", "MISSING_ACCOUNT",
            "INVENTED_ACCOUNT", "JOURNAL_UNBALANCED", "WRONG_AMOUNT",
            "WRONG_CLASSIFICATION"):
        detail = _journal_error_detail(question, submission or {})
        out["first_mistake"] = detail.get("first_mistake")
        out["affected_component"] = detail.get("component")
    return out


# ---------------------------------------------------------------------------
# 4. Failure classifier (spec section 8) - one primary category per case
# ---------------------------------------------------------------------------
def _merged_lines(out: Dict[str, Any]) -> Tuple[List[Any], List[Any]]:
    journals = out.get("journals") or [out.get("journal")] or []
    journals = [j for j in journals if j is not None]
    dr = [l for j in journals for l in (j.get("debit_lines") or [])]
    cr = [l for j in journals for l in (j.get("credit_lines") or [])]
    return dr, cr


def _norm(lines: List[Any]) -> List[Tuple[str, int]]:
    return sorted(
        (str(l.get("account") or ""),
         int(round(float(l.get("amount", 0)))))
        for l in lines if l.get("account"))


def _line_mismatch(out: Dict[str, Any],
                   expected: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What differs between the engine's lines and the oracle's."""
    dr, cr = _merged_lines(out)
    exp_dr = sorted((a, int(v)) for a, v in expected.get("debit") or [])
    exp_cr = sorted((a, int(v)) for a, v in expected.get("credit") or [])
    got_dr, got_cr = _norm(dr), _norm(cr)
    if got_dr != exp_dr or got_cr != exp_cr:
        if {a for a, _ in got_dr} != {a for a, _ in exp_dr} \
                or {a for a, _ in got_cr} != {a for a, _ in exp_cr}:
            return {"category": "ACCOUNT_IDENTIFICATION_FAILURE",
                    "detail": f"Dr {got_dr} != {exp_dr} | "
                              f"Cr {got_cr} != {exp_cr}"}
        calc = [str(r.get("calculation_id") or "")
                for j in ((out.get("journals") or [out.get("journal")]) or [])
                for r in (j.get("calculation_records") or [])]
        joined = " ".join(calc)
        if "discount" in joined:
            return {"category": "DISCOUNT_FAILURE",
                    "detail": f"amounts {got_dr}/{got_cr} != "
                              f"{exp_dr}/{exp_cr}"}
        if "paid" in joined or "credit" in joined:
            return {"category": "PAYMENT_SPLIT_FAILURE",
                    "detail": f"split {got_dr}/{got_cr} != "
                              f"{exp_dr}/{exp_cr}"}
        return {"category": "JOURNAL_FAILURE",
                "detail": f"Dr {got_dr} != {exp_dr} | Cr {got_cr} != {exp_cr}"}
    if (out.get("ledger") or {}).get("balanced") is not True:
        return {"category": "LEDGER_FAILURE",
                "detail": "ledger unbalanced"}
    if (out.get("trial_balance") or {}).get("balanced") is not True:
        return {"category": "TRIAL_BALANCE_FAILURE",
                "detail": "trial balance unbalanced"}
    return None


def classify_failure(question: str, out: Dict[str, Any],
                     expected: Dict[str, Any]) -> Dict[str, Any]:
    """One primary failure-taxonomy category + outcome label for a case.

    outcome: correct_verified | correct_derived | correct_refusal |
             incorrect_confident | incorrect_refusal
    """
    exp_status = expected.get("status")
    got_status = out.get("status")
    if exp_status == got_status:
        if exp_status != VERIFIED:
            return {"category": "EXPECTED_REFUSAL",
                    "outcome": "correct_refusal",
                    "detail": out.get("why_not")}
        mismatch = _line_mismatch(out, expected)
        if mismatch is None:
            return {"category": None, "outcome": "correct_verified",
                    "detail": None}
        return {"category": mismatch["category"],
                "outcome": "incorrect_confident",
                "detail": mismatch["detail"]}
    if got_status == VERIFIED:
        return {"category": "UNSAFE_CONFIDENCE",
                "outcome": "incorrect_confident",
                "detail": (f"engine VERIFIED where the oracle expects "
                           f"{exp_status}")}
    # the engine refused a question the oracle says is valid
    if exp_status == VERIFIED:
        if got_status == REVIEW_REQUIRED:
            cat = "NORMALIZATION_FAILURE"
        elif got_status == BLOCKED:
            cat = "EXTRACTION_FAILURE" if "missing" in str(
                out.get("why_not") or "").lower() else "NORMALIZATION_FAILURE"
        else:
            cat = "TRANSACTION_TYPE_FAILURE"
        return {"category": cat, "outcome": "incorrect_refusal",
                "detail": out.get("why_not")}
    # both refused, but the refusal STATE differs
    return {"category": "TRANSACTION_TYPE_FAILURE",
            "outcome": "incorrect_refusal",
            "detail": f"expected {exp_status}, got {got_status}"}


# ---------------------------------------------------------------------------
# 5. Coverage report (spec section 9) - separate counters, never one %
# ---------------------------------------------------------------------------
COVERAGE_KEYS: Tuple[str, ...] = (
    "cases",
    "correct_verified",          # correct confident answer
    "correct_derived",           # 0 in the BK pipeline (resolved = VERIFIED)
    "correct_review_required",   # correctly refused for clarification
    "correct_blocked",           # correctly blocked (missing/unusable input)
    "correct_not_supported",     # correctly refused as outside Ch.1-3
    "incorrect_confident",       # confident answer that contradicts the oracle
    "incorrect_refusal",         # valid question wrongly refused
    "extraction_failure",        # extraction boundary mis-gated
    "parser_failure",            # engine raised / malformed output
)

_REFUSAL_KEY = {
    REVIEW_REQUIRED: "correct_review_required",
    BLOCKED: "correct_blocked",
    NOT_SUPPORTED: "correct_not_supported",
}


def coverage_report(cases: List[Dict[str, Any]],
                    runner) -> Dict[str, Any]:
    """Machine-readable coverage over a corpus. `runner(case) -> out`.

    Every case is counted EXACTLY once across the outcome counters; the
    failure taxonomy breaks out the categories (spec section 8).
    """
    stats = {key: 0 for key in COVERAGE_KEYS}
    stats["cases"] = len(cases)
    by_category: Dict[str, int] = {}
    parser_failures: List[Dict[str, Any]] = []
    for case in cases:
        try:
            out = runner(case)
        except Exception as exc:  # noqa: BLE001 - a parser failure is a finding
            stats["parser_failure"] += 1
            parser_failures.append({"question": case.get("question"),
                                    "error": f"{type(exc).__name__}: {exc}"})
            continue
        f = classify_failure(case.get("question") or "", out, case)
        outcome = f["outcome"]
        if outcome == "correct_verified":
            stats["correct_verified"] += 1
        elif outcome == "correct_refusal":
            stats[_REFUSAL_KEY.get(case.get("status"), "correct_refusal")] += 1
        elif outcome == "incorrect_confident":
            stats["incorrect_confident"] += 1
        elif outcome == "incorrect_refusal":
            stats["incorrect_refusal"] += 1
        if f["category"]:
            by_category[f["category"]] = by_category.get(f["category"], 0) + 1
    return {
        "report": stats,
        "failure_taxonomy": dict(sorted(by_category.items())),
        "parser_failures": parser_failures,
        "unsafe_confident_answers": by_category.get("UNSAFE_CONFIDENCE", 0),
    }


# ---------------------------------------------------------------------------
# 6. Replay failure capture (spec section 7)
# ---------------------------------------------------------------------------
def capture_replay_fixture(question: str,
                           out: Dict[str, Any]) -> Dict[str, Any]:
    """A deterministic replay fixture for a case that reached the reasoning
    stage. The fixture captures the canonical IR + calculation plan + C++
    authority + verification - enough to reproduce the outcome without any
    natural-language re-interpretation."""
    record = build_replay_record(str(question or "").strip())
    return {
        "question": str(question or "").strip(),
        "status": out.get("status"),
        "replay_id": record.get("replay_id"),
        "schema_version": REPLAY_SCHEMA_VERSION,
        "replay": serialize_replay(record),
    }


def replay_fixture_regression(fixtures: List[Dict[str, Any]],
                              ) -> Dict[str, Any]:
    """Re-execute stored replay fixtures and prove byte-identical replay.

    For every fixture: replay_execute(record) twice and
    serialize -> deserialize -> execute must all produce the SAME
    serialized output. Any divergence is REPORTED (REPLAY_DIVERGED), never
    silently repaired (15G contract)."""
    diverged: List[Dict[str, Any]] = []
    replayed = 0
    for fx in fixtures:
        record = deserialize_replay(fx["replay"])
        out1 = replay_execute(record)
        out2 = replay_execute(record)
        if serialize_replay(out1) != serialize_replay(out2):
            diverged.append({
                "question": fx.get("question"),
                "replay_id": fx.get("replay_id"),
                "reason": "repeated execution diverged",
            })
            continue
        out3 = replay_execute(deserialize_replay(fx["replay"]))
        if serialize_replay(out1) != serialize_replay(out3):
            diverged.append({
                "question": fx.get("question"),
                "replay_id": fx.get("replay_id"),
                "reason": "serialize->deserialize->execute diverged",
            })
            continue
        if (out1.get("status") or out1.get("final_result", {}).get("status")
                ) != fx.get("status"):
            diverged.append({
                "question": fx.get("question"),
                "replay_id": fx.get("replay_id"),
                "reason": f"replay status {out1.get('status')} != fixture "
                          f"{fx.get('status')}",
            })
            continue
        replayed += 1
    return {"fixtures": len(fixtures), "replayed_ok": replayed,
            "diverged": diverged}


# ---------------------------------------------------------------------------
# 7. Hard-gate scan (spec section 12) - powered by the 15G validators
# ---------------------------------------------------------------------------
def hard_gate_violations(question: str, out: Dict[str, Any],
                         ) -> List[str]:
    """The absolute release gates for ONE case. Returns [] when clean.

    unsafe confident answers, fabricated amounts, invented accounts,
    silent substitutions, unbalanced VERIFIED journals, formula_id=None
    confident results, C++ authority violations, replay divergence on
    deterministic cases, lineage missing on confident outputs,
    discrepancies silently repaired - all surface here."""
    violations: List[str] = []
    if out.get("status") != VERIFIED:
        # a refusal must be clean (zero fabricated lines)
        if out.get("debit_lines") or out.get("credit_lines"):
            violations.append("FABRICATED_REFUSAL_OUTPUT")
        return violations

    pipeline = validate_pipeline(out)
    codes = {d.get("code") for d in pipeline.get("discrepancies") or []}
    if codes:
        violations.append("DISCREPANCY_" + ",".join(sorted(codes)))

    # lineage must be present and complete on every confident output
    lineage = build_lineage(question, out)
    supplied = [v for v in (lineage.get("values") or [])
                if v.get("provenance") == "QUESTION_SUPPLIED"]
    calculated = [v for v in (lineage.get("values") or [])
                  if v.get("provenance") == "CALCULATED"]
    supplied_roles = {v.get("role") for v in supplied}
    calculated_roles = {v.get("role") for v in calculated}
    overlap = sorted(supplied_roles & calculated_roles)
    if not lineage.get("received") or not lineage.get("canonical"):
        violations.append("LINEAGE_MISSING")
    if overlap:
        violations.append("SUPPLIED_AS_CALCULATED:" + ",".join(overlap))
    if not calculated and (out.get("calculation_records")):
        pass  # calculated list may legitimately be empty for pure postings
    # C++ authority: registered metric results must carry a formula_id and
    # be routed to C++ (the maths bridge is checked by the C++ tests; here
    # we assert no confident result claims a missing formula id).
    for j in ((out.get("journals") or [out.get("journal")]) or []):
        for rec in (j.get("calculation_records") or []):
            if rec.get("result") is not None and not rec.get("formula_id") \
                    and rec.get("requires_cpp"):
                violations.append("FORMULA_ID_NONE_CONFIDENT")
    return violations


def hard_gate_summary(cases: List[Dict[str, Any]],
                      runner) -> Dict[str, Any]:
    """Aggregate hard-gate violations across a corpus."""
    violations: Dict[str, int] = {}
    details: List[Dict[str, Any]] = []
    unsafe_confident = 0
    for case in cases:
        out = runner(case)
        q = case.get("question") or ""
        # a confident answer where the oracle demands a refusal is the most
        # dangerous gate breach - counted explicitly here (UNSAFE_CONFIDENCE
        # is a coverage-classifier label, never a violation code, so it is
        # measured directly, not via the violations map).
        if case.get("status") != VERIFIED and out.get("status") == VERIFIED:
            unsafe_confident += 1
        for code in hard_gate_violations(q, out):
            violations[code] = violations.get(code, 0) + 1
            details.append({"question": q, "code": code})
    return {"total_cases": len(cases), "violations": violations,
            "details": details,
            "clean": not violations and unsafe_confident == 0,
            "unsafe_confident": unsafe_confident}
