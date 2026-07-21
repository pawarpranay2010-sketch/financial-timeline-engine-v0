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
# Filings
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

    source = Column(String)

    pdf_url = Column(Text)

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
