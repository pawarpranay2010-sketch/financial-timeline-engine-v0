"""
Financial Timeline Engine
Sprint 15G - Deterministic Financial Engineering Layer
backend/maths/fyjc_15g.py

HFT + Private Equity engineering hardening over the existing FYJC reasoning
pipeline (Sprint 15E + 15F are the untouched baseline). This layer makes the
pipeline:

    deterministic -> replayable -> canonically normalized -> traceable
    -> auditable -> discrepancy-aware

Deliverables in this module:

  1. REPLAY IR (HFT principle)
       build_replay_record()  - capture every resolved FYJC reasoning case as
                                a stable, versioned, deterministic record:
                                Raw Input -> Extracted Facts -> Canonical IR
                                -> Requested Intent -> Rules/Formula IDs ->
                                Dependencies -> Calculation Plan -> C++
                                Authority -> Verification -> Final Result.
       serialize_replay()     - deterministic serialization (no timestamps,
                                no random ids; Decimals -> canonical strings;
                                sort_keys + stable separators).
       deserialize_replay()   - round-trip loader.
       ir_to_journal_lines()  - rebuild journal lines from the canonical IR
                                WITHOUT the natural-language interpretation.
       replay_execute()       - deterministic replay executor: re-derives
                                journal lines from the IR, re-posts the
                                ledger, re-builds the trial balance,
                                re-verifies arithmetic, re-validates the
                                plan and the final result.

  2. CANONICAL NORMALIZATION (PE principle)
       canonicalize_bk()      - equivalent textbook wording collapses onto
                                ONE canonical representation:
                                canonical transaction id, canonical account
                                ids (ACCOUNT:/PARTY:), canonical rule ids
                                (REAL/PERSONAL/NOMINAL), canonical formula
                                ids (the registered FYJC commercial-
                                arithmetic relationships). Original wording
                                is preserved separately for auditability.
                                Insufficient confidence -> REVIEW_REQUIRED;
                                FT-E never guesses.
       canonical_equivalent() - two wordings converge to the same IR.

  3. LINEAGE PASSPORT
       build_lineage()        - machine-readable chain answering: what FT-E
                                received, understood, which canonical
                                concepts/rules/formulas were selected, which
                                values were supplied vs calculated, what was
                                sent to C++, what C++ verified, and why the
                                result is VERIFIED. A supplied fact never
                                appears as a calculated value.

  4. IMMUTABLE AUDIT RECORD
       AuditLedger / append_audit_record() / audit_snapshot()
                                - append-only, versioned audit trail. No
                                in-place mutation of historical records; a
                                registry/rule/reasoning version change
                                produces a NEW record. No secrets, no
                                personal information.

  5. DISCREPANCY DETECTION
       validate_journal()      - Total Debit == Total Credit; structural
                                checks (missing/duplicate lines, account on
                                both sides, invented account).
       validate_ledger()       - per-account Opening + Debit - Credit ==
                                Closing; ledger totals.
       validate_trial_balance()- Total Debit == Total Credit; row sanity.
       validate_pipeline()     - whole-result validation (journal + ledger +
                                TB + provenance/formula invariants).
       A discrepancy is NEVER silently repaired: the validator returns an
       explicit state (OK / REVIEW_REQUIRED / BLOCKED) with machine-readable
       discrepancy codes.

  6. C++ AUTHORITY PERFORMANCE (HFT-inspired)
       CppAuthorityWorker      - persistent `--worker` transport for the
                                compiled C++ authority (one process, one
                                JSON document per line) with an exact
                                equivalence guarantee against the one-shot
                                CLI path. Python orchestration is never
                                rewritten into C++; C++ remains the
                                mathematical authority.
       cpp_authority_execute() - optimized execution + equivalence check.

Determinism contract: identical input IR + registry version + engine version
+ execution configuration -> byte-identical serialized output. No hidden
randomness anywhere in this module.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from backend.formula_engine_cpp import (
    _STATUS_MAP,
    binary_path,
    cpp_calculate,
    cpp_solve_metric,
)
from backend.maths.fyjc_accounting import (
    ACCOUNT_ROLES,
    build_trial_balance,
    named_assets,
    post_ledger,
    verify_arithmetic,
)
from backend.maths.fyjc_bk_15f import BK_PATTERN_LIBRARY
from backend.maths.fyjc_bk_reasoning import (
    CLASS_PERSONAL,
    NOT_SUPPORTED,
    TRADITIONAL_GOLDEN_RULES,
    _EXPENSE_ACCOUNT_WORDS,
    _INCOME_ACCOUNT_WORDS,
    _TRADITIONAL_OVERRIDES,
    _requested_operation,
    _resolve_side_specs,
    _split_transactions,
    classify_bk_type,
    reason_bk_question,
    resolve_transaction_amounts,
    side_decision_for,
    traditional_class_for,
)
from backend.maths.fyjc_canonical import (
    BK_RULE_BY_CLASS,
    CANONICAL_REGISTRY,
    FYJC_FORMULA_REGISTRY,
)
from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED

# ---------------------------------------------------------------------------
# Versioning (deterministic; never timestamps)
# ---------------------------------------------------------------------------

REPLAY_SCHEMA_VERSION = "15G.1.0"
REASONING_VERSION = "15E-15F-15G.1"
# The canonical formula registry was built in Sprint 15D and has not changed
# since; the C++ FYJC registry mirrors it exactly. These are stable strings
# so a replay produced under these registries is reproducible.
REGISTRY_VERSION = "fyjc-canonical:15D.1"
FORMULA_REGISTRY_VERSION = "fyjc-formula:15D.1"
CPP_REGISTRY_VERSION = "cpp-fyjc:15D.1"

# ---------------------------------------------------------------------------
# Discrepancy vocabulary (machine-readable, never silently repaired)
# ---------------------------------------------------------------------------

DISC_JOURNAL_UNBALANCED = "JOURNAL_UNBALANCED"
DISC_MISSING_DEBIT_LINE = "MISSING_DEBIT_LINE"
DISC_MISSING_CREDIT_LINE = "MISSING_CREDIT_LINE"
DISC_MISSING_JOURNAL_LINE = "MISSING_JOURNAL_LINE"
DISC_DUPLICATE_LINE = "DUPLICATE_LINE"
DISC_ACCOUNT_BOTH_SIDES = "ACCOUNT_BOTH_SIDES"
DISC_INVENTED_ACCOUNT = "INVENTED_ACCOUNT"
DISC_LEDGER_UNBALANCED = "LEDGER_UNBALANCED"
DISC_LEDGER_ACCOUNT_INCONSISTENT = "LEDGER_ACCOUNT_INCONSISTENT"
DISC_TB_UNBALANCED = "TB_UNBALANCED"
DISC_TB_ROW_INCONSISTENT = "TB_ROW_INCONSISTENT"
DISC_FORMULA_ID_NONE_CONFIDENT = "FORMULA_ID_NONE_CONFIDENT"
DISC_CPP_RESULT_MISMATCH = "CPP_RESULT_MISMATCH"
DISC_REPLAY_DIVERGED = "REPLAY_DIVERGED"
DISC_UNSUPPORTED_DEPENDENCY = "UNSUPPORTED_DEPENDENCY"

OK_STATE = "OK"

# ---------------------------------------------------------------------------
# Canonical formula mapping for the calculation plan (deterministic)
# ---------------------------------------------------------------------------
# The Book-Keeping posting pipeline's calculation steps (BK_*) correspond to
# registered canonical FYJC relationships (TRADE_DISCOUNT, CASH_DISCOUNT,
# NET_PRICE, CASH_PAID, CREDITOR_BALANCE / DEBTOR_BALANCE). BK_LIST_PRICE is
# a pure question-supplied value with no registered canonical formula.
_CALC_TO_CANONICAL: Dict[str, str] = {
    "BK_TRADE_DISCOUNT_AMOUNT": "TRADE_DISCOUNT",
    "BK_NET_TRANSACTION_VALUE": "NET_PRICE",
    "BK_CASH_DISCOUNT_AMOUNT": "CASH_DISCOUNT",
    "BK_CASH_PAID_NET": "CASH_PAID",
    "BK_PAID_CREDIT_SPLIT": "CREDITOR_BALANCE",  # sale variants -> DEBTOR_BALANCE
}


def canonical_formula_for_calc(calculation_id: str, sale: bool = False) -> Optional[str]:
    """The registered canonical formula id behind one BK calculation step."""
    if calculation_id == "BK_PAID_CREDIT_SPLIT":
        return "DEBTOR_BALANCE" if sale else "CREDITOR_BALANCE"
    return _CALC_TO_CANONICAL.get(calculation_id)


# ---------------------------------------------------------------------------
# Deterministic helpers (Decimal normalization / account classification)
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Optional[Decimal]:
    """Decimal from a Decimal, str or number; None when unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _fmt(value: Any) -> str:
    d = _dec(value)
    if d is None:
        return ""
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


