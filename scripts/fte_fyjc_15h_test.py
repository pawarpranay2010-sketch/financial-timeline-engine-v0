#!/usr/bin/env python3
"""
Platrixa
Sprint 15H - Real-World FYJC BK Validation & Adversarial Hardening Gate
scripts/fte_fyjc_15h_test.py

Runs the INDEPENDENT 15H corpus (backend/maths/fyjc_bk_15h_benchmark.py -
genuine FYJC Ch.1-3 style questions hand-written as golden oracles, never
generated from the pattern registry) through the FULL pipeline
(question -> facts -> canonical normalization -> intent -> BK reasoning ->
replay IR -> C++ authority -> discrepancy validation) and enforces the
Sprint 15H hard release gates:

  * real-question corpus        (38 genuine Ch.1-3 textbook-style questions)
  * wording adversarial matrix  (7 misleading + 5 convergence families /
                                 21 equivalent wordings -> ONE treatment)
  * multi-transaction stress    (9 chained questions, continuation pronouns)
  * ambiguity attack set        (12 genuinely ambiguous -> REVIEW_REQUIRED /
                                 BLOCKED / NOT_SUPPORTED, never guessed)
  * student-error verification  (10 cases: first deterministic mistake with
                                 a SPECIFIC category, never 'Incorrect')
  * OCR / extraction boundary   (9 controlled Good/Uncertain/Unusable gates;
                                 an unreadable digit NEVER yields a number)
  * replay failure capture      (every deterministic case -> replay fixture;
                                 byte-identical re-execution, 0 divergence)
  * failure taxonomy            (one primary category per finding)
  * coverage report             (separate counters - never one accuracy %)
  * hard release gates          (unsafe confident = 0, fabricated = 0,
                                 invented = 0, unbalanced VERIFIED = 0,
                                 formula_id=None confident = 0, lineage = 0
                                 missing, replay divergence = 0, silent
                                 repair = 0)

The oracle NEVER calls the engine. No AI, no network, no guessing.
"""

import json
import sys
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, ".")

from backend.maths.fyjc_bk_15h import (
    classify_extraction_quality,
    coverage_report,
    hard_gate_summary,
    hard_gate_violations,
    process_extraction,
    replay_fixture_regression,
    verify_student_with_category,
)
from backend.maths.fyjc_bk_15h_benchmark import (
    AMBIGUITY_ATTACKS,
    BK15H_BENCHMARK,
    CONVERGENCE_FAMILIES,
    FAMILY_WORDINGS,
    FIX_REGRESSION_CASES,
    MISLEADING_CASES,
    MULTI_TRANSACTION_STRESS,
    OCR_BOUNDARY_CASES,
    REAL_QUESTION_CASES,
    REFUSAL_CASES,
    STUDENT_ERROR_15H,
    VERIFIED_CASES,
)
from backend.maths.fyjc_bk_reasoning import reason_bk_question
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED

NOT_SUPPORTED = "NOT_SUPPORTED"

CHECKS: list = []
FAILURES: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def norm_lines(lines) -> list:
    return sorted(
        (str(line.get("account") or ""),
         int(round(float(line.get("amount", 0)))))
        for line in lines if line.get("account")
    )


def merged_lines(out) -> tuple:
    journals = out.get("journals") or [out.get("journal")] or []
    dr = [l for j in journals for l in (j.get("debit_lines") or [])]
    cr = [l for j in journals for l in (j.get("credit_lines") or [])]
    return dr, cr


def _reason(case) -> Dict[str, Any]:
    return reason_bk_question(str(case.get("question") or "").strip())


