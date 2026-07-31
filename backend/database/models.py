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
    String,
    Text,
)

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
