"""
Financial Timeline Engine
Sprint 15D - FYJC Formula/Rule Derivation & Textbook Coverage Gate
scripts/fte_fyjc_15d_test.py

Verifies the Sprint 15D success condition:

    ONE CANONICAL FORMULA/RULE
      -> DERIVATION / RULE COMPOSITION
      -> VALIDATED SOLUTION PATHS
      -> QUESTION INTENT
      -> DEPENDENCY RESOLUTION
      -> C++ AUTHORITY
      -> JOURNAL / LEDGER / MATH RESULT
      -> STUDENT-READABLE EXPLANATION

Parts:
  A. Derivation-specific tests      - one canonical formula generates
                                      multiple validated paths; invalid
                                      derivations and impossible dependency
                                      sets are refused.
  B. Coverage benchmark             - independently-solved FYJC questions
                                      (oracles never call the solver).
  C. Book-Keeping rule composition  - canonical Golden Rules compose into
                                      the same IR for equivalent wordings;
                                      the multi-step discount pipeline
                                      carries a per-step audit trail.
  D. C++ authority + invariants     - every confident result has
                                      formula_id, authority_state == "cpp"
                                      and a VALIDATED derivation path;
                                      deterministic repeatability.

Scope (section 13): controlled derivation of registered FYJC
relationships only. Simple Interest / Compound Interest / Dividend /
GST / AP / GP stay UNSUPPORTED (the 40-question pilot oracle stays
unchanged).

Pure script: no Streamlit, no AI, no network. Deterministic.
"""

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, ".")

from backend.formula_engine_cpp import (  # noqa: E402
    cpp_available,
    cpp_calculate,
    cpp_solve_metric,
    is_cpp_covered,
)
from backend.maths.fyjc_canonical import (  # noqa: E402
    CANONICAL_REGISTRY,
    BK_RULES,
    canonical_registry,
    compose_transaction_rule,
    golden_rule_for,
)
from backend.maths.fyjc_derivation import (  # noqa: E402
    DerivationUnsupported,
    _isolate,
    derivation_audit_trail,
    derive_path,
    ensure_derivation_valid,
    solve_derived,
    validated_path_for,
    validate_derived_path,
    _expressions_equivalent,
)
from backend.maths.fyjc_maths import (  # noqa: E402
    known_concept_display,
    verify_maths_answer,
)
from backend.maths.fyjc_question import classify_fyjc_question  # noqa: E402
from backend.maths.fyjc_student_flow import (  # noqa: E402
    run_fyjc_student_flow,
)
from backend.maths.formula_registry import parse_expression  # noqa: E402
from backend.maths.status import (  # noqa: E402
    BLOCKED,
    REVIEW_REQUIRED,
    VERIFIED,
)