_ENGINE_ACCOUNT_CHART: frozenset = frozenset(
    set(ACCOUNT_ROLES)
    | set(_TRADITIONAL_OVERRIDES)
    | {v for _, v in _EXPENSE_ACCOUNT_WORDS}
    | {v for _, v in _INCOME_ACCOUNT_WORDS}
)


def account_kind(account: str) -> str:
    """'chart' when the account is part of the engine chart, 'party' when it
    is a named Personal party, else 'unknown' (an invented account)."""
    name = str(account or "").strip()
    if name in _ENGINE_ACCOUNT_CHART:
        return "chart"
    cls = traditional_class_for(name)
    if cls == CLASS_PERSONAL:
        return "party"
    return "unknown"


def canonical_account_id(account: str) -> str:
    """'ACCOUNT:<name>' for chart accounts, 'PARTY:<name>' for parties."""
    if account_kind(account) == "party":
        return f"PARTY:{account}"
    return f"ACCOUNT:{account}"


# ---------------------------------------------------------------------------
# 2. Canonical normalization (PE principle)
# ---------------------------------------------------------------------------


def _resolved_accounts(pattern: Optional[Dict[str, Any]],
                       text: str) -> Tuple[List[str], List[str]]:
    if not pattern or pattern.get("refuse"):
        return [], []
    return (_resolve_side_specs(pattern.get("debit") or [], text, "receiver"),
            _resolve_side_specs(pattern.get("credit") or [], text, "giver"))


def _plan_formula_ids(text: str, sale: bool) -> List[str]:
    """Registered canonical formula ids referenced by the amount pipeline."""
    amounts = resolve_transaction_amounts(text)
    ids: List[str] = []
    for step in amounts.get("steps") or []:
        fid = canonical_formula_for_calc(step.get("calculation_id"), sale=sale)
        if fid and fid not in ids:
            ids.append(fid)
    return ids


def canonicalize_bk(question: str) -> Dict[str, Any]:
    """Equivalent textbook wording collapses onto ONE canonical
    representation (canonical transaction id + account ids + rule ids +
    formula ids). Original wording is preserved separately. When the
    normalization confidence is insufficient -> REVIEW_REQUIRED (never a
    guessed canonical concept)."""
    text = str(question or "").strip()
    if not text:
        return {
            "status": REVIEW_REQUIRED,
            "canonical_transaction_id": None,
            "canonical_accounts": {"debit": [], "credit": []},
            "canonical_rule_ids": [],
            "canonical_formula_ids": [],
            "confidence": "REVIEW",
            "original_wording": "",
            "why": "No transaction wording was provided.",
        }
    pattern = classify_bk_type(text)
    if pattern is None:
        return {
            "status": REVIEW_REQUIRED,
            "canonical_transaction_id": None,
            "canonical_accounts": {"debit": [], "credit": []},
            "canonical_rule_ids": [],
            "canonical_formula_ids": [],
            "confidence": "REVIEW",
            "original_wording": text,
            "why": "The transaction could not be classified deterministically.",
        }
    transaction_id = pattern.get("key")
    refuse = bool(pattern.get("refuse"))
    amounts = resolve_transaction_amounts(text)
    ambiguous = bool(amounts.get("concerns")) or amounts.get("status") != VERIFIED

    debit_accounts, credit_accounts = _resolved_accounts(pattern, text)
    sale = "SALE" in str(transaction_id)
    rule_ids: List[str] = []
    for account in debit_accounts + credit_accounts:
        cls = traditional_class_for(account)
        rule = BK_RULE_BY_CLASS.get(str(cls).lower())
        rid = rule.get("rule_id") if rule else None
        if rid and rid not in rule_ids:
            rule_ids.append(rid)
    formula_ids = _plan_formula_ids(text, sale=sale)

    # a party placeholder that resolved to nothing = insufficient confidence
    party_missing = any(
        isinstance(spec, dict) and spec.get("party")
        for spec in (pattern.get("debit") or []) + (pattern.get("credit") or [])
    ) and not any(account_kind(a) == "party"
                  for a in debit_accounts + credit_accounts)

    if refuse or ambiguous or party_missing:
        return {
            "status": REVIEW_REQUIRED,
            "canonical_transaction_id": transaction_id,
            "canonical_accounts": {
                "debit": [canonical_account_id(a) for a in debit_accounts],
                "credit": [canonical_account_id(a) for a in credit_accounts],
            },
            "canonical_rule_ids": rule_ids,
            "canonical_formula_ids": formula_ids,
            "confidence": "REVIEW",
            "original_wording": text,
            "why": (
                (pattern.get("why") or "") if refuse else
                "The wording is ambiguous - FT-E never guesses a canonical "
                "treatment."
            ),
        }

    return {
        "status": VERIFIED,
        "canonical_transaction_id": transaction_id,
        "canonical_accounts": {
            "debit": [canonical_account_id(a) for a in debit_accounts],
            "credit": [canonical_account_id(a) for a in credit_accounts],
        },
        "canonical_rule_ids": rule_ids,
        "canonical_formula_ids": formula_ids,
        "confidence": "HIGH",
        "original_wording": text,
        "why": (
            f"{pattern.get('label')} -> {transaction_id} with accounts "
            f"{', '.join(canonical_account_id(a) for a in debit_accounts)} "
            f"Dr / {', '.join(canonical_account_id(a) for a in credit_accounts)} "
            f"Cr."
        ),
    }


def canonical_equivalent(question_a: str, question_b: str) -> bool:
    """Two wordings converge to the same canonical IR (transaction id,
    account ids, rule ids, formula ids) with HIGH confidence."""
    ca = canonicalize_bk(question_a)
    cb = canonicalize_bk(question_b)
    if ca.get("status") != VERIFIED or cb.get("status") != VERIFIED:
        return False
    keys = ("canonical_transaction_id", "canonical_accounts",
            "canonical_rule_ids", "canonical_formula_ids")
    return all(ca[k] == cb[k] for k in keys)


# ---------------------------------------------------------------------------
# Deterministic serialization (replay-safe)
# ---------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    raise TypeError(f"Not JSON-serializable: {type(value).__name__}")


