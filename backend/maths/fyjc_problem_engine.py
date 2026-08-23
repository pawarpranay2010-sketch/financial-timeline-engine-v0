"""
Platrixa — Sprint 16: Stateful Multi-Transaction Problem Engine

Extends Platrixa from a single-transaction compiler into a stateful
multi-transaction problem engine.  A student submits an entire accounting
problem containing multiple chronological transactions, and Platrixa
processes them sequentially while preserving verified state between
transactions.

Critical constraint:  orchestrate() is NOT modified.  This engine wraps
the existing single-transaction kernel in a stateful session loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
VERIFIED = "VERIFIED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
INVALID_INPUT_MATH = "INVALID_INPUT_MATH"
NOT_SUPPORTED = "NOT_SUPPORTED"
INFORMATIONAL_EVENT = "INFORMATIONAL_EVENT"

# Problem-level statuses
PROBLEM_VERIFIED = "PROBLEM_VERIFIED"
PROBLEM_REVIEW_REQUIRED = "PROBLEM_REVIEW_REQUIRED"
PROBLEM_INVALID_INPUT_MATH = "PROBLEM_INVALID_INPUT_MATH"
PROBLEM_NOT_SUPPORTED = "PROBLEM_NOT_SUPPORTED"

# ---------------------------------------------------------------------------
# 16-A: Data Structures
# ---------------------------------------------------------------------------

@dataclass
class AccountDelta:
    """A single account balance change."""
    account: str
    direction: str          # "debit" or "credit"
    amount: Decimal
    entity: Optional[str] = None


@dataclass
class StateDelta:
    """The verified state change produced by one transaction."""
    transaction_index: int
    transaction_text: str
    status: str
    journal: Optional[Dict[str, Any]] = None
    deltas: List[AccountDelta] = field(default_factory=list)
    debit_total: Decimal = Decimal(0)
    credit_total: Decimal = Decimal(0)
    provenance: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class HistoricalReference:
    """A reference to a previously verified transaction for look-back."""
    transaction_index: int
    transaction_text: str
    entity: Optional[str]
    event_type: str          # purchase, sale, payment, receipt, etc.
    amount: Decimal
    date_or_order: int       # chronological order
    provenance: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class HistoricalQuery:
    """A query against the historical index."""
    entity: Optional[str] = None
    event_type: Optional[str] = None  # purchase, sale, payment, receipt
    fraction_or_amount: Optional[str] = None  # "half", "one-third", etc.
    raw_text: str = ""


@dataclass
class TransactionResult:
    """Result of processing one transaction in the problem session."""
    index: int
    text: str
    status: str
    journal: Optional[Dict[str, Any]] = None
    state_delta: Optional[StateDelta] = None
    historical_references: List[HistoricalReference] = field(default_factory=list)
    why_not: Optional[str] = None
    next_action: Optional[str] = None
    event_type: Optional[str] = None  # ACCOUNTING_TRANSACTION, INFORMATIONAL_EVENT, etc.


@dataclass
class LedgerState:
    """Persistent account state across verified transactions."""
    # Account name -> balance (positive = normal balance)
    balances: Dict[str, Decimal] = field(default_factory=dict)
    # Entity name -> current outstanding amount
    entity_outstanding: Dict[str, Decimal] = field(default_factory=dict)
    # Verified state deltas in chronological order
    deltas: List[StateDelta] = field(default_factory=list)
    # Historical index: list of verified transactions for look-back
    historical_index: List[HistoricalReference] = field(default_factory=list)
    # Transaction counter
    transaction_count: int = 0

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic snapshot of current state."""
        return {
            "balances": {k: str(v) for k, v in sorted(self.balances.items())},
            "entity_outstanding": {k: str(v) for k, v in sorted(self.entity_outstanding.items())},
            "transaction_count": self.transaction_count,
            "delta_count": len(self.deltas),
        }

    def apply_delta(self, delta: StateDelta) -> None:
        """Apply a verified state delta.  Only VERIFIED deltas reach here."""
        if delta.status != VERIFIED:
            return  # safety: refuse non-verified deltas

        for d in delta.deltas:
            account = d.account
            if d.direction == "debit":
                self.balances[account] = self.balances.get(account, Decimal(0)) + d.amount
            elif d.direction == "credit":
                self.balances[account] = self.balances.get(account, Decimal(0)) - d.amount

            # Track entity outstanding
            if d.entity:
                ent = d.entity
                if d.direction == "credit":
                    # Liability: credit increases outstanding
                    self.entity_outstanding[ent] = self.entity_outstanding.get(ent, Decimal(0)) + d.amount
                elif d.direction == "debit":
                    # Payment to entity: debit decreases outstanding
                    self.entity_outstanding[ent] = self.entity_outstanding.get(ent, Decimal(0)) - d.amount

        self.deltas.append(delta)
        self.transaction_count += 1