def _full_check(case, out) -> Optional[str]:
    """Full pipeline check for one VERIFIED case (mirrors 15F gate)."""
    if out.get("status") != VERIFIED:
        return (f"status={out.get('status')} expected=VERIFIED "
                f"why={str(out.get('why_not'))[:80]}")
    journals = out.get("journals") or [out.get("journal")] or []
    if len(journals) != case.get("journals", 1):
        return f"journal count={len(journals)} expected={case.get('journals')}"
    dr, cr = merged_lines(out)
    exp_dr = sorted((a, int(v)) for a, v in case["debit"])
    exp_cr = sorted((a, int(v)) for a, v in case["credit"])
    if norm_lines(dr) != exp_dr or norm_lines(cr) != exp_cr:
        return (f"Dr {norm_lines(dr)} != {exp_dr} | "
                f"Cr {norm_lines(cr)} != {exp_cr}")
    if case.get("journals", 1) == 1 and case.get("type_key"):
        tk = (out.get("understanding") or {}).get("question_type_key")
        if tk != case.get("type_key"):
            return f"type_key={tk} expected={case.get('type_key')}"
    if any(not j.get("balanced") for j in journals):
        return "unbalanced journal(s)"
    tb = out.get("trial_balance") or {}
    ledger = out.get("ledger") or {}
    if tb.get("balanced") is not True:
        return f"trial_balance balanced={tb.get('balanced')}"
    if ledger.get("balanced") is not True:
        return "ledger unbalanced"
    bad = [l for l in dr + cr
           if not (l.get("class") and l.get("rule") and l.get("why"))]
    if bad:
        return "line missing class/rule/why"
    if (out.get("journal") or {}).get("narration") is None:
        return "missing narration"
    ids = [r.get("calculation_id")
           for r in (out.get("calculation_records") or [])
           if r.get("calculation_id")]
    if not ids:
        return "no calculation provenance"
    return None


def _refusal_check(case, out) -> Optional[str]:
    if out.get("status") != case.get("status"):
        return (f"status={out.get('status')} expected={case.get('status')} "
                f"why={str(out.get('why_not'))[:80]}")
    if out.get("debit_lines") or out.get("credit_lines"):
        return "refusal carries fabricated journal lines"
    return None


def _run_bucket(name: str, cases, full=True) -> None:
    passed = 0
    for case in cases:
        out = _reason(case)
        if case.get("status") == VERIFIED:
            err = None if not full else _full_check(case, out)
        else:
            err = _refusal_check(case, out)
        if err is None:
            passed += 1
        else:
            check(f"{name}: {str(case.get('question'))[:55]}", False, err)
    check(f"{name}: {passed}/{len(cases)}", passed == len(cases),
          f"{passed}/{len(cases)}")


# ---------------------------------------------------------------------------
# 1. Independent real-question corpus (spec 1)
# ---------------------------------------------------------------------------
def test_real_question_corpus() -> None:
    _run_bucket("real-question corpus", REAL_QUESTION_CASES)


# ---------------------------------------------------------------------------
# 2. Wording adversarial matrix (spec 2)
# ---------------------------------------------------------------------------
def test_misleading_matrix() -> None:
    _run_bucket("misleading wording matrix", MISLEADING_CASES)


def test_convergence_families() -> None:
    """Equivalent wordings MUST collapse onto ONE canonical treatment."""
    bad = 0
    for fam in CONVERGENCE_FAMILIES:
        wordings = fam.get("wordings") or []
        signatures = set()
        for w in wordings:
            out = reason_bk_question(str(w).strip())
            if out.get("status") != VERIFIED:
                bad += 1
                check(f"convergence: {w[:55]}", False,
                      f"status={out.get('status')} why={str(out.get('why_not'))[:60]}")
                continue
            dr, cr = merged_lines(out)
            tk = (out.get("understanding") or {}).get("question_type_key")
            signatures.add((tk, tuple(norm_lines(dr)), tuple(norm_lines(cr))))
        if len(signatures) != 1:
            bad += 1
            check(f"convergence family: {fam.get('family')}", False,
                  f"{len(signatures)} distinct canonical treatments: "
                  f"{sorted(signatures)}")
        # the family treatment must match its own expected lines
        out = reason_bk_question(str(wordings[0]).strip())
        dr, cr = merged_lines(out)
        exp_dr = sorted((a, int(v)) for a, v in fam.get("debit") or [])
        exp_cr = sorted((a, int(v)) for a, v in fam.get("credit") or [])
        if norm_lines(dr) != exp_dr or norm_lines(cr) != exp_cr:
            bad += 1
            check(f"convergence oracle: {fam.get('family')}", False,
                  f"Dr {norm_lines(dr)} != {exp_dr} | Cr {norm_lines(cr)} "
                  f"!= {exp_cr}")
    # every expanded wording variant also runs VERIFIED with the family IR
    vbad = 0
    for w in FAMILY_WORDINGS:
        out = reason_bk_question(str(w["question"]).strip())
        if out.get("status") != VERIFIED:
            vbad += 1
            check(f"wording variant: {str(w['question'])[:55]}", False,
                  f"status={out.get('status')} why={str(out.get('why_not'))[:60]}")
            continue
        dr, cr = merged_lines(out)
        exp_dr = sorted((a, int(v)) for a, v in w["debit"])
        exp_cr = sorted((a, int(v)) for a, v in w["credit"])
        if norm_lines(dr) != exp_dr or norm_lines(cr) != exp_cr:
            vbad += 1
            check(f"wording variant lines: {str(w['question'])[:55]}", False,
                  f"Dr {norm_lines(dr)} != {exp_dr}")
    check(f"convergence: {len(CONVERGENCE_FAMILIES)} families collapse to "
          f"1 IR each + {len(FAMILY_WORDINGS)} wordings VERIFIED",
          bad == 0 and vbad == 0,
          f"{bad} family failures, {vbad} wording failures")


