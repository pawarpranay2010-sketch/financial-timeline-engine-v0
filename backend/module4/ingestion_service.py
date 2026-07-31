"""
Financial Intelligence Ingestion Service

Pipeline

Provider Orchestrator
        ↓
PostgreSQL Cache
        ↓
Provider Fetch (if needed)
        ↓
Validation
        ↓
Normalization
        ↓
Database
        ↓
Redis Cache
"""

from backend.module4.provider_orchestrator import ProviderOrchestrator
from backend.module4.validator import Validator
from backend.module4.normalizer import Normalizer
from backend.module4.database_manager import DatabaseManager
from backend.module4.cache_manager import CacheManager
from backend.module4.logger import logger


class IngestionService:

    def __init__(self):

        self.validator = Validator()
        self.normalizer = Normalizer()

        self.database = DatabaseManager()
        self.cache = CacheManager()

        self.provider = ProviderOrchestrator()

    def ingest_company(self, provider_name: str, ticker: str):

        logger.info(f"[Ingestion] Starting ingestion for {ticker}")

        try:

            # --------------------------------------------------
            # Provider Orchestrator
            # --------------------------------------------------

            profile_data = self.provider.fetch_company_profile(ticker)
            financials_data = self.provider.fetch_financials(ticker)
            price_data = self.provider.fetch_market_price(ticker)
            news_data = self.provider.fetch_news(ticker)

            data = {
                "profile": profile_data,
                "financials": financials_data,
                "price": price_data,
                "news": news_data,
            }

            logger.info("[Ingestion] Provider fetch completed")

            profile = data["profile"]
            financials = data["financials"]
            price = data["price"]
            news = data["news"]

            # --------------------------------------------------
            # Validation
            # --------------------------------------------------

            profile_validation = self.validator.validate_company(profile)

            if not profile_validation.valid:
                raise ValueError(
                    f"Profile validation failed: {'; '.join(profile_validation.errors)}"
                )

            logger.info("[Ingestion] Validation completed")

            # --------------------------------------------------
            # Financials Expansion (period-level → metric-level)
            # --------------------------------------------------
            #
            # External providers (YFinance, FMP) return financials as
            # period-level dicts — one dict per fiscal year where all
            # metrics are keys on that dict:
            #
            #   {"date": "2024-09-28", "Total Revenue": 3.9e11, ...}
            #
            # The Normalizer and DatabaseManager expect metric-level
            # records — one dict per individual metric:
            #
            #   {"metric_name": "Total Revenue", "metric_value": 3.9e11,
            #    "financial_year": 2024, "statement_type": "income"}
            #
            # This expansion converts between the two formats.

            if isinstance(financials, dict):
                expanded = {}
                for statement_type, rows in financials.items():
                    if not isinstance(rows, list):
                        expanded[statement_type] = rows
                        continue
                    metrics = []
                    for row in rows:
                        # Check if already metric-level
                        if "metric_name" in row and "metric_value" in row:
                            metrics.append(row)
                            continue
                        # Extract financial year from date field
                        f_year = None
                        for date_key in ("date", "fiscal_year", "calendar_year"):
                            dv = row.get(date_key)
                            if dv is not None:
                                try:
                                    f_year = int(str(dv)[:4])
                                except (ValueError, TypeError):
                                    pass
                                break
                        # Expand each numeric key into a metric record
                        for key, val in row.items():
                            if key in ("date", "fiscal_year", "calendar_year",
                                       "company_id", "statement_type", "symbol"):
                                continue
                            if isinstance(val, (int, float)):
                                metrics.append({
                                    "company_id": None,
                                    "financial_year": f_year,
                                    "statement_type": statement_type,
                                    "metric_name": key,
                                    "metric_value": val,
                                    "currency": None,
                                    "source_provider": None,
                                    "source_document": None,
                                })
                    expanded[statement_type] = metrics
                financials = expanded
                logger.info(
                    f"[Ingestion] Expanded financials: "
                    f"{sum(len(v) for v in expanded.values())} metric records "
                    f"across {len(expanded)} statements"
                )

            # --------------------------------------------------
            # Normalization
            # --------------------------------------------------

            profile = self.normalizer.normalize_company(profile)

            if isinstance(financials, dict):
                financials = {
                    statement_type: [
                        self.normalizer.normalize_financial(item)
                        for item in rows
                    ]
                    for statement_type, rows in financials.items()
                    if isinstance(rows, list)
                }

            price = self.normalizer.normalize_price(price)

            if isinstance(news, list):
                news = [
                    self.normalizer.normalize_news(item)
                    for item in news
                ]
            else:
                news = [
                    self.normalizer.normalize_news(news)
                ]

            logger.info("[Ingestion] Normalization completed")

            # --------------------------------------------------
            # Database
            # --------------------------------------------------

            self.database.begin_transaction()

            # Save Company
            self.database.save_company(profile)

            # Flush so PostgreSQL generates company ID before
            # dependent inserts.
            self.database.connection.flush()

            logger.info("[Ingestion] Company saved and flushed")

            # Inject DB-generated company_id into financial & news items
            ticker_key = profile.get("ticker")
            if ticker_key:
                saved_company = self.database.get_latest_company(ticker_key)
                if saved_company:
                    cid = saved_company.id

                    # Inject into financial items
                    if isinstance(financials, dict):
                        fin_count = 0
                        for s_type in financials:
                            for item in financials[s_type]:
                                if isinstance(item, dict):
                                    item["company_id"] = cid
                                    fin_count += 1
                        logger.info(
                            f"[Ingestion] Injected company_id={cid} into "
                            f"{fin_count} financial items"
                        )

                    # Inject into news items
                    if isinstance(news, list):
                        news_count = 0
                        for item in news:
                            if isinstance(item, dict):
                                item["company_id"] = cid
                                news_count += 1
                        logger.info(
                            f"[Ingestion] Injected company_id={cid} into "
                            f"{news_count} news items"
                        )

                    # Also inject into price item
                    if isinstance(price, dict):
                        price["company_id"] = cid

            # Save Financial Statements
            self.database.save_financials(financials)

            logger.info("[Ingestion] Financial statements saved")

            # Save Market Price
            self.database.save_market_price(price)

            logger.info("[Ingestion] Market price saved")

            # Save News
            for item in news:
                self.database.save_news(item)

            logger.info("[Ingestion] News saved")

            self.database.commit()

            logger.info("[Ingestion] Database updated")

            # --------------------------------------------------
            # Redis Cache
            # --------------------------------------------------

            self.cache.cache_company(profile)
            self.cache.cache_price(price)
            self.cache.cache_news(news)

            logger.info("[Ingestion] Cache updated")

            return {
                "status": "success",
                "ticker": ticker,
            }

        except Exception as e:

            logger.error(f"[Ingestion] Failed for {ticker}: {e}")

            self.database.rollback()

            return {
                "status": "failed",
                "ticker": ticker,
                "error": str(e),
            }

        finally:

            self.database.close()


ingestion_service = IngestionService()