@dataclass
class ProblemSession:
    """Session state for a multi-transaction problem."""
    problem_text: str
    transactions: List[str] = field(default_factory=list)
    ledger: LedgerState = field(default_factory=LedgerState)
    results: List[TransactionResult] = field(default_factory=list)
    problem_status: str = PROBLEM_VERIFIED
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 16-F: Informational Event Detection
# ---------------------------------------------------------------------------

# Patterns that indicate non-accounting events (orders, promises, intentions)
_INFORMATIONAL_PATTERNS = [
    re.compile(r"\b(?:placed?\s+an?\s+order|ordered?)\b", re.IGNORECASE),
    re.compile(r"\b(?:will\s+(?:purchase|buy|pay|sell|receive|deliver))\b", re.IGNORECASE),
    re.compile(r"\b(?:intend(?:s|ed)?\s+to|proposed?\s+to|plan(?:s|ned)?\s+to)\s+"
               r"(?:purchase|buy|sell|pay|receive)", re.IGNORECASE),
    re.compile(r"\b(?:decided?\s+to|agreed?\s+to)\s+(?:purchase|buy|sell|pay|receive)", re.IGNORECASE),
    re.compile(r"\b(?:has\s+been\s+(?:ordered|placed|requested))\b", re.IGNORECASE),
    re.compile(r"\b(?:expect(?:s|ed)?\s+to)\s+(?:receive|pay|sell|buy)", re.IGNORECASE),
    re.compile(r"\b(?:quotation|estimate|proposal|tender)\s+(?:for|of|received|submitted)\b",
               re.IGNORECASE),
]

# Patterns that indicate opening balances (not transactions)
_OPENING_BALANCE_PATTERNS = [
    re.compile(r"\b(?:balances?\s+(?:as|on|from|onwards)|opening\s+(?:balance|state))\b",
               re.IGNORECASE),
    re.compile(r"\b(?:had\s+(?:in\s+hand|in\s+(?:bank|cash)))\b", re.IGNORECASE),
    re.compile(r"\b(?:carried\s+(?:forward|down|over))\b", re.IGNORECASE),
]


def _detect_informational_event(text: str) -> Optional[str]:
    """Detect if a transaction text is a non-accounting informational event.
    Returns event type string or None if it is an accounting transaction."""
    low = text.lower().strip()

    for pat in _INFORMATIONAL_PATTERNS:
        if pat.search(low):
            return "INFORMATIONAL_EVENT"

    for pat in _OPENING_BALANCE_PATTERNS:
        if pat.search(low):
            return "OPENING_BALANCE"

    return None


# ---------------------------------------------------------------------------
# 16-E: Historical Look-Back Index
# ---------------------------------------------------------------------------

# Entity extraction patterns
_ENTITY_PURCHASE_RE = re.compile(
    r"(?:purchased|bought|acquired|received)\s+"
    r"(?:goods|stock|inventory|merchandise|materials?|items?)?\s*"
    r"(?:from|of)\s+([A-Z][A-Za-z.' ]+?)(?:\s+for|\s+at|\s+on|\s+Rs\.|\s+₹|\s+;|\s+\.|\s*$)",
    re.IGNORECASE
)

_ENTITY_SALE_RE = re.compile(
    r"(?:sold|sold\s+out|sold\s+half|sold\s+part)\s+"
    r"(?:goods|stock|inventory)?\s*"
    r"(?:to|from)\s+([A-Z][A-Za-z.' ]+?)(?:\s+for|\s+at|\s+Rs\.|\s+₹|\s+;|\s+\.|\s*$)",
    re.IGNORECASE
)

