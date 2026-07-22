"""
Module 4 Scheduler

Responsible for scheduling background collection jobs.

This file DOES NOT perform API calls.

It only defines recurring jobs.

Execution Flow

Scheduler
    ↓
Data Collector
    ↓
Provider Manager
    ↓
Validation
    ↓
Normalization
    ↓
Database
    ↓
Redis Cache

Future:
This scheduler can run with:

- APScheduler
- Celery Beat
- Cron
- Kubernetes CronJobs

Current implementation uses APScheduler.
"""

from apscheduler.schedulers.background import BackgroundScheduler

from backend.module4.config import (
    PRICE_REFRESH_SECONDS,
    NEWS_REFRESH_MINUTES,
    FILINGS_REFRESH_MINUTES,
    FINANCIALS_REFRESH_HOURS,
    COMPANY_METADATA_REFRESH_DAYS,
)

from backend.module4.logger import logger


# ---------------------------------------------------
# Placeholder Jobs
# ---------------------------------------------------


def update_live_prices():
    """
    Refresh latest market prices.

    TODO:
    Call DataCollector.fetch_market_prices()
    """
    logger.info("[Scheduler] Updating Live Prices...")


def update_news():
    """
    Refresh company news.

    TODO:
    Call DataCollector.fetch_news()
    """
    logger.info("[Scheduler] Updating News...")


def update_filings():
    """
    Refresh latest filings.

    TODO:
    Call DataCollector.fetch_filings()
    """
    logger.info("[Scheduler] Updating Filings...")


def update_financial_statements():
    """
    Refresh financial statements.

    TODO:
    Call DataCollector.fetch_financials()
    """
    logger.info("[Scheduler] Updating Financial Statements...")


def update_company_metadata():
    """
    Refresh company profiles.

    TODO:
    Call DataCollector.fetch_company_profiles()
    """
    logger.info("[Scheduler] Updating Company Metadata...")


# ---------------------------------------------------
# Scheduler
# ---------------------------------------------------


class Module4Scheduler:

    def __init__(self):

        self.scheduler = BackgroundScheduler()
        logger.info("[Scheduler] BackgroundScheduler initialized")

    def register_jobs(self):

        logger.info("[Scheduler] Registering scheduled jobs...")

        # Live Prices
        self.scheduler.add_job(
            update_live_prices,
            "interval",
            seconds=PRICE_REFRESH_SECONDS,
            id="live_prices",
            replace_existing=True,
        )

        # News
        self.scheduler.add_job(
            update_news,
            "interval",
            minutes=NEWS_REFRESH_MINUTES,
            id="news",
            replace_existing=True,
        )

        # Filings
        self.scheduler.add_job(
            update_filings,
            "interval",
            minutes=FILINGS_REFRESH_MINUTES,
            id="filings",
            replace_existing=True,
        )

        # Financial Statements
        self.scheduler.add_job(
            update_financial_statements,
            "interval",
            hours=FINANCIALS_REFRESH_HOURS,
            id="financials",
            replace_existing=True,
        )

        # Company Metadata
        self.scheduler.add_job(
            update_company_metadata,
            "interval",
            days=COMPANY_METADATA_REFRESH_DAYS,
            id="company_metadata",
            replace_existing=True,
        )

        logger.info("[Scheduler] All jobs registered successfully")

    def start(self):

        self.register_jobs()
        self.scheduler.start()

        logger.info("[Module4] Scheduler Started.")

    def shutdown(self):

        self.scheduler.shutdown()

        logger.info("[Module4] Scheduler Stopped.")

    def list_jobs(self):

        jobs = self.scheduler.get_jobs()

        logger.info(f"[Scheduler] Total Active Jobs: {len(jobs)}")

        return jobs


# ---------------------------------------------------
# Singleton instance
# ---------------------------------------------------

module4_scheduler = Module4Scheduler()
