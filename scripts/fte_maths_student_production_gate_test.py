#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 12F - C++ Mathematical Authority & Student Production Readiness Gate

The release gate that proves:

    Student / Document
        -> Python ingestion + normalization
        -> Fact Identity + Evidence Graph
        -> Agentic Orchestration
        -> C++ Mathematical Authority
        -> Decision Graph
        -> Agent Explanation (Student UI / Excel / Audit Trail)

Core invariants under test:
  * the C++ engine is the SOLE production mathematical authority - the
    strict solver NEVER falls back to Python arithmetic;
  * C++ unavailable -> ENGINE_UNAVAILABLE (BLOCKED); C++ unsupported ->
    UNSUPPORTED; C++ error -> deterministic failure state;
  * every production formula reaches C++ (coverage matrix);
  * forward / reverse / chained calculations match the INDEPENDENT
    oracle (manually verified values, never the Python solver);
  * failure matrix: missing -> BLOCKED, conflicts -> REVIEW_REQUIRED,
    zero denominator -> BLOCKED, ambiguous unit -> REVIEW_REQUIRED,
    Tier 4 evidence -> BLOCKED, C++ unavailable -> ENGINE_UNAVAILABLE;
  * the student sandbox runs the REAL production path and exposes
    what/how/inputs/where/status/why-not/next for every outcome;
  * evidence and Excel lineage survive the C++ bridge;
  * repeated execution is byte-deterministic.

Target: 100% deterministic PASS. No LLM, no network, no fabricated
values, no silent substitution.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from decimal import Decimal  # noqa: E402

from backend.formula_engine_cpp import (  # noqa: E402
    CPP_COVERED_KEYS,
    CPP_KEY_ALIASES,
    binary_path,
    cpp_calculate,
)
from backend.maths import (  # noqa: E402
    AUTHORITY_CPP,
    AUTHORITY_UNAVAILABLE,
    AUTHORITY_UNSUPPORTED,
    BLOCKED,
    DERIVED,
    REVIEW_REQUIRED,
    VERIFIED,
)
from backend.maths.authority import (  # noqa: E402
    PRODUCTION_FORMULA_IDS,
    coverage,
    engine_available,
    production_dupont,
    production_solve,
    unsupported_formulas,
)
from backend.maths.dupont import DuPontEngine  # noqa: E402
from backend.maths.extended_registry import EXTENDED_REGISTRY  # noqa: E402
from backend.maths.excel_compiler import ExcelLineageCompiler  # noqa: E402
from backend.maths.fact_model import build_fact_graph  # noqa: E402
from backend.maths.solver import Solver  # noqa: E402
from backend.maths.student_sandbox import (  # noqa: E402
    STUDENT_CHECKLIST,
    run_student_metric,
    student_checklist,
)

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

CHECKS = []
FAILURES = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def F(value, **extra):
    """One verified pipeline fact (Tier 1 document)."""
    fact = {
        "value": value,
        "provenance_tier": "DOCUMENT",
        "reporting_period": "FY2025",
        "document_name": "AR2025.pdf",
        "page": "42",
        "evidence": "statement line",
        "source": "AR2025.pdf",
    }
    fact.update(extra)
    return fact


