CREATE INDEX idx_company_ticker
ON companies(ticker);

CREATE INDEX idx_financial_company
ON financials(company_id);

CREATE INDEX idx_financial_year
ON financials(financial_year);

CREATE INDEX idx_market_company
ON market_prices(company_id);

CREATE INDEX idx_market_date
ON market_prices(trade_date);

CREATE INDEX idx_news_company
ON news(company_id);

CREATE INDEX idx_filings_company
ON filings(company_id);

CREATE INDEX idx_ratios_company
ON ratios(company_id);
