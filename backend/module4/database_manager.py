from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.database.db import SessionLocal
from backend.database.models import (
    Company,
    CorporateAction,
    Filing,
    Financial,
    MarketPrice,
    News,
)
from backend.module4.logger import logger


class DatabaseManager:
    def __init__(self):
        """Initialize the database session."""
        self.connection = SessionLocal()
        logger.info("[DB] Database session initialized")

    # --------------------------------------------------
    # Company
    # --------------------------------------------------

    def save_company(self, company):
        """Save or update a company record."""
        ticker_val = (company or {}).get("ticker")
        if not ticker_val:
            logger.warning("[DB] Cannot save company missing a ticker attribute")
            return

        ticker_upper = ticker_val.strip().upper()
        logger.info(f"[DB] Saving company: {ticker_upper}")

        try:
            existing = (
                self.connection.query(Company)
                .filter(Company.ticker == ticker_upper)
                .first()
            )

            if existing:
                existing.company_name = company.get("company_name", existing.company_name)
                existing.exchange = company.get("exchange", existing.exchange)
                existing.sector = company.get("sector", existing.sector)
                existing.industry = company.get("industry", existing.industry)
                existing.isin = company.get("isin", existing.isin)
                existing.market_cap = company.get("market_cap", existing.market_cap)
                existing.currency = company.get("currency", existing.currency)
                logger.info(f"[DB] Updated existing company: {ticker_upper}")
                return

            self.connection.add(
                Company(
                    ticker=ticker_upper,
                    company_name=company.get("company_name"),
                    exchange=company.get("exchange"),
                    sector=company.get("sector"),
                    industry=company.get("industry"),
                    isin=company.get("isin"),
                    market_cap=company.get("market_cap"),
                    currency=company.get("currency"),
                )
            )
            logger.info(f"[DB] Created new company record: {ticker_upper}")
        except Exception as e:
            logger.error(f"[DB] Error saving company {ticker_upper}: {e}")
            raise

    # --------------------------------------------------
    # Financial Statements
    # --------------------------------------------------

    def save_financials(self, financials):
        """Flatten and save financial statement data."""
        logger.info("[DB] Saving financial statements")

        if not financials:
            return

        try:
            records_list = []
            if isinstance(financials, dict):
                for items in financials.values():
                    if isinstance(items, list):
                        records_list.extend(items)
            elif isinstance(financials, list):
                records_list = financials

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
                        "is_latest": item.get("is_latest", True),
                    }

                metric_name = item.get("metric_name", "").lower().replace(" ", "_")
                metric_value = item.get("metric_value")
                if metric_name and metric_value is not None:
                    grouped_periods[key][metric_name] = float(metric_value)

                for direct_field in [
                    "revenue",
                    "ebitda",
                    "ebit",
                    "net_income",
                    "eps",
                    "total_assets",
                    "total_liabilities",
                    "shareholders_equity",
                    "operating_cash_flow",
                    "free_cash_flow",
                ]:
                    if item.get(direct_field) is not None:
                        grouped_periods[key][direct_field] = float(item.get(direct_field))

            for (comp_id, year, quarter, s_type), fields in grouped_periods.items():
                existing = (
                    self.connection.query(Financial)
                    .filter(
                        Financial.company_id == comp_id,
                        Financial.fiscal_year == year,
                        Financial.fiscal_quarter == quarter,
                        Financial.statement_type == s_type,
                    )
                    .first()
                )

                if existing:
                    existing.revenue = fields.get("revenue", existing.revenue)
                    existing.ebitda = fields.get("ebitda", existing.ebitda)
                    existing.ebit = fields.get("ebit", existing.ebit)
                    existing.net_income = fields.get("net_income", existing.net_income)
                    existing.eps = fields.get("eps", existing.eps)
                    existing.total_assets = fields.get("total_assets", existing.total_assets)
                    existing.total_liabilities = fields.get(
                        "total_liabilities",
                        existing.total_liabilities,
                    )
                    existing.shareholders_equity = fields.get(
                        "shareholders_equity",
                        existing.shareholders_equity,
                    )
                    existing.operating_cash_flow = fields.get(
                        "operating_cash_flow",
                        existing.operating_cash_flow,
                    )
                    existing.free_cash_flow = fields.get(
                        "free_cash_flow",
                        existing.free_cash_flow,
                    )
                    existing.is_latest = fields.get("is_latest", existing.is_latest)
                    existing.source = fields.get("source", existing.source)
                    continue

                self.connection.add(
                    Financial(
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
                        source=fields.get("source"),
                    )
                )
        except Exception as e:
            logger.error(f"[DB] Error saving financial statements: {e}")
            raise

    # --------------------------------------------------
    # Market Prices
    # --------------------------------------------------

    def save_market_price(self, price):
        """Save or update a market price record."""
        logger.info("[DB] Saving latest market price")

        comp_id = (price or {}).get("company_id")
        trading_date = self._coerce_trading_date(price or {})

        if not comp_id or not trading_date:
            logger.warning("[DB] Missing mandatory parameters to map market price item")
            return

        try:
            existing = (
                self.connection.query(MarketPrice)
                .filter(
                    MarketPrice.company_id == int(comp_id),
                    MarketPrice.trading_date == trading_date,
                )
                .first()
            )

            close_val = price.get("close_price", price.get("price"))

            if existing:
                existing.open_price = price.get("open_price", existing.open_price)
                existing.high_price = price.get("high_price", existing.high_price)
                existing.low_price = price.get("low_price", existing.low_price)
                existing.close_price = (
                    close_val if close_val is not None else existing.close_price
                )
                existing.adjusted_close = price.get(
                    "adjusted_close",
                    existing.adjusted_close,
                )
                existing.volume = price.get("volume", existing.volume)
                return

            self.connection.add(
                MarketPrice(
                    company_id=int(comp_id),
                    trading_date=trading_date,
                    open_price=price.get("open_price"),
                    high_price=price.get("high_price"),
                    low_price=price.get("low_price"),
                    close_price=close_val,
                    adjusted_close=price.get("adjusted_close"),
                    volume=price.get("volume"),
                )
            )
        except Exception as e:
            logger.error(f"[DB] Error saving market price for company ID {comp_id}: {e}")
            raise

    # --------------------------------------------------
    # News
    # --------------------------------------------------

    def save_news(self, news):
        """Save one news item or a list of items."""
        logger.info("[DB] Saving company news")

        if isinstance(news, list):
            for item in news:
                self.save_news(item)
            return

        comp_id = (news or {}).get("company_id")
        headline_val = (news or {}).get("headline")
        if not comp_id or not headline_val:
            return

        try:
            existing = (
                self.connection.query(News)
                .filter(
                    News.company_id == int(comp_id),
                    News.headline == headline_val,
                )
                .first()
            )
            if existing:
                return

            published_at = self._coerce_datetime(news.get("published_at"))
            self.connection.add(
                News(
                    company_id=int(comp_id),
                    headline=headline_val,
                    summary=news.get("summary", news.get("text")),
                    source=news.get("source", news.get("site")),
                    url=news.get("url"),
                    published_at=published_at,
                )
            )
        except Exception as e:
            logger.error(f"[DB] Error saving news item: {e}")
            raise

    # --------------------------------------------------
    # Corporate Actions
    # --------------------------------------------------

    def save_corporate_actions(self, actions):
        """Store a corporate action if it does not already exist."""
        logger.info("[DB] Saving corporate actions")

        comp_id = (actions or {}).get("company_id")
        action_date = self._coerce_date((actions or {}).get("action_date"))
        action_type = (actions or {}).get("action_type")
        if not comp_id or not action_date or not action_type:
            return

        try:
            existing = (
                self.connection.query(CorporateAction)
                .filter(
                    CorporateAction.company_id == int(comp_id),
                    CorporateAction.action_date == action_date,
                    CorporateAction.action_type == action_type,
                )
                .first()
            )
            if existing:
                return

            self.connection.add(
                CorporateAction(
                    company_id=int(comp_id),
                    action_type=action_type,
                    action_date=action_date,
                    description=actions.get("description"),
                )
            )
        except Exception as e:
            logger.error(f"[DB] Error saving corporate action: {e}")
            raise

    # --------------------------------------------------
    # Filings
    # --------------------------------------------------

    def save_filing(self, filing):
        """Save a filing if it does not already exist."""
        logger.info("[DB] Saving filing")

        filing_data = filing or {}
        comp_id = filing_data.get("company_id")
        filing_type = filing_data.get("filing_type") or filing_data.get("form")
        filing_date = self._coerce_date(
            filing_data.get("filing_date") or filing_data.get("filling_date")
        )
        if not comp_id or not filing_type or not filing_date:
            return

        try:
            existing = (
                self.connection.query(Filing)
                .filter(
                    Filing.company_id == int(comp_id),
                    Filing.filing_type == filing_type,
                    Filing.filing_date == filing_date,
                )
                .first()
            )
            if existing:
                return

            self.connection.add(
                Filing(
                    company_id=int(comp_id),
                    filing_type=filing_type,
                    filing_date=filing_date,
                    source=filing_data.get("source", "SEC"),
                    pdf_url=filing_data.get("pdf_url", filing_data.get("link")),
                    processed=filing_data.get("processed", False),
                )
            )
        except Exception as e:
            logger.error(f"[DB] Error saving filing: {e}")
            raise

    # --------------------------------------------------
    # Lookup Methods
    # --------------------------------------------------

    def company_exists(self, ticker) -> bool:
        logger.info(f"[DB] Checking company: {ticker}")
        if not ticker:
            return False
        return (
            self.connection.query(Company)
            .filter(Company.ticker == ticker.strip().upper())
            .count()
            > 0
        )

    def get_latest_company(self, ticker) -> Optional[Company]:
        logger.info(f"[DB] Fetching company: {ticker}")
        if not ticker:
            return None
        return (
            self.connection.query(Company)
            .filter(Company.ticker == ticker.strip().upper())
            .first()
        )

    def get_latest_financials(self, company_id) -> list:
        logger.info(f"[DB] Fetching financials: {company_id}")
        return (
            self.connection.query(Financial)
            .filter(
                Financial.company_id == int(company_id),
                Financial.is_latest.is_(True),
            )
            .all()
        )

    def get_latest_price(self, ticker) -> Optional[MarketPrice]:
        logger.info(f"[DB] Fetching latest price: {ticker}")
        company = self.get_latest_company(ticker)
        if not company:
            return None
        return (
            self.connection.query(MarketPrice)
            .filter(MarketPrice.company_id == company.id)
            .order_by(MarketPrice.trading_date.desc())
            .first()
        )

    def get_latest_news(self, ticker) -> list:
        logger.info(f"[DB] Fetching latest news: {ticker}")
        company = self.get_latest_company(ticker)
        if not company:
            return []
        return (
            self.connection.query(News)
            .filter(News.company_id == company.id)
            .order_by(News.published_at.desc())
            .limit(10)
            .all()
        )

    # --------------------------------------------------
    # Restatement Engine Support
    # --------------------------------------------------

    def mark_old_record(self, record_id):
        logger.info(f"[DB] Marking old record: {record_id}")
        try:
            record = (
                self.connection.query(Financial)
                .filter(Financial.id == int(record_id))
                .first()
            )
            if record:
                record.is_latest = False
                logger.info(
                    f"[DB] Record {record_id} marked as outdated (is_latest=False)"
                )
        except Exception as e:
            logger.error(f"[DB] Error marking old record {record_id}: {e}")
            raise

    def insert_new_version(self, record):
        logger.info("[DB] Inserting updated record")
        try:
            if isinstance(record, Financial):
                record.is_latest = True
                self.connection.add(record)
                return

            if isinstance(record, dict):
                payload = dict(record)
                payload["is_latest"] = True
                self.connection.add(Financial(**payload))
        except Exception as e:
            logger.error(f"[DB] Error inserting new statement version: {e}")
            raise

    # --------------------------------------------------
    # Transaction Support
    # --------------------------------------------------

    def begin_transaction(self):
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

    # --------------------------------------------------
    # Internal helpers
    # --------------------------------------------------

    def _coerce_trading_date(self, price) -> Optional[datetime.date]:
        trading_date = price.get("trading_date")
        if trading_date is not None:
            return self._coerce_date(trading_date)

        timestamp = price.get("timestamp")
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp).date()
        if isinstance(timestamp, datetime):
            return timestamp.date()
        return None

    def _coerce_datetime(self, value) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return None

    def _coerce_date(self, value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            return value
        if isinstance(value, str):
            return datetime.strptime(value.split(" ")[0], "%Y-%m-%d").date()
        return None