def serialize_replay(record: Dict[str, Any]) -> str:
    """Deterministic serialization: sorted keys, stable separators, Decimals
    as canonical strings. Same record -> byte-identical string."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      default=_json_default, ensure_ascii=False)


def deserialize_replay(text: str) -> Dict[str, Any]:
    """Load a serialized replay record."""
    return json.loads(text)


# ---------------------------------------------------------------------------
# Replay IR construction (from the deterministic pipeline output)
# ---------------------------------------------------------------------------


def _amounts_from_records(records: List[Dict[str, Any]],
                          sale: bool) -> Dict[str, Any]:
    """Rebuild the amount-pipeline snapshot from the journal's
    calculation_records (deterministic; no NL re-interpretation). When no
    cash discount applies, cash_paid equals paid_amount (the pipeline's own
    default) - a missing BK_CASH_PAID_NET step never means 'no payment'."""
    out: Dict[str, Any] = {
        "list_price": None, "trade_discount_rate": None,
        "trade_discount_amount": None, "net_value": None,
        "paid_amount": None, "credit_amount": None,
        "cash_discount_rate": None, "cash_discount_amount": None,
        "cash_paid": None, "explicit_discount": None,
    }
    for step in records or []:
        cid = step.get("calculation_id")
        inputs = step.get("inputs") or {}
        if cid == "BK_LIST_PRICE":
            out["list_price"] = _dec(step.get("result"))
        elif cid == "BK_TRADE_DISCOUNT_AMOUNT":
            out["trade_discount_rate"] = _dec(inputs.get("trade_discount_rate"))
            out["trade_discount_amount"] = _dec(step.get("result"))
        elif cid == "BK_NET_TRANSACTION_VALUE":
            out["net_value"] = _dec(step.get("result"))
        elif cid == "BK_PAID_CREDIT_SPLIT":
            result = step.get("result")
            if isinstance(result, dict):
                out["paid_amount"] = _dec(result.get("paid"))
                out["credit_amount"] = _dec(result.get("credit"))
        elif cid == "BK_CASH_DISCOUNT_AMOUNT":
            out["cash_discount_rate"] = _dec(inputs.get("cash_discount_rate"))
            out["cash_discount_amount"] = _dec(step.get("result"))
        elif cid == "BK_CASH_PAID_NET":
            out["cash_paid"] = _dec(step.get("result"))
        elif cid == "BK_EXPLICIT_DISCOUNT":
            out["explicit_discount"] = {
                "cash_amount": _dec(inputs.get("cash")),
                "discount_amount": _dec(inputs.get("discount")),
                "party_total": _dec(step.get("result")),
            }
    if out["cash_paid"] is None and out["paid_amount"] is not None:
        out["cash_paid"] = out["paid_amount"]
    return out


def _is_party_account(account: str) -> bool:
    return account_kind(account) == "party"


def _infer_segment_shape(pattern_key: Optional[str],
                         debit_accounts: List[str],
                         credit_accounts: List[str],
                         amounts: Dict[str, Any],
                         journal: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic shape inference for one journal (mirrors the shapes
    generate_journal produces; verified against the corpus in the 15G gate).
    The shape is inferred from the journal's OWN resolved lines + amounts -
    never by re-parsing the whole question (which would mis-attribute
    amounts across transactions).

    Returns {kind, cash_or_bank, party, startup_components, startup_total,
    explicit}.
    """
    explicit = amounts.get("explicit_discount")
    sale = "SALE" in str(pattern_key or "")
    if not sale:
        # fallback when the per-journal pattern key is not recorded (merged
        # multi-segment questions): the journal's own lines decide the side
        # (a Sales credit is a sale). 'Sales Returns' is never split, so the
        # fallback cannot misclassify a returns journal as a split sale.
        sale = "Sales" in credit_accounts
    split = (amounts.get("credit_amount") is not None
             and _dec(amounts["credit_amount"]) is not None
             and _dec(amounts["credit_amount"]) > 0) or (
                 amounts.get("cash_discount_amount") is not None
                 and _dec(amounts["cash_discount_amount"]) > 0)
    # cash vs bank comes from the journal's OWN resolved lines (ground
    # truth) - never by re-parsing the whole question text.
    all_accounts = set(debit_accounts) | set(credit_accounts)
    cash_or_bank = "Bank" if "Bank" in all_accounts else "Cash"

    if explicit is not None and explicit:
        # explicit discount settlement: Discount Allowed on the debit side ->
        # allowed; Discount Received on the credit side -> received.
        if "Discount Allowed" in debit_accounts:
            party = next((a for a in credit_accounts if _is_party_account(a)),
                         None)
            return {"kind": "explicit_allowed", "cash_or_bank": cash_or_bank,
                    "party": party, "explicit": explicit,
                    "startup_components": None, "startup_total": None}
        if "Discount Received" in credit_accounts:
            party = next((a for a in debit_accounts if _is_party_account(a)),
                         None)
            return {"kind": "explicit_received", "cash_or_bank": cash_or_bank,
                    "party": party, "explicit": explicit,
                    "startup_components": None, "startup_total": None}
        return {"kind": "simple", "cash_or_bank": cash_or_bank,
                "party": None, "explicit": explicit,
                "startup_components": None, "startup_total": None}

    # A multi-line debit against one Capital credit is the started-business /
    # capital-introduced asset breakdown. The per-component amounts come from
    # the journal's own debit lines (the ground truth) - the whole-question
    # text is never re-parsed for this.
    if credit_accounts == ["Capital"] and len(debit_accounts) > 1:
        components = [
            [l.get("account") or "", _fmt(l.get("amount"))]
            for l in (journal.get("debit_lines") or [])
        ]
        total = sum((_dec(l.get("amount")) or Decimal(0)
                     for l in (journal.get("debit_lines") or [])), Decimal(0))
        return {"kind": "startup", "cash_or_bank": cash_or_bank,
                "party": None, "explicit": None,
                "startup_components": components,
                "startup_total": _fmt(total)}

    if sale and split:
        party = next((a for a in debit_accounts if _is_party_account(a)), None)
        return {"kind": "split_sale", "cash_or_bank": cash_or_bank,
                "party": party, "explicit": None,
                "startup_components": None, "startup_total": None}
    if split and len(debit_accounts) == 1:
        party = next((a for a in credit_accounts if _is_party_account(a)),
                     None)
        return {"kind": "split_purchase", "cash_or_bank": cash_or_bank,
                "party": party, "explicit": None,
                "startup_components": None, "startup_total": None}
    return {"kind": "simple", "cash_or_bank": cash_or_bank, "party": None,
            "explicit": None, "startup_components": None, "startup_total": None}


def _segment_ir(index: int, journal: Dict[str, Any], text: str,
                pattern_key: Optional[str]) -> Dict[str, Any]:
    debit_accounts = [l.get("account") or ""
                      for l in (journal.get("debit_lines") or [])]
    credit_accounts = [l.get("account") or ""
                       for l in (journal.get("credit_lines") or [])]
    records = journal.get("calculation_records") or []
    sale = "SALE" in str(pattern_key or "")
    amounts = _amounts_from_records(records, sale=sale)
    amounts["raw_text"] = text  # for cash/bank resolution, never re-parsed
    shape = _infer_segment_shape(pattern_key, debit_accounts, credit_accounts,
                                 amounts, journal)
    amounts.pop("raw_text", None)
    return {
        "journal_index": index,
        "pattern_key": pattern_key,
        "debit_accounts": debit_accounts,
        "credit_accounts": credit_accounts,
        "amounts": {k: _canonical_value(v) for k, v in amounts.items()},
        "shape": shape,
    }


