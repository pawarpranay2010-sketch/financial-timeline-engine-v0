"""
Clean test: Delete existing Financial records, run IngestionService,
verify persistence.
"""
import sys, os, logging
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

from backend.module4.database_manager import DatabaseManager
from backend.database.models import Financial, Company
from sqlalchemy import text

dbm = DatabaseManager()

# 1. Show state before
before = dbm.connection.query(Financial).all()
print(f"Financial records before: {len(before)}")

# 2. Get AAPL company
company = dbm.connection.query(Company).filter(Company.ticker == "AAPL").first()
if company:
    print(f"AAPL company: id={company.id}, name={company.company_name}")
else:
    print("Creating AAPL company...")
    from backend.database.models import Company as C
    c = C(ticker="AAPL", company_name="Test AAPL v2", exchange="NASDAQ")
    dbm.connection.add(c)
    dbm.connection.commit()
    company = dbm.connection.query(Company).filter(Company.ticker == "AAPL").first()
    print(f"Created AAPL: id={company.id}")

# 3. Run the FULL IngestionService pipeline
print("\n" + "=" * 60)
print("Calling IngestionService.ingest_company()")
print("=" * 60)

from backend.module4.ingestion_service import IngestionService
ingestion = IngestionService()

result = ingestion.ingest_company("yfinance", "AAPL")
print(f"\nIngestion status: {result.get('status')}")
if result.get('status') == 'failed':
    print(f"Error: {result.get('error')}")
else:
    print("Ingestion succeeded!")

# 4. Check financial records
print("\n" + "=" * 60)
print("Checking Financial records after ingestion")
print("=" * 60)

records = dbm.connection.query(Financial).filter(
    Financial.company_id == company.id
).all()
print(f"Financial records for AAPL: {len(records)}")

if records:
    for r in records:
        print(f"  id={r.id} company_id={r.company_id} year={r.fiscal_year} "
              f"quarter={r.fiscal_quarter} type={r.statement_type:<18} "
              f"rev={r.revenue} ni={r.net_income} ebitda={r.ebitda} "
              f"fcf={r.free_cash_flow} ta={r.total_assets}")
else:
    print("  NO RECORDS FOUND!")
    
    # Check ALL financial records (maybe company_id mismatch)
    all_records = dbm.connection.query(Financial).all()
    print(f"\nAll Financial records in DB: {len(all_records)}")
    for r in all_records:
        print(f"  id={r.id} company_id={r.company_id} year={r.fiscal_year} type={r.statement_type}")

dbm.close()
