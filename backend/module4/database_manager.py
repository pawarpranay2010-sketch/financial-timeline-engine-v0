"""
Module 4 - Database Manager

Responsible for:

- PostgreSQL connection
- Session management
- Safe transactions
- CRUD operations
- Restatement handling

Pipeline

Provider
↓

Validation
↓

Normalization
↓

DatabaseManager
↓

PostgreSQL

The rest of the application should NEVER directly
talk to PostgreSQL.

Everything should go through this layer.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.module4.config import Config

from backend.module4.models import Base
from backend.module4.models import Company
from backend.module4.models import FinancialMetric
from backend.module4.models import MarketPrice
from backend.module4.models import Filing
from backend.module4.models import News


class DatabaseManager:

    def __init__(self):

        self.engine = create_engine(
            Config.POSTGRES_URL,
            pool_pre_ping=True,
            future=True
        )

        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False
        )

    def create_tables(self):

        Base.metadata.create_all(bind=self.engine)

    @contextmanager
    def session(self):

        db = self.SessionLocal()

        try:

            yield db

            db.commit()

        except Exception:

            db.rollback()

            raise

        finally:

            db.close()

    # --------------------------------------------------
    # Company
    # --------------------------------------------------

    def save_company(self, company):

        with self.session() as db:

            obj = Company(**company)

            db.merge(obj)

    # --------------------------------------------------
    # Financials
    # --------------------------------------------------

    def save_financial_metric(self, metric):

        with self.session() as db:

            latest = (

                db.query(FinancialMetric)

                .filter(
                    FinancialMetric.company_id == metric["company_id"],
                    FinancialMetric.financial_year == metric["financial_year"],
                    FinancialMetric.metric_name == metric["metric_name"],
                    FinancialMetric.statement_type == metric["statement_type"],
                    FinancialMetric.is_latest == True
                )

                .first()

            )

            if latest:

                if latest.metric_value != metric["metric_value"]:

                    latest.is_latest = False

                    metric["version"] = latest.version + 1

                    metric["restated_from_version"] = latest.id

                else:

                    return

            obj = FinancialMetric(**metric)

            db.add(obj)

    # --------------------------------------------------
    # Prices
    # --------------------------------------------------

    def save_market_price(self, price):

        with self.session() as db:

            db.add(MarketPrice(**price))

    # --------------------------------------------------
    # News
    # --------------------------------------------------

    def save_news(self, article):

        with self.session() as db:

            db.add(News(**article))

    # --------------------------------------------------
    # Filings
    # --------------------------------------------------

    def save_filing(self, filing):

        with self.session() as db:

            db.add(Filing(**filing))

    # --------------------------------------------------
    # Read APIs
    # --------------------------------------------------

    def get_company(self, company_id):

        with self.session() as db:

            return (

                db.query(Company)

                .filter(
                    Company.company_id == company_id
                )

                .first()

            )

    def get_latest_financials(self, company_id):

        with self.session() as db:

            return (

                db.query(FinancialMetric)

                .filter(
                    FinancialMetric.company_id == company_id,
                    FinancialMetric.is_latest == True
                )

                .all()

            )

    def get_latest_news(self, company_id):

        with self.session() as db:

            return (

                db.query(News)

                .filter(
                    News.company_id == company_id
                )

                .all()

            )

    def get_latest_price(self, company_id):

        with self.session() as db:

            return (

                db.query(MarketPrice)

                .filter(
                    MarketPrice.company_id == company_id
                )

                .order_by(
                    MarketPrice.timestamp.desc()
                )

                .first()

            )


database_manager = DatabaseManager()
