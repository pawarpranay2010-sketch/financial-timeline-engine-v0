ALTER TABLE financials
ADD CONSTRAINT unique_financial_metric
UNIQUE
(
company_id,
financial_year,
statement_type,
metric_name,
source_file
);