# ---------------------------------------------------------------------------
# 3. Multi-transaction stress + continuation (spec 3)
# ---------------------------------------------------------------------------
def test_multi_transaction_stress() -> None:
    _run_bucket("multi-transaction stress", MULTI_TRANSACTION_STRESS)
    # continuation semantics: a pronoun/continuation folds into the SAME
    # journal; a genuinely new sentence starts a NEW journal.
    q_fold = ("Purchased goods from Rahul for Rs.10,000. "
              "Paid him Rs.4,000 immediately.")
    out = reason_bk_question(q_fold)
    journals = out.get("journals") or [out.get("journal")] or []
    check("continuation: 'paid him' folds into the purchase journal",
          out.get("status") == VERIFIED and len(journals) == 1,
          f"status={out.get('status')} journals={len(journals)}")
    q_new = ("Purchased goods from Rahul for Rs.10,000. "
             "Paid rent Rs.4,000.")
    out = reason_bk_question(q_new)
    journals = out.get("journals") or [out.get("journal")] or []
    check("continuation: 'Paid rent' is a NEW transaction",
          out.get("status") == VERIFIED and len(journals) == 2,
          f"status={out.get('status')} journals={len(journals)}")


# ---------------------------------------------------------------------------
# 4. Ambiguity attack set (spec 4) - REVIEW_REQUIRED / BLOCKED, never guess
# ---------------------------------------------------------------------------
def test_ambiguity_attacks() -> None:
    _run_bucket("ambiguity attack set", AMBIGUITY_ATTACKS)
    unsafe = 0
    for case in AMBIGUITY_ATTACKS:
        out = _reason(case)
        if out.get("status") == VERIFIED:
            unsafe += 1
    check("ambiguity: 0 confident answers on ambiguous cases", unsafe == 0,
          f"{unsafe} guessed")
    # every refusal must carry a machine-readable reason
    missing = [c.get("question") for c in AMBIGUITY_ATTACKS
               if not (_reason(c).get("why_not") or "").strip()]
    check("ambiguity: every refusal has a reason", not missing,
          f"{len(missing)} without why_not")


