#!/usr/bin/env python3
"""
Platrixa
Sprint 13 - FYJC Student Maths & Book-Keeping Readiness Gate
scripts/fte_fyjc_readiness_test.py

The release gate for the FYJC student-facing layer on top of the
existing 12A-12F deterministic architecture. It proves that a student
who understands finance but knows nothing about Platrixa's internals can:

    Photo/Question -> Extraction -> Interpretation -> Accounting
    Reasoning -> C++ Calculation -> Answer -> Explanation

and receives a correct evidence-backed result when sufficient evidence
exists, or a clear, deterministic explanation (never a guessed value)
when Platrixa cannot safely calculate.

Areas verified (Sprint 13 section H):
    1.  FYJC question classification
    2.  account classification
    3.  golden-rule selection
    4.  debit/credit reasoning
    5.  journal generation
    6.  ledger posting
    7.  trial-balance construction
    8.  trial-balance tally detection
    9.  discrepancy handling
    10. missing-input blocking
    11. ambiguity handling
    12. unsupported-operation refusal
    13. C++ authority enforcement
    14. forward numerical calculations
    15. multi-step deterministic chains
    16. lineage preservation
    17. evidence preservation
    18. no silent substitution
    19. no fabricated values
    20. repeated-run determinism
    21. regression against all 12A-12F suites

Section I adds the student acceptance workflow (correct AND incorrect
student work, plus a readable refusal).

Every numerical expectation in the FYJC golden dataset is a hand-verified
constant - the dataset never calls the engine. The C++ engine remains the
sole mathematical authority; Python never performs a fallback calculation.

Target: 100% deterministic PASS. No LLM, no network, no fabricated
values, no silent substitution.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.maths import (  # noqa: E402
    BLOCKED,
    DERIVED,
    REVIEW_REQUIRED,
    STUDENT_INPUT,
    VERIFIED,
    accounting_calculation,
    build_trial_balance,
    canonical_account,
    classify_transaction,
    classify_fyjc_question,
    extract_facts_from_question,
    identify_debit_credit,
    ledger_balance,
    post_ledger,
    verify_arithmetic,
    verify_journal_entry,
    verify_ledger_balance,
    verify_maths_answer,
    verify_trial_balance,
    FYJC_ACCEPTANCE_CASES,
    FYJC_ACCOUNTING_CASES,
    FYJC_JOURNAL_CASES,
    FYJC_LEDGER_ENTRIES,
    FYJC_LEDGER_EXPECT,
    FYJC_LEDGER_TOTALS,
    FYJC_LEDGER_VERIFY_CASES,
    FYJC_MATHS_CASES,
    FYJC_QUESTION_CASES,
    FYJC_TB_CASES,
    FYJC_TB_EXPECT,
)
from backend.maths.authority import engine_available  # noqa: E402
from backend.maths.fyjc_maths import (  # noqa: E402
    AUTHORITY_CPP,
    AUTHORITY_UNAVAILABLE,
    UNSUPPORTED,
    FYJC_MATHS_CHECKLIST,
    resolve_metric,
    solve_strict,
    supported_metric_names,
)
from backend.maths.fyjc_question import (  # noqa: E402
    DOMAIN_BOOKKEEPING,
    DOMAIN_MATHS,
)
from backend.maths.student_sandbox import STATUS_WORDS  # noqa: E402

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

CHECKS = []
FAILURES = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def stable(obj):
    """Deterministic, JSON-normalized fingerprint for equality checks."""
    return json.dumps(obj, sort_keys=True, default=str)


def F(value, **extra):
    """One verified pipeline fact (Tier 1 document), student-test shape."""
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
# Part A - FYJC question classification (area 1)
# ---------------------------------------------------------------------------


def test_a_classification():
    print("PART A - FYJC QUESTION CLASSIFICATION")

    for case in FYJC_QUESTION_CASES:
        out = classify_fyjc_question(case["question"])
        ok = (out["domain"] == case["expect_domain"]
              and out["kind"] == case["expect_kind"])
        metric_ok = True
        if "expect_metric" in case:
            metric_ok = (out.get("metric") == case["expect_metric"])
        check(f"A.{case['id']} domain/kind",
              ok, f"{out['domain']}/{out['kind']} vs "
                  f"{case['expect_domain']}/{case['expect_kind']}")
        check(f"A.{case['id']} metric", metric_ok,
              f"{out.get('metric')} vs {case.get('expect_metric')}")

    # deterministic intermediate representation: extraction never guesses
    facts = extract_facts_from_question(
        "Current Assets: Rs.5,00,000\nCurrent Liabilities: Rs.2,50,000"
    )
    ca = facts.get("Current Assets") or {}
    cl = facts.get("Current Liabilities") or {}
    check("A.extract lakh-style facts deterministically",
          ca.get("value") == 500000 and cl.get("value") == 250000,
          f"{ca.get('value')} / {cl.get('value')}")
    check("A.extracted facts carry Tier-1 provenance",
          str(ca.get("provenance_tier")) == "DOCUMENT",
          str(ca.get("provenance_tier")))
    check("A.unparseable text yields no fabricated fact",
          extract_facts_from_question("Revenue: 1.234,56") == {})


# ---------------------------------------------------------------------------
# Part B - account classification (area 2)
# ---------------------------------------------------------------------------


def test_b_accounts():
    print("PART B - ACCOUNT CLASSIFICATION")

    from backend.maths.fyjc_accounting import (
        ACCOUNT_ALIASES,
        ACCOUNT_ROLES,
        SIDE_EXPLANATION,
        account_role,
        side_for_role,
    )

    for account in sorted(ACCOUNT_ROLES):
        role = account_role(account)
        check(f"B.role({account})", role is not None and role in SIDE_EXPLANATION,
              str(role))
        check(f"B.side({account})", side_for_role(role) in ("debit", "credit"),
              side_for_role(role))

    alias_checks = [
        ("purchases", "Purchases"), ("sundry debtors", "Debtors"),
        ("cash", "Cash"), ("accounts payable", "Creditors"),
        ("returns inward", "Sales Returns"), ("plant and machinery", "Machinery"),
        ("closing stock", "Stock"),
    ]
    for raw, canon in alias_checks:
        check(f"B.alias {raw!r} -> {canon}",
              canonical_account(raw) == canon,
              str(canonical_account(raw)))
    check("B.unknown account refused",
          canonical_account("Rocket Fuel") is None)
    check("B.every alias resolves into the chart",
          all(canonical_account(a) in ACCOUNT_ROLES
              for a in ACCOUNT_ALIASES), "alias set maps into the chart")


# ---------------------------------------------------------------------------
# Part C/D/E - golden rules, debit/credit reasoning, journal generation
# (areas 3-5)
# ---------------------------------------------------------------------------


def test_cde_golden_rules_journal():
    print("PART C/D/E - GOLDEN RULES + DEBIT/CREDIT + JOURNAL GENERATION")

    from backend.maths.fyjc_accounting import ACCOUNT_ROLES, account_role

    for case in FYJC_ACCOUNTING_CASES:
        out = classify_transaction(case["question"])
        cid = case["id"]
        expect = case["expect_status"]
        if expect == VERIFIED:
            got_debit = {line["account"] for line in out["debit_lines"]}
            got_credit = {line["account"] for line in out["credit_lines"]}
            check(f"E.{cid} classified VERIFIED",
                  out["status"] == VERIFIED, out["status"])
            check(f"E.{cid} debit accounts",
                  got_debit == case["expect_debit"], str(got_debit))
            check(f"E.{cid} credit accounts",
                  got_credit == case["expect_credit"], str(got_credit))
            check(f"C.{cid} golden rule exposed",
                  bool(out.get("rule"))
                  and "debit" in out["rule"].lower()
                  and "credit" in out["rule"].lower(),
                  str(out.get("rule"))[:120])
            check(f"C.{cid} rule_key",
                  out.get("rule_key") == case["expect_rule_key"],
                  str(out.get("rule_key")))
            # every debit line really is a debit and vice versa
            sides_ok = (
                all(l["side"] == "debit" for l in out["debit_lines"])
                and all(l["side"] == "credit" for l in out["credit_lines"])
            )
            check(f"D.{cid} line sides correct", sides_ok,
                  str([(l["account"], l["side"])
                       for l in out["debit_lines"] + out["credit_lines"]]))
            # journal is arithmetically balanced (single amount)
            amounts = ([l.get("amount") for l in out["debit_lines"]]
                       + [l.get("amount") for l in out["credit_lines"]])
            check(f"D.{cid} single amount on every line",
                  len({str(a) for a in amounts}) == 1, str(amounts))
            # roles are attached and known (credit lines legitimately
            # include asset/expense decreases, e.g. Cash credited when
            # an asset falls - never assumed by the engine, checked
            # against the golden-rule account sets in E.* above)
            known_roles = set(ACCOUNT_ROLES.values())
            for line in out["debit_lines"]:
                role = account_role(line["account"])
                check(f"D.{cid} debit role", role in known_roles
                      or role is None, str(role))
            for line in out["credit_lines"]:
                role = account_role(line["account"])
                check(f"D.{cid} credit role", role in known_roles
                      or role is None, str(role))
        elif expect == BLOCKED:
            check(f"J.{cid} blocked (missing amount)", out["status"] == BLOCKED,
                  out["status"])
            check(f"E.{cid} treatment still determinable",
                  {l["account"] for l in out["debit_lines"]} == case["expect_debit"]
                  and {l["account"] for l in out["credit_lines"]}
                  == case["expect_credit"],
                  str(out["debit_lines"]) + str(out["credit_lines"]))
            check(f"S.{cid} no fabricated amount",
                  all(l.get("amount") is None
                      for l in out["debit_lines"] + out["credit_lines"]),
                  "amounts present")
            check(f"J.{cid} next action asks for the amount",
                  "amount" in str(out.get("next_action", "")).lower(),
                  str(out.get("next_action")))
        else:  # REVIEW_REQUIRED
            check(f"K.{cid} review required",
                  out["status"] == REVIEW_REQUIRED, out["status"])
            check(f"S.{cid} no journal lines fabricated",
                  not out["debit_lines"] and not out["credit_lines"],
                  str(out["debit_lines"]) + str(out["credit_lines"]))
            check(f"R.{cid} no rule_key claimed",
                  out.get("rule_key") is None, str(out.get("rule_key")))
            check(f"K.{cid} student-readable why_not",
                  bool(out.get("why_not")) and "never" in str(
                      out.get("why_not", "")).lower(),
                  str(out.get("why_not"))[:160])

    # identify_debit_credit UX exposes the same reasoning
    iddc = identify_debit_credit("Purchased goods for cash Rs.10,000")
    check("D.identify_debit_credit debit",
          [l["account"] for l in iddc["debit"]] == ["Purchases"],
          str(iddc["debit"]))
    check("D.identify_debit_credit credit",
          [l["account"] for l in iddc["credit"]] == ["Cash"],
          str(iddc["credit"]))
    check("D.identify_debit_credit rule",
          "Purchases" in str(iddc["rule"]), str(iddc["rule"])[:120])


# ---------------------------------------------------------------------------
# Part F/G/H/I - journal verification, ledger, trial balance, discrepancies
# (areas 6-9)
# ---------------------------------------------------------------------------


def test_fghi_ledger_trial():
    print("PART F/G/H/I - JOURNAL VERIFICATION + LEDGER + TRIAL BALANCE")

    # ---- journal verification (dataset) ---------------------------------
    for case in FYJC_JOURNAL_CASES:
        out = verify_journal_entry(case["description"], case["entry"])
        expect = case["expect_verdict"]
        check(f"F.{case['id']} journal verdict {expect}",
              out["verdict"] == expect,
              f"{out['verdict']} {out.get('why_not')}")
        if expect == "INCORRECT" and case.get("expect_discrepancy"):
            check(f"I.{case['id']} exact discrepancy",
                  out.get("discrepancy") == case["expect_discrepancy"],
                  str(out.get("discrepancy")))
        if expect == "REFUSED":
            check(f"J.{case['id']} refused -> blocked",
                  out.get("status") == BLOCKED, out.get("status"))
        if expect == "BALANCED":
            check(f"K.{case['id']} balanced without description -> review",
                  out.get("status") == REVIEW_REQUIRED, out.get("status"))

    # ---- ledger posting (area 6) ----------------------------------------
    ledger = post_ledger(FYJC_LEDGER_ENTRIES)
    check("F.ledger balanced", ledger["balanced"], str(ledger))
    check("F.ledger totals",
          ledger["total_debit"] == FYJC_LEDGER_TOTALS["total_debit"]
          and ledger["total_credit"] == FYJC_LEDGER_TOTALS["total_credit"],
          f"{ledger['total_debit']}/{ledger['total_credit']}")
    for account, exp in FYJC_LEDGER_EXPECT.items():
        row = ledger["accounts"].get(account)
        ok = (row is not None
              and row["debit"] == exp["debit"]
              and row["credit"] == exp["credit"]
              and row["balance"] == exp["balance"]
              and row["balance_side"] == exp["balance_side"])
        check(f"F.ledger {account} balance", ok, str(row))

    for case in FYJC_LEDGER_VERIFY_CASES:
        out = verify_ledger_balance(
            case["account"], case["student_balance"], case["student_side"],
            FYJC_LEDGER_ENTRIES,
        )
        check(f"F.{case['id']} verify ledger balance",
              out["verdict"] == case["expect_verdict"],
              f"{out['verdict']} {out.get('why_not')}")
        if case.get("expect_discrepancy"):
            check(f"I.{case['id']} ledger discrepancy",
                  out.get("discrepancy") == case["expect_discrepancy"],
                  str(out.get("discrepancy")))

    # ledger_balance single-account API agrees with post_ledger
    lb = ledger_balance("Cash", FYJC_LEDGER_ENTRIES)
    check("F.ledger_balance Cash", lb["found"] and lb["balance"] == 55000.0
          and lb["balance_side"] == "Dr", str(lb))

    # ---- trial balance construction (area 7) ----------------------------
    tb = build_trial_balance(FYJC_LEDGER_ENTRIES)
    check("G.trial balance tallies",
          tb["balanced"] and tb["total_debit"] == FYJC_TB_EXPECT["total_debit"]
          and tb["total_credit"] == FYJC_TB_EXPECT["total_credit"],
          f"{tb['total_debit']}/{tb['total_credit']}")
    row_map = {r["account"]: r for r in tb["rows"]}
    for account, exp in FYJC_TB_EXPECT["rows"].items():
        row = row_map.get(account)
        check(f"G.tb row {account}",
              row is not None and row["debit"] == exp["debit"]
              and row["credit"] == exp["credit"], str(row))

    # ---- tally detection + discrepancies (areas 8-9) --------------------
    for case in FYJC_TB_CASES:
        out = verify_trial_balance(case["student_rows"], FYJC_LEDGER_ENTRIES)
        check(f"H.{case['id']} trial balance verdict",
              out["verdict"] == case["expect_verdict"],
              f"{out['verdict']} {out.get('why_not')}")
        if case.get("expect_mention"):
            check(f"I.{case['id']} names the discrepancy",
                  case["expect_mention"] in str(out.get("why_not", "")),
                  str(out.get("why_not"))[:160])
        if case.get("expect_discrepancy"):
            check(f"I.{case['id']} exact discrepancy",
                  out.get("discrepancy") == case["expect_discrepancy"],
                  str(out.get("discrepancy")))

    # generic arithmetic verification (student's own numbers, never altered)
    ok = verify_arithmetic([
        {"side": "debit", "amount": 5000}, {"side": "debit", "amount": 2000},
        {"side": "credit", "amount": 7000},
    ])
    check("H.arithmetic balanced", ok["balanced"] and ok["verdict"] == "CORRECT",
          str(ok))
    bad = verify_arithmetic([
        {"side": "debit", "amount": 5000}, {"side": "debit", "amount": 2000},
        {"side": "credit", "amount": 6000},
    ])
    check("I.arithmetic discrepancy exposed",
          not bad["balanced"] and bad["discrepancy"] == 1000.0, str(bad))


# ---------------------------------------------------------------------------
# Part J/K/L - missing inputs, ambiguity, unsupported (areas 10-12)
# ---------------------------------------------------------------------------


def test_jkl_refusals():
    print("PART J/K/L - MISSING INPUTS + AMBIGUITY + UNSUPPORTED")

    # no facts at all
    out = verify_maths_answer("Profit", facts={})
    check("J.no facts -> BLOCKED",
          out["verdict"] == "REFUSED" and out["status"] == BLOCKED,
          f"{out['verdict']} {out['status']}")
    check("J.blocked exposes next action",
          bool(out.get("next_action")), str(out.get("next_action")))

    # unknown metric -> UNSUPPORTED (never a guessed number)
    out = verify_maths_answer("Simple Interest", facts={"Principal": 1000})
    check("L.unsupported metric refused",
          out["verdict"] == "REFUSED" and out["status"] == UNSUPPORTED,
          f"{out['verdict']} {out['status']}")
    check("L.unsupported exposes no value", out["value"] is None)
    check("L.unsupported explains the limit",
          "registered" in str(out.get("why_not", "")),
          str(out.get("why_not"))[:160])

    # DCF through the strict solver fails closed
    sol = solve_strict("DCF", {"Revenue": 1000})
    check("L.solver refuses unregistered formula",
          sol.status == BLOCKED and sol.value is None,
          f"{sol.status} {sol.reason}")

    # european-style numeric is ambiguous -> BLOCKED, never guessed
    out = verify_maths_answer("Profit", text="Revenue: 1.234,56\nExpenses: 600")
    check("K.european comma refused as ambiguous",
          out["status"] == BLOCKED and out["value"] is None,
          f"{out['status']} {out['value']}")


# ---------------------------------------------------------------------------
# Part M - C++ mathematical authority (area 13)
# ---------------------------------------------------------------------------


def test_m_authority():
    print("PART M - C++ MATHEMATICAL AUTHORITY")

    check("M.engine available", engine_available())
    out = verify_maths_answer("Gross Profit",
                              facts={"Revenue": 50000, "Cost of Sales": 30000})
    check("M.gross profit routed to C++",
          out["resolved"] and out["authority_state"] == AUTHORITY_CPP,
          str(out["authority_state"]))
    check("M.calculation comes from C++ authority",
          out["value"] == 20000.0, str(out["value"]))

    # accounting_calculation reuses the same strict path
    ac = accounting_calculation("Gross Profit",
                                facts={"Revenue": 50000,
                                       "Cost of Sales": 30000})
    check("M.accounting_calculation authority cpp",
          ac["authority_state"] == AUTHORITY_CPP, str(ac["authority_state"]))

    # strict mode NEVER falls back to Python arithmetic
    os.environ["FTE_FORMULA_ENGINE_BIN"] = "/nonexistent/fte-binary"
    try:
        sol = solve_strict("Profit", {"Revenue": 1000, "Expenses": 800})
    finally:
        del os.environ["FTE_FORMULA_ENGINE_BIN"]
    check("M.strict solve blocks when C++ unavailable (no Python fallback)",
          sol.status == BLOCKED
          and sol.sufficiency_state == "ENGINE_UNAVAILABLE",
          f"{sol.status} {sol.sufficiency_state} {sol.reason}")
    check("M.no value fabricated without C++", sol.value is None)

    os.environ["FTE_FORMULA_ENGINE_BIN"] = "/nonexistent/fte-binary"
    try:
        out = verify_maths_answer("Profit",
                                  facts={"Revenue": 1000, "Expenses": 800})
    finally:
        del os.environ["FTE_FORMULA_ENGINE_BIN"]
    check("M.verify_maths_answer blocks without C++",
          out["status"] == BLOCKED
          and out["authority_state"] == AUTHORITY_UNAVAILABLE,
          f"{out['status']} {out['authority_state']}")


# ---------------------------------------------------------------------------
# Part N/O - forward calculations + multi-step chains (areas 14-15)
# ---------------------------------------------------------------------------


def test_no_maths():
    print("PART N/O - FORWARD CALCULATIONS + MULTI-STEP CHAINS")

    for case in FYJC_MATHS_CASES:
        out = verify_maths_answer(
            case["metric"], facts=case.get("facts"), text=case.get("text"),
            student_answer=case.get("student_answer"),
        )
        cid = case["id"]
        if case["expect_verdict"] in ("CORRECT", "INCORRECT"):
            check(f"N.{cid} verdict {case['expect_verdict']}",
                  out["verdict"] == case["expect_verdict"],
                  f"{out['verdict']} {out.get('mismatch')}")
            check(f"N.{cid} display value",
                  out["display_value"] == case["expect_display"],
                  f"{out['display_value']} vs {case['expect_display']}")
            check(f"N.{cid} status is student-input/derived",
                  out["status"] in (STUDENT_INPUT, DERIVED),
                  str(out["status"]))
            check(f"N.{cid} authority is C++",
                  out["authority_state"] == AUTHORITY_CPP,
                  str(out["authority_state"]))
            check(f"N.{cid} value present", out["value"] is not None)
        else:
            check(f"N.{cid} refused", out["verdict"] == "REFUSED",
                  f"{out['verdict']} {out.get('why_not')}")
            check(f"N.{cid} status {case['expect_status']}",
                  out["status"] == case["expect_status"],
                  f"{out['status']} vs {case['expect_status']}")
            check(f"S.{cid} refused exposes no value", out["value"] is None)
            if case.get("expect_missing"):
                check(f"J.{cid} names missing inputs",
                      any(m in str(out.get("missing", []))
                          for m in case["expect_missing"]),
                      str(out.get("missing")))

    # multi-step deterministic chain: Revenue -> Profit -> Profit Margin
    # (typed facts are STUDENT_INPUT per the 12A propagation rule)
    sol = solve_strict("Profit Margin", {"Revenue": 1000, "Expenses": 800})
    check("O.chained Profit Margin = 20.00%",
          sol.status in (DERIVED, STUDENT_INPUT)
          and sol.display_value == "20.00%",
          f"{sol.status} {sol.display_value}")
    check("O.deterministic traversal path",
          sol.traversal_path == ["Revenue", "Expenses", "Profit",
                                 "Profit Margin"],
          str(sol.traversal_path))
    # reverse through the same registry
    rev = solve_strict("Expenses", {"Revenue": 1000, "Profit": 200})
    check("O.reverse Expenses = 800.00",
          rev.status in (DERIVED, STUDENT_INPUT)
          and rev.display_value == "800.00",
          f"{rev.status} {rev.display_value}")


# ---------------------------------------------------------------------------
# Part P/Q/R/S - lineage, evidence, no substitution, no fabrication
# (areas 16-19)
# ---------------------------------------------------------------------------


def test_pqrs_evidence():
    print("PART P/Q/R/S - LINEAGE + EVIDENCE + SAFETY")

    # ---- lineage (area 16) ---------------------------------------------
    out = verify_maths_answer("Profit",
                              facts={"Revenue": 1000, "Expenses": 800})
    check("P.inputs rows carry concepts+values",
          bool(out.get("inputs"))
          and all(r.get("concept") and r.get("value") is not None
                  for r in out["inputs"]),
          str(out.get("inputs"))[:200])
    check("P.inputs include the leaves",
          {"Revenue", "Expenses"} <= {r["concept"] for r in out["inputs"]},
          str([r["concept"] for r in out["inputs"]]))
    check("P.what/how answer the student",
          "Profit" in str(out.get("what", ""))
          and bool(out.get("how")) and "—" not in str(out.get("how")),
          f"{out.get('what')} | {out.get('how')}")

    # ---- evidence preservation (area 17) --------------------------------
    doc1 = {"document_name": "AR2025.pdf", "page": "42",
            "facts": {"Net Profit": 200, "Equity": 1000}}
    out = verify_maths_answer("ROE", documents=[doc1])
    check("Q.tier-1 document -> DERIVED result",
          out["status"] == DERIVED and out["display_value"] == "20.00%",
          f"{out['status']} {out['display_value']}")
    doc_page = any(r.get("page") == "42"
                   and "AR2025.pdf" in str(r.get("document"))
                   for r in out.get("inputs", []))
    check("Q.document + page preserved in lineage", doc_page,
          str(out.get("inputs"))[:200])

    doc2 = {"document_name": "Appendix-B.pdf", "page": "9",
            "tier": "APPENDIX",
            "facts": {"Net Profit": 200, "Equity": 1000}}
    out = verify_maths_answer("ROE", documents=[doc2])
    check("Q.tier-2 appendix accepted -> DERIVED",
          out["status"] == DERIVED, f"{out['status']} {out['display_value']}")

    # ---- tier-4 / forbidden sources fail closed (area 18) ---------------
    doc_web = {"document_name": "somewebsite.html", "page": "1",
               "tier": "WEB",
               "facts": {"Net Profit": 200, "Equity": 1000}}
    out = verify_maths_answer("ROE", documents=[doc_web])
    check("R.tier-4 document is never used as evidence",
          out["status"] == BLOCKED and out["value"] is None,
          f"{out['status']} {out['value']}")

    # ---- conflicting evidence stays separately traceable ---------------
    doc_a = {"document_name": "AR2024.pdf", "page": "40",
             "facts": {"Revenue": 1000}}
    doc_b = {"document_name": "AR2024.pdf", "page": "41",
             "facts": {"Revenue": 1200}}
    out = verify_maths_answer("Profit Margin", facts={"Profit": 200},
                              documents=[doc_a, doc_b])
    check("R.conflicting sources -> REVIEW_REQUIRED",
          out["status"] == REVIEW_REQUIRED, out["status"])
    check("R.conflict never silently resolved",
          out["value"] is None and out["verdict"] == "REFUSED",
          f"{out['value']} {out['verdict']}")

    # conflicting derivations through the solver
    out = verify_maths_answer(
        "Net Profit",
        facts={"ROE": 20, "Equity": 1000, "EPS": 1, "Shares Outstanding": 100},
    )
    check("R.conflicting derivations -> REVIEW_REQUIRED",
          out["status"] == REVIEW_REQUIRED
          and "never silently choose" in str(out.get("why_not", "")),
          f"{out['status']} {out.get('why_not')}")

    # ---- no fabricated values (area 19) --------------------------------
    for case in FYJC_MATHS_CASES:
        if case["expect_verdict"] == "REFUSED":
            out = verify_maths_answer(
                case["metric"], facts=case.get("facts"),
                text=case.get("text"),
                student_answer=case.get("student_answer"),
            )
            check(f"S.{case['id']} refusal shows no fake value",
                  out["value"] is None
                  and str(out.get("display_value")) in ("—", "None", ""),
                  f"{out['value']} {out.get('display_value')}")


# ---------------------------------------------------------------------------
# Part T - repeated-run determinism (area 20)
# ---------------------------------------------------------------------------


def _run_all_outcomes():
    maths = []
    for case in FYJC_MATHS_CASES:
        out = verify_maths_answer(
            case["metric"], facts=case.get("facts"), text=case.get("text"),
            student_answer=case.get("student_answer"),
        )
        maths.append({
            "id": case["id"], "verdict": out["verdict"],
            "status": out["status"], "display": out.get("display_value"),
            "value": out.get("value"),
        })
    acc = []
    for case in FYJC_ACCOUNTING_CASES:
        out = classify_transaction(case["question"])
        acc.append({
            "id": case["id"], "status": out["status"],
            "debit": sorted(l["account"] for l in out["debit_lines"]),
            "credit": sorted(l["account"] for l in out["credit_lines"]),
            "rule_key": out.get("rule_key"),
        })
    jrn = []
    for case in FYJC_JOURNAL_CASES:
        out = verify_journal_entry(case["description"], case["entry"])
        jrn.append({"id": case["id"], "verdict": out["verdict"],
                    "discrepancy": out.get("discrepancy")})
    ledger = post_ledger(FYJC_LEDGER_ENTRIES)
    tb = build_trial_balance(FYJC_LEDGER_ENTRIES)
    return stable({"maths": maths, "acc": acc, "jrn": jrn,
                   "ledger": ledger, "tb": tb})


def test_t_determinism_body():
    first = _run_all_outcomes()
    for i in range(3):
        again = _run_all_outcomes()
        check(f"T.run {i + 2} identical to run 1", again == first,
              "outcomes diverged")
    check("T.full dataset deterministic over 4 runs", True, "identical")
    # spot determinism of an individual metric
    a = verify_maths_answer("Profit Margin",
                            facts={"Profit": 200, "Revenue": 1000},
                            student_answer=20)
    b = verify_maths_answer("Profit Margin",
                            facts={"Profit": 200, "Revenue": 1000},
                            student_answer=20)
    check("T.single metric outcome deterministic",
          stable(a) == stable(b), "diverged")


# ---------------------------------------------------------------------------
# Part U - regression against all 12A-12F suites (area 21)
# ---------------------------------------------------------------------------

_REGRESSION_SUITES = [
    "fte_maths_core_test.py",              # 12A (202 checks)
    "fte_maths_reasoning_test.py",         # 12B (123 checks)
    "fte_maths_decision_graph_test.py",    # 12C (99 checks)
    "fte_maths_production_hardening_test.py",  # 12D (68 checks)
    "fte_maths_production_integration_test.py",  # 12E (92 checks)
    "fte_maths_student_production_gate_test.py",  # 12F (152 checks)
    "fte_formula_engine_test.py",
    "fte_formula_engine_cpp_test.py",
]


def test_u_regression():
    print("PART U - REGRESSION AGAINST 12A-12F SUITES")

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    for suite in _REGRESSION_SUITES:
        path = os.path.join(scripts_dir, suite)
        try:
            proc = subprocess.run([sys.executable, path],
                                  capture_output=True, text=True,
                                  timeout=600, cwd=os.getcwd())
        except subprocess.TimeoutExpired:
            check(f"U.{suite} completed", False, "timed out")
            continue
        tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-200:]
        ok = proc.returncode == 0
        check(f"U.{suite} passes", ok, tail.replace("\n", " | ")[-300:])


# ---------------------------------------------------------------------------
# Part V - student acceptance workflow (section I)
# ---------------------------------------------------------------------------


def test_v_acceptance():
    print("PART V - STUDENT ACCEPTANCE WORKFLOW")

    for case in FYJC_ACCEPTANCE_CASES:
        cid = case["id"]
        kind = case.get("kind")
        if kind is None or kind == "maths":
            out = verify_maths_answer(
                case["metric"], text=case.get("text"),
                student_answer=case.get("student_answer"),
            )
            if case["expect_verdict"] in ("CORRECT", "INCORRECT"):
                check(f"V.{cid} student answer verdict",
                      out["verdict"] == case["expect_verdict"],
                      f"{out['verdict']} {out.get('mismatch')}")
                check(f"V.{cid} display",
                      out["display_value"] == case["expect_display"],
                      out["display_value"])
                for field in ("what", "how", "inputs", "where", "status",
                              "why_not", "next_action"):
                    check(f"V.{cid} payload exposes {field}", field in out,
                          f"missing {field}")
            else:
                check(f"V.{cid} refused",
                      out["verdict"] == "REFUSED"
                      and out["status"] == case["expect_status"],
                      f"{out['verdict']} {out['status']}")
                if case.get("expect_next_action_mentions"):
                    check(f"V.{cid} next action tells the student what to do",
                          case["expect_next_action_mentions"]
                          in str(out.get("next_action", "")),
                          str(out.get("next_action")))
                check(f"V.{cid} why_not is student-readable",
                      bool(out.get("why_not")), str(out.get("why_not")))
        elif kind == "journal":
            out = classify_transaction(case["question"])
            check(f"V.{cid} classification verified",
                  out["status"] == VERIFIED
                  and {l["account"] for l in out["debit_lines"]}
                  == case["expect_debit"]
                  and {l["account"] for l in out["credit_lines"]}
                  == case["expect_credit"],
                  str(out))
            check(f"V.{cid} golden rule explanation readable",
                  bool(out.get("rule"))
                  and "debit" in out["rule"].lower()
                  and "credit" in out["rule"].lower(),
                  str(out.get("rule"))[:160])
        elif kind == "journal_verify":
            out = verify_journal_entry(case["description"], case["entry"])
            check(f"V.{cid} student's journal {case['expect_verdict']}",
                  out["verdict"] == case["expect_verdict"],
                  f"{out['verdict']} {out.get('why_not')}")
            if case["expect_verdict"] == "INCORRECT":
                check(f"V.{cid} explains what differs",
                      bool(out.get("why_not"))
                      and "Expected" in str(out.get("why_not", "")),
                      str(out.get("why_not"))[:200])

    # a student can answer every acceptance question from the payload
    out = verify_maths_answer(
        "Current Ratio",
        text="Current Assets: Rs.5,00,000\nCurrent Liabilities: Rs.2,50,000",
        student_answer=2,
    )
    check("V.checklist maps onto the outcome",
          all(item.get("payload_field") in out
              for item in FYJC_MATHS_CHECKLIST),
          str([i["payload_field"] for i in FYJC_MATHS_CHECKLIST]))
    check("V.checklist is student-facing",
          len(FYJC_MATHS_CHECKLIST) == 7, str(len(FYJC_MATHS_CHECKLIST)))

    # the refusal explains WHAT is missing and WHY it is required
    out = verify_maths_answer("Current Ratio",
                              text="Current Assets: Rs.5,00,000")
    check("V.blocked explains what is missing",
          "Current Liabilities" in str(out.get("why_not", "")),
          str(out.get("why_not"))[:200])
    check("V.blocked explains what the student can provide",
          "Upload the relevant page or enter the verified value"
          in str(out.get("next_action", "")),
          str(out.get("next_action"))[:200])


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def verdict():
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("=" * 72)
    print(f"RESULT: {passed}/{total} checks passed")
    if FAILURES:
        print("FAILED CHECKS:")
        for f in FAILURES[:50]:
            print(f"  - {f}")
        print("=" * 72)
        print("SPRINT 13 FAIL - FYJC LAYER NOT READY")
        return 1
    print("=" * 72)
    print("SPRINT 13 GATE: ALL CHECKS COMPLETE")
    if engine_available():
        print("13 PASS - FYJC STUDENT READINESS VERIFIED "
              "(C++ mathematical authority active)")
    else:
        print("13 CONDITIONAL PASS - FYJC REASONING VERIFIED "
              "(C++ authority not deployed - strict path BLOCKs)")
    return 0


def main():
    test_a_classification()
    test_b_accounts()
    test_cde_golden_rules_journal()
    test_fghi_ledger_trial()
    test_jkl_refusals()
    test_m_authority()
    test_no_maths()
    test_pqrs_evidence()
    test_t_determinism_body()
    test_u_regression()
    test_v_acceptance()
    return verdict()


if __name__ == "__main__":
    sys.exit(main())