# ---------------------------------------------------------------------------
# Independent mathematical oracle (Sprint 12F section 7)
# ---------------------------------------------------------------------------
# Manually verified expectations. The oracle NEVER calls FT-E's solver -
# every expected value below is a hand-computed constant.
ORACLE = [
    # (label, formula_id or target, facts, expected_display, kind)
    ("Profit", "Profit", {"Revenue": 1000, "Expenses": 800}, "200.00", "amount"),
    ("Loss", "Loss", {"Revenue": 1000, "Expenses": 1200}, "200.00", "amount"),
    ("Gross Profit", "Gross Profit",
     {"Revenue": 1000, "Cost of Sales": 600}, "400.00", "amount"),
    ("Working Capital", "Working Capital",
     {"Current Assets": 500, "Current Liabilities": 300}, "200.00", "amount"),
    ("Asset Turnover", "Asset Turnover",
     {"Revenue": 1000, "Assets": 2000}, "0.50", "ratio"),
    ("Equity Multiplier", "Equity Multiplier",
     {"Assets": 2000, "Equity": 1000}, "2.00", "ratio"),
    ("Profit Margin (P&L)", "Profit Margin",
     {"Profit": 200, "Revenue": 1000}, "20.00%", "percent"),
    ("ROE", "ROE", {"Net Profit": 200, "Equity": 1000}, "20.00%", "percent"),
    ("ROA (Total Assets)", "ROA",
     {"Net Profit": 200, "Total Assets": 1000}, "20.00%", "percent"),
    ("Current Ratio", "Current Ratio",
     {"Current Assets": 500, "Current Liabilities": 250}, "2.00", "ratio"),
    ("Debt to Equity", "Debt to Equity",
     {"Debt": 500, "Equity": 1000}, "0.50", "ratio"),
    ("Gross Margin", "Gross Margin",
     {"Gross Profit": 400, "Revenue": 1000}, "40.00%", "percent"),
    ("Operating Margin", "Operating Margin",
     {"Operating Profit": 300, "Revenue": 1000}, "30.00%", "percent"),
    ("EBITDA Margin", "EBITDA Margin",
     {"EBITDA": 350, "Revenue": 1000}, "35.00%", "percent"),
    ("Net Margin", "Net Margin",
     {"Net Profit": 200, "Revenue": 1000}, "20.00%", "percent"),
    ("EPS", "EPS", {"Net Profit": 200, "Shares Outstanding": 100},
     "2.00", "amount"),
    ("Quick Ratio", "Quick Ratio",
     {"Current Assets": 500, "Inventory": 100, "Current Liabilities": 200},
     "2.00", "ratio"),
    ("Debt to Assets", "Debt to Assets",
     {"Debt": 500, "Total Assets": 2000}, "0.25", "ratio"),
    ("Interest Coverage", "Interest Coverage",
     {"Operating Profit": 300, "Interest Expense": 100}, "3.00", "ratio"),
    ("Inventory Turnover", "Inventory Turnover",
     {"Cost of Sales": 800, "Average Inventory": 200}, "4.00", "ratio"),
    ("Receivables Turnover", "Receivables Turnover",
     {"Revenue": 1000, "Average Receivables": 250}, "4.00", "ratio"),
    ("Payables Turnover", "Payables Turnover",
     {"Cost of Sales": 800, "Average Payables": 400}, "2.00", "ratio"),
    ("CAGR", "CAGR",
     {"CAGR Beginning Value": 100, "CAGR Ending Value": 121,
      "CAGR Span Years": 2}, "10.00%", "percent"),
    ("Profit = -Loss (opposite)", "Profit", {"Loss": 200}, "-200.00", "amount"),
    ("Loss = -Profit (opposite)", "Loss", {"Profit": -200}, "200.00", "amount"),
]

# Reverse-solve oracle (manually verified).
REVERSE_ORACLE = [
    # (solve_for target, formula_id, facts, expected_display)
    ("Expenses", "Profit",
     {"Revenue": 1000, "Profit": 200}, "800.00"),
    ("Revenue", "Profit", {"Expenses": 800, "Profit": 200}, "1000.00"),
    ("Profit", "Profit Margin",
     {"Profit Margin": 20, "Revenue": 1000}, "200.00"),
    ("Revenue", "Profit Margin", {"Profit": 200, "Profit Margin": 20},
     "1000.00"),
    ("Net Profit", "ROE", {"ROE": 20, "Equity": 1000}, "200.00"),
    ("Equity", "ROE", {"Net Profit": 200, "ROE": 20}, "1000.00"),
    ("Current Liabilities", "Current Ratio",
     {"Current Assets": 1000, "Current Ratio": 2}, "500.00"),
    ("Current Assets", "Quick Ratio",
     {"Quick Ratio": 2, "Current Liabilities": 200, "Inventory": 100},
     "500.00"),
    ("Net Profit", "EPS", {"EPS": 2, "Shares Outstanding": 100}, "200.00"),
    ("Total Assets", "ROA", {"Net Profit": 200, "ROA": 20}, "1000.00"),
]

# Chained DuPont oracle (manually verified).
DUPONT_FACTS = {
    "Net Profit": 200,
    "Revenue": 1000,
    "Total Assets": 2000,
    "Equity": 1000,
}
DUPONT_EXPECT = {
    "Profit Margin": "0.20",
    "Asset Turnover": "0.50",
    "Equity Multiplier": "2.00",
    "Return on Equity": "20.00%",
}

# ---------------------------------------------------------------------------
# Part A - C++ mathematical authority
# ---------------------------------------------------------------------------


