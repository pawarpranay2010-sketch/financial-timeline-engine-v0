

from datetime import datetime, datefrom backend.database.db import SessionLocalfrom backend.module4.logger import logger
# Import your explicit SQLAlchemy modelsfrom backend.database.models import Company, Financial, MarketPrice, News, Filing, CorporateAction

class DatabaseManager:

def __init__(self):
"""
Initialize PostgreSQL session.
"""
self.connection = SessionLocal()
logger.info("[DB] Database session initialized")

# --------------------------------------------------
# Company
# --------------------------------------------------

def save_company(self, company):
"""
Saves or updates a company record in the database using an upsert strategy.
Accepts a dictionary representation of the company profile data.
"""
ticker_val = company.get("ticker")
if not ticker_val:
logger.warning("[DB] Cannot save company missing a ticker attribute")
return

ticker_upper = ticker_val.strip().upper()
logger.info(f"[DB] Saving company: {ticker_upper}")

try:
existing = self.connection.query(Company).filter(Company.ticker == ticker_upper).first()

if existing:
existing.company_name = company.get("company_name", existing.company_name)
existing.exchange = company.get("exchange", existing.exchange)
existing.sector = company.get("sector", existing.sector)
existing.industry = company.get("industry", existing.industry)
existing.isin = company.get("isin", existing.isin)
existing.market_cap = company.get("market_cap", existing.market_cap)
existing.currency = company.get("currency", existing.currency)
logger.info(f"[DB] Updated existing company: {ticker_upper}")
else:
new_company = Company(
ticker=ticker_upper,
company_name=company.get("company_name"),
exchange=company.get("exchange"),
sector=company.get("sector"),
industry=company.get("industry"),
isin=company.get("isin"),
market_cap=company.get("market_cap"),
currency=company.get("currency")
)
self.connection.add(new_company)
logger.info(f"[DB] Created new company record: {ticker_upper}")
except Exception as e:
logger.error(f"[DB] Error saving company {ticker_upper}: {e}")
raise

# --------------------------------------------------
# Financial Statements
# --------------------------------------------------

def save_financials(self, financials):
"""
Aggregates and saves statement structures into flattened database fields.
Accepts a list of financial entries or a dictionary grouping statement arrays.
"""
logger.info("[DB] Saving financial statements")

if not financials:
return

try:
records_list = []
if isinstance(financials, dict):
for s_type, items in financials.items():
if isinstance(items, list):
records_list.extend(items)
elif isinstance(financials, list):
records_list = financials

# Group key-value tracking objects dynamically to populate matching model fields
grouped_periods = {}
for item in records_list:
comp_id = item.get("company_id")
f_year = item.get("financial_year", item.get("fiscal_year"))
f_quarter = item.get("fiscal_quarter", "FY")
s_type = item.get("statement_type")

if not comp_id or not f_year:
continue

key = (int(comp_id), int(f_year), str(f_quarter), s_type)
if key not in grouped_periods:
grouped_periods[key] = {
"source": item.get("reporting_source", item.get("source", "FMP")),
"is_latest": item.get("is_latest", True)
}

# Check for metric dictionary components or direct field attributes
m_name = item.get("metric_name", "").lower().replace(" ", "_")
m_val = item.get("metric_value")

if m_name and m_val is not None:
grouped_periods[key][m_name] = float(m_val)

for direct_field in ["revenue", "ebitda", "ebit", "net_income", "eps",
"total_assets", "total_liabilities", "shareholders_equity",
"operating_cash_flow", "free_cash_flow"]:
if item.get(direct_field) is not None:
grouped_periods[key][direct_field] = float(item.get(direct_field))

# Store consolidated profiles to physical table columns
for (comp_id, year, quarter, s_type), fields in grouped_periods.items():
existing = self.connection.query(Financial).filter(
Financial.company_id == comp_id,
Financial.fiscal_year == year,
Financial.fiscal_quarter == quarter,
Financial.statement_type == s_type
).first()

