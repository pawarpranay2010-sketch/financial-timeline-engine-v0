"""
Database Manager

Handles all PostgreSQL interactions.

Responsibilities
----------------
- Insert new data
- Update existing data
- Mark old records as outdated
- Future support for Restatement Engine
"""

from datetime import datetime

from backend.database.db import SessionLocal
from backend.module4.logger import logger


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

        logger.info(f"[DB] Saving company: {company.get('ticker')}")

        # TODO
        # INSERT INTO companies ...

    # --------------------------------------------------
    # Financial Statements
    # --------------------------------------------------

    def save_financials(self, financials):

        logger.info("[DB] Saving financial statements")

        # TODO

    # --------------------------------------------------
    # Market Prices
    # --------------------------------------------------

    def save_market_price(self, price):

        logger.info("[DB] Saving latest market price")

        # TODO

    # --------------------------------------------------
    # News
    # --------------------------------------------------

    def save_news(self, news):

        logger.info("[DB] Saving company news")

        # TODO

    # --------------------------------------------------
    # Corporate Actions
    # --------------------------------------------------

    def save_corporate_actions(self, actions):

        logger.info("[DB] Saving corporate actions")

        # TODO

    # --------------------------------------------------
    # Filings
    # --------------------------------------------------

    def save_filing(self, filing):

        logger.info("[DB] Saving filing")

        # TODO

    # --------------------------------------------------
    # Lookup Methods
    # --------------------------------------------------

    def company_exists(self, ticker):

        logger.info(f"[DB] Checking company: {ticker}")

        return False

    def get_latest_company(self, ticker):

        logger.info(f"[DB] Fetching company: {ticker}")

        return None

    def get_latest_financials(self, company_id):

        logger.info(f"[DB] Fetching financials: {company_id}")

        return None

    def get_latest_price(self, ticker):

        logger.info(f"[DB] Fetching latest price: {ticker}")

        return None

    def get_latest_news(self, ticker):

        logger.info(f"[DB] Fetching latest news: {ticker}")

        return None

    # --------------------------------------------------
    # Restatement Engine Support
    # --------------------------------------------------

    def mark_old_record(self, record_id):

        logger.info(f"[DB] Marking old record: {record_id}")

    def insert_new_version(self, record):

        logger.info("[DB] Inserting updated record")

    # --------------------------------------------------
    # Transaction Support
    # --------------------------------------------------

    def begin_transaction():

        logger.info("[DB] BEGIN TRANSACTION")

    def commit(self):

        try:
            self.connection.commit()
            logger.info("[DB] COMMIT SUCCESS")
        except Exception as e:
            logger.error(f"[DB] COMMIT FAILED: {e}")
            raise

    def rollback(self):

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

        self.connection.close()
        logger.info("[DB] Database session closed")