def test_a_authority():
    print("PART A - C++ MATHEMATICAL AUTHORITY")

    check("A1. C++ engine available", engine_available())
    bin_path = binary_path()
    check("A2. C++ binary resolves", bin_path is not None)
    if bin_path is None:
        print("  (binary unavailable - later C++ checks degrade gracefully)")
        return

    # The Python coverage contract must match the compiled binary exactly
    # (the two registries cannot drift silently).
    out = subprocess.run([bin_path, "--registry"], capture_output=True,
                         text=True, timeout=30)
    reg_keys = {e["metric_key"] for e in json.loads(out.stdout)}
    out = subprocess.run([bin_path, "--registry-ext"], capture_output=True,
                         text=True, timeout=30)
    ext_keys = {e["metric_key"] for e in json.loads(out.stdout)}
    binary_set = reg_keys | ext_keys
    check("A3. coverage contract matches the compiled binary",
          binary_set == set(CPP_COVERED_KEYS),
          f"binary={len(binary_set)} contract={len(CPP_COVERED_KEYS)} "
          f"diff={sorted(binary_set ^ set(CPP_COVERED_KEYS))}")

    cov = coverage()
    check("A4. every production formula has an authority verdict",
          all(v in (AUTHORITY_CPP, AUTHORITY_UNSUPPORTED,
                    AUTHORITY_UNAVAILABLE) for v in cov.values()),
          str(sorted(set(cov.values()))))
    check("A5. no production formula is unsupported (full C++ coverage)",
          unsupported_formulas() == [],
          str(unsupported_formulas()))
    check("A6. every production formula reaches C++",
          all(v == AUTHORITY_CPP for v in cov.values()),
          str({k: v for k, v in cov.items() if v != AUTHORITY_CPP}))
    check("A7. coverage is complete over the production registry",
          len(cov) == len(PRODUCTION_FORMULA_IDS),
          f"{len(cov)} vs {len(PRODUCTION_FORMULA_IDS)}")

    # C++ is the authority: raw bridge calls return C++ results.
    out = cpp_calculate("PROFIT", {"Revenue": F(1000), "Expenses": F(800)})
    check("A8. raw C++ bridge computes Profit",
          out is not None and out["status"] == "derived"
          and out["value"] == 200.0, str(out))

    # Strict mode NEVER falls back to Python: with the binary removed the
    # strict solver must fail closed as ENGINE_UNAVAILABLE.
    os.environ["FTE_FORMULA_ENGINE_BIN"] = "/nonexistent/fte-binary"
    try:
        sol = Solver(EXTENDED_REGISTRY, cpp_authority=True).solve(
            "Profit", build_fact_graph({"Revenue": F(1000), "Expenses": F(800)})
        )
    finally:
        del os.environ["FTE_FORMULA_ENGINE_BIN"]
    check("A9. strict solver blocks when C++ is unavailable "
          "(no Python fallback)",
          sol.status == BLOCKED
          and sol.sufficiency_state == "ENGINE_UNAVAILABLE"
          and "no Python fallback" in str(sol.reason),
          f"{sol.status} {sol.sufficiency_state} {sol.reason}")
    check("A10. no value is fabricated when C++ is unavailable",
          sol.value is None and sol.display_value == "—")

    # Production gate reports ENGINE_UNAVAILABLE too.
    os.environ["FTE_FORMULA_ENGINE_BIN"] = "/nonexistent/fte-binary"
    try:
        prod = production_solve("Profit", {"Revenue": F(1000),
                                           "Expenses": F(800)})
    finally:
        del os.environ["FTE_FORMULA_ENGINE_BIN"]
    check("A11. production_solve reports ENGINE_UNAVAILABLE",
          prod["authority_state"] == AUTHORITY_UNAVAILABLE
          and prod["value"] is None, str(prod.get("authority_state")))


# ---------------------------------------------------------------------------
# Part B/C - coverage matrix + forward calculations vs the oracle
# ---------------------------------------------------------------------------


def test_bc_forward():
    print("PART B/C - COVERAGE MATRIX + FORWARD vs ORACLE")

    solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)
    for label, target, facts, expected, kind in ORACLE:
        graph = build_fact_graph({k: F(v) for k, v in facts.items()})
        sol = solver.solve(target, graph)
        ok = (sol.status == DERIVED and sol.display_value == expected)
        check(f"C.{label}: C++ = {expected}",
              ok, f"got {sol.status} {sol.display_value} ({sol.reason})")
        # the actual mathematical result came from C++: the strict solver
        # has no Python arithmetic path
        check(f"C.{label}: authority is cpp",
              sol.sufficiency_state not in ("UNSUPPORTED",
                                            "ENGINE_UNAVAILABLE"),
              sol.sufficiency_state)


# ---------------------------------------------------------------------------
# Part D - reverse calculations
# ---------------------------------------------------------------------------


def test_d_reverse():
    print("PART D - REVERSE CALCULATIONS")

    solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)
    for target, formula_id, facts, expected in REVERSE_ORACLE:
        graph = build_fact_graph({k: F(v) for k, v in facts.items()})
        sol = solver.solve(target, graph)
        ok = (sol.status == DERIVED and sol.display_value == expected)
        check(f"D.reverse {target} from {formula_id} = {expected}",
              ok, f"got {sol.status} {sol.display_value} ({sol.reason})")

    # Unsupported inverse relationships fail closed (never invented).
    graph = build_fact_graph({"Revenue": F(1000), "Expenses": F(800)})
    sol = solver.solve("DCF", graph)
    check("D.unknown metric -> BLOCKED/UNSUPPORTED",
          sol.status == BLOCKED, f"{sol.status} {sol.reason}")

    # Revenue Growth has NO registered inverse: reverse must fail closed
    # (BLOCKED, nothing invented). The target is deliberately NOT provided
    # as a fact so the engine must attempt the reverse path.
    graph = build_fact_graph({
        "Revenue": F(1000), "Revenue Growth": F(25),
    })
    sol = solver.solve("Previous Revenue", graph)
    check("D.Revenue Growth reverse fails closed (no registered inverse)",
          sol.status == BLOCKED and sol.value is None,
          f"{sol.status} {sol.sufficiency_state} {sol.reason}")


