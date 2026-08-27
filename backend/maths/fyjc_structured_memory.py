"""
Platrixa
Sprint 43 — Deterministic Structured Working Memory + Ambiguity Detection

A lightweight state representation around the existing transaction pipeline.
This is NOT neural memory.  Every value is exact Decimal arithmetic.

Architecture:

    Question
      → Splitter (existing)
      → Structured Working Memory (this module)
      → Existing orchestrate() per transaction
      → LedgerState update (existing)
      → Student UI projection (existing)

The working-memory layer may organise and retrieve state.
It must NOT independently calculate accounting truth.

Safety rules:
  - Never approximate money (Decimal only)
  - Never infer missing amounts
  - Never invent payment methods
  - Never overwrite historical transactions
  - Never bypass the kernel
  - Never bypass integrity validation

Pure module: no Streamlit, no AI, no network.  Deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED

# ---------------------------------------------------------------------------
# Status constants (local aliases — never override the canonical ones)
# ---------------------------------------------------------------------------
NOT_SUPPORTED = "NOT_SUPPORTED"
INVALID_INPUT_MATH = "INVALID_INPUT_MATH"

# ---------------------------------------------------------------------------
# Transaction identity
# ---------------------------------------------------------------------------

@dataclass
class TransactionIdentity:
    """Immutable identity for one transaction in the problem."""
    index: int                          # 1-based chronological order
    text: str                           # original student text
    enriched_text: Optional[str] = None # after historical resolution
    event_type: str = "ACCOUNTING_TRANSACTION"


# ---------------------------------------------------------------------------
# Per-transaction working memory
# ---------------------------------------------------------------------------

@dataclass
class TransactionMemory:
    """Deterministic working memory for a single transaction.

    All monetary values are Decimal.  Nothing here is approximated.
    """
    identity: TransactionIdentity
    parties: List[str] = field(default_factory=list)
    accounts_debited: List[str] = field(default_factory=list)
    accounts_credited: List[str] = field(default_factory=list)
    amount: Optional[Decimal] = None
    payment_method: Optional[str] = None   # "cash" / "bank" / "credit" / None
    outstanding_party_balance: Optional[Decimal] = None
    purchases: List[str] = field(default_factory=list)
    sales: List[str] = field(default_factory=list)
    returns: List[str] = field(default_factory=list)
    trade_discount: Optional[Decimal] = None
    cash_discount: Optional[Decimal] = None
    gst_rate: Optional[Decimal] = None
    previous_transaction_refs: List[int] = field(default_factory=list)
    unresolved_fields: List[str] = field(default_factory=list)
    status: Optional[str] = None
    journal: Optional[Dict[str, Any]] = None
    calculation_records: List[Dict[str, Any]] = field(default_factory=list)
    state_delta: Optional[Dict[str, Any]] = None
    clarification_question: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic snapshot — safe for serialization."""
        return {
            "index": self.identity.index,
            "text": self.identity.text,
            "parties": list(self.parties),
            "amount": str(self.amount) if self.amount is not None else None,
            "payment_method": self.payment_method,
            "unresolved_fields": list(self.unresolved_fields),
            "status": self.status,
            "has_journal": self.journal is not None,
            "clarification_question": self.clarification_question,
        }


# ---------------------------------------------------------------------------
# Problem-level working memory
# ---------------------------------------------------------------------------

@dataclass
class ProblemMemory:
    """Deterministic working memory for the entire multi-transaction problem.

    Tracks per-transaction state and cross-transaction dependencies
    (party balances, historical references).
    """
    problem_text: str
    transactions: List[TransactionMemory] = field(default_factory=list)
    party_balances: Dict[str, Decimal] = field(default_factory=dict)
    account_balances: Dict[str, Decimal] = field(default_factory=dict)
    unresolved_items: List[Dict[str, Any]] = field(default_factory=list)
    historical_index: List[Dict[str, Any]] = field(default_factory=list)

    def add_transaction(self, mem: TransactionMemory) -> None:
        self.transactions.append(mem)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "problem_text": self.problem_text,
            "transaction_count": len(self.transactions),
            "tx_snapshots": [t.snapshot() for t in self.transactions],
            "party_balances": {k: str(v) for k, v in sorted(self.party_balances.items())},
            "account_balances": {k: str(v) for k, v in sorted(self.account_balances.items())},
            "unresolved_count": len(self.unresolved_items),
        }


# ---------------------------------------------------------------------------
# Payment mode detection
# ---------------------------------------------------------------------------