def _facts_from_text(text: str, amounts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """QUESTION-SUPPLIED facts extracted from the wording (deterministic
    ids; never invented values). Numeric facts are canonicalized with the
    numeric formatter; textual facts (party/asset names) are preserved
    verbatim for auditability."""
    facts: List[Dict[str, Any]] = []
    counter = 0

    def _fact(role: str, value: Any, text_value: bool = False) -> None:
        nonlocal counter
        counter += 1
        facts.append({
            "fact_id": f"FACT_{counter}",
            "role": role,
            "value": str(value) if text_value else _fmt(value),
            "provenance": "QUESTION_SUPPLIED",
        })

    if amounts.get("list_price") is not None:
        _fact("list_price", amounts["list_price"])
    if amounts.get("trade_discount_rate") is not None:
        _fact("trade_discount_rate", amounts["trade_discount_rate"])
    if amounts.get("cash_discount_rate") is not None:
        _fact("cash_discount_rate", amounts["cash_discount_rate"])
    party = next((a for a in _party_names(text)), None)
    if party:
        _fact("party", party, text_value=True)
    for asset in named_assets(text):
        _fact("asset", asset, text_value=True)
    return facts


def _party_names(text: str) -> List[str]:
    """Deterministic extraction of named Personal parties in the wording."""
    from backend.maths.fyjc_bk_reasoning import _party_from_text
    party = _party_from_text(text)
    if party:
        return [party]
    return []


def _canonical_value(value: Any) -> Any:
    """Canonical value form: scalars -> numeric string; dicts -> dict of
    canonical strings (so a JSON round-trip never changes replay semantics)."""
    if isinstance(value, dict):
        return {k: _canonical_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(v) for v in value]
    return _fmt(value)


def _plan_with_provenance(records: List[Dict[str, Any]],
                          sale: bool) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    for idx, step in enumerate(records or [], start=1):
        entry = {
            "calculation_id": step.get("calculation_id"),
            "calculation_order": idx,
            "label": step.get("label"),
            "formula": step.get("formula"),
            "inputs": _canonical_value(step.get("inputs") or {}),
            "result": _canonical_value(step.get("result")),
            "provenance": "CALCULATED",
            "canonical_formula_id": canonical_formula_for_calc(
                step.get("calculation_id"), sale=sale),
        }
        plan.append(entry)
    return plan


def _cpp_authority_block(question: str,
                         canonical_formula_ids: List[str]) -> Dict[str, Any]:
    """Best-effort deterministic C++ authority snapshot for the canonical
    formulas referenced by the question's amount pipeline. When the compiled
    authority is unavailable the state is recorded honestly (never a fake
    'cpp' verdict)."""
    if not canonical_formula_ids:
        return {
            "state": "not_requested",
            "formula_ids": [],
            "executed": False,
            "verified": False,
            "note": ("No registered financial metric was requested by this "
                     "question; posting arithmetic is verification/preparation "
                     "arithmetic traced by calculation_ids."),
        }
    if not binary_path():
        return {
            "state": "engine_unavailable",
            "formula_ids": canonical_formula_ids,
            "executed": False,
            "verified": False,
            "note": "Compiled C++ authority not deployed - no Python fallback.",
        }
    outcomes: List[Dict[str, Any]] = []
    for fid in canonical_formula_ids:
        # deterministic facts for the canonical formulas present in the plan
        amounts = resolve_transaction_amounts(question)
        facts: Dict[str, Dict[str, Any]] = {}
        if fid == "TRADE_DISCOUNT" and amounts.get("list_price") is not None \
                and amounts.get("trade_discount_rate") is not None:
            facts = {"List Price": {"value": float(amounts["list_price"]),
                                    "reporting_period": "FY2025"},
                     "Trade Discount Rate": {
                         "value": float(amounts["trade_discount_rate"]),
                         "reporting_period": "FY2025"}}
        elif fid == "NET_PRICE" and amounts.get("list_price") is not None \
                and amounts.get("trade_discount_amount") is not None:
            facts = {"List Price": {"value": float(amounts["list_price"]),
                                    "reporting_period": "FY2025"},
                     "Trade Discount": {
                         "value": float(amounts["trade_discount_amount"]),
                         "reporting_period": "FY2025"}}
        elif fid == "CASH_DISCOUNT" and amounts.get("paid_amount") is not None \
                and amounts.get("cash_discount_rate") is not None:
            facts = {"Paid Amount": {"value": float(amounts["paid_amount"]),
                                     "reporting_period": "FY2025"},
                     "Cash Discount Rate": {
                         "value": float(amounts["cash_discount_rate"]),
                         "reporting_period": "FY2025"}}
        elif fid == "CASH_PAID" and amounts.get("paid_amount") is not None \
                and amounts.get("cash_discount_amount") is not None:
            facts = {"Paid Amount": {"value": float(amounts["paid_amount"]),
                                     "reporting_period": "FY2025"},
                     "Cash Discount": {
                         "value": float(amounts["cash_discount_amount"]),
                         "reporting_period": "FY2025"}}
        elif fid == "CREDITOR_BALANCE" \
                and amounts.get("net_value") is not None \
                and amounts.get("paid_amount") is not None:
            facts = {"Net Purchase": {"value": float(amounts["net_value"]),
                                      "reporting_period": "FY2025"},
                     "Amount Paid": {"value": float(amounts["paid_amount"]),
                                     "reporting_period": "FY2025"}}
        if facts:
            result = cpp_calculate(fid, facts)
            outcomes.append({
                "formula_id": fid,
                "sent": {k: {"value": v["value"]} for k, v in facts.items()},
                "status": result.get("status") if result else None,
                "value": result.get("value") if result else None,
                "display_value": result.get("display_value") if result else "",
            })
    executed = bool(outcomes)
    return {
        "state": "cpp" if executed else "not_applicable",
        "formula_ids": canonical_formula_ids,
        "executed": executed,
        "verified": all(o.get("status") in ("derived", "external_derived")
                        for o in outcomes) if executed else False,
        "outcomes": outcomes,
        "note": ("Registered canonical relationships routed through the "
                 "compiled C++ mathematical authority (never a Python "
                 "financial calculation)."),
    }


def build_replay_record(question: str,
                        verify_cpp: bool = False) -> Dict[str, Any]:
    """Capture one resolved FYJC reasoning case as a versioned, deterministic
    replay record (see module docstring for the pipeline)."""
    text = str(question or "").strip()
    out = reason_bk_question(text)
    segments = _split_transactions(text)
    understanding = out.get("understanding") or {}
    single_key = understanding.get("question_type_key")
    journals = out.get("journals") or [out.get("journal")] or []
    journals = [j for j in journals if j is not None]

    # canonical normalization (never guesses)
    canon = canonicalize_bk(text)

    segment_irs: List[Dict[str, Any]] = []
    if len(journals) == 1:
        # the pipeline collapsed the whole question into ONE journal (payment
        # steps merged into their purchase/sale), so the single pattern key
        # applies to it - never rely on the raw segment count.
        segment_irs.append(_segment_ir(1, journals[0], text, single_key))
    else:
        for idx, journal in enumerate(journals, start=1):
            segment_irs.append(_segment_ir(idx, journal, text, None))

    sale = "SALE" in str(single_key or "")
    records = [step for j in journals
               for step in (j.get("calculation_records") or [])]
    plan = _plan_with_provenance(records, sale=sale)

    amounts_all: Dict[str, Any] = {}
    for seg in segment_irs:
        amounts_all.update(seg.get("amounts") or {})

    facts = _facts_from_text(text, amounts_all)
    requested = _requested_operation(text)

    canonical_formula_ids = list(dict.fromkeys(
        cid for cid in (canon.get("canonical_formula_ids") or [])))
    cpp_block = (_cpp_authority_block(text, canonical_formula_ids)
                 if verify_cpp else {
                     "state": "not_requested", "formula_ids": [],
                     "executed": False, "verified": False,
                     "note": ("No registered financial metric was requested by "
                              "this question; posting arithmetic is "
                              "verification/preparation arithmetic traced by "
                              "calculation_ids.")})

    verification = out.get("verification") or {}
    ledger = out.get("ledger") or {}
    tb = out.get("trial_balance") or {}
    final_result = {
        "status": out.get("status"),
        "journal": out.get("journal"),
        "ledger": ledger,
        "trial_balance": tb,
        "debit_lines": out.get("debit_lines") or [],
        "credit_lines": out.get("credit_lines") or [],
        "why_not": out.get("why_not"),
        "next_action": out.get("next_action"),
    }

    ir = {
        "transaction_id": canon.get("canonical_transaction_id"),
        "label": understanding.get("question_type"),
        "status": out.get("status"),
        "segments": segment_irs,
        "canonical_accounts": canon.get("canonical_accounts"),
        "rule_ids": canon.get("canonical_rule_ids"),
        "formula_ids": canonical_formula_ids,
        "requested_operation": requested,
    }

    replay_id = _replay_id(ir)

    record = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "reasoning_version": REASONING_VERSION,
        "registry_version": REGISTRY_VERSION,
        "formula_registry_version": FORMULA_REGISTRY_VERSION,
        "cpp_registry_version": CPP_REGISTRY_VERSION,
        "replay_id": replay_id,
        "input": {
            "raw_text": text,
            "segments": segments,
            "facts": facts,
            "requested_operation": requested,
        },
        "canonical_ir": ir,
        "calculation_plan": plan,
        "cpp_authority": cpp_block,
        "verification": {
            "journal_balanced": all(
                j.get("balanced") for j in journals) if journals else False,
            "ledger_balanced": bool(ledger.get("balanced")),
            "trial_balance_balanced": bool(tb.get("balanced")),
            "arithmetic": {
                "total_debit": _fmt(verification.get("total_debit")),
                "total_credit": _fmt(verification.get("total_credit")),
                "balanced": bool(verification.get("balanced")),
            },
        },
        "final_result": final_result,
        "lineage": build_lineage(text, out),
        "discrepancies": [],
    }
    # deterministic discrepancy scan at capture time (never silently repairs)
    discrepancies = validate_pipeline(out).get("discrepancies") or []
    record["discrepancies"] = discrepancies
    if discrepancies:
        record["verification"]["state"] = REVIEW_REQUIRED
    else:
        record["verification"]["state"] = OK_STATE
    return record