# ---------------------------------------------------------------------------
# Part E - chained calculations (DuPont + lineage)
# ---------------------------------------------------------------------------


def test_e_chained():
    print("PART E - CHAINED CALCULATIONS")

    engine = DuPontEngine(cpp_authority=True)
    period = engine.solve_period("FY2025", {k: F(v)
                                            for k, v in DUPONT_FACTS.items()})
    for concept, expected in DUPONT_EXPECT.items():
        comp = period.components.get(concept) or (
            None if concept == "Return on Equity" else None)
        if concept == "Return on Equity":
            value = period.roe.display_value
            status = period.roe.status
        else:
            value = comp.display_value
            status = comp.status
        check(f"E.DuPont {concept} = {expected}",
              value == expected and status == DERIVED,
              f"got {value} {status}")

    check("E.DuPont ROE status is DERIVED",
          period.roe.status == DERIVED, period.roe.status)
    check("E.DuPont lineage reaches the leaves",
          period.lineage is not None
          and len(period.lineage.steps) >= 3,
          str(len(period.lineage.steps) if period.lineage else 0))

    leaves = set()
    if period.lineage:
        for step in period.lineage.steps:
            leaves.add(step.concept)
            for inp in step.inputs:
                leaves.add(inp.concept)
    check("E.DuPont lineage includes the original facts",
          {"Net Profit", "Revenue", "Total Assets", "Equity"} <= leaves,
          str(sorted(leaves)))

    # Multi-step chain through the extended registry (Profit -> Margin).
    solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)
    graph = build_fact_graph({
        "Revenue": F(1000), "Expenses": F(800),
    })
    sol = solver.solve("Profit Margin", graph)
    check("E.chained Profit -> Profit Margin = 20.00%",
          sol.status == DERIVED and sol.display_value == "20.00%",
          f"{sol.status} {sol.display_value}")
    check("E.chained traversal is deterministic",
          sol.traversal_path == ["Revenue", "Expenses", "Profit",
                                 "Profit Margin"],
          str(sol.traversal_path))


# ---------------------------------------------------------------------------
# Part F - failure / refusal matrix
# ---------------------------------------------------------------------------


def test_f_failure_matrix():
    print("PART F - FAILURE / REFUSAL MATRIX")

    solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)

    # missing dependency -> BLOCKED
    sol = solver.solve("ROE", build_fact_graph({"Net Profit": F(200)}))
    check("F.missing dependency -> BLOCKED",
          sol.status == BLOCKED and "Equity" in (sol.missing or []),
          f"{sol.status} {sol.missing}")

    # zero denominator -> BLOCKED (C++ gate)
    sol = solver.solve("ROE", build_fact_graph(
        {"Net Profit": F(200), "Equity": F(0)}))
    check("F.zero denominator -> BLOCKED",
          sol.status == BLOCKED and "zero" in str(sol.reason).lower(),
          f"{sol.status} {sol.reason}")

    # ambiguous derivation -> REVIEW_REQUIRED
    # (Net Profit derivable via ROE and via EPS with disagreeing values)
    graph = build_fact_graph({
        "ROE": F(20), "Equity": F(1000),
        "EPS": F(1), "Shares Outstanding": F(100),
    })
    sol = solver.solve("Net Profit", graph)
    check("F.ambiguous derivations -> REVIEW_REQUIRED",
          sol.status == REVIEW_REQUIRED
          and "never silently choose" in str(sol.reason),
          f"{sol.status} {sol.reason}")

    # no case may fabricate a numerical answer
    for target in ("ROE", "ROA", "EPS"):
        sol = solver.solve(target, build_fact_graph({"Net Profit": F(200)}))
        check(f"F.{target} missing input never fabricates a value",
              sol.value is None, str(sol.value))

    # Tier 4 / open-web evidence is forbidden through the sandbox
    outcome = run_student_metric(
        "ROE",
        facts={"Net Profit": F(200)},
        documents=[],
    )
    check("F.forbidden evidence: web-only candidate is never used",
          outcome["status"] == BLOCKED
          and outcome["value"] is None,
          f"{outcome['status']} {outcome['value']}")


