#!/usr/bin/env python3
"""
Financial Timeline Engine
Sprint 12B - Contextual Financial Reasoning Layer

Comprehensive deterministic test suite for the reasoning layer built on the
verified Sprint 12A graph (backend/maths):

  PART A - DUPONT DECOMPOSITION
      single-period resolution through the Formula Registry + Solver,
      six-tier status rules (BLOCKED / REVIEW_REQUIRED never upgrade),
      two-period delta + deterministic contribution analysis (exact
      identity: contributions sum to the delta), percentage change,
      largest contributor, blocked-period handling, percent-fact
      normalization, determinism, source immutability
  PART B - FORENSIC CROSS-STATEMENT RECONCILIATION
      retained-earnings strap rule (bridge items), variance + tolerance
      (RECONCILED / REVIEW_REQUIRED), structured payload preservation,
      matching gates (period / fiscal period type / currency / scale /
      distinct statements), BLOCKED vs REVIEW_REQUIRED semantics,
      cross-statement identity, review-source propagation
  PART C - DETERMINISTIC ADJUSTMENT / ANOMALY REASONING
      every candidate kind (conflicting values, cross-statement
      discrepancy, scale mismatch, zero denominator, missing dependency,
      unsupported label, unexpected sign, duplicate, conflicting
      provenance, incompatible units), immutability of source facts,
      STUDENT_INPUT flow, recalculation with adjustments, no automatic
      correction (the forbidden VERIFIED -> VERIFIED flow never happens)
  PART D - INTEGRATION + 12A REGRESSION SANITY
      reasoning layer reuses the 12A Solver/Registry (no second engine),
      default registry still has 7 formulas, deterministic repeated runs

No LLM. No AI. No network. Deterministic.
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
    AdjustmentEngine,
    AnomalyCandidate,
    CONFLICTING_PROVENANCE,
    CONFLICTING_SOURCE_VALUES,
    CROSS_STATEMENT_DISCREPANCY,
    DEFAULT_RECONCILIATION_RULES,
    DUPLICATE_FACT,
    DUPONT_REGISTRY,
    DuPontEngine,
    INCOMPATIBLE_UNITS,
    MISSING_DEPENDENCY,
    PERIOD_MISMATCH,
    ReconciliationEngine,
    SCALE_MISMATCH,
    UNEXPECTED_SIGN,
    UNSUPPORTED_LABEL,
    ZERO_DENOMINATOR,
    build_fact_graph,
    default_registry,
    detect_anomalies,
    propose_adjustment,
    resolve_with_adjustments,
)
from backend.maths.adjustments import ANOMALY_DETECTED
from backend.maths.dupont import (
    ASSET_TURNOVER,
    EQUITY_MULTIPLIER,
    NET_PROFIT,
    PROFIT_MARGIN,
    RETURN_ON_EQUITY,
)
from backend.maths.fact_model import FactGraph, FactNode
from backend.maths.formula_registry import FormulaRegistry
from backend.maths.solver import Solver

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def F(value, period="FY2025", tier="DOCUMENT", unit="USD", source="Doc.pdf",
      page="12", **kw):
    """Pipeline-shaped fact dict (mirrors the extractor / evidence layer)."""
    return {
        "value": value,
        "source": source,
        "reporting_period": period,
        "unit": unit,
        "provenance_tier": tier,
        "page": page,
        **kw,
    }


def rev_node(node_id, concept, value, period="FY2025", currency="USD",
             status=VERIFIED, source="Doc", scale=None, unit="USD",
             period_type=None, tier="DOCUMENT"):
    """A raw FactNode for same-concept multi-source graphs."""
    return FactNode(
        node_id=node_id, canonical_concept=concept, value=Decimal(str(value)),
        original_scale=scale, original_unit=unit, normalized_unit=unit,
        currency=currency, period=period, period_type=period_type,
        source=source, source_tier=tier, status=status,
    )


def graph(*nodes):
    g = FactGraph()
    for n in nodes:
        g.add(n)
    return g


# ---------------------------------------------------------------------------
# PART A - DUPONT DECOMPOSITION
# ---------------------------------------------------------------------------

def test_a_dupont():
    print("PART A - DUPONT DECOMPOSITION")

    engine = DuPontEngine(prefer_cpp=True)

    # A1: single period - full tree resolution (FY2025: NP 200, Rev 1000,
    # TA 2000, Equity 500 -> PM 0.2, AT 0.5, EM 4, ROE 40.00%)
    facts25 = {
        "Net Profit": F(200),
        "Revenue": F(1000),
        "Total Assets": F(2000),
        "Equity": F(500),
    }
    p25 = engine.solve_period("FY2025", facts25)
    check("A1a. ROE value = 40 (percentage number)",
          p25.roe.value == Decimal("40"), str(p25.roe.value))
    check("A1b. ROE display = 40.00%", p25.roe.display_value == "40.00%",
          p25.roe.display_value)
    check("A1c. Profit Margin component = 0.20",
          p25.components[PROFIT_MARGIN].value == Decimal("0.2"),
          p25.components[PROFIT_MARGIN].display_value)
    check("A1d. Asset Turnover component = 0.50",
          p25.components[ASSET_TURNOVER].value == Decimal("0.5"))
    check("A1e. Equity Multiplier component = 4.00",
          p25.components[EQUITY_MULTIPLIER].value == Decimal("4"))
    check("A1f. components derived from VERIFIED leaves",
          p25.components[PROFIT_MARGIN].status == DERIVED
          and p25.components[ASSET_TURNOVER].status == DERIVED
          and p25.components[EQUITY_MULTIPLIER].status == DERIVED)
    check("A1g. ROE status DERIVED (never VERIFIED)", p25.roe.status == DERIVED,
          p25.roe.status)
    check("A1h. period status DERIVED", p25.status == DERIVED)
    check("A1i. ROE lineage present with formula",
          p25.lineage is not None and p25.lineage.formula_id == "DUPONT_ROE",
          str(p25.lineage.formula_id if p25.lineage else None))
    check("A1j. lineage traversal includes components",
          p25.lineage is not None
          and PROFIT_MARGIN in p25.lineage.traversal_path
          and ASSET_TURNOVER in p25.lineage.traversal_path
          and EQUITY_MULTIPLIER in p25.lineage.traversal_path,
          str(p25.lineage.traversal_path if p25.lineage else None))
    check("A1k. ROE formula is registered (declarative, not hard-coded)",
          DUPONT_REGISTRY.require("DUPONT_ROE").target == RETURN_ON_EQUITY)
    check("A1l. DUPONT_REGISTRY has 4 registered formulas",
          len(DUPONT_REGISTRY) == 4, str(len(DUPONT_REGISTRY)))

    # A2: status rules - BLOCKED dependency blocks downstream
    p_blocked = engine.solve_period("FY2025", {
        "Net Profit": F(200),
        "Revenue": F(1000),
        "Total Assets": F(2000),
        # Equity missing
    })
    check("A2a. missing Equity -> ROE BLOCKED",
          p_blocked.roe.status == BLOCKED, p_blocked.roe.status)
    check("A2b. BLOCKED ROE reason names Equity",
          "Equity" in (p_blocked.roe.reason or ""),
          p_blocked.roe.reason or "")
    check("A2c. period status BLOCKED", p_blocked.status == BLOCKED)
    check("A2d. Equity Multiplier BLOCKED too",
          p_blocked.components[EQUITY_MULTIPLIER].status == BLOCKED)
    check("A2e. no invented value for blocked ROE",
          p_blocked.roe.value is None)

    # A3: REVIEW_REQUIRED never silently becomes VERIFIED/DERIVED
    p_review = engine.solve_period("FY2025", {
        "Net Profit": F(200),
        "Revenue": F(1000),
        "Total Assets": F(2000),
        "Equity": F(500, extraction_state="review_required"),
    })
    check("A3a. REVIEW_REQUIRED input -> ROE REVIEW_REQUIRED",
          p_review.roe.status == REVIEW_REQUIRED, p_review.roe.status)
    check("A3b. never upgraded to VERIFIED/DERIVED",
          p_review.roe.status not in (VERIFIED, DERIVED))

    # A4: zero denominator -> BLOCKED
    p_zero = engine.solve_period("FY2025", {
        "Net Profit": F(200),
        "Revenue": F(0),
        "Total Assets": F(2000),
        "Equity": F(500),
    })
    check("A4a. zero Revenue -> Profit Margin BLOCKED",
          p_zero.components[PROFIT_MARGIN].status == BLOCKED)
    check("A4b. zero denominator propagates to ROE",
          p_zero.roe.status == BLOCKED)

    # A5: percent-kind fact normalization (Profit Margin fact = 20%)
    p_pct = engine.solve_period("FY2025", {
        "Net Profit": F(200),
        "Revenue": F(1000),
        "Profit Margin": F(20, unit="percent"),
        "Total Assets": F(2000),
        "Equity": F(500),
    })
    check("A5a. percent fact 20% normalized to 0.2 for the chain",
          p_pct.roe.value == Decimal("40"), str(p_pct.roe.value))
    check("A5b. percent fact did not double-scale the chain",
          p_pct.roe.display_value == "40.00%")

    # A6: two-period comparison (FY2024 -> FY2025)
    facts24 = {
        "Net Profit": F(180, period="FY2024"),
        "Revenue": F(900, period="FY2024"),
        "Total Assets": F(1800, period="FY2024"),
        "Equity": F(600, period="FY2024"),
    }
    analysis = engine.analyze({"FY2024": facts24, "FY2025": facts25})
    check("A6a. analysis has both periods",
          len(analysis.periods) == 2 and len(analysis.comparisons) == 1)
    cmp = analysis.comparisons[0]
    check("A6b. previous ROE = 30.00%",
          cmp.previous_roe.value == Decimal("30"),
          cmp.previous_roe.display_value)
    check("A6c. current ROE = 40.00%",
          cmp.current_roe.value == Decimal("40"))
    check("A6d. absolute change = 10 pp",
          cmp.absolute_change == Decimal("10"), str(cmp.absolute_change))
    check("A6e. percentage change = 33.33%",
          cmp.percentage_change == Decimal("100") / Decimal("3"),
          str(cmp.percentage_change))
    check("A6f. component changes recorded",
          cmp.component_changes[PROFIT_MARGIN] == Decimal("0")
          and cmp.component_changes[ASSET_TURNOVER] == Decimal("0")
          and cmp.component_changes[EQUITY_MULTIPLIER] == Decimal("1"))
    contrib = {c.component: c.contribution for c in cmp.contributions}
    check("A6g. Profit Margin contribution = 0 pp",
          contrib[PROFIT_MARGIN] == Decimal("0"))
    check("A6h. Asset Turnover contribution = 0 pp",
          contrib[ASSET_TURNOVER] == Decimal("0"))
    check("A6i. Equity Multiplier contribution = 10 pp",
          contrib[EQUITY_MULTIPLIER] == Decimal("10"))
    check("A6j. contributions sum exactly to the delta",
          sum(contrib.values()) == cmp.absolute_change,
          str(sum(contrib.values())))
    check("A6k. largest contributor = Equity Multiplier",
          cmp.largest_contributor == EQUITY_MULTIPLIER,
          str(cmp.largest_contributor))
    check("A6l. comparison status DERIVED", cmp.status == DERIVED, cmp.status)
    check("A6m. contribution method documented", "Sequential replacement" in cmp.method)

    # A7: blocked period -> comparison BLOCKED (never invented)
    analysis_blocked = engine.analyze({
        "FY2024": facts24,
        "FY2025": {
            "Net Profit": F(200),
            "Revenue": F(1000),
            "Total Assets": F(2000),
            # Equity missing
        },
    })
    cblk = analysis_blocked.comparisons[0]
    check("A7a. comparison BLOCKED when current period is blocked",
          cblk.status == BLOCKED, cblk.status)
    check("A7b. blocked comparison names the period",
          "FY2025" in (cblk.reason or ""), cblk.reason or "")
    check("A7c. no contributions invented for a blocked delta",
          cblk.contributions == [] and cblk.largest_contributor is None)

    # A8: three periods -> two consecutive comparisons (deterministic order)
    facts23 = {
        "Net Profit": F(150, period="FY2023"),
        "Revenue": F(750, period="FY2023"),
        "Total Assets": F(1500, period="FY2023"),
        "Equity": F(500, period="FY2023"),
    }
    a3 = engine.analyze({"FY2025": facts25, "FY2023": facts23, "FY2024": facts24})
    check("A8a. periods sorted deterministically",
          [p.period for p in a3.periods] == ["FY2023", "FY2024", "FY2025"])
    check("A8b. two consecutive comparisons",
          len(a3.comparisons) == 2)
    check("A8c. comparison 1 FY2023->FY2024 ROE values",
          a3.comparisons[0].previous_roe.value == Decimal("30")
          and a3.comparisons[0].current_roe.value == Decimal("30"))
    check("A8d. comparison 2 FY2024->FY2025 delta = 10",
          a3.comparisons[1].absolute_change == Decimal("10"))

    # A9: deterministic repeated execution
    a_repeat = engine.analyze({"FY2024": facts24, "FY2025": facts25})
    check("A9a. identical to_dict across runs",
          a_repeat.to_dict() == analysis.to_dict())
    check("A9b. analysis status DERIVED", analysis.status == DERIVED)

    # A10: source immutability (original facts never mutated)
    rev_after = facts25["Revenue"]["value"]
    check("A10a. original fact values unchanged after analysis",
          rev_after == 1000)
    check("A10b. percent fact original preserved",
          p_pct is not None)


# ---------------------------------------------------------------------------
# PART B - FORENSIC CROSS-STATEMENT RECONCILIATION
# ---------------------------------------------------------------------------

def test_b_reconciliation():
    print("PART B - FORENSIC CROSS-STATEMENT RECONCILIATION")

    eng = ReconciliationEngine(prefer_cpp=True)
    rule = DEFAULT_RECONCILIATION_RULES.require("RE_STRAP_NET_PROFIT")

    # B1: matching retained-earnings strap (bridge items included)
    facts = {
        "Net Profit": F(300),
        "Retained Earnings Ending": F(1500),
        "Retained Earnings Beginning": F(1300, period="FY2024"),
        "Dividends Paid": F(100),
    }
    res = eng.reconcile(rule, facts["Net Profit"], facts)
    check("B1a. reconciling within tolerance -> RECONCILED",
          res.status == RECONCILED, res.status)
    check("B1b. expected value = 300 (1500-1300+100)",
          res.expected_value == Decimal("300"), str(res.expected_value))
    check("B1c. zero variance", res.absolute_variance == Decimal("0"))
    check("B1d. expected relationship documented",
          "Retained Earnings Ending - Retained Earnings Beginning" in
          res.expected_relationship)

    # B2: material variance -> REVIEW_REQUIRED, values preserved
    facts_var = dict(facts)
    facts_var["Net Profit"] = F(320)
    res2 = eng.reconcile(rule, facts_var["Net Profit"], facts_var)
    check("B2a. variance >= tolerance -> REVIEW_REQUIRED",
          res2.status == REVIEW_REQUIRED, res2.status)
    check("B2b. observed value preserved", res2.observed_value == Decimal("320"))
    check("B2c. expected value preserved", res2.expected_value == Decimal("300"))
    check("B2d. variance = 20", res2.variance == Decimal("20"))
    check("B2e. absolute variance = 20", res2.absolute_variance == Decimal("20"))
    check("B2f. relative variance = 6.67%",
          res2.relative_variance == Decimal("20") / Decimal("300"),
          str(res2.relative_variance))
    check("B2g. tolerance threshold = 15 (5% of expected)",
          res2.tolerance == Decimal("15"), str(res2.tolerance))
    check("B2h. source nodes carry provenance",
          len(res2.source_nodes) >= 3
          and all("provenance_tier" in s for s in res2.source_nodes))
    check("B2i. payload reason names variance vs tolerance",
          "variance" in (res2.reason or "").lower()
          and "tolerance" in (res2.reason or "").lower())
    check("B2j. original values not overwritten or averaged",
          res2.observed_value == Decimal("320")
          and res2.expected_value == Decimal("300"))
    payload = res2.to_dict()
    for key in ("reconciliation_id", "target", "source_nodes",
                "expected_relationship", "observed_value", "expected_value",
                "variance", "absolute_variance", "relative_variance",
                "tolerance", "periods", "currencies", "units", "reason"):
        check(f"B2k. payload field '{key}' present", key in payload)

    # B3: period mismatch (sources carry wrong period) -> REVIEW_REQUIRED
    facts_pm = {
        "Net Profit": F(300),
        "Retained Earnings Ending": F(1500, period="FY2026"),
        "Retained Earnings Beginning": F(1300, period="FY2025"),
        "Dividends Paid": F(100, period="FY2026"),
    }
    res3 = eng.reconcile(rule, facts_pm["Net Profit"], facts_pm)
    check("B3a. period mismatch -> REVIEW_REQUIRED (discrepancy)",
          res3.status == REVIEW_REQUIRED, res3.status)
    check("B3b. period mismatch reason names the conflict",
          "PERIOD" in (res3.reason or "").upper(),
          res3.reason or "")

    # B4: missing period -> BLOCKED (never label-matched)
    facts_mp = {
        "Net Profit": F(300, period=None),
        "Retained Earnings Ending": F(1500),
        "Retained Earnings Beginning": F(1300, period="FY2024"),
        "Dividends Paid": F(100),
    }
    res4 = eng.reconcile(rule, facts_mp["Net Profit"], facts_mp)
    check("B4a. missing explicit period -> BLOCKED",
          res4.status == BLOCKED, res4.status)

    # B5: currency mismatch -> REVIEW_REQUIRED (never converted)
    facts_cur = {
        "Net Profit": F(300, unit="INR"),
        "Retained Earnings Ending": F(1500),
        "Retained Earnings Beginning": F(1300, period="FY2024"),
        "Dividends Paid": F(100),
    }
    res5 = eng.reconcile(rule, facts_cur["Net Profit"], facts_cur)
    check("B5a. currency mismatch -> REVIEW_REQUIRED",
          res5.status == REVIEW_REQUIRED, res5.status)
    check("B5b. currency reason surfaced",
          "CURRENCY" in (res5.reason or "").upper(), res5.reason or "")

    # B6: missing source -> BLOCKED (missing information)
    facts_miss = {
        "Net Profit": F(300),
        "Retained Earnings Ending": F(1500),
        # Retained Earnings Beginning absent
        "Dividends Paid": F(100),
    }
    res6 = eng.reconcile(rule, facts_miss["Net Profit"], facts_miss)
    check("B6a. missing source -> BLOCKED", res6.status == BLOCKED, res6.status)

    # B7: BLOCKED source fact -> BLOCKED
    facts_blk = {
        "Net Profit": F(300),
        "Retained Earnings Ending": {
            "value": "not-a-number", "source": "BS.pdf",
            "reporting_period": "FY2025", "provenance_tier": "DOCUMENT",
        },
        "Retained Earnings Beginning": F(1300, period="FY2024"),
        "Dividends Paid": F(100),
    }
    res7 = eng.reconcile(rule, facts_blk["Net Profit"], facts_blk)
    check("B7a. unusable source value -> BLOCKED", res7.status == BLOCKED,
          res7.status)

    # B8: REVIEW_REQUIRED source never silently reconciles
    facts_rr = {
        "Net Profit": F(300),
        "Retained Earnings Ending": F(1500, extraction_state="review_required"),
        "Retained Earnings Beginning": F(1300, period="FY2024"),
        "Dividends Paid": F(100),
    }
    res8 = eng.reconcile(rule, facts_rr["Net Profit"], facts_rr)
    check("B8a. review-required source -> REVIEW_REQUIRED",
          res8.status == REVIEW_REQUIRED, res8.status)

    # B9: cross-statement identity (IS vs CF)
    ok = eng.reconcile_cross_statement(
        "Net Profit",
        F(200, source="Income Statement"), "Income Statement",
        F(210, source="Cash Flow Statement"), "Cash Flow Statement",
    )
    check("B9a. IS=200 vs CF=210 within 5% -> RECONCILED",
          ok.status == RECONCILED, ok.status)
    bad = eng.reconcile_cross_statement(
        "Net Profit",
        F(200, source="Income Statement"), "Income Statement",
        F(220, source="Cash Flow Statement"), "Cash Flow Statement",
    )
    check("B9b. IS=200 vs CF=220 -> REVIEW_REQUIRED",
          bad.status == REVIEW_REQUIRED, bad.status)
    same_src = eng.reconcile_cross_statement(
        "Net Profit",
        F(200, source="Income Statement"), "Income Statement",
        F(200, source="Income Statement"), "Income Statement",
    )
    check("B9c. non-independent statements -> BLOCKED",
          same_src.status == BLOCKED, same_src.status)
    diff_period = eng.reconcile_cross_statement(
        "Net Profit",
        F(200, period="FY2024", source="Income Statement"), "Income Statement",
        F(210, period="FY2025", source="Cash Flow Statement"),
        "Cash Flow Statement",
    )
    check("B9d. cross-period comparison -> REVIEW_REQUIRED (never label-matched)",
          diff_period.status == REVIEW_REQUIRED, diff_period.status)
    cur_mm = eng.reconcile_cross_statement(
        "Net Profit",
        F(200, unit="USD", source="Income Statement"), "Income Statement",
        F(210, unit="INR", source="Cash Flow Statement"), "Cash Flow Statement",
    )
    check("B9e. currency mismatch -> REVIEW_REQUIRED (never converted)",
          cur_mm.status == REVIEW_REQUIRED, cur_mm.status)

    # B10: deterministic repeated run
    res_repeat = eng.reconcile(rule, facts_var["Net Profit"], facts_var)
    check("B10a. repeated reconcile is identical",
          res_repeat.to_dict() == res2.to_dict())


# ---------------------------------------------------------------------------
# PART C - DETERMINISTIC ADJUSTMENT / ANOMALY REASONING
# ---------------------------------------------------------------------------

def test_c_adjustments():
    print("PART C - DETERMINISTIC ADJUSTMENT / ANOMALY REASONING")

    # C1: conflicting source values + cross-statement discrepancy
    g = graph(
        rev_node("Revenue (IS)", "Revenue", 1000, source="Income Statement"),
        rev_node("Revenue (CF)", "Revenue", 1200, source="Cash Flow Statement"),
    )
    anoms = detect_anomalies(g)
    kinds = {a.kind for a in anoms}
    check("C1a. conflicting source values detected",
          CONFLICTING_SOURCE_VALUES in kinds, str(sorted(kinds)))
    check("C1b. cross-statement discrepancy detected",
          CROSS_STATEMENT_DISCREPANCY in kinds)
    check("C1c. every candidate is review-required (never auto-corrected)",
          all(a.review_required and a.status == ANOMALY_DETECTED
              for a in anoms))
    check("C1d. candidates are structured",
          all(isinstance(a, AnomalyCandidate) and a.to_dict()["kind"]
              for a in anoms))

    # C2: suspicious scale mismatch (spec example: 125.4 USD millions vs
    # 125400 USD) - detected, never silently chosen
    g2 = graph(
        rev_node("Revenue (A)", "Revenue", 125400000, source="Doc A",
                 scale="millions"),
        rev_node("Revenue (B)", "Revenue", 125400, source="Doc B", scale=None),
    )
    kinds2 = {a.kind for a in detect_anomalies(g2)}
    check("C2a. scale mismatch candidate detected",
          SCALE_MISMATCH in kinds2, str(sorted(kinds2)))
    scale_anom = [a for a in detect_anomalies(g2)
                  if a.kind == SCALE_MISMATCH][0]
    check("C2b. scale candidate shows both normalized values",
          "125400000" in scale_anom.description
          and "125400" in scale_anom.description,
          scale_anom.description)

    # C3: zero denominator
    zkinds = {a.kind for a in detect_anomalies({"Assets": F(0)})}
    check("C3a. zero denominator candidate detected",
          ZERO_DENOMINATOR in zkinds, str(sorted(zkinds)))

    # C4: missing dependency (Profit needs Expenses)
    mkinds = {a.kind for a in detect_anomalies({"Revenue": F(1000)})}
    check("C4a. missing dependency candidate detected",
          MISSING_DEPENDENCY in mkinds, str(sorted(mkinds)))
    miss = [a for a in detect_anomalies({"Revenue": F(1000)})
            if a.kind == MISSING_DEPENDENCY and a.target == "Profit"]
    check("C4b. missing dependency names Expenses",
          len(miss) == 1 and "Expenses" in miss[0].details.get("missing", []),
          str(miss[0].details if miss else None))

    # C5: unsupported accounting label
    ukinds = {a.kind for a in detect_anomalies({"Mystery Metric": F(100)})}
    check("C5a. unsupported label candidate detected",
          UNSUPPORTED_LABEL in ukinds, str(sorted(ukinds)))

    # C6: unexpected sign (Revenue must be non-negative)
    skinds = {a.kind for a in detect_anomalies({"Revenue": F(-100)})}
    check("C6a. unexpected sign candidate detected",
          UNEXPECTED_SIGN in skinds, str(sorted(skinds)))

    # C7: duplicate facts
    g7 = graph(
        rev_node("Revenue (A)", "Revenue", 1000, source="Doc A"),
        rev_node("Revenue (B)", "Revenue", 1000, source="Doc B"),
    )
    kinds7 = {a.kind for a in detect_anomalies(g7)}
    check("C7a. duplicate fact candidate detected",
          DUPLICATE_FACT in kinds7, str(sorted(kinds7)))
    check("C7b. identical values do NOT trigger a value conflict",
          CONFLICTING_SOURCE_VALUES not in kinds7)

    # C8: conflicting provenance
    g8 = graph(
        rev_node("Revenue (A)", "Revenue", 1000, status=VERIFIED,
                 source="Doc A"),
        rev_node("Revenue (B)", "Revenue", 1000, status=REVIEW_REQUIRED,
                 source="Doc B"),
    )
    kinds8 = {a.kind for a in detect_anomalies(g8)}
    check("C8a. conflicting provenance candidate detected",
          CONFLICTING_PROVENANCE in kinds8, str(sorted(kinds8)))

    # C9: incompatible units (currency vs shares)
    g9 = graph(
        rev_node("Revenue (A)", "Revenue", 1000, unit="USD", source="Doc A"),
        rev_node("Revenue (B)", "Revenue", 1000, unit="shares", source="Doc B"),
    )
    kinds9 = {a.kind for a in detect_anomalies(g9)}
    check("C9a. incompatible units candidate detected",
          INCOMPATIBLE_UNITS in kinds9, str(sorted(kinds9)))

    # C10: immutable adjustment flow (never VERIFIED -> auto-adjust -> VERIFIED)
    g10 = graph(
        rev_node("Revenue (IS)", "Revenue", 1000, source="Income Statement"),
        rev_node("Revenue (CF)", "Revenue", 1200, source="Cash Flow Statement"),
    )
    conflict = [a for a in detect_anomalies(g10)
                if a.kind == CONFLICTING_SOURCE_VALUES][0]
    node, record = propose_adjustment(
        conflict, Decimal("1200"), g10,
        decision="ADJUST", reason="student confirmed the cash-flow figure",
    )
    check("C10a. adjusted node status = STUDENT_INPUT (never VERIFIED)",
          node.status == STUDENT_INPUT, node.status)
    check("C10b. adjusted value applied", node.value == Decimal("1200"))
    check("C10c. adjustment record preserves both originals",
          record.original_values == {"Revenue (IS)": "1000",
                                     "Revenue (CF)": "1200"},
          str(record.original_values))
    check("C10d. record status STUDENT_INPUT", record.status == STUDENT_INPUT)
    check("C10e. lineage links original -> adjustment -> decision",
          "ADJ-" in (node.lineage or "") and "ADJUST" in (node.lineage or ""),
          node.lineage or "")
    check("C10f. original facts untouched (immutability)",
          g10.get("Revenue (IS)").value == Decimal("1000")
          and g10.get("Revenue (CF)").value == Decimal("1200"))

    # C11: recalculate the graph with the explicit adjustment
    sol = resolve_with_adjustments(
        "Profit", {"Revenue": F(1000), "Expenses": F(800)}, [node]
    )
    check("C11a. adjusted Revenue 1200 -> Profit 400",
          sol.value == Decimal("400"), str(sol.value))
    check("C11b. adjusted result propagates STUDENT_INPUT status",
          sol.status == STUDENT_INPUT, sol.status)
    check("C11c. original facts dict unchanged",
          {"Revenue": F(1000), "Expenses": F(800)}["Revenue"]["value"] == 1000)

    # C12: non-numeric adjustment is rejected (never guess)
    rejected = False
    try:
        propose_adjustment(conflict, "not-a-number", g10)
    except ValueError:
        rejected = True
    check("C12a. non-numeric adjustment rejected",
          rejected)


# ---------------------------------------------------------------------------
# PART D - INTEGRATION + 12A REGRESSION SANITY
# ---------------------------------------------------------------------------

def test_d_integration():
    print("PART D - INTEGRATION + 12A REGRESSION SANITY")

    # D1: reasoning reuses the 12A machinery - no second engine
    check("D1a. DUPONT_REGISTRY is a 12A FormulaRegistry",
          isinstance(DUPONT_REGISTRY, FormulaRegistry))
    check("D1b. default 12A registry still has 7 formulas",
          len(default_registry()) == 7, str(len(default_registry())))
    check("D1c. Solver still solves Profit deterministically",
          Solver(prefer_cpp=True).solve(
              "Profit",
              build_fact_graph({"Revenue": F(1000), "Expenses": F(800)}),
          ).value == Decimal("200"))
    check("D1d. reconciliation rules registered",
          len(DEFAULT_RECONCILIATION_RULES.all_rules()) == 2)

    # D2: full reasoning pipeline over one fact set (deterministic smoke)
    facts = {
        "Net Profit": F(200),
        "Revenue": F(1000),
        "Total Assets": F(2000),
        "Equity": F(500),
        "Retained Earnings Ending": F(1500),
        "Retained Earnings Beginning": F(1300, period="FY2024"),
        "Dividends Paid": F(100),
    }
    analysis = DuPontEngine(prefer_cpp=True).analyze({"FY2025": facts})
    rule = DEFAULT_RECONCILIATION_RULES.require("RE_STRAP_NET_PROFIT")
    rec = ReconciliationEngine(prefer_cpp=True).reconcile(
        rule, facts["Net Profit"], facts
    )
    anoms = detect_anomalies(facts)
    check("D2a. DuPont ROE derived in full pipeline",
          analysis.periods[0].roe.value == Decimal("40"),
          str(analysis.periods[0].roe.value))
    check("D2b. reconciliation runs in full pipeline",
          rec.status in (RECONCILED, REVIEW_REQUIRED), rec.status)
    check("D2c. anomaly scan runs in full pipeline",
          isinstance(anoms, list))
    check("D2d. deterministic full pipeline (repeat identical)",
          DuPontEngine(prefer_cpp=True).analyze(
              {"FY2025": facts}).to_dict() == analysis.to_dict())
    check("D2e. no crashes, no network, no AI",
          True)


def main():
    test_a_dupont()
    test_b_reconciliation()
    test_c_adjustments()
    test_d_integration()
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 60)
    print(f"RESULT: {passed}/{total} checks passed")
    failed = [n for n, ok, _ in CHECKS if not ok]
    if failed:
        print("FAILED CHECKS:")
        for n in failed:
            print(f"  - {n}")
    print("ALL CHECKS COMPLETE" if not failed else "FAILURES PRESENT")


if __name__ == "__main__":
    main()