def _replay_id(ir: Dict[str, Any]) -> str:
    """Deterministic content hash of the canonical IR + versions (no
    timestamps, no random ids). Equivalent wordings share a replay_id."""
    digest = hashlib.sha256()
    payload = "|".join([
        REPLAY_SCHEMA_VERSION, REASONING_VERSION, REGISTRY_VERSION,
        FORMULA_REGISTRY_VERSION, CPP_REGISTRY_VERSION,
        serialize_replay(ir),
    ])
    digest.update(payload.encode("utf-8"))
    return digest.hexdigest()[:24]


# ---------------------------------------------------------------------------
# 1. Replay executor (re-executes from the IR, never from the NL)
# ---------------------------------------------------------------------------


def _line(account: str, amount: Decimal, side: str) -> Dict[str, Any]:
    cls = traditional_class_for(account)
    return {
        "account": account,
        "class": cls,
        "rule": TRADITIONAL_GOLDEN_RULES[cls],
        "why": side_decision_for(account, side),
        "amount": amount,
        "side": side,
    }


def ir_to_journal_lines(segment: Dict[str, Any]) -> Tuple[List[Dict[str, Any]],
                                                          List[Dict[str, Any]]]:
    """Rebuild a journal's debit/credit lines from the canonical IR ONLY.

    This is the replay core: it mirrors the deterministic journal shapes of
    the pipeline from the stored accounts + amounts + shape metadata, with
    zero natural-language interpretation. Faithfulness is asserted over the
    whole 15F corpus by the 15G gate."""
    amounts = segment.get("amounts") or {}
    shape = segment.get("shape") or {}
    kind = shape.get("kind", "simple")
    debit_accounts = segment.get("debit_accounts") or []
    credit_accounts = segment.get("credit_accounts") or []

    net = _dec(amounts.get("net_value")) or Decimal(0)
    cash_paid = _dec(amounts.get("cash_paid"))
    cash_discount = _dec(amounts.get("cash_discount_amount"))
    credit_portion = _dec(amounts.get("credit_amount"))
    cash_or_bank = shape.get("cash_or_bank") or "Cash"

    debit_lines: List[Dict[str, Any]] = []
    credit_lines: List[Dict[str, Any]] = []

    if kind == "explicit_allowed" or kind == "explicit_received":
        explicit = shape.get("explicit") or {}
        party = shape.get("party")
        cash_amount = _dec(explicit.get("cash_amount")) or Decimal(0)
        discount = _dec(explicit.get("discount_amount")) or Decimal(0)
        party_total = _dec(explicit.get("party_total")) or Decimal(0)
        if kind == "explicit_allowed":
            debit_lines.append(_line(cash_or_bank, cash_amount, "debit"))
            if discount > 0:
                debit_lines.append(_line("Discount Allowed", discount, "debit"))
            if party:
                credit_lines.append(_line(party, party_total, "credit"))
        else:
            if party:
                debit_lines.append(_line(party, party_total, "debit"))
            credit_lines.append(_line(cash_or_bank, cash_amount, "credit"))
            if discount > 0:
                credit_lines.append(_line("Discount Received", discount,
                                          "credit"))
        return debit_lines, credit_lines

    if kind == "startup":
        for account, amount in shape.get("startup_components") or []:
            debit_lines.append(_line(account, _dec(amount) or Decimal(0),
                                     "debit"))
        credit_lines.append(_line("Capital", _dec(shape.get("startup_total"))
                                  or Decimal(0), "credit"))
        return debit_lines, credit_lines

    if kind == "split_sale":
        party = shape.get("party")
        if party and credit_portion is not None and credit_portion > 0:
            debit_lines.append(_line(party, credit_portion, "debit"))
        if cash_paid is not None and cash_paid > 0:
            debit_lines.append(_line(cash_or_bank, cash_paid, "debit"))
        if cash_discount is not None and cash_discount > 0:
            debit_lines.append(_line("Discount Allowed", cash_discount,
                                     "debit"))
        for account in credit_accounts:
            credit_lines.append(_line(account, net, "credit"))
        return debit_lines, credit_lines

    if kind == "split_purchase":
        for account in debit_accounts:
            debit_lines.append(_line(account, net, "debit"))
        if cash_paid is not None and cash_paid > 0:
            credit_lines.append(_line(cash_or_bank, cash_paid, "credit"))
        if cash_discount is not None and cash_discount > 0:
            credit_lines.append(_line("Discount Received", cash_discount,
                                      "credit"))
        if credit_portion is not None and credit_portion > 0:
            party = shape.get("party")
            credit_lines.append(_line(party if party else "Creditors",
                                      credit_portion, "credit"))
        return debit_lines, credit_lines

    # simple: every debit account at net, every credit account at net
    for account in debit_accounts:
        debit_lines.append(_line(account, net, "debit"))
    for account in credit_accounts:
        credit_lines.append(_line(account, net, "credit"))
    return debit_lines, credit_lines


