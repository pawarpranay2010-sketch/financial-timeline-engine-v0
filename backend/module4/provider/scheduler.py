"""
Module 4.4

Background Scheduler

Runs ingestion jobs automatically.

Current version:
- Architecture only
- Ready for APScheduler integration
"""

from apscheduler.schedulers.background import BackgroundScheduler
from backend.module4.ingestion_service import ingestion_service


class SchedulerManager:

    def __init__(self):
        self.scheduler = BackgroundScheduler()

    def add_company_job(
        self,
        provider_name: str,
        ticker: str,
        interval_minutes: int = 60,
    ):
        """
        Schedule automatic ingestion.

        Example:
            Every 60 minutes:
            Fetch RELIANCE from NSE
        """

        self.scheduler.add_job(
            ingestion_service.ingest_company,
            "interval",
            minutes=interval_minutes,
            args=[provider_name, ticker],
            id=f"{provider_name}_{ticker}",
            replace_existing=True,
        )

    def start(self):
        print("[Scheduler] Starting...")
        self.scheduler.start()

    def stop(self):
        print("[Scheduler] Stopping...")
        self.scheduler.shutdown()

    def list_jobs(self):
        return self.scheduler.get_jobs()


scheduler_manager = SchedulerManager()