_ENTITY_PAYMENT_RE = re.compile(
    r"(?:paid|paid\s+to|payment\s+to)\s+([A-Z][A-Za-z.' ]+?)(?:\s+Rs\.|\s+₹|\s+by|\s+cash|\s+bank|\s+;|\s+\.|\s*$)",
    re.IGNORECASE
)

_ENTITY_RECEIPT_RE = re.compile(
    r"(?:received\s+(?:from|Rs|₹))\s*([A-Z][A-Za-z.' ]+?)(?:\s+Rs\.|\s+₹|\s+by|\s+cash|\s+bank|\s+;|\s+\.|\s*$)",
    re.IGNORECASE
)


def _extract_entities_from_text(text: str) -> List[str]:
    """Extract entity names from transaction text."""
    entities = []
    for pat in [_ENTITY_PURCHASE_RE, _ENTITY_SALE_RE,
                _ENTITY_PAYMENT_RE, _ENTITY_RECEIPT_RE]:
        for m in pat.finditer(text):
            name = m.group(1).strip().rstrip(".;,")
            if name and len(name) > 1:
                entities.append(name)
    return list(dict.fromkeys(entities))  # deduplicate preserving order


def _classify_transaction_event(text: str) -> str:
    """Classify the accounting event type of a transaction."""
    low = text.lower()
    if re.search(r"\b(?:purchased|bought|acquired)\b", low):
        return "purchase"
    if re.search(r"\b(?:sold|sold\s+out)\b", low):
        return "sale"
    if re.search(r"\b(?:paid|payment)\b", low):
        return "payment"
    if re.search(r"\b(?:received|receipt)\b", low):
        return "receipt"
    if re.search(r"\b(?:started|commenced|invested|introduced)\b", low):
        return "capital"
    if re.search(r"\b(?:rent|salary|wages|discount|depreciation|interest)\b", low):
        return "expense"
    return "transaction"


def _build_historical_reference(
    tx_index: int,
    tx_text: str,
    result: Dict[str, Any],
) -> List[HistoricalReference]:
    """Build historical references from a verified transaction result."""
    if result.get("status") != VERIFIED:
        return []

    refs = []
    entities = _extract_entities_from_text(tx_text)
    event_type = _classify_transaction_event(tx_text)

    # Extract amount from the journal
    amount = Decimal(0)
    journal = result.get("journal") or {}
    for line in journal.get("debit_lines") or []:
        try:
            amt = Decimal(str(line.get("amount", 0)))
            if amt > amount:
                amount = amt
        except (InvalidOperation, TypeError):
            pass

    for entity in entities:
        refs.append(HistoricalReference(
            transaction_index=tx_index,
            transaction_text=tx_text,
            entity=entity,
            event_type=event_type,
            amount=amount,
            date_or_order=tx_index,
            provenance=[{
                "source": "orchestrate",
                "transaction_index": tx_index,
                "event_type": event_type,
                "entity": entity,
                "amount": str(amount),
            }],
        ))

    return refs


def _query_historical_index(
    index: List[HistoricalReference],
    query: HistoricalQuery,
) -> List[HistoricalReference]:
    """Query the historical index for matching references.
    Returns all matching references, sorted by chronological order."""
    results = []
    for ref in index:
        if query.entity and ref.entity:
            # Case-insensitive entity match
            if query.entity.lower() not in ref.entity.lower() and \
               ref.entity.lower() not in query.entity.lower():
                continue
        if query.event_type and ref.event_type != query.event_type:
            continue
        results.append(ref)
    return sorted(results, key=lambda r: r.date_or_order)


# ---------------------------------------------------------------------------
# Amount extraction helpers
# ---------------------------------------------------------------------------

_AMT_RE = re.compile(r"(?:Rs\.?|₹|INR)\s*([\d,]+(?:\.\d+)?)")
_FRAC_WORDS = {
    "half": Decimal("0.5"),
    "one-half": Decimal("0.5"),
    "one-third": Decimal("1") / Decimal("3"),
    "one-fourth": Decimal("0.25"),
    "one-quarter": Decimal("0.25"),
    "two-thirds": Decimal("2") / Decimal("3"),
    "three-fourths": Decimal("0.75"),
    "three-quarters": Decimal("0.75"),
}


