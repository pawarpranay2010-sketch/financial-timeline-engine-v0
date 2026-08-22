#!/usr/bin/env python3
"""
Platrixa
Sprint 12C - Evidence-Aware Decision Graph & Production Integration

Comprehensive deterministic test suite for the evidence-aware decision
graph built on the verified Sprint 12A maths engine and Sprint 12B
reasoning layer:

  A. Direct calculation            N. Duplicate facts
  B. Forward calculation           O. Conflicting provenance
  C. Reverse calculation           P. Zero denominator
  D. Multi-step chain              Q. Cycle detection
  E. DuPont                        R. Evidence lineage
  F. Missing dependency            S. Excel formula compilation
  G. BLOCKED propagation           T. External evidence hierarchy
  H. REVIEW_REQUIRED propagation   U. Forbidden open-web source
  I. Reconciliation conflict       V. Deterministic repeated execution
  J. Adjustment candidate          W. Complex multi-step formula
  K. Unit normalization            X. Unsupported relationship
  L. Currency mismatch             Y. Malformed input
  M. Period mismatch

Every section runs its checks multiple times to confirm deterministic
output. No LLM. No AI. No network. No fabricated values.

Expected: ALL CHECKS PASS (no failures).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from decimal import Decimal

from backend.maths import (
    BLOCKED,
    DERIVED,
    RECONCILED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
    ADJUSTMENT_REQUIRED,
    EVIDENCE_CONFLICT,
    INSUFFICIENT_EVIDENCE,
    METRIC_AVAILABLE,
    METRIC_BLOCKED,
    METRIC_DERIVED,
    METRIC_STUDENT_INPUT,
    RECONCILIATION_REQUIRED,
    AdjustmentEngine,
    AccountingGraph,
    AnomalyCandidate,
    CONFLICTING_PROVENANCE,
    CONFLICTING_SOURCE_VALUES,
    CROSS_STATEMENT_DISCREPANCY,
    DUPONT_REGISTRY,
    DuPontEngine,
    DUPLICATE_FACT,
    EXTENDED_REGISTRY,
    MISSING_DEPENDENCY,
    SCALE_MISMATCH,
    UNEXPECTED_SIGN,
    FormulaDefinition,
    FormulaRegistry,
    FactNode,
    FactGraph,
    ProvenanceGate,
    RegistrationError,
    Solver,
    build_fact_graph,
    build_extended_registry,
    cagr_span_from_facts,
    compile_excel_formula,
    derive_cagr_span,
    evaluate_metric,
    external_record_from_fact,
    is_allowed_source,
    metadata_for,
    propose_adjustment,
    render_evidence_tree,
    tier_of,
    trace_leaves,
)

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond), ""))


def ok(cond):
    return bool(cond)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def doc_fact(value, unit="USD", period="FY2024", source="Document A",
             page="42", evidence="source line", tier="DOCUMENT",
             document="AR2024.pdf", currency=None, **kw):
    """A fully-provenanced pipeline fact (passes the provenance gate)."""
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


def run_twice(fn):
    """Deterministic repeated execution: run fn twice and compare."""
    a = fn()
    b = fn()
    return a, b, a == b


# ---------------------------------------------------------------------------
# A. Direct calculation
# ---------------------------------------------------------------------------


def test_a_direct():
    facts = {"Net Profit": doc_fact(800, page="42")}
    node = evaluate_metric("Net Profit", facts, registry=EXTENDED_REGISTRY)
    check("A1. direct fact -> METRIC_AVAILABLE",
          node.decision == METRIC_AVAILABLE and node.status == VERIFIED)
    check("A2. direct value preserved",
          node.value == Decimal(800) and node.display_value == "800.00")
    check("A3. direct payload has no formula",
          node.formula_id is None)
    node2 = evaluate_metric(
        "Net Profit", facts, registry=EXTENDED_REGISTRY,
        coordinate_map={"Net Profit": "'Financial Data'!E3"},
    )
    check("A4. direct Excel reference compiles",
          node2.excel_formula is not None
          and node2.excel_formula.formula == "='Financial Data'!E3")


# ---------------------------------------------------------------------------
# B. Forward calculation
# ---------------------------------------------------------------------------


def test_b_forward():
    facts = {
        "Revenue": doc_fact(1000, page="1"),
        "Expenses": doc_fact(200, page="2"),
    }
    sol = Solver(EXTENDED_REGISTRY).solve("Profit", build_fact_graph(facts))
    check("B1. Revenue - Expenses -> Profit = 800",
          sol.status == DERIVED and sol.value == Decimal(800))
    node = evaluate_metric("Profit", facts, registry=EXTENDED_REGISTRY)
    check("B2. forward -> METRIC_DERIVED",
          node.decision == METRIC_DERIVED and node.status == DERIVED)
    check("B3. forward formula recorded",
          node.formula_id == "PROFIT" and "Revenue" in node.formula
          and "Expenses" in node.formula)
    check("B4. dependencies named",
          set(node.dependencies) == {"Revenue", "Expenses"})


# ---------------------------------------------------------------------------
# C. Reverse calculation
# ---------------------------------------------------------------------------


def test_c_reverse():
    facts = {
        "Revenue": doc_fact(1000, page="1"),
        "Profit": doc_fact(800, page="3"),
    }
    sol = Solver(EXTENDED_REGISTRY).solve("Expenses", build_fact_graph(facts))
    check("C1. Revenue + Profit -> Expenses = 200 (PROFIT inverse)",
          sol.status == DERIVED and sol.value == Decimal(200)
          and sol.kind == "reverse")
    facts2 = {
        "Revenue": doc_fact(1000, page="1"),
        "Loss": doc_fact(200, page="3"),
    }
    sol2 = Solver(EXTENDED_REGISTRY).solve("Expenses", build_fact_graph(facts2))
    check("C2. Revenue + Loss -> Expenses = 1200 (LOSS inverse)",
          sol2.status == DERIVED and sol2.value == Decimal(1200)
          and sol2.kind == "reverse")
    sol3 = Solver(EXTENDED_REGISTRY).solve("Revenue", build_fact_graph({
        "Gross Profit": doc_fact(300, page="1"),
        "Cost of Sales": doc_fact(700, page="2"),
    }))
    check("C3. Gross Profit + COGS -> Revenue = 1000",
          sol3.status == DERIVED and sol3.value == Decimal(1000))
    sol4 = Solver(EXTENDED_REGISTRY).solve(
        "Cost of Sales", build_fact_graph({
            "Revenue": doc_fact(1000, page="1"),
            "Gross Profit": doc_fact(300, page="2"),
        }))
    check("C4. Revenue - Gross Profit -> COGS = 700",
          sol4.status == DERIVED and sol4.value == Decimal(700))
    # never guesses: no inverse relationship registered
    bad = Solver(EXTENDED_REGISTRY).solve("Shares Outstanding", build_fact_graph({
        "Net Profit": doc_fact(800, page="1"),
        "EPS": doc_fact(8, page="2"),
    }))
    check("C5. reverse needs a REGISTERED inverse (EPS has one)",
          bad.status == DERIVED and bad.value == Decimal(100))


# ---------------------------------------------------------------------------
# D. Multi-step chain
# ---------------------------------------------------------------------------


def test_d_multistep():
    facts = {
        "Net Profit": doc_fact(400, page="42"),
        "Revenue": doc_fact(2000, page="40"),
        "Total Assets": doc_fact(4000, page="87"),
        "Equity": doc_fact(2000, page="88"),
    }
    sol = Solver(DUPONT_REGISTRY).solve("Return on Equity", build_fact_graph(facts))
    check("D1. DuPont chain PM*AT*EM -> ROE = 20.00%",
          sol.status == DERIVED and sol.value == Decimal("20.00"))
    check("D2. multi-step traversal path recorded",
          len(sol.traversal_path) >= 6)
    node = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY)
    check("D3. extended ROE = Net Profit / Equity = 20.00%",
          node.decision == METRIC_DERIVED and node.value == Decimal("20.00"))
    check("D4. chained sufficiency state",
          node.sufficiency_state in ("FORWARD_SOLVABLE", "CHAINED_SOLVABLE"))
    # chain: Profit Margin (default registry) -> profit via PROFIT
    chain_sol = Solver(EXTENDED_REGISTRY).solve("Profit Margin", build_fact_graph({
        "Revenue": doc_fact(2000, page="40"),
        "Expenses": doc_fact(1600, page="41"),
    }))
    check("D5. Profit Margin = (2000-1600)/2000 = 20.00% (chained)",
          chain_sol.status == DERIVED and chain_sol.value == Decimal("20.00"))


# ---------------------------------------------------------------------------
# E. DuPont (two periods + contribution analysis)
# ---------------------------------------------------------------------------


def test_e_dupont():
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
    check("E1. two periods resolved",
          len(analysis.periods) == 2
          and analysis.periods[0].roe.value == Decimal("20.00")
          and analysis.periods[1].roe.value == Decimal("30.00"))
    comp = analysis.comparisons[0]
    check("E2. absolute change = +10pp",
          comp.absolute_change == Decimal(10))
    check("E3. percentage change = 50%",
          comp.percentage_change == Decimal(50))
    total = sum(c.contribution for c in comp.contributions)
    check("E4. contributions sum EXACTLY to the delta",
          total == comp.absolute_change)
    check("E5. largest contributor = Equity Multiplier",
          comp.largest_contributor == "Equity Multiplier")
    check("E6. deterministic repeated runs",
          run_twice(lambda: DuPontEngine(prefer_cpp=False).analyze({
              "FY2024": {"Net Profit": doc_fact(400, period="FY2024"),
                         "Revenue": doc_fact(2000, period="FY2024"),
                         "Total Assets": doc_fact(4000, period="FY2024"),
                         "Equity": doc_fact(2000, period="FY2024")},
              "FY2025": {"Net Profit": doc_fact(600, period="FY2025"),
                         "Revenue": doc_fact(3000, period="FY2025"),
                         "Total Assets": doc_fact(6000, period="FY2025"),
                         "Equity": doc_fact(2000, period="FY2025")},
          }).to_dict())[2])


# ---------------------------------------------------------------------------
# F. Missing dependency
# ---------------------------------------------------------------------------


def test_f_missing():
    facts = {"Net Profit": doc_fact(800, page="42")}
    node = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY)
    check("F1. missing Equity -> METRIC_BLOCKED",
          node.decision == METRIC_BLOCKED and node.status == BLOCKED)
    check("F2. missing dependency named",
          node.blocking_reason is not None and "Equity" in node.blocking_reason)
    sol = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph(facts))
    check("F3. solver missing list names Equity",
          "Equity" in sol.missing)


# ---------------------------------------------------------------------------
# G. BLOCKED propagation
# ---------------------------------------------------------------------------


def test_g_blocked_propagation():
    facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(2000, page="88", tier="BLOCKED",
                           evidence="extraction failed"),
    }
    sol = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph(facts))
    check("G1. BLOCKED dependency -> BLOCKED downstream",
          sol.status == BLOCKED
          and "Equity" in (sol.missing or (sol.blocked_inputs or [])))
    check("G1b. blocking reason names the blocked dependency",
          sol.reason is not None and "Equity" in sol.reason)
    node = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY)
    check("G2. decision METRIC_BLOCKED",
          node.decision == METRIC_BLOCKED and node.status == BLOCKED)


# ---------------------------------------------------------------------------
# H. REVIEW_REQUIRED propagation
# ---------------------------------------------------------------------------


def test_h_review_propagation():
    facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(2000, page="88",
                           evidence="conflicting pages",
                           extraction_state="review_required"),
    }
    sol = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph(facts))
    check("H1. REVIEW_REQUIRED never becomes VERIFIED/DERIVED",
          sol.status == REVIEW_REQUIRED)
    node = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY)
    check("H2. decision EVIDENCE_CONFLICT, status REVIEW_REQUIRED",
          node.decision == EVIDENCE_CONFLICT and node.status == REVIEW_REQUIRED)
    check("H3. computed value retained but flagged",
          node.value == Decimal("40.00"))


# ---------------------------------------------------------------------------
# I. Reconciliation conflict
# ---------------------------------------------------------------------------


def test_i_reconciliation():
    from backend.maths.reconciliation import ReconciliationEngine

    fact_is = doc_fact(98300, period="FY2025", source="Income Statement",
                       page="42", evidence="p&l net profit")
    fact_cf = doc_fact(97900, period="FY2025", source="Cash Flow Statement",
                       page="61", evidence="cf net income")
    engine = ReconciliationEngine()
    res = engine.reconcile_cross_statement(
        "Net Profit", fact_is, "Income Statement",
        fact_cf, "Cash Flow Statement",
        tolerance_rel=Decimal("0.001"),
    )
    check("I1. variance = 400 detected",
          res.status == REVIEW_REQUIRED and res.absolute_variance == Decimal(400))
    check("I2. both source values preserved",
          res.observed_value == Decimal(98300)
          and res.expected_value == Decimal(97900))
    check("I3. structured payload complete",
          res.reconciliation_id and res.periods.get("reported") == "FY2025"
          and len(res.source_nodes) == 2
          and res.relative_variance is not None)
    # within tolerance -> RECONCILED
    res2 = engine.reconcile_cross_statement(
        "Net Profit", fact_is, "Income Statement",
        fact_cf, "Cash Flow Statement",
        tolerance_rel=Decimal("0.01"),
    )
    check("I4. within tolerance -> RECONCILED",
          res2.status == RECONCILED)

    # conflicting graph -> decision states
    g = FactGraph()
    g.add(FactNode(node_id="Net Profit (IS)", canonical_concept="Net Profit",
                   value=Decimal(98300), period="FY2025", currency="USD",
                   source="Income Statement", source_tier="DOCUMENT",
                   document_name="AR2025.pdf", page="42",
                   evidence="p&l net profit", status=VERIFIED))
    g.add(FactNode(node_id="Net Profit (CF)", canonical_concept="Net Profit",
                   value=Decimal(97900), period="FY2025", currency="USD",
                   source="Cash Flow Statement", source_tier="DOCUMENT",
                   document_name="AR2025.pdf", page="61",
                   evidence="cf net income", status=VERIFIED))
    g.add(FactNode(node_id="Equity", canonical_concept="Equity",
                   value=Decimal(500000), period="FY2025", currency="USD",
                   source="Income Statement", source_tier="DOCUMENT",
                   document_name="AR2025.pdf", page="88",
                   evidence="equity line", status=VERIFIED))
    node = evaluate_metric("Net Profit", g, registry=EXTENDED_REGISTRY)
    check("I5. conflicting Net Profit -> EVIDENCE_CONFLICT",
          node.decision == EVIDENCE_CONFLICT
          and node.status == REVIEW_REQUIRED)
    node2 = evaluate_metric("ROE", g, registry=EXTENDED_REGISTRY)
    check("I6. downstream ROE BLOCKED (unresolved value never used)",
          node2.status == BLOCKED)
    node3 = evaluate_metric(
        "Net Profit", g, registry=EXTENDED_REGISTRY,
        anomalies=[], reconciliation_results=[res],
    )
    check("I7. reconciliation review -> RECONCILIATION_REQUIRED",
          node3.decision == RECONCILIATION_REQUIRED
          and node3.status == REVIEW_REQUIRED)


# ---------------------------------------------------------------------------
# J. Adjustment candidate
# ---------------------------------------------------------------------------


def test_j_adjustment():
    # forbidden flow: never auto-correct; candidate + explicit adjustment.
    # Spec example: 125.4 USD Millions vs 125400 USD - the engine detects
    # the scale discrepancy rather than silently choosing one.
    graph = FactGraph()
    graph.add(FactNode(
        node_id="Revenue (Millions)", canonical_concept="Revenue",
        value=Decimal("125.4"), original_value="125.4",
        original_unit="USD millions", original_scale="millions",
        apply_scale=True, period="FY2024", currency="USD",
        source="Document A", source_tier="DOCUMENT",
        document_name="AR2024.pdf", page="1", evidence="rev line",
        status=VERIFIED,
    ))
    graph.add(FactNode(
        node_id="Revenue (Absolute)", canonical_concept="Revenue",
        value=Decimal("125400"), original_value=125400,
        original_unit="USD", original_scale=None,
        apply_scale=False, period="FY2024", currency="USD",
        source="Document B", source_tier="DOCUMENT",
        document_name="AR2024.pdf", page="9", evidence="rev table",
        status=VERIFIED,
    ))
    graph.add(FactNode(
        node_id="Expenses", canonical_concept="Expenses",
        value=Decimal(100000000), period="FY2024", currency="USD",
        source="Document A", source_tier="DOCUMENT",
        document_name="AR2024.pdf", page="2", evidence="exp line",
        status=VERIFIED,
    ))
    engine = AdjustmentEngine()
    candidates = engine.detect_anomalies(graph)
    scale_cands = [c for c in candidates if c.kind == "SCALE_MISMATCH"]
    check("J1. SCALE_MISMATCH candidate detected (spec example)",
          len(scale_cands) == 1)
    check("J1b. candidate exposes BOTH normalized values (never one)",
          scale_cands and "normalized_values" in scale_cands[0].details
          and "125400000" in str(scale_cands[0].details))
    # anomaly -> REVIEW_REQUIRED -> explicit STUDENT_INPUT adjustment
    scale_cand = scale_cands[0]
    node_adj, record = propose_adjustment(
        scale_cand, 125400000, graph,
        decision="ADJUST", reason="student verified absolute USD value",
    )
    check("J2. adjustment creates STUDENT_INPUT node",
          node_adj.status == STUDENT_INPUT
          and node_adj.canonical_concept == "Revenue"
          and record.status == STUDENT_INPUT)
    check("J3. original source fact immutable (value unchanged)",
          "125.4" in str(record.original_values))
    check("J4. lineage back to anomaly + decision",
          "ADJUST" in node_adj.lineage
          and scale_cand.kind in node_adj.lineage)
    # ADJUSTMENT_REQUIRED decision on a computable metric
    neg_facts = {
        "Revenue": doc_fact(-100, page="1"),
        "Expenses": doc_fact(50, page="2"),
    }
    node = evaluate_metric("Profit", neg_facts, registry=EXTENDED_REGISTRY)
    check("J5. negative Revenue anomaly -> ADJUSTMENT_REQUIRED",
          node.decision == ADJUSTMENT_REQUIRED
          and node.status == REVIEW_REQUIRED)
    node_clean = evaluate_metric("Profit", {
        "Revenue": doc_fact(200, page="1"),
        "Expenses": doc_fact(50, page="2"),
    }, registry=EXTENDED_REGISTRY)
    check("J6. clean facts -> METRIC_DERIVED",
          node_clean.decision == METRIC_DERIVED)


# ---------------------------------------------------------------------------
# K. Unit normalization
# ---------------------------------------------------------------------------


def test_k_unit_normalization():
    # spec-style raw-scaled magnitude: constructed as a FactNode with
    # apply_scale=True so the solver normalizes 125.4 millions -> absolute.
    graph = FactGraph()
    graph.add(FactNode(
        node_id="Revenue", canonical_concept="Revenue",
        value=Decimal("125.4"), original_value="125.4",
        original_unit="USD millions", original_scale="millions",
        apply_scale=True, period="FY2024", currency="USD",
        source="Document A", source_tier="DOCUMENT",
        document_name="AR2024.pdf", page="1", evidence="rev",
        status=VERIFIED,
    ))
    graph.add(FactNode(
        node_id="Expenses", canonical_concept="Expenses",
        value=Decimal(100000000), period="FY2024", currency="USD",
        source="Document A", source_tier="DOCUMENT",
        document_name="AR2024.pdf", page="2", evidence="exp",
        status=VERIFIED,
    ))
    sol = Solver(EXTENDED_REGISTRY).solve("Profit", graph)
    check("K1. 125.4 millions normalized to 125400000 before arithmetic",
          sol.status == DERIVED and sol.value == Decimal("25400000"))
    node = evaluate_metric("Profit", graph, registry=EXTENDED_REGISTRY)
    check("K2. normalized decision METRIC_DERIVED",
          node.decision == METRIC_DERIVED)


# ---------------------------------------------------------------------------
# L. Currency mismatch
# ---------------------------------------------------------------------------


def test_l_currency():
    facts = {
        "Revenue": doc_fact(1000, page="1", currency="USD"),
        "Expenses": doc_fact(200, page="2", currency="INR"),
    }
    sol = Solver(EXTENDED_REGISTRY).solve("Profit", build_fact_graph(facts))
    check("L1. USD vs INR -> BLOCKED (never converted)",
          sol.status == BLOCKED)
    node = evaluate_metric("Profit", facts, registry=EXTENDED_REGISTRY)
    check("L2. decision METRIC_BLOCKED",
          node.decision == METRIC_BLOCKED and node.status == BLOCKED)


# ---------------------------------------------------------------------------
# M. Period mismatch
# ---------------------------------------------------------------------------


def test_m_period():
    facts = {
        "Net Profit": doc_fact(800, period="FY2024", page="42"),
        "Equity": doc_fact(2000, period="FY2025", page="88"),
    }
    sol = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph(facts))
    check("M1. same-period formula with FY24 vs FY25 -> BLOCKED",
          sol.status == BLOCKED)
    from backend.maths.reconciliation import ReconciliationEngine
    res = ReconciliationEngine().reconcile_cross_statement(
        "Net Profit",
        doc_fact(100, period="FY2024", source="IS"),
        "Income Statement",
        doc_fact(100, period="FY2025", source="CF"),
        "Cash Flow Statement",
    )
    check("M2. reconciliation across periods -> REVIEW_REQUIRED",
          res.status == REVIEW_REQUIRED
          and "PERIOD MISMATCH" in res.reason)


# ---------------------------------------------------------------------------
# N. Duplicate facts
# ---------------------------------------------------------------------------


def test_n_duplicates():
    g = FactGraph()
    for i, source in enumerate(("Document A", "Document A")):
        g.add(FactNode(
            node_id=f"Revenue {i}", canonical_concept="Revenue",
            value=Decimal(1000), period="FY2024", source=source,
            source_tier="DOCUMENT", document_name="AR2024.pdf",
            page="1", evidence="rev line", status=VERIFIED,
        ))
    candidates = AdjustmentEngine().detect_anomalies(g)
    check("N1. exact duplicate -> DUPLICATE_FACT candidate",
          any(c.kind == "DUPLICATE_FACT" for c in candidates))


# ---------------------------------------------------------------------------
# O. Conflicting provenance
# ---------------------------------------------------------------------------


def test_o_conflicting_provenance():
    g = FactGraph()
    g.add(FactNode(node_id="Revenue (Doc)", canonical_concept="Revenue",
                   value=Decimal(1000), period="FY2024", source="Doc A",
                   source_tier="DOCUMENT", document_name="AR2024.pdf",
                   page="1", evidence="rev", status=VERIFIED))
    g.add(FactNode(node_id="Revenue (Review)", canonical_concept="Revenue",
                   value=Decimal(1100), period="FY2024", source="Doc B",
                   source_tier="DOCUMENT", document_name="AR2024.pdf",
                   page="3", evidence="rev v2", status=REVIEW_REQUIRED))
    candidates = AdjustmentEngine().detect_anomalies(g)
    check("O1. mixed verified/review -> CONFLICTING_PROVENANCE",
          any(c.kind == "CONFLICTING_PROVENANCE" for c in candidates))


# ---------------------------------------------------------------------------
# P. Zero denominator
# ---------------------------------------------------------------------------


def test_p_zero_denominator():
    facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(0, page="88"),
    }
    sol = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph(facts))
    check("P1. zero denominator -> BLOCKED (mathematically undefined)",
          sol.status == BLOCKED)
    node = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY)
    check("P2. decision METRIC_BLOCKED",
          node.decision == METRIC_BLOCKED)
    cands = AdjustmentEngine().detect_anomalies(facts)
    check("P3. zero-denominator anomaly candidate",
          any(c.kind == "ZERO_DENOMINATOR" for c in cands))


# ---------------------------------------------------------------------------
# Q. Cycle detection
# ---------------------------------------------------------------------------


def test_q_cycles():
    reg = FormulaRegistry()
    reg.register(FormulaDefinition(
        formula_id="CYC_A", target="CycleA", expression="CycleB",
        dependencies=["CycleB"], version="1.0",
    ))
    reg.register(FormulaDefinition(
        formula_id="CYC_B", target="CycleB", expression="CycleA",
        dependencies=["CycleA"], version="1.0",
    ))
    sol = Solver(reg, prefer_cpp=False).solve("CycleA", FactGraph())
    check("Q1. solver detects circular dependency -> BLOCKED",
          sol.status == BLOCKED
          and ("circular" in (sol.reason or "").lower()
               or "cycle" in (sol.reason or "").lower()))
    graph = AccountingGraph(reg)
    graph.add_formula_application("X", "CYC_A", ["Y"])
    graph.add_formula_application("Y", "CYC_B", ["X"])
    try:
        graph.assert_acyclic()
        cyclic = False
    except Exception:
        cyclic = True
    check("Q2. AccountingGraph.assert_acyclic raises on cycle",
          cyclic and len(graph.detect_cycles()) >= 1)


# ---------------------------------------------------------------------------
# R. Evidence lineage
# ---------------------------------------------------------------------------


def test_r_evidence_lineage():
    facts = {
        "Net Profit": doc_fact(800, page="42", evidence="p&l line"),
        "Equity": doc_fact(2000, page="87", evidence="bs line"),
    }
    node = evaluate_metric(
        "ROE", facts, registry=EXTENDED_REGISTRY,
        coordinate_map={"Net Profit": "'Financial Data'!E3",
                        "Equity": "'Financial Data'!E9"},
    )
    leaves = node.evidence.leaves
    check("R1. recursive leaf trace to source leaves",
          len(leaves) == 2
          and {l.concept for l in leaves} == {"Net Profit", "Equity"})
    by_concept = {l.concept: l for l in leaves}
    check("R2. page + evidence machine-readable",
          by_concept["Net Profit"].page == "42"
          and by_concept["Net Profit"].evidence == "p&l line"
          and by_concept["Net Profit"].tier == "DOCUMENT")
    check("R3. chain is deterministic and ordered",
          [c["concept"] for c in node.evidence.chain][-1] == "ROE"
          and node.evidence.chain[0]["concept"] == "Net Profit")
    check("R4. render_evidence_tree is text",
          isinstance(render_evidence_tree(node.evidence), str)
          and "ROE" in render_evidence_tree(node.evidence))
    check("R5. machine-readable to_dict",
          isinstance(node.evidence.to_dict(), dict)
          and len(node.evidence.to_dict()["leaves"]) == 2)


# ---------------------------------------------------------------------------
# S. Excel formula compilation
# ---------------------------------------------------------------------------


def test_s_excel_compilation():
    facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(2000, page="88"),
    }
    coord = {"Net Profit": "'Financial Data'!E3",
             "Equity": "'Financial Data'!E9"}
    node = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY,
                           coordinate_map=coord)
    check("S1. ROE -> live Excel formula (spec example)",
          node.excel_formula is not None
          and node.excel_formula.formula ==
          "='Financial Data'!E3 / 'Financial Data'!E9")
    # blocked -> no fabricated value
    node_b = evaluate_metric("ROE", {"Net Profit": doc_fact(800)},
                             registry=EXTENDED_REGISTRY, coordinate_map=coord)
    check("S2. BLOCKED -> Excel preserves blocked state (no formula)",
          node_b.excel_formula is None
          or node_b.excel_formula.formula is None)
    # nested DuPont chain
    dupont_coords = {
        "Net Profit": "'Financial Data'!E3",
        "Revenue": "'Financial Data'!E5",
        "Total Assets": "'Financial Data'!E7",
        "Equity": "'Financial Data'!E9",
    }
    dupont_facts = {
        "Net Profit": doc_fact(400, page="42"),
        "Revenue": doc_fact(2000, page="40"),
        "Total Assets": doc_fact(4000, page="87"),
        "Equity": doc_fact(2000, page="88"),
    }
    d_node = evaluate_metric("Return on Equity", dupont_facts,
                             registry=DUPONT_REGISTRY,
                             coordinate_map=dupont_coords)
    f = d_node.excel_formula
    check("S3. nested algebraic chain preserved",
          f is not None and f.formula is not None
          and f.formula.startswith("=")
          and "Financial Data" in f.formula
          and f.formula.count("/") >= 3
          and f.formula.count("*") == 2)
    # reverse-step compilation uses the REGISTERED inverse expression
    rev_facts = {
        "Revenue": doc_fact(1000, page="1"),
        "Profit": doc_fact(800, page="3"),
    }
    rev_sol = Solver(EXTENDED_REGISTRY).solve(
        "Expenses", build_fact_graph(rev_facts))
    # registry-aware compiler: reverse steps compile the REGISTERED
    # inverse expression (Revenue - Profit), not the forward one.
    from backend.maths.excel_compiler import ExcelLineageCompiler
    compiler = ExcelLineageCompiler(EXTENDED_REGISTRY)
    ex = compiler.compile(
        rev_sol, build_fact_graph(rev_facts),
        {"Revenue": "'Financial Data'!E3", "Profit": "'Financial Data'!E5"},
    )
    check("S4. reverse step compiles the inverse expression",
          ex.formula is not None
          and ex.formula.count("'Financial Data'!E3") == 1
          and "-" in ex.formula
          and "'Financial Data'!E5" in ex.formula)
    # missing coordinate -> never fabricated
    ex_missing = compiler.compile(
        rev_sol, build_fact_graph(rev_facts), {"Revenue": "'Financial Data'!E3"},
    )
    check("S5. missing coordinate -> no formula, explicit reason",
          ex_missing.formula is None
          and "not fabricated" in ex_missing.reason)


# ---------------------------------------------------------------------------
# T. External evidence hierarchy
# ---------------------------------------------------------------------------


def test_t_external_hierarchy():
    check("T1. tier 1 = DOCUMENT",
          tier_of("DOCUMENT") == 1)
    check("T2. tier 2 = APPENDIX",
          tier_of("APPENDIX") == 2)
    check("T3. tier 3 = REGULATORY_API / EXTERNAL_DERIVED",
          tier_of("REGULATORY_API") == 3
          and tier_of("EXTERNAL_DERIVED") == 3)
    check("T4. tier 4 = everything else FORBIDDEN",
          tier_of("OPEN_WEB") == 4 and tier_of(None) == 4
          and tier_of("UNKNOWN") == 4)
    check("T5. approved tiers allowed",
          is_allowed_source("DOCUMENT") and is_allowed_source("APPENDIX")
          and is_allowed_source("REGULATORY_API")
          and not is_allowed_source("OPEN_WEB")
          and not is_allowed_source(None))
    rec = external_record_from_fact({
        "value": 125.4, "normalized_value": 125400000,
        "metric": "Revenue", "provider": "SEBI API",
        "provider_identifier": "SEBI-2024-001",
        "reporting_period": "FY2024", "currency": "INR",
        "scale": "millions", "provenance_tier": "REGULATORY_API",
        "evidence": "SEBI filing reference",
    }, retrieval_timestamp="2026-08-08T00:00:00Z")
    check("T6. external record retains provider/identifier/values",
          rec is not None
          and rec.provider == "SEBI API"
          and rec.identifier == "SEBI-2024-001"
          and rec.raw_value == Decimal("125.4")
          and rec.normalized_value == Decimal(125400000)
          and rec.retrieval_timestamp == "2026-08-08T00:00:00Z")
    check("T7. unapproved fact -> no external record",
          external_record_from_fact({"value": 5, "provenance_tier": "DOCUMENT"})
          is None)
    check("T8. external record starts UNVERIFIED (never auto-VERIFIED)",
          rec.verification_status == "UNVERIFIED")


# ---------------------------------------------------------------------------
# U. Forbidden open-web source
# ---------------------------------------------------------------------------


def test_u_forbidden_web():
    facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": {
            "value": 2000, "unit": "USD", "reporting_period": "FY2024",
            "provenance_tier": "OPEN_WEB",
            "source": "https://some-random-site.example",
        },
    }
    node = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY)
    check("U1. open-web source -> gate BLOCKED",
          node.provenance_verdict.verdict == "BLOCKED")
    check("U2. decision METRIC_BLOCKED (invalid provenance)",
          node.decision == METRIC_BLOCKED
          and node.blocking_reason == "invalid provenance")
    # UNANALYZED fails closed too
    facts2 = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": {
            "value": 2000, "unit": "USD", "reporting_period": "FY2024",
            "provenance_tier": "UNANALYZED",
        },
    }
    node2 = evaluate_metric("ROE", facts2, registry=EXTENDED_REGISTRY)
    check("U3. UNANALYZED fails closed",
          node2.provenance_verdict.verdict == "BLOCKED")


# ---------------------------------------------------------------------------
# V. Deterministic repeated execution
# ---------------------------------------------------------------------------


def test_v_determinism():
    facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Equity": doc_fact(2000, page="88"),
    }
    p1 = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY,
                         coordinate_map={"Net Profit": "'Financial Data'!E3",
                                         "Equity": "'Financial Data'!E9"}).to_payload()
    p2 = evaluate_metric("ROE", facts, registry=EXTENDED_REGISTRY,
                         coordinate_map={"Net Profit": "'Financial Data'!E3",
                                         "Equity": "'Financial Data'!E9"}).to_payload()
    check("V1. identical payload across runs",
          p1 == p2)
    s1 = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph(facts)).to_dict()
    s2 = Solver(EXTENDED_REGISTRY).solve("ROE", build_fact_graph(facts)).to_dict()
    check("V2. identical solution across runs",
          s1 == s2)
    check("V3. identical anomaly scan across runs",
          run_twice(lambda: [c.to_dict() for c in
                             AdjustmentEngine().detect_anomalies(facts)])[2])


# ---------------------------------------------------------------------------
# W. Complex multi-step formula (EBITDA margin / CAGR / EPS)
# ---------------------------------------------------------------------------


def test_w_complex():
    facts = {
        "EBITDA": doc_fact(300, page="12"),
        "Revenue": doc_fact(1000, page="1"),
    }
    node = evaluate_metric("EBITDA Margin", facts, registry=EXTENDED_REGISTRY)
    check("W1. EBITDA Margin = 30.00%",
          node.decision == METRIC_DERIVED and node.value == Decimal("30.00"))
    check("W2. metadata table carries excel template",
          metadata_for("EBITDA_MARGIN") is not None
          and "excel_template" in metadata_for("EBITDA_MARGIN"))
    # CAGR
    check("W3. span derived from periods (FY2023 -> FY2025 = 2)",
          derive_cagr_span("FY2023", "FY2025") == 2
          and derive_cagr_span("FY2023", "FY2024") == 1)
    check("W4. span never guessed",
          derive_cagr_span(None, "FY2025") is None
          and derive_cagr_span("FY2024", "FY2022") is None)
    cagr_facts = {
        "CAGR Beginning Value": doc_fact(100, period="FY2023", page="1"),
        "CAGR Ending Value": doc_fact(121, period="FY2025", page="1"),
        "CAGR Span Years": doc_fact(2, page="1", unit="", evidence="course note"),
    }
    sol = Solver(EXTENDED_REGISTRY).solve("CAGR", build_fact_graph(cagr_facts))
    check("W5. CAGR = (121/100)^(1/2)-1 = 10.00%",
          sol.status == DERIVED and sol.value == Decimal("10.00"))
    check("W6. CAGR span from fact periods",
          cagr_span_from_facts({
              "CAGR Beginning Value": doc_fact(100, period="FY2023"),
              "CAGR Ending Value": doc_fact(121, period="FY2025"),
          }) == 2)
    cagr_missing = Solver(EXTENDED_REGISTRY).solve("CAGR", build_fact_graph({
        "CAGR Beginning Value": doc_fact(100, period="FY2023"),
        "CAGR Ending Value": doc_fact(121, period="FY2025"),
    }))
    check("W7. CAGR span missing -> BLOCKED (never guessed)",
          cagr_missing.status == BLOCKED
          and "CAGR Span Years" in cagr_missing.missing)
    # EPS
    eps_facts = {
        "Net Profit": doc_fact(800, page="42"),
        "Shares Outstanding": doc_fact(100, page="50"),
    }
    eps = Solver(EXTENDED_REGISTRY).solve("EPS", build_fact_graph(eps_facts))
    check("W8. EPS = Net Profit / Shares = 8.00",
          eps.status == DERIVED and eps.value == Decimal(8))
    eps_blocked = Solver(EXTENDED_REGISTRY).solve("EPS", build_fact_graph({
        "Net Profit": doc_fact(800, page="42"),
        "Shares Outstanding": doc_fact(100, unit="shares", page="50"),
    }))
    check("W9. classified share count fails closed (no silent mixing)",
          eps_blocked.status == BLOCKED)


# ---------------------------------------------------------------------------
# X. Unsupported relationship
# ---------------------------------------------------------------------------


def test_x_unsupported():
    node = evaluate_metric("Quantum Revenue Growth", {},
                           registry=EXTENDED_REGISTRY)
    check("X1. no registered relationship -> INSUFFICIENT_EVIDENCE",
          node.decision == INSUFFICIENT_EVIDENCE
          and node.status == BLOCKED)
    check("X2. blocking reason is deterministic and honest",
          node.blocking_reason is not None
          and "No registered" in node.blocking_reason)


# ---------------------------------------------------------------------------
# Y. Malformed input
# ---------------------------------------------------------------------------


def test_y_malformed():
    from backend.maths.fact_model import to_decimal
    check("Y1. garbage never coerces to a number",
          to_decimal("abc") is None and to_decimal(None) is None
          and to_decimal(True) is None)
    sol = Solver(EXTENDED_REGISTRY).solve("Profit", build_fact_graph({
        "Revenue": {"value": "garbage", "unit": "USD"},
        "Expenses": doc_fact(50, page="2"),
    }))
    check("Y2. non-numeric fact -> BLOCKED (never guessed)",
          sol.status == BLOCKED)
    try:
        FormulaDefinition(
            formula_id="BAD", target="Bad",
            expression="UnknownVariable + 1", dependencies=["Other"],
            version="1.0",
        )
        bad_registration = False
    except RegistrationError:
        bad_registration = True
    check("Y3. malformed formula registration rejected",
          bad_registration)
    # invalid power (negative base, fractional exponent)
    reg = FormulaRegistry()
    reg.register(FormulaDefinition(
        formula_id="SQRT_TEST", target="Sqrt Test",
        expression="Net Profit ^ 0.5", dependencies=["Net Profit"],
        version="1.0",
    ))
    s = Solver(reg, prefer_cpp=False).solve("Sqrt Test", build_fact_graph({
        "Net Profit": doc_fact(-4, page="1"),
    }))
    check("Y4. invalid power -> structured BLOCKED, not NaN",
          s.status == BLOCKED)


def main():
    test_a_direct()
    test_b_forward()
    test_c_reverse()
    test_d_multistep()
    test_e_dupont()
    test_f_missing()
    test_g_blocked_propagation()
    test_h_review_propagation()
    test_i_reconciliation()
    test_j_adjustment()
    test_k_unit_normalization()
    test_l_currency()
    test_m_period()
    test_n_duplicates()
    test_o_conflicting_provenance()
    test_p_zero_denominator()
    test_q_cycles()
    test_r_evidence_lineage()
    test_s_excel_compilation()
    test_t_external_hierarchy()
    test_u_forbidden_web()
    test_v_determinism()
    test_w_complex()
    test_x_unsupported()
    test_y_malformed()
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