# ---------------------------------------------------------------------------
# 5. Student-error verification (spec 5) - specific first error
# ---------------------------------------------------------------------------
def test_student_errors() -> None:
    from backend.maths.fyjc_bk_15h import _journal_error_detail
    _SAME_ROOT_CAUSE = ("WRONG_SIDE", "WRONG_ACCOUNT", "MISSING_ACCOUNT",
                        "INVENTED_ACCOUNT", "JOURNAL_UNBALANCED",
                        "WRONG_AMOUNT", "WRONG_CLASSIFICATION")
    passed = 0
    for case in STUDENT_ERROR_15H:
        res = verify_student_with_category(
            case["question"], case["student"], case["kind"])
        ok = (res.get("verdict") == case["expected_verdict"]
              and res.get("error_category") == case["expected_category"]
              and (case["expected_verdict"] != "INCORRECT"
                   or res.get("affected_component")))
        # hard invariant: for a journal submission, error_category,
        # first_mistake and affected_component must ALL derive from the
        # SAME root-cause-ordered detail - they can never disagree, even
        # on a combined-error answer (Sprint 15H remediation B).
        if ok and case["kind"] == "journal" and case["expected_category"] \
                in _SAME_ROOT_CAUSE:
            detail = _journal_error_detail(
                case["question"], case["student"] or {})
            agree = (res.get("error_category") == detail.get("category")
                     and res.get("first_mistake") == detail.get(
                         "first_mistake")
                     and res.get("affected_component") == detail.get(
                         "component"))
            if not agree:
                ok = False
                check(f"student-error fields disagree: "
                      f"{str(case['question'])[:50]}", False,
                      f"cat={res.get('error_category')} vs "
                      f"{detail.get('category')} | first_mistake="
                      f"{str(res.get('first_mistake'))[:40]!r} vs "
                      f"{str(detail.get('first_mistake'))[:40]!r} | "
                      f"component={res.get('affected_component')} vs "
                      f"{detail.get('component')}")
        if ok:
            passed += 1
        else:
            if not any(f.startswith(f"student-error fields disagree: "
                                    f"{str(case['question'])[:50]}")
                       for f in FAILURES):
                check(f"student-error: {str(case['question'])[:50]} "
                      f"[{case['expected_category']}]", False,
                      f"verdict={res.get('verdict')} "
                      f"cat={res.get('error_category')} "
                      f"component={res.get('affected_component')} "
                      f"why={str(res.get('why_not'))[:60]}")
    check(f"student-error categories: {passed}/{len(STUDENT_ERROR_15H)}",
          passed == len(STUDENT_ERROR_15H),
          f"{passed}/{len(STUDENT_ERROR_15H)}")


# ---------------------------------------------------------------------------
# 6. OCR / extraction boundary (spec 6) - never invent a digit
# ---------------------------------------------------------------------------
def test_ocr_boundary() -> None:
    passed = 0
    for case in OCR_BOUNDARY_CASES:
        quality = classify_extraction_quality(case["question"], case["signals"])
        out = process_extraction(case["question"], case["signals"])
        ok = (quality.get("state") == case["expected_state"]
              and out.get("status") == case["expected_status"])
        if ok:
            passed += 1
        else:
            check(f"ocr: {str(case['question'])[:45]} "
                  f"[{case['expected_state']}]", False,
                  f"state={quality.get('state')} "
                  f"status={out.get('status')} expected={case['expected_status']}")
    check(f"OCR/extraction boundary: {passed}/{len(OCR_BOUNDARY_CASES)}",
          passed == len(OCR_BOUNDARY_CASES),
          f"{passed}/{len(OCR_BOUNDARY_CASES)}")
    # a flagged unreadable digit must never surface as a parsed amount
    unreadable = [c for c in OCR_BOUNDARY_CASES
                  if (c.get("signals") or {}).get("unreadable_digit")
                  or (c.get("signals") or {}).get("unreadable_amount")]
    leaked = [c for c in unreadable
              if (process_extraction(c["question"], c["signals"]).get("status")
                  == VERIFIED)]
    check("ocr: 0 invented digits from unreadable signals", not leaked,
          f"{len(leaked)} cases produced VERIFIED from unreadable digits")


# ---------------------------------------------------------------------------
# 7. Fix regressions (spec 7) - fixed failures stay fixed
# ---------------------------------------------------------------------------
def test_fix_regressions() -> None:
    _run_bucket("fix regressions", FIX_REGRESSION_CASES)