if existing:
existing.revenue = fields.get("revenue", existing.revenue)
existing.ebitda = fields.get("ebitda", existing.ebitda)
existing.ebit = fields.get("ebit", existing.ebit)
existing.net_income = fields.get("net_income", existing.net_income)
existing.eps = fields.get("eps", existing.eps)
existing.total_assets = fields.get("total_assets", existing.total_assets)
existing.total_liabilities = fields.get("total_liabilities", existing.total_liabilities)
existing.shareholders_equity = fields.get("shareholders_equity", existing.shareholders_equity)
existing.operating_cash_flow = fields.get("operating_cash_flow", existing.operating_cash_flow)
existing.free_cash_flow = fields.get("free_cash_flow", existing.free_cash_flow)
existing.is_latest = fields.get("is_latest", existing.is_latest)
existing.source = fields.get("source", existing.source)
else:
new_financial = Financial(
company_id=comp_id,
statement_type=s_type,
fiscal_year=year,
fiscal_quarter=quarter,
revenue=fields.get("revenue"),
ebitda=fields.get("ebitda"),
ebit=fields.get("ebit"),
net_income=fields.get("net_income"),
eps=fields.get("eps"),
total_assets=fields.get("total_assets"),
total_liabilities=fields.get("total_liabilities"),
shareholders_equity=fields.get("shareholders_equity"),
operating_cash_flow=fields.get("operating_cash_flow"),
free_cash_flow=fields.get("free_cash_flow"),
is_latest=fields.get("is_latest", True),
source=fields.get("source")
)
self.connection.add(new_financial)
except Exception as e:
logger.error(f"[DB] Error saving financial statements: {e}")
raise

# --------------------------------------------------
# Market Prices
# --------------------------------------------------

def save_market_price(self, price):
"""
Saves or updates daily market price indices to the database.
"""
logger.info("[DB] Saving latest market price")

comp_id = price.get("company_id")
t_date = price.get("trading_date")

# Cleanly convert various datetime formats down to date instances
if isinstance(t_date, str):
t_date = datetime.strptime(t_date.split(" ")[0], "%Y-%m-%d").date()
elif isinstance(t_date, datetime):
t_date = t_date.date()
elif t_date is None and price.get("timestamp"):
ts = price.get("timestamp")
t_date = datetime.fromtimestamp(ts).date() if isinstance(ts, (int, float)) else ts.date()

if not comp_id or not t_date:
logger.warning("[DB] Missing mandatory parameters to map market price item")
return

try:
existing = self.connection.query(MarketPrice).filter(
MarketPrice.company_id == int(comp_id),
MarketPrice.trading_date == t_date
).first()

close_val = price.get("close_price", price.get("price"))