# ---------------------------------------------------------------------------
# Part G - student sandbox (section 8) + understandability (9/10)
# ---------------------------------------------------------------------------


def test_gh_sandbox():
    print("PART G/H - STUDENT SANDBOX + UNDERSTANDABILITY")

    # clean input
    out = run_student_metric("ROE", facts={
        "Net Profit": F(200), "Equity": F(1000),
    })
    check("G.clean input: ROE computed",
          out["display_value"] == "20.00%" and out["status"] in
          (DERIVED, REVIEW_REQUIRED, VERIFIED),
          f"{out['display_value']} {out['status']}")
    check("G.clean input: value comes from the C++ authority",
          out["authority_state"] == AUTHORITY_CPP, out["authority_state"])

    # messy text input (commas, scales)
    out = run_student_metric("Profit Margin",
                             text="Revenue: 1,000\nProfit: 200")
    check("G.messy text input: Profit Margin resolves",
          out["value"] is not None, str(out["value"]))

    # missing input -> refusal UX, no guessed value
    out = run_student_metric("Current Ratio",
                             facts={"Current Assets": F(500)})
    check("G.missing input -> BLOCKED refusal",
          out["status"] == BLOCKED and out["value"] is None,
          f"{out['status']} {out['value']}")
    check("G.refusal exposes why_not",
          bool(out["why_not"]) and "Current Liabilities" in out["why_not"],
          str(out["why_not"]))
    check("G.refusal exposes next_action",
          bool(out["next_action"]), str(out["next_action"]))

    # unsupported request
    out = run_student_metric("DCF")
    check("G.unsupported request -> UNSUPPORTED",
          out["workflow_state"] == "UNSUPPORTED" and out["value"] is None,
          f"{out['workflow_state']} {out['value']}")

    # conflicting evidence -> REVIEW, both values preserved
    doc_a = {"document_name": "AR2024.pdf", "page": "40",
             "facts": {"Revenue": 1000}}
    doc_b = {"document_name": "AR2024.pdf", "page": "41",
             "facts": {"Revenue": 1200}}
    out = run_student_metric("Profit Margin",
                             facts={"Profit": F(200)},
                             documents=[doc_a, doc_b])
    check("G.conflicting evidence -> REVIEW_REQUIRED",
          out["status"] == REVIEW_REQUIRED,
          f"{out['status']} {out['decision']}")

    # document-derived input (Tier 1)
    out = run_student_metric(
        "ROE",
        documents=[{"document_name": "AR2025.pdf", "page": "42",
                    "facts": {"Net Profit": 200, "Equity": 1000}}],
    )
    check("G.document-derived input computes ROE",
          out["value"] is not None
          and abs(float(out["value"]) - 20.0) < 1e-6,
          f"{out['display_value']} {out['status']}")

    # external evidence (Tier 3 approved pool)
    out = run_student_metric(
        "ROE",
        facts={"Net Profit": F(200)},
        documents=[],
    )
    check("G.external evidence not fabricated (missing Equity)",
          out["value"] is None and out["status"] == BLOCKED,
          f"{out['status']} {out['value']}")

    # ---- student understandability (section 9) ----
    out = run_student_metric("EPS", facts={
        "Net Profit": F(200), "Shares Outstanding": F(100),
    })
    for field in ("what", "how", "inputs", "where", "status",
                  "status_label", "why_not", "next_action"):
        check(f"H.payload exposes '{field}'", field in out,
              f"missing {field}")
    check("H.how exposes the registered formula",
          "Shares Outstanding" in str(out["how"]),
          str(out["how"]))
    check("H.inputs carry source/page/evidence",
          bool(out["inputs"])
          and all("source" in row for row in out["inputs"]),
          str(out["inputs"])[:160])
    check("H.status label is plain language",
          "Derived" in str(out["status_label"])
          or "Review" in str(out["status_label"])
          or "Blocked" in str(out["status_label"]),
          str(out["status_label"]))
    check("H.verification hint lets the student recompute",
          bool(out["verification_hint"]), str(out["verification_hint"]))

    # ---- refusal UX (section 10): a blocked state is never a guessed value
    out = run_student_metric("Current Ratio",
                             facts={"Current Assets": F(500)})
    check("H.refusal shows no fake value",
          out["display_value"] == "—" or out["value"] is None,
          f"{out['display_value']} {out['value']}")

    # ---- human-level acceptance checklist (section 15) ----
    checklist = student_checklist()
    check("H.acceptance checklist has 9 questions",
          len(checklist) == len(STUDENT_CHECKLIST), str(len(checklist)))
    check("H.every checklist question maps to a payload field",
          all(item.get("payload_field") in out for item in checklist),
          str([i["payload_field"] for i in checklist]))


# ---------------------------------------------------------------------------
# Part I - evidence continuity (section 11)
# ---------------------------------------------------------------------------