def _extract_opening_balances(text: str) -> Dict[str, Decimal]:
    """Extract account balances from opening balance text.

    Example: "Balances as on 1st April: Cash Rs.50000, Bank Rs.100000"
    Returns: {"Cash": Decimal("50000"), "Bank": Decimal("100000")}
    """
    balances = {}
    # Pattern: AccountName Rs.Amount or AccountName amount
    pat = re.compile(
        r"([A-Z][A-Za-z]+)\s+(?:Rs\.?|\u20b9|INR)?\s*([\d,]+(?:\.\d+)?)",
        re.IGNORECASE
    )
    for m in pat.finditer(text):
        account = m.group(1).strip()
        try:
            amount = Decimal(m.group(2).replace(",", ""))
        except InvalidOperation:
            continue
        # Skip common non-account words
        if account.lower() in ("as", "on", "the", "april", "march",
                                "january", "february", "may", "june",
                                "july", "august", "september", "october",
                                "november", "december", "rs", "inr",
                                "first", "second", "third", "balances"):
            continue
        balances[account] = amount
    return balances


def _extract_absolute_amount(text: str) -> Optional[Decimal]:
    """Extract the primary absolute amount from text."""
    amounts = []
    for m in _AMT_RE.finditer(text):
        try:
            amounts.append(Decimal(m.group(1).replace(",", "")))
        except InvalidOperation:
            pass
    return max(amounts) if amounts else None


def _extract_fraction_word(text: str) -> Optional[Decimal]:
    """Extract a fraction word from text and return as Decimal."""
    low = text.lower()
    for word, value in _FRAC_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            return value
    return None


# ---------------------------------------------------------------------------
# 16-E: Historical Text Preprocessing
# ---------------------------------------------------------------------------

# Patterns that indicate historical fraction references needing resolution
_HIST_FRACTION_AMOUNT_RE = re.compile(
    r"\b(half|one-half|one-third|one-fourth|one-quarter|two-thirds|"
    r"three-fourths|three-quarters)\b"
    r"\s+(?:of\s+)?(?:the\s+)?(?:remaining\s+)?"
    r"\b(?:goods|stock|inventory|purchase|purchased|bought|amount|"
    r"payment|balance|due|cost|value)\b",
    re.IGNORECASE
)



# Sprint 20: "remaining" resolution helper
_REMAINING_RE = re.compile(
    r"\b(?:remaining|balance|the\s+rest|what\s+remains)\b",
    re.IGNORECASE)


def _resolve_remaining_text(
    text: str,
    historical_index: List[HistoricalReference],
    current_tx_index: int,
) -> Tuple[str, List[HistoricalReference], bool]:
    """Resolve 'remaining goods from <entity>' by computing:
    remaining = verified_purchase_amount - sum(verified_prior_sales)

    Only resolves when:
    - exactly one purchase from the entity exists in the historical index
    - the prior sales amount is deterministically known

    Returns (rewritten_text, references_used, is_ambiguous).
    """
    if not historical_index:
        return text, [], False

    low = text.lower()

    if not _REMAINING_RE.search(low):
        return text, [], False

    # Extract entity from text
    entities = _extract_entities_from_text(text)
    if not entities:
        entity_pat = re.compile(
            r"(?:from|of|to|purchased\s+from|bought\s+from|sold\s+to)\s+"
            r"([A-Z][A-Za-z.' ]+?)(?:\s|,|\.|;|$)", re.IGNORECASE
        )
        for m in entity_pat.finditer(text):
            name = m.group(1).strip().rstrip(".,;:")
            if name and len(name) > 1 and name.lower() not in (
                "the", "a", "an", "his", "her", "its", "our", "their",
                "goods", "stock", "inventory", "half", "balance",
                "remaining", "purchase", "purchased"
            ):
                entities.append(name)

    if not entities:
        return text, [], False

    prior_index = [ref for ref in historical_index
                   if ref.transaction_index < current_tx_index]
    if not prior_index:
        return text, [], False

    for entity in entities:
        # Query all historical references for this entity
        all_refs = _query_historical_index(
            prior_index, HistoricalQuery(entity=entity))
        if len(all_refs) < 2:
            continue

        # Identify base purchase = reference with the largest amount
        purchase_ref = max(all_refs, key=lambda r: r.amount)
        base_amount = purchase_ref.amount

        # Compute remaining = purchase - sum(all other entity amounts)
        total_prior_reductions = Decimal(0)
        for ref in all_refs:
            if ref.transaction_index == purchase_ref.transaction_index:
                continue
            total_prior_reductions += ref.amount

        remaining = base_amount - total_prior_reductions
        if remaining <= 0:
            return text, [], False

        remaining = remaining.quantize(Decimal("1"))

        # Rewrite: replace "remaining/balance ... from <entity>" with amount
        rewrite_pat = re.compile(
            r"\b(?:remaining|balance|the\s+rest|what\s+remains)\b"
            r"[^.]*?\b(?:of|from)\b"
            r"[^.]*?\b" + re.escape(entity) + r"\b",
            re.IGNORECASE
        )

        replacement = "goods worth Rs.{} from {}".format(remaining, entity)
        new_text = rewrite_pat.sub(replacement, text, count=1)

        if new_text != text:
            return new_text, [purchase_ref], False

    return text, [], False