# Patterns that deterministically indicate CASH payment
_CASH_PATTERNS = re.compile(
    r"\b(?:for\s+cash|by\s+cash|in\s+cash|cash\s+(?:purchase|sale|payment)|"
    r"paid\s+in\s+cash|received\s+in\s+cash)\b",
    re.IGNORECASE,
)

# Patterns that deterministically indicate BANK/CHEQUE payment
_BANK_PATTERNS = re.compile(
    r"\b(?:by\s+cheque|by\s+chq|by\s+bank|by\s+neft|by\s+upi|by\s+rtgs|"
    r"through\s+bank|via\s+bank|by\s+draft|cheque\s+(?:payment|received)|"
    r"bank\s+(?:transfer|payment))\b",
    re.IGNORECASE,
)

# Patterns that deterministically indicate CREDIT (no immediate payment)
_CREDIT_PATTERNS = re.compile(
    r"\b(?:on\s+credit|credit\s+(?:purchase|sale|from|to)|"
    r"(?:goods|stock)\s+(?:purchased|bought|sold)\s+(?:from|to)\s+\w+"
    r"(?:\s+(?:on|for)\s+credit)?)\b",
    re.IGNORECASE,
)

# Purchase verbs (used to detect if a transaction is a purchase)
_PURCHASE_VERBS = re.compile(
    r"\b(?:purchas|bought|acqui)\w*",
    re.IGNORECASE,
)

# Settlement / payment verbs
_SETTLEMENT_VERBS = re.compile(
    r"\b(?:settled?|paid|payment|receipt|received)\b",
    re.IGNORECASE,
)


def detect_payment_method(text: str) -> Optional[str]:
    """Deterministically detect the payment mode from transaction text.

    Returns:
        "cash"    — explicit cash payment
        "bank"    — explicit cheque/bank/NEFT/UPI/RTGS
        "credit"  — explicit credit purchase/sale
        None      — payment mode is ambiguous / not stated
    """
    low = " " + str(text or "").lower() + " "

    # Explicit cash
    if _CASH_PATTERNS.search(low):
        return "cash"

    # Explicit bank/cheque
    if _BANK_PATTERNS.search(low):
        return "bank"

    # Explicit credit
    if _CREDIT_PATTERNS.search(low):
        return "credit"

    return None


def is_purchase_transaction(text: str) -> bool:
    """True when the text describes a purchase (goods coming in)."""
    return bool(_PURCHASE_VERBS.search(str(text or "")))


def is_settlement_transaction(text: str) -> bool:
    """True when the text describes a settlement / payment / receipt."""
    return bool(_SETTLEMENT_VERBS.search(str(text or "")))


# ---------------------------------------------------------------------------
# Cash/Credit ambiguity detection (Priority 1)
# ---------------------------------------------------------------------------