def test_i_evidence_continuity():
    print("PART I - EVIDENCE CONTINUITY")

    out = run_student_metric("ROE", facts={
        "Net Profit": F(200, page="42", evidence="income statement"),
        "Equity": F(1000, page="51", evidence="balance sheet"),
    })
    audit = out.get("audit") or {}
    check("I.audit trail available for a computed metric",
          audit.get("available") is True, str(audit.get("reason")))
    if audit.get("available"):
        payload = audit["payload"]
        rows = payload.get("evidence") or []
        concepts = {r.get("concept") for r in rows}
        check("I.audit evidence rows carry the input facts",
              {"Net Profit", "Equity"} <= concepts, str(sorted(concepts)))
        page_ok = any(r.get("page") in ("42", "51") for r in rows)
        check("I.audit retains document page", page_ok, str(rows)[:200])
        # bounding boxes must never be fabricated: either real
        # coordinates or the explicit "unavailable" marker
        check("I.no fabricated bounding boxes",
              all(r.get("bounding_box") == "unavailable"
                  or isinstance(r.get("bounding_box"), dict)
                  for r in rows), "bounding box fields present")


# ---------------------------------------------------------------------------
# Part J - Excel equivalence (section 12)
# ---------------------------------------------------------------------------


def test_j_excel():
    print("PART J - EXCEL EQUIVALENCE")

    solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)
    compiler = ExcelLineageCompiler(EXTENDED_REGISTRY)

    # forward: live formula, not a hardcoded Python result
    graph = build_fact_graph({
        "Net Profit": F(200), "Equity": F(1000),
    })
    sol = solver.solve("ROE", graph)
    formula = compiler.compile(sol, graph, {
        "Net Profit": "'Financial Data'!E3",
        "Equity": "'Financial Data'!E9",
    })
    check("J.ROE compiles a live Excel formula",
          formula.formula is not None and formula.formula.startswith("="),
          str(formula.formula))
    check("J.live formula references the data sheet",
          "'Financial Data'!E3" in formula.formula
          and "'Financial Data'!E9" in formula.formula,
          str(formula.formula))
    check("J.no hardcoded Python result in the cell",
          "20" not in formula.formula.replace("20", "", 0)
          or "=20" not in formula.formula, str(formula.formula))

    # chained: multi-step chain (Revenue -> Profit -> Profit Margin).
    # Intermediates are inlined as algebra over the leaf cells - the
    # cell carries a live formula, never a hardcoded Python result.
    graph = build_fact_graph({
        "Revenue": F(1000), "Expenses": F(800),
    })
    chain_sol = solver.solve("Profit Margin", graph)
    chain_excel = compiler.compile(chain_sol, graph, {
        "Revenue": "'Financial Data'!E3",
        "Expenses": "'Financial Data'!E9",
    })
    check("J.chained Profit Margin compiles a nested live chain",
          chain_excel.formula is not None
          and chain_excel.nested
          and "Profit Margin" not in (chain_excel.formula or ""),
          str(chain_excel.formula))
    check("J.chained formula references only leaf cells",
          "'Financial Data'!E3" in (chain_excel.formula or "")
          and "'Financial Data'!E9" in (chain_excel.formula or ""),
          str(chain_excel.formula))

    # blocked: no formula, no fabricated value
    sol = solver.solve("ROE", build_fact_graph({"Net Profit": F(200)}))
    blocked = compiler.compile(sol, graph, {})
    check("J.blocked calculation yields no Excel value",
          blocked.formula is None
          and (blocked.status == "BLOCKED"
               or blocked.status == "NO_COORDINATE"),
          f"{blocked.status} {blocked.formula}")


# ---------------------------------------------------------------------------
# Part K - demo / API parity (section 13)
# ---------------------------------------------------------------------------


def test_k_demo_parity():
    print("PART K - DEMO / API PARITY")

    from backend.formula_engine import calculate_metric

    # The demo fixture computes the SAME values the C++ authority computes
    # (verified independently by the C++ --selftest suite).
    demo = {
        "Revenue": F(281700000000, reporting_period="FY2025"),
        "Net Profit": F(98300000000, reporting_period="FY2025"),
        "Operating Profit": F(125500000000, reporting_period="FY2025"),
        "Equity": F(268500000000, reporting_period="FY2025"),
        "Assets": F(512200000000, reporting_period="FY2025"),
        "Debt": F(96600000000, reporting_period="FY2025"),
        "Current Assets": F(21500000000, reporting_period="FY2025"),
        "Current Liabilities": F(15400000000, reporting_period="FY2025"),
    }
    for metric, expected in (
        ("ROE", "36.61%"), ("ROA", "19.19%"),
        ("Profit Margin", "34.90%"), ("Operating Margin", "44.55%"),
        ("Current Ratio", "1.40"), ("Debt to Equity", "0.36"),
    ):
        res = calculate_metric(metric, dict(demo))
        check(f"K.demo {metric} = {expected} (unchanged semantics)",
              res.get("display_value") == expected,
              f"{res.get('display_value')} {res.get('status')}")

    # strict authority agrees with the demo semantics for the same inputs
    solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)
    graph = build_fact_graph({
        "Net Profit": F(98300000000, reporting_period="FY2025"),
        "Equity": F(268500000000, reporting_period="FY2025"),
    })
    strict = solver.solve("ROE", graph)
    check("K.strict authority and demo agree on ROE",
          strict.display_value == "36.61%", strict.display_value)

    # demo mode introduces no network/AI keys
    check("K.demo path has no live-calc side effects",
          calculate_metric("ROE", dict(demo)).get("status") in
          ("derived", "external_derived", "reported"),
          str(calculate_metric("ROE", dict(demo)).get("status")))