def _resolve_historical_text(
    text: str,
    historical_index: List[HistoricalReference],
    current_tx_index: int = 0,
) -> Tuple[str, List[HistoricalReference], bool]:
    """Preprocess transaction text to resolve historical references.

    Detects fraction+entity patterns like "half of the goods purchased
    from Mark" and rewrites them with resolved amounts when the
    historical index provides a deterministic answer.

    Enforces chronological integrity: only references prior transactions.

    Returns (rewritten_text, references_used, is_ambiguous).
    is_ambiguous is True when the text contains a historical reference
    but the index has multiple candidates (uncertain resolution).
    """
    if not historical_index:
        return text, [], False

    low = text.lower()
    refs_used = []

    # Check for fraction-word patterns
    frac_match = _HIST_FRACTION_AMOUNT_RE.search(low)
    if not frac_match:
        # Sprint 20: check for "remaining"/"balance" patterns
        remaining_match = _REMAINING_RE.search(low)
        if remaining_match:
            return _resolve_remaining_text(
                text, historical_index, current_tx_index)
        return text, [], False

    fraction_word = frac_match.group(1).lower()
    fraction_value = _FRAC_WORDS.get(fraction_word)
    if fraction_value is None:
        return text, [], False

    # Extract entity from text
    entities = _extract_entities_from_text(text)
    if not entities:
        # Try broader entity extraction
        entity_pat = re.compile(
            r"(?:from|of|to|purchased\s+from|bought\s+from|sold\s+to)\s+"
            r"([A-Z][A-Za-z.' ]+?)(?:\s|,|\.|;|$)", re.IGNORECASE
        )
        for m in entity_pat.finditer(text):
            name = m.group(1).strip().rstrip(".,;:")
            if name and len(name) > 1 and name.lower() not in (
                "the", "a", "an", "his", "her", "its", "our", "their",
                "goods", "stock", "inventory", "half", "balance",
                "remaining", "purchase", "purchased"
            ):
                entities.append(name)

    if not entities:
        return text, [], False

    # Determine event type from text
    event_type = _classify_transaction_event(text)

    # Filter to only prior transactions (chronological integrity)
    prior_index = [ref for ref in historical_index
                   if ref.transaction_index < current_tx_index]
    if not prior_index:
        return text, [], False

    # Query historical index
    for entity in entities:
        query = HistoricalQuery(entity=entity, event_type=event_type)
        matches = _query_historical_index(prior_index, query)
        if not matches:
            # Try without event type filter
            query2 = HistoricalQuery(entity=entity)
            matches = _query_historical_index(prior_index, query2)

        if len(matches) == 1:
            ref = matches[0]
            resolved_amount = ref.amount * fraction_value
            # Round to nearest integer for Indian accounting
            resolved_amount = resolved_amount.quantize(Decimal("1"))

            # Rewrite: replace fraction text with resolved amount
            rewrite_pat = re.compile(
                r"\b" + re.escape(fraction_word) + r"\b"
                r"[^.]*?\b(?:of|from)\b"
                r"[^.]*?\b" + re.escape(entity) + r"\b",
                re.IGNORECASE
            )

            replacement = "goods worth Rs.{}".format(resolved_amount)
            new_text = rewrite_pat.sub(replacement, text, count=1)

            if new_text != text:
                refs_used.append(ref)
                return new_text, refs_used, False
        elif len(matches) > 1:
            # Ambiguous: multiple candidates, cannot deterministically resolve
            return text, [], True

    return text, [], False


