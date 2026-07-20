-- ============================================
-- Financial Intelligence Database Schema
-- ============================================

CREATE TABLE companies (
    id SERIAL PRIMARY KEY,

    ticker VARCHAR(20) NOT NULL UNIQUE,

    company_name TEXT NOT NULL,

    exchange VARCHAR(20),

    sector TEXT,

    industry TEXT,

    isin VARCHAR(30),

    market_cap NUMERIC,

    created_at TIMESTAMP DEFAULT NOW(),

    updated_at TIMESTAMP DEFAULT NOW()
);

--------------------------------------------------

CREATE TABLE financials (

    id SERIAL PRIMARY KEY,

    company_id INTEGER REFERENCES companies(id),

    financial_year VARCHAR(20),

    statement_type VARCHAR(50),

    metric_name TEXT,

    metric_alias TEXT,

    metric_value NUMERIC,

    unit VARCHAR(20),

    source_file TEXT,

    filing_date DATE,

    reporting_date DATE,

    is_latest BOOLEAN DEFAULT TRUE,

    restated_from INTEGER,

    confidence_score NUMERIC,

    created_at TIMESTAMP DEFAULT NOW()
);

--------------------------------------------------

CREATE TABLE market_prices (

    id SERIAL PRIMARY KEY,

    company_id INTEGER REFERENCES companies(id),

    trade_date DATE,

    open_price NUMERIC,

    high_price NUMERIC,

    low_price NUMERIC,

    close_price NUMERIC,

    volume BIGINT,

    source TEXT,

    created_at TIMESTAMP DEFAULT NOW()
);

--------------------------------------------------

CREATE TABLE ratios (

    id SERIAL PRIMARY KEY,

    company_id INTEGER REFERENCES companies(id),

    financial_year VARCHAR(20),

    pe_ratio NUMERIC,

    pb_ratio NUMERIC,

    roe NUMERIC,

    roa NUMERIC,

    debt_to_equity NUMERIC,

    current_ratio NUMERIC,

    quick_ratio NUMERIC,

    eps NUMERIC,

    dividend_yield NUMERIC,

    created_at TIMESTAMP DEFAULT NOW()
);

--------------------------------------------------

CREATE TABLE filings (

    id SERIAL PRIMARY KEY,

    company_id INTEGER REFERENCES companies(id),

    filing_type VARCHAR(100),

    filing_date DATE,

    source_url TEXT,

    local_path TEXT,

    checksum TEXT,

    created_at TIMESTAMP DEFAULT NOW()
);

--------------------------------------------------

CREATE TABLE news (

    id SERIAL PRIMARY KEY,

    company_id INTEGER REFERENCES companies(id),

    headline TEXT,

    summary TEXT,

    published_at TIMESTAMP,

    source TEXT,

    url TEXT,

    sentiment VARCHAR(20),

    created_at TIMESTAMP DEFAULT NOW()
);

--------------------------------------------------

CREATE TABLE corporate_actions (

    id SERIAL PRIMARY KEY,

    company_id INTEGER REFERENCES companies(id),

    action_type VARCHAR(100),

    announcement_date DATE,

    effective_date DATE,

    description TEXT,

    created_at TIMESTAMP DEFAULT NOW()
);

--------------------------------------------------

CREATE TABLE peer_relationships (

    id SERIAL PRIMARY KEY,

    company_id INTEGER REFERENCES companies(id),

    peer_company_id INTEGER REFERENCES companies(id),

    relation_type VARCHAR(100),

    confidence NUMERIC,

    created_at TIMESTAMP DEFAULT NOW()
);

--------------------------------------------------

CREATE TABLE metadata (

    id SERIAL PRIMARY KEY,

    key TEXT,

    value TEXT,

    updated_at TIMESTAMP DEFAULT NOW()
);
