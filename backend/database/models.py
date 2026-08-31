"""
SQLAlchemy ORM Models

These models represent the Financial Intelligence Database.

Used by:
- Module 4 Data Collector
- Database Manager
- Future Modules (5+)

"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.database.db import Base


# =====================================================
# Currency Role Constants
# =====================================================

CURRENCY_ROLE_REPORTING = "REPORTING"
CURRENCY_ROLE_FUNCTIONAL = "FUNCTIONAL"
CURRENCY_ROLE_PRESENTATION = "PRESENTATION"
CURRENCY_ROLE_TRANSACTION = "TRANSACTION"
CURRENCY_ROLE_TAX = "TAX"


# =====================================================
# Verification Status Constants
# =====================================================

VERIFICATION_PENDING = "PENDING"
VERIFICATION_VERIFIED = "VERIFIED"
VERIFICATION_CONFLICT = "CONFLICT"
VERIFICATION_SUPERSEDED = "SUPERSEDED"
VERIFICATION_CORRUPTED = "CORRUPTED"


# =====================================================
# Company
# =====================================================

class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)

    ticker = Column(String, unique=True, nullable=False, index=True)

    company_name = Column(String, nullable=False)

    exchange = Column(String)

    sector = Column(String)

    industry = Column(String)

    isin = Column(String)

    market_cap = Column(Float)

    currency = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    financials = relationship(
        "Financial",
        back_populates="company",
    )

    prices = relationship(
        "MarketPrice",
        back_populates="company",
    )

    news = relationship(
        "News",
        back_populates="company",
    )

    filings = relationship(
        "Filing",
        back_populates="company",
    )

    corporate_actions = relationship(
        "CorporateAction",
        back_populates="company",
    )


# =====================================================
# Financial Statements
# =====================================================

class Financial(Base):
    __tablename__ = "financials"

    id = Column(Integer, primary_key=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
    )

    statement_type = Column(String)

    fiscal_year = Column(Integer)

    fiscal_quarter = Column(String)

    revenue = Column(Float)

    ebitda = Column(Float)

    ebit = Column(Float)

    net_income = Column(Float)

    eps = Column(Float)

    total_assets = Column(Float)

    total_liabilities = Column(Float)

    shareholders_equity = Column(Float)

    operating_cash_flow = Column(Float)

    free_cash_flow = Column(Float)

    is_latest = Column(Boolean, default=True)

    source = Column(String)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    company = relationship(
        "Company",
        back_populates="financials",
    )


# =====================================================
# Market Prices
# =====================================================

class MarketPrice(Base):
    __tablename__ = "market_prices"

    id = Column(Integer, primary_key=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
    )

    trading_date = Column(Date)

    open_price = Column(Float)

    high_price = Column(Float)

    low_price = Column(Float)

    close_price = Column(Float)

    adjusted_close = Column(Float)

    volume = Column(Float)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    company = relationship(
        "Company",
        back_populates="prices",
    )


# =====================================================
# News
# =====================================================

class News(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
    )

    headline = Column(String)

    summary = Column(Text)

    source = Column(String)

    url = Column(Text)

    published_at = Column(DateTime)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    company = relationship(
        "Company",
        back_populates="news",
    )


# =====================================================
# Filings (with amendment/restatement support)
# =====================================================

class Filing(Base):
    __tablename__ = "filings"

    id = Column(Integer, primary_key=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
    )

    filing_type = Column(String)

    filing_date = Column(Date)

    fiscal_period = Column(String, nullable=True)

    fiscal_year = Column(Integer, nullable=True)

    fiscal_quarter = Column(String, nullable=True)

    source = Column(String)

    pdf_url = Column(Text, nullable=True)

    accession_number = Column(String, nullable=True, index=True)

    amendment_to = Column(
        String,
        nullable=True,
        comment="Accession number of the original filing this amends",
    )

    is_amendment = Column(Boolean, default=False)

    processed = Column(
        Boolean,
        default=False,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    company = relationship(
        "Company",
        back_populates="filings",
    )


# =====================================================
# Extracted Fact (Agentic RAG Evidence Store)
# =====================================================

class ExtractedFact(Base):
    __tablename__ = "extracted_facts"

    id = Column(Integer, primary_key=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
        index=True,
    )

    # Metric identity
    metric_id = Column(String, index=True)
    metric_name = Column(String)  # canonical metric name
    metric_definition = Column(String, nullable=True)

    # Value
    metric_value = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    scale = Column(String, nullable=True)

    # Currency
    currency_code = Column(String, nullable=True)
    currency_role = Column(String, nullable=True)
    fx_rate = Column(Float, nullable=True)
    fx_source = Column(String, nullable=True)
    fx_timestamp = Column(DateTime, nullable=True)

    # Period
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    fiscal_period = Column(String, nullable=True)

    # Accounting context
    accounting_basis = Column(String, nullable=True)
    scope = Column(String, nullable=True)

    # Source
    source = Column(String, nullable=True)
    source_tier = Column(Integer, nullable=True)
    source_type = Column(String, nullable=True)
    source_url = Column(Text, nullable=True)

    # Filing context
    filing_type = Column(String, nullable=True)
    accession_number = Column(String, nullable=True)
    amendment_relationship = Column(String, nullable=True)
    taxonomy = Column(String, nullable=True)  # e.g. us-gaap, ifrs-full (additive)

    # Evidence integrity
    evidence_hash = Column(String, unique=True, index=True)
    evidence_text_anchor = Column(Text, nullable=True)
    confidence_score = Column(Float, nullable=True)
    verification_status = Column(String, default=VERIFICATION_PENDING)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    company = relationship(
        "Company",
        backref="extracted_facts",
    )


# =====================================================
# Corporate Actions
# =====================================================

class CorporateAction(Base):
    __tablename__ = "corporate_actions"

    id = Column(Integer, primary_key=True)

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
    )

    action_type = Column(String)

    action_date = Column(Date)

    description = Column(Text)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    company = relationship(
        "Company",
        back_populates="corporate_actions",
    )


# =====================================================
# FYJC Accounting AI — Minimum Viable Schema
# =====================================================
# These 4 tables store the complete FYJC student-interaction →
# AI-interpretation → kernel-verification → training-candidate
# → human-approval → fine-tuning-export lifecycle.
#
# Module 4 tables above are untouched.
# =====================================================


FYJC_STATUS_CANDIDATE = "CANDIDATE"
FYJC_STATUS_VALIDATED = "VALIDATED"
FYJC_STATUS_REJECTED = "REJECTED"
FYJC_STATUS_RETIRED = "RETIRED"
FYJC_STATUS_CONFLICTING = "CONFLICTING"

FYJC_KB_TYPE_VOCABULARY = "VOCABULARY"
FYJC_KB_TYPE_RULE = "RULE"
FYJC_KB_TYPE_FORMULA = "FORMULA"
FYJC_KB_TYPE_EDGE_CASE = "EDGE_CASE"

FYJC_KB_SCOPE_PERSONAL = "PERSONAL"
FYJC_KB_SCOPE_CLASSROOM = "CLASSROOM"
FYJC_KB_SCOPE_SCHOOL = "SCHOOL"
FYJC_KB_SCOPE_GLOBAL = "GLOBAL"


# ----------------------------------------------------
# FYJC Table 1: Interactions
# ----------------------------------------------------

class FYJCInteraction(Base):
    """Raw student input.  Write-once, never modified."""

    __tablename__ = "fyjc_interactions"

    id = Column(Integer, primary_key=True, index=True)

    session_id = Column(
        String(64),
        index=True,
        nullable=True,
        comment="Groups multi-transaction problems in one session",
    )

    raw_input = Column(
        Text,
        nullable=False,
        comment="Exact student text, never normalised",
    )

    board = Column(
        String(32),
        nullable=True,
        comment="HSC / CBSE / ICSE",
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    # Relationships
    interpretations = relationship(
        "FYJCInterpretation",
        back_populates="interaction",
        order_by="FYJCInterpretation.created_at",
    )
    candidates = relationship(
        "FYJCTrainingCandidate",
        back_populates="interaction",
    )

    def __repr__(self) -> str:
        return f"<FYJCInteraction id={self.id} board={self.board}>",


# ----------------------------------------------------
# FYJC Table 2: Interpretations
# ----------------------------------------------------

class FYJCInterpretation(Base):
    """AI interpretation + kernel verdict.  One row per attempt."""

    __tablename__ = "fyjc_interpretations"

    id = Column(Integer, primary_key=True, index=True)

    interaction_id = Column(
        Integer,
        ForeignKey("fyjc_interactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    model_id = Column(
        String(128),
        nullable=False,
        comment="qwen2.5-1.5b-lora-p5a / deterministic-fallback / kernel-only",
    )

    # -- AI output fields (maps to AIInterpretation dataclass) --

    transaction_type = Column(
        String(32),
        nullable=True,
        comment="PURCHASE / SALE / EXPENSE / CAPITAL / etc.",
    )

    parties = Column(
        JSONB,
        nullable=True,
        comment='["Raj", "Sharma"]',
    )

    amounts = Column(
        JSONB,
        nullable=True,
        comment='["20000", "5000"] — strings for Decimal precision',
    )

    payment_method = Column(
        String(32),
        nullable=True,
        comment="CASH / BANK / CHEQUE / CREDIT / UNKNOWN",
    )

    ambiguity_flags = Column(
        JSONB,
        nullable=True,
        comment='["MISSING_PAYMENT_MODE"]',
    )

    field_confidences = Column(
        JSONB,
        nullable=True,
        comment='[{"field": "parties", "confidence": 0.95, "grounding": "GROUNDED"}]',
    )

    raw_model_output = Column(
        Text,
        nullable=True,
        comment="Verbatim model response before parsing",
    )

    parse_success = Column(Boolean, default=True)

    # -- Kernel verdict (merged — one interpretation → one verification) --

    kernel_status = Column(
        String(32),
        nullable=True,
        comment="VERIFIED / REVIEW_REQUIRED / NOT_SUPPORTED / BLOCKED",
    )

    reason_classification = Column(
        String(64),
        nullable=True,
        comment="balanced_journal / UNRESOLVED_PRONOUN / etc.",
    )

    journal_balanced = Column(Boolean, nullable=True)

    journal_narration = Column(Text, nullable=True)

    debit_accounts = Column(
        JSONB,
        nullable=True,
        comment='[{"account": "Purchases", "amount": "20000", "rule": "..."}]',
    )

    credit_accounts = Column(
        JSONB,
        nullable=True,
        comment='[{"account": "Raj", "amount": "20000", "rule": "..."}]',
    )

    calculations = Column(
        JSONB,
        nullable=True,
        comment='[{"id": "BK_LIST_PRICE", "label": "...", "result": "..."}]',
    )

    latency_ms = Column(Integer, nullable=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    # Relationships
    interaction = relationship(
        "FYJCInteraction",
        back_populates="interpretations",
    )
    candidate = relationship(
        "FYJCTrainingCandidate",
        back_populates="interpretation",
        uselist=False,
    )

    def __repr__(self) -> str:
        return (
            f"<FYJCInterpretation id={self.id} "
            f"model={self.model_id} kernel={self.kernel_status}>",
        )


# ----------------------------------------------------
# FYJC Table 3: Training Candidates
# ----------------------------------------------------

class FYJCTrainingCandidate(Base):
    """P4 lifecycle: candidate → validated/rejected/retired → export."""

    __tablename__ = "fyjc_training_candidates"

    id = Column(Integer, primary_key=True, index=True)

    interaction_id = Column(
        Integer,
        ForeignKey("fyjc_interactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    interpretation_id = Column(
        Integer,
        ForeignKey("fyjc_interpretations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    problem_id = Column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        comment="Deterministic hash from ProblemRecord",
    )

    content_hash = Column(
        String(64),
        index=True,
        nullable=True,
        comment="Deduplication key across interactions",
    )

    # -- Classification --

    category = Column(
        String(64),
        nullable=True,
        comment="cash_credit / settlement / gst / compound / etc.",
    )

    subcategory = Column(
        String(64),
        nullable=True,
        comment="missing_payment_mode / explicit_cash / etc.",
    )

    # -- Lifecycle --

    status = Column(
        String(32),
        nullable=False,
        default=FYJC_STATUS_CANDIDATE,
        index=True,
        comment="CANDIDATE / VALIDATED / REJECTED / RETIRED / CONFLICTING",
    )

    evidence_count = Column(Integer, default=0, nullable=False)
    validation_count = Column(Integer, default=0, nullable=False)
    rejection_count = Column(Integer, default=0, nullable=False)
    source_diversity = Column(Integer, default=0, nullable=False)

    confidence = Column(
        Numeric(5, 4),
        default=0,
        nullable=False,
        comment="Deterministic confidence from evidence counts",
    )

    # -- Human approval --

    human_approved = Column(Boolean, default=False, nullable=False)

    human_notes = Column(Text, nullable=True)

    human_approved_at = Column(DateTime, nullable=True)

    # -- Export --

    exported_to_jsonl = Column(Boolean, default=False, nullable=False)

    export_batch_id = Column(String(64), nullable=True)

    exported_at = Column(DateTime, nullable=True)

    # -- Provenance --

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    version = Column(Integer, default=1, nullable=False)

    # Relationships
    interaction = relationship(
        "FYJCInteraction",
        back_populates="candidates",
    )
    interpretation = relationship(
        "FYJCInterpretation",
        back_populates="candidate",
    )

    def __repr__(self) -> str:
        return (
            f"<FYJCTrainingCandidate id={self.id} "
            f"problem={self.problem_id} status={self.status}>",
        )


# ----------------------------------------------------
# FYJC Table 4: Knowledge Base
# ----------------------------------------------------

class FYJCKnowledgeBase(Base):
    """Validated knowledge patterns.  Reusable rules/vocabulary."""

    __tablename__ = "fyjc_knowledge_base"

    id = Column(Integer, primary_key=True, index=True)

    knowledge_id = Column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
        comment="Deterministic from pattern+type+scope",
    )

    pattern = Column(
        Text,
        nullable=False,
        comment="The input pattern observed",
    )

    canonical_interpretation = Column(
        Text,
        nullable=False,
        comment="What the pattern maps to",
    )

    knowledge_type = Column(
        String(32),
        nullable=False,
        comment="VOCABULARY / RULE / FORMULA / EDGE_CASE",
    )

    scope = Column(
        String(32),
        nullable=False,
        default=FYJC_KB_SCOPE_PERSONAL,
        comment="PERSONAL / CLASSROOM / SCHOOL / GLOBAL",
    )

    status = Column(
        String(32),
        nullable=False,
        default=FYJC_STATUS_CANDIDATE,
        index=True,
        comment="CANDIDATE / VALIDATED / REJECTED / RETIRED",
    )

    confidence = Column(
        Numeric(5, 4),
        default=0,
        nullable=False,
    )

    evidence_count = Column(Integer, default=0, nullable=False)
    validation_count = Column(Integer, default=0, nullable=False)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    version = Column(Integer, default=1, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<FYJCKnowledgeBase id={self.id} "
            f"kid={self.knowledge_id} status={self.status}>",
        )