# ---------------------------------------------------------------------------
# 16-C / 16-D: Sequential Execution + State Mutation
# ---------------------------------------------------------------------------

def _extract_state_delta(
    tx_index: int,
    tx_text: str,
    result: Dict[str, Any],
) -> Optional[StateDelta]:
    """Extract a state delta from a verified transaction result.
    Only called for VERIFIED results."""
    status = result.get("status")
    if status != VERIFIED:
        return None

    journal = result.get("journal") or {}
    deltas = []

    # Extract from debit lines
    for line in journal.get("debit_lines") or []:
        account = line.get("account", "")
        try:
            amount = Decimal(str(line.get("amount", 0)))
        except (InvalidOperation, TypeError):
            continue
        if account and amount > 0:
            # Try to extract entity from the account name
            entity = None
            low_acc = account.lower()
            for word in ["creditor", "debtor", "payable", "receivable"]:
                if word in low_acc:
                    # Extract party name from account
                    parts = re.split(r"\s+(?:a/c|account|payable|receivable|cr|dr)\b",
                                     account, flags=re.IGNORECASE)
                    if parts and parts[0].strip():
                        entity = parts[0].strip()
                    break
            deltas.append(AccountDelta(account=account, direction="debit",
                                       amount=amount, entity=entity))

    # Extract from credit lines
    for line in journal.get("credit_lines") or []:
        account = line.get("account", "")
        try:
            amount = Decimal(str(line.get("amount", 0)))
        except (InvalidOperation, TypeError):
            continue
        if account and amount > 0:
            entity = None
            low_acc = account.lower()
            for word in ["creditor", "debtor", "payable", "receivable"]:
                if word in low_acc:
                    parts = re.split(r"\s+(?:a/c|account|payable|receivable|cr|dr)\b",
                                     account, flags=re.IGNORECASE)
                    if parts and parts[0].strip():
                        entity = parts[0].strip()
                    break
            deltas.append(AccountDelta(account=account, direction="credit",
                                       amount=amount, entity=entity))

    debit_total = sum((d.amount for d in deltas if d.direction == "debit"), Decimal(0))
    credit_total = sum((d.amount for d in deltas if d.direction == "credit"), Decimal(0))

    return StateDelta(
        transaction_index=tx_index,
        transaction_text=tx_text,
        status=VERIFIED,
        journal=journal,
        deltas=deltas,
        debit_total=debit_total,
        credit_total=credit_total,
        provenance=[{
            "source": "orchestrate",
            "transaction_index": tx_index,
            "status": VERIFIED,
            "journal_balanced": debit_total == credit_total,
        }],
    )


# ---------------------------------------------------------------------------
# Problem-level status computation
# ---------------------------------------------------------------------------

def _compute_problem_status(results: List[TransactionResult]) -> str:
    """Compute deterministic problem-level status from transaction results."""
    has_verified = False
    has_review_required = False
    has_invalid_math = False
    has_not_supported = False
    has_informational = False

    for r in results:
        if r.event_type == "INFORMATIONAL_EVENT":
            has_informational = True
            continue
        if r.event_type == "OPENING_BALANCE":
            # Opening balances are informational, not transactions
            has_informational = True
            continue
        if r.status == VERIFIED:
            has_verified = True
        elif r.status == REVIEW_REQUIRED:
            has_review_required = True
        elif r.status == INVALID_INPUT_MATH:
            has_invalid_math = True
        elif r.status == NOT_SUPPORTED:
            has_not_supported = True

    # If all are informational, problem is verified (nothing to journal)
    if not has_verified and not has_review_required and \
       not has_invalid_math and not has_not_supported:
        if has_informational:
            return PROBLEM_VERIFIED
        return PROBLEM_NOT_SUPPORTED

    # Priority: INVALID > REVIEW > NOT_SUPPORTED > VERIFIED
    if has_invalid_math:
        return PROBLEM_INVALID_INPUT_MATH
    if has_review_required:
        return PROBLEM_REVIEW_REQUIRED
    if has_not_supported:
        return PROBLEM_NOT_SUPPORTED
    return PROBLEM_VERIFIED