# ---------------------------------------------------------------------------
# 8. Replay failure capture (spec 7) - deterministic, byte-identical
# ---------------------------------------------------------------------------
def test_replay_regression() -> None:
    from backend.maths.fyjc_bk_15h import capture_replay_fixture
    fixtures = []
    for case in VERIFIED_CASES:
        out = _reason(case)
        fixtures.append(capture_replay_fixture(case["question"], out))
    for case in FIX_REGRESSION_CASES:
        out = _reason(case)
        fixtures.append(capture_replay_fixture(case["question"], out))
    result = replay_fixture_regression(fixtures)
    check(f"replay: {result['replayed_ok']}/{result['fixtures']} re-execute "
          f"byte-identically", not result["diverged"],
          f"diverged={result['diverged'][:3]}")
    # persist the fixtures so a fixed failure stays a permanent regression
    try:
        with open("docs/fyjc_bk_15h_replay_fixtures.json", "w",
                  encoding="utf-8") as fh:
            json.dump(fixtures, fh, indent=1, default=str, sort_keys=True)
        check("replay: fixtures persisted", True,
              "docs/fyjc_bk_15h_replay_fixtures.json")
    except OSError as exc:  # pragma: no cover - docs dir missing
        check("replay: fixtures persisted", False, f"OSError: {exc}")


# ---------------------------------------------------------------------------
# 9. Hard release gates (spec 12)
# ---------------------------------------------------------------------------
def test_hard_gates() -> None:
    summary = hard_gate_summary(BK15H_BENCHMARK, _reason)
    check("hard-gate: 0 violations across all 66 reasoning cases",
          summary["clean"], f"violations={summary['violations']}")
    check("hard-gate: 0 unsafe confident answers",
          summary["unsafe_confident"] == 0,
          f"unsafe={summary['unsafe_confident']}")
    # explicit zero counters (independent of the summary)
    invented = 0
    unbalanced_journal = 0
    unbalanced_tb = 0
    for case in VERIFIED_CASES:
        out = _reason(case)
        if out.get("status") != VERIFIED:
            continue
        journals = out.get("journals") or [out.get("journal")] or []
        unbalanced_journal += sum(1 for j in journals
                                  if not j.get("balanced"))
        tb = out.get("trial_balance") or {}
        if tb.get("balanced") is not True:
            unbalanced_tb += 1
        found = set()
        for j in journals:
            for l in (j.get("debit_lines") or []) + (j.get("credit_lines")
                                                     or []):
                if l.get("account"):
                    found.add(l["account"])
        allowed = {a for a, _ in case["debit"]} | {a for a, _ in
                                                   case["credit"]}
        invented += len(found - allowed)
        for code in hard_gate_violations(case["question"], out):
            if code.startswith("FORMULA_ID_NONE_CONFIDENT"):
                pass  # counted via summary violations; kept for the report
    fabricated = sum(
        1 for case in REFUSAL_CASES
        if (_reason(case).get("debit_lines")
            or _reason(case).get("credit_lines")))
    check("hard-gate: 0 invented accounts in VERIFIED output",
          invented == 0, f"{invented}")
    check("hard-gate: 0 unbalanced VERIFIED journals",
          unbalanced_journal == 0, f"{unbalanced_journal}")
    check("hard-gate: 0 unbalanced VERIFIED trial balances",
          unbalanced_tb == 0, f"{unbalanced_tb}")
    check("hard-gate: 0 fabricated lines in refusals",
          fabricated == 0, f"{fabricated}")


