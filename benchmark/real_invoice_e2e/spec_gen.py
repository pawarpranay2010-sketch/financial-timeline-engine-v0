"""Platrixa Real Invoice E2E Benchmark — corpus generator (spec, deterministic).

Run ONCE from the repository root:

    python3 benchmark/real_invoice_e2e/spec_gen.py

Writes (all deterministic, seeded; no wall-clock dependence):
    benchmark/real_invoice_e2e/manifest.json
    benchmark/real_invoice_e2e/corpus.json
    benchmark/real_invoice_e2e/capability_snapshot.json   (from LIVE registries)
    benchmark/real_invoice_e2e/gold/<invoice_id>.json     (ground truth per case)
    benchmark/real_invoice_e2e/expected/<invoice_id>.json (candidate + contract check)

Ground truth is derived from the LIVE production code only:

    backend/maths/schema_verifier.py        — exact 18-field contract + validator
    backend/maths/fyjc_contract.py          — LEGACY_FIELDS / EXPANDED_FIELDS / enums
    backend/maths/fyjc_grounding_gate.py    — ExpandedGroundingGate (build-time check)
    backend/maths/fyjc_orchestration.py     — authority registry (authority_report)
    backend/maths/formula_registry.py       — formula authority registry
    backend/maths/finance_knowledge.py      — finance knowledge authority
    backend/maths/capability_registry.py    — live capability registry

Nothing here claims a capability the live registries do not expose. Expected
statuses/authorities are recorded as gold EXPECTATIONS to be compared against
actual Kernel behaviour by the harness — they are never treated as measured.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from pathlib import Path

# benchmark/real_invoice_e2e/ is two levels below the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- LIVE production interfaces (imports fail loudly if the contract drifts) --
from backend.maths.fyjc_orchestration import AUTHORITIES, authority_report
from backend.maths.formula_registry import default_registry as build_formula_registry
from backend.maths.finance_knowledge import build_knowledge_authority
from backend.maths.fyjc_contract import (
    ALL_VALID_FIELDS,
    EXPANDED_FIELDS,
    LEGACY_FIELDS,
    VALID_PAYMENT_METHODS,
    VALID_SCOPE_FLAGS,
    VALID_SAFETY_FLAGS,
    VALID_TRANSACTION_TYPES,
)
from backend.maths.schema_verifier import (
    VALID_AMBIG_FLAGS,
    VALID_PM_ENUM,
    VALID_SAFETY_FLAGS as SV_SAFETY_FLAGS,
    VALID_SCOPE_FLAGS as SV_SCOPE_FLAGS,
    VALID_TX_ENUM,
    StructuredInterpretationValidator,
)
from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate
from backend.maths import capability_registry

# --------------------------------------------------------------------------- #
# Canonical field order: legacy 7 + expanded 11 (= the live 18-field contract).
# --------------------------------------------------------------------------- #
LEGACY_ORDER = [
    "transaction_type", "parties", "amounts", "payment_method",
    "references", "ambiguities", "grounding",
]
EXPANDED_ORDER = [
    "transaction_type_enum", "payment_method_enum", "ambiguity_flags",
    "referenced_transaction_index", "referenced_party", "referenced_amount",
    "field_confidences", "overall_confidence", "suggested_status",
    "safety_flags", "scope_flags",
]
FIELD_NAMES: list[str] = LEGACY_ORDER + EXPANDED_ORDER

_SEED = 20260901
random.seed(_SEED)
DATE_CLEAN = "2026-08-20"          # deterministic; benchmark never uses wall-clock
DUE_CLEAN = "2026-09-04"           # DATE_CLEAN + 15 days (Net 15)
COHORT = "PLX2"

PM_CHOICES = sorted(VALID_PM_ENUM - {"UNKNOWN"})
CURRENCY = "INR"

OUTPUT_DIR = PROJECT_ROOT / "benchmark" / "real_invoice_e2e"

# --------------------------------------------------------------------------- #
# Scenario → live-authority expectation map. Only authorities that exist in the
# live AUTHORITIES registry are referenced; expectations are CHECKED, not asserted.
# --------------------------------------------------------------------------- #
SCENARIO_AUTHORITY = {
    "simple_purchase": "COMMERCIAL_CORE",
    "multi_line_purchase": "COMMERCIAL_CORE",
    "gst_purchase": "GST_AUTHORITY",
    "sale": "COMMERCIAL_CORE",
    "payment_settlement": "SETTLEMENT_AUTHORITY",
    "credit_note_return": "DISCREPANCY_AUTHORITY",
    "expense_bank_fee": "COMMERCIAL_CORE",
    "refund": "DISCREPANCY_AUTHORITY",
    "multi_page_table_heavy": "COMMERCIAL_CORE",
    "ambiguous_incomplete": "DISCREPANCY_AUTHORITY",
    "defensive_conflicting": "CONSIGNMENT_AUTHORITY",
}

# --------------------------------------------------------------------------- #
# Deterministic per-scenario amounts (no RNG drift between runs).
# --------------------------------------------------------------------------- #
def _amt(scenario: str, i: int) -> float:
    return round(10000.0 + 500.0 * i + (250.0 if scenario in ("gst_purchase", "multi_line_purchase") else 0.0), 2)


def _id(scenario: str, idx: int) -> str:
    # Sprint INV-ROLE: multi_line_purchase and multi_page_table_heavy
    # BOTH truncate to the prefix 'MULT', producing duplicate invoice
    # IDs (28 unique for 30 cases; the later case's gold/expected files
    # silently overwrote the earlier). Scenarios sharing a first segment
    # are distinguished by that segment + their second segment
    # ('MLIN' vs 'MPAG'); every other scenario keeps its historical ID.
    prefix = scenario[:4].upper()
    parts = scenario.split("_")
    if len(parts) > 1 and parts[0] == "multi":
        prefix = (parts[0][:1] + parts[1][:3]).upper()
    return f"INV-{COHORT}-{prefix}-{idx:03d}"


def _scenario_index(scenario: str, i: int) -> str:
    return f"{scenario[:2].upper()}-{i:02d}"


def _expected_status(scenario: str) -> str:
    if scenario in {"ambiguous_incomplete", "defensive_conflicting"}:
        return "REVIEW_REQUIRED"
    return "VERIFIED"


def _expected_authority(scenario: str) -> str:
    return SCENARIO_AUTHORITY[scenario]


# --------------------------------------------------------------------------- #
# Invoice text — the ONLY input the model provider sees. Every candidate value
# is a verbatim substring of this text so the LIVE grounding gate can ground it.
# --------------------------------------------------------------------------- #
def _invoice_text(case: dict) -> str:
    lines = [
        f"TAX INVOICE {case['bill_reference']}",
        f"Invoice Date: {case['invoice_date']}",
        f"Due Date: {case['due_date']}",
        f"From (Buyer): {case['party_payer']}",
        f"To (Seller): {case['party_payee']}",
        f"Transaction: {case['tx_evidence']}",
        f"Description: {case['description']}",
        f"Account Code: {case['account_code']}",
        f"Net Amount: {case['amount_net']}",
        f"GST Amount: {case['amount_tax']}",
        f"Total Amount: {case['amount_total']}",
        f"Currency: {CURRENCY}",
        f"Payment Mode: {case['pm_evidence']}",
        f"Payment Terms: {case['payment_terms']}",
        f"Reference No: {case['reference_number']}",
        f"Doc Ref: {case['doc_ref']}",
    ]
    extra = case.get("extra_lines") or []
    lines.extend(extra)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Base builder: every scenario varies amounts/parties/references but keeps the
# same grounded-text discipline.
# --------------------------------------------------------------------------- #
def _base(scenario: str, i: int, *, tx: str, tx_evidence: str,
          pm: str, desc: str, account: str, terms: str,
          net: float, tax_rate: float) -> dict:
    i2 = _scenario_index(scenario, i)
    tax = round(net * tax_rate, 2)
    total = round(net + tax, 2)
    payer = f"B-{i:03d}-TEST-BUYER"
    payee = f"S-{i:03d}-TEST-SELLER"
    return {
        "invoice_id": _id(scenario, i),
        "scenario": scenario,
        "source_type": "digital",
        "party_payer": payer,
        "party_payee": payee,
        "invoice_date": DATE_CLEAN,
        "due_date": DUE_CLEAN,
        "bill_reference": f"BILL-{i2}",
        "reference_number": f"REF-{i2}",
        "doc_ref": f"DOC-{i2}",
        "account_code": account,
        "description": desc,
        "amount_net": f"{net:,.2f}",
        "amount_tax": f"{tax:,.2f}",
        "amount_total": f"{total:,.2f}",
        "payment_terms": terms,
        "tx_evidence": tx_evidence,
        "pm_evidence": pm,
        "pm": pm,
        "tx": tx,
    }


def _purchase_like(scenario: str, i: int, *, tx_evidence: str = "Goods purchased from seller as per purchase order") -> dict:
    return _base(
        scenario, i,
        tx="PURCHASE", tx_evidence=tx_evidence,
        pm="NEFT", desc="Benchmark synthetic purchase invoice",
        account="4000-00", terms="Net 15",
        net=_amt(scenario, i), tax_rate=0.18,
    )


def _sale(i: int) -> dict:
    c = _base(
        "sale", i,
        tx="SALE", tx_evidence="Goods sold and delivered to buyer",
        pm="UPI", desc="Benchmark synthetic sale invoice",
        account="4001-00", terms="Net 15",
        net=_amt("sale", i), tax_rate=0.12,
    )
    return c


def _payment_settlement(i: int) -> dict:
    return _base(
        "payment_settlement", i,
        tx="PAYMENT", tx_evidence="Payment paid in full settlement of bill",
        pm="CHEQUE", desc="Benchmark synthetic settlement of supplier bill",
        account="5000-00", terms="Full settlement",
        net=_amt("payment_settlement", i), tax_rate=0.0,
    )


def _credit_note(i: int) -> dict:
    c = _base(
        "credit_note_return", i,
        tx="RETURN_OUT", tx_evidence="Goods returned to seller under purchase return",
        pm="CREDIT", desc="Benchmark synthetic credit note for purchase return",
        account="4002-00", terms="Return",
        net=_amt("credit_note_return", i), tax_rate=0.0,
    )
    c["extra_lines"] = ["Credit Note against original purchase bill"]
    return c


def _expense_bank_fee(i: int) -> dict:
    c = _base(
        "expense_bank_fee", i,
        tx="EXPENSE", tx_evidence="Bank charges paid as debited by bank",
        pm="BANK", desc="Benchmark synthetic bank fee expense",
        account="6000-00", terms="Net 15",
        net=_amt("expense_bank_fee", i), tax_rate=0.0,
    )
    c["extra_lines"] = ["paid rent equivalent period fee: none"]
    return c


def _refund(i: int) -> dict:
    c = _base(
        "refund", i,
        tx="RETURN_OUT", tx_evidence="Purchase return processed and refunded by supplier",
        pm="NEFT", desc="Benchmark synthetic supplier refund after purchase return",
        account="4003-00", terms="Net 15",
        net=_amt("refund", i), tax_rate=0.0,
    )
    c["extra_lines"] = ["Original purchase bill referenced for this return"]
    return c


def _multi_page(i: int) -> dict:
    c = _purchase_like("multi_page_table_heavy", i,
                       tx_evidence="Goods purchased from seller across attached pages")
    c["extra_lines"] = [
        "Page 2: continuation of line items",
        f"Carried-forward Total Amount on page 2: {c['amount_total']}",
    ]
    c["page_count"] = 2
    return c


def _ambiguous(i: int) -> dict:
    c = _base(
        "ambiguous_incomplete", i,
        tx="UNKNOWN", tx_evidence="Service billed as per discussion",
        pm="UNKNOWN", desc="Benchmark synthetic incomplete invoice",
        account="TBD", terms="TBD",
        net=0.0, tax_rate=0.0,
    )
    # Deliberately incomplete: payer unknown, no itemised net/tax, mode absent.
    c["party_payer"] = ""
    c["amount_net"] = "0.00"
    c["amount_tax"] = "0.00"
    c["pm_evidence"] = "not specified"
    c["extra_lines"] = ["Total Amount payable: 1,234.56"]
    c["amount_total"] = "1,234.56"
    return c


def _defensive(i: int) -> dict:
    c = _base(
        "defensive_conflicting", i,
        tx="PURCHASE", tx_evidence="Goods purchased from seller under consignment terms",
        pm="CASH", desc="Benchmark synthetic conflicting consignment invoice",
        account="4000-00", terms="Net 15",
        net=_amt("defensive_conflicting", i), tax_rate=0.0,
    )
    c["extra_lines"] = [
        "Consignor: S-001-TEST-SELLER (agent holds goods on our behalf)",
        "Consignee: B-001-TEST-BUYER",
        "Ownership has not transferred; this document is NOT a sale",
    ]
    return c


SCENARIOS = [
    ("simple_purchase", 4, lambda i: _purchase_like("simple_purchase", i)),
    ("multi_line_purchase", 4, lambda i: _purchase_like("multi_line_purchase", i, tx_evidence="Goods purchased from seller, three line items")),
    ("gst_purchase", 4, lambda i: _purchase_like("gst_purchase", i, tx_evidence="GST taxable goods purchased from seller")),
    ("sale", 3, _sale),
    ("payment_settlement", 3, _payment_settlement),
    ("credit_note_return", 3, _credit_note),
    ("expense_bank_fee", 2, _expense_bank_fee),
    ("refund", 2, _refund),
    ("multi_page_table_heavy", 2, _multi_page),
    ("ambiguous_incomplete", 2, _ambiguous),
    ("defensive_conflicting", 1, _defensive),
]

# --------------------------------------------------------------------------- #
# Candidate construction — the EXACT live 18-field contract, filled from the
# case's own deterministic values (values the gate can ground from the text).
# --------------------------------------------------------------------------- #
def _ambiguity_flags_for(case: dict) -> list[str]:
    flags: list[str] = []
    if case["scenario"] == "ambiguous_incomplete":
        flags = ["MISSING_PAYMENT_MODE", "MISSING_AMOUNT", "MISSING_PARTY"]
    elif case["scenario"] == "defensive_conflicting":
        flags = ["CONFLICTING_INFORMATION"]
    return flags


def _safety_flags_for(case: dict) -> list[str]:
    if case["scenario"] == "ambiguous_incomplete":
        return ["MISSING_REQUIRED_FIELDS", "UNRESOLVED_FIELDS"]
    return []


def _candidate_for(case: dict) -> dict:
    parties = [p for p in (case["party_payer"], case["party_payee"]) if p]
    amounts = [{"value": case["amount_total"], "source": "explicit"}]
    references = [case["bill_reference"], case["reference_number"], case["doc_ref"]]
    ambiguities = _ambiguity_flags_for(case)
    amb = "NONE" if not ambiguities else ambiguities[0]
    return {
        # legacy 7
        "transaction_type": case["tx"],
        "parties": parties,
        "amounts": amounts,
        "payment_method": case["pm"],
        "references": references,
        "ambiguities": ambiguities if ambiguities else ["NONE"],
        "grounding": {"level": "GROUNDED"},
        # expanded 11
        "transaction_type_enum": case["tx"],
        "payment_method_enum": case["pm"],
        "ambiguity_flags": ambiguities,
        "referenced_transaction_index": None,
        "referenced_party": None,
        "referenced_amount": None,
        "field_confidences": [],
        "overall_confidence": "0.50" if case["scenario"] in ("ambiguous_incomplete", "defensive_conflicting") else "0.85",
        "suggested_status": "REVIEW_REQUIRED",  # NEVER VERIFIED (gate Rule 0)
        "safety_flags": _safety_flags_for(case),
        "scope_flags": ["GST_SPECIFIC"] if case["scenario"] == "gst_purchase" else [],
    }


# --------------------------------------------------------------------------- #
# Build-time contract verification: every candidate MUST pass the LIVE
# StructuredInterpretationValidator; every clean case MUST be grounding-safe.
# This is the corpus's own quality gate — a generator bug fails loudly here.
# --------------------------------------------------------------------------- #
def _verify_contract(case: dict, candidate: dict, text: str) -> dict:
    validator = StructuredInterpretationValidator()
    report = validator.validate(copy.deepcopy(candidate), allow_expanded=True)
    gate = ExpandedGroundingGate()
    ground = gate.ground(copy.deepcopy(candidate), text)
    return {
        "schema_valid": report.valid,
        "schema_status": report.status.value,
        "schema_errors": [e.to_dict() for e in report.errors],
        "grounding_safe_for_kernel": ground.safe_for_kernel,
        "grounding_issues": list(ground.issues),
    }


# --------------------------------------------------------------------------- #
# Capability snapshot — derived from LIVE registries at build time.
# --------------------------------------------------------------------------- #
def _capability_snapshot() -> dict:
    formula_reg = build_formula_registry()
    knowledge = build_knowledge_authority()
    caps = capability_registry.summary()
    return {
        "captured_from": "live code at corpus build time",
        "accounting_kernel_authorities": {
            a["authority"]: {"implemented": a["implemented"], "name": a["name"]}
            for a in authority_report()
        },
        "formula_authority": {
            "formula_ids": formula_reg.all_ids(),
            "targets": formula_reg.targets(),
        },
        "finance_knowledge": {
            "counts_by_status": knowledge.counts_by_status(),
        },
        "capability_registry_summary": caps,
        "schema_contract": {
            "legacy_fields": sorted(LEGACY_FIELDS),
            "expanded_fields": sorted(EXPANDED_FIELDS),
            "all_valid_fields": sorted(ALL_VALID_FIELDS),
            "valid_tx_enum": sorted(VALID_TX_ENUM),
            "valid_pm_enum": sorted(VALID_PM_ENUM),
            "valid_ambiguity_flags": sorted(VALID_AMBIG_FLAGS),
            "valid_safety_flags": sorted(SV_SAFETY_FLAGS),
            "valid_scope_flags": sorted(SV_SCOPE_FLAGS),
            "valid_transaction_types_fyjc": sorted(VALID_TRANSACTION_TYPES),
            "valid_payment_methods_fyjc": sorted(VALID_PAYMENT_METHODS),
        },
        "implementation_refs": {
            "schema": "backend/maths/schema_verifier.py",
            "contract": "backend/maths/fyjc_contract.py",
            "grounding_gate": "backend/maths/fyjc_grounding_gate.py",
            "kernel": "backend/kernel/kernel.py",
            "authorities": "backend/maths/fyjc_orchestration.py",
            "formulas": "backend/maths/formula_registry.py",
            "knowledge": "backend/maths/finance_knowledge.py",
            "capabilities": "backend/maths/capability_registry.py",
        },
    }


# --------------------------------------------------------------------------- #
def generate() -> dict:
    cases: list[dict] = []
    for scenario, count, builder in SCENARIOS:
        for i in range(1, count + 1):
            case = builder(i)
            case["expected_status"] = _expected_status(scenario)
            case["expected_authority"] = _expected_authority(scenario)
            case["difficulty"] = ("hard" if scenario in
                                  ("ambiguous_incomplete", "defensive_conflicting", "multi_page_table_heavy")
                                  else "medium" if scenario in ("gst_purchase", "multi_line_purchase", "payment_settlement", "credit_note_return")
                                  else "easy")
            cases.append(case)

    for case in cases:
        text = _invoice_text(case)
        candidate = _candidate_for(case)
        contract = _verify_contract(case, candidate, text)
        case["text_for_model"] = text
        case["candidate_18"] = candidate
        case["contract_check"] = contract
        # Build-time gate: a clean scenario whose candidate cannot pass the
        # live validator/grounding is a GENERATOR bug — fail loudly, never
        # ship a silently-broken corpus.
        if not contract["schema_valid"]:
            raise SystemExit(
                f"spec_gen: candidate fails live schema for {case['invoice_id']}:\n"
                f"{contract['schema_errors']}"
            )
        if case["expected_status"] == "VERIFIED" and not contract["grounding_safe_for_kernel"]:
            raise SystemExit(
                f"spec_gen: candidate not grounding-safe for {case['invoice_id']}:\n"
                f"{contract['grounding_issues']}"
            )

    manifest = {
        "corpus": "platrixa_real_invoice_e2e",
        "generation_seed": _SEED,
        "creation_version": "2.0.0",
        "total_cases": len(cases),
        "scenario_counts": {k: sum(1 for c in cases if c["scenario"] == k)
                            for k, _, _ in SCENARIOS},
        "source_type_counts": {"digital": len(cases)},
        "expected_statuses": {
            s: sum(1 for c in cases if c["expected_status"] == s)
            for s in ("VERIFIED", "REVIEW_REQUIRED", "UNSUPPORTED")
        },
        "note": (
            "Spec-only corpus: PDFs/images are intentionally NOT rendered "
            "(no PDF writer available in this sandbox; pypdf is read-only and "
            "no OCR engine is installed). OCR/scanned paths are measured only "
            "in a GPU/OCR-enabled environment and reported NOT_MEASURED here."
        ),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (OUTPUT_DIR / "capability_snapshot.json").write_text(
        json.dumps(_capability_snapshot(), indent=2) + "\n")

    gold_dir = OUTPUT_DIR / "gold"
    exp_dir = OUTPUT_DIR / "expected"
    gold_dir.mkdir(exist_ok=True)
    exp_dir.mkdir(exist_ok=True)

    current_ids = {c["invoice_id"] for c in cases}

    # Sprint INV-ROLE: the pre-fix generator wrote duplicate IDs (later
    # cases silently overwrote earlier ones). Self-heal the output dirs:
    # any gold/expected file that is not a current case is a stale
    # artifact of an older run and is pruned so the corpus on disk is
    # exactly the generated corpus.
    for d in (gold_dir, exp_dir):
        for stale in d.glob("*.json"):
            if stale.stem not in current_ids:
                stale.unlink()

    corpus_cases = []
    for case in cases:
        gold = {
            "invoice_id": case["invoice_id"],
            "scenario": case["scenario"],
            "source_type": case["source_type"],
            "difficulty": case["difficulty"],
            "expected_transaction_type": case["tx"],
            "expected_status": case["expected_status"],
            "expected_authority": case["expected_authority"],
            "expected_amount_total": case["amount_total"],
            "expected_party_payer": case["party_payer"],
            "expected_party_payee": case["party_payee"],
            "expected_payment_method": case["pm"],
        }
        (gold_dir / f"{case['invoice_id']}.json").write_text(json.dumps(gold, indent=2) + "\n")
        (exp_dir / f"{case['invoice_id']}.json").write_text(json.dumps(
            {"candidate_18": case["candidate_18"],
             "contract_check": case["contract_check"],
             "text_for_model": case["text_for_model"]}, indent=2) + "\n")
        corpus_cases.append({
            "invoice_id": case["invoice_id"],
            "scenario": case["scenario"],
            "source_type": case["source_type"],
            "difficulty": case["difficulty"],
            "page_count": case.get("page_count", 1),
            "text_for_model": case["text_for_model"],
            "candidate_18": case["candidate_18"],
            "expected_status": case["expected_status"],
            "expected_transaction_type": case["tx"],
            "expected_authority": case["expected_authority"],
            "expected_amount_total": case["amount_total"],
        })

    corpus = {
        "manifest": manifest,
        "cases": corpus_cases,
    }
    (OUTPUT_DIR / "corpus.json").write_text(json.dumps(corpus, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    gen = generate()
    print(json.dumps({
        "total_cases": gen["total_cases"],
        "scenario_counts": gen["scenario_counts"],
        "expected_statuses": gen["expected_statuses"],
        "build_time_contract_gate": "all candidates passed live schema + grounding checks",
    }, indent=2))