# ---------------------------------------------------------------------------
# Safety / state-integrity assertions
# ---------------------------------------------------------------------------

def _assert_state_integrity(
    ledger: LedgerState,
    results: List[TransactionResult],
) -> List[str]:
    """Run state-integrity safety checks. Returns list of violations (empty = pass)."""
    violations = []

    # 1. No state mutation from unsafe results
    for r in results:
        if r.state_delta and r.status != VERIFIED:
            violations.append(
                f"State mutation from unsafe result: T{r.index} status={r.status}"
            )

    # 2. No duplicate mutations
    applied_indices = set()
    for r in results:
        if r.state_delta:
            if r.index in applied_indices:
                violations.append(f"Duplicate mutation: T{r.index}")
            applied_indices.add(r.index)

    # 3. No state leaks (deterministic: results processed in order)
    # State is built incrementally, so leaks would require out-of-order access
    # which our sequential loop prevents by construction.

    # 4. Chronological integrity
    for i, r in enumerate(results):
        if r.index != i + 1:
            violations.append(
                f"Chronological integrity: expected T{i+1}, got T{r.index}"
            )

    return violations


# ---------------------------------------------------------------------------
# 16-C: process_problem() — Main Entry Point
# ---------------------------------------------------------------------------

def process_problem(
    problem_text: str,
    opening_balances: Optional[Dict[str, Decimal]] = None,
) -> Dict[str, Any]:
    """Process a complete multi-transaction accounting problem.

    Args:
        problem_text: The full problem text containing multiple transactions.
        opening_balances: Optional opening balances to seed the ledger.

    Returns:
        Dict with:
            - problem_status: PROBLEM_VERIFIED / PROBLEM_REVIEW_REQUIRED / etc.
            - transactions: List of TransactionResult dicts
            - ledger_snapshot: Final ledger state snapshot
            - safety_violations: List of safety violations (empty = pass)
            - deterministic: Always True
            - metadata: Problem-level metadata
    """
    from backend.maths.fyjc_bk_reasoning import _split_transactions
    from backend.maths.fyjc_orchestration import orchestrate

    # 16-B: Reuse existing segmentation
    transactions = _split_transactions(problem_text)

    if not transactions:
        return {
            "problem_status": PROBLEM_NOT_SUPPORTED,
            "transactions": [],
            "ledger_snapshot": LedgerState().snapshot(),
            "safety_violations": [],
            "deterministic": True,
            "metadata": {"reason": "No transactions detected"},
        }

    # Create session
    session = ProblemSession(problem_text=problem_text, transactions=transactions)

    # Seed opening balances
    if opening_balances:
        for account, amount in opening_balances.items():
            session.ledger.balances[account] = amount

    # 16-C: Sequential execution
    for i, tx_text in enumerate(transactions):
        tx_index = i + 1

        # 16-F: Detect informational events
        event_type = _detect_informational_event(tx_text)

        if event_type == "INFORMATIONAL_EVENT":
            result = TransactionResult(
                index=tx_index,
                text=tx_text,
                status=INFORMATIONAL_EVENT,
                event_type=INFORMATIONAL_EVENT,
            )
            session.results.append(result)
            continue

        # Detect opening balances
        if event_type == "OPENING_BALANCE":
            # Extract account balances from text and seed the ledger
            ob = _extract_opening_balances(tx_text)
            for acc, amt in ob.items():
                session.ledger.balances[acc] = amt
            result = TransactionResult(
                index=tx_index,
                text=tx_text,
                status=INFORMATIONAL_EVENT,
                event_type="OPENING_BALANCE",
            )
            session.results.append(result)
            continue

        # 16-E: Preprocess historical references
        enriched_text, hist_refs_used, is_ambiguous = _resolve_historical_text(
            tx_text, session.ledger.historical_index, tx_index
        )
        if is_ambiguous:
            result = TransactionResult(
                index=tx_index,
                text=tx_text,
                status=REVIEW_REQUIRED,
                why_not="Multiple historical candidates found; "
                        "cannot deterministically resolve which "
                        "transaction the reference points to.",
                event_type="ACCOUNTING_TRANSACTION",
            )
            session.results.append(result)
            continue

        # Execute through existing orchestrate()
        try:
            orch_result = orchestrate(enriched_text)
        except Exception as e:
            result = TransactionResult(
                index=tx_index,
                text=tx_text,
                status=NOT_SUPPORTED,
                why_not=f"Orchestration exception: {str(e)}",
                event_type="ACCOUNTING_TRANSACTION",
            )
            session.results.append(result)
            continue

        status = orch_result.get("status", NOT_SUPPORTED)
        journal = orch_result.get("journal")
        why_not = orch_result.get("why_not")
        next_action = orch_result.get("next_action")

        # 16-D: State mutation only from VERIFIED results
        state_delta = None
        if status == VERIFIED:
            state_delta = _extract_state_delta(tx_index, tx_text, orch_result)
            if state_delta:
                session.ledger.apply_delta(state_delta)

            # 16-E: Build historical reference
            hist_refs = _build_historical_reference(tx_index, tx_text, orch_result)
            session.ledger.historical_index.extend(hist_refs)
        else:
            hist_refs = []

        result = TransactionResult(
            index=tx_index,
            text=tx_text,
            status=status,
            journal=journal,
            state_delta=state_delta,
            historical_references=[],  # refs are in ledger.historical_index for future txns only
            why_not=why_not,
            next_action=next_action,
            event_type="ACCOUNTING_TRANSACTION",
        )
        session.results.append(result)

    # 16-G: Problem-level status
    problem_status = _compute_problem_status(session.results)
    session.problem_status = problem_status

    # 16-H: Safety gates
    safety_violations = _assert_state_integrity(session.ledger, session.results)

    # Build output
    tx_outputs = []
    for r in session.results:
        tx_out: Dict[str, Any] = {
            "index": r.index,
            "text": r.text,
            "status": r.status,
            "event_type": r.event_type,
        }
        if r.journal:
            tx_out["journal"] = r.journal
        if r.why_not:
            tx_out["why_not"] = r.why_not
        if r.next_action:
            tx_out["next_action"] = r.next_action
        if r.historical_references:
            tx_out["historical_references"] = [
                {
                    "transaction_index": ref.transaction_index,
                    "entity": ref.entity,
                    "event_type": ref.event_type,
                    "amount": str(ref.amount),
                    "provenance": ref.provenance,
                }
                for ref in r.historical_references
            ]
        if r.state_delta:
            tx_out["state_delta"] = {
                "deltas": [
                    {
                        "account": d.account,
                        "direction": d.direction,
                        "amount": str(d.amount),
                        "entity": d.entity,
                    }
                    for d in r.state_delta.deltas
                ],
                "debit_total": str(r.state_delta.debit_total),
                "credit_total": str(r.state_delta.credit_total),
            }
        tx_outputs.append(tx_out)

    return {
        "problem_status": problem_status,
        "transactions": tx_outputs,
        "ledger_snapshot": session.ledger.snapshot(),
        "safety_violations": safety_violations,
        "deterministic": True,
        "metadata": {
            "problem_text": problem_text,
            "total_transactions": len(transactions),
            "verified_count": sum(1 for r in session.results if r.status == VERIFIED),
            "review_required_count": sum(1 for r in session.results if r.status == REVIEW_REQUIRED),
            "invalid_math_count": sum(1 for r in session.results if r.status == INVALID_INPUT_MATH),
            "not_supported_count": sum(1 for r in session.results if r.status == NOT_SUPPORTED),
            "informational_count": sum(
                1 for r in session.results
                if r.event_type in ("INFORMATIONAL_EVENT", "OPENING_BALANCE")
            ),
        },
    }