def _recompute_plan(plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic replay of the calculation plan: re-derive each step's
    result from its stored inputs with the registered canonical formula, and
    flag any divergence (a discrepancy is never silently repaired)."""
    replayed: List[Dict[str, Any]] = []
    for step in plan:
        cid = step.get("calculation_id")
        inputs = step.get("inputs") or {}
        expected = _dec(step.get("result"))
        recomputed: Optional[Decimal] = None
        formula = None
        try:
            if cid == "BK_LIST_PRICE":
                recomputed = _dec(inputs.get("list_price"))
                formula = "List price from the question"
            elif cid == "BK_TRADE_DISCOUNT_AMOUNT":
                lp, rate = _dec(inputs.get("list_price")), _dec(
                    inputs.get("trade_discount_rate"))
                if lp is not None and rate is not None:
                    recomputed = (lp * rate / Decimal(100)).quantize(
                        Decimal("0.01"))
                    formula = "Trade discount = List price x Trade discount %"
            elif cid == "BK_NET_TRANSACTION_VALUE":
                lp, td = _dec(inputs.get("list_price")), _dec(
                    inputs.get("trade_discount"))
                if lp is not None:
                    recomputed = lp - (td or Decimal(0))
                    formula = "Net = List price - Trade discount"
            elif cid == "BK_PAID_CREDIT_SPLIT":
                net, paid = _dec(inputs.get("net_value")), _dec(
                    inputs.get("paid_amount"))
                if net is not None and paid is not None:
                    recomputed = net - paid
                    formula = "Credit = Net - Paid"
            elif cid == "BK_CASH_DISCOUNT_AMOUNT":
                paid, rate = _dec(inputs.get("paid_amount")), _dec(
                    inputs.get("cash_discount_rate"))
                if paid is not None and rate is not None:
                    recomputed = (paid * rate / Decimal(100)).quantize(
                        Decimal("0.01"))
                    formula = "Cash discount = Paid x Cash discount %"
            elif cid == "BK_CASH_PAID_NET":
                paid, cd = _dec(inputs.get("paid_amount")), _dec(
                    inputs.get("cash_discount"))
                if paid is not None:
                    recomputed = paid - (cd or Decimal(0))
                    formula = "Cash paid = Paid - Cash discount"
            elif cid == "BK_EXPLICIT_DISCOUNT":
                cash, discount = _dec(inputs.get("cash")), _dec(
                    inputs.get("discount"))
                if cash is not None and discount is not None:
                    recomputed = cash + discount
                    formula = "Party account = Cash paid + Discount amount"
        except (InvalidOperation, ArithmeticError):
            recomputed = None
        replay_row = dict(step)
        replay_row["replayed_result"] = _fmt(recomputed) \
            if recomputed is not None else None
        if cid == "BK_PAID_CREDIT_SPLIT" and isinstance(
                step.get("result"), dict):
            # the split step's result is a {paid, credit} pair; replay the
            # CREDIT remainder (the paid portion is the supplied input).
            recorded_credit = _dec((step.get("result") or {}).get("credit"))
            replay_row["replay_match"] = (
                recomputed is not None and recorded_credit is not None
                and recomputed == recorded_credit)
        else:
            replay_row["replay_match"] = (
                expected is not None and recomputed is not None
                and expected == recomputed)
        replayed.append(replay_row)
    return replayed


def replay_execute(record: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a replay record WITHOUT the natural-language interpretation:
    rebuild every journal from the canonical IR, re-post the ledger,
    re-build the trial balance, re-verify arithmetic, re-run the plan, and
    validate the result. Same IR + same registry version -> same result."""
    schema = record.get("schema_version")
    if schema != REPLAY_SCHEMA_VERSION:
        return {
            "status": BLOCKED,
            "replay_ok": False,
            "replay_id": record.get("replay_id"),
            "why_not": (f"Replay schema {schema!r} is not supported by "
                        f"{REPLAY_SCHEMA_VERSION!r}."),
            "discrepancies": [{
                "code": DISC_UNSUPPORTED_DEPENDENCY,
                "reason": "Unsupported replay schema version.",
            }],
        }
    status = (record.get("canonical_ir") or {}).get("status")
    if status != VERIFIED:
        final = record.get("final_result") or {}
        return {
            "status": status,
            "replay_ok": True,
            "replay_id": record.get("replay_id"),
            "why_not": final.get("why_not"),
            "next_action": final.get("next_action"),
            "journal": None, "ledger": None, "trial_balance": None,
            "discrepancies": [],
        }

    segments = (record.get("canonical_ir") or {}).get("segments") or []
    journals: List[Dict[str, Any]] = []
    discrepancies: List[Dict[str, Any]] = []
    for seg in segments:
        debit_lines, credit_lines = ir_to_journal_lines(seg)
        total_debit = sum((l["amount"] for l in debit_lines), Decimal(0))
        total_credit = sum((l["amount"] for l in credit_lines), Decimal(0))
        journal = {
            "status": VERIFIED,
            "debit_lines": debit_lines,
            "credit_lines": credit_lines,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balanced": total_debit == total_credit,
        }
        journals.append(journal)
        discrepancies.extend(validate_journal(journal).get("discrepancies")
                             or [])

    entries: List[Dict[str, Any]] = []
    for j in journals:
        entries.append({
            "debits": [{"account": l["account"], "amount": l["amount"]}
                       for l in j["debit_lines"]],
            "credits": [{"account": l["account"], "amount": l["amount"]}
                        for l in j["credit_lines"]],
        })
    ledger = post_ledger(entries)
    trial_balance = build_trial_balance(entries)
    discrepancies.extend(validate_ledger(ledger).get("discrepancies") or [])
    discrepancies.extend(
        validate_trial_balance(trial_balance).get("discrepancies") or [])

    all_lines = [l for j in journals for l in
                 j.get("debit_lines") + j.get("credit_lines")]
    arithmetic = verify_arithmetic([
        {"side": l["side"], "amount": l["amount"]} for l in all_lines])

    plan = _recompute_plan(record.get("calculation_plan") or [])
    plan_mismatches = [p for p in plan if p.get("replay_match") is False]
    if plan_mismatches:
        discrepancies.append({
            "code": DISC_REPLAY_DIVERGED,
            "reason": ("Replay arithmetic diverged from the recorded plan "
                       f"({len(plan_mismatches)} step(s))."),
        })

    # compare against the recorded final result (the authority snapshot)
    recorded = record.get("final_result") or {}
    recorded_journals = record.get("canonical_ir") or {}
    recorded_lines = ((recorded.get("debit_lines") or [])
                      + (recorded.get("credit_lines") or []))
    replayed_lines = all_lines
    if _lines_key(recorded_lines) != _lines_key(replayed_lines):
        discrepancies.append({
            "code": DISC_REPLAY_DIVERGED,
            "reason": ("Replayed journal lines differ from the recorded "
                       "canonical outcome."),
        })

    state = REVIEW_REQUIRED if discrepancies else OK_STATE
    return {
        "status": VERIFIED if state == OK_STATE else REVIEW_REQUIRED,
        "replay_ok": state == OK_STATE,
        "replay_id": record.get("replay_id"),
        "journal": journals[0] if len(journals) == 1 else {
            "multi": True, "count": len(journals),
            "debit_lines": [l for j in journals for l in j["debit_lines"]],
            "credit_lines": [l for j in journals for l in j["credit_lines"]],
        },
        "journals": journals,
        "ledger": ledger,
        "trial_balance": trial_balance,
        "verification": {
            "journal_balanced": all(j["balanced"] for j in journals),
            "ledger_balanced": bool(ledger.get("balanced")),
            "trial_balance_balanced": bool(trial_balance.get("balanced")),
            "arithmetic": {
                "total_debit": _fmt(arithmetic.get("total_debit")),
                "total_credit": _fmt(arithmetic.get("total_credit")),
                "balanced": bool(arithmetic.get("balanced")),
            },
            "plan_replayed": [p for p in plan],
        },
        "discrepancies": discrepancies,
        "state": state,
    }


def _lines_key(lines: List[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    return sorted((str(l.get("account") or ""), str(l.get("side") or ""),
                   _fmt(l.get("amount"))) for l in lines)


# ---------------------------------------------------------------------------
# 3. Lineage passport
# ---------------------------------------------------------------------------


def build_lineage(question: str, out: Dict[str, Any]) -> Dict[str, Any]:
    """Machine-readable lineage chain for a resolved case, answering:
    1 what FT-E received, 2 what it understood, 3 which canonical concepts
    were selected, 4 which rule/formula was used, 5 which values were
    supplied vs calculated, 6 what was sent to C++, 7 what C++ verified,
    8 why the result is VERIFIED. A supplied fact never appears as a
    calculated value."""
    text = str(question or "").strip()
    understanding = out.get("understanding") or {}
    journals = out.get("journals") or [out.get("journal")] or []
    journals = [j for j in journals if j is not None]
    pattern_key = understanding.get("question_type_key")
    sale = "SALE" in str(pattern_key or "")
    records = [step for j in journals
               for step in (j.get("calculation_records") or [])]

    amounts_all = _amounts_from_records(records, sale=sale)
    supplied = _facts_from_text(text, amounts_all)
    supplied_roles = {f["role"] for f in supplied}

    calculated: List[Dict[str, Any]] = []
    for step in records:
        fid = canonical_formula_for_calc(step.get("calculation_id"), sale=sale)
        calculated.append({
            "role": step.get("calculation_id"),
            "value": _fmt(step.get("result")),
            "provenance": "CALCULATED",
            "canonical_formula_id": fid,
        })

    # invariant: a supplied fact never appears as a calculated value
    overlap = sorted(supplied_roles & {c["role"] for c in calculated})
    status = out.get("status")
    why_final = None
    if status == VERIFIED:
        why_final = (
            "VERIFIED: the transaction was classified deterministically "
            f"({pattern_key}), the exact accounts were resolved, the journal "
            "is balanced (total Debit == total Credit), the ledger posting "
            "and trial balance agree, every derived amount carries a "
            "calculation_id with its inputs, and no discrepancy was found."
        )
    elif status in (REVIEW_REQUIRED, BLOCKED, NOT_SUPPORTED):
        why_final = (f"{status}: {out.get('why_not')}")

    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "received": {
            "raw_input": text,
            "segments": _split_transactions(text),
        },
        "understood": {
            "pattern_key": pattern_key,
            "label": understanding.get("question_type"),
            "requested_operation": _requested_operation(text),
            "accounts_identified": understanding.get("accounts_identified"),
        },
        "canonical": canonicalize_bk(text),
        "rules_used": _rule_ids_for(understanding, journals),
        "formulas_used": list(dict.fromkeys(
            c["canonical_formula_id"] for c in calculated
            if c["canonical_formula_id"])),
        "values": supplied + calculated,
        "supplied_vs_calculated_overlap": overlap,
        "cpp": {
            "sent": None,
            "verified": False,
            "note": ("Registered metrics are routed through verify_bk_metric "
                     "-> C++ authority; journal posting arithmetic is "
                     "preparation arithmetic with calculation_ids."),
        },
        "output": {
            "status": status,
            "debit_lines": _lines_key(out.get("debit_lines") or []),
            "credit_lines": _lines_key(out.get("credit_lines") or []),
            "why_final": why_final,
        },
    }


def _rule_ids_for(understanding: Dict[str, Any],
                  journals: List[Dict[str, Any]]) -> List[str]:
    ids: List[str] = []
    for journal in journals:
        for line in (journal.get("debit_lines") or []) \
                + (journal.get("credit_lines") or []):
            cls = str(line.get("class") or "") \
                or traditional_class_for(line.get("account") or "")
            rule = BK_RULE_BY_CLASS.get(str(cls).lower())
            rid = rule.get("rule_id") if rule else None
            if rid and rid not in ids:
                ids.append(rid)
    return ids


# ---------------------------------------------------------------------------
# 4. Immutable audit record (append-only, versioned)
# ---------------------------------------------------------------------------


class AuditLedger:
    """Append-only audit trail. Records are deep-copied on append and on
    snapshot, so no caller can mutate a historical record. A version change
    produces a NEW record (the replay_id includes the versions)."""

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            "audit_sequence": len(self._records) + 1,
            "schema_version": record.get("schema_version"),
            "registry_version": record.get("registry_version"),
            "reasoning_version": record.get("reasoning_version"),
            "replay_id": record.get("replay_id"),
            "execution_status": (record.get("final_result") or {}).get(
                "status"),
            "verification_status": (record.get("verification") or {}).get(
                "state"),
            "authority_state": (record.get("cpp_authority") or {}).get(
                "state"),
            "lineage": record.get("lineage"),
            "discrepancy_count": len(record.get("discrepancies") or []),
        }
        self._records.append(entry)
        return dict(entry)

    def snapshot(self) -> List[Dict[str, Any]]:
        return deepcopy(self._records)

    def count(self) -> int:
        return len(self._records)

    def clear(self) -> None:
        self._records = []


AUDIT_LEDGER = AuditLedger()


def append_audit_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Append one immutable audit entry (never mutates the record)."""
    return AUDIT_LEDGER.append(record)


def audit_snapshot() -> List[Dict[str, Any]]:
    return AUDIT_LEDGER.snapshot()


def reset_audit() -> None:
    AUDIT_LEDGER.clear()


# ---------------------------------------------------------------------------
# 5. Discrepancy detection (never silently repaired)
# ---------------------------------------------------------------------------


def _disc(code: str, reason: str, **extra: Any) -> Dict[str, Any]:
    out = {"code": code, "reason": reason}
    out.update(extra)
    return out


def validate_journal(journal: Dict[str, Any]) -> Dict[str, Any]:
    """Journal structural validation: Dr == Cr, non-empty sides, no
    duplicates, no account on both sides, no invented account."""
    discrepancies: List[Dict[str, Any]] = []
    if not journal:
        discrepancies.append(_disc(DISC_MISSING_JOURNAL_LINE,
                                   "No journal was produced."))
        return {"state": REVIEW_REQUIRED, "discrepancies": discrepancies}
    debit_lines = journal.get("debit_lines") or []
    credit_lines = journal.get("credit_lines") or []
    if not debit_lines:
        discrepancies.append(_disc(DISC_MISSING_DEBIT_LINE,
                                   "The journal has no debit line."))
    if not credit_lines:
        discrepancies.append(_disc(DISC_MISSING_CREDIT_LINE,
                                   "The journal has no credit line."))
    seen: Dict[Tuple[str, str, str], int] = {}
    for line in debit_lines + credit_lines:
        account = str(line.get("account") or "")
        side = str(line.get("side") or "")
        amount = _fmt(line.get("amount"))
        key = (account, side, amount)
        seen[key] = seen.get(key, 0) + 1
        kind = account_kind(account)
        if kind == "unknown":
            discrepancies.append(_disc(
                DISC_INVENTED_ACCOUNT,
                f"'{account}' is not a chart account or a named party.",
                account=account))
    for key, count in seen.items():
        if count > 1:
            discrepancies.append(_disc(
                DISC_DUPLICATE_LINE,
                f"Duplicate line {key[0]} {key[1]} {key[2]} appears "
                f"{count} times.", account=key[0]))
    debit_accounts = {l.get("account") for l in debit_lines}
    credit_accounts = {l.get("account") for l in credit_lines}
    both = sorted(debit_accounts & credit_accounts)
    if both:
        discrepancies.append(_disc(
            DISC_ACCOUNT_BOTH_SIDES,
            f"Account(s) on both debit and credit sides: {', '.join(both)}.",
            accounts=both))
    total_debit = sum((_dec(l.get("amount")) or Decimal(0)
                       for l in debit_lines), Decimal(0))
    total_credit = sum((_dec(l.get("amount")) or Decimal(0)
                        for l in credit_lines), Decimal(0))
    if total_debit != total_credit:
        discrepancies.append(_disc(
            DISC_JOURNAL_UNBALANCED,
            f"Total Debit {total_debit} != Total Credit {total_credit}.",
            total_debit=_fmt(total_debit), total_credit=_fmt(total_credit)))
    state = REVIEW_REQUIRED if discrepancies else OK_STATE
    return {
        "state": state,
        "discrepancies": discrepancies,
        "checks": {
            "balanced": total_debit == total_credit,
            "has_debit": bool(debit_lines),
            "has_credit": bool(credit_lines),
        },
    }


def validate_ledger(ledger: Dict[str, Any]) -> Dict[str, Any]:
    """Ledger validation: per-account Opening + Debit - Credit == Closing
    and overall Dr == Cr."""
    discrepancies: List[Dict[str, Any]] = []
    accounts = (ledger or {}).get("accounts") or {}
    for account in sorted(accounts):
        row = accounts[account]
        debit = _dec(row.get("debit"))
        credit = _dec(row.get("credit"))
        balance = _dec(row.get("balance"))
        if debit is None or credit is None or balance is None:
            discrepancies.append(_disc(
                DISC_LEDGER_ACCOUNT_INCONSISTENT,
                f"Ledger row for '{account}' is unreadable.", account=account))
            continue
        # Opening balance is 0 for a fresh question: 0 + Debit - Credit
        closing = debit - credit
        if closing != balance:
            discrepancies.append(_disc(
                DISC_LEDGER_ACCOUNT_INCONSISTENT,
                f"'{account}': Opening(0) + Debit {debit} - Credit {credit} "
                f"= {closing}, recorded closing is {balance}.",
                account=account, expected=closing, given=balance))
    total_debit = _dec(ledger.get("total_debit"))
    total_credit = _dec(ledger.get("total_credit"))
    if total_debit is not None and total_credit is not None \
            and total_debit != total_credit:
        discrepancies.append(_disc(
            DISC_LEDGER_UNBALANCED,
            f"Ledger total Debit {total_debit} != total Credit "
            f"{total_credit}.", total_debit=_fmt(total_debit),
            total_credit=_fmt(total_credit)))
    state = REVIEW_REQUIRED if discrepancies else OK_STATE
    return {"state": state, "discrepancies": discrepancies,
            "checks": {"balanced": total_debit == total_credit}}


def validate_trial_balance(tb: Dict[str, Any]) -> Dict[str, Any]:
    """Trial-balance validation: total Debit == total Credit and sane rows."""
    discrepancies: List[Dict[str, Any]] = []
    rows = (tb or {}).get("rows") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account = row.get("account") or ""
        debit = _dec(row.get("debit"))
        credit = _dec(row.get("credit"))
        if debit is not None and debit < 0 or credit is not None and credit < 0:
            discrepancies.append(_disc(
                DISC_TB_ROW_INCONSISTENT,
                f"Trial-balance row '{account}' has a negative amount.",
                account=account))
        if debit and credit:
            discrepancies.append(_disc(
                DISC_TB_ROW_INCONSISTENT,
                f"Trial-balance row '{account}' carries both a debit and a "
                f"credit amount.", account=account))
    total_debit = _dec(tb.get("total_debit"))
    total_credit = _dec(tb.get("total_credit"))
    if total_debit is not None and total_credit is not None \
            and total_debit != total_credit:
        discrepancies.append(_disc(
            DISC_TB_UNBALANCED,
            f"Trial balance total Debit {total_debit} != total Credit "
            f"{total_credit}.", total_debit=_fmt(total_debit),
            total_credit=_fmt(total_credit)))
    state = REVIEW_REQUIRED if discrepancies else OK_STATE
    return {"state": state, "discrepancies": discrepancies,
            "checks": {"balanced": total_debit == total_credit}}


def validate_pipeline(out: Dict[str, Any]) -> Dict[str, Any]:
    """Whole-result validation: every VERIFIED journal, the ledger, the trial
    balance, and the confident-result provenance invariants."""
    discrepancies: List[Dict[str, Any]] = []
    checks: Dict[str, bool] = {}
    status = out.get("status")
    if status == VERIFIED:
        journals = out.get("journals") or [out.get("journal")] or []
        if not journals:
            discrepancies.append(_disc(
                DISC_MISSING_JOURNAL_LINE,
                "A VERIFIED result carried no journal."))
        for idx, j in enumerate(journals, start=1):
            result = validate_journal(j)
            for d in result["discrepancies"]:
                d = dict(d)
                d["journal_index"] = idx
                discrepancies.append(d)
            checks[f"journal_{idx}_balanced"] = \
                result["checks"]["balanced"]
        ledger = out.get("ledger") or {}
        tb = out.get("trial_balance") or {}
        ledger_result = validate_ledger(ledger)
        tb_result = validate_trial_balance(tb)
        discrepancies.extend(ledger_result["discrepancies"])
        discrepancies.extend(tb_result["discrepancies"])
        checks["ledger_balanced"] = ledger_result["checks"]["balanced"]
        checks["trial_balance_balanced"] = tb_result["checks"]["balanced"]
        # provenance invariant: every VERIFIED journal carries calculation
        # provenance (never a confident answer without traced numbers)
        for idx, j in enumerate(journals, start=1):
            ids = [r.get("calculation_id")
                   for r in (j.get("calculation_records") or [])
                   if r.get("calculation_id")]
            if not ids:
                discrepancies.append(_disc(
                    DISC_FORMULA_ID_NONE_CONFIDENT,
                    f"VERIFIED journal {idx} has no calculation provenance.",
                    journal_index=idx))
            checks[f"journal_{idx}_provenance"] = bool(ids)
    else:
        checks["refusal_clean"] = not (
            out.get("debit_lines") or out.get("credit_lines"))
        if not checks["refusal_clean"]:
            discrepancies.append(_disc(
                DISC_INVENTED_ACCOUNT,
                "A refusal produced journal lines (fabricated output)."))
    state = REVIEW_REQUIRED if discrepancies else OK_STATE
    return {"state": state, "discrepancies": discrepancies, "checks": checks}


# ---------------------------------------------------------------------------
# 6. C++ authority performance (persistent worker + equivalence guarantee)
# ---------------------------------------------------------------------------


class CppAuthorityWorker:
    """Persistent transport for the compiled C++ authority (`--worker` mode).

    One long-lived subprocess; one JSON document per line in, one result per
    line out. Results are byte-identical to the one-shot CLI (same run_cli
    path). On any I/O failure the worker is torn down and the caller falls
    back to the one-shot path - correctness and determinism are never
    weakened."""

    def __init__(self, bin_path: Optional[str] = None) -> None:
        self._bin = bin_path or binary_path()
        self._proc: Optional[subprocess.Popen] = None
        self._broken = False

    @property
    def available(self) -> bool:
        return self._bin is not None

    def _ensure(self) -> Optional[subprocess.Popen]:
        if self._broken or self._bin is None:
            return None
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            self._proc = subprocess.Popen(
                [self._bin, "--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
            )
            return self._proc
        except (OSError, ValueError):
            self._broken = True
            return None

    def submit(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send one payload and return the normalized result (same shape as
        cpp_calculate), or None on failure (caller falls back)."""
        proc = self._ensure()
        if proc is None or proc.stdin is None or proc.stdout is None:
            return None
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                self._teardown()
                return None
            out = json.loads(line)
        except (OSError, ValueError, subprocess.SubprocessError):
            self._teardown()
            return None
        return _normalize_cpp_output(out)

    def _teardown(self) -> None:
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                self._proc.kill()
            except (OSError, ValueError):
                pass
            self._proc = None
        self._broken = True

    def close(self) -> None:
        self._teardown()

    def __enter__(self) -> "CppAuthorityWorker":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _normalize_cpp_output(out: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Shared result normalization for the one-shot and worker paths so the
    equivalence guarantee is structural (identical shape and semantics)."""
    if not isinstance(out, dict) or out.get("error"):
        return None
    status = _STATUS_MAP.get(str(out.get("status") or ""))
    if status is None:
        return None
    return {
        "status": status,
        "value": out.get("value"),
        "display_value": out.get("display_value") or "",
        "steps": out.get("calculation_steps") or [],
        "lineage": out.get("lineage") or "",
        "block_reason": out.get("block_reason"),
    }


def cpp_authority_execute(metric_key: str,
                          resolved_facts: Dict[str, Dict[str, Any]],
                          solve_for: str = "",
                          worker: Optional["CppAuthorityWorker"] = None,
                          ) -> Dict[str, Any]:
    """Optimized C++ authority execution with an exact equivalence check.

    Uses the persistent worker when available, verifies the result equals
    the one-shot CLI result, and records the outcome deterministically.
    Returns {authority_state, formula_id, matched, worker_result,
    one_shot_result, reason}."""
    if not binary_path():
        return {
            "authority_state": "engine_unavailable",
            "formula_id": metric_key,
            "matched": None,
            "reason": "Compiled C++ authority not deployed - no Python "
                      "fallback.",
        }
    one_shot = (cpp_solve_metric(metric_key, solve_for, resolved_facts)
                if solve_for else cpp_calculate(metric_key, resolved_facts))
    worker_result = None
    if worker is not None:
        payload: Dict[str, Any] = {
            "metric": metric_key,
            "inputs": {k: _fact_json(f) for k, f in resolved_facts.items()},
        }
        if solve_for:
            payload["solve_for"] = solve_for
        worker_result = worker.submit(payload)
    if worker_result is not None and one_shot is not None:
        matched = _cpp_results_equal(one_shot, worker_result)
        return {
            "authority_state": "cpp",
            "formula_id": metric_key,
            "matched": matched,
            "worker_result": worker_result,
            "one_shot_result": one_shot,
            "reason": ("Persistent worker result matches the one-shot C++ "
                       "authority byte-for-byte."
                       if matched else "Worker/one-shot result MISMATCH."),
        }
    if worker_result is None and one_shot is not None:
        return {
            "authority_state": "cpp",
            "formula_id": metric_key,
            "matched": None,
            "one_shot_result": one_shot,
            "reason": "Worker unavailable; one-shot C++ authority used.",
        }
    if one_shot is None:
        return {
            "authority_state": "unsupported",
            "formula_id": metric_key,
            "matched": None,
            "reason": "The C++ authority did not compute this metric.",
        }
    return {
        "authority_state": "cpp",
        "formula_id": metric_key,
        "matched": False,
        "reason": "Worker result was lost; one-shot result used.",
    }


def _fact_json(fact: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("metric", "unit", "scale", "reporting_period",
                "provenance_tier", "document_name", "page", "evidence",
                "provider", "source_ref"):
        v = fact.get(key) if isinstance(fact, dict) else None
        if v not in (None, ""):
            out[key] = str(v)
    v = fact.get("value") if isinstance(fact, dict) else None
    if v is not None:
        try:
            out["value"] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def _cpp_results_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Exact structural equality of two normalized C++ results (Decimals and
    numbers are compared canonically)."""
    if a is None or b is None:
        return False
    for key in ("status", "value", "display_value", "block_reason"):
        if a.get(key) != b.get(key):
            return False
    if list(a.get("steps") or []) != list(b.get("steps") or []):
        return False
    if (a.get("lineage") or "") != (b.get("lineage") or ""):
        return False
    return True


# ---------------------------------------------------------------------------
# Registry snapshots (deterministic, for the audit/replay headers)
# ---------------------------------------------------------------------------


def canonical_registry_snapshot() -> Dict[str, Any]:
    return {
        "registry_version": REGISTRY_VERSION,
        "formula_count": len(CANONICAL_REGISTRY),
        "formula_ids": sorted(CANONICAL_REGISTRY.all_ids()),
        "executable_formula_ids": sorted(FYJC_FORMULA_REGISTRY.all_ids()),
        "bk_rules": [r.get("rule_id") for r in
                     sorted(BK_RULE_BY_CLASS.values(),
                            key=lambda r: r.get("rule_id") or "")],
        "pattern_count": len(BK_PATTERN_LIBRARY),
    }
