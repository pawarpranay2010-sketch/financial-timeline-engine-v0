#!/usr/bin/env python3
"""
Platrixa
Sprint 12D - Production-Grade Financial Reasoning, Evidence Recovery &
Adversarial Hardening

Adversarial test suite covering the 45 required items (section N):

  1. clean direct fact          24. forbidden web evidence
  2. forward calculation        25. approved external evidence
  3. reverse calculation        26. conflicting external evidence
  4. multi-step calculation     27. adjustment candidate
  5. DuPont                     28. explicit student adjustment
  6. missing dependency         29. Excel forward compilation
  7. blocked propagation        30. Excel reverse compilation
  8. review propagation         31. nested Excel lineage
  9. reconciliation conflict    32. blocked Excel output
 10. duplicate fact             33. deterministic repeated execution
 11. conflicting provenance     34. adversarial financial labels
 12. unit mismatch              35. parentheses negative values
 13. scale mismatch             36. millions/thousands normalization
 14. currency mismatch          37. percentage normalization
 15. period mismatch            38. EPS/share-unit safety
 16. entity mismatch            39. consolidated vs standalone isolation
 17. restatement                40. annual vs quarterly isolation
 18. zero denominator           41. restated filing handling
 19. negative denominator       42. evidence lineage reconstruction
 20. graph cycle                43. agent payload correctness
 21. long dependency chain      44. no fabricated value invariant
 22. unsupported inverse        45. no silent substitution invariant
 23. malformed formula

Target: 100% deterministic PASS. No LLM, no network, no fabricated values.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from decimal import Decimal

from backend.maths import (
    BLOCKED,
    DERIVED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
    ADJUSTMENT_REQUIRED,
    EVIDENCE_CONFLICT,
    METRIC_AVAILABLE,
    METRIC_BLOCKED,
    METRIC_DERIVED,
    INSUFFICIENT_EVIDENCE,
    RECONCILIATION_REQUIRED,
    AdjustmentEngine,
    AccountingGraph,
    DUPONT_REGISTRY,
    DuPontEngine,
    EXTENDED_REGISTRY,
    FormulaDefinition,
    FormulaRegistry,
    FactNode,
    FactGraph,
    ProvenanceGate,
    RegistrationError,
    Solver,
    build_fact_graph,
    evaluate_metric,
    propose_adjustment,
    trace_leaves,
    compile_excel_formula,
)
from backend.maths.normalization import parse_numeric_text, harden_fact_text
from backend.maths.identity import (
    identity_key,
    same_identity,
    differing_dimensions,
    detect_identity_ambiguity,
    group_by_identity,
)
from backend.maths.restatement import (
    CONFLICT,
    DIFFERENT_IDENTITY,
    DUPLICATE,
    INCOMPATIBLE_PERIODS,
    RESTATEMENT,
    classify_pair,
    resolve_analytical_fact,
)
from backend.maths.recovery import (
    BLOCKED as RECOVERY_BLOCKED,
    CONFLICT as RECOVERY_CONFLICT,
    MISSING as RECOVERY_MISSING,
    RECOVERED,
    EvidenceRecoveryEngine,
    recover_evidence,
)
from backend.maths.forensic_reconciliation import (
    FORENSIC_RECONCILIATION_REGISTRY,
    ForensicReconciliationEngine,
)
from backend.maths.excel_compiler import ExcelLineageCompiler

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond), ""))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def doc_fact(value, unit="USD", period="FY2024", source="Document A",
             page="42", evidence="source line", tier="DOCUMENT",
             document="AR2024.pdf", currency=None, **kw):
    d = {
        "value": value,
        "unit": unit,
        "reporting_period": period,
        "source": source,
        "page": page,
        "evidence": evidence,
        "provenance_tier": tier,
        "document_name": document,
    }
    if currency:
        d["currency"] = currency
    d.update(kw)
    return d


def make_node(node_id, concept, value, period="FY2024", entity=None,
              statement=None, period_type=None, version=None,
              filing_id=None, source="Doc A", status=VERIFIED, **kw):
    return FactNode(
        node_id=node_id, canonical_concept=concept, value=Decimal(value),
        period=period, entity=entity, statement=statement,
        period_type=period_type, version=version, filing_id=filing_id,
        source=source, source_tier="DOCUMENT", document_name="AR2024.pdf",
        page="1", evidence="line", status=status, **kw,
    )


# ---------------------------------------------------------------------------
# 1-5. basic engine paths
# ---------------------------------------------------------------------------


def test_01_05():
    # 1. clean direct fact
    node = evaluate_metric("Net Profit", {
        "Net Profit": doc_fact(800, page="42"),
    })
    check("1. clean direct fact -> METRIC_AVAILABLE / confidence verified",
          node.decision == METRIC_AVAILABLE
          and node.confidence_state == "verified"
          and node.source_tier == "DOCUMENT")

    # 2. forward calculation
    node2 = evaluate_metric("Profit", {
        "Revenue": doc_fact(1000, page="1"),
        "Expenses": doc_fact(200, page="2"),
    })
    check("2. forward calculation Revenue-Expenses -> Profit 800",
          node2.decision == METRIC_DERIVED
          and node2.value == Decimal(800)
          and node2.confidence_state == "derived")

    # 3. reverse calculation
    rev = Solver(EXTENDED_REGISTRY).solve("Expenses", build_fact_graph({
        "Revenue": doc_fact(1000, page="1"),
        "Profit": doc_fact(800, page="3"),
    }))
    check("3a. reverse Revenue+Profit -> Expenses = 200",
          rev.status == DERIVED and rev.value == Decimal(200))
    rev2 = Solver(EXTENDED_REGISTRY).solve("Expenses", build_fact_graph({
        "Revenue": doc_fact(1000, page="1"),
        "Loss": doc_fact(200, page="3"),
    }))
    check("3b. reverse Revenue+Loss -> Expenses = 1200",
          rev2.status == DERIVED and rev2.value == Decimal(1200))
    rev3 = Solver(EXTENDED_REGISTRY).solve("Profit", build_fact_graph({
        "Revenue": doc_fact(1000, page="1"),
        "Loss": doc_fact(200, page="3"),
    }))
    check("3c. Revenue+Loss -> Profit = -200 (registered opposite)",
          rev3.status == DERIVED and rev3.value == Decimal(-200))
    rev4 = Solver(EXTENDED_REGISTRY).solve("Revenue", build_fact_graph({
        "Profit": doc_fact(200, page="1"),
        "Profit Margin": doc_fact(20, page="2", unit="%"),
    }))
    check("3d. Profit+ProfitMargin=20% -> Revenue = 1000",
          rev4.status == DERIVED and rev4.value == Decimal(1000))
    rev5 = Solver(EXTENDED_REGISTRY).solve("Profit", build_fact_graph({
        "Revenue": doc_fact(1000, page="1"),
        "Profit Margin": doc_fact(20, page="2", unit="%"),
    }))
    check("3e. Revenue+ProfitMargin=20% -> Profit = 200",
          rev5.status == DERIVED and rev5.value == Decimal(200))

    # 4. multi-step calculation
    node4 = evaluate_metric("ROE", {
        "Net Profit": doc_fact(400, page="42"),
        "Equity": doc_fact(2000, page="88"),
    })
    check("4. multi-step ROE = 20.00%",
          node4.decision == METRIC_DERIVED
          and node4.value == Decimal("20.00"))

    # 5. DuPont
    analysis = DuPontEngine(prefer_cpp=False).analyze({
        "FY2024": {
            "Net Profit": doc_fact(400, period="FY2024", page="42"),
            "Revenue": doc_fact(2000, period="FY2024", page="40"),
            "Total Assets": doc_fact(4000, period="FY2024", page="87"),
            "Equity": doc_fact(2000, period="FY2024", page="88"),
        },
        "FY2025": {
            "Net Profit": doc_fact(600, period="FY2025", page="42"),
            "Revenue": doc_fact(3000, period="FY2025", page="40"),
            "Total Assets": doc_fact(6000, period="FY2025", page="87"),
            "Equity": doc_fact(2000, period="FY2025", page="88"),
        },
    })
    comp = analysis.comparisons[0]
    total = sum(c.contribution for c in comp.contributions)
    check("5. DuPont two periods, contributions sum to delta",
          len(analysis.periods) == 2
          and total == comp.absolute_change
          and comp.absolute_change == Decimal(10))


# ---------------------------------------------------------------------------
# 6-8. status propagation
# ---------------------------------------------------------------------------


def test_06_08():
    # 6. missing dependency
    node = evaluate_metric("ROE", {"Net Profit": doc_fact(800, page="42")})
    check("6. missing dependency -> METRIC_BLOCKED + missing + action",
          node.decision == METRIC_BLOCKED
          and "Equity" in node.missing
          and node.next_action == "provide_missing_evidence"
          and node.explanation["user_action_required"] is True)

    # 7. blocked propagation
    sol = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph({
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(2000, page="88", tier="BLOCKED",
                           evidence="extraction failed"),
    }))
    check("7. BLOCKED dependency -> BLOCKED downstream",
          sol.status == BLOCKED)

    # 8. review propagation
    sol8 = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph({
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(2000, page="88",
                           evidence="conflicting pages",
                           extraction_state="review_required"),
    }))
    check("8. REVIEW_REQUIRED input -> REVIEW_REQUIRED result",
          sol8.status == REVIEW_REQUIRED)


# ---------------------------------------------------------------------------
# 9-11. reconciliation conflict / duplicate / provenance
# ---------------------------------------------------------------------------


def test_09_11():
    # 9. reconciliation conflict (forensic BS identity)
    engine = ForensicReconciliationEngine()
    reconciled = engine.reconcile("BS_IDENTITY_ASSETS", doc_fact(200000), {
        "Liabilities": doc_fact(120000),
        "Equity": doc_fact(80000),
    })
    check("9a. BS identity reconciles (200000 = 120000 + 80000)",
          reconciled.status == "RECONCILED")
    conflicted = engine.reconcile("BS_IDENTITY_ASSETS", doc_fact(210000), {
        "Liabilities": doc_fact(120000),
        "Equity": doc_fact(80000),
    })
    check("9b. BS identity variance -> REVIEW_REQUIRED, values preserved",
          conflicted.status == REVIEW_REQUIRED
          and conflicted.observed_value == Decimal(210000)
          and conflicted.expected_value == Decimal(200000)
          and conflicted.absolute_variance == Decimal(10000))
    cf = engine.reconcile("CF_CASH_RECONCILIATION",
                          doc_fact(1000, period="FY2025"), {
        "Beginning Cash": doc_fact(100, period="FY2025"),
        "Cash from Operating Activities": doc_fact(500, period="FY2025"),
        "Cash from Investing Activities": doc_fact(300, period="FY2025"),
        "Cash from Financing Activities": doc_fact(100, period="FY2025"),
        "FX Effect": doc_fact(0, period="FY2025"),
    })
    check("9c. cash-flow bridge reconciles",
          cf.status == "RECONCILED")

    # 10. duplicate fact
    g = FactGraph()
    for i in range(2):
        g.add(make_node(f"Revenue {i}", "Revenue", 1000))
    cands = AdjustmentEngine().detect_anomalies(g)
    check("10. duplicate fact -> DUPLICATE_FACT candidate",
          any(c.kind == "DUPLICATE_FACT" for c in cands))

    # 11. conflicting provenance
    g2 = FactGraph()
    g2.add(make_node("Revenue (A)", "Revenue", 1000, status=VERIFIED))
    g2.add(make_node("Revenue (B)", "Revenue", 1100,
                     status=REVIEW_REQUIRED))
    cands2 = AdjustmentEngine().detect_anomalies(g2)
    check("11. conflicting provenance -> candidate",
          any(c.kind == "CONFLICTING_PROVENANCE" for c in cands2))


# ---------------------------------------------------------------------------
# 12-15. units / scale / currency / period mismatches
# ---------------------------------------------------------------------------


def test_12_15():
    # 12. unit mismatch (currency vs shares cannot divide)
    sol = Solver(EXTENDED_REGISTRY).solve("EPS", build_fact_graph({
        "Net Profit": doc_fact(800, page="42"),
        "Shares Outstanding": doc_fact(100, unit="shares", page="50"),
    }))
    check("12. currency/shares mixing fails closed",
          sol.status == BLOCKED)

    # 13. scale mismatch (spec example 125.4 millions vs 125400)
    g = FactGraph()
    g.add(FactNode(
        node_id="Revenue (M)", canonical_concept="Revenue",
        value=Decimal("125.4"), original_scale="millions",
        original_unit="USD millions", apply_scale=True, period="FY2024",
        currency="USD", source="A", source_tier="DOCUMENT",
        document_name="d.pdf", page="1", evidence="rev", status=VERIFIED,
    ))
    g.add(FactNode(
        node_id="Revenue (Abs)", canonical_concept="Revenue",
        value=Decimal(125400), period="FY2024", currency="USD",
        source="B", source_tier="DOCUMENT", document_name="d.pdf",
        page="9", evidence="rev", status=VERIFIED,
    ))
    cands = AdjustmentEngine().detect_anomalies(g)
    check("13. scale mismatch -> SCALE_MISMATCH candidate (both shown)",
          any(c.kind == "SCALE_MISMATCH" for c in cands))

    # 14. currency mismatch
    sol14 = Solver(EXTENDED_REGISTRY).solve("Profit", build_fact_graph({
        "Revenue": doc_fact(1000, page="1", currency="USD"),
        "Expenses": doc_fact(200, page="2", currency="INR"),
    }))
    check("14. USD vs INR -> BLOCKED (never converted)",
          sol14.status == BLOCKED)

    # 15. period mismatch
    sol15 = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph({
        "Net Profit": doc_fact(800, period="FY2024", page="42"),
        "Equity": doc_fact(2000, period="FY2025", page="88"),
    }))
    check("15. FY2024 vs FY2025 in same-period formula -> BLOCKED",
          sol15.status == BLOCKED)


# ---------------------------------------------------------------------------
# 16-17. entity isolation / restatement classification
# ---------------------------------------------------------------------------


def test_16_17():
    # 16. entity mismatch
    g = FactGraph()
    g.add(make_node("Rev (Cons)", "Revenue", 1000, entity="Consolidated"))
    g.add(make_node("Rev (Stand)", "Revenue", 1100, entity="Standalone"))
    issues = detect_identity_ambiguity(g)
    check("16a. entity mismatch -> ENTITY_MISMATCH isolation issue",
          any(i.kind == "ENTITY_MISMATCH" for i in issues))
    nodes = [g.get("Rev (Cons)"), g.get("Rev (Stand)")]
    check("16b. same_identity False across entities, identity_key differs",
          not same_identity(nodes[0], nodes[1])
          and identity_key(nodes[0]) != identity_key(nodes[1])
          and len(group_by_identity(g)) == 2)

    # 17. restatement classification
    a = make_node("NP v1", "Net Profit", 900, version="1")
    b = make_node("NP v2", "Net Profit", 950, version="2")
    v = classify_pair(a, b)
    check("17a. versioned pair -> RESTATEMENT",
          v.kind == RESTATEMENT)
    same_v = make_node("NP v1b", "Net Profit", 900, version="1")
    check("17b. identical value -> DUPLICATE",
          classify_pair(a, same_v).kind == DUPLICATE)
    no_v = make_node("NP nover", "Net Profit", 960)
    check("17c. no version metadata -> REVIEW_REQUIRED (not CONFLICT guess)",
          classify_pair(b, no_v).kind == REVIEW_REQUIRED)
    p_diff = make_node("NP fy25", "Net Profit", 900, period="FY2025")
    check("17d. different periods -> INCOMPATIBLE_PERIODS",
          classify_pair(a, p_diff).kind == INCOMPATIBLE_PERIODS)
    ent_diff = make_node("NP sub", "Net Profit", 900, entity="Subsidiary")
    check("17e. different entity -> DIFFERENT_IDENTITY",
          classify_pair(a, ent_diff).kind == DIFFERENT_IDENTITY)


# ---------------------------------------------------------------------------
# 18-23. denominators / cycles / chains / inverses / malformed
# ---------------------------------------------------------------------------


def test_18_23():
    # 18. zero denominator
    sol = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph({
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(0, page="88"),
    }))
    check("18. zero denominator -> BLOCKED",
          sol.status == BLOCKED)

    # 19. negative denominator (deterministic, no crash, no fabrication)
    sol19 = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph({
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(-100, page="88"),
    }))
    check("19. negative denominator -> deterministic negative result",
          sol19.status == DERIVED and sol19.value == Decimal(-800)
          and sol19.lineage is not None)

    # 20. graph cycle
    reg = FormulaRegistry()
    reg.register(FormulaDefinition(
        formula_id="CYC_A", target="CycleA", expression="CycleB",
        dependencies=["CycleB"], version="1.0",
    ))
    reg.register(FormulaDefinition(
        formula_id="CYC_B", target="CycleB", expression="CycleA",
        dependencies=["CycleA"], version="1.0",
    ))
    sol20 = Solver(reg, prefer_cpp=False).solve("CycleA", FactGraph())
    # The cycle is unsatisfiable in both directions, so the solver
    # terminates with BLOCKED naming the dependency that cannot be
    # established; the cycle itself is surfaced by detect_cycles /
    # assert_acyclic (checked below). Never infinite, never guessed.
    check("20. graph cycle terminates as BLOCKED (never infinite)",
          sol20.status == BLOCKED
          and sol20.reason is not None
          and ("CycleA" in sol20.reason or "CycleB" in sol20.reason))
    ag = AccountingGraph(reg)
    ag.add_formula_application("X", "CYC_A", ["Y"])
    ag.add_formula_application("Y", "CYC_B", ["X"])
    try:
        ag.assert_acyclic()
        cyclic = False
    except Exception:
        cyclic = True
    check("20b. AccountingGraph.assert_acyclic raises on cycle",
          cyclic)

    # 21. long dependency chain (10 links)
    reg2 = FormulaRegistry()
    reg2.register(FormulaDefinition(
        formula_id="CH_BASE", target="Ch0", expression="Base",
        dependencies=["Base"], version="1.0",
    ))
    for i in range(1, 10):
        reg2.register(FormulaDefinition(
            formula_id=f"CH_{i}", target=f"Ch{i}",
            expression=f"Ch{i - 1} + 1", dependencies=[f"Ch{i - 1}"],
            version="1.0",
        ))
    sol21 = Solver(reg2, prefer_cpp=False).solve("Ch9", build_fact_graph({
        "Base": doc_fact(0, page="1"),
    }))
    check("21. 10-link dependency chain -> 9, deterministic",
          sol21.status == DERIVED and sol21.value == Decimal(9)
          and len(sol21.traversal_path) >= 10)

    # 22. unsupported inverse
    sol22 = Solver(EXTENDED_REGISTRY).solve("Inventory", build_fact_graph({
        "Quick Ratio": doc_fact(1.5, page="1"),
        "Current Assets": doc_fact(200, page="2"),
        "Current Liabilities": doc_fact(100, page="3"),
    }))
    check("22. no registered inverse -> BLOCKED (never guessed)",
          sol22.status == BLOCKED)

    # 23. malformed formula
    try:
        FormulaDefinition(
            formula_id="BAD", target="Bad",
            expression="UnknownVariable + 1", dependencies=["Other"],
            version="1.0",
        )
        bad = False
    except RegistrationError:
        bad = True
    check("23. malformed formula registration rejected",
          bad)


# ---------------------------------------------------------------------------
# 24-26. evidence recovery (forbidden / approved / conflicting)
# ---------------------------------------------------------------------------


def test_24_26():
    # 24. forbidden web evidence
    res = recover_evidence("Revenue", {
        "web": {"value": 100, "provenance_tier": "OPEN_WEB"},
    })
    check("24a. open-web source -> BLOCKED, never substituted",
          res.status == RECOVERY_BLOCKED
          and "forbidden" in res.reason.lower())
    gate = ProvenanceGate()
    from backend.maths.fact_model import from_pipeline_fact
    from backend.maths.fact_model import build_fact_graph
    g = build_fact_graph({"Equity": {
        "value": 2000, "provenance_tier": "OPEN_WEB",
    }})
    check("24b. provenance gate BLOCKED on open-web tier",
          gate.validate_facts(g).verdict == "BLOCKED")

    # 25. approved external evidence
    res25 = recover_evidence("Revenue", {
        "api": {
            "value": 125.4, "normalized_value": 125400000,
            "provider": "SEBI API", "provider_identifier": "SEBI-2024-001",
            "reporting_period": "FY2024", "currency": "INR",
            "scale": "millions", "provenance_tier": "REGULATORY_API",
            "evidence": "filing reference",
        },
    }, retrieval_timestamp="2026-08-08T00:00:00Z")
    check("25a. approved external evidence -> RECOVERED",
          res25.status == RECOVERED
          and res25.value == Decimal(125400000))
    check("25b. external record retains full attribution, VERIFIED",
          res25.external_record is not None
          and res25.external_record.provider == "SEBI API"
          and res25.external_record.identifier == "SEBI-2024-001"
          and res25.external_record.retrieval_timestamp
          == "2026-08-08T00:00:00Z"
          and res25.external_record.verification_status == "VERIFIED")

    # 26. conflicting external evidence (both preserved)
    res26 = recover_evidence("Net Profit", {
        "is": {
            "value": 98300, "provenance_tier": "REGULATORY_API",
            "provider": "API A", "reporting_period": "FY2025",
            "evidence": "IS line",
        },
        "cf": {
            "value": 97900, "provenance_tier": "REGULATORY_API",
            "provider": "API B", "reporting_period": "FY2025",
            "evidence": "CF line",
        },
    })
    check("26. conflicting external evidence -> CONFLICT, both preserved",
          res26.status == RECOVERY_CONFLICT
          and res26.value is None and res26.chosen is None
          and len(res26.conflicts) >= 1
          and 98300 in [c.get("value") for c in res26.candidates])


# ---------------------------------------------------------------------------
# 27-28. adjustments
# ---------------------------------------------------------------------------


def test_27_28():
    g = FactGraph()
    g.add(FactNode(
        node_id="Revenue (M)", canonical_concept="Revenue",
        value=Decimal("125.4"), original_scale="millions",
        original_unit="USD millions", apply_scale=True, period="FY2024",
        currency="USD", source="A", source_tier="DOCUMENT",
        document_name="d.pdf", page="1", evidence="rev", status=VERIFIED,
    ))
    g.add(FactNode(
        node_id="Revenue (Abs)", canonical_concept="Revenue",
        value=Decimal(125400), period="FY2024", currency="USD",
        source="B", source_tier="DOCUMENT", document_name="d.pdf",
        page="9", evidence="rev", status=VERIFIED,
    ))
    cands = AdjustmentEngine().detect_anomalies(g)
    check("27. adjustment candidate detected (never auto-corrected)",
          any(c.kind == "SCALE_MISMATCH" for c in cands))
    scale_cand = next(c for c in cands if c.kind == "SCALE_MISMATCH")
    adj_node, record = propose_adjustment(
        scale_cand, 125400000, g,
        decision="ADJUST", reason="student verified absolute USD value",
    )
    check("28a. explicit adjustment -> STUDENT_INPUT node, original intact",
          adj_node.status == STUDENT_INPUT
          and record.status == STUDENT_INPUT
          and "125.4" in str(record.original_values))
    check("28b. adjustment lineage to anomaly + decision",
          "ADJUST" in adj_node.lineage and scale_cand.kind in adj_node.lineage)


# ---------------------------------------------------------------------------
# 29-32. Excel compilation
# ---------------------------------------------------------------------------


def test_29_32():
    coord = {"Net Profit": "'Financial Data'!E3",
             "Equity": "'Financial Data'!E9"}
    facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(2000, page="88"),
    }
    node = evaluate_metric("ROE", facts,
                           coordinate_map=coord)
    check("29. Excel forward compilation (spec example)",
          node.excel_formula is not None
          and node.excel_formula.formula ==
          "='Financial Data'!E3 / 'Financial Data'!E9")
    rev_sol = Solver(EXTENDED_REGISTRY).solve("Expenses", build_fact_graph({
        "Revenue": doc_fact(1000, page="1"),
        "Profit": doc_fact(800, page="3"),
    }))
    compiler = ExcelLineageCompiler(EXTENDED_REGISTRY)
    ex = compiler.compile(
        rev_sol, build_fact_graph({
            "Revenue": doc_fact(1000, page="1"),
            "Profit": doc_fact(800, page="3"),
        }),
        {"Revenue": "'Financial Data'!E3", "Profit": "'Financial Data'!E5"},
    )
    check("30. Excel reverse compilation uses registered inverse",
          ex.formula is not None and "-" in ex.formula
          and "'Financial Data'!E5" in ex.formula)
    d_node = evaluate_metric("Return on Equity", {
        "Net Profit": doc_fact(400, page="42"),
        "Revenue": doc_fact(2000, page="40"),
        "Total Assets": doc_fact(4000, page="87"),
        "Equity": doc_fact(2000, page="88"),
    }, registry=DUPONT_REGISTRY,
        coordinate_map={
            "Net Profit": "'Financial Data'!E3",
            "Revenue": "'Financial Data'!E5",
            "Total Assets": "'Financial Data'!E7",
            "Equity": "'Financial Data'!E9",
        })
    check("31. nested Excel lineage (DuPont chain)",
          d_node.excel_formula is not None
          and d_node.excel_formula.formula is not None
          and d_node.excel_formula.nested
          and d_node.excel_formula.formula.count("*") == 2)
    node_b = evaluate_metric("ROE", {"Net Profit": doc_fact(800)},
                             coordinate_map=coord)
    check("32. blocked Excel output preserves blocked state (no value)",
          node_b.excel_formula is None
          or node_b.excel_formula.formula is None)


# ---------------------------------------------------------------------------
# 33. determinism
# ---------------------------------------------------------------------------


def test_33():
    facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(2000, page="88"),
    }
    p1 = evaluate_metric("ROE", facts,
                         coordinate_map={"Net Profit": "'Financial Data'!E3",
                                         "Equity": "'Financial Data'!E9"}).to_payload()
    p2 = evaluate_metric("ROE", facts,
                         coordinate_map={"Net Profit": "'Financial Data'!E3",
                                         "Equity": "'Financial Data'!E9"}).to_payload()
    check("33. deterministic repeated execution (identical payloads)",
          p1 == p2)


# ---------------------------------------------------------------------------
# 34-38. adversarial normalization
# ---------------------------------------------------------------------------


def test_34_38():
    # 34. adversarial financial labels
    cases = [
        ("(1,234)", Decimal(-1234), None, None),
        ("1,234.5", Decimal("1234.5"), None, None),
        ("-1,234", Decimal(-1234), None, None),
        ("−5", Decimal(-5), None, None),           # unicode minus
        ("25%", Decimal(25), "%", None),
        ("garbage", None, None, None),
        ("12.5 per share", Decimal("12.5"), None, None),
    ]
    ok_cases = True
    for raw, exp_v, exp_unit, _ in cases:
        p = parse_numeric_text(raw)
        got = p.value
        if exp_v is None:
            ok_cases = ok_cases and got is None
        else:
            ok_cases = ok_cases and got == exp_v
        if exp_unit:
            ok_cases = ok_cases and p.unit == exp_unit
    check("34. adversarial financial labels parse deterministically",
          ok_cases)
    p_sym = parse_numeric_text("$1.2B")
    check("34b. $1.2B -> 1.2 billions USD",
          p_sym.value == Decimal("1.2")
          and p_sym.scale == "billions"
          and p_sym.currency == "USD")
    p_cr = parse_numeric_text("₹500 Cr")
    check("34c. 500 Cr -> 500 crores INR (5,000,000,000 absolute)",
          p_cr.value == Decimal(500)
          and p_cr.scale == "crores"
          and p_cr.currency == "INR"
          and p_cr.value * Decimal(10000000) == Decimal(5000000000))

    # 35. parentheses negatives
    check("35. parentheses negative (1,234) -> -1234",
          parse_numeric_text("(1,234)").value == Decimal(-1234))
    hardened = harden_fact_text("Revenue", {"value": "(1,234)"})
    check("35b. harden_fact_text carries negative through",
          hardened.get("value") == Decimal(-1234))

    # 36. millions/thousands normalization
    g = FactGraph()
    g.add(FactNode(
        node_id="Revenue", canonical_concept="Revenue",
        value=Decimal("125.4"), original_scale="millions",
        original_unit="USD millions", apply_scale=True, period="FY2024",
        currency="USD", source="A", source_tier="DOCUMENT",
        document_name="d.pdf", page="1", evidence="rev", status=VERIFIED,
    ))
    g.add(FactNode(
        node_id="Expenses", canonical_concept="Expenses",
        value=Decimal(100000000), period="FY2024", currency="USD",
        source="A", source_tier="DOCUMENT", document_name="d.pdf",
        page="2", evidence="exp", status=VERIFIED,
    ))
    sol36 = Solver(EXTENDED_REGISTRY).solve("Profit", g)
    check("36. 125.4 millions normalized to 125400000 before arithmetic",
          sol36.status == DERIVED and sol36.value == Decimal("25400000"))

    # 37. percentage normalization
    pct = parse_numeric_text("20%")
    check("37a. 20% parsed as percent kind",
          pct.value == Decimal(20) and pct.kind == "percent")
    pm = Solver(EXTENDED_REGISTRY).solve("Profit Margin", build_fact_graph({
        "Profit": doc_fact(200, page="1"),
        "Revenue": doc_fact(1000, page="2"),
    }))
    check("37b. Profit Margin = 20.00% (percent convention)",
          pm.status == DERIVED and pm.value == Decimal("20.00"))

    # 38. EPS / share-unit safety
    eps_ok = Solver(EXTENDED_REGISTRY).solve("EPS", build_fact_graph({
        "Net Profit": doc_fact(800, page="42"),
        "Shares Outstanding": doc_fact(100, page="50"),
    }))
    eps_bad = Solver(EXTENDED_REGISTRY).solve("EPS", build_fact_graph({
        "Net Profit": doc_fact(800, page="42"),
        "Shares Outstanding": doc_fact(100, unit="shares", page="50"),
    }))
    check("38. EPS with unclassified shares -> DERIVED; labelled -> BLOCKED",
          eps_ok.status == DERIVED and eps_ok.value == Decimal(8)
          and eps_bad.status == BLOCKED)


# ---------------------------------------------------------------------------
# 39-41. identity isolation / restatement handling
# ---------------------------------------------------------------------------


def test_39_41():
    # 39. consolidated vs standalone isolation
    g = FactGraph()
    g.add(make_node("Rev C", "Revenue", 1000, entity="Consolidated",
                    statement="Income Statement"))
    g.add(make_node("Rev S", "Revenue", 1100, entity="Standalone",
                    statement="Income Statement"))
    issues = detect_identity_ambiguity(g)
    check("39. consolidated vs standalone never merged (ENTITY_MISMATCH)",
          any(i.kind == "ENTITY_MISMATCH" for i in issues))

    # 40. annual vs quarterly isolation
    g2 = FactGraph()
    g2.add(make_node("Rev FY", "Revenue", 4000, period="FY2024",
                     period_type="annual"))
    g2.add(make_node("Rev Q4", "Revenue", 1000, period="FY2024",
                     period_type="quarterly"))
    issues2 = detect_identity_ambiguity(g2)
    check("40. annual vs quarterly never merged (PERIOD_TYPE_MISMATCH)",
          any(i.kind == "PERIOD_TYPE_MISMATCH" for i in issues2))

    # 41. restated filing handling
    v1 = make_node("NP v1", "Net Profit", 900, version="1",
                   filing_id="F2024-001")
    v2 = make_node("NP v2", "Net Profit", 950, version="2",
                   filing_id="F2024-002")
    resolved = resolve_analytical_fact([v1, v2])
    check("41a. restated filing -> current analytical fact picked",
          resolved.status == "VERIFIED"
          and resolved.current.node_id == "NP v2"
          and resolved.value == Decimal(950)
          and len(resolved.all_facts) == 2
          and any(v.kind == RESTATEMENT for v in resolved.verdicts))
    nover = make_node("NP nover", "Net Profit", 960)
    unresolved = resolve_analytical_fact([v1, nover])
    check("41b. indistinguishable restatement/conflict -> REVIEW_REQUIRED",
          unresolved.status == "REVIEW_REQUIRED"
          and unresolved.value is None)


# ---------------------------------------------------------------------------
# 42-43. lineage / payload
# ---------------------------------------------------------------------------


def test_42_43():
    facts = {
        "Net Profit": doc_fact(800, page="42", evidence="p&l line"),
        "Equity": doc_fact(2000, page="87", evidence="bs line"),
    }
    node = evaluate_metric("ROE", facts,
                           coordinate_map={"Net Profit": "'Financial Data'!E3",
                                           "Equity": "'Financial Data'!E9"})
    leaves = {l.concept: l for l in node.evidence.leaves}
    check("42. evidence lineage reconstructed to source leaves",
          len(node.evidence.leaves) == 2
          and leaves["Net Profit"].page == "42"
          and leaves["Net Profit"].evidence == "p&l line"
          and leaves["Equity"].document_name == "AR2024.pdf")
    payload = node.to_payload()
    required = {
        "target", "value", "status", "confidence_state", "formula",
        "dependencies", "lineage", "evidence", "blocking_reason",
        "source_tier", "explanation", "next_action",
    }
    check("43. agent payload carries section-K fields",
          required.issubset(set(payload.keys())))
    check("43b. explanation explains WHAT/WHY/action",
          node.explanation.get("what") is not None
          and node.explanation.get("why") is not None
          and "user_action_required" in node.explanation
          and node.next_action == "none"
          and node.confidence_state == "derived")


# ---------------------------------------------------------------------------
# 44-45. invariants
# ---------------------------------------------------------------------------


def test_44_45():
    # 44. no fabricated value invariant
    node = evaluate_metric("ROE", {"Net Profit": {"value": "garbage"}})
    check("44. no fabricated value on garbage input",
          node.value is None and node.status == BLOCKED)
    sol = Solver(EXTENDED_REGISTRY).solve("Profit", build_fact_graph({
        "Revenue": {"value": "abc", "unit": "USD"},
        "Expenses": doc_fact(50, page="2"),
    }))
    check("44b. unparseable fact never coerced to a number",
          sol.status == BLOCKED and sol.value is None)

    # 45. no silent substitution invariant
    g = FactGraph()
    g.add(FactNode(node_id="NP IS", canonical_concept="Net Profit",
                   value=Decimal(98300), period="FY2025",
                   source="Income Statement", source_tier="DOCUMENT",
                   document_name="AR2025.pdf", page="42",
                   evidence="p&l", status=VERIFIED))
    g.add(FactNode(node_id="NP CF", canonical_concept="Net Profit",
                   value=Decimal(97900), period="FY2025",
                   source="Cash Flow Statement", source_tier="DOCUMENT",
                   document_name="AR2025.pdf", page="61",
                   evidence="cf", status=VERIFIED))
    g.add(FactNode(node_id="Equity", canonical_concept="Equity",
                   value=Decimal(500000), period="FY2025",
                   source="Income Statement", source_tier="DOCUMENT",
                   document_name="AR2025.pdf", page="88",
                   evidence="equity", status=VERIFIED))
    n = evaluate_metric("Net Profit", g)
    check("45. conflicting sources -> EVIDENCE_CONFLICT (both preserved)",
          n.decision == EVIDENCE_CONFLICT
          and n.status == REVIEW_REQUIRED)
    n_roe = evaluate_metric("ROE", g)
    check("45b. downstream never computes a falsely authoritative ROE",
          n_roe.status == BLOCKED)


def main():
    test_01_05()
    test_06_08()
    test_09_11()
    test_12_15()
    test_16_17()
    test_18_23()
    test_24_26()
    test_27_28()
    test_29_32()
    test_33()
    test_34_38()
    test_39_41()
    test_42_43()
    test_44_45()
    passed = sum(1 for _, okc, _ in CHECKS if okc)
    total = len(CHECKS)
    print("=" * 60)
    print(f"RESULT: {passed}/{total} checks passed")
    failed = [n for n, okc, _ in CHECKS if not okc]
    if failed:
        print("FAILED CHECKS:")
        for n in failed:
            print(f"  - {n}")
    print("ALL CHECKS COMPLETE" if not failed else "FAILURES PRESENT")


if __name__ == "__main__":
    main()