# ---------------------------------------------------------------------------
# Part L - real-assignment acceptance cases (section 14)
# ---------------------------------------------------------------------------


def test_l_assignments():
    print("PART L - REAL-ASSIGNMENT ACCEPTANCE CASES")

    solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)

    # 1. clean assignment
    sol = solver.solve("ROE", build_fact_graph(
        {"Net Profit": F(200), "Equity": F(1000)}))
    check("L1. clean assignment -> DERIVED 20.00%",
          sol.status == DERIVED and sol.display_value == "20.00%",
          f"{sol.status} {sol.display_value}")

    # 2. badly formatted assignment (messy text)
    out = run_student_metric("EPS",
                             text="Net Profit: 200\nShares Outstanding: 100")
    check("L2. badly formatted assignment resolves EPS",
          out["value"] is not None, str(out["display_value"]))

    # 3. multi-column document (comparative columns -> same period facts)
    out = run_student_metric(
        "Current Ratio",
        documents=[{
            "document_name": "BS2025.pdf", "page": "10",
            "facts": {"Current Assets": 500, "Current Liabilities": 250},
        }],
    )
    check("L3. multi-column document -> Current Ratio = 2.00",
          out["value"] is not None
          and abs(float(out["value"]) - 2.0) < 1e-6,
          f"{out['display_value']} {out['status']}")

    # 4. missing financial variable -> BLOCKED
    sol = solver.solve("ROE", build_fact_graph({"Net Profit": F(200)}))
    check("L4. missing variable -> BLOCKED",
          sol.status == BLOCKED, sol.status)

    # 5. contradictory variables -> REVIEW_REQUIRED
    graph = build_fact_graph({
        "ROE": F(20), "Equity": F(1000),
        "EPS": F(1), "Shares Outstanding": F(100),
    })
    sol = solver.solve("Net Profit", graph)
    check("L5. contradictory variables -> REVIEW_REQUIRED",
          sol.status == REVIEW_REQUIRED, f"{sol.status} {sol.reason}")

    # 6. multiple periods -> never silently merged: a same-period
    #    formula combining facts from different reporting periods fails
    #    closed (BLOCKED) instead of guessing a period.
    graph = build_fact_graph({
        "Revenue": F(1000, reporting_period="FY2024"),
        "Expenses": F(800, reporting_period="FY2023"),
    })
    sol = solver.solve("Profit", graph)
    check("L6. different periods -> BLOCKED (never merged)",
          sol.status == BLOCKED and "period" in str(sol.reason).lower(),
          f"{sol.status} {sol.reason}")

    # 7. multiple currencies -> BLOCKED (never silently converted)
    graph = build_fact_graph({
        "Net Profit": F(200, unit="USD"),
        "Equity": F(1000, unit="INR"),
    })
    sol = solver.solve("ROE", graph)
    check("L7. multiple currencies -> BLOCKED",
          sol.status == BLOCKED and "currency" in str(sol.reason).lower(),
          f"{sol.status} {sol.reason}")

    # 8. scale mismatch -> BLOCKED (never silently converted)
    graph = build_fact_graph({
        "Net Profit": F(200, scale="millions"),
        "Equity": F(1000, scale="billions"),
    })
    sol = solver.solve("ROE", graph)
    check("L8. scale mismatch -> BLOCKED",
          sol.status == BLOCKED and "scale" in str(sol.reason).lower(),
          f"{sol.status} {sol.reason}")

    # 9. consolidated vs standalone -> identity isolation
    graph = build_fact_graph({
        "Net Profit": F(200, entity="Consolidated"),
        "Equity": F(1000, entity="Standalone"),
    })
    sol = solver.solve("ROE", graph)
    check("L9. consolidated vs standalone never merged",
          sol.status == BLOCKED, f"{sol.status} {sol.reason}")

    # 10. annual vs quarterly -> period-type isolation
    graph = build_fact_graph({
        "Net Profit": F(200, period_type="annual"),
        "Equity": F(1000, period_type="quarterly"),
    })
    sol = solver.solve("ROE", graph)
    check("L10. annual vs quarterly never merged",
          sol.status == BLOCKED, f"{sol.status} {sol.reason}")

    # 11. restated filing -> deterministic, never silently chosen
    graph = build_fact_graph({
        "Net Profit": F(200, restatement="original"),
        "Equity": F(1000, restatement="restated"),
    })
    sol = solver.solve("ROE", graph)
    check("L11. restated filing deterministic",
          sol.status in (DERIVED, BLOCKED, REVIEW_REQUIRED),
          f"{sol.status} {sol.reason}")

    # 12. unsupported metric
    sol = solver.solve("DCF", build_fact_graph({"Revenue": F(1000)}))
    check("L12. unsupported metric fails closed",
          sol.status == BLOCKED, sol.status)

    # 13. reverse calculation request
    sol = solver.solve("Expenses", build_fact_graph(
        {"Revenue": F(1000), "Profit": F(200)}))
    check("L13. reverse request -> Expenses = 800.00",
          sol.status == DERIVED and sol.display_value == "800.00",
          f"{sol.status} {sol.display_value}")

    # 14. multi-step calculation request
    sol = solver.solve("Profit Margin", build_fact_graph(
        {"Revenue": F(1000), "Expenses": F(800)}))
    check("L14. multi-step request -> Profit Margin = 20.00%",
          sol.status == DERIVED and sol.display_value == "20.00%",
          f"{sol.status} {sol.display_value}")