# ---------------------------------------------------------------------------
# 10. Coverage report (spec 9) - separate counters, never one %
# ---------------------------------------------------------------------------
def test_coverage_report() -> None:
    report = coverage_report(BK15H_BENCHMARK, _reason)
    stats = report["report"]
    total = sum(stats[k] for k in (
        "correct_verified", "correct_review_required", "correct_blocked",
        "correct_not_supported", "incorrect_confident", "incorrect_refusal",
        "extraction_failure", "parser_failure"))
    check(f"coverage: every case counted exactly once "
          f"({total}/{stats['cases']})", total == stats["cases"],
          f"{total} != {stats['cases']}")
    check("coverage: 0 incorrect confident answers",
          stats["incorrect_confident"] == 0,
          f"{stats['incorrect_confident']}")
    check("coverage: 0 incorrect refusals",
          stats["incorrect_refusal"] == 0,
          f"{stats['incorrect_refusal']}")
    check("coverage: 0 parser failures", stats["parser_failure"] == 0,
          f"{stats['parser_failure']}")
    check("coverage: 0 unsafe confident answers",
          report["unsafe_confident_answers"] == 0,
          f"{report['unsafe_confident_answers']}")
    check("coverage: refusal buckets match the corpus split",
          (stats["correct_review_required"] + stats["correct_blocked"]
           + stats["correct_not_supported"])
          == len(REFUSAL_CASES),
          f"{stats['correct_review_required']}+{stats['correct_blocked']}+"
          f"{stats['correct_not_supported']} != {len(REFUSAL_CASES)}")
    # machine-readable report + human-readable coverage doc
    try:
        with open("docs/fyjc_bk_15h_coverage.json", "w",
                  encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=str, sort_keys=True)
        lines = [
            "# FYJC Book-Keeping - Sprint 15H Coverage",
            "",
            "Independent real-question validation corpus. The oracle is",
            "hand-written FYJC Ch.1-3 treatment; the engine never feeds it.",
            "",
            "| Counter | Count |",
            "|---|---|",
        ]
        for k in ("cases", "correct_verified", "correct_derived",
                  "correct_review_required", "correct_blocked",
                  "correct_not_supported", "incorrect_confident",
                  "incorrect_refusal", "extraction_failure",
                  "parser_failure"):
            lines.append(f"| {k} | {stats[k]} |")
        lines.append("")
        lines.append("## Failure taxonomy (primary category per finding)")
        for k, v in (report.get("failure_taxonomy") or {}).items():
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append("## Hard release gates")
        lines.append(f"- Unsafe confident answers: "
                     f"{report['unsafe_confident_answers']} (must be 0)")
        with open("docs/FYJC_15H_COVERAGE.md", "w",
                  encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        check("coverage: reports written", True,
              "docs/fyjc_bk_15h_coverage.json + docs/FYJC_15H_COVERAGE.md")
    except OSError as exc:  # pragma: no cover - docs dir missing
        check("coverage: reports written", False, f"OSError: {exc}")


def main() -> int:
    test_real_question_corpus()
    test_misleading_matrix()
    test_convergence_families()
    test_multi_transaction_stress()
    test_ambiguity_attacks()
    test_student_errors()
    test_ocr_boundary()
    test_fix_regressions()
    test_replay_regression()
    test_hard_gates()
    test_coverage_report()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 76)
    print(f"SPRINT 15H FYJC BK REAL-WORLD VALIDATION GATE: "
          f"{passed}/{total} checks passed")
    print(f"corpus: {len(REAL_QUESTION_CASES)} real questions + "
          f"{len(MISLEADING_CASES)} misleading + "
          f"{len(MULTI_TRANSACTION_STRESS)} multi-tx + "
          f"{len(AMBIGUITY_ATTACKS)} ambiguity = "
          f"{len(BK15H_BENCHMARK)} reasoning cases "
          f"(verified {len(VERIFIED_CASES)}, refusals {len(REFUSAL_CASES)})")
    print(f"        + {len(STUDENT_ERROR_15H)} student-error + "
          f"{len(OCR_BOUNDARY_CASES)} OCR-boundary + "
          f"{len(FIX_REGRESSION_CASES)} fix-regressions + "
          f"{len(CONVERGENCE_FAMILIES)} convergence families "
          f"({len(FAMILY_WORDINGS)} wordings)")
    if FAILURES:
        for f in FAILURES[:30]:
            print(f"  FAIL - {f}")
        print("=" * 76)
        print("SPRINT 15H FAIL - REAL-WORLD VALIDATION BLOCKER REMAINS")
        return 1
    print("HARD GATES: UNSAFE CONFIDENT=0 | FABRICATED AMOUNTS=0 | "
          "INVENTED ACCOUNTS=0 | UNBALANCED VERIFIED=0 | "
          "FORMULA_ID=None=0 | REPLAY DIVERGENCE=0 | LINEAGE MISSING=0 | "
          "SILENT REPAIR=0")
    print("REPLAY: DETERMINISTIC | C++ AUTHORITY: VERIFIED | "
          "EXTRACTION BOUNDARY: ENFORCED")
    print("=" * 76)
    print("SPRINT 15H PASS - FYJC BK CH.1-3 REAL-WORLD VALIDATION VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