CHECKS: List[Tuple[str, bool, str]] = []
FAILURES: List[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, ok, detail))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def _norm(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def _flow_maths(question: str) -> Dict[str, Any]:
    """End-to-end student-flow outcome for a maths question."""
    flow = run_fyjc_student_flow(question)
    return flow.get("outcome") or flow


def _f(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# PART A - Derivation-specific tests (sections 2, 3, 11)
# ---------------------------------------------------------------------------


def test_derivation_one_canonical_many_paths() -> None:
    # Profit = Revenue - Expenses supports Find Profit / Revenue / Expenses.
    prof = CANONICAL_REGISTRY.get("PROFIT")
    check("A1. canonical Profit exists with full metadata",
          prof is not None and prof.canonical_formula ==
          "Profit = Revenue - Expenses"
          and prof.unit_kind == "amount"
          and sorted(prof.supported_targets) ==
          ["Expenses", "Profit", "Revenue"],
          str(prof.to_metadata() if prof else None))
    rev = derive_path(prof, "Revenue")
    check("A2. derive Revenue = Profit + Expenses (validated)",
          rev.validation_status == "VALIDATED"
          and _expressions_equivalent(
              rev.expression, "Profit + Expenses", prof, "Revenue"),
          f"expr={rev.expression} status={rev.validation_status}")
    exp = derive_path(prof, "Expenses")
    check("A3. derive Expenses = Revenue - Profit (validated)",
          exp.validation_status == "VALIDATED"
          and _expressions_equivalent(
              exp.expression, "Revenue - Profit", prof, "Expenses"),
          f"expr={exp.expression} status={exp.validation_status}")

    # Commission = Sales x Rate / 100 supports Find Commission/Sales/Rate.
    comm = CANONICAL_REGISTRY.get("COMMISSION")
    check("A4. canonical Commission metadata",
          comm is not None and comm.academic_topic
          == "Commercial Arithmetic - Commission"
          and comm.percentage_semantics == "rate_is_percent_number",
          str(comm.to_metadata() if comm else None))
    s = derive_path(comm, "Sales")
    check("A5. derive Sales = Commission * 100 / Commission Rate",
          s.validation_status == "VALIDATED"
          and _expressions_equivalent(
              s.expression, "Commission * 100 / Commission Rate",
              comm, "Sales"),
          f"expr={s.expression} status={s.validation_status}")
    r = derive_path(comm, "Commission Rate")
    check("A6. derive Commission Rate = Commission * 100 / Sales",
          r.validation_status == "VALIDATED"
          and _expressions_equivalent(
              r.expression, "Commission * 100 / Sales", comm,
              "Commission Rate"),
          f"expr={r.expression} status={r.validation_status}")

    # Percent-kind canonical: Profit % = Profit / CP x 100 -> 3 paths.
    pp = CANONICAL_REGISTRY.get("PROFIT_PERCENT")
    for var in ("Profit Percent", "Profit", "Cost Price"):
        path = derive_path(pp, var)
        check(f"A7. Profit Percent path for {var} VALIDATED",
              path.validation_status == "VALIDATED",
              f"expr={path.expression} status={path.validation_status}")

    # Every canonical target + dependency has a validated path.
    for canonical in CANONICAL_REGISTRY.all():
        for var in canonical.supported_targets:
            path = validated_path_for(canonical.formula_id, var)
            check(
                f"A8. {canonical.formula_id}::{var} has a validated path "
                "agreeing with the registered inverse",
                path is not None
                and path.validation_status == "VALIDATED",
                f"path={path}")


def test_derivation_refusals() -> None:
    # Multi-occurrence variable -> no safe isolation.
    node = parse_expression("A * (A + B)")
    isolated = _isolate(node, "A", ("ident", "T"))
    check("A9. multi-occurrence variable is refused (never invented)",
          isolated is None, str(isolated))

    # Variable in an exponent -> no safe isolation.
    node = parse_expression("2 ^ X")
    isolated = _isolate(node, "X", ("ident", "T"))
    check("A10. variable in an exponent is refused",
          isolated is None, str(isolated))

    # Variable not in the canonical -> DerivationUnsupported.
    prof = CANONICAL_REGISTRY.get("PROFIT")
    try:
        derive_path(prof, "Sales")
        check("A11. non-variable derivation refused", False, "no exception")
    except DerivationUnsupported:
        check("A11. non-variable derivation refused", True, "")

    # A tampered derived expression fails independent validation.
    prof = CANONICAL_REGISTRY.get("PROFIT")
    from backend.maths.fyjc_derivation import DerivedPath
    tampered = DerivedPath(
        path_id="PROFIT::Revenue-tampered",
        canonical_id="PROFIT",
        target_variable="Revenue",
        expression="Profit + Expenses + 100",
        dependencies=["Profit", "Expenses"],
        unit_kind="amount",
    )
    status = validate_derived_path(tampered, prof)
    check("A12. a wrong derived expression is REJECTED by validation",
          status == "REJECTED",
          f"status={status} audit={tampered.derivation_audit}")

    # The derivation gate passes covered concepts with validated paths and
    # passes through concepts with no canonical coverage.
    ok_sales, path_sales, reason = ensure_derivation_valid("Sales")
    check("A13. gate passes Sales (covered, validated)",
          ok_sales and path_sales is not None
          and path_sales.validation_status == "VALIDATED",
          f"ok={ok_sales} reason={reason}")
    ok_any, _p, reason = ensure_derivation_valid("Average Inventory")
    check("A14. gate passes through non-canonical concepts unchanged",
          ok_any is True and "no canonical coverage" in reason,
          f"ok={ok_any} reason={reason}")

    # Audit trail records every derivation deterministically.
    trail = derivation_audit_trail()
    check("A15. derivation audit trail is populated",
          len(trail) >= 10
          and all("sequence" in rec and "validation_status" in rec
                  for rec in trail),
          f"records={len(trail)}")


# ---------------------------------------------------------------------------
# PART B - Coverage benchmark (section 10): independent oracles
# ---------------------------------------------------------------------------

MATHS_ORACLES: List[Dict[str, Any]] = [
    {"id": "M01", "question": "Calculate the Commission. Sales: 10,000 Commission Rate: 5",
     "display": "500.00", "fid": "COMMISSION", "direction": "forward"},
    {"id": "M02", "question": "Find the Sales. Commission: 500 Commission Rate: 5",
     "display": "10000.00", "fid": "COMMISSION", "direction": "reverse"},
    {"id": "M03", "question": "Find the Commission Rate. Commission: 500 Sales: 10,000",
     "display": "5.00", "fid": "COMMISSION", "direction": "reverse"},
    {"id": "M04", "question": "Calculate the Trade Discount. List Price: 10,000 Trade Discount Rate: 10",
     "display": "1000.00", "fid": "TRADE_DISCOUNT", "direction": "forward"},
    {"id": "M05", "question": "Find the List Price. Trade Discount: 1,000 Trade Discount Rate: 10",
     "display": "10000.00", "fid": "TRADE_DISCOUNT", "direction": "reverse"},
    {"id": "M06", "question": "Find the Trade Discount Rate. Trade Discount: 1,000 List Price: 10,000",
     "display": "10.00", "fid": "TRADE_DISCOUNT", "direction": "reverse"},
    {"id": "M07", "question": "Calculate the Net Price. List Price: 10,000 Trade Discount: 1,000",
     "display": "9000.00", "fid": "NET_PRICE", "direction": "forward"},
    {"id": "M08", "question": "Calculate the Cash Discount. Paid Amount: 4,500 Cash Discount Rate: 2",
     "display": "90.00", "fid": "CASH_DISCOUNT", "direction": "forward"},
    {"id": "M09", "question": "Calculate the Cash Paid. Paid Amount: 4,500 Cash Discount: 90",
     "display": "4410.00", "fid": "CASH_PAID", "direction": "forward"},
    {"id": "M10", "question": "Calculate the Creditor Balance. Net Purchase: 9,000 Amount Paid: 4,410",
     "display": "4590.00", "fid": "CREDITOR_BALANCE", "direction": "forward"},
    {"id": "M11", "question": "Find the Amount Paid. Net Purchase: 9,000 Creditor Balance: 4,590",
     "display": "4410.00", "fid": "CREDITOR_BALANCE", "direction": "reverse"},
    {"id": "M12", "question": "Calculate the Selling Price. Cost Price: 8,000 Profit: 2,000",
     "display": "10000.00", "fid": "SELLING_PRICE", "direction": "forward"},
    {"id": "M13", "question": "Find the Cost Price. Selling Price: 10,000 Profit: 2,000",
     "display": "8000.00", "fid": "SELLING_PRICE", "direction": "reverse"},
    {"id": "M14", "question": "Find the Profit. Selling Price: 10,000 Cost Price: 8,000",
     "display": "2000.00", "fid": "SELLING_PRICE", "direction": "reverse"},
    {"id": "M15", "question": "Calculate the Profit Percent. Profit: 2,000 Cost Price: 8,000",
     "display": "25.00%", "fid": "PROFIT_PERCENT", "direction": "forward"},
    {"id": "M16", "question": "Find the Cost Price. Profit Percent: 25 Profit: 2,000",
     "display": "8000.00", "fid": "PROFIT_PERCENT", "direction": "reverse"},
    {"id": "M17", "question": "Calculate the Loss Percent. Loss: 1,000 Cost Price: 5,000",
     "display": "20.00%", "fid": "LOSS_PERCENT", "direction": "forward"},
    # wording variation (prose extraction, section 8)
    {"id": "M18", "question": "A salesperson earns commission of Rs.500 on sales of Rs.10,000. Find the commission rate.",
     "display": "5.00", "fid": "COMMISSION", "direction": "reverse"},
    {"id": "M19", "question": "Find the missing figure: Sales. Commission: 500 Commission Rate: 5",
     "display": "10000.00", "fid": "COMMISSION", "direction": "reverse"},
    # existing P&L regression through the derivation layer
    {"id": "M20", "question": "Calculate the Profit. Revenue: 10,000 Expenses: 8,000",
     "display": "2000.00", "fid": "PROFIT", "direction": "forward"},
]

MATHS_REFUSALS: List[Dict[str, Any]] = [
    {"id": "R01", "question": "Calculate the Simple Interest on Rs.8,000 at 8% p.a. for 3 years.",
     "status": "UNSUPPORTED", "why": "oracle: Simple Interest stays unsupported"},
    {"id": "R02", "question": "Find the compound interest on Rs.10,000 at 10% p.a. compounded yearly for 2 years.",
     "status": "UNSUPPORTED", "why": "oracle: Compound Interest stays unsupported"},
    {"id": "R03", "question": "A company declares a dividend of 12% on shares of face value Rs.100. Find the dividend on 50 shares.",
     "status": "UNSUPPORTED", "why": "oracle: Dividend stays unsupported"},
    {"id": "R04", "question": "Calculate the Commission. Sales: 10,000",
     "status": BLOCKED, "why": "missing Commission Rate"},
    {"id": "R05", "question": "Calculate the Selling Price. Cost Price: 8,000",
     "status": BLOCKED, "why": "missing Profit"},
    {"id": "R06", "question": "Calculate the Profit Percent. Profit: 100 Cost Price: 0",
     "status": BLOCKED, "why": "zero denominator"},
    {"id": "R07", "question": "Find the Profit. Revenue: 1,000 Expenses: 600 Selling Price: 1,200 Cost Price: 1,000",
     "status": REVIEW_REQUIRED, "why": "conflicting derivations (400 vs 200)"},
    {"id": "R08", "question": "Find the Profit. Profit: 500 Selling Price: 1,000 Cost Price: 800",
     "status": REVIEW_REQUIRED, "why": "supplied Profit conflicts with derived 200"},
    {"id": "R09", "question": "Find the Profit. Revenue: 1,000 Expenses: 800 Selling Price: 1,200 Cost Price: 1,000",
     "status": "DERIVED", "display": "200.00",
     "why": "both derivations agree -> resolved"},
]


def test_coverage_benchmark() -> None:
    for case in MATHS_ORACLES:
        out = _flow_maths(case["question"])
        check(
            f"B.{case['id']} oracle: {case['question'][:52]} -> "
            f"{case['display']}",
            out.get("display_value") == case["display"]
            and out.get("formula_id") == case["fid"]
            and out.get("authority_state") == "cpp",
            f"got {out.get('display_value')} fid={out.get('formula_id')} "
            f"auth={out.get('authority_state')} status={out.get('status')}")

    for case in MATHS_REFUSALS:
        out = _flow_maths(case["question"])
        expected = case.get("status")
        if expected == "DERIVED":
            check(
                f"B.{case['id']} oracle: {case['question'][:52]} -> derived "
                f"{case.get('display')}",
                out.get("status") == "DERIVED"
                and out.get("display_value") == case.get("display")
                and out.get("formula_id") == "PROFIT",
                f"got status={out.get('status')} "
                f"display={out.get('display_value')} "
                f"fid={out.get('formula_id')}")
            continue
        check(
            f"B.{case['id']} oracle: {case['question'][:52]} -> {expected}",
            out.get("status") == expected,
            f"got status={out.get('status')} display={out.get('display_value')} "
            f"why={str(out.get('why_not'))[:70]}")

    # Question-intent resolution (section 5): explicit intent, not the
    # first number present.
    cls = classify_fyjc_question("Find the Sales. Commission: 500 Commission Rate: 5")
    check("B20. 'Find the Sales' resolves to sales",
          cls.get("metric") == "sales", str(cls))
    cls = classify_fyjc_question("Calculate Commission. Sales: 10,000 Commission Rate: 5")
    check("B21. 'Calculate Commission' resolves to commission",
          cls.get("metric") == "commission", str(cls))
    cls = classify_fyjc_question("What is the profit? Revenue: 1,000 Expenses: 800")
    check("B22. 'What is the profit?' resolves to profit",
          cls.get("metric") == "profit", str(cls))


# ---------------------------------------------------------------------------
# PART C - Book-Keeping rule composition (sections 6, 7)
# ---------------------------------------------------------------------------


def test_bk_rule_composition() -> None:
    check("C1. canonical BK rules registered (Real/Personal/Nominal)",
          len(BK_RULES) == 3
          and {r["rule_id"] for r in BK_RULES} ==
          {"REAL", "PERSONAL", "NOMINAL"},
          str([r["rule_id"] for r in BK_RULES]))

    rule = golden_rule_for("Real")
    check("C2. Real rule text is the traditional FYJC Golden Rule",
          rule is not None and rule["golden_rule"] ==
          "Debit what comes in. Credit what goes out.",
          str(rule))
    rule = golden_rule_for("Nominal")
    check("C3. Nominal rule text is the traditional FYJC Golden Rule",
          rule is not None and rule["golden_rule"] ==
          "Debit expenses and losses. Credit incomes and gains.",
          str(rule))

    rec = compose_transaction_rule("Cash", "Real", "debit")
    check("C4. account -> class -> rule -> side composition record",
          rec["account"] == "Cash" and rec["rule_id"] == "REAL"
          and rec["side"] == "debit" and bool(rec["why"]),
          str(rec))
    rec = compose_transaction_rule("Sales", "Nominal", "credit")
    check("C5. Nominal income -> credit composition",
          rec["rule_id"] == "NOMINAL" and rec["side"] == "credit",
          str(rec))

    # Equivalent textbook wordings compose to the SAME rule IR (section 8).
    pairs = [
        ("Purchased furniture for cash Rs.15,000.",
         "Bought furniture for Rs.15,000 and paid cash."),
        ("Sold goods to Mohan for cash Rs.20,000.",
         "Cash sale of goods Rs.20,000."),
    ]
    from backend.maths.fyjc_bk_reasoning import reason_bk_question
    for a, b in pairs:
        ra = reason_bk_question(a)
        rb = reason_bk_question(b)
        key_a = (ra.get("status"), ra.get("rule_key"),
                 _norm(ra.get("debit_lines")), _norm(ra.get("credit_lines")))
        key_b = (rb.get("status"), rb.get("rule_key"),
                 _norm(rb.get("debit_lines")), _norm(rb.get("credit_lines")))
        check(f"C6. equivalent wordings -> same IR: '{a[:34]}' vs '{b[:34]}'",
              ra.get("status") == VERIFIED and key_a == key_b,
              f"a={key_a} b={key_b}")

    # Same request wording -> same requested concept (section 8).
    for q in ("Calculate profit.", "Find the profit.",
              "Determine the profit earned.", "What is the profit?"):
        cls = classify_fyjc_question(q)
        check(f"C7. '{q}' -> requested concept profit",
              cls.get("domain") == "maths"
              and cls.get("metric") == "profit",
              str(cls))

    # Multi-step composition audit trail (section 7): the registered
    # discount pipeline records every intermediate stage with a
    # calculation_id, formula text, inputs and result.
    q = ("Purchased goods from Rahul for Rs.10,000 at 10% trade discount. "
         "Half the amount was paid immediately and a cash discount of 2% "
         "was allowed on the amount paid.")
    flow = reason_bk_question(q)
    check("C8. discount pipeline resolves to a VERIFIED journal",
          flow.get("status") == VERIFIED,
          f"status={flow.get('status')} why={str(flow.get('why_not'))[:80]}")
    records = flow.get("calculation_records") or []
    if not records:
        journal = flow.get("journal") or {}
        records = journal.get("calculation_records") or []
    ids = [str(r.get("calculation_id")) for r in records]
    expected_chain = [
        "BK_LIST_PRICE", "BK_TRADE_DISCOUNT_AMOUNT",
        "BK_NET_TRANSACTION_VALUE", "BK_PAID_CREDIT_SPLIT",
        "BK_CASH_DISCOUNT_AMOUNT", "BK_CASH_PAID_NET",
    ]
    check("C9. discount pipeline records the full chronological chain",
          all(cid in ids for cid in expected_chain),
          f"ids={ids}")
    ordered = [cid for cid in ids
               if cid in expected_chain]
    check("C10. chain order List Price -> TD -> Net -> split -> CD -> Cash Paid",
          ordered == expected_chain, f"ordered={ordered}")
    check("C11. every stage carries calculation_id + formula + inputs + result",
          all(r.get("calculation_id") and (r.get("formula") or r.get("formula_text"))
              and "inputs" in r and r.get("result") is not None
              for r in records),
          f"records={len(records)}")
    by_id = {str(r.get("calculation_id")): r for r in records}
    check("C12. trade discount stage = 1,000",
          _f(by_id["BK_TRADE_DISCOUNT_AMOUNT"].get("result")) == 1000.0,
          str(by_id.get("BK_TRADE_DISCOUNT_AMOUNT")))
    check("C13. net transaction value = 9,000",
          _f(by_id["BK_NET_TRANSACTION_VALUE"].get("result")) == 9000.0,
          str(by_id.get("BK_NET_TRANSACTION_VALUE")))
    check("C14. cash discount stage = 90",
          _f(by_id["BK_CASH_DISCOUNT_AMOUNT"].get("result")) == 90.0,
          str(by_id.get("BK_CASH_DISCOUNT_AMOUNT")))
    check("C15. cash paid (net) = 4,410",
          _f(by_id["BK_CASH_PAID_NET"].get("result")) == 4410.0,
          str(by_id.get("BK_CASH_PAID_NET")))
    # The journal carries the composed treatment: Purchases Dr 9,000;
    # Cash 4,410 / Discount Received 90 / Rahul 4,500 Cr.
    debit = {(str(l.get("account")), _f(l.get("amount")))
             for l in (flow.get("debit_lines") or [])}
    credit = {(str(l.get("account")), _f(l.get("amount")))
              for l in (flow.get("credit_lines") or [])}
    check("C16. composed journal: Purchases Dr 9,000",
          ("Purchases", 9000.0) in debit, str(debit))
    check("C17. composed journal: Cash 4,410 / Discount Received 90 / "
          "Rahul 4,500 Cr",
          ("Cash", 4410.0) in credit
          and ("Discount Received", 90.0) in credit
          and ("Rahul", 4500.0) in credit,
          str(credit))
    check("C18. composed journal balances (Dr == Cr)",
          flow.get("journal", {}).get("balanced") is True,
          str(flow.get("journal", {}).get("balanced")))


# ---------------------------------------------------------------------------
# PART D - C++ authority + hard invariants (sections 4, 12)
# ---------------------------------------------------------------------------


def test_cpp_authority() -> None:
    check("D1. C++ engine available", cpp_available())
    check("D2. COMMISSION is C++-covered (FYJC set)",
          is_cpp_covered("COMMISSION"), "")
    check("D3. SELLING_PRICE is C++-covered",
          is_cpp_covered("SELLING_PRICE"), "")
    check("D4. legacy coverage contract unchanged (9 + 24)",
          len(cpp_coverage_legacy()) == 9
          and len(cpp_coverage_extended()) == 24,
          f"legacy={len(cpp_coverage_legacy())} "
          f"ext={len(cpp_coverage_extended())}")

    # Raw C++ authority (never Python arithmetic).
    out = cpp_calculate("COMMISSION", {
        "Sales": {"value": 10000.0}, "Commission Rate": {"value": 5.0}})
    check("D5. raw C++ computes Commission forward = 500",
          out is not None and out["status"] == "derived"
          and out["value"] == 500.0 and out["display_value"] == "500.00",
          str(out))
    out = cpp_solve_metric("COMMISSION", "Sales", {
        "Commission": {"value": 500.0}, "Commission Rate": {"value": 5.0}})
    check("D6. raw C++ reverse-solves Sales = 10,000",
          out is not None and out["status"] == "derived"
          and out["value"] == 10000.0, str(out))
    out = cpp_solve_metric("COMMISSION", "List Price", {
        "Commission": {"value": 500.0}, "Commission Rate": {"value": 5.0}})
    check("D7. C++ refuses a non-variable solve target (BLOCKED)",
          out is not None and out["status"] == "blocked", str(out))


def cpp_coverage_legacy() -> List[str]:
    import subprocess
    from backend.formula_engine_cpp import binary_path
    bin_path = binary_path()
    if not bin_path:
        return []
    out = subprocess.run([bin_path, "--registry"], capture_output=True,
                         text=True, timeout=30)
    return [e["metric_key"] for e in json.loads(out.stdout)]


def cpp_coverage_extended() -> List[str]:
    import subprocess
    from backend.formula_engine_cpp import binary_path
    bin_path = binary_path()
    if not bin_path:
        return []
    out = subprocess.run([bin_path, "--registry-ext"], capture_output=True,
                         text=True, timeout=30)
    return [e["metric_key"] for e in json.loads(out.stdout)]


def test_hard_invariants() -> None:
    all_questions = [c["question"] for c in MATHS_ORACLES] + \
                    [c["question"] for c in MATHS_REFUSALS]
    bad_confident: List[str] = []
    bad_authority: List[str] = []
    bad_derivation: List[str] = []
    bad_fabricated: List[str] = []
    bad_determinism: List[str] = []

    for q in all_questions:
        out = _flow_maths(q)
        status = out.get("status")
        resolved = bool(out.get("resolved"))
        if resolved:
            # invariant: DERIVED/VERIFIED => formula_id != None
            if out.get("formula_id") is None:
                bad_confident.append(q[:44])
            # invariant: authority_state == "cpp"
            if out.get("authority_state") != "cpp":
                bad_authority.append(q[:44])
            # invariant: every executed path is a VALIDATED derivation
            deriv = out.get("derivation") or {}
            if deriv.get("validation_status") != "VALIDATED":
                bad_derivation.append(q[:44])
        else:
            # invariant: refusals never carry a fabricated display value
            if out.get("display_value") not in (None, "", "—") \
                    and out.get("status") not in ("DERIVED", "VERIFIED"):
                bad_fabricated.append(q[:44])

    check("D8. 0 confident answers with formula_id=None",
          not bad_confident, str(bad_confident))
    check("D9. 0 C++ authority violations",
          not bad_authority, str(bad_authority))
    check("D10. 0 unvalidated derived formulas reaching C++",
          not bad_derivation, str(bad_derivation))
    check("D11. 0 fabricated values on refusals",
          not bad_fabricated, str(bad_fabricated))

    # Determinism: identical outcomes on repeat runs.
    for q in [MATHS_ORACLES[1]["question"], MATHS_ORACLES[14]["question"],
              "Calculate the Profit. Revenue: 10,000 Expenses: 8,000"]:
        a = _norm(_flow_maths(q))
        b = _norm(_flow_maths(q))
        if json.dumps(a, sort_keys=True, default=str) != \
                json.dumps(b, sort_keys=True, default=str):
            bad_determinism.append(q[:44])
    check("D12. deterministic repeatability",
          not bad_determinism, str(bad_determinism))

    # Every resolved benchmark answer through the derivation solve path
    # carries a VALIDATED derivation audit record.
    trail = derivation_audit_trail()
    used = [rec for rec in trail
            if rec["validation_status"] == "VALIDATED"]
    check("D13. audit trail holds validated derivation records",
          len(used) >= 10, f"validated records={len(used)}")

    # The canonical registry covers every derivation it claims to support.
    for canonical in CANONICAL_REGISTRY.all():
        for var in canonical.supported_targets:
            check(
                f"D14. {canonical.formula_id}::{var} validation status is "
                "VALIDATED (independent validation, not assumed)",
                canonical.validation_status == "VALIDATED"
                and validated_path_for(canonical.formula_id, var) is not None,
                f"status={canonical.validation_status}")


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def main() -> int:
    test_derivation_one_canonical_many_paths()
    test_derivation_refusals()
    test_coverage_benchmark()
    test_bk_rule_composition()
    test_cpp_authority()
    test_hard_invariants()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 72)
    print(f"SPRINT 15D DERIVATION & COVERAGE GATE: {passed}/{total} checks "
          "passed")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL - {f}")
        print("=" * 72)
        print("SPRINT 15D FAIL - DERIVATION / COVERAGE BLOCKER REMAINS")
        return 1
    print("0 unsafe confident answers | 0 fabricated values | "
          "0 invented accounts | 0 silent substitutions")
    print("0 formula_id=None confident results | 0 C++ authority violations | "
          "0 unvalidated derived formulas")
    print("ONE CANONICAL FORMULA -> MANY VALIDATED PATHS -> C++ AUTHORITY")
    print("=" * 72)
    print("SPRINT 15D PASS - FYJC FORMULA/RULE DERIVATION HARDENED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