def detect_cash_credit_ambiguity(text: str) -> Optional[Dict[str, Any]]:
    """Detect whether a purchase/sale transaction has ambiguous payment mode.

    Only fires for transactions that LOOK like purchases or sales but do
    not specify the payment mode.

    Returns None when:
      - the payment mode is explicit (cash / bank / credit)
      - the transaction is not a purchase or sale
    Returns a clarification dict when the mode is genuinely ambiguous.
    """
    low = " " + str(text or "").lower() + " "

    # If payment mode is already explicit, no ambiguity
    if detect_payment_method(text) is not None:
        return None

    # Only flag purchases and sales (not expenses, drawings, settlements)
    has_purchase = bool(_PURCHASE_VERBS.search(low))
    has_sale = bool(re.search(r"\b(?:sold|selling|sale\s+of)\b", low))

    if not has_purchase and not has_sale:
        return None

    # Check if the text has an amount (otherwise it's a different kind of issue)
    has_amount = bool(re.search(r"(?:₹|Rs\.?|INR)\s*[\d,]+", text or ""))
    if not has_amount:
        return None

    # This is a purchase/sale with an amount but no payment mode → ambiguous
    return {
        "type": "CASH_CREDIT_AMBIGUITY",
        "gate_id": "CASH_CREDIT",
        "question": "Was this transaction for cash or on credit?",
        "clarification": (
            "Platrixa understood this transaction, but the payment mode "
            "is not specified. Was this for cash or on credit?"
        ),
        "alternatives": [
            {
                "id": "cash",
                "label": "For cash",
                "effect": "The payment is recorded as a cash transaction.",
            },
            {
                "id": "credit",
                "label": "On credit",
                "effect": "The purchase/sale is on credit from/to a party.",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Settlement without explicit amount detection (Priority 2)
# ---------------------------------------------------------------------------

def detect_settlement_ambiguity(
    text: str,
    historical_index: List[Dict[str, Any]],
    party_outstanding: Optional[Decimal] = None,
) -> Optional[Dict[str, Any]]:
    """Detect settlement transactions where the amount cannot be uniquely determined.

    Uses historical state to determine if the outstanding balance is known.

    Returns None when:
      - the settlement has an explicit amount
      - the outstanding balance is unambiguous and equals the payment
    Returns a clarification dict when the amount is genuinely unknown.
    """
    low = " " + str(text or "").lower() + " "

    # Only for settlement-type transactions
    if not is_settlement_transaction(text):
        return None

    # Check if an explicit amount is present
    has_explicit_amount = bool(re.search(
        r"(?:₹|Rs\.?|INR)\s*[\d,]+(?:\.\d+)?",
        text or "",
    ))

    if has_explicit_amount:
        return None  # Amount is explicit, no ambiguity

    # Check for "settled in full" / "full settlement" wording
    is_full_settlement = bool(re.search(
        r"\b(?:settled?\s+(?:in\s+full|his\s+account|the\s+account|fully)|"
        r"full\s+settlement|paid\s+off|cleared?\s+(?:his|the)\s+(?:account|balance))\b",
        low,
    ))

    if is_full_settlement and party_outstanding is not None and party_outstanding > 0:
        # Outstanding is known → settlement amount = outstanding balance
        # This is deterministically resolvable
        return None

    if is_full_settlement and party_outstanding is None:
        # Outstanding is unknown → cannot determine settlement amount
        return {
            "type": "SETTLEMENT_NO_AMOUNT",
            "gate_id": "SETTLEMENT_AMOUNT",
            "question": "How much was paid to settle the account?",
            "clarification": (
                "Platrixa understood that an account is being settled, but "
                "the settlement amount is not specified and the historical "
                "balance is not available. How much was paid?"
            ),
        }

    if is_full_settlement and party_outstanding is not None and party_outstanding <= 0:
        # Account already settled / zero balance → ambiguous
        return {
            "type": "SETTLEMENT_ZERO_BALANCE",
            "gate_id": "SETTLEMENT_AMOUNT",
            "question": "The account balance appears to be zero. What was paid?",
            "clarification": (
                "Platrixa found that the outstanding balance for this party "
                "appears to be zero. What amount was paid in this transaction?"
            ),
        }

    # Non-full-settlement payment without explicit amount
    has_payment_word = bool(re.search(
        r"\b(?:paid|payment|received|receipt)\b", low,
    ))
    if has_payment_word and not has_explicit_amount:
        return {
            "type": "SETTLEMENT_NO_AMOUNT",
            "gate_id": "SETTLEMENT_AMOUNT",
            "question": "How much was paid/received?",
            "clarification": (
                "Platrixa understood this as a payment/receipt transaction, "
                "but the amount is not specified. How much was paid or received?"
            ),
        }

    return None


# ---------------------------------------------------------------------------
# Build structured memory for a transaction
# ---------------------------------------------------------------------------

def build_transaction_memory(
    index: int,
    text: str,
    enriched_text: Optional[str] = None,
    historical_index: Optional[List[Dict[str, Any]]] = None,
    party_outstanding: Optional[Decimal] = None,
) -> TransactionMemory:
    """Build deterministic working memory for a single transaction.

    Analyses the text to extract parties, payment method, and detect
    ambiguities.  Does NOT compute accounting truth — that is the
    kernel's job.
    """
    mem = TransactionMemory(
        identity=TransactionIdentity(
            index=index,
            text=text,
            enriched_text=enriched_text,
        ),
    )

    # Extract parties
    party_pat = re.compile(
        r"\b(?:from|to|paid\s+to|received\s+from|purchased\s+from|"
        r"sold\s+to|settled?\s+(?:with|to|his\s+account\s+with))\s+"
        r"([A-Z][A-Za-z.' ]+?)(?:\s|,|\.|;|$)",
        re.IGNORECASE,
    )
    for m in party_pat.finditer(text):
        name = m.group(1).strip().rstrip(".,;:")
        if name and len(name) > 1 and name.lower() not in (
            "the", "a", "an", "his", "her", "its", "goods", "stock",
        ):
            mem.parties.append(name)
    mem.parties = list(dict.fromkeys(mem.parties))  # dedupe preserving order

    # Extract amount
    amt_pat = re.compile(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)")
    amounts = []
    for m in amt_pat.finditer(text):
        try:
            amounts.append(Decimal(m.group(1).replace(",", "")))
        except InvalidOperation:
            pass
    if amounts:
        mem.amount = max(amounts)  # largest amount is typically the transaction value

    # Detect payment method
    mem.payment_method = detect_payment_method(text)

    # Detect ambiguities
    cash_credit_amb = detect_cash_credit_ambiguity(text)
    settlement_amb = detect_settlement_ambiguity(
        text, historical_index or [], party_outstanding,
    )

    if cash_credit_amb is not None:
        mem.unresolved_fields.append("payment_mode")
        mem.clarification_question = cash_credit_amb["clarification"]

    if settlement_amb is not None:
        mem.unresolved_fields.append("settlement_amount")
        mem.clarification_question = settlement_amb["clarification"]

    # Detect GST
    gst_match = re.search(r"(?:gst|igst|cgst|sgst)\s*@\s*(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    if not gst_match:
        gst_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:gst|igst|cgst|sgst)", text, re.IGNORECASE)
    if gst_match:
        try:
            mem.gst_rate = Decimal(gst_match.group(1))
        except InvalidOperation:
            pass

    # Detect trade discount
    td_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:trade\s+discount|td)", text, re.IGNORECASE)
    if td_match:
        try:
            mem.trade_discount = Decimal(td_match.group(1))
        except InvalidOperation:
            pass

    return mem


# ---------------------------------------------------------------------------
# Build problem-level memory
# ---------------------------------------------------------------------------

def build_problem_memory(
    problem_text: str,
    transactions: List[str],
    historical_index: Optional[List[Any]] = None,
    ledger_balances: Optional[Dict[str, Decimal]] = None,
) -> ProblemMemory:
    """Build deterministic working memory for the entire problem.

    Iterates through transactions, building per-transaction memory and
    tracking cross-transaction state (party balances).
    """
    memory = ProblemMemory(problem_text=problem_text)

    # Seed account balances from ledger
    if ledger_balances:
        memory.account_balances.update(ledger_balances)

    # Build historical index for lookups
    hist_refs = []
    if historical_index:
        for ref in historical_index:
            if hasattr(ref, "transaction_index"):
                hist_refs.append({
                    "transaction_index": ref.transaction_index,
                    "entity": ref.entity,
                    "event_type": ref.event_type,
                    "amount": ref.amount,
                })
            elif isinstance(ref, dict):
                hist_refs.append(ref)
    memory.historical_index = hist_refs

    for i, tx_text in enumerate(transactions):
        tx_index = i + 1

        # Determine outstanding balance for settlement detection
        party_outstanding = None
        # Check if any party from this transaction has a known outstanding balance
        party_pat = re.compile(
            r"(?:from|to|paid\s+to|received\s+from|purchased\s+from|"
            r"sold\s+to|settled?\s+(?:with|to|his\s+account\s+with))\s+"
            r"([A-Z][A-Za-z.' ]+?)(?:\s|,|\.|;|$)",
            re.IGNORECASE,
        )
        for m in party_pat.finditer(tx_text):
            name = m.group(1).strip().rstrip(".,;:")
            if name and len(name) > 1 and name.lower() not in (
                "the", "a", "an", "his", "her", "its", "goods", "stock",
            ):
                if name in memory.party_balances:
                    party_outstanding = memory.party_balances[name]

        mem = build_transaction_memory(
            index=tx_index,
            text=tx_text,
            historical_index=hist_refs,
            party_outstanding=party_outstanding,
        )

        memory.add_transaction(mem)

        # Track unresolved items
        if mem.unresolved_fields:
            memory.unresolved_items.append({
                "transaction_index": tx_index,
                "fields": list(mem.unresolved_fields),
                "clarification": mem.clarification_question,
            })

    return memory


# ---------------------------------------------------------------------------
# Re-resolve with clarified transaction
# ---------------------------------------------------------------------------

def re_resolve_with_clarification(
    problem_text: str,
    transactions: List[str],
    transaction_index: int,
    clarification_text: str,
    historical_index: Optional[List[Any]] = None,
    ledger_balances: Optional[Dict[str, Decimal]] = None,
) -> ProblemMemory:
    """Re-build problem memory with a clarified transaction text.

    Replaces the original transaction text at the given index with the
    clarified version, then rebuilds the full memory.

    This is deterministic — the same inputs always produce the same output.
    """
    if transaction_index < 1 or transaction_index > len(transactions):
        # Return original memory unchanged
        return build_problem_memory(
            problem_text, transactions, historical_index, ledger_balances,
        )

    # Replace the target transaction
    clarified_transactions = list(transactions)
    clarified_transactions[transaction_index - 1] = clarification_text

    return build_problem_memory(
        problem_text,
        clarified_transactions,
        historical_index,
        ledger_balances,
    )