if existing:
existing.open_price = price.get("open_price", existing.open_price)
existing.high_price = price.get("high_price", existing.high_price)
existing.low_price = price.get("low_price", existing.low_price)
existing.close_price = close_val if close_val is not None else existing.close_price
existing.adjusted_close = price.get("adjusted_close", existing.adjusted_close)
existing.volume = price.get("volume", existing.volume)
else:
new_price = MarketPrice(
company_id=int(comp_id),
trading_date=t_date,
open_price=price.get("open_price"),

high_price=price.get("high_price"),
low_price=price.get("low_price"),
close_price=close_val,
adjusted_close=price.get("adjusted_close"),
volume=price.get("volume")
)
self.connection.add(new_price)
except Exception as e:
logger.error(f"[DB] Error saving market price for company ID {comp_id}: {e}")
raise
# --------------------------------------------------
# News
# --------------------------------------------------
def save_news(self, news):
"""
Saves a corporate news item into the news table. Deduplicates by headline text.
"""
logger.info("[DB] Saving company news")
comp_id = news.get("company_id")
headline_val = news.get("headline")
if not comp_id or not headline_val:
return
try:
existing = self.connection.query(News).filter(
News.company_id == int(comp_id),
News.headline == headline_val
).first()
pub_at = news.get("published_at")
if isinstance(pub_at, str):
pub_at = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
if not existing:
new_news = News(
company_id=int(comp_id),
headline=headline_val,
summary=news.get("summary", news.get("text")),
source=news.get("source", news.get("site")),
url=news.get("url"),
published_at=pub_at
)
self.connection.add(new_news)
except Exception as e:
logger.error(f"[DB] Error saving news item: {e}")
raise
# --------------------------------------------------
# Corporate Actions
# --------------------------------------------------
def save_corporate_actions(self, actions):
"""
Stores distinct corporate actions (such as stock splits or dividends) to the repository.
"""
logger.info("[DB] Saving corporate actions")
comp_id = actions.get("company_id")
act_date = actions.get("action_date")
act_type = actions.get("action_type")
if isinstance(act_date, str):
act_date = datetime.strptime(act_date, "%Y-%m-%d").date()
if not comp_id or not act_date or not act_type:
return
try:
existing = self.connection.query(CorporateAction).filter(
CorporateAction.company_id == int(comp_id),
CorporateAction.action_date == act_date,
CorporateAction.action_type == act_type
).first()
if not existing:
new_action = CorporateAction(
company_id=int(comp_id),
action_type=act_type,
action_date=act_date,
description=actions.get("description")
)
self.connection.add(new_action)
except Exception as e:
logger.error(f"[DB] Error saving corporate action: {e}")
raise
# --------------------------------------------------
# Filings
# --------------------------------------------------
def save_filing(self, filing):
"""
Saves corporate transparency documents and regulatory filing tracks.
"""
logger.info("[DB] Saving filing")
comp_id = filing.get("company_id")
f_type = filing.get("filing_type", filing.get("form"))
f_date = filing.get("filing_date", filing.get("filling_date"))
if isinstance(f_date, str):
f_date = datetime.strptime(f_date.split(" ")[0], "%Y-%m-%d").date()
if not comp_id or not f_type or not f_date:
return
try:
existing = self.connection.query(Filing).filter(
Filing.company_id == int(comp_id),
Filing.filing_type == f_type,
Filing.filing_date == f_date
).first()
if not existing:
new_filing = Filing(
company_id=int(comp_id),
filing_type=f_type,
filing_date=f_date,
source=filing.get("source", "SEC"),
pdf_url=filing.get("pdf_url", filing.get("link")),
processed=filing.get("processed", False)
)
self.connection.add(new_filing)
except Exception as e:
logger.error(f"[DB] Error saving filing: {e}")
raise
# --------------------------------------------------
# Lookup Methods
# --------------------------------------------------
def company_exists(self, ticker) -> bool:
"""Checks if a company ticker identifier is active in the repository."""
logger.info(f"[DB] Checking company: {ticker}")
if not ticker:
return False
return self.connection.query(Company).filter(Company.ticker == ticker.strip().upper()).count() > 0
def get_latest_company(self, ticker) -> Company:
"""Fetches the primary company mapping record for a given ticker string."""
logger.info(f"[DB] Fetching company: {ticker}")
if not ticker:
return None
return self.connection.query(Company).filter(Company.ticker == ticker.strip().upper()).first()
def get_latest_financials(self, company_id) -> list:
"""Retrieves active historical financial statement sets by company database identifier."""
logger.info(f"[DB] Fetching financials: {company_id}")
return self.connection.query(Financial).filter(
Financial.company_id == int(company_id),
Financial.is_latest == True
).all()
def get_latest_price(self, ticker) -> MarketPrice:
"""Retrieves the most recent end-of-day pricing record for a ticker."""
logger.info(f"[DB] Fetching latest price: {ticker}")
comp = self.get_latest_company(ticker)
if not comp:
return None
return self.connection.query(MarketPrice).filter(
MarketPrice.company_id == comp.id
).order_by(MarketPrice.trading_date.desc()).first()
def get_latest_news(self, ticker) -> list:
"""Retrieves the top 10 most recent news articles for a ticker."""
logger.info(f"[DB] Fetching latest news: {ticker}")
comp = self.get_latest_company(ticker)
if not comp:
return []
return self.connection.query(News).filter(
News.company_id == comp.id
).order_by(News.published_at.desc()).limit(10).all()
# --------------------------------------------------
# Restatement Engine Support
# --------------------------------------------------
def mark_old_record(self, record_id):
"""
Marks an old financial metrics profile as outdated during correction ingestion.
"""
logger.info(f"[DB] Marking old record: {record_id}")
try:
record = self.connection.query(Financial).filter(Financial.id == int(record_id)).first()
if record:
record.is_latest = False
logger.info(f"[DB] Record {record_id} marked as outdated (is_latest=False)")
except Exception as e:
logger.error(f"[DB] Error marking old record {record_id}: {e}")
raise
def insert_new_version(self, record):
"""
Inserts an updated statement instance or data dictionary marked as the current active version.
"""
logger.info("[DB] Inserting updated record")
try:
if isinstance(record, Financial):
record.is_latest = True
self.connection.add(record)
elif isinstance(record, dict):
record["is_latest"] = True
new_fin = Financial(**record)
self.connection.add(new_fin)
except Exception as e:
logger.error(f"[DB] Error inserting new statement version: {e}")
raise
# --------------------------------------------------
# Transaction Support
# --------------------------------------------------
def begin_transaction(self):
"""
Establishes transaction boundaries. Logs tracking info as required by the pipeline.
"""
logger.info("[DB] BEGIN TRANSACTION")
def commit(self):
"""
Commits all staged entity updates safely to the physical database engine.
"""
try:
self.connection.commit()
logger.info("[DB] COMMIT SUCCESS")
except Exception as e:
logger.error(f"[DB] COMMIT FAILED: {e}")
raise
def rollback(self):
"""
Rolls back modifications if a database exception occurs.
"""
try:
self.connection.rollback()
logger.warning("[DB] ROLLBACK EXECUTED")
except Exception as e:
logger.error(f"[DB] ROLLBACK FAILED: {e}")
raise
# --------------------------------------------------
# Close Session
# --------------------------------------------------
def close(self):
"""
Closes the active database connection session cleanly.
"""
self.connection.close()
logger.info("[DB] Database session closed")




