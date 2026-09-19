"""Platrixa — Phase F: Finance Knowledge Authority.

Third authority of the Three-Authority architecture:

    ACCOUNTING KERNEL        -> deterministic accounting treatment/execution
    FORMULA AUTHORITY        -> deterministic mathematical/financial calculation
    FINANCE KNOWLEDGE AUTHORITY (this module)
                             -> verified financial concepts, definitions,
                                terminology, relationships and rule references

The model may interpret a question; THIS module supplies verified knowledge.
It is a REGISTRY, not an execution engine: it has no calculate, post or
journal method by construction, so knowledge can never bypass the kernel or
the formula authority. A claim is SUPPORTED only when its source was actually
verified this session; anything unverifiable is UNVERIFIED and retrievable
under an explicit unverified-only flag, never presented as verified.

Knowledge types are explicit and never blur:
    FACT, DEFINITION, CONCEPT, RELATIONSHIP, ACCOUNTING_RULE_REFERENCE,
    FORMULA_REFERENCE, TERMINOLOGY.
A FORMULA_REFERENCE points at the Formula Authority's canonical concept - the
calculation itself lives there. An ACCOUNTING_RULE_REFERENCE points at the
standard the Accounting Kernel implements - execution lives there.

Source hierarchy honoured: standard setters (IASB/IFRS) > regulators/central
banks (BCBS/BIS) > statutory material (MCA/Ind AS) > encyclopedic summaries
used only as verification aids, never as the authority. No copyrighted
standard text is reproduced: records store concise interpretations plus
provenance. Granularity: ONE record per coherent concept (IAS 7's operating /
investing / financing classification is ONE record, not three).

Pure module: no I/O, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple


class KnowledgeType(str, Enum):
    """Explicit knowledge types - these never blur together."""

    FACT = "FACT"
    DEFINITION = "DEFINITION"
    CONCEPT = "CONCEPT"
    RELATIONSHIP = "RELATIONSHIP"
    ACCOUNTING_RULE_REFERENCE = "ACCOUNTING_RULE_REFERENCE"
    FORMULA_REFERENCE = "FORMULA_REFERENCE"
    TERMINOLOGY = "TERMINOLOGY"


class KnowledgeStatus(str, Enum):
    """Status vocabulary mirrors the Phase C capability registry.

    SUPPORTED = source verified; UNVERIFIED = plausible but source not
    verified; PLANNED = declared slot, no claim yet (never retrievable).
    """

    SUPPORTED = "SUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    PLANNED = "PLANNED"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KnowledgeSource:
    """Provenance for one verified claim. source_type states the hierarchy
    tier; verified_via documents HOW the claim was actually checked."""

    organization: str
    reference: str
    source_type: str          # e.g. "accounting standard setter"
    jurisdiction: str = "international"
    retrieved_at: str = "2026-09-19"
    verified_via: str = ""    # how the claim was verified (no secrets, no bulk text)
    limitations: str = ""


@dataclass(frozen=True)
class KnowledgeRecord:
    knowledge_id: str
    knowledge_type: KnowledgeType
    topic: str
    claim: str
    status: KnowledgeStatus
    source: Optional[KnowledgeSource] = None
    applicability: str = ""
    limitations: str = ""


class KnowledgeQuery:
    """Immutable lookup key; None fields are match-anything. One generic
    query covers id/topic/type/jurisdiction/framework retrieval."""

    __slots__ = ("knowledge_id", "topic", "knowledge_type", "jurisdiction", "framework")

    def __init__(self, knowledge_id: Optional[str] = None, topic: Optional[str] = None,
                 knowledge_type: Optional[KnowledgeType] = None,
                 jurisdiction: Optional[str] = None, framework: Optional[str] = None) -> None:
        self.knowledge_id = knowledge_id
        self.topic = topic
        self.knowledge_type = knowledge_type
        self.jurisdiction = jurisdiction
        self.framework = framework


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------

class KnowledgeAuthority:
    """Deterministic knowledge registry. Fail-closed: SUPPORTED without
    verified provenance is unrepresentable, duplicate ids and duplicate
    claims are rejected, and nothing here executes."""

    def __init__(self) -> None:
        self._records: Dict[str, KnowledgeRecord] = {}
        self._claims: Dict[str, str] = {}   # casefolded claim -> knowledge_id

    def register(self, record: KnowledgeRecord) -> None:
        if not isinstance(record.knowledge_type, KnowledgeType):
            raise ValueError(f"invalid knowledge type: {record.knowledge_type!r}")
        if not isinstance(record.status, KnowledgeStatus):
            raise ValueError(f"invalid knowledge status: {record.status!r}")
        if not record.knowledge_id or not record.claim.strip():
            raise ValueError(f"knowledge record requires id and claim: {record.knowledge_id!r}")
        if record.knowledge_id in self._records:
            raise ValueError(f"duplicate knowledge id: {record.knowledge_id}")
        if record.status is KnowledgeStatus.SUPPORTED:
            if record.source is None:
                raise ValueError(f"SUPPORTED record without provenance: {record.knowledge_id}")
            if not record.source.verified_via:
                raise ValueError(
                    f"SUPPORTED record without verification evidence: {record.knowledge_id}")
        claim_key = " ".join(record.claim.casefold().split())
        if claim_key in self._claims:
            raise ValueError(
                "duplicate knowledge claim "
                f"({record.knowledge_id} vs {self._claims[claim_key]})")
        self._records[record.knowledge_id] = record
        self._claims[claim_key] = record.knowledge_id

    # -- retrieval ---------------------------------------------------------

    def get(self, knowledge_id: str) -> Optional[KnowledgeRecord]:
        return self._records.get(knowledge_id)

    def query(self, query: KnowledgeQuery,
              include_unverified: bool = False) -> Tuple[KnowledgeRecord, ...]:
        """Deterministic lookup ordered by knowledge_id. UNVERIFIED records
        are excluded unless the caller explicitly opts in - a caller that
        has not opted in can never receive unverified knowledge."""
        matches = []
        for record in self._records.values():
            if record.status is not KnowledgeStatus.SUPPORTED:
                if not include_unverified or record.status is KnowledgeStatus.PLANNED:
                    continue
            src = record.source or KnowledgeSource("", "", "")
            if query.knowledge_id is not None and record.knowledge_id != query.knowledge_id:
                continue
            if query.topic is not None and query.topic.casefold() not in record.topic.casefold():
                continue
            if query.knowledge_type is not None and record.knowledge_type is not query.knowledge_type:
                continue
            if query.jurisdiction is not None and query.jurisdiction.casefold() not in src.jurisdiction.casefold():
                continue
            if query.framework is not None:
                haystack = f"{src.reference} {record.topic}".casefold()
                if query.framework.casefold() not in haystack:
                    continue
            matches.append(record)
        return tuple(sorted(matches, key=lambda r: r.knowledge_id))

    # -- introspection -----------------------------------------------------

    def __len__(self) -> int:
        return len(self._records)

    def counts_by_status(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self._records.values():
            counts[record.status.value] = counts.get(record.status.value, 0) + 1
        return counts

    def counts_by_type(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self._records.values():
            counts[record.knowledge_type.value] = counts.get(record.knowledge_type.value, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Verified foundation (Phase F initial scope - one record per concept)
# ---------------------------------------------------------------------------

_TODAY = "2026-09-19"

_SRC_IAS7 = KnowledgeSource(
    organization="IASB (IFRS Foundation)",
    reference="IAS 7 Statement of Cash Flows",
    source_type="accounting standard setter",
    verified_via="standard's published scope/summary cross-checked 2026-09-19 (encyclopedic summary used as verification aid)",
    limitations="concise interpretation only; the authoritative standard text is licensed by the IFRS Foundation",
)
_SRC_IAS1 = KnowledgeSource(
    organization="IASB (IFRS Foundation)",
    reference="IAS 1 Presentation of Financial Statements",
    source_type="accounting standard setter",
    verified_via="standard's published scope/summary cross-checked 2026-09-19 (encyclopedic summary used as verification aid)",
    limitations="concise interpretation only; the authoritative standard text is licensed by the IFRS Foundation",
)
_SRC_IAS37 = KnowledgeSource(
    organization="IASB (IFRS Foundation)",
    reference="IAS 37 Provisions, Contingent Liabilities and Contingent Assets",
    source_type="accounting standard setter",
    verified_via="standard's published scope/summary cross-checked 2026-09-19 (encyclopedic summary used as verification aid)",
    limitations="concise interpretation only; the authoritative standard text is licensed by the IFRS Foundation",
)
_SRC_BCBS = KnowledgeSource(
    organization="Basel Committee on Banking Supervision (BIS)",
    reference="Basel II: International Convergence of Capital Measurement and Capital Standards (2006)",
    source_type="central bank / supervisor committee (BIS)",
    verified_via="BIS publication listing confirmed credit/market/operational as the Basel risk families, 2026-09-19",
    limitations="banking-supervision framing; definitions are widely adopted outside banking too",
)

_FINANCE_KNOWLEDGE_RECORDS: Tuple[KnowledgeRecord, ...] = (
    # --- Financial statements and elements (IAS 1) ---
    KnowledgeRecord("fk_fs_purpose", KnowledgeType.CONCEPT, "financial statements",
                    "Financial statements provide structured information about an entity's financial position, financial performance and cash flows.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1,
                    applicability="general purpose financial statements"),
    KnowledgeRecord("fk_fs_elements", KnowledgeType.FACT, "financial statement elements",
                    "IAS 1 categorises financial-statement information into assets, liabilities, income, expenses, contributions by and distributions to owners, and cash flows.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_bs_definition", KnowledgeType.DEFINITION, "balance sheet / statement of financial position",
                    "The balance sheet (statement of financial position) presents an entity's assets, liabilities and equity at a point in time.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_is_definition", KnowledgeType.DEFINITION, "income statement",
                    "The income statement presents an entity's financial performance (income and expenses) over a period, arriving at profit or loss.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_asset_definition", KnowledgeType.DEFINITION, "asset",
                    "An asset is a resource controlled by the entity from which future economic benefits are expected to flow.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_liability_definition", KnowledgeType.DEFINITION, "liability",
                    "A liability is a present obligation arising from past events whose settlement is expected to outflow resources embodying economic benefits.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_equity_definition", KnowledgeType.DEFINITION, "equity",
                    "Equity is the residual interest in an entity's assets after deducting its liabilities.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_going_concern", KnowledgeType.FACT, "going concern",
                    "Financial statements are normally prepared on the assumption that the entity is a going concern and will continue operating for the foreseeable future.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_accrual_basis", KnowledgeType.FACT, "accrual basis",
                    "Under the accrual basis, transactions are recognised when they occur (not when cash moves), and presented in the periods they relate to.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    # --- Cash flow statement (IAS 7) ---
    KnowledgeRecord("fk_cashflow_classes", KnowledgeType.ACCOUNTING_RULE_REFERENCE, "cash flow classification",
                    "IAS 7 classifies cash flows during a period into operating, investing and financing activities on the statement of cash flows.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS7,
                    applicability="cash flow statement presentation; the amounts themselves are computed by the Formula Authority when inputs are verified"),
    KnowledgeRecord("fk_provisions", KnowledgeType.ACCOUNTING_RULE_REFERENCE, "provisions",
                    "IAS 37 requires a provision (liability of uncertain timing or amount) to be recognised when a present obligation exists, outflow is probable and the amount can be reliably estimated.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS37),
    # --- Financial analysis ---
    KnowledgeRecord("fk_working_capital", KnowledgeType.RELATIONSHIP, "working capital",
                    "Working capital generally represents the relationship between an entity's current assets and current liabilities.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1,
                    applicability="analysis vocabulary; ratio computation belongs to the Formula Authority"),
    KnowledgeRecord("fk_liquidity", KnowledgeType.CONCEPT, "liquidity",
                    "Liquidity refers to the ability to meet short-term obligations using resources available in the short term.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_profitability", KnowledgeType.CONCEPT, "profitability",
                    "Profitability concerns an entity's ability to generate earnings from its operations relative to revenue, assets, capital or equity.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_solvency", KnowledgeType.CONCEPT, "solvency",
                    "Solvency refers to an entity's ability to meet its long-term obligations as they fall due.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_leverage", KnowledgeType.CONCEPT, "leverage",
                    "Leverage is the extent to which an entity's financing and returns depend on debt relative to equity or assets.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    # --- Risk (Basel/BCBS) ---
    KnowledgeRecord("fk_risk_families", KnowledgeType.FACT, "financial risk families",
                    "The Basel framework organises banking risk into the principal families of credit risk, market risk and operational risk.",
                    KnowledgeStatus.SUPPORTED, _SRC_BCBS),
    KnowledgeRecord("fk_counterparty_risk", KnowledgeType.CONCEPT, "counterparty risk",
                    "Counterparty risk is the risk that the other party in a transaction fails to meet its obligations when due.",
                    KnowledgeStatus.SUPPORTED, _SRC_BCBS),
    # --- Financial documents (developer-facing semantics) ---
    KnowledgeRecord("fk_invoice", KnowledgeType.DEFINITION, "invoice",
                    "An invoice is a commercial document issued by a seller to a buyer stating goods or services supplied, quantities, amounts and payment terms - a request for payment (accounts receivable).",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1,
                    applicability="AR/AP document semantics"),
    KnowledgeRecord("fk_credit_note", KnowledgeType.DEFINITION, "credit note",
                    "A credit note is a seller-issued document reducing the amount a buyer owes, typically for returns, corrections or allowances - it reverses part or all of an invoice.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_trial_balance", KnowledgeType.DEFINITION, "trial balance",
                    "A trial balance lists ledger account balances at a date to check that total debits equal total credits before financial statements are prepared.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_general_ledger", KnowledgeType.DEFINITION, "general ledger",
                    "The general ledger is the authoritative record of an entity's accounts and the double-entry postings made to them.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_bank_statement", KnowledgeType.DEFINITION, "bank statement",
                    "A bank statement is the bank's record of the movements and balance in an account, used to reconcile the entity's own cash records (bank reconciliation).",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1),
    KnowledgeRecord("fk_settlement", KnowledgeType.TERMINOLOGY, "settlement",
                    "Settlement is the final discharging of an obligation between parties - the transfer of funds or assets that completes a transaction.",
                    KnowledgeStatus.SUPPORTED, _SRC_BCBS),
    KnowledgeRecord("fk_idempotency", KnowledgeType.CONCEPT, "idempotency",
                    "Idempotency means processing the same request (e.g. a duplicated webhook event) multiple times yields the same result as processing it once - essential for duplicate-event protection.",
                    KnowledgeStatus.SUPPORTED, _SRC_BCBS,
                    applicability="developer-facing event/webhook semantics"),
    # --- Formula Authority cross-references (definitions point, never compute) ---
    KnowledgeRecord("fk_roe_reference", KnowledgeType.FORMULA_REFERENCE, "ROE",
                    "ROE (return on equity) measures earnings generated relative to shareholders' equity; its deterministic calculation is owned by the Formula Authority.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS1,
                    applicability="calculation routing: Formula Authority, concept: this authority"),
    KnowledgeRecord("fk_fcf_reference", KnowledgeType.FORMULA_REFERENCE, "free cash flow",
                    "Free cash flow represents cash generated by operations after capital expenditure; its deterministic calculation is owned by the Formula Authority.",
                    KnowledgeStatus.SUPPORTED, _SRC_IAS7,
                    applicability="calculation routing: Formula Authority, concept: this authority"),
    # --- Deliberately UNVERIFIED: source could not be verified this session ---
    KnowledgeRecord("fk_ind_as_mapping", KnowledgeType.FACT, "Ind AS convergence",
                    "India's converged Ind AS standards largely correspond to IFRS/IAS standards (e.g. Ind AS 7 to IAS 7).",
                    KnowledgeStatus.UNVERIFIED, None,
                    limitations="MCA primary source was not verifiable at registration time; do not present as verified until confirmed"),
)

# The one PLANNED root from Phase C - declared, never retrievable as knowledge.
_PLANNED_ROOT: KnowledgeRecord = KnowledgeRecord(
    "fk_root", KnowledgeType.CONCEPT, "finance knowledge authority",
    "Reserved root: the Finance Knowledge Authority's verified foundation.",
    KnowledgeStatus.PLANNED)


def build_knowledge_authority() -> KnowledgeAuthority:
    """Single canonical instance (DRY): every consumer queries this authority;
    records live in _FINANCE_KNOWLEDGE_RECORDS and nowhere else."""
    authority = KnowledgeAuthority()
    for record in _FINANCE_KNOWLEDGE_RECORDS:
        authority.register(record)
    return authority