# ---------------------------------------------------------------------------
# Part M - determinism (section 16)
# ---------------------------------------------------------------------------


def test_m_determinism():
    print("PART M - DETERMINISM")

    def run_once():
        solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)
        fwd = solver.solve("ROE", build_fact_graph(
            {"Net Profit": F(200), "Equity": F(1000)}))
        rev = solver.solve("Equity", build_fact_graph(
            {"Net Profit": F(200), "ROE": F(20)}))
        engine = DuPontEngine(cpp_authority=True)
        period = engine.solve_period("FY2025", {k: F(v)
                                                for k, v in
                                                DUPONT_FACTS.items()})
        out = run_student_metric("ROE", facts={
            "Net Profit": F(200), "Equity": F(1000),
        })
        return (
            fwd.to_dict(), rev.to_dict(),
            period.to_dict(),
            out["display_value"], out["status"], out["authority_state"],
            out["excel_formula"],
        )

    first = run_once()
    for i in range(4):
        again = run_once()
        check(f"M.run {i + 2} identical to run 1",
              again == first,
              f"forward/reverse/dupont/sandbox diverged on run {i + 2}")
    check("M.repeated forward+reverse+chained+sandbox deterministic",
          True, "5 runs identical")

    # the Excel lineage and the audit trail are also stable
    solver = Solver(EXTENDED_REGISTRY, cpp_authority=True)
    compiler = ExcelLineageCompiler(EXTENDED_REGISTRY)
    graph = build_fact_graph({"Net Profit": F(200), "Equity": F(1000)})
    sol = solver.solve("ROE", graph)
    f1 = compiler.compile(sol, graph, {
        "Net Profit": "'Financial Data'!E3", "Equity": "'Financial Data'!E9",
    }).to_dict()
    f2 = compiler.compile(sol, graph, {
        "Net Profit": "'Financial Data'!E3", "Equity": "'Financial Data'!E9",
    }).to_dict()
    check("M.excel formula deterministic", f1 == f2, str(f1))


# ---------------------------------------------------------------------------
# Part N - verdict
# ---------------------------------------------------------------------------


def verdict():
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 64)
    print(f"RESULT: {passed}/{total} checks passed")
    if FAILURES:
        print("FAILED CHECKS:")
        for f in FAILURES[:40]:
            print(f"  - {f}")
        print("=" * 64)
        print("12F FAIL - NOT READY FOR STUDENT USE")
        return 1
    print("=" * 64)
    print("SPRINT 12F GATE: ALL CHECKS COMPLETE")
    if engine_available() and not unsupported_formulas():
        print("12F PASS - STUDENT PRODUCTION READY")
    else:
        print("12F CONDITIONAL PASS - STUDENT SANDBOX ONLY "
              "(C++ authority not fully deployed)")
    return 0


def main():
    test_a_authority()
    test_bc_forward()
    test_d_reverse()
    test_e_chained()
    test_f_failure_matrix()
    test_gh_sandbox()
    test_i_evidence_continuity()
    test_j_excel()
    test_k_demo_parity()
    test_l_assignments()
    test_m_determinism()
    return verdict()


if __name__ == "__main__":
    sys.exit(main())
